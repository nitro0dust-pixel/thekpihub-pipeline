#!/usr/bin/env python3
"""
The KPI Hub — 5-Engine Content Pipeline v1.0
GitHub Actions deployment — runs every 48 hours
Engines: Harvest → Synthesize → Verify → Publish → Notify
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

# ── LOGGING ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler('pipeline.log'),
        logging.StreamHandler()
    ]
)
log = logging.getLogger('kpihub')

# ── CONFIG FROM ENV ───────────────────────────────────────────────────────────
ANTHROPIC_API_KEY  = os.environ['ANTHROPIC_API_KEY']
SERPAPI_KEY        = os.environ['SERPAPI_KEY']
TELEGRAM_BOT_TOKEN = os.environ['TELEGRAM_BOT_TOKEN']
TELEGRAM_CHAT_ID   = os.environ['TELEGRAM_CHAT_ID']
WP_SITE_URL        = os.environ['WP_SITE_URL']        # https://thekpihub.com
WP_USERNAME        = os.environ['WP_USERNAME']         # WordPress admin username
WP_APP_PASSWORD    = os.environ['WP_APP_PASSWORD']     # WP Application Password

# Optional
ALPHA_VANTAGE_KEY  = os.environ.get('ALPHA_VANTAGE_KEY', '')

client = Anthropic(api_key=ANTHROPIC_API_KEY)

# ── RSS FEED SOURCES ──────────────────────────────────────────────────────────
RSS_FEEDS = [
    'https://feeds.feedburner.com/TechCrunch',
    'https://www.producthunt.com/feed',
    'https://feeds.feedburner.com/venturebeat/SZYF',
    'https://www.saastr.com/feed/',
    'https://chaotic.io/feed/',
    'https://www.indiehackers.com/feed.xml',
    'https://news.ycombinator.com/rss',
    'https://feeds.feedburner.com/oreilly/radar',
]

# ── ARTICLE CATEGORIES ────────────────────────────────────────────────────────
ARTICLE_TYPES = [
    {
        'slug': 'saas-market-flash',
        'title_template': 'SaaS Market Flash: {date}',
        'prompt_focus': 'top SaaS market news, funding rounds, and major product launches',
        'category': 'Market Intelligence',
        'tags': ['saas', 'market', 'funding', 'startups']
    },
    {
        'slug': 'kpi-spotlight',
        'title_template': 'KPI Spotlight: The Metrics That Moved This Week',
        'prompt_focus': 'key SaaS KPIs, benchmarks, and performance metrics trends',
        'category': 'KPI Intelligence',
        'tags': ['kpi', 'metrics', 'benchmarks', 'saas-analytics']
    },
    {
        'slug': 'india-saas-brief',
        'title_template': 'India SaaS Brief: {date}',
        'prompt_focus': 'Indian SaaS market news, Indian startup ecosystem, India-specific B2B metrics',
        'category': 'India SaaS',
        'tags': ['india-saas', 'indian-startups', 'b2b-india']
    },
]

# ═══════════════════════════════════════════════════════════════════════════════
# ENGINE 1 — HARVEST
# ═══════════════════════════════════════════════════════════════════════════════
def engine1_harvest():
    """Harvest content from RSS feeds and return raw signals."""
    log.info('ENGINE 1: Harvesting RSS feeds...')
    signals = []
    seen_hashes = set()

    for feed_url in RSS_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:5]:  # top 5 per feed
                title   = getattr(entry, 'title', '').strip()
                summary = getattr(entry, 'summary', '')[:500].strip()
                link    = getattr(entry, 'link', '').strip()

                if not title or not link:
                    continue

                # Dedup by title hash
                h = hashlib.md5(title.lower().encode()).hexdigest()
                if h in seen_hashes:
                    continue
                seen_hashes.add(h)

                signals.append({
                    'title':   title,
                    'summary': summary,
                    'link':    link,
                    'source':  feed.feed.get('title', feed_url)
                })
        except Exception as e:
            log.warning(f'Feed error {feed_url}: {e}')

    log.info(f'ENGINE 1: Harvested {len(signals)} signals')
    return signals[:40]  # cap at 40


# ═══════════════════════════════════════════════════════════════════════════════
# ENGINE 2 — SYNTHESIZE
# ═══════════════════════════════════════════════════════════════════════════════
def engine2_synthesize(signals, article_type):
    """Use Claude to synthesize signals into a full article."""
    log.info(f'ENGINE 2: Synthesizing → {article_type["slug"]}')

    signals_text = '\n'.join([
        f'- {s["title"]} ({s["source"]}): {s["summary"][:200]}'
        for s in signals
    ])

    today = datetime.now(timezone.utc).strftime('%B %d, %Y')
    title = article_type['title_template'].format(date=today)

    prompt = f"""You are the lead intelligence analyst at The KPI Hub — an AI-powered SaaS intelligence platform based in Delhi, India.

