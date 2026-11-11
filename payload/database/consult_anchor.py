"""consult_anchor.py -- every consult answer lands in the kit (migration 074).

Owner 2026-10-09 09:23 / 09:25: every consult answer lands in the lifekit
system; the kit goal consult is the anchor that holds them; pack consults
write THROUGH kit verbs.  Audit: consult-to-kit-audit.md (fixes 2 5 6 7 8 + the anchor; fix 4 is
kit_life.nutrition_ledger_put).

Dispatched from kit_life._DISPATCH through cli(K, verb, argv) with K = the
running kit_life module, so this file never imports kit_life (a second copy
would re-read DB_PATH) -- the shop.py / intake.py convention.

VERBS
  consult-answer-add --domain D --question Q --landed TABLE:REF
        [--words W] [--why-id N] [--asked-by S] [--replace] [--json]
  consult-answers [--domain D] [--why-id N] [--all] [--json]
        each answer, where it landed and whether that row still holds it
        (held: the landed row is live; --all adds the rest)
  preference-add --topic T --item I [--polarity negotiable|inviolable]
        [--note W] [--detail '<json>'] [--replace] [--source S]
        [--anchor D:Q] [--said W] [--json]
  preference-list [--topic T] [--all] [--json] | preference-retire <id>
  availability-add --domain D --days MON,WED (--from HH:MM --to HH:MM |
        --anytime) [--note W] [--replace] [--source S] [--anchor D:Q]
        [--said W] [--json]
  availability-list [--domain D] [--all] [--json] | availability-retire <id>
  resource-equipment <id> (--set a,b | --add a,b | --remove a,b)
        [--source S] [--anchor D:Q] [--said W] [--json]
  resource-place-link <id> [--place <id|name>] [--address A]
        [--lat D --lng D] [--new-confirmed] [--json]
  constraint-care (<id> | --key K) (--care '<json>' | --clear)
        [--anchor D:Q] [--said W] [--json]
  diet-target-set (--deficit N | --surplus N | --from-config)
        [--protein G] [--source S] [--anchor D:Q] [--said W] [--json]
        ledger first (ledger_constraint nutrition), then the config copy,
        one transaction

--anchor <domain>:<question> on a write verb records the consult answer the
written row holds (consult_answer), in the same transaction.

EXIT CODES: 0 ok; 1 usage / refused (stderr, nothing written); 5 NEAR_MATCH
(resource-place-link, the kit's one place question).
"""
import json
import re
import sqlite3
import sys

VERBS = ('consult-answer-add', 'consult-answers', 'preference-add',
         'preference-list', 'preference-retire', 'availability-add',
         'availability-list', 'availability-retire', 'resource-equipment',
         'resource-place-link', 'constraint-care', 'diet-target-set')

SCHEMA_VERSION = 74
WEEKDAYS = ('MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN')
POLARITIES = ('negotiable', 'inviolable')
CARE_WHEN = ('before', 'after', 'anytime', 'heavy_sets')
_QUESTION_RE = re.compile(r'^[a-z][a-z0-9_.]*$')
_HM_RE = re.compile(r'^([01][0-9]|2[0-3]):[0-5][0-9]$')

# landed_table -> (key column, held predicate over the row) for the reader.
# A pointer is "held" while the row it names is live; pack_file is outside
# the kit and reads as held=None.
_LANDED = {
    'ledger_resource': ('id', "status = 'active'"),
    'ledger_constraint': ('id', "status = 'active'"),
    'ledger_preference': ('id', "status = 'active'"),
    'availability_window': ('id', "status = 'active'"),
    'place': ('id', "status = 'active'"),
    'routine_def': ('id', "status = 'active'"),
    'config': ('key', "1"),
    'life_why': ('id', "status = 'active'"),
    'ledger_goal': ('id', "status IN ('proposed','active','suspended')"),
    'goal_target': ('id', "valid_until IS NULL"),
    'metric_axis': ('axis_key', "1"),
    'pack_file': (None, None),
}
# what the reader shows of a landed row (the live value, never a copy)
_SHOW = {
    'ledger_resource': ('kind', 'name', 'detail', 'place_id'),
    'ledger_constraint': ('class', 'key', 'value', 'care'),
    'ledger_preference': ('topic', 'item', 'polarity', 'note', 'detail'),
    'availability_window': ('domain', 'weekdays', 'start_hm', 'end_hm'),
    'place': ('name', 'address'),
    'routine_def': ('title', 'cadence', 'time_of_day', 'place_id'),
    'config': ('value',),
    'life_why': ('text', 'label'),
    'ledger_goal': ('title', 'metric', 'target_value', 'horizon_date'),
    'goal_target': ('metric', 'target_low', 'target_high'),
    'metric_axis': ('label', 'domain'),
}
_JSON_COLS = ('detail', 'care')


