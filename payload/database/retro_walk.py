"""Kit-owned daily card walk, persisted per agent/day in config.

The composer is the only source of item order. Progress acknowledges the
current (kind,id), never a title, and finish refuses an unfinished walk.
No schema change; no task/event disposition is invented by this cursor.
"""
import datetime
import hashlib
import json
import os
import re
import shlex
import sqlite3
import subprocess
import sys


ASK_RESULTS = ('recorded', 'skipped', 'discussed')


def key(agent, day):
    return 'retro_walk.' + agent + '.' + day


def read(conn, agent, day):
    row = conn.execute('SELECT value FROM config WHERE key=?', (key(agent, day),)).fetchone()
    return json.loads(row[0]) if row else None


def put(conn, agent, day, state):
    conn.execute('INSERT INTO config(key,value) VALUES(?,?) '
                 'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                 (key(agent, day), json.dumps(state, ensure_ascii=False)))


def packet(raw, day):
    if not isinstance(raw, dict) or raw.get('date') != day or not isinstance(raw.get('body'), str):
        raise ValueError('INVALID_CARD')
    items = raw.get('items')
    if not isinstance(items, list):
        raise ValueError('INVALID_WALK')
    seen = set()
    for item in items:
        if (not isinstance(item, dict) or item.get('kind') not in ('task', 'event', 'module', 'experience')
                or not isinstance(item.get('id'), str) or not item['id']
                or not isinstance(item.get('text'), str) or not item['text']):
            raise ValueError('INVALID_ITEM')
        if 'ask' in item:
            ask = item['ask']
            labels = [c.get('label') for c in ask.get('choices', [])] if isinstance(ask, dict) else None
            if (item['kind'] != 'module' or not labels or not all(isinstance(l, str) and l for l in labels)
                    or len(set(labels)) != len(labels)):
                raise ValueError('INVALID_ASK')
        identity = (item['kind'], item['id'])
        if identity in seen:
            raise ValueError('DUPLICATE_ITEM')
        seen.add(identity)
    digest = hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    state = dict(date=day, card_id=digest, items=items, body=raw['body'], cursor=0,
                 phase='ready', results=[])
    if raw.get('opener'):
        state['opener'] = raw['opener']
    if 'entry_mode' in raw:
        if raw['entry_mode'] not in ('reply', 'automatic'):
            raise ValueError('INVALID_ENTRY_MODE')
        state.update(entry_mode=raw['entry_mode'], first_step=raw.get('first_step', ''),
                     first_step_sent=False)
    return state


def _skip_resolved_waits(state):
    if state is None:
        return
    resolved = set(state.get('resolved_wait_ids', []))
    while state['cursor'] < len(state['items']):
        item = state['items'][state['cursor']]
        if item['kind'] != 'task' or item['id'] not in resolved:
            break
        state['cursor'] += 1


def view(state):
    if state is None:
        return dict(status='NEED_CARD', next='Run retro-walk D open to compose this date once.')
    state = dict(state)
    _skip_resolved_waits(state)
    out = dict(state)
    cursor = state['cursor']
    out['current'] = state['items'][cursor] if cursor < len(state['items']) else None
    out['remaining'] = sum(i['kind'] != 'task' or i['id'] not in state.get('resolved_wait_ids', [])
                           for i in state['items'][cursor:])
    out['can_finish'] = cursor == len(state['items']) and state['phase'] != 'stopped'
    out['status'] = ('STOPPED' if state['phase'] == 'stopped' else 'CLOSED' if state['phase'] == 'closed'
                     else 'READY_TO_CLOSE' if out['can_finish'] else 'WALK')
    if state.get('talk') and out['current'] and out['status'] == 'WALK':
        out['status'] = 'TALK'
    out['next'] = ('Relay current only; record the owner disposition with the normal kit verb, '
                   'then retro-walk D advance KIND ID RESULT. Re-read current; do not jump to tomorrow or close.'
                   if out['current'] else 'Walk exhausted. Stage 4 may plan tomorrow, then retro-walk D finish.')
    if out['current'] and out['current'].get('ask') and state['phase'] == 'active':
        out['next'] = ('The kit already posted this question with its own keyboard; do not repeat it. '
                       'When the owner taps or types one of its labels run retro-walk %s answer LABEL '
                       '(it records the answer and posts the next question). If it reports TALK, finish '
                       'that short conversation, then run retro-walk %s resume.' % (state['date'], state['date']))
    elif state.get('first_step_sent') and cursor == 0:
        out['next'] = ('The first step was already sent immediately after the card. '
                       'Act on the owner answer for current, then advance that kind/id; '
                       'do not repeat the question. ' + out['next'])
    if state['phase'] in ('stopped', 'closed'):
        out['next'] = 'Session ended. Do not restart or consume remaining rows without the owner.'
    return out


def apply(conn, agent, day, operation, args=(), raw=None, output=None, kit=None):
    """One transaction per cursor transition; stale/out-of-order writes fail."""
    conn.execute('BEGIN IMMEDIATE')
    try:
        state = read(conn, agent, day)
        import retro_correction as correction
        before = json.loads(json.dumps(state))
        _skip_resolved_waits(state)
        deliver = False
        if operation in ('save', 'send'):
            new = packet(raw, day)
            if state and state['card_id'] == new['card_id']:
                return view(state)
            if state and state['phase'] in ('active', 'closed', 'stopped'):
                raise ValueError('WALK_ALREADY_STARTED')
            # A question printed in the card is already an offer. Claim it
            # with the saved walk, before delivery, never during dry render.
            reviews = [i for i in new['items'] if i['kind'] == 'experience'
                       and i.get('next_question', {}).get('field') == 'reaction']
            if reviews:
                import course_review
                import experience_retro
                import kit_life
                roots = {str(r['id']): r for r in experience_retro.listing(kit_life, conn, day)}
                for item in reviews:
                    course_review.claim(conn, roots[item['id']], 'daily', kit_life.now_utc())
            state = new
        elif state is None:
            raise ValueError('NEED_CARD')
        elif operation == 'undo':
            if args:
                raise ValueError('UNDO_TAKES_NO_ARGUMENTS')
            state = correction.pop(conn, key(agent, day), state)
        elif operation == 'back':
            if args or not state['results']:
                raise ValueError('NO_PREVIOUS_ITEM')
            previous = state['results'].pop()
            cursor = next((i for i, item in enumerate(state['items'])
                           if (item['kind'], item['id']) == (previous['kind'], previous['id'])), None)
            if cursor is None:
                raise ValueError('INVALID_WALK')
            state['cursor'] = cursor
            state['phase'] = 'active'
            state.pop('talk', None)
            state.pop('owner_stop', None)
            state.pop('answer_output', None)
            state['first_step_sent'] = False
        elif operation == 'reopen':
            if args or state['phase'] not in ('closed', 'stopped'):
                raise ValueError('WALK_NOT_ENDED')
            state['phase'] = 'active'
            state.pop('owner_stop', None)
        elif operation == 'open':
            if state['phase'] == 'ready':
                state['phase'] = 'active'
        elif operation == 'advance':
            if state['phase'] != 'active' or len(args) != 3:
                raise ValueError('WALK_NOT_ACTIVE: open first; give KIND ID RESULT')
            current = view(state)['current']
            if not current or (current['kind'], current['id']) != tuple(args[:2]):
                raise ValueError('WALK_OUT_OF_ORDER')
            if current['kind'] == 'experience' and args[2] not in ('skipped', 'deferred'):
                import experience_retro
                import kit_life
                roots = experience_retro.listing(kit_life, conn, day)
                if any(str(r['id']) == current['id'] and r['pending'] for r in roots):
                    raise ValueError('EXPERIENCE_DETAILS_PENDING: refresh; ask one missing field')
            if args[2] not in ('done', 'recorded', 'skipped', 'deferred', 'discussed', 'removed'):
                raise ValueError('INVALID_RESULT')
            state['results'].append(dict(kind=args[0], id=args[1], result=args[2]))
            state['cursor'] += 1
            deliver = _settle_current(kit, state) is not None
        elif operation in ('post', 'answer', 'resume'):
            deliver = _ask_step(state, operation, args, output, kit)
        elif operation == 'finish':
            if not view(state)['can_finish']:
                raise ValueError('WALK_INCOMPLETE')
            state['phase'] = 'closed'
        elif operation == 'stop':
            if len(args) != 1 or not args[0].strip():
                raise ValueError('STOP_REQUIRES_OWNER_WORDS')
            state.update(phase='stopped', owner_stop=args[0])
        elif operation != 'show':
            raise ValueError('INVALID_OPERATION')
        if state is not None:
            put(conn, agent, day, state)
        if operation == 'send':
            if args:
                raise ValueError('SEND_TAKES_NO_BUTTON')
            import kit_life
            push = kit_life._photo_push_sh()
            if not push:
                raise ValueError('RETRO_TRANSPORT_UNAVAILABLE')
            # Keep the writer lock through delivery. A failed send rolls back
            # both the course claim and walk; two runners cannot ask twice.
            subprocess.run([push, '--text', state['body']],
                           check=True, capture_output=True, text=True, timeout=60)
            if state.get('entry_mode') == 'automatic' and view(state)['current']:
                state['phase'] = 'active'
                if _ask_current(state):
                    if _settle_current(kit, state) is not None:
                        _post_current(state)
                else:
                    if not state.get('first_step'):
                        raise ValueError('MISSING_FIRST_STEP')
                    subprocess.run([push, '--text', state['first_step']],
                                   check=True, capture_output=True, text=True, timeout=60)
                state['first_step_sent'] = True
                put(conn, agent, day, state)
        if deliver:
            # Same rule as `send`: the writer lock is held through delivery, so
            # a failed post rolls the cursor back and the question is re-askable.
            _post_current(state)
        if operation not in ('undo', 'show'):
            correction.remember(conn, key(agent, day), before, state)
        if state is None:
            correction.put(conn, key(agent, day), None)
        return view(state)
    finally:
        if sys.exc_info()[0]:
            conn.rollback()
        else:
            conn.commit()


def _ask_current(state):
    """The current item when it carries a question payload, else None."""
    cur = view(state)['current']
    return cur if cur and cur.get('ask') else None


def _settle_current(kit, state):
    """The question to post next, after its `recheck` (ask row contract v2).

    A current question that carries `recheck` is re-evaluated once, right
    before it is posted, so it sees every answer recorded before it. Provider
    stdout: empty = unchanged, `drop` = skip it (result `skipped`), an
    `ask<TAB>json` row = post that text/choices instead. Any failure leaves
    the question as composed. Returns the current question or None; closes the
    walk when the last question was dropped."""
    while True:
        cur = _ask_current(state)
        if cur is None or not cur['ask'].get('recheck') or cur.get('rechecked'):
            break
        cur['rechecked'] = True
        try:
            got = _run_choice(kit, cur['ask'].get('base'), cur['ask']['recheck'])
        except (ValueError, OSError, subprocess.SubprocessError):
            break
        if got == 'drop':
            state['results'].append(dict(kind=cur['kind'], id=cur['id'], result='skipped'))
            state['cursor'] += 1
            continue
        if got.startswith('ask\t'):
            new = _recheck_ask(got.split('\t', 1)[1], cur)
            if new:
                cur['text'], cur['ask']['choices'] = new
        break
    if state['cursor'] == len(state['items']) and state['items'] and all(i.get('ask') for i in state['items']):
        state['phase'] = 'closed'
        return None
    return _ask_current(state)


def _recheck_ask(raw, cur):
    """(text, choices) of a recomputed ask row for the same question id, else None."""
    import retro_sections
    new = retro_sections.ask_row(raw)
    if not new or cur['id'].split(':', 2)[-1] != new['id']:
        return None
    return new['text'], new['choices']


def _post_current(state):
    """Send the current question as its own message, keyboard posted by code
    (push.sh --strict --buttons-json). Without the strict lane the question goes
    as plain text; the typed label is matched the same way."""
    import kit_life
    push = kit_life._photo_push_sh()
    if not push:
        raise ValueError('RETRO_TRANSPORT_UNAVAILABLE')
    cur = _ask_current(state)
    rows = [[{'text': c['label'], 'callback_data': 'opt:' + c['label']} for c in cur['ask']['choices']]]
    caps = subprocess.run([push, '--capabilities'], capture_output=True, text=True, timeout=60)
    cmd = [push, '--text', cur['text']]
    if caps.returncode == 0 and 'strict' in caps.stdout.split():
        cmd = [push, '--strict', '--text', cur['text'], '--buttons-json',
               json.dumps(rows, ensure_ascii=False, separators=(',', ':'))]
    subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=60)


