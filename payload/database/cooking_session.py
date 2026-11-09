"""Typed, scalable cooking steps on the shared activity-session frame.

Recipes are data, not chat. A task binds one prep/cook/contiguous plan. Multiple
recipes share a resource-constrained frontier back-scheduled from a target.
Durations are never compressed to hide a late finish. Armed timer expiry
completes a step by time; untimed work still requires the owner's next/done.
"""
import copy
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import hashlib
import json
import math
from pathlib import Path
import re
import time

import activity_session as frame
import agent_conf
import cooking_card
import cooking_delivery
import cooking_plan
import cooking_pending
import pantry_stock
import kitchen
import cooking_schedule as schedule
import cooking_live
import cooking_owner
import cooking_stages
from locale_store import read_json

PLAN_PREFIX = 'cooking.plan.'
PLANNED_PREFIX = 'cooking.planned.'
LAST_POSTED = []
SCHEMA = 'cooking_recipe/1'


def data(K, name):
    path = Path(K.SCRIPT_DIR).parent / 'service/kit_life' / name
    return read_json(path)


def words(K):
    lang = K.agent_conf.conf_str('AGENT_LANG', 'en').split('_')[0].split('-')[0]
    bundle = data(K, 'i18n/%s.json' % lang)
    return {k[len('cooking.'):]: v for k, v in bundle.items() if k.startswith('cooking.')}


