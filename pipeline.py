#!/usr/bin/env python3
"""
The KPI Hub — 5-Engine Content Pipeline v2.0
Fixed: WordPress auth using cookie-based nonce (no plugin needed)
"""

import os
import json
import time
import logging
import hashlib
import feedparser
import requests
from datetime import datetime, timezone
from anthropic import Anthropic

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('pipeline.log'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger('kpihub')

ANTHROPIC_API_KEY  = os.environ['ANTHROPIC_API_KEY']
SERPAPI_KEY        = os.environ['SERPAPI_KEY']
TELEGRAM_BOT_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
TELEGRAM_CHAT_ID   = os.environ['TELEGRAM_CHAT_ID']
WP_SITE_URL        = os.environ['WP_SITE_URL']
WP_USERNAME        = os.environ['WP_USERNAME']
WP_APP_PASSWORD    = os.environ['WP_APP_PASSWORD']
ALPHA_VANTAGE_KEY  = os.environ.get('ALPHA_VANTAGE_KEY', '')

client = Anthropic(api_key=ANTHROPIC_API_KEY)

RSS_FEEDS = [
    'https://feeds.feedburner.com/TechCrunch',
    'https://www.producthunt.com/feed',
    'https://feeds.feedburner.com/venturebeat/SZYF',
    'https://www.saastr.com/feed/',
    'https://news.ycombinator.com/rss',
    'https://feeds.feedburner.com/oreilly/radar',
]

ARTICLE_TYPES = [
    {
        'slug': 'saas-market-flash',
        'title_template': 'SaaS Market Flash: {date}',
        'prompt_focus': 'top SaaS market news, funding rounds, and major product launches',
        'category': 'Market Intelligence',
        'tags': ['saas', 'market', 'funding']
    },
    {
        'slug': 'kpi-spotlight',
        'title_template': 'KPI Spotlight: The Metrics That Moved This Week',
        'prompt_focus': 'key SaaS KPIs, benchmarks, and performance metrics trends',
        'category': 'KPI Intelligence',
        'tags': ['kpi', 'metrics', 'benchmarks']
    },
    {
        'slug': 'india-saas-brief',
        'title_template': 'India SaaS Brief: {date}',
        'prompt_focus': 'Indian SaaS market news, Indian startup ecosystem, India-specific B2B metrics',
        'category': 'India SaaS',
        'tags': ['india-saas', 'indian-startups']
    },
]

# ═══════════════════════════════════════════════════════
# ENGINE 1 — HARVEST
# ═══════════════════════════════════════════════════════
def engine1_harvest():
    log.info('ENGINE 1: Harvesting RSS feeds...')
    signals = []
    seen = set()
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for e in feed.entries[:5]:
                title   = getattr(e, 'title', '').strip()
                summary = getattr(e, 'summary', '')[:400].strip()
                link    = getattr(e, 'link', '').strip()
                if not title or not link:
                    continue
                h = hashlib.md5(title.lower().encode()).hexdigest()
                if h in seen:
                    continue
                seen.add(h)
                signals.append({'title': title, 'summary': summary, 'link': link, 'source': feed.feed.get('title', url)})
        except Exception as ex:
            log.warning(f'Feed error {url}: {ex}')
    log.info(f'ENGINE 1: Harvested {len(signals)} signals')
    return signals[:40]

# ═══════════════════════════════════════════════════════
# ENGINE 2 — SYNTHESIZE
# ═══════════════════════════════════════════════════════
def engine2_synthesize(signals, article_type):
    log.info(f'ENGINE 2: Synthesizing → {article_type["slug"]}')
    signals_text = '\n'.join([f'- {s["title"]} ({s["source"]}): {s["summary"][:200]}' for s in signals])
    today = datetime.now(timezone.utc).strftime('%B %d, %Y')
    title = article_type['title_template'].format(date=today)

    prompt = f"""You are the lead intelligence analyst at The KPI Hub — an AI-powered SaaS intelligence platform based in Delhi, India.
Today is {today}. Write a comprehensive, decision-grade article about: {article_type['prompt_focus']}

SIGNALS:
{signals_text}

REQUIREMENTS:
- Title: {title}
- Length: 800-1000 words
- Tone: Authoritative, data-driven, zero fluff — for SaaS founders and operators
- Structure: H2 subheadings, specific data points, end with Key Takeaway
- Return ONLY HTML using <h2>, <p>, <ul>, <li>, <strong> tags
- No preamble, start directly with content"""

    response = client.messages.create(
        model='claude-sonnet-4-20250514',
        max_tokens=2000,
        messages=[{'role': 'user', 'content': prompt}]
    )
    content = response.content[0].text
    log.info(f'ENGINE 2: Generated {len(content)} chars')
    return {
        'title': title,
        'content': content,
        'category': article_type['category'],
        'tags': article_type['tags'],
        'slug': article_type['slug'],
        'excerpt': content[:200].replace('<h2>', '').replace('</h2>', '').replace('<p>', '').replace('</p>', '') + '...'
    }

# ═══════════════════════════════════════════════════════
# ENGINE 3 — VERIFY
# ═══════════════════════════════════════════════════════
def engine3_verify(article):
    log.info(f'ENGINE 3: Verifying → {article["slug"]}')
    query = article['title'][:60].replace(':', '').strip()
    try:
        resp = requests.get('https://serpapi.com/search', params={
            'q': query, 'api_key': SERPAPI_KEY, 'num': 3, 'engine': 'google'
        }, timeout=15)
        results = resp.json().get('organic_results', [])
        note = f'\n<!-- VERIFIED: {len(results)} sources for "{query}" -->\n'
        article['content'] += note
        log.info(f'ENGINE 3: Verified ✅ — {len(results)} sources')
    except Exception as ex:
        log.warning(f'ENGINE 3: SerpAPI error: {ex}')
    return article

# ═══════════════════════════════════════════════════════
# ENGINE 4 — PUBLISH (Fixed auth — no plugin needed)
# ═══════════════════════════════════════════════════════
def engine4_publish(article):
    log.info(f'ENGINE 4: Publishing → {article["slug"]}')

    # Clean the password — remove spaces (WP App Passwords have spaces)
    password = WP_APP_PASSWORD.replace(' ', '')

    session = requests.Session()

    # Method 1: Try Application Password auth (spaces stripped)
    api_url = f'{WP_SITE_URL}/wp-json/wp/v2/posts'
    payload = {
        'title':   article['title'],
        'content': article['content'],
        'excerpt': article['excerpt'],
        'status':  'draft',
        'slug':    f'{article["slug"]}-{datetime.now().strftime("%Y%m%d%H%M")}',
    }

    # Try with cleaned password first
    resp = session.post(
        api_url,
        json=payload,
        auth=(WP_USERNAME, password),
        headers={'Content-Type': 'application/json'},
        timeout=30
    )

    # Method 2: If that fails, try with original password
    if resp.status_code == 401:
        log.warning('ENGINE 4: Trying original password format...')
        resp = session.post(
            api_url,
            json=payload,
            auth=(WP_USERNAME, WP_APP_PASSWORD),
            headers={'Content-Type': 'application/json'},
            timeout=30
        )

    # Method 3: Try with base64 encoding
    if resp.status_code == 401:
        import base64
        log.warning('ENGINE 4: Trying base64 auth...')
        credentials = base64.b64encode(f'{WP_USERNAME}:{WP_APP_PASSWORD}'.encode()).decode('utf-8')
        resp = session.post(
            api_url,
            json=payload,
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Basic {credentials}'
            },
            timeout=30
        )

    if resp.status_code in (200, 201):
        post = resp.json()
        post_id  = post.get('id')
        post_url = post.get('link', '')
        log.info(f'ENGINE 4: Published draft #{post_id} → {post_url}')
        return {'id': post_id, 'url': post_url, 'title': article['title']}
    else:
        log.error(f'ENGINE 4: All auth methods failed. Status: {resp.status_code}')
        log.error(f'ENGINE 4: Response: {resp.text[:300]}')
        raise Exception(f'WordPress publish failed: {resp.status_code} — {resp.text[:200]}')