class Refused(Exception):
    pass


# -- shared helpers (kit_life calls these too) -------------------------------
def anchor_parse(raw):
    """'<domain>:<question>' -> ((domain, question), None) | (None, err)."""
    dom, sep, q = str(raw or '').partition(':')
    dom, q = dom.strip(), q.strip()
    if not sep or not dom or not _QUESTION_RE.match(q):
        return None, ('--anchor is <domain>:<question> (question: lowercase '
                      'ascii, e.g. health:cardio), got %r' % (raw,))
    return (dom, q), None


def care_parse(raw):
    """The care drill list of a safety site -> (list, None) | (None, err).

    [{"drill": "<key>", "when": before|after|anytime|heavy_sets,
      "minutes": N, "for": "<what it precedes>", "words": "<user's words>"}]
    drill is required; when defaults to before; the rest optional."""
    try:
        items = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        return None, 'care is not JSON'
    if not isinstance(items, list):
        return None, 'care must be a JSON array of drills'
    out = []
    for it in items:
        if not isinstance(it, dict):
            return None, 'each care item is an object'
        drill = str(it.get('drill') or '').strip()
        if not drill:
            return None, 'each care item needs a drill'
        when = it.get('when') or 'before'
        if when not in CARE_WHEN:
            return None, 'care when must be one of %s' % '|'.join(CARE_WHEN)
        got = {'drill': drill, 'when': when}
        if it.get('minutes') is not None:
            m = it['minutes']
            if not isinstance(m, int) or isinstance(m, bool) or m < 0:
                return None, 'care minutes is a whole number >= 0'
            got['minutes'] = m
        for k in ('for', 'words'):
            if it.get(k) is not None:
                v = str(it[k]).strip()
                if v:
                    got[k] = v
        unknown = set(it) - {'drill', 'when', 'minutes', 'for', 'words'}
        if unknown:
            return None, 'unknown care field(s): %s' % ','.join(
                sorted(unknown))
        out.append(got)
    return out, None


def _why_for(K, conn, domain):
    whys = K._active_whys(conn, domain)
    return whys[-1]['why_id'] if whys else None


def anchor_put(K, conn, domain, question, table, ref, words=None,
               asked_by='chat', why_id=None, replace=False):
    """One consult_answer row, no commit. Same active (domain, question,
    table, ref) -> updated (the newest words), never duplicated. replace ->
    every other active landing of (domain, question) is superseded first.
    Returns {"id", "action"}."""
    if table not in _LANDED:
        raise Refused('unknown landed table %r (have: %s)'
                      % (table, ','.join(sorted(_LANDED))))
    if not _QUESTION_RE.match(str(question or '')):
        raise Refused('question is lowercase ascii (a-z0-9_.), got %r'
                      % (question,))
    ref = str(ref).strip()
    if not ref:
        raise Refused('landed ref is empty')
    if why_id is None:
        why_id = _why_for(K, conn, domain)
    now = K._utc_now_str()
    w = None if words is None else (str(words).strip() or None)
    if replace:
        conn.execute(
            "UPDATE consult_answer SET status='superseded', superseded_at=? "
            "WHERE domain=? AND question=? AND status='active' AND NOT "
            "(landed_table=? AND landed_ref=?);",
            (now, domain, question, table, ref))
    row = conn.execute(
        "SELECT id FROM consult_answer WHERE domain=? AND question=? AND "
        "landed_table=? AND landed_ref=? AND status='active';",
        (domain, question, table, ref)).fetchone()
    if row:
        conn.execute(
            "UPDATE consult_answer SET words=COALESCE(?, words), asked_by=?, "
            "why_id=COALESCE(?, why_id), recorded_at=? WHERE id=?;",
            (w, asked_by, why_id, now, row[0]))
        return {'id': row[0], 'action': 'updated'}
    cid = conn.execute(
        "INSERT INTO consult_answer (ulid, domain, why_id, question, words, "
        "landed_table, landed_ref, asked_by, status, recorded_at) "
        "VALUES (?,?,?,?,?,?,?,?,'active',?);",
        (K.new_ulid(), domain, why_id, question, w, table, ref, asked_by,
         now)).lastrowid
    return {'id': cid, 'action': 'added'}