def _run_choice(kit, base, cmd):
    """Run a choice's answer command. `kit:<verb ...>` is a kit verb; anything
    else is a provider command whose first word stays inside the provider dir."""
    if cmd.startswith('kit:'):
        argv = [os.path.join(kit.SCRIPT_DIR, 'kit_life.sh')] + shlex.split(cmd[4:])
        cwd = None
    else:
        argv = shlex.split(cmd)
        root = os.path.realpath(base or '')
        exe = os.path.realpath(os.path.join(root, argv[0]))
        if not base or os.path.commonpath([root, exe]) != root or not os.path.isfile(exe):
            raise ValueError('ASK_COMMAND_OUTSIDE_PROVIDER')
        argv[0], cwd = exe, root
    got = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=30,
                         env=dict(os.environ, KIT_RETRO_LANE='daily', KIT_RETRO_MODE='answer'))
    if got.returncode != 0:
        raise ValueError('ASK_COMMAND_FAILED: ' + (got.stderr.strip() or got.stdout.strip())[-300:])
    return got.stdout.strip()


def choice_for(state, label):
    """(current question, chosen choice) for an owner label, read-only."""
    cur = _ask_current(state) if state and state['phase'] == 'active' else None
    if cur is None:
        raise ValueError('NO_CURRENT_QUESTION')
    choice = next((c for c in cur['ask']['choices'] if c['label'] == label), None)
    if choice is None:
        raise ValueError('UNKNOWN_LABEL: ' + label)
    return cur, choice


