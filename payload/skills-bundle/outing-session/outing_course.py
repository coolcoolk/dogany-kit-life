#!/usr/bin/env python3
"""Read-only course optimizer, map links, reply, and calendar preview.

usage: outing_course.py plan.json --db /tmp/backup.db [--lang ko|en]
       [--format reply|html|calendar|audit] [--layout timeline|compact|cards]

Input extends outing_plan.py: stops have stable id, address, facts (text,
source, date), optional coord (lat/lng/naver_ref), reservation, and windows.
legs is a directed lookup matrix of {from,to,minutes,source:'route',evidence}.
origin is a {id,name,address,coord} object; return is derived automatically.
The optimizer considers ALL orders, then minimizes measured driving minutes,
home arrival, and stable ids. A missing measured edge excludes that order;
never infer reverse travel. Coverage reports how many edges remain unmeasured.
No live DB connection, appointment write, reservation, or network request here.
"""
import argparse
import copy
import html
import importlib.util
import itertools
import datetime
import json
import math
import re
from pathlib import Path
import sqlite3
import sys
sys.dont_write_bytecode = True
from urllib.parse import urlsplit, unquote

from outing_plan import hhmm, mins, run

HERE = Path(__file__).resolve().parent


class CourseError(ValueError):
    """Internal diagnostics are audit-only; never part of an owner reply."""


def web_url(value):
    u = urlsplit(str(value or ''))
    return u.scheme == 'https' and bool(u.hostname) and not u.username


def load_plan(path):
    """Bind each collected fact to its page; materialize prices in the renderer.

    Receipt paths are relative to the plan. Resolved facts retain their receipt
    and price index so a measured plan is checked again before rendering.
    Legacy non-whole-day previews can still use inline sourced facts.
    """
    path = Path(path).resolve()
    plan = json.loads(path.read_text())
    receipts = {}
    for stop in plan['stops']:
        for fact in stop.get('facts', []):
            ref = fact.get('receipt')
            if not ref:
                if plan.get('whole_day'):
                    raise CourseError('whole-day facts require saved page receipts')
                continue
            file = (path.parent / ref).resolve()
            if file not in receipts:
                receipts[file] = json.loads(file.read_text())
            page = receipts[file]
            source = page.get('official_page')
            if page.get('at') and page['at'][:10] != plan['date']:
                raise CourseError('page receipt was checked for a different date')
            if not web_url(source) or not (page.get('structured_page_facts_read') or page.get('official_text')):
                raise CourseError('fact receipt has no readable official page')
            if stop.get('place_id') and page.get('place_id') and str(stop['place_id']) != str(page['place_id']):
                raise CourseError('fact receipt belongs to another venue')
            if fact.get('source') and fact['source'] != source:
                raise CourseError('fact source differs from its page receipt')
            if 'price_index' in fact:
                index = fact['price_index']
                quotes = page.get('price_quotes', [])
                if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(quotes):
                    raise CourseError('price index is outside the page receipt')
                text = quotes[index]
                if fact.get('text') and fact['text'] != text:
                    raise CourseError('price text differs from its page entry')
            else:
                text = fact.get('text', '')
                if re.search(r'\d[\d,]*\s*(?:\uc6d0|KRW|won)', text, re.I):
                    raise CourseError('prices require a page price_index, one entry per fact')
            if not isinstance(text, str) or not text.strip() or '\n' in text or '\r' in text:
                raise CourseError('expected one nonempty page fact per line')
            fact.update(text=text, source=source, date=plan['date'], receipt=str(file))
    return plan


