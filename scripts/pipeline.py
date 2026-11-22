from pathlib import Path
import datetime as dt, json, re, html
from urllib.parse import quote
import feedparser, requests, yaml
from dateutil import parser as dateparser
ROOT=Path(__file__).resolve().parents[1]

def load_yaml(name):
    return yaml.safe_load((ROOT/'config'/name).read_text(encoding='utf-8'))

def clean(value):
    return re.sub(r'<[^>]+>', ' ', value or '').replace('&nbsp;', ' ').strip()

def iso_date(value):
    try: return dateparser.parse(value).date().isoformat()
    except Exception: return dt.date.today().isoformat()

def collect_rss(source):
    result=[]
    for e in feedparser.parse(source['url']).entries:
        result.append({'title':clean(e.get('title','')),'url':e.get('link',''),'date':iso_date(e.get('published') or e.get('updated')),'summary':clean(e.get('summary','')),'source':source['name'],'source_score':source['source_score'],'doi':''})
    return result

def collect_crossref(source):
    start=(dt.date.today()-dt.timedelta(days=30)).isoformat()
    url='https://api.crossref.org/journals/'+quote(str(source['issn']))+'/works'
    params={'filter':'from-pub-date:'+start,'sort':'published','order':'desc','rows':100}
    response=requests.get(url,params=params,timeout=45,headers={'User-Agent':'SiliconeIntelligence/2.0'}); response.raise_for_status()
    result=[]
    for item in response.json()['message']['items']:
        parts=(item.get('published-online') or item.get('published-print') or {}).get('date-parts',[[dt.date.today().year,1,1]])[0]
        date=f"{parts[0]}-{parts[1] if len(parts)>1 else 1:02d}-{parts[2] if len(parts)>2 else 1:02d}"
        result.append({'title':clean((item.get('title') or [''])[0]),'url':item.get('URL',''),'date':date,'summary':clean(item.get('abstract','')),'source':source['name'],'source_score':source['source_score'],'doi':item.get('DOI','')})
    return result

def score(article, keywords):
def score(article, keywords):
    text = (article['title'] + ' ' + article['summary']).lower()

    gates = [
        weight
        for term, weight in keywords['material_gate'].items()
        if term in text
    ]

    abbreviation_rules = {
        ' lsr ': 16,
        ' hcr ': 16,
        ' htv ': 14,
        ' rtv ': 12,
        ' vmq ': 16,
        ' pvmq ': 16,
        ' fvmq ': 18,
    }

    padded_text = ' ' + re.sub(r'[^a-z0-9]+', ' ', text) + ' '

    silicone_context = any(term in text for term in [
        'silicone',
        'siloxane',
        'polysiloxane',
        'elastomer',
        'rubber',
        'crosslink',
        'cross-link',
        'vulcaniz',
    ])

    if silicone_context:
        for abbreviation, weight in abbreviation_rules.items():
            if abbreviation in padded_text:
                gates.append(weight)

    if not gates:
        return None
    if any(x in text for x in keywords['exclusions']) and 'silicone rubber' not in text and 'silicone elastomer' not in text: return None
    topics=[]; topic_score=0
    for name,group in keywords['topics'].items():
        if any(term in text for term in group['terms']): topics.append(name); topic_score=min(20,topic_score+group['weight'])
    app_score=min(20,sum(weight for term,weight in keywords['applications'].items() if term in text))
    age=(dt.date.today()-dt.date.fromisoformat(article['date'])).days
    recency=10 if age<=7 else 8 if age<=14 else 5
    article['topics']=topics or ['General Silicone']
    article['score']=min(100,max(gates)+topic_score+app_score+article['source_score']+recency+5)
    return article

def unique(items):
    output={}
    for item in items:
        key=(item.get('doi') or re.sub(r'[^a-z0-9]+',' ',item['title'].lower())).strip()
        if key and (key not in output or item['score']>output[key]['score']): output[key]=item
    return list(output.values())

def build(items, settings):
    (ROOT / 'weekly').mkdir(parents=True, exist_ok=True)
    (ROOT / 'data').mkdir(parents=True, exist_ok=True)
    (ROOT / 'docs').mkdir(parents=True, exist_ok=True)
    top=sorted([a for a in items if a['score']>=settings['minimum_score']],key=lambda x:(x['score'],x['date']),reverse=True)[:settings['weekly_top_n']]
    cards=[]
    for a in top:
        tags=''.join('<span>'+html.escape(t)+'</span>' for t in a['topics'])
        summary=(a['summary'][:360]+'...') if len(a['summary'])>360 else (a['summary'] or 'No abstract available. Open the source for details.')
        cards.append('<article><div class="meta">'+html.escape(a['source'])+' | '+a['date']+' | <b>'+str(a['score'])+'/100</b></div><h2>'+html.escape(a['title'])+'</h2><div class="tags">'+tags+'</div><p>'+html.escape(summary)+'</p><a href="'+html.escape(a['url'])+'" target="_blank">Read source</a></article>')
    week=f"{dt.date.today().isocalendar().year}-W{dt.date.today().isocalendar().week:02d}"
    page=(ROOT/'scripts/template.html').read_text(encoding='utf-8').replace('{{TITLE}}',settings['site_title']).replace('{{WEEK}}',week).replace('{{COUNT}}',str(len(top))).replace('{{CARDS}}',''.join(cards))
    (ROOT/'docs/index.html').write_text(page,encoding='utf-8')
    (ROOT/'data/articles.json').write_text(json.dumps(items,ensure_ascii=False,indent=2),encoding='utf-8')
    (ROOT/'data/latest.json').write_text(json.dumps(top,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# '+settings['site_title']+' '+week,'']+[f"## {i+1}. [{a['title']}]({a['url']})\n\n**{a['source']} | {a['score']}/100**\n\n{a['summary'][:500]}" for i,a in enumerate(top)]
    (ROOT/'weekly'/(week+'.md')).write_text('\n\n'.join(lines),encoding='utf-8')

def main():
    sources=load_yaml('sources.yml')['sources']; keywords=load_yaml('keywords.yml'); settings=load_yaml('settings.yml')
    gathered=[]
    for source in sources:
        if not source.get('enabled'): continue
        try:
            rows=collect_rss(source) if source['type']=='rss' else collect_crossref(source)
            gathered.extend(x for row in rows if (x:=score(row,keywords)))
            print(source['name'],len(rows),'retrieved')
        except Exception as error: print('WARNING',source['name'],error)
    old=[]
    try: old=json.loads((ROOT/'data/articles.json').read_text(encoding='utf-8'))
    except Exception: pass
    build(unique(old+gathered),settings)
if __name__=='__main__': main()