def anchor_from_verb(K, conn, anchor, table, ref, said, source):
    """The --anchor leg of a write verb: None on success, else the error
    (the caller rolls back)."""
    try:
        anchor_put(K, conn, anchor[0], anchor[1], table, ref, said,
                   source or 'chat')
    except (Refused, sqlite3.Error) as e:
        return 'anchor: %s' % e
    return None


def _landed_row(conn, table, ref):
    """(held, shown) for one pointer: held True/False/None, shown = the
    live values the reader prints."""
    key, pred = _LANDED[table]
    if key is None:
        return None, None
    try:
        r = conn.execute(
            "SELECT %s, (%s) FROM %s WHERE %s = ?;" % (
                ', '.join(_SHOW[table]), pred, table, key),
            (int(ref) if key == 'id' and str(ref).isdigit() else ref,)
        ).fetchone()
    except sqlite3.Error:
        return False, None
    if r is None:
        return False, None
    shown = {}
    for col, v in zip(_SHOW[table], r[:-1]):
        if col in _JSON_COLS and isinstance(v, str):
            try:
                v = json.loads(v)
            except ValueError:
                pass
        shown[col] = v
    return bool(r[-1]), shown


def consult_answers(K, conn, domain=None, why_id=None, include_all=False):
    conds, args = [], []
    if domain:
        conds.append('domain = ?')
        args.append(domain)
    if why_id is not None:
        conds.append('why_id = ?')
        args.append(int(why_id))
    if not include_all:
        conds.append("status = 'active'")
    where = (' WHERE ' + ' AND '.join(conds)) if conds else ''
    out = []
    for (cid, dom, wid, q, words, table, ref, by, st, at) in conn.execute(
            "SELECT id, domain, why_id, question, words, landed_table, "
            "landed_ref, asked_by, status, recorded_at FROM consult_answer"
            "%s ORDER BY domain, question, id;" % where, tuple(args)):
        held, shown = _landed_row(conn, table, ref)
        if not include_all and held is False:
            continue
        out.append({'id': cid, 'domain': dom, 'why_id': wid, 'question': q,
                    'words': words, 'landed_table': table,
                    'landed_ref': ref, 'held': held, 'landed': shown,
                    'asked_by': by, 'status': st, 'recorded_at': at})
    return out


# -- preference (fix 6) -------------------------------------------------------
def preference_add(K, conn, topic, item, polarity='negotiable', note=None,
                   detail=None, replace=False, source='chat'):
    """One stated preference, no commit. Same active (topic, item) ->
    updated. replace -> the topic's other active rows are retired (the
    topic has one answer: cardio = stepmill)."""
    t = K._pain_site_token(topic)
    it = str(item or '').strip()
    if not t or not it:
        raise Refused('topic and item are required')
    if polarity not in POLARITIES:
        raise Refused('polarity is %s' % '|'.join(POLARITIES))
    dj = None
    if detail is not None:
        try:
            d = json.loads(detail) if isinstance(detail, str) else detail
        except ValueError:
            raise Refused('detail is not JSON')
        if not isinstance(d, dict):
            raise Refused('detail must be a JSON object')
        dj = json.dumps(d, ensure_ascii=False)
    now = K._utc_now_str()
    if replace:
        conn.execute("UPDATE ledger_preference SET status='retired' WHERE "
                     "topic=? AND item<>? AND status='active';", (t, it))
    row = conn.execute("SELECT id FROM ledger_preference WHERE topic=? AND "
                       "item=? AND status='active' ORDER BY id DESC LIMIT 1;",
                       (t, it)).fetchone()
    if row:
        conn.execute("UPDATE ledger_preference SET polarity=?, "
                     "note=COALESCE(?, note), detail=COALESCE(?, detail), "
                     "source=?, recorded_at=? WHERE id=?;",
                     (polarity, note, dj, source, now, row[0]))
        pid, action = row[0], 'updated'
    else:
        pid = conn.execute(
            "INSERT INTO ledger_preference (ulid, item, polarity, note, "
            "status, source, recorded_at, topic, detail) "
            "VALUES (?,?,?,?,'active',?,?,?,?);",
            (K.new_ulid(), it, polarity, note, source, now, t, dj)).lastrowid
        action = 'added'
    return dict(preference_list(K, conn, ids=[pid])[0], action=action)


def preference_retire(conn, preference_id):
    """Retire one active preference on the caller's transaction."""
    n = conn.execute("UPDATE ledger_preference SET status='retired' "
                     "WHERE id=? AND status='active';", (preference_id,)).rowcount
    if not n:
        raise Refused('no active row #%s' % preference_id)
    return {'id': preference_id, 'action': 'retired'}


