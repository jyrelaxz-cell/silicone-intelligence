from pathlib import Path
import datetime as dt
import html
import json
import re
from urllib.parse import quote

import feedparser
import requests
import yaml
from dateutil import parser as dateparser

ROOT = Path(__file__).resolve().parents[1]


def load_yaml(name):
    return yaml.safe_load((ROOT / 'config' / name).read_text(encoding='utf-8'))


def clean(value):
    return re.sub(r'<[^>]+>', ' ', value or '').replace('&nbsp;', ' ').strip()


def iso_date(value):
    try:
        return dateparser.parse(value).date().isoformat()
    except Exception:
        return dt.date.today().isoformat()


def collect_rss(source):
    result = []
    for entry in feedparser.parse(source['url']).entries:
        result.append({
            'title': clean(entry.get('title', '')),
            'url': entry.get('link', ''),
            'date': iso_date(entry.get('published') or entry.get('updated')),
            'summary': clean(entry.get('summary', '')),
            'source': source['name'],
            'source_score': source['source_score'],
            'doi': '',
        })
    return result


def collect_crossref(source):
    start = (dt.date.today() - dt.timedelta(days=90)).isoformat()
    url = 'https://api.crossref.org/journals/' + quote(str(source['issn'])) + '/works'
    response = requests.get(
        url,
        params={'filter': 'from-pub-date:' + start, 'sort': 'published', 'order': 'desc', 'rows': 100},
        timeout=45,
        headers={'User-Agent': 'SiliconeIntelligence/3.0'},
    )
    response.raise_for_status()
    result = []
    for item in response.json()['message']['items']:
        parts = (item.get('published-online') or item.get('published-print') or {}).get(
            'date-parts', [[dt.date.today().year, 1, 1]]
        )[0]
        published = f"{parts[0]}-{parts[1] if len(parts) > 1 else 1:02d}-{parts[2] if len(parts) > 2 else 1:02d}"
        result.append({
            'title': clean((item.get('title') or [''])[0]),
            'url': item.get('URL', ''),
            'date': published,
            'summary': clean(item.get('abstract', '')),
            'source': source['name'],
            'source_score': source['source_score'],
            'doi': item.get('DOI', ''),
        })
    return result


def score(article, keywords):
    text = (article.get('title', '') + ' ' + article.get('summary', '')).lower()
    gates = [weight for term, weight in keywords['material_gate'].items() if term.lower() in text]
    abbreviations = {'lsr': 16, 'hcr': 16, 'htv': 14, 'rtv': 12, 'vmq': 16, 'pvmq': 16, 'fvmq': 18}
    padded = ' ' + re.sub(r'[^a-z0-9]+', ' ', text) + ' '
    context = any(term in text for term in ['silicone', 'siloxane', 'polysiloxane', 'elastomer', 'rubber', 'crosslink', 'vulcaniz'])
    if context:
        for abbreviation, weight in abbreviations.items():
            if f' {abbreviation} ' in padded:
                gates.append(weight)
    if not gates:
        return None
    explicit = any(term in text for term in ['silicone rubber', 'silicone elastomer', 'polysiloxane elastomer', 'fluorosilicone rubber', 'liquid silicone rubber'])
    if any(term.lower() in text for term in keywords.get('exclusions', [])) and not explicit:
        return None
    topics, matched, topic_score = [], [], 0
    for name, group in keywords['topics'].items():
        hits = [term for term in group['terms'] if term.lower() in text]
        if hits:
            topics.append(name)
            matched.extend(hits)
            topic_score = min(20, topic_score + int(group['weight']))
    application_score = min(20, sum(int(weight) for term, weight in keywords['applications'].items() if term.lower() in text))
    try:
        age = (dt.date.today() - dt.date.fromisoformat(article['date'])).days
    except Exception:
        age = 90
    recency = 10 if age <= 7 else 8 if age <= 14 else 5 if age <= 30 else 2 if age <= 90 else 0
    novelty = min(15, 4 + len(set(matched)))
    article.update(
        topics=topics or ['General Silicone'], matched=sorted(set(matched)),
        score=min(100, max(gates) + topic_score + application_score + int(article.get('source_score', 0)) + recency + novelty),
    )
    return article


def unique(items):
    output = {}
    for item in items:
        key = (item.get('doi') or re.sub(r'[^a-z0-9]+', ' ', item['title'].lower())).strip()
        if key and (key not in output or item['score'] > output[key]['score']):
            output[key] = item
    return list(output.values())


def excerpt(article, limit):
    value = article.get('summary', '').strip()
    if not value:
        return 'No abstract is available from the current data source. Open the original article for more information.'
    return value[:limit] + ('...' if len(value) > limit else '')


def nav(active, prefix=''):
    links = [('latest', prefix + 'index.html', 'Latest'), ('weekly', prefix + 'weekly.html', 'Weekly Summary'), ('archive', prefix + 'archive.html', 'Archive')]
    return ''.join(
        '<a class="' + ('active' if key == active else '') + '" href="' + href + '">' + label + '</a>'
        for key, href, label in links
    )


