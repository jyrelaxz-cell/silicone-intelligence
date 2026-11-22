import os,re,json,html,pathlib,datetime as dt
from urllib.parse import quote
import requests,feedparser,yaml
from dateutil import parser as dp
from jinja2 import Template
R=pathlib.Path(__file__).resolve().parents[1]; D=R/'data'; O=R/'docs'; W=R/'weekly'
def y(n): return yaml.safe_load((R/'config'/n).read_text(encoding='utf-8'))
def clean(s): return re.sub(r'<[^>]+>',' ',s or '').replace('&nbsp;',' ').strip()
def date(v):
 try:return dp.parse(v).date().isoformat()
 except:return dt.date.today().isoformat()
def rss(s):
 return [{'title':clean(e.get('title','')),'url':e.get('link',''),'date':date(e.get('published') or e.get('updated')),'summary':clean(e.get('summary','')),'source':s['name'],'source_score':s['source_score'],'doi':''} for e in feedparser.parse(s['url']).entries]
def crossref(s):
 start=(dt.date.today()-dt.timedelta(days=s.get('lookback_days',14))).isoformat(); u='https://api.crossref.org/journals/'+quote(s['issn'])+'/works'
 p={'filter':'from-pub-date:'+start,'sort':'published','order':'desc','rows':100,'select':'DOI,title,abstract,URL,published-online,published-print'}
 x=requests.get(u,params=p,timeout=40,headers={'User-Agent':'SiliconeIntelligence/1.0'}).json()['message']['items']; out=[]
 for a in x:
  z=(a.get('published-online') or a.get('published-print') or {}).get('date-parts',[[2026,1,1]])[0]; dd=f'{z[0]}-{z[1] if len(z)>1 else 1:02d}-{z[2] if len(z)>2 else 1:02d}'
  out.append({'title':clean((a.get('title') or [''])[0]),'url':a.get('URL',''),'date':dd,'summary':clean(a.get('abstract','')),'source':s['name'],'source_score':s['source_score'],'doi':a.get('DOI','')})
 return out
def score(a,k):
 t=(a['title']+' '+a['summary']).lower(); mats=[v for q,v in k['material_gate'].items() if q in t]
 if not mats:return None
 if any(q in t for q in k['exclusions']) and not ('silicone rubber' in t or 'silicone elastomer' in t):return None
 ts=[]; ks=0; hits=[]
 for n,g in k['topics'].items():
  h=[q for q in g['terms'] if q in t]
  if h:ts.append(n);hits+=h;ks=min(20,ks+g['weight'])
 aps=min(20,sum(v for q,v in k['applications'].items() if q in t)); age=(dt.date.today()-dt.date.fromisoformat(a['date'])).days; rec=10 if age<=7 else 8 if age<=14 else 5
 a.update(topics=ts or ['General Silicone'],matched=hits,score=min(100,max(mats)+ks+aps+a['source_score']+rec+min(15,4+len(hits))),why='; '.join(ts[:2]) or 'Silicone-related update')
 return a
def dedupe(x):
 d={}
 for a in x:
  q=(a.get('doi') or re.sub('[^a-z0-9]+',' ',a['title'].lower())).strip()
  if q and (q not in d or a['score']>d[q]['score']):d[q]=a
 return list(d.values())
def summary(a,cfg):
 key=os.getenv(cfg.get('secret_name','AI_API_KEY'))
 if not cfg.get('enabled') or not key:return (a['summary'][:260]+'...') if len(a['summary'])>260 else (a['summary'] or 'No abstract available. Open the source for details.')
 p='Write a factual Chinese summary under 120 Chinese characters for a materials engineer. Title: '+a['title']+' Abstract: '+a['summary']
 r=requests.post(cfg['base_url'].rstrip('/')+'/chat/completions',headers={'Authorization':'Bearer '+key},json={'model':cfg['model'],'messages':[{'role':'user','content':p}],'temperature':0.2},timeout=60);r.raise_for_status();return r.json()['choices'][0]['message']['content']
def build(items,cfg):
 yy,ww,_=dt.date.today().isocalendar(); wk=f'{yy}-W{ww:02d}'; top=sorted([a for a in items if a['score']>=cfg['selection']['minimum_score']],key=lambda a:(a['score'],a['date']),reverse=True)[:cfg['selection']['weekly_top_n']]
 for a in top:a['summary_cn']=summary(a,cfg['ai'])
 D.mkdir(exist_ok=True);O.mkdir(exist_ok=True);W.mkdir(exist_ok=True);(D/'articles.json').write_text(json.dumps(items,ensure_ascii=False,indent=2),encoding='utf-8');(D/'latest.json').write_text(json.dumps(top,ensure_ascii=False,indent=2),encoding='utf-8')
 cards=[]
 for a in top:
  tags=''.join('<i>'+html.escape(q)+'</i>' for q in a['topics']); cards.append('<article class="card"><div class="meta"><span>'+html.escape(a['source'])+'</span><span>'+a['date']+'</span><b>'+str(a['score'])+'/100</b></div><h2>'+html.escape(a['title'])+'</h2><div class="tags">'+tags+'</div><p>'+html.escape(a['summary_cn'])+'</p><p class="why"><strong>Why it matters: </strong>'+html.escape(a['why'])+'</p><a href="'+html.escape(a['url'])+'" target="_blank">Read source</a></article>')
 tpl=Template((R/'scripts/template.html').read_text(encoding='utf-8'));(O/'index.html').write_text(tpl.render(title=cfg['site']['title'],subtitle=cfg['site']['subtitle'],week=wk,count=len(top),cards=''.join(cards)),encoding='utf-8')
 md = '# ' + cfg['site']['title'] + ' ' + wk + chr(10) + chr(10)
 md += (chr(10)+chr(10)).join('## '+str(i+1)+'. ['+a['title']+']('+a['url']+')'+chr(10)+chr(10)+a['summary_cn'] for i,a in enumerate(top))
 (W/(wk+'.md')).write_text(md,encoding='utf-8')

def main():
 ss=y('sources.yml')['sources'];k=y('keywords.yml');cfg=y('settings.yml');new=[]
 for s in ss:
  if not s.get('enabled'):continue
  try:
   rows=rss(s) if s['type']=='rss' else crossref(s);new += [x for a in rows if (x:=score(a,k))];print(s['name'],len(rows))
  except Exception as e:print('WARN',s['name'],e)
 old=[];p=D/'articles.json'
 if p.exists():
  try:old=json.loads(p.read_text(encoding='utf-8'))
  except:pass
 build(dedupe(old+new),cfg)
if __name__=='__main__':main()