def answer_command(kit, conn, agent, day, args):
    """Run the chosen label's command BEFORE the cursor transaction: a kit-verb
    command writes the same database, so it must not run under the writer lock.
    Returns its stdout ('' when the choice carries no command)."""
    if len(args) != 1:
        raise ValueError('answer requires the tapped label')
    state = read(conn, agent, day)
    if state is None:
        raise ValueError('NEED_CARD')
    _skip_resolved_waits(state)
    cur, choice = choice_for(state, args[0])
    return _run_choice(kit, cur['ask'].get('base'), choice['cmd']) if choice.get('cmd') else ''


def _ask_step(state, operation, args, output=None, kit=None):
    """post / answer / resume on a walk whose current item is a question.
    Returns True when the next question must be delivered after the save."""
    if state['phase'] == 'ready' and operation == 'post':
        state['phase'] = 'active'
    if state['phase'] != 'active':
        raise ValueError('WALK_NOT_ACTIVE')
    cur = _ask_current(state)
    if cur is None:
        raise ValueError('NO_CURRENT_QUESTION')
    if operation == 'post':
        return _settle_current(kit, state) is not None
    if operation == 'resume':
        if state.get('talk') != [cur['kind'], cur['id']]:
            raise ValueError('NOTHING_TO_RESUME')
        result = 'discussed'
    else:
        if len(args) != 1:
            raise ValueError('answer requires the tapped label')
        _cur, choice = choice_for(state, args[0])
        state['answer_output'] = output or ''
        if choice.get('then') == 'talk':
            state['talk'] = [cur['kind'], cur['id']]
            state['results'].append(dict(kind=cur['kind'], id=cur['id'], result='discussed-open', label=args[0]))
            return False
        result = 'recorded'
    state.pop('talk', None)
    state['results'] = [r for r in state['results'] if r.get('result') != 'discussed-open']
    state['results'].append(dict(kind=cur['kind'], id=cur['id'], result=result))
    state['cursor'] += 1
    if state['cursor'] == len(state['items']) and all(i.get('ask') for i in state['items']):
        state['phase'] = 'closed'
        return False
    return _settle_current(kit, state) is not None


