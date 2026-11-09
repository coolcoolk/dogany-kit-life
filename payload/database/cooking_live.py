"""Owner-language operations for the cooking activity, meals and close copy."""
import copy
from datetime import datetime
import json
import math
import re
import statistics
from zoneinfo import ZoneInfo

import activity_session as frame
import cooking_owner
import pantry_stock
import kitchen


def compact(text):
    return re.sub(r'[\W_]+', '', text).casefold()


def complete_step(K, conn, state, step, now, evidence, *, completion_source=None):
    if step['id'] in state['completed']:
        return
    frame.countdown(K, state, step)
    started = state.get('pace_started', {}).get(step['id'])
    if completion_source != 'timer' and started is not None and 0 < now - started <= 86400 and step['kind'] == 'active' and step['heat'] == 'none':
        actual = max(1, int(now - started))
        row = conn.execute('SELECT samples,avg_sec FROM cooking_pace WHERE owning_agent=? AND recipe_id=? AND step_id=?',
                           (K._pin_agent(), step['dish'], step['id'][len(step['dish']) + 1:])).fetchone()
        samples, average = row if row else (0, actual)
        learned = round((average * samples + actual) / (samples + 1))
        conn.execute('INSERT INTO cooking_pace(owning_agent,recipe_id,step_id,samples,avg_sec,updated_at) VALUES(?,?,?,?,?,?) '
                     'ON CONFLICT(owning_agent,recipe_id,step_id) DO UPDATE SET samples=excluded.samples,avg_sec=excluded.avg_sec,updated_at=excluded.updated_at',
                     (K._pin_agent(), step['dish'], step['id'][len(step['dish']) + 1:], samples + 1, learned, int(now)))
    state['running'].pop(step['id'], None)
    if not step.get('ingredient_prep'):
        pantry_stock.cooking_use(K, conn, state, step)
    state['completed'].append(step['id'])
    if step['id'] in state.get('awaiting_start', []):
        state['awaiting_start'].remove(step['id'])
    state['history'].append(dict(step=copy.deepcopy(step), completed_at=now, owner_words=evidence,
        ingredients=copy.deepcopy([i for i in state['plan']['ingredients'] if i['id'] in step['ingredients']])))
    if completion_source is not None:
        state['history'][-1]['completion_source'] = completion_source
    if state.get('current') == step['id']:
        state['current'] = None


def complete_expired(K, conn, state, now):
    """Only armed timers complete by time; projected durations are not timers.

    Keep the deadline as completion time so late heartbeats preserve rest
    periods. The normal history/undo path also reopens every dependent step.
    Closing the session remains an explicit action with its own undo snapshot.
    """
    if state['phase'] != 'active' or state.get('editing'):
        return
    steps = {s['id']: s for s in state['plan']['steps']}
    for sid, end in sorted(list(state['running'].items()), key=lambda item: item[1]):
        if end <= now and sid not in state['completed']:
            complete_step(K, conn, state, steps[sid], end, '', completion_source='timer')


def close(K, conn, state, text, now):
    import made_dish
    for step in state['plan']['steps']:
        complete_step(K, conn, state, step, now, text)
        frame.countdown(K, state, step)
    state['phase'], state['running'], state['current'] = 'closed', {}, None
    state['closed_owner_words'] = text
    made_dish.complete(K, conn, state, now)


