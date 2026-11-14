"""Clock continuation for captured meals, using the shared ask/reminder lane.

No inferred occurrence clocks. State lives in config and satiety_answer.state;
the record's declared instant is a block attribute (date-only block invariant).
"""
import datetime as dt
import json
import re
import sqlite3
import sys
from contextlib import closing
from zoneinfo import ZoneInfo

import meal_slots

QUESTION = '지금 드시나요, 아니면 언제쯤 드셨나요?'
CHOICES = [('now', '지금'), ('manual', '직접 입력')]
CARD_RELAY = ('MEAL_TIME_CARD_RELAY For a time answer, send only the printed card '
              'between MEAL_CARD_BEGIN and MEAL_CARD_END VERBATIM inside ONE '
              'code block (```), preserving every space and line. Then relay '
              'any printed SATIETY_ASK with its options last. '
              'When TARGET_OFFER is present, end with its text and two '
              'TARGET_BUTTON labels in order; '
              'do not repeat the previous recording confirmation. No fullness '
              'question printed means do not invent one. Never print machine tags. '
              'This overrides generic MEAL_CARD_RELAY/SATIETY_RELAY order.')
SENT_RELAY = ('MEAL_TIME_CARD_SENT The code-block card and any fullness question '
              'with buttons were delivered together. Reply exactly NO_PUSH; '
              'do not repeat the card, question, options or confirmation.')
RELAY = ('MEAL_TIME_RELAY Relay RESOLVED then MEAL_TIME_ASK verbatim in ONE reply; '
         'put its [[OPTIONS]] last. No card or fullness yet. Multiple items in '
         'this event: combine the recorded foods and ask once after the last item. '
         'Run the printed answer command; manual waits for the typed clock, '
         'using that same command with the typed value. Never print machine tags.')


def zone(K):
    return ZoneInfo(K.agent_conf.conf_str('AGENT_TIMEZONE', 'Asia/Seoul'))


def instant(K, value):
    return K._satiety_instant(value)