def optimize(plan, diagnostic=False):
    party = plan.get('party', 2)
    if isinstance(party, bool) or not isinstance(party, int) or party < 1:
        raise CourseError('party must be a positive integer headcount')
    if not 1 <= len(plan['stops']) <= 8:
        raise CourseError('expected 1-8 stops')
    if plan.get('whole_day'):
        if not 3 <= len(plan['stops']) <= 5:
            raise CourseError('a whole day requires 3-5 named stops')
        if not any(s.get('kind') == 'meal' and s.get('meal') == 'lunch'
                   and s.get('windows') for s in plan['stops']):
            raise CourseError('a whole day requires lunch in its meal window')
    ids = [s['id'] for s in plan['stops']]
    origin = plan['origin']['id']
    if len(set(ids + [origin])) != len(ids) + 1:
        raise CourseError('duplicate stop ids')
    nodes = {n['id']: n for n in [plan['origin']] + plan['stops']}
    receipts = plan.get('route_receipts')
    if receipts is not None:
        if len(receipts) != len(plan['legs']):
            raise CourseError('route receipts do not cover the measured edges')
        for edge, receipt in zip(plan['legs'], receipts):
            expected = [nodes[edge['from']]['address'], nodes[edge['to']]['address'],
                        plan.get('mode', 'car')]
            if receipt.get('command', [])[-3:] != expected:
                raise CourseError('stop address changed after route measurement; remeasure the saved plan')
    edges = {}
    for e in plan['legs']:
        v = e.get('minutes')
        if (e.get('source') != 'route' or not web_url(e.get('evidence')) or
                isinstance(v, bool) or not isinstance(v, (int, float)) or
                not math.isfinite(v) or v < 0):
            raise CourseError('unverified route measurement')
        key = (e['from'], e['to'])
        if any(n not in nodes for n in key):
            raise CourseError('route endpoint is outside this course')
        # Naver autocomplete can choose a different city for a similar POI.
        # A valid duration is insufficient: bind it to the expected place ids.
        expected = [nodes[n].get('place_id') for n in key]
        if any(expected):
            path = unquote(urlsplit(e['evidence']).path)
            if '/directions/' not in path:
                raise CourseError('route evidence has no endpoints')
            segments = path.split('/directions/', 1)[1].split('/')
            if len(segments) < 2:
                raise CourseError('route evidence has no endpoints')
            for wanted, segment in zip(expected, segments):
                bits = segment.split(',')
                if wanted and (len(bits) < 4 or bits[3] != str(wanted)):
                    raise CourseError('route lookup selected the wrong place')
        if key in edges:
            raise CourseError('duplicate route measurement')
        edges[key] = e
    for s in plan['stops']:
        if not s.get('address') or not s.get('facts'):
            raise CourseError('stop has no address or facts')
        if any(not web_url(f.get('source')) or not f.get('text') or
               f.get('date') != plan['date'] for f in s['facts']):
            raise CourseError('unsourced or wrong-date stop fact')
        if not s.get('hours') or not s['hours'].get('open') or not s['hours'].get('close'):
            raise CourseError('missing opening window')
        if not s.get('booking'):
            raise CourseError('missing booking policy')
    best, checked, feasible = None, 0, 0
    rejected = []
    for order in itertools.permutations(plan['stops']):
        pairs = list(zip([origin] + [s['id'] for s in order],
                         [s['id'] for s in order] + [origin]))
        if any(p not in edges for p in pairs):
            continue
        checked += 1
        p = copy.deepcopy(plan)
        p['origin'] = plan['origin']['name']
        p['stops'] = copy.deepcopy(list(order))
        for s, pair in zip(p['stops'], pairs):
            s.update(leg_min=edges[pair]['minutes'], leg_src='route')
            s['leg'] = edges[pair]
        p['return'] = dict(leg_min=edges[pairs[-1]]['minutes'], leg_src='route',
                           leg=edges[pairs[-1]])
        rows, bad = run(p)
        if plan.get('whole_day'):
            end = mins(next(r.split('\t')[1] for r in rows if r.startswith('END\t')))
            if end >= mins('17:30') and not any(
                    s.get('kind') == 'meal' and s.get('meal') == 'dinner'
                    and s.get('windows') for s in p['stops']):
                bad = True
                rows.append('REFUSE\twhole day extends into dinner without a dinner stop')
        unresolved = list(dict.fromkeys(p.get('unresolved', [])))
        inherited = [r for r in rows if r.startswith('UNRESOLVED\t')]
        if len(unresolved) + len(inherited) > 1:
            bad = True
        rows += ['UNRESOLVED\t' + fact for fact in unresolved]
        if bad:
            rejected.append(dict(order=[s['id'] for s in p['stops']], rows=rows,
                driving_min=sum(edges[pair]['minutes'] for pair in pairs)))
            continue
        feasible += 1
        score = (sum(edges[pair]['minutes'] for pair in pairs),
                 mins(next(r.split('\t')[1] for r in rows if r.startswith('END\t'))),
                 tuple(str(s['id']) for s in order))
        if best is None or score < best[0]:
            best = (score, p, rows)
    if best is None:
        if diagnostic:
            return dict(no_feasible_course=True, checked_orders=checked,
                        rejected_orders=sorted(rejected, key=lambda r:r['driving_min']))
        raise CourseError('no feasible measured course; resolve windows or route coverage')
    _, p, rows = best
    all_ids = [origin] + ids
    missing = [(a, b) for a in all_ids for b in all_ids if a != b and (a, b) not in edges]
    return dict(plan=p, rows=rows, driving_min=best[0][0], checked_orders=checked,
                feasible_orders=feasible, missing_edges=missing,
                scope='measured orders' if missing else 'all orders')


