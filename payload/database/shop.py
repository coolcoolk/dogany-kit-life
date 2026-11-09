"""shop.py -- module_shopping verbs, phase 1: find-things (migration 071).

Spec: the maintainer worklog 2026-10-07-module-shopping-SPEC (A2 verbs, Gate 2 CRUD,
Gate 4 decision points, build notes P1).  Dispatched from kit_life.main the
way intake.py is: cli(K, verb, argv) with K = the running kit_life module, so
this file never imports kit_life (a second copy would re-read DB_PATH).

THE FIND REQUEST IS A LEDGER (D11).  shop_hunt -> shop_round -> shop_shown.
The cursor (shop-hunt-cursor) says what input the ledger expects next; the
round render (shop-round-add / shop-round-show) is emitted ONLY by the verb,
so a card the engine did not accept can never reach the owner.

ENGINE RULES (refusals, not prose).  shop-round-add writes nothing unless
every card passes; each failure is one line REFUSED<TAB>CODE<TAB>detail:
  NO_COVERAGE       no --seen source (the coverage line is mandatory)
  ROUND_SIZE        fewer than 3 cards (unless --partial "<reason>") or more
                    than shop_round_size (074 set-size, 3-4)
  REAIM_DUE         4 rounds in a row without a pick: re-aim with the owner
                    first (--reaimed once they chose to narrow)
  BAD_CARD / MISSING_FIELD   card JSON unreadable / a required field absent
                    (name url image direction source price checks; ship_by
                    when the hunt has a need_by)
  UNVERIFIED_CARD   a check the category requires (shop_category.
                    checks_required) is absent or not true, or any check false
  DUP_ITEM          two cards are one product (same item, same url, or same
                    brand+name in another colour/size -- 074 set-diversity)
  DUP_DIRECTION     two cards carry the same style direction
  DROPPED_DIRECTION a direction the owner rejected in this hunt
  REJECTED_ITEM     a product the owner rejected as a thing (scope item)
  SHOWN_BEFORE      a product already shown in this hunt and not picked
  OVER_BUDGET       above budget_max after the owner chose "within budget"
Flags on accepted cards (labels, never hidden): OVER_BUDGET, LATE (ship_by
after need_by), THIN_REVIEWS (review count under shop_review_floor).
Slot order is the engine's (074 rotate-slots): the model's order is stored
as rank, slots rotate by one per round across the whole store.

EXIT CODES (kit conventions): 0 ok; 1 usage / not found (stderr);
3 EXISTS (find-first hit, nothing created); 4 REFUSED (engine gate, nothing
written); 5 NEAR_MATCH (kit near-match block, one question to the owner).

PHASE 2 (migration 072, skill shopping-log): purchase capture (shop-bought
fills store / link / price from the hunt's picked card, returns MISSING for
the one-line ask, writes the money row through the existing spend-add path
when the ledger link is on -- price then lives ONLY there -- and says the KR
withdrawal line once for an online purchase), owned items (find-first on
item + date, ref-safe delete OWNED_HAS_SPEND), garment measurements, stated
taste (find-first, TASTE_OPPOSITE, AVOID_HIT on brand), the opt-in
cooling-off offer (one reminder, nothing resurfaced) and the size draft
(owned garment fit > body measures > height/weight band).  Section "phase
2" below; exit 3 also = a ledger or owned row already there (nothing
written).

Not built here (P3 of the spec): wishlist, rebuy, nudges, tendency
confirmation.  Reference checks against those tables run only when the
table exists.
"""
import datetime
import json
import os
import re
import sqlite3
import sys
import unicodedata
from money_format import comma

CAPTURE_VERBS = ('shop-capture', 'shop-capture-upd', 'shop-capture-find',
                 'shop-stock-find', 'shop-stock-set', 'shop-stock-use',
                 'shop-stock-empty', 'shop-payment-match')

VERBS = (
    'shop-cat-list', 'shop-cat-add', 'shop-cat-upd', 'shop-cat-retire',
    'shop-cat-del',
    'shop-item-upsert', 'shop-item-find', 'shop-item-upd', 'shop-item-merge',
    'shop-item-del',
    'shop-hunt-open', 'shop-hunt-cursor', 'shop-hunt-list', 'shop-hunt-upd',
    'shop-hunt-close', 'shop-hunt-del',
    'shop-round-add', 'shop-round-show', 'shop-round-upd',
    'shop-verdict', 'shop-shown-list', 'shop-shown-void',
    # phase 2 (072): purchase log, taste, garment measurements, cooling-off,
    # size draft
    'shop-bought', 'shop-owned-add', 'shop-owned-find', 'shop-owned-list',
    'shop-owned-upd', 'shop-owned-retire', 'shop-owned-del',
    'shop-measure-add', 'shop-measure-list', 'shop-measure-del',
    'shop-taste-add', 'shop-taste-list', 'shop-taste-upd', 'shop-taste-retire',
    'shop-taste-del', 'shop-want', 'shop-want-answer', 'shop-size-draft',
) + CAPTURE_VERBS
P2_VERBS = VERBS[VERBS.index('shop-bought'):-len(CAPTURE_VERBS)]
READ_VERBS = ('shop-cat-list', 'shop-item-find', 'shop-hunt-list',
              'shop-round-show', 'shop-shown-list', 'shop-owned-find',
              'shop-owned-list', 'shop-measure-list', 'shop-taste-list',
              'shop-want', 'shop-size-draft', 'shop-capture-find', 'shop-stock-find')
# the one verb that writes through the kit's event SDK (timeblock_add opens
# its own BEGIN IMMEDIATE): no outer transaction around it
NOTXN_VERBS = ('shop-want-answer',)

SCHEMA_VERSION = 71            # phase 1 verbs; phase 2 needs P2_SCHEMA_VERSION
EXISTS_CODE = 3
REFUSED_CODE = 4
ROUND_MIN = 3                  # 074 set-size lower bound (product constant)
ROUND_MAX = 4                  # 074 set-size upper bound (product constant)
REAIM_EVERY = 4                # spec A4: re-aim checkpoint after 4 blind rounds
HUNT_EXPIRY_DAYS = 14          # D11 rule 5 / spec Gate 4 "hunt expiry"
REVIEW_FLOOR_DEFAULT = 10      # shop_review_floor absent (074 thin-reviews:
                               # a product constant, no source sets it)
# Where to look when the owner set nothing (config shop_sources_kr /
# shop_sources_global, JSON lists, override). Defaults, not seller facts.
SOURCES_KR_DEFAULT = ('musinsa.com', '29cm.co.kr', 'coupang.com',
                      'shopping.naver.com', 'ssg.com')
SOURCES_GLOBAL_DEFAULT = ('amazon.com', 'ebay.com', 'aliexpress.com')
MAX_REASONS = 3                # spec Gate 4 model payload: <= 3 reason tokens
CARD_FIELDS = ('name', 'url', 'image', 'direction', 'source', 'price', 'checks')
TOKEN_RE = re.compile(r'^[a-z][a-z0-9_]*$')
DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
SIDE_TABLES = ('owned_item', 'wish_item')     # P2 / P3 item referrers
NOW = None                     # tests pin the clock: shop.NOW = datetime


class CaptureLines(list):
    """Output plus structured capture metadata, consumed only after commit."""
    def __init__(self, lines, receipt=None):
        super().__init__(lines)
        self.receipt = receipt


class Refuse(Exception):
    """An engine gate said no. lines = [(code, detail)]; extra = rows that
    explain it (category list, valid slots)."""

    def __init__(self, lines, extra=()):
        Exception.__init__(self, '; '.join(c for c, _d in lines))
        self.lines = list(lines)
        self.extra = list(extra)


class Usage(Exception):
    pass


# -- small helpers ----------------------------------------------------------

def _now():
    return NOW or datetime.datetime.now(datetime.timezone.utc).replace(
        tzinfo=None)


def _stamp(dt=None):
    return (dt or _now()).strftime('%Y-%m-%dT%H:%M:%SZ')


def _parse_stamp(s):
    try:
        return datetime.datetime.strptime(str(s)[:19], '%Y-%m-%dT%H:%M:%S')
    except ValueError:
        try:
            return datetime.datetime.strptime(str(s)[:19], '%Y-%m-%d %H:%M:%S')
        except ValueError:
            return None


def _date(s, what):
    if s is None:
        return None
    s = str(s).strip()
    if not DATE_RE.match(s):
        raise Usage('%s must be YYYY-MM-DD: %r' % (what, s))
    try:
        datetime.date.fromisoformat(s)
    except ValueError:
        raise Usage('%s is not a calendar date: %r' % (what, s))
    return s


def _ws(s):
    return ' '.join(str(s or '').split())


def compact(s):
    """Identity key for directions and product names: NFKC, casefold, letters
    and digits only ("Work coverall" == "work-coverall")."""
    return ''.join(ch for ch in unicodedata.normalize('NFKC', str(s or ''))
                   .casefold() if ch.isalnum())


def _json_list(blob):
    try:
        v = json.loads(blob) if blob else []
    except (TypeError, ValueError):
        return []
    return [x for x in v if isinstance(x, str)] if isinstance(v, list) else []


def _csv(s):
    return [x.strip() for x in str(s or '').split(',') if x.strip()]


