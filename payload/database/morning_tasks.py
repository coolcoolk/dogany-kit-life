"""Morning continuation of tasks left unanswered by the evening retro.

Selection shares task-overdue's predicate; dispositions and keyboard delivery
use the existing kit verbs and retro walk transport. Evening state is read only.
"""
import datetime as dt
import json
import os
import shlex
import sqlite3
import subprocess

import retro_walk as walk


def key(agent, day):
    return 'morning_tasks.' + agent + '.' + day


def read(conn, agent, day):
    row = conn.execute('SELECT value FROM config WHERE key=?', (key(agent, day),)).fetchone()
    return json.loads(row[0]) if row else None


def put(conn, agent, day, state):
    conn.execute('INSERT INTO config(key,value) VALUES(?,?) '
                 'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                 (key(agent, day), json.dumps(state, ensure_ascii=False)))


def local_zone(kit):
    # conf_str lowercases values, but IANA zone names are case sensitive.
    # Use the same declared offset / zone fallback as the briefing's day card.
    name = os.environ.get('AGENT_TZ')
    if name is None:
        name = kit.agent_conf._raw('AGENT_TZ')
    return kit._day_tz(tz_name=name or None, tz_offset=kit._card_tz_offset())


def instant(value):
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00'))


def candidates(kit, conn, day, capped=True):
    today = dt.date.fromisoformat(day)
    zone = local_zone(kit)
    midnight = dt.datetime.combine(today, dt.time(), zone)
    boundary = midnight.astimezone(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    _, scope = kit.task_find_owning_agent([])
    rows = kit.task_overdue_rows(conn, boundary, scope)
    # A deferred/discussed evening task can still be open. The saved result,
    # rather than settlement alone, proves that the owner answered it.
    answered = {}
    for (raw,) in conn.execute("SELECT value FROM config WHERE key LIKE 'retro_walk.%'"):
        state = json.loads(raw)
        if state['date'] >= day:
            continue
        for result in state.get('results', []):
            if result['kind'] == 'task' and result['result'] != 'discussed-open':
                tid = str(result['id'])
                answered[tid] = max(answered.get(tid, ''), state['date'])
    prior = read(conn, kit._pin_agent(), day)
    morning_answered = {r['id'] for r in (prior or {}).get('results', [])}
    excluded = os.environ.get('BRIEF_EXCLUDE_TITLE_PREFIXES')
    if excluded is None:
        excluded = kit.agent_conf._raw('BRIEF_EXCLUDE_TITLE_PREFIXES')
    prefixes = [p.strip() for p in (excluded or '').split(',') if p.strip()]
    new, old = [], []
    for row in rows:
        tid = str(row[0])
        start = instant(row[3]).astimezone(zone)
        end = instant(row[4] or row[3]).astimezone(zone)
        if tid in morning_answered or answered.get(tid, '') >= start.date().isoformat():
            continue
        if any(row[2].lstrip().startswith(p) for p in prefixes):
            continue
        attrs = dict(conn.execute('SELECT attr_token,value FROM block_attr WHERE block_id=?',
                                  (row[0],)))
        if attrs.get('action_kind') == 'retro':
            continue
        settled = conn.execute('SELECT settled_by FROM timeblock WHERE id=?', (row[0],)).fetchone()
        if settled[0]:
            continue
        date_label = (kit._i18n('brief.tasks.yesterday', 'Yesterday')
                      if start.date() == today - dt.timedelta(days=1)
                      else f'{start.month}/{start.day}')
        clock = start.strftime('%H:%M') if row[5] == 'timed' else ''
        line = kit._i18n('brief.tasks.row', '[ ] {date} {clock}{title}').format(
            date=date_label, clock=clock + ' ' if clock else '', title=row[2])
        item = dict(id=tid, line=line, start_at=row[3], end_at=row[4])
        # An all-day end exactly at midnight belongs to the day just ended.
        end_day = (end - dt.timedelta(microseconds=1) if row[4] else end).date()
        (new if end_day == today - dt.timedelta(days=1) else old).append(item)
    return new + (old[:2] if capped else old), len(old[2:])


def compose(kit, conn, day):
    selected, rest = candidates(kit, conn, day)
    items = []
    header = kit._i18n('brief.tasks.notice', '☀️ You have {n} unfinished tasks.').format(n=len(selected))
    for index, row in enumerate(selected):
        choices = []
        for name, fallback, command in (
                ('retro.daily.btn.done', 'Done', ['task-done', row['id'], '--owner-chat']),
                ('brief.tasks.btn.today', 'Do today', ['task-reschedule', row['id'], day]),
                ('retro.daily.btn.later', 'Unschedule', ['task-undate', row['id']]),
                ('retro.daily.btn.drop', 'Delete', ['task-archive', row['id']])):
            choices.append(dict(label=kit._i18n(name, fallback), cmd='kit:' + shlex.join(command)))
        text = row['line'] + '\n' + kit._i18n('retro.daily.ask.done', 'Did you do it?')
        if index == 0 and len(selected) < 3:
            text = header + '\n' + text
        items.append(dict(id=row['id'], kind='module', text=text,
                          start_at=row['start_at'], end_at=row['end_at'], ask=dict(choices=choices)))
    body = ''
    if len(selected) >= 3:
        body = header + '\n' + '\n'.join(row['line'] for row in selected)
        if rest:
            body += '\n' + kit._i18n('retro.daily.more', 'and {n} more').format(n=rest)
    state = walk.packet(dict(date=day, items=items, body=body), day)
    return state


def refresh(kit, conn, state):
    """Drop tasks settled or moved elsewhere since the morning question."""
    valid = {r['id'] for r in candidates(kit, conn, state['date'], capped=False)[0]}
    # Check each saved identity against its original schedule too. A morning answer must never mutate a newly moved task.
    while state['cursor'] < len(state['items']):
        current = state['items'][state['cursor']]
        row = kit._task_fetch(conn, current['id'])
        if (current['id'] in valid and row and row[3] == current['start_at']
                and row[4] == current['end_at']):
            break
        state['results'].append(dict(kind='task', id=current['id'], result='skipped'))
        state['cursor'] += 1
    if state['cursor'] == len(state['items']):
        state['phase'] = 'closed'


def render(state):
    current = walk.view(state)['current']
    text = state['body'] if state['body'] and state['cursor'] == 0 else ''
    if current:
        text += ('\n\n' if text else '') + current['text']
        text += '\n[[OPTIONS: ' + ' | '.join(c['label'] for c in current['ask']['choices']) + ']]'
    return text


def cli(kit, argv):
    if len(argv) not in (1, 2, 3):
        kit._err('usage: morning-tasks YYYY-MM-DD [render|show|post|answer LABEL]')
    day, operation = argv[0], argv[1] if len(argv) > 1 else 'show'
    try:
        if dt.date.fromisoformat(day).isoformat() != day:
            raise ValueError('morning-tasks requires YYYY-MM-DD')
        if (operation not in ('render', 'show', 'post', 'answer')
                or (operation == 'answer' and len(argv) != 3)
                or (operation != 'answer' and len(argv) > 2)):
            raise ValueError('invalid morning-tasks operation/arguments')
        agent = kit._pin_agent()
        conn = kit.event_conn()
        try:
            state = read(conn, agent, day)
            issued = walk.view(state).get('current') if state else None
            if state:
                refresh(kit, conn, state)
            else:
                state = compose(kit, conn, day)
            if operation == 'render':
                text = render(state) if state['phase'] != 'closed' else ''
                if text:
                    print(text)
                return
            if operation == 'post':
                if state.get('posted') or not state['items'] or state['phase'] == 'closed':
                    return
                state['phase'] = 'active'
                conn.execute('BEGIN IMMEDIATE')
                latest = read(conn, agent, day)
                if latest and latest.get('posted'):
                    return
                if state['body']:
                    subprocess.run([kit._photo_push_sh(), '--text', state['body']],
                                   check=True, capture_output=True, text=True, timeout=60)
                walk._post_current(state)
                state['posted'] = True
                put(conn, agent, day, state)
                conn.commit()
            elif operation == 'answer':
                current = walk.view(state)['current']
                if issued and (not current or issued['id'] != current['id']):
                    put(conn, agent, day, state)
                    conn.commit()
                    if current:
                        walk._post_current(state)
                    raise ValueError('MORNING_TASK_CHANGED: answer the newly posted item')
                if not state.get('posted') or state['phase'] != 'active':
                    raise ValueError('NO_CURRENT_MORNING_TASK')
                current, choice = walk.choice_for(state, argv[2])
                # Same commands as evening task disposal. Do not hold the
                # cursor writer lock while a verb writes its own transaction.
                output = walk._run_choice(kit, None, choice['cmd'])
                state['results'].append(dict(kind='task', id=current['id'], result='recorded'))
                state['cursor'] += 1
                refresh(kit, conn, state)
                put(conn, agent, day, state)
                conn.commit()
                if state['phase'] != 'closed':
                    walk._post_current(state)
                state['answer_output'] = output
            out = walk.view(state)
            out['next'] = (f"Use morning-tasks {day} answer LABEL for the current posted task. "
                           'The kit applies its disposition and posts the next question itself.'
                           if out['current'] else 'Morning tasks complete; do not repeat the question.')
            print(json.dumps(out, ensure_ascii=False))
        finally:
            conn.close()
    except (ValueError, sqlite3.Error, subprocess.SubprocessError) as exc:
        kit._err(str(exc))


def reply_context(db_path, agent, prompt):
    """Bind the posted morning question before a stalled evening walk."""
    import retro_session
    words = prompt.rsplit(retro_session.END, 1)[-1].strip()
    with sqlite3.connect('file:' + db_path + '?mode=ro', uri=True) as conn:
        states = [json.loads(row[0]) for row in conn.execute(
            'SELECT value FROM config WHERE key LIKE ?', ('morning_tasks.' + agent + '.%',))]
        evenings = [json.loads(row[0]) for row in conn.execute(
            'SELECT value FROM config WHERE key LIKE ?', ('retro_walk.' + agent + '.%',))]
    active = [s for s in states if s.get('phase') == 'active' and s.get('posted')]
    replied = retro_session.reply_card(prompt)
    for state in sorted(active, key=lambda s: s['date'], reverse=True):
        current = walk.view(state)['current']
        if replied and current and current['text'] not in replied:
            continue
        # At the next evening's stage 2 the same labels belong to that walk.
        # Only an explicit reply to the morning card may override it.
        if not replied and any(e.get('phase') == 'active' and e['date'] >= state['date']
                               for e in evenings):
            continue
        if current and words in [c['label'] for c in current['ask']['choices']]:
            return ('[morning-tasks] The owner answered the posted morning task. FIRST run ' +
                    shlex.join(['database/kit_life.sh', 'morning-tasks', state['date'], 'answer', words]) +
                    '. It applies the existing task verb and posts the next question. Do not repeat it.')
    return ''
