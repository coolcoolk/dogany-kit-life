"""Completed cooking output, ingredient evidence and atomic fractional meals.

Nutrition is frozen at completion, independently of recipe/cache edits. Missing
nutrition remains explicit; it is never silently counted as zero. Refresh can
fill an unresolved dish after an ingredient cache lookup, before its first meal.
"""
import copy
from datetime import datetime
from fractions import Fraction
import json
import math
import re
from zoneinfo import ZoneInfo

import activity_session as frame
import meal_slots

NUTRIENTS = ('kcal', 'protein', 'carb', 'fat', 'fiber', 'sugar', 'alcohol')


def ready(c):
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE name='made_dish' AND type='table'").fetchone())


def catalog(K):
    import cooking_session
    return cooking_session.data(K, 'cooking-nutrition.json')['foods']


def numeric(value, positive=False):
    value = float(value)
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise ValueError('quantity must be finite and positive')
    return value


def nutrition(K, c, ing):
    names = [ing.get('food_name'), ing['name']] + ing.get('aliases', [])
    names = [n for n in names if n]
    default = next((f for f in catalog(K) if any(n.casefold() in [a.casefold() for a in f['names']] for n in names)), None)
    unit, qty = ing['unit'].lower(), numeric(ing['quantity'])
    estimated = False
    if ing.get('grams_per_unit') is not None:
        grams = qty * numeric(ing['grams_per_unit'], True)
    elif unit == 'g':
        grams = qty
    elif unit == 'kg':
        grams = qty * 1000
    elif unit in ('ml', 'l'):
        grams = qty * (1000 if unit == 'l' else 1) * numeric(ing.get('density_g_ml', 1), True)
        estimated = 'density_g_ml' not in ing
    elif unit in ('piece', 'pieces', '\uac1c') and default and default.get('piece_grams'):
        grams = qty * default['piece_grams']
        estimated = True
    elif unit in ('tbsp', '\ud070\uc220') and default and default.get('tablespoon_grams'):
        grams = qty * default['tablespoon_grams']
        estimated = True
    else:
        return dict(missing='ingredient unit conversion', ingredient=ing['name'])
    hit = None
    for name in names:
        row = K.foodcache_lookup(name, grams, conn=c)
        if row.get('found') and row.get('base_grams') and all(row.get(k) is not None for k in NUTRIENTS[:4]):
            hit = row
            break
    if hit is None and default:
        hit = dict(default, **{k: default[k] * grams / 100 for k in NUTRIENTS})
    if hit is None:
        return dict(missing='ingredient nutrition lookup', ingredient=ing['name'], grams=grams)
    vals = {k: numeric(hit.get(k) or 0) for k in NUTRIENTS}
    # meal_food has no alcohol column. A declared spirit ABV is stronger evidence
    # than a generic food row; missing ABV for known spirits is an explicit estimate.
    spirit = default and default['ndb'] == '14037'
    if 'abv' in ing or spirit:
        abv = numeric(ing.get('abv', .4))
        if abv > 1 or unit not in ('ml', 'l'):
            return dict(missing='ABV requires ml/l and a fraction from 0 to 1', ingredient=ing['name'])
        vals['alcohol'] = qty * (1000 if unit == 'l' else 1) * abv * .789
        vals['kcal'] = K._meal_kcal(vals)
        estimated = estimated or 'abv' not in ing
    return dict(grams=grams, source=hit.get('source'), estimated=bool(estimated or hit.get('estimated')), **vals)


def totals(K, c, ingredients):
    total = dict.fromkeys(NUTRIENTS, 0.)
    missing, estimated = [], False
    for ing in ingredients:
        value = nutrition(K, c, ing)
        ing['nutrition'] = value
        if value.get('missing'):
            missing.append(value)
            continue
        for key in total:
            total[key] += value[key]
        estimated = estimated or value['estimated']
    return {k: round(v, 6) for k, v in total.items()}, missing, estimated


