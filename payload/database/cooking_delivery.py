"""Durable per-step cards and checks on the public sender/footer contracts.

Card revisions are local: another lane advancing never spends an untouched
card. State is committed before transport and each ack before the next send.
"""
import json
import subprocess
from pathlib import Path

import activity_session as frame
import cooking_card
import cooking_schedule as schedule
import start_card


def action(state, token, message_id):
    parts = token.split(':')
    if len(parts) != 4 or parts[:2] != ['activity', state['id']]:
        return None
    for card in (list(state.get('step_cards', {}).values()) + list(state.get('check_cards', {}).values())
                 + list(state.get('notice_cards', {}).values())):
        if str(card.get('message_id')) == str(message_id) and str(card.get('revision')) == parts[2] and parts[3] in card.get('actions', []):
            return parts[3]
    if parts[2] == str(state.get('pin_revision', state['revision'])) and parts[3] in state.get('pin_actions', []):
        return parts[3]
    return None


def fresh(K, conn, state, store=frame):
    if not conn.in_transaction:
        conn.execute('BEGIN IMMEDIATE')
    live = store.load(K, conn)
    if not live or live['id'] != state['id'] or live['revision'] != state['revision']:
        conn.rollback()
        return False
    return True


def send(K, conn, state, key, text, labels, collection='step_cards', new_message=False, store=frame):
    if not fresh(K, conn, state, store):
        return False
    cards = state.setdefault(collection, {})
    old = cards.get(key, {})
    if not new_message and old.get('text') == text and old.get('labels') == [list(x) for x in labels] and old.get('message_id') and old.get('delivered'):
        return True
    card = dict(old)
    if new_message:
        # Persist a missing receipt for a new post; a retry must not edit
        # the previous bubble after a rejected replacement send.
        card.pop('message_id', None)
    if not old or old.get('labels') != [list(x) for x in labels]:
        card['revision'] = old.get('revision', 0) + 1
    card.update(text=text, labels=[list(x) for x in labels], actions=[a for a, _ in labels])
    if len(labels) > 3:
        raise ValueError('cooking cards support at most three buttons')
    rows = frame.buttons(dict(id=state['id'], revision=card['revision'], kind='cooking'), labels)
    card['rows'] = rows
    card['delivered'] = False
    cards[key] = card
    store.save(K, conn, state)
    conn.commit()
    sender = K._photo_push_sh()
    if not sender:
        return False
    args = ['--strict', '--text', text]
    if rows:
        args += ['--buttons-json', json.dumps(rows, ensure_ascii=False)]
    if card.get('message_id') is not None and not new_message:
        args += ['--edit', str(card['message_id'])]
    if state.get('chat_id') is not None:
        args += ['--chat', str(state['chat_id'])]
    rc, ack = start_card._push(sender, args)
    if rc not in ((0, 8) if card.get('message_id') and not new_message else (0, 6)):
        return False
    card['message_id'] = ack.get('message_id') if new_message else card.get('message_id') or ack.get('message_id')
    if card['message_id'] is None or not fresh(K, conn, state, store):
        return False
    card['delivered'] = True
    store.save(K, conn, state)
    conn.commit()
    return True


def due_steps(state, now):
    """Cards owed by the active frontier, including resumed legacy snapshots."""
    if state['phase'] != 'active' or state.get('editing'):
        return []
    eligible = set(state.get('schedule', {}).get('frontier', []))
    if state['source'].get('target_at'):
        steps = {s['id']: s for s in state['plan']['steps']}
        eligible = {sid for sid in eligible if state['schedule']['slots'][sid]['start'] <= now + .001
                    or schedule.timer_released(state, steps[sid])}
    eligible.update(sid for sid in state.get('step_started', {}) if sid not in state['completed'])
    if state.get('current'):
        eligible.add(state['current'])
    steps = [s for s in cooking_card.ordered(state) if s['id'] in eligible and s['id'] not in state['completed']]
    return sorted(steps, key=lambda s: s['id'] != state.get('current'))