def _table_exists(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name=?", (name,)).fetchone() is not None


def _cfg(conn, key, default=None):
    row = conn.execute('SELECT value FROM config WHERE key=?',
                       (key,)).fetchone()
    return row[0] if row and row[0] not in (None, '') else default


def round_size(conn):
    """shop_round_size: 3 or 4 (074 set-size); anything else reads as 4."""
    try:
        n = int(_cfg(conn, 'shop_round_size', ROUND_MAX))
    except (TypeError, ValueError):
        return ROUND_MAX
    return n if ROUND_MIN <= n <= ROUND_MAX else ROUND_MAX


def review_floor(conn):
    try:
        n = int(_cfg(conn, 'shop_review_floor', REVIEW_FLOOR_DEFAULT))
    except (TypeError, ValueError):
        return REVIEW_FLOOR_DEFAULT
    return n if n >= 0 else REVIEW_FLOOR_DEFAULT


def sources(conn):
    """(kr, global) source lists: the owner's config value, else default."""
    out = []
    for key, dflt in (('shop_sources_kr', SOURCES_KR_DEFAULT),
                      ('shop_sources_global', SOURCES_GLOBAL_DEFAULT)):
        got = _json_list(_cfg(conn, key))
        out.append(got or list(dflt))
    return out[0], out[1]


def _say(K, key, fallback, **kw):
    text = K._i18n(key, fallback) or fallback
    try:
        return text.format(**kw)
    except (KeyError, IndexError, ValueError):
        return fallback.format(**kw)


def _won(n):
    return comma(int(n))


def schema_ready(conn):
    return _table_exists(conn, 'shop_shown')


# -- category ---------------------------------------------------------------

CAT_COLS = 'id, name, display_name, consumable, checks_required, active'


def _cat_dict(row):
    return dict(zip(('id', 'name', 'display_name', 'consumable',
                     'checks_required', 'active'), row))


def cat_label(K, cat):
    """What the owner sees: the name they gave, else the bundle label."""
    if cat.get('display_name'):
        return cat['display_name']
    return K._i18n('shop.category.%s' % cat['name'], cat['name'])


def _cat_labels(K, cat):
    """Every exact string that names this category (token, owner name, the
    bundle label in the agent language and in English)."""
    out = [cat['name']]
    if cat.get('display_name'):
        out.append(cat['display_name'])
    key = 'shop.category.%s' % cat['name']
    for lab in K._copy_candidates(key):
        out.append(lab)
    return [x for x in out if x]


def _cat_rows(conn, active_only=False):
    sql = 'SELECT %s FROM shop_category' % CAT_COLS
    if active_only:
        sql += ' WHERE active=1'
    return [_cat_dict(r) for r in conn.execute(sql + ' ORDER BY id')]


def near_rows(K, conn, kind):
    """Rows for the kit near-match gate (kit_life._NEAR_ROWS)."""
    if kind == 'category':
        return [(c['name'], cat_label(K, c), _cat_labels(K, c))
                for c in _cat_rows(conn, active_only=True)]
    rows = []
    for iid, name, brand, aliases in conn.execute(
            'SELECT id, name, brand, aliases FROM shop_item ORDER BY id'):
        extra = _json_list(aliases)
        if brand:
            extra.append('%s %s' % (brand, name))
        rows.append((iid, name, extra))
    return rows


def near_accept(K, conn, domain, ref, query):
    """Owner said "yes, the same": an item learns the words as an alias; a
    category has no alias store (its token is the agent's) -- yes = use it."""
    if domain == 'category':
        cat = _cat_get(conn, ref)
        if cat is None:
            raise ValueError('category %r not found' % (ref,))
        return cat['name'], cat_label(K, cat), False
    item = _item_get(conn, ref)
    if item is None:
        raise ValueError('item %r not found' % (ref,))
    q = _ws(query)
    have = _json_list(item['aliases'])
    if q == item['name'] or q in have:
        return item['id'], item['name'], False
    other = _item_exact(conn, None, q, None, None, None)
    if other is not None and other['id'] != item['id']:
        raise ValueError("'%s' already names another item (%s. %s)"
                         % (q, other['id'], other['name']))
    have.append(q)
    conn.execute('UPDATE shop_item SET aliases=? WHERE id=?',
                 (json.dumps(have, ensure_ascii=False), item['id']))
    conn.commit()
    return item['id'], item['name'], True


def _cat_get(conn, ref):
    ref = str(ref).strip()
    if ref.isdigit():
        row = conn.execute('SELECT %s FROM shop_category WHERE id=?'
                           % CAT_COLS, (int(ref),)).fetchone()
    else:
        row = conn.execute('SELECT %s FROM shop_category WHERE name=?'
                           % CAT_COLS, (ref,)).fetchone()
    return _cat_dict(row) if row else None


def _cat_list_lines(K, conn):
    return ['CATEGORY\t%d\t%s\t%s' % (c['id'], c['name'], cat_label(K, c))
            for c in _cat_rows(conn, active_only=True)]


def cat_resolve(K, conn, ref):
    """Exact (id, token, owner name, bundle label) -> the row.  A near
    variant -> NEAR_MATCH (exit 5, one question).  Nothing -> CAT_UNKNOWN with
    the active list, so the model picks within the options or adds one."""
    cat = _cat_get(conn, ref)
    if cat is None:
        q = compact(ref)
        for c in _cat_rows(conn):
            if any(compact(lab) == q for lab in _cat_labels(K, c)):
                cat = c
                break
    if cat is not None:
        if not cat['active']:
            raise Refuse([('CAT_RETIRED', '%s %s' % (cat['id'], cat['name']))],
                         _cat_list_lines(K, conn))
        return cat
    cands = K.near_candidates(conn, 'category', ref)
    if cands:
        K._near_exit('category', ref, cands)
    raise Refuse([('CAT_UNKNOWN', _ws(ref))], _cat_list_lines(K, conn))


def _checks_arg(s):
    keys = _csv(s)
    if not keys or any(not TOKEN_RE.match(k) for k in keys):
        raise Usage('--checks must be comma-separated tokens: %r' % (s,))
    return json.dumps(keys)


def cat_add(K, conn, name, display=None, consumable=None, checks=None,
            new_confirmed=False):
    if not TOKEN_RE.match(name or ''):
        raise Usage('category name must be an ASCII token [a-z][a-z0-9_]*: '
                    '%r (the owner\'s words go in --display)' % (name,))
    hit = _cat_get(conn, name)
    if hit is None and display:
        q = compact(display)
        for c in _cat_rows(conn):
            if any(compact(lab) == q for lab in _cat_labels(K, c)):
                hit = c
                break
    if hit is not None:
        return 'EXISTS', hit
    for q in ([display] if display else []) + [name]:
        K._near_gate_new_confirmed(conn, 'category', q, new_confirmed)
    conn.execute(
        'INSERT INTO shop_category (name, display_name, consumable, '
        'checks_required) VALUES (?,?,?,?)',
        (name, _ws(display) or None, int(consumable or 0),
         _checks_arg(checks) if checks else '["form","adult"]'))
    return 'ADDED', _cat_get(conn, name)


def _cat_counts(conn, cid):
    out = [('n_items', conn.execute('SELECT COUNT(*) FROM shop_item WHERE '
                                    'category_id=?', (cid,)).fetchone()[0]),
           ('n_hunts', conn.execute('SELECT COUNT(*) FROM shop_hunt WHERE '
                                    'category_id=?', (cid,)).fetchone()[0])]
    if _table_exists(conn, 'taste_mark'):
        out.append(('n_taste', conn.execute(
            'SELECT COUNT(*) FROM taste_mark WHERE category_id=?',
            (cid,)).fetchone()[0]))
    return out


def _cat_line(K, conn, c):
    counts = dict(_cat_counts(conn, c['id']))
    return 'CATEGORY\t%d\t%s\t%s\tconsumable=%d\tactive=%d\tchecks=%s\t' \
           'items=%d\thunts=%d' % (
               c['id'], c['name'], cat_label(K, c), c['consumable'],
               c['active'], ','.join(_json_list(c['checks_required'])),
               counts['n_items'], counts['n_hunts']) + (
        '\tledger=%s' % (_spend_category_name(conn, c) or '-')
        if _table_exists(conn, 'owned_item') else '')


def _cat_must(conn, ref):
    cat = _cat_get(conn, ref)
    if cat is None:
        raise Usage('category %r not found (shop-cat-list)' % (ref,))
    return cat


def _spend_cat_resolve(conn, ref):
    """S-L2: an existing ledger category by key or label (never created
    here -- the ledger owns its vocabulary)."""
    row = conn.execute("SELECT id FROM spend_category WHERE name=? OR "
                       "display_name=? ORDER BY id LIMIT 1",
                       (_ws(ref), _ws(ref))).fetchone()
    if row is None:
        raise Refuse([('SPEND_CATEGORY_UNKNOWN', _ws(ref))], [
            'SPEND_CATEGORY\t%s\t%s' % r for r in conn.execute(
                "SELECT name, COALESCE(display_name, name) FROM spend_category "
                "WHERE active=1 AND kind='expense' ORDER BY sort, id")])
    return row[0]


def cat_upd(conn, ref, display=None, consumable=None, checks=None,
            active=None, spend_category=None):
    cat = _cat_must(conn, ref)
    sets, args = [], []
    backfill = None
    if spend_category is not None:
        if not _table_exists(conn, 'owned_item'):
            raise Usage('--spend-category needs migration 072')
        sid = None if spend_category.strip().lower() == 'none' \
            else _spend_cat_resolve(conn, spend_category)
        sets.append('spend_category_id=?')
        args.append(sid)
        backfill = sid
    if display is not None:
        sets.append('display_name=?')
        args.append(None if display.strip().lower() == 'none' else _ws(display))
    if consumable is not None:
        sets.append('consumable=?')
        args.append(int(consumable))
    if checks is not None:
        sets.append('checks_required=?')
        args.append(_checks_arg(checks))
    if active is not None:
        sets.append('active=?')
        args.append(int(active))
    if not sets:
        raise Usage('nothing to change (--display --consumable --checks '
                    '--active --spend-category)')
    conn.execute('UPDATE shop_category SET %s WHERE id=?' % ', '.join(sets),
                 args + [cat['id']])
    if backfill is not None:
        # the answer to the one S-L2 question also files the purchases of
        # this kind that went into the ledger uncategorized while it was open
        conn.execute('UPDATE spend_entry SET category_id=? WHERE category_id '
                     'IS NULL AND id IN (SELECT o.spend_id FROM owned_item o '
                     'JOIN shop_item i ON i.id=o.item_id WHERE i.category_id=?)',
                     (backfill, cat['id']))
    return _cat_get(conn, cat['id'])


def cat_del(conn, ref):
    cat = _cat_must(conn, ref)
    counts = _cat_counts(conn, cat['id'])
    if any(n for _k, n in counts):
        raise Refuse([('CAT_IN_USE', ' '.join('%s=%d' % kv for kv in counts)
                       + ' -- retire it instead (shop-cat-retire)')])
    conn.execute('DELETE FROM shop_category WHERE id=?', (cat['id'],))
    return cat


# -- item -------------------------------------------------------------------

ITEM_COLS = ('id, ulid, category_id, name, brand, variant, url, image_ref, '
             'aliases, consumable_override')


def _item_dict(row):
    return dict(zip(('id', 'ulid', 'category_id', 'name', 'brand', 'variant',
                     'url', 'image_ref', 'aliases', 'consumable_override'),
                    row))


def _item_get(conn, ref):
    ref = str(ref).strip()
    col = 'id' if ref.isdigit() else 'ulid'
    row = conn.execute('SELECT %s FROM shop_item WHERE %s=?' % (ITEM_COLS, col),
                       (int(ref) if col == 'id' else ref,)).fetchone()
    return _item_dict(row) if row else None


def _item_must(conn, ref):
    item = _item_get(conn, ref)
    if item is None:
        raise Usage('item %r not found (shop-item-find)' % (ref,))
    return item


def _model_key(brand, name):
    """One product regardless of colour / size (074 set-diversity)."""
    return compact('%s %s' % (brand or '', name or ''))


def _item_exact(conn, cat_id, name, brand, variant, url):
    """The same product, exactly: the same url, or (category, brand, name,
    variant) equal after compaction, or the words equal to a stored alias."""
    if url:
        row = conn.execute('SELECT %s FROM shop_item WHERE url=? ORDER BY id '
                           'LIMIT 1' % ITEM_COLS, (url.strip(),)).fetchone()
        if row:
            return _item_dict(row)
    if not name:
        return None
    key = (_model_key(brand, name), compact(variant))
    sql = 'SELECT %s FROM shop_item' % ITEM_COLS
    args = ()
    if cat_id is not None:
        sql += ' WHERE category_id=?'
        args = (cat_id,)
    qn = _ws(name)
    for row in conn.execute(sql + ' ORDER BY id', args):
        it = _item_dict(row)
        if (_model_key(it['brand'], it['name']), compact(it['variant'])) == key:
            return it
        if brand is None and variant is None and qn in _json_list(it['aliases']):
            return it
    return None


def _item_fill(conn, item, brand, variant, url, image):
    """Upsert half: a field the store lacks is filled; a stored value is never
    overwritten here (shop-item-upd changes values)."""
    filled = []
    for col, val in (('brand', brand), ('variant', variant), ('url', url),
                     ('image_ref', image)):
        if val and not item.get(col):
            conn.execute('UPDATE shop_item SET %s=? WHERE id=?' % col,
                         (_ws(val) if col in ('brand', 'variant') else
                          val.strip(), item['id']))
            filled.append(col)
    return filled


def item_upsert(K, conn, cat, name, brand=None, variant=None, url=None,
                image=None, new_confirmed=False, near_gate=True):
    """-> (status, item, filled).  status EXISTS | ADDED.  near_gate=False is
    the round path: a seller's product title is exact text, not a name the
    owner recalls from memory, so only exact identity applies there."""
    name = _ws(name)
    if not name:
        raise Usage('item name required')
    hit = _item_exact(conn, cat['id'], name, brand, variant, url)
    if hit is not None:
        return 'EXISTS', hit, _item_fill(conn, hit, brand, variant, url, image)
    if near_gate:
        K._near_gate_new_confirmed(conn, 'item', name, new_confirmed)
    ulid = K.new_ulid()
    conn.execute(
        'INSERT INTO shop_item (ulid, category_id, name, brand, variant, url, '
        'image_ref) VALUES (?,?,?,?,?,?,?)',
        (ulid, cat['id'], name, _ws(brand) or None, _ws(variant) or None,
         (url or '').strip() or None, (image or '').strip() or None))
    return 'ADDED', _item_get(conn, ulid), []


def _item_line(conn, it):
    cat = conn.execute('SELECT name FROM shop_category WHERE id=?',
                       (it['category_id'],)).fetchone()
    return 'ITEM\t%d\t%s\t%s\t%s\t%s\t%s\t%s' % (
        it['id'], cat[0] if cat else '-', it['name'], it['brand'] or '-',
        it['variant'] or '-', it['url'] or '-', it['image_ref'] or '-')


def item_find(K, conn, words, cat=None):
    """Exact identity first (name / brand+name / alias), then the near list.
    Read only."""
    q = _ws(words)
    qk = compact(q)
    exact = []
    sql = 'SELECT %s FROM shop_item' % ITEM_COLS
    args = ()
    if cat is not None:
        sql += ' WHERE category_id=?'
        args = (cat['id'],)
    for row in conn.execute(sql + ' ORDER BY id', args):
        it = _item_dict(row)
        names = [it['name'], '%s %s' % (it['brand'] or '', it['name'])] + \
            _json_list(it['aliases'])
        if any(compact(n) == qk for n in names):
            exact.append(it)
    near = [] if exact else K.near_candidates(conn, 'item', q)
    if cat is not None:
        near = [c for c in near
                if (_item_get(conn, c['ref']) or {}).get('category_id')
                == cat['id']]
    return exact, near


def item_upd(conn, ref, name=None, brand=None, variant=None, url=None,
             image=None, alias=None, consumable=None, cat=None):
    it = _item_must(conn, ref)
    sets, args = [], []
    for col, val in (('name', name), ('brand', brand), ('variant', variant),
                     ('url', url), ('image_ref', image)):
        if val is None:
            continue
        if col != 'name' and val.strip().lower() == 'none':
            val = None
        elif col == 'name' and not _ws(val):
            raise Usage('name cannot be empty')
        sets.append('%s=?' % col)
        args.append(_ws(val) if val is not None else None)
    if consumable is not None:
        sets.append('consumable_override=?')
        args.append(None if str(consumable).lower() == 'none'
                    else int(consumable))
    if cat is not None:
        sets.append('category_id=?')
        args.append(cat['id'])
    if alias:
        have = _json_list(it['aliases'])
        if _ws(alias) not in have:
            have.append(_ws(alias))
        sets.append('aliases=?')
        args.append(json.dumps(have, ensure_ascii=False))
    if not sets:
        raise Usage('nothing to change')
    conn.execute('UPDATE shop_item SET %s WHERE id=?' % ', '.join(sets),
                 args + [it['id']])
    return _item_get(conn, it['id'])


def _item_refs(conn, iid):
    out = [('shown', [r[0] for r in conn.execute(
        'SELECT id FROM shop_shown WHERE item_id=? ORDER BY id', (iid,))])]
    for t in SIDE_TABLES:
        if _table_exists(conn, t):
            out.append((t.split('_')[0], [r[0] for r in conn.execute(
                'SELECT id FROM %s WHERE item_id=? ORDER BY id' % t, (iid,))]))
    if _table_exists(conn, 'shop_capture'):
        out.append(('capture', [r[0] for r in conn.execute(
            'SELECT id FROM shop_capture WHERE item_id=? ORDER BY id', (iid,))]))
    return out


def item_merge(conn, src_ref, dst_ref):
    src, dst = _item_must(conn, src_ref), _item_must(conn, dst_ref)
    if src['id'] == dst['id']:
        raise Usage('merge needs two different items')
    clash = conn.execute(
        'SELECT a.round_id FROM shop_shown a JOIN shop_shown b ON '
        'a.round_id=b.round_id WHERE a.item_id=? AND b.item_id=?',
        (src['id'], dst['id'])).fetchall()
    if clash:
        raise Refuse([('MERGE_CONFLICT', 'both items were shown in round(s) '
                       '%s -- they were two cards, not one product'
                       % ','.join(str(r[0]) for r in clash))])
    moved = []
    for kind, ids in _item_refs(conn, src['id']):
        if not ids:
            continue
        table = {'shown': 'shop_shown', 'capture': 'shop_capture'}.get(
            kind, '%s_item' % kind)
        conn.execute('UPDATE %s SET item_id=? WHERE item_id=?' % table,
                     (dst['id'], src['id']))
        moved.append('%s=%s' % (kind, ','.join(str(i) for i in ids)))
    if _table_exists(conn, 'stock_use'):
        conn.execute('UPDATE stock_use SET item_id=? WHERE item_id=?', (dst['id'], src['id']))
    have = _json_list(dst['aliases'])
    for a in [src['name']] + _json_list(src['aliases']):
        if a and a != dst['name'] and a not in have:
            have.append(a)
    conn.execute('UPDATE shop_item SET aliases=? WHERE id=?',
                 (json.dumps(have, ensure_ascii=False), dst['id']))
    _item_fill(conn, dst, src['brand'], src['variant'], src['url'],
               src['image_ref'])
    conn.execute('DELETE FROM shop_item WHERE id=?', (src['id'],))
    return dst, moved


def item_del(conn, ref):
    it = _item_must(conn, ref)
    refs = [(k, ids) for k, ids in _item_refs(conn, it['id']) if ids]
    if refs:
        raise Refuse([('ITEM_IN_USE', ' '.join(
            '%s=%s' % (k, ','.join(str(i) for i in ids)) for k, ids in refs)
            + ' -- merge it (shop-item-merge) or keep it')])
    if _table_exists(conn, 'stock_use'):
        conn.execute('DELETE FROM stock_use WHERE item_id=?', (it['id'],))
    conn.execute('DELETE FROM shop_item WHERE id=?', (it['id'],))
    return it


# -- hunt -------------------------------------------------------------------

HUNT_COLS = ('id, ulid, category_id, ask_verbatim, for_person_id, budget_max, '
             'budget_hard, need_by, state, opened_at, closed_at')


def _hunt_dict(row):
    return dict(zip(('id', 'ulid', 'category_id', 'ask', 'for_person_id',
                     'budget_max', 'budget_hard', 'need_by', 'state',
                     'opened_at', 'closed_at'), row))


def _hunt_get(conn, ref):
    ref = str(ref).strip()
    col = 'id' if ref.isdigit() else 'ulid'
    row = conn.execute('SELECT %s FROM shop_hunt WHERE %s=?' % (HUNT_COLS, col),
                       (int(ref) if col == 'id' else ref,)).fetchone()
    return _hunt_dict(row) if row else None


def _hunt_must(conn, ref):
    h = _hunt_get(conn, ref)
    if h is None:
        raise Usage('hunt %r not found (shop-hunt-list)' % (ref,))
    return h


def person_resolve(K, conn, ref):
    """--for: an id or an exact name/alias; the owner is NULL (self)."""
    if ref is None:
        return None
    ref = _ws(ref)
    if ref.lower() in ('none', 'self', 'me'):
        return None
    if ref.isdigit():
        if conn.execute('SELECT 1 FROM persons WHERE id=?',
                        (int(ref),)).fetchone() is None:
            raise Refuse([('PERSON_UNKNOWN', ref)])
        return int(ref)
    hits = K._person_exact_matches(conn, ref)
    if not hits:
        raise Refuse([('PERSON_UNKNOWN', ref + ' -- person-find / person-add '
                       'first')])
    if len(hits) > 1:
        raise Refuse([('PERSON_AMBIGUOUS', ref)],
                     ['PERSON\t%d\t%s' % (h[0], h[1]) for h in hits])
    return hits[0][0]


def _person_name(conn, pid):
    if pid is None:
        return 'self'
    row = conn.execute('SELECT name FROM persons WHERE id=?', (pid,)).fetchone()
    return row[0] if row else str(pid)


def _last_activity(conn, hunt):
    row = conn.execute(
        'SELECT MAX(r.created_at), MAX(s.verdict_at) FROM shop_round r '
        'LEFT JOIN shop_shown s ON s.round_id=r.id WHERE r.hunt_id=?',
        (hunt['id'],)).fetchone()
    stamps = [_parse_stamp(x) for x in (hunt['opened_at'],) + tuple(row) if x]
    stamps = [s for s in stamps if s]
    return max(stamps) if stamps else None


def expire_pass(conn):
    """Open hunts idle more than HUNT_EXPIRY_DAYS -> expired (D11 rule 5).
    Rounds and verdicts stay: expiry cleans nothing.  Returns expired ids."""
    out = []
    cut = _now() - datetime.timedelta(days=HUNT_EXPIRY_DAYS)
    for row in conn.execute("SELECT %s FROM shop_hunt WHERE state='open'"
                            % HUNT_COLS).fetchall():
        h = _hunt_dict(row)
        last = _last_activity(conn, h)
        if last is not None and last < cut:
            out.append(h['id'])
    for hid in out:
        conn.execute("UPDATE shop_hunt SET state='expired', closed_at=? "
                     "WHERE id=?", (_stamp(), hid))
    return out


def hunt_open(K, conn, cat_ref, ask, for_ref=None, budget=None, by=None,
              new=False):
    """-> (status, hunt, changed).  status OPENED | RESUMED | EXPIRED (an
    expired hunt for the same thing exists: one resume question, nothing
    written; --new opens a fresh one)."""
    if not _ws(ask):
        raise Usage('--ask "<the owner\'s words>" is required')
    cat = cat_resolve(K, conn, cat_ref)
    pid = person_resolve(K, conn, for_ref)
    by = _date(by, '--by')
    if budget is not None and int(budget) <= 0:
        raise Usage('--budget must be a positive amount')
    expire_pass(conn)
    row = conn.execute(
        "SELECT %s FROM shop_hunt WHERE state='open' AND category_id=? AND "
        "COALESCE(for_person_id,0)=?" % HUNT_COLS,
        (cat['id'], pid or 0)).fetchone()
    if row:
        h = _hunt_dict(row)
        changed = []
        if budget is not None and int(budget) != h['budget_max']:
            conn.execute('UPDATE shop_hunt SET budget_max=? WHERE id=?',
                         (int(budget), h['id']))
            changed.append('budget_max')
        if by is not None and by != h['need_by']:
            conn.execute('UPDATE shop_hunt SET need_by=? WHERE id=?',
                         (by, h['id']))
            changed.append('need_by')
        return 'RESUMED', _hunt_get(conn, h['id']), changed
    if not new:
        row = conn.execute(
            "SELECT %s FROM shop_hunt WHERE state='expired' AND category_id=? "
            "AND COALESCE(for_person_id,0)=? ORDER BY id DESC LIMIT 1"
            % HUNT_COLS, (cat['id'], pid or 0)).fetchone()
        if row:
            return 'EXPIRED', _hunt_dict(row), []
    ulid = K.new_ulid()
    conn.execute(
        'INSERT INTO shop_hunt (ulid, category_id, ask_verbatim, '
        'for_person_id, budget_max, need_by, opened_at) VALUES (?,?,?,?,?,?,?)',
        (ulid, cat['id'], ask.strip(), pid,
         int(budget) if budget is not None else None, by, _stamp()))
    return 'OPENED', _hunt_get(conn, ulid), []


def _rounds(conn, hid):
    return conn.execute(
        'SELECT id, round_no, sources_seen, sources_blocked, assumption, '
        'partial_reason, created_at FROM shop_round WHERE hunt_id=? '
        'ORDER BY round_no', (hid,)).fetchall()


SHOWN_COLS = ('s.id, s.round_id, s.slot, s.rank, s.item_id, s.direction, '
              's.price_shown, s.ship_by, s.source, s.checks, s.reasons, '
              's.review_count, s.verdict, s.reject_scope, s.verdict_verbatim, '
              's.closest, i.name, i.brand, i.variant, i.url, i.image_ref')


def _shown_dict(row):
    return dict(zip(('id', 'round_id', 'slot', 'rank', 'item_id', 'direction',
                     'price', 'ship_by', 'source', 'checks', 'reasons',
                     'reviews', 'verdict', 'scope', 'verbatim', 'closest',
                     'name', 'brand', 'variant', 'url', 'image'), row))


def _shown_of_round(conn, rid):
    return [_shown_dict(r) for r in conn.execute(
        'SELECT %s FROM shop_shown s JOIN shop_item i ON i.id=s.item_id '
        'WHERE s.round_id=? ORDER BY s.slot' % SHOWN_COLS, (rid,))]


def _shown_of_hunt(conn, hid):
    return [_shown_dict(r) for r in conn.execute(
        'SELECT %s FROM shop_shown s JOIN shop_item i ON i.id=s.item_id '
        'JOIN shop_round r ON r.id=s.round_id WHERE r.hunt_id=? '
        'ORDER BY r.round_no, s.slot' % SHOWN_COLS, (hid,))]


def _is_pick(card):
    return card['verdict'] in ('picked', 'accepted_default')


def _over(card, hunt):
    return hunt['budget_max'] is not None and card['price'] is not None \
        and card['price'] > hunt['budget_max']


def _blind_streak(conn, hid):
    """Rounds since the last round that had a pick (all verdicted)."""
    n = 0
    for rid in [r[0] for r in reversed(_rounds(conn, hid))]:
        cards = _shown_of_round(conn, rid)
        if any(_is_pick(c) for c in cards):
            break
        n += 1
    return n


def expect(conn, hunt):
    """The input the ledger expects next (spec A2 shop-hunt-cursor):
      none            hunt not open
      round           the agent's move: serve the next round
      verdict         the owner has not answered the latest round
      direction       the latest round was rejected whole: ONE follow-up
                      ("which is least off?"), answered with --closest
      budget_decision a pick is above budget_max (Q-budget)
      close           a pick within budget: buy / close, or another round
      reaim           REAIM_EVERY rounds without a pick: narrow or stop"""
    if hunt['state'] != 'open':
        return 'none'
    rounds = _rounds(conn, hunt['id'])
    if not rounds:
        return 'round'
    cards = _shown_of_round(conn, rounds[-1][0])
    picks = [c for c in cards if _is_pick(c)]
    if picks:
        if any(_over(c, hunt) for c in picks):
            return 'round' if hunt['budget_hard'] else 'budget_decision'
        return 'close'
    if any(c['verdict'] is None for c in cards):
        return 'verdict'
    live = [c for c in cards if c['verdict'] != 'void']
    streak = _blind_streak(conn, hunt['id'])
    if streak and streak % REAIM_EVERY == 0:
        return 'reaim'
    if live and all(c['verdict'] == 'rejected' and c['scope'] == 'round'
                    for c in live) and not any(c['closest'] for c in live):
        return 'direction'
    return 'round'


def _dropped_directions(conn, hid):
    out = []
    for c in _shown_of_hunt(conn, hid):
        if c['verdict'] == 'rejected' and c['scope'] == 'round' \
                and not c['closest'] and c['direction'] not in out:
            out.append(c['direction'])
    return out


def _rejected_items(conn, cat_id):
    return conn.execute(
        "SELECT DISTINCT i.id, i.name FROM shop_shown s JOIN shop_item i ON "
        "i.id=s.item_id WHERE s.verdict='rejected' AND s.reject_scope='item' "
        "AND i.category_id=? ORDER BY i.id", (cat_id,)).fetchall()


def _card_flags(card, hunt, floor):
    flags = []
    if _over(card, hunt):
        flags.append('OVER_BUDGET')
    if hunt['need_by'] and card['ship_by'] and card['ship_by'] > hunt['need_by']:
        flags.append('LATE')
    if card['reviews'] is not None and card['reviews'] < floor:
        flags.append('THIN_REVIEWS')
    return flags


def _card_title(c):
    return ' '.join(x for x in (c['brand'], c['name'], c['variant']) if x)


def cursor_lines(K, conn, hunt):
    cat = _cat_get(conn, hunt['category_id'])
    lines = ['HUNT\t%d\t%s\t%s\t%s\t%s' % (
        hunt['id'], hunt['state'], cat['name'], cat_label(K, cat),
        _ws(hunt['ask']))]
    lines.append('CONSTRAINTS\tbudget=%s\thard=%d\tneed_by=%s\tfor=%s' % (
        hunt['budget_max'] if hunt['budget_max'] is not None else 'none',
        hunt['budget_hard'], hunt['need_by'] or 'none',
        _person_name(conn, hunt['for_person_id'])))
    rounds = _rounds(conn, hunt['id'])
    floor = review_floor(conn)
    for rid, no, _seen, _blocked, _a, _p, _ts in rounds[:-1]:
        cards = _shown_of_round(conn, rid)
        lines.append('ROUND_SUMMARY\t%d\tpicked=%s\tclosest=%s' % (
            no, ','.join(str(c['slot']) for c in cards if _is_pick(c)) or '-',
            ','.join(str(c['slot']) for c in cards if c['closest']) or '-'))
    if rounds:
        rid, no = rounds[-1][0], rounds[-1][1]
        cards = _shown_of_round(conn, rid)
        lines.append('ROUND\t%d\t%d' % (no, len(cards)))
        for c in cards:
            lines.append('CARD\t%d\t%d\t%s\t%s\t%s\t%s\t%s' % (
                c['slot'], c['id'], c['direction'], _card_title(c),
                c['price'] if c['price'] is not None else '-',
                ','.join(_card_flags(c, hunt, floor)) or '-',
                (c['verdict'] or '-') + ('/' + c['scope'] if c['scope'] else '')
                + ('/closest' if c['closest'] else '')))
    for c in _shown_of_hunt(conn, hunt['id']):
        if _is_pick(c):
            lines.append('PICKED\t%d\t%s\t%s\t%s' % (
                c['id'], c['direction'], _card_title(c),
                c['price'] if c['price'] is not None else '-'))
    for d in _dropped_directions(conn, hunt['id']):
        lines.append('DROPPED_DIRECTION\t%s' % d)
    for iid, name in _rejected_items(conn, hunt['category_id']):
        lines.append('REJECTED_ITEM\t%d\t%s' % (iid, name))
    if hunt['state'] == 'open':
        kr, gl = sources(conn)
        lines.append('SOURCES\tkr=%s\tglobal=%s' % (','.join(kr), ','.join(gl)))
    if hunt['state'] == 'open':
        lines += cursor_p2_lines(conn, hunt)
    last = _last_activity(conn, hunt)
    if last is not None:
        lines.append('IDLE_DAYS\t%d' % max(0, (_now() - last).days))
    exp = expect(conn, hunt)
    lines.append('EXPECT\t%s' % exp)
    if exp == 'reaim':
        lines.append('ASK\t%s' % _say(
            K, 'shop.reaim.ask', 'Nothing has fit after a few rounds. '
            'Narrow it down, or stop here?'))
    return lines


def hunt_upd(K, conn, ref, ask=None, budget=None, by=None, for_ref=None,
             hard=None, reopen=False):
    h = _hunt_must(conn, ref)
    sets, args = [], []
    if ask is not None:
        if not _ws(ask):
            raise Usage('--ask cannot be empty')
        sets.append('ask_verbatim=?')
        args.append(ask.strip())
    new_budget = h['budget_max']
    if budget is not None:
        if str(budget).lower() == 'none':
            new_budget = None
        else:
            new_budget = int(budget)
            if new_budget <= 0:
                raise Usage('--budget must be a positive amount or none')
        sets.append('budget_max=?')
        args.append(new_budget)
    if by is not None:
        sets.append('need_by=?')
        args.append(None if str(by).lower() == 'none' else _date(by, '--by'))
    if for_ref is not None:
        sets.append('for_person_id=?')
        args.append(person_resolve(K, conn, for_ref))
    if hard is not None:
        if int(hard) and new_budget is None:
            raise Refuse([('NO_BUDGET', 'a hard budget needs --budget first')])
        sets.append('budget_hard=?')
        args.append(int(hard))
    elif budget is not None and new_budget is None:
        sets.append('budget_hard=0')
    if reopen:
        if h['state'] not in ('expired', 'abandoned'):
            raise Refuse([('NOT_REOPENABLE', 'state=%s' % h['state'])])
        sets.append("state='open'")
        sets.append('closed_at=NULL')
    if not sets:
        raise Usage('nothing to change (--ask --budget --by --for '
                    '--budget-hard --reopen)')
    try:
        conn.execute('UPDATE shop_hunt SET %s WHERE id=?' % ', '.join(sets),
                     args + [h['id']])
    except sqlite3.IntegrityError as e:
        if 'ux_shop_hunt_open' in str(e) or 'UNIQUE' in str(e):
            raise Refuse([('HUNT_OPEN_EXISTS', 'another open hunt covers the '
                           'same category and person')])
        raise
    return _hunt_get(conn, h['id'])


def _shown_must(conn, sid, hunt=None):
    row = conn.execute(
        'SELECT %s FROM shop_shown s JOIN shop_item i ON i.id=s.item_id '
        'WHERE s.id=?' % SHOWN_COLS, (int(sid),)).fetchone()
    if row is None:
        raise Usage('shown card %r not found (shop-shown-list)' % (sid,))
    card = _shown_dict(row)
    if hunt is not None:
        hid = conn.execute('SELECT hunt_id FROM shop_round WHERE id=?',
                           (card['round_id'],)).fetchone()[0]
        if hid != hunt['id']:
            raise Refuse([('NOT_THIS_HUNT', 'card %s belongs to hunt %s'
                           % (card['id'], hid))])
    return card


def hunt_close(K, conn, ref, picked=None, abandon=False, to_wish=None):
    h = _hunt_must(conn, ref)
    if h['state'] != 'open':
        raise Refuse([('HUNT_NOT_OPEN', 'state=%s' % h['state'])])
    if to_wish is not None:
        raise Refuse([('WISH_NOT_ON', 'the wishlist is phase 3 of '
                       'module_shopping and is not built yet')])
    if abandon:
        conn.execute("UPDATE shop_hunt SET state='abandoned', closed_at=? "
                     "WHERE id=?", (_stamp(), h['id']))
        return _hunt_get(conn, h['id']), None
    card = _shown_must(conn, picked, h)
    if card['verdict'] == 'void':
        raise Refuse([('CARD_VOID', 'card %d was voided' % card['id'])])
    if _over(card, h):
        raise Refuse([('OVER_BUDGET', 'slot=%d price=%d budget=%d diff=%d -- '
                       'ask the owner (Q-budget); "this one" -> shop-hunt-upd '
                       '--budget %d' % (card['slot'], card['price'],
                                        h['budget_max'],
                                        card['price'] - h['budget_max'],
                                        card['price']))])
    if not _is_pick(card):
        conn.execute("UPDATE shop_shown SET verdict='picked', "
                     "reject_scope=NULL, closest=0, verdict_at=? WHERE id=?",
                     (_stamp(), card['id']))
    conn.execute("UPDATE shop_hunt SET state='picked', closed_at=? WHERE id=?",
                 (_stamp(), h['id']))
    return _hunt_get(conn, h['id']), card


def hunt_del(conn, ref):
    h = _hunt_must(conn, ref)
    if _table_exists(conn, 'owned_item'):
        owned = [r[0] for r in conn.execute(
            'SELECT id FROM owned_item WHERE hunt_id=?', (h['id'],))]
        if owned:
            raise Refuse([('HUNT_HAS_PURCHASE', 'owned=%s'
                           % ','.join(str(i) for i in owned))])
    rids = [r[0] for r in _rounds(conn, h['id'])]
    n_cards = 0
    for rid in rids:
        n_cards += conn.execute('DELETE FROM shop_shown WHERE round_id=?',
                                (rid,)).rowcount
    conn.execute('DELETE FROM shop_round WHERE hunt_id=?', (h['id'],))
    conn.execute('DELETE FROM shop_hunt WHERE id=?', (h['id'],))
    return h, len(rids), n_cards


# -- round ------------------------------------------------------------------

def _int_or_none(v):
    if v is None or v == '':
        return None
    if isinstance(v, bool):
        raise ValueError(v)
    if isinstance(v, (int, float)):
        return int(v)
    return int(re.sub(r'[,\s]', '', str(v)))


def _parse_card(i, raw, need_ship):
    """-> (card dict, [(code, detail)])."""
    errs = []
    try:
        card = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError as e:
        return None, [('BAD_CARD', '%d %s' % (i, e))]
    if not isinstance(card, dict):
        return None, [('BAD_CARD', '%d not a JSON object' % i)]
    for f in CARD_FIELDS:
        v = card.get(f)
        if v is None or (isinstance(v, str) and not v.strip()):
            errs.append(('MISSING_FIELD', '%d %s' % (i, f)))
    if need_ship and not card.get('ship_by'):
        errs.append(('MISSING_FIELD', '%d ship_by (the hunt has a need_by)'
                     % i))
    try:
        card['price'] = _int_or_none(card.get('price'))
        if card['price'] is not None and card['price'] < 0:
            raise ValueError(card['price'])
    except (TypeError, ValueError):
        errs.append(('BAD_CARD', '%d price is not an amount' % i))
        card['price'] = None
    try:
        card['reviews'] = _int_or_none(card.get('reviews'))
    except (TypeError, ValueError):
        errs.append(('BAD_CARD', '%d reviews is not a count' % i))
        card['reviews'] = None
    if card.get('ship_by'):
        try:
            card['ship_by'] = _date(card['ship_by'], 'ship_by')
        except Usage as e:
            errs.append(('BAD_CARD', '%d %s' % (i, e)))
    reasons = card.get('reasons') or []
    if not isinstance(reasons, list) or \
            not all(isinstance(r, str) for r in reasons):
        errs.append(('BAD_CARD', '%d reasons must be a list of strings' % i))
        reasons = []
    if len(reasons) > MAX_REASONS:
        errs.append(('BAD_CARD', '%d at most %d reasons' % (i, MAX_REASONS)))
    card['reasons'] = reasons[:MAX_REASONS]
    if card.get('checks') is not None and not isinstance(card['checks'], dict):
        errs.append(('BAD_CARD', '%d checks must be an object' % i))
        card['checks'] = {}
    return card, errs


def _check_errors(i, card, required):
    checks = card.get('checks') or {}
    errs = []
    for k in required:
        if checks.get(k) is not True:
            errs.append(('UNVERIFIED_CARD', '%d %s' % (i, k)))
    for k, v in sorted(checks.items()):
        if k not in required and v is not True:
            errs.append(('UNVERIFIED_CARD', '%d %s' % (i, k)))
    return errs


def round_add(K, conn, hunt_ref, seen, blocked=None, assume=None,
              partial=None, reaimed=False, cards=()):
    """Gate every card, then write the round.  -> round id.  Raises Refuse
    with every failing rule at once (nothing written)."""
    h = _hunt_must(conn, hunt_ref)
    expire_pass(conn)
    h = _hunt_get(conn, h['id'])
    errs = []
    if h['state'] != 'open':
        raise Refuse([('HUNT_NOT_OPEN', 'state=%s' % h['state'])])
    seen, blocked = _csv(seen), _csv(blocked)
    if not seen:
        errs.append(('NO_COVERAGE', '--seen names no source'))
    if partial is not None and not _ws(partial):
        raise Usage('--partial needs the reason')
    cap = round_size(conn)
    low = 1 if partial else min(ROUND_MIN, cap)
    if not low <= len(cards) <= cap:
        errs.append(('ROUND_SIZE', 'n=%d min=%d max=%d' % (len(cards), low, cap)))
    streak = _blind_streak(conn, h['id'])
    if streak and streak % REAIM_EVERY == 0 and not reaimed:
        errs.append(('REAIM_DUE', '%d rounds without a pick -- ask the owner '
                     'to narrow or stop, then --reaimed' % streak))
    cat = _cat_get(conn, h['category_id'])
    required = _json_list(cat['checks_required'])
    parsed = []
    for i, raw in enumerate(cards, 1):
        card, cerrs = _parse_card(i, raw, bool(h['need_by']))
        errs.extend(cerrs)
        if card is None:
            continue
        errs.extend(_check_errors(i, card, required))
        parsed.append((i, card))
    # identity of each card against the store (read only here)
    for i, card in parsed:
        hit = None
        if card.get('name'):
            hit = _item_exact(conn, cat['id'], _ws(card['name']),
                              card.get('brand'), card.get('variant'),
                              card.get('url'))
        card['_item'] = hit['id'] if hit else None
        card['_model'] = _model_key(card.get('brand'), card.get('name'))
        card['_dir'] = compact(card.get('direction'))
    for a in range(len(parsed)):
        for b in range(a + 1, len(parsed)):
            (ia, ca), (ib, cb) = parsed[a], parsed[b]
            same = (ca['_item'] and ca['_item'] == cb['_item']) or \
                (ca.get('url') and ca.get('url') == cb.get('url')) or \
                (ca['_model'] and ca['_model'] == cb['_model'])
            if same:
                errs.append(('DUP_ITEM', '%d,%d' % (ia, ib)))
            if ca['_dir'] and ca['_dir'] == cb['_dir']:
                errs.append(('DUP_DIRECTION', '%d,%d' % (ia, ib)))
    dropped = set(compact(d) for d in _dropped_directions(conn, h['id']))
    rejected = set(r[0] for r in _rejected_items(conn, cat['id']))
    earlier = {}
    for c in _shown_of_hunt(conn, h['id']):
        if c['verdict'] != 'void':
            earlier.setdefault(c['item_id'], []).append(c)
    for i, card in parsed:
        if card['_dir'] and card['_dir'] in dropped:
            errs.append(('DROPPED_DIRECTION', '%d %s' % (i, card['direction'])))
        iid = card['_item']
        if iid in rejected:
            errs.append(('REJECTED_ITEM', '%d item=%d' % (i, iid)))
        elif iid in earlier and not any(_is_pick(c) for c in earlier[iid]):
            errs.append(('SHOWN_BEFORE', '%d item=%d' % (i, iid)))
        if h['budget_hard'] and card['price'] is not None \
                and card['price'] > h['budget_max']:
            errs.append(('OVER_BUDGET', '%d price=%d budget=%d (hard)'
                         % (i, card['price'], h['budget_max'])))
    errs.extend(_avoid_hits(conn, h, parsed))
    if errs:
        raise Refuse(errs)
    # write
    total_before = conn.execute('SELECT COUNT(*) FROM shop_round').fetchone()[0]
    no = (conn.execute('SELECT MAX(round_no) FROM shop_round WHERE hunt_id=?',
                       (h['id'],)).fetchone()[0] or 0) + 1
    cur = conn.execute(
        'INSERT INTO shop_round (hunt_id, round_no, sources_seen, '
        'sources_blocked, assumption, partial_reason, created_at) '
        'VALUES (?,?,?,?,?,?,?)',
        (h['id'], no, json.dumps(seen, ensure_ascii=False),
         json.dumps(blocked, ensure_ascii=False), _ws(assume) or None,
         _ws(partial) or None, _stamp()))
    rid = cur.lastrowid
    n = len(parsed)
    for i, card in parsed:
        _st, item, _f = item_upsert(K, conn, cat, card['name'],
                                    card.get('brand'), card.get('variant'),
                                    card.get('url'), card.get('image'),
                                    near_gate=False)
        conn.execute(
            'INSERT INTO shop_shown (round_id, slot, rank, item_id, direction, '
            'price_shown, ship_by, source, checks, reasons, review_count) '
            'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            (rid, slot_for(i - 1, n, total_before), i, item['id'],
             _ws(card['direction']), card['price'], card.get('ship_by'),
             _ws(card['source']),
             json.dumps(card['checks'], ensure_ascii=False, sort_keys=True),
             json.dumps(card['reasons'], ensure_ascii=False),
             card['reviews']))
    return rid


