"""One planning choice and durable stage alerts on the cooking heartbeat.

The agent interprets free text using its owner receipt. This module accepts a
structured choice only; button callbacks carry the choice directly. The owner
approved the optional-prep card and its three labels on 2026-11-02.
"""
import copy
import json
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import activity_session as frame
import cooking_card as card
import cooking_delivery as delivery
import cooking_schedule as schedule
from item_grouping import markdown_items

PREFIX = 'cooking.stages.'
COPY_KEY = 'cooking.stage_choice_copy'
LABELS = {'ingredients': '🧺 재료 준비', 'prep': '🔪 프렙',
          'cook': '🍳 요리', 'joined': '🍳 요리'}
BUTTONS = {'ingredients': '▶️ 재료 준비 시작', 'prep': '▶️ 프렙 시작',
           'cook': '▶️ 요리 시작', 'joined': '▶️ 요리 시작'}


def load(K, conn):
    raw = K._config_stored(conn, PREFIX + K._pin_agent())
    return json.loads(raw) if raw else None


def save(K, conn, state):
    K._config_put(conn, PREFIX + K._pin_agent(), json.dumps(state, ensure_ascii=False))


def send(K, conn, state, key, text, labels):
    sent = delivery.send(K, conn, state, key, text, labels, collection='stage_cards',
                         store=sys.modules[__name__])
    if conn.in_transaction:
        conn.commit()
    return sent


def identity(source):
    import cooking_session as C
    meal = copy.deepcopy(source)
    meal.pop('prep_mode', None)
    meal.pop('_prep_choices', None)
    meal.pop('_prep_pending', None)
    return C.digest(meal)


def same_meal(left, right, *, live_restart=False):
    """A re-plan changes amounts and clocks, not the dinner being cooked."""
    if not left or not right:
        return False
    if left.get('target_at') and right.get('target_at'):
        def day(source):
            value = source['target_at']
            return (datetime.fromtimestamp(value, ZoneInfo(source.get('timezone', 'Asia/Seoul'))).date()
                    if isinstance(value, (int, float)) else datetime.fromisoformat(value).date())
        if day(left) != day(right):
            return False
    if left.get('plan_id') and left.get('plan_id') == right.get('plan_id'):
        return True
    if left.get('plan_id') and right.get('plan_id') and not live_restart:
        return False
    return (sorted(r['id'] for r in left.get('recipes', [])) == sorted(r['id'] for r in right.get('recipes', []))
            and left.get('eaters', []) == right.get('eaters', []))


def allowed(source):
    return [(r['id'] + '-' + s['id'], s) for r in source['recipes']
            for s in r['steps'] if s['phase'] == 'prep' and s.get('ahead', 'never') == 'allowed']


