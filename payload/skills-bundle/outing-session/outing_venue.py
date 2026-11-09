#!/usr/bin/env python3
"""Read Naver discovery and official venue facts through the installed browser.

usage: outing_venue.py --search QUERY
       outing_venue.py PLACE_ID --at 'YYYY-MM-DD HH:MM' [--page home|menu|information]
       outing_venue.py --url HTTPS_URL
No API key, database, booking or message write. Browser calls are sequential.
Unknown page fields stay unknown. Raw official hours and page text are retained
alongside the existing dining parser's date-specific verdict for review.
"""
import argparse
import datetime
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import quote

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent


def browser_page(url, runner=subprocess.run, require_state=True):
    binary = os.environ.get('ROUTE_AGENT_BROWSER', 'agent-browser')
    for args in (['open', url], ['wait', '2000']):
        proc = runner([binary, *args], capture_output=True, text=True, timeout=60)
        if proc.returncode:
            raise ValueError(proc.stderr.strip() or 'browser navigation failed')
    script = '({url:location.href,state:window.__APOLLO_STATE__||{},text:document.body.innerText})'
    proc = runner([binary, 'eval', script], capture_output=True, text=True, timeout=30)
    if proc.returncode:
        raise ValueError(proc.stderr.strip() or 'official page unreadable')
    page = json.loads(proc.stdout)
    if require_state and not page.get('state'):
        raise ValueError('official page has no readable venue state')
    if not require_state and re.search(r'\uc11c\ube44\uc2a4 \uc774\uc6a9\uc774 \uc81c\ud55c|\uacfc\ub3c4\ud55c \uc811\uadfc|Access Denied|404 Not Found', page.get('text', ''), re.I):
        raise ValueError('official page body is restricted; no readable booking or ticket terms')
    if not page.get('text') and not page.get('state'):
        raise ValueError('official page has no readable content')
    page['requested_url'] = url
    recorder = os.environ.get('AR_RECORDER_DIR')
    if recorder:
        # Keep the browser's actual result, not model-authored page summaries.
        with (Path(recorder) / 'venue-reads.jsonl').open('a') as output:
            output.write(json.dumps(dict(operation='read', rc=0, **page)) + '\n')
    return page


def price_quotes(state, text=""):
    """Original menu labels and display prices; never a per-person conversion."""
    quotes = []
    for key, value in state.items():
        if not key.startswith('PlaceMenuItem:') or not isinstance(value, dict):
            continue
        label = value.get('name') or ''
        display = (value.get('price') or {}).get('displayText') or ''
        if label and display:
            quotes.append(label + ' ' + display)
    if not quotes:
        # Body-only official ticket/menu pages have no embedded menu objects.
        # Keep each printed tariff entry; never combine adjacent priced items.
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for index, line in enumerate(lines):
            if not re.search(r'\d[\d,]*\s*(?:\uc6d0|KRW|won)', line, re.I):
                continue
            if re.fullmatch(r'[\d,]+\s*(?:\uc6d0|KRW|won)', line, re.I) and index:
                line = lines[index - 1] + ' ' + line
            quotes.extend(re.split(
                r'(?:(?<=\uc6d0)|(?<=KRW)|(?<=won))\s*/\s*'
                r'(?=[^/\n]*\d[\d,]*\s*(?:\uc6d0|KRW|won))', line, flags=re.I))
    return quotes


def search_rows(page, source):
    rows, seen = [], set()
    for value in page['state'].values():
        if not isinstance(value, dict):
            continue
        ident = str(value.get('id') or '')
        if (not re.fullmatch(r'\d+', ident) or not value.get('name')
                or not value.get('roadAddress') or ident in seen):
            continue
        seen.add(ident)
        rows.append(dict(place_id=ident, name=value['name'], address=value['roadAddress'],
                         category=value.get('category'), discovery_source=source,
                         official_page='https://pcmap.place.naver.com/place/%s/home' % ident))
    if not rows:
        raise ValueError('no named addressed places on the discovery page')
    return rows


def venue_facts(page, ident, when, source):
    sys.path.insert(0, str(HERE.parent / 'dining-session'))
    import venue_facts as dining
    state = page['state']
    if not isinstance(state.get('PlaceDetailBase:' + ident), dict):
        raise ValueError('official page is not the requested venue')
    html = 'window.__APOLLO_STATE__ = ' + json.dumps(state) + ';\n'
    result = dining.facts(ident, when, html=html)
    result.pop('per_person', None)
    result['price_quotes'] = price_quotes(state, page.get('text', ''))
    result['content_basis'] = 'live official page embedded state; requested venue identity verified'
    result['structured_page_facts_read'] = True
    result['official_page'] = source
    result['official_text'] = page.get('text', '')
    result['official_hours'] = dining._detail(state).get('newBusinessHours')
    return result


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('place_id', nargs='?')
    ap.add_argument('--search')
    ap.add_argument('--at')
    ap.add_argument('--page', choices=('home', 'menu', 'information'), default='home')
    ap.add_argument('--url')
    args = ap.parse_args()
    if sum(bool(v) for v in (args.search, args.place_id, args.url)) != 1 or (args.place_id and not args.at):
        ap.error('use --search QUERY, --url HTTPS_URL or PLACE_ID --at DATETIME')
    try:
        if args.url:
            if not args.url.startswith('https://'):
                raise ValueError('official page URL must use HTTPS')
            page = browser_page(args.url, require_state=False)
            result = dict(official_page=args.url, resolved_url=page.get('url'),
                          official_text=page.get('text', ''),
                          price_quotes=price_quotes(page.get('state', {}), page.get('text', '')))
        elif args.search:
            source = 'https://pcmap.place.naver.com/place/list?query=' + quote(args.search)
            result = search_rows(browser_page(source), source)
        else:
            if not re.fullmatch(r'\d+', args.place_id):
                raise ValueError('expected a numeric Naver place id')
            source = 'https://pcmap.place.naver.com/place/%s/%s' % (args.place_id, args.page)
            result = venue_facts(browser_page(source), args.place_id,
                                 datetime.datetime.strptime(args.at, '%Y-%m-%d %H:%M'), source)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print('OUTING_VENUE_BLOCKED: ' + str(error), file=sys.stderr)
        return 5


if __name__ == '__main__':
    sys.exit(main())
