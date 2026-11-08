#!/usr/bin/env python3
"""start-tap-context.py -- UserPromptSubmit hook for the task/routine start
card (database/start_card.py; declared in the kit's pack-manifest.json
hooks[], wired by pack_install.sh -- DGN-1496).

A tap on a start-card button reaches the agent as button text, possibly
wrapped in multiline outbound/reply context by the bridge. In a
session that never saw the card, a bare "done" reads as chat and "help me
start" as a request for the agent's feature list (agent rehearsal S5,
2026-10-11). The tapped bubble is known, though: the bridge writes it to
.telegram_bot/inbound-context.json (source "callback", message_id) before
the turn starts.

One line or nothing: when this turn is a callback whose message_id is a
live start card AND the resolved label is one of its live button texts,
name the card's item and the verb to run first (start-tap). An open pending
cooking context gets an internal procedure pointer on every turn, based only
on state. Other turns are silent. The tap line is copy: i18n key remind.start.hook.tap in
service/kit_life/i18n/<lang>.json ({text}, {title}).

CONTRACT (fail-open, always): any error, missing file or missing DB prints
nothing and exits 0. DB reads only (mode=ro, else a query_only connection). The latest owner
turn receipt is written atomically for cooking provenance; it is not session state.
"""
import json
import os
import sqlite3
import sys

KEY = 'remind.start.hook.tap'
CARDS_KEY = 'task_start_cards'


def root():
    env = os.environ.get('DOGANY_INSTANCE_ROOT')
    if env and os.path.isdir(env):
        return env
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def lang(r):
    val = os.environ.get('AGENT_LANG')
    if not val:
        try:
            with open(os.path.join(r, 'config', 'agent.conf'), encoding='utf-8') as fh:
                for line in fh:
                    line = line.strip()
                    if line.startswith('export '):
                        line = line[7:].lstrip()
                    if line.startswith('AGENT_LANG='):
                        val = line.split('=', 1)[1].strip().strip('"\'')
                        break
        except OSError:
            pass
    return (val or 'ko').lower().split('_')[0].split('-')[0] or 'ko'


def copy(r, key=KEY):
    for code in (lang(r), 'ko'):
        try:
            with open(os.path.join(r, 'service', 'kit_life', 'i18n', code + '.json'),
                      encoding='utf-8') as fh:
                s = json.load(fh).get(key)
            if s:
                return s
        except (OSError, ValueError):
            continue
    return None


def tapped_context(r):
    with open(os.path.join(r, '.telegram_bot', 'inbound-context.json'), encoding='utf-8') as fh:
        ctx = json.load(fh)
    if not isinstance(ctx, dict) or ctx.get('source') != 'callback':
        return {}
    return ctx


def tapped_label(ctx, payload):
    """Prefer callback metadata; prompt decoration is not callback identity.

    Older bridges omit callback_data: use their one-based option_index into
    the inbound keyboard, or (only without either field) a bare prompt.
    Every candidate is subsequently checked against the DB's live keyboard.
    """
    data = ctx.get('callback_data')
    if data is not None and data != '':
        return data[4:] if isinstance(data, str) and data.startswith('opt:') else ''
    keyboard, index = ctx.get('keyboard'), ctx.get('option_index')
    if keyboard is not None or index is not None:
        if isinstance(keyboard, dict):
            keyboard = keyboard.get('inline_keyboard')
        if not isinstance(keyboard, list) or type(index) is not int or index < 1:
            return ''
        buttons = []
        for row in keyboard:
            buttons.extend(row if isinstance(row, list) else [row])
        if index > len(buttons):
            return ''
        button = buttons[index - 1]
        return button.get('text', '') if isinstance(button, dict) else button
    return (payload.get('prompt') or '').strip() if isinstance(payload, dict) else ''