def needs_delivery(state, now):
    for step in due_steps(state, now):
        card = state.get('step_cards', {}).get(step['id'], {})
        if not (card.get('delivered') and card.get('message_id') and card.get('actions')):
            return True
    return False


def publish_due(K, conn, state, copy, now):
    # Deliver the owner's next action before editing ancillary surfaces. A
    # failed progress-table edit must never starve a newly due step card.
    for step in due_steps(state, now):
        card = state.get('step_cards', {}).get(step['id'], {})
        if not (card.get('delivered') and card.get('message_id') and card.get('actions')):
            text, labels = cooking_card.step_card(copy, state, step, now)
            # A spent legacy/undone card is no longer an actionable receipt.
            # Post its replacement at the new due clock so it can notify.
            repost = bool(card.get('delivered') and not card.get('actions'))
            if not send(K, conn, state, step['id'], text, labels, new_message=repost):
                return False
    return True


def publish(K, conn, state, copy, now):
    active = state['phase'] == 'active' and not state.get('editing')
    # Keep one per-dish table throughout the session, including check replies.
    for key, card in list(state.get('progress_cards', {}).items()):
        if key not in {r['id'] for r in state['source']['recipes']}:
            if not send(K, conn, state, key, '\uc694\ub9ac\ubcc4 \uc9c4\ud589\uc0ac\ud56d\uc744 \uc0c8 \uce74\ub4dc\ub85c \uc62e\uacbc\uc5b4\uc694.', [], 'progress_cards'):
                return False
    if state.get('started_at') is not None:
        for recipe in state['source']['recipes']:
            step = next((s for s in state['plan']['steps'] if s['dish'] == recipe['id']), None)
            if step and not send(K, conn, state, recipe['id'], cooking_card.dish_progress(copy, state, step, now), [], 'progress_cards'):
                return False
        state.pop('progress_new_message', None)
    eligible = {s['id'] for s in due_steps(state, now)}
    existing = {s['id'] for s in state['plan']['steps']}
    for sid, card in list(state.get('step_cards', {}).items()):
        if sid not in existing and (card.get('actions') or not card.get('delivered')):
            if not send(K, conn, state, sid, card['text'], []):
                return False
    for step in cooking_card.ordered(state):
        n = state['plan']['steps'].index(step)
        sid = step['id']
        if active and sid in eligible or sid in state.get('step_cards', {}):
            text, labels = cooking_card.step_card(copy, state, step, now)
            # Keep a pin-preview card's normal controls across later publishes;
            # applying them still enforces the step's prerequisites.
            if not active:
                labels = []
            old = state.get('step_cards', {}).get(sid, {})
            if sid in state['completed'] and old and not old.get('message_id'):
                # Completion superseded an unsuccessful first delivery.
                # There is no old message to edit and no action left to post.
                del state['step_cards'][sid]
                frame.save(K, conn, state)
                conn.commit()
                continue
            if (sid not in eligible and sid not in state['completed']
                    and sid not in state.get('step_started', {})
                    and old.get('delivered') and not old.get('actions')):
                # Updating the schedule must not revive an old spent keyboard
                # ahead of its due clock or suppress that clock's new card.
                labels = []
            bar_only = False
            if sid in state['completed'] and old.get('delivered') and not old.get('actions'):
                text, labels = old['text'], []
                if cooking_card.heat_elapsed(state, step, now) is not None:
                    text = replace_heading(text, cooking_card.live_heading(copy, state, step, now))
            elif old.get('delivered') and old.get('labels') == [list(x) for x in labels] and (
                    sid in state['running'] and now < state['running'][sid] or
                    sid in state.get('step_started', {}) and step.get('kind') != 'unattended'):
                if cooking_card.heat_elapsed(state, step, now) is not None:
                    text = replace_heading(old['text'], cooking_card.live_heading(copy, state, step, now))
                    # The bar edit also refreshes the quoted next-step line, else it keeps the start-time "N min later".
                    boundary = state['step_started'][sid] + cooking_card.heat_elapsed(state, step, now)
                    text = refresh_quotes(text, cooking_card.step_card(copy, state, step, boundary)[0])
                    bar_only = True
                elif sid in state['running']:
                    text = old['text']
            elif cooking_card.heat_elapsed(state, step, now) is not None:
                text = replace_heading(text, cooking_card.live_heading(copy, state, step, now))
            changed = old.get('text') != text or old.get('labels') != [list(x) for x in labels] or not old.get('delivered')
            if state['phase'] in ('closed', 'cancelled'):
                text, labels = step['name'] + (' \uc644\ub8cc' if state['phase'] == 'closed' else ' \uc885\ub8cc'), []
            if not send(K, conn, state, sid, text, labels):
                return False
        check = state.setdefault('checks', {}).get(sid, {})
        if active and sid in state['running'] and step.get('kind') == 'unattended' and step.get('check_at_sec'):
            elapsed = now - state['step_started'][sid]
            # Persist each reminder's own message before marking it delivered.
            stage = 'overrun' if now >= state['running'][sid] else 'mid' if elapsed >= step['check_at_sec'] else None
            if stage and stage not in check:
                key = sid + ':' + stage
                text = step['check_cue'] if stage == 'mid' else copy['check_overrun'].format(cue=step['check_cue'], done=step['done_cue'])
                button = copy['check_button']
                if step.get('auto_check') == 'oven':
                    text = cooking_card.step_heading(copy, state, step, now) + '\n' + copy['oven_check_' + stage].format(emoji=cooking_card.dish(state, step)[0])
                    button = copy['oven_check_button']
                button = cooking_card.check_button(copy, state, step, button)
                if not send(K, conn, state, key, text, [('check_' + str(n) + '_' + stage, button)], 'check_cards'):
                    return False
                check[stage] = dict(delivered_at=now, acknowledged=False)
                state['checks'][sid] = check
                frame.save(K, conn, state)
                conn.commit()
    # Retire reminders when their food is done, cancelled, or superseded.
    for key, card in list(state.get('check_cards', {}).items()):
        sid = key.split(':')[0]
        retired = not active or sid not in state['running']
        if retired and not card.get('message_id'):
            # A failed old reminder has no bubble to retire. Never post it
            # after its step was completed or the session was closed.
            del state['check_cards'][key]
            frame.save(K, conn, state)
            conn.commit()
            continue
        if not card.get('delivered') and not retired:
            if not send(K, conn, state, key, card['text'], card['labels'], 'check_cards'):
                return False
        if (card.get('actions') or not card.get('delivered')) and retired:
            if not send(K, conn, state, key, '\ud655\uc778 \uc885\ub8cc' if not active else card['text'], [], 'check_cards'):
                return False
    for index, prompt in enumerate(state.get('stock_prompts', [])):
        if not send(K, conn, state, str(index), prompt, [], 'notice_cards'):
            return False
    for key, text in state.get('cooking_notices', {}).items():
        if not send(K, conn, state, key, text, [], 'notice_cards'):
            return False
    warning_keys = set()
    for hold in state.get('schedule', {}).get('holds', []):
        if not hold['warning']:
            continue
        name = next(r['name'] for r in state['source']['recipes'] if r['id'] == hold['dish'])
        tail = ord(name[-1]) - 0xac00
        particle = '\ub97c' if 0 <= tail <= 11171 and tail % 28 == 0 else '\uc744'
        text = copy['hold_warning'].format(dish=name, dish_object=name + particle)
        key = schedule.hold_key(hold)
        warning_keys.add(key)
        index = next(n for n, r in enumerate(state['source']['recipes']) if r['id'] == hold['dish'])
        labels = [('hold_keep_' + str(index), copy['hold_keep_button']),
                  ('hold_end_' + str(index), copy['hold_end_button'])]
        if not active or key in state.get('hold_decisions', {}):
            labels = []
        if not send(K, conn, state, key, text, labels, 'notice_cards'):
            return False
    # Retire the same question in place; never introduce new confirmation copy.
    for key, card in list(state.get('notice_cards', {}).items()):
        if key.startswith('hold_warning:') and key not in warning_keys and (card.get('actions') or not card.get('delivered')):
            if not send(K, conn, state, key, card['text'], [], 'notice_cards'):
                return False
    return publish_pin(K, conn, state, copy, now)