def slot_for(rank0, n, rotation):
    """074 rotate-slots: the model's top card sits in slot 1, 2, 3 ... on
    successive rounds store-wide; deterministic, so the position bias of a
    pick can be read back from (slot, rank)."""
    return ((rank0 + rotation) % n) + 1


def render_lines(K, conn, rid):
    row = conn.execute(
        'SELECT hunt_id, round_no, sources_seen, sources_blocked, assumption, '
        'partial_reason FROM shop_round WHERE id=?', (rid,)).fetchone()
    hid, no, seen, blocked, assume, partial = row
    h = _hunt_get(conn, hid)
    seen, blocked = _json_list(seen), _json_list(blocked)
    cards = _shown_of_round(conn, rid)
    floor = review_floor(conn)
    lines = ['ROUND\t%d\t%d\t%d' % (hid, no, len(cards))]
    lines.append('COVERAGE\tseen=%s\tblocked=%s\tbudget=%s\tassume=%s' % (
        ','.join(seen), ','.join(blocked) or '-',
        h['budget_max'] if h['budget_max'] is not None else 'none',
        assume or '-'))
    parts = [_say(K, 'shop.coverage.seen', 'Looked at: {seen}',
                  seen=', '.join(seen))]
    if blocked:
        parts.append(_say(K, 'shop.coverage.blocked',
                          'Could not look at: {blocked}',
                          blocked=', '.join(blocked)))
    if h['budget_max'] is None:
        parts.append(_say(K, 'shop.coverage.no_budget', 'No budget set'))
    if assume:
        parts.append(_say(K, 'shop.coverage.assume', 'Assuming {assume}',
                          assume=assume))
    lines.append('SAY_COVERAGE\t%s' % _say(K, 'shop.coverage.sep', ' / ')
                 .join(parts))
    if partial:
        lines.append('PARTIAL\t%s' % partial)
    for c in cards:
        flags = _card_flags(c, h, floor)
        labels = []
        for f in flags:
            labels.append(_say(K, 'shop.flag.%s' % f.lower(),
                               f.lower().replace('_', ' ')))
        lines.append('CARD\t%d\t%d\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s' % (
            c['slot'], c['id'], c['direction'], _card_title(c),
            c['price'] if c['price'] is not None else '-',
            c['ship_by'] or '-', c['source'], c['url'] or '-',
            c['image'] or '-', ','.join(flags) or '-',
            ';'.join(labels) or '-', ';'.join(_json_list(c['reasons'])) or '-'))
    priced = [c['price'] for c in cards if c['price'] is not None]
    if h['budget_max'] is not None and priced and \
            min(priced) > h['budget_max']:
        lo = min(priced)
        lines.append('SHORTFALL\tmin_price=%d\tbudget=%d\tgap=%d' % (
            lo, h['budget_max'], lo - h['budget_max']))
        lines.append('SAY_SHORTFALL\t%s' % _say(
            K, 'shop.shortfall', 'Cheapest under these conditions is {min} '
            'won, {gap} won over your {budget} won.', min=_won(lo),
            budget=_won(h['budget_max']), gap=_won(lo - h['budget_max'])))
    lines.append('ASK\t%s' % _say(
        K, 'shop.round.ask', 'Any of these catch your eye? If none, just say '
        'which comes closest.'))
    return lines