def live_card(r, message_id, text):
    """The card shown in bubble `message_id` whose live keyboard carries
    `text`, or None."""
    db = os.path.join(r, 'database', 'kit_life.db')
    if not os.path.exists(db):
        return None
    row = None
    for uri in ('file:%s?mode=ro' % db.replace('?', '%3F'), None):
        # mode=ro on the WAL crew DB fails with "unable to open database
        # file" when the hook runs under the CLI (measured 2026-10-11, S5);
        # a plain connection pinned query_only still never writes a row
        try:
            conn = (sqlite3.connect(uri, uri=True, timeout=5) if uri
                    else sqlite3.connect(db, timeout=5))
        except sqlite3.Error:
            continue
        try:
            conn.execute('PRAGMA query_only = 1;')
            if text.startswith('pin:'):
                for (raw,) in conn.execute("SELECT value FROM config WHERE key LIKE 'day_pin_actions.%'"):
                    hit = json.loads(raw or '{}').get(text)
                    if hit:
                        live = conn.execute('SELECT 1 FROM timeblock WHERE ulid=? AND version=? AND settled_at IS NULL',
                                            (hit['ulid'], hit['version'])).fetchone()
                        return hit if live else None
                return None
            row = conn.execute('SELECT value FROM config WHERE key=?;',
                               (CARDS_KEY,)).fetchone()
            break
        except sqlite3.OperationalError:
            continue
        finally:
            conn.close()
    cards = json.loads(row[0]) if row and row[0] else {}
    for c in (cards.values() if isinstance(cards, dict) else []):
        if not isinstance(c, dict) or c.get('message_id') is None:
            continue
        if str(c['message_id']) == str(message_id) and text in (c.get('keyboard') or {}):
            return c
    return None


def session_agent(r):
    agent = os.environ.get('LIFEKIT_AGENT') or os.environ.get('DOGANY_AGENT_NAME')
    if not agent:
        try:
            with open(os.path.join(r, '.instance.conf')) as fh:
                for line in fh:
                    if line.startswith('DOGANY_AGENT_NAME='):
                        agent = line.split('=', 1)[1].strip().strip("\"'")
        except OSError:
            pass
    return (agent or 'ag').lower()


