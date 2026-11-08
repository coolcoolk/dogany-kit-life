"""Cooking card layout in one replaceable template; code owns all copy.

Bar arithmetic is adapted from pack_health exercise-enter CARD_BAR_*.
The dispatcher's final ruling pins WIDE=2, NARROW=1, MAX=14 (the live pack's
later WIDE=1 override is deliberately not imported). No step-list renderer.
"""
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import cooking_schedule
import pantry_stock
import sys
from item_grouping import markdown_items

CARD_BAR_FILLED = '\u2588'
CARD_BAR_CURRENT = '\u2593'
CARD_BAR_EMPTY = '\u2592'
CARD_BAR_MAX_CELLS = 14
CARD_BAR_WIDE_MAX_SLOTS = 7
CARD_BAR_CELLS_WIDE = 2
CARD_BAR_CELLS_NARROW = 1


def progress(done, total, active=1):
    natural = total * (CARD_BAR_CELLS_WIDE if total <= CARD_BAR_WIDE_MAX_SLOTS else CARD_BAR_CELLS_NARROW)
    w = min(natural, CARD_BAR_MAX_CELLS)
    if natural > w:
        sys.stderr.write('CARD_BAR_OVERFLOW slots=%d natural_cells=%d cap=%d\n' % (total, natural, w))
    if total <= 0:
        return ''
    filled = done * (w // total) if w >= total else round(done / float(total) * w)
    current = min(max(0, w - filled), active * (w // total if w >= total else max(1, round(w / total)))) if done < total else 0
    return CARD_BAR_FILLED * filled + CARD_BAR_CURRENT * current + CARD_BAR_EMPTY * (w - filled - current)


def unit(copy, ingredient, stock_unit=None):
    raw = stock_unit or ingredient['unit']
    display = ingredient.get('display_unit', raw) if pantry_stock.norm_unit(raw) == pantry_stock.norm_unit(ingredient['unit']) else raw
    return copy.get('unit_' + pantry_stock.norm_unit(display), display)


def quantity(copy, ingredient):
    text = '%s %s%s' % (ingredient['name'], format(ingredient['quantity'], '.4g'), unit(copy, ingredient))
    stock = ingredient.get('stock')
    if stock:
        note = copy['stock_unknown'] if stock['quantity'] is None else copy['stock_available'].format(
            quantity=format(stock['quantity'], '.4g'), unit=unit(copy, ingredient, stock['unit']))
        text += ' (' + note + ')'
    return text


def merged_ingredients(ingredients):
    """Combine display amounts without changing per-dish use or history."""
    groups = {}
    for ingredient in ingredients:
        key = (ingredient.get('stock_item') or ingredient['name'], pantry_stock.norm_unit(ingredient['unit']), ingredient.get('type_hint', ''))
        if key not in groups:
            groups[key] = dict(ingredient, quantity=0)
        groups[key]['quantity'] = round(groups[key]['quantity'] + ingredient['quantity'], 4)
    return list(groups.values())


def clock(state, epoch):
    source = state.get('source', {})
    tz = ZoneInfo(source['timezone']) if source.get('timezone') else timezone(timedelta(seconds=source.get('target_offset_sec', 0)))
    return datetime.fromtimestamp(epoch, tz).strftime('%H:%M')


def render(copy, state, steps, now):
    """Returns text and semantic button labels. All layout lives here."""
    ingredients = state['plan']['ingredients']
    completed = state['completed']
    total, done = len(steps), len(completed)
    current = next((s for s in steps if s['id'] == state['current']), None)
    title = state['plan']['name']
    if state['phase'] == 'ready' or state.get('editing'):
        text = copy['start'].format(dish=title) + '\n' + markdown_items(
            line.removeprefix('- ') for line in ingredient_lines(copy, ingredients).splitlines())
        if state.get('schedule', {}).get('target'):
            planned = state['schedule']
            text += '\n' + copy['target'].format(target=clock(state, planned['target']), eta=clock(state, planned['eta']), late=math.ceil(planned['late_sec'] / 60))
        if current and state.get('editing') and state['started_at'] is not None:
            text += '\n' + copy['resume'].format(step=current['name'])
        return text, [('start', copy['start_button']), ('edit', copy['edit_button'])]
    if state['phase'] in ('closed', 'cancelled'):
        text = copy['closed' if state['phase'] == 'closed' else 'cancelled'].format(dish=title)
        return text, []
    elapsed = max(0, int((now - state['started_at']) // 60))
    schedule = cooking_schedule.derive(state, now) if state.get('source') else state.get('schedule', {})
    remaining = schedule.get('remaining_sec', sum(max(0, state['running'][s['id']] - now) if s['id'] in state['running'] else s['duration_sec']
                    for s in steps if s['id'] not in completed))
    text = copy['header'].format(dish=title, bar=progress(done, total, max(1, len(state['running']) + int(current is not None and current['id'] not in state['running']))), n=min(done + 1, total), total=total)
    text += '\n' + copy['elapsed'].format(elapsed=elapsed, remaining=math.ceil(remaining / 60))
    if schedule.get('target'):
        text += '\n' + copy['target'].format(target=clock(state, schedule['target']), eta=clock(state, schedule['eta']), late=math.ceil(schedule['late_sec'] / 60))
    running = [s for s in steps if s['id'] in state['running']]
    if running:
        text += '\n' + copy['parallel'].format(steps=', '.join(s['name'] + copy['running_left'].format(minutes=max(0, math.ceil((state['running'][s['id']] - now) / 60))) for s in running))
    text += '\n' + copy['waiting']
    return text, [('refresh', copy['refresh']), ('edit', copy['edit_button'])]


def ingredient_lines(copy, ingredients):
    return '\n'.join('- ' + quantity(copy, i) + (' (' + copy['ingredient_hint'].format(hint=i['type_hint']) + ')' if i.get('type_hint') else '') for i in merged_ingredients(ingredients))


def fence(lines):
    return '```\n' + '\n'.join(row for line in lines for row in phone_rows(line)) + '\n```'


def phone_rows(text, width=None):
    """Bound monospace rows; ingredient continuations keep each amount whole."""
    from kit_life import MEAL_CARD_WIDTH, _cell_width, sub_line
    width = MEAL_CARD_WIDTH if width is None else width
    continuation = sub_line('')
    if text.startswith(continuation):
        rows, row = [], continuation
        ingredients = text[len(continuation):].split(', ')
        for index, ingredient in enumerate(ingredients):
            candidate = row + (', ' if row != continuation else '') + ingredient
            cells = sum(_cell_width(ch) for ch in candidate) + int(index < len(ingredients) - 1)
            if row != continuation and cells > width:
                rows.append(row + ',')
                row = continuation + ingredient
            else:
                row = candidate
        # An ingredient wider than the card occupies its own row intact.
        return rows + [row]
    rows, prefix, start = [], '', 0
    while start < len(text):
        cells, end = sum(_cell_width(ch) for ch in prefix), start
        while end < len(text):
            cell = _cell_width(text[end])
            if cells + cell > width:
                break
            cells += cell
            end += 1
        # Inspect only this bounded chunk: long timeline rows stay linear.
        chunk = text[start:end]
        space = chunk.rfind(' ')
        if end < len(text) and space > 0:
            end = start + space
            chunk = chunk[:space]
        rows.append(prefix + chunk.rstrip())
        start, prefix = end, sub_line('')
        while start < len(text) and text[start].isspace():
            start += 1
    return rows or ['']


def dish(state, step):
    recipe = next(r for r in state['source']['recipes'] if r['id'] == step['dish'])
    return recipe.get('emoji', '\U0001f373'), recipe['name']


def slots(state):
    result = dict(state.get('chart_slots', {}))
    result.update(state.get('schedule', {}).get('slots', {}))
    for h in state.get('history', []):
        sid = h['step']['id']
        result[sid] = dict(start=state.get('step_started', {}).get(sid, h['completed_at']), end=h['completed_at'])
    return result


def ordered(state, phase=None):
    table = slots(state)
    return sorted((s for s in state['plan']['steps'] if phase is None or s['phase'] == phase),
                  key=lambda s: (table.get(s['id'], {}).get('start', 0), s['dish'], s['order']))


def mark(state, step, planning=False):
    if planning:
        return '[ ]'
    sid = step['id']
    return '[v]' if sid in state['completed'] else '[\u25ba]' if sid == state.get('current') or sid in state.get('step_started', {}) else '[ ]'


def timed(step):
    return bool(step['duration_sec'] and step.get('kind') == 'unattended')


def step_label(copy, step):
    return step['name'] + (copy['step_minutes'].format(minutes=format(step['duration_sec'] / 60, '.4g')) if timed(step) else '')


def ingredient_suffix(copy, state, step):
    """Measured ingredients in plan order, including the existing separator."""
    used = [i for i in state['plan']['ingredients'] if i['id'] in step['ingredients']]
    return ' / ' + ', '.join(quantity(copy, i) for i in used) if used else ''


def measured_step(copy, state, step):
    return step_label(copy, step) + ingredient_suffix(copy, state, step)


def prep_card(copy, state):
    tasks = ordered(state, 'prep')
    heading = copy['prep_heading'].format(done=sum(s['id'] in state['completed'] for s in tasks), total=len(tasks))
    stage = state.get('source', {}).get('_stage_context', {}).get('key')
    if stage in ('ingredients', 'prep'):
        import cooking_stages
        heading = cooking_stages.LABELS[stage] + ' %d/%d' % (sum(s['id'] in state['completed'] for s in tasks), len(tasks))
    lines = [heading]
    lines += [mark(state, s) + ' ' + measured_step(copy, state, s) for s in tasks]
    first = next(iter(ordered(state, 'cook')), None)
    if first:
        emoji, _ = dish(state, first)
        lines.append(copy['prep_next'].format(emoji=emoji, time=clock(state, slots(state)[first['id']]['start']), step=first['name']))
    return fence(lines)


def relative(copy, state, epoch, now):
    return copy['at_relative'].format(time=clock(state, epoch), minutes=max(0, math.ceil((epoch - now) / 60)))


def next_step(state, step):
    table = slots(state)
    # Parallel unattended work can begin while this action runs. It is not
    # the action to take after finishing this card. Include prep in that
    # sequence too, and never point backwards to an earlier slot.
    boundary = table[step['id']]['end']
    return next((s for s in ordered(state) if s['id'] != step['id']
                 and s['id'] not in state['completed']
                 and s['id'] not in state.get('step_started', {})
                 and table[s['id']]['start'] >= boundary - .001), None)


def dish_progress(copy, state, step, now):
    local = [s for s in ordered(state) if s['dish'] == step['dish']]
    table = slots(state)
    lines = ['\uc694\ub9ac\ubcc4 \uc9c4\ud589\uc0ac\ud56d'] + dish_timeline(copy, state, table, local, now)
    lines += [mark(state, s) + ' ' + clock(state, table[s['id']]['start']) + ' ' + step_label(copy, s) for s in local]
    if state['phase'] in ('closed', 'cancelled'):
        return fence(lines + [copy['pin_done'] if state['phase'] == 'closed' else '\uc885\ub8cc', copy['timeline_legend']])
    lines += ['-----', copy['timeline_legend']]
    for recipe in state['source']['recipes']:
        if recipe['id'] == step['dish']:
            continue
        other = [s for s in ordered(state, 'cook') if s['dish'] == recipe['id']]
        pending = [s for s in other if s['id'] not in state['completed']]
        future = next((s for s in pending if s['id'] not in state.get('step_started', {})), None)
        fields = dict(emoji=recipe.get('emoji', '\U0001f373'), dish=recipe['name'])
        if future:
            lines.append(copy['dish_next'].format(**fields, at=relative(copy, state, table[future['id']]['start'], now), step=future['name']))
        else:
            end = max((table[s['id']]['end'] for s in other), default=now)
            lines.append(copy['dish_finish'].format(**fields, at=relative(copy, state, end, now)))
    return fence(lines)


def step_card(copy, state, step, now):
    sid = step['id']
    done = sid in state['completed']
    historical = next((h for h in state.get('history', []) if h['step']['id'] == sid), None) if done else None
    text = step_heading(copy, state, step, now)
    used = historical['ingredients'] if historical else [i for i in state['plan']['ingredients'] if i['id'] in step['ingredients']]
    # Recipes may supply a checked multi-line how-to. Legacy recipes retain
    # their measured ingredients and explained heat as numbered instructions.
    actions = list(step.get('instructions', []))
    ingredient_lines = ['- ' + quantity(copy, i) + (' (' + i['type_hint'] + ')' if i.get('type_hint') else '')
                        for i in merged_ingredients(used)]
    if actions and ingredient_lines:
        actions[0:0] = [copy['ingredients'].rstrip()] + ingredient_lines
    if not actions:
        if ingredient_lines:
            actions += [copy['ingredients'].rstrip()] + ingredient_lines
        actions.append(step['action'])
        # Heat help must describe the vessel/appliance actually used by this
        # step.  A generic pan test is actively misleading for a pot.
        vessel = (step.get('gear') or step.get('action', '')).casefold()
        resource = step.get('requires', 'none')
        compatible = (step['heat'] == 'oven' and resource == 'oven') or (
            step['heat'] in ('low', 'medium', 'medium_high') and
            resource in ('burner', 'pan') and any(x in vessel for x in ('pan', '\ud32c')))
        if compatible:
            actions.append(copy['heat'].format(heat=copy['heat_' + step['heat']]))
        elif step['heat'] != 'none':
            actions.append(copy['heat'].format(heat=copy['heat_' + step['heat']].split(':', 1)[0]))
    if step.get('warning'):
        actions.append(copy['warning'].format(warning=step['warning']))
    # The plan supplies a cue only when the action needs a separate finish condition.
    if step.get('done_cue', '').strip():
        actions.append(copy['gate_prep' if step['heat'] == 'none' and 'gate_prep' in copy else 'gate'].format(cue=step['done_cue']))
    rendered, number = [], 1
    for line in actions:
        if line == copy['ingredients'].rstrip() or line.startswith('- '):
            rendered.append(line)
        else:
            rendered.append('%d. %s' % (number, line))
            number += 1
    groups, pending = [], []
    for line in rendered:
        if line == copy['ingredients'].rstrip():
            if pending:
                groups.append(markdown_items(pending))
                pending = []
            groups.append(line)
        else:
            pending.append(line.removeprefix('- '))
    if pending:
        groups.append(markdown_items(pending))
    if groups and groups[0].startswith('> '):
        groups[0] = groups[0].replace('> ', '>^ ', 1)
    text += '\n' + '\n'.join(groups)
    upcoming = next_step(state, step)
    if upcoming:
        icon, name = dish(state, upcoming)
        text += '\n>^ ' + copy['step_next'].format(at=relative(copy, state, slots(state)[upcoming['id']]['start'], now), emoji=icon, dish=name, step=upcoming['name'])
    else:
        text += '\n>^ ' + copy['step_last']
    if done:
        return text + '\n' + copy['pin_done'], []
    n = state['plan']['steps'].index(step)
    if timed(step) and sid not in state['running']:
        return text, [('timer_' + str(n), copy['timer'].format(step=step.get('timer_name', step['name']), minutes=format(step['duration_sec'] / 60, '.4g')))]
    if sid in state['running'] and now >= state['running'][sid]:
        text += '\n' + copy['timer_done'].format(cue=step.get('done_cue', ''))
    labels = [('done_' + str(n), copy['done_button'])]
    if step['kind'] == 'active' and step['heat'] == 'none' and step['duration_sec'] and sid not in state.get('pace_started', {}):
        labels.insert(0, ('begin_' + str(n), copy['start_button']))
    return text, labels


def check_button(copy, state, step, fallback):
    """Name the food and its stated vessel; an appliance is not a vessel."""
    gear = step.get('gear', '').strip()
    ingredients = [i for i in state['plan']['ingredients'] if i['id'] in step['ingredients']]
    if not gear or not ingredients:
        return fallback
    recipe = next((r for r in state['source']['recipes'] if r['id'] == step['dish']), {})
    anchor = step['dish'] + '-' + recipe.get('anchor', '')
    food = next((i for i in ingredients if i['id'] == anchor), ingredients[0]).get('name', '').strip()
    return copy['check_food_button'].format(food=food, gear=gear) if food else fallback


def heat_elapsed(state, step, now):
    """Seconds shown on live cards: five-minute boundaries, then the exact end."""
    duration = step.get('duration_sec', 0)
    if step.get('heat', 'none') == 'none' or not duration:
        return None
    if step['id'] in state['completed']:
        return duration
    elapsed = max(0, now - state.get('step_started', {}).get(step['id'], now))
    return duration if elapsed >= duration else int(elapsed // 300) * 300


def heat_progress(copy, state, step, now):
    """One cell per five minutes, with whole minutes on every surface."""
    if heat_elapsed(state, step, now) is None:
        return ''
    duration = step['duration_sec']
    elapsed = duration if step['id'] in state['completed'] else min(duration, max(0, now - state.get('step_started', {}).get(step['id'], now)))
    cells = math.ceil(duration / 300)
    filled = cells if elapsed >= duration else int(elapsed // 300)
    return copy['heat_progress'].format(bar=CARD_BAR_CURRENT * filled + '\u2591' * (cells - filled),
                                      elapsed=round(elapsed / 60), total=round(duration / 60))


def step_heading(copy, state, step, now):
    emoji, _ = dish(state, step)
    title = emoji + ' ' + step['name'] + (copy['duration'].format(minutes=format(step['duration_sec'] / 60, '.4g')) if timed(step) else '')
    bar = heat_progress(copy, state, step, now)
    return '> ' + title + ('\n> ' + bar if bar else '')


def live_heading(copy, state, step, now):
    elapsed = heat_elapsed(state, step, now)
    started = state.get('step_started', {}).get(step['id'], now)
    return step_heading(copy, state, step, started + elapsed if elapsed is not None else now)


TIMELINE_CELLS = 15


def hour_ticks(copy, state, start, end):
    """Thin hour labels before fencing, so an axis never folds into a row."""
    from kit_life import _card_dwidth
    span = max(1, end - start)
    candidates = [(0, clock(state, start)[:2])]
    # Align to local hours, including timezones with half-hour offsets.
    minute = int(clock(state, start)[3:])
    tick = start - minute * 60 - start % 60 + 3600
    stride = max(1, math.ceil(span / 3600 / TIMELINE_CELLS)) * 3600
    while tick <= end:
        candidates.append((round((tick - start) / span * TIMELINE_CELLS), clock(state, tick)[:2]))
        tick += stride
    # Give the last hour its unit; earlier labels remain compact.
    candidates[-1] = (candidates[-1][0], copy['timeline_hour'].format(hour=candidates[-1][1]))
    axis, occupied = [' '] * (TIMELINE_CELLS + 4), set()
    for pos, label in [candidates[-1]] + candidates[:-1]:
        width = _card_dwidth(label)
        if any(n in occupied for n in range(max(0, pos - 1), pos + width + 1)):
            continue
        axis[pos:pos + len(label)] = label
        occupied.update(range(pos, pos + width))
    return ''.join(axis).rstrip()


def dish_timeline(copy, state, table, steps, now, planning=False):
    """One bounded time track, using actual checks rather than clock expiry."""
    recipe = next(r for r in state['source']['recipes'] if r['id'] == steps[0]['dish'])
    first = min(table[s['id']]['start'] for s in state['plan']['steps'])
    start = state.get('started_at') if state.get('started_at') is not None else first
    start = min(start, min(table[s['id']]['start'] for s in steps))
    end = max(table[s['id']]['end'] for s in steps) + recipe.get('serve_after_sec', 0)
    span = max(1, end - start)
    done = set() if planning else set(state['completed'])
    cells = []
    for n in range(TIMELINE_CELLS):
        a, b = start + span * n / TIMELINE_CELLS, start + span * (n + 1) / TIMELINE_CELLS
        overlapping = [s for s in steps if table[s['id']]['start'] < b and table[s['id']]['end'] > a
                       or a <= table[s['id']]['start'] < b and table[s['id']]['start'] == table[s['id']]['end']]
        pending = [s for s in overlapping if s['id'] not in done]
        # Short work remains visible even if most of its bin is idle. Pending
        # work wins a mixed bin so compression cannot imply it was checked.
        cells.append(('\u2593' if any(s['phase'] == 'prep' for s in pending) else '\u2592') if pending
                     else '\u2588' if overlapping else '\u2591')
    heading = copy['dish_heading'].format(emoji=recipe.get('emoji', '\U0001f373'), dish=recipe['name'],
                                        n=len(done.intersection(s['id'] for s in steps)), total=len(steps), time=clock(state, end))
    lines = [heading, hour_ticks(copy, state, start, end), ''.join(cells) + '|']
    if not planning:
        active = [s for s in steps if s['id'] not in done and (
            s['id'] in state.get('step_started', {}) or s['id'] == state.get('current'))]
        current = next((s for s in active if s['id'] == state.get('current')), next(iter(active), None))
        label = current['name'] if current else copy['pin_done'] if all(s['id'] in done for s in steps) else copy['timeline_waiting']
        pos = min(TIMELINE_CELLS, max(0, int((now - start) / span * TIMELINE_CELLS)))
        lines.append(' ' * pos + '\u25b2 ' + clock(state, now) + ' ' + label)
    return lines


def pin_card(copy, state, now, planning=False):
    from kit_life import sub_line
    table = slots(state)
    steps = ordered(state)
    front = cooking_schedule.prep_front(steps) if planning else set()
    blocks = [[s for s in steps if s['id'] in front],
              [s for s in steps if s['id'] not in front]] if front else [steps]
    lines = []
    for recipe in state['source']['recipes']:
        track = [s for s in steps if s['dish'] == recipe['id']]
        if not track:
            continue
        if lines:
            lines.append('')
        lines += dish_timeline(copy, state, table, track, now, planning)
    for block in blocks:
        if not block:
            continue
        if lines:
            lines.append('')
        is_prep = planning and block[0]['id'] in front
        if is_prep:
            lines.append(copy['prep_ahead_heading'])
        for step in block:
            emoji, _ = dish(state, step)
            check = mark(state, step, planning)
            lines.append(check + ' ' + clock(state, table[step['id']]['start']) + ' ' + emoji + ' ' + step_label(copy, step))
            used = [i for i in state['plan']['ingredients'] if i['id'] in step['ingredients']]
            if used:
                lines.append(sub_line(', '.join(quantity(copy, i) for i in used)))
    return fence(lines)