def positive(value, label, zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or (value == 0 and not zero):
        raise ValueError('invalid ' + label)
    return value


def approved_notice(K, kind, subject='', minutes=''):
    tail = ord(subject[-1]) - 0xac00 if subject else -1
    particle = '\ub97c' if 0 <= tail <= 11171 and tail % 28 == 0 else '\uc744'
    return words(K)[kind].format(dish=subject, dish_object=subject + particle, minutes=minutes)


def queue_notice(K, state, kind, subject='', minutes=''):
    key = '%s:%s' % (kind, state['revision'])
    state.setdefault('cooking_notices', {})[key] = approved_notice(K, kind, subject, minutes)


def validate(recipe):
    if recipe.get('schema') != SCHEMA:
        raise ValueError('expected ' + SCHEMA)
    if not re.fullmatch(r'[a-z0-9_-]{1,32}', recipe.get('id', '')):
        raise ValueError('invalid recipe id')
    positive(recipe['base_amount'], 'base amount')
    positive(recipe['amount'], 'amount')
    positive(recipe.get('serve_after_sec', 0), 'serving buffer', zero=True)
    if recipe.get('service_style', 'immediate') not in ('immediate', 'hold'):
        raise ValueError('invalid service style')
    positive(recipe.get('hold_limit_sec', 0), 'hold limit', zero=True)
    if recipe.get('service_style') == 'hold' and not recipe.get('hold_limit_sec'):
        raise ValueError('hold dishes need hold_limit_sec')
    if 'yield' in recipe:
        positive(recipe['yield']['quantity'], 'yield quantity')
        if recipe['yield']['unit'] not in ('whole', 'piece', 'serving', 'glass', 'cup', '\uac1c', '\uc794', '\uc778\ubd84'):
            raise ValueError('invalid dish yield unit')
    if not recipe.get('name') or not recipe.get('ingredients') or not recipe.get('steps'):
        raise ValueError('recipe needs name, ingredients and steps')
    ids = set()
    for i in recipe['ingredients']:
        if not re.fullmatch(r'[a-z0-9_-]{1,32}', i.get('id', '')) or i['id'] in ids:
            raise ValueError('duplicate/invalid ingredient id')
        ids.add(i['id'])
        positive(i['quantity'], 'ingredient quantity', zero=True)
        if not i.get('name') or not i.get('unit'):
            raise ValueError('ingredient needs name and unit')
        for field in ('grams_per_unit', 'density_g_ml'):
            if field in i:
                positive(i[field], field)
        if 'abv' in i and not 0 <= positive(i['abv'], 'ABV', zero=True) <= 1:
            raise ValueError('ABV must be a fraction from 0 to 1')
    if recipe['anchor'] not in ids:
        raise ValueError('anchor ingredient missing')
    steps = set()
    for s in recipe['steps']:
        if not re.fullmatch(r'[a-z0-9_-]{1,32}', s.get('id', '')) or s['id'] in steps:
            raise ValueError('duplicate/invalid step id')
        steps.add(s['id'])
        if s['heat'] not in cooking_plan.HEATS:
            raise ValueError('invalid heat')
        for field in ('temp_c', 'oven_mode'):
            if field in s and s['heat'] != 'oven':
                raise ValueError(field + ' is only valid with heat=oven')
        if 'temp_c' in s:
            positive(s['temp_c'], 'temp_c')
        if 'oven_mode' in s and s['oven_mode'] not in cooking_plan.OVEN_MODES:
            raise ValueError('invalid oven_mode')
        if s['phase'] not in ('prep', 'cook') or not set(s['ingredients']) <= ids:
            raise ValueError('invalid phase or ingredient reference')
        if s.get('ingredient_prep') and (s['phase'] != 'prep' or s['heat'] != 'none' or s.get('requires', 'none') not in ('none',)):
            raise ValueError('ingredient preparation must use phase=prep without heat or knife/appliance resources')
        if not s.get('name') or not s.get('action'):
            raise ValueError('each step needs a name and one action')
        if not isinstance(s.get('done_cue', ''), str):
            raise ValueError('done_cue must be a string (may be empty)')
        if not isinstance(s.get('warning', ''), str):
            raise ValueError('one warning string only')
        if 'gear' in s and (not isinstance(s['gear'], str) or not s['gear'].strip()):
            raise ValueError('step gear must be a nonempty vessel name')
        if s.get('kind', 'active') not in ('active', 'attended', 'unattended'):
            raise ValueError('invalid cooking step kind')
        if s.get('requires', 'none') not in ('none',) + schedule.RESOURCES[1:]:
            raise ValueError('invalid cooking resource')
        if 'prereq' in s and (not isinstance(s['prereq'], list) or not all(isinstance(p, str) for p in s['prereq'])):
            raise ValueError('prereq must be a list of step references')
        if 'check_at_sec' in s:
            positive(s['check_at_sec'], 'mid-step check')
            if s['kind'] != 'unattended' or not s.get('check_cue') or s['check_at_sec'] >= s['duration_sec']:
                raise ValueError('check needs unattended duration and cue')
        if 'instructions' in s and (not isinstance(s['instructions'], list) or not s['instructions'] or not all(isinstance(line, str) and line for line in s['instructions'])):
            raise ValueError('instructions must be nonempty lines')
        if s.get('ahead', 'never') not in ('never', 'required', 'allowed'):
            raise ValueError('ahead must be never, required or allowed')
        if s.get('ahead', 'never') != 'never' and s['phase'] != 'prep':
            raise ValueError('only prep steps can be ahead')
        if s.get('ahead') == 'required':
            positive(s.get('ahead_min_sec'), 'ahead_min_sec')
        if s.get('ahead') == 'allowed':
            positive(s.get('hold_max_sec'), 'hold_max_sec')
        positive(s.get('wait_after_sec', 0), 'wait interval', zero=True)
        positive(s['duration_sec'], 'duration', zero=True)
        if s['duration_sec'] > 86400:
            raise ValueError('duration outside bridge range')
    return recipe


def normalize(payload):
    recipes = payload.get('recipes', [])
    if not 1 <= len(recipes) <= 8:
        raise ValueError('supply one to eight recipes')
    if payload.get('block', 'all') not in ('all', 'prep', 'cook'):
        raise ValueError('block must be prep, cook or all')
    ids = set()
    for recipe in recipes:
        validate(recipe)
        if recipe['id'] in ids:
            raise ValueError('duplicate recipe id')
        ids.add(recipe['id'])
    out = copy.deepcopy(payload)
    def timestamp(value, label, set_offset=True):
        target = value
        if isinstance(target, str):
            parsed = datetime.fromisoformat(target.replace('Z', '+00:00'))
            if parsed.tzinfo is None:
                raise ValueError(label + ' needs a timezone')
            if set_offset and 'timezone' not in out:
                out['target_offset_sec'] = int(parsed.utcoffset().total_seconds())
            target = parsed.timestamp()
        return positive(target, label)
    if out.get('guest_arrival_at') is not None:
        out['guest_arrival_at'] = timestamp(out['guest_arrival_at'], 'guest arrival time')
        out['user_slack_sec'] = positive(out.get('user_slack_sec', 0), 'user slack', zero=True)
        promised = out['guest_arrival_at'] + out['user_slack_sec']
        if out.get('target_at') is not None and abs(timestamp(out['target_at'], 'target time') - promised) > .001:
            raise ValueError('target_at must equal guest_arrival_at + user_slack_sec')
        out['target_at'] = promised
    elif out.get('user_slack_sec') is not None:
        raise ValueError('user_slack_sec needs guest_arrival_at')
    elif out.get('target_at') is not None:
        out['target_at'] = timestamp(out['target_at'], 'target time')
    cooking_plan.meat_prep(out)
    for recipe in out['recipes']:
        for step in recipe['steps']:
            if 'start_at' in step:
                step['start_at'] = timestamp(step['start_at'], 'step start time', set_offset=False)
    ZoneInfo(out.get('timezone', 'UTC'))
    if out.get('target_at') is not None:
        datetime.fromtimestamp(out['target_at'], timezone.utc)
    if out.get('prep_mode') not in (None, 'joined', 'separate'):
        raise ValueError('prep_mode must be joined or separate')
    if any('ahead' in step for recipe in recipes for step in recipe['steps']):
        out.setdefault('_prep_choices', {})
    out.setdefault('block', 'all')
    out.setdefault('changes', {})
    return out


def compile_plan(source, conn=None, agent=None):
    ingredients, tracks = [], []
    lang = agent_conf.conf_str('AGENT_LANG', 'en').split('_')[0].split('-')[0]
    unit_words = read_json(Path(__file__).absolute().parents[1] / 'service/kit_life/i18n' / (lang + '.json'))
    unit_words = {k[len('cooking.'):]: v for k, v in unit_words.items() if k.startswith('cooking.')}
    for r in source['recipes']:
        ratio = r['amount'] / r['base_amount']
        changes = source.get('changes', {}).get(r['id'], {})
        names, display_names, missing, overrides = {}, {}, set(), {}
        for ing in r['ingredients']:
            key = r['id'] + '-' + ing['id']
            mod = changes.get(ing['id'], {})
            if mod.get('missing'):
                missing.add(ing['id'])
                continue
            i = copy.deepcopy(ing)
            i.update(mod)
            if mod.get('name') and mod['name'] != ing['name']:
                i['aliases'] = mod.get('aliases', [])
                i.pop('food_name', None)
            scaled = round(i['quantity'] * ratio, 4)
            positive(scaled, 'scaled ingredient quantity', zero=True)
            i.update(id=key, quantity=scaled)
            ingredients.append(i)
            # Placeholders use the same localized unit formatter as ingredient lists.
            names[ing['id']] = cooking_card.quantity(unit_words, i)
            display_names[ing['id']] = i['name']
            overrides.update(mod.get('step_overrides', {}))
        track = []
        for original_index, original in enumerate(r['steps']):
            if r['id'] + '-' + original['id'] in source.get('_deleted_steps', []):
                continue
            if source.get('_stage_ids') is not None and r['id'] + '-' + original['id'] not in source['_stage_ids']:
                continue
            if source['block'] != 'all' and original['phase'] != source['block']:
                continue
            if set(original.get('omit_if_missing', [])) & missing:
                continue
            s = copy.deepcopy(original)
            override = overrides.get(s['id'], {})
            if 'instructions' not in override and any(field in override for field in ('action', 'heat', 'done_cue')):
                s.pop('instructions', None)
            s.update(override)
            # The approved preparation title uses ingredient display names without amounts.
            step_names = display_names if s.get('step_type') == 'ingredient_prep' and s.get('tempering_lead_sec') else names
            for field in ('name', 'action', 'done_cue', 'warning'):
                s[field] = s.get(field, '').format_map(step_names)
            s.update(id=r['id'] + '-' + s['id'], dish=r['id'],
                     ingredients=[r['id'] + '-' + i for i in s['ingredients'] if i not in missing])
            s['instructions'] = [line.format_map(names) for line in s.get('instructions', [])]
            s['order'] = len(track)
            refs = original.get('prereq', [r['steps'][original_index - 1]['id']] if original_index else [])
            s['depends'] = [p if ':' in p else r['id'] + ':' + p for p in refs]
            s.setdefault('kind', 'active')
            s.setdefault('requires', 'oven' if s['heat'] == 'oven' else 'burner' if s['heat'] != 'none' else 'none')
            if (s['kind'] == 'unattended' and s['heat'] not in ('none', 'oven') and s['duration_sec']
                    and (s.get('check_cue') or s['done_cue'].strip())):
                # An optional completion cue must not create an empty reminder.
                s.setdefault('check_at_sec', s['duration_sec'] / 2)
                s.setdefault('check_cue', s['done_cue'])
            elif s['kind'] == 'unattended' and s['heat'] == 'oven' and s['duration_sec'] and 'check_at_sec' not in s:
                # Owner 2026-10-24: oven steps check at half time and at the end unless the plan sets its own.
                s['check_at_sec'] = s['duration_sec'] / 2
                s['check_cue'] = s['done_cue']
                s['auto_check'] = 'oven'
            track.append(s)
        tracks.append(track)
    steps = [s for track in tracks for s in track]
    # Resolve omitted optional prerequisites transitively, including across dishes.
    all_steps = {r['id'] + ':' + s['id']: (r, n, s) for r in source['recipes'] for n, s in enumerate(r['steps'])}
    included = {s['id'] for s in steps}
    if len(included) != len(steps):
        raise ValueError('ambiguous global step ids')
    def resolve(ref, trail=()):
        if ref not in all_steps or ref in trail:
            raise ValueError('unknown or cyclic cooking prerequisite: ' + ref)
        r, n, original = all_steps[ref]
        sid = r['id'] + '-' + original['id']
        if sid in included:
            return [sid]
        # Other phase outputs were verified in an earlier task block.
        if source.get('_stage_ids') is not None or (source['block'] != 'all' and original['phase'] != source['block']):
            return []
        refs = original.get('prereq', [r['steps'][n-1]['id']] if n else [])
        return [sid for p in refs for sid in resolve(p if ':' in p else r['id'] + ':' + p, trail + (ref,))]
    for s in steps:
        s['depends'] = list(dict.fromkeys(sid for p in s['depends'] for sid in resolve(p)))
    if source.get('prep_mode') and source['block'] == 'all' and '_prep_choices' not in source:
        gathered = [s['id'] for s in steps if s.get('ingredient_prep')]
        for s in steps:
            if not s.get('ingredient_prep'):
                s['depends'] = list(dict.fromkeys(s['depends'] + gathered))
    if source['block'] == 'all' and '_prep_choices' not in source:
        prep_ids = [s['id'] for s in steps if s['phase'] == 'prep'
                    and s.get('step_type') != 'ingredient_prep' and 'start_at' not in s]
        for s in steps:
            if s['phase'] == 'cook':
                s['depends'] = list(dict.fromkeys(s['depends'] + prep_ids))
    visited, visiting = set(), set()
    by_id = {s['id']: s for s in steps}
    def visit(sid):
        if sid in visiting:
            raise ValueError('cyclic cooking dependencies')
        if sid in visited:
            return
        visiting.add(sid)
        for dep in by_id[sid]['depends']:
            visit(dep)
        visiting.remove(sid)
        visited.add(sid)
    for sid in by_id:
        visit(sid)
    schedule.capacities(source, steps)
    if not steps:
        raise ValueError('no steps remain in this block')
    used = {i for s in steps for i in s['ingredients']}
    plan = dict(name=', '.join(r['name'] for r in source['recipes']),
                ingredients=[i for i in ingredients if i['id'] in used], steps=steps)
    if conn is not None:
        kitchen.tune_plan(conn, plan)
        for step in steps:
            if step['kind'] != 'active' or step['heat'] != 'none':
                continue
            learned = conn.execute('SELECT avg_sec FROM cooking_pace WHERE owning_agent=? AND recipe_id=? AND step_id=?',
                                   (agent, step['dish'], step['id'][len(step['dish']) + 1:])).fetchone()
            if learned:
                step['duration_sec'] = learned[0]
    for step in steps:
        if step['id'] in source.get('_duration_overrides', {}):
            step['duration_sec'] = source['_duration_overrides'][step['id']]
        if step.get('step_type') == 'ingredient_prep' and step.get('tempering_lead_sec'):
            step['wait_after_sec'] = max(0, step['tempering_lead_sec'] - step['duration_sec'])
    return plan


def bound(K, conn, block_id):
    raw = K._config_stored(conn, PLAN_PREFIX + str(block_id))
    return json.loads(raw) if raw else None


def bind(K, conn, block_id, payload):
    row = conn.execute('SELECT owning_agent,settled_at FROM timeblock WHERE id=? AND kind=\'task\'', (block_id,)).fetchone()
    if not row or row[0] != K._pin_agent() or row[1]:
        raise ValueError('task is not open or belongs to another agent')
    if frame.task_open(K, conn, block_id):
        raise ValueError('use cooking input to change an open session')
    source = normalize(payload)
    compile_plan(source)
    K._config_put(conn, PLAN_PREFIX + str(block_id), json.dumps(source, ensure_ascii=False))
    if source.get('prep_mode'):
        staged = cooking_stages.load(K, conn)
        if staged and staged['meal'] == cooking_stages.identity(source):
            staged['block_id'] = block_id
            cooking_stages.save(K, conn, staged)
    conn.execute("DELETE FROM block_attr WHERE block_id=? AND attr_token='action_kind'", (block_id,))
    conn.execute("INSERT INTO block_attr(block_id,attr_token,value,source) VALUES(?,'action_kind','session','user')", (block_id,))
    conn.commit()
    return dict(status='BOUND', id=block_id, block=source['block'])


def reminder(K, conn, block_id):
    source = bound(K, conn, block_id)
    if not source:
        return None
    plan, c = compile_plan(source), words(K)
    preview = dict(plan=plan, source=source, completed=[], step_started={})
    first_id = schedule.derive(preview, 0)['order'][0]
    first = next(s for s in plan['steps'] if s['id'] == first_id)
    return c['preview'].format(step=first['name'], action=first['action'])


def open_session(K, conn, source, block_id=None, now=None, post=False):
    if source.get('target_at') and source.get('prep_mode') and not source.get('_stage_ids'):
        source = normalize(source)
        if not post_plan(K, conn, source, now):
            return dict(status='DELIVERY_RETRY', next='Run cooking plan PLAN.json.')
        return dict(status='PLANNED', next=frame.SILENT)
    if post and not conn.in_transaction:
        current = frame.load(K, conn)
        if not (current and current['phase'] not in ('closed', 'cancelled')):
            if not post_plan(K, conn, normalize(source), now):
                return dict(status='DELIVERY_RETRY', next='Run the same cooking open again; plan messages already posted are not repeated.')
    if not conn.in_transaction:
        conn.execute('BEGIN IMMEDIATE')
    current = frame.load(K, conn)
    if current and current['phase'] not in ('closed', 'cancelled'):
        if block_id is not None and current.get('block_id') == block_id:
            return show(K, conn, current, now)
        raise ValueError('an activity is already open; continue or cancel it first')
    if K._workout_history.open_sessions(K, conn, K._pin_agent()):
        raise ValueError('a workout session is already open')
    source = normalize(source)
    state = frame.create('cooking', compile_plan(source, conn, K._pin_agent()), block_id)
    state['source'], state['history'], state['step_started'] = source, [], {}
    pantry_stock.cooking_batch(K, conn, state, now if now is not None else time.time())
    schedule.refresh(state, now if now is not None else time.time())
    state['chart_slots'] = copy.deepcopy(state['schedule']['slots'])
    if block_id is not None:
        row = conn.execute('SELECT owning_agent,settled_at FROM timeblock WHERE id=?', (block_id,)).fetchone()
        if not row or row[0] != K._pin_agent() or row[1]:
            raise ValueError('task is no longer open')
    frame.save(K, conn, state)
    cooking_pending.consume(K, conn)
    return show(K, conn, state, now)


def schedule_result(state):
    """Expose the same live slots used by cards, with explicit local clocks."""
    live = state['schedule']
    zone = state['source'].get('timezone', 'Asia/Seoul')
    tz = ZoneInfo(zone)
    steps = {s['id']: s for s in state['plan']['steps']}
    return dict(computed_at=live['computed_at'], timezone=zone, eta=live['eta'],
                slots=[dict(step_id=sid, name=steps[sid]['name'],
                            start=datetime.fromtimestamp(live['slots'][sid]['start'], tz).strftime('%H:%M'),
                            end=datetime.fromtimestamp(live['slots'][sid]['end'], tz).strftime('%H:%M'),
                            start_at=live['slots'][sid]['start'], end_at=live['slots'][sid]['end'])
                       for sid in live['order']])


def show(K, conn, state=None, now=None):
    state = state or frame.load(K, conn)
    if not state:
        return dict(status='NO_SESSION')
    if state['phase'] == 'closed' and state.get('block_id'):
        if conn.in_transaction:
            conn.commit()
        row = conn.execute('SELECT version,settled_at FROM timeblock WHERE id=?', (state['block_id'],)).fetchone()
        if row and row[1] is None:
            result = K.force_settle(conn, state['block_id'], row[0], 'owner-chat')
            if result != K.MutationResult.APPLIED:
                return dict(status='SETTLE_RETRY', next='Run cooking show; session finished but task completion needs retry.')
            K._outbox_enqueue_best_effort(conn.execute('SELECT ulid FROM timeblock WHERE id=?', (state['block_id'],)).fetchone()[0])
            # The settled task is the only day-card change a cooking session
            # makes; kit_life.main refreshes the card for it after the verb.
            K._DAY_CARD_DIRTY = True
    now = time.time() if now is None else now
    if not conn.in_transaction:
        conn.execute('BEGIN IMMEDIATE')
    live = frame.load(K, conn)
    if live and (live['id'] != state['id'] or live['revision'] != state['revision']):
        conn.rollback()
        return dict(status='SPENT', next='NO_PUSH')
    previous = (state['current'], dict(state.get('step_started', {})))
    schedule.refresh(state, now, focus=True)
    if previous != (state['current'], state.get('step_started', {})):
        state['revision'] += 1
    state['last_tick'] = int(now // 60)
    cooking_stages.sync(K, conn, state)
    state['delivery_pending'] = True
    stock_notices = state.pop('stock_notices', [])
    frame.save(K, conn, state)
    if not cooking_delivery.publish_due(K, conn, state, words(K), now):
        return dict(status='DELIVERY_RETRY', next='Run cooking show to retry delivery; never replay the action.')
    if state['phase'] == 'closed' and frame.pin_symmetry_candidate(K, cooking_delivery.CLOSE_DEFAULT) in ('session-pin-first', 'session-pin-summary'):
        # Finish the session pin before any of the established close cards.
        if not cooking_delivery.publish_pin(K, conn, state, words(K), now):
            return dict(status='DELIVERY_RETRY', next='Run cooking show to retry the finished pin.')
    pantry_stock.decorate(K, conn, state['plan'])
    text, labels = cooking_card.render(words(K), state, state['plan']['steps'], now)
    if state['phase'] == 'ready' or state.get('editing'):
        import shop_capture
        captures = shop_capture.cooking_context(K, conn, state['plan'])
        if captures:
            text += '\n' + words(K)['captures'] + ', '.join(captures)
    prompts = state.setdefault('stock_prompts', [])
    for line in stock_notices:
        if line.startswith('ASK\t') and line.split('\t', 1)[1] not in prompts:
            prompts.append(line.split('\t', 1)[1])
    prep = [s for s in state['plan']['steps'] if s['phase'] == 'prep']
    prep_active = prep and any(s['id'] not in state['completed'] for s in prep)
    if state['phase'] == 'active' and not state.get('editing'):
        if prep_active or state.get('prep_shown') is True:
            text = cooking_card.prep_card(words(K), state)
            state['prep_shown'] = bool(prep_active)
            if state.get('prep_delivered_text') == text and state.get('message_id'):
                frame.save(K, conn, state)
                conn.commit()
                result = dict(status='ACTIVE', revision=state['revision'], next=frame.SILENT)
            else:
                state.pop('prep_delivered_text', None)
                result = frame.publish(K, conn, state, text, [])
                if result['status'] == 'ACTIVE':
                    state['prep_delivered_text'] = text
                    frame.save(K, conn, state)
                    conn.commit()
        else:
            frame.save(K, conn, state)
            conn.commit()
            result = dict(status='ACTIVE', revision=state['revision'], next=frame.SILENT)
    else:
        result = frame.publish(K, conn, state, text, labels)
    if result['status'] != 'DELIVERY_RETRY' and not cooking_delivery.publish(K, conn, state, words(K), now):
        return dict(status='DELIVERY_RETRY', next='Run cooking show to retry delivery; never replay the action.')
    if result['status'] != 'DELIVERY_RETRY':
        state.pop('delivery_pending', None)
        frame.save(K, conn, state)
        conn.commit()
    if state.get('pin_delivery_error'):
        result['delivery_errors'] = dict(pin_buttons=state['pin_delivery_error'])
    if stock_notices:
        result['stock_notices'] = stock_notices
    result['schedule'] = schedule_result(state)
    return result


def due_check(state, now):
    if not state or state.get('editing'):
        return False
    for step in state['plan']['steps']:
        sid = step['id']
        sched = state.get('schedule', {})
        if (sid not in state['completed'] and sid not in state.get('step_cards', {})
                and sched.get('slots', {}).get(sid, {}).get('start', float('inf')) <= now
                and all(dep in state['completed'] for dep in step['depends'])
                and all(sched.get('occupied', {}).get(key, 0) < sched.get('capacities', {}).get(key, 1)
                        for key in schedule.resources(step))):
            return True
        if sid in state['running'] and step.get('check_at_sec'):
            checks = state.get('checks', {}).get(sid, {})
            if now >= state['running'][sid] and 'overrun' not in checks or now >= state.get('step_started', {}).get(sid, now) + step['check_at_sec'] and 'mid' not in checks and now < state['running'][sid]:
                return True
    return False


def tick_due(K, state, now):
    return bool(state and state.get('kind') == 'cooking' and state['phase'] == 'active' and (
        state.get('last_tick') != int(now // 60) or state.get('delivery_pending')
        or any(end <= now for end in state['running'].values())
        or cooking_delivery.needs_delivery(state, now) or due_check(state, now)
        or cooking_delivery.due_heat(words(K), state, now)))


def tick(K, read_conn, db_path, now):
    """Use the existing reminder heartbeat; no new daemon or future step arms.

    Only this agent's open activity is refreshed. The countdown files remain
    limited to actually started frontier steps. Expiry unlocks dependencies
    in this same tick; the session still closes through the existing action.
    """
    stage_conn = K.event_conn(db_path)
    try:
        cooking_stages.tick(K, stage_conn, now)
    finally:
        stage_conn.close()
    state = frame.load(K, read_conn)
    if not tick_due(K, state, now):
        return
    conn = K.event_conn(db_path)
    try:
        conn.execute('BEGIN IMMEDIATE')
        state = frame.load(K, conn)
        if tick_due(K, state, now):
            completed = len(state['completed'])
            cooking_live.complete_expired(K, conn, state, now)
            if len(state['completed']) != completed:
                state['revision'] += 1
                frame.save(K, conn, state)
            return show(K, conn, state, now)
        conn.rollback()
    finally:
        conn.close()


def start_task(K, conn, block_id):
    source = bound(K, conn, block_id)
    return open_session(K, conn, source, block_id) if source else None


def add_dishes(K, conn, payload, now=None):
    """Attach typed recipes to the live dinner and replan only unfinished work."""
    now = time.time() if now is None else now
    conn.execute('BEGIN IMMEDIATE')
    state = frame.load(K, conn)
    if not state or state.get('kind') != 'cooking' or state['phase'] not in ('ready', 'active') or state.get('editing'):
        conn.rollback()
        raise ValueError('no editable cooking session')
    incoming = normalize(payload)
    source = copy.deepcopy(state['source'])
    known = {r['id'] for r in source['recipes']}
    additions = [copy.deepcopy(r) for r in incoming['recipes'] if r['id'] not in known]
    if not additions or len(additions) != len(incoming['recipes']):
        conn.rollback()
        raise ValueError('added dishes need new recipe ids')
    source['recipes'].extend(additions)
    source = normalize(source)
    plan = compile_plan(source, conn, K._pin_agent())
    # Indices are part of already-posted callback tokens. Keep every existing
    # step (including relaxed dependencies and actual work) in its old position.
    old_ids = {s['id'] for s in state['plan']['steps']}
    new_steps = [s for s in plan['steps'] if s['id'] not in old_ids]
    plan['steps'] = copy.deepcopy(state['plan']['steps']) + new_steps
    state['source'], state['plan'] = source, plan
    schedule.refresh(state, now, focus=True)
    state['chart_slots'] = copy.deepcopy(state['schedule']['slots'])
    state['revision'] += 1
    queue_notice(K, state, 'add_dish_confirm', ', '.join(r['name'] for r in additions))
    frame.save(K, conn, state)
    conn.commit()
    result = show(K, conn, state, now)
    if result['status'] != 'DELIVERY_RETRY':
        result['status'] = 'DISH_ADDED'
    result.update(added=[r['name'] for r in additions], reply=None,
                  next='Code delivers confirmation. Retry failed delivery with cooking show only; never replay add.')
    return result


def add_step(K, conn, payload, now=None):
    """Insert one schema-checked step without invalidating live callback indices."""
    now = time.time() if now is None else now
    if not isinstance(payload, dict):
        raise ValueError('STEP.json must be a step object with dish and before/after/start_at')
    step = copy.deepcopy(payload)
    dish = step.pop('dish', None)
    before, after = step.pop('before', None), step.pop('after', None)
    errors = []
    cooking_plan._fields(step, cooking_plan.STEP_FIELDS, '$', errors)
    if errors:
        return cooking_plan.report(errors, [])
    if step['phase'] == 'prep':
        # A live insertion belongs at its requested position, rather than
        # becoming a new prerequisite of every already-running heat step.
        step.setdefault('step_type', 'ingredient_prep')
        step.setdefault('prep_ahead', False)
    if sum(k in payload for k in ('before', 'after', 'start_at')) != 1:
        raise ValueError('supply exactly one of before, after or start_at')
    conn.execute('BEGIN IMMEDIATE')
    state = frame.load(K, conn)
    if not state or state.get('kind') != 'cooking' or state['phase'] not in ('ready', 'active') or state.get('editing'):
        conn.rollback()
        raise ValueError('no editable cooking session')
    source = copy.deepcopy(state['source'])
    recipe = next((r for r in source['recipes'] if r['id'] == dish), None)
    if recipe is None:
        raise ValueError('unknown dish id')
    if step.get('id') in {s['id'] for s in recipe['steps']}:
        raise ValueError('added step needs a new step id; retry delivery with cooking show')
    if source['block'] != 'all' and step.get('phase') != source['block']:
        raise ValueError('added step must belong to the open session block')
    steps = recipe['steps']
    # Make implicit edges explicit before changing the source list.
    for n, old in enumerate(steps):
        old.setdefault('prereq', [steps[n - 1]['id']] if n else [])
    relative = before if 'before' in payload else after
    if 'before' in payload or 'after' in payload:
        if not isinstance(relative, str) or relative not in {s['id'] for s in steps}:
            raise ValueError('before/after must name a step id in this dish')
        index = next(n for n, s in enumerate(steps) if s['id'] == relative)
        anchor = steps[index]
        frozen = set(state['completed']) | set(state.get('step_started', {})) | set(state['running'])
        if 'before' in payload and dish + '-' + relative in frozen:
            raise ValueError('before must name a pending, unstarted step; use after or start_at for new work')
        if before is not None:
            step.setdefault('prereq', list(anchor['prereq']))
            anchor['prereq'] = list(dict.fromkeys(anchor['prereq'] + [step.get('id')]))
        else:
            step.setdefault('prereq', [relative])
            # Redirect only pending successors, never completed/started work.
            for r in source['recipes']:
                for s in r['steps']:
                    if r['id'] + '-' + s['id'] in frozen:
                        continue
                    s['prereq'] = [
                        (step.get('id') if r['id'] == dish else dish + ':' + str(step.get('id')))
                        if ref == dish + ':' + relative or r['id'] == dish and ref == relative else ref
                        for ref in s.get('prereq', [])]
            index += 1
        steps.insert(index, step)
    else:
        step.setdefault('prereq', [])
        steps.append(step)
    if '_stage_ids' in source:
        # The stage filter describes this live session, including inserted work.
        source['_stage_ids'].append(dish + '-' + step['id'])
    checked_source = {key: value for key, value in source.items()
                      if not key.startswith('_') and key not in ('target_offset_sec', 'reported_delay_sec')}
    errors, warnings = cooking_plan.check(checked_source, lambda p: compile_plan(normalize(p)))
    if errors:
        conn.rollback()
        return cooking_plan.report(errors, warnings)
    source = normalize(source)
    plan = compile_plan(source, conn, K._pin_agent())
    old_steps = state['plan']['steps']
    old_ids = {s['id'] for s in old_steps}
    frozen = set(state['completed']) | set(state.get('step_started', {})) | set(state['running'])
    compiled = {s['id']: s for s in plan['steps']}
    # Keep done records and running steps exactly, and preserve any dependencies
    # relaxed by owner progress on other pending steps. Add only new edges.
    previous = compile_plan(normalize(state['source']))
    previous = {s['id']: set(s['depends']) for s in previous['steps']}
    retained = []
    for old in old_steps:
        fresh = copy.deepcopy(old)
        if old['id'] not in frozen:
            fresh['depends'] = list(dict.fromkeys(old['depends'] + [
                dep for dep in compiled[old['id']]['depends'] if dep not in previous[old['id']]]))
            fresh['order'] = compiled[old['id']]['order']
        retained.append(fresh)
    plan['steps'] = retained + [s for s in plan['steps'] if s['id'] not in old_ids]
    state['source'], state['plan'] = source, plan
    schedule.refresh(state, now, focus=True)
    state.setdefault('chart_slots', {}).update(copy.deepcopy(state['schedule']['slots']))
    state['revision'] += 1
    frame.save(K, conn, state)
    conn.commit()
    result = show(K, conn, state, now)
    if result['status'] != 'DELIVERY_RETRY':
        result['status'] = 'STEP_ADDED'
    result.update(added=dish + '-' + step['id'], reply=None,
                  next='Updated plan, pin and dish tables are delivered by code. Retry delivery with cooking show only; never replay add-step or cancel/reopen.')
    return result


def record_delay(K, conn, state, seconds, now, request=None):
    """Pause the cook, preserve food already heating and the promised dinner.

    A delay spends work time, never owner slack or the target. Only a started
    hands-on step's countdown is affected; unattended food keeps cooking.
    """
    until = max(now, state.get('delay_until', now)) + seconds
    replacements = []
    for step in state['plan']['steps']:
        sid = step['id']
        if sid in state.get('step_started', {}) and sid not in state['completed'] and step['kind'] != 'unattended':
            extra = max(0, now - (state['step_started'][sid] + step['duration_sec'] + state.get('step_delays', {}).get(sid, 0))) + seconds
            state.setdefault('step_delays', {})[sid] = state.get('step_delays', {}).get(sid, 0) + extra
            if sid in state['running']:
                deadline = max(now, state['running'][sid]) + seconds
                if deadline - now > 86400:
                    raise ValueError('timer duration outside bridge range')
                replacements.append((step, deadline))
    state['delay_until'] = until
    state['source']['reported_delay_sec'] = state['source'].get('reported_delay_sec', 0) + seconds
    schedule.refresh(state, now, focus=True)
    state['chart_slots'] = copy.deepcopy(state['schedule']['slots'])
    state['revision'] += 1
    state['last_request'] = request
    queue_notice(K, state, 'delay_reannounce', minutes=format(seconds / 60, 'g'))
    for step, deadline in replacements:
        state['running'][step['id']] = deadline
        frame.countdown(K, state, step, deadline - now)
    frame.save(K, conn, state)
    conn.commit()
    result = show(K, conn, state, now)
    result.update(delay_sec=seconds, affected_timers=[s['id'] for s, _ in replacements], reply=None)
    if result['status'] != 'DELIVERY_RETRY':
        result['status'] = 'PLAN_DELAYED'
    result['next'] = 'Code delivers the delay confirmation and replanned clocks. Retry failed delivery with cooking show only.'
    return result


def shift_delay(K, conn, seconds, now=None):
    now = time.time() if now is None else now
    seconds = positive(seconds, 'delay seconds')
    conn.execute('BEGIN IMMEDIATE')
    state = frame.load(K, conn)
    if not state or state.get('kind') != 'cooking' or state['phase'] not in ('ready', 'active') or state.get('editing'):
        conn.rollback()
        raise ValueError('no cooking session to delay')
    request = frame.inbound(K).get('request_id')
    if request and request == state.get('last_request'):
        conn.rollback()
        return dict(status='DUPLICATE', next=frame.SILENT)
    return record_delay(K, conn, state, seconds, now, request)


def adjust(K, state, changes, conn=None):
    """Apply typed recipe/ingredient changes; never interpret owner prose."""
    c = words(K)
    source = copy.deepcopy(state['source'])
    hits = []
    for change in changes:
        r = next(r for r in source['recipes'] if r['id'] == change['recipe'])
        i = next(i for i in r['ingredients'] if i['id'] == change['ingredient'])
        hits.append((r, i))
        if 'grams' in change:
            if i['id'] != r['anchor']:
                raise ValueError('amount change must name the recipe anchor')
            r['amount'] = positive(change['grams'], 'amount') / positive(i['quantity'], 'anchor quantity') * r['base_amount']
        elif change.get('missing') is True:
            if i['id'] == r['anchor'] or not i.get('removable', False):
                return c['required'].format(ingredient=i['name'])
            source.setdefault('changes', {}).setdefault(r['id'], {})[i['id']] = dict(missing=True)
        elif 'replacement' in change:
            replacement = next(x for x in i.get('substitutions', []) if x['name'] == change['replacement'])
            source.setdefault('changes', {}).setdefault(r['id'], {})[i['id']] = copy.deepcopy(replacement)
        else:
            raise ValueError('change requires grams, missing or replacement')
    plan = compile_plan(source, conn, K._pin_agent())
    # Completed physical actions and their exact quantities remain evidence.
    done = set(state['completed'])
    old_done = [s for s in state['plan']['steps'] if s['id'] in done]
    hit_dishes = {r['id'] for r, _ in hits}
    previous = {s['id']: s for s in state['plan']['steps']}
    pending = [previous.get(s['id'], s) if s['dish'] not in hit_dishes else s
               for s in plan['steps'] if s['id'] not in done]
    plan['steps'] = old_done + pending
    affected_dishes = {r['id'] for r, _ in hits}
    affected = {s['id'] for s in state['plan']['steps'] if s['id'] not in done and s['dish'] in affected_dishes}
    for s in state['plan']['steps']:
        if s['id'] in state['running'] and s['id'] in affected:
            frame.countdown(K, state, s)
    state['running'] = {sid: end for sid, end in state['running'].items() if sid not in affected}
    state['step_started'] = {sid: at for sid, at in state.get('step_started', {}).items() if sid not in affected or sid in done}
    state['pace_started'] = {sid: at for sid, at in state.get('pace_started', {}).items() if sid not in affected}
    state['checks'] = {sid: check for sid, check in state.get('checks', {}).items() if sid not in affected}
    # Spend every old step/check keyboard atomically with the plan edit, even
    # when the subsequent control-bubble or card transport fails.
    for collection in ('step_cards', 'check_cards'):
        for card in state.get(collection, {}).values():
            if card.get('actions'):
                card['revision'] += 1
                card.update(actions=[], labels=[], rows=[], delivered=False)
    state['source'], state['plan'] = source, plan
    state['current'] = pending[0]['id'] if pending else None
    state['editing'] = True
    return None


def question(K, state, text):
    c = words(K)
    current = next((s for s in state['plan']['steps'] if s['id'] == state['current']), None)
    matches = []
    for ing in state['plan']['ingredients']:
        if ing['name'] in text or any(a in text for a in ing.get('aliases', [])):
            matches.append(ing)
    if matches:
        local = [i for i in matches if current and i['id'] in current['ingredients']]
        return c['quantity_answer'].format(ingredients=', '.join(cooking_card.quantity(c, i) for i in cooking_card.merged_ingredients(local or matches)))
    return None


def progress_names(state, step, split_words=True):
    recipe = next((r for r in state['source']['recipes'] if r['id'] == step['dish']), {})
    ingredients = [i for i in state['plan']['ingredients'] if i['id'] in step['ingredients']]
    names = [recipe.get('name', ''), step['name']]
    names += [i['name'] for i in ingredients] + [a for i in ingredients for a in i.get('aliases', [])]
    return names + [w for n in names for w in n.split() if len(w) >= 2] if split_words else names


def cue_progress(c, state, text):
    """Compatibility reader: the agent supplies a typed step action."""
    return None


def progress(K, state, text):
    return None


def apply(K, conn, text, *, message_id=None, now=None, require_owner=False, step_name=None, action=None, changes=None, render=True):
    """Every input first reaches the open session. Callback CAS is mandatory."""
    now = time.time() if now is None else now
    callback = action is None and step_name is None and changes is None and text.startswith('activity:')
    requested_jump = None
    if callback and require_owner:
        ctx = frame.inbound(K)
        if ctx.get('source') != 'callback' or ctx.get('callback_data') != 'opt:' + text:
            raise ValueError('callback provenance required: use only the bridge callback for this turn')
    if not callback and (require_owner or cooking_owner.latest(K) is not None):
        text = cooking_owner.require(K, text)
    text = cooking_owner.owner_words(text) if not callback else text
    if callback:
        if message_id is None:
            message_id = frame.inbound(K).get('message_id')
        staged = cooking_stages.apply(K, conn, text, message_id, now)
        if staged is not None:
            return staged
    conn.execute('BEGIN IMMEDIATE')
    state = frame.load(K, conn)
    if not state or state['phase'] in ('closed', 'cancelled'):
        conn.rollback()
        return dict(status='NO_SESSION', next='NO_PUSH')
    schedule.prepare(state)
    import made_dish
    ctx = frame.inbound(K)
    request = ctx.get('request_id')
    if request and not callback:
        request = json.dumps([request, action, step_name, changes], sort_keys=True)
    if request and request == state.get('last_request'):
        conn.rollback()
        return dict(status='DUPLICATE', next=frame.SILENT)
    if not callback and action == 'close':
        clean = copy.deepcopy(state)
        clean.pop('_before_closed', None)
        stock = _close_stock(conn)
        keys = ['cooking:%s:%s' % (state.get('stock_plan_key', state['id']), i['id'])
                for i in state['plan']['ingredients']]
        uses = {key: conn.execute('SELECT * FROM stock_use WHERE usage_key=?', (key,)).fetchone()
                for key in keys}
        dishes = {r[0] for r in conn.execute('SELECT id FROM made_dish WHERE owning_agent=? AND session_id=?',
                                             (K._pin_agent(), state['id']))}
        cooking_live.close(K, conn, state, text, now)
        after = _close_stock(conn)
        changed_uses = []
        for key, before in uses.items():
            row = conn.execute('SELECT * FROM stock_use WHERE usage_key=?', (key,)).fetchone()
            if row != before:
                changed_uses.append(dict(key=key, before=before, after=row))
        created = [list(r) for r in conn.execute('SELECT * FROM made_dish WHERE owning_agent=? AND session_id=?',
                                                (K._pin_agent(), state['id'])) if r[0] not in dishes]
        state['_before_closed'] = dict(state=clean,
            stock=[dict(owned_id=oid, before=row, after=after[oid])
                   for oid, row in stock.items() if row != after[oid]],
            stock_uses=changed_uses, made_dishes=created)
        state['revision'] += 1
        state['last_request'] = request
        frame.save(K, conn, state)
        conn.commit()
        result = show(K, conn, state, now)
        if result['status'] != 'DELIVERY_RETRY':
            date = datetime.fromtimestamp(now, ZoneInfo(state['source'].get('timezone', 'Asia/Seoul'))).date().isoformat()
            result['reply'] = '\uc694\ub9ac\ub97c \ub9c8\ucce4\uc5b4\uc694. \uc0ac\uc6a9\ud55c \uc7ac\ub8cc\ub97c \uc7ac\uace0\uc5d0 \ubc18\uc601\ud588\uc5b4\uc694.\n' + cooking_live.day_line(K, conn, date)
        return result
    if not callback and action == 'change':
        reply = adjust(K, state, changes or [], conn)
        if reply:
            conn.rollback()
            return dict(status='NEEDS_INGREDIENT', reply=reply, next='Interpret the requested ingredient change; use typed ingredient ids and values.')
        action = 'refresh'
    if callback:
        action = frame.action(state, text, message_id if message_id is not None else ctx.get('message_id'))
        if not action:
            conn.rollback()
            return dict(status='SPENT', next='NO_PUSH')
    else:
        if step_name is not None:
            candidates = [s for s in state['plan']['steps'] if step_name in (s['name'], s['id'])]
            if len(candidates) != 1:
                conn.rollback()
                raise ValueError('name one step id')
            step = candidates[0]
            if action == 'undo':
                reply = cooking_live.undo(K, state, step['id'], text)
                state['last_request'] = request
                state['revision'] += 1
                frame.save(K, conn, state)
                conn.commit()
                result = show(K, conn, state, now)
                result['reply'] = reply
                return result
            if action not in (None, 'done', 'timer', 'begin'):
                conn.rollback()
                raise ValueError('step action must be done, timer, begin or undo')
            index = state['plan']['steps'].index(step)
            action = (action or 'done') + '_' + str(index)
            state['phase'] = 'active'
            state['started_at'] = state.get('started_at') or now
            # A reported completion is physical evidence. Starting a step
            # still requires its prerequisites, just like tapping its card.
            if action.startswith('done_'):
                dropped = [dep for dep in step['depends'] if dep not in state['completed']]
                state.setdefault('relaxed', {}).setdefault(step['id'], []).extend(dropped)
                step['depends'] = [dep for dep in step['depends'] if dep in state['completed']]
            state['last_owner_progress'] = dict(words=text, steps=[step['id']])
        elif action not in ('start', 'cancel', 'refresh', 'edit', 'next', 'pin_next', 'pin_prev'):
            pantry_stock.decorate(K, conn, state['plan'])
            schedule.refresh(state, now)
            reply = question(K, state, text)
            conn.rollback()
            return dict(status='QUESTION', reply=reply, schedule=schedule_result(state),
                step=next((s for s in state['plan']['steps'] if s['id'] == state['current']), None),
                next='Answer timing only from the returned schedule, never from earlier messages. Answer the owner question once; interpret their intent yourself and choose a typed cooking verb. Keep session buttons open.')
    if action == 'words':
        conn.rollback()
        return dict(status='NO_MATCH', reply='')
    elif action.startswith(('hold_keep_', 'hold_end_')):
        # Bind the tap to the actual warning bubble AND its current hold episode.
        choice, index = action.split('_')[1:]
        dish = state['source']['recipes'][int(index)]['id']
        hold = next((h for h in schedule.holds(state, now) if h['dish'] == dish and h['warning']), None)
        key = schedule.hold_key(hold) if hold else None
        card = state.get('notice_cards', {}).get(key, {})
        if (state['phase'] != 'active' or state.get('editing') or not hold
                or key in state.get('hold_decisions', {}) or action not in card.get('actions', [])
                or str(card.get('message_id')) != str(message_id if message_id is not None else ctx.get('message_id'))
                or str(card.get('revision')) != text.split(':')[2]):
            conn.rollback()
            return dict(status='SPENT', next='NO_PUSH')
        state.setdefault('hold_decisions', {})[key] = dict(choice=choice, at=now, dish=dish, since=hold['since'])
        if choice == 'end':
            state.setdefault('served_dishes', {})[dish] = dict(served_at=now, hold_since=hold['since'], off_heat=True)
        schedule.refresh(state, now, focus=True)
        state['revision'] += 1
        state['last_request'] = request
        frame.save(K, conn, state)
        conn.commit()
        return show(K, conn, state, now)
    elif action == 'pin_next':
        state['pin_page'] = state.get('pin_page', 0) + 1
    elif action.startswith('jump_'):
        conn.rollback()
        return cooking_delivery.jump(K, state, int(action[5:]), words(K), conn, now)
    elif action.startswith('check_'):
        _, index, stage = action.split('_')
        sid = state['plan']['steps'][int(index)]['id']
        check = state.get('checks', {}).get(sid, {}).get(stage)
        if sid not in state['running'] or not check or check['acknowledged']:
            conn.rollback()
            return dict(status='SPENT', next='NO_PUSH')
        check['acknowledged'] = True
        step = state['plan']['steps'][int(index)]
        # A check before expiry only acknowledges looking. Once time is up,
        # that same owner confirmation completes this step, including a
        # reply to the half-time card that arrives after the timer ended.
        if now >= state['running'][sid]:
            cooking_live.complete_step(K, conn, state, step, now, text)
            made_dish.complete(K, conn, state, now)
            if len(state['completed']) == len(state['plan']['steps']):
                state['phase'] = 'closed'
        key = sid + ':' + stage
        acknowledgement = (words(K)['pin_done'] if sid in state['completed'] else words(K)['check_ack'])
        if not cooking_delivery.send(K, conn, state, key, acknowledgement, [], 'check_cards'):
            return dict(status='DELIVERY_RETRY', next='Run cooking show to retry delivery.')
    elif action == 'edit':
        conn.rollback()
        return dict(status='EDIT_INGREDIENTS', reply=words(K)['ask_change'], next='Say reply verbatim. Keep the current step and buttons open.')
    elif action == 'refresh':
        pass
    elif action == 'start':
        state['editing'] = False
        state['phase'] = 'active'
        state['actions'] = []
        if state['started_at'] is None:
            state['started_at'] = now
        state['current'] = next((s['id'] for s in state['plan']['steps'] if s['id'] not in state['completed']), None)
        if state['current'] is None:
            state['phase'] = 'closed'
    elif action in ('next', 'timer') or action.startswith(('done_', 'timer_', 'begin_')):
        if state['phase'] != 'active' or state.get('editing'):
            conn.rollback()
            return dict(status='SPENT', next='NO_PUSH')
        if not callback and action == 'next' and len({sid for sid, c in state.get('step_cards', {}).items() if c.get('actions')} | set(state['running'])) > 1:
            conn.rollback()
            return dict(status='NEEDS_STEP', reply=words(K)['needs_step'], next='Say reply verbatim; do not advance food.')
        sid = state['current']
        if action.startswith(('done_', 'timer_', 'begin_')):
            index = int(action.split('_')[1])
            sid = state['plan']['steps'][index]['id']
        elif sid is None:
            live = [s['id'] for s in state['plan']['steps'] if s['id'] in state['running']]
            sid = live[0] if len(live) == 1 else None
        visible = state.get('step_cards', {}).get(sid, {})
        step = next((s for s in state['plan']['steps'] if s['id'] == sid), None)
        done = set(state['completed'])
        depends = list(step['depends']) if step else []
        if action == 'timer' or action.startswith(('timer_', 'begin_')):
            depends += state.get('relaxed', {}).get(sid, [])
        auto_confirmation = next((h for h in state['history'] if h['step']['id'] == sid
            and h.get('completion_source') == 'timer'), None) if not callback and action.startswith('done_') else None
        if not step or (sid in done and auto_confirmation is None) or any(dep not in done for dep in depends) or not (visible.get('actions') or (not callback and (sid in state['running'] or action != 'next'))):
            conn.rollback()
            return dict(status='NEEDS_STEP', reply=words(K)['needs_step'], next='Say reply verbatim; do not advance food.')
        step = next(s for s in state['plan']['steps'] if s['id'] == sid)
        if ((action == 'timer' or action.startswith(('timer_', 'begin_'))) and sid not in state.get('step_started', {})
                and any(h['step']['id'] in depends and h['completed_at'] + h['step'].get('wait_after_sec', 0) > now
                        for h in state.get('history', []))):
            conn.rollback()
            return dict(status='SPENT', next='NO_PUSH')
        held = schedule.holds(state, now)
        live_schedule = schedule.derive(state, now) if held else None
        if sid not in state.get('step_started', {}) and any(
                key in hold['resource'] and live_schedule['occupied'][key] >= live_schedule['capacities'][key]
                for hold in held for key in schedule.resources(step)):
            conn.rollback()
            return dict(status='NEEDS_STEP', reply=words(K)['waiting'], next='Keep the hold and its resource occupied; do not start this blocked step.')
        if action.startswith('begin_'):
            if step['kind'] != 'active' or step['heat'] != 'none' or sid in state.get('pace_started', {}):
                conn.rollback()
                return dict(status='SPENT', next='NO_PUSH')
            state.setdefault('pace_started', {})[sid] = now
            state['step_started'][sid] = now
        elif action == 'timer' or action.startswith('timer_'):
            if not step['duration_sec'] or step['id'] in state['running']:
                conn.rollback()
                return dict(status='SPENT', next='NO_PUSH')
            live_schedule = schedule.derive(state, now)
            if callback and sid not in state.get('step_started', {}) and sid not in live_schedule['frontier']:
                conn.rollback()
                return dict(status='SPENT', next='NO_PUSH')
            frame.countdown(K, state, step, step['duration_sec'])
            state['running'][step['id']] = now + step['duration_sec']
            state.get('step_delays', {}).pop(step['id'], None)
            state.setdefault('step_started', {})[step['id']] = now
            if step['kind'] == 'unattended':
                state['current'] = None
        else:
            if auto_confirmation is not None:
                auto_confirmation.update(confirmed_at=now, owner_words=text)
            cooking_live.complete_step(K, conn, state, step, now, text)
            made_dish.complete(K, conn, state, now)
            if len(state['completed']) == len(state['plan']['steps']):
                state['phase'] = 'closed'
    elif action == 'cancel':
        clean = copy.deepcopy(state)
        clean.pop('_before_cancelled', None)
        state['_before_cancelled'] = dict(state=clean)
        for s in state['plan']['steps']:
            frame.countdown(K, state, s)
        state['running'], state['phase'] = {}, 'cancelled'
    if action.startswith(('begin_', 'timer_')) or action == 'timer':
        if sid in state.get('awaiting_start', []):
            state['awaiting_start'].remove(sid)
    schedule.refresh(state, now, focus=True)
    state['revision'] += 1
    state['last_request'] = request
    frame.save(K, conn, state)
    conn.commit()
    if not render:
        return dict(status='APPLIED', revision=state['revision'])
    result = show(K, conn, state, now)
    if requested_jump is not None and result['status'] != 'DELIVERY_RETRY':
        surfaced = cooking_delivery.jump(K, state, requested_jump, words(K), conn, now)
        if surfaced['status'] != 'JUMPED':
            return dict(status='DELIVERY_RETRY', reply='\ud0c0\uc774\uba38 \uce74\ub4dc\ub97c \ub2e4\uc2dc \ubcf4\uc5ec\ub4dc\ub9ac\uc9c0 \ubabb\ud588\uc5b4\uc694.', next='Send reply once and run cooking show; never create a generic reminder.')
    if result['status'] != 'DELIVERY_RETRY':
        if state['phase'] == 'closed':
            date = datetime.fromtimestamp(now, ZoneInfo(state['source'].get('timezone', 'Asia/Seoul'))).date().isoformat()
            result['reply'] = '\uc694\ub9ac\ub97c \ub9c8\ucce4\uc5b4\uc694. \uc0ac\uc6a9\ud55c \uc7ac\ub8cc\ub97c \uc7ac\uace0\uc5d0 \ubc18\uc601\ud588\uc5b4\uc694.\n' + cooking_live.day_line(K, conn, date)
        else:
            result['reply'] = '\uc694\ub9ac \uc9c4\ud589\uc0ac\ud56d\uc744 \uc5c5\ub370\uc774\ud2b8\ud588\uc5b4\uc694.'
        result['next'] = 'Send reply once. Code already updated the cooking cards. Never reply NO_PUSH after changing state.'
    return result


def held_target(K, conn, source, now):
    """The live same-meal source whose target the owner set, else None."""
    ids = {r['id'] for r in source['recipes']}
    held = []
    staged = cooking_stages.load(K, conn)
    if (staged and not staged.get('cancelled') and now - staged.get('at', now) < 43200
            and any(s.get('phase') != 'closed' for s in staged.get('stages') or [dict()])):
        held.append(staged['source'])
    live = frame.load(K, conn)
    if live and live.get('kind') == 'cooking' and live['phase'] not in ('closed', 'cancelled') \
            and not live['source'].get('_stage_context'):
        held.append(live['source'])
    return next((h for h in held if h.get('target_source') == 'owner' and h.get('target_at')
                 and {r['id'] for r in h['recipes']} == ids), None)


def target_guard(K, conn, payload, confirmed, now=None):
    """Owner target is owner-owned. -> refusal dict, or None (payload may inherit)."""
    now = time.time() if now is None else now
    source = normalize(payload)
    held = held_target(K, conn, source, now)
    if not held:
        return None
    want = source.get('target_at')
    if confirmed:
        if want is not None:
            payload['target_source'] = 'owner'
        return None
    if want is None:
        for key in ('target_at', 'guest_arrival_at', 'user_slack_sec', 'target_offset_sec'):
            if key in held and key not in payload:
                payload[key] = held[key]
        payload['target_source'] = 'owner'
        return None
    if abs(want - held['target_at']) > .001:
        zone = ZoneInfo(held['timezone']) if held.get('timezone') else timezone(timedelta(seconds=held.get('target_offset_sec', 0)))
        clock = lambda t: datetime.fromtimestamp(t, zone).isoformat()
        return dict(status='TARGET_OWNER_LOCKED', owner_target_at=clock(held['target_at']),
                    requested_target_at=clock(want),
                    next='The owner set this target. Ask the owner; rerun with --owner-confirmed only after '
                         'they agree to a new time. Lateness moves the ETA only.')
    payload['target_source'] = 'owner'
    return None


def catch_up(K, conn, done, start=None, now=None):
    """Apply several done steps and one start, then render once."""
    now = time.time() if now is None else now
    said = cooking_owner.latest(K) or ''
    steps = [(n, 'done') for n in done] + ([(start, 'timer')] if start else [])
    applied, refused = [], []
    for name, action in steps:
        try:
            r = apply(K, conn, said, step_name=name, action=action, now=now, render=False)
        except ValueError as exc:
            if conn.in_transaction:
                conn.rollback()
            r = dict(status='REFUSED', error=str(exc))
        (applied if r['status'] == 'APPLIED' else refused).append(
            name if r['status'] == 'APPLIED' else dict(step=name, status=r['status']))
    state = frame.load(K, conn)
    if not state:
        return dict(status='NO_SESSION', next='NO_PUSH')
    result = show(K, conn, state, now)
    if result['status'] != 'DELIVERY_RETRY':
        result['applied'], result['refused'] = applied, refused
        result['reply'] = '\uc694\ub9ac \uc9c4\ud589\uc0ac\ud56d\uc744 \uc5c5\ub370\uc774\ud2b8\ud588\uc5b4\uc694.'
        if state['phase'] == 'closed':
            date = datetime.fromtimestamp(now, ZoneInfo(state['source'].get('timezone', 'Asia/Seoul'))).date().isoformat()
            result['reply'] = '\uc694\ub9ac\ub97c \ub9c8\ucce4\uc5b4\uc694. \uc0ac\uc6a9\ud55c \uc7ac\ub8cc\ub97c \uc7ac\uace0\uc5d0 \ubc18\uc601\ud588\uc5b4\uc694.\n' + cooking_live.day_line(K, conn, date)
        result['next'] = 'Send reply once. Code already updated the cooking cards. Never reply NO_PUSH after changing state.'
    return result


def planning(K, conn, payload, now=None):
    """Prep and plan, preceded by shopping only when something is needed.

    Stock is read once per matched item and shared across recipes. Unknown
    stock is a check, never a guessed purchase. Gear uses active owned items.
    """
    source = normalize(payload)
    state = frame.create('cooking', compile_plan(source, conn, K._pin_agent()))
    state['source'], state['history'], state['step_started'] = source, [], {}
    now = time.time() if now is None else now
    schedule.refresh(state, now)
    state['chart_slots'] = copy.deepcopy(state['schedule']['slots'])
    c = words(K)
    groups = {}
    for ing in state['plan']['ingredients']:
        item = pantry_stock.resolve(conn, ing)
        key = (item if item is not None else ing['name'], pantry_stock.norm_unit(ing['unit']))
        if key not in groups:
            groups[key] = dict(ing, quantity=0, item=item)
        groups[key]['quantity'] += ing['quantity']
    shopping = []
    for ing in groups.values():
        amount, unknown = ing['quantity'], False
        for lot in pantry_stock.lots(conn, ing['item']) if ing['item'] is not None else []:
            if lot['quantity'] is None:
                unknown = True
                continue
            unit = pantry_stock.norm_unit(ing['unit'])
            if unit == pantry_stock.norm_unit(lot['unit']):
                amount -= lot['quantity']
            elif lot['unit_size'] and unit == pantry_stock.norm_unit(lot['size_unit']):
                amount -= lot['quantity'] * lot['unit_size']
            else:
                unknown = True
        if amount > 1e-9:
            shopping.append(c['stock_check'].format(name=ing['name']) if unknown else cooking_card.quantity(c, dict(ing, quantity=amount)))
    owned = {name for (name,) in conn.execute("SELECT i.name FROM shop_item i JOIN owned_item o ON o.item_id=i.id WHERE o.status='in_use'")}
    for gear in source.get('gear', []):
        if gear not in owned and gear not in source.get('owned_gear', []):
            shopping.append(gear)
    prep = [s['name'] for s in cooking_card.ordered(state, 'prep') if s.get('prep_ahead', True)]
    messages = [c['shopping_heading'] + '\n' + '\n'.join('- ' + x for x in shopping)] if shopping else []
    if source.get('prep_mode'):
        messages = [cooking_stages.plan_card(c, state)]
        return dict(status='PLANNED', messages=messages, stage_snapshot=state, next='Code posts this plan and arms stage alerts. Do not open a full cooking session.')
    messages += [c['prep_ahead_heading'] + '\n' + ('\n'.join('- ' + x for x in prep) or c['prep_ahead_none']),
                 cooking_card.pin_card(c, state, now, planning=True)]
    return dict(status='PLANNED', messages=messages, next='Send messages verbatim as separate messages in order. No session or pin exists. Bind or open this plan at cooking time.')


def digest(source):
    return hashlib.sha256(json.dumps(source, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def post_plan(K, conn, source, now=None):
    """Post the plan messages once per plan, in order, from code.

    The agent never relays them (10-23: the agent skipped plan and the owner
    never saw them). Each acknowledged message is recorded before the next is
    sent, so a retry resumes without repeating one. The dish set lands as
    today's fact so a restarted chat reads it back.
    """
    import start_card
    import fact_router
    key = PLANNED_PREFIX + K._pin_agent()
    raw = K._config_stored(conn, key)
    record = json.loads(raw) if raw else {}
    day = fact_router._shop(K)._today()
    if record.get('digest') != digest(source) or record.get('day') != day:
        preview = planning(K, conn, source, now)
        # A changed recipe/target revises the same meal's single plan card.
        # Keep its transport identity separately from acknowledged edits, so
        # a failed edit retries in place rather than posting a replacement.
        meal_key = source.get('plan_id') or sorted(r['id'] for r in source['recipes'])
        old_key = record.get('meal_key')
        if old_key is None:
            prior = (record.get('stage_snapshot') or {}).get('source', {})
            old_key = prior.get('plan_id') or sorted(r['id'] for r in prior.get('recipes', []))
        receipts = record.get('edit_ids') or record.get('posted', [])
        edit_ids = (receipts[:1] if source.get('prep_mode') and old_key == meal_key
                    and record.get('day') == day and len(record.get('messages', [])) == 1 else [])
        record = dict(digest=digest(source), day=day, posted=[], at=time.time() if now is None else now,
                      messages=preview['messages'], stage_snapshot=preview.get('stage_snapshot'),
                      meal_key=meal_key, edit_ids=edit_ids)
        K._config_put(conn, key, json.dumps(record, ensure_ascii=False))
        conn.commit()
    sender = K._photo_push_sh()
    for text in record['messages'][len(record['posted']):]:
        if not sender:
            return False
        index = len(record['posted'])
        ids = record.get('edit_ids', [])
        message_id = ids[index] if index < len(ids) else None
        args = ['--strict', '--text', text]
        if message_id is not None:
            args += ['--edit', str(message_id)]
        rc, ack = start_card._push(sender, args)
        if rc not in ((0, 8) if message_id is not None else (0, 6)):
            return False
        receipt = message_id if message_id is not None else ack.get('message_id')
        if receipt is None:
            return False
        record['posted'].append(receipt)
        K._config_put(conn, key, json.dumps(record, ensure_ascii=False))
        conn.commit()
    if not record.get('dishes_recorded'):
        names = [r['name'] for r in source['recipes']]
        fact_router.route(K, conn, 'menu: ' + ', '.join(names),
                          data=dict(kind='dish', names=names))
        record['dishes_recorded'] = True
        K._config_put(conn, key, json.dumps(record, ensure_ascii=False))
        conn.commit()
    LAST_POSTED[:] = []
    if source.get('prep_mode') and not source.get('_prep_pending'):
        cooking_stages.arm(K, conn, source, record['at'], record.get('stage_snapshot'))
        # A re-plan after a stage start has passed posts that ready stage now,
        # not on the next heartbeat.
        ticked = cooking_stages.tick(K, conn, time.time() if now is None else now)
        if ticked and ticked['status'] == 'DELIVERY_RETRY':
            return False
        LAST_POSTED[:] = (ticked or {}).get('posted', [])
    cooking_pending.consume(K, conn)
    conn.commit()
    return True


def plan_payload(path):
    """-> (payload, report): report is a PLAN_INVALID dict, or None when valid."""
    payload, error = cooking_plan.load(path)
    if error:
        return None, cooking_plan.report([error], [])
    errors, warnings = cooking_plan.check(payload, lambda p: compile_plan(normalize(p)))
    return payload, (cooking_plan.report(errors, warnings) if errors else None)


USAGE = ('cooking pending DISH [DISH ...] [--eaters owner,NAME] [--target ISO_WITH_OFFSET] | template | check PLAN.json | plan PLAN.json | prep-mode joined|separate|individual PLAN.json | prep-step STEP_ID ahead|cook PLAN.json | bind BLOCK_ID PLAN.json | '
         'open PLAN.json | catch-up --done ID,ID --start ID | add PLAN.json | add-step STEP.json | step-upd STEP_ID STEP.json | step-del STEP_ID | dish-del DISH_ID | delay SECONDS | delay-set SECONDS | input TEXT | close | close-undo | reopen | cancel | cancel-undo | check-step NAME | appliance | portions | weight PLAN.json RECIPE INGREDIENT | show')


def _mutable_state(K, conn, phases=('ready', 'active')):
    state = frame.load(K, conn)
    if not state or state.get('phase') not in phases:
        raise ValueError('no mutable cooking session')
    return state


def _save_mutation(K, conn, state, verb, detail):
    state.setdefault('mutation_history', []).append(dict(verb=verb, detail=detail,
                                                          revision=state.get('revision', 0)))
    state['revision'] = state.get('revision', 0) + 1
    state.pop('chart_slots', None)
    frame.save(K, conn, state)
    conn.commit()
    return show(K, conn, state)


def mutate_step(K, conn, step_id, replacement=None):
    try:
        return _mutate_step(K, conn, step_id, replacement)
    except Exception:
        conn.rollback()
        raise


def _mutate_step(K, conn, step_id, replacement):
    conn.execute('BEGIN IMMEDIATE')
    state = _mutable_state(K, conn)
    hits = [s for s in state['plan']['steps'] if s['id'] == step_id]
    if len(hits) != 1 or step_id in state.get('completed', []) or step_id in state.get('running', {}):
        raise ValueError('step-upd/step-del requires one pending step id')
    old = copy.deepcopy(hits[0])
    if replacement is None:
        # Retain the recipe graph for prerequisite resolution, but never compile
        # this work again when ingredients or other steps are added later.
        state['source'].setdefault('_deleted_steps', []).append(step_id)
        state['plan']['steps'] = [s for s in state['plan']['steps'] if s['id'] != step_id]
        for step in state['plan']['steps']:
            step['depends'] = [d for d in step.get('depends', []) if d != step_id]
        if state.get('current') == step_id:
            state['current'] = None
        return _save_mutation(K, conn, state, 'step-del', old)
    forbidden = {'id', 'dish'} & set(replacement)
    if forbidden:
        raise ValueError('step-upd cannot change id or dish')
    allowed = {field[0] for field in cooking_plan.STEP_FIELDS} - {'id'}
    unknown = set(replacement) - allowed
    if unknown:
        raise ValueError('unknown step fields: ' + ','.join(sorted(unknown)))
    recipe = next(r for r in state['source']['recipes'] if r['id'] == old['dish'])
    original = next(s for s in recipe['steps'] if recipe['id'] + '-' + s['id'] == step_id)
    patch = copy.deepcopy(replacement)
    if 'ingredients' in patch:
        local = {recipe['id'] + '-' + i['id']: i['id'] for i in recipe['ingredients']}
        patch['ingredients'] = [local.get(i, i) for i in patch['ingredients']]
    candidate = dict(original, **patch)
    errors = []
    cooking_plan._fields(candidate, cooking_plan.STEP_FIELDS, '$', errors)
    if errors:
        raise ValueError(json.dumps(errors))
    original.update(patch)
    if 'duration_sec' in patch:
        state['source'].setdefault('_duration_overrides', {})[step_id] = patch['duration_sec']
    source = normalize(state['source'])
    plan = compile_plan(source)
    fresh = next(s for s in plan['steps'] if s['id'] == step_id)
    if 'prereq' not in patch:
        fresh['depends'] = old['depends']
    state['source'] = source
    hits[0].clear()
    hits[0].update(fresh)
    return _save_mutation(K, conn, state, 'step-upd', dict(before=old, after=copy.deepcopy(hits[0])))


def delete_dish(K, conn, dish_id):
    conn.execute('BEGIN IMMEDIATE')
    state = _mutable_state(K, conn)
    recipes = [r for r in state['source']['recipes'] if r['id'] == dish_id]
    if len(recipes) != 1:
        raise ValueError('name one dish id')
    step_ids = {s['id'] for s in state['plan']['steps'] if s['dish'] == dish_id}
    if step_ids & (set(state.get('completed', [])) | set(state.get('running', {}))):
        raise ValueError('dish-del cannot remove started food')
    before = dict(recipe=copy.deepcopy(recipes[0]),
                  steps=copy.deepcopy([s for s in state['plan']['steps'] if s['id'] in step_ids]))
    state['source']['recipes'] = [r for r in state['source']['recipes'] if r['id'] != dish_id]
    all_step_ids = {dish_id + '-' + s['id'] for s in recipes[0]['steps']}
    state['source']['_deleted_steps'] = [sid for sid in state['source'].get('_deleted_steps', []) if sid not in all_step_ids]
    state['source']['_duration_overrides'] = {sid: value for sid, value in state['source'].get('_duration_overrides', {}).items()
                                             if sid not in all_step_ids}
    state['plan']['steps'] = [s for s in state['plan']['steps'] if s['id'] not in step_ids]
    ingredient_ids = {dish_id + '-' + i['id'] for i in recipes[0]['ingredients']}
    state['plan']['ingredients'] = [i for i in state['plan']['ingredients'] if i['id'] not in ingredient_ids]
    # Make implicit edges explicit before removing another dish's references.
    for recipe in state['source']['recipes']:
        for n, step in enumerate(recipe['steps']):
            refs = step.get('prereq', [recipe['steps'][n - 1]['id']] if n else [])
            step['prereq'] = [ref for ref in refs if not ref.startswith(dish_id + ':')]
    for step in state['plan']['steps']:
        step['depends'] = [d for d in step.get('depends', []) if d not in step_ids]
    return _save_mutation(K, conn, state, 'dish-del', before)


def _close_stock(conn):
    return {r[0]: list(r[1:]) for r in conn.execute(
        'SELECT s.owned_id,s.quantity,s.unit,s.unit_size,s.size_unit,o.status,o.status_on,o.item_id '
        'FROM owned_stock s JOIN owned_item o ON o.id=s.owned_id')}


def _undo_close(conn, snapshot):
    # Older snapshots lack the post-close values needed for a safe reversal.
    if 'stock_uses' not in snapshot or 'made_dishes' not in snapshot:
        raise ValueError('closed has no undo snapshot')
    stock = _close_stock(conn)
    restorations = []
    for change in snapshot['stock']:
        oid, before, after = change['owned_id'], change['before'], change['after']
        current = stock.get(oid)
        if current is None or current[1:] != after[1:] or current[0] is None:
            raise ValueError('close-undo conflicts with later stock changes')
        # Reverse only this close's deduction; later uses/restocks keep their
        # quantities. A changed unit, lot identity or lifecycle needs review.
        quantity = current[0] + before[0] - after[0]
        if quantity < 0:
            raise ValueError('close-undo conflicts with later stock changes')
        restorations.append((oid, quantity, before[4], before[5]))
    for change in snapshot['stock_uses']:
        row = conn.execute('SELECT * FROM stock_use WHERE usage_key=?', (change['key'],)).fetchone()
        if row is None or list(row) != change['after']:
            raise ValueError('close-undo conflicts with later stock changes')
    for dish in snapshot['made_dishes']:
        row = conn.execute('SELECT * FROM made_dish WHERE id=?', (dish[0],)).fetchone()
        if (row is None or list(row) != dish or
                conn.execute('SELECT 1 FROM made_dish_meal WHERE dish_id=?', (dish[0],)).fetchone()):
            raise ValueError('close-undo conflicts with later dish changes')
    # All conflict checks precede writes. The caller also rolls back on errors.
    for oid, quantity, status, status_on in restorations:
        conn.execute('UPDATE owned_item SET status=?,status_on=? WHERE id=?', (status, status_on, oid))
        conn.execute('UPDATE owned_stock SET quantity=? WHERE owned_id=?', (quantity, oid))
    for change in snapshot['stock_uses']:
        if change['before'] is None:
            conn.execute('DELETE FROM stock_use WHERE usage_key=?', (change['key'],))
        else:
            conn.execute('UPDATE stock_use SET item_id=?,quantity=?,unit=?,state=?,detail=?,asked=? WHERE usage_key=?',
                         change['before'][1:] + [change['key']])
    for dish in snapshot['made_dishes']:
        conn.execute('DELETE FROM made_dish WHERE id=?', (dish[0],))


def undo_terminal(K, conn, terminal):
    try:
        return _undo_terminal(K, conn, terminal)
    except Exception:
        conn.rollback()
        raise


def _undo_terminal(K, conn, terminal):
    conn.execute('BEGIN IMMEDIATE')
    state = frame.load(K, conn)
    if not state or state.get('phase') != terminal:
        raise ValueError('no %s cooking session' % terminal)
    snapshot = state.pop('_before_' + terminal, None)
    if not snapshot:
        raise ValueError('%s has no undo snapshot' % terminal)
    restored = snapshot['state']
    if terminal == 'closed':
        _undo_close(conn, snapshot)
    restored.setdefault('mutation_history', []).append(dict(verb=terminal + '-undo'))
    restored['revision'] = max(restored.get('revision', 0), state.get('revision', 0)) + 1
    frame.save(K, conn, restored)
    conn.commit()
    return show(K, conn, restored)


def reopen_terminal(K, conn):
    conn.execute('BEGIN IMMEDIATE')
    state = frame.load(K, conn)
    if not state or state.get('phase') not in ('closed', 'cancelled'):
        raise ValueError('no closed or cancelled cooking session')
    state['phase'] = 'active'
    state['current'] = next((s['id'] for s in state['plan']['steps']
                             if s['id'] not in state.get('completed', [])), None)
    state.setdefault('mutation_history', []).append(dict(verb='reopen'))
    state['revision'] = state.get('revision', 0) + 1
    frame.save(K, conn, state)
    conn.commit()
    return show(K, conn, state)


def cli(K, argv):
    command, args = (argv[0], argv[1:]) if argv else ('', [])
    rc = 0
    confirmed = '--owner-confirmed' in args
    args = [a for a in args if a != '--owner-confirmed']
    conn = K.event_conn()
    try:
        plan_file = args[-1] if command in ('check', 'plan', 'prep-mode', 'prep-step', 'bind', 'open', 'add') and args else None
        payload, invalid = plan_payload(plan_file) if plan_file else (None, None)
        locked = target_guard(K, conn, payload, confirmed) if payload and not invalid and command in ('plan', 'bind', 'open') else None
        gear_gate = cooking_live.appliance_gate(K, conn, payload) if payload and not invalid and not locked else None
        stage_gate = None
        if payload and not invalid and not locked and not gear_gate and command in ('plan', 'bind', 'open'):
            source = normalize(payload)
            stage_gate = cooking_stages.choice(K, conn, source)
            if not stage_gate and source.get('prep_mode'):
                payload['prep_mode'] = source['prep_mode']
                if '_prep_choices' in source:
                    payload['_prep_choices'] = source['_prep_choices']
        if invalid:
            out, rc = invalid, 2
        elif locked:
            out, rc = locked, 1
        elif gear_gate:
            out = gear_gate
            conn.commit()
        elif stage_gate:
            out = stage_gate
        elif command == 'prep-mode' and len(args) == 2:
            said = cooking_owner.require(K, cooking_owner.latest(K) or '')
            pending = cooking_stages.load(K, conn)
            if not pending or pending['meal'] != cooking_stages.identity(normalize(payload)):
                raise ValueError('no matching pending planning question')
            out = cooking_stages.set_choice(K, conn, args[0], owner_words=said)
        elif command == 'prep-step' and len(args) == 3:
            said = cooking_owner.require(K, cooking_owner.latest(K) or '')
            pending = cooking_stages.load(K, conn)
            if not pending or pending['meal'] != cooking_stages.identity(normalize(payload)):
                raise ValueError('no matching pending planning question')
            if args[1] not in ('ahead', 'cook'):
                raise ValueError('prep-step choice must be ahead or cook')
            out = cooking_stages.set_choice(K, conn, 'separate' if args[1] == 'ahead' else 'joined', step_id=args[0], owner_words=said)
        elif command == 'pending':
            args, eaters = K._pop_opt(args, '--eaters')
            args, target = K._pop_opt(args, '--target')
            state = cooking_pending.store(K, conn, args,
                                          eaters=eaters.split(',') if eaters is not None else None,
                                          target=target)
            conn.commit()
            out = dict(status='PENDING', pending=state, next='Context stored silently. Follow core-log/reference/cooking.md; never task-add for cooking.')
        elif command == 'template' and not args:
            import fact_router
            out = cooking_plan.template(fact_router.dishes_today(K, conn),
                                        cooking_live.appliance(K, conn))
            pending = cooking_pending.load(conn, K._pin_agent())
            if pending:
                out['pending'] = pending
        elif command == 'check' and len(args) == 1:
            errors, warnings = cooking_plan.check(payload, lambda p: compile_plan(normalize(p)))
            out = cooking_plan.report(errors, warnings, payload)
        elif command == 'plan' and len(args) == 1:
            source = normalize(payload)
            if not post_plan(K, conn, source):
                out = dict(status='DELIVERY_RETRY', next='Run the same cooking plan again; messages already posted are not repeated.')
            else:
                record = json.loads(K._config_stored(conn, PLANNED_PREFIX + K._pin_agent()))
                out = dict(status='PLANNED', posted=len(record['posted']), dishes=[r['name'] for r in source['recipes']],
                           posted_steps=list(LAST_POSTED),
                           next='The plan messages were posted by code; never repeat them. '
                                'Stage alerts are armed for a target plan. Without a target: run cooking open PLAN.json or cooking bind TASK_ID PLAN.json. '
                                'Otherwise reply exactly NO_PUSH.')
        elif command == 'bind' and len(args) == 2:
            out = bind(K, conn, int(args[0]), payload)
            if not post_plan(K, conn, normalize(payload)):
                out = dict(out, status='BOUND_DELIVERY_RETRY', next='Run the same cooking bind again; plan messages already posted are not repeated.')
            else:
                out['posted_steps'] = list(LAST_POSTED)
        elif command == 'open' and len(args) == 1:
            out = open_session(K, conn, payload, post=True)
            if out.get('status') == 'PLANNED':
                out['posted_steps'] = list(LAST_POSTED)
        elif command == 'add' and len(args) == 1:
            out = add_dishes(K, conn, payload)
        elif command == 'add-step' and len(args) == 1:
            step, error = cooking_plan.load(args[0])
            if error:
                out, rc = cooking_plan.report([error], []), 2
            else:
                out = add_step(K, conn, step)
                if out['status'] == 'PLAN_INVALID':
                    rc = 2
        elif command == 'delay' and len(args) == 1:
            out = shift_delay(K, conn, float(args[0]))
        elif command == 'delay-set' and len(args) == 1:
            state = frame.load(K, conn)
            if not state:
                raise ValueError('no cooking session to delay')
            wanted = positive(float(args[0]), 'delay seconds')
            current = float(state.get('source', {}).get('reported_delay_sec', 0))
            if wanted < current:
                raise ValueError('delay-set cannot move cooking clocks backwards')
            out = show(K, conn, state) if wanted == current else shift_delay(K, conn, wanted - current)
        elif command == 'step-upd' and len(args) == 2:
            out = mutate_step(K, conn, args[0], json.loads(Path(args[1]).read_text()))
        elif command == 'step-del' and len(args) == 1:
            out = mutate_step(K, conn, args[0])
        elif command == 'dish-del' and len(args) == 1:
            out = delete_dish(K, conn, args[0])
        elif command == 'timer-set' and len(args) == 2:
            conn.execute('BEGIN IMMEDIATE')
            state = frame.load(K, conn)
            step = next(s for s in state['plan']['steps'] if s['id'] == args[0])
            if step['id'] not in state['running']:
                raise ValueError('timer-set requires a running step')
            seconds = positive(float(args[1]), 'timer seconds')
            step['duration_sec'] = seconds
            step['check_at_sec'] = seconds / 2
            state['running'][step['id']] = state['step_started'][step['id']] + seconds
            frame.countdown(K, state, step, max(1, state['running'][step['id']] - time.time()))
            state['revision'] += 1
            frame.save(K, conn, state)
            conn.commit()
            out = show(K, conn, state)

        elif command == 'input' and len(args) == 1 and args[0].startswith('activity:'):
            out = apply(K, conn, args[0], require_owner=True)
        elif command == 'input' and len(args) == 1:
            # An inbound sentence is evidence, not a command. Keep the entry
            # point inert for old skills while explicit verbs do the writes.
            out = dict(status='NO_MATCH', reply='',
                       next='Choose an explicit cooking verb; do not infer one from words.')
        elif command == 'close' and not args:
            out = apply(K, conn, cooking_owner.latest(K) or '', action='close')
        elif command == 'close-undo' and not args:
            out = undo_terminal(K, conn, 'closed')
        elif command == 'cancel-undo' and not args:
            out = undo_terminal(K, conn, 'cancelled')
        elif command == 'reopen' and not args:
            out = reopen_terminal(K, conn)
        elif command == 'catch-up':
            args, done = K._pop_opt(args, '--done')
            args, start = K._pop_opt(args, '--start')
            if args or (not done and not start):
                raise ValueError('cooking catch-up --done ID,ID --start ID')
            out = catch_up(K, conn, [x for x in (done or '').split(',') if x], start)
        elif command in ('check-step', 'start-step', 'begin-step', 'undo-step') and len(args) == 1:
            action = {'check-step': 'done', 'start-step': 'timer', 'begin-step': 'begin', 'undo-step': 'undo'}[command]
            out = apply(K, conn, cooking_owner.latest(K) or '', step_name=args[0], action=action)
        elif command in ('start', 'cancel', 'refresh', 'edit') and not args:
            out = apply(K, conn, cooking_owner.latest(K) or '', action=command)
        elif command == 'tap' and len(args) == 1:
            out = apply(K, conn, args[0], require_owner=True)
        elif command == 'change' and len(args) == 1:
            changes = json.loads(Path(args[0]).read_text())
            conn.execute('BEGIN IMMEDIATE')
            state = frame.load(K, conn)
            reply = adjust(K, state, changes, conn)
            if reply:
                conn.rollback()
                out = dict(status='NEEDS_INGREDIENT', reply=reply)
            else:
                state['revision'] += 1
                frame.save(K, conn, state)
                conn.commit()
                out = show(K, conn, state)
        elif command == 'appliance' and args[:1] == ['--modes'] and len(args) in (2, 4) and (len(args) == 2 or args[2] == '--type'):
            said = cooking_owner.latest(K) or None
            modes = [m.strip() for m in args[1].split(',') if m.strip()]
            out = dict(status='APPLIANCE_STORED', appliance=cooking_live.appliance(
                K, conn, said, modes=modes, kind=args[3] if len(args) == 4 else None))
            conn.commit()
        elif command == 'appliance' and not args:
            out = dict(status='APPLIANCE', appliance=cooking_live.appliance(K, conn))
        elif command == 'portions' and not args:
            out = cooking_live.portions(K, conn)
        elif command == 'weight':
            args, basis = K._pop_opt(args, '--basis')
            args, grams = K._pop_opt(args, '--grams')
            args, count = K._pop_opt(args, '--count')
            if len(args) != 3:
                raise ValueError('cooking weight PLAN RECIPE INGREDIENT [--basis per_piece|total --grams N --count N]')
            source = json.loads(Path(args[0]).read_text())
            said = cooking_owner.latest(K) or ''
            out = cooking_live.weight(K, conn, source, args[1], args[2], said,
                basis=basis, grams=float(grams) if grams is not None else None,
                count=float(count) if count is not None else None)
            if out['status'] == 'WEIGHT_STORED':
                Path(args[0]).write_text(json.dumps(source, ensure_ascii=False, indent=2) + '\n')
            conn.commit()
        elif command == 'show' and not args:
            out = show(K, conn)
        else:
            out, rc = dict(status='USAGE', usage=USAGE, next='Run cooking template for the plan file shape.'), 2
    except ValueError as exc:
        if conn.in_transaction:
            conn.rollback()
        out, rc = dict(status='REFUSED', error=str(exc), next='Do not cook from chat; fix the cause and rerun the same cooking command.'), 1
    finally:
        conn.close()
    print(json.dumps(out, ensure_ascii=False))
    if rc:
        raise SystemExit(rc)