def kit_root():
    # Repository payload/skills-bundle and installed .claude/skills-bundle
    # differ by one directory. The latter never has .claude/database or
    # .claude/service; resolve the kit instance rather than assuming payload.
    for root in (HERE.parents[1], HERE.parents[2]):
        if (root / 'database/kit_life.py').is_file():
            return root
    raise ImportError('outing renderer cannot locate the kit database module')


def kit_module():
    path = kit_root() / 'database' / 'kit_life.py'
    spec = importlib.util.spec_from_file_location('outing_kit', path)
    k = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(k)
    return k


def map_links(plan, db, kit=None):
    """Reuse the alert provider resolver/builders in an IN-MEMORY copy.

    Named search links are provider-specific even when the stored place URL
    points elsewhere. Missing coords -> chosen provider destination search.
    Source connection is query_only and mode=ro; nothing persists in the copy.
    """
    k = kit or kit_module()
    src = sqlite3.connect(Path(db).resolve().as_uri() + '?mode=ro', uri=True)
    src.execute('PRAGMA query_only=ON')
    conn = sqlite3.connect(':memory:')
    try:
        src.backup(conn)
    finally:
        src.close()
    conn.row_factory = sqlite3.Row
    try:
        nodes = [plan['origin']] + plan['stops']
        ids = {}
        for index, node in enumerate(nodes):
            pid = -100000 - index
            ids[node['id']] = pid
            conn.execute('INSERT INTO place(id,ulid,name,address) VALUES(?,?,?,NULL)',
                         (pid, 'outing-preview-%s' % index, node['name']))
            if node.get('address'):
                conn.execute(
                    'INSERT INTO place_address(place_id,kind,value,is_primary) VALUES(?,?,?,1)',
                    (pid, 'legacy', node['address']))
            c = node.get('coord')
            if c:
                conn.execute('INSERT INTO place_coord(place_id,lat,lng,naver_ref,source) VALUES(?,?,?,?,?)',
                             (pid, c['lat'], c['lng'], c.get('naver_ref'), 'outing_preview'))
        # Explicit ask mode overrides the recorded default only in this copy.
        conn.execute("INSERT OR REPLACE INTO config(key,value) VALUES('travel_mode',?)",
                     (plan.get('mode', 'car'),))
        conn.execute('PRAGMA query_only=ON')
        providers = k.map_providers_for_leg(conn, plan.get('region'))
        # The Naver-measured course must not silently turn into Google search
        # links just because its preview nodes have no stored place_region.
        # Keep an explicit course app choice; otherwise use the measured map.
        naver_measured = bool(plan.get('legs')) and all(
            urlsplit(e.get('evidence', '')).hostname == 'map.naver.com' for e in plan['legs'])
        provider = plan.get('map_provider') or ('naver' if naver_measured else providers[0])
        if provider not in k.MAP_PLACE_LINKS:
            raise CourseError('map provider has no search link')
        from urllib.parse import quote
        search = k.MAP_PLACE_LINKS[provider][1]
        links = {}
        for node in nodes:
            q = ' '.join(x for x in (node['name'], node.get('address')) if x)
            links[node['id']] = search.format(q=quote(q, safe=''))
        legs = {}
        for a, b in itertools.permutations(nodes, 2):
            ac, bc = a.get('coord'), b.get('coord')
            directions = []
            if ac and bc:
                directions = k._MAP_LINK_BUILDERS[provider](
                    conn, ids[a['id']], ids[b['id']], ac, bc,
                    k._travel_map_mode(conn), k._travel_base_place_id(conn))
            route = next((x['url'] for x in directions if x.get('kind') == 'route'), None)
            legs[a['id'], b['id']] = route or links[b['id']]
        return dict(provider=provider, stops=links, legs=legs)
    finally:
        conn.close()


def load_copy(lang):
    p = kit_root() / 'service' / 'kit_life' / 'i18n' / (lang + '.json')
    return json.loads(p.read_text())


def calendar_preview(result):
    """One row per stop. This function never executes a kit write."""
    rows = []
    for s, r in zip(result['plan']['stops'], result['rows']):
        fields = r.split('\t')
        rows.append(dict(title=s['name'], start=result['plan']['date'] + ' ' + fields[4],
                         end=result['plan']['date'] + ' ' + fields[5],
                         place=s['name'], address=s['address']))
    return rows


