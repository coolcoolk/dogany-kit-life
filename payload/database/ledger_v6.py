"""ledger_v6.py -- the ledger v6 import + reconcile + service verbs (kit_life
2.46.x, migration 075; owner 2026-10-12 first live run, the maintainer worklog
2026-10-12-ledger-redesign-v6.md, decisions D1-D5 + spend-links-to-modules).

One structure instead of nineteen point fixes. A bank export goes in ONCE;
what comes out is a LANDED ledger -- every payment a spend_entry row with its
class, nothing pending, nothing counted twice:

  ledger-import <file>     parse -> canonical payment methods (layers) ->
                           classes (own move / top-up / card bill /
                           pass-through / saving / loan / meal point) ->
                           import identity + bucket reconcile against the rows
                           already in the ledger -> land -> streams (ledger_diag)
  ledger-find <text>       find a payment in the ledger / streams / the file
  ledger-reset --dry-run|--yes
  ledger-restart --dry-run|--yes   reset ledger data/onboarding and retire active ledger goals/preferences
  ledger-dedupe --dry-run  proof that no bucket holds more rows than it should
  funding-set / method-scope / spend-link* / spend-person*   (links below)

Rules here are deterministic and carry no model call: the model only carries
the owner's WORDS into the structured verbs (stop, keep, funding, link).

SPEND <-> MODULE LINK (owner 2026-10-12 17:10): one table (spend_link), kinds
  person   persons.id               who the money was spent FOR / WITH
  venue    place.id                 a gym, a studio, a shop
  workout  workouts.id              a PT session, a class
  meal     block.id (diet module)   the meal the money paid for
  supplement  a label               a supplement the money bought
  gift     persons.id               the recipient of a gift
  shop_item / owned_item  shop ids  a thing bought
The row stays the owner's spend (amount, scope unchanged). A standing rule
(spend_link_rule.match_json: method / weekday / time / merchant / cat / class
/ amount) links past and future rows. Each module reads its domain with
spend-link-sum <kind> [<ref>] [--month YYYY-MM].
"""
import argparse
import collections
import datetime
import difflib
import hashlib
import json
import os
import re
import sqlite3
import sys
import unicodedata

import ledger_profile as LP

VERBS = ('amount-note-add', 'amount-note-remove', 'purpose-evidence', 'funding-evidence', 'bank-evidence', 'method-merge', 'ledger-import', 'ledger-find', 'ledger-reset', 'ledger-restart', 'ledger-dedupe', 'funding-set', 'funding-get', 'purpose-set', 'purpose-get', 'method-scope',
         'spend-link', 'spend-unlink', 'spend-link-rule-add', 'spend-link-rule-list', 'spend-link-rule-off',
         'spend-link-sum', 'spend-person', 'spend-person-rule-add',
         'ledger-diagnosis', 'stream-list', 'stream-reconcile', 'stream-stop', 'stream-keep', 'stream-set',
         'stream-merge', 'stream-link', 'ledger-card-answer', 'ledger-review-answer', 'ledger-state', 'ledger-benefit-compare', 'ledger-history', 'ledger-history-reconcile', 'ledger-plan', 'ledger-plan-answer', 'ledger-plan-commit', 'ledger-plan-project', 'ledger-merchant-kind-set', 'ledger-merchant-kinds', 'ledger-merchant-names', 'ledger-merchant-names-set')
READ_VERBS = ('purpose-evidence', 'funding-evidence', 'bank-evidence', 'ledger-benefit-compare', 'purpose-get', 'funding-get', 'ledger-find', 'ledger-dedupe', 'spend-link-rule-list', 'spend-link-sum', 'ledger-diagnosis',
              'stream-list', 'ledger-state', 'ledger-history', 'ledger-plan', 'ledger-plan-project', 'ledger-merchant-kinds', 'ledger-merchant-names')

SOURCE = 'import'
EXPORT_SOURCE = 'csv:banksalad'
H = {'date': '\ub0a0\uc9dc', 'time': '\uc2dc\uac04', 'typ': '\ud0c0\uc785', 'cat': '\ub300\ubd84\ub958', 'sub': '\uc18c\ubd84\ub958', 'merchant': '\ub0b4\uc6a9',
     'amount': '\uae08\uc561', 'currency': '\ud654\ud3d0', 'method': '\uacb0\uc81c\uc218\ub2e8', 'memo': '\uba54\ubaa8'}
KIND_LAYER = {'corporate_card': 'card', 'credit_card': 'card', 'debit_card': 'card', 'account': 'account', 'securities': 'account',
              'savings': 'account', 'cash': 'account', 'easy_pay': 'wallet'}
LAYER_KIND = {'card': 'credit_card', 'account': 'account', 'wallet': 'easy_pay'}
KEPT_KINDS = ('savings', 'securities')
VAULT_WORDS = ('\uae08\uace0',)
PIGGY_WORDS = ('\uc800\uae08\ud1b5',)
WALLET_BRANDS = (('\ub124\uc774\ubc84\ud398\uc774', ('\ub124\uc774\ubc84\ud398\uc774', 'naverpay', '\ub124\uc774\ubc84\ud30c\uc774\ub0b8\uc15c')), ('\uce74\uce74\uc624\ud398\uc774', ('\uce74\uce74\uc624\ud398\uc774', 'kakaopay')),
                 ('\ud1a0\uc2a4\ud398\uc774', ('\ud1a0\uc2a4 \uac04\ud3b8', '\ud1a0\uc2a4\uac04\ud3b8', '\ud1a0\uc2a4\ud398\uc774')), ('\ud398\uc774\ucf54', ('\ud398\uc774\ucf54', 'payco')))
FAMILIES = (('payco', ('\ud398\uc774\ucf54', 'payco')), ('naver', ('\ub124\uc774\ubc84', 'naver')), ('kakao', ('\uce74\uce74\uc624', 'kakao')),
            ('toss', ('\ud1a0\uc2a4', 'toss')), ('ibk', ('ibk', '\uae30\uc5c5')), ('kb', ('kb', '\uad6d\ubbfc')), ('samsung', ('\uc0bc\uc131', 'samsung')),
            ('hyundai', ('\ud604\ub300',)), ('shinhan', ('\uc2e0\ud55c',)), ('woori', ('\uc6b0\ub9ac',)), ('hana', ('\ud558\ub098',)))
PERSON_RX = re.compile('^(\uD1A0\uC2A4|\uD1A0\uBC45|\uCE74\uCE74\uC624|\uB124\uC774\uBC84)?\\s*[\uAC00-\uD7A3*]{2,4}$')
LOAN_WORDS = ('\ub300\ucd9c', '\uc6d0\ub9ac\uae08', '\uc0c1\ud658', '\ub860')
SAVE_WORDS = ('\uc801\uae08', '\uc5f0\uae08', '\uccad\uc57d', '\uc800\ucd95', '\ubaa8\uc73c\uae30', '\ud380\ub4dc', '\uc99d\uad8c', '\ub3c4\uc57d')
TAX_WORDS = ('\uad6d\uc138', '\uc9c0\ubc29\uc138', '\uc138\uae08')
MEAL_POINT = 8000
PAIR_MINUTES = 5
S2_SHARE = 0.03
DUST_WON = LP.DUST_KRW
STREAM_MIN_MONTHS = 3


# ---- small helpers --------------------------------------------------------

def nfkc(s):
    return unicodedata.normalize('NFKC', s or '').strip()


def norm(s):
    return ''.join(ch for ch in nfkc(s).casefold() if ch.isalnum())


def rows(c, sql, args=()):
    cur = c.execute(sql, args)
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r)) for r in cur]


def family(name):
    s = nfkc(name).casefold()
    for fam, words in FAMILIES:
        if any(w in s for w in words):
            return fam
    return ''


def minutes(hm):
    try:
        h, m = hm.split(':')
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def day_add(iso, n):
    return (datetime.date.fromisoformat(iso) + datetime.timedelta(days=n)).isoformat()


def month_of(iso):
    return iso[:7]


