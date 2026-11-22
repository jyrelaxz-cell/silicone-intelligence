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
    params = {
        'filter': 'from-pub-date:' + start,
        'sort': 'published',
        'order': 'desc',
        'rows': 100,
    }
    response = requests.get(
        url,
        params=params,
        timeout=45,
        headers={'User-Agent': 'SiliconeIntelligence/2.1'},
    )
    response.raise_for_status()
    result = []
    for item in response.json()['message']['items']:
        parts = (
            item.get('published-online')
            or item.get('published-print')
            or {}
        ).get('date-parts', [[dt.date.today().year, 1, 1]])[0]
        published = (
            f"{parts[0]}-"
            f"{parts[1] if len(parts) > 1 else 1:02d}-"
            f"{parts[2] if len(parts) > 2 else 1:02d}"
        )
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
    padded_text = ' ' + re.sub(r'[^a-z0-9]+', ' ', text) + ' '
    silicone_context = any(term in text for term in [
        'silicone', 'siloxane', 'polysiloxane', 'elastomer',
        'rubber', 'crosslink', 'cross-link', 'vulcaniz',
    ])
    if silicone_context:
        for abbreviation, weight in abbreviation_rules.items():
            if f' {abbreviation} ' in padded_text:
                gates.append(weight)

    if not gates:
        return None

    explicit_silicone_elastomer = any(term in text for term in [
        'silicone rubber', 'silicone elastomer', 'polysiloxane elastomer',
        'fluorosilicone rubber', 'liquid silicone rubber',
    ])
    if (
        any(term.lower() in text for term in keywords.get('exclusions', []))
        and not explicit_silicone_elastomer
    ):
        return None

    topics = []
    topic_score = 0
    matched_terms = []
    for topic_name, topic_group in keywords['topics'].items():
        hits = [
            term for term in topic_group['terms']
            if term.lower() in text
        ]
        if hits:
            topics.append(topic_name)
            matched_terms.extend(hits)
            topic_score = min(20, topic_score + int(topic_group['weight']))

    application_score = min(
        20,
        sum(
            int(weight)
            for term, weight in keywords['applications'].items()
            if term.lower() in text
        ),
    )

    try:
        age_days = (dt.date.today() - dt.date.fromisoformat(article['date'])).days
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

    novelty_score = min(15, 4 + len(set(matched_terms)))
    final_score = min(
        100,
        max(gates)
        + topic_score
        + application_score
        + int(article.get('source_score', 0))
        + recency_score
        + novelty_score,
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
    output = {}
    for item in items:
        key = (
            item.get('doi')
            or re.sub(r'[^a-z0-9]+', ' ', item['title'].lower())
        ).strip()
        if key and (key not in output or item['score'] > output[key]['score']):
            output[key] = item
    return list(output.values())


def short_summary(article, limit=500):
    value = article.get('summary', '').strip()
    if not value:
        return 'No abstract is available from the current data source. Open the original article for more information.'
    return value[:limit] + ('...' if len(value) > limit else '')


def build(items, settings):
    docs_folder = ROOT / 'docs'
    data_folder = ROOT / 'data'
    markdown_folder = ROOT / 'weekly'
    html_archive_folder = docs_folder / 'weekly'

    for folder in [docs_folder, data_folder, markdown_folder, html_archive_folder]:
        folder.mkdir(parents=True, exist_ok=True)

    selected = [
        article for article in items
        if article['score'] >= settings['minimum_score']
    ]
    top = sorted(
        selected,
        key=lambda article: (article['score'], article['date']),
        reverse=True,
    )[:settings['weekly_top_n']]

    today = dt.date.today()
    iso = today.isocalendar()
    week = f'{iso.year}-W{iso.week:02d}'

    summary_items = []
    article_cards = []
    for article in top:
        summary = short_summary(article)
        summary_items.append(
            '<li class="summary-item">'
            '<h3><a href="' + html.escape(article['url'])
            + '" target="_blank" rel="noopener">'
            + html.escape(article['title']) + '</a></h3>'
            '<p>' + html.escape(summary) + '</p>'
            '<div class="summary-meta">' + html.escape(article['source'])
            + ' | ' + html.escape(article['date'])
            + ' | Score: ' + str(article['score']) + '/100</div>'
            '</li>'
        )
        topic_tags = ''.join(
            '<span>' + html.escape(topic) + '</span>'
            for topic in article.get('topics', ['General Silicone'])
        )
        article_cards.append(
            '<article class="article-card">'
            '<div class="meta">' + html.escape(article['source'])
            + ' | ' + html.escape(article['date'])
            + ' | <b>' + str(article['score']) + '/100</b></div>'
            '<h2>' + html.escape(article['title']) + '</h2>'
            '<div class="tags">' + topic_tags + '</div>'
            '<p>' + html.escape(summary) + '</p>'
            '<a href="' + html.escape(article['url'])
            + '" target="_blank" rel="noopener">Read source</a>'
            '</article>'
        )

    archive_files = sorted(html_archive_folder.glob('*.html'), reverse=True)
    archive_weeks = {archive_file.stem for archive_file in archive_files}
    archive_weeks.add(week)
    archive_links = [
        '<li><a href="weekly/' + archive_week + '.html">'
        + archive_week + ' Weekly Summary</a></li>'
        for archive_week in sorted(archive_weeks, reverse=True)
    ]

    template = (ROOT / 'scripts' / 'template.html').read_text(encoding='utf-8')
    page = (
        template
        .replace('{{TITLE}}', settings['site_title'])
        .replace('{{WEEK}}', week)
        .replace('{{COUNT}}', str(len(top)))
        .replace('{{SUMMARY_LIST}}', ''.join(summary_items))
        .replace('{{CARDS}}', ''.join(article_cards))
        .replace('{{ARCHIVE_LINKS}}', ''.join(archive_links))
    )

    (docs_folder / 'index.html').write_text(page, encoding='utf-8')

    archive_page = (
        page
        .replace('href="assets/style.css"', 'href="../assets/style.css"')
        .replace('href="index.html"', 'href="../index.html"')
        .replace('href="archive.html"', 'href="../archive.html"')
        .replace('href="weekly/', 'href="')
    )
    (html_archive_folder / f'{week}.html').write_text(
        archive_page,
        encoding='utf-8',
    )

    archive_html = '''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Weekly Archive</title><link rel="stylesheet" href="assets/style.css"></head><body><header><div class="wrap"><small>SILICONE INTELLIGENCE</small><h1>Weekly Archive</h1><p>Previous silicone materials intelligence summaries</p><nav class="site-nav"><a href="index.html">Latest</a><a href="index.html#weekly-summary">Weekly Summary</a><a href="archive.html">Archive</a></nav></div></header><main class="wrap"><section class="archive-section"><div class="section-heading"><small>HISTORY</small><h2>All Weekly Summaries</h2></div><ol class="archive-list">''' + ''.join(archive_links) + '''</ol></section></main><footer class="wrap">Public-source technical intelligence for R&amp;D reference.</footer></body></html>'''
    (docs_folder / 'archive.html').write_text(archive_html, encoding='utf-8')

    (data_folder / 'articles.json').write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    (data_folder / 'latest.json').write_text(
        json.dumps(top, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    (data_folder / f'{week}.json').write_text(
        json.dumps(top, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )

    markdown_lines = [
        '# ' + settings['site_title'] + ' ' + week,
        '',
        '## Weekly Article Summary',
        '',
    ]
    for index, article in enumerate(top, start=1):
        markdown_lines.extend([
            f"{index}. [{article['title']}]({article['url']})",
            '',
            short_summary(article),
            '',
            '**Source:** ' + article['source']
            + ' | **Date:** ' + article['date']
            + ' | **Score:** ' + str(article['score']) + '/100',
            '',
        ])
    (markdown_folder / f'{week}.md').write_text(
        '\n'.join(markdown_lines),
        encoding='utf-8',
    )


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
            gathered.extend(
                result
                for row in rows
                if (result := score(row, keywords))
            )
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
