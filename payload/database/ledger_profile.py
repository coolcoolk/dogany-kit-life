"""ledger_profile.py -- what the owner's money records say, as kit read verbs
(kit_life 2.46.0, ledger onboarding v5; owner rehearsal 2026-10-10).

Every number the ledger onboarding says comes from here, never from a model
sum (owner 2026-10-10 11:56 / 12:08: totals from the kit). READ ONLY: no verb
in this file writes.

RECORDS = spend_entry rows (scope personal|unknown for the picture; every
scope for the methods) + money intake items still PENDING (an import the
owner has not reviewed yet: a Banksalad file, screenshots, SMS). A pending
reversal counts negative; transfer items (excluded by the kit) are not
spending -- they are read only to infer which account pays a card bill.

ONE WINDOW (owner real-data run 2026-10-11): the methods averages, the
habits screen, the fixed amounts' window, the gap and the wrap all read the
recent 3 full months (window_months) and one row classification (flows()).

VERBS (JSON out; --json accepted for symmetry)
  ledger-records    count, first / last date, pending count, full months
  methods-seen      per payment method: count, monthly average, top
                    merchants, kind (registered, else from the name), the
                    account its bill leaves (registered link, else inferred
                    from bill-payment rows), name-variant twins
  fixed-detect      recurring candidates: monthly (stable, variable
                    utility / loan / insurance / telecom, multi-charge
                    subscription, a new pair of months), yearly, saving,
                    income (amount, nominal day, payday shift inferred);
                    merchant normalized across methods; each matched to a
                    registered fixed_cost; registered rows the records do
                    not show are listed too (record-first confirm)
  flow-picture [--months N]
                    monthly average over the last N (3) full months with
                    records (THE window, flows()): income / fixed (+ loan) /
                    saving / regular / irregular / remit / surplus, per
                    month, and the top regular merchants; plus
                    from_savings (money moved in from the owner's own
                    securities / savings accounts) and the deficit split
                    into covered / unexplained; saving_names (the saving
                    line's names)
  spend-habits      the window by category after the methods; loan / remit /
                    saving apart (monthly = living spending)
  sub-tiers [--month YYYY-MM]
                    tiered / usage subscriptions (subscription_tiers.json):
                    per service every charge tagged with its tier (amount
                    band + plan words) or as usage, every scope kept; per
                    month the plan total, the tier, the usage total; tier
                    changes month over month

RULES (deterministic; the spec's tables, docs/ledger/onboarding-v5-spec.md)
  key        NFKC casefold; leading rail word (toss / naver pay / kakao
             pay) and period token ('26.03') dropped; cut at '(' '[' '/';
             letters only; then MERCHANT_ALIASES. Two keys sharing (method,
             exact amount >= 50,000) in >= 3 months are one group.
  monthly    >= 3 months, last seen <= 45 days before the records end;
             stable (max/min <= 1.3), coverage >= 70% and ~1 charge a month;
             or a variable word (utility / loan / insurance / telecom /
             saving) with coverage >= 50%; or a subscription key with
             coverage >= 70% (both: <= 3 charges a month)
  new pair   exactly 2 consecutive months, equal +-10%, a fixed word
  yearly     one month only, older than 45 days before the records end, a
             charge >= 10,000, a membership word or a subscription key; or
             two charges 11-13 months apart
  saving     a monthly group whose key carries a saving word: money kept
  income     income rows >= 100,000, >= 3 months, last seen <= 45 days;
             the regular payment = each month's largest (a one-off small
             credit under the same name is listed apart as `extra`);
             amount = the latest regular payment; nominal day = the max
             day of the last 3 (or the month's end); shift = prev / next /
             same only with evidence (a payment whose nominal day was a
             weekend / public holiday, `shift_evidence`), else None; a
             schedule change (`schedule_change`) when >= 2 earlier payments
             kept another nominal day
  irregular  non-fixed, non-saving expense >= max(floor, 3 x the median
             of its merchant key); floor = guard irregular_floor, else
             100,000 (stated default)
  own money  (owner answer 2026-10-10 19:38) a transfer INTO a non-savings
             account whose counterpart names a securities / savings account
             (OWN_SAVINGS words), or a transfer OUT of such an account to a
             non-savings one: money the owner moved from what he had kept.
             Both sides of one move count once (date + amount). Not income;
             shown as its own line and set against the deficit.
  tiers      a service in subscription_tiers.json (one merchant key): a
             charge is a plan charge when its amount sits in a tier band
             (KRW incl. VAT, FX tolerance) and, if the service lists
             plan_words, its descriptor carries one; every other charge is
             usage -> one fixed candidate per service (the actual monthly
             plan total of the latest month, tier tagged), usage counted as
             regular spending under the service's usage name
  aliases    a fixed cost renamed by the owner keeps the bank text as an
             alias (ledger_preference topic ledger_fixed_alias, item
             fixed:<id>/<key>): matching reads both
  twins      (real-data run 2026-10-11) a business account is never the
             twin of a personal one; two names with records in >= 2 months
             each are twins only with the same kind, a shared top place and
             a similar amount a month; a pay money / debit card links to an
             account of another brand only when that account's rows name
             its brand
  not fixed  a convenience store; an everyday-spending category (food,
             cafe, shopping ...) without a fixed-cost word; a business
             account's uncategorized transfer; under ~3,000 won, or under 1 %
             of a month's spending without such a word; a wandering day
             without such a word. Display names drop the bank's suffix noise
             (the raw text stays: `raw`, landed as an alias)
"""
import datetime
import difflib
import json
import os
import re
import statistics
import unicodedata

VERBS = ('ledger-records', 'methods-seen', 'fixed-detect', 'flow-picture', 'sub-tiers', 'spend-habits',
         'recurring-summary')

# Amounts below this (absolute KRW) never create an owner question or evidence
# row; the rows stay in the ledger and in every total. Same meaning as
# pay_method.dust and the "< 1,000 KRW" evidence rule.
DUST_KRW = 1000
IRREGULAR_FLOOR = 100000
INCOME_MIN = 100000
TWIN_AMOUNT_MIN = 50000
ACTIVE_DAYS = 45
WINDOW_DAYS = 400
PICTURE_MONTHS = 3
PICTURE_LOOKBACK = 6
FULL_TAIL_DAYS = 10
BODY_MIN = 10

RAIL_PREFIX = re.compile('^(\ud1a0\uc2a4|toss|\ub124\uc774\ubc84\ud398\uc774|naverpay|\uce74\uce74\uc624\ud398\uc774|kakaopay)\\s+')
PERIOD_PREFIX = re.compile(r'^\d{2,4}[./-]\d{1,2}\.?\s*')
# (key, regex on the letters-only name, display name)
MERCHANT_ALIASES = (
    ('kepco', re.compile('(\ud55c\uc804|\ud55c\uad6d\uc804\ub825)'), '\ud55c\uad6d\uc804\ub825'),
    ('kbinsurance', re.compile('^(kb\uc190|\ucf00\uc774\ube44\uc190)'), 'KB\uc190\ud574\ubcf4\ud5d8'),
    ('apple', re.compile('^(apple|\uc560\ud50c)$'), 'Apple'),
    ('anthropic', re.compile('^(anthropic|claude|\ud074\ub85c\ub4dc)'), 'Claude'),
    ('openai', re.compile('^(openai|chatgpt)'), 'ChatGPT'),
)
UTILITY = ('\uc804\uae30', '\ud55c\uc804', 'kepco', '\uac00\uc2a4', '\uc5d0\ub108\uc9c0', '\uc218\ub3c4', '\uad00\ub9ac\ube44')
LOAN = ('\ub300\ucd9c', '\uc6d0\ub9ac\uae08', '\uc0c1\ud658')
INSURANCE = ('\ubcf4\ud5d8', '\uc190\ud574', '\uc0dd\uba85', '\ud654\uc7ac', 'kbinsurance')
TELECOM = ('\ud1b5\uc2e0', '\uc778\ud130\ub137', 'skt', 'sk7', 'kt', 'lg\uc720\ud50c\ub7ec\uc2a4', '\uc54c\ub730\ud3f0',
           '\ubaa8\ubc14\uc77c', '\ud734\ub300\ud3f0')
SAVING = ('\uc801\uae08', '\uc5f0\uae08', '\uccad\uc57d', '\uc800\ucd95', '\ubaa8\uc73c\uae30')
MEMBERSHIP = ('\uba64\ubc84\uc2ed', '\uc5f0\ud68c\ube44', '\uc5f0\uac04', 'membership', 'annual', 'yearly')
SUBSCRIPTION = ('apple', 'appleservices', 'icloud', 'googleone', 'youtube', 'netflix', 'spotify', 'adobe',
                'microsoft', 'openai', 'chatgpt', 'claude', 'anthropic', 'notion', 'disney', 'tving',
                '\ud2f0\ube59', '\uc6e8\uc774\ube0c', '\uc653\ucc60', '\uba5c\ub860', '\uc720\ud29c\ube0c',
                '\ub137\ud50c\ub9ad\uc2a4', 'cursor', 'perplexity', 'midjourney', 'github')
# payment-method name -> kind, when the kit has no row for it
KIND_WORDS = (('easy_pay', ('\uac04\ud3b8\uacb0\uc81c',)), ('debit_card', ('\uccb4\ud06c',)),
              ('savings', ('\uc801\uae08', '\uccad\uc57d', '\ub3c4\uc57d', '\uc800\uae08\ud1b5', '\uae08\uace0')),
              ('securities', ('\uc99d\uad8c', 'cma')), ('credit_card', ('\uce74\ub4dc', 'card')),
              ('account', ('\uacc4\uc88c', '\ud1b5\uc7a5', '\uc740\ud589', '\ubc45\ud06c', 'bank', '\uc608\uae08')),
              ('easy_pay', ('\ud398\uc774', 'pay', '\uba38\ub2c8')), ('cash', ('\ud604\uae08', 'cash')))
# account-like kinds (kit 2.46.x: securities and installment savings join the plain account)
ACCOUNT_KINDS = ('account', 'securities', 'savings')
# a credit union's name carries the savings word; read without it
KIND_NOISE = '\uc0c8\ub9c8\uc744\uae08\uace0'
BANKS = (('ibk', ('ibk', '\uae30\uc5c5')), ('toss', ('\ud1a0\uc2a4',)), ('kb', ('kb', '\uad6d\ubbfc')),
         ('shinhan', ('\uc2e0\ud55c',)), ('woori', ('\uc6b0\ub9ac',)), ('hana', ('\ud558\ub098',)),
         ('nh', ('nh', '\ub18d\ud611')), ('kakao', ('\uce74\uce74\uc624\ubc45\ud06c', '\uce74\ubc45')),
         ('sc', ('sc\uc81c\uc77c', '\uc81c\uc77c')))
CARD_WORD = '\uce74\ub4dc'
CHECK_WORD = '\uccb4\ud06c'
EASY_WORD = '\uac04\ud3b8\uacb0\uc81c'
MONEY_WORD = '\uba38\ub2c8'
# latin -> hangul brand forms (on the alnum-only casefolded name)
BRAND_FORMS = (('global', '\uae00\ub85c\ubc8c'), ('naverpay', '\ub124\uc774\ubc84\ud398\uc774'),
               ('naver', '\ub124\uc774\ubc84'), ('kakaopay', '\uce74\uce74\uc624\ud398\uc774'),
               ('kakao', '\uce74\uce74\uc624'), ('toss', '\ud1a0\uc2a4'), ('payco', '\ud398\uc774\ucf54'),
               ('samsung', '\uc0bc\uc131'), ('hyundai', '\ud604\ub300'), ('shinhan', '\uc2e0\ud55c'),
               ('woori', '\uc6b0\ub9ac'), ('hana', '\ud558\ub098'))
# a bank's names -> one key, then the generic words a method name carries
BANK_FORMS = (('ibk\uae30\uc5c5\uc740\ud589', 'ibk'), ('\uae30\uc5c5\uc740\ud589', 'ibk'), ('kb\uad6d\ubbfc', 'kb'),
              ('\uad6d\ubbfc\uc740\ud589', 'kb'), ('nh\ub18d\ud611', 'nh'), ('\ub18d\ud611\uc740\ud589', 'nh'))
GENERIC_WORDS = ('\ubcf4\ud1b5\uc608\uae08', '\uc785\ucd9c\uae08', '\uc8fc\uac70\ub798', '\ud1b5\uc7a5', '\uacc4\uc88c',
                 '\uce74\ub4dc', 'card', '\uc608\uae08', '\uc740\ud589')
PAY_BRANDS = ('\ub124\uc774\ubc84\ud398\uc774', '\uce74\uce74\uc624\ud398\uc774', '\ud1a0\uc2a4', '\ud398\uc774\ucf54')
RENT = ('\uc6d4\uc138', '\uc784\ub300')
BUSINESS_WORDS = ('\uc0ac\uc5c5', '\ubc95\uc778', 'business')
# a crypto exchange: money moved there is kept money (saving), not spending
EXCHANGE = ('\ube57\uc378', 'bithumb', '\uc5c5\ube44\ud2b8', 'upbit', '\ucf54\uc778\uc6d0', 'coinone',
            '\ucf54\ube57', 'korbit')
# a counterpart / account name that is the owner's own kept money:
# securities, savings, deposits (maturity), CMA, funds, Toss "moa"
OWN_SAVINGS = ('\uc99d\uad8c', '\ud22c\uc790', 'cma', '\uc801\uae08', '\uc608\uae08', '\ub9cc\uae30', '\uc800\ucd95',
               '\uccad\uc57d', '\ud380\ub4dc', '\ubaa8\uc73c\uae30')