def choice(K, conn, source, now=None):
    """Plan first; ask once, only for typed optional preparation."""
    if not source.get('target_at') or source.get('_stage_ids'):
        return None
    now = time.time() if now is None else now
    current = load(K, conn)
    same = current and not current.get('cancelled') and current['meal'] == identity(source)
    if same and current.get('stages') and '_prep_choices' not in current['source']:
        # Resume an already armed legacy meal with its original frozen source;
        # only a newly planned meal receives the new typed choice map.
        source.update(copy.deepcopy(current['source']))
        return None
    if same:
        state = current
    else:
        live = frame.load(K, conn)
        import cooking_session as C
        C.finish_completed(K, conn, live)
        if live and live['phase'] not in ('closed', 'cancelled'):
            return dict(status='ACTIVITY_OPEN', next='Continue or cancel the current activity before planning another meal.')
        if current and current.get('cancelled'):
            import cooking_session as C
            K._config_put(conn, C.PLANNED_PREFIX + K._pin_agent(), '{}')
        state = dict(frame.create('cooking', {}), meal=identity(source), source=copy.deepcopy(source),
                     at=now, stage_cards={}, prep_choices={})
        if (current and not current.get('cancelled') and same_meal(current.get('source'), source)
                and datetime.fromtimestamp(current['at'], ZoneInfo(source.get('timezone', 'Asia/Seoul'))).date()
                    == datetime.fromtimestamp(now, ZoneInfo(source.get('timezone', 'Asia/Seoul'))).date()):
            # Preserve delivered stage bubbles across pre-start edits. Spent
            # callbacks keep the old scope and cannot start the revised meal.
            state['stage_cards'] = {k: dict(v) for k, v in current.get('stage_cards', {}).items()
                                    if k in LABELS and v.get('message_id')}
    items = allowed(source)
    source['prep_mode'] = 'separate'
    source['_prep_choices'] = copy.deepcopy(state.get('prep_choices', {}))
    if not items or state.get('choice'):
        state['choice'] = state.get('choice') or 'automatic'
        state['source'] = copy.deepcopy(source)
        save(K, conn, state)
        conn.commit()
        if not cleanup_choices(K, conn, state):
            return dict(status='DELIVERY_RETRY', next='Run cooking plan PLAN.json.')
        return None
    source['_prep_pending'] = True
    state['source'] = copy.deepcopy(source)
    save(K, conn, state)
    conn.commit()
    import cooking_session as C
    if not C.post_plan(K, conn, source, now):
        return dict(status='DELIVERY_RETRY', next='Run cooking plan PLAN.json.')
    if state.get('individual'):
        if not individual_cards(K, conn, state):
            return dict(status='DELIVERY_RETRY', next='Run cooking plan PLAN.json.')
    else:
        text = '미리 해도 되는 프렙 %d개: ' % len(items) + ' · '.join(s['name'] for _, s in items)
        if not send(K, conn, state, 'choice', text,
                    [('prep_all_ahead', '모두 미리하기'), ('prep_all_cook', '모두 요리 때 같이하기'),
                     ('prep_individual', '하나씩 고르기')]):
            return dict(status='DELIVERY_RETRY', next='Run cooking plan PLAN.json.')
    return dict(status='NEEDS_PREP_MODE', next='Wait for scoped buttons or an owner-receipted structured prep-mode/prep-step answer; never classify words in code. No full cooking open.')


def cleanup_choices(K, conn, state):
    for key, old in list(state.get('stage_cards', {}).items()):
        if key == 'choice' or key.startswith('prep_choice_'):
            if (old.get('actions') or not old.get('delivered')) and not send(K, conn, state, key, old['text'], []):
                return False
    return True


def individual_cards(K, conn, state):
    old = state['stage_cards'].get('choice')
    if old and not send(K, conn, state, 'choice', old['text'], []):
        return False
    for index, (sid, step) in enumerate(allowed(state['source'])):
        key = 'prep_choice_' + str(index)
        if sid in state['prep_choices']:
            old = state['stage_cards'].get(key)
            if old and not send(K, conn, state, key, old['text'], []):
                return False
            continue
        text = '• %s (%s분)' % (step['name'], format(step['duration_sec'] / 60, 'g'))
        if not send(K, conn, state, key, text,
                    [('prep_ahead_' + str(index), '미리'), ('prep_cook_' + str(index), '요리 때')]):
            return False
    return True