def undo(K, state, step_id, text=''):
    """The agent names a started or completed step; words are audit evidence only."""
    matches = [s for s in state['plan']['steps']
               if step_id in (s['id'], s['name']) and (s['id'] in state['completed'] or s['id'] in state['running']
                    or s['id'] in state.get('step_started', {}))]
    if len(matches) != 1:
        raise ValueError('name one started or completed step id')
    ids = {matches[0]['id']}
    # Reopen dependent ticks as well, retaining the audit trail. Stock use is
    # idempotent; undoing a UI tick never manufactures food back into stock.
    for _ in state['plan']['steps']:
        ids.update(s['id'] for s in state['plan']['steps'] if (set(s['depends']) | set(state.get('relaxed', {}).get(s['id'], []))) & ids)
    state.setdefault('undo_history', []).append(dict(owner_words=text, steps=sorted(ids)))
    state['completed'] = [s for s in state['completed'] if s not in ids]
    state['history'] = [h for h in state['history'] if h['step']['id'] not in ids]
    for step in state['plan']['steps']:
        if step['id'] in ids:
            frame.countdown(K, state, step)
            state['running'].pop(step['id'], None)
            state.get('step_started', {}).pop(step['id'], None)
            state.get('checks', {}).pop(step['id'], None)
            state.get('pace_started', {}).pop(step['id'], None)
            state.get('step_delays', {}).pop(step['id'], None)
            if step['id'] in state.get('step_cards', {}):
                state['step_cards'][step['id']]['actions'] = []
            restored = state.get('relaxed', {}).pop(step['id'], [])
            step['depends'] = list(dict.fromkeys(step['depends'] + restored))
    # Focus may offer the reverted step again, but only an explicit start
    # may recreate its actual start time or pace sample.
    state['awaiting_start'] = sorted(set(state.get('awaiting_start', [])) | ids)
    state['current'] = None
    state.pop('chart_slots', None)
    return matches[0]['name'] + ' \uccb4\ud06c\ub97c \ub418\ub3cc\ub838\uc5b4\uc694. \ub0a8\uc740 \uc2dc\uac04\uc744 \ub2e4\uc2dc \uacc4\uc0b0\ud588\uc5b4\uc694.'


def day_line(K, conn, date, *, snapshot=None):
    snap = snapshot or K.nutrition_snapshot(conn, date)
    if snap.target is None:
        return ''
    left = round(snap.remaining_kcal)
    goals = {key: snap.bands[key]['max'] if key in snap.bands else snap.target.get(key)
             for key in ('fat', 'carb', 'protein')}
    over = ['%s %d/%dg (\ubaa9\ud45c\ubcf4\ub2e4 %dg \ub9ce\uc544\uc694)' %
            (name, round(snap.eaten[key]), round(goals[key]),
             round(snap.eaten[key]) - round(goals[key]))
            for key, name in [('fat', '\uc9c0\ubc29'), ('carb', '\ud0c4\uc218\ud654\ubb3c'), ('protein', '\ub2e8\ubc31\uc9c8')]
            if goals[key] is not None and round(snap.eaten[key]) > round(goals[key])]
    line = '\uc624\ub298 ' + (', '.join(over) + '. ' if over else '') + ('%dkcal \ub0a8\uc558\uc5b4\uc694.' % left if left >= 0 else '%dkcal \ucd08\uacfc\ud588\uc5b4\uc694.' % -left)
    if left <= 100:
        line += ' \uc624\ub298 \ucd94\uac00 \uc2dd\uc0ac \uc5ec\uc720\ub294 \uac70\uc758 \uc5c6\uc5b4\uc694. \ubc30\uac00 \ubd80\ub974\uba74 \ub354 \ub4dc\uc2e4 \ud544\uc694 \uc5c6\uc5b4\uc694.'
    elif over:
        line += ' \uc774\ud6c4 \ub4dc\uc2e4 \ub54c\ub294 \ucd08\uacfc\ud55c \uc601\uc591\uc18c\ub97c \ub354 \ub298\ub9ac\uc9c0 \uc54a\ub294 \uac00\ubcbc\uc6b4 \uc591\uc73c\ub85c \uc7a1\uc544\uc694.'
    else:
        line += ' \uc774\ud6c4 \ub4dc\uc2e4 \uc591\uc740 \uc774 \ub0a8\uc740 \uc5f4\ub7c9 \uc548\uc5d0\uc11c \uc815\ud558\uba74 \ub3fc\uc694.'
    return line


def day_line_preview(K, conn, date):
    """Owner-review band candidate; production copy remains in day_line."""
    snap = K.nutrition_snapshot(conn, date)
    block = K.nutrition_block(conn, date, variant='target', lang='ko', snapshot=snap)
    return day_line(K, conn, date, snapshot=snap) + '\n' + block.text