def complete(K, c, state, now):
    if not ready(c) or state['source']['block'] == 'prep':
        return
    for recipe in state['source']['recipes']:
        steps = [s['id'] for s in state['plan']['steps'] if s['dish'] == recipe['id']]
        if not steps or not set(steps) <= set(state['completed']):
            continue
        exists = c.execute('SELECT id FROM made_dish WHERE owning_agent=? AND session_id=? AND recipe_id=?',
                           (K._pin_agent(), state['id'], recipe['id'])).fetchone()
        if exists:
            _capture(K, c, state, exists[0], recipe)
            continue
        # First physically completed use freezes the quantity, just like pantry
        # deductions. Later steps referring to that ingredient do not add it twice.
        used = {}
        for h in state.get('stage_inputs', []) + state['history']:
            if h['step']['dish'] == recipe['id']:
                for ing in h['ingredients']:
                    used.setdefault(ing['id'], copy.deepcopy(ing))
        if state['source']['block'] == 'cook':
            import cooking_session
            all_plan = cooking_session.compile_plan(dict(state['source'], block='all'))
            ids = {recipe['id'] + '-' + i['id'] for i in recipe['ingredients']}
            for ing in all_plan['ingredients']:
                if ing['id'] in ids:
                    used.setdefault(ing['id'], dict(copy.deepcopy(ing), evidence='prior prep declared in cook plan'))
        ingredients = list(used.values())
        for ing in ingredients:
            row = c.execute('SELECT item_id,quantity,unit,state,detail FROM stock_use WHERE usage_key=?',
                            ('cooking:%s:%s' % (state.get('stock_plan_key', state['id']), ing['id']),)).fetchone()
            if row:
                ing['stock_use'] = dict(zip(('item_id', 'quantity', 'unit', 'state', 'detail'), row))
        total, missing, estimated = totals(K, c, ingredients)
        ratio = recipe['amount'] / recipe['base_amount']
        y = recipe.get('yield', dict(quantity=1, unit='whole'))
        count = numeric(y['quantity'], True) * (ratio if y['unit'] != 'whole' else 1)
        cur = c.execute('INSERT INTO made_dish(owning_agent,session_id,recipe_id,name,aliases,completed_at,ingredients,nutrition,missing,estimated,yield_quantity,yield_unit) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                  (K._pin_agent(), state['id'], recipe['id'], recipe.get('dish_name', recipe['name']),
                   json.dumps(recipe.get('aliases', [])), now, json.dumps(ingredients), json.dumps(total),
                   json.dumps(missing), int(estimated), count, y['unit']))
        _capture(K, c, state, cur.lastrowid, recipe)


def _capture(K, c, state, dish_id, recipe):
    # made_dish.completed_at is stock metadata. The session's owner progress
    # supplies cooking occurrence evidence through cooking_experience below.
    evidence = [('made_dish', dish_id)]
    for ing in c.execute('SELECT ingredients FROM made_dish WHERE id=?', (dish_id,)):
        for part in json.loads(ing[0]):
            item = part.get('stock_use', {}).get('item_id')
            if item is not None:
                evidence.append(('shop_item', item))
            usage = c.execute('SELECT rowid FROM stock_use WHERE usage_key=?',
                              ('cooking:%s:%s' % (state['id'], part['id']),)).fetchone()
            if usage:
                evidence.append(('stock_use', usage[0]))
    bid = state.get('block_id')
    if bid is not None:
        eid = K._experience_block(c, bid, evidence=evidence)
        import cooking_experience
        cooking_experience.capture(K, c, state, eid)
    else:
        # Explicit evidence attachment may adopt a provisional container.
        # Recover its surviving occurrence before another dish completes.
        eid = state.get('experience_id')
        if eid is not None and K._event_row(c, eid) is None:
            linked = c.execute("SELECT re.event_id FROM made_dish d JOIN record r ON r.source_table='made_dish' AND r.source_id=d.id JOIN record_event re ON re.record_id=r.id WHERE d.session_id=? AND d.owning_agent=? ORDER BY re.event_id LIMIT 1",
                               (state['id'], K._pin_agent())).fetchone()
            state['experience_id'] = linked[0] if linked else None
        # Several dishes from one unplanned session share one experience.
        if state.get('experience_id') is None:
            import cooking_experience
            state['experience_id'] = cooking_experience.session_event(K, c, state, recipe.get('dish_name', recipe['name']))
        state['experience_id'] = K.experience_ensure(c, recipe.get('dish_name', recipe['name']),
            evidence=evidence, event_id=state.get('experience_id'))
        import cooking_experience
        cooking_experience.capture(K, c, state, state['experience_id'])