def preference_list(K, conn, topic=None, include_retired=False, ids=None):
    conds, args = [], []
    if ids:
        conds.append('id IN (%s)' % ','.join('?' * len(ids)))
        args += list(ids)
    if topic:
        conds.append('topic = ?')
        args.append(K._pain_site_token(topic))
    if not include_retired and not ids:
        conds.append("status = 'active'")
    where = (' WHERE ' + ' AND '.join(conds)) if conds else ''
    out = []
    for pid, t, it, pol, note, dj, st, src, at in conn.execute(
            "SELECT id, topic, item, polarity, note, detail, status, source, "
            "recorded_at FROM ledger_preference%s ORDER BY topic, id;"
            % where, tuple(args)):
        out.append({'id': pid, 'topic': t, 'item': it, 'polarity': pol,
                    'note': note, 'detail': json.loads(dj) if dj else None,
                    'status': st, 'source': src, 'recorded_at': at})
    return out


# -- availability (fix 7) -----------------------------------------------------
def days_parse(raw):
    days = [d.strip().upper() for d in str(raw or '').split(',') if d.strip()]
    if not days or any(d not in WEEKDAYS for d in days):
        raise Refused('--days is a comma list of %s' % ','.join(WEEKDAYS))
    return ','.join(d for d in WEEKDAYS if d in days)


def availability_add(K, conn, domain, days, start=None, end=None, note=None,
                     replace=False, source='chat'):
    """One extra-day window, no commit. start/end None = anytime that day.
    Same active (domain, days, window) -> updated. replace -> the domain's
    other active windows are retired (a re-consult answers the whole set)."""
    dom = str(domain or '').strip()
    if not dom:
        raise Refused('--domain is required')
    wd = days_parse(days)
    if (start is None) != (end is None):
        raise Refused('give both --from and --to, or --anytime')
    if start is not None:
        if not (_HM_RE.match(start) and _HM_RE.match(end)) or start >= end:
            raise Refused('--from / --to are HH:MM with from < to')
    now = K._utc_now_str()
    row = conn.execute(
        "SELECT id FROM availability_window WHERE domain=? AND weekdays=? "
        "AND start_hm IS ? AND end_hm IS ? AND status='active';",
        (dom, wd, start, end)).fetchone()
    if replace:
        conn.execute(
            "UPDATE availability_window SET status='retired', retired_at=? "
            "WHERE domain=? AND status='active' AND id IS NOT ?;",
            (now, dom, row[0] if row else None))
    if row:
        conn.execute("UPDATE availability_window SET note=COALESCE(?, note), "
                     "source=?, recorded_at=? WHERE id=?;",
                     (note, source, now, row[0]))
        wid, action = row[0], 'updated'
    else:
        wid = conn.execute(
            "INSERT INTO availability_window (ulid, domain, weekdays, "
            "start_hm, end_hm, note, status, source, recorded_at) "
            "VALUES (?,?,?,?,?,?,'active',?,?);",
            (K.new_ulid(), dom, wd, start, end, note, source,
             now)).lastrowid
        action = 'added'
    return dict(availability_list(K, conn, ids=[wid])[0], action=action)


def availability_list(K, conn, domain=None, include_retired=False, ids=None):
    conds, args = [], []
    if ids:
        conds.append('id IN (%s)' % ','.join('?' * len(ids)))
        args += list(ids)
    if domain:
        conds.append('domain = ?')
        args.append(domain)
    if not include_retired and not ids:
        conds.append("status = 'active'")
    where = (' WHERE ' + ' AND '.join(conds)) if conds else ''
    out = []
    for wid, dom, wd, s, e, note, st, src, at in conn.execute(
            "SELECT id, domain, weekdays, start_hm, end_hm, note, status, "
            "source, recorded_at FROM availability_window%s ORDER BY domain, "
            "id;" % where, tuple(args)):
        out.append({'id': wid, 'domain': dom, 'weekdays': wd.split(','),
                    'start': s, 'end': e, 'anytime': s is None, 'note': note,
                    'status': st, 'source': src, 'recorded_at': at})
    return out