def portions(K, conn):
    rows = conn.execute('SELECT SUM(kcal) FROM ' + K.MEALS_V32 +
                        " WHERE owning_agent=? AND meal IN ('breakfast','lunch','dinner','\uc544\uce68','\uc810\uc2ec','\uc800\ub141') GROUP BY date,meal ORDER BY date DESC LIMIT 30",
                        (K._health_agent(conn),)).fetchall()
    values = [r[0] for r in rows if r[0] and r[0] > 0]
    typical = round(statistics.median(values)) if values else None
    companion = round(typical * .8) if typical else None
    reply = '\ud30c\uc774\ub294 1\uc778 1\uc7a5\ubd80\ud130 \uc7a1\uc544\uc694. 80g \ud55c \uc7a5\uc774\uba74 \uc57d 440kcal\uc608\uc694.'
    if typical:
        reply += ' \ud3c9\uc18c \ud55c \ub07c\uac00 \uc57d %dkcal\ub77c, \ud30c\uc774\ub97c \uacc1\ub4e4\uc774\uba74 \uc8fc \uc694\ub9ac\ub294 \uc791\uac8c \uc7a1\uc544\uc57c \ud574\uc694.' % typical
    return dict(status='PORTION_GUIDANCE', owner_typical_meal_kcal=typical,
                companion_initial_meal_kcal=companion,
                owner_main_kcal_with_pastry=max(0, typical-440) if typical else None,
                companion_main_kcal_with_pastry=max(0, companion-440) if companion else None,
                companion='No history: start smaller than owner; ask once if the amount matters.',
                side_pieces_per_person=1, pastry_sheet_80g_kcal=440,
                reply=reply,
                next='Use owner meal history for the main dish. Ask ambiguous weight once and store weight_basis on the ingredient. Split the recipe only from owner words; uneven shares are allowed.')


APPLIANCE_TYPES = ('oven', 'air_fryer')
APPLIANCE_MODES = ('top_bottom', 'convection')


def appliance(K, conn, text=None, modes=None, kind=None):
    """Read saved modes, or store explicitly typed modes; text is evidence."""
    if modes is not None:
        bad = [m for m in modes if m not in APPLIANCE_MODES] or ([kind] if kind and kind not in APPLIANCE_TYPES else [])
        if bad or not modes:
            raise ValueError('modes must be one or more of %s; type one of %s' % (','.join(APPLIANCE_MODES), ','.join(APPLIANCE_TYPES)))
        return _appliance_store(K, conn, text, sorted(set(modes)), kind or 'oven')

    key = 'cooking.appliance.' + K._pin_agent()
    raw = K._config_stored(conn, key)
    saved = json.loads(raw) if raw else {}
    owned = conn.execute('SELECT appliance_type,modes_json,model FROM cooking_appliance WHERE owning_agent=? AND active=1 ORDER BY updated_at DESC LIMIT 1',
                         (K._pin_agent(),)).fetchone()
    if owned:
        saved.update(type=owned[0], modes=json.loads(owned[1]), model=owned[2])
    known = kitchen.appliance(conn)
    if known:
        saved.update(known)
    return saved


def _appliance_store(K, conn, text, modes, kind):
    key = 'cooking.appliance.' + K._pin_agent()
    raw = K._config_stored(conn, key)
    saved = json.loads(raw) if raw else {}
    known = kitchen.appliance(conn)
    saved.update(type=kind, modes=modes)
    if known.get('model'):
        saved['model'] = known['model']
    if text:
        saved.setdefault('owner_words', []).append(text)
    if known.get('owned_id'):
        kitchen.fact_add(K, conn, known['owned_id'], 'modes', modes, commit=False)
    K._config_put(conn, key, json.dumps(saved, ensure_ascii=False))
    conn.execute("INSERT INTO cooking_appliance(owning_agent,appliance_type,modes_json,model,owner_words,active,updated_at) "
                 "VALUES(?,?,?,?,?,1,strftime('%s','now')) ON CONFLICT(owning_agent,appliance_type) DO UPDATE SET "
                 "modes_json=excluded.modes_json,model=coalesce(excluded.model,cooking_appliance.model),owner_words=excluded.owner_words,active=1,updated_at=excluded.updated_at",
                 (K._pin_agent(), kind, json.dumps(modes), saved.get('model'), text or ''))
    return saved