def task_menu(kit, out):
    """Resolve each task card's labels from the active locale, including overrides."""
    current = out.get('current')
    if current and current['kind'] == 'task':
        labels = [kit._i18n('retro.daily.btn.' + name, fallback)
                  for name, fallback in (('done', 'Done'), ('tomorrow', 'Tomorrow'),
                                         ('later', 'Unschedule'), ('drop', 'Delete'))]
        if current.get('age_clock') != 'step.updated_at/local-date':
            current['question'] = kit._i18n('retro.daily.ask.done', 'Did you do it?')
            out['next'] += (' First relay current.question: unchecked means unknown, not unfinished. '
                            'Only after the owner says it was not done, use the existing disposal ask/options.')
        current['buttons'] = labels
        current['options'] = '[[OPTIONS: ' + ' | '.join(labels) + ']]'
        out['next'] += (' For this task relay current.options verbatim as the last line of '
                        'every disposal card (after completion is checked). These localized labels override skill examples. '
                        'For a requested clock move use task-postpone with the exact owner '
                        'words; its parser resolves the next occurrence without an AM/PM question.')
    return out


def reply_context(db_path, agent, prompt, now=None):
    """Bind explicit reactions and clock moves to one active walk, read-only."""
    import course_review
    import kit_life
    import retro_session
    words = prompt.rsplit(retro_session.END, 1)[-1].strip()
    conn = sqlite3.connect('file:' + db_path + '?mode=ro', uri=True)
    try:
        states = [json.loads(row[0]) for row in conn.execute(
            "SELECT value FROM config WHERE key LIKE ?", ('retro_walk.' + agent + '.%',))]
        bound = retro_session.resolve(prompt, now=now)
        if bound and bound['status'] == 'READY' and retro_session.reply_card(prompt):
            offered = next((s for s in states if s['date'] == bound['retro_date']
                            and s.get('phase') == 'ready'), None)
            if offered:
                opener = offered.get('opener')
                target = (' The opener already asked about %s id=%s. Interpret the owner reply '
                          'as its reaction and record it with %s before resuming the ordered walk.' %
                          (opener['kind'], opener['id'],
                           ('experience-enrich ' + opener['id'] + ' reaction=... verbatim=...'
                            if opener['kind'] == 'experience' else
                            'retro-item-set ' + opener['id'] + ' OWNER_WORDS')) if opener else
                          ' The count opener binds no individual task. A general reply must not '
                          'complete an arbitrary task; resolve a named task before recording its disposition.')
                return ('[daily-retro] ' + bound['next'] + target +
                        ' The reply continues the offered retro. Do not ask whether to start. '
                        'An unchecked task is unknown; ask whether it was done before disposal.')
        states = [s for s in states if s.get('phase') == 'active']
        if len(states) != 1:
            return ''
        state = states[0]
        asked = _ask_current(state)
        if asked and words in [c['label'] for c in asked['ask']['choices']]:
            return ('[daily-retro] The owner answered the posted question with a button label. FIRST run ' +
                    shlex.join(['database/kit_life.sh', 'retro-walk', state['date'], 'answer', words]) +
                    '. It records the answer and posts the next question itself; do not ask it again.')
        return ('[daily-retro] Active retro date ' + state['date'] +
                ('; the first step was already posted after the card. Act on its answer '
                 'before advancing; do not repeat it'
                 if state.get('first_step_sent') and state['cursor'] == 0 else '') +
                '. Interpret the owner message. Use experience-enrich EVENT_ID for '
                'reported visits or reactions, task-postpone TASK_ID WHEN for a requested move, '
                'or the current issued disposition. Keep each existing id; never require a stock sentence.')
    finally:
        conn.close()