def dishes(K, c, query=''):
    if not ready(c):
        return []
    cur = c.execute('SELECT * FROM made_dish WHERE owning_agent=? ORDER BY completed_at DESC,id', (K._pin_agent(),))
    result = []
    for row in cur:
        d = dict(zip([x[0] for x in cur.description], row))
        for key in ('aliases', 'ingredients', 'nutrition', 'missing'):
            d[key] = json.loads(d[key])
        if not query or str(d['id']) == query or any(query.casefold() in n.casefold() for n in [d['name']] + d['aliases']):
            result.append(d)
    return result


def portion(text, dish):
    """Fractions always refer to the original whole, pieces to known yield."""
    text = text.strip().lower()
    if text in ('half', '\ubc18', '\uc808\ubc18', '반반'):
        return .5
    if text in ('all', 'whole', '\uc804\ubd80', '\ub2e4', '\ud55c \uac1c \uc804\uccb4'):
        return 1.
    if text in ('left', 'remaining', '\ub0a8\uc740 \uac70', '\ub098\uba38\uc9c0'):
        return dish['remaining']
    m = re.fullmatch(r'(\d+(?:\.\d+)?)\s*(pieces?|servings?|cups?|glass(?:es)?|\uac1c|\uc794|\uc778\ubd84)', text)
    if m:
        kinds = {'piece': ('piece', 'pieces', '\uac1c'), 'serving': ('serving', 'servings', '\uc778\ubd84'), 'glass': ('glass', 'glasses', 'cup', 'cups', '\uc794')}
        if not any(m[2] in units and dish['yield_unit'] in units for units in kinds.values()):
            raise ValueError('piece/serving portion requires matching known dish yield')
        return numeric(m[1], True) / dish['yield_quantity']
    if re.fullmatch(r'\d+\s*/\s*\d+|(?:0?\.\d+)|1', text):
        try:
            value = float(Fraction(text.replace(' ', '')))
        except ZeroDivisionError:
            raise ValueError('fraction denominator must be positive')
        if 0 < value <= 1:
            return value
    raise ValueError('portion must be a fraction, half, remaining or known pieces/servings')


def split_shares(value):
    """Parse an amount only; this never selects or changes a cooking action."""
    if isinstance(value, str):
        percentages = re.findall(r'(?<![\d.])[+-]?\d+(?:\.\d+)?\s*[%％]', value)
        halves = re.findall(r'반반', value)
        if len(percentages) == 1 and not halves:
            owner = float(percentages[0][:-1].strip()) / 100
            if not 0 < owner <= 1:
                raise ValueError('invalid shared dish portions')
            # An owner's percentage does not declare the companion's intake.
            return owner, 0.
        if len(halves) == 1 and not percentages:
            return .5, .5
        return None
    if not isinstance(value, dict) or 'companion' not in value:
        return None
    owner, other = float(value['owner']), float(value['companion'])
    if not math.isfinite(owner) or not math.isfinite(other) or owner <= 0 or other < 0 or owner + other > 1 + 1e-9:
        raise ValueError('invalid shared dish portions')
    return owner, other