def prev_month(ym, n=1):
    y, m = int(ym[:4]), int(ym[5:7])
    t = y * 12 + (m - 1) - n
    return '%04d-%02d' % (t // 12, t % 12 + 1)


def won(n):
    n = int(round(n))
    return '{:,}\uc6d0'.format(n)


def _json_list(text):
    try:
        v = json.loads(text or '[]')
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
    except (ValueError, TypeError):
        pass
    return [x.strip() for x in re.split(r'[|,]', text or '') if x.strip()]


# ---- payment methods: canonical + layer (G-B) ------------------------------

def method_profile(name):
    """-> {kind, layer, wallet (brand name | None), vault, piggy} read from the name alone."""
    s = nfkc(name).casefold()
    vault = any(w in s for w in VAULT_WORDS)
    piggy = any(w in s for w in PIGGY_WORDS)
    wallet = None
    for canon, words in WALLET_BRANDS:
        if any(w in s for w in words):
            wallet = canon
            break
    acct = any(w in s for w in ('\ud1b5\uc7a5', '\uacc4\uc88c', '\uc608\uae08', '\uc785\ucd9c\uae08'))
    kind = None
    if vault or piggy or any(w in s for w in ('\uc801\uae08', '\uccad\uc57d', '\ub3c4\uc57d')):
        kind = 'savings'
    elif '\uc99d\uad8c' in s or 'cma' in s:
        kind = 'securities'
    elif '\uccb4\ud06c' in s and '\uce74\ub4dc' in s:
        kind = 'debit_card'
    elif '\uce74\ub4dc' in s or 'card' in s:
        kind = 'credit_card'
    elif acct:
        kind = 'account'
    elif wallet or any(w in s for w in ('\uac04\ud3b8\uacb0\uc81c', '\ud398\uc774', '\uba38\ub2c8')):
        kind = 'easy_pay'
    elif '\ud604\uae08' in s:
        kind = 'cash'
    else:
        kind = LP.kind_from_name(name) or 'account'
    if kind == 'easy_pay' and wallet is None:
        wallet = nfkc(name)
    return {'kind': kind, 'layer': KIND_LAYER.get(kind, 'account'), 'wallet': wallet if kind == 'easy_pay' else None,
            'vault': vault, 'piggy': piggy}


TWIN_NOISE = ('\uba38\ub2c8', '\uce74\ub4dc', '\ud1b5\uc7a5', '\uacc4\uc88c', '\uccb4\ud06c\uce74\ub4dc', '\uccb4\ud06c', '\uae00\ub85c\ubc8c', 'global', '\uc6b0\ub300', '\uc785\ucd9c\uae08', '\ubcf4\ud1b5\uc608\uae08', '\uc8fc\uac70\ub798')


def twin_core(name):
    s = norm(name)
    for w in sorted(TWIN_NOISE, key=len, reverse=True):
        s = s.replace(norm(w), '')
    return s if len(s) >= 4 else ''


class Methods:
    """The canonical alias graph over pay_method: resolve(raw) -> method row,
    creating the canonical row (and collapsing bank-text variants) as needed."""

    def __init__(self, conn, K=None):
        self.c = conn
        self.K = K
        self.cache = {}
        self.load()

    def load(self):
        self.by_id = {r['id']: r for r in rows(self.c, 'SELECT * FROM pay_method ORDER BY id')}
        self.names = {}
        for r in self.by_id.values():
            self.names.setdefault(nfkc(r['name']), r['id'])
        for r in self.by_id.values():
            for a in _json_list(r['aliases']):
                self.names.setdefault(nfkc(a), r['id'])

    def canon(self, mid):
        seen = set()
        while mid in self.by_id and self.by_id[mid]['canonical_id'] and mid not in seen:
            seen.add(mid)
            mid = self.by_id[mid]['canonical_id']
        return mid

    def _add_alias(self, mid, alias):
        r = self.by_id[mid]
        al = _json_list(r['aliases'])
        if nfkc(alias) not in [nfkc(x) for x in al] and nfkc(alias) != nfkc(r['name']):
            al.append(nfkc(alias))
            self.c.execute('UPDATE pay_method SET aliases=? WHERE id=?', (json.dumps(al, ensure_ascii=False), mid))
            r['aliases'] = json.dumps(al, ensure_ascii=False)
            self.names.setdefault(nfkc(alias), mid)

    def _insert(self, name, prof):
        cur = self.c.execute('INSERT INTO pay_method(name, kind, layer) VALUES(?,?,?)',
                             (name, prof['kind'], prof['layer']))
        self.by_id[cur.lastrowid] = rows(self.c, 'SELECT * FROM pay_method WHERE id=?', (cur.lastrowid,))[0]
        self.names[nfkc(name)] = cur.lastrowid
        if self.K is not None:               # the kit's instrument entity + label, find-first (as method-add does)
            self.K._method_entity_accrue(self.c, name)
        return cur.lastrowid

    def backfill_layers(self):
        """layer for every legacy row; easy-pay variants of one brand collapse into one wallet (L6)."""
        for r in list(self.by_id.values()):
            if r['layer'] is None:
                lay = KIND_LAYER.get(r['kind'] or '') or method_profile(r['name'])['layer']
                self.c.execute('UPDATE pay_method SET layer=? WHERE id=?', (lay, r['id']))
                r['layer'] = lay
        twins = collections.defaultdict(list)
        for r in self.by_id.values():
            core = twin_core(r['name'])
            if core and not r['canonical_id'] and r['layer'] in ('card', 'account'):
                twins[(r['layer'], r['kind'], core)].append(r['id'])
        for ids in twins.values():           # near-duplicate names of one card / account collapse (L6)
            for i in sorted(ids)[1:]:
                self._variant(i, sorted(ids)[0])
        groups = collections.defaultdict(list)
        for r in self.by_id.values():
            if r['layer'] == 'wallet' and not r['canonical_id']:
                prof = method_profile(r['name'])
                if prof['wallet']:
                    groups[prof['wallet']].append(r['id'])
        for brand, ids in groups.items():
            exact = [i for i in ids if nfkc(self.by_id[i]['name']) == brand]
            if len(ids) == 1 and not exact:
                continue
            canon = exact[0] if exact else self._insert(brand, {'kind': 'easy_pay', 'layer': 'wallet'})
            for i in ids:
                if i != canon:
                    self._variant(i, canon)

    def _variant(self, variant_id, canon_id):
        v = self.by_id[variant_id]
        self.c.execute('UPDATE pay_method SET canonical_id=?, dust=0 WHERE id=?', (canon_id, variant_id))
        v['canonical_id'] = canon_id
        self._add_alias(canon_id, v['name'])
        for a in _json_list(v['aliases']):
            self._add_alias(canon_id, a)

    def resolve(self, raw):
        raw = nfkc(raw)
        if raw in self.cache:
            return self.cache[raw]
        mid = self.names.get(raw)
        if mid is None:
            prof = method_profile(raw)
            if prof['wallet']:
                mid = self.names.get(prof['wallet'])
                if mid is None:
                    mid = self._insert(prof['wallet'], prof)
                self._add_alias(self.canon(mid), raw)
            else:
                mid = self._insert(raw, prof)
        mid = self.canon(mid)
        self.cache[raw] = mid
        return mid

    def row(self, mid):
        return self.by_id[mid]


def mark_dust(conn, methods, events):
    """A 5-won piggy bank / a dormant account is never rendered (L6)."""
    big = collections.Counter()
    for e in events:
        if not LP.is_dust(e['amt']):
            big[e['mid']] += 1
    for mid, r in methods.by_id.items():
        if r['canonical_id']:
            continue
        piggy = any(w in nfkc(r['name']) for w in PIGGY_WORDS)
        dust = 1 if (piggy or (mid in {e['mid'] for e in events} and not big[mid])) else r['dust']
        if dust != r['dust']:
            conn.execute('UPDATE pay_method SET dust=? WHERE id=?', (dust, mid))
            r['dust'] = dust


# ---- the export -> events ----------------------------------------------------

def read_export(path, password=None):
    import sheet_read
    if sheet_read.kind(path) == 'csv':
        import csv
        with open(path, encoding='utf-8-sig', newline='') as f:
            reader = csv.DictReader(f)
            table = [(i + 2, row) for i, row in enumerate(reader)]
            names = reader.fieldnames or []
    else:
        got = sheet_read.read(path, [H['date'], H['typ'], H['merchant'], H['amount'], H['method']], password=password,
                              date_field=H['date'], time_field=H['time'])
        if got[0] == 'csv-text':
            import csv
            import io
            reader = csv.DictReader(io.StringIO(got[1], newline=''))
            table = [(i + 2, row) for i, row in enumerate(reader)]
            names = reader.fieldnames or []
        else:
            names, table = got
    need = [H['date'], H['typ'], H['merchant'], H['amount'], H['method']]
    if not set(need) <= set(names):
        raise ValueError('expected Banksalad headers: ' + ', '.join(need))
    return [r for _, r in table if r is not None]


def is_cardbill(e):
    """The export files a card bill under the 'card bill' category (major or minor column), or names it."""
    return '\uce74\ub4dc\ub300\uae08' in (e['cat'], e['sub']) or ('\uce74\ub4dc' in e['merchant'] and '\uacb0\uc81c' in e['merchant'])


def parse_events(raw_rows):
    ev = []
    for i, r in enumerate(raw_rows):
        typ = (r.get(H['typ']) or '').strip()
        if typ not in ('\uc9c0\ucd9c', '\uc218\uc785', '\uc774\uccb4'):
            continue
        try:
            amt = int(float(str(r.get(H['amount'])).replace(',', '').strip()))
        except ValueError:
            continue
        if amt == 0:
            continue
        cur = (r.get(H['currency']) or 'KRW').strip()
        if cur not in ('KRW', '\uc6d0', ''):
            continue
        d = (r.get(H['date']) or '').strip().replace('.', '-').replace('/', '-')[:10]
        if not re.match(r'^\d{4}-\d{2}-\d{2}$', d):
            continue
        cat, sub, merchant = (r.get(H['cat']) or '').strip(), (r.get(H['sub']) or '').strip(), nfkc(r.get(H['merchant']))
        if typ == '\uc9c0\ucd9c' and any(w in merchant for w in LP.RENT) and cat in ('\uae08\uc735', '\uc8fc\uac70/\ud1b5\uc2e0', '\ubbf8\ubd84\ub958', ''):
            cat, sub = '\uc8fc\uac70/\ud1b5\uc2e0', '\uc6d4\uc138'               # one rent category whatever the bank filed it under
        ev.append({'i': i, 'date': d, 'time': (r.get(H['time']) or '').strip()[:5], 'typ': typ,
                   'cat': cat, 'sub': sub,
                   'merchant': merchant, 'amt': amt, 'method_raw': nfkc(r.get(H['method'])),
                   'memo': (r.get(H['memo']) or '').strip()})
    expense_sign = [e['amt'] < 0 for e in ev if e['typ'] == '\uc9c0\ucd9c']
    out_minus = (not expense_sign) or sum(expense_sign) * 2 > len(expense_sign)
    for e in ev:
        # money effect on the owner: out > 0, in < 0 (the sign a spend row stores)
        e['out'] = -e['amt'] if out_minus else e['amt']
        if e['typ'] == '\uc774\uccb4' or e['typ'] == '\uc218\uc785':
            e['out'] = -e['amt']
        if e['typ'] == '\uc774\uccb4' and is_cardbill(e):
            e['out'] = abs(e['amt'])               # a card bill is a payment whatever sign the file gave it
    return ev


# ---- classes (G-B, G-D) -------------------------------------------------------

def _gap(a, b):
    x, y = minutes(a['time']), minutes(b['time'])
    return abs(x - y) if x is not None and y is not None else 0


def _pair_up(group, ok, cost, ev_pair):
    """Greedy pairing of opposite legs inside one bucket."""
    outs = [e for e in group if e['out'] > 0 and not e.get('pair')]
    ins = [e for e in group if e['out'] < 0 and not e.get('pair')]
    cand = sorted(((cost(o, i), id(o), id(i), o, i) for o in outs for i in ins if ok(o, i)), key=lambda t: t[:3])
    for _, _, _, o, i in cand:
        if o.get('pair') or i.get('pair'):
            continue
        ev_pair(o, i)


def topup_wallet(methods, name):
    """A recharge word plus an exact registered wallet/alias is funding."""
    match = re.fullmatch(r'(.+?)\s*(?:충전|top[ -]?up|recharge)(?:\s*내역)?', name.strip(), re.I)
    if not match:
        return None
    mid = methods.names.get(nfkc(match[1].strip()))
    if mid is None:
        return None
    mid = methods.canon(mid)
    return mid if _method_layer(methods.row(mid)) == 'wallet' else None


def classify(events, methods, passthrough_sources):
    """Mutates events: cls, pair (token), store (db kind), and notes the funding graph."""
    layer = lambda e: methods.row(e['mid'])['layer']
    mkind = lambda e: methods.row(e['mid'])['kind']
    tokens = iter(range(1, 10 ** 9))
    funding = collections.defaultdict(lambda: collections.Counter())
    fund_seen = {}

    def link(o, i):
        t = next(tokens)
        o['pair'] = i['pair'] = t

    for e in events:
        e['cls'] = None
    by_day = collections.defaultdict(list)
    for e in events:
        by_day[(e['date'], abs(e['amt']))].append(e)

    # 1. noise: bank "verify" pings of a few won are neither income nor spend
    for e in events:
        if e['typ'] == '\uc774\uccb4' and abs(e['amt']) <= 10:
            e['cls'] = 'own_transfer'

    # 2. own-account moves: the export's own category pairs the two legs
    def own_ok(o, i):
        return o['mid'] != i['mid'] and _gap(o, i) <= 60 and o['cat'] == '\ub0b4\uacc4\uc88c\uc774\uccb4' and i['cat'] == '\ub0b4\uacc4\uc88c\uc774\uccb4'

    for grp in by_day.values():
        _pair_up([e for e in grp if e['typ'] == '\uc774\uccb4' and e['cat'] == '\ub0b4\uacc4\uc88c\uc774\uccb4' and e['cls'] is None], own_ok,
                 _gap, lambda o, i: link(o, i))
    for e in events:
        if e['typ'] != '\uc774\uccb4' or e['cat'] != '\ub0b4\uacc4\uc88c\uc774\uccb4' or e['cls']:
            continue
        pm = None
        if e.get('pair'):
            pm = next(x for x in by_day[(e['date'], abs(e['amt']))] if x.get('pair') == e['pair'] and x is not e)
        src, dst = (e, pm) if e['out'] > 0 else (pm, e)
        if pm is not None and e['out'] < 0:
            continue                        # handled from the out leg
        if pm is not None:
            kept_dst = methods.row(dst['mid'])['kind'] in KEPT_KINDS and not _vault(methods, dst['mid'])
            kept_src = methods.row(src['mid'])['kind'] in KEPT_KINDS and not _vault(methods, src['mid'])
            if dst['mid'] and layer(dst) == 'wallet' and not kept_dst:
                src['cls'] = dst['cls'] = 'topup'
                funding[dst['mid']][(src['mid'], 'account' if layer(src) == 'account' else 'card')] += 1
                fund_seen[(dst['mid'], src['mid'])] = max(fund_seen.get((dst['mid'], src['mid']), ''), e['date'])
            elif kept_dst and not kept_src:
                src['cls'], dst['cls'] = 'saving', 'own_transfer'
            elif kept_src and not kept_dst:
                src['cls'], dst['cls'] = 'own_transfer', 'saving_back'
            else:
                src['cls'] = dst['cls'] = 'own_transfer'
        else:
            # unpaired leg: the other side is an own account the export does not carry
            if '\ucda9\uc804' in e['merchant'] or layer(e) == 'wallet':
                e['cls'] = 'topup'
            elif methods.row(e['mid'])['kind'] in KEPT_KINDS and not _vault(methods, e['mid']):
                e['cls'] = 'saving' if e['out'] < 0 else 'saving_back'
            else:
                e['cls'] = 'own_transfer'
    for e in events:                          # a paired leg whose partner was classified
        if e['typ'] == '\uc774\uccb4' and e['cat'] == '\ub0b4\uacc4\uc88c\uc774\uccb4' and not e['cls']:
            e['cls'] = 'own_transfer'

    # Named registered wallet funding is neutral even without an imported
    # own-transfer category or a second exported leg.
    for e in events:
        wallet = topup_wallet(methods, e['merchant'])
        if e['cls'] is None and e['out'] > 0 and wallet is not None and wallet != e['mid']:
            e['cls'] = 'topup'
            funding[wallet][(e['mid'], 'card' if layer(e) == 'card' else 'account')] += 1
            fund_seen[(wallet, e['mid'])] = max(fund_seen.get((wallet, e['mid']), ''), e['date'])

    # 3. card bills, tax, pass-through (corporate card settled the same day)
    for e in events:
        if e['typ'] != '\uc774\uccb4' or e['cls'] or not is_cardbill(e):
            continue
        m = e['merchant']
        if any(w in m for w in TAX_WORDS):
            e['cls'] = 'spend'
            e['tax'] = True
        elif '\uce74\ub4dc' in m or any(w in m for w in ('\uc0bc\uc131', '\ud604\ub300', '\uc2e0\ud55c', '\uad6d\ubbfc', '\ub86f\ub370', '\ud558\ub098', '\uc6b0\ub9ac')) and e['out'] > 0:
            e['cls'] = 'card_bill'
        else:
            e['cls'] = 'spend'                      # a payment to a person filed as a card bill

    def pt_ok(o, i):
        return (o['cls'] == 'card_bill' and o['mid'] == i['mid'] and abs((datetime.date.fromisoformat(o['date'])
                - datetime.date.fromisoformat(i['date'])).days) <= 1 and not PERSON_RX.match(i['merchant'])
                and i['typ'] in ('\uc774\uccb4', '\uc218\uc785') and i['cat'] not in ('\ub0b4\uacc4\uc88c\uc774\uccb4',))

    amt_groups = collections.defaultdict(list)
    for e in events:
        if e['typ'] in ('\uc774\uccb4', '\uc218\uc785') and not e.get('pair') and abs(e['amt']) > 10:
            amt_groups[abs(e['amt'])].append(e)
    for grp in amt_groups.values():
        outs = [e for e in grp if e['out'] > 0 and e['cls'] == 'card_bill']
        ins = [e for e in grp if e['out'] < 0 and not e['cls']]
        for o in outs:
            for i in ins:
                if not i.get('pair') and not o.get('pair') and pt_ok(o, i):
                    link(o, i)
                    o['cls'] = i['cls'] = 'pass_through'
                    passthrough_sources.add(norm(i['merchant']))
    # a source learned earlier (same payer) is silent the next month: an inflow from a known
    # pass-through payer whose settlement is not in the file yet stays a pass-through
    for e in events:
        if e['cls'] is None and e['typ'] in ('\uc774\uccb4', '\uc218\uc785') and e['out'] < 0 and \
                norm(e['merchant']) in passthrough_sources and e['cat'] in ('\uc800\ucd95', '\ubbf8\ubd84\ub958', '\uc774\uccb4'):
            e['cls'] = 'pass_through'

    # 5. employer meal points through the easy-pay wallet (D5): the points are not spend
    for e in events:
        if e['typ'] == '\uc9c0\ucd9c' and e['cls'] is None and abs(e['amt']) == MEAL_POINT and \
                method_profile(methods.row(e['mid'])['name'])['wallet'] == '\ud398\uc774\ucf54':
            e['cls'] = 'meal_point'

    # 4. a wallet payment and the card/account row that funds it are ONE spend (L5)
    spend_rows = [e for e in events if e['typ'] == '\uc9c0\ucd9c' and e['cls'] is None]
    by_amt = collections.defaultdict(list)
    for e in spend_rows:
        by_amt[(e['date'], e['out'])].append(e)
    for grp in by_amt.values():
        wall = [e for e in grp if layer(e) == 'wallet']
        fund = [e for e in grp if layer(e) != 'wallet']
        for w in wall:
            best = None
            for f in fund:
                if f.get('pair') or w.get('pair') or f['mid'] == w['mid']:
                    continue
                if minutes(w['time']) is None or minutes(f['time']) is None:
                    continue
                g = _gap(w, f)
                if g <= PAIR_MINUTES and (best is None or g < best[0]):
                    best = (g, f)
            if best:
                f = best[1]
                link(w, f)
                f['cls'] = 'own_transfer'
                f['funding_of'] = w['mid']
                funding[w['mid']][(f['mid'], 'card' if layer(f) == 'card' else 'account')] += 1
                fund_seen[(w['mid'], f['mid'])] = max(fund_seen.get((w['mid'], f['mid']), ''), w['date'])

    # 6. the rest
    for e in events:
        if e['cls']:
            continue
        m, cat, sub = e['merchant'], e['cat'], e['sub']
        if e['typ'] == '\uc9c0\ucd9c':
            if cat == '\uae08\uc735' and sub == '\uc774\uc790/\ub300\ucd9c':
                e['cls'] = 'loan'
            else:
                e['cls'] = 'spend'
        elif e['typ'] == '\uc218\uc785':
            e['cls'] = 'income'
        else:                                         # a plain transfer (the export's 'transfer' type)
            if e['out'] > 0:
                from ledger_money_flow import registered
                if registered(methods, m).get('kind') == 'savings' and layer(e) != 'card':
                    e['cls'] = 'saving'
                else:
                    e['cls'] = 'spend'                # money sent to a person / a business: the owner's spend
                    e['remit'] = True
            else:
                if any(w in m for w in LOAN_WORDS) and abs(e['amt']) >= 1000000:
                    e['cls'] = 'own_transfer'         # a loan paid out to the account is not income
                elif any(norm(x) in norm(m) for x in LP.EXCHANGE) or (mkind(e) in ('savings', 'securities') and not _vault(methods, e['mid'])):
                    e['cls'] = 'saving_back'
                else:
                    e['cls'] = 'income'
    from ledger_money_flow import saving_destination, investment_destination
    for e in events:
        partner = next((p for p in by_day[(e['date'], abs(e['amt']))]
                        if p is not e and e.get('pair') and p.get('pair') == e['pair']), None)
        def money_row(ev):
            return dict(method=methods.row(ev['mid'])['name'], name=ev['merchant'],
                        kind='transfer', amount=-ev['out']) if ev else None
        row, other = money_row(e), money_row(partner)
        if (not partner and e['typ'] == '\uc774\uccb4' and abs(e['amt']) > 10
                and mkind(e) == 'savings' and not _vault(methods, e['mid'])
                and e['cls'] in ('spend','income','saving','saving_back')):
            e['cls'] = 'saving' if e['out'] < 0 else 'saving_back'
        destination = saving_destination(methods, row, other)
        if e['out'] > 0 and e['cls'] == 'spend' and destination:
            e['cls'] = 'saving'
        if e['cls'] == 'saving' and not destination:
            e['cls'] = 'spend' if e['out'] > 0 else 'own_transfer'
        if (e['out'] > 0 and e['cls'] in ('spend', 'own_transfer')
                and investment_destination(methods, row, other)):
            e['cls'] = 'spend'
    for e in events:
        c = e['cls']
        e['store'] = ('transfer' if c == 'saving' and e['out'] < 0 else
                      'expense' if c in ('spend', 'saving', 'loan') else 'income' if c == 'income' else 'transfer')
    # Personal deposits are unresolved transfers, not the owner's account
    # moves and not confirmed earnings. Import and replay share the same test.
    from ledger_deposits import own_sender, unknown_receipt
    known_people = {norm(n) for p in rows(methods.c, 'SELECT name,display_name,aliases FROM persons')
                    for n in [p['name'], p['display_name']] + _json_list(p['aliases']) if n}
    for e in events:
        if (e['typ'] == '이체' and e['out'] < 0 and not e.get('pair')
                and e['cls'] in (None, 'income', 'own_transfer')):
            personal = PERSON_RX.fullmatch(e['merchant']) or norm(e['merchant']) in known_people
            if own_sender(methods.c, e['merchant']):
                e['cls'], e['store'] = 'own_transfer', 'transfer'
            elif (personal or unknown_receipt(e['merchant'], e['cat'])) and e['cls'] in ('income', 'own_transfer'):
                e['cls'], e['store'] = None, 'transfer'
    return funding, fund_seen


def _vault(methods, mid):
    return any(w in nfkc(methods.row(mid)['name']) for w in VAULT_WORDS)


# ---- identity + bucket reconcile (G-A) -----------------------------------------

def import_key(e, n):
    key = '|'.join((e['date'], e['time'], str(e['amt']), norm(e['merchant']), nfkc(e['method_raw'])))
    return 'bs:' + hashlib.sha256(key.encode()).hexdigest()[:24] + ':' + str(n)


def db_amount(e):
    """The signed amount a spend_entry row stores: expense > 0 (refund < 0), income > 0,
    transfer signed (in > 0, out < 0)."""
    if e['store'] == 'expense':
        return e['out']
    if e['store'] == 'income':
        return -e['out']
    return -e['out']


def effect(row):
    """money effect on the owner of an existing row: out > 0."""
    if row['kind'] == 'income':
        return -row['amount']
    if row['kind'] == 'transfer':
        return -row['amount']
    return row['amount']


def reconcile(conn, events, methods):
    """-> (plan list of (event, existing_id|None), stats). Multiset per (date, effect, family);
    a second pass takes the leftovers on (date, effect) with a merchant or family match."""
    if not events:
        return [], {}
    lo, hi = min(e['date'] for e in events), max(e['date'] for e in events)
    pool = rows(conn, 'SELECT id, date, amount, kind, name, method FROM spend_entry '
                      'WHERE import_key IS NULL AND date BETWEEN ? AND ?', (lo, hi))
    bucket = collections.defaultdict(list)
    for r in pool:
        bucket[(r['date'], effect(r))].append(r)
    taken = set()
    plan, left = [], []
    mfam = {mid: family(methods.row(mid)['name']) for mid in {e['mid'] for e in events}}

    def score(e, r):
        s = 0
        rf = family(r['method'] or '')
        if rf and rf == mfam[e['mid']]:
            s += 3
        sim = difflib.SequenceMatcher(None, norm(e['merchant']), norm(r['name'] or '')).ratio()
        if sim >= 0.6:
            s += 2
        elif sim >= 0.35:
            s += 1
        return s

    cand = []
    for e in events:
        for r in bucket.get((e['date'], e['out']), ()):
            s = score(e, r)
            if s >= 2 or (s >= 1 and not (r['method'] or '')):
                cand.append((-s, e['i'], r['id'], e, r))
    cand.sort(key=lambda t: t[:3])
    matched = {}
    for _, _, rid, e, r in cand:
        if e['i'] in matched or rid in taken:
            continue
        matched[e['i']] = r
        taken.add(rid)
    attached = 0
    for e in events:
        r = matched.get(e['i'])
        plan.append((e, r['id'] if r else None))
        attached += 1 if r else 0
    return plan, {'attached': attached, 'new': len(events) - attached}


# ---- landing ---------------------------------------------------------------------

CATMAP = None


def catmap():
    global CATMAP
    if CATMAP is None:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ledger_category_map.json'),
                  encoding='utf-8') as f:
            CATMAP = json.load(f)
    return CATMAP