# kept-money words beyond OWN_SAVINGS (youth leap savings, piggy bank,
# vault); a demand-deposit / free in-out passbook is spending money
KEPT_EXTRA = ('\ub3c4\uc57d', '\uc800\uae08\ud1b5', '\uae08\uace0')
DEMAND_WORDS = ('\ubcf4\ud1b5\uc608\uae08', '\uc785\ucd9c\uae08', '\uc0c8\ub9c8\uc744\uae08\uace0')
ALIAS_TOPIC = 'ledger_fixed_alias'
# the export's category columns (Banksalad: major / minor category)
EXPORT_CAT = '\ub300\ubd84\ub958'
EXPORT_SUB = '\uc18c\ubd84\ub958'
EXPORT_TIME = '\uc2dc\uac04'
# volatile parts of a bank text (owner rehearsal 2026-10-11: one loan as
# "<loan>-NNNNNNN next-due-YYYYMMDD" every month): the next-due label with
# its date, a 'round N' counter; digits are dropped by the key anyway
VOLATILE = re.compile(r'(\ub2e4\uc74c)?\ub0a9\uc785\s*\uc608\uc815\uc77c[-:\s]*\d*|\d+\s*\ud68c\ucc28')
# a fixed charge the bank pulls by itself (auto debit / CMS)
AUTO_WORDS = ('\uc790\ub3d9\uc774\uccb4', '\uc790\ub3d9\ub0a9\ubd80', '\uc81c\ud734\ub0a9\ubd80', 'cms')
# money sent to a person (not a category of spending; its own line)
REMIT_WORDS = ('\uc1a1\uae08',)
HOUSING = '\uc8fc\uac70'
RENT_SUB = '\uc6d4\uc138'
# the one category for subscriptions (owner real-data run 2026-10-11): the
# export's 'service subscription' under online shopping, AI tools filed
# under education, app-store charges
SUB_CAT = '\uad6c\ub3c5'
APP_STORE = ('appstore', '\uc571\uc2a4\ud1a0\uc5b4', 'googleplay', '\uad6c\uae00\ud50c\ub808\uc774', 'itunes')
# never a fixed cost by itself: a convenience store, or a place in an
# everyday-spending category whose monthly repeat is a coincidence
CONVENIENCE = ('\ud3b8\uc758\uc810', 'gs25', '\uc138\ube10\uc77c\ub808\ube10', '7eleven', '\uc774\ub9c8\ud2b824',
               '\ubbf8\ub2c8\uc2a4\ud1b1', '\uc528\uc720')
EVERYDAY_CATS = ('\uc2dd\ube44', '\uce74\ud398/\uac04\uc2dd', '\uc220/\uc720\ud765', '\ud3b8\uc758\uc810/\ub9c8\ud2b8/\uc7a1\ud654',
                 '\ud328\uc158/\uc1fc\ud551', '\ubdf0\ud2f0/\ubbf8\uc6a9', '\uc5ec\ud589/\uc219\ubc15', '\uc628\ub77c\uc778\uc1fc\ud551')
RENTAL = ('\ub80c\ud0c8', '\ub80c\ud138')
TINY_FIXED = 3000
TINY_SHARE = 0.01
# '<insurer>son' = a non-life insurer in a bank text (DB son, <X>son 2 items)
INSURANCE_RX = re.compile('\uc190(\uac74)?$|^(db|kb|mg|nh)\uc190')
# bank suffix noise in a fixed cost's name (the raw text stays an alias)
NAME_NOISE = ('\uc81c\ud734\ub0a9\ubd80', '\uc790\ub3d9\uc774\uccb4', '\uc790\ub3d9\ub0a9\ubd80', '\uc11c\ube44\uc2a4')
NAME_NOUN_SUBS = ('\uc778\ud130\ub137', '\ud734\ub300\ud3f0', '\uad00\ub9ac\ube44', '\uc6d4\uc138')
GAS_WORDS = ('\uc5d0\ub108\uc9c0', '\ub3c4\uc2dc\uac00\uc2a4')
GAS_NOUN = '\uac00\uc2a4\ube44'
MOBILE_WORD = '\ubaa8\ubc14\uc77c'
MOBILE_NOUN = '\ud734\ub300\ud3f0'
TIERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'subscription_tiers.json')
CORP_WORDS = re.compile('(\uc8fc\uc2dd\ud68c\uc0ac|\\(\uc8fc\\))\\s*')


# ---- normalization -----------------------------------------------------------

def _base(raw):
    s = unicodedata.normalize('NFKC', raw or '').strip().casefold()
    s = VOLATILE.sub('', s)
    s = PERIOD_PREFIX.sub('', s)
    s = RAIL_PREFIX.sub('', s)
    s = re.split(r'[(\[/]', s, 1)[0]
    return ''.join(ch for ch in s if ch.isalpha())


def merchant_key(raw):
    s = _base(raw)
    for key, rx, _ in MERCHANT_ALIASES:
        if rx.search(s):
            s = key
            break
    return _MERGE.get(s, s)


# the agent's review of the recurring summary (ledger-setup SKILL, owner
# rehearsal 2026-10-11): name merges, category fixes, fixed vs variable;
# ledger_preference rows read once per call (records() loads them)
MERGE_TOPIC = 'ledger_merchant_merge'
CATFIX_TOPIC = 'ledger_category_fix'
HINT_TOPIC = 'ledger_fixed_hint'
_MERGE, _CATFIX, _HINT = {}, {}, {}
_CATNAME = {}


def _pref_rows(conn, topic):
    try:
        return conn.execute("SELECT item, note FROM ledger_preference WHERE topic=? AND status='active' "
                            "ORDER BY id", (topic,)).fetchall()
    except Exception:
        return []


def _review(conn):
    _MERGE.clear()
    _CATFIX.clear()
    _HINT.clear()
    for item, note in _pref_rows(conn, MERGE_TOPIC):
        k = (item or '')[4:] if (item or '').startswith('key:') else ''
        if k and note and note != k:
            _MERGE[k] = note
    for _ in range(4):          # a chain a -> b -> c lands on c
        for k, v in list(_MERGE.items()):
            if v in _MERGE and _MERGE[v] != k:
                _MERGE[k] = _MERGE[v]
    for topic, out in ((CATFIX_TOPIC, _CATFIX), (HINT_TOPIC, _HINT)):
        for item, note in _pref_rows(conn, topic):
            if (item or '').startswith('key:') and note:
                out[_MERGE.get(item[4:], item[4:])] = note


def display_name(raw, *, merchant=False):
    s = unicodedata.normalize('NFKC', raw or '').strip()
    # Bank-masked personal names are evidence, not merchant separators.
    if len(s) <= 4 and '*' in s and re.search(r'[\uac00-\ud7a3]', s) and re.fullmatch(r'[\uac00-\ud7a3*]+', s):
        return (raw or '').strip()
    s = PERIOD_PREFIX.sub('', s)
    s = CORP_WORDS.sub('', s).replace('*', ' ')
    s = re.split(r'\s{2,}', s.strip(), 1)[0]
    s = re.split(r'[-_ ]?\d{4,}', s, 1)[0]
    if merchant:
        # Preserve a textual product label (e.g. Shop(Membership)); ordinary
        # account displays keep their existing bank-memo cleanup below.
        s = re.sub(r'\(([^()]+)\)',
                   lambda m: ' '+m[1]+' ' if all(ch.isalpha() or ch.isspace() for ch in m[1]) else '', s)
    s = re.split(r'[(\[/]', s, 1)[0]
    s = re.sub(r'\s+', ' ', s.replace('_', ' ')).strip(' -')
    if merchant:
        # Work before the length cap so a repeated token cannot truncate the
        # useful name. Remove only whole legal suffixes, never substrings.
        s = re.sub(r'\b([\w]+)(?:\s+\1\b)+', r'\1', s, flags=re.I)
        s = re.sub(r'(?:[ ,]+(?:limited|ltd|incorporated|inc|corporation|corp|llc|plc)\.?)+$', '', s, flags=re.I).strip()
    if len(s) > 20:
        clipped = s[:20].rstrip()
        if merchant and not s[20].isspace() and ' ' in clipped:
            # Do not leave a partial trailing word such as a receipt memo.
            clipped = clipped.rsplit(' ', 1)[0]
        s = clipped
    return s or (raw or '').strip()


def _has(key, words):
    return any(w in key for w in words)


def kind_from_name(name):
    s = unicodedata.normalize('NFKC', name or '').casefold().replace(KIND_NOISE, '')
    for kind, words in KIND_WORDS:
        if _has(s, words):
            return kind
    return None


# ---- reading ----------------------------------------------------------------

def _month(d):
    return d[:7]


def _months_between(a, b):
    ya, ma = int(a[:4]), int(a[5:7])
    yb, mb = int(b[:4]), int(b[5:7])
    return (yb - ya) * 12 + (mb - ma)


def records(conn, personal_only=True):
    """-> (rows, transfers). row = {date, amount (signed: expense > 0, refund
    < 0), merchant, method, kind 'expense'|'income', src 'spend'|'pending',
    cat / sub (the export's category pair, or the kit category)}."""
    rows, transfers = [], []
    try:
        got = conn.execute('SELECT s.date, s.amount, s.name, s.method, s.kind, s.scope, '
                           'COALESCE(c.display_name, c.name) FROM spend_entry s '
                           'LEFT JOIN spend_category c ON c.id = s.category_id').fetchall()
    except Exception:
        got = [r + (None,) for r in conn.execute('SELECT date, amount, name, method, kind, scope FROM spend_entry')]
    _review(conn)
    for date, amount, name, method, kind, scope, cat in got:
        if personal_only and scope == 'business':
            continue
        if kind == 'transfer':               # v6 rows of class own_transfer / topup / card_bill / pass_through ...
            transfers.append({'date': date, 'amount': abs(int(amount)), 'merchant': name or '', 'method': method or '',
                              'kind': 'expense', 'src': 'spend', 'scope': scope or 'personal', 'cat': cat or '',
                              'sub': '', 'time': '', 'flow': 'out' if amount < 0 else 'in'})
            continue
        rows.append({'date': date, 'amount': int(amount), 'merchant': name or '', 'method': method or '',
                     'kind': kind or 'expense', 'src': 'spend', 'scope': scope or 'personal',
                     'cat': cat or '', 'sub': '', 'time': ''})
    # v6 (G-A): ONE read model -- landed rows only. A pending intake item is evidence waiting for a verdict, never
    # a payment: it is not counted (the 939-pending double count of the owner's first live run).
    rows = [r for r in rows if re.match(r'^\d{4}-\d{2}-\d{2}$', r['date'])]
    rows.sort(key=lambda r: (r['date'], r['time']))
    # the export's own housing category ('housing/telecom'): a rent fix
    # lands there, never in a second housing category (real-data run)
    house = {}
    for r in rows:
        if HOUSING in r['cat']:
            house[r['cat']] = house.get(r['cat'], 0) + 1
    _CATNAME['housing'] = max(sorted(house), key=lambda c: house[c]) if house else HOUSING
    transfers.sort(key=lambda r: r['date'])
    return _vouchered(conn, rows), transfers


def _weekday(iso):
    try:
        d = datetime.date.fromisoformat(iso)
    except ValueError:
        return False
    if d.weekday() >= 5:
        return False
    try:
        import holiday_source
        return not holiday_source.is_holiday(d)
    except Exception:
        return True


def _vouchered(conn, rows):
    """An employer meal voucher (owner rehearsal 2026-10-11: "company meal
    ticket 8000"): a pay method whose spending the employer pays up to N won
    per workday. Per (method, workday) the first N won of expense are taken
    off (largest charge first); only what is above it is the owner's money.
    A row taken off entirely leaves the records (counted on the method as
    `voucher`)."""
    vs = meal_vouchers(conn)
    if not vs:
        return rows
    regs = {m['id']: m for m in _registered_methods(conn)}
    names = {}
    for mid, amt in vs.items():
        m = regs.get(mid)
        if m:
            for n in [m['name']] + m['aliases']:
                names[n] = (mid, amt)
    left = {}
    for r in sorted((r for r in rows if r['kind'] == 'expense' and r['amount'] > 0 and r['method'] in names),
                    key=lambda r: (r['date'], -r['amount'])):
        mid, amt = names[r['method']]
        if not _weekday(r['date']):
            continue
        k = (mid, r['date'])
        rest = left.setdefault(k, amt)
        cut = min(rest, r['amount'])
        if cut:
            r['gross'] = r['amount']
            r['amount'] -= cut
            left[k] = rest - cut
    return [r for r in rows if not (r.get('gross') and r['amount'] <= 0)]


def _registered_methods(conn):
    out = []
    for mid, name, kind, aliases, linked in conn.execute(
            'SELECT id, name, kind, aliases, linked_account_id FROM pay_method ORDER BY id'):
        al = []
        try:
            al = json.loads(aliases or '[]')
            if not isinstance(al, list):
                raise ValueError()
        except (ValueError, TypeError):
            al = re.split(r'[|,]', aliases or '')
        out.append({'id': mid, 'name': name, 'kind': kind, 'linked_account_id': linked,
                    'aliases': [str(a).strip() for a in al if str(a).strip()]})
    return out


def _method_row(name, regs):
    for m in regs:
        if name == m['name'] or name in m['aliases']:
            return m
    return None


def import_origins(conn):
    """Origin apps backed by retained history, never merchant/bank names.

    ledger_v6's validated Banksalad parser stamps bs: keys. Other ingest
    adapters retain their origin in refs linked to actual spend rows. Merely
    enabling an intake source is not evidence of an imported export.
    """
    apps = set()
    if conn.execute("SELECT 1 FROM spend_entry WHERE import_key GLOB 'bs:*' LIMIT 1").fetchone():
        apps.add('banksalad')
    for source, in conn.execute(
            "SELECT DISTINCT r.source FROM ingest_ref r JOIN spend_entry s "
            "ON r.target_table='spend_entry' AND r.target_id=CAST(s.id AS TEXT)"):
        if source in ('banksalad', 'csv:banksalad', 'toss', 'csv:toss'):
            apps.add(source.split(':')[-1])
    for source, in conn.execute(
            "SELECT DISTINCT i.source FROM intake_item i JOIN intake_link l ON l.item_id=i.id "
            "JOIN block b ON b.id=l.block_id JOIN spend_entry s ON b.ulid='x-spend-' || s.id"):
        if source in ('csv:banksalad', 'csv:toss'):
            apps.add(source.split(':')[-1])
    return sorted(apps)