# -- resource equipment / place link (fixes 5, 2) -----------------------------
def _resource_row(conn, rid):
    r = conn.execute("SELECT id, kind, name, detail, status, place_id FROM "
                     "ledger_resource WHERE id=?;", (int(rid),)).fetchone()
    if r is None or r[4] != 'active':
        raise Refused('no active resource #%s' % rid)
    try:
        d = json.loads(r[3]) if r[3] else {}
    except ValueError:
        d = {}
    return {'id': r[0], 'kind': r[1], 'name': r[2],
            'detail': d if isinstance(d, dict) else {}, 'place_id': r[5]}


def _keys(raw):
    out = []
    for k in str(raw or '').split(','):
        k = k.strip()
        if k and k not in out:
            out.append(k)
    return out


def resource_equipment(K, conn, rid, set_=None, add=None, remove=None,
                       source='chat'):
    """The equipment list of one env resource, no commit. --set replaces,
    --add appends (no duplicates), --remove drops; order kept."""
    if sum(x is not None for x in (set_, add, remove)) != 1:
        raise Refused('give exactly one of --set / --add / --remove')
    r = _resource_row(conn, rid)
    if r['kind'] != 'env':
        raise Refused('resource #%s is %s, not env' % (rid, r['kind']))
    eq = list(r['detail'].get('equipment') or [])
    if set_ is not None:
        eq = _keys(set_)
    elif add is not None:
        eq += [k for k in _keys(add) if k not in eq]
    else:
        gone = set(_keys(remove))
        eq = [k for k in eq if k not in gone]
    d = dict(r['detail'], equipment=eq)
    now = K._utc_now_str()
    conn.execute("UPDATE ledger_resource SET detail=?, as_of=?, source=?, "
                 "recorded_at=? WHERE id=?;",
                 (json.dumps(d, ensure_ascii=False), now[:10], source, now,
                  r['id']))
    return {'id': r['id'], 'name': r['name'], 'equipment': eq,
            'detail': d}


# -- constraint care (fix 8) --------------------------------------------------
def constraint_care(K, conn, cid=None, key=None, care=None, clear=False):
    if (cid is None) == (key is None):
        raise Refused('give the constraint id or --key, not both')
    if cid is not None:
        r = conn.execute("SELECT id, class, key FROM ledger_constraint WHERE "
                         "id=? AND status='active';", (int(cid),)).fetchone()
    else:
        r = conn.execute("SELECT id, class, key FROM ledger_constraint WHERE "
                         "class='safety' AND key=? AND status='active' ORDER "
                         "BY id DESC LIMIT 1;",
                         (K._pain_site_token(key),)).fetchone()
    if r is None:
        raise Refused('no active constraint %s' % (cid or key))
    if r[1] != 'safety':
        raise Refused('care is for safety rows only (#%d is %s)' % (r[0],
                                                                    r[1]))
    if clear:
        items = None
    else:
        items, err = care_parse(care)
        if err:
            raise Refused(err)
    conn.execute("UPDATE ledger_constraint SET care=? WHERE id=?;",
                 (json.dumps(items, ensure_ascii=False)
                  if items is not None else None, r[0]))
    return {'id': r[0], 'key': r[2], 'care': items}