def _round_of(conn, hunt, round_no=None):
    rounds = _rounds(conn, hunt['id'])
    if not rounds:
        raise Refuse([('NO_ROUND', 'hunt %d has no round yet' % hunt['id'])])
    if round_no is None:
        return rounds[-1]
    for r in rounds:
        if r[1] == int(round_no):
            return r
    raise Refuse([('NO_ROUND', 'round %s of hunt %d' % (round_no, hunt['id']))],
                 ['ROUND_NO\t%d' % r[1] for r in rounds])


def round_upd(conn, rid, seen=None, blocked=None, assume=None):
    if conn.execute('SELECT 1 FROM shop_round WHERE id=?',
                    (int(rid),)).fetchone() is None:
        raise Usage('round %r not found' % (rid,))
    sets, args = [], []
    if seen is not None:
        if not _csv(seen):
            raise Refuse([('NO_COVERAGE', '--seen names no source')])
        sets.append('sources_seen=?')
        args.append(json.dumps(_csv(seen), ensure_ascii=False))
    if blocked is not None:
        sets.append('sources_blocked=?')
        args.append(json.dumps(_csv(blocked), ensure_ascii=False))
    if assume is not None:
        sets.append('assumption=?')
        args.append(None if assume.strip().lower() == 'none' else _ws(assume))
    if not sets:
        raise Usage('nothing to change (--seen --blocked --assume)')
    conn.execute('UPDATE shop_round SET %s WHERE id=?' % ', '.join(sets),
                 args + [int(rid)])
    return int(rid)


# -- verdict ----------------------------------------------------------------

def _slots(s):
    out = []
    for x in _csv(s):
        if not x.isdigit():
            raise Usage('slot must be a number: %r' % (x,))
        out.append(int(x))
    return out


def verdict(K, conn, hunt_ref, pick=None, reject=None, scope=None,
            reject_all=False, default=None, closest=None, verbatim=None,
            round_no=None):
    """Record the owner's answer to one round (latest unless --round).
    -> (hunt, [(card, verdict)], extra lines)."""
    h = _hunt_must(conn, hunt_ref)
    if h['state'] != 'open':
        raise Refuse([('HUNT_NOT_OPEN', 'state=%s' % h['state'])])
    actions = [a for a in (pick, reject, default, closest) if a] + \
        (['all'] if reject_all else [])
    if len(actions) != 1:
        raise Usage('exactly one of --pick --reject --reject-all --default '
                    '--closest')
    if scope is not None and not reject:
        raise Usage('--scope goes with --reject')
    rid = _round_of(conn, h, round_no)[0]
    cards = {c['slot']: c for c in _shown_of_round(conn, rid)}
    valid = ','.join(str(s) for s in sorted(cards))
    want = _slots(pick or reject or default or closest or '') \
        if not reject_all else sorted(cards)
    bad = [s for s in want if s not in cards]
    if bad:
        raise Refuse([('BAD_SLOT', '%s valid=%s' % (
            ','.join(str(s) for s in bad), valid))])
    if default and len(want) != 1:
        raise Usage('--default takes one slot')
    if closest:
        live = [c for c in cards.values() if c['verdict'] != 'void']
        if len(want) != 1 or not all(c['verdict'] == 'rejected' for c in live):
            raise Refuse([('NOT_REJECTED_ROUND', '--closest answers "which is '
                           'least off" after the whole round was rejected')])
    now = _stamp()
    done = []
    for s in want:
        c = cards[s]
        words = _ws(verbatim)
        vb = c['verbatim']
        if words:
            vb = (vb + '\n' + words) if vb else words
        if closest:
            conn.execute('UPDATE shop_shown SET closest=0 WHERE round_id=?',
                         (rid,))
            conn.execute('UPDATE shop_shown SET closest=1, verdict_verbatim=? '
                         'WHERE id=?', (vb, c['id']))
            done.append((c, 'closest'))
            continue
        if pick:
            v, sc = 'picked', None
        elif default:
            v, sc = 'accepted_default', None
        else:
            v, sc = 'rejected', scope or 'round'
        conn.execute('UPDATE shop_shown SET verdict=?, reject_scope=?, '
                     'closest=0, verdict_verbatim=?, verdict_at=? WHERE id=?',
                     (v, sc, vb, now, c['id']))
        done.append((c, v if sc is None else '%s/%s' % (v, sc)))
    extra = []
    if pick or default:
        for c, _v in done:
            if _over(c, h) and not h['budget_hard']:
                diff = c['price'] - h['budget_max']
                extra.append('OVER_BUDGET\t%d\t%d\t%d\t%d' % (
                    c['slot'], c['price'], h['budget_max'], diff))
                title = _card_title(c)
                extra.append('ASK\t%s' % _say(
                    K, 'shop.budget.ask', '{item} is {price} won, {diff} won '
                    'over your {budget} won. Go with it, or look for something '
                    'similar within budget?', item=title,
                    eun=K._josa(title, 'eun'), price=_won(c['price']),
                    budget=_won(h['budget_max']), diff=_won(diff)))
    if reject_all:
        extra.append('ASK\t%s' % _say(
            K, 'shop.round.reject_all_ask', 'Which one is the least off?'))
    return _hunt_get(conn, h['id']), done, extra


def shown_void(conn, sid):
    c = _shown_must(conn, sid)
    conn.execute("UPDATE shop_shown SET verdict='void', reject_scope=NULL, "
                 "closest=0, verdict_at=? WHERE id=?", (_stamp(), c['id']))
    return c


# -- phase 2: purchase log, taste, measurements (migration 072) --------------
#
# Spec: build notes P2 + owner decisions 12:01-12:07 (1 ledger link ON, 2 price
# has ONE home, 3 cooling-off is an opt-in offer, 6 purchase capture fills
# from context and asks only what is missing, once) + 14:03 (body vs garment
# measurements, size draft order) + 14:25 (reflection option, KR withdrawal
# line).  Skill: shopping-log.

P2_SCHEMA_VERSION = 72
AXES = ('colour', 'fit', 'material', 'brand', 'style', 'scent', 'price_band',
        'size')
POLARITIES = ('like', 'avoid')
ORIGINS = ('stated', 'confirmed_tendency')
SATISFACTIONS = ('liked', 'neutral', 'disliked')
FITS = ('small', 'right', 'large')
OWNED_STATES = ('in_use', 'used_up', 'returned', 'gone')
# garment measurement parts (KR shops' "real size" chart), cm
PARTS = ('total_length', 'shoulder', 'chest_half', 'waist_half', 'hip_half',
         'thigh_half', 'rise', 'hem_half', 'sleeve')
# body measurement tokens (kit-shared body facts in metric_log, 072 vocab)
BODY_SIZES = ('chest_cm', 'waist_cm', 'hip_cm', 'shoulder_width_cm',
              'inseam_cm', 'arm_length_cm', 'foot_length_mm')
NEED_SIGNALS = ('runout', 'broken', 'event', 'deadline')
COOLING_DAYS_DEFAULT = 7       # owner rule (decision 3), never framed as research
COOLOFF_AT_DEFAULT = '10:00'   # local time the one reminder fires
COOLOFF_NOTE = 'shop:cooloff'  # marks the reminder block as the shopping one
LEDGER_LINK_DEFAULT = 'on'     # owner decision 1 (2026-10-07 12:05)
RECENT_OWNED = 3               # owned rows the cursor carries (record-first)
# Size draft, body tier: a garment girth must cover the body girth plus this
# much room (cm, whole-garment girth = half-width x 2).  Product constants of
# the draft rule (nearest chart row that fits), not advice spoken to anyone.
EASE_CM = {'chest_cm': 4.0, 'waist_cm': 0.0, 'hip_cm': 2.0,
           'shoulder_width_cm': 0.0}
PART_FOR_BODY = (('chest_half', 'chest_cm', 2.0), ('waist_half', 'waist_cm', 2.0),
                 ('hip_half', 'hip_cm', 2.0), ('shoulder', 'shoulder_width_cm', 1.0))
NEED_FOR_CATEGORY = {'apparel_bottom': 'waist_cm', 'shoes': 'foot_length_mm'}
TZ_LOCAL = 'Asia/Seoul'        # the kit's display zone (dates are KST)


class Exists(Exception):
    """A find-first hit: nothing written, exit 3, lines explain it."""

    def __init__(self, lines):
        Exception.__init__(self, 'EXISTS')
        self.lines = list(lines)


def p2_ready(conn):
    return _table_exists(conn, 'owned_item')


def _today():
    from zoneinfo import ZoneInfo
    now = _now().replace(tzinfo=datetime.timezone.utc)
    return now.astimezone(ZoneInfo(TZ_LOCAL)).date().isoformat()


def _plus_days(day, n):
    return (datetime.date.fromisoformat(day)
            + datetime.timedelta(days=n)).isoformat()


def ledger_link_on(conn):
    v = str(_cfg(conn, 'shop_ledger_link', LEDGER_LINK_DEFAULT)).strip().lower()
    return v not in ('off', '0', 'no', 'false')


def cooling_days(conn):
    try:
        n = int(_cfg(conn, 'shop_cooling_days', COOLING_DAYS_DEFAULT))
    except (TypeError, ValueError):
        return COOLING_DAYS_DEFAULT
    return n if 1 <= n <= 365 else COOLING_DAYS_DEFAULT


def cooloff_at(conn):
    v = str(_cfg(conn, 'shop_cooloff_at', COOLOFF_AT_DEFAULT)).strip()
    return v if re.match(r'^[0-2][0-9]:[0-5][0-9]$', v) and v < '24:00' \
        else COOLOFF_AT_DEFAULT


def shop_region(K, conn):
    """Where the owner buys: config shop_region, else the region of the base
    (home) place.  None = unknown -> no region-bound line is said."""
    r = K._region_code(_cfg(conn, 'shop_region'))
    if r:
        return r
    row = conn.execute('SELECT id FROM place WHERE is_base=1').fetchone() \
        if _table_exists(conn, 'place') else None
    if row is None:
        return None
    try:
        return K.place_region_of(conn, row[0])[0]
    except sqlite3.Error:
        return None


def _amount(v, what='--price'):
    try:
        n = _int_or_none(v)
    except (TypeError, ValueError):
        raise Usage('%s must be an amount (won): %r' % (what, v))
    if n is None or n < 0:
        raise Usage('%s must be an amount >= 0: %r' % (what, v))
    return n


# -- owned ------------------------------------------------------------------

OWNED_SQL = (
    'SELECT o.id, o.ulid, o.item_id, o.bought_on, o.store, o.link, o.hunt_id, '
    'o.spend_id, o.price, o.size_label, o.satisfaction, '
    'o.satisfaction_verbatim, o.fit, o.status, o.status_on, i.name, i.brand, '
    'i.variant, i.category_id, s.amount, i.aliases FROM owned_item o '
    'JOIN shop_item i ON i.id=o.item_id '
    'LEFT JOIN spend_entry s ON s.id=o.spend_id')


def _owned_dict(row):
    return dict(zip(('id', 'ulid', 'item_id', 'bought_on', 'store', 'link',
                     'hunt_id', 'spend_id', 'price', 'size', 'satisfaction',
                     'verbatim', 'fit', 'status', 'status_on', 'name', 'brand',
                     'variant', 'category_id', 'spend_amount', 'aliases'),
                    row))


def _owned_get(conn, ref):
    ref = str(ref).strip()
    col = 'o.id' if ref.isdigit() else 'o.ulid'
    row = conn.execute(OWNED_SQL + ' WHERE %s=?' % col,
                       (int(ref) if ref.isdigit() else ref,)).fetchone()
    result = _owned_dict(row) if row else None
    if result and _table_exists(conn, 'shop_capture_payment'):
        allocation = conn.execute('SELECT amount FROM shop_capture_payment WHERE owned_id=?', (result['id'],)).fetchone()
        if allocation:
            result['spend_amount'] = allocation[0]
    return result


def _owned_must(conn, ref):
    o = _owned_get(conn, ref)
    if o is None:
        raise Usage('owned item %r not found (shop-owned-list)' % (ref,))
    return o


def price_of(o):
    """The purchase price from its one home: the linked money row, else the
    owned row's own price (owner decision 2)."""
    return o['spend_amount'] if o['spend_id'] is not None else o['price']


def _owned_title(o):
    return ' '.join(x for x in (o['brand'], o['name'], o['variant']) if x)


def _owned_line(conn, o):
    cat = _cat_get(conn, o['category_id'])
    p = price_of(o)
    if _table_exists(conn, 'shop_capture_payment'):
        allocated = conn.execute('SELECT amount FROM shop_capture_payment WHERE owned_id=?', (o['id'],)).fetchone()
        if allocated:
            p = allocated[0]
    home = 'spend:%d' % o['spend_id'] if o['spend_id'] is not None else (
        'owned' if o['price'] is not None else '-')
    return 'OWNED\t%d\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s' % (
        o['id'], o['bought_on'], cat['name'] if cat else '-', _owned_title(o),
        o['store'] or '-', o['link'] or '-', p if p is not None else '-',
        home, o['size'] or '-', o['satisfaction'] or '-', o['fit'] or '-',
        o['status'])


def _spend_matches(conn, day, amount):
    return conn.execute(
        "SELECT e.id, e.name, e.amount, (SELECT o.id FROM owned_item o "
        "WHERE o.spend_id=e.id LIMIT 1) FROM spend_entry e WHERE e.date=? "
        "AND e.amount=? AND e.kind='expense' ORDER BY e.id",
        (day, amount)).fetchall()


def _spend_category_name(conn, cat):
    row = conn.execute('SELECT s.name FROM shop_category c JOIN spend_category '
                       's ON s.id=c.spend_category_id WHERE c.id=?',
                       (cat['id'],)).fetchone()
    return row[0] if row else None


def _spend_cat_lines(K, conn, cat):
    """S-L2: a kind with no ledger bucket asks ONCE (first linked purchase),
    then the answer is remembered on shop_category.spend_category_id."""
    key = 'shop_spendcat_asked_%s' % cat['name']
    if _spend_category_name(conn, cat) is not None or _cfg(conn, key):
        return []
    conn.execute('INSERT INTO config(key, value) VALUES (?, ?) ON CONFLICT(key) '
                 'DO UPDATE SET value=excluded.value', (key, _today()))
    cats = conn.execute(
        "SELECT name, COALESCE(display_name, name) FROM spend_category "
        "WHERE active=1 AND kind='expense' ORDER BY sort, id").fetchall()
    # 073 ask-choices: the ledger categories ARE the closed answer set; the
    # token line carries them, the exact [[OPTIONS]] marker follows the ASK.
    ac = K._ask_choices()
    out = ['SPEND_CATEGORY_ASK\t%s\t%s' % (cat['name'], ac.choices_field(
        [(n, d) for n, d in cats]))]
    for name, disp in cats:
        out.append('SPEND_CATEGORY\t%s\t%s' % (name, disp))
    label = cat_label(K, cat)
    out.append('ASK\t%s' % _say(
        K, 'shop.spendcat.ask', 'Which ledger category should {category} '
        'purchases go under?', category=label))
    if cats:
        out.append(ac.options_line('SPEND_CATEGORY_ASK', cat['name'],
                                   [(n, d) for n, d in cats]))
    return out