def info(conn):
    rows, transfers = records(conn, personal_only=False)
    # Intake counts all retained records, including non-spending transfers.
    # Keep the analytical population unchanged for existing readers.
    retained = rows + transfers
    dates = [r['date'] for r in retained]
    spend = [r for r in rows if r['src'] == 'spend']
    pending = [r for r in rows if r['src'] == 'pending']
    out = {'count': len(rows), 'spend': len(spend), 'pending': len(pending),
           'total_count': len(retained), 'transfer_count': len(transfers),
           'total_first': min(dates) if dates else None, 'total_last': max(dates) if dates else None,
           'first': rows[0]['date'] if rows else None, 'last': rows[-1]['date'] if rows else None,
           'full_months': [m for m, _ in _picture_months(rows)] if rows else [],
           'import_origins': import_origins(conn)}
    return out


# ---- methods -----------------------------------------------------------------

def _window(rows):
    if not rows:
        return rows
    end = datetime.date.fromisoformat(rows[-1]['date'])
    since = (end - datetime.timedelta(days=WINDOW_DAYS)).isoformat()
    return [r for r in rows if r['date'] >= since]


# ---- method names: normalized cores, twins, links (owner rehearsal 2026-10-11) --

def _norm(s):
    s = ''.join(ch for ch in unicodedata.normalize('NFKC', s or '').casefold() if ch.isalnum())
    for latin, hangul in BRAND_FORMS:
        s = s.replace(latin, hangul)
    return s


def name_core(s):
    """A method name without spaces / punctuation / generic words (passbook,
    account, card, deposit ...); latin brand forms in hangul; bank names as
    their key (IBK = the bank's hangul name)."""
    n = _norm(s)
    for word, key in BANK_FORMS:
        n = n.replace(word, key)
    for g in GENERIC_WORDS:
        n = n.replace(g, '')
    return n


def _strings(e):
    out = []
    for s in [e['name']] + list(e.get('aliases') or []):
        c = name_core(s)
        if len(c) >= 2 and c not in out:
            out.append(c)
    return out


def _covered(target, strings, core=None):
    """How many characters of `target` the strings explain: every occurrence
    of a string inside it, plus the matching blocks (>= 2 chars) with the
    other side's core name."""
    hit = [False] * len(target)
    for s in strings:
        start = target.find(s)
        while start >= 0:
            for k in range(start, start + len(s)):
                hit[k] = True
            start = target.find(s, start + 1)
    if core:
        for b in difflib.SequenceMatcher(None, target, core, autojunk=False).get_matching_blocks():
            if b.size >= 2:
                for k in range(b.a, b.a + b.size):
                    hit[k] = True
    return sum(hit)


def _family(kind):
    if kind in ACCOUNT_KINDS:
        return 'account'
    if kind in ('credit_card', 'corporate_card', 'debit_card'):
        return 'card'
    return kind


def _kinds_fit(a, b):
    ka, kb = a.get('kind'), b.get('kind')
    if ka and kb:
        return ka == kb
    return _family(ka or kb) in ('account', 'card')


def _business(e):
    """A business account / card by its own name or aliases."""
    return _has(_norm(' '.join([e['name']] + list(e.get('aliases') or []))), BUSINESS_WORDS)


def _same_use(a, b):
    """Both names carry records: the same kind, a shared top merchant and a
    similar amount a month (owner real-data run 2026-10-11: a name alike
    is not enough)."""
    if not (a.get('kind') and a.get('kind') == b.get('kind')):
        return False
    if not set((a.get('merchants') or [])[:3]) & set((b.get('merchants') or [])[:3]):
        return False
    x, y = a.get('_per_month') or 0, b.get('_per_month') or 0
    return bool(x and y) and min(x, y) >= 0.5 * max(x, y)


def _twin_score(a, b):
    """(rank, explained share of the better side, of the other) or None.
    rank 3 = the same core name; 2 = one name / alias inside the other;
    1 = the names share >= 80 % (matching blocks), or a weaker share with
    the same top merchants and a similar monthly amount. A business account
    is never the twin of a personal one; two names that both carry records
    (in >= 2 months each) need the same use on top of the names (owner
    real-data run 2026-10-11); a name with a stray record or two has too
    little to judge by, so the names decide (CASE 23)."""
    if not _kinds_fit(a, b) or _family(a.get('kind') or b.get('kind')) not in ('account', 'card'):
        return None
    if _business(a) != _business(b):
        return None
    if (a.get('_months') or 0) >= 2 and (b.get('_months') or 0) >= 2 and not _same_use(a, b):
        return None
    sa, sb = _strings(a), _strings(b)
    ca, cb = name_core(a['name']), name_core(b['name'])
    if not ca or not cb:
        return None
    if set(sa) & set(sb):
        rank = 3
    elif any(x in y for x in sa for y in sb) or any(y in x for x in sa for y in sb):
        rank = 2
    else:
        short = min(len(ca), len(cb))
        blocks = sum(m.size for m in difflib.SequenceMatcher(None, ca, cb, autojunk=False).get_matching_blocks()
                     if m.size >= 2)
        share = blocks / short if short >= 4 else 0
        same_use = (a.get('count') and b.get('count') and len(set(a['merchants'][:3]) & set(b['merchants'][:3])) >= 2
                    and min(a['monthly'], b['monthly']) >= 0.7 * max(a['monthly'], b['monthly']))
        if share >= 0.8 or (share >= 0.5 and same_use):
            rank = 1
        else:
            return None
    da = _covered(ca, sb, cb) / len(ca)
    db = _covered(cb, sa, ca) / len(cb)
    return (rank, max(da, db), min(da, db))


def _activity(e):
    return e.get('count', 0) + e.get('moved', 0)


def find_twins(entries):
    """{twin name: primary name} -- the same account / card under two names
    (kit row vs export name, export vs export). Best pairs first; a name is
    the twin of one primary only; a primary takes a second twin only on the
    same core name. Primary: the registered one (lowest id), else the most
    active."""
    pairs = []
    for i, a in enumerate(entries):
        for b in entries[i + 1:]:
            sc = _twin_score(a, b)
            if sc:
                pairs.append((sc, a['name'], b['name']))
    pairs.sort(key=lambda p: (-p[0][0], -p[0][1], -p[0][2], p[1], p[2]))
    by = {e['name']: e for e in entries}
    twin_of, primary = {}, {}

    def order(e):
        return (0 if e.get('registered') else 1, e.get('method_id') or 0, -_activity(e), e['name'])
    for sc, x, y in pairs:
        if x in twin_of or y in twin_of:
            continue
        p, t = sorted((by[x], by[y]), key=order)
        if t['name'] in primary:
            if p['name'] in primary:
                continue
            p, t = t, p
            if t.get('registered') and not p.get('registered'):
                continue
        if p['name'] in primary and sc[0] < 3:
            continue
        twin_of[t['name']] = p['name']
        primary.setdefault(p['name'], []).append(t['name'])
    return twin_of


def _bank(name):
    s = _norm(name)
    for key, words in BANKS:
        if any(w in s for w in words):
            return key
    return None


def _pay_brand(name):
    s = _norm(name)
    for b in PAY_BRANDS:
        if b in s:
            return b
    return None


def _link_score(card, acct):
    """How well an account's name explains a debit card / pay money's name:
    (explained chars of the card's core, -unexplained chars of the account)."""
    cc, ca = name_core(card['name']).replace(CHECK_WORD, ''), name_core(acct['name'])
    if not cc or not ca:
        return None
    n = _covered(cc, _strings(acct), ca)
    if n < 3:
        return None
    return (n, -(len(ca) - _covered(ca, [cc], cc)))


def _brands(name):
    """The pay brand / bank a method name carries."""
    return {b for b in (_pay_brand(name), _bank(name)) if b}


def _brand_fit(e, a, rows_by_acct):
    """A pay money / debit card links to an account of another brand only
    on the records' word (owner real-data run 2026-10-11: one brand's pay
    money was linked to another brand's account by the shared 'pay money'
    words): the account's rows name the card / pay's brand (a charge)."""
    eb, ab = _brands(e['name']), _brands(a['name'])
    if not eb or not ab or eb & ab:
        return True
    return any(any(b in _norm(r['merchant']) for b in eb) for r in rows_by_acct.get(a['name'], ()))


def infer_link(e, accounts, rows_by_acct):
    """The account a card / easy-pay's money leaves (inferred): a credit
    card -> the account with bill rows naming its brand; a debit card / pay
    money -> the account whose name explains it best (same bank / pay
    brand); else the only account of the same bank. Never across brands
    without the records' word. None when unsure."""
    kind = e.get('kind')
    if kind in ('credit_card', 'corporate_card'):
        brand = _card_brand(e['name'])
        if brand:
            hits = {a: n for a, n in ((a, sum(1 for r in rows_by_acct.get(a, ()) if brand in _base(r['merchant'])))
                                      for a in accounts) if n}
            if len(hits) == 1:
                return next(iter(hits))
        return None
    if kind == 'easy_pay' and not (MONEY_WORD in _norm(e['name'])):
        return None
    if kind not in ('debit_card', 'easy_pay'):
        return None
    best = []
    for a in accounts.values():
        if not _brand_fit(e, a, rows_by_acct):
            continue
        sc = _link_score(e, a)
        if sc:
            best.append((sc, a['name']))
    best.sort(key=lambda x: (-x[0][0], -x[0][1], x[1]))
    if best and (len(best) == 1 or best[0][0] != best[1][0]):
        return best[0][1]
    if kind == 'debit_card':
        bank = _bank(e['name'])
        same = [a for a in accounts.values() if bank and _bank(a['name']) == bank]
        if len(same) == 1:
            return same[0]['name']
    return None


ROLES = ('salary_fixed', 'living', 'saving', 'business')
ROLE_TOPIC = 'ledger_method_role'


def method_roles(conn):
    """{method id: [roles]} the owner confirmed (ledger_preference topic
    ledger_method_role, item method:<id>, note = role keys joined by ','):
    one account can be salary_fixed AND living (owner rehearsal 2026-10-11)."""
    out = {}
    for item, note in _pref_rows(conn, ROLE_TOPIC):
        m = re.match(r'^method:(\d+)$', item or '')
        rs = [r for r in (note or '').split(',') if r in ROLES]
        if m and rs:
            out[int(m.group(1))] = rs
    return out


MEMO_TOPIC = 'ledger_method_memo'
VOUCHER_TOPIC = 'ledger_meal_voucher'


def method_memos(conn):
    """{method id: the owner's free 'what it is for' words}."""
    out = {}
    for item, note in _pref_rows(conn, MEMO_TOPIC):
        m = re.match(r'^method:(\d+)$', item or '')
        if m and (note or '').strip():
            out[int(m.group(1))] = note.strip()
    return out


def meal_vouchers(conn):
    """{method id: won the employer pays per workday} (0 = none)."""
    out = {}
    for item, note in _pref_rows(conn, VOUCHER_TOPIC):
        m = re.match(r'^method:(\d+)$', item or '')
        if m and str(note or '').isdigit() and int(note) > 0:
            out[int(m.group(1))] = int(note)
    return out


def _fixed_like(key):
    return _has(key, UTILITY + LOAN + INSURANCE + TELECOM + RENT)


def infer_role(e):
    """An account's (or an unlinked card's) role from its own records:
    business by name; savings / securities kept money; salary in or rent /
    loan / insurance out -> salary_fixed; daily spending -> living."""
    core = _norm(' '.join([e['name']] + list(e.get('twins') or []) + list(e.get('aliases') or [])))
    if _has(core, BUSINESS_WORDS):
        return 'business'
    kind = e.get('kind')
    spent = e.get('spent', 0) + e.get('linked_spent', 0)
    if kind == 'savings' or (kind == 'securities' and spent < 3):
        return 'saving'
    if e.get('salary') or (e.get('total') and e.get('fixed_amt', 0) * 10 >= e['total'] * 4):
        return 'salary_fixed'
    if spent >= 3:
        return 'living'
    if _has(core, SAVING) or (e.get('moved_in') and not e.get('count')):
        return 'saving'
    return None


AVG_MONTHS = 3


def _monthly(rs, months):
    """The monthly average over the recent full months (owner rehearsal
    2026-10-11: amounts are the recent 3 full months' average); an item
    that started inside them is averaged from its start."""
    own = [r for r in rs if not r.get('flow') and r['kind'] == 'expense']
    if not own or not months:
        return 0
    first = min(_month(r['date']) for r in rs)
    span = [m for m in months if m >= first] or months
    return sum(r['amount'] for r in own if _month(r['date']) in months) // len(span)


def window_months(rows, n=AVG_MONTHS):
    """THE window every onboarding screen reads (owner real-data run
    2026-10-11: one window, the same numbers on every screen): the recent n
    full months over every record (all scopes), oldest first."""
    return [m for m, _ in _picture_months(_window(rows), n)]


def ledger_window(conn, n=AVG_MONTHS):
    return window_months(records(conn, personal_only=False)[0], n)


def _is_remit(r):
    """Money sent to a person (export's remittance words): its own line,
    never a category or a place the money went."""
    cat, sub = fix_category(r)
    return _has(cat + sub + _norm(r['merchant']), REMIT_WORDS)


