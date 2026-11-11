"""Shared task-session frame: durable scope, revisioned buttons and code surfaces.

Domain runners own steps; this frame owns the single open activity per agent.
It uses the existing config store and the public sender/countdown/pin contracts.
No private trainer tables, bridge implementation or background process is used.
"""
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys

PREFIX = 'activity.session.'
SILENT = 'Reply exactly NO_PUSH. The session card was posted by code; never repeat it.'


def load(K, conn):
    raw = K._config_stored(conn, PREFIX + K._pin_agent())
    return json.loads(raw) if raw else None


def save(K, conn, state):
    K._config_put(conn, PREFIX + K._pin_agent(), json.dumps(state, ensure_ascii=False))


def create(kind, plan, block_id=None):
    return dict(id=secrets.token_hex(5), kind=kind, revision=0, phase='ready',
                plan=plan, block_id=block_id, completed=[], running={},
                started_at=None, current=None, message_id=None, chat_id=None)


def buttons(state, labels):
    buttons = [dict(text=label, callback_data='opt:activity:%s:%d:%s' % (
        state['id'], state['revision'], action)) for action, label in labels]
    if state.get('kind') == 'cooking':
        return [[b] for b in buttons[:3]]
    return [buttons[i:i + 4] for i in range(0, len(buttons), 4)]


def action(state, token, message_id):
    if state and state.get('kind') == 'cooking':
        import cooking_delivery
        scoped = cooking_delivery.action(state, token, message_id)
        if scoped:
            return scoped
    if not state or str(state.get('message_id')) != str(message_id):
        return None
    parts = token.split(':')
    if len(parts) != 4 or parts[:3] != ['activity', state['id'], str(state['revision'])]:
        return None
    return parts[3] if parts[3] in state.get('actions', []) else None


def inbound(K):
    try:
        return json.loads((Path(K._instance_root()) / '.telegram_bot/inbound-context.json').read_text())
    except (OSError, ValueError):
        return {}


def publish(K, conn, state, text, labels, *, push=None):
    """Edit one session bubble, preserving its pinned identity across steps.

    State advances before transport; a failed edit is recoverable with show,
    without executing the action twice. A stale visible keyboard is inert.
    """
    import start_card
    if not conn.in_transaction:
        conn.execute('BEGIN IMMEDIATE')
    current = load(K, conn)
    if current and (current['id'] != state['id'] or current['revision'] != state['revision']):
        conn.rollback()
        return dict(status='SPENT', next='NO_PUSH')
    rows = buttons(state, labels)
    state['actions'] = [a for a, _ in labels]
    state['text'], state['rows'] = text, rows
    save(K, conn, state)
    conn.commit()
    sender = push or K._photo_push_sh()
    if not sender:
        return dict(status='DELIVERY_RETRY', next='Run cooking show to retry the code card.')
    args = ['--strict', '--text', text]
    if rows:
        args += ['--buttons-json', json.dumps(rows, ensure_ascii=False)]
    if state.get('message_id') is not None:
        args += ['--edit', str(state['message_id'])]
    if state.get('chat_id') is not None:
        args += ['--chat', str(state['chat_id'])]
    rc, ack = start_card._push(sender, args)
    ok = rc in ((0, 8) if state.get('message_id') is not None else (0, 6))
    if ok and (state.get('message_id') is not None or ack.get('message_id') is not None):
        state['message_id'] = state.get('message_id') or ack['message_id']
        state['chat_id'] = state.get('chat_id') or ack.get('chat_id')
        conn.execute('BEGIN IMMEDIATE')
        current = load(K, conn)
        if not current or current['id'] != state['id'] or current['revision'] != state['revision']:
            conn.rollback()
            return dict(status='SPENT', next='NO_PUSH')
        current['message_id'], current['chat_id'] = state['message_id'], state['chat_id']
        save(K, conn, current)
        conn.commit()
        if state.get('kind') != 'cooking':
            pin(K, text, clear=state['phase'] in ('closed', 'cancelled'))
        return dict(status=state['phase'].upper(), revision=state['revision'], next=SILENT)
    return dict(status='DELIVERY_RETRY', next='Run cooking show; never re-execute the previous action.')