# -- diet target (fix 4) ------------------------------------------------------
def diet_target_set(K, conn, deficit=None, surplus=None, protein=None,
                    from_config=False, source='chat', style=None,
                    protein_mode=None):
    """Ledger first (nutrition constraints), then the config copy; the
    caller commits once. --from-config lands the numbers config already
    holds (an install whose target was set before 074). --style is the
    standing diet-style policy and --protein-mode manual|auto its protein
    intent (track A, macro targets); both validate BEFORE any write."""
    if style is not None and style not in K._macro_mod().STYLES:
        raise Refused('--style is one of %s' % ','.join(K._macro_mod().STYLES))
    if protein_mode is not None and protein_mode not in ('manual', 'auto'):
        raise Refused('--protein-mode is manual or auto')
    if protein_mode == 'auto' and protein is not None:
        raise Refused('--protein-mode auto takes no --protein')
    if protein is not None and K._config_stored(conn, 'diet_protein_mode') == 'auto':
        protein_mode = 'manual'
    if protein_mode == 'manual' and protein is None and \
            K._num(K._config_stored(conn, 'protein_g'), None) is None:
        raise Refused('--protein-mode manual needs --protein')
    if (protein is None and protein_mode is None and not from_config
            and K._config_stored(conn, 'protein_g') is None
            and K._config_stored(conn, 'diet_protein_mode') is None):
        protein_mode = 'auto'        # a target written with no protein = auto
    if from_config:
        if deficit is not None or surplus is not None:
            raise Refused('--from-config takes no --deficit / --surplus')
        deficit = K._num(K._config_stored(conn, 'deficit_kcal'), None)
        surplus = K._num(K._config_stored(conn, 'surplus_kcal'), None)
        if (protein is None and protein_mode != 'auto'
                and K._config_stored(conn, 'diet_protein_mode') != 'auto'):
            protein = K._num(K._config_stored(conn, 'protein_g'), None)
        if deficit is None and surplus is None:
            raise Refused('config holds no deficit_kcal / surplus_kcal')
    elif (deficit is None) == (surplus is None):
        raise Refused('give exactly one of --deficit / --surplus')
    if protein_mode == 'manual' and protein is None:
        protein = K._num(K._config_stored(conn, 'protein_g'), None)
    vals = {}
    for k, v in (('deficit', deficit), ('surplus', surplus),
                 ('protein', protein)):
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise Refused('--%s is a number' % k)
        if f < 0 or f > 5000:
            raise Refused('--%s out of range' % k)
        vals[k] = int(round(f))
    d, s = vals.get('deficit', 0), vals.get('surplus', 0)
    try:
        ids = K.nutrition_ledger_put(conn, d, s, vals.get('protein'), source)
        ids.update(K.nutrition_policy_put(conn, style, protein_mode, source))
    except ValueError as e:
        raise Refused(str(e))
    K._config_put(conn, 'deficit_kcal', d if not s else 0)
    K._config_put(conn, 'surplus_kcal', s)
    if 'protein' in vals:
        K._config_put(conn, 'protein_g', vals['protein'])
    if style is not None:
        K._config_put(conn, 'diet_style', style)
    if protein_mode == 'manual':
        K._config_put(conn, 'diet_protein_mode', 'manual')
        K._config_put(conn, 'diet_protein_override', '1')
    elif protein_mode == 'auto':
        K._config_put(conn, 'diet_protein_mode', 'auto')
        K._config_put(conn, 'diet_protein_override', '0')
        conn.execute("DELETE FROM config WHERE key='protein_g'")
    return {'ledger': ids, 'deficit_kcal': d if not s else 0,
            'surplus_kcal': s, 'protein_g': vals.get('protein'),
            'diet_style': style, 'protein_mode': protein_mode}


# -- CLI ----------------------------------------------------------------------
def _usage(K, verb):
    K._err('usage: kit_life.sh %s -- see database/consult_anchor.py' % verb)


def _out(as_json, res, text):
    if as_json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        for line in text:
            print(line)


def _anchor_after(K, conn, anchor, said, table, ref, source):
    if anchor:
        anchor_put(K, conn, anchor[0], anchor[1], table, ref, said,
                   source or 'chat')


def cli(K, verb, argv):
    argv, as_json = K._pop_flag(argv, '--json')
    conn = K.get_conn()
    try:
        v = conn.execute('PRAGMA user_version').fetchone()[0]
        if v < SCHEMA_VERSION:
            K._err('SCHEMA_NOT_READY\t%s needs user_version >= %d (this '
                   'database: %d)' % (verb, SCHEMA_VERSION, v))
        try:
            _run(K, conn, verb, argv, as_json)
            if conn.in_transaction:
                conn.commit()
        except (Refused, sqlite3.IntegrityError) as e:
            if conn.in_transaction:
                conn.rollback()
            K._err('%s: %s' % (verb, e))
    finally:
        conn.close()