def replace_heading(text, heading):
    lines = text.split('\n')
    # Also upgrade a card delivered before titles were quoted.
    count = 2 if len(lines) > 1 and lines[0].startswith('> ') and lines[1].startswith('> ') else 1
    return heading + '\n' + '\n'.join(lines[count:])


def refresh_quotes(text, fresh):
    """Refresh the next-step note on legacy and grouped instruction cards.

    The heat heading is updated separately. Instructions are static; only the
    final quoted next-step note carries the changing relative clock.
    """
    quoted = next((line for line in reversed(fresh.split('\n'))
                   if line.startswith(('> ', '>^ '))), None)
    lines = text.split('\n')
    for i in range(len(lines)-1, -1, -1):
        if lines[i].startswith(('> ', '>^ ')) and quoted is not None:
            lines[i] = quoted
            break
    return '\n'.join(lines)


def due_heat(copy, state, now):
    if state.get('editing'):
        return False
    for step in state['plan']['steps']:
        sid = step['id']
        if sid in state.get('step_started', {}) and sid not in state['completed'] and cooking_card.heat_elapsed(state, step, now) is not None:
            card = state.get('step_cards', {}).get(sid, {})
            heading = cooking_card.live_heading(copy, state, step, now)
            if card.get('message_id') and not card.get('text', '').startswith(heading + '\n'):
                return True
    return False