def pin(K, text, clear=False, rows=None):
    path = Path(K._instance_root()) / 'routines/status-footer.py'
    if not path.is_file():
        return 'status footer unavailable'
    args = [sys.executable, '-B', str(path), '--pin-clear' if clear else '--pin-set', 'cooking']
    if not clear:
        args += ['--ttl', '86400']
    if rows is not None and not clear:
        args += ['--buttons-json', json.dumps(rows, ensure_ascii=False)]
    try:
        result = subprocess.run(args, input='' if clear else text, capture_output=True, text=True, timeout=20)
        if result.returncode:
            if rows is not None:
                pin(K, text, clear=clear)
            return result.stderr.strip() or 'domain pin buttons unsupported'
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)


SESSION_PIN_TTL = 12 * 3600


def pin_symmetry_candidate(K, default=''):
    """Return the close-shape candidate for one caller.

    An unset/empty variable means ``default`` (callers opt in to a default;
    the frame's own default stays the old path).  Explicit off/0/false is
    always the old path.
    """
    value = os.environ.get('LIFEKIT_PIN_SYMMETRY', '').strip()
    if value == '':
        value = default
    return '' if value.lower() in ('', '0', 'off', 'false') else value


def finish_session_pin(K, slug, text, rows=None, summary='', chat_id=None, default=''):
    """Show a composer's finished pin, then retire the framework pin.

    This is the kit session close contract.  Session implementations supply
    their normal pin composer output; the frame owns ordering and transport so
    later session kinds do not grow a caller-specific close implementation.
    The feature is deliberately candidate-only until the owner selects a
    visible shape.
    """
    candidate = pin_symmetry_candidate(K, default)
    if not text or candidate not in ('session-pin-first', 'session-pin-summary'):
        return session_pin(K, 'close', slug)
    error = session_pin(K, 'update', slug, text, rows)
    if error == 'NO_LEASE':
        error = session_pin(K, 'open', slug, text, rows)
    if error:
        return error
    sender = K._photo_push_sh()
    if not sender:
        return 'session finish sender unavailable'
    body = text + (('\n' + summary) if candidate == 'session-pin-summary' and summary else '')
    args = [sender, '--strict', '--text', body]
    if chat_id is not None:
        args += ['--chat', str(chat_id)]
    try:
        result = subprocess.run(args,
                                capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    if result.returncode not in (0, 6):
        return result.stderr.strip() or 'session finish pin delivery failed'
    return session_pin(K, 'close', slug)


def session_pin(K, action, slug, text='', rows=None):
    """The framework's SECOND pinned message (status-footer --session-pin,
    framework DGN-2262): open | update | close one slug. It never touches the
    day-card store. -> None on success, 'NO_LEASE' when update found no live
    lease, else the error text. There is no fallback to the day-card pin: a
    framework without the session slot reports the error instead.
    """
    path = Path(K._instance_root()) / 'routines/status-footer.py'
    if not path.is_file():
        return 'status footer unavailable'
    args = [sys.executable, '-B', str(path), '--session-pin', action, slug]
    if action != 'close':
        args += ['--ttl', str(SESSION_PIN_TTL)]
        if rows:
            args += ['--buttons-json', json.dumps(rows, ensure_ascii=False)]
    try:
        result = subprocess.run(args, input='' if action == 'close' else text,
                                capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc)
    if result.returncode == 0:
        return None
    if result.returncode == 2 and action == 'update':
        return 'NO_LEASE'
    return result.stderr.strip() or 'session pin unsupported by this framework'


def countdown(K, state, step, seconds=None):
    """The bridge hold timer primitive, also used by workout and cooking v2.

    Unlink means cancel. Completion is acknowledged by the owner, never inferred
    from a clock: a timer cannot assert that food has reached its done cue.
    """
    directory = Path(K._instance_root()) / '.telegram_bot/countdown'
    path = directory / ('activity-%s-%s.json' % (state['id'], step['id']))
    if seconds is None:
        path.unlink(missing_ok=True)
        path.with_suffix('.json.tmp').unlink(missing_ok=True)
    else:
        if not 0 < seconds <= 86400:
            raise ValueError('timer duration outside bridge range')
        directory.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix('.json.tmp')
        tmp.write_text(json.dumps(dict(seconds=seconds, label=step['name'],
            cadence=30 if seconds >= 600 else 5 if seconds < 180 else 10), ensure_ascii=False))
        os.replace(tmp, path)


def task_open(K, conn, block_id):
    state = load(K, conn)
    return bool(state and state.get('block_id') == block_id and
                state['phase'] not in ('closed', 'cancelled'))