def price_plan(K, conn, cat, day, name, price, link_spend=None,
               new_spend=False, note=None):
    """Where a stated price goes (decisions 1 + 2).  -> (spend_id, own_price,
    lines).  Ledger on: the existing spend-add path, find-first on (date,
    amount, expense) -- a hit raises Exists (nothing written) unless the owner
    confirmed it is that row (--link-spend) or a different one (--new-spend).
    Ledger off: the price stays on the owned row."""
    if link_spend is not None:
        row = conn.execute("SELECT id, kind FROM spend_entry WHERE id=?",
                           (int(link_spend),)).fetchone()
        if row is None:
            raise Usage('spend %r not found' % (link_spend,))
        other = conn.execute('SELECT id FROM owned_item WHERE spend_id=?',
                             (row[0],)).fetchone()
        if other is not None:
            raise Refuse([('SPEND_LINKED', 'spend=%d owned=%d' % (row[0],
                                                                 other[0]))])
        return row[0], None, ['LEDGER\tlinked\t%d' % row[0]]
    if price is None:
        return None, None, ['LEDGER\tno_price']
    if not ledger_link_on(conn):
        return None, price, ['LEDGER\toff']
    if not new_spend:
        hits = _spend_matches(conn, day, price)
        if hits:
            lines = ['LEDGER_EXISTS\t%d\t%s\t%d\t%s' % (
                sid, nm, amt, ('owned=%d' % oid) if oid else '-')
                for sid, nm, amt, oid in hits]
            lines.append('ASK\t%s' % _say(
                K, 'shop.ledger.exists_ask', 'There is already a {price} won '
                'expense that day ({name}). Is it this purchase?',
                price=_won(price), name=hits[0][1]))
            raise Exists(lines)
    sid = K.spend_add(day, price, name, category=_spend_category_name(conn, cat),
                      note=note, source='chat', conn=conn, commit=False, match_captures=False, capture_experience=False)
    # record-always-on: the bucket question is the shopping module's
    # management; not eligible -> the spend lands in its default bucket.
    ask = (_spend_cat_lines(K, conn, cat)
           if K._manage_on(conn, 'module_shopping') else [])
    return sid, None, ['LEDGER\tspend\t%d\t%d' % (sid, price)] + ask


def owned_add(K, conn, item, day, store=None, link=None, hunt_id=None,
              price=None, size=None, new=False, link_spend=None,
              new_spend=False, note=None, experience_id=None):
    """Find-first on (item, bought_on).  -> (owned, ledger lines)."""
    if not new:
        hits = [_owned_dict(r) for r in conn.execute(
            OWNED_SQL + ' WHERE o.item_id=? AND o.bought_on=? ORDER BY o.id',
            (item['id'], day))]
        if hits:
            raise Exists(['EXISTS\t%d' % hits[0]['id']] +
                         [_owned_line(conn, o) for o in hits])
    cat = _cat_get(conn, item['category_id'])
    title = ' '.join(x for x in (item['brand'], item['name'], item['variant'])
                     if x)
    sid, own, lines = price_plan(K, conn, cat, day, title, price, link_spend,
                                 new_spend, note)
    ulid = K.new_ulid()
    conn.execute(
        'INSERT INTO owned_item (ulid, item_id, bought_on, store, link, '
        'hunt_id, spend_id, price, size_label, created_at) '
        'VALUES (?,?,?,?,?,?,?,?,?,?)',
        (ulid, item['id'], day, _ws(store) or None, (link or '').strip() or None,
         hunt_id, sid, own, _ws(size) or None, _stamp()))
    import pantry_stock
    oid = conn.execute('SELECT id FROM owned_item WHERE ulid=?', (ulid,)).fetchone()[0]
    pantry_stock.add_purchase(conn, oid, size=size)
    evidence = [('owned_item', oid), ('shop_item', item['id'])]
    if sid is not None:
        evidence.append(('spend_entry', sid))
    if pantry_stock.ready(conn) and conn.execute('SELECT 1 FROM owned_stock WHERE owned_id=?', (oid,)).fetchone():
        evidence.append(('owned_stock', oid))
    K.experience_ensure(conn, title, occurred_on=day, evidence=evidence, note=note,
                        event_id=experience_id)
    return _owned_get(conn, ulid), lines


def _item_from_opts(K, conn, ref=None, name=None, cat_ref=None, brand=None,
                    variant=None, url=None, new_confirmed=False):
    if ref is not None:
        return _item_must(conn, ref), None
    if not name or not cat_ref:
        raise Usage('give the item id, or --name "<n>" --category <c>')
    cat = cat_resolve(K, conn, cat_ref)
    st, it, _f = item_upsert(K, conn, cat, name, brand, variant, url, None,
                             new_confirmed)
    return _item_get(conn, it['id']), st


def _latest_pick(conn, hunt):
    picks = [c for c in _shown_of_hunt(conn, hunt['id']) if _is_pick(c)]
    return picks[-1] if picks else None


def _bought_hunt(K, conn, hunt_ref=None, shown=None):
    """The hunt card a purchase came from (decision 6: fill from context).
    -> (hunt, card) or (None, None)."""
    if shown is not None:
        card = _shown_must(conn, shown)
        hid = conn.execute('SELECT hunt_id FROM shop_round WHERE id=?',
                           (card['round_id'],)).fetchone()[0]
        return _hunt_get(conn, hid), card
    if hunt_ref is not None:
        h = _hunt_must(conn, hunt_ref)
        card = _latest_pick(conn, h)
        if card is None:
            raise Refuse([('NO_PICK', 'hunt %d has no picked card -- '
                           '--shown <id>, or name the item' % h['id'])])
        return h, card
    cands = []
    cut = _now() - datetime.timedelta(days=HUNT_EXPIRY_DAYS)
    for row in conn.execute("SELECT %s FROM shop_hunt WHERE state IN "
                            "('open','picked') ORDER BY id" % HUNT_COLS):
        h = _hunt_dict(row)
        closed = _parse_stamp(h['closed_at']) if h['closed_at'] else None
        if closed is not None and closed < cut:
            continue        # a pick closed long ago is not "the" context
        if conn.execute('SELECT 1 FROM owned_item WHERE hunt_id=?',
                        (h['id'],)).fetchone():
            continue
        card = _latest_pick(conn, h)
        if card is not None:
            cands.append((h, card))
    if len(cands) > 1:
        raise Refuse([('AMBIGUOUS_HUNT', 'more than one hunt has a pick -- '
                       '--hunt <id>')],
                     ['HUNT\t%d\t%s\t%s' % (h['id'], _ws(h['ask']),
                                            _card_title(c)) for h, c in cands])
    return cands[0] if cands else (None, None)


def _close_bought_hunt(conn, hunt, card):
    """A purchase out of a hunt closes it as picked (the owner decided; an
    overrun question is moot once paid)."""
    if hunt is None or hunt['state'] != 'open':
        return False
    if card['verdict'] not in ('picked', 'accepted_default'):
        conn.execute("UPDATE shop_shown SET verdict='picked', reject_scope=NULL, "
                     "closest=0, verdict_at=? WHERE id=?", (_stamp(), card['id']))
    conn.execute("UPDATE shop_hunt SET state='picked', closed_at=? WHERE id=?",
                 (_stamp(), hunt['id']))
    return True


def _is_online(link, card, online):
    if online is not None:
        return bool(int(online))
    if link:
        return True
    if card is not None and '.' in (card.get('source') or ''):
        return True
    return None


def _host(v):
    """Lower-cased host of a URL or a bare domain ('www.' dropped), or ''
    when v names no domain (a store typed as words)."""
    v = (v or '').strip().lower()
    if not v:
        return ''
    if '://' in v:
        v = v.split('://', 1)[1]
    v = v.split('/', 1)[0].split('?', 1)[0].split(':', 1)[0]
    if '.' not in v or ' ' in v:
        return ''
    return v[4:] if v.startswith('www.') else v


def _host_in(h, domains):
    return any(h == d or h.endswith('.' + d)
               for d in (_host(x) for x in domains) if d)


def store_is_kr(conn, link, store, card=None):
    """True iff the purchase was made at a KOREAN store (trial caution
    2.46.0-dev.2: the KR 7-day withdrawal right covers domestic e-commerce,
    not overseas direct purchase -- buying direct from amazon.com / aliexpress is
    a foreign seller's terms).  Read from the hunt card's source, then the
    link, then a store written as a domain: a '.kr' host or one of the KR
    sources (config shop_sources_kr, else the defaults) is KR; a global
    source is not.  Unknown -> False: the line is a legal claim, so it is
    said only when the store is known to be Korean."""
    kr, glob = sources(conn)
    for raw in ((card or {}).get('source'), link, store):
        h = _host(raw)
        if not h:
            continue
        if h.endswith('.kr') or _host_in(h, kr):
            return True
        if _host_in(h, glob):
            return False
    return False


def bought(K, conn, hunt_ref=None, shown=None, item_ref=None, name=None,
           cat_ref=None, brand=None, variant=None, day=None, store=None,
           link=None, price=None, size=None, online=None, new=False,
           link_spend=None, new_spend=False, note=None, new_confirmed=False, experience_id=None):
    """Purchase capture ("I bought it").  Fill from the hunt card, ask only
    what is missing (one line), write the money row when the ledger link is
    on, say the KR withdrawal line once for an online purchase at a Korean
    store (store_is_kr -- never for overseas direct purchase)."""
    day = _date(day, '--on') or _today()
    hunt, card = (None, None)
    if item_ref is None and not name or shown is not None or \
            hunt_ref is not None:
        hunt, card = _bought_hunt(K, conn, hunt_ref, shown)
    filled = []
    if card is not None:
        item = _item_get(conn, card['item_id'])
        if not _ws(store) and card.get('source'):
            store = card['source']
            filled.append('store')
        if not (link or '').strip() and card.get('url'):
            link = card['url']
            filled.append('link')
        if price is None and card.get('price') is not None:
            price = card['price']
            filled.append('price')
    else:
        if item_ref is None and not name:
            raise Refuse([('NEED_ITEM', 'no hunt pick to fill from -- name '
                           'the thing (--name "<n>" --category <c>)')],
                         ['ASK\t%s' % _say(K, 'shop.bought.ask_item',
                                           'What did you buy?')])
        item, _st = _item_from_opts(K, conn, item_ref, name, cat_ref, brand,
                                    variant, None, new_confirmed)
    o, ledger = owned_add(K, conn, item, day, store, link,
                          hunt['id'] if hunt else None, price, size, new,
                          link_spend, new_spend, note, experience_id)
    lines = ['ADDED\t%d' % o['id'], _owned_line(conn, o)]
    if filled:
        lines.append('FILLED\t%s\thunt=%d' % (','.join(filled), hunt['id']))
    lines += ledger
    if _close_bought_hunt(conn, hunt, card):
        lines.append('HUNT_CLOSED\t%d' % hunt['id'])
    missing = []
    if not o['store'] and not o['link']:
        missing.append('store')
    if price_of(o) is None:
        missing.append('price')
    title = _owned_title(o)
    # record-always-on: asking for a missing store / price is enrichment,
    # the shopping module's management; the purchase is already recorded.
    if missing and K._manage_on(conn, 'module_shopping'):
        lines.append('MISSING\t%s' % ','.join(missing))
        key = 'shop.bought.ask_%s' % '_'.join(missing)
        fb = {'shop.bought.ask_store_price': 'Where did you buy it, and for '
              'how much? A link works too.',
              'shop.bought.ask_store': 'Where did you buy it? A link works '
              'too.',
              'shop.bought.ask_price': 'How much was it?'}[key]
        lines.append('ASK\t%s' % _say(K, key, fb, item=title))
    if o['spend_id'] is not None:
        lines.append('SAY\t%s' % _say(
            K, 'shop.bought.saved_ledger', 'Logged it in shopping and the '
            'ledger ({price} won).', item=title, price=_won(price_of(o))))
    else:
        lines.append('SAY\t%s' % _say(
            K, 'shop.bought.saved', 'Logged it in shopping: {item}, {date}.',
            item=title, date=o['bought_on']))
    if (_is_online(o['link'], card, online) and shop_region(K, conn) == 'KR'
            and store_is_kr(conn, o['link'], o['store'], card)):
        lines.append('NOTICE\twithdrawal_kr')
        lines.append('SAY_NOTICE\t%s' % _say(
            K, 'shop.withdrawal.kr', 'Online purchases in Korea can be '
            'withdrawn within 7 days of delivery.'))
    return CaptureLines(lines, ("shop-bought", o["id"], o["bought_on"], "owned_item"))


def owned_find(K, conn, words, cat=None):
    """Recall ("what was that shampoo"): exact item identity, then the kit
    near-match, then plain containment over name / brand / alias / store; a
    category label alone lists that kind.  -> (tier, [owned], near)."""
    q = _ws(words)
    qk = compact(q)
    if not qk:
        raise Usage('owned-find needs words')
    sql = OWNED_SQL
    args = []
    if cat is not None:
        sql += ' WHERE i.category_id=?'
        args.append(cat['id'])
    rows = [_owned_dict(r) for r in conn.execute(
        sql + ' ORDER BY o.bought_on DESC, o.id DESC', args)]
    exact, near = item_find(K, conn, q, cat)
    ids = set(it['id'] for it in exact)
    if ids:
        return 'exact', [o for o in rows if o['item_id'] in ids], []
    if cat is None:
        for c in _cat_rows(conn):
            if any(compact(lab) == qk for lab in _cat_labels(K, c)):
                return 'category', [o for o in rows
                                    if o['category_id'] == c['id']], []
    hit = []
    for o in rows:
        hay = [o['name'], o['brand'], o['store'], o['variant'],
               '%s %s' % (o['brand'] or '', o['name'])] + \
            _json_list(o['aliases'])
        if any(qk in compact(h) for h in hay if h):
            hit.append(o)
    if hit:
        return 'contains', hit, []
    nids = set(int(c['ref']) for c in near)
    return 'near', [o for o in rows if o['item_id'] in nids], near


def _refund(K, conn, o):
    """Returned + linked: the ledger's refund convention (a negative row the
    day it is said), find-first on (date, -amount)."""
    if o['spend_id'] is None or not o['spend_amount']:
        return ['REFUND\tno_ledger_row']
    day = _today()
    amt = -abs(int(o['spend_amount']))
    allocation = (_table_exists(conn, 'shop_capture_payment') and
                  conn.execute('SELECT 1 FROM shop_capture_payment WHERE owned_id=?', (o['id'],)).fetchone())
    hit = conn.execute("SELECT id FROM spend_entry WHERE date=? AND amount=? "
                       "AND kind='expense'" + (" AND note=?" if allocation else ''),
                       (day,amt,'refund owned=%d' % o['id']) if allocation else (day,amt)).fetchone()
    if hit:
        return ['REFUND\texists\t%d' % hit[0]]
    row = conn.execute('SELECT name, category_id FROM spend_entry WHERE id=?',
                       (o['spend_id'],)).fetchone()
    cat = conn.execute('SELECT name FROM spend_category WHERE id=?',
                       (row[1],)).fetchone() if row[1] else None
    sid = K.spend_add(day, amt, row[0], category=cat[0] if cat else None,
                      note='refund owned=%d' % o['id'], source='chat',
                      conn=conn, commit=False)
    return ['REFUND\tspend\t%d\t%d' % (sid, amt)]


def owned_upd(K, conn, ref, satisfaction=None, verbatim=None, fit=None,
              status=None, status_on=None, day=None, store=None, link=None,
              size=None, price=None, link_spend=None, new_spend=False,
              refund=False, note=None):
    o = _owned_must(conn, ref)
    sets, args, lines = [], [], []
    if satisfaction is not None:
        if satisfaction not in SATISFACTIONS:
            raise Usage('--satisfaction %s' % '|'.join(SATISFACTIONS))
        sets.append('satisfaction=?')
        args.append(satisfaction)
    if verbatim is not None and _ws(verbatim):
        vb = o['verbatim']
        sets.append('satisfaction_verbatim=?')
        args.append((vb + '\n' + _ws(verbatim)) if vb else _ws(verbatim))
    if fit is not None:
        if fit not in FITS + ('none',):
            raise Usage('--fit %s|none' % '|'.join(FITS))
        sets.append('fit=?')
        args.append(None if fit == 'none' else fit)
    if status is not None:
        if status not in OWNED_STATES:
            raise Usage('--status %s' % '|'.join(OWNED_STATES))
        sets.append('status=?')
        args.append(status)
        sets.append('status_on=?')
        args.append(None if status == 'in_use'
                    else (_date(status_on, '--status-on') or _today()))
    if day is not None:
        sets.append('bought_on=?')
        args.append(_date(day, '--on'))
    for col, val in (('store', store), ('link', link), ('size_label', size)):
        if val is None:
            continue
        sets.append('%s=?' % col)
        args.append(None if val.strip().lower() == 'none' else _ws(val))
    if price is not None and o['spend_id'] is not None:
        if conn.execute('SELECT count(*) FROM owned_item WHERE spend_id=?', (o['spend_id'],)).fetchone()[0] > 1:
            raise Refuse([('SHARED_ORDER_PRICE', 'edit the ledger order total explicitly; a line cannot rewrite a shared payment')])
    if link_spend is not None or price is not None:
        if str(price).lower() == 'none':
            if o['spend_id'] is not None:
                raise Refuse([('PRICE_IN_LEDGER', 'spend=%d -- the money row '
                               'is the price; change it in the ledger'
                               % o['spend_id'])])
            sets.append('price=NULL')
        elif o['spend_id'] is not None and link_spend is None:
            amt = _amount(price)
            conn.execute('UPDATE spend_entry SET amount=? WHERE id=?',
                         (amt, o['spend_id']))
            if _table_exists(conn, 'shop_capture_payment'):
                conn.execute('UPDATE shop_capture_payment SET amount=? WHERE owned_id=?', (amt,o['id']))
            r = conn.execute('SELECT date, name, amount, kind, method FROM '
                             'spend_entry WHERE id=?', (o['spend_id'],)).fetchone()
            K._spend_write_v32(conn, o['spend_id'], *r)
            lines.append('LEDGER\tspend_updated\t%d\t%d' % (o['spend_id'], amt))
        else:
            if o['spend_id'] is not None:
                raise Refuse([('SPEND_LINKED', 'owned=%d already has spend=%d'
                               % (o['id'], o['spend_id']))])
            amt = None if price is None else _amount(price)
            item = _item_get(conn, o['item_id'])
            cat = _cat_get(conn, item['category_id'])
            sid, own, ll = price_plan(
                K, conn, cat, _date(day, '--on') or o['bought_on'],
                _owned_title(o), amt if amt is not None else o['price'],
                link_spend, new_spend, note)
            lines += ll
            sets.append('spend_id=?')
            args.append(sid)
            sets.append('price=?')
            args.append(own)
    if refund:
        if (status or o['status']) != 'returned':
            raise Usage('--refund goes with --status returned')
    if not sets and not refund and not lines:
        raise Usage('nothing to change')
    if sets:
        conn.execute('UPDATE owned_item SET %s WHERE id=?' % ', '.join(sets),
                     args + [o['id']])
    o = _owned_must(conn, o['id'])
    if refund:
        lines += _refund(K, conn, o)
    evidence = [('owned_item', o['id']), ('shop_item', o['item_id'])]
    if o['spend_id'] is not None:
        evidence.append(('spend_entry', o['spend_id']))
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='owned_stock'").fetchone() and conn.execute('SELECT 1 FROM owned_stock WHERE owned_id=?', (o['id'],)).fetchone():
        evidence.append(('owned_stock', o['id']))
    K.experience_ensure(conn, _owned_title(o), occurred_on=o['bought_on'],
                        evidence=evidence, note=note, verbatim=verbatim)
    return o, lines