def methods_seen(conn):
    rows, transfers = records(conn, personal_only=False)
    recent = window_months(rows)
    rows = _window(rows)
    if rows:
        since = (datetime.date.fromisoformat(rows[-1]['date']) - datetime.timedelta(days=WINDOW_DAYS)).isoformat()
        transfers = [t for t in transfers if t['date'] >= since]
    regs = _registered_methods(conn)
    by_id = {m['id']: m for m in regs}
    from ledger_instrument_facts import records as instrument_records, confirmed_roles
    instrument_facts = instrument_records(conn)
    memos = method_memos(conn)
    vouchers = meal_vouchers(conn)
    months_all = sorted({_month(r['date']) for r in rows})
    n_months = max(1, len(months_all))
    seen = {}

    def entry(name, m):
        return seen.setdefault(name, {'name': name, 'count': 0, 'total': 0, 'income': 0, 'merchants': {},
                                      'registered': m is not None, 'moved': 0, 'moved_in': 0, 'fixed_amt': 0,
                                      'salary_months': set(), 'rows': [], 'spent': 0})
    for r in rows:
        if not r['method']:
            continue
        m = _method_row(r['method'], regs)
        e = entry(m['name'] if m else r['method'], m)
        e['count'] += 1
        e['rows'].append(r)
        if r['kind'] == 'income':
            e['income'] += r['amount']
            if r['amount'] >= INCOME_MIN:
                e['salary_months'].add(_month(r['date']))
        else:
            e['total'] += r['amount']
            e['spent'] += 1
            k = merchant_key(r['merchant'])
            if _fixed_like(k):
                e['fixed_amt'] += r['amount']
    for t in transfers:
        if not t['method']:
            continue
        m = _method_row(t['method'], regs)
        e = entry(m['name'] if m else t['method'], m)
        e['moved'] += 1
        e['rows'].append(t)
        if t.get('flow') == 'in':
            e['moved_in'] += t['amount']
    for m in regs:
        entry(m['name'], m)
    out = []
    for e in seen.values():
        m = _method_row(e['name'], regs)
        named = kind_from_name(e['name'])
        kind = m['kind'] if m else named
        kind_from = 'registered' if m else ('name' if kind else None)
        # the name outranks a stored kind for these (owner rehearsal
        # 2026-10-11: "<pay> easy payment" stored as a credit card; a
        # securities / savings account stored as a plain account)
        if m and named and named != kind and (named == 'easy_pay' and EASY_WORD in _norm(e['name'])
                                             or (kind == 'account' and named in ('securities', 'savings'))):
            kind, kind_from = named, 'name'
        linked = by_id[m['linked_account_id']]['name'] if m and m['linked_account_id'] in by_id else None
        out.append({'name': e['name'], 'kind': kind, 'kind_from': kind_from, 'count': e['count'],
                    'total': e['total'], 'income': e['income'], 'monthly': e['total'] // n_months,
                    'merchant_totals': e['merchants'], 'registered': bool(m), 'method_id': m['id'] if m else None,
                    'aliases': m['aliases'] if m else [], 'linked': linked, 'bill_account': None, 'twin_of': None,
                    'twins': [], 'moved': e['moved'], 'moved_in': e['moved_in'], 'fixed_amt': e['fixed_amt'],
                    'salary': len(e['salary_months']) >= 3, 'spent': e['spent'], '_rows': e['rows']})
    # card / pay brands and own account names: a merchant that is one of
    # them is a bill or a move, never "where the money went"
    own = [x for x in out]
    # This roster is fixed during the merchant pass. Normalize its account
    # names once, rather than once per payment (also used by plan roles).
    own_cores = _own_cores(own)
    brands = {b for b in (_card_brand(x['name']) for x in own if x['kind'] in ('credit_card', 'corporate_card', 'debit_card')) if b}
    for x in out:
        tops = {}
        for r in x['_rows']:
            if r.get('flow') or r['kind'] == 'income':
                continue
            merchant_base = _base(r['merchant'])
            if any(b in merchant_base for b in brands) or _is_own(r['merchant'], own, cores=own_cores) or _is_remit(r):
                continue
            dn = display_name(r['merchant'])
            tops[dn] = tops.get(dn, 0) + r['amount']
        spent_months = {_month(r['date']) for r in x['_rows'] if not r.get('flow') and r['kind'] == 'expense'}
        x['_per_month'] = x['total'] // len(spent_months) if spent_months else 0
        x['_months'] = len(spent_months)
        x['merchant_totals'] = tops
        x['merchants'] = [k for k, _ in sorted(tops.items(), key=lambda kv: (-kv[1], kv[0]))[:3]]
        x['monthly'] = _monthly(x['_rows'], recent) if recent else x['monthly']
    out.sort(key=lambda e: (-e['total'], -e['count'], -e['moved'], e['name']))
    twin_of = find_twins(out)
    by_name = {e['name']: e for e in out}
    for t, p in twin_of.items():
        by_name[t]['twin_of'] = p
        by_name[p]['twins'].append(t)
    # merged numbers on the primary (the twins' records are its records)
    for e in out:
        if not e['twins']:
            continue
        group = [e] + [by_name[t] for t in e['twins']]
        e['count'] = sum(g['count'] for g in group)
        e['total'] = sum(g['total'] for g in group)
        e['income'] = sum(g['income'] for g in group)
        e['moved'] = sum(g['moved'] for g in group)
        e['spent'] = sum(g['spent'] for g in group)
        e['moved_in'] = sum(g['moved_in'] for g in group)
        e['fixed_amt'] = sum(g['fixed_amt'] for g in group)
        e['salary'] = any(g['salary'] for g in group)
        tops = {}
        for g in group:
            for name, v in g['merchant_totals'].items():
                tops[name] = tops.get(name, 0) + v
        e['merchant_totals'] = tops
        e['merchants'] = [k for k, _ in sorted(tops.items(), key=lambda kv: (-kv[1], kv[0]))[:3]]
        e['_rows'] = [r for g in group for r in g['_rows']]
        e['monthly'] = _monthly(e['_rows'], recent) if recent else e['total'] // n_months
        if not e['kind']:
            e['kind'] = next((g['kind'] for g in group if g['kind']), None)
    accounts = {e['name']: e for e in out if e['kind'] in ACCOUNT_KINDS and not e['twin_of']}
    rows_by_acct = {n: a['_rows'] for n, a in accounts.items()}
    for e in out:
        if e['twin_of'] or e['kind'] not in ('credit_card', 'corporate_card', 'debit_card', 'easy_pay'):
            continue
        if e['linked']:
            e['linked'] = twin_of.get(e['linked'], e['linked'])
            continue
        e['bill_account'] = infer_link(e, accounts, rows_by_acct)
    for e in out:
        acct = accounts.get(e['linked'] or e['bill_account'] or '') if not e['twin_of'] else None
        if acct is not None and acct is not e:
            acct['linked_spent'] = acct.get('linked_spent', 0) + e['spent']
    # roles: the owner's word first; an account from its records; a card /
    # pay inherits its account's role, else from its own records
    for e in out:
        if e['twin_of']:
            continue
        fact = instrument_facts.get(e['method_id'], {})
        own_role, source = confirmed_roles(fact)
        if own_role:
            e['roles'], e['role_from'] = own_role, source
        elif e['kind'] in ACCOUNT_KINDS or e['kind'] is None:
            r = infer_role(e)
            e['roles'] = [r] if r else []
            e['role_from'] = 'inferred' if r else None
    for e in out:
        if e['twin_of'] or 'roles' in e:
            continue
        acct = accounts.get(e['linked'] or e['bill_account'] or '')
        if acct and acct.get('roles'):
            e['roles'] = list(acct['roles'])
            e['role_from'] = 'account' if acct.get('role_from') == 'owner' else 'inferred'
        else:
            r = infer_role(e)
            e['roles'] = [r] if r else []
            e['role_from'] = 'inferred' if r else None
    for e in out:
        e.setdefault('roles', [])
        e.setdefault('role_from', None)
        e['role'] = e['roles'][0] if e['roles'] else None
        e['instrument_facts'] = instrument_facts.get(e['method_id'])
        e['memo'] = memos.get(e['method_id'])
        e['meal_voucher'] = vouchers.get(e['method_id'])
        e['active'] = bool(e['count'] or e['moved'])
        # money put into a savings / securities account a month (its block
        # says it; such an account is never 'no records')
        e['saved_monthly'] = (sum(r['amount'] for r in e.get('_rows') or () if r.get('flow') == 'in'
                                  and _month(r['date']) in recent) // len(recent)) if recent else 0
        e['generic'] = not name_core(e['name'])
        # [name, total] of the year, for an account block that sums in its cards
        e['merchant_sums'] = [[k, v] for k, v in sorted((e.get('merchant_totals') or {}).items(),
                                                        key=lambda kv: (-kv[1], kv[0]))[:6]]
        for k in ('_rows', 'merchant_totals', 'fixed_amt', 'linked_spent', '_per_month', '_months'):
            e.pop(k, None)
    return {'months': n_months, 'avg_months': recent, 'methods': out}


def _own_cores(entries):
    return [s for e in entries if e.get('kind') in ACCOUNT_KINDS
            for n in [e['name']] + [a for a in e.get('aliases') or [] if kind_from_name(a) in ACCOUNT_KINDS]
            for s in [name_core(n)] if len(s) >= 3]


def _is_own(merchant, entries, *, cores=None):
    """A merchant text that names one of the owner's accounts (a move
    between own accounts, not spending)."""
    mc = name_core(merchant)
    if len(mc) < 3:
        return False
    # Only account names / account aliases count, never a pay brand alias.
    return any(s == mc or (len(s) >= 4 and s in mc)
               for s in (_own_cores(entries) if cores is None else cores))


def _card_brand(name):
    s = unicodedata.normalize('NFKC', name or '').casefold().replace(' ', '')
    m = re.match('^([\uac00-\ud7a3]+)', s)
    if not m:
        return None
    core = m.group(1)
    if CARD_WORD in core:
        core = core.split(CARD_WORD)[0]
    return (core + CARD_WORD) if core else None


# ---- tiered / usage subscriptions (owner answer 2026-10-10 19:38) -------------

_SERVICES = {}


def services(path=None):
    """{merchant key: service} from subscription_tiers.json (data, sourced and
    dated there). A missing / broken file = no tiered service."""
    path = path or TIERS_FILE
    if path not in _SERVICES:
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, ValueError):
            data = {}
        _SERVICES[path] = {sv['key']: sv for sv in data.get('services', []) if sv.get('key') and sv.get('tiers')}
    return _SERVICES[path]


def tier_of(r, svc):
    """The tier a charge pays for, or None (= usage): the amount sits in a
    band and, when the service lists plan words, the descriptor has one."""
    if r['kind'] != 'expense' or r['amount'] <= 0:
        return None
    words = svc.get('plan_words') or []
    if words and not _has(_base(r['merchant']), words):
        return None
    for t in svc['tiers']:
        if t['lo'] <= r['amount'] <= t['hi']:
            return t['tier']
    return None


def _plan_months(charges):
    """[{month, plan, usage, usage_business, tier}] oldest first; the month's
    tier = the tier of its last plan charge."""
    months = {}
    for ch in charges:
        m = months.setdefault(ch['date'][:7], {'month': ch['date'][:7], 'plan': 0, 'usage': 0,
                                               'usage_business': 0, 'tier': None})
        if ch['tier']:
            m['plan'] += ch['amount']
            m['tier'] = ch['tier']
        else:
            m['usage'] += ch['amount']
            if ch['scope'] == 'business':
                m['usage_business'] += ch['amount']
    return [months[k] for k in sorted(months)]


def _changes(charges, months):
    """Every switch between consecutive plan charges (a month can hold two:
    Max 5x -> Max 20x -> Pro), with that month's plan total."""
    plan = {m['month']: m['plan'] for m in months}
    out, prev = [], None
    for ch in sorted((c for c in charges if c['tier']), key=lambda c: c['date']):
        if prev and prev != ch['tier']:
            out.append({'month': ch['date'][:7], 'date': ch['date'], 'from': prev, 'to': ch['tier'],
                        'plan': plan.get(ch['date'][:7], 0)})
        prev = ch['tier']
    return out


def sub_tiers(conn, month=None):
    rows, _ = records(conn, personal_only=False)
    out = []
    for key, svc in sorted(services().items()):
        charges = []
        for r in rows:
            if r['kind'] != 'expense' or merchant_key(r['merchant']) != key:
                continue
            t = tier_of(r, svc)
            charges.append({'date': r['date'], 'amount': r['amount'], 'merchant': r['merchant'],
                            'method': r['method'], 'scope': r.get('scope') or 'personal', 'tier': t,
                            'usage': t is None})
        if not charges:
            continue
        months = _plan_months(charges)
        changes = _changes(charges, months)
        if month:
            charges = [ch for ch in charges if ch['date'][:7] == month]
            months = [m for m in months if m['month'] == month]
            changes = [ch for ch in changes if ch['month'] == month]
        out.append({'key': key, 'name': svc.get('name'), 'usage_name': svc.get('usage_name'),
                    'charges': charges, 'months': months, 'changes': changes})
    return {'services': out}


# ---- own money moved in (securities / savings -> spending account) -----------

def savings_inflows(transfers):
    """[{date, amount, source}] -- each move from a securities / savings
    account into a spending account, once (an export lists both sides)."""
    sides = {}
    for r in transfers:
        mi, ai = _kept(r['merchant']), _kept(r['method'])
        if r.get('flow') == 'in' and mi and not ai:
            side, src = 'in', display_name(r['merchant'])
        elif r.get('flow') == 'out' and ai and not mi:
            side, src = 'out', r['method']
        else:
            continue
        sides.setdefault((r['date'], r['amount']), {'in': [], 'out': []})[side].append(src)
    out = []
    for (date, amount), sd in sorted(sides.items()):
        names = sd['out'] or sd['in']
        for k in range(max(len(sd['in']), len(sd['out']))):
            out.append({'date': date, 'amount': amount, 'source': names[min(k, len(names) - 1)]})
    return out