def kit_category(e):
    m = catmap()
    from ledger_actual import category
    merchant = category(e.get('name') or e.get('merchant'))
    if merchant:
        return list(merchant)
    from ledger_diag import is_transit
    if is_transit(e.get('name') or e.get('merchant')):
        return m['major']['교통']
    if e.get('tax'):
        return m['sub']['\uae08\uc735/\uc138\uae08/\uacfc\ud0dc\ub8cc']
    pair = m['sub'].get('%s/%s' % (e['cat'], e['sub'])) or m['major'].get(e['cat']) or m['major']['*']
    return pair


LEGACY_CATEGORY_LABELS = {'식생활': '식비', '이동': '교통', '주거/인프라': '주거', '콘텐츠/취미': '콘텐츠와 취미'}


def _cat_id(conn, key, display):
    labels = [display] + [old for old, new in LEGACY_CATEGORY_LABELS.items() if new == display]
    marks = ','.join('?' for _ in labels)
    got = conn.execute('SELECT id FROM spend_category WHERE name=? OR name IN (' + marks + ') OR display_name IN (' + marks + ') ORDER BY name=? DESC,id LIMIT 1', (key, *labels, *labels, key)).fetchone()
    if got:
        conn.execute('UPDATE spend_category SET display_name=? WHERE id=?', (display, got[0]))
        return got[0]
    cur = conn.execute('INSERT INTO spend_category(name, display_name) VALUES(?,?)', (key, display))
    return cur.lastrowid