def _run(K, conn, verb, argv, as_json):
    if verb == 'consult-answer-add':
        argv, replace = K._pop_flag(argv, '--replace')
        argv, dom = K._pop_opt(argv, '--domain')
        argv, q = K._pop_opt(argv, '--question')
        argv, landed = K._pop_opt(argv, '--landed')
        argv, words = K._pop_opt(argv, '--words')
        argv, wid = K._pop_opt(argv, '--why-id')
        argv, by = K._pop_opt(argv, '--asked-by')
        table, _s, ref = str(landed or '').partition(':')
        if argv or not dom or not q or not table or not ref or (
                wid is not None and not str(wid).isdigit()):
            _usage(K, verb)
        res = anchor_put(K, conn, dom, q, table, ref, words, by or 'chat',
                         int(wid) if wid else None, replace)
        _out(as_json, res, ['%s\t#%d\t%s\t%s:%s' % (
            res['action'].upper(), res['id'], q, table, ref)])
    elif verb == 'consult-answers':
        argv, allr = K._pop_flag(argv, '--all')
        argv, dom = K._pop_opt(argv, '--domain')
        argv, wid = K._pop_opt(argv, '--why-id')
        if argv or (wid is not None and not str(wid).isdigit()):
            _usage(K, verb)
        rows = consult_answers(K, conn, dom, wid, allr)
        _out(as_json, rows, ['ANSWER\t%s\t%s\t%s:%s\t%s\t"%s"' % (
            r['domain'], r['question'], r['landed_table'], r['landed_ref'],
            {True: 'held', False: 'not_held', None: 'external'}[r['held']],
            r['words'] or '') for r in rows] or ['NO_ANSWERS'])
    elif verb == 'preference-add':
        argv, replace = K._pop_flag(argv, '--replace')
        argv, topic = K._pop_opt(argv, '--topic')
        argv, item = K._pop_opt(argv, '--item')
        argv, pol = K._pop_opt(argv, '--polarity')
        argv, note = K._pop_opt(argv, '--note')
        argv, detail = K._pop_opt(argv, '--detail')
        argv, src = K._pop_opt(argv, '--source')
        argv, anchor, said = K._pop_anchor(argv)
        if argv or not topic or not item:
            _usage(K, verb)
        res = preference_add(K, conn, topic, item, pol or 'negotiable', note,
                             detail, replace, src or 'chat')
        _anchor_after(K, conn, anchor, said, 'ledger_preference', res['id'],
                      src)
        _out(as_json, res, ['%s\t#%d\t%s\t%s' % (
            res['action'].upper(), res['id'], res['topic'], res['item'])])
    elif verb == 'preference-list':
        argv, allr = K._pop_flag(argv, '--all')
        argv, topic = K._pop_opt(argv, '--topic')
        if argv:
            _usage(K, verb)
        rows = preference_list(K, conn, topic, allr)
        _out(as_json, rows, ['#%d\t%s\t%s\t%s\t[%s]' % (
            r['id'], r['topic'], r['item'], json.dumps(
                r['detail'], ensure_ascii=False), r['status'])
            for r in rows] or ['NO_PREFERENCES'])
    elif verb in ('preference-retire', 'availability-retire'):
        if len(argv) != 1 or not argv[0].isdigit():
            _usage(K, verb)
        if verb == 'preference-retire':
            preference_retire(conn, int(argv[0]))
            n = 1
        else:
            n = conn.execute("UPDATE availability_window SET status="
                             "'retired', retired_at=? WHERE id=? AND "
                             "status='active';", (K._utc_now_str(),
                                                  int(argv[0]))).rowcount
        if not n:
            raise Refused('no active row #%s' % argv[0])
        _out(as_json, {'id': int(argv[0]), 'action': 'retired'},
             ['RETIRED\t#%s' % argv[0]])
    elif verb == 'availability-add':
        argv, replace = K._pop_flag(argv, '--replace')
        argv, anytime = K._pop_flag(argv, '--anytime')
        argv, dom = K._pop_opt(argv, '--domain')
        argv, days = K._pop_opt(argv, '--days')
        argv, start = K._pop_opt(argv, '--from')
        argv, end = K._pop_opt(argv, '--to')
        argv, note = K._pop_opt(argv, '--note')
        argv, src = K._pop_opt(argv, '--source')
        argv, anchor, said = K._pop_anchor(argv)
        if argv or not dom or not days or (anytime and (start or end)) or (
                not anytime and not (start and end)):
            _usage(K, verb)
        res = availability_add(K, conn, dom, days, start, end, note, replace,
                               src or 'chat')
        _anchor_after(K, conn, anchor, said, 'availability_window',
                      res['id'], src)
        _out(as_json, res, ['%s\t#%d\t%s\t%s' % (
            res['action'].upper(), res['id'], ','.join(res['weekdays']),
            'anytime' if res['anytime'] else '%s-%s' % (res['start'],
                                                        res['end']))])
    elif verb == 'availability-list':
        argv, allr = K._pop_flag(argv, '--all')
        argv, dom = K._pop_opt(argv, '--domain')
        if argv:
            _usage(K, verb)
        rows = availability_list(K, conn, dom, allr)
        _out(as_json, rows, ['#%d\t%s\t%s\t%s\t[%s]' % (
            r['id'], r['domain'], ','.join(r['weekdays']),
            'anytime' if r['anytime'] else '%s-%s' % (r['start'], r['end']),
            r['status']) for r in rows] or ['NO_WINDOWS'])
    elif verb == 'resource-equipment':
        argv, set_ = K._pop_opt(argv, '--set')
        argv, add = K._pop_opt(argv, '--add')
        argv, rm = K._pop_opt(argv, '--remove')
        argv, src = K._pop_opt(argv, '--source')
        argv, anchor, said = K._pop_anchor(argv)
        if len(argv) != 1 or not argv[0].isdigit():
            _usage(K, verb)
        res = resource_equipment(K, conn, argv[0], set_, add, rm,
                                 src or 'chat')
        _anchor_after(K, conn, anchor, said, 'ledger_resource', res['id'],
                      src)
        _out(as_json, res, ['EQUIPMENT\t#%d\t%s\t%s' % (
            res['id'], res['name'], ','.join(res['equipment']) or '-')])
    elif verb == 'resource-place-link':
        argv, new_place = K._pop_flag(argv, '--new-confirmed')
        argv, place = K._pop_opt(argv, '--place')
        argv, address = K._pop_opt(argv, '--address')
        argv, lat = K._pop_opt(argv, '--lat')
        argv, lng = K._pop_opt(argv, '--lng')
        if len(argv) != 1 or not argv[0].isdigit() or (
                (lat is None) != (lng is None)):
            _usage(K, verb)
        r = _resource_row(conn, argv[0])
        if r['kind'] != 'env':
            raise Refused('resource #%s is %s, not env' % (argv[0],
                                                          r['kind']))
        venue = r['detail'].get('venue') or 'other'
        pl = K._resource_place_resolve(conn, venue, r['name'], place,
                                       address, lat, lng, new_place)
        if pl.get('near'):
            K._near_exit('place', pl['query'], pl['near'])
        if pl.get('error'):
            raise Refused(pl['error'])
        if not pl.get('place_id'):
            raise Refused('no base place on file for a home resource')
        conn.execute("UPDATE ledger_resource SET place_id=? WHERE id=?;",
                     (pl['place_id'], r['id']))
        if as_json:
            K._place_coord_register_lines(conn)
        res = {'id': r['id'], 'name': r['name'],
               'place_id': pl['place_id'],
               'place_action': pl['place_action'],
               'coord_needed': bool(pl.get('coord_needed'))}
        _out(as_json, res, ['LINKED\t#%d\t%s\tplace=%s(%s)' % (
            r['id'], r['name'], pl['place_id'], pl['place_action'])])
    elif verb == 'constraint-care':
        argv, clear = K._pop_flag(argv, '--clear')
        argv, key = K._pop_opt(argv, '--key')
        argv, care = K._pop_opt(argv, '--care')
        argv, anchor, said = K._pop_anchor(argv)
        cid = argv[0] if len(argv) == 1 and argv[0].isdigit() else None
        if (argv and cid is None) or len(argv) > 1 or (
                clear == (care is not None)):
            _usage(K, verb)
        res = constraint_care(K, conn, cid, key, care, clear)
        _anchor_after(K, conn, anchor, said, 'ledger_constraint', res['id'],
                      'chat')
        _out(as_json, res, ['CARE\t#%d\t%s\t%s' % (
            res['id'], res['key'], ','.join(
                c['drill'] for c in res['care'] or []) or '-')])
    elif verb == 'diet-target-set':
        argv, from_config = K._pop_flag(argv, '--from-config')
        argv, deficit = K._pop_opt(argv, '--deficit')
        argv, surplus = K._pop_opt(argv, '--surplus')
        argv, protein = K._pop_opt(argv, '--protein')
        argv, src = K._pop_opt(argv, '--source')
        argv, style = K._pop_opt(argv, '--style')
        argv, pmode = K._pop_opt(argv, '--protein-mode')
        argv, anchor, said = K._pop_anchor(argv)
        if argv:
            _usage(K, verb)
        res = diet_target_set(K, conn, deficit, surplus, protein,
                              from_config, src or 'chat', style, pmode)
        if anchor:
            for k, cid in sorted(res['ledger'].items()):
                anchor_put(K, conn, anchor[0], '%s.%s' % (anchor[1], k),
                           'ledger_constraint', cid, said, src or 'chat')
        _out(as_json, res, ['DIET_TARGET\t%s' % '\t'.join(
            '%s=%s' % (k, res[k]) for k in (
                ('deficit_kcal', 'surplus_kcal', 'protein_g')
                + tuple(k for k in ('diet_style', 'protein_mode')
                        if res[k] is not None)))])
        if not as_json and not from_config:
            day = K.datetime.date.today().isoformat()
            targets = K.meal_targets(conn, day)
            if targets:
                K._emit_diet_consultation(conn, targets['kcal'])
    else:
        _usage(K, verb)