def cli(kit, argv):
    if not 1 <= len(argv) <= 5:
        kit._err('usage: retro-walk YYYY-MM-DD [open|advance KIND ID RESULT|finish|stop "owner words"|save|post|answer LABEL|resume]')
    day = argv[0]
    try:
        if datetime.date.fromisoformat(day).isoformat() != day:
            raise ValueError()
    except ValueError:
        kit._err('retro-walk requires YYYY-MM-DD')
    operation = argv[1] if len(argv) > 1 else 'show'
    agent = kit._pin_agent()
    conn = kit.event_conn()
    try:
        if operation == 'show':
            out = view(read(conn, agent, day))
        else:
            raw = json.load(sys.stdin) if operation in ('save', 'send') else None
            if operation == 'open':
                import wait_resolution
                conn.execute('BEGIN IMMEDIATE')
                wait_resolution.reconcile(kit, conn, kit._facade_agent())
                conn.commit()
            if operation == 'open' and read(conn, agent, day) is None:
                # For typed starts / scheduled retro tasks with no prior cron
                # card, compose once with the identical production composer.
                script = os.path.join(os.path.dirname(kit.SCRIPT_DIR), 'routines', 'bundle', 'daily-retro.sh')
                rendered = subprocess.run(['bash', script, '--dry', '--json'], check=True,
                                          capture_output=True, text=True,
                                          env=dict(os.environ, BRIEF_FAKE_TODAY=day))
                apply(conn, agent, day, 'save', raw=json.loads(rendered.stdout))
            shown = answer_command(kit, conn, agent, day, argv[2:]) if operation == 'answer' else None
            out = apply(conn, agent, day, operation, argv[2:], raw, output=shown, kit=kit)
        print(json.dumps(task_menu(kit, out), ensure_ascii=False))
    except (ValueError, subprocess.CalledProcessError) as exc:
        kit._err(str(exc))
    finally:
        conn.close()