def render(result, original, links, lang='ko'):
    """Owner pick 2026-10-24 14:37: one blockquote per stop, legs between, no middle dots."""
    c = load_copy(lang)
    def tr(key, **kw):
        return c['outing.' + key].format(**kw)
    def plain(text):
        return text.replace(' \u00b7 ', ', ').replace('\u00b7', ', ')
    p = result['plan']
    day = datetime.date.fromisoformat(p['date'])
    lines = ['**' + ' \u2192 '.join(s['name'] for s in p['stops']) + '**',
             tr('context', date=tr('date', m=day.month, d=day.day, wd=c['outing.weekdays'].split(',')[day.weekday()]),
                mode=tr('mode.' + p.get('mode', 'car')), party=tr('party', count=p.get('party', 2))) + ' ' + tr('reason'), '']
    previous = original['origin']['id']
    for i, (s, row) in enumerate(zip(p['stops'], result['rows']), 1):
        f = row.split('\t')
        leg = tr('first_leg' if i == 1 else 'leg', start=f[3], minutes=s['leg_min'], url=links['legs'][previous, s['id']])
        if int(f[6]):
            leg += ' ' + tr('wait', minutes=int(f[6]))
        lines.append(leg)
        # The plan accepts walk/activity; both use the existing visit label
        # unless the sourced plan supplies a more specific purpose. Do not
        # invent missing locale keys after the complete course is measured.
        kind = 'sight' if s['kind'] in ('walk', 'activity') else s['kind']
        lines.append('> ' + tr('stop', n=i, start=f[4], end=f[5], name=s['name'], url=links['stops'][s['id']])
                     + ' ' + plain(s.get('purpose') or tr('kind.' + kind)))
        lines += ['> \u2022 ' + fact['text'] + ' ([' + tr('source') + '](' + fact['source'] + '))' for fact in s['facts']]
        r = s.get('reservation') or {}
        if r.get('required'):
            lines.append('> \u2022 ' + tr('reserved' if r.get('status') == 'confirmed' else 'reservation_available'))
        lines.append('')
        previous = s['id']
    end = next(r.split('\t')[1] for r in result['rows'] if r.startswith('END\t'))
    lines += [tr('leg', start=hhmm(mins(end) - p['return']['leg_min']), minutes=p['return']['leg_min'],
                 url=links['legs'][previous, original['origin']['id']]),
              tr('home', time=end, url=links['stops'][original['origin']['id']])]
    tail = [tr('unresolved', fact=plain(r.split('\t')[1])) for r in result['rows'] if r.startswith('UNRESOLVED\t')]
    lines += [''] + tail + [tr('decision')]
    return '\n'.join(lines)


def render_html(reply, layout='timeline'):
    """Static screenshot surface; all labels come from the actual reply."""
    import re
    blocks = []
    for line in reply.splitlines():
        safe = html.escape(line)
        safe = re.sub(r'\[([^\]]+)\]\((https://[^\s)]+)\)', r'<a href="\2">\1</a>', safe)
        safe = re.sub(r'\*\*(.*?)\*\*', r'<strong>\1</strong>', safe)
        blocks.append('<p>' + safe + '</p>' if line else '<div class="gap"></div>')
    return ('<!doctype html><html lang="ko"><meta charset="utf-8"><style>'
            'body{margin:0;background:#edf1f0;color:#20302e;font:17px/1.55 -apple-system,sans-serif}'
            'main{box-sizing:border-box;max-width:760px;margin:24px auto;padding:30px;background:white;border-radius:18px}'
            'p{margin:5px 0;white-space:pre-wrap}a{color:#167b69;text-decoration:none}'
            'strong{font-size:19px}.gap{height:17px}'
            '.compact p{font-size:15px;line-height:1.35}.compact .gap{height:7px}'
            '.cards strong{display:block;background:#e6f4ed;border-radius:8px;padding:8px}'
            '</style><main class="' + layout + '">' + ''.join(blocks) + '</main></html>')


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('plan'); ap.add_argument('--db', required=True)
    ap.add_argument('--lang', choices=['ko', 'en'], default='ko')
    ap.add_argument('--format', choices=['reply', 'html', 'calendar', 'audit'], default='reply')
    ap.add_argument('--layout', choices=['timeline', 'compact', 'cards'], default='timeline')
    args = ap.parse_args()
    try:
        original = load_plan(args.plan)
        result = optimize(original, diagnostic=args.format == 'audit')
        if args.format == 'audit':
            print(json.dumps(result, ensure_ascii=False, indent=2)); return 0
        if args.format == 'calendar':
            print(json.dumps(calendar_preview(result), ensure_ascii=False, indent=2)); return 0
        links = map_links(original, args.db)
        reply = render(result, original, links, args.lang)
        print(render_html(reply, args.layout) if args.format == 'html' else reply)
        return 0
    except (ValueError, KeyError, TypeError, OSError, sqlite3.Error, ImportError) as e:
        # stderr is an agent audit surface; stdout remains empty on failure.
        print('OUTING_BLOCKED: ' + str(e), file=sys.stderr)
        return 3


if __name__ == '__main__':
    sys.exit(main())