def reconcile_categories(conn):
    """Refresh vocabulary and clear kit-inferred fixed telecom on transit rows."""
    from ledger_diag import is_transit
    mapping = catmap()
    labels = dict(mapping['major'].values())
    labels.update(dict(mapping['sub'].values()))
    for row in conn.execute('SELECT id,name,display_name FROM spend_category').fetchall():
        label = labels.get(row[1]) or LEGACY_CATEGORY_LABELS.get(row[2]) or LEGACY_CATEGORY_LABELS.get(row[1])
        if label:
            conn.execute('UPDATE spend_category SET display_name=? WHERE id=?', (label, row[0]))
    for sid, name in conn.execute("SELECT id,name FROM spend_entry WHERE kind='expense' AND COALESCE(class,'spend')='spend'"):
        from ledger_actual import category
        merchant = category(name)
        if merchant:
            cid = _cat_id(conn, *merchant)
            conn.execute('UPDATE spend_entry SET category_id=? WHERE id=?', (cid, sid))
        elif is_transit(name):
            cid = _cat_id(conn, *mapping['major']['교통'])
            conn.execute('UPDATE spend_entry SET category_id=?,is_fixed=0,stream_id=NULL WHERE id=?', (cid, sid))


def _next_id(conn):
    floor = conn.execute("SELECT value FROM ledger_guard WHERE guard_key='spend_id_floor' AND scope='global'").fetchone()
    return max(conn.execute('SELECT COALESCE(MAX(id),0) FROM spend_entry').fetchone()[0], int(floor[0] or 0) if floor else 0) + 1


def land(K, conn, plan, methods, pair_base):
    """Insert the new rows; stamp the attached ones. Returns counts."""
    nk, now_new = 0, 0
    pair_ids = {}
    cats = {}
    ids = {}
    from ledger_review import annotations
    owner_facts = annotations(conn)
    for e, rid in plan:
        e['mname'] = methods.row(e['mid'])['name']
        e['scope'] = methods.row(e['mid'])['scope_default'] or 'personal'
    for e, rid in plan:
        pid = None
        if e.get('pair'):
            pid = pair_ids.setdefault(e['pair'], pair_base + len(pair_ids) + 1)
        dbamt = db_amount(e)
        ck = kit_category(e) if e['store'] == 'expense' else None
        cid = None
        if ck:
            cid = cats.get(ck[0]) or cats.setdefault(ck[0], _cat_id(conn, ck[0], ck[1]))
        name = e['merchant'] or e['sub'] or e['cat'] or '-'
        src_cat = '%s/%s' % (e['cat'], e['sub']) if e['sub'] else e['cat']
        at = e['time'] if re.match(r'^[0-2]\d:[0-5]\d$', e['time'] or '') else None
        if rid is not None:
            fact = owner_facts.get(rid,{})
            if any(fact.get(k) for k in ('owner_class','allocation_mode','spend_id','debt_direction','salary_evidence','deposit_reviewed','insurance_receipt','history_reviewed')):
                # Attaching another source cannot undo an owner correction
                # or release an already allocated receipt through the raw
                # import's kind/class/pair update triggers.
                conn.execute('UPDATE spend_entry SET import_key=?,at_time=COALESCE(at_time,?),src_cat=COALESCE(src_cat,?) WHERE id=?',
                             (e['key'],at,src_cat,rid))
                ids[e['i']] = rid
                nk += 1
                continue
            sets = ['class=?', 'pair_id=?', 'import_key=?', 'at_time=COALESCE(at_time,?)', 'src_cat=COALESCE(src_cat,?)']
            args = [e['cls'], pid, e['key'], at, src_cat]
            old = rows(conn, 'SELECT kind, amount FROM spend_entry WHERE id=?', (rid,))[0]
            if e['store'] != old['kind']:         # a legacy row the new classes reclassify (top-up booked as spend)
                sets += ['kind=?', 'amount=?']
                args += [e['store'], dbamt]
                if e['store'] == 'transfer':
                    K._spend_shadow_del(conn, rid)
                    conn.execute("DELETE FROM record WHERE source_table='spend_entry' AND source_id=?", (rid,))
                else:
                    K._spend_write_v32(conn, rid, e['date'], name, dbamt, e['store'], e['mname'])
            if e['store'] == 'expense' and cid:
                sets.append('category_id=COALESCE(category_id,?)')
                args.append(cid)
            conn.execute('UPDATE spend_entry SET %s WHERE id=?' % ', '.join(sets), args + [rid])
            ids[e['i']] = rid
            nk += 1
            continue
        if e['store'] == 'transfer':
            sid = _next_id(conn)
            conn.execute('INSERT INTO spend_entry(id, date, amount, name, category_id, method, is_fixed, scope, source, '
                         'kind, class, pair_id, import_key, at_time, src_cat) VALUES(?,?,?,?,?,?,0,?,?,?,?,?,?,?,?)',
                         (sid, e['date'], dbamt, name, None, e['mname'], e['scope'], SOURCE, 'transfer', e['cls'], pid,
                          e['key'], at, src_cat))
        else:
            sid = K.spend_add(e['date'], dbamt, name, category=None, method=e['mname'], scope=e['scope'],
                              source=SOURCE, kind=e['store'], conn=conn, commit=False, match_captures=False)
            conn.execute('UPDATE spend_entry SET category_id=?, class=?, pair_id=?, import_key=?, at_time=?, src_cat=? '
                         'WHERE id=?', (cid, e['cls'], pid, e['key'], at, src_cat, sid))
        import shop_payment
        shop_payment.match(K, conn, sid)
        ids[e['i']] = sid
        now_new += 1
    return {'new': now_new, 'attached': nk, 'ids': ids}


# ---- funding graph (G-B) ------------------------------------------------------------

def save_funding(conn, methods, funding, seen):
    for wid, cnt in funding.items():
        if not cnt:
            continue
        top = cnt.most_common(1)[0][0]
        for (sid, sub), n in cnt.items():
            sid = methods.canon(sid)
            if sid == wid:
                continue
            first = seen.get((wid, sid), '')
            row = conn.execute('SELECT 1 FROM pay_funding WHERE wallet_id=? AND source_id=? AND sub=?',
                               (wid, sid, sub)).fetchone()
            if row:
                conn.execute('UPDATE pay_funding SET last_seen=MAX(COALESCE(last_seen,\'\'),?) WHERE wallet_id=? '
                             'AND source_id=? AND sub=?', (first, wid, sid, sub))
            else:
                has_default = conn.execute('SELECT 1 FROM pay_funding WHERE wallet_id=? AND is_default=1', (wid,)).fetchone()
                conn.execute('INSERT INTO pay_funding(wallet_id, source_id, sub, is_default, first_seen, last_seen) '
                             'VALUES(?,?,?,?,?,?)', (wid, sid, sub, 0 if has_default or (sid, sub) != top else 1, first, first))


# ---- the import verb ----------------------------------------------------------------------