def cancelled_plan(K, c, state, text, date):
    """Freeze declared cooked food without reviving session UI or timers."""
    import cooking_session
    import pantry_stock
    stamp = state.get('started_at') or state.get('schedule', {}).get('computed_at')
    if stamp is None or datetime.fromtimestamp(stamp, ZoneInfo(
            state['source'].get('timezone', 'Asia/Seoul'))).date().isoformat() != date:
        return False
    snapshot = copy.deepcopy(state)
    snapshot['source']['block'] = 'all'
    snapshot['plan'] = cooking_session.compile_plan(snapshot['source'])
    # Preserve previously used quantities first, as in normal completion.
    for step in snapshot['plan']['steps']:
        if not step.get('ingredient_prep'):
            pantry_stock.cooking_use(K, c, snapshot, step)
        snapshot['history'].append(dict(step=step, completed_at=stamp, owner_words=text,
            ingredients=copy.deepcopy([i for i in snapshot['plan']['ingredients']
                                       if i['id'] in step['ingredients']])))
    snapshot['completed'] = [s['id'] for s in snapshot['plan']['steps']]
    complete(K, c, snapshot, stamp)
    return True


def resolve_cooked(K, c, text, date, *, part=None, companion=None):
    state = frame.load(K, c)
    if not state or state.get('kind') != 'cooking' or state.get('phase') != 'cancelled':
        return resolve(K, c, '', date, part=part, companion=companion)
    shares = split_shares(text) if part is None else None
    if not cancelled_plan(K, c, state, text, date):
        return dict(status='NO_DISH')
    rows = [d for d in dishes(K, c) if d['session_id'] == state['id']]
    if not rows:
        return dict(status='NO_DISH')
    if shares is None and part is None:
        # Like cooking close, freeze the declared food before asking its share.
        c.commit()
        return resolve(K, c, str(rows[0]['id']), date)
    requests = []
    for d in rows:
        owner, other = shares if shares is not None else (portion(part, d), 0.)
        if companion is not None:
            other = portion(companion, d)
        label = dict(owner=owner, companion=other, words=text)
        split_shares(label)
        requests.append((d, owner, label))
    # As with normal close, food survives an unresolved nutrition lookup;
    # refresh can then fill it before the atomic meal write is retried.
    c.commit()
    return dict(status='RESOLVED', requests=requests)


def resolve(K, c, query, date=None, *, part=None, companion=None):
    """The agent supplies dish identity and portion; query is a lookup value."""
    rows = dishes(K, c, query)
    if not rows:
        return dict(status='NO_DISH')
    if not query:
        rows = [d for d in rows if d['session_id'] == rows[0]['session_id']]
    if len(rows) != 1 and (query or part is not None):
        return dict(status='AMBIGUOUS_DISH', candidates=[dict(id=d['id'], name=d['name'], remaining=d['remaining']) for d in rows], next='Choose a made-dish id; do not ask for weight.')
    d = rows[0]
    if part is None:
        state = frame.load(K, c)
        company = state and state.get('source', {}).get('eaters', [])
        reply = '두 분은 요리별로 각각 얼마나 드셨어요? (반반 또는 각자 비율)' if company and len(company) > 1 else K._i18n('dish.portion.ask', 'How much of {name} did you eat?').format(name=d['name'])
        return dict(status='NEEDS_PORTION', dish_id=d['id'], reply=reply,
                    buttons=[d['name']+' '+p for p in K._i18n('dish.portion.options', 'half|1/3|all').split('|')],
                    next='Ask only this one line with these buttons. Interpret the answer and supply --dish ID --portion VALUE; add --companion-portion only when stated. Never ask for weight.')
    share = portion(part, d)
    label = part
    if companion is not None:
        label = dict(owner=share, companion=portion(companion, d), words=part)
        split_shares(label)
    return dict(status='RESOLVED', requests=[(d, share, label)])


