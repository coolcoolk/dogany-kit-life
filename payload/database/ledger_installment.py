"""ledger_installment.py -- card installments (halbu) as 0% debt lines on the
card's entity (kit_life 2.46.0).

Owner 2026-10-10 11:57: per purchase the ledger knows the total, the months,
which month we are in, the monthly charge, the remaining balance and the end
month; the next card bill estimate includes the installment charges.
Knowledge-warehouse rule: an interest-free installment is a debt line at 0%
(balance, rate, monthly payment); the monthly payment is a minimum payment.

STORAGE -- no migration, the ladder measured (2026-10-10):
  fixed_cost  no end / total / months column, and auto_entry would write a
              monthly spend row beside the purchase the ledger already holds
              at full amount (ledger-log rule: one row, purchase date) -- a
              double count. Not a fit.
  block_delta realized money on a day; an installment charge is not a spend
              event (the purchase is). Not a fit.
  entity +    fits: one entity per purchase (ulid x-ent-inst-<digest>, origin
  measure     module), owner_entity_id = the card's entity (the 039 rule, the
              one ledger_balance reads); its name in label namespace
              'installment' (NOT 'entity': the 039 resolution must never see
              a purchase as an instrument). The terms are measure rows on it,
              basis actual, observed_at = the purchase date:
                installment_total_krw  krw    qualifier first:YYYY-MM[;spend:N]
                installment_months     count
                installment_rate_pct   pct    0 = interest-free (always written)
              source = hand | spend:N | intake:N (the candidate it came from).
              Closing (done / cancel): measure installment_closed (count = the
              months charged, qualifier done|cancel, observed_at = the day)
              and entity.status 'gone'. A declined candidate: measure
              installment_declined on the CARD entity, qualifier = its ref,
              seq = the ref encoded (spend 2n, intake 2n+1) so two never
              collide on a day.
  Metrics are registered by this module on first write (design B-3).

ASSUMPTIONS (stated, also in the --say note):
  rounding   Korean card practice: principal = total // months each month,
             the remainder on the FIRST month (won units).
  fee        a --rate installment: fee_k = remaining principal before month k
             x rate / 100 / 12, floored to the won (issuers compute daily; this
             is the estimate the bill corrects).
  first      the first charge's bill month. Default: the month after the
             purchase month (--first overrides). Observed billed parts use
             the month of their earliest uniquely matched charge instead.
  remaining  principal still owed AFTER this month's charge.
  close      done (finished / paid off): charges through the close month,
             none after. cancel (purchase cancelled): none from the close
             month on.

DETECTION (proposals only, never a write):
  spend_entry  note / name '<halbu> N<gaewol>' (the ledger-log installment
               note) on a credit card
  intake SMS   data installment_months >= 2 (issuer templates, group
               'installment' in issuers/*.json)
  Banksalad    the export carries NO installment column (headers date, time,
               type, category, sub-category, content, amount, currency,
               method, memo); 'N<gaewol>' in content / memo is the only hint
Each candidate: ref spend:N | intake:N, the card, amount, months, date. An
SMS item already landed on a spend row is proposed once, under the spend.

VERBS
  installment-add <card> <total> <months> [--first YYYY-MM] [--rate PCT]
        [--name N] [--date YYYY-MM-DD] [--spend ID] [--new] [--json]
        the same open card / total / months / day is refused unless --new
  installment-list [--month YYYY-MM] [--all] [--say] [--json]
  installment-done <ref> [--date D] [--json]
  installment-cancel <ref> [--date D] [--json]
  installment-detect [--say] [--json]
  installment-confirm <cand-ref> [--months N] [--first YYYY-MM] [--rate PCT]
        [--name N] [--json]
  installment-dismiss <cand-ref> [--json]
<card> resolves like ledger_balance (id | name | alias | entity token/label),
credit cards only. <ref> = installment id ('#12' or '12') or its exact name.

EXIT CODES: 0 ok; 1 usage / refused (stderr, nothing written).
"""
import datetime
import hashlib
import json
import os
import re
import sqlite3

import ledger_balance as LB

VERBS = ('installment-upd', 'installment-reopen', 'installment-add', 'installment-list', 'installment-done',
         'installment-cancel', 'installment-detect', 'installment-confirm',
         'installment-dismiss')

M_TOTAL = 'installment_total_krw'
M_MONTHS = 'installment_months'
M_RATE = 'installment_rate_pct'
M_CLOSED = 'installment_closed'
M_REPLACED = 'installment_replaced_by'
M_DECLINED = 'installment_declined'
METRICS = ((M_TOTAL, 'krw'), (M_MONTHS, 'count'), (M_RATE, 'pct'),
           (M_CLOSED, 'count'), (M_DECLINED, 'count'))
ULID_PREFIX = 'x-ent-inst-'
LABEL_NS = 'installment'
MONTHS_MAX = 60
RATE_MAX = 30.0
LINK_WINDOW_DAYS = 3
# '\ud560\ubd80' halbu, '\uac1c\uc6d4' gaewol: escaped, the source stays ASCII
_NOTE_RE = re.compile(u'\ud560\ubd80\\s*(\\d{1,2})\\s*\uac1c\uc6d4|'
                      u'(\\d{1,2})\\s*\uac1c\uc6d4\\s*\ud560\ubd80')
_HINT_RE = re.compile(u'(?<!\\d)(\\d{1,2})\\s*\uac1c\uc6d4')
CSV_CONTENT, CSV_MEMO, CSV_METHOD = u'\ub0b4\uc6a9', u'\uba54\ubaa8', \
    u'\uacb0\uc81c\uc218\ub2e8'

Refused = LB.Refused


class RegistrationRefused(Refused, ValueError):
    """Handled by both the installment CLI and the ledger review entrypoint."""


# -- months -------------------------------------------------------------------
def _midx(month):
    return int(month[:4]) * 12 + int(month[5:7]) - 1