def set_choice(K, conn, mode, *, owner_words=None, now=None, step_id=None):
    if mode not in ('joined', 'separate', 'individual'):
        raise ValueError('prep mode must be joined, separate or individual')
    if not conn.in_transaction:
        conn.execute('BEGIN IMMEDIATE')
    state = load(K, conn)
    if not state or state.get('choice'):
        conn.rollback()
        return dict(status='SPENT', next='NO_PUSH')
    question = state.get('stage_cards', {}).get('choice', {})
    if not question.get('delivered'):
        conn.rollback()
        raise ValueError('a delivered planning question is required before its answer')
    items = allowed(state['source'])
    if step_id is not None:
        if not state.get('individual') or step_id not in dict(items) or step_id in state['prep_choices']:
            conn.rollback()
            return dict(status='SPENT', next='NO_PUSH')
        index = [sid for sid, _ in items].index(step_id)
        if not state['stage_cards'].get('prep_choice_' + str(index), {}).get('delivered'):
            conn.rollback()
            raise ValueError('a delivered individual question is required')
        state['prep_choices'][step_id] = mode == 'separate'
    elif mode == 'individual':
        state['individual'] = True
    else:
        state['prep_choices'] = {sid: mode == 'separate' for sid, _ in items}
    state['owner_words'] = owner_words
    state['revision'] += 1
    complete = len(state['prep_choices']) == len(items)
    if complete:
        state['choice'] = mode
        state['source'].update(prep_mode='separate', _prep_choices=copy.deepcopy(state['prep_choices']))
        state['source'].pop('_prep_pending', None)
    save(K, conn, state)
    conn.commit()
    if not complete:
        ok = individual_cards(K, conn, state)
        return dict(status='NEEDS_PREP_MODE' if ok else 'DELIVERY_RETRY', next=frame.SILENT)
    if not cleanup_choices(K, conn, state):
        return dict(status='DELIVERY_RETRY', next='Run cooking plan PLAN.json.')
    import cooking_session as C
    if not C.post_plan(K, conn, state['source'], now):
        return dict(status='DELIVERY_RETRY', next='Run cooking plan PLAN.json.')
    return dict(status='PLANNED', next=frame.SILENT)


def build(state):
    typed = '_prep_choices' in state['source']
    front = schedule.prep_front(state['plan']['steps'], state['source'])
    if typed:
        slots = state['schedule']['slots']
        lane_start = min((slots[s['id']]['start'] for s in state['plan']['steps'] if s['id'] not in front), default=float('inf'))
        # A short hold may put this dish's ahead prep after another dish has
        # started heating. Keep that work in the shared cooking session so an
        # earlier stage cannot block already scheduled cooking. Its planned
        # slot and hold floor still apply.
        front = {sid for sid in front if slots[sid]['end'] <= lane_start}
    joined = state['source']['prep_mode'] == 'joined'
    has_cook_prep = typed and any(s['phase'] == 'prep' and s['id'] not in front for s in state['plan']['steps'])
    groups = {}
    for step in card.ordered(state):
        key = schedule.stage(step)
        if typed and step['phase'] == 'prep' and step['id'] not in front:
            key = 'joined'
        if typed and key == 'cook' and has_cook_prep:
            key = 'joined'
        if not typed and joined and key in ('prep', 'cook'):
            key = 'joined'
        groups.setdefault(key, []).append(step['id'])
    slots = state['schedule']['slots']
    return [dict(key=key, ids=ids, at=min(slots[sid]['start'] for sid in ids))
            for key, ids in sorted(groups.items(), key=lambda kv: (min(slots[s]['start'] for s in kv[1]),
                      ('ingredients', 'prep', 'joined', 'cook').index(kv[0])))]


def timeline(state, stages=None):
    stages = stages if stages is not None else build(state)
    return ' · '.join([LABELS[s['key']] + ' ' + card.clock(state, s['at']) for s in stages]
                      + ['완성 ' + card.clock(state, state['schedule']['eta'])])


def plan_step(words, state, step):
    """Plan-only details from typed fields; session pin labels stay shared."""
    conditions = []
    if step['heat'] in ('low', 'medium', 'medium_high', 'high'):
        conditions.append(words['heat_' + step['heat']].split(':', 1)[0])
    elif step['heat'] == 'oven':
        if step.get('oven_mode'):
            conditions.append(words['plan_oven_mode_' + step['oven_mode']])
        if step.get('temp_c') is not None:
            conditions.append(words['plan_temp'].format(temp=format(step['temp_c'], 'g')))
    label = card.step_label(words, step)
    if conditions:
        label += ' \u00b7 ' + ' '.join(conditions)
    return label + card.ingredient_suffix(words, state, step)