def shell(title, subtitle, active, body, prefix=''):
    return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title><link rel="stylesheet" href="{prefix}assets/style.css"></head><body><header><div class="wrap"><small>SILICONE INTELLIGENCE</small><h1>{html.escape(title)}</h1><p>{html.escape(subtitle)}</p><nav class="site-nav">{nav(active, prefix)}</nav></div></header><main class="wrap">{body}</main><footer class="wrap">Public-source technical intelligence for R&amp;D reference. Verify decisions against original publications, patents, and supplier documents.</footer></body></html>'''


def latest_rows(articles):
    rows = []
    for index, article in enumerate(articles, 1):
        rows.append(
            '<li class="latest-item"><h2><a href="' + html.escape(article['url']) + '" target="_blank" rel="noopener">'
            + html.escape(article['title']) + '</a></h2><p>' + html.escape(excerpt(article, 320))
            + '</p><div class="summary-meta">' + html.escape(article['source']) + ' | ' + html.escape(article['date'])
            + ' | Score: ' + str(article['score']) + '/100</div></li>'
        )
    return ''.join(rows)


def weekly_rows(articles):
    rows = []
    for article in articles:
        rows.append(
            '<li class="summary-item"><h3><a href="' + html.escape(article['url']) + '" target="_blank" rel="noopener">'
            + html.escape(article['title']) + '</a></h3><p>' + html.escape(excerpt(article, 500))
            + '</p><div class="summary-meta">' + html.escape(article['source']) + ' | ' + html.escape(article['date'])
            + ' | Score: ' + str(article['score']) + '/100</div></li>'
        )
    return ''.join(rows)


def build(items, settings):
    docs = ROOT / 'docs'
    data = ROOT / 'data'
    markdown = ROOT / 'weekly'
    history = docs / 'weekly'
    for folder in [docs, data, markdown, history]:
        folder.mkdir(parents=True, exist_ok=True)

    ordered = sorted(items, key=lambda x: (x['score'], x['date']), reverse=True)
    latest_threshold = max(20, int(settings.get('minimum_score', 30)) - 10)
    latest = [a for a in ordered if a['score'] >= latest_threshold][:40]
    weekly = [a for a in ordered if a['score'] >= int(settings.get('minimum_score', 30))][:int(settings.get('weekly_top_n', 20))]

    iso = dt.date.today().isocalendar()
    week = f'{iso.year}-W{iso.week:02d}'

    latest_body = (
        '<div class="summary-bar"><strong>Latest related research</strong><strong>' + str(len(latest)) + ' articles</strong></div>'
        '<section class="latest-section"><div class="section-heading"><small>LATEST</small><h2>Related Articles</h2>'
        '<p>Up to 40 recent silicone-related articles using a broader relevance threshold.</p></div>'
        '<ol class="latest-list">' + latest_rows(latest) + '</ol></section>'
    )
    (docs / 'index.html').write_text(
        shell(settings['site_title'], 'Broader recent coverage of silicone elastomer research', 'latest', latest_body), encoding='utf-8'
    )

    weekly_body = (
        '<div class="summary-bar"><strong>' + week + '</strong><strong>' + str(len(weekly)) + ' articles selected</strong></div>'
        '<section class="weekly-summary"><div class="section-heading"><small>WEEKLY DIGEST</small><h2>Weekly Article Summary</h2>'
        '<p>A concise overview of this week\'s selected articles, ranked by technical relevance.</p></div>'
        '<ol class="summary-list">' + weekly_rows(weekly) + '</ol></section>'
    )
    weekly_page = shell(settings['site_title'], 'Weekly intelligence for silicone elastomer innovation', 'weekly', weekly_body)
    (docs / 'weekly.html').write_text(weekly_page, encoding='utf-8')

    history_files = sorted(history.glob('*.html'), reverse=True)
    history_weeks = {item.stem for item in history_files}
    history_weeks.add(week)
    archive_links = ''.join(
        '<li><a href="weekly/' + wk + '.html">' + wk + ' Weekly Summary</a></li>'
        for wk in sorted(history_weeks, reverse=True)
    )
    archive_body = '<section class="archive-section"><div class="section-heading"><small>HISTORY</small><h2>All Weekly Summaries</h2></div><ol class="archive-list">' + archive_links + '</ol></section>'
    (docs / 'archive.html').write_text(
        shell('Weekly Archive', 'Previous silicone materials intelligence summaries', 'archive', archive_body), encoding='utf-8'
    )

    historical_page = shell(settings['site_title'] + ' ' + week, 'Archived weekly digest', 'weekly', weekly_body, prefix='../')
    (history / f'{week}.html').write_text(historical_page, encoding='utf-8')

    (data / 'articles.json').write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding='utf-8')
    (data / 'latest.json').write_text(json.dumps(latest, ensure_ascii=False, indent=2), encoding='utf-8')
    (data / f'{week}.json').write_text(json.dumps(weekly, ensure_ascii=False, indent=2), encoding='utf-8')
    lines = ['# ' + settings['site_title'] + ' ' + week, '', '## Weekly Article Summary', '']
    for index, article in enumerate(weekly, 1):
        lines.extend([f"{index}. [{article['title']}]({article['url']})", '', excerpt(article, 500), ''])
    (markdown / f'{week}.md').write_text('\n'.join(lines), encoding='utf-8')


def main():
    sources = load_yaml('sources.yml')['sources']
    keywords = load_yaml('keywords.yml')
    settings = load_yaml('settings.yml')
    gathered = []
    for source in sources:
        if not source.get('enabled'):
            continue
        try:
            rows = collect_rss(source) if source['type'] == 'rss' else collect_crossref(source)
            gathered.extend(result for row in rows if (result := score(row, keywords)))
            print(source['name'], len(rows), 'retrieved')
        except Exception as error:
            print('WARNING', source['name'], error)
    old = []
    try:
        old = json.loads((ROOT / 'data' / 'articles.json').read_text(encoding='utf-8'))
    except Exception:
        pass
    build(unique(old + gathered), settings)


if __name__ == '__main__':
    main()