def _mstr(idx):
    return '%04d-%02d' % (idx // 12, idx % 12 + 1)


def _month(raw, what):
    if raw is None:
        return None
    if not LB._MONTH_RE.match(str(raw)) or not 1 <= int(raw[5:7]) <= 12:
        raise Refused('%s must be YYYY-MM: %r' % (what, raw))
    return raw


def schedule(total, months, rate=0.0):
    """[{k, principal, fee, charge, remaining}] -- remaining = principal
    still owed after month k. Remainder on the first month."""
    base = total // months
    out, left = [], total
    for k in range(1, months + 1):
        p = total - base * (months - 1) if k == 1 else base
        fee = int(left * float(rate) / 100.0 / 12.0)
        left -= p
        out.append({'k': k, 'principal': p, 'fee': fee, 'charge': p + fee,
                    'remaining': left})
    return out


# -- storage ------------------------------------------------------------------
def ensure_metrics(conn):
    for tok, unit in METRICS:
        conn.execute(
            "INSERT OR IGNORE INTO metric_def(token, scope_kind, accrual, "
            "unit_token, owner_module, origin) "
            "VALUES(?, 'entity', 'observed', ?, 'ledger', 'module');",
            (tok, unit))


def _put(conn, eid, metric, value, unit, day, source, qual=None, seq=None):
    conn.execute(
        "INSERT INTO measure(metric, value, unit, qualifier, basis, entity_id, "
        "seq, observed_at, source, value_origin) "
        "VALUES(?,?,?,?,'actual',?,?,?,?,'declared');",
        (metric, float(value), unit, qual, eid, seq, day, source))


def _quals(q):
    out = {}
    for part in (q or '').split(';'):
        if ':' in part:
            k, v = part.split(':', 1)
            out[k] = v
    return out


def _card(conn, ref):
    m = LB.resolve(conn, ref)
    if m['kind'] not in LB.BILL_KINDS:
        raise Refused('%s is not a credit card (%s): an installment rides a '
                      'credit card' % (m['name'], m['kind']))
    return m


def _block_of_spend(conn, sid):
    r = conn.execute(
        "SELECT b.id, substr(COALESCE(b.date, b.start_at),1,10), d.amount, "
        "d.entity_id FROM block b JOIN block_delta d ON d.block_id=b.id AND "
        "d.resource='money' WHERE b.ulid=?;", ('x-spend-%d' % sid,)).fetchone()
    return r


def _auto_link(conn, card_eid, total, day, taken):
    """The one spend row on this card of exactly -total within +-3 days of
    the purchase day, not already linked; else None (never a guess)."""
    lo = (day - datetime.timedelta(days=LINK_WINDOW_DAYS)).isoformat()
    hi = (day + datetime.timedelta(days=LINK_WINDOW_DAYS)).isoformat()
    rows = conn.execute(
        "SELECT b.ulid FROM block_delta d JOIN block b ON b.id=d.block_id "
        "WHERE d.resource='money' AND d.entity_id=? AND d.amount=? AND "
        "substr(COALESCE(b.date, b.start_at),1,10) BETWEEN ? AND ? "
        "AND b.ulid LIKE 'x-spend-%';",
        (card_eid, -float(total), lo, hi)).fetchall()
    sids = [int(r[0][8:]) for r in rows if r[0][8:].isdigit()]
    sids = [s for s in sids if s not in taken]
    return sids[0] if len(sids) == 1 else None


def _billed_rows(conn, card, total, months, day, spend_id=None):
    """Unique monthly debits around a purchase, not a full-total spend row.

    The first debit may omit the rounding remainder retained in the declared
    total. Never use a merchant name alone to establish a billed schedule.
    """
    base = total // months
    rows = conn.execute(
        "SELECT id,date,amount,name FROM spend_entry WHERE method=? "
        "AND kind='expense' AND COALESCE(class,'spend')='spend' "
        "AND amount BETWEEN ? AND ? ORDER BY date,id",
        (card['name'], base, base + total % months)).fetchall()
    anchors = [r for r in rows if r[0] == spend_id] if spend_id else [
        r for r in rows if r[1] == day.isoformat()]
    if len(anchors) != 1:
        return []
    anchor = anchors[0]
    same = [r for r in rows if r[3] == anchor[3] and r[1][8:] == anchor[1][8:]]
    by_month = {}
    for row in same:
        by_month.setdefault(_midx(row[1][:7]), []).append(row)
    start = _midx(anchor[1][:7])
    while len(by_month.get(start - 1, [])) == 1:
        start -= 1
    out = []
    for index in range(months):
        matches = by_month.get(start + index, [])
        if len(matches) != 1:
            break
        out.append(matches[0])
    if anchor not in out or len(by_month.get(start + months, [])) == 1:
        return []
    # Without an explicit row link, require a repeated debit as evidence.
    return out if spend_id or len(out) > 1 else []


def _covered_installment(conn, existing, card, total, day, spend_id):
    from ledger_review import annotations
    covered = annotations(conn).get(spend_id, {}).get('billed_installment_id')
    for old in existing:
        if old['closed'] or old.get('superseded_by') or old['method_id'] != card['method_id']:
            continue
        if spend_id and (old['spend_id'] == spend_id or old['id'] == covered):
            return old
        month = day.isoformat()[:7]
        if old['purchase_date'][:7] <= month <= old['end'] and total in {
                row['charge'] for row in schedule(old['total'], old['months'], old['rate'])}:
            return old
    for bill in _remaining_bill_rows(conn, day.isoformat()[:7]):
        if bill['method'] == card['name'] and ((spend_id is not None and spend_id in (-bill['id'], bill.get('latest_source_id')))
                                             or total == bill['monthly']):
            return bill
    return None


def billed_groups(conn):
    """Monthly card debits are tracked without inventing purchase terms."""
    groups = {}
    for sid, date, amount, name, method in conn.execute(
            "SELECT id,date,amount,name,method FROM spend_entry WHERE kind='expense' "
            "AND COALESCE(class,'spend')='spend' AND scope<>'business' AND amount>=100000 ORDER BY date,id"):
        groups.setdefault((method, name, amount), {}).setdefault(
            _midx(date[:7]), []).append(dict(id=sid, date=date, amount=amount, name=name, method=method))
    out = []
    for (method, name, amount), months in groups.items():
        if not _card_quiet(conn, method):
            continue
        run = []
        for month in sorted(months):
            if len(months[month]) != 1 or run and month != _midx(run[-1]['date'][:7])+1:
                if len(run)>1: out.append(run)
                run = []
            if len(months[month]) == 1: run.extend(months[month])
        if len(run)>1: out.append(run)
    return out


def group_schedules(conn, rows):
    """Match retained source IDs, or unique dated billed evidence after reimport."""
    card = _card(conn, rows[0]['method'])
    ids = {r['id'] for r in rows}
    dates = {r['date'] for r in rows}
    amount = rows[0]['amount']
    matches = []
    for it in items(conn):
        if it['closed'] or it.get('superseded_by') or it['method_id'] != card['method_id']:
            continue
        orphan = it['spend_id'] and not conn.execute('SELECT 1 FROM spend_entry WHERE id=?', (it['spend_id'],)).fetchone()
        if it['spend_id'] in ids or (orphan and it['purchase_date'] in dates
                and conn.execute("SELECT count(*) FROM spend_entry WHERE date=? AND method=? AND amount=? AND kind='expense' AND COALESCE(class,'spend')='spend'", (it['purchase_date'], rows[0]['method'], amount)).fetchone()[0] == 1
                and (it['total'] == amount or it['total']//it['months'] == amount)):
            matches.append(it)
    from ledger_plan import facts
    for key, it in facts(conn).items():
        if (key.startswith('installment:') and it.get('kind') == 'remaining_installment'
                and it['method'] == rows[0]['method'] and it.get('source_name', it['name']) == rows[0]['name']
                and it['monthly'] == amount and it['charge_day'] == int(rows[0]['date'][8:])
                and it['end'] >= rows[0]['date'][:7]):
            matches.append(dict(it, id=-it['id'], bill_only=True))
    return matches


def billed_terms(conn, data):
    """Confirm one repeated debit's term/name without inventing principal."""
    sid, months = data.get('id'), data.get('months')
    if type(sid) is not int or (months is not None and (type(months) is not int or not 2<=months<=MONTHS_MAX)) or not data.get('said'):
        raise ValueError('issued billed payment, months and owner words required')
    groups = [rows for rows in billed_groups(conn) if any(r['id']==sid for r in rows)]
    if len(groups) != 1:
        raise ValueError('choose one unambiguous repeated billed payment')
    rows = groups[0]; first = rows[0]; last = rows[-1]
    saved = group_schedules(conn, rows)
    if len(saved)==1 and not saved[0].get('bill_only') and saved[0]['spend_id'] in {r['id'] for r in rows}:
        old = saved[0]
        if old['first']==first['date'][:7] and (months is None or old['months']==months):
            if data.get('name'):
                conn.execute("UPDATE label SET display=? WHERE namespace=? AND token=(SELECT token FROM entity WHERE id=?)",
                             (data['name'], LABEL_NS, old['id']))
            match_billed_parts(conn)
            return dict(saved=True, id=old['id'], end=old['end'], superseded=[])
    if months is None:
        if len(saved)!=1 or not saved[0].get('bill_only') or not data.get('name'):
            raise ValueError('confirm installment months before naming it')
        from ledger_plan import put
        fact = dict(saved[0], id=-saved[0]['id'], name=data['name'], said=data['said'])
        fact.pop('bill_only', None)
        put(conn, 'installment:'+str(fact['id']), fact)
        return dict(saved=True, id=-fact['id'], end=fact['end'], superseded=[])
    if len(rows)>months: raise ValueError('term is shorter than observed charges')
    if any(not it.get('bill_only') and it['months'] != months for it in saved):
        raise ValueError('the saved installment has different terms')
    previous = [it for it in saved if it.get('bill_only')]
    if len(previous)>1: raise ValueError('multiple saved installments cover this charge')
    purchase_id = -previous[0]['id'] if previous else first['id']
    end = _mstr(_midx(first['date'][:7])+months-1)
    from ledger_plan import put
    put(conn, 'installment:'+str(purchase_id), dict(kind='remaining_installment', id=purchase_id,
        latest_source_id=last['id'], source_name=first['name'], name=data.get('name') or (previous[0]['name'] if previous else first['name']),
        method=first['method'], monthly=first['amount'], first=first['date'][:7], end=end,
        remaining_payments=months, charge_day=int(first['date'][8:]), as_of=data.get('as_of'),
        observed_ids=[r['id'] for r in rows],
        rate=0 if data.get('interest_free') is True or re.search(r'무이자|interest[- ]free',data['said'],re.I)
             else previous[0].get('rate') if previous else None, said=data['said']))
    old = [it for it in saved if not it.get('bill_only')]
    _supersede_billed_duplicates(conn, old, dict(id=-purchase_id), first['date'], data['said'])
    from ledger_review import put as review_put
    for r in rows:
        review_put(conn, r['id'], dict(billed_installment_id=-purchase_id, installment_shape='monthly_charge', checked=True))
    return dict(saved=True, id=-purchase_id, end=end, superseded=[it['id'] for it in old])


def add(K, conn, card, total, months, first=None, rate=0.0, name=None,
        day=None, spend_id=None, source='hand', auto_link=True,
        force=False):
    if not 2 <= months <= MONTHS_MAX:
        raise RegistrationRefused('months must be 2..%d (1 month is a lump-sum '
                      'payment): %r' % (MONTHS_MAX, months))
    if total <= 0:
        raise RegistrationRefused('total must be positive: %r' % total)
    if not 0 <= rate <= RATE_MAX:
        raise RegistrationRefused('--rate must be 0..%g (%% a year): %r' % (RATE_MAX,
                                                               rate))
    ensure_metrics(conn)
    card_eid = LB._seat(K, conn, card)
    day = day or K.person_today_local()
    existing = items(conn)
    covered = _covered_installment(conn, existing, card, total, day, spend_id)
    if covered:
        raise RegistrationRefused('spend or monthly charge is already an installment: #%d' % covered['id'])
    # An export's repeated monthly debit cannot become a full principal just
    # because the owner supplies a name and a term. Confirm its billed shape.
    if not force and any(rows[0]['amount']==total and rows[0]['method']==card['name']
            and any(r['id']==spend_id if spend_id else r['date']==day.isoformat() for r in rows)
            for rows in billed_groups(conn)):
        raise RegistrationRefused('state the charge, purchase total, months, interest-free terms and owner words')
    taken = {i['spend_id'] for i in existing if i['spend_id'] and not i.get('superseded_by')}
    same = [i for i in existing if not i['closed'] and not i.get('superseded_by')
            and i['method_id'] == card['method_id'] and i['total'] == total
            and i['months'] == months
            and i['purchase_date'] == day.isoformat()]
    if same and not force:
        raise RegistrationRefused('already recorded: #%d %s (%d / %d months, %s) -- '
                      '--new records a second one' % (
                          same[0]['id'], same[0]['name'], total, months,
                          day.isoformat()))
    linked = 'given'
    if spend_id is not None:
        b = _block_of_spend(conn, spend_id)
        if b is None:
            raise RegistrationRefused('spend %d not found' % spend_id)
        if b[3] != card_eid or b[2] >= 0:
            raise RegistrationRefused('spend %d is not a charge on this card' % spend_id)
        if spend_id in taken:
            raise RegistrationRefused('spend %d is already an installment' % spend_id)
    elif auto_link:
        spend_id = _auto_link(conn, card_eid, total, day, taken)
        linked = 'matched' if spend_id else None
    else:
        linked = None
    billed = _billed_rows(conn, card, total, months, day, spend_id) if rate == 0 else []
    if billed:
        if first and first != billed[0][1][:7]:
            raise RegistrationRefused('--first must match the first observed charge month %s' % billed[0][1][:7])
        first = billed[0][1][:7]
        day = datetime.date.fromisoformat(billed[0][1])
        spend_id = billed[0][0]
        for payment in billed:
            covered = _covered_installment(conn, existing, card, total, day, payment[0])
            if covered:
                raise RegistrationRefused('spend is already an installment: #%d' % covered['id'])
        linked = linked or 'matched'
    first = first or _mstr(_midx(day.isoformat()[:7]) + 1)
    if _midx(first) < _midx(day.isoformat()[:7]):
        raise RegistrationRefused('--first %s is before the purchase month %s'
                      % (first, day.isoformat()[:7]))
    if name is None and spend_id is not None:
        r = conn.execute("SELECT name FROM spend_entry WHERE id=?;",
                         (spend_id,)).fetchone()
        name = r[0] if r else None
    if not name:
        name = K._i18n('ledger.installment.default_name',
                       '{card} {date}').format(card=card['name'],
                                               date=day.isoformat())
    if source != 'hand' and any(i['source'] == source for i in existing):
        raise RegistrationRefused('%s is already an installment' % source)
    digest = hashlib.sha256(('%d|%s|%d|%d|%s|%s|%s' % (
        card['method_id'], name, total, months, first, day.isoformat(),
        source)).encode('utf-8')).hexdigest()[:12]
    ulid = ULID_PREFIX + digest
    if conn.execute("SELECT 1 FROM entity WHERE ulid=?;", (ulid,)).fetchone():
        raise RegistrationRefused('already recorded: %s %d / %d months from %s'
                      % (name, total, months, first))
    eid = conn.execute(
        "INSERT INTO entity(ulid, token, owner_entity_id, fungible, origin, "
        "status) VALUES(?,?,?,0,'module','active');",
        (ulid, 'installment_' + digest, card_eid)).lastrowid
    conn.execute(
        "INSERT INTO label(namespace, token, lang, display) "
        "VALUES(?,?,?,?);", (LABEL_NS, 'installment_' + digest,
                             (os.environ.get('AGENT_LANG') or 'ko')[:2],
                             name))
    d = day.isoformat()
    qual = 'first:%s' % first + (';spend:%d' % spend_id if spend_id else '')
    _put(conn, eid, M_TOTAL, total, 'krw', d, source, qual)
    _put(conn, eid, M_MONTHS, months, 'count', d, source)
    _put(conn, eid, M_RATE, rate, 'pct', d, source)
    if billed:
        from ledger_review import put
        put(conn, spend_id, dict(installment_shape='monthly_charge',
                                 billed_installment_id=eid, role='one_off', checked=True))
        match_billed_parts(conn)
    it = item(conn, eid)
    it['linked'] = linked
    return it


def update(K, conn, ref, changes):
    it = find(conn, ref)
    allowed = {'total','months','rate','first','name','date','spend','card'}
    if not changes or set(changes)-allowed or it.get('superseded_by'):
        raise Refused('invalid installment update')
    total = LB._amount(str(changes.get('total',it['total'])))
    months = _int(str(changes.get('months',it['months'])), 'months')
    rate = _rate(str(changes.get('rate',it['rate'])))
    first = _month(changes.get('first',it['first']), 'first')
    day = LB._date(changes.get('date',it['purchase_date']), 'date')
    name = changes.get('name',it['name'])
    card = _card(conn, changes.get('card', str(it['method_id'])))
    spend = changes.get('spend', it['spend_id'])
    spend = _int(str(spend), 'spend') if spend not in (None, '') else None
    if (not 2 <= months <= MONTHS_MAX or not 0 <= rate <= RATE_MAX or total <= 0 or not name.strip()
            or day > K.person_today_local() or first < day.isoformat()[:7]):
        raise Refused('invalid installment terms')
    if it['closed'] and day.isoformat() > it['closed']['date']:
        raise Refused('purchase date follows closure')
    eid = LB._seat(K, conn, card)
    if spend is not None:
        b = _block_of_spend(conn, spend)
        if not b or b[3] != eid or b[2] >= 0:
            raise Refused('spend is not a charge on this card')
        if any(x['id'] != it['id'] and x['spend_id']==spend and not x.get('superseded_by') for x in items(conn)):
            raise Refused('spend is already an installment')
    # Observed monthly-charge groups carry additional review facts. Keep
    # that evidence attached; changing their link requires a reviewed shape.
    from ledger_review import annotations
    linked = [sid for sid, fact in annotations(conn).items() if fact.get('billed_installment_id')==it['id']]
    if linked and (spend != it['spend_id'] or eid != it['card_entity_id']):
        raise Refused('review billed installment links before changing the card or spend')
    qual = 'first:'+first+(';spend:%d'%spend if spend else '')
    for metric, value, unit, q in ((M_TOTAL,total,'krw',qual), (M_MONTHS,months,'count',None), (M_RATE,rate,'pct',None)):
        conn.execute('DELETE FROM measure WHERE entity_id=? AND metric=?', (it['id'],metric))
        _put(conn,it['id'],metric,value,unit,day.isoformat(),it['source'],q)
    token = conn.execute('SELECT token FROM entity WHERE id=?',(it['id'],)).fetchone()[0]
    conn.execute('UPDATE label SET display=? WHERE namespace=? AND token=?',(name,LABEL_NS,token))
    conn.execute('UPDATE entity SET owner_entity_id=? WHERE id=?',(eid,it['id']))
    if it['closed']:
        close = it['closed']
        charged = max(0,min(months,_midx(close['date'][:7])-_midx(first)+(close['kind']=='done')))
        conn.execute('UPDATE measure SET value=? WHERE entity_id=? AND metric=?',(charged,it['id'],M_CLOSED))
    return item(conn,it['id'])


def reopen(conn, ref):
    it = find(conn, ref)
    if it.get('superseded_by'):
        raise Refused('superseded installment cannot be reopened')
    conn.execute('DELETE FROM measure WHERE entity_id=? AND metric=?',(it['id'],M_CLOSED))
    conn.execute("UPDATE entity SET status='active' WHERE id=?",(it['id'],))
    return item(conn,it['id'])


def _close(K, conn, ref, kind, day):
    it = find(conn, ref)
    if it['closed']:
        raise Refused('%s is already closed (%s %s)' % (
            it['name'], it['closed']['kind'], it['closed']['date']))
    d = day.isoformat()
    if d < it['purchase_date']:
        raise Refused('--date %s is before the purchase %s'
                      % (d, it['purchase_date']))
    ensure_metrics(conn)
    k = _midx(d[:7]) - _midx(it['first']) + (1 if kind == 'done' else 0)
    charged = max(0, min(it['months'], k))
    _put(conn, it['id'], M_CLOSED, charged, 'count', d, 'hand', kind)
    conn.execute("UPDATE entity SET status='gone' WHERE id=?;", (it['id'],))
    return item(conn, it['id'])


# -- reads --------------------------------------------------------------------
def _card_of_entity(conn):
    return {m['entity_id']: m for m in LB._methods(conn)
            if m['entity_id'] is not None}


def items(conn, ids=None):
    cards = _card_of_entity(conn)
    q = ("SELECT e.id, e.owner_entity_id, e.status, l.display FROM entity e "
         "LEFT JOIN label l ON l.namespace=? AND l.token=e.token "
         "WHERE e.ulid LIKE ? ORDER BY e.id;")
    out = []
    for eid, owner, status, name in conn.execute(q, (LABEL_NS,
                                                     ULID_PREFIX + '%')):
        if ids is not None and eid not in ids:
            continue
        terms = {}
        for metric, value, qual, day, src in conn.execute(
                "SELECT metric, value, qualifier, observed_at, source FROM "
                "measure WHERE entity_id=? ORDER BY id;", (eid,)):
            terms[metric] = (value, qual, day, src)
        if M_TOTAL not in terms or M_MONTHS not in terms:
            continue
        tq = _quals(terms[M_TOTAL][1])
        card = cards.get(owner)
        months = int(terms[M_MONTHS][0])
        first = tq.get('first')
        it = {'id': eid, 'name': name or '#%d' % eid,
              'card': card['name'] if card else None,
              'method_id': card['method_id'] if card else None,
              'card_entity_id': owner,
              'total': int(terms[M_TOTAL][0]), 'months': months,
              'rate': float(terms[M_RATE][0]) if M_RATE in terms else 0.0,
              'first': first, 'end': _mstr(_midx(first) + months - 1),
              'purchase_date': terms[M_TOTAL][2][:10],
              'source': terms[M_TOTAL][3],
              'spend_id': int(tq['spend']) if tq.get('spend', '').isdigit()
              else None,
              'status': status, 'closed': None}
        if M_REPLACED in terms:
            it['superseded_by']=int(terms[M_REPLACED][0])
        if M_CLOSED in terms:
            v, qual, day, _ = terms[M_CLOSED]
            it['closed'] = {'kind': qual, 'date': day[:10],
                            'charged': int(v)}
        out.append(it)
    return out


def item(conn, eid):
    r = items(conn, {eid})
    if not r:
        raise Refused('no installment #%s' % eid)
    return r[0]


def find(conn, ref):
    s = str(ref or '').strip().lstrip('#')
    if not s:
        raise Refused('installment required (installment-list shows them)')
    all_ = items(conn)
    if s.isdigit():
        hit = [i for i in all_ if i['id'] == int(s)]
    else:
        hit = [i for i in all_ if i['name'] == s and not i['closed']] or \
              [i for i in all_ if i['name'] == s]
    if len(hit) == 1:
        return hit[0]
    if len(hit) > 1:
        raise Refused('ambiguous installment %r: %s' % (
            s, ', '.join('#%d' % i['id'] for i in hit)))
    raise Refused('no installment %r (installment-list shows them)' % s)


def charge_in(it, month):
    """This item's charge in `month` -> (k, row) or (k, None) when nothing
    is charged that month (before / after the run, or closed)."""
    k = _midx(month) - _midx(it['first']) + 1
    if it.get('superseded_by') or not 1 <= k <= it['months']:
        return k, None
    if it['closed'] and k > it['closed']['charged']:
        return k, None
    return k, schedule(it['total'], it['months'], it['rate'])[k - 1]


def at(it, month):
    """The item as of `month`: n/N, this month's charge, remaining, state."""
    if it.get('bill_only'):
        k=_midx(month)-_midx(it['first'])+1
        active=1<=k<=it['months']
        return dict(it,month=month,n=max(0,min(k,it['months'])),this_month=it['monthly'] if active else 0,
                    remaining=None,remaining_bills=max(0,it['months']-max(0,k)),
                    state='active' if active else 'upcoming' if k<1 else 'ended')
    sch = schedule(it['total'], it['months'], it['rate'])
    k, row = charge_in(it, month)
    r = dict(it)
    r['month'] = month
    r['first_charge'] = sch[0]['charge']
    r['monthly'] = sch[1]['charge']      # the regular month (months >= 2)
    if it.get('superseded_by'):
        r.update(state='superseded', n=0, this_month=0, remaining=0)
    elif it['closed']:
        r.update(state=it['closed']['kind'], n=it['closed']['charged'],
                 this_month=row['charge'] if row else 0, remaining=0)
    elif k < 1:
        r.update(state='upcoming', n=0, this_month=0,
                 remaining=it['total'])
    elif k > it['months']:
        r.update(state='ended', n=it['months'], this_month=0, remaining=0)
    else:
        r.update(state='active', n=k, this_month=row['charge'],
                 remaining=row['remaining'])
    # the knowledge-warehouse debt line: balance, rate, monthly payment
    r['debt_line'] = {'balance': r['remaining'], 'rate_pct': it['rate'],
                      'monthly_payment': r['this_month'] or r['monthly']}
    return r


def listing(conn, month, include_all=False):
    rows = [at(i, month) for i in items(conn)]+_remaining_bill_rows(conn,month)
    if not include_all:
        rows = [r for r in rows if r['state'] in ('active', 'upcoming')]
    load = {}
    for r in rows:
        if r['this_month']:
            c = load.setdefault(r['card'], {'card': r['card'], 'amount': 0,
                                            'n': 0})
            c['amount'] += r['this_month']
            c['n'] += 1
    return {'month': month, 'items': rows, 'cards': list(load.values())}


def month_charges(conn, m, month):
    """For ledger_balance.bill_month: the installment charges due on card m
    in `month`, and the linked purchase rows dated in that month (they are
    in the recorded spend at full amount and must leave the one-off part)."""
    lo, hi = LB._month_bounds(month)
    out = {'charges': 0, 'n': 0, 'purchases': 0, 'purchases_n': 0,
           'items': []}
    from ledger_review import annotations
    meta = annotations(conn)
    for it in items(conn):
        if it.get('superseded_by') or it['method_id'] != m['method_id']:
            continue
        k, row = charge_in(it, month)
        if row:
            out['charges'] += row['charge']
            out['n'] += 1
            out['items'].append({'id': it['id'], 'name': it['name'], 'k': k,
                                 'months': it['months'],
                                 'charge': row['charge']})
        if it['spend_id'] and meta.get(it['spend_id'],{}).get('installment_shape')=='monthly_charge':
            if row is None:
                continue
            # Replace observed billed installments instead of counting the schedule twice.
            for sid,fact in meta.items():
                if fact.get('billed_installment_id') != it['id']:
                    continue
                payment=conn.execute("SELECT amount FROM spend_entry WHERE id=? AND kind='expense' AND date>=? AND date<?",(sid,lo.isoformat(),hi.isoformat())).fetchone()
                if payment:
                    out['purchases'] += payment[0]
                    out['purchases_n'] += 1
        elif it['spend_id']:
            b = _block_of_spend(conn, it['spend_id'])
            if b and b[3] == m['entity_id'] and \
                    lo.isoformat() <= b[1] < hi.isoformat():
                out['purchases'] += int(-b[2])
                out['purchases_n'] += 1
    for it in _remaining_bill_rows(conn,month):
        if it['method']!=m['name'] or not it['this_month']:continue
        out['charges']+=it['this_month'];out['n']+=1
        out['items'].append(dict(id=it['id'],name=it['name'],k=it['n'],months=it['months'],charge=it['this_month']))
        observed=conn.execute("SELECT amount FROM spend_entry WHERE name=? AND method=? AND date>=? AND date<? AND amount=? AND kind='expense' AND COALESCE(class,'spend')='spend'",(it.get('source_name',it['name']),it['method'],lo.isoformat(),hi.isoformat(),it['monthly'])).fetchall()
        if len(observed)==1:out['purchases']+=observed[0][0];out['purchases_n']+=1
    return out


def match_billed_parts(conn):
    """Attach exact later charges of an owner-confirmed monthly-charge export."""
    from ledger_review import annotations, put
    meta=annotations(conn)
    linked=[]
    for it in items(conn):
        if it.get('superseded_by') or not it['spend_id'] or meta.get(it['spend_id'],{}).get('installment_shape')!='monthly_charge':
            continue
        original=conn.execute('SELECT name,method,date FROM spend_entry WHERE id=?',(it['spend_id'],)).fetchone()
        if original is None:
            continue
        for index,scheduled in enumerate(schedule(it['total'],it['months'],it['rate'])):
            ym=_mstr(_midx(it['first'])+index)
            if charge_in(it,ym)[1] is None:
                continue
            date=ym+original[2][7:]
            matches=conn.execute("SELECT id FROM spend_entry WHERE name=? AND method=? AND date=? AND amount BETWEEN ? AND ? AND kind='expense' AND COALESCE(class,'spend')='spend'",
                                 (original[0],original[1],date,
                                  scheduled['charge'] - (it['total'] % it['months'] if index == 0 and not it['rate'] else 0),
                                  scheduled['charge'])).fetchall()
            if len(matches)==1:
                sid=matches[0][0]
                put(conn,sid,dict(role='one_off',checked=True,billed_installment_id=it['id']))
                linked.append(sid)
    return linked


def _orphaned_billed_duplicates(conn, card, payment, total, months):
    """Old import IDs can disappear while their installment entities survive.

    Only a missing linked spend, same card, exact term and uniquely observed
    schedule date/amount can support replacement. A name match never does.
    Retain the old measures as an audit trail, without calling this a refund.
    """
    dates={}
    for index,row in enumerate(schedule(total,months,0)):
        date=_mstr(_midx(payment[0][:7])+index)+payment[0][7:]
        observed=conn.execute("SELECT id,amount FROM spend_entry WHERE method=? AND date=? "
                              "AND kind='expense' AND COALESCE(class,'spend')='spend' AND amount BETWEEN ? AND ?",
                              (payment[3],date,total // months,row['charge'])).fetchall()
        if len(observed)==1:
            dates[date]=observed[0][1]
    return [it for it in items(conn) if not it['closed'] and not it.get('superseded_by')
            and it['method_id']==card['method_id'] and it['months']==months
            and it['spend_id'] and not conn.execute('SELECT 1 FROM spend_entry WHERE id=?',(it['spend_id'],)).fetchone()
            and it['purchase_date'] in dates
            and (abs(it['total']-total)<=months or it['total']==dates[it['purchase_date']])]


def _supersede_billed_duplicates(conn, duplicates, created, day, said):
    if duplicates:
        conn.execute("INSERT OR IGNORE INTO metric_def(token,scope_kind,accrual,unit_token,owner_module,origin) VALUES(?,'entity','observed','count','ledger','module')",(M_REPLACED,))
    for old in duplicates:
        _put(conn,old['id'],M_REPLACED,created['id'],'count',day,'owner-billed-correction',said)
        conn.execute("UPDATE entity SET status='gone' WHERE id=?",(old['id'],))


def remaining_bills(conn, data):
    """Owner states future bill count; never infer purchase total or APR.

    The observed monthly debit is a cash-flow estimate, not a principal
    balance. Keep this separate from the existing full-purchase model.
    """
    from ledger_plan import facts, put
    sid,count,day=data.get('id'),data.get('remaining_payments'),data.get('as_of')
    if type(sid) is not int or type(count) is not int or not 0<=count<=MONTHS_MAX or not data.get('said'):
        raise ValueError('issued payment, remaining bill count and owner words required')
    day=datetime.date.fromisoformat(day).isoformat()
    row=conn.execute("SELECT date,amount,name,method FROM spend_entry WHERE id=? AND kind='expense' AND COALESCE(class,'spend')='spend'",(sid,)).fetchone()
    if not row or row[0]>day or row[1]<=0:raise ValueError('observed billed payment required')
    latest=conn.execute("SELECT max(date) FROM spend_entry WHERE name=? AND method=? AND amount=? AND date<=? AND kind='expense' AND COALESCE(class,'spend')='spend'",(row[2],row[3],row[1],day)).fetchone()[0]
    first=_mstr(_midx(latest[:7])+1)
    last=_mstr(_midx(first)+count-1)
    previous=[f for key,f in facts(conn).items() if key.startswith('installment:')
              and f.get('kind')=='remaining_installment' and f.get('source_name',f['name'])==row[2]
              and f['method']==row[3] and f['monthly']==row[1]
              and f['charge_day']==int(row[0][8:]) and f['end']>=row[0][:7]]
    if len(previous)>1:
        raise RegistrationRefused('multiple remaining installment schedules cover this charge')
    purchase_id=previous[0]['id'] if previous else sid
    from ledger_review import annotations, put as review_put
    covered=annotations(conn).get(sid,{}).get('billed_installment_id')
    # A remaining-count answer confirms the existing purchase when its end
    # agrees. A changed count supersedes that schedule, never adds beside it.
    current=[it for it in items(conn) if not it['closed'] and not it.get('superseded_by')
             and (it['id']==covered or it['spend_id']==sid)]
    if len(current)==1 and current[0]['end']==last:
        review_put(conn,sid,dict(remaining_payments=count,remaining_as_of=day,said=data['said']))
        return dict(saved=True,remaining_payments=count,end=last,superseded=[],merged_into=current[0]['id'])
    # Retire a covered schedule with corrected terms, or a uniquely evidenced
    # orphan after reimport. Unrelated live purchases survive.
    card=_card(conn,row[3]);superseded=[]
    for old in items(conn):
        if old['closed'] or old.get('superseded_by') or old['method_id']!=card['method_id'] or not old['spend_id']:
            continue
        if old['id']!=covered and old['spend_id']!=sid:
            if conn.execute('SELECT 1 FROM spend_entry WHERE id=?',(old['spend_id'],)).fetchone():continue
            observed=conn.execute("SELECT id,name,amount FROM spend_entry WHERE date=? AND method=? AND kind='expense' AND COALESCE(class,'spend')='spend' AND amount=?",
                                  (old['purchase_date'],row[3],row[1])).fetchall()
            if len(observed)!=1 or observed[0][1]!=row[2]:continue
        conn.execute("INSERT OR IGNORE INTO metric_def(token,scope_kind,accrual,unit_token,owner_module,origin) VALUES(?,'entity','observed','count','ledger','module')",(M_REPLACED,))
        _put(conn,old['id'],M_REPLACED,-purchase_id,'count',day,'owner-remaining-bills',data['said'])
        superseded.append(old['id'])
    # Future bills start after the latest actual charge, never before today.
    put(conn,'installment:'+str(purchase_id),dict(kind='remaining_installment',id=purchase_id,
         latest_source_id=sid,source_name=row[2],name=data.get('name') or (previous[0]['name'] if previous else row[2]),method=row[3],
         as_of=day,first=first,end=last,monthly=row[1],remaining_payments=count,
         rate=previous[0].get('rate') if previous else None,
         charge_day=int(latest[8:]),said=data['said'],superseded=superseded))
    return dict(saved=True,remaining_payments=count,end=last,superseded=superseded)


def _remaining_bill_rows(conn, month):
    from ledger_plan import facts
    rows=[]
    for key,f in facts(conn).items():
        if not key.startswith('installment:') or f.get('kind')!='remaining_installment':continue
        if month>f['end'] or not f['remaining_payments']:continue
        n=max(0,_midx(month)-_midx(f['first'])+1)
        left=max(0,f['remaining_payments']-n)
        rows.append(dict(f,id=-f['id'],month=month,bill_only=True,remaining=None,remaining_bills=left,
                         this_month=f['monthly'] if f['first']<=month<=f['end'] else 0,
                         state='active' if n else 'upcoming',n=n,months=f['remaining_payments'],
                         card=f['method'],total=None,rate=f.get('rate'),
                         debt_line=dict(balance=None,rate_pct=f.get('rate'),monthly_payment=f['monthly'])))
    return rows


def billed_parts(K, conn, data):
    """Explicit owner terms for a source that contains billed parts, not a full purchase."""
    from ledger_review import annotations, put
    sid,total,months=data.get('id'),data.get('total'),data.get('months')
    said=data.get('said')
    if (type(sid) is not int or type(total) is not int or type(months) is not int
            or not 2<=months<=MONTHS_MAX or total<=0 or data.get('interest_free') is not True
            or not isinstance(said,str) or not said.strip()):
        raise ValueError('state the charge, purchase total, months, interest-free terms and owner words')
    payment=conn.execute("SELECT date,amount,name,method FROM spend_entry WHERE id=? AND kind='expense' AND COALESCE(class,'spend')='spend'",(sid,)).fetchone()
    if not payment or not total // months <= payment[1] <= schedule(total,months,0)[0]['charge']:
        raise ValueError('first billed charge must reconcile to the owner purchase total and term')
    prior=annotations(conn).get(sid,{})
    if prior.get('installment_shape')=='monthly_charge' and prior.get('billed_installment_id'):
        saved=item(conn,prior['billed_installment_id'])
        if (saved['total'],saved['months'],saved['rate'],saved['purchase_date']) != (total,months,0,payment[0]):
            raise ValueError('the charge already has different installment terms')
        duplicates=_orphaned_billed_duplicates(conn,_card(conn,payment[3]),payment,total,months)
        _supersede_billed_duplicates(conn,duplicates,saved,payment[0],said)
        return dict(reviewed='billed_installment',installment=saved,observed=match_billed_parts(conn),superseded=[it['id'] for it in duplicates])
    card=_card(conn,payment[3])
    duplicates=_orphaned_billed_duplicates(conn,card,payment,total,months)
    conflict=any(it['id'] not in {old['id'] for old in duplicates} and not it['closed']
                 and not it.get('superseded_by') and it['method_id']==card['method_id']
                 and it['total']==total and it['months']==months and it['purchase_date']==payment[0]
                 for it in items(conn))
    created=add(K,conn,card,total,months,first=payment[0][:7],rate=0,
             name=data.get('name') or payment[2],day=datetime.date.fromisoformat(payment[0]),spend_id=sid,
             force=bool(duplicates) and not conflict)
    _supersede_billed_duplicates(conn,duplicates,created,payment[0],said)
    put(conn,sid,dict(installment_shape='monthly_charge',billed_installment_id=created['id'],
                      role='one_off',checked=True,said=said))
    return dict(reviewed='billed_installment',installment=created,observed=match_billed_parts(conn),superseded=[it['id'] for it in duplicates])


# -- detection ----------------------------------------------------------------
def _months_of(text, rx=_NOTE_RE):
    m = rx.search(text or '')
    if not m:
        return None
    n = int(next(g for g in m.groups() if g))
    return n if 2 <= n <= MONTHS_MAX else None


def _seq(ref):
    kind, n = ref.split(':')
    return int(n) * 2 + (1 if kind == 'intake' else 0)


def _known_refs(conn):
    known, spends = set(), set()
    for it in items(conn):
        known.add(it['source'])
        if it['spend_id'] and not it.get('superseded_by'):
            spends.add(it['spend_id'])
    for (q,) in conn.execute("SELECT qualifier FROM measure WHERE metric=?;",
                             (M_DECLINED,)):
        known.add(q)
    return known, spends


def _card_quiet(conn, ref):
    try:
        return _card(conn, ref)
    except Refused:
        return None


def candidates(conn):
    known, spends = _known_refs(conn)
    out = {}
    # 1. spend rows the ledger-log lane noted as installments
    for sid, day, amount, name, method, note in conn.execute(
            "SELECT id, date, amount, name, method, note FROM spend_entry "
            "WHERE amount > 0 AND kind='expense' ORDER BY id;"):
        n = _months_of(note) or _months_of(name)
        if not n:
            continue
        card = _card_quiet(conn, method)
        if card:
            out['spend:%d' % sid] = {
                'ref': 'spend:%d' % sid, 'basis': 'spend_note',
                'card': card['name'], 'amount': amount, 'months': n,
                'date': day, 'merchant': name, 'spend_id': sid}
    # 2./3. intake items: SMS templates carry the months; a Banksalad row
    # only hints in its content / memo text
    for iid, data, raw in conn.execute(
            "SELECT id, data_json, raw_payload FROM intake_item WHERE "
            "state != 'excluded' ORDER BY id;"):
        try:
            d = json.loads(data)
        except ValueError:
            continue
        if d.get('operation', 'payment') != 'payment':
            continue
        n, basis = d.get('installment_months'), 'sms_template'
        if not isinstance(n, int) or not 2 <= n <= MONTHS_MAX:
            n, basis = None, 'export_text'
            try:
                row = json.loads(raw)
            except ValueError:
                row = None
            if isinstance(row, dict) and CSV_METHOD in row:
                n = _months_of(row.get(CSV_MEMO), _HINT_RE) or \
                    _months_of(row.get(CSV_CONTENT), _HINT_RE)
        if not n:
            continue
        card = _card_quiet(conn, d.get('method'))
        if not card:
            continue
        lk = conn.execute(
            "SELECT b.ulid FROM intake_link l JOIN block b ON b.id=l.block_id "
            "WHERE l.item_id=? AND l.reversal_of IS NULL;", (iid,)).fetchone()
        sid = int(lk[0][8:]) if lk and lk[0].startswith('x-spend-') and \
            lk[0][8:].isdigit() else None
        ref = 'spend:%d' % sid if sid else 'intake:%d' % iid
        c = out.get(ref) or {
            'ref': ref, 'card': card['name'], 'amount': int(d['amount']),
            'date': d.get('date'), 'merchant': d.get('merchant'),
            'spend_id': sid}
        c.update(months=n, basis=basis, intake_id=iid)
        out[ref] = c
    return [c for ref, c in sorted(out.items(), key=lambda x: (
        x[1]['date'] or '', x[0]))
            if ref not in known and not (c.get('spend_id') in spends)
            and not ('intake_id' in c
                     and 'intake:%d' % c['intake_id'] in known)]


def _candidate(conn, ref):
    if not re.match(r'^(spend|intake):\d+$', str(ref or '')):
        raise Refused('candidate ref must be spend:N or intake:N: %r' % ref)
    for c in candidates(conn):
        if c['ref'] == ref:
            return c
    raise Refused('no open installment candidate %s (installment-detect '
                  'lists them)' % ref)


def decline(K, conn, ref):
    c = _candidate(conn, ref)
    ensure_metrics(conn)
    card = _card(conn, c['card'])
    _put(conn, LB._seat(K, conn, card), M_DECLINED, c['months'], 'count',
         c['date'] or K.person_today_local().isoformat(), 'hand', ref,
         _seq(ref))
    return {'action': 'declined', 'ref': ref, 'card': c['card'],
            'amount': c['amount'], 'months': c['months']}


# -- owner-language render ----------------------------------------------------
def _won(K, n):
    return LB._won(K, n)


def _ym(K, month):
    return K._i18n('ledger.installment.ym', '{y}-{m}').format(
        y=int(month[:4]), m=int(month[5:7]))


def say_list(K, res):
    t = K._i18n
    lines = []
    for r in res['items']:
        if r.get('bill_only'):
            lines.append(t('ledger.onboarding.reduce.bills_remaining','').format(name=r['name'],end=r['end']+'-%02d'%r['charge_day'],amount=_won(K,r['monthly'])))
            continue
        key = 'ledger.installment.say.item_%s' % r['state']
        line = t(key, '').format(
            name=r['name'], card=r['card'] or '-', n=r['n'], N=r['months'],
            monthly=_won(K, r['this_month'] or r['monthly']),
            remaining=_won(K, r['remaining']), total=_won(K, r['total']),
            first=_ym(K, r['first']), end=_ym(K, r['end']),
            date=LB._md(K, r['closed']['date']) if r['closed'] else '-')
        line += (t('ledger.installment.say.rate', '').format(
            rate=('%g' % r['rate'])) if r['rate'] else
            t('ledger.installment.say.interest_free', ''))
        lines.append(line)
    for c in res['cards']:
        lines.append(t('ledger.installment.say.card_load', '').format(
            card=c['card'], month=_ym(K, res['month']),
            amount=_won(K, c['amount']), n=c['n']))
    if not res['items']:
        return [t('ledger.installment.say.none', '')]
    if any(not r.get('bill_only') for r in res['items']):lines.append(t('ledger.installment.say.rounding_note', ''))
    if any(r['rate'] for r in res['items']):
        lines.append(t('ledger.installment.say.fee_note', ''))
    return lines


def say_added(K, r):
    return [K._i18n('ledger.installment.say.added', '').format(
        name=r['name'], card=r['card'], total=_won(K, r['total']),
        N=r['months'], first=_ym(K, r['first']), end=_ym(K, r['end']),
        first_charge=_won(K, r['first_charge']),
        monthly=_won(K, r['monthly']))]


def say_closed(K, r):
    return [K._i18n('ledger.installment.say.closed_%s'
                    % r['closed']['kind'], '').format(
        name=r['name'], n=r['closed']['charged'], N=r['months'])]


def detect_items(K, cands):
    """Confirm items: a question, two short buttons, the proposal."""
    t = K._i18n
    out = []
    for c in cands:
        out.append({
            'ref': c['ref'],
            'text': t('ledger.installment.detect.ask', '').format(
                date=LB._md(K, c['date']) if c['date'] else '-',
                merchant=c['merchant'] or '-', amount=_won(K, c['amount']),
                card=c['card'], N=c['months']),
            'basis': t('ledger.installment.detect.basis.%s' % c['basis'], ''),
            'options': [t('ledger.installment.btn.confirm', ''),
                        t('ledger.installment.btn.dismiss', '')],
            'proposal': {'kind': 'installment_candidate', 'ref': c['ref'],
                         'confirm': 'installment-confirm %s' % c['ref'],
                         'dismiss': 'installment-dismiss %s' % c['ref']},
            'candidate': c})
    return out


# -- CLI ----------------------------------------------------------------------
def _rate(raw):
    if raw is None:
        return 0.0
    try:
        v = float(str(raw).rstrip('%'))
    except ValueError:
        raise Refused('--rate must be a number (%% a year): %r' % raw)
    return v


def _int(raw, what):
    s = str(raw).replace(',', '').strip()
    if not re.match(r'^\d+$', s):
        raise Refused('%s must be a whole number: %r' % (what, raw))
    return int(s)


def _line(r):
    if r.get('bill_only'):
        return 'BILLS\t#%d\t%s\t%s\t%d\t%s' % (r['id'],r['name'],r['month'],r['this_month'],r['end'])
    return '%s\t#%d\t%s\t%s\t%d/%d\tthis_month=%d\tremaining=%d\tmonthly=%d' \
        '\tfirst=%s\tend=%s\trate=%g%s' % (
            r['state'].upper(), r['id'], r['card'], r['name'], r['n'],
            r['months'], r['this_month'], r['remaining'], r['monthly'],
            r['first'], r['end'], r['rate'],
            ('\tspend=%d' % r['spend_id']) if r['spend_id'] else '')


def cli(K, verb, argv):
    argv, as_json = K._pop_flag(list(argv), '--json')
    conn = K.get_conn()
    try:
        try:
            if verb in ('installment-upd', 'installment-reopen'):
                conn.execute('BEGIN IMMEDIATE')
            _run(K, conn, verb, argv, as_json)
            if conn.in_transaction:
                conn.commit()
        except (Refused, sqlite3.IntegrityError) as e:
            if conn.in_transaction:
                conn.rollback()
            K._err('%s: %s' % (verb, e))
    finally:
        conn.close()


def _usage(K, verb):
    K._err('usage: kit_life.sh %s -- see database/ledger_installment.py'
           % verb)


def _terms_opts(argv):
    argv = list(argv)
    out = {}
    for flag in ('--first', '--rate', '--name', '--date', '--spend',
                 '--months'):
        if flag in argv:
            i = argv.index(flag)
            if i + 1 >= len(argv):
                raise Refused('%s requires a value' % flag)
            out[flag[2:]] = argv[i + 1]
            del argv[i:i + 2]
    return argv, out


def _run(K, conn, verb, argv, as_json):
    today = K.person_today_local()
    if verb in ('installment-upd','installment-reopen'):
        if not argv:
            _usage(K, verb)
        if verb == 'installment-reopen':
            if len(argv) != 1:
                _usage(K, verb)
            result = reopen(conn, argv[0])
        else:
            changes = {}
            for part in argv[1:]:
                if '=' not in part:
                    _usage(K, verb)
                field, value = part.split('=',1)
                changes[field] = value
            result = update(K, conn, argv[0], changes)
        print(json.dumps(result, ensure_ascii=False))
        return
    if verb in ('installment-add', 'installment-confirm'):
        argv, o = _terms_opts(argv)
        first = _month(o.get('first'), '--first')
        rate = _rate(o.get('rate'))
        argv, force = K._pop_flag(argv, '--new')
        if verb == 'installment-add':
            if len(argv) != 3 or 'months' in o:
                _usage(K, verb)
            day = LB._date(o.get('date'), '--date') or today
            if day > today:
                raise Refused('--date is in the future: %s' % day)
            r = add(K, conn, _card(conn, argv[0]), LB._amount(argv[1]),
                    _int(argv[2], 'months'), first, rate, o.get('name'), day,
                    _int(o['spend'], '--spend') if 'spend' in o else None,
                    force=force)
        else:
            if len(argv) != 1 or 'date' in o or 'spend' in o:
                _usage(K, verb)
            c = _candidate(conn, argv[0])
            months = _int(o['months'], '--months') if 'months' in o \
                else c['months']
            r = add(K, conn, _card(conn, c['card']), c['amount'], months,
                    first, rate, o.get('name') or c.get('merchant'),
                    LB._date(c['date'], 'candidate date'), c.get('spend_id'),
                    source=c['ref'], auto_link=False, force=force)
        r = at(r, today.isoformat()[:7])
        if as_json:
            print(json.dumps(r, ensure_ascii=False))
        else:
            print('ADDED\t#%d\t%s\t%s\ttotal=%d\tmonths=%d\tfirst=%s\tend=%s'
                  '\tfirst_charge=%d\tmonthly=%d\trate=%g\tlinked=%s' % (
                      r['id'], r['card'], r['name'], r['total'], r['months'],
                      r['first'], r['end'], r['first_charge'], r['monthly'],
                      r['rate'], ('spend:%d(%s)' % (r['spend_id'],
                                                    r['linked']))
                      if r['spend_id'] else '-'))
            for ln in say_added(K, r):
                print(ln)
        return
    if verb in ('installment-done', 'installment-cancel'):
        argv, d = K._pop_opt(argv, '--date')
        if len(argv) != 1:
            _usage(K, verb)
        day = LB._date(d, '--date') or today
        if day > today:
            raise Refused('--date is in the future: %s' % day)
        r = _close(K, conn, argv[0], verb.split('-')[1], day)
        if as_json:
            print(json.dumps(r, ensure_ascii=False))
        else:
            print('CLOSED\t#%d\t%s\t%s\tcharged=%d/%d\t%s' % (
                r['id'], r['name'], r['closed']['kind'],
                r['closed']['charged'], r['months'], r['closed']['date']))
            for ln in say_closed(K, r):
                print(ln)
        return
    if verb == 'installment-dismiss':
        if len(argv) != 1:
            _usage(K, verb)
        r = decline(K, conn, argv[0])
        print(json.dumps(r, ensure_ascii=False) if as_json else
              'DECLINED\t%s\t%s\t%d\t%d' % (r['ref'], r['card'], r['amount'],
                                            r['months']))
        return
    argv, say = K._pop_flag(argv, '--say')
    if verb == 'installment-list':
        argv, month = K._pop_opt(argv, '--month')
        argv, include_all = K._pop_flag(argv, '--all')
        if argv:
            _usage(K, verb)
        month = _month(month, '--month') or today.isoformat()[:7]
        res = listing(conn, month, include_all)
        if as_json:
            print(json.dumps(res, ensure_ascii=False))
        elif say:
            for ln in say_list(K, res):
                print(ln)
        else:
            for r in res['items']:
                print(_line(r))
            for c in res['cards']:
                print('LOAD\t%s\t%s\t%d\tn=%d' % (c['card'], res['month'],
                                                  c['amount'], c['n']))
            if not res['items']:
                print('NO_INSTALLMENTS')
    elif verb == 'installment-detect':
        if argv:
            _usage(K, verb)
        cands = candidates(conn)
        if as_json:
            print(json.dumps(detect_items(K, cands), ensure_ascii=False))
        elif say:
            its = detect_items(K, cands)
            for it in its:
                print('%s\n  [%s] [%s]' % (it['text'], it['options'][0],
                                           it['options'][1]))
            if not its:
                print(K._i18n('ledger.installment.detect.none', ''))
        else:
            for c in cands:
                print('CANDIDATE\t%s\t%s\t%s\t%d\t%d\t%s\t%s' % (
                    c['ref'], c['card'], c['date'], c['amount'], c['months'],
                    c['basis'], c['merchant']))
            if not cands:
                print('NO_CANDIDATES')
    else:
        _usage(K, verb)