def main():
    try:
        payload = json.load(sys.stdin)
    except ValueError:
        return 0
    r = root()
    # Capture the bridge-owned turn before any routing or early return. The
    # cooking CLI compares exact words with this request-bound receipt.
    try:
        sys.path.insert(0, os.path.join(r, 'database'))
        import cooking_owner
        cooking_owner.record(r, payload.get('prompt') or '')
    except (OSError, ValueError, ImportError):
        pass
    # Pending context is independent of prompt wording and later hook routing.
    # Emit it before any early return, including another open question/card.
    db = os.path.join(r, 'database', 'kit_life.db')
    if os.path.isfile(db):
        try:
            import cooking_pending
            with sqlite3.connect('file:' + db + '?mode=ro', uri=True) as conn:
                line = cooking_pending.hook_context(conn, session_agent(r))
            if line:
                print(line)
        except (ImportError, ValueError, sqlite3.Error):
            pass
    regimen_deferred = None
    # Intake replies own their supplement/medication words before food capture.
    db = os.path.join(r, 'database', 'kit_life.db')
    if os.path.isfile(db):
        sys.path.insert(0, os.path.join(r, 'database'))
        try:
            import health_regimen
            import kit_life
            try:
                with open(os.path.join(r, '.telegram_bot', 'inbound-context.json')) as fh:
                    regimen_ctx = json.load(fh)
            except (OSError, ValueError):
                regimen_ctx = {}
            with sqlite3.connect('file:' + db + '?mode=ro', uri=True) as conn:
                line = health_regimen.hook_context(kit_life, conn, payload.get('prompt') or '', regimen_ctx)
            if line:
                regimen_deferred = line
        except (ImportError, ValueError, sqlite3.Error):
            pass
    # An open activity owns every input before generic task/photo routing.
    db = os.path.join(r, 'database', 'kit_life.db')
    if os.path.isfile(db):
        with sqlite3.connect(db, timeout=5) as conn:
            conn.execute('PRAGMA query_only=1')
            saved = conn.execute('SELECT value FROM config WHERE key=?',
                                 ('activity.session.' + session_agent(r),)).fetchone()
        active = json.loads(saved[0]) if saved else None
        if active and active.get('phase') not in ('closed', 'cancelled'):
            try:
                with open(os.path.join(r, '.telegram_bot', 'inbound-context.json')) as fh:
                    inbound = json.load(fh)
            except (OSError, ValueError):
                inbound = {}
            data = inbound.get('callback_data') or ''
            text = data[4:] if inbound.get('source') == 'callback' and data.startswith('opt:activity:') else (payload.get('prompt') or '')
            print('[cooking-session] An activity is open. Read core-log/reference/cooking.md. Interpret the owner message and choose a typed cooking action. Authenticated callback: cooking tap TOKEN. Prose: check-step/start-step/undo-step ID, delay SECONDS, change FILE, appliance --modes MODES, close or cancel. Questions keep all steps open; code posts the cards.')
            return 0
        if regimen_deferred:
            print(regimen_deferred)
        sys.path.insert(0, os.path.join(r, 'database'))
        try:
            import wait_resolution
            import kit_life
            import retro_session
            words = (payload.get('prompt') or '').rsplit(retro_session.END, 1)[-1].strip()
            with sqlite3.connect('file:' + db + '?mode=ro', uri=True) as conn:
                line = wait_resolution.reply_context(conn, words, kit_life._facade_agent())
            if line:
                print(line)
                return 0
        except (ImportError, ValueError, sqlite3.Error):
            pass
        # A reply to an offered course question owns its stop reactions,
        # including food words that are opinions rather than a new meal.
        sys.path.insert(0, os.path.join(r, 'database'))
        try:
            import course_review
            course_line = course_review.reply_context(db, payload.get('prompt') or '')
            if course_line:
                print(course_line)
                return 0
        except (ImportError, ValueError, sqlite3.Error):
            pass
        # An active retro binds stop opinions and time moves before generic
        # capture/meal routing. The helper reads state and emits commands only.
        try:
            import datetime
            import retro_walk
            import kit_life
            with open(os.path.join(r, '.telegram_bot', 'inbound-context.json')) as fh:
                inbound = json.load(fh)
            stamp = inbound.get('ts')
            now = (datetime.datetime.fromtimestamp(stamp, datetime.timezone.utc).isoformat()
                   if isinstance(stamp, (int, float)) else None)
            walk_line = retro_walk.reply_context(db, kit_life._pin_agent(),
                                                  payload.get('prompt') or '', now=now)
            if walk_line:
                print(walk_line)
                return 0
        except (ImportError, OSError, ValueError, sqlite3.Error):
            pass
    # Daily cron cards use a dated callback, and typed replies carry the
    # original bubble in outbound-context. Resolve both before generic taps.
    sys.path.insert(0, os.path.join(r, 'database'))
    try:
        import retro_session
        try:
            with open(os.path.join(r, '.telegram_bot', 'inbound-context.json')) as fh:
                retro_inbound = json.load(fh)
        except (OSError, ValueError):
            retro_inbound = {}
        line = retro_session.hook_context(payload.get('prompt') or '', retro_inbound)
        if line:
            print(line)
            return 0
    except ImportError:
        pass
    ctx = tapped_context(r)
    mid = ctx.get('message_id')
    text = tapped_label(ctx, payload)
    if not isinstance(text, str) or not text or '\n' in text or '\r' in text or mid is None:
        return 0
    is_pin = text.startswith('pin:')
    card = live_card(r, mid, text)
    if card is None:
        return 0
    line = copy(r, 'day.pin.hook.tap' if is_pin else KEY)
    if line:
        print(line.format(text=text, title=card.get('title') or ''))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
