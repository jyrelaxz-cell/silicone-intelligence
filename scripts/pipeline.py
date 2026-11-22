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
    start=(dt.date.today()-dt.timedelta(days=90)).isoformat()
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
    text = (
        article.get('title', '') + ' ' +
        article.get('summary', '')
    ).lower()

    gates = [
        weight
        for term, weight in keywords['material_gate'].items()
        if term.lower() in text
    ]

    abbreviation_rules = {
        'lsr': 16,
        'hcr': 16,
        'htv': 14,
        'rtv': 12,
        'vmq': 16,
        'pvmq': 16,
        'fvmq': 18,
    }

    padded_text = (
        ' ' +
        re.sub(r'[^a-z0-9]+', ' ', text) +
        ' '
    )

    silicone_context = any(
        term in text
        for term in [
            'silicone',
            'siloxane',
            'polysiloxane',
            'elastomer',
            'rubber',
            'crosslink',
            'cross-link',
            'vulcaniz',
        ]
    )

    if silicone_context:
        for abbreviation, weight in abbreviation_rules.items():
            if f' {abbreviation} ' in padded_text:
                gates.append(weight)

    if not gates:
        return None

    exclusions = keywords.get('exclusions', [])

    explicit_silicone_elastomer = any(
        term in text
        for term in [
            'silicone rubber',
            'silicone elastomer',
            'polysiloxane elastomer',
            'fluorosilicone rubber',
            'liquid silicone rubber',
        ]
    )

    if (
        any(term.lower() in text for term in exclusions)
        and not explicit_silicone_elastomer
    ):
        return None

    topics = []
    topic_score = 0
    matched_terms = []

    for topic_name, topic_group in keywords['topics'].items():
        hits = [
            term
            for term in topic_group['terms']
            if term.lower() in text
        ]

        if hits:
            topics.append(topic_name)
            matched_terms.extend(hits)
            topic_score = min(
                20,
                topic_score + int(topic_group['weight'])
            )

    application_score = min(
        20,
        sum(
            int(weight)
            for term, weight in keywords['applications'].items()
            if term.lower() in text
        )
    )

    try:
        publish_date = dt.date.fromisoformat(article['date'])
        age_days = (dt.date.today() - publish_date).days
    except (ValueError, TypeError, KeyError):
        age_days = 30

    if age_days <= 7:
        recency_score = 10
    elif age_days <= 14:
        recency_score = 8
    elif age_days <= 30:
        recency_score = 5
    elif age_days <= 90:
        recency_score = 2
    else:
        recency_score = 0

    novelty_score = min(
        15,
        4 + len(set(matched_terms))
    )

    final_score = min(
        100,
        max(gates)
        + topic_score
        + application_score
        + int(article.get('source_score', 0))
        + recency_score
        + novelty_score
    )

    article.update(
        topics=topics or ['General Silicone'],
        matched=sorted(set(matched_terms)),
        material_score=max(gates),
        keyword_score=topic_score,
        application_score=application_score,
        recency_score=recency_score,
        novelty_score=novelty_score,
        score=final_score,
    )

    return article

def unique(items):
    output={}
    for item in items:
        key=(item.get('doi') or re.sub(r'[^a-z0-9]+',' ',item['title'].lower())).strip()
        if key and (key not in output or item['score']>output[key]['score']): output[key]=item
    return list(output.values())

def build(items, settings):
    docs_folder = ROOT / 'docs'
    data_folder = ROOT / 'data'
    markdown_folder = ROOT / 'weekly'
    html_archive_folder = docs_folder / 'weekly'

    docs_folder.mkdir(parents=True, exist_ok=True)
    data_folder.mkdir(parents=True, exist_ok=True)
    markdown_folder.mkdir(parents=True, exist_ok=True)
    html_archive_folder.mkdir(parents=True, exist_ok=True)

    selected_articles = [
        article
        for article in items
        if article['score'] >= settings['minimum_score']
    ]

    top = sorted(
        selected_articles,
        key=lambda article: (
            article['score'],
            article['date']
        ),
        reverse=True
    )[:settings['weekly_top_n']]

    current_date = dt.date.today()
    iso_calendar = current_date.isocalendar()

    week = (
        str(iso_calendar.year)
        + '-W'
        + str(iso_calendar.week).zfill(2)
    )

    summary_items = []
    article_cards = []

    for article in top:
        original_summary = article.get('summary', '').strip()

        if original_summary:
            short_summary = original_summary[:500]

            if len(original_summary) > 500:
                short_summary += '...'
        else:
            short_summary = (
                'No abstract is available from the current '
                'data source. Open the original article '
                'for more information.'
            )

        summary_items.append(
            '<li class="summary-item">'
            '<h3>'
            '<a href="' + html.escape(article['url']) + '" '
            'target="_blank" rel="noopener">'
            + html.escape(article['title']) +
            '</a>'
            '</h3>'
            '<p>' + html.escape(short_summary) + '</p>'
            '<div class="summary-meta">'
            + html.escape(article['source'])
            + ' | '
            + html.escape(article['date'])
            + ' | Score: '
            + str(article['score'])
            + '/100'
            '</div>'
            '</li>'
        )

        topic_tags = ''.join(
            '<span>' + html.escape(topic) + '</span>'
            for topic in article.get(
                'topics',
                ['General Silicone']
            )
        )

        article_cards.append(
            '<article class="article-card">'
            '<div class="meta">'
            + html.escape(article['source'])
            + ' | '
            + html.escape(article['date'])
            + ' | <b>'
            + str(article['score'])
            + '/100</b>'
            '</div>'
            '<h2>'
            + html.escape(article['title'])
            + '</h2>'
            '<div class="tags">'
            + topic_tags
            + '</div>'
            '<p>'
            + html.escape(short_summary)
            + '</p>'
            '<a href="'
            + html.escape(article['url'])
            + '" target="_blank" rel="noopener">'
            'Read source'
            '</a>'
            '</article>'
        )