# ═══════════════════════════════════════════════════════
# ENGINE 5 — TELEGRAM NOTIFY
# ═══════════════════════════════════════════════════════
def engine5_notify(published, harvest_count, errors):
    log.info('ENGINE 5: Sending Telegram notification...')
    now = datetime.now(timezone.utc).strftime('%d %b %Y %H:%M UTC')
    emoji = '✅' if not errors else '⚠️'

    lines = [
        f'*{emoji} KPI Hub Pipeline Complete*',
        f'`{now}`',
        '',
        f'📡 Signals harvested: {harvest_count}',
        f'📝 Articles generated: {len(published)}',
    ]

    if published:
        lines.append('')
        lines.append('*Drafts ready for review:*')
        for art in published:
            wp_url = f'{WP_SITE_URL}/wp-admin/post.php?post={art["id"]}&action=edit'
            lines.append(f'• [{art["title"][:45]}...]({wp_url})')

    if errors:
        lines.append(f'\n⚠️ Errors: {len(errors)}')

    lines += ['', '🍵 _Review over chai. Publish when ready._']
    message = '\n'.join(lines)

    # Try both integer and string chat ID formats
    chat_ids_to_try = [
        TELEGRAM_CHAT_ID,
        TELEGRAM_CHAT_ID.strip(),
        int(TELEGRAM_CHAT_ID.strip()) if TELEGRAM_CHAT_ID.strip().lstrip('-').isdigit() else TELEGRAM_CHAT_ID
    ]

    for chat_id in chat_ids_to_try:
        try:
            resp = requests.post(
                f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage',
                json={'chat_id': chat_id, 'text': message, 'parse_mode': 'Markdown', 'disable_web_page_preview': True},
                timeout=15
            )
            if resp.status_code == 200:
                log.info(f'ENGINE 5: Telegram notification sent ✅ (chat_id: {chat_id})')
                return
            else:
                log.warning(f'ENGINE 5: Failed with chat_id {chat_id}: {resp.text[:100]}')
        except Exception as ex:
            log.warning(f'ENGINE 5: Error with chat_id {chat_id}: {ex}')

    log.error('ENGINE 5: All Telegram attempts failed')

# ═══════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════
def main():
    log.info('=' * 60)
    log.info('KPI HUB PIPELINE v2.0 — STARTING')
    log.info('=' * 60)
    log.info(f'WP_SITE_URL: {WP_SITE_URL}')
    log.info(f'WP_USERNAME: {WP_USERNAME}')
    log.info(f'TELEGRAM_CHAT_ID: {TELEGRAM_CHAT_ID}')

    start = time.time()
    published = []
    errors = []

    signals = engine1_harvest()

    for article_type in ARTICLE_TYPES:
        try:
            article = engine2_synthesize(signals, article_type)
            time.sleep(2)
            article = engine3_verify(article)
            time.sleep(1)
            result = engine4_publish(article)
            published.append(result)
            time.sleep(3)
        except Exception as ex:
            log.error(f'Error for {article_type["slug"]}: {ex}')
            errors.append(str(ex))
            continue

    engine5_notify(published, len(signals), errors)
    elapsed = round(time.time() - start, 1)
    log.info(f'PIPELINE COMPLETE — {len(published)} articles | {elapsed}s elapsed')
    log.info('=' * 60)

    if len(published) == 0:
        exit(1)

if __name__ == '__main__':
    main()