def iso(value):
    return value.astimezone(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def load(conn, eid):
    row = conn.execute('SELECT value FROM config WHERE key=?', ('meal_time.'+str(eid),)).fetchone()
    return json.loads(row[0]) if row else {}


def save(conn, eid, state):
    conn.execute('INSERT OR REPLACE INTO config(key,value) VALUES(?,?)',
                 ('meal_time.'+str(eid), json.dumps(state, ensure_ascii=False)))


def foods(K, conn, eid):
    return conn.execute('SELECT DISTINCT m.id,m.date,m.meal,m.name FROM '+K.MEALS_V32+
        " m JOIN record r ON r.source_table='block' AND r.source_id=m.id "
        'JOIN record_event re ON re.record_id=r.id WHERE re.event_id=? ORDER BY m.id', (eid,)).fetchall()


def parse(K, day, value):
    value = str(value).strip()
    if value in ('now', '지금', '방금'):
        at = instant(K, K.now_utc())
        if at.astimezone(zone(K)).date().isoformat() != day:
            raise ValueError('now does not match the meal day')
        return at, 'current'
    match = re.fullmatch(r'(?:(오전|오후)\s*)?(\d{1,2})(?::(\d{2})|시(?:\s*(\d{1,2})분?)?)\s*(반)?', value)
    if match:
        period, hour, colon, minute, half = match.groups()
        hour, minute = int(hour), 30 if half else int(colon or minute or 0)
        if period:
            if not 1 <= hour <= 12:
                raise ValueError('clock hour must be 1..12 with 오전/오후')
            hour = hour % 12 + (12 if period == '오후' else 0)
        value = day+'T%02d:%02d:00' % (hour, minute)
    try:
        at = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if at.tzinfo is None:
            at = at.replace(tzinfo=zone(K))
    except ValueError:
        raise ValueError('meal clock must be HH:MM, N시, or an ISO instant')
    if at.astimezone(zone(K)).date().isoformat() != day:
        raise ValueError('meal clock and occurrence day disagree')
    if at > instant(K, K.now_utc()):
        raise ValueError('eaten meal cannot be in the future')
    return at, 'recalled'


def stated(K, day, note, occurred_at):
    # Capture supplies an occurrence instant explicitly. Notes are audit text.
    return (instant(K, occurred_at), 'recalled') if occurred_at else None


def resolved(K, conn, eid):
    rows = foods(K, conn, eid)
    slot = meal_slots.label(rows[0][2])
    particle = '을' if slot and 0xAC00 <= ord(slot[-1]) <= 0xD7A3 and (ord(slot[-1])-0xAC00) % 28 else '를'
    return ', '.join(r[3] for r in rows)+'으로 '+slot+particle+' 기록했어요.'


def question(K, eid, manual=False):
    choices = [] if manual else CHOICES
    return ['MEAL_TIME_ASK\t%s\t%s\t%s' % (eid, QUESTION, K._ask_choices().choices_field(choices)),
            K._ask_choices().options_line('MEAL_TIME_ASK', eid, choices), RELAY]


def sibling(K, conn, eid, rows):
    """Another event of the same eating occasion that already owns the clock
    question (pending/open/manual) or its answer. Each food may open its own event,
    so a meal logged in pieces must share one question and one answer."""
    mine = {r[0] for r in rows}
    ids, captured_ids = set(), set()
    # An answered recalled clock may be hours before capture. The next food
    # entered alongside it still belongs to that capture occasion.
    for capture in (False, True):
        group = next((g for g in K._satiety_occasions(conn, rows[0][1], capture=capture)
                      if mine & {r[0] for r in g}), [])
        ids.update(r[0] for r in group)
        if capture:
            captured_ids.update(r[0] for r in group)
    candidates, captured = [], []
    for key, value in conn.execute("SELECT key,value FROM config WHERE key LIKE 'meal_time.%'"):
        other = int(key.split('.', 1)[1])
        state = json.loads(value)
        if other == eid or state.get('join') or not state.get('status'):
            continue
        other_ids = {r[0] for r in foods(K, conn, other)}
        if ids & other_ids:
            candidates.append((other, state))
            if captured_ids & other_ids:
                captured.append((other, state))
    # A recalled clock can bridge a prior, separately captured meal in the
    # declared-time groups. Prefer this capture occasion's clock owners.
    candidates = captured or candidates
    if len({state['at'] for _, state in candidates if state.get('at')}) > 1:
        return None  # Distinct declared meals logged together are ambiguous.
    return min(candidates, key=lambda item: (not bool(item[1].get('at')), item[0]),
               default=None)


def pending_question(K, conn, eid, *, once=False):
    state = load(conn, eid)
    if state.get('status') == 'pending':
        # A reservation is not an output receipt (including legacy state).
        return question(K, eid)
    if state.get('status') == 'open' and not once:
        turn = K._inbound_request_id()
        if not turn or turn == state.get('ask_turn'):
            # The agent relays once AFTER its last tool call in this turn.
            # Keep that final output self-contained; without turn context we
            # cannot establish that the owner has seen the earlier output.
            return question(K, eid)
    return []


def begin(K, conn, eid, join=True, once=False):
    rows = foods(K, conn, eid)
    if not rows:
        return []
    state = load(conn, eid)
    if state.get('join'):
        return pending_question(K, conn, state['join'], once=once)
    event = conn.execute('SELECT note,occurred_at FROM event WHERE id=?', (eid,)).fetchone()
    clock = stated(K, rows[0][1], event[0], event[1])
    if clock and not state.get('at'):
        # An explicitly supplied occurrence clock remains authoritative.
        set_clock(K, conn, eid, *clock)
        return finish(K, conn, eid, once=once)
    if join and not state.get('at'):
        found = sibling(K, conn, eid, rows)
        if found and found[1].get('at'):
            state = dict(found[1])
            save(conn, eid, state)
        elif found:
            save(conn, eid, dict(status='pending', join=found[0]))
            conn.commit()
            return pending_question(K, conn, found[0], once=once)
    if state.get('at'):
        # A later food in the same meal inherits the exact answered clock.
        set_clock(K, conn, eid, instant(K, state['at']), state['basis'], schedule=False)
        return finish(K, conn, eid, once=once)
    if state.get('status') in ('pending', 'open', 'manual'):
        return pending_question(K, conn, eid, once=once)
    save(conn, eid, dict(status='pending'))
    conn.commit()
    return question(K, eid)


def emit_begin(K, conn, eid, *, once=False):
    lines = begin(K, conn, eid, once=once)
    for line in lines:
        print(line)
    asked = next((int(line.split('\t')[1]) for line in lines
                  if line.startswith('MEAL_TIME_ASK\t')), None)
    if asked is not None:
        # Do not suppress a retry if building/printing/flushing the question
        # failed. OPEN means the complete relay reached stdout, not merely
        # that begin() reserved it. No notification carrier is needed here.
        sys.stdout.flush()
        if not conn.in_transaction:
            conn.execute('BEGIN IMMEDIATE')
        state = load(conn, asked)
        if state.get('status') in ('pending', 'open') and not state.get('join'):
            state.update(status='open', ask_turn=K._inbound_request_id())
            save(conn, asked, state)
        conn.commit()


def set_clock(K, conn, eid, at, basis, schedule=True):
    rows = foods(K, conn, eid)
    if not rows:
        raise ValueError('meal event has no recorded food')
    day, slot = rows[0][1:3]
    stamp = iso(at)
    conn.execute('UPDATE event SET occurred_on=?,occurred_at=? WHERE id=?', (day, at.astimezone(zone(K)).isoformat(), eid))
    for bid, *_ in rows:
        conn.execute("INSERT OR REPLACE INTO block_attr VALUES(?,'meal_occurred_at',?,'user')", (bid, stamp))
        conn.execute("UPDATE record SET ts=? WHERE source_table='block' AND source_id=?", (stamp, bid))
        conn.execute('UPDATE measure SET observed_at=? WHERE block_id=?', (stamp, bid))
    data = K._satiety_state_load(conn=conn)
    key, ids = K._satiety_context(conn, data, day, slot)
    entry = data['meal_times'].setdefault(key, {})
    entry['occasion_ids'] = ids
    if entry.get('fullness') not in K.SATIETY_LEVELS:
        entry.update(basis=basis, eaten_at=stamp, time_event_id=eid)
        entry['minutes_since_meal'] = max(0, round((instant(K, K.now_utc())-at).total_seconds()/60, 1))
    if schedule and entry.get('fullness') not in K.SATIETY_LEVELS:
        entry.pop('asked_ids', None)
        entry.pop('reminder_due', None)
    K._satiety_state_save(data, conn=conn, commit=False)
    save(conn, eid, dict(status='answered', at=stamp, basis=basis, occasion=key))
    conn.commit()


def ask(K, date, slot, key, entry, required=True):
    slot = meal_slots.label(slot)
    question = (K._i18n('meal.satiety.ask_current',
                'After you finish {slot}, take your time and tell me how full you felt.').format(slot=slot)
                if entry['basis'] == 'current'
                else slot+' 다 드셨을 때 포만감은 어땠어요?')
    return K.satiety_ask_lines(date, slot, required, question=question, occasion=key)


def send_card(K, conn, day, eid, lines):
    """Bypass prose regeneration using the existing strict text carrier.

    Card fences/spaces and the question travel in one message. A missing or
    failed carrier falls back to the explicit fenced relay contract.
    """
    import start_card
    push = K._photo_push_sh()
    if not push:
        return False
    text = '```\n'+K.meal_card_render(conn, day)+'\n```'
    args = ['--strict', '--text', text]
    if lines:
        args[2] += '\n\n'+lines[0].split('\t')[4]
        choices = [(token, K.satiety_label(token)) for token in K.SATIETY_LEVELS]
        if lines[0].split('\t')[3] == 'optional':
            choices.append((K.SATIETY_SKIP, K._i18n('meal.satiety.skip', 'Skip')))
        buttons = [[dict(text=label,
                        callback_data='opt:meal-fullness:%s:%s' % (eid, token))]
                   for token, label in choices]
        args += ['--buttons-json', json.dumps(buttons, ensure_ascii=False)]
    rc, ack = start_card._push(push, args)
    return not rc and bool(ack.get('message_id'))


def finish(K, conn, eid, *, push_card=False, once=False):
    # once: plain (non-experience) foods keep the one-ask-per-occasion rule of
    # the satiety store; a captured event re-asks after its last added item.
    rows = foods(K, conn, eid)
    day, slot = rows[0][1:3]
    state = load(conn, eid)
    data = K._satiety_state_load(conn=conn)
    key = state['occasion']
    entry = data['meal_times'][key]
    manage = K._manage_on(conn, 'module_meal')
    if (manage and not K.meal_targets_set(conn)
            and K._config_stored(conn, K._MEAL_TARGET_OFFERED_KEY) is None):
        # The one-time offer owns this turn's buttons. Leave asked_ids unset
        # so the next eligible write can ask about fullness as before.
        K._emit_meal_card(conn, day, offer=True)
        return [CARD_RELAY]
    lines = []
    if (manage
            and entry.get('fullness') not in K.SATIETY_LEVELS
            and (not set(entry.get('asked_ids') or []).intersection(entry['occasion_ids']) if once
                 else set(entry.get('asked_ids') or []) != set(entry['occasion_ids']))):
        # An estimated food keeps the REQUIRED ask (no skip); exact ones stay optional.
        required = any(K.lookup_receipt_grade(conn, r[3]) == 'estimate' for r in rows)
        lines = ask(K, day, slot, key, entry, required)
        entry['asked_ids'] = entry['occasion_ids']
        entry.pop('reminder_due', None)
        K._satiety_state_save(data, conn=conn)
    if push_card and send_card(K, conn, day, eid, lines):
        return [SENT_RELAY]
    K._emit_meal_card(conn, day)
    return lines+[CARD_RELAY]


def answer(K, args):
    if len(args) != 2:
        raise ValueError('meal-time <event id> <now|manual|clock>')
    eid, value = int(args[0]), args[1].strip()
    with closing(K.get_conn()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        state = load(conn, eid)
        if not state:
            raise ValueError('no pending meal clock')
        if state.get('at'):
            return  # A duplicate/stale button never moves the meal or resets its timer.
        if state.get('join'):
            eid = state['join']  # Stale button from a joined food: the root owns the answer.
            state = load(conn, eid)
            if state.get('at'):
                return
        rows = foods(K, conn, eid)
        if value in ('manual', '직접 입력'):
            save(conn, eid, dict(status='manual'))
            for line in question(K, eid, manual=True):
                print(line)
            return
        at, basis = parse(K, rows[0][1], value)
        set_clock(K, conn, eid, at, basis)
        for other, in conn.execute("SELECT key FROM config WHERE key LIKE 'meal_time.%'").fetchall():
            joined = int(other.split('.', 1)[1])
            if load(conn, joined).get('join') == eid and foods(K, conn, joined):
                set_clock(K, conn, joined, at, basis, schedule=False)
        for line in finish(K, conn, eid, push_card=True):
            print(line)


def alerts(K, conn, now_ts, scope):
    import satiety_store
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='satiety_answer'").fetchone():
        return []
    if not scope.accepts(K._health_agent(conn)) or not K._manage_on(conn, 'module_meal'):
        return []
    now = dt.datetime.fromtimestamp(now_ts, dt.timezone.utc)
    out = []
    for key, entry in satiety_store.load(conn)['meal_times'].items():
        due = instant(K, entry.get('reminder_due'))
        if not due or now < due or entry.get('fullness') in K.SATIETY_LEVELS or entry.get('asked_ids'):
            continue
        eid = entry.get('time_event_id')
        if not eid or not foods(K, conn, eid):
            continue
        day, slot = foods(K, conn, eid)[0][1:3]
        lines = ask(K, day, slot, key, entry)
        text = lines[0].split('\t')[4]
        choices = [(t, K.satiety_label(t)) for t in K.SATIETY_LEVELS]
        buttons = [[dict(text=label, callback_data='opt:meal-fullness:%s:%s' % (eid, token))]
                   for token, label in choices]
        out.append(dict(key='meal-satiety:'+key, kind='meal_satiety', alert='start',
            header_time=0, lead_min=0, hhmm='', title=text, lines=[text],
            links=[], helper=True, buttons=buttons, occasion=key, event_id=eid))
    return out


def deliver(K, db_path, alert, now, push=None):
    """Existing strict reminder carrier; a failed send remains due for retry."""
    import start_card
    push = push if push is not None else K._photo_push_sh()
    if not push:
        return False
    with sqlite3.connect(db_path, timeout=5) as conn:
        conn.execute('BEGIN IMMEDIATE')
        data = K._satiety_state_load(conn=conn)
        entry = data['meal_times'].get(alert['occasion'], {})
        if (not K._manage_on(conn, 'module_meal') or entry.get('fullness') in K.SATIETY_LEVELS
                or entry.get('asked_ids') or not entry.get('reminder_due')):
            return True
        if instant(K, entry['reminder_due']) > instant(K, now):
            return False
        rc, ack = start_card._push(push, ['--strict', '--text', alert['title'],
            '--buttons-json', json.dumps(alert['buttons'], ensure_ascii=False)])
        if rc or not ack.get('message_id'):
            return False
        entry['asked_ids'] = entry['occasion_ids']
        entry.pop('reminder_due', None)
        K._satiety_state_save(data, conn=conn, commit=False)
    return True


def fullness(K, args):
    if len(args) != 2:
        raise ValueError('meal-fullness <event id> <satiety token>')
    with closing(K.get_conn()) as conn, conn:
        state = load(conn, int(args[0]))
        rows = foods(K, conn, int(args[0]))
        if not state.get('occasion') or not rows:
            raise ValueError('meal time is unresolved')
        day, slot = rows[0][1:3]
    K.cli_meal_satiety([day, slot, args[1], '--occasion', state['occasion']])