archive_files = sorted(
    html_archive_folder.glob('*.html'),
    reverse=True
)

archive_weeks = {
    archive_file.stem
    for archive_file in archive_files
}

archive_weeks.add(week))

    *rchive_links = []

    for archive*week in sorted(
        archive_we*ks,
        reverse=True
    ):
  *     archive_links.append(
       *    '<li>'
            '<a href="w*ekly/'
            + archive_week
*           + '.html">'
           *+ archive_week
            + ' Wee*ly Summary'
            '</a>'
   *        '</li>'
        )

    tem*late = (
        ROOT / 'scripts' * 'template.html'
    ).read_text(
*       encoding='utf-8'
    )

   *page = (
        template
        *replace(
            '{{TITLE}}',
*           settings['site_title']
*       )
        .replace(
       *    '{{WEEK}}',
            week
 *      )
        .replace(
        *   '{{COUNT}}',
            str(le*(top))
        )
        .replace(*            '{{SUMMARY_LIST}}',
  *         ''.join(summary_items)
  *     )
        .replace(
         *  '{{CARDS}}',
            ''.join*article_cards)
        )
        .*eplace(
            '{{ARCHIVE_LIN*S}}',
            ''.join(archive_*inks)
        )
    )

    (
     *  docs_folder / 'index.html'
    )*write_text(
        page,
        *ncoding='utf-8'
    )

    archive*page = (
        page
        .rep*ace(
            'href="weekly/',
*           'href="'
        )
    *   .replace(
            'href="as*ets/style.css"',
            'href*"../assets/style.css"'
        )
 *      .replace(
            'href=*index.html"',
            'href=".*/index.html"'
        )
        .r*place(
            'href="archive.*tml"',
            'href="../archi*e.html"'
        )
    )

    (
  *     html_archive_folder / (week +*'.html')
    ).write_text(
       *archive_page,
        encoding='ut*-8'
    )

    archive_template = *""<!doctype html>
<html lang="zh-C*">
<head>
  <meta charset="utf-8">*  <meta
    name="viewport"
    co*tent="width=device-width, initial-*cale=1"
  >
  <title>Weekly Archiv*</title>
  <link
    rel="styleshe*t"
    href="assets/style.css"
  >*</head>

<body>
  <header>
    <di* class="wrap">
      <small>SILICO*E INTELLIGENCE</small>
      <h1>W*ekly Archive</h1>
      <p>Previou* silicone materials intelligence s*mmaries</p>

      <nav class="sit*-nav">
        <a href="index.html*>Latest</a>
        <a href="index*html#weekly-summary">
          We*kly Summary
        </a>
        <* href="archive.html">Archive</a>
 *    </nav>
    </div>
  </header>
*  <main class="wrap">
    <section*class="archive-section">
      <di* class="section-heading">
        *small>HISTORY</small>
        <h2>*ll Weekly Summaries</h2>
      </d*v>

      <ol class="archive-list"*
        """ + ''.join(archive_lin*s) + """
      </ol>
    </section*
  </main>

  <footer class="wrap"*
    Public-source technical intel*igence for R&amp;D reference.
  </*ooter>
</body>
</html>
"""

    (
*       docs_folder / 'archive.html*
    ).write_text(
        archive*template,
        encoding='utf-8'*    )

    (
        data_folder /*'articles.json'
    ).write_text(
*       json.dumps(
            ite*s,
            ensure_ascii=False,*            indent=2
        ),
  *     encoding='utf-8'
    )

    (*        data_folder / 'latest.json*
    ).write_text(
        json.du*ps(
            top,
            e*sure_ascii=False,
            inde*t=2
        ),
        encoding='u*f-8'
    )

    weekly_data_file =*(
        data_folder / (week + '.*son')
    )

    weekly_data_file.*rite_text(
        json.dumps(
   *        top,
            ensure_as*ii=False,
            indent=2
   *    ),
        encoding='utf-8'
  * )

    markdown_lines = [
        '# ' + settings['site_title'] + ' ' + week,
        '',
        '## Weekly Article Summary',
        ''
    ]

    for index, article in en*merate(top, start=1):
        orig*nal_summary = article.get(
       *    'summary',
            ''
    *   ).strip()

        if original_*ummary:
            markdown_summa*y = original_summary[:500]

      *     if len(original_summary) > 50*:
                markdown_summary*+= '...'
        else:
           *markdown_summary = (
             *  'No abstract is available from '*                'the current sourc*.'
            )

        markdown*lines.extend([
            str(index)
            + '. ['
            + article['title']
            + ']('
            + article['url']
            + ')',
            '',
  *         markdown_summary,
       *    '',
            '**Source:** '*            + article['source']
  *         + ' | **Date:** '
       *    + article['date']
            * ' | **Score:** '
            + st*(article['score'])
            + '*100',
            ''
        ])

 *  (
        markdown_folder / (wee* + '.md')
    ).write_text(
      * '\n'.join(markdown_lines),
      * encoding='utf-8'
    )
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