# ---- own money moved out (spending account -> securities / savings / exchange) -

def _kept(text):
    """A name that is the owner's kept money (securities / savings /
    exchange); a demand-deposit passbook ('ordinary deposit') is not."""
    k = _norm(text)
    if _has(k, DEMAND_WORDS):
        return False
    return _has(k, OWN_SAVINGS + KEPT_EXTRA) or _has(k, EXCHANGE)


def savings_outflows(transfers, kept_names=()):
    """[{date, amount, target}] -- each move from a spending account into a
    securities / savings account or a crypto exchange, once (an export
    lists both sides): kept money, a saving -- never spending (owner
    rehearsal 2026-10-11). Sum only; balances and returns are not read."""
    kept_names = set(kept_names)

    def kept(name):
        return name in kept_names or _kept(name)
    sides = {}
    for r in transfers:
        mi, ai = kept(r['merchant']), kept(r['method'])
        if r.get('flow') == 'out' and (mi or r.get('cat') in SAVE_CATS) and not ai:
            side, tgt = 'out', display_name(r['merchant'])
        elif r.get('flow') == 'in' and ai and not mi:
            side, tgt = 'in', r['method']
        else:
            continue
        sides.setdefault((r['date'], r['amount']), {'in': [], 'out': []})[side].append(tgt)
    out = []
    for (date, amount), sd in sorted(sides.items()):
        names = sd['in'] or sd['out']
        for k in range(max(len(sd['in']), len(sd['out']))):
            out.append({'date': date, 'amount': amount, 'target': names[min(k, len(names) - 1)]})
    return out


def _kept_names(conn, transfers):
    out = set()
    for m in _registered_methods(conn):
        if m['kind'] in ('securities', 'savings') or kind_from_name(m['name']) in ('securities', 'savings'):
            out.update([m['name']] + m['aliases'])
    for t in transfers:
        if kind_from_name(t['method']) in ('securities', 'savings'):
            out.add(t['method'])
    return out


# ---- the spending habit of the year (onboarding screen after the methods) -------

# the export's transfer categories for money put away (savings / investment)
SAVE_CATS = ('\uc800\ucd95', '\ud22c\uc790')
HABIT_TOP = 5
HABIT_SUBS = 3
LOAN_SHARE = 0.25
OWN_MOVE_WORDS = ('\ub0b4\uacc4\uc88c\uc774\uccb4',)
BILL_PAIR = ('\uae08\uc735', '\uce74\ub4dc')
CHARGE_SUB = '\uacb0\uc81c/\ucda9\uc804'
LOAN_SUB = '\ub300\ucd9c'
UNCLASSIFIED = '\ubbf8\ubd84\ub958'


def _spend_filter(conn, transfers):
    """r -> None | 'saving' | 'own' | 'bill' | 'charge': an expense row that
    is not spending once the methods are known (twins are one method):
    money put into kept money (securities / savings / exchange), a move
    between own accounts, a card bill paid from an account, pay money
    charged from an own account."""
    seen = methods_seen(conn)['methods']
    by_name = {e['name']: e for e in seen}
    regs = _registered_methods(conn)

    def kind_of(name):
        e = by_name.get(name)
        if e is None:
            m = _method_row(name, regs)
            e = by_name.get(m['name']) if m else None
        if e is None:
            return kind_from_name(name)
        if e.get('twin_of'):
            e = by_name.get(e['twin_of'], e)
        return e.get('kind')
    accts = [e for e in seen if (e.get('kind') or kind_of(e['twin_of'] or '')) in ACCOUNT_KINDS]
    kept_names = _kept_names(conn, transfers) | {e['name'] for e in seen if e.get('kind') in ('securities', 'savings')}
    brands = {b for b in (_card_brand(e['name']) for e in seen if e.get('kind') in ('credit_card', 'corporate_card', 'debit_card')) if b}
    pays = {b for b in (_pay_brand(e['name']) for e in seen if e.get('kind') == 'easy_pay') if b}

    def why(r):
        mk = _base(r['merchant'])
        kind = kind_of(r['method'])
        on_acct = kind in ACCOUNT_KINDS
        # money put INTO kept money (not a payment made FROM a securities /
        # savings account: that is spending by its role)
        if kind not in ('securities', 'savings') and (_has(mk, SAVING) or _kept(r['merchant'])
                                                       or r['merchant'] in kept_names):
            return 'saving'
        if r['cat'] in OWN_MOVE_WORDS or r['sub'] in OWN_MOVE_WORDS or _is_own(r['merchant'], accts):
            return 'own'
        if on_acct and ((r['cat'], r['sub']) == BILL_PAIR or any(b in mk for b in brands)):
            return 'bill'
        if on_acct and r['sub'] == CHARGE_SUB and _pay_brand(r['merchant']) in pays:
            return 'charge'
        return None
    def canon(name):
        """The one name of a method (a twin's primary; a registered alias's
        row): changes are read on these names only."""
        e = by_name.get(name)
        if e is None:
            m = _method_row(name, regs)
            return m['name'] if m else name
        return e.get('twin_of') or name

    def business(name):
        e = by_name.get(canon(name))
        return bool(e and ('business' in (e.get('roles') or []) or _business(e)))
    why.kept_names = kept_names
    why.kind_of = kind_of
    why.canon = canon
    why.business = business
    return why


def _loan_row(r, cat, sub):
    return HOUSING not in cat and (LOAN_SUB in sub or _has(_base(r['merchant']), LOAN))


def flows(conn, n=AVG_MONTHS):
    """THE one computation of the window (owner real-data run 2026-10-11:
    the habits, gap and wrap screens said different numbers): every
    personal record of the recent n full months, each with its class --
    income | own | bill | charge (not spending: a move between own
    accounts, a card bill, pay money charged) | saving (into kept money) |
    remit (to a person) | loan (repayment) | spend; plus the moves into /
    out of kept money in those months."""
    rows, transfers = records(conn)
    months = ledger_window(conn, n)
    ns = _spend_filter(conn, transfers)
    out = []
    for r in rows:
        if _month(r['date']) not in months:
            continue
        cat, sub = fix_category(r)
        if r['kind'] == 'income':
            cls = 'income'
        else:
            cls = ns(r)
            if cls:
                pass
            elif _is_remit(r):
                cls = 'remit'
            elif _loan_row(r, cat, sub):
                cls = 'loan'
            else:
                cls = 'spend'
        out.append(dict(r, cls=cls, fcat=cat, fsub=sub))
    kept = [ev for ev in savings_outflows(transfers, ns.kept_names) if _month(ev['date']) in months]
    moved = [ev for ev in savings_inflows(transfers) if _month(ev['date']) in months]
    return {'months': months, 'rows': out, 'kept': kept, 'moved': moved, 'all': rows, 'filter': ns}