PIN_SLUG = 'cooking'
CLOSE_DEFAULT = 'session-pin-summary'  # cooking close default when LIFEKIT_PIN_SYMMETRY is unset


def session_pin_available(K):
    """Probe the installed footer without invoking its legacy Stop-hook path."""
    path = Path(K._instance_root()) / 'routines/status-footer.py'
    try:
        source = path.read_text(encoding='utf-8')
    except (OSError, UnicodeError):
        return False
    # Like the day-card probe, inspect the CLI flag instead of a version stamp.
    return any(flag in source for flag in ("'--session-pin'", '"--session-pin"'))


def publish_pin(K, conn, state, copy, now):
    """The cooking overview lives in its own session pin (slug "cooking").

    Owner 2026-10-23: no pin before Start; the pin is a second pinned message,
    never stacked on the day card. Open at Start, update on every visible
    change (checks tick as progress arrives), close when the session ends.
    """
    if not fresh(K, conn, state):
        return False
    if not session_pin_available(K):
        # A session remains fully usable without the optional framework slot.
        # Clear old delivery state too, so a framework change cannot retain
        # an error or treat a later supported pin as already delivered.
        for key in ('pin_open', 'pin_delivered', 'pin_transition', 'pin_text',
                    'pin_actions', 'pin_rows', 'pin_revision', 'pin_delivery_error'):
            state.pop(key, None)
        frame.save(K, conn, state)
        conn.commit()
        return True
    closing = state['phase'] in ('closed', 'cancelled')
    started = state.get('started_at') is not None
    if not state.get('pin_open') and (closing or not started):
        # Nothing pinned yet: before Start there is no pin; an unstarted
        # session that ends leaves nothing to close.
        state.pop('pin_delivery_error', None)
        frame.save(K, conn, state)
        conn.commit()
        return True
    # The close candidate uses the exact same composer as the live pin.  At a
    # successful close every mark is complete; its overview is therefore the
    # finished state rather than a separately maintained recap.
    candidate = frame.pin_symmetry_candidate(K, CLOSE_DEFAULT)
    finished = state['phase'] == 'closed' and candidate in ('session-pin-first', 'session-pin-summary')
    text = cooking_card.pin_card(copy, state, now) if not closing or finished else ''
    transition = [state['phase'], state.get('current'), list(state['completed']), sorted(state['running']), state.get('pin_page', 0)]
    # A legacy reopen can retain the previous close's cached overview while
    # claiming an open pin. Text equality must not skip retiring that pin.
    if (state.get('pin_delivered') and state.get('pin_transition') == transition
            and state.get('pin_text') == text and not state.get('pin_delivery_error')
            and bool(state.get('pin_open')) == (not closing)):
        conn.commit()
        return True
    indices = {s['id']: n for n, s in enumerate(state['plan']['steps'])}
    pending = [(indices[s['id']], s) for s in cooking_card.ordered(state) if s['id'] not in state['completed']]
    page_size = 2 if len(pending) > 3 else 3
    page_count = max(1, (len(pending) + page_size - 1) // page_size)
    page = state.get('pin_page', 0) % page_count
    visible = pending[page * page_size:(page + 1) * page_size]
    labels = [] if closing else [('jump_' + str(n), s['name']) for n, s in visible]
    if not closing and page_count > 1:
        labels.append(('pin_next', copy['next_button']))
    state['pin_actions'] = [a for a, _ in labels]
    state['pin_revision'] = state['revision']
    state['pin_rows'] = frame.buttons(state, labels)
    frame.save(K, conn, state)
    conn.commit()
    if finished:
        elapsed = max(0, int((now - state['started_at']) // 60)) if state.get('started_at') is not None else 0
        summary = copy['close_total'].format(minutes=elapsed) if candidate == 'session-pin-summary' else ''
        error = frame.finish_session_pin(K, PIN_SLUG, text, summary=summary, chat_id=state.get('chat_id'), default=CLOSE_DEFAULT)
    elif closing:
        error = frame.session_pin(K, 'close', PIN_SLUG)
    else:
        error = frame.session_pin(K, 'update' if state.get('pin_open') else 'open', PIN_SLUG, text, state['pin_rows'])
        if error == 'NO_LEASE':
            error = frame.session_pin(K, 'open', PIN_SLUG, text, state['pin_rows'])
    if not fresh(K, conn, state):
        return False
    state['pin_text'] = text
    state['pin_delivered'] = not error
    state['pin_transition'] = transition
    if not error:
        state['pin_open'] = not closing
    if error:
        state['pin_delivery_error'] = error
    else:
        state.pop('pin_delivery_error', None)
    frame.save(K, conn, state)
    conn.commit()
    return True


def jump(K, state, index, copy, conn=None, now=None):
    step = state['plan']['steps'][index]
    card = state.get('step_cards', {}).get(step['id'])
    posted = False
    if (not card or not card.get('message_id')) and conn is not None:
        # A future step is reviewable from the pin without starting it.
        text, labels = cooking_card.step_card(copy, state, step, now)
        if not send(K, conn, state, step['id'], text, labels):
            return dict(status='DELIVERY_RETRY', next='NO_PUSH')
        card = state['step_cards'][step['id']]
        posted = True
    if not card or not card.get('message_id'):
        return dict(status='UPCOMING', reply=copy['waiting'], next='Say reply verbatim. Do not advance the session.')
    # Use a quoted reply when supported, otherwise post the card itself.
    sender = K._photo_push_sh()
    if not sender:
        return dict(status='DELIVERY_RETRY', next='NO_PUSH')
    try:
        caps = subprocess.run([sender, '--capabilities'], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return dict(status='DELIVERY_RETRY', next='NO_PUSH')
    if caps.returncode or 'reply-to' not in caps.stdout.split():
        if posted:
            return dict(status='JUMPED', next=frame.SILENT)
        # No quoted-reply sender (the framework push.sh has none): bring the
        # step card itself to the bottom of the chat as a new message.
        if conn is None:
            return dict(status='DELIVERY_RETRY', next='NO_PUSH')
        labels = [tuple(x) for x in card.get('labels', [])]
        if not send(K, conn, state, step['id'], card['text'], labels, new_message=True):
            return dict(status='DELIVERY_RETRY', next='NO_PUSH')
        return dict(status='JUMPED', next=frame.SILENT)
    args = ['--strict', '--text', copy['jump_reply'], '--reply-to', str(card['message_id'])]
    if state.get('chat_id') is not None:
        args += ['--chat', str(state['chat_id'])]
    rc, _ = start_card._push(sender, args)
    return dict(status='JUMPED' if rc in (0, 6) else 'DELIVERY_RETRY', next=frame.SILENT)