def run_import(K, conn, path, password=None, today=None, since=None, until=None):
    raw = read_export(path, password)
    events = parse_events(raw)
    if since or until:
        if since:
            datetime.date.fromisoformat(since)
        if until:
            datetime.date.fromisoformat(until)
        events = [e for e in events if (not since or e['date'] >= since)
                  and (not until or e['date'] <= until)]
    if not events:
        raise ValueError('the file has no payments')
    K.spend_category_rekey(conn)
    methods = Methods(conn, K)
    methods.backfill_layers()
    seen = {r[0] for r in conn.execute('SELECT import_key FROM spend_entry WHERE import_key IS NOT NULL')}
    for e in events:
        e['mid'] = methods.resolve(e['method_raw'])
    mark_dust(conn, methods, events)
    pt = {r[0] for r in conn.execute("SELECT item FROM ledger_preference WHERE topic='passthrough_source' AND status='active'")}
    pt = {x[len('src:'):] if x.startswith('src:') else x for x in pt}
    funding, fseen = classify(events, methods, pt)
    # same file twice, or an overlapping re-export: the key is the identity (G-A)
    occ = collections.Counter()
    fresh = []
    for e in events:
        base = import_key(e, 0)[:-2]
        occ[base] += 1
        e['key'] = base + ':' + str(occ[base])
        if e['key'] not in seen:
            fresh.append(e)
    skipped = len(events) - len(fresh)
    plan, rstat = reconcile(conn, fresh, methods)
    pair_base = conn.execute('SELECT COALESCE(MAX(pair_id),0) FROM spend_entry').fetchone()[0]
    stat = land(K, conn, plan, methods, pair_base)
    import ledger_history
    history_repairs = ledger_history.repair(K, conn)
    save_funding(conn, methods, funding, fseen)
    for src in pt:
        if not conn.execute("SELECT 1 FROM ledger_preference WHERE topic='passthrough_source' AND item=?", ('src:' + src,)).fetchone():
            conn.execute("INSERT INTO ledger_preference(ulid, item, polarity, note, status, source, recorded_at, topic) "
                         "VALUES(?,?,?,?,?,?,?,?)", ('pt-' + hashlib.sha1(src.encode()).hexdigest()[:16], 'src:' + src,
                                                      'negotiable', 'pass-through payer', 'active', 'ledger_v6',
                                                      datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'), 'passthrough_source'))
    # the old pending blend is superseded: the file's evidence is now landed rows (G-A, L17)
    conn.execute("UPDATE intake_item SET state='excluded', reason='v6_landed' WHERE state='pending' AND source=? "
                 "AND target='block'", (EXPORT_SOURCE,))
    # a landed row shows up through ledger-diag; streams read the landed rows only
    import ledger_diag as D
    streams = D.stream_reconcile(conn, month=until[:7] if until else None, today=today,
                                 short_history=True)
    D.apply_link_rules(conn)
    summary = {'events': len(events), 'new': stat['new'], 'attached': stat['attached'], 'same': skipped,
               'first': min(e['date'] for e in events), 'last': max(e['date'] for e in events),
               'classes': dict(collections.Counter(e['cls'] or 'unclassified' for e in events)),
               'methods': D.method_count(conn), 'streams': streams['count']}
    import ledger_split
    summary['settlement'] = ledger_split.sweep(conn, summary['first'], summary['last'])
    summary['history_repairs'] = history_repairs
    # The existing service still owns its unresolved-person questions. The
    # redesigned history reader opts into holding unmatched reimbursements
    # through ledger-history-reconcile; do not strand the legacy pair UI.
    summary['history_settlement'] = ledger_history.match_history(conn, K=K, hold_unmatched=False)
    import ledger_installment
    summary['billed_installments'] = ledger_installment.match_billed_parts(conn)
    return summary


# ---- find / reset / dedupe ------------------------------------------------------------------------

def find(conn, text, month=None, raw=False, path=None, password=None, limit=40):
    t = norm(text)
    out = {'ledger': [], 'streams': []}
    if not t:
        raise ValueError('give a word to look for')
    for r in rows(conn, "SELECT id, date, amount, name, method, kind, class, src_cat FROM spend_entry "
                        "WHERE (? IS NULL OR substr(date,1,7)=?) ORDER BY date DESC, id DESC", (month, month)):
        if t in norm(r['name']) or t in norm(r['method'] or '') or t in norm(r['src_cat'] or ''):
            out['ledger'].append({'id': r['id'], 'date': r['date'], 'amount': r['amount'], 'name': r['name'],
                                  'method': r['method'], 'kind': r['kind'], 'class': r['class'] or 'spend'})
    out['ledger_count'] = len(out['ledger'])
    out['ledger'] = out['ledger'][:limit]
    for r in rows(conn, "SELECT f.id, f.name, f.amount, f.current_amount, f.state, f.class, f.last_seen, f.keep_class "
                        "FROM fixed_cost f"):
        keys = [k[0] for k in conn.execute('SELECT key FROM fixed_cost_key WHERE fixed_id=?', (r['id'],))]
        if t in norm(r['name']) or any(t in k for k in keys):
            out['streams'].append(r)
    if raw and path:
        got = [e for e in parse_events(read_export(path, password)) if t in norm(e['merchant']) or t in norm(e['method_raw'])]
        out['file'] = [{'date': e['date'], 'time': e['time'], 'type': e['typ'], 'merchant': e['merchant'],
                        'amount': e['amt'], 'method': e['method_raw']} for e in got[:limit]]
    return out


def dedupe(conn):
    """Proof, not a fix: any (date, signed amount, method, merchant) bucket holding two rows with the same import
    key family, and any import_key held twice."""
    dup_keys = rows(conn, 'SELECT import_key, COUNT(*) n FROM spend_entry WHERE import_key IS NOT NULL '
                          'GROUP BY source, import_key HAVING n > 1')
    pairs = rows(conn, "SELECT date, amount, kind, method, name, COUNT(*) n, GROUP_CONCAT(source) srcs "
                       "FROM spend_entry WHERE class IS NOT NULL GROUP BY date, amount, kind, method, name "
                       "HAVING n > 1 AND COUNT(DISTINCT source) > 1")
    return {'duplicate_keys': dup_keys, 'cross_source_twins': pairs, 'ok': not dup_keys and not pairs}


def reset(K, conn, dry_run, preserve_preferences=False):
    import intake
    live = conn.execute('SELECT COUNT(*) FROM spend_entry').fetchone()[0]
    plan = {'spend_rows': live, 'streams': conn.execute('SELECT COUNT(*) FROM fixed_cost').fetchone()[0],
            'methods': conn.execute('SELECT COUNT(*) FROM pay_method').fetchone()[0],
            'keeps': ['ledger_goal', 'ledger_preference', 'ledger_constraint']}
    if dry_run:
        return dict(plan, status='dry_run')
    res = intake.archive(K, conn, live)
    conn.execute("DELETE FROM intake_item WHERE source IN (SELECT key FROM intake_source) "
                 "AND id NOT IN (SELECT item_id FROM intake_link)")
    conn.execute('DELETE FROM pay_funding')
    conn.execute('DELETE FROM fixed_cost_key')
    conn.execute('DELETE FROM fixed_cost')
    conn.execute('DELETE FROM spend_link')
    conn.execute('UPDATE pay_method SET linked_account_id=NULL')
    conn.execute('DELETE FROM pay_method')
    if not preserve_preferences:
        conn.execute("DELETE FROM ledger_preference WHERE topic IN ('passthrough_source','ledger_fixed_alias',"
                     "'ledger_merchant_merge','ledger_category_fix','ledger_fixed_hint','ledger_review_item','pay_funding_source',"
                     "'pay_history_coverage','pay_spend_context','pay_benefit_terms')")
        import ledger_plan
        ledger_plan.reset(conn)
    return dict(plan, status='reset', archive=res.get('archive'))


ONBOARDING_STATE = ('files', 'ledger', 'onboarding.json')

# The ledger goal writer (ledgerlib/kit.py) uses spend. Keep the legacy
# spending spelling too. Preference ownership comes from the database ledger_*
# writers and ledgerlib/kit.py, including topics without a ledger_ prefix.
LEDGER_GOAL_DOMAINS = ('spend', 'spending')
LEDGER_PREFERENCE_TOPICS = (
    'ledger_watch', 'ledger_method_role', 'ledger_method_memo', 'ledger_meal_voucher',
    'ledger_fixed_transfer', 'ledger_fixed_purpose', 'ledger_fixed_alias',
    'ledger_merchant_merge', 'ledger_category_fix', 'ledger_fixed_hint',
    'ledger_payday', 'ledger_gap', 'ledger_plan_fact', 'ledger_merchant_kind',
    'ledger_merchant_category', 'ledger_budget', 'ledger_review_item',
    'passthrough_source', 'pay_funding_source', 'pay_history_coverage',
    'pay_spend_context', 'pay_benefit_terms', 'salary_bank', 'stream_stop_words',
    'insurance_claim', 'card_statement',
)


def restart(K, conn, dry_run):
    """"Start over" as ONE step (2026-10-23): ledger-reset, plus the module's onboarding state file -- the
    recording-from date lives there, so a reset alone left it standing and the flow believed it was mid-recording.
    The file goes to the same kit archive dir as the spend rows (content-addressed, copy first, then removed).
    Active spend/spending goals and ledger-owned preferences are retired, preserving their rows and health state.
    Refuses when there is nothing to restart; a rerun after a half-done restart finishes the missing half."""
    from pathlib import Path
    root = Path(K.DB_PATH).absolute().parent.parent
    state = root.joinpath(*ONBOARDING_STATE)
    has_state = state.is_file()
    conf_changes = []
    for name in ('kit_life.conf', 'pack_ledger.conf'):
        path = root / 'config' / name
        if not path.is_file():
            continue
        body = path.read_bytes()
        kept = [line for line in body.splitlines(keepends=True)
                if not re.match(rb'^\s*(?:export\s+)?LEDGER_SPEND_APP\s*=', line)]
        clean = b''.join(kept)
        if clean != body:
            conf_changes.append((path, body, clean))
    # Restart bypasses reset's legacy preference deletion and uses the existing
    # retirement paths, preserving rows and their history.
    goals = rows(conn, "SELECT * FROM ledger_goal WHERE status='active' AND domain IN (?,?)",
                 LEDGER_GOAL_DOMAINS)
    placeholders = ','.join('?' for _ in LEDGER_PREFERENCE_TOPICS)
    preferences = rows(conn, "SELECT id FROM ledger_preference WHERE status='active' "
                       "AND (topic IN (" + placeholders + ") OR source='ledger_plan_fact')",
                       LEDGER_PREFERENCE_TOPICS)
    plan = dict(reset(K, conn, True), onboarding_state=has_state,
                onboarding_conf=[str(p) for p, body, clean in conf_changes],
                retire_goals=len(goals), retire_preferences=len(preferences),
                keeps=['ledger_constraint'])
    if dry_run:
        return dict(plan, status='dry_run')
    if not plan['spend_rows'] and not (
            has_state or conf_changes or goals or preferences):
        raise ValueError('nothing to restart: no spend rows, onboarding state, or active ledger goals/preferences')
    state_archive = None
    if has_state:
        body = state.read_text(encoding='utf-8')
        arch = root / 'files' / '_archive'
        arch.mkdir(parents=True, exist_ok=True)
        state_archive = arch / ('ledger-onboarding-' + hashlib.sha256(body.encode()).hexdigest() + '.json')
        tmp = state_archive.with_suffix('.tmp')
        tmp.write_text(body, encoding='utf-8')
        os.replace(tmp, state_archive)
    conf_archives = []
    for path, body, clean in conf_changes:
        arch = root / 'files' / '_archive'
        arch.mkdir(parents=True, exist_ok=True)
        backup = arch / ('ledger-onboarding-' + path.name + '-' + hashlib.sha256(body).hexdigest() + '.bak')
        tmp = backup.with_suffix('.tmp')
        tmp.write_bytes(body)
        os.replace(tmp, backup)
        conf_archives.append(str(backup))
    import consult_anchor
    # Capture reset's recovery snapshot before changing goals or preferences.
    res = reset(K, conn, False, preserve_preferences=True)
    for goal in goals:
        K._goal_archive_row(conn, goal, 'ledger restart')
    for preference in preferences:
        consult_anchor.preference_retire(conn, preference['id'])
    for path, body, clean in conf_changes:
        tmp = path.with_suffix('.tmp')
        tmp.write_bytes(clean)
        os.replace(tmp, path)
    if has_state:
        state.unlink()
    return dict(res, status='restarted', keeps=plan['keeps'],
                retire_goals=len(goals), retire_preferences=len(preferences), onboarding_state=has_state,
                onboarding_archive=str(state_archive) if state_archive else None,
                onboarding_conf=[str(p) for p, body, clean in conf_changes], onboarding_conf_archives=conf_archives)


# ---- funding / scope verbs --------------------------------------------------------------------------------

def _existing_method(conn, ref):
    """Resolve an existing ID/name/alias without accruing aliases or methods."""
    m = Methods(conn)
    text = str(ref)
    mid = int(text) if text.isdecimal() and int(text) in m.by_id else m.names.get(nfkc(text))
    if mid is None:
        raise ValueError('unknown pay method: %s' % ref)
    return m.row(m.canon(mid))


def _method_layer(method):
    return method['layer'] or KIND_LAYER.get(method['kind'])


def _paying_account(conn, method):
    if method['linked_account_id']:
        account = _existing_method(conn, method['linked_account_id'])
        return account if _method_layer(account) == 'account' else None
    accounts = rows(conn, "SELECT m.* FROM pay_funding f JOIN pay_method m ON m.id=f.source_id "
                         "WHERE f.wallet_id=? AND (m.layer='account' OR "
                         "(m.layer IS NULL AND m.kind IN ('account','savings','securities','cash'))) "
                         "ORDER BY f.is_default DESC", (method['id'],))
    if accounts and (len(accounts) == 1 or conn.execute(
            'SELECT 1 FROM pay_funding WHERE wallet_id=? AND source_id=? AND is_default=1',
            (method['id'], accounts[0]['id'])).fetchone()):
        return _existing_method(conn, accounts[0]['id'])
    if method['kind'] in ('credit_card', 'corporate_card'):
        # Same settlement evidence as the roster: exact names/aliases first,
        # issuer family only when there is a single credit card in that family.
        m = Methods(conn)
        names = {method['name'], *_json_list(method['aliases'])}
        issuer = family(method['name'])
        unique = issuer and sum(family(r['name']) == issuer for r in m.by_id.values()
                                if r['kind'] in ('credit_card', 'corporate_card') and not r['canonical_id']) == 1
        for bill in rows(conn, "SELECT name,method FROM spend_entry WHERE class='card_bill' ORDER BY date DESC,id DESC"):
            if bill['name'] in names or (unique and family(bill['name']) == issuer):
                mid = m.names.get(nfkc(bill['method']))
                account = m.row(m.canon(mid)) if mid is not None else None
                return account if account and _method_layer(account) == 'account' else None
    return None


def _purpose(conn, method, instrument_facts=None):
    from ledger_purpose_labels import owner_purpose, VOCABULARY
    normalized = owner_purpose(conn, method)
    own = json.loads(method['purpose_tags']) if method['purpose_tags'] else None
    if not own and normalized['tags']:
        own = normalized['tags']
    account = _paying_account(conn, method) if not own and _method_layer(method) == 'card' else None
    inherited = account if account and account['purpose_tags'] else None
    basis = inherited or method
    if instrument_facts is None:
        from ledger_instrument_facts import records
        instrument_facts = records(conn)
    return dict(id=method['id'], name=method['name'], layer=_method_layer(method),
                instrument_facts=instrument_facts.get(method['id']),
                own_tags=own, own_memo=method['purpose_memo'], own_origin=method['purpose_origin'],
                tags=own if basis is method else json.loads(basis['purpose_tags']),
                memo=method['purpose_memo'] or (basis['purpose_memo'] if inherited else None),
                origin=basis['purpose_origin'] or ('owner-stated' if own else None),
                normalized=normalized,
                inherited_from=dict(id=inherited['id'], name=inherited['name']) if inherited else None,
                vocabulary=list(VOCABULARY))


def purpose_get(conn, method=None):
    """Read-only roster SDK: None lists canonical methods; tags=None means no purpose."""
    from ledger_instrument_facts import records
    facts = records(conn)
    if method is not None:
        return _purpose(conn, _existing_method(conn, method), facts)
    return [_purpose(conn, r, facts) for r in rows(conn, 'SELECT * FROM pay_method WHERE canonical_id IS NULL ORDER BY id')]


def purpose_set(K, conn, method, tags, memo='', origin='owner-stated'):
    """Replace own tags/memo. Empty tags store NULL and restore card inheritance.

    SDK tags accept a sequence or the CLI's comma-separated fixed labels.
    Owner words go in memo and never expand the vocabulary.
    The caller owns the transaction, as for funding_set.
    """
    if origin not in ('owner-stated', 'learned', 'user'):
        raise ValueError('purpose origin is owner-stated, learned or user')
    row = _existing_method(conn, method)
    if isinstance(tags, str):
        tags = tags.split(',')
    if not isinstance(tags, (list, tuple)) or any(not isinstance(t, str) for t in tags):
        raise ValueError('tags must be a list of strings or comma-separated text')
    tags = list(dict.fromkeys(t.strip() for t in tags if t.strip()))
    if not tags and memo:
        from ledger_purpose_labels import normalize
        tags = normalize(memo)
    from ledger_purpose_labels import normalize
    tags = normalize(tags=tags)
    if origin == 'user':
        origin = 'owner-stated'
    conn.execute('UPDATE pay_method SET purpose_tags=?,purpose_memo=?,purpose_origin=? WHERE id=?',
                 (json.dumps(tags, ensure_ascii=False) if tags else None, memo or None, origin, row['id']))
    return purpose_get(conn, row['id'])


def funding_get(conn, method=None):
    """Read-only graph for roster consumers; main aliases stored is_default."""
    params = () if method is None else (_existing_method(conn, method)['id'],)
    return rows(conn, "SELECT f.wallet_id,w.name AS wallet,f.source_id,s.name AS source,f.sub,f.source_kind,"
                     "f.is_default AS main,f.is_default AS 'default',f.first_seen,f.last_seen,"
                     "COALESCE(p.source,'learned') AS origin "
                     "FROM pay_funding f JOIN pay_method w ON w.id=f.wallet_id JOIN pay_method s ON s.id=f.source_id "
                     "LEFT JOIN ledger_preference p ON p.ulid='pay-funding-'||f.wallet_id||':'||f.source_id||':'||f.sub "
                     "AND p.status='active' " + ('WHERE f.wallet_id=? ' if method is not None else '') +
                     'ORDER BY f.wallet_id,f.is_default DESC,f.source_id,f.sub', params)


def funding_set(K, conn, wallet, source, sub=None, default=False, origin='owner-stated', main=False, said=None):
    if origin not in ('owner-stated', 'learned'):
        raise ValueError('funding origin is owner-stated or learned')
    m = Methods(conn, K)
    wid = _existing_method(conn, wallet)['id'] if str(wallet).isdecimal() else m.resolve(wallet)
    sid = _existing_method(conn, source)['id'] if str(source).isdecimal() else m.resolve(source)
    if wid == sid:
        raise ValueError('a wallet cannot fund itself')
    if m.row(wid)['layer'] != 'wallet' and m.row(wid)['kind'] not in ('credit_card', 'corporate_card', 'debit_card'):
        conn.execute("UPDATE pay_method SET layer='wallet', kind='easy_pay' WHERE id=?", (wid,))
    if sub is None:
        sub = 'card' if _method_layer(m.row(sid)) == 'card' else 'account'
    today = datetime.date.today().isoformat()
    default = default or main
    if default:
        conn.execute('UPDATE pay_funding SET is_default=0 WHERE wallet_id=?', (wid,))
    conn.execute('INSERT INTO pay_funding(wallet_id, source_id, sub, is_default, first_seen, last_seen) VALUES(?,?,?,?,?,?) '
                 'ON CONFLICT(wallet_id, source_id, sub) DO UPDATE SET is_default=MAX(pay_funding.is_default,excluded.is_default), last_seen=excluded.last_seen',
                 (wid, sid, sub, 1 if default else 0, today, today))
    # Provenance is an existing preference fact, keyed to the graph edge.
    # Missing facts on imported edges mean learned; no schema copy is needed.
    edge = '%s:%s:%s' % (wid, sid, sub)
    conn.execute("INSERT INTO ledger_preference(ulid,item,polarity,status,source,recorded_at,topic,detail) "
                 "VALUES(?,?,'negotiable','active',?,?,'pay_funding_source',?) "
                 "ON CONFLICT(ulid) DO UPDATE SET source=excluded.source,detail=excluded.detail,"
                 "recorded_at=excluded.recorded_at,status='active'",
                 ('pay-funding-' + edge, edge, origin, today, json.dumps(dict(origin=origin, **({'said': said} if said else {})))))
    if m.row(wid)['kind'] in ('credit_card', 'corporate_card', 'debit_card') and m.row(sid)['kind'] == 'account':
        conn.execute('UPDATE pay_method SET linked_account_id=? WHERE id=?', (sid, wid))
    current_default = conn.execute('SELECT is_default FROM pay_funding WHERE wallet_id=? AND source_id=? AND sub=?',
                                   (wid,sid,sub)).fetchone()[0]
    source_kind = conn.execute('SELECT source_kind FROM pay_funding WHERE wallet_id=? AND source_id=? AND sub=?',
                               (wid, sid, sub)).fetchone()[0]
    return {'wallet': m.row(wid)['name'], 'source': m.row(sid)['name'], 'sub': sub,
            'source_kind': source_kind, 'main': bool(current_default),
            'default': bool(current_default), 'origin': origin}


def card_answer(K, conn, spend_id, answer):
    from ledger_correction import history, store, save, pop, spend_snapshot, restore_spends, current_spends
    from ledger_review import put
    key = 'card-answer:'+str(spend_id)
    entries = history(conn, key)
    if answer not in ('company', 'mine', 'undo'):
        raise ValueError('answer is company|mine|undo')
    if entries:
        initial = entries[0]['before']
        ids = [r['id'] for r in initial['rows']]
        payer = initial['payer']
    else:
        bill = rows(conn, "SELECT * FROM spend_entry WHERE id=? AND class='card_bill'", (spend_id,))
        if not bill or answer == 'undo':
            raise ValueError('no card bill answer to change')
        bill = bill[0]
        inflow = rows(conn, "SELECT * FROM spend_entry WHERE kind='income' AND amount=? AND method=? "
                      "AND date BETWEEN ? AND ? ORDER BY ABS(julianday(date)-julianday(?)),id LIMIT 1",
                      (abs(bill['amount']), bill['method'], day_add(bill['date'],-1), day_add(bill['date'],1),bill['date']))
        ids = [spend_id] + [r['id'] for r in inflow]
        payer = 'pt-'+hashlib.sha1(norm(inflow[0]['name']).encode()).hexdigest()[:16] if inflow else None
    payer_key = 'card-payer:'+str(payer)
    if payer and not history(conn, payer_key):
        store(conn, payer_key, [dict(original=rows(conn, 'SELECT * FROM ledger_preference WHERE ulid=?', (payer,)))])
    def snapshot():
        return dict(rows=spend_snapshot(conn, ids), payer=payer)
    def restore(state):
        restore_spends(K, conn, state['rows'])
    def refresh_payer():
        if not payer:
            return
        # A payer fact can be shared by several company bills. Reversing
        # one must not remove another bill's still-active owner evidence.
        active = any('pt-'+hashlib.sha1(norm(r['name']).encode()).hexdigest()[:16] == payer
                     for r in rows(conn, "SELECT name FROM spend_entry WHERE class='pass_through' AND amount>0 AND kind='transfer'"))
        if not active:
            conn.execute('DELETE FROM ledger_preference WHERE ulid=?', (payer,))
            for pref in history(conn, payer_key)[0]['original']:
                conn.execute('INSERT INTO ledger_preference('+','.join(pref)+') VALUES('+','.join('?' for _ in pref)+')', list(pref.values()))
    before = snapshot()
    comparison = dict(before, rows=current_spends(conn, entries[-1]['after']['rows'])) if entries else before
    if answer == 'undo':
        restore(pop(conn, key, comparison))
        refresh_payer()
        return dict(spend_id=spend_id, restored=True)
    if entries:
        if comparison != entries[-1]['after']:
            raise ValueError('card answer changed; undo later edits first')
        restore(initial)
    result = _card_answer(K, conn, spend_id, answer, ids[1] if len(ids)>1 else None)
    for sid in ids:
        cls = conn.execute('SELECT class FROM spend_entry WHERE id=?', (sid,)).fetchone()[0]
        put(conn, sid, dict(owner_class=cls))
    save(conn, key, before, snapshot())
    refresh_payer()
    return result


def _card_answer(K, conn, spend_id, answer, inflow_id):
    """The one plumbing question (S2): a settlement to a card that is not one of the owner's.
    company -> pass-through (and the same-amount inflow of that day with it); mine -> the bill is the owner's spend
    (the purchases behind it are not in the file) and the card is registered, so it is never asked again."""
    r = rows(conn, "SELECT * FROM spend_entry WHERE id=? AND class='card_bill'", (spend_id,))
    if not r:
        raise ValueError('spend %s is not an unanswered card bill' % spend_id)
    r = r[0]
    amt = abs(r['amount'])
    if answer == 'company':
        pid = conn.execute('SELECT COALESCE(MAX(pair_id),0)+1 FROM spend_entry').fetchone()[0]
        conn.execute("UPDATE spend_entry SET class='pass_through', scope='business', pair_id=? WHERE id=?", (pid, spend_id))
        inflow = rows(conn, "SELECT * FROM spend_entry WHERE id=? AND kind='income'", (inflow_id,)) if inflow_id else []
        if inflow:
            K._spend_shadow_del(conn, inflow[0]['id'])
            conn.execute("UPDATE spend_entry SET class='pass_through', kind='transfer', amount=?, pair_id=? WHERE id=?",
                         (amt, pid, inflow[0]['id']))
            src = norm(inflow[0]['name'])
            conn.execute("INSERT OR IGNORE INTO ledger_preference(ulid, item, polarity, note, status, source, recorded_at, topic) "
                         "VALUES(?,?,?,?,?,?,?,?)", ('pt-' + hashlib.sha1(src.encode()).hexdigest()[:16], 'src:' + src,
                                                      'negotiable', 'pass-through payer', 'active', 'owner',
                                                      datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'), 'passthrough_source'))
        return {'spend_id': spend_id, 'class': 'pass_through', 'paired_inflow': inflow[0]['id'] if inflow else None}
    if answer != 'mine':
        raise ValueError('answer is company|mine')
    name = r['name']
    conn.execute("UPDATE spend_entry SET class='spend', kind='expense', amount=? WHERE id=?", (amt, spend_id))
    K._spend_write_v32(conn, spend_id, r['date'], name, amt, 'expense', r['method'])
    m = Methods(conn, K)
    issuer = family(name)
    if issuer and not conn.execute("SELECT 1 FROM pay_method WHERE kind IN ('credit_card','corporate_card') AND canonical_id IS NULL AND name LIKE ?",
                                   ('%' + nfkc(name).replace('\uacb0\uc81c', '').strip() + '%',)).fetchone():
        card = nfkc(name).replace('\uacb0\uc81c', '').strip() or name
        if card not in m.names:
            m._insert(card, {'kind': 'credit_card', 'layer': 'card'})
    return {'spend_id': spend_id, 'class': 'spend'}


def stream_link(conn, sid, kind, ref, role='for'):
    n = 0
    for (x,) in conn.execute("SELECT id FROM spend_entry WHERE stream_id=? AND kind='expense'", (sid,)).fetchall():
        spend_link(conn, x, kind, ref, role)
        n += 1
    return {'stream': sid, 'linked': n, 'kind': kind}


def method_scope(K, conn, name, scope):
    m = Methods(conn, K)
    mid = m.resolve(name)
    conn.execute('UPDATE pay_method SET scope_default=? WHERE id=?', (scope, mid))
    names = [m.row(mid)['name']] + [x for x in _json_list(m.row(mid)['aliases'])]
    moved = conn.execute('UPDATE spend_entry SET scope=? WHERE method IN (%s)' % ','.join('?' * len(names)),
                         (scope,) + tuple(names)).rowcount
    return {'method': m.row(mid)['name'], 'scope_default': scope, 'rows_moved': moved}


# ---- spend <-> module links ---------------------------------------------------------------------------------

KINDS = ('person', 'venue', 'workout', 'meal', 'supplement', 'gift', 'shop_item', 'owned_item')
REF_TABLE = {'person': ('persons', 'id'), 'gift': ('persons', 'id'), 'venue': ('place', 'id'),
             'workout': ('workouts', 'id'), 'shop_item': ('shop_item', 'id'), 'owned_item': ('owned_item', 'id')}


def link_ref(conn, kind, ref):
    """Validate a link target and return its stored ref text. person/gift accept a name."""
    if kind not in KINDS:
        raise ValueError('kind must be one of: ' + ', '.join(KINDS))
    ref = str(ref).strip()
    if not ref:
        raise ValueError('ref required')
    if kind in ('supplement',):
        return nfkc(ref)
    if kind == 'meal':
        if not ref.isdigit() or not conn.execute("SELECT 1 FROM block WHERE id=? AND source_module='diet'", (int(ref),)).fetchone():
            raise ValueError('meal ref must be a diet block id')
        return ref
    table, col = REF_TABLE[kind]
    if ref.isdigit():
        if not conn.execute('SELECT 1 FROM %s WHERE %s=?' % (table, col), (int(ref),)).fetchone():
            raise ValueError('%s %s not found' % (kind, ref))
        return ref
    if kind in ('person', 'gift'):
        got = conn.execute('SELECT id FROM persons WHERE name=? OR display_name=?', (ref, ref)).fetchall()
        if not got:
            got = [(r['id'],) for r in rows(conn, 'SELECT id, aliases FROM persons') if ref in _json_list(r['aliases'])]
        if len(got) != 1:
            raise ValueError('%s "%s": %s' % (kind, ref, 'not found' if not got else 'more than one -- give the id'))
        return str(got[0][0])
    if kind == 'venue':
        got = conn.execute('SELECT id FROM place WHERE name=?', (ref,)).fetchall()
        if len(got) != 1:
            raise ValueError('venue "%s": %s' % (ref, 'not found' if not got else 'more than one -- give the id'))
        return str(got[0][0])
    raise ValueError('%s ref must be an id' % kind)


def spend_link(conn, spend_id, kind, ref, role='for', source='owner', rule_id=None):
    if not conn.execute('SELECT 1 FROM spend_entry WHERE id=?', (spend_id,)).fetchone():
        raise ValueError('spend %s not found' % spend_id)
    ref = link_ref(conn, kind, ref)
    conn.execute('INSERT INTO spend_link(spend_id, entity_kind, entity_ref, role, source, rule_id) VALUES(?,?,?,?,?,?) '
                 'ON CONFLICT(spend_id, entity_kind, entity_ref) DO UPDATE SET role=excluded.role, '
                 "source=CASE WHEN spend_link.source='owner' THEN 'owner' ELSE excluded.source END, "
                 "rule_id=CASE WHEN spend_link.source='owner' THEN NULL ELSE excluded.rule_id END",
                 (spend_id, kind, ref, role, source, rule_id))
    return {'spend_id': spend_id, 'kind': kind, 'ref': ref, 'role': role}


def rule_matches(match, r):
    """match_json keys (all optional, all must hold): method (name or alias), weekday [1..7 Mon=1],
    time [HH:MM, HH:MM], merchant (substring), cat (substring of the export category), class, min, max."""
    if 'method' in match and norm(match['method']) not in norm(r.get('method') or ''):
        return False
    if 'weekday' in match:
        try:
            wd = datetime.date.fromisoformat(r['date']).isoweekday()
        except ValueError:
            return False
        if wd not in match['weekday']:
            return False
    if 'time' in match:
        t = minutes(r.get('at_time') or '')
        lo, hi = minutes(match['time'][0]), minutes(match['time'][1])
        if t is None or not (lo <= t <= hi):
            return False
    if 'merchant' in match and norm(match['merchant']) not in norm(r.get('name') or ''):
        return False
    if 'cat' in match and match['cat'] not in (r.get('src_cat') or ''):
        return False
    if 'class' in match and (r.get('class') or 'spend') != match['class']:
        return False
    if 'min' in match and abs(r['amount']) < match['min']:
        return False
    if 'max' in match and abs(r['amount']) > match['max']:
        return False
    return True


def link_rule_add(conn, kind, ref, match, said=None, role='for'):
    ref = link_ref(conn, kind, ref)
    if not isinstance(match, dict) or not match:
        raise ValueError('a rule needs at least one condition')
    cur = conn.execute('INSERT INTO spend_link_rule(entity_kind, entity_ref, role, match_json, said) VALUES(?,?,?,?,?)',
                       (kind, ref, role, json.dumps(match, ensure_ascii=False, sort_keys=True), said))
    n = apply_rule(conn, cur.lastrowid)
    return {'rule_id': cur.lastrowid, 'kind': kind, 'ref': ref, 'linked': n}


def apply_rule(conn, rid):
    r = rows(conn, 'SELECT * FROM spend_link_rule WHERE id=? AND active=1', (rid,))
    if not r:
        return 0
    r = r[0]
    match = json.loads(r['match_json'])
    n = 0
    for s in rows(conn, "SELECT id, date, amount, name, method, class, src_cat, at_time FROM spend_entry "
                        "WHERE kind='expense'"):
        if rule_matches(match, s):
            conn.execute('INSERT OR IGNORE INTO spend_link(spend_id, entity_kind, entity_ref, role, source, rule_id) '
                         "VALUES(?,?,?,?, 'rule', ?)", (s['id'], r['entity_kind'], r['entity_ref'], r['role'], rid))
            n += conn.execute('SELECT changes()').fetchone()[0]
    return n


def link_sum(conn, kind, ref=None, month=None):
    if kind not in KINDS:
        raise ValueError('kind must be one of: ' + ', '.join(KINDS))
    if ref is not None:
        ref = link_ref(conn, kind, ref)
    out = rows(conn, "SELECT entity_ref, role, COUNT(*) n, SUM(amount) total FROM v_spend_link WHERE entity_kind=? "
                     "AND (? IS NULL OR entity_ref=?) AND (? IS NULL OR substr(date,1,7)=?) GROUP BY entity_ref, role "
                     "ORDER BY total DESC", (kind, ref, ref, month, month))
    return {'kind': kind, 'month': month, 'by_entity': out, 'total': sum(r['total'] or 0 for r in out)}


# ---- CLI ---------------------------------------------------------------------------------------------------------------

class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def cli(K, verb, argv):
    import ledger_diag as D
    p = Parser(prog='kit_life.sh ' + verb)
    if verb in ('amount-note-add', 'amount-note-remove'):
        target = p.add_mutually_exclusive_group(required=True)
        target.add_argument('--account')
        target.add_argument('--fixed', type=int)
        p.add_argument('reasons', nargs='+')
    elif verb == 'ledger-merchant-names-set':
        p.add_argument('--data', required=True)
    elif verb == 'ledger-merchant-names':
        pass
    elif verb == 'ledger-merchant-kind-set':
        import ledger_plan
        p.add_argument('name'); p.add_argument('kind', choices=(*ledger_plan.KIND_BUCKETS, 'unresolved'))
        p.add_argument('--source', required=True, choices=('web', 'owner'))
        p.add_argument('--evidence', required=True)
        p.add_argument('--query', help='exact executed search query for this unresolved merchant')
    elif verb == 'ledger-merchant-kinds':
        p.add_argument('--today', default=datetime.date.today().isoformat())
    elif verb in ('ledger-plan', 'ledger-plan-answer', 'ledger-plan-commit', 'ledger-plan-project'):
        p.add_argument('--all-months', action='store_true')
        p.add_argument('--today', default=datetime.date.today().isoformat())
        if verb != 'ledger-plan':
            p.add_argument('--data', required=True)
    elif verb in ('ledger-history', 'ledger-history-reconcile'):
        p.add_argument('--all-months', action='store_true')
        p.add_argument('--today')
        p.add_argument('--review-deposits', action='store_true')
    elif verb in ('bank-evidence', 'funding-evidence'):
        p.add_argument('method')
    elif verb == 'method-merge':
        p.add_argument('method'); p.add_argument('into')
    elif verb == 'ledger-import':
        p.add_argument('file'); p.add_argument('--password-stdin', action='store_true'); p.add_argument('--today')
        p.add_argument('--since'); p.add_argument('--until')
    elif verb == 'ledger-find':
        p.add_argument('text'); p.add_argument('--month'); p.add_argument('--raw', action='store_true')
        p.add_argument('--file'); p.add_argument('--password-stdin', action='store_true')
    elif verb == 'ledger-reset':
        g = p.add_mutually_exclusive_group(required=True)
        g.add_argument('--dry-run', action='store_true'); g.add_argument('--yes', action='store_true')
    elif verb == 'ledger-restart':
        g = p.add_mutually_exclusive_group(required=True)
        g.add_argument('--dry-run', action='store_true'); g.add_argument('--yes', action='store_true')
    elif verb == 'ledger-dedupe':
        p.add_argument('--dry-run', action='store_true')
    elif verb == 'funding-set':
        p.add_argument('wallet'); p.add_argument('source'); p.add_argument('--default', action='store_true')
        p.add_argument('--main', action='store_true', help='main funding source (alias of --default)')
        p.add_argument('--said', help='owner words establishing the funding source')
        p.add_argument('--sub', choices=('balance', 'account', 'card', 'points', 'meal'))
        p.add_argument('--origin', choices=('owner-stated', 'learned'), default='owner-stated')
    elif verb == 'purpose-evidence':
        p.add_argument('--today')
    elif verb in ('purpose-get', 'funding-get'):
        p.add_argument('method', nargs='?', help='existing method ID, name or alias; omitted = roster')
    elif verb == 'purpose-set':
        p.add_argument('method'); p.add_argument('--tags', required=True, help='comma-separated tags; empty clears tags')
        p.add_argument('--memo', default='', help='free text/custom purpose; empty clears memo')
        p.add_argument('--origin', choices=('owner-stated', 'learned', 'user'), default='owner-stated')
    elif verb == 'method-scope':
        p.add_argument('name'); p.add_argument('scope', choices=('personal', 'business'))
    elif verb in ('spend-link', 'spend-unlink'):
        p.add_argument('spend_id', type=int); p.add_argument('kind', choices=KINDS); p.add_argument('ref')
        p.add_argument('--role', choices=('for', 'with'), default='for')
    elif verb == 'spend-person':
        p.add_argument('spend_id', type=int); p.add_argument('person'); p.add_argument('--role', choices=('for', 'with'), default='for')
    elif verb in ('spend-link-rule-add', 'spend-person-rule-add'):
        if verb == 'spend-link-rule-add':
            p.add_argument('--kind', choices=KINDS, required=True); p.add_argument('--ref', required=True)
        else:
            p.add_argument('--person', required=True)
        p.add_argument('--method'); p.add_argument('--weekday'); p.add_argument('--time'); p.add_argument('--merchant')
        p.add_argument('--cat'); p.add_argument('--class', dest='cls'); p.add_argument('--min', type=int)
        p.add_argument('--max', type=int); p.add_argument('--said'); p.add_argument('--role', choices=('for', 'with'), default='for')
    elif verb == 'spend-link-rule-list':
        p.add_argument('--kind', choices=KINDS)
    elif verb == 'spend-link-rule-off':
        p.add_argument('id', type=int)
    elif verb == 'spend-link-sum':
        p.add_argument('kind', choices=KINDS); p.add_argument('ref', nargs='?'); p.add_argument('--month')
    elif verb == 'ledger-benefit-compare':
        p.add_argument('--data', required=True)
    elif verb == 'ledger-review-answer':
        p.add_argument('--month', required=True); p.add_argument('--data', required=True)
    elif verb == 'ledger-diagnosis':
        p.add_argument('--month'); p.add_argument('--json', action='store_true')
        p.add_argument('--roster-only', action='store_true', help=argparse.SUPPRESS)
    elif verb == 'stream-list':
        p.add_argument('--all', action='store_true')
    elif verb == 'stream-reconcile':
        p.add_argument('--month')
    elif verb == 'stream-stop':
        p.add_argument('id', type=int); p.add_argument('--said')
    elif verb == 'stream-keep':
        p.add_argument('id', type=int); p.add_argument('keep', choices=('must', 'reducible', 'droppable'))
    elif verb == 'stream-set':
        p.add_argument('id', type=int); p.add_argument('field', choices=('amount', 'name', 'class', 'state', 'pay_mode'))
        p.add_argument('value')
    elif verb == 'stream-merge':
        p.add_argument('id', type=int); p.add_argument('into', type=int)
    elif verb == 'ledger-card-answer':
        p.add_argument('spend_id', type=int); p.add_argument('answer', choices=('company', 'mine', 'undo'))
    elif verb == 'stream-link':
        p.add_argument('id', type=int); p.add_argument('kind', choices=KINDS); p.add_argument('ref')
        p.add_argument('--role', choices=('for', 'with'), default='for')
    elif verb == 'ledger-state':
        p.add_argument('--month')
    conn = None
    try:
        a = p.parse_args(argv)
        if getattr(a, 'password_stdin', False):
            a.password = sys.stdin.readline().rstrip('\r\n') or None
        else:
            a.password = None
        if not os.path.isfile(K.DB_PATH):
            raise ValueError('database not found')
        conn = K.get_conn()
        if verb in ('purpose-evidence', 'funding-evidence', 'bank-evidence', 'purpose-get', 'funding-get', 'ledger-benefit-compare', 'ledger-history', 'ledger-merchant-kinds'):
            conn.execute('PRAGMA query_only=ON')
        roster_only = verb == 'ledger-diagnosis' and a.roster_only
        if roster_only:
            conn.execute('PRAGMA query_only=ON')
        write = (verb not in READ_VERBS or verb == 'ledger-diagnosis' and not roster_only) and not getattr(a, 'dry_run', False)
        if write:
            conn.execute('BEGIN IMMEDIATE')
        if verb in ('amount-note-add', 'amount-note-remove'):
            import ledger_payday
            result = ledger_payday.edit_notes(K, conn, a.account, a.fixed, a.reasons,
                                             remove=verb == 'amount-note-remove')
        elif verb == 'ledger-merchant-names-set':
            import ledger_names
            result = ledger_names.set_names(conn, json.loads(a.data))
        elif verb == 'ledger-merchant-names':
            import ledger_names
            result = ledger_names.catalog(conn)
        elif verb == 'ledger-merchant-kind-set':
            import ledger_plan
            result = ledger_plan.merchant_kind_set(conn, a.name, a.kind, a.source, a.evidence, query=a.query)
        elif verb == 'ledger-merchant-kinds':
            import ledger_plan
            result = dict(cache=ledger_plan.merchant_kinds(conn), kinds=list(ledger_plan.KIND_BUCKETS),
                          instructions=ledger_plan.MERCHANT_LOOKUP_INSTRUCTIONS,
                          pending=ledger_plan.read(conn, a.today)['merchant_lookups'])
        elif verb in ('ledger-plan', 'ledger-plan-answer', 'ledger-plan-commit', 'ledger-plan-project'):
            import ledger_plan
            if verb == 'ledger-plan':
                ledger_plan.reconcile_annual_reminders(K,conn,today=a.today)
                result = ledger_plan.read(conn, a.today, all_months=a.all_months)
            elif verb == 'ledger-plan-project':
                data=json.loads(a.data)
                if data.get('month'):
                    import ledger_obligation
                    result=ledger_obligation.forecast(data['plan'],data['targets'],data['month'])
                else:
                    result=ledger_plan.projection(data['plan'],data['targets'])
                    if data.get('end_schedule'):
                        import ledger_obligation
                        result['end_schedule']=ledger_obligation.end_schedule(data['plan'],data['targets'])
            elif verb == 'ledger-plan-answer':
                result = ledger_plan.answer(conn, json.loads(a.data), K=K, today=a.today, all_months=a.all_months)
            else:
                result = ledger_plan.commit(conn, json.loads(a.data))
        elif verb in ('ledger-history', 'ledger-history-reconcile'):
            import ledger_history
            if verb == 'ledger-history-reconcile':
                repairs = ledger_history.repair(K, conn)
                D.stream_reconcile(conn)
                settlement = ledger_history.match_history(conn, K=K, review_deposits=a.review_deposits)
                result = dict(repairs=repairs, settlement=settlement, history=ledger_history.history(conn, a.today, all_months=a.all_months))
            else:
                result = ledger_history.history(conn, a.today, all_months=a.all_months)
        elif verb == 'purpose-evidence':
            import ledger_purpose
            result = ledger_purpose.evidence(conn, a.today)
        elif verb == 'funding-evidence':
            import ledger_accounts
            result = ledger_accounts.funding_evidence(conn, a.method)
        elif verb == 'bank-evidence':
            import ledger_accounts
            result = ledger_accounts.evidence(K, conn, a.method)
        elif verb == 'method-merge':
            import ledger_accounts
            result = ledger_accounts.merge(K, conn, a.method, a.into)
        elif verb == 'ledger-import':
            result = run_import(K, conn, a.file, a.password, a.today, a.since, a.until)
        elif verb == 'ledger-find':
            result = find(conn, a.text, a.month, a.raw, a.file, a.password)
        elif verb == 'ledger-reset':
            result = reset(K, conn, a.dry_run)
        elif verb == 'ledger-restart':
            result = restart(K, conn, a.dry_run)
        elif verb == 'ledger-dedupe':
            result = dedupe(conn)
        elif verb == 'funding-set':
            result = funding_set(K, conn, a.wallet, a.source, a.sub, a.default, a.origin, main=a.main, said=a.said)
        elif verb == 'purpose-set':
            result = purpose_set(K, conn, a.method, a.tags, a.memo, a.origin)
        elif verb == 'purpose-get':
            result = purpose_get(conn, a.method)
        elif verb == 'funding-get':
            result = funding_get(conn, a.method)
        elif verb == 'method-scope':
            result = method_scope(K, conn, a.name, a.scope)
        elif verb == 'spend-link':
            result = spend_link(conn, a.spend_id, a.kind, a.ref, a.role)
        elif verb == 'spend-unlink':
            ref = link_ref(conn, a.kind, a.ref)
            conn.execute('DELETE FROM spend_link WHERE spend_id=? AND entity_kind=? AND entity_ref=?', (a.spend_id, a.kind, ref))
            result = {'unlinked': a.spend_id}
        elif verb == 'spend-person':
            result = spend_link(conn, a.spend_id, 'person', a.person, a.role)
        elif verb in ('spend-link-rule-add', 'spend-person-rule-add'):
            match = {}
            if a.method: match['method'] = a.method
            if a.weekday:
                lo, _, hi = a.weekday.partition('-')
                match['weekday'] = list(range(int(lo), int(hi or lo) + 1)) if '-' in a.weekday else [int(x) for x in a.weekday.split(',')]
            if a.time:
                lo, _, hi = a.time.partition('-')
                match['time'] = [lo, hi]
            if a.merchant: match['merchant'] = a.merchant
            if a.cat: match['cat'] = a.cat
            if a.cls: match['class'] = a.cls
            if a.min is not None: match['min'] = a.min
            if a.max is not None: match['max'] = a.max
            kind, ref = (a.kind, a.ref) if verb == 'spend-link-rule-add' else ('person', a.person)
            result = link_rule_add(conn, kind, ref, match, a.said, a.role)
        elif verb == 'spend-link-rule-list':
            result = rows(conn, 'SELECT * FROM spend_link_rule WHERE (? IS NULL OR entity_kind=?) ORDER BY id', (a.kind, a.kind))
        elif verb == 'spend-link-rule-off':
            conn.execute('UPDATE spend_link_rule SET active=0 WHERE id=?', (a.id,))
            conn.execute("DELETE FROM spend_link WHERE rule_id=? AND source='rule'", (a.id,))
            result = {'off': a.id}
        elif verb == 'spend-link-sum':
            result = link_sum(conn, a.kind, a.ref, a.month)
        elif verb == 'ledger-benefit-compare':
            import ledger_benefits
            result = ledger_benefits.compare(conn, json.loads(a.data))
        elif verb == 'ledger-review-answer':
            import ledger_review
            result = ledger_review.answer(conn, a.month, json.loads(a.data), K=K)
        elif verb == 'ledger-diagnosis':
            if roster_only:
                import ledger_review
                result = dict(month=a.month or D.basis_month(conn), review=dict(roster=ledger_review.roster(conn)))
            else:
                D.stream_reconcile(conn, month=a.month)          # states are relative to the month asked for
                result = D.diagnosis(conn, a.month)
        elif verb == 'stream-list':
            result = D.stream_list(conn, a.all)
        elif verb == 'stream-reconcile':
            result = D.stream_reconcile(conn, month=a.month)
        elif verb == 'stream-stop':
            result = D.stream_stop(conn, a.id, a.said)
        elif verb == 'stream-keep':
            result = D.stream_keep(conn, a.id, a.keep)
        elif verb == 'stream-set':
            result = D.stream_set(conn, a.id, a.field, a.value)
        elif verb == 'stream-merge':
            result = D.stream_merge(conn, a.id, a.into)
        elif verb == 'stream-link':
            result = stream_link(conn, a.id, a.kind, a.ref, a.role)
        elif verb == 'ledger-card-answer':
            result = card_answer(K, conn, a.spend_id, a.answer)
        else:
            result = D.ledger_state(conn, a.month)
        if conn.in_transaction:
            conn.commit()
            if verb == 'ledger-plan-answer':
                ledger_plan.reconcile_annual_reminders(K,conn,json.loads(a.data),today=a.today)
        if verb in READ_VERBS and verb not in ('ledger-merchant-names', 'ledger-plan', 'ledger-plan-project'):
            import ledger_names
            result = ledger_names.present(conn, result)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    except (ValueError, KeyError, TypeError, sqlite3.Error, OSError) as e:
        if conn and conn.in_transaction:
            conn.rollback()
        print(json.dumps({'error': str(e)}, ensure_ascii=False))
        raise SystemExit(2)
    finally:
        if conn:
            conn.close()