def owned_del(conn, ref, keep_spend=False, with_spend=False, K=None):
    o = _owned_must(conn, ref)
    if o['spend_id'] is not None and not (keep_spend or with_spend):
        raise Refuse([('OWNED_HAS_SPEND', 'spend=%d -- ask the owner: keep the '
                       'money row (--keep-spend) or delete it too '
                       '(--with-spend)' % o['spend_id'])])
    sid = o['spend_id']
    if sid is not None and with_spend and conn.execute('SELECT count(*) FROM owned_item WHERE spend_id=?', (sid,)).fetchone()[0] > 1:
        raise Refuse([('SHARED_ORDER_SPEND', 'other purchases share this payment; use --keep-spend')])
    conn.execute('UPDATE owned_item SET spend_id=NULL WHERE id=?', (o['id'],))
    if sid is not None and with_spend:
        conn.execute('DELETE FROM spend_entry WHERE id=?', (sid,))
        K._spend_shadow_del(conn, sid)
    conn.execute('DELETE FROM owned_item_measure WHERE owned_item_id=?',
                 (o['id'],))
    conn.execute('DELETE FROM owned_item WHERE id=?', (o['id'],))
    return o, sid if with_spend else None


# -- garment measurements ---------------------------------------------------

def _garment_must(conn, o):
    cat = _cat_get(conn, o['category_id'])
    if 'wearer' not in _json_list(cat['checks_required']) or \
            cat['name'] == 'shoes':
        raise Refuse([('NOT_GARMENT', 'owned=%d category=%s -- measurements '
                       'are for clothes' % (o['id'], cat['name']))])
    return cat


def _measure_pairs(pairs):
    out = []
    for p in pairs:
        if '=' not in p:
            raise Usage('part=cm expected: %r' % (p,))
        part, val = p.split('=', 1)
        part = part.strip()
        if part not in PARTS:
            raise Usage('part must be one of %s: %r' % ('|'.join(PARTS), part))
        try:
            v = float(val)
        except ValueError:
            raise Usage('cm must be a number: %r' % (val,))
        if not 0 < v < 300:
            raise Usage('cm out of range: %r' % (val,))
        out.append((part, v))
    if not out:
        raise Usage('give at least one part=cm')
    return out


def _measures_of(conn, oid):
    return dict(conn.execute('SELECT part, value_cm FROM owned_item_measure '
                             'WHERE owned_item_id=?', (oid,)).fetchall())


def measure_add(conn, ref, pairs, replace=False):
    o = _owned_must(conn, ref)
    _garment_must(conn, o)
    have = _measures_of(conn, o['id'])
    errs, out = [], []
    for part, v in _measure_pairs(pairs):
        if part in have and abs(have[part] - v) < 0.05:
            out.append((part, v, 'EXISTS'))
        elif part in have and not replace:
            errs.append(('MEASURE_DIFFERS', '%s stored=%s new=%s -- --replace '
                         'when the owner corrects it' % (part, _fcm(have[part]),
                                                         _fcm(v))))
        else:
            out.append((part, v, 'REPLACED' if part in have else 'ADDED'))
    if errs:
        raise Refuse(errs)
    for part, v, st in out:
        if st == 'ADDED':
            conn.execute('INSERT INTO owned_item_measure (owned_item_id, part, '
                         'value_cm, recorded_at) VALUES (?,?,?,?)',
                         (o['id'], part, v, _stamp()))
        elif st == 'REPLACED':
            conn.execute('UPDATE owned_item_measure SET value_cm=?, '
                         'recorded_at=? WHERE owned_item_id=? AND part=?',
                         (v, _stamp(), o['id'], part))
    return o, out


def _fcm(v):
    return ('%.1f' % v).rstrip('0').rstrip('.')


def _measure_lines(conn, o):
    return ['MEASURE\t%d\t%s\t%s' % (o['id'], p, _fcm(v))
            for p, v in sorted(_measures_of(conn, o['id']).items(),
                               key=lambda kv: PARTS.index(kv[0]))]


# -- taste ------------------------------------------------------------------

TASTE_COLS = ('id, ulid, category_id, axis, value, polarity, origin, '
              'source_ref, status, recorded_at')


def _taste_dict(row):
    return dict(zip(('id', 'ulid', 'category_id', 'axis', 'value', 'polarity',
                     'origin', 'source', 'status', 'recorded_at'), row))


def _taste_must(conn, ref):
    ref = str(ref).strip()
    col = 'id' if ref.isdigit() else 'ulid'
    row = conn.execute('SELECT %s FROM taste_mark WHERE %s=?' % (TASTE_COLS,
                                                                 col),
                       (int(ref) if ref.isdigit() else ref,)).fetchone()
    if row is None:
        raise Usage('taste %r not found (shop-taste-list)' % (ref,))
    return _taste_dict(row)


def _taste_active(conn, cat_id, axis, value, polarity=None):
    out = []
    for row in conn.execute("SELECT %s FROM taste_mark WHERE status='active' "
                            "AND category_id=? AND axis=? ORDER BY id"
                            % TASTE_COLS, (cat_id, axis)):
        t = _taste_dict(row)
        if compact(t['value']) == compact(value) and \
                (polarity is None or t['polarity'] == polarity):
            out.append(t)
    return out


def _taste_line(conn, t):
    cat = _cat_get(conn, t['category_id'])
    return 'TASTE\t%d\t%s\t%s\t%s\t%s\t%s\t%s' % (
        t['id'], cat['name'] if cat else '-', t['axis'], t['polarity'],
        t['value'], t['origin'], t['status'])


def _taste_args(axis, polarity, origin):
    if axis is not None and axis not in AXES:
        raise Usage('--axis %s' % '|'.join(AXES))
    if polarity is not None and polarity not in POLARITIES:
        raise Usage('--polarity like|avoid')
    if origin is not None and origin not in ORIGINS:
        raise Usage('--origin stated|confirmed_tendency')


def taste_add(K, conn, cat_ref, axis, value, polarity, origin=None,
              source=None, replace=False):
    _taste_args(axis, polarity, origin)
    if not axis or not polarity or not _ws(value):
        raise Usage('--axis, --value and --polarity are required')
    cat = cat_resolve(K, conn, cat_ref)
    same = _taste_active(conn, cat['id'], axis, value, polarity)
    if same:
        raise Exists(['EXISTS\t%d' % same[0]['id'], _taste_line(conn, same[0])])
    other = 'avoid' if polarity == 'like' else 'like'
    opp = _taste_active(conn, cat['id'], axis, value, other)
    if opp and not replace:
        raise Refuse([('TASTE_OPPOSITE', 'taste=%d says %s -- ask the owner, '
                       'then --replace' % (opp[0]['id'], other))],
                     [_taste_line(conn, opp[0])])
    for t in opp:
        conn.execute("UPDATE taste_mark SET status='retired', retired_at=? "
                     "WHERE id=?", (_stamp(), t['id']))
    ulid = K.new_ulid()
    conn.execute('INSERT INTO taste_mark (ulid, category_id, axis, value, '
                 'polarity, origin, source_ref, recorded_at) '
                 'VALUES (?,?,?,?,?,?,?,?)',
                 (ulid, cat['id'], axis, _ws(value), polarity,
                  origin or 'stated', _ws(source) or None, _stamp()))
    return _taste_must(conn, ulid), [t['id'] for t in opp]


def taste_upd(conn, ref, value=None, polarity=None, axis=None, source=None):
    _taste_args(axis, polarity, None)
    t = _taste_must(conn, ref)
    if t['status'] != 'active':
        raise Refuse([('TASTE_RETIRED', 'taste=%d' % t['id'])])
    nv = _ws(value) if value is not None else t['value']
    if not nv:
        raise Usage('--value cannot be empty')
    na, np_ = axis or t['axis'], polarity or t['polarity']
    clash = [x for x in _taste_active(conn, t['category_id'], na, nv, np_)
             if x['id'] != t['id']]
    if clash:
        raise Refuse([('TASTE_EXISTS', 'taste=%d' % clash[0]['id'])])
    conn.execute('UPDATE taste_mark SET value=?, polarity=?, axis=?, '
                 'source_ref=COALESCE(?, source_ref) WHERE id=?',
                 (nv, np_, na, _ws(source) or None, t['id']))
    return _taste_must(conn, t['id'])


def taste_retire(conn, ref):
    t = _taste_must(conn, ref)
    if t['status'] == 'active':
        conn.execute("UPDATE taste_mark SET status='retired', retired_at=? "
                     "WHERE id=?", (_stamp(), t['id']))
    return _taste_must(conn, t['id'])


def _avoid_hits(conn, hunt, parsed):
    """Gate 4 AVOID_HIT on the one axis a card states as data: brand."""
    if hunt['for_person_id'] is not None or not _table_exists(conn,
                                                               'taste_mark'):
        return []
    avoid = [_taste_dict(r) for r in conn.execute(
        "SELECT %s FROM taste_mark WHERE status='active' AND polarity='avoid' "
        "AND axis='brand' AND category_id=?" % TASTE_COLS,
        (hunt['category_id'],))]
    errs = []
    for i, card in parsed:
        b = compact(card.get('brand'))
        for t in avoid:
            if b and b == compact(t['value']):
                errs.append(('AVOID_HIT', '%d taste=%d brand %s'
                             % (i, t['id'], t['value'])))
    return errs


def cursor_p2_lines(conn, hunt):
    """Record-first for the owner's own hunts: stated taste and the latest
    purchases of the kind (brand / size / fit) -- read before asking."""
    if hunt['for_person_id'] is not None or not p2_ready(conn):
        return []
    out = [_taste_line(conn, _taste_dict(r)) for r in conn.execute(
        "SELECT %s FROM taste_mark WHERE status='active' AND category_id=? "
        "ORDER BY id" % TASTE_COLS, (hunt['category_id'],))]
    for row in conn.execute(OWNED_SQL + ' WHERE i.category_id=? ORDER BY '
                            'o.bought_on DESC, o.id DESC LIMIT ?',
                            (hunt['category_id'], RECENT_OWNED)):
        o = _owned_dict(row)
        out.append('OWNED_RECENT\t%d\t%s\t%s\t%s\t%s\t%s' % (
            o['id'], o['bought_on'], _owned_title(o), o['size'] or '-',
            o['fit'] or '-', o['satisfaction'] or '-'))
    return out


# -- cooling-off (decision 3 + 14:25) ----------------------------------------

def _cooloff_title(K, want):
    w = _ws(want)
    if len(w) > 60:
        w = w[:59] + '~'
    return _say(K, 'shop.cooloff.reminder_title', 'Still want it? {want}',
                want=w)


def _cooloff_pending(K, conn, want):
    return conn.execute(
        "SELECT id, start_at FROM timeblock WHERE kind='task' AND title=? AND "
        "settled_at IS NULL AND note=? ORDER BY id LIMIT 1",
        (_cooloff_title(K, want), COOLOFF_NOTE)).fetchone()


def _local_day(stamp):
    from zoneinfo import ZoneInfo
    dt = _parse_stamp(stamp)
    if dt is None:
        return '-'
    return dt.replace(tzinfo=datetime.timezone.utc).astimezone(
        ZoneInfo(TZ_LOCAL)).date().isoformat()


def want(K, conn, ask, need=None, by=None, cat_ref=None):
    """A want, before any search.  A need signal (run out, broken, a dated
    event / deadline) goes straight to find-things; without one, ONE offer
    with three choices (buy now / ask again in N days / 3 quick questions).
    Nothing is written; nothing is ever resurfaced on the module's own.
    Shopping management not eligible: the want is stored (ledger_preference
    topic 'want') and no offer is made."""
    if not _ws(ask):
        raise Usage('--ask "<the owner\'s words>" is required')
    if need is not None and need not in NEED_SIGNALS:
        raise Usage('--need %s' % '|'.join(NEED_SIGNALS))
    by = _date(by, '--by')
    if cat_ref:
        cat_resolve(K, conn, cat_ref)
    if need or by:
        return ['GO_FIND\t%s' % (need or 'deadline')]
    pend = _cooloff_pending(K, conn, ask)
    if pend:
        return ['COOLOFF_PENDING\t%d\t%s' % (pend[0], _local_day(pend[1]))]
    if not K._manage_on(conn, 'module_shopping'):
        # record-always-on: the cooling-off offer is management. Not
        # eligible -> the want is kept as a fact (find-first on its words),
        # no offer, no reminder.
        row = K._anchor_mod().preference_add(K, conn, 'want', _ws(ask),
                                             note=_ws(ask), source='chat')
        return ['WANT_NOTED\t%d\t%s' % (row['id'], row['action']),
                'SAY\t%s' % _say(K, 'shop.want.noted',
                                  'Noted as something you want.')]
    days = cooling_days(conn)
    return ['OFFER_COOLOFF\t%d' % days,
            'CHOICE\tnow', 'CHOICE\tlater\t%s' % _plus_days(_today(), days),
            'CHOICE\treflect',
            'SAY\t%s' % _say(K, 'shop.cooloff.offer', 'No rush unless you need '
                             'it now: buy it now, have me ask again in {days} '
                             'days, or go through 3 quick questions?',
                             days=days)]


def want_answer(K, conn, ask, choice, cat_ref=None):
    if choice not in ('now', 'later', 'reflect'):
        raise Usage('--choice now|later|reflect')
    if not _ws(ask):
        raise Usage('--ask "<the owner\'s words>" is required')
    if choice == 'now':
        return ['GO_FIND\tnow'], 0
    if choice == 'reflect':
        out = ['QUESTION\t1\t%s' % _say(K, 'shop.cooloff.q1', 'Anything you '
                                         'already have that could do the '
                                         'job?'),
               'QUESTION\t2\t%s' % _say(K, 'shop.cooloff.q2', 'From 1 to 10, '
                                         'how much do you need it?'),
               'QUESTION\t3\t%s' % _say(K, 'shop.cooloff.q3', 'What is the '
                                         'one thing it must get right?')]
        if cat_ref:
            cat = cat_resolve(K, conn, cat_ref)
            for row in conn.execute(OWNED_SQL + " WHERE i.category_id=? AND "
                                    "o.status='in_use' ORDER BY o.bought_on "
                                    "DESC LIMIT 5", (cat['id'],)):
                o = _owned_dict(row)
                out.append('ON_HAND\t%d\t%s\t%s' % (o['id'], _owned_title(o),
                                                    o['bought_on']))
        return out, 0
    days = cooling_days(conn)
    pend = _cooloff_pending(K, conn, ask)
    if pend:
        return ['EXISTS\t%d\t%s' % (pend[0], _local_day(pend[1]))], EXISTS_CODE
    day = _plus_days(_today(), days)
    start = K._to_canonical_utc('%s %s' % (day, cooloff_at(conn)))
    eid = K.timeblock_add(conn, 'task', _cooloff_title(K, ask), 'timed',
                          start_at=start, end_at=K._iso_shift_min(start, 10),
                          owning_agent=K._facade_agent(),
                          created_by=K._facade_agent(), note=COOLOFF_NOTE,
                          notify_policy='start_only', exclusive_override=0)
    return ['REMINDER\t%d\t%s\t%s' % (eid, day, cooloff_at(conn)),
            'SAY\t%s' % _say(K, 'shop.cooloff.later_ok', 'I will ask once, in '
                             '{days} days ({date}).', days=days, date=day)], 0


# -- size draft (14:03: owned garment fit > body measures > height/weight) ---

def _band(v):
    """'170-175' / [170, 175] / 170 -> (lo, hi) or None."""
    if v is None:
        return None
    if isinstance(v, (list, tuple)) and len(v) == 2:
        try:
            lo, hi = float(v[0]), float(v[1])
        except (TypeError, ValueError):
            return None
        return (min(lo, hi), max(lo, hi))
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return (float(v), float(v))
    m = re.match(r'^\s*(\d+(?:\.\d+)?)\s*[-~]\s*(\d+(?:\.\d+)?)\s*$', str(v))
    if m:
        lo, hi = float(m.group(1)), float(m.group(2))
        return (min(lo, hi), max(lo, hi))
    return None


def _chart_rows(raw):
    try:
        rows = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError as e:
        raise Usage('--chart is not JSON: %s' % e)
    if not isinstance(rows, list) or not rows:
        raise Usage('--chart must be a JSON list of size rows')
    out = []
    for i, r in enumerate(rows, 1):
        if not isinstance(r, dict) or not _ws(r.get('size')):
            raise Usage('--chart row %d needs "size"' % i)
        row = {'size': _ws(r['size']), 'parts': {}}
        for p in PARTS:
            if r.get(p) is not None:
                try:
                    row['parts'][p] = float(r[p])
                except (TypeError, ValueError):
                    raise Usage('--chart row %d %s is not a number' % (i, p))
        for k in ('height', 'weight'):
            if r.get(k) is not None:
                b = _band(r[k])
                if b is None:
                    raise Usage('--chart row %d %s band unreadable' % (i, k))
                row[k] = b
        if r.get('foot_mm') is not None:
            try:
                row['foot_mm'] = float(r['foot_mm'])
            except (TypeError, ValueError):
                raise Usage('--chart row %d foot_mm is not a number' % i)
        out.append(row)
    return out