def plan_card(words, state):
    stages = build(state)
    multiple = len(state['source']['recipes']) > 1
    lines = ['\U0001f9fe **\uc7ac\ub8cc**'] if multiple else []
    for recipe in state['source']['recipes']:
        ids = {recipe['id'] + '-' + i['id'] for i in recipe['ingredients']}
        ingredients = [i for i in state['plan']['ingredients'] if i['id'] in ids]
        lines.append('**' + recipe.get('emoji', '\U0001f373') + ' ' + recipe['name'] + '**')
        if multiple:
            lines += ['> \u2022 ' + card.ingredient_lines(words, [i])[2:] for i in ingredients]
        else:
            lines.append(markdown_items(line.removeprefix('- ') for line in card.ingredient_lines(words, ingredients).splitlines()))
    for stage in stages:
        emoji, label = LABELS[stage['key']].split(' ', 1)
        heading = '\n' + LABELS[stage['key']]
        if multiple:
            heading = '\n' + emoji + ' **' + label + '** ' + card.clock(state, stage['at'])
        if multiple and stage is stages[-1]:
            heading += ' \u00b7 \uc644\uc131 ' + card.clock(state, state['schedule']['eta'])
        lines.append(heading)
        items = []
        for step in card.ordered(state):
            if step['id'] in stage['ids']:
                emoji, _ = card.dish(state, step)
                item = (card.clock(state, state['schedule']['slots'][step['id']]['start'])
                             + ' ' + (emoji + ' ' if multiple else '') + plan_step(words, state, step))
                if step.get('done_cue', '').strip():
                    item += '\n\u3134 ' + step['done_cue']
                items.append(item)
        lines.append(markdown_items(items))
    if '_prep_choices' in state['source']:
        for step in state['plan']['steps']:
            if step.get('ahead') == 'required':
                following = [s for s in state['plan']['steps'] if step['id'] in s['depends']]
                if following:
                    nxt = min(following, key=lambda s: state['schedule']['slots'][s['id']]['start'])
                    lines.append('\n%s는 %s %s분 전까지 꼭 해 둬야 해서 앞에 넣었어요.' % (step['name'], nxt['name'], format(step['ahead_min_sec'] / 60, 'g')))
    return '\n'.join(lines) + ('' if multiple else '\n\n' + timeline(state, stages))


def arm(K, conn, source, now, snapshot=None):
    import cooking_session as C
    state = load(K, conn)
    if not state or state['meal'] != identity(source):
        state = dict(frame.create('cooking', {}), meal=identity(source), at=now, stage_cards={})
    if state.get('stages'):
        return state
    state.update(source=copy.deepcopy(source), choice=state.get('choice') or source['prep_mode'],
                 plan=C.compile_plan(source, conn, K._pin_agent()), history=[], step_started={})
    if snapshot:
        state['plan'] = copy.deepcopy(snapshot['plan'])
        state['schedule'] = copy.deepcopy(snapshot['schedule'])
    else:
        schedule.refresh(state, now)
    state['stages'] = build(state)
    save(K, conn, state)
    conn.commit()
    return state