def log(K, c, date, slot, requests, key, experience_id=None, **portion_options):
    """One transaction locks stock, writes all meals and debits all portions."""
    slot = meal_slots.canonical(slot)
    if not c.in_transaction:
        c.execute('BEGIN IMMEDIATE')
    output = []
    seen = set()
    try:
        for d, share, label in requests:
            if d['id'] in seen:
                raise ValueError('invalid or duplicate dish portion')
            seen.add(d['id'])
            previous = c.execute('SELECT block_id,fraction FROM made_dish_meal WHERE dish_id=? AND request_key=?', (d['id'], key)).fetchone()
            if previous:
                row = c.execute('SELECT date,meal FROM '+K.MEALS_V32+' WHERE id=?',(previous[0],)).fetchone()
                rest = label in ('remaining', 'left', '\ub098\uba38\uc9c0', '\ub0a8\uc740 \uac70')
                if (not rest and abs(previous[1]-share)>1e-9) or row[0] != date or row[1] != slot:
                    raise ValueError('meal request key identifies different intake')
                import cooking_experience
                cooking_experience.link_meal(K, c, d['id'], previous[0])
                output.append(dict(id=previous[0], dish_id=d['id'], duplicate=True))
                continue
            if not math.isfinite(share) or not 0 < share <= 1:
                raise ValueError('invalid dish portion')
            fresh = next(x for x in dishes(K, c, str(d['id'])) if x['id']==d['id'])
            if fresh['missing']:
                c.rollback()
                return dict(status='NEEDS_NUTRITION', ingredients=fresh['missing'], next='Look up these ingredients, save to food cache, then made-dish refresh ID. Use recipe quantities; never ask for dish weight.')
            grams = sum(i['nutrition']['grams'] for i in fresh['ingredients']) * share
            basis = portion_options.get('portion_basis')
            evidence = portion_options.get('portion_evidence')
            source = portion_options.get('portion_source')
            count = portion_options.get('portion_count')
            fraction = portion_options.get('portion_fraction')
            volume = portion_options.get('portion_volume_ml')
            # A frozen recipe sum in g/kg establishes mass independently of
            # estimated nutrients. Unit conversions cannot establish grams.
            if not any(v is not None for v in portion_options.values()) and all(
                    i.get('unit', '').lower() in ('g', 'kg') and
                    i.get('quantity') is not None for i in fresh['ingredients']):
                basis = 'exact'
                evidence = json.dumps([dict(name=i['name'], quantity=i['quantity'], unit=i['unit'])
                                       for i in fresh['ingredients']], ensure_ascii=False)
            if source not in (None, 'stated', 'package', 'estimate'):
                raise ValueError('portion source must be stated, package or estimate')
            basis, source, count, fraction, volume = K._portion_basis_check(
                basis, evidence, source, grams, count, fraction, volume)
            cur = c.execute('UPDATE made_dish SET remaining=MAX(0,remaining-?) WHERE id=? AND remaining+1e-9>=?', (share,d['id'],share))
            if cur.rowcount != 1:
                raise ValueError('portion exceeds remaining made dish stock')
            companion = 0
            shares = split_shares(label) if isinstance(label, dict) else None
            if shares:
                # Companion consumption is explicit sharing metadata.
                # Companion intake is stock use, never an owner meal record.
                companion = shares[1]
                cur = c.execute('UPDATE made_dish SET remaining=MAX(0,remaining-?) WHERE id=? AND remaining+1e-9>=?', (companion,d['id'],companion))
                if cur.rowcount != 1:
                    raise ValueError('shared portions exceed remaining made dish stock')
                K._config_put(c, 'cooking.companion.%s.%s' % (d['id'], key), json.dumps(dict(fraction=companion, owner_words=label)))
            vals = {k: v*share for k,v in fresh['nutrition'].items() if k != 'kcal'}
            vals['portion_grams'] = grams
            share_label = str(Fraction(share).limit_denominator(1000000)) if shares else label
            name = fresh['name'] + ' ' + share_label
            import cooking_experience
            meal_event = experience_id if experience_id is not None else cooking_experience.meal_event(c, d['id'], date, slot)
            bid = K._meal_write_v32(c, date, slot, name, vals, owning_agent=K._health_agent_write(c), experience_id=meal_event)
            K._portion_save(c, bid, basis, evidence, source, count, fraction, volume)
            apply_nutrition(c, bid, fresh, share)
            c.execute('INSERT INTO made_dish_meal VALUES(?,?,?,?)', (bid,d['id'],share,key))
            import cooking_experience
            cooking_experience.link_meal(K, c, d['id'], bid)
            output.append(dict(id=bid,dish_id=d['id'],name=name,kcal=round(fresh['nutrition']['kcal']*share,2),estimated=bool(fresh['estimated']),remaining=max(0,fresh['remaining']-share-companion)))
        c.commit()
    except Exception:
        c.rollback()
        raise
    for item in output:
        if not item.get('duplicate'):
            K._meal_autolink_safe(c, item['id'])
    return dict(status='LOGGED', meals=output)