def _body_size(conn, token):
    row = conn.execute('SELECT value, date FROM metric_log WHERE metric=? AND '
                       'value IS NOT NULL ORDER BY date DESC, id DESC LIMIT 1',
                       (token,)).fetchone()
    return (float(row[0]), row[1]) if row else None


def _fit_shift(conn, cat, brand):
    """Fit history moves the draft one row: the latest same-kind purchase (same
    brand when given) that ran small -> one up, large -> one down."""
    sql = OWNED_SQL + " WHERE i.category_id=? AND o.fit IN ('small','large')"
    args = [cat['id']]
    rows = [_owned_dict(r) for r in conn.execute(
        sql + ' ORDER BY o.bought_on DESC, o.id DESC', args)]
    if brand:
        same = [o for o in rows if compact(o['brand']) == compact(brand)]
        rows = same or rows
    if not rows:
        return 0, None
    return (1 if rows[0]['fit'] == 'small' else -1), rows[0]


def _shifted(rows, idx, shift):
    j = max(0, min(len(rows) - 1, idx + shift))
    return j, j != idx + shift


def size_draft(K, conn, chart, hunt_ref=None, cat_ref=None, brand=None,
               for_ref=None):
    rows = _chart_rows(chart)
    if hunt_ref is not None:
        h = _hunt_must(conn, hunt_ref)
        cat, pid = _cat_get(conn, h['category_id']), h['for_person_id']
    elif cat_ref:
        cat, pid = cat_resolve(K, conn, cat_ref), person_resolve(K, conn,
                                                                for_ref)
    else:
        raise Usage('--hunt <id> or --category <c>')
    if pid is not None:
        # a gift reads that person's facts, never the owner's body / closet
        return ['SIZE_NONE\tgift\tfor=%s' % _person_name(conn, pid)]
    is_shoes = cat['name'] == 'shoes'
    # tier 1: the owner's best-fitting owned garment of the same kind
    if not is_shoes:
        refs = []
        for row in conn.execute(OWNED_SQL + " WHERE i.category_id=? AND "
                                "o.fit='right' ORDER BY o.bought_on DESC, "
                                "o.id DESC", (cat['id'],)):
            o = _owned_dict(row)
            ms = _measures_of(conn, o['id'])
            common = [p for p in PARTS if p in ms
                      and any(p in r['parts'] for r in rows)]
            if common:
                refs.append((len(common), o, ms))
        if refs:
            n, o, ms = sorted(refs, key=lambda x: -x[0])[0]
            best = None
            for idx, r in enumerate(rows):
                cp = [p for p in PARTS if p in ms and p in r['parts']]
                if not cp:
                    continue
                score = sum(abs(r['parts'][p] - ms[p]) for p in cp) / len(cp)
                if best is None or score < best[0]:
                    best = (score, idx, cp)
            _s, idx, cp = best
            r = rows[idx]
            return ['SIZE_DRAFT\t%s\tbasis=owned\towned=%d\t%s' % (
                r['size'], o['id'], ','.join(
                    '%s:%+.1f' % (p, r['parts'][p] - ms[p]) for p in cp))]
    # tier 2: body measures (metric_log, kit-shared)
    idx, basis = None, None
    if is_shoes:
        foot = _body_size(conn, 'foot_length_mm')
        sized = [(i, r.get('foot_mm') or _num_size(r['size']))
                 for i, r in enumerate(rows)]
        sized = [(i, mm) for i, mm in sized if mm]
        if foot and sized:
            fit = [i for i, mm in sized if mm >= foot[0]]
            idx = fit[0] if fit else sized[-1][0]
            basis = 'body\tfoot_length_mm=%s' % _fcm(foot[0])
            out_of = not fit
    else:
        checks = []
        for part, token, mult in PART_FOR_BODY:
            body = _body_size(conn, token)
            if body and any(part in r['parts'] for r in rows):
                checks.append((part, token, mult, body[0]))
        if checks:
            fit = [i for i, r in enumerate(rows)
                   if all(part in r['parts'] and r['parts'][part] * mult
                          >= b + EASE_CM[token]
                          for part, token, mult, b in checks)]
            idx = fit[0] if fit else len(rows) - 1
            out_of = not fit
            basis = 'body\t%s' % ','.join('%s=%s' % (t, _fcm(b))
                                          for _p, t, _m, b in checks)
    # tier 3: height / weight band on the chart
    if idx is None:
        ms = K.body_brief(conn).get('measures') or {}
        height = (ms.get('height') or {}).get('value')
        weight = (ms.get('weight') or {}).get('value')
        banded = [i for i, r in enumerate(rows) if r.get('height')]
        if height is not None and banded:
            inh = [i for i in banded if rows[i]['height'][0] <= height
                   <= rows[i]['height'][1]]
            if weight is not None:
                both = [i for i in inh if rows[i].get('weight') and
                        rows[i]['weight'][0] <= weight <= rows[i]['weight'][1]]
                inh = both or inh
            out_of = not inh
            idx = inh[0] if inh else min(
                banded, key=lambda i: min(abs(rows[i]['height'][0] - height),
                                          abs(rows[i]['height'][1] - height)))
            basis = 'band\theight_cm=%s%s' % (
                _fcm(height), '' if weight is None else
                ',weight_kg=%s' % _fcm(weight))
    if idx is None:
        token = NEED_FOR_CATEGORY.get(cat['name'], 'chest_cm')
        if not is_shoes and not any(r['parts'] for r in rows):
            token = 'height_cm'
        if not is_shoes and token != 'height_cm' and not any(
                p in r['parts'] for r in rows
                for p, t, _m in PART_FOR_BODY if t == token):
            token = 'height_cm'
        what = K._i18n('shop.body.%s' % token, token)
        return ['SIZE_NEED\t%s' % token,
                'ASK\t%s' % _say(K, 'shop.size.need', 'To size this, just '
                                 'tell me your {what}.', what=what)]
    shift, src = _fit_shift(conn, cat, brand)
    lines = []
    if shift:
        j, clipped = _shifted(rows, idx, shift)
        lines.append('FIT_SHIFT\t%+d\towned=%d\t%s' % (shift, src['id'],
                                                       src['fit']))
        idx = j
        out_of = out_of or clipped
    lines.insert(0, 'SIZE_DRAFT\t%s\tbasis=%s' % (rows[idx]['size'], basis))
    if out_of:
        lines.append('SIZE_OUT_OF_CHART\t%s' % rows[idx]['size'])
    return lines


def _num_size(label):
    m = re.match(r'^\s*(\d{3})\s*(mm)?\s*$', str(label or ''))
    return float(m.group(1)) if m else None


# -- phase 2 CLI ------------------------------------------------------------

def _price_opt(v):
    return None if v is None else _amount(v)


def run_p2(K, conn, verb, argv):
    """-> (lines, exit_code) for the 072 verbs."""
    if verb == 'shop-bought':
        o, p = _opt(argv, {'--hunt': True, '--shown': True, '--name': True,
                           '--category': True, '--brand': True,
                           '--variant': True, '--on': True, '--store': True,
                           '--link': True, '--price': True, '--size': True,
                           '--online': True, '--new': False,
                           '--link-spend': True, '--new-spend': False,
                           '--note': True, '--new-confirmed': False, '--experience': True})
        if o.get('--online') not in (None, '0', '1'):
            raise Usage('--online 0|1')
        return bought(K, conn, o.get('--hunt'), o.get('--shown'),
                      p[0] if p else None, o.get('--name'),
                      o.get('--category'), o.get('--brand'),
                      o.get('--variant'), o.get('--on'), o.get('--store'),
                      o.get('--link'), _price_opt(o.get('--price')),
                      o.get('--size'), o.get('--online'), '--new' in o,
                      o.get('--link-spend'), '--new-spend' in o,
                      o.get('--note'), '--new-confirmed' in o, o.get('--experience')), 0
    if verb == 'shop-owned-add':
        o, p = _opt(argv, {'--name': True, '--category': True, '--brand': True,
                           '--variant': True, '--url': True, '--on': True,
                           '--store': True, '--link': True, '--hunt': True,
                           '--price': True, '--size': True, '--new': False,
                           '--link-spend': True, '--new-spend': False,
                           '--note': True, '--new-confirmed': False, '--experience': True})
        item, _st = _item_from_opts(K, conn, p[0] if p else None,
                                    o.get('--name'), o.get('--category'),
                                    o.get('--brand'), o.get('--variant'),
                                    o.get('--url'), '--new-confirmed' in o)
        hid = _hunt_must(conn, o['--hunt'])['id'] if o.get('--hunt') else None
        ow, ledger = owned_add(K, conn, item, _date(o.get('--on'), '--on')
                               or _today(), o.get('--store'), o.get('--link'),
                               hid, _price_opt(o.get('--price')),
                               o.get('--size'), '--new' in o,
                               o.get('--link-spend'), '--new-spend' in o,
                               o.get('--note'), o.get('--experience'))
        return CaptureLines(['ADDED\t%d' % ow['id'], _owned_line(conn, ow)] + ledger,
                            ('shop-owned-add', ow['id'], ow['bought_on'], 'owned_item')), 0
    if verb == 'shop-owned-find':
        o, p = _opt(argv, {'--category': True})
        _need(p, 1, 'shop-owned-find "<words>" [--category c]')
        cat = cat_resolve(K, conn, o['--category']) if o.get('--category') \
            else None
        tier, rows, near = owned_find(K, conn, ' '.join(p), cat)
        lines = ['FOUND\t%s\t%d' % (tier, len(rows))] if rows else []
        lines += [_owned_line(conn, r) for r in rows]
        import pantry_stock
        lines += pantry_stock.owned_lines(conn, rows)
        for c in near:
            lines.append('NEAR\t%s\t%s\t%s' % (c['ref'], c['name'],
                                               c['signal']))
        return lines or ['NONE\t%s\tsearched=owned' % _ws(' '.join(p))], 0
    if verb == 'shop-owned-list':
        o, _p = _opt(argv, {'--category': True, '--status': True})
        sql, args = OWNED_SQL + ' WHERE 1=1', []
        if o.get('--category'):
            sql += ' AND i.category_id=?'
            args.append(cat_resolve(K, conn, o['--category'])['id'])
        if o.get('--status'):
            if o['--status'] not in OWNED_STATES:
                raise Usage('--status %s' % '|'.join(OWNED_STATES))
            sql += ' AND o.status=?'
            args.append(o['--status'])
        rows = [_owned_dict(r) for r in conn.execute(
            sql + ' ORDER BY o.bought_on DESC, o.id DESC', args)]
        import pantry_stock
        return ([_owned_line(conn, r) for r in rows] + pantry_stock.owned_lines(conn, rows)) or ['NONE'], 0
    if verb == 'shop-owned-upd':
        o, p = _opt(argv, {'--satisfaction': True, '--verbatim': True,
                           '--fit': True, '--status': True,
                           '--status-on': True, '--on': True, '--store': True,
                           '--link': True, '--size': True, '--price': True,
                           '--link-spend': True, '--new-spend': False,
                           '--refund': False, '--note': True})
        _need(p, 1, 'shop-owned-upd <id> [--satisfaction s] [--verbatim ..] '
              '[--fit f|none] [--status s] [--status-on D] [--on D] '
              '[--store s|none] [--link u|none] [--size l|none] '
              '[--price N|none] [--link-spend id] [--new-spend] [--refund]')
        before = _owned_must(conn, p[0])
        ow, lines = owned_upd(K, conn, p[0], o.get('--satisfaction'),
                              o.get('--verbatim'), o.get('--fit'),
                              o.get('--status'), o.get('--status-on'),
                              o.get('--on'), o.get('--store'), o.get('--link'),
                              o.get('--size'), o.get('--price'),
                              o.get('--link-spend'), '--new-spend' in o,
                              '--refund' in o, o.get('--note'))
        receipt = None
        if o.get('--status') and ow['status'] != before['status'] and ow['status_on']:
            receipt = ('shop-owned-upd', ow['id'], ow['status_on'], 'owned_item')
        return CaptureLines(['UPDATED\t%d' % ow['id'], _owned_line(conn, ow)] + lines,
                            receipt), 0
    if verb == 'shop-owned-retire':
        _o, p = _opt(argv, {})
        _need(p, 1, 'shop-owned-retire <id>')
        ow, _l = owned_upd(K, conn, p[0], status='gone')
        return ['RETIRED\t%d' % ow['id'], _owned_line(conn, ow)], 0
    if verb == 'shop-owned-del':
        o, p = _opt(argv, {'--keep-spend': False, '--with-spend': False})
        _need(p, 1, 'shop-owned-del <id> [--keep-spend | --with-spend]')
        if '--keep-spend' in o and '--with-spend' in o:
            raise Usage('one of --keep-spend --with-spend')
        ow, sid = owned_del(conn, p[0], '--keep-spend' in o,
                            '--with-spend' in o, K)
        return ['DELETED\t%d%s' % (ow['id'], '\tspend=%d' % sid if sid
                                   else '')], 0
    if verb == 'shop-measure-add':
        o, p = _opt(argv, {'--replace': False})
        _need(p, 2, 'shop-measure-add <owned_id> <part>=<cm> [...] '
              '[--replace]   parts: ' + ' '.join(PARTS))
        ow, done = measure_add(conn, p[0], p[1:], '--replace' in o)
        lines = ['MEASURE\t%d\t%s\t%s\t%s' % (ow['id'], part, _fcm(v), st)
                 for part, v, st in done]
        new = any(st != 'EXISTS' for _p, _v, st in done)
        # Catalog measurements carry recording time, not measurement time.
        # List candidates but require photo/owner proof; never date an activity
        # from recorded_at (could be a size chart copied months later).
        return CaptureLines(lines), (0 if new else EXISTS_CODE)
    if verb == 'shop-measure-list':
        o, p = _opt(argv, {'--category': True})
        if p:
            ow = _owned_must(conn, p[0])
            return [_owned_line(conn, ow)] + (_measure_lines(conn, ow) or
                                               ['NONE']), 0
        sql, args = (OWNED_SQL + ' WHERE EXISTS (SELECT 1 FROM '
                     'owned_item_measure m WHERE m.owned_item_id=o.id)'), []
        if o.get('--category'):
            sql += ' AND i.category_id=?'
            args.append(cat_resolve(K, conn, o['--category'])['id'])
        lines = []
        for row in conn.execute(sql + ' ORDER BY o.bought_on DESC', args):
            ow = _owned_dict(row)
            lines += [_owned_line(conn, ow)] + _measure_lines(conn, ow)
        return lines or ['NONE'], 0
    if verb == 'shop-measure-del':
        o, p = _opt(argv, {'--all': False})
        _need(p, 1, 'shop-measure-del <owned_id> <part>[,<part>] | --all')
        ow = _owned_must(conn, p[0])
        parts = _csv(p[1]) if len(p) > 1 else []
        if not parts and '--all' not in o:
            raise Usage('name the part(s), or --all')
        bad = [x for x in parts if x not in PARTS]
        if bad:
            raise Usage('unknown part %s' % ','.join(bad))
        if '--all' in o:
            n = conn.execute('DELETE FROM owned_item_measure WHERE '
                             'owned_item_id=?', (ow['id'],)).rowcount
        else:
            n = conn.execute('DELETE FROM owned_item_measure WHERE '
                             'owned_item_id=? AND part IN (%s)'
                             % ','.join('?' * len(parts)),
                             [ow['id']] + parts).rowcount
        return ['DELETED\t%d\tmeasures=%d' % (ow['id'], n)], 0
    if verb == 'shop-taste-add':
        o, p = _opt(argv, {'--axis': True, '--value': True, '--polarity': True,
                           '--origin': True, '--source': True,
                           '--replace': False})
        _need(p, 1, 'shop-taste-add <category> --axis a --value "<w>" '
              '--polarity like|avoid [--origin stated|confirmed_tendency] '
              '[--source ".."] [--replace]')
        t, retired = taste_add(K, conn, p[0], o.get('--axis'),
                               o.get('--value'), o.get('--polarity'),
                               o.get('--origin'), o.get('--source'),
                               '--replace' in o)
        return ['ADDED\t%d' % t['id'], _taste_line(conn, t)] + [
            'RETIRED\t%d' % r for r in retired], 0
    if verb == 'shop-taste-list':
        o, p = _opt(argv, {'--all': False})
        sql, args = 'SELECT %s FROM taste_mark WHERE 1=1' % TASTE_COLS, []
        if p:
            sql += ' AND category_id=?'
            args.append(cat_resolve(K, conn, p[0])['id'])
        if '--all' not in o:
            sql += " AND status='active'"
        return [_taste_line(conn, _taste_dict(r)) for r in conn.execute(
            sql + ' ORDER BY category_id, axis, id', args)] or ['NONE'], 0
    if verb == 'shop-taste-upd':
        o, p = _opt(argv, {'--axis': True, '--value': True, '--polarity': True,
                           '--source': True})
        _need(p, 1, 'shop-taste-upd <id> [--value] [--polarity] [--axis] '
              '[--source]')
        if not any(k in o for k in ('--axis', '--value', '--polarity',
                                    '--source')):
            raise Usage('nothing to change')
        t = taste_upd(conn, p[0], o.get('--value'), o.get('--polarity'),
                      o.get('--axis'), o.get('--source'))
        return ['UPDATED\t%d' % t['id'], _taste_line(conn, t)], 0
    if verb == 'shop-taste-retire':
        _o, p = _opt(argv, {})
        _need(p, 1, 'shop-taste-retire <id>')
        t = taste_retire(conn, p[0])
        return ['RETIRED\t%d' % t['id']], 0
    if verb == 'shop-taste-del':
        _o, p = _opt(argv, {})
        _need(p, 1, 'shop-taste-del <id>')
        t = _taste_must(conn, p[0])
        conn.execute('DELETE FROM taste_mark WHERE id=?', (t['id'],))
        return ['DELETED\t%d' % t['id']], 0
    if verb == 'shop-want':
        o, _p = _opt(argv, {'--ask': True, '--need': True, '--by': True,
                            '--category': True})
        return want(K, conn, o.get('--ask'), o.get('--need'), o.get('--by'),
                    o.get('--category')), 0
    if verb == 'shop-want-answer':
        o, _p = _opt(argv, {'--ask': True, '--choice': True,
                            '--category': True})
        return want_answer(K, conn, o.get('--ask'), o.get('--choice'),
                           o.get('--category'))
    if verb == 'shop-size-draft':
        o, _p = _opt(argv, {'--hunt': True, '--category': True,
                            '--chart': True, '--brand': True, '--for': True})
        if not o.get('--chart'):
            raise Usage("usage: kit_life.sh shop-size-draft --hunt <id> | "
                        "--category <c> [--for p] --chart '<json rows>' "
                        "[--brand b]")
        return size_draft(K, conn, o['--chart'], o.get('--hunt'),
                          o.get('--category'), o.get('--brand'),
                          o.get('--for')), 0
    raise Usage('unknown shop verb %s' % verb)