def tick(K, conn, now):
    state = load(K, conn)
    if not state or state.get('cancelled') or not state.get('stages'):
        return
    live = frame.load(K, conn)
    context = (live or {}).get('source', {}).get('_stage_context', {})
    superseded = (live and live.get('started_at') is not None
                  and same_meal(state.get('source'), live.get('source'), live_restart=True)
                  and context.get('id') != state['id'])
    if superseded:
        # A later direct start/catch-up owns this dinner now. Retire only
        # bubbles actually delivered; never retry an unposted old overview.
        state['revision'] += 1
        save(K, conn, state)
        conn.commit()
        for key, old in list(state.get('stage_cards', {}).items()):
            if old.get('message_id') and (old.get('actions') or not old.get('delivered')):
                if not send(K, conn, state, key, old['text'], []):
                    return dict(status='DELIVERY_RETRY')
        state['cancelled'] = True
        save(K, conn, state)
        conn.commit()
        return dict(status='STAGES_CHECKED', posted=[])
    import cooking_session as C
    words = C.words(K)
    posted = []
    active_keys = {stage['key'] for stage in state['stages']}
    for key, old in list(state.get('stage_cards', {}).items()):
        if key in LABELS and key not in active_keys and old.get('message_id') and old.get('actions'):
            if not send(K, conn, state, key, old['text'], []):
                return dict(status='DELIVERY_RETRY', posted=posted)
    for stage in state['stages']:
        if stage.get('session_id'):
            old = state['stage_cards'].get(stage['key'], {})
            if old.get('actions') or not old.get('delivered', True):
                if not send(K, conn, state, stage['key'], old['text'], []):
                    return dict(status='DELIVERY_RETRY')
            continue
        if now + .001 < stage['at']:
            continue
        if stage['key'] in ('cook', 'joined'):
            preview = dict(state, plan=dict(state['plan'], steps=[s for s in state['plan']['steps'] if s['id'] in stage['ids']]))
            text = card.session_overview(words, preview)
        else:
            text = LABELS[stage['key']] + ' ' + card.clock(state, stage['at'])
            text += '\n' + markdown_items(card.measured_step(words, state, s)
                                      for s in card.ordered(state) if s['id'] in stage['ids'])
        had = state['stage_cards'].get(stage['key'], {})
        if not send(K, conn, state, stage['key'], text,
                    [('stage_' + stage['key'], BUTTONS[stage['key']])]):
            return dict(status='DELIVERY_RETRY', posted=posted)
        if not (had.get('delivered') and had.get('message_id')):
            posted += stage['ids']
    return dict(status='STAGES_CHECKED', posted=posted)


def sync(K, conn, session):
    context = session['source'].get('_stage_context')
    if not context:
        return
    state = load(K, conn)
    if not state or state['id'] != context['id']:
        return
    stage = next(s for s in state['stages'] if s['key'] == context['key'])
    if session['phase'] == 'cancelled':
        state['cancelled'] = True
    stage.update(session_id=session['id'], phase=session['phase'],
                 history=copy.deepcopy(session.get('history', [])))
    state['revision'] += 1
    save(K, conn, state)


def start(K, conn, now):
    """Start the pending meal through the same durable stage-card contract."""
    if not conn.in_transaction:
        conn.execute('BEGIN IMMEDIATE')
    live = frame.load(K, conn)
    import cooking_session as C
    C.finish_completed(K, conn, live)
    state = load(K, conn)
    if (not state or state.get('cancelled') or not state.get('stages')
            or live and live['phase'] not in ('closed', 'cancelled')):
        conn.commit()
        return None
    pending = next((s for s in state['stages'] if not s.get('session_id')), None)
    if not pending:
        conn.commit()
        return None
    conn.commit()
    result = tick(K, conn, now)
    if result and result['status'] == 'DELIVERY_RETRY':
        return result
    state = load(K, conn)
    key = pending['key']
    keyboard = state.get('stage_cards', {}).get(key, {})
    action = 'stage_' + key
    if not keyboard.get('delivered') or action not in keyboard.get('actions', []):
        return dict(status='NEEDS_STAGE', next='Wait for the scheduled stage card.')
    token = 'activity:%s:%d:%s' % (state['id'], keyboard['revision'], action)
    return apply(K, conn, token, keyboard['message_id'], now)