def apply_nutrition(c, bid, dish, share):
    kcal = dish['nutrition']['kcal'] * share
    origin = 'default' if dish['estimated'] else 'computed'
    c.execute("UPDATE block_delta SET amount=?,value_origin=? WHERE block_id=? AND resource='nutrition'", (kcal,origin,bid))
    c.execute("UPDATE measure SET value_origin=?,source='module:made-dish' WHERE block_id=?", (origin,bid))
    c.execute("UPDATE measure SET value=? WHERE block_id=? AND metric='kcal_intake'", (kcal,bid))


def _pending_key(K, date, slot):
    return 'cooking.meal.pending.%s.%s.%s' % (K._pin_agent(), date, slot)


def pending_meal(K, c, date, slot):
    row = c.execute('SELECT value FROM config WHERE key=?', (_pending_key(K, date, slot),)).fetchone()
    if not row:
        return None
    pending = json.loads(row[0])
    state = frame.load(K, c)
    return pending if pending and state and pending['session_id'] == state['id'] else None


def defer_satiety(K, c, date, slot, token):
    """Keep an early answer with the cooking share, away from earlier meals."""
    pending = pending_meal(K, c, date, slot)
    if pending is None:
        return False
    if token is not None:
        if token not in (*K.SATIETY_LEVELS, K.SATIETY_SKIP):
            raise ValueError('unknown fullness answer: %r' % token)
        pending['fullness'] = token
        K._config_put(c, _pending_key(K, date, slot), json.dumps(pending))
        c.commit()
    return True