def appliance_gate(K, conn, payload):
    if not any(s['heat'] == 'oven' for r in payload['recipes'] for s in r['steps']):
        return None
    saved = appliance(K, conn)
    if not saved.get('type') or not saved.get('modes'):
        asked = saved.get('asked')
        saved['asked'] = True
        K._config_put(conn, 'cooking.appliance.' + K._pin_agent(), json.dumps(saved, ensure_ascii=False))
        return dict(status='NEEDS_APPLIANCE', reply=None if asked else '\uc624\ube10 \uc885\ub958\uc640 \uc4f8 \uc218 \uc788\ub294 \ubaa8\ub4dc(\uc704\uc544\ub798 \uac00\uc5f4\xb7\uc5f4\ud48d)\ub97c \uc54c\ub824\uc8fc\uc138\uc694.',
                    next='Ask reply once if present; otherwise wait for that answer. Then read the answer yourself and run cooking appliance --modes top_bottom,convection (only the modes the owner said; \"모두/다 돼\" = both) to store it. Do not give heat instructions before it is stored.')
    text = json.dumps(payload, ensure_ascii=False)
    if re.search(r'\uc5f4\ud48d|convection|fan', text, re.I) and 'convection' not in saved['modes']:
        return dict(status='APPLIANCE_MODE_MISMATCH', next='Rewrite heat instructions for the stored top/bottom oven mode, then re-check the plan.')
    return None


def weight(K, conn, payload, recipe_id, ingredient_id, text, *, basis=None, grams=None, count=None):
    ing = next(i for r in payload['recipes'] if r['id'] == recipe_id for i in r['ingredients'] if i['id'] == ingredient_id)
    key = 'cooking.weight.%s.%s.%s' % (K._pin_agent(), recipe_id, ingredient_id)
    raw = K._config_stored(conn, key)
    prior = json.loads(raw) if raw else {}
    if ing.get('weight_basis'):
        return dict(status='WEIGHT_STORED', ingredient=ing)
    match = re.search(r'(\d+(?:\.\d+)?)\s*(?:g|\uadf8\ub78c|\uadf8\ub7a8)', text, re.I)
    grams = grams if grams is not None else float(match[1]) if match else prior.get('grams')
    count_match = re.search(r'(\d+)\s*(?:\uac1c|\uc7a5)', text)
    count = count if count is not None else float(count_match[1]) if count_match else prior.get('count')
    if basis not in (None, 'per_piece', 'total'):
        raise ValueError('weight basis must be per_piece or total')
    for value in (grams, count):
        if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value <= 0):
            raise ValueError('weight grams and count must be positive finite numbers')
    if grams and basis and (basis == 'per_piece' or count):
        ing.update(weight_basis=basis, weight_owner_words=text)
        ing['grams_per_unit'] = grams if basis == 'per_piece' else grams / count
        if basis == 'total':
            ing['weight_package_units'] = count
        K._config_put(conn, key, json.dumps(dict(grams=grams, count=count, basis=basis, owner_words=text), ensure_ascii=False))
        return dict(status='WEIGHT_STORED', ingredient=ing)
    K._config_put(conn, key, json.dumps(dict(grams=grams, count=count, asked=True, owner_words=text), ensure_ascii=False))
    return dict(status='NEEDS_WEIGHT', reply=('%gg\uc774 \ud55c \uc7a5\uc774\uc5d0\uc694, \uc804\uccb4\uc608\uc694?' % grams if grams else '\ud55c \uc7a5\uc758 \ubb34\uac8c\uc778\uac00\uc694, \uc804\uccb4 \ubb34\uac8c\uc778\uac00\uc694?') if not prior.get('asked') else None,
                next='Ask this weight basis once, wait for the owner answer, then run cooking weight with typed --basis per_piece|total and the stated --grams/--count. Do not assume grams per sheet.')