def apply(K, conn, token, message_id, now):
    """Scoped stage callbacks share the existing activity:start-tap ingress."""
    state = load(K, conn)
    parts = token.split(':')
    if not state or len(parts) != 4 or parts[:2] != ['activity', state['id']]:
        return None
    if not conn.in_transaction:
        conn.execute('BEGIN IMMEDIATE')
    # Re-read under the writer lock; a replay can never open two sessions.
    state = load(K, conn)
    import cooking_session as C
    live = frame.load(K, conn)
    C.finish_completed(K, conn, live)
    state = load(K, conn)
    action = parts[3]
    keyboard = next((c for c in state.get('stage_cards', {}).values()
                     if str(c.get('message_id')) == str(message_id)
                     and str(c['revision']) == parts[2] and action in c['actions']), None)
    if state.get('cancelled') or not keyboard:
        conn.rollback()
        return dict(status='SPENT', next='NO_PUSH')
    if action.startswith('prep_') and not state.get('stages'):
        if action == 'prep_individual':
            return set_choice(K, conn, 'individual', owner_words=token, now=now)
        if action in ('prep_all_ahead', 'prep_all_cook'):
            return set_choice(K, conn, 'separate' if action == 'prep_all_ahead' else 'joined', owner_words=token, now=now)
        index = int(action.rsplit('_', 1)[1])
        sid = allowed(state['source'])[index][0]
        return set_choice(K, conn, 'separate' if action.startswith('prep_ahead_') else 'joined', step_id=sid, owner_words=token, now=now)
    if action in ('stage_joined', 'stage_separate') and not state.get('stages'):
        if state.get('choice'):
            conn.rollback()
            return dict(status='SPENT', next='NO_PUSH')
        return set_choice(K, conn, action[6:], owner_words=token, now=now)
    key = action[6:]
    stage = next((s for s in state.get('stages', []) if s['key'] == key), None)
    if not stage or stage.get('session_id'):
        conn.rollback()
        return dict(status='SPENT', next='NO_PUSH')
    earlier = state['stages'][:state['stages'].index(stage)]
    if any(s.get('phase') != 'closed' for s in earlier):
        conn.rollback()
        return dict(status='NEEDS_STAGE', next='Finish the previous stage; this button remains usable.')
    if live and live['phase'] not in ('closed', 'cancelled'):
        conn.rollback()
        return dict(status='ACTIVITY_OPEN', next='Continue or cancel the current activity first.')
    if live and live.get('pin_open'):
        # Close the old lease before replacing the activity slot. A failed
        # ancillary close must never prevent the new meal's first card.
        delivery.publish_pin(K, conn, live, C.words(K), now)
    source = copy.deepcopy(state['source'])
    source.update(_stage_ids=stage['ids'], _stage_context=dict(id=state['id'], key=key),
                  block='prep' if key in ('ingredients', 'prep') else 'all')
    if key in ('ingredients', 'prep'):
        source['target_at'] = max(state['schedule']['slots'][sid]['end'] for sid in stage['ids'])
        source.pop('guest_arrival_at', None)
        source.pop('user_slack_sec', None)
    block_id = state.get('block_id') if stage == state['stages'][-1] else None
    if '_prep_choices' in source:
        source['_prep_not_before'] = {sid: state['schedule']['slots'][sid]['start'] for sid in stage['ids']}
    session = frame.create('cooking', C.compile_plan(source, conn, K._pin_agent()), block_id)
    # Earlier outputs stay as traceable evidence, not as pending work. A rest
    # after a late prep still blocks the cook; the target is never consumed.
    inputs = [h for s in earlier for h in s.get('history', [])]
    ready_at = max([now] + [h['completed_at'] + schedule.lead(h['step']) for h in inputs])
    session.update(source=source, history=[], stage_inputs=inputs, step_started={},
                   stock_plan_key=state['id'], phase='active', started_at=now, delay_until=ready_at)
    schedule.refresh(session, now, focus=True)
    session['chart_slots'] = copy.deepcopy(session['schedule']['slots'])
    stage.update(session_id=session['id'], phase='active', history=[])
    state['revision'] += 1
    save(K, conn, state)
    frame.save(K, conn, session)
    conn.commit()
    # Remove the consumed keyboard; do not expose a second generic Start card.
    if not send(K, conn, state, key, keyboard['text'], []):
        return dict(status='DELIVERY_RETRY', next='Run cooking show; the stage is already open.')
    return C.show(K, conn, session, now)