def cli_meal(K, argv):
    args = list(argv)
    args, portion_options = K._portion_options(args)
    args, cooked = K._pop_opt(args, '--cooked')
    args, dish_id = K._pop_opt(args, '--dish')
    args, part = K._pop_opt(args, '--portion')
    args, companion = K._pop_opt(args, '--companion-portion')
    args, shares_json = K._pop_opt(args, '--shares')
    args, key = K._pop_opt(args, '--key')
    args, experience_id = K._pop_opt(args, '--experience')
    if cooked is None and dish_id is None and part is None and shares_json is None:
        return False
    if len(args) < 2:
        raise ValueError('meal-add DATE SLOT NAME --portion HALF | --dish ID --portion HALF | --cooked TEXT')
    date, slot = args[:2]
    slot = meal_slots.canonical(slot)
    c = K.get_conn()
    try:
        c.execute('BEGIN IMMEDIATE')
        # --cooked explicitly declares cooked food. Only the amount is parsed;
        # owner text never routes or reopens a cooking session.
        query = dish_id or (args[2] if len(args)>2 else '')
        if shares_json is not None:
            shares = json.loads(shares_json)
            if not isinstance(shares, list) or not shares:
                raise ValueError('shares must be a nonempty list of typed dish portions')
            requests = []
            for share in shares:
                one = resolve(K, c, str(share['dish']), date, part=share['portion'], companion=share.get('companion'))
                if one['status'] != 'RESOLVED':
                    raise ValueError('typed share must identify one made dish')
                requests.extend(one['requests'])
            resolved = dict(status='RESOLVED', requests=requests)
        elif cooked is not None and not query:
            resolved = resolve_cooked(K, c, cooked, date, part=part, companion=companion)
        else:
            resolved = resolve(K, c, query, date, part=part, companion=companion)
        if resolved['status'] != 'RESOLVED':
            c.rollback()
            if resolved['status'] == 'NEEDS_PORTION':
                dish = dishes(K, c, str(resolved['dish_id']))[0]
                pending = pending_meal(K, c, date, slot)
                if not pending or pending['session_id'] != dish['session_id']:
                    K._config_put(c, _pending_key(K, date, slot), json.dumps(dict(session_id=dish['session_id'])))
                    c.commit()
            print(json.dumps(resolved,ensure_ascii=False))
            return True
        if len(resolved['requests']) != 1 and any(v is not None for v in portion_options.values()):
            raise ValueError('portion declaration requires one dish per meal-add')
        inbound = frame.inbound(K).get('request_id')
        key = key or (str(inbound) if inbound else K.new_ulid())
        result = log(K,c,date,slot,resolved['requests'],key,experience_id, **portion_options)
        if result['status'] == 'LOGGED':
            import cooking_live
            new_meals = [item for item in result['meals'] if not item.get('duplicate')]
            pending = pending_meal(K, c, date, slot)
            retained = None
            if pending and all(d['session_id'] == pending['session_id'] for d, _, _ in resolved['requests']):
                retained = pending.get('fullness')
                if retained is not None:
                    K.meal_satiety(date, slot, retained, conn=c)
                c.execute('DELETE FROM config WHERE key=?', (_pending_key(K, date, slot),))
                c.commit()
            result['reply'] = '말씀하신 몫으로 식사를 기록했어요.\n\n```\n' + K.meal_card_render(c, date) + '\n```\n' + cooking_live.day_line(K, c, date)
            if retained and retained != K.SATIETY_SKIP:
                result['reply'] += '\n포만감도 “%s”로 기록했어요.' % K.satiety_label(retained)
            left = [m for m in result['meals'] if m.get('remaining', 0) > 1e-9]
            if left:
                result['reply'] += '\n남은 요리는 보관하실까요?'
            result['reply'] = result['reply'].rstrip()
            result['next'] = 'Send reply once verbatim: it includes the normal meal card and day line. Do not paraphrase or append a parenthetical restatement of dishes, shares or owner words. Do not repeat the separately printed MEAL_CARD. Append the satiety question only if printed. Never invent a portion or advice.'
        print(json.dumps(result,ensure_ascii=False))
        if result['status'] == 'LOGGED':
            new_meals = [item for item in result['meals'] if not item.get('duplicate')]
            swap_pending = any(K.meal_planned_swap_candidates(c, item['id']) for item in new_meals)
            manage = K._manage_on(c, 'module_meal')
            offered = K._emit_meal_card(c,date,offer=manage and not swap_pending) == 'offer'
            satiety_lines = (K._meal_satiety_ask_once(c, date, slot, any(m.get('estimated') for m in new_meals))
                             if manage and not offered and not swap_pending and retained is None else [])
            if satiety_lines:
                c.commit()
                print('\n'.join(satiety_lines))
                print(K.SATIETY_RELAY)
            for item in new_meals:
                K._emit_planned_swap(c, item['id'])
    finally:
        c.close()
    return True


def cli(K, argv):
    c = K.get_conn()
    try:
        command = argv[0] if argv else 'list'
        query = argv[1] if len(argv)>1 else ''
        if command == 'resolve':
            out = resolve(K,c,query,argv[2] if len(argv)>2 else None)
        elif command in ('list','show','refresh'):
            if command == 'refresh':
                c.execute('BEGIN IMMEDIATE')
                for d in dishes(K,c,query):
                    if c.execute('SELECT 1 FROM made_dish_meal WHERE dish_id=?',(d['id'],)).fetchone() or not d['missing']:
                        continue
                    total,missing,estimated = totals(K,c,d['ingredients'])
                    c.execute('UPDATE made_dish SET ingredients=?,nutrition=?,missing=?,estimated=? WHERE id=?',
                              (json.dumps(d['ingredients']),json.dumps(total),json.dumps(missing),int(estimated),d['id']))
                c.commit()
            out = dict(status='DISHES',dishes=dishes(K,c,query))
        else:
            raise ValueError('made-dish list [NAME] | show ID | refresh ID | resolve TEXT [DATE]')
        print(json.dumps(out,ensure_ascii=False))
    finally:
        c.close()