def spend_habits(conn):
    """The recent full months in categories, AFTER the methods step (owner
    real-data run 2026-10-11: the same window as every other screen).
    Not spending: moves between own accounts, card bills paid from an
    account, pay money charged from an own account; money kept (securities
    / savings / exchange), money sent to a person and loan repayment are
    summed apart ("money seen apart"), never ranked as a category. monthly
    = what was spent on living = the total minus those."""
    f = flows(conn)
    n = len(f['months'])
    cats, living, loan, saving, remit, count = {}, 0, 0, 0, 0, 0
    excluded = {'own': 0, 'bill': 0, 'saving': 0, 'charge': 0, 'remit': 0, 'loan': 0}
    for r in f['rows']:
        if r['kind'] != 'expense':
            continue
        amt, cls = r['amount'], r['cls']
        if cls in excluded:
            excluded[cls] += 1
        if cls == 'saving':
            saving += amt
        elif cls == 'remit':
            remit += amt
        elif cls == 'loan':
            loan += amt
        elif cls == 'spend':
            count += 1
            living += amt
            c = cats.setdefault(r['fcat'] or UNCLASSIFIED, {'total': 0, 'subs': {}})
            c['total'] += amt
            sub = r['fsub']
            if sub and sub != UNCLASSIFIED:
                c['subs'][sub] = c['subs'].get(sub, 0) + amt
    saving += sum(ev['amount'] for ev in f['kept'])
    if not n:
        return {'count': 0, 'months': 0, 'month_list': [], 'monthly': 0, 'categories': [], 'loan_monthly': 0,
                'loan_pct': 0, 'loan_dominant': False, 'saving_monthly': 0, 'remit_monthly': 0, 'spend_monthly': 0,
                'total_monthly': 0, 'excluded': excluded, 'first': None, 'last': None}
    out = []
    for name, c in sorted(cats.items(), key=lambda kv: (-kv[1]['total'], kv[0])):
        if c['total'] <= 0:
            continue
        subs = sorted(c['subs'].items(), key=lambda kv: (-kv[1], kv[0]))[:HABIT_SUBS]
        out.append({'name': name, 'monthly': c['total'] // n,
                    'pct': int(round(100.0 * c['total'] / living)) if living > 0 else 0,
                    'subs': [{'name': k, 'monthly': v // n} for k, v in subs if v > 0]})
    total = living + loan
    return {'count': count, 'months': n, 'month_list': f['months'], 'first': f['months'][0], 'last': f['months'][-1],
            'monthly': living // n, 'categories': out, 'loan_monthly': loan // n,
            'loan_pct': int(round(100.0 * loan / total)) if total > 0 else 0,
            'loan_dominant': bool(total and loan >= LOAN_SHARE * total),
            'saving_monthly': saving // n, 'remit_monthly': remit // n,
            'spend_monthly': living // n, 'total_monthly': total // n, 'excluded': excluded}


def _subscription(r, k):
    """A subscription charge by the export's sub category ('service
    subscription'), a subscription merchant or an app store; a tiered
    service's plan charges only (its usage is not a subscription)."""
    svc = services().get(k)
    if svc:
        return bool(tier_of(r, svc))
    return SUB_CAT in (r.get('sub') or '') or _has(k, SUBSCRIPTION + APP_STORE)


def fix_category(r):
    """(category, sub) after the obvious fixes, before any total (owner
    rehearsal 2026-10-11 + real-data run): the agent's review word for the
    merchant first; rent the export filed under interest / loan is housing
    (the export's own housing category -- one housing category only);
    subscriptions are their own category, by service name."""
    cat, sub = r.get('cat') or '', r.get('sub') or ''
    k = merchant_key(r['merchant'])
    house = _CATNAME.get('housing') or HOUSING
    if _CATFIX.get(k):
        fixed = _CATFIX[k]
        return (house if fixed == HOUSING else fixed), sub
    if _has(k, RENT) and not _has(k, LOAN) and (LOAN_SUB in sub or cat == BILL_PAIR[0]):
        return house, RENT_SUB
    if r.get('kind', 'expense') == 'expense' and _subscription(r, k):
        svc = services().get(k)
        name = (svc.get('name') if svc else None) or next((d for kk, _, d in MERCHANT_ALIASES if kk == k), None)
        return SUB_CAT, name or display_name(r['merchant'])
    return cat, sub


# ---- recurring detection ------------------------------------------------------

class _UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def _groups(rows, kind):
    """key -> rows, after merging keys that share (method, exact amount) in
    >= 3 months."""
    rows = [r for r in rows if r['kind'] == kind and r['amount'] > 0]
    uf = _UF()
    pair = {}
    for r in rows:
        k = merchant_key(r['merchant'])
        uf.find(k)
        if r['amount'] >= TWIN_AMOUNT_MIN and r['method']:
            pair.setdefault((r['method'], r['amount']), {}).setdefault(k, set()).add(_month(r['date']))
    for keys in pair.values():
        if len(keys) > 1 and len(set().union(*keys.values())) >= 3:
            ks = sorted(keys)
            for k in ks[1:]:
                uf.union(ks[0], k)
    out = {}
    for r in rows:
        out.setdefault(uf.find(merchant_key(r['merchant'])), []).append(r)
    return _renamed(out, uf, rows)


def _shape(grs):
    months = sorted({_month(r['date']) for r in grs})
    return {'months': months, 'last': grs[-1], 'first': grs[0],
            'cpm': len(grs) / float(len(months))}


def _renamed(groups, uf, rows):
    """One item whose bank text changed (owner rehearsal 2026-10-11: "<X>
    rent" became "rent"): a group that stops and a group that starts within
    2 months, one charge a month each, the same account, the same amount
    (+-5 %) and the same day (+-3) continue each other."""
    shapes = {k: _shape(sorted(v, key=lambda r: r['date'])) for k, v in groups.items()}
    keys = sorted(shapes, key=lambda k: shapes[k]['first']['date'])
    merged = False
    for a in keys:
        A = shapes[a]
        if A['cpm'] > 1.2:
            continue
        for b in keys:
            B = shapes[b]
            if a == b or B['cpm'] > 1.2 or uf.find(a) == uf.find(b):
                continue
            if not (A['months'][-1] < B['months'][0] and _months_between(A['months'][-1], B['months'][0]) <= 2):
                continue
            if len(A['months']) + len(B['months']) < 3:
                continue
            x, y = A['last'], B['first']
            if x['method'] != y['method'] or not x['amount'] or abs(x['amount'] - y['amount']) > 0.05 * x['amount']:
                continue
            if abs(int(x['date'][8:10]) - int(y['date'][8:10])) > 3:
                continue
            uf.union(a, b)
            merged = True
    if not merged:
        return groups
    out = {}
    for r in rows:
        out.setdefault(uf.find(merchant_key(r['merchant'])), []).append(r)
    return out


def _group_name(key, grs, conn=None):
    if conn is not None:
        from ledger_names import Names
        fact = Names(conn).fact(max(grs, key=lambda r: r['date'])['merchant'])
        if fact:
            return fact['display_name']
    for k, _, disp in MERCHANT_ALIASES:
        if k == key:
            return disp
    names = {}
    for r in grs:
        dn = display_name(r['merchant'])
        names[dn] = names.get(dn, 0) + 1
    # the shortest variant that every other variant contains (rent vs <landlord> rent)
    keys = {dn: _base(dn) for dn in names}
    for dn in sorted(names, key=lambda d: (len(keys[d]), d)):
        if keys[dn] and all(keys[dn] in keys[o] for o in names):
            return dn
    # else the name it goes by now (the latest charge's)
    return display_name(max(grs, key=lambda r: r['date'])['merchant'])


def fixed_aliases(conn):
    """{fixed id: [merchant keys]} -- the bank texts the owner renamed away
    from (ledger_preference topic ledger_fixed_alias, item fixed:<id>/<key>,
    note = the bank text)."""
    out = {}
    try:
        got = conn.execute("SELECT item, note FROM ledger_preference WHERE topic=? AND status='active'",
                           (ALIAS_TOPIC,)).fetchall()
    except Exception:
        got = []
    for item, note in got:
        m = re.match(r'^fixed:(\d+)(?:/|$)', item or '')
        if m:
            k = merchant_key(note or (item or '').split('/', 1)[-1])
            if k:
                out.setdefault(int(m.group(1)), []).append(k)
    return out


def _registered_fixed(conn):
    out = []
    aliases = fixed_aliases(conn)
    for fid, name, amount, direction, cycle, method in conn.execute(
            "SELECT f.id, f.name, f.amount, f.direction, f.cycle, COALESCE(m.name, '') "
            "FROM fixed_cost f LEFT JOIN pay_method m ON m.id = f.method_id ORDER BY f.id"):
        key = merchant_key(name)
        out.append({'id': fid, 'name': name, 'amount': int(amount or 0), 'direction': direction,
                    'cycle': cycle, 'method': method, 'key': key,
                    'keys': [k for k in [key] + aliases.get(fid, []) if k]})
    return out


def _match_score(cand, f):
    """Lower = better; None = no match. Name key first, then the same
    method with the closest amount (<= 10 %), then (income) the only
    registered income on that method."""
    direction = 'income' if cand['cls'] == 'income' else 'expense'
    if f['direction'] != direction:
        return None
    for fk in f.get('keys') or [f['key']]:
        if fk and (fk == cand['key'] or fk in cand['keys'] or
                   any(len(k) >= 2 and k in fk for k in cand['keys'])):
            return 0.0
    if f['method'] and f['method'] in cand['methods'] and f['amount'] and cand['amount']:
        diff = abs(f['amount'] - cand['amount']) / max(f['amount'], cand['amount'])
        if diff <= 0.1:
            return 1.0 + diff
        if direction == 'income':
            return 3.0 + diff
    return None


def _assign(cands, regs):
    """One registered row per candidate and back, best score first."""
    pairs = []
    for ci, c in enumerate(cands):
        for f in regs:
            sc = _match_score(c, f)
            if sc is not None:
                pairs.append((sc, ci, f['id']))
    done_c, done_f = {}, set()
    for sc, ci, fid in sorted(pairs):
        if ci in done_c or fid in done_f:
            continue
        if sc >= 3.0 and sum(1 for f in regs if f['direction'] == 'income' and
                             f['method'] in cands[ci]['methods']) != 1:
            continue
        done_c[ci] = fid
        done_f.add(fid)
    return done_c


def _nominal(year, month, day):
    last = (datetime.date(year + month // 12, month % 12 + 1, 1) - datetime.timedelta(days=1)).day
    return datetime.date(year, month, min(day, last))


def _off(d):
    try:
        import holiday_source
        hol = holiday_source.is_holiday(d)
    except Exception:
        hol = False
    return d.weekday() >= 5 or hol


def body_end(rows):
    """The end of the dense record body: the latest date with >= BODY_MIN
    records in the 30 days up to it (a stray chat row weeks after an export
    file is not where the records end). Few records at all -> the last date."""
    if not rows:
        return None
    dates = [datetime.date.fromisoformat(r['date']) for r in rows]
    if len(dates) < BODY_MIN:
        return dates[-1].isoformat()
    for i in range(len(dates) - 1, -1, -1):
        lo = dates[i] - datetime.timedelta(days=30)
        if sum(1 for d in dates[:i + 1] if d >= lo) >= BODY_MIN:
            return dates[i].isoformat()
    return dates[-1].isoformat()


def is_dust(amount):
    """True for |amount| < DUST_KRW; the one gate for every owner-facing prompt."""
    return abs(amount or 0) < DUST_KRW


def detect(conn):
    rows, transfers = records(conn)
    end = body_end(rows)
    rows = _window([r for r in rows if end is None or r['date'] <= end])
    # a card bill, a move between own accounts, pay money charged: never a
    # fixed cost (the methods step knows the owner's accounts and cards)
    ns = _spend_filter(conn, transfers)
    rows = [r for r in rows if r['kind'] != 'expense' or ns(r) not in ('own', 'bill', 'charge')]
    # one method, one name: a change is read on the canonical (twin-merged)
    # names only (owner real-data run 2026-10-11: a spurious "moved to
    # <account>" from one account seen under two names)
    rows = [dict(r, method=ns.canon(r['method'])) if r['method'] else r for r in rows]
    recent = ledger_window(conn)
    living = sum(r['amount'] for r in rows if r['kind'] == 'expense' and _month(r['date']) in recent
                 and ns(r) is None and not _is_remit(r) and not _loan_row(r, *fix_category(r)))
    spend_month = living // len(recent) if recent else 0
    regs = _registered_fixed(conn)
    out = []
    svcs = services()
    mkinds = {}
    for m in _registered_methods(conn):
        for n in [m['name']] + m['aliases']:
            mkinds.setdefault(n, m['kind'])

    def kinds(name):
        return mkinds.get(name) or kind_from_name(name)
    frequent = []
    for kind in ('expense', 'income'):
        for key, grs in sorted(_groups(rows, kind).items()):
            svc = svcs.get(key) if kind == 'expense' else None
            if svc:
                # one fixed cost per service: the plan charges only; usage is
                # regular spending (picture), never a fixed candidate
                plan = [r for r in grs if tier_of(r, svc)]
                c = _classify(key, plan, end, kind, kinds, recent) if plan else None
                if c is None:
                    continue
                tagged = [dict(r, tier=tier_of(r, svc), scope=r.get('scope') or 'personal') for r in plan]
                months = _plan_months(tagged)
                # the screen says the recent months' amount and one change
                # line, never a plan / tier guess (owner real-data run
                # 2026-10-11); the tier stays data (month close)
                amount, change = _service_amount(months, recent)
                c.update(name=svc.get('name') or _group_name(key, grs, conn), service=key, tier=months[-1]['tier'],
                         amount=amount, change=change, tier_changes=_changes(tagged, months),
                         usage=sum(r['amount'] for r in grs if not tier_of(r, svc)))
                out.append(c)
                continue
            junk = kind == 'expense' and _HINT.get(key) != 'fixed' and _junk(key, grs, ns)
            c = None if junk else _classify(key, grs, end, kind, kinds, recent)
            if c is None:
                if kind == 'expense':
                    ms = {_month(r['date']) for r in grs}
                    if len(ms) >= 3 and len(grs) >= 2 * len(ms) and ns(grs[-1]) is None and not _is_remit(grs[-1]):
                        frequent.append((-sum(r['amount'] for r in grs), _group_name(key, grs, conn)))
                continue
            c['name'] = _group_name(key, grs, conn)
            if kind == 'expense' and c['cls'] == 'fixed' and ns(grs[-1]) == 'saving':
                c['cls'] = 'saving'             # kept money (an exchange, securities)
            if kind == 'expense' and c['cls'] == 'fixed' and _HINT.get(key) != 'fixed' and \
                    _too_small_or_loose(key, grs, c, spend_month):
                continue
            if kind == 'expense':
                clean = fixed_name(c['name'], grs[-1].get('sub') or '')
                if clean != c['name']:
                    c['raw'], c['name'] = c['name'], clean
            out.append(c)
    fw = flows(conn)
    loan_other = _window_amounts(out, fw, svcs)
    match = _assign(out, regs)
    for ci, c in enumerate(out):
        c['registered'] = match.get(ci)
    used = set(match.values())
    for f in regs:
        if f['id'] not in used:
            out.append({'key': f['key'], 'keys': [f['key']], 'name': f['name'],
                        'cls': 'income' if f['direction'] == 'income' else 'fixed',
                        'cycle': f['cycle'] or 'monthly', 'amount': f['amount'], 'lo': f['amount'],
                        'hi': f['amount'], 'variable': False, 'multi': False, 'day': None, 'month': None,
                        'methods': [f['method']] if f['method'] else [], 'months': 0, 'shift': None,
                        'registered': f['id'], 'seen': False, 'method': f['method'] or None, 'change': None,
                        'transfer': None, 'stopped': False})
    owned = fixed_purposes(conn)
    for c in out:
        c['purpose'] = owned.get(c.get('registered')) or purpose(c)
    order = {'fixed': 0, 'saving': 1, 'income': 2}
    out.sort(key=lambda c: (order[c['cls']], -c['amount'], c['name']))
    for n, c in enumerate(out, 1):
        c['id'] = 'c%d' % n
    # frequent but never the same amount: kept as living costs (named on
    # the fixed screen, owner rehearsal 2026-10-11)
    # THE window the amounts are of; the loan repayment no candidate holds
    # (the wrap counts it as fixed too) -- the fixed screen's total reads
    # these with fixed_monthly(), the same sum the wrap's fixed line is
    return {'end': end, 'candidates': out, 'frequent_variable': [n for _, n in sorted(frequent)[:3]],
            'window_months': fw['months'], 'loan_other': loan_other}


# ---- the window's amount of a fixed cost (F4, pre-cut trial 2026-10-11) -----------
# The fixed screen said ~3 % more than the wrap's fixed line: the screen
# summed each candidate's latest / median / recent-run amount (a month after
# the window included), the wrap the window's rows; a saving candidate was
# in the screen's total, and a tiered service's change line read a month
# outside the window. One rule now: a monthly fixed cost's amount is its
# window rows / the window's months -- the rows the wrap counts -- and its
# change line falls inside the window.

def _fixed_row(r, keys, svcs):
    """A window row counted as a fixed cost: a loan repayment, or a spend
    row of a fixed group's key (a tiered service: its plan charges only --
    usage is living spending). picture() and the fixed amounts read it."""
    if r['cls'] == 'loan':
        return True
    if r['cls'] != 'spend':
        return False
    k = merchant_key(r['merchant'])
    if k not in keys:
        return False
    svc = svcs.get(k)
    return not svc or bool(tier_of(r, svc))


def _monthly_live(c):
    """A candidate charged every month now, seen in the records."""
    return c['cls'] in ('fixed', 'saving') and c.get('seen') and c['cycle'] == 'monthly' and not c.get('stopped')


def fixed_keys(cands):
    """The keys whose window rows are the wrap's fixed cost: registered,
    seen, monthly, still charged (a yearly / stopped one is said apart on
    the fixed screen, so the wrap does not count it either)."""
    out = set()
    for c in cands:
        if c['cls'] == 'fixed' and c.get('registered') and _monthly_live(c):
            out.update(c['keys'])
    return out


def fixed_monthly(cands, loan_other, n):
    """THE monthly fixed cost of the window: the window rows of the monthly
    fixed candidates + the loan repayment none of them holds, / n -- the
    sum and the division picture() makes for its fixed line."""
    if not n:
        return 0
    total = sum(c.get('window_sum') or 0 for c in cands if c['cls'] == 'fixed' and _monthly_live(c))
    return int((total + loan_other) / n)


def _window_change(live, pm, m):
    """One change line inside the window: the window months before /
    from month m (the shown amount is their mean over the window); the
    older figure a range when those months differ."""
    if not m or m not in live or m == live[0]:
        return None
    before = [pm[k] for k in live if k < m]
    after = [pm[k] for k in live if k >= m]
    was, to = int(round(sum(before) / len(before))), int(round(sum(after) / len(after)))
    if _close(was, to, 0.01):
        return None
    ch = {'what': 'amount', 'month': m, 'from': was, 'to': to}
    if not _close(min(before), max(before)):
        ch.update(from_lo=min(before), from_hi=max(before))
    return ch


def _window_amounts(cands, f, svcs):
    """Sets each seen monthly fixed / saving candidate's `amount` to the
    window's monthly amount (`window_sum` / the window's months; per month
    in `window`, lo / hi from them) and its amount change line to the one
    inside the window (a tiered service: read on the window's plan
    amounts). A candidate with no row in the window keeps its amount and
    gets window_sum 0 (the screen's total leaves it out and says so).
    Returns the window's loan repayment no candidate holds."""
    months = f['months']
    n = len(months)
    if not n:
        return 0
    owner = {}
    for c in cands:
        if _monthly_live(c):
            for k in c['keys']:
                owner.setdefault(k, c)
    per, loan_other = {}, 0
    for r in f['rows']:
        if r['kind'] != 'expense':
            continue
        c = owner.get(merchant_key(r['merchant']))
        if r['cls'] == 'loan' and (c is None or c['cls'] != 'fixed'):
            loan_other += r['amount']           # the wrap's fixed line counts every repayment
            continue
        if c is None or (c['cls'] == 'fixed' and not _fixed_row(r, c['keys'], svcs)) or \
                r['cls'] in ('own', 'bill', 'charge'):
            continue
        per.setdefault(id(c), dict.fromkeys(months, 0))[_month(r['date'])] += r['amount']
    for c in cands:
        if not _monthly_live(c):
            continue
        pm = per.get(id(c))
        if not pm:
            c['window_sum'] = 0
            continue
        c['window_sum'] = sum(pm.values())
        c['window'] = [pm[m] for m in months]
        c['amount'] = int(round(c['window_sum'] / n))
        nz = [v for v in pm.values() if v > 0]
        if nz:
            c['lo'], c['hi'] = min(nz), max(nz)
        live = [m for m in months if m >= (c.get('first') or months[0])]
        ch = c.get('change')
        if c.get('service'):
            m = _run_start(live, lambda k: pm[k], lambda x, y: _close(x, y, 0.15)) if len(live) > 1 else None
            c['change'] = _window_change(live, pm, m)
        elif ch and ch['what'] == 'amount':
            c['change'] = _window_change(live, pm, ch['month'])
    return loan_other


# ---- what a fixed cost is not (owner real-data run 2026-10-11) -------------------

def _insurance(key):
    return _has(key, INSURANCE) or bool(INSURANCE_RX.search(key or ''))


def _fixed_word(key):
    """A word that makes a recurring charge a fixed cost by itself."""
    return (_has(key, UTILITY + LOAN + TELECOM + SAVING + RENT + RENTAL + SUBSCRIPTION + MEMBERSHIP + APP_STORE)
            or _insurance(key))


def _junk(key, grs, ns):
    """Never a fixed candidate: a convenience store; a place in an
    everyday-spending category (its monthly repeat is a coincidence); a
    business account's transfer with no category (the owner adds it if it
    is one)."""
    last = grs[-1]
    raw = unicodedata.normalize('NFKC', last['merchant'] or '').strip().casefold()
    if (_has(_norm(last['merchant']), CONVENIENCE) or re.match(r'^cu(\b|\s|$)', raw)
            or _has(last.get('sub') or '', ('\ud3b8\uc758\uc810',))):
        return True
    if _fixed_word(key):
        return False
    cat = fix_category(last)[0]
    if cat in EVERYDAY_CATS:
        return True
    if all(r['method'] and ns.business(r['method']) for r in grs) and cat in ('', UNCLASSIFIED, '\uc774\uccb4'):
        return True
    return False


def _too_small_or_loose(key, grs, c, spend_month):
    """A tiny recurring amount (under ~3,000 won, or under 1 % of a month's
    spending without a fixed-cost word), or a charge with no such word whose
    day wanders (a coincidence, not a debit)."""
    if c.get('stopped'):
        return False
    if c['amount'] < TINY_FIXED:
        return True
    if _fixed_word(key):
        return False
    if spend_month and c['amount'] < TINY_SHARE * spend_month:
        return True
    if c['cycle'] == 'monthly' and not c.get('multi'):
        days = [int(r['date'][8:10]) for r in grs[-6:]]
        mid = statistics.median(days)
        near = sum(1 for d in days if abs(d - mid) <= 3)
        if near * 3 < len(days) * 2:
            return True
    return False


def fixed_name(name, sub=''):
    """A fixed cost's display name without the bank's suffix noise (joint
    payment / auto debit / service), with what it is when the name does not
    say it ('KT internet', '<gas co.> gas bill', '<MVNO> phone')."""
    s = name
    for w in NAME_NOISE:
        s = s.replace(w, ' ')
    s = re.sub(r'\s+', ' ', s).strip(' -') or name
    noun = None
    if sub in NAME_NOUN_SUBS and sub not in s:
        noun = sub
    elif _has(s, GAS_WORDS) and '\uac00\uc2a4' not in s:
        noun = GAS_NOUN
    elif MOBILE_WORD in s and MOBILE_NOUN not in s:
        noun = MOBILE_NOUN
    if noun and s != noun:
        s = s + ' ' + noun
    return s


def _service_amount(months, recent):
    """(the recent full months' monthly plan amount, one change line) of a
    tiered service; averaged from its start when it began inside them."""
    plan = {m['month']: m['plan'] for m in months}
    keys = sorted(plan)
    if recent:
        span = [m for m in recent if m >= keys[0]] or recent
        amount = sum(plan.get(m, 0) for m in span) // len(span)
    else:
        amount = plan[keys[-1]]
    change = None
    # a plan's price moves a little with the exchange rate: a change is a
    # step beyond the band tolerance (subscription_tiers.json, 15 %)
    m = _run_start(keys, lambda k: plan[k], lambda x, y: _close(x, y, 0.15)) if len(keys) > 1 else None
    if m and (not recent or m >= recent[0]):
        before = [k for k in keys if k < m]
        change = {'what': 'amount', 'month': m, 'from': plan[before[-1]], 'to': plan[keys[-1]]}
    return amount or plan[keys[-1]], change


def _run_start(months, value_of, same):
    """The first month of the run that reaches the latest month (values
    `same` as the latest), or None when the run is every month."""
    last = value_of(months[-1])
    start = len(months) - 1
    while start > 0 and same(value_of(months[start - 1]), last):
        start -= 1
    return months[start] if start > 0 else None


def _close(a, b, tol=0.03):
    return bool(a) and bool(b) and abs(a - b) <= tol * max(a, b)


def _change(grs, months, per_month, variable):
    """(the run of the current values, one change line): the current card
    first ("from June on <card>"), else the current amount of a steady
    charge; None when nothing changed."""
    meth = {}
    for r in grs:
        meth.setdefault(_month(r['date']), set()).add(r['method'])
    cur = grs[-1]['method']
    m = _run_start(months, lambda k: meth[k], lambda x, y: x == y) if cur else None
    if m and len(meth[months[-1]]) == 1:
        prev = next(r['method'] for r in reversed(grs) if _month(r['date']) < m)
        if prev and prev != cur:
            return {'what': 'method', 'month': m, 'from': prev, 'to': cur}
    if variable:
        return None
    m = _run_start(months, lambda k: per_month[k], _close)
    if m:
        before = [per_month[k] for k in months if k < m]
        if len([k for k in months if k >= m]) >= 2 and max(before) <= 1.03 * min(before):
            return {'what': 'amount', 'month': m, 'from': before[-1], 'to': per_month[months[-1]]}
    return None


def _segments_steady(months, per_month, change):
    """A charge that changed its amount once is steady when both sides are."""
    if not change or change['what'] != 'amount':
        return False
    before = [per_month[k] for k in months if k < change['month']]
    after = [per_month[k] for k in months if k >= change['month']]
    return all(len(x) >= 1 and max(x) <= 1.05 * min(x) for x in (before, after)) and len(before) + len(after) >= 3


def _transfer(grs, kinds):
    """auto | manual | None (inferred; owner rehearsal 2026-10-11): an
    auto-debit word, or a card charge -> auto; otherwise the time of day
    (an auto debit lands at the same minute) and the day drift beyond a
    weekend / holiday shift (a hand transfer wanders)."""
    words = _norm(' '.join(r['merchant'] for r in grs))
    if _has(words, AUTO_WORDS):
        return 'auto'
    if kinds(grs[-1]['method']) in ('credit_card', 'corporate_card', 'debit_card', 'easy_pay'):
        return 'auto'
    recent = grs[-6:]
    day = int(statistics.median(int(r['date'][8:10]) for r in recent))
    drift = 0
    for r in recent:
        d = datetime.date.fromisoformat(r['date'])
        nom = _nominal(d.year, d.month, day)
        if abs((d - nom).days) > 3 or (d != nom and not _off(nom) and abs((d - nom).days) > 1):
            drift += 1
    mins = []
    for r in recent:
        t = re.match(r'^(\d{1,2}):(\d{2})', r.get('time') or '')
        if t:
            mins.append(int(t.group(1)) * 60 + int(t.group(2)))
    if len(mins) >= 3:
        spread = max(mins) - min(mins)
        # the same minute, or the same hour every month (a loan debit whose
        # amount changed once is still pulled by the bank)
        if (spread <= 15 and not drift) or (len({m // 60 for m in mins}) == 1 and drift <= 1):
            return 'auto'
        if spread >= 120 or drift >= 2:
            return 'manual'
        return None
    return 'manual' if drift >= 2 else None


STOPPED_MONTHS = 6


def _latest_run(months, per_month, recent):
    """A loan / insurance / telecom / saving charge (a variable word): the
    recent constant amount, and the change into it when it fell in the
    window (the older amounts as a range) -> (amount, change) or None when
    the latest two months differ."""
    same = lambda x, y: _close(x, y, 0.01)
    if len(months) < 2 or not same(per_month[months[-1]], per_month[months[-2]]):
        return None
    m = _run_start(months, lambda k: per_month[k], same)
    cur = per_month[months[-1]]
    if not m or (recent and m < recent[0]):
        return cur, None
    before = [per_month[k] for k in months if k < m][-3:]
    return cur, {'what': 'amount', 'month': m, 'from': before[-1], 'to': cur,
                 'from_lo': min(before), 'from_hi': max(before)}


def _payday(pays):
    """(nominal day, shift, evidence, schedule change) from the regular
    payments (oldest first). Nominal: the latest payments' day or the
    month's end; shift: prev | next | same, only from payments whose
    nominal day was a weekend / public holiday (evidence = [[nominal,
    paid]]), None without evidence or when it disagrees; change: the
    nominal day the earlier payments kept (>= 2 of them), from the month
    the current one began."""
    dates = [datetime.date.fromisoformat(r['date']) for r in pays]

    def nominal(d, day):
        last = (datetime.date(d.year + d.month // 12, d.month % 12 + 1, 1) - datetime.timedelta(days=1)).day
        return datetime.date(d.year, d.month, last if day == 'end' else min(day, last))

    def fit(d, day):
        nom = nominal(d, day)
        if d == nom:
            return 'same' if _off(nom) else 'exact'
        if not _off(nom) or abs((d - nom).days) > 6:
            return None
        step = -1 if d < nom else 1
        x = nom + datetime.timedelta(days=step)
        while _off(x):
            x += datetime.timedelta(days=step)
        return ('prev' if step < 0 else 'next') if x == d else None

    def pick(ds):
        day = max(d.day for d in ds[-3:])
        if day >= 28 and all(fit(d, 'end') for d in ds[-3:]):
            return 'end'
        return day
    day = pick(dates)
    k = len(dates)
    while k > 0 and fit(dates[k - 1], day):
        k -= 1
    run, early = dates[k:] or dates[-1:], dates[:k]
    change = None
    if len(early) >= 2 and run:
        old = pick(early)
        if old != day and all(fit(d, old) for d in early[-2:]):
            change = {'month': run[0].isoformat()[:7], 'from': old, 'to': day}
    ev, hows = [], set()
    for d in run:
        how = fit(d, day)
        if how in ('prev', 'next', 'same'):
            ev.append([nominal(d, day).isoformat(), d.isoformat()])
            hows.add(how)
    shift = hows.pop() if len(hows) == 1 else None
    return (max(d.day for d in run[-3:]) if day == 'end' else day), shift, (ev if shift else []), change


def _classify(key, grs, end, kind, kinds=lambda name: None, recent=()):
    months = sorted({_month(r['date']) for r in grs})
    per_month = {}
    for r in grs:
        per_month[_month(r['date'])] = per_month.get(_month(r['date']), 0) + r['amount']
    n = len(months)
    span = _months_between(months[0], months[-1]) + 1
    coverage = n / span
    cpm = len(grs) / n
    last = grs[-1]['date']
    gap_days = (datetime.date.fromisoformat(end) - datetime.date.fromisoformat(last)).days if end else None
    active = end is not None and gap_days <= ACTIVE_DAYS
    sums = [per_month[m] for m in months]
    lo, hi = min(sums), max(sums)
    stable = lo > 0 and hi / lo <= 1.3
    methods = []
    for r in grs:
        if r['method'] and r['method'] not in methods:
            methods.append(r['method'])
    if grs[-1]['method'] in methods:
        # the current card / account last (the line shows the current one)
        methods.remove(grs[-1]['method'])
        methods.append(grs[-1]['method'])
    days = [int(r['date'][8:10]) for r in grs]
    base = {'key': key, 'keys': sorted({merchant_key(r['merchant']) for r in grs}), 'methods': methods,
            'months': n, 'lo': lo, 'hi': hi, 'seen': True, 'shift': None, 'month': None,
            'method': grs[-1]['method'] or None, 'change': None, 'transfer': None, 'stopped': False,
            'first': months[0], 'last': months[-1]}
    if kind == 'income':
        if max(r['amount'] for r in grs) < INCOME_MIN or n < 3 or not active:
            return None
        # the regular payment = the month's largest; a one-off small credit
        # under the same name is shown apart, never averaged in (owner
        # real-data run 2026-10-11)
        mains = {}
        for r in grs:
            m = _month(r['date'])
            if r['amount'] >= INCOME_MIN and (m not in mains or r['amount'] > mains[m]['amount']):
                mains[m] = r
        pays = [mains[m] for m in sorted(mains)]
        if len(pays) < 3:
            return None
        main_ids = {id(r) for r in pays}
        last3 = [r['amount'] for r in pays[-3:]]
        amount = pays[-1]['amount']             # what comes in now: the latest regular payment
        day, shift, evidence, sched = _payday(pays)
        span = set(recent or months[-3:])
        extra = [{'date': r['date'], 'amount': r['amount']} for r in grs
                 if id(r) not in main_ids and _month(r['date']) in span and r['amount'] < amount // 2]
        base.update({'cls': 'income', 'cycle': 'monthly', 'amount': amount,
                     'variable': not (min(last3) > 0 and max(last3) / min(last3) <= 1.3),
                     'multi': False, 'day': day, 'shift': shift, 'shift_evidence': evidence,
                     'schedule_change': sched, 'extra': extra, 'lo': min(last3), 'hi': max(last3)})
        return base
    hint = _HINT.get(key)
    if hint == 'variable':
        return None                             # the agent's review: spending that varies
    variable_word = _has(key, UTILITY + LOAN + TELECOM + SAVING) or _insurance(key)
    sub = _has(key, SUBSCRIPTION) or _has(key, MEMBERSHIP)
    saving = _has(key, SAVING)
    fixed_word = variable_word or sub or saving
    change = _change(grs, months, per_month, variable_word)
    steady = stable or _segments_steady(months, per_month, change)
    # a fixed cost is one charge a month; several a month only for a
    # subscription merchant or a utility / loan / insurance / telecom word
    shape = n >= 3 and (
        (steady and coverage >= 0.7 and cpm <= 1.2) or
        (variable_word and coverage >= 0.5 and cpm <= 3) or
        (_has(key, SUBSCRIPTION) and coverage >= 0.7 and cpm <= 3) or
        (hint == 'fixed' and cpm <= 3))
    monthly = active and shape
    pair = (active and n == 2 and _months_between(months[0], months[1]) == 1 and (fixed_word or hint == 'fixed')
            and cpm == 1 and abs(sums[0] - sums[1]) <= max(sums) // 10)
    if monthly or pair:
        last3 = months[-3:]
        if steady and not variable_word:
            amount = per_month[months[-1]]      # the current amount
        else:
            amount = int(statistics.median([per_month[m] for m in last3]))
        run = _latest_run(months, per_month, recent) if variable_word and not _has(key, UTILITY) \
            and not change and cpm <= 1.2 else None
        if run:
            # a loan / insurance at its recent constant amount; the older
            # amounts only when it changed in the window (real-data run)
            amount, change = run
            steady = True
        recent_days = days[-3:]
        base.update({'cls': 'saving' if saving else 'fixed', 'cycle': 'monthly', 'amount': amount,
                     'variable': not steady, 'multi': cpm > 1.2,
                     'day': None if cpm > 1.2 or not steady else int(statistics.median(recent_days)),
                     'change': change, 'transfer': _transfer(grs, kinds) if cpm <= 1.2 else None})
        return base
    if shape and not active and gap_days is not None and gap_days <= STOPPED_MONTHS * 31:
        # a steady charge that stopped: asked once on the fixed screen
        base.update({'cls': 'saving' if saving else 'fixed', 'cycle': 'monthly', 'amount': per_month[months[-1]],
                     'variable': not steady, 'multi': cpm > 1.2, 'day': None, 'stopped': True})
        return base
    big = max(r['amount'] for r in grs)
    # one charge in the file's last weeks: a new subscription or a one-off --
    # the cadence is not guessed; it is asked when it recurs
    if n == 1 and big >= 10000 and sub and not active:
        base.update({'cls': 'fixed', 'cycle': 'yearly', 'amount': big, 'variable': False, 'multi': False,
                     'day': None, 'month': int(months[0][5:7]), 'lo': big, 'hi': big})
        return base
    if n == 2 and 11 <= _months_between(months[0], months[1]) <= 13 and cpm == 1 and \
            abs(sums[0] - sums[1]) <= max(sums) // 10:
        base.update({'cls': 'fixed', 'cycle': 'yearly', 'amount': sums[-1], 'variable': False, 'multi': False,
                     'day': None, 'month': int(months[-1][5:7])})
        return base
    return None


SUMMARY_MAX = 100


def recurring_summary(conn):
    """The compact aggregate the agent reviews in its live turn (ledger-setup
    SKILL; no model call from the kit): one line per merchant key seen in
    >= 2 months, the most months first -- name, months seen, charge amount
    range, category (after the fixes), the current method, what the rules
    made of it. The agent answers name merges, category fixes and fixed vs
    variable (`onboarding review-set`)."""
    rows, _ = records(conn)
    end = body_end(rows)
    rows = _window([r for r in rows if end is None or r['date'] <= end])
    det = {}
    for c in detect(conn)['candidates']:
        if c.get('seen'):
            for k in c['keys']:
                det[k] = 'stopped' if c.get('stopped') else c['cls']
    by = {}
    for r in rows:
        if r['kind'] == 'expense' and r['amount'] > 0:
            by.setdefault(merchant_key(r['merchant']), []).append(r)
    lines = []
    for key, grs in by.items():
        months = sorted({_month(r['date']) for r in grs})
        if len(months) < 2:
            continue
        cats = {}
        for r in grs:
            c = ' / '.join(x for x in fix_category(r) if x)
            cats[c] = cats.get(c, 0) + 1
        amts = [r['amount'] for r in grs]
        lines.append({'key': key, 'name': _group_name(key, grs, conn), 'months': len(months), 'charges': len(grs),
                      'lo': min(amts), 'hi': max(amts), 'category': max(cats.items(), key=lambda kv: (kv[1], kv[0]))[0],
                      'method': grs[-1]['method'], 'rule': det.get(key, 'variable'),
                      'total': sum(amts)})
    lines.sort(key=lambda x: (-x['months'], -x['total'], x['name']))
    lines = lines[:SUMMARY_MAX]
    for x in lines:
        x['text'] = '%s | %d mo | %s-%s | %s | %s | %s' % (x['name'], x['months'], format(x['lo'], ','),
                                                         format(x['hi'], ','), x['category'] or '-',
                                                         x['method'] or '-', x['rule'])
    return {'count': len(lines), 'end': end, 'lines': lines}


PURPOSES = ('housing', 'loan', 'insurance', 'utility', 'subscription', 'saving', 'other')
PENSION = ('\uc5f0\uae08',)
PURPOSE_TOPIC = 'ledger_fixed_purpose'


def purpose(c):
    """The fixed screen's group, by the item's own words only (owner
    rehearsal 2026-10-11: never by guess -- rent and a loan stay apart
    unless the owner says the loan is for housing)."""
    if c.get('cls') == 'income':
        return None
    ks = ' '.join(c.get('keys') or [c.get('key') or ''])
    if HOUSING in (_CATFIX.get(c.get('key')) or '') or (_has(ks, RENT) and not _has(ks, LOAN)):
        return 'housing'
    if _has(ks, LOAN):
        return 'loan'
    if _has(ks, INSURANCE + PENSION) or any(_insurance(k) for k in (c.get('keys') or [c.get('key') or ''])):
        return 'insurance'
    if _has(ks, UTILITY + TELECOM):
        return 'utility'
    if c.get('service') or _has(ks, SUBSCRIPTION + MEMBERSHIP):
        return 'subscription'
    if c.get('cls') == 'saving':
        return 'saving'
    return 'other'


def fixed_purposes(conn):
    """{fixed id: purpose} the owner said ("that loan is for the house")."""
    out = {}
    for item, note in _pref_rows(conn, PURPOSE_TOPIC):
        m = re.match(r'^fixed:(\d+)$', item or '')
        if m and note in PURPOSES:
            out[int(m.group(1))] = note
    return out


# ---- the monthly picture --------------------------------------------------------

def _picture_months(rows, n=PICTURE_MONTHS):
    """Up to n full calendar months with records, before the month of the
    last record (that month counts when the last record is its last day),
    within PICTURE_LOOKBACK months; a month whose records stop before its
    last FULL_TAIL_DAYS days is partial and skipped. -> [(YYYY-MM, rows)]
    oldest first."""
    if not rows:
        return []
    end = datetime.date.fromisoformat(rows[-1]['date'])
    nxt = end + datetime.timedelta(days=1)
    cur = _month(nxt.isoformat()) if nxt.day == 1 else _month(end.isoformat())
    by = {}
    for r in rows:
        by.setdefault(_month(r['date']), []).append(r)
    out = []
    y, m = int(cur[:4]), int(cur[5:7])
    for _ in range(PICTURE_LOOKBACK):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        key = '%04d-%02d' % (y, m)
        last_day = (datetime.date(y + m // 12, m % 12 + 1, 1) - datetime.timedelta(days=1)).day
        # a month counts only when its records reach its last 10 days (the
        # month an export file stops in is a partial month, not a low one)
        if by.get(key) and max(int(r['date'][8:10]) for r in by[key]) >= last_day - FULL_TAIL_DAYS:
            out.append((key, by[key]))
            if len(out) == n:
                break
    return list(reversed(out))


def _floor(conn):
    try:
        row = conn.execute("SELECT value FROM ledger_guard WHERE guard_key='irregular_floor' "
                           "AND scope='global'").fetchone()
    except Exception:
        row = None
    return int(row[0]) if row and row[0] is not None else IRREGULAR_FLOOR


def picture(conn, months=PICTURE_MONTHS):
    """The month picture over THE window (flows(): the habits screen's
    numbers, owner real-data run 2026-10-11): fixed = registered fixed
    costs + loan repayment; living (regular) / big (irregular) = the rest
    of the spending; saving = money kept; remit = money sent to a person.
    living spending on the habits screen = fixed - loan + regular +
    irregular (the same rows)."""
    f = flows(conn, months)
    rows = f['all']
    svcs = services()
    det = detect(conn)
    keys = fixed_keys(det['candidates'])
    floor = _floor(conn)
    usual = {}
    for r in _window(rows):
        if r['kind'] == 'expense' and r['amount'] > 0:
            usual.setdefault(merchant_key(r['merchant']), []).append(r['amount'])
    med = {k: statistics.median(v) for k, v in usual.items()}
    per, top, src, sav = [], {}, {}, {}
    by = {}
    for r in f['rows']:
        by.setdefault(_month(r['date']), []).append(r)
    for month in f['months']:
        moved = [ev for ev in f['moved'] if _month(ev['date']) == month]
        e = {'month': month, 'income': 0, 'fixed': 0, 'loan': 0, 'saving': 0, 'regular': 0, 'irregular': 0,
             'usage': 0, 'remit': 0, 'from_savings': sum(ev['amount'] for ev in moved)}
        for ev in moved:
            src[ev['source']] = src.get(ev['source'], 0) + ev['amount']
        for ev in f['kept']:
            if _month(ev['date']) == month:
                # money moved into securities / savings / an exchange: kept, a saving
                e['saving'] += ev['amount']
                sav[ev['target']] = sav.get(ev['target'], 0) + ev['amount']
        for r in by.get(month, []):
            cls = r['cls']
            if cls == 'income':
                e['income'] += r['amount']
                continue
            if cls in ('own', 'bill', 'charge'):
                continue
            if cls == 'saving':
                e['saving'] += r['amount']
                dn = display_name(r['merchant'])
                sav[dn] = sav.get(dn, 0) + r['amount']
                continue
            if cls == 'remit':
                e['remit'] += r['amount']
                continue
            if cls == 'loan':
                e['fixed'] += r['amount']
                e['loan'] += r['amount']
                continue
            k = merchant_key(r['merchant'])
            svc = svcs.get(k)
            if svc and not tier_of(r, svc):
                # a tiered service's usage (API) is regular spending, by name
                e['regular'] += r['amount']
                e['usage'] += r['amount']
                name = svc.get('usage_name') or display_name(r['merchant'])
                top[name] = top.get(name, 0) + r['amount']
                continue
            if _fixed_row(r, keys, svcs):
                e['fixed'] += r['amount']
            elif r['amount'] >= max(floor, 3 * med.get(k, 0)):
                e['irregular'] += r['amount']
            else:
                e['regular'] += r['amount']
                dn = (svc.get('name') if svc else None) or display_name(r['merchant'])
                top[dn] = top.get(dn, 0) + r['amount']
        e['out'] = e['fixed'] + e['regular'] + e['irregular'] + e['remit']
        e['surplus'] = e['income'] - e['out'] - e['saving']
        per.append(e)
    n = len(per)
    avg = {}
    for k in ('income', 'fixed', 'loan', 'saving', 'regular', 'irregular', 'remit', 'out', 'surplus', 'usage',
              'from_savings'):
        avg[k] = int(sum(e[k] for e in per) / n) if n else 0
    # the deficit split (owner 2026-10-10 19:38): what own money moved in
    # covered, and the remainder nothing in the records explains
    deficit = max(0, -avg['surplus'])
    avg['covered'] = min(deficit, avg['from_savings'])
    avg['unexplained'] = deficit - avg['covered']
    tops = [{'name': k, 'monthly': int(v / n)} for k, v in sorted(top.items(), key=lambda kv: -kv[1])[:3]] if n else []
    return {'months': [e['month'] for e in per], 'per_month': per, 'avg': avg, 'top_regular': tops,
            'irregular_floor': floor, 'records': bool(rows),
            'savings_sources': [k for k, _ in sorted(src.items(), key=lambda kv: -kv[1])],
            # what the saving line names ("Savings: 30,000 a month (pension)"),
            # owner review 2026-10-10 20:56
            'saving_names': [k for k, _ in sorted(sav.items(), key=lambda kv: -kv[1])][:2]}


# ---- CLI -------------------------------------------------------------------------

def cli(K, verb, argv):
    argv, _ = K._pop_flag(list(argv), '--json')
    argv, months = K._pop_opt(argv, '--months')
    argv, month = K._pop_opt(argv, '--month')
    if argv or (months is not None and verb != 'flow-picture') or (month is not None and verb != 'sub-tiers'):
        K._err('usage: kit_life.sh %s [--json]%s' % (verb, ' [--months N]' if verb == 'flow-picture' else
                                                    ' [--month YYYY-MM]' if verb == 'sub-tiers' else ''))
    if month is not None and not re.match(r'^\d{4}-\d{2}$', str(month)):
        K._err('sub-tiers: --month must be YYYY-MM')
    conn = K.get_conn()
    try:
        if verb == 'ledger-records':
            out = info(conn)
        elif verb == 'methods-seen':
            out = methods_seen(conn)
        elif verb == 'fixed-detect':
            out = detect(conn)
        elif verb == 'sub-tiers':
            out = sub_tiers(conn, month)
        elif verb == 'spend-habits':
            out = spend_habits(conn)
        elif verb == 'recurring-summary':
            out = recurring_summary(conn)
        else:
            n = PICTURE_MONTHS
            if months is not None:
                if not str(months).isdigit() or not 1 <= int(months) <= 12:
                    K._err('flow-picture: --months must be 1..12')
                n = int(months)
            out = picture(conn, n)
        import ledger_names
        out = ledger_names.present(conn, out)
    finally:
        conn.close()
    print(json.dumps(out, ensure_ascii=False, sort_keys=True))