# -- CLI --------------------------------------------------------------------

def _opt(argv, flags, multi=()):
    """Tiny parser: {flag: value | True | [values]} + positionals.  flags maps
    '--name' -> takes_value(bool)."""
    out, pos = {}, []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith('--') and a in flags:
            if flags[a]:
                if i + 1 >= len(argv):
                    raise Usage('%s needs a value' % a)
                if a in multi:
                    out.setdefault(a, []).append(argv[i + 1])
                else:
                    out[a] = argv[i + 1]
                i += 2
                continue
            out[a] = True
            i += 1
            continue
        if a.startswith('--'):
            raise Usage('unknown option %s' % a)
        pos.append(a)
        i += 1
    return out, pos


def _need(pos, n, usage):
    if len(pos) < n:
        raise Usage('usage: kit_life.sh ' + usage)
    return pos


def _hunt_line(K, conn, h):
    cat = _cat_get(conn, h['category_id'])
    n = conn.execute('SELECT COUNT(*) FROM shop_round WHERE hunt_id=?',
                     (h['id'],)).fetchone()[0]
    return 'HUNT\t%d\t%s\t%s\t%s\trounds=%d\tbudget=%s\tneed_by=%s\tfor=%s' % (
        h['id'], h['state'], cat['name'], _ws(h['ask']), n,
        h['budget_max'] if h['budget_max'] is not None else 'none',
        h['need_by'] or 'none', _person_name(conn, h['for_person_id']))


def _shown_line(c, round_no):
    return 'SHOWN\t%d\t%d\t%d\t%d\t%s\t%s\t%s\t%s\t%s' % (
        c['id'], round_no, c['slot'], c['rank'], c['direction'],
        _card_title(c), c['price'] if c['price'] is not None else '-',
        (c['verdict'] or '-') + ('/' + c['scope'] if c['scope'] else '')
        + ('/closest' if c['closest'] else ''), c['source'])


def run(K, conn, verb, argv):
    """-> (lines, exit_code)."""
    if verb.startswith('shop-stock-'):
        import pantry_stock
        return pantry_stock.cli(K, conn, verb, argv)
    if verb == 'shop-payment-match':
        import shop_payment
        return shop_payment.cli(K, conn, argv)
    if verb in CAPTURE_VERBS:
        import shop_capture
        return shop_capture.cli(K, conn, verb, argv)
    if verb in P2_VERBS:
        return run_p2(K, conn, verb, argv)
    if verb == 'shop-cat-list':
        o, _p = _opt(argv, {'--all': False})
        rows = _cat_rows(conn, active_only='--all' not in o)
        return [_cat_line(K, conn, c) for c in rows], 0
    if verb == 'shop-cat-add':
        o, p = _opt(argv, {'--display': True, '--consumable': True,
                           '--checks': True, '--new-confirmed': False})
        _need(p, 1, 'shop-cat-add <token> [--display "<words>"] '
              '[--consumable 0|1] [--checks a,b] [--new-confirmed]')
        st, cat = cat_add(K, conn, p[0], o.get('--display'),
                          o.get('--consumable'), o.get('--checks'),
                          '--new-confirmed' in o)
        return ['%s\t%d\t%s\t%s' % (st, cat['id'], cat['name'],
                                    cat_label(K, cat))], \
            (EXISTS_CODE if st == 'EXISTS' else 0)
    if verb in ('shop-cat-upd', 'shop-cat-retire'):
        o, p = _opt(argv, {'--display': True, '--consumable': True,
                           '--checks': True, '--active': True,
                           '--spend-category': True})
        _need(p, 1, '%s <id|token> ...' % verb)
        if verb == 'shop-cat-retire':
            cat = cat_upd(conn, p[0], active=0)
            return ['RETIRED\t%d\t%s' % (cat['id'], cat['name'])], 0
        cat = cat_upd(conn, p[0], o.get('--display'), o.get('--consumable'),
                      o.get('--checks'), o.get('--active'),
                      o.get('--spend-category'))
        return ['UPDATED\t' + _cat_line(K, conn, cat)], 0
    if verb == 'shop-cat-del':
        _o, p = _opt(argv, {})
        _need(p, 1, 'shop-cat-del <id|token>')
        cat = cat_del(conn, p[0])
        return ['DELETED\t%d\t%s' % (cat['id'], cat['name'])], 0

    if verb == 'shop-item-upsert':
        o, _p = _opt(argv, {'--category': True, '--name': True, '--brand': True,
                            '--variant': True, '--url': True, '--image': True,
                            '--new-confirmed': False})
        if not o.get('--category') or not o.get('--name'):
            raise Usage('usage: kit_life.sh shop-item-upsert --category <c> '
                        '--name "<n>" [--brand] [--variant] [--url] [--image] '
                        '[--new-confirmed]')
        cat = cat_resolve(K, conn, o['--category'])
        st, it, filled = item_upsert(
            K, conn, cat, o['--name'], o.get('--brand'), o.get('--variant'),
            o.get('--url'), o.get('--image'), '--new-confirmed' in o)
        lines = ['%s\t%d' % (st, it['id']), _item_line(conn, _item_get(
            conn, it['id']))]
        if filled:
            lines.append('FILLED\t%s' % ','.join(filled))
        return lines, (EXISTS_CODE if st == 'EXISTS' else 0)
    if verb == 'shop-item-find':
        o, p = _opt(argv, {'--category': True})
        _need(p, 1, 'shop-item-find "<words>" [--category c]')
        cat = cat_resolve(K, conn, o['--category']) if o.get('--category') \
            else None
        exact, near = item_find(K, conn, p[0], cat)
        lines = [_item_line(conn, it) for it in exact]
        for c in near:
            lines.append('NEAR\t%s\t%s\t%s\t%s' % (c['ref'], c['name'],
                                                   c['signal'], c['matched']))
        import pantry_stock
        lines += pantry_stock.lines(conn, p[0])
        return lines or ['NONE\t%s' % _ws(p[0])], 0
    if verb == 'shop-item-upd':
        o, p = _opt(argv, {'--name': True, '--brand': True, '--variant': True,
                           '--url': True, '--image': True, '--alias': True,
                           '--consumable': True, '--category': True})
        _need(p, 1, 'shop-item-upd <id> [--name] [--brand] [--variant] [--url] '
              '[--image] [--alias "<words>"] [--consumable 0|1|none] '
              '[--category c]')
        cat = cat_resolve(K, conn, o['--category']) if o.get('--category') \
            else None
        it = item_upd(conn, p[0], o.get('--name'), o.get('--brand'),
                      o.get('--variant'), o.get('--url'), o.get('--image'),
                      o.get('--alias'), o.get('--consumable'), cat)
        return ['UPDATED\t%d' % it['id'], _item_line(conn, it)], 0
    if verb == 'shop-item-merge':
        _o, p = _opt(argv, {})
        _need(p, 2, 'shop-item-merge <from_id> <into_id>')
        dst, moved = item_merge(conn, p[0], p[1])
        return ['MERGED\t%s\t%d\t%s' % (p[0], dst['id'], ' '.join(moved) or
                                        '-')], 0
    if verb == 'shop-item-del':
        _o, p = _opt(argv, {})
        _need(p, 1, 'shop-item-del <id>')
        it = item_del(conn, p[0])
        return ['DELETED\t%d\t%s' % (it['id'], it['name'])], 0

    if verb == 'shop-hunt-open':
        o, p = _opt(argv, {'--ask': True, '--for': True, '--budget': True,
                           '--by': True, '--new': False})
        _need(p, 1, 'shop-hunt-open <category> --ask "<words>" [--for '
              '<person>] [--budget N] [--by YYYY-MM-DD] [--new]')
        budget = o.get('--budget')
        try:
            budget = _int_or_none(budget)
        except (TypeError, ValueError):
            raise Usage('--budget must be an amount')
        st, h, changed = hunt_open(K, conn, p[0], o.get('--ask'),
                                   o.get('--for'), budget, o.get('--by'),
                                   '--new' in o)
        if st == 'EXPIRED':
            return ['EXPIRED\t%d\t%s' % (h['id'], _ws(h['ask'])),
                    'ASK\t%s' % _say(K, 'shop.hunt.resume_offer',
                                     'Pick up where we left off on "{ask}"?',
                                     ask=_ws(h['ask']))], EXISTS_CODE
        lines = ['%s\t%d' % (st, h['id'])]
        if changed:
            lines.append('UPDATED\t%s' % ','.join(changed))
        return lines + cursor_lines(K, conn, h), 0
    if verb == 'shop-hunt-cursor':
        _o, p = _opt(argv, {})
        expire_pass(conn)
        if p:
            return cursor_lines(K, conn, _hunt_must(conn, p[0])), 0
        rows = [_hunt_dict(r) for r in conn.execute(
            "SELECT %s FROM shop_hunt WHERE state='open' ORDER BY id DESC"
            % HUNT_COLS)]
        if not rows:
            return ['NONE'], 0
        lines = cursor_lines(K, conn, rows[0])
        for h in rows[1:]:
            lines.append('OTHER_OPEN\t%d\t%s' % (h['id'], _ws(h['ask'])))
        return lines, 0
    if verb == 'shop-hunt-list':
        o, _p = _opt(argv, {'--state': True, '--all': False})
        sql = 'SELECT %s FROM shop_hunt' % HUNT_COLS
        args = ()
        if o.get('--state'):
            sql += ' WHERE state=?'
            args = (o['--state'],)
        elif '--all' not in o:
            sql += " WHERE state='open'"
        rows = [_hunt_dict(r) for r in conn.execute(sql + ' ORDER BY id', args)]
        return [_hunt_line(K, conn, h) for h in rows] or ['NONE'], 0
    if verb == 'shop-hunt-upd':
        o, p = _opt(argv, {'--ask': True, '--budget': True, '--by': True,
                           '--for': True, '--budget-hard': True,
                           '--reopen': False})
        _need(p, 1, 'shop-hunt-upd <id> [--ask] [--budget N|none] '
              '[--by D|none] [--for p|self] [--budget-hard 0|1] [--reopen]')
        budget = o.get('--budget')
        if budget is not None and budget.lower() != 'none':
            try:
                budget = _int_or_none(budget)
            except (TypeError, ValueError):
                raise Usage('--budget must be an amount or none')
        h = hunt_upd(K, conn, p[0], o.get('--ask'), budget, o.get('--by'),
                     o.get('--for'), o.get('--budget-hard'), '--reopen' in o)
        return ['UPDATED\t%d' % h['id']] + cursor_lines(K, conn, h), 0
    if verb == 'shop-hunt-close':
        o, p = _opt(argv, {'--picked': True, '--abandon': False,
                           '--to-wish': True})
        _need(p, 1, 'shop-hunt-close <id> --picked <shown_id> | --abandon | '
              '--to-wish <shown_id>')
        n = sum(1 for k in ('--picked', '--abandon', '--to-wish') if k in o)
        if n != 1:
            raise Usage('exactly one of --picked --abandon --to-wish')
        h, card = hunt_close(K, conn, p[0], o.get('--picked'),
                             '--abandon' in o, o.get('--to-wish'))
        line = 'CLOSED\t%d\t%s' % (h['id'], h['state'])
        if card is not None:
            line += '\t%d\t%s\t%s' % (card['id'], _card_title(card),
                                      card['url'] or '-')
        return [line], 0
    if verb == 'shop-hunt-del':
        _o, p = _opt(argv, {})
        _need(p, 1, 'shop-hunt-del <id>')
        h, nr, nc = hunt_del(conn, p[0])
        return ['DELETED\t%d\trounds=%d\tcards=%d' % (h['id'], nr, nc)], 0

    if verb == 'shop-round-add':
        o, p = _opt(argv, {'--seen': True, '--blocked': True, '--assume': True,
                           '--partial': True, '--reaimed': False,
                           '--card': True}, multi=('--card',))
        _need(p, 1, "shop-round-add <hunt> --seen a,b [--blocked c,d] "
              "[--assume \"<basis>\"] [--partial \"<reason>\"] [--reaimed] "
              "--card '<json>' (3-4x)")
        rid = round_add(K, conn, p[0], o.get('--seen'), o.get('--blocked'),
                        o.get('--assume'), o.get('--partial'),
                        '--reaimed' in o, o.get('--card', []))
        return render_lines(K, conn, rid), 0
    if verb == 'shop-round-show':
        _o, p = _opt(argv, {})
        _need(p, 1, 'shop-round-show <hunt> [<round_no>]')
        h = _hunt_must(conn, p[0])
        r = _round_of(conn, h, p[1] if len(p) > 1 else None)
        return render_lines(K, conn, r[0]), 0
    if verb == 'shop-round-upd':
        o, p = _opt(argv, {'--seen': True, '--blocked': True, '--assume': True})
        _need(p, 1, 'shop-round-upd <round_id> [--seen a,b] [--blocked c,d] '
              '[--assume "<basis>"|none]')
        rid = round_upd(conn, p[0], o.get('--seen'), o.get('--blocked'),
                        o.get('--assume'))
        return ['UPDATED\t%d' % rid] + render_lines(K, conn, rid), 0

    if verb == 'shop-verdict':
        o, p = _opt(argv, {'--pick': True, '--reject': True, '--scope': True,
                           '--reject-all': False, '--default': True,
                           '--closest': True, '--verbatim': True,
                           '--round': True})
        _need(p, 1, 'shop-verdict <hunt> --pick <slot>[,<slot>] | --reject '
              '<slot>[,..] [--scope round|item] | --reject-all | --default '
              '<slot> | --closest <slot>  [--verbatim "<words>"] [--round n]')
        if o.get('--scope') not in (None, 'round', 'item'):
            raise Usage('--scope round|item')
        h, done, extra = verdict(
            K, conn, p[0], o.get('--pick'), o.get('--reject'), o.get('--scope'),
            '--reject-all' in o, o.get('--default'), o.get('--closest'),
            o.get('--verbatim'), o.get('--round'))
        lines = ['VERDICT\t%d\t%d\t%s' % (c['slot'], c['id'], v)
                 for c, v in done]
        return lines + extra + ['EXPECT\t%s' % expect(conn, h)], 0
    if verb == 'shop-shown-list':
        _o, p = _opt(argv, {})
        _need(p, 1, 'shop-shown-list <hunt>')
        h = _hunt_must(conn, p[0])
        nos = dict((r[0], r[1]) for r in _rounds(conn, h['id']))
        return [_shown_line(c, nos[c['round_id']])
                for c in _shown_of_hunt(conn, h['id'])] or ['NONE'], 0
    if verb == 'shop-shown-void':
        _o, p = _opt(argv, {})
        _need(p, 1, 'shop-shown-void <shown_id>')
        c = shown_void(conn, p[0])
        return ['VOIDED\t%d' % c['id']], 0
    raise Usage('unknown shop verb %s' % verb)


def cli(K, verb, argv):
    if not os.path.isfile(K.DB_PATH):
        print('kit_life.db not found (%s)' % K.DB_PATH, file=sys.stderr)
        sys.exit(1)
    conn = K.get_conn()
    try:
        v = conn.execute('PRAGMA user_version').fetchone()[0]
        if not schema_ready(conn) or (verb in P2_VERBS + CAPTURE_VERBS
                                      and not p2_ready(conn)):
            print('SCHEMA_NOT_READY\tmodule_shopping needs user_version >= %d '
                  '(this database: %d)' % (
                      P2_SCHEMA_VERSION if verb in P2_VERBS + CAPTURE_VERBS
                      else SCHEMA_VERSION, v), file=sys.stderr)
            sys.exit(1)
        if verb not in READ_VERBS and verb not in NOTXN_VERBS:
            conn.execute('BEGIN IMMEDIATE')
        try:
            stock_opts = {}
            if verb in ('shop-bought', 'shop-owned-add'):
                argv = list(argv)
                for flag in ('--quantity','--unit','--unit-size','--size-unit'):
                    if flag in argv:
                        idx = argv.index(flag)
                        if idx+1 >= len(argv):
                            raise Usage(flag + ' needs a value')
                        stock_opts[flag] = argv[idx+1]
                        del argv[idx:idx+2]
            lines, code = run(K, conn, verb, argv)
            if stock_opts and isinstance(lines, CaptureLines) and lines.receipt:
                import pantry_stock
                oid = lines.receipt[1]
                pantry_stock.set_lot(conn, oid, pantry_stock.specification(stock_opts, _owned_must(conn, oid)['size']))
                lines.extend(pantry_stock.lines(conn, _owned_must(conn, oid)['name']))
        except Refuse as e:
            if conn.in_transaction:
                conn.rollback()
            for code_, detail in e.lines:
                print('REFUSED\t%s\t%s' % (code_, detail))
            for line in e.extra:
                print(line)
            sys.exit(REFUSED_CODE)
        except Exists as e:
            if conn.in_transaction:
                conn.rollback()
            for line in e.lines:
                print(line)
            sys.exit(EXISTS_CODE)
        except Usage as e:
            if conn.in_transaction:
                conn.rollback()
            print(str(e), file=sys.stderr)
            sys.exit(1)
        except (sqlite3.IntegrityError, ValueError) as e:
            if conn.in_transaction:
                conn.rollback()
            print('error: %s' % e, file=sys.stderr)
            sys.exit(1)
        if conn.in_transaction:
            conn.commit()
        if (verb in ('shop-owned-upd', 'shop-owned-retire', 'shop-owned-del')
                and _table_exists(conn, 'contract_record')
                and conn.execute('SELECT 1 FROM contract_record LIMIT 1').fetchone()):
            try:
                import contracts
                contracts.sync_owned(K, conn)
            except (ValueError, sqlite3.Error, OSError, KeyError, ImportError) as error:
                conn.rollback()
                # The shopping mutation has committed. Warranty enrichment
                # failure must never disguise a successful purchase edit.
                lines.append('CONTRACT_WARNING\t' + str(error))
        for line in lines:
            print(line)
        if not code and isinstance(lines, CaptureLines):
            K._capture_open_today(conn, *(lines.receipt or ()))
        if code:
            sys.exit(code)
    finally:
        conn.close()