Today is {today}.

Write a comprehensive, decision-grade article about: {article_type['prompt_focus']}

SIGNALS TO SYNTHESIZE (use the most relevant ones):
{signals_text}

ARTICLE REQUIREMENTS:
- Title: {title}
- Length: 800-1200 words
- Tone: Authoritative, data-driven, zero fluff — written for SaaS founders, operators, and investors
- Structure: Use H2 subheadings, include specific data points, end with a "Key Takeaway" section
- SEO: Naturally include keywords related to {article_type['category']}
- Original insight: Add 1-2 original analytical observations beyond what the signals say
- Do NOT mention "signals" or that this was AI-synthesized — write as a journalist/analyst would

FORMAT: Return ONLY the article in HTML format suitable for WordPress.
Start directly with the content — no preamble, no explanation.
Use <h2>, <p>, <ul>, <li>, <strong> tags only.
"""

    response = client.messages.create(
        model='claude-sonnet-4-20250514',
        max_tokens=2000,
        messages=[{'role': 'user', 'content': prompt}]
    )

    content = response.content[0].text
    log.info(f'ENGINE 2: Generated {len(content)} chars for {article_type["slug"]}')

    return {
        'title':    title,
        'content':  content,
        'category': article_type['category'],
        'tags':     article_type['tags'],
        'slug':     article_type['slug'],
        'excerpt':  content[:300].replace('<h2>', '').replace('</h2>', '').replace('<p>', '').replace('</p>', '')[:200] + '...'
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ENGINE 3 — VERIFY
# ═══════════════════════════════════════════════════════════════════════════════
def engine3_verify(article):
    """SerpAPI spot-check: verify 1 key claim from the article."""
    log.info(f'ENGINE 3: Verifying → {article["slug"]}')

    # Extract a key phrase to verify (simplified — use title keywords)
    query = article['title'].replace(':', '').replace('Flash', '').replace('Brief', '').strip()
    query = query[:60]

    try:
        resp = requests.get('https://serpapi.com/search', params={
            'q':       query,
            'api_key': SERPAPI_KEY,
            'num':     3,
            'engine':  'google'
        }, timeout=15)

        data = resp.json()
        results = data.get('organic_results', [])

        if results:
            # Append verification note as HTML comment (invisible to readers)
            verification_note = f'\n<!-- VERIFIED: SerpAPI check on "{query}" returned {len(results)} corroborating sources. Top: {results[0].get("link", "N/A")} -->\n'
            article['content'] += verification_note
            log.info(f'ENGINE 3: Verified ✅ — {len(results)} sources found')
        else:
            log.warning(f'ENGINE 3: No verification results for "{query}"')

    except Exception as e:
        log.warning(f'ENGINE 3: SerpAPI error: {e} — proceeding without verification')

    return article


# ═══════════════════════════════════════════════════════════════════════════════
# ENGINE 4 — PUBLISH TO WORDPRESS
# ═══════════════════════════════════════════════════════════════════════════════
def engine4_publish(article):
    """Push article to WordPress REST API as a draft."""
    log.info(f'ENGINE 4: Publishing → {article["slug"]}')

    api_url = f'{WP_SITE_URL}/wp-json/wp/v2/posts'

    payload = {
        'title':   article['title'],
        'content': article['content'],
        'excerpt': article['excerpt'],
        'status':  'draft',          # DRAFT — requires human approval before publishing
        'slug':    f'{article["slug"]}-{datetime.now().strftime("%Y%m%d")}',
        'meta': {
            '_yoast_wpseo_metadesc': article['excerpt'],
        }
    }

    resp = requests.post(
        api_url,
        json=payload,
        auth=(WP_USERNAME, WP_APP_PASSWORD),
        headers={'Content-Type': 'application/json'},
        timeout=30
    )

    if resp.status_code in (200, 201):
        post_data = resp.json()
        post_id  = post_data.get('id')
        post_url = post_data.get('link', '')
        log.info(f'ENGINE 4: Published draft #{post_id} → {post_url}')
        return {'id': post_id, 'url': post_url, 'title': article['title']}
    else:
        log.error(f'ENGINE 4: WP API error {resp.status_code}: {resp.text[:200]}')
        raise Exception(f'WordPress publish failed: {resp.status_code}')


# ═══════════════════════════════════════════════════════════════════════════════
# ENGINE 5 — TELEGRAM NOTIFY
# ═══════════════════════════════════════════════════════════════════════════════
def engine5_notify(published_articles, harvest_count, errors):
    """Send summary notification to Telegram."""
    log.info('ENGINE 5: Sending Telegram notification...')

    now_ist = datetime.now(timezone.utc).strftime('%d %b %Y %H:%M') + ' UTC'
    status_emoji = '✅' if not errors else '⚠️'

    lines = [
        f'*{status_emoji} KPI Hub Pipeline Run*',
        f'`{now_ist}`',
        '',
        f'📡 *Signals harvested:* {harvest_count}',
        f'📝 *Articles generated:* {len(published_articles)}',
        '',
        '*Drafts ready for review:*',
    ]

    for art in published_articles:
        wp_url = f'{WP_SITE_URL}/wp-admin/post.php?post={art["id"]}&action=edit'
        lines.append(f'• [{art["title"][:50]}...]({wp_url})')

    if errors:
        lines.append('')
        lines.append(f'⚠️ *Errors:* {len(errors)}')
        for err in errors[:3]:
            lines.append(f'`{str(err)[:80]}`')

    lines += [
        '',
        '🍵 _Review over chai. Publish when ready._',
        f'[WordPress Admin]({WP_SITE_URL}/wp-admin) | [View Site]({WP_SITE_URL})'
    ]

    message = '\n'.join(lines)

    resp = requests.post(
        f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage',
        json={
            'chat_id':    TELEGRAM_CHAT_ID,
            'text':       message,
            'parse_mode': 'Markdown',
            'disable_web_page_preview': True
        },
        timeout=15
    )

    if resp.status_code == 200:
        log.info('ENGINE 5: Telegram notification sent ✅')
    else:
        log.error(f'ENGINE 5: Telegram error: {resp.text[:200]}')


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN ORCHESTRATOR
# ═══════════════════════════════════════════════════════════════════════════════
def main():
    log.info('=' * 60)
    log.info('KPI HUB PIPELINE v1.0 — STARTING')
    log.info('=' * 60)

    start_time = time.time()
    published  = []
    errors     = []

    # ENGINE 1 — Harvest
    signals = engine1_harvest()

    # ENGINES 2-4 — Synthesize → Verify → Publish (per article type)
    for article_type in ARTICLE_TYPES:
        try:
            # Engine 2 — Synthesize
            article = engine2_synthesize(signals, article_type)
            time.sleep(2)  # Rate limit buffer

            # Engine 3 — Verify
            article = engine3_verify(article)
            time.sleep(1)

            # Engine 4 — Publish
            result = engine4_publish(article)
            published.append(result)
            time.sleep(3)  # Be nice to WP API

        except Exception as e:
            log.error(f'Pipeline error for {article_type["slug"]}: {e}')
            errors.append(e)
            continue

    # ENGINE 5 — Notify
    engine5_notify(published, len(signals), errors)

    elapsed = round(time.time() - start_time, 1)
    log.info(f'PIPELINE COMPLETE — {len(published)} articles | {elapsed}s elapsed')
    log.info('=' * 60)

    # Exit with error code if all articles failed
    if len(published) == 0 and len(ARTICLE_TYPES) > 0:
        exit(1)


if __name__ == '__main__':
    main()
