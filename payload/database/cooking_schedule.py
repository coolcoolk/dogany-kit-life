"""Resource-constrained back scheduling and event-relative ready frontier.

Adapted from the instance cooking frontier engine. No OS timers or live-agent
storage: the activity frame owns persistence, countdowns and delivery.
"""

RESOURCES = ('hands', 'burner', 'oven', 'airfryer', 'microwave', 'board', 'pan')


def resources(step):
    needed = ['hands'] if step.get('kind', 'active') in ('active', 'attended') else []
    appliance = step.get('requires', 'none')
    return needed + ([appliance] if appliance != 'none' else [])


def capacities(source, steps):
    caps = {'hands': 1}
    caps.update(source.get('resources', {}))
    for key, value in caps.items():
        if key not in RESOURCES or isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 16:
            raise ValueError('invalid kitchen capacity: ' + key)
    if caps['hands'] != 1:
        raise ValueError('this cooking frame requires one cook')
    for step in steps:
        for key in resources(step):
            if key not in caps:
                if source.get('target_at') is not None:
                    raise ValueError('missing kitchen resource: ' + key)
                caps[key] = 1
            if not caps[key]:
                raise ValueError('unavailable kitchen resource: ' + key)
    return caps


def fits(calendar, caps, needed, start, end):
    if end <= start:
        return True
    for key in needed:
        intervals = calendar.get(key, [])
        points = {start} | {a for a, b in intervals if start < a < end}
        if any(1 + sum(a <= t < b for a, b in intervals) > caps[key] for t in points):
            return False
    return True


def stage(step):
    return 'ingredients' if step.get('ingredient_prep') else step['phase']


def prep_front(steps, source=None):
    """Prep that can run ahead without pulling a cook prerequisite forward."""
    if source is not None and '_prep_choices' in source:
        front = {s['id'] for s in steps if s['phase'] == 'prep' and (s.get('ahead') == 'required' or
                 (s.get('ahead') == 'allowed' and source['_prep_choices'].get(s['id']) is True))}
    else:
        front = {s['id'] for s in steps if s['phase'] == 'prep' and s.get('prep_ahead', True)}
    while True:
        eligible = {s['id'] for s in steps if s['id'] in front and set(s['depends']) <= front}
        if eligible == front:
            return front
        front = eligible


def lead(step):
    return max(step.get('wait_after_sec', 0), step.get('ahead_min_sec', 0) if step.get('ahead') == 'required' else 0)


def hold_resources(step):
    # Keeping a finished dish warm occupies its appliance, never the cook.
    return [key for key in resources(step) if key != 'hands']


def hold_key(hold):
    return 'hold_warning:%s:%s' % (hold['dish'], hold['since'])


def timer_released(state, step):
    """A timed prerequisite releases its successor without a target-time wait.

    Frontier membership still enforces every prerequisite, rest and resource.
    """
    return any(h.get('completion_source') == 'timer' and h['step']['id'] in step['depends']
               for h in state.get('history', []))


def holds(state, now):
    if state.get('phase') in ('closed', 'cancelled'):
        return []
    steps = state['plan']['steps']
    recipes = {r['id']: r for r in state['source']['recipes']}
    tails = {s['id'] for s in steps if not any(s['id'] in t['depends'] and s['dish'] == t['dish'] for t in steps)}
    return [dict(dish=h['step']['dish'], resource=hold_resources(h['step']), since=h['completed_at'],
                 limit_sec=recipes[h['step']['dish']]['hold_limit_sec'],
                 warning=now - h['completed_at'] >= recipes[h['step']['dish']]['hold_limit_sec'])
            for h in state.get('history', [])
            if h['step']['id'] in tails and h['step'].get('phase') == 'cook'
            and recipes.get(h['step']['dish'], {}).get('service_style') == 'hold'
            and state.get('hold_decisions', {}).get(hold_key(dict(dish=h['step']['dish'], since=h['completed_at'])), {}).get('choice') != 'end']


def derive(state, now):
    """Place prep forward from now, then cook tails backward from the target.

    Started actions are rigid. Overdue actions retain actual resource ownership
    until an owner confirms completion, even when projected end is now.
    """
    # A reported out-of-order completion may relax an edge for that event,
    # but unfinished work still follows its original prerequisites. Legacy
    # snapshots can retain those edges only in the audit map.
    steps = [dict(s, depends=list(dict.fromkeys(s['depends'] + state.get('relaxed', {}).get(s['id'], []))))
             if s['id'] not in state['completed'] else s for s in state['plan']['steps']]
    caps = capacities(state['source'], steps)
    done, started = set(state['completed']), state.get('step_started', {})
    typed = '_prep_choices' in state['source']
    front = prep_front(steps, state['source']) if typed else set() if state['source'].get('prep_mode') == 'joined' else prep_front(steps)
    history = {h['step']['id']: h for h in state.get('history', [])}
    by_id = {s['id']: s for s in steps}
    target = state['source'].get('target_at')
    buffers = {r['id']: r.get('serve_after_sec', 0) for r in state['source']['recipes']}
    holding = holds(state, now)
    resume = max(now, state.get('delay_until', now))
    def projected_end(step):
        return started[step['id']] + step['duration_sec'] + state.get('step_delays', {}).get(step['id'], 0)
    tails = {s['id'] for s in steps if not any(s['id'] in t['depends'] and s['dish'] == t['dish'] for t in steps)}
    serving = [projected_end(s) + buffers.get(s['dish'], 0) for s in steps if s['id'] in started and s['id'] not in done and s['id'] in tails]
    serving += [h['completed_at'] + buffers.get(h['step']['dish'], 0) for h in state.get('history', []) if h['step']['id'] in tails]
    anchor = max([now, target or now] + serving + [projected_end(s) for s in steps if s['id'] in started and s['id'] not in done])

    def attempt(end_at, forward=None, hold_floors=None):
        forward = front if forward is None else forward
        hold_floors = hold_floors or {}
        calendar, slots, pending = {}, {}, {}

        def occupy(needed, a, b):
            for key in needed:
                calendar.setdefault(key, []).append((a, b))

        for s in steps:
            sid = s['id']
            if sid in done:
                continue
            if sid in started:
                end = max(now, projected_end(s))
                slots[sid] = dict(start=started[sid], end=end)
                occupy(resources(s), now, end)
            else:
                pending[sid] = s
        # Hold expiry is advisory. Actual start permission stays blocked even
        # after the target, just like an overdue unconfirmed cooking step.
        for hold in holding:
            occupy(hold['resource'], now, max(now, target or now))
        # Keep the front block rigid across backward trials. Its earliest
        # resource/dependency-feasible slots never slide toward dinner time.
        prep = {sid: s for sid, s in pending.items() if sid in forward}
        while prep:
            ready = [s for s in prep.values() if all(p in done or p in slots for p in s['depends'])]
            if not ready:
                raise ValueError('cyclic cooking dependencies')
            def earliest(s):
                floor = max([resume, s.get('start_at', resume), state['source'].get('_prep_not_before', {}).get(s['id'], resume), hold_floors.get(s['id'], resume)] + [
                    (history[p]['completed_at'] if p in history else slots[p]['end'] if p in slots else now)
                    + lead(by_id[p]) for p in s['depends']])
                needed, duration = resources(s), s['duration_sec']
                candidates = {floor}
                for key in needed:
                    candidates.update(b for a, b in calendar.get(key, []) if b >= floor)
                return next(a for a in sorted(candidates) if fits(calendar, caps, needed, a, a + duration))
            step = min(ready, key=lambda s: (earliest(s), s['dish'], s['order']))
            start = earliest(step)
            end = start + step['duration_sec']
            slots[step['id']] = dict(start=start, end=end)
            occupy(resources(step), start, end)
            del prep[step['id']]
            del pending[step['id']]
        successors = {s['id']: [t['id'] for t in steps if s['id'] in t['depends'] and t['id'] not in done] for s in steps}
        deficit = 0
        while pending:
            sinks = [s for s in pending.values() if all(sid in slots for sid in successors[s['id']])]
            if not sinks:
                raise ValueError('cyclic cooking dependencies')
            def bound(s):
                buffer = buffers.get(s['dish'], 0)
                return min([slots[sid]['start'] for sid in successors[s['id']]] or [end_at - buffer])
            step = min(sinks, key=lambda s: (-bound(s), s['dish'], -s['order']))
            needed, duration = resources(step), step['duration_sec']
            ceiling = bound(step) - lead(step)
            if 'start_at' in step:
                ceiling = min(ceiling, max(resume, step['start_at']) + duration)
            candidates = {ceiling}
            for key in needed:
                candidates.update(a for a, b in calendar.get(key, []) if a < ceiling)
            for end in sorted(candidates, reverse=True):
                start = end - duration
                if fits(calendar, caps, needed, start, end):
                    break
            else:
                raise ValueError('kitchen resources cannot fit the plan')
            slots[step['id']] = dict(start=start, end=end)
            occupy(needed, start, end)
            floor = max([resume, step.get('start_at', resume), state['source'].get('_prep_not_before', {}).get(step['id'], resume)] + [h['completed_at'] + lead(h['step']) for h in state.get('history', []) if h['step']['id'] in step['depends']] + [slots[sid]['end'] + (lead(by_id[sid]) if sid in forward else 0) for sid in step['depends'] if sid in slots and sid not in done and (sid in forward or sid in started)])
            deficit = max(deficit, floor - start)
            del pending[step['id']]
        return slots, deficit

    # Each shift clears at least one pending critical path; finite small plans.
    def trial(end_at):
        if not typed or not front:
            return attempt(end_at)
        baseline, _ = attempt(end_at, set())
        floors = {}
        for step in steps:
            if step['id'] in front and step.get('ahead') == 'allowed':
                cooks = [s for s in steps if s['dish'] == step['dish'] and s['phase'] == 'cook' and s['order'] > step['order'] and s['id'] in baseline]
                if cooks:
                    floors[step['id']] = min(baseline[s['id']]['start'] for s in cooks) - step['hold_max_sec']
        return attempt(end_at, front, floors)

    earliest_anchor = anchor
    for _ in range(len(steps) * 4 + 4):
        slots, deficit = trial(anchor)
        if deficit <= 0.000001:
            break
        anchor += deficit
    else:
        raise ValueError('kitchen schedule did not converge')
    # A failed backward trial can hit a rigid interval in the past and
    # overestimate its shift. Tighten the feasible bound, so resuming a timer
    # does not add phantom minutes to a previously valid remaining estimate.
    if anchor > earliest_anchor:
        low, high = earliest_anchor, anchor
        for _ in range(40):
            if high - low < 0.000001:
                break
            middle = (low + high) / 2
            _, deficit = trial(middle)
            if deficit <= 0.000001:
                high = middle
            else:
                low = middle
        rounded = round(high)
        anchor = rounded if rounded >= earliest_anchor and abs(high - rounded) < 0.00001 else high
        slots, deficit = trial(anchor)
        if deficit > 0.000001:
            anchor = high
            slots, _ = trial(anchor)
    order = sorted((s for s in steps if s['id'] not in done),
                   key=lambda s: (slots[s['id']]['start'], s['dish'], s['order']))
    # Actual occupancy, not a predicted clock expiry, governs start permission.
    occupied = {key: 0 for key in caps}
    for s in steps:
        if s['id'] in started and s['id'] not in done:
            for key in resources(s):
                occupied[key] += 1
    for hold in holding:
        for key in hold['resource']:
            occupied[key] += 1
    actual_occupied = dict(occupied)
    frontier = []
    for s in order:
        if now < max(resume, s.get('start_at', resume)) or s['id'] in started or any(p not in done for p in s['depends']):
            continue
        if any(h['step']['id'] in s['depends'] and h['completed_at'] + lead(h['step']) > now for h in state.get('history', [])):
            continue
        if all(occupied[key] < caps[key] for key in resources(s)):
            frontier.append(s['id'])
            for key in resources(s):
                occupied[key] += 1
    eta = max([anchor] + [v['end'] for v in slots.values()])
    return dict(computed_at=now, slots=slots, order=[s['id'] for s in order], frontier=frontier,
                target=target, eta=eta, late_sec=max(0, eta - target) if target else 0,
                capacities=caps, occupied=actual_occupied,
                remaining_sec=max(0, eta - now), holds=holding)


def prepare(state):
    """Upgrade the previous frame's open snapshots without losing evidence."""
    previous = {}
    started = state.setdefault('step_started', {})
    for step in state['plan']['steps']:
        dish = step['dish']
        track = previous.setdefault(dish, [])
        step.setdefault('order', len(track))
        step.setdefault('depends', [track[-1]] if track else [])
        if step['id'] not in state['completed']:
            step['depends'] = list(dict.fromkeys(step['depends'] + state.get('relaxed', {}).get(step['id'], [])))
        step.setdefault('kind', 'active')
        step.setdefault('requires', 'oven' if step['heat'] == 'oven' else 'burner' if step['heat'] != 'none' else 'none')
        if step['id'] in state['running'] and step['id'] not in started:
            started[step['id']] = state['running'][step['id']] - step['duration_sec']
        track.append(step['id'])


def refresh(state, now, *, focus=False):
    if not state.get('source'):
        return
    prepare(state)
    state['schedule'] = derive(state, now)
    if not focus or state['phase'] != 'active' or state.get('editing'):
        return
    steps = {s['id']: s for s in state['plan']['steps']}
    current = state.get('current')
    started = state.setdefault('step_started', {})
    attended = [sid for sid in started if sid not in state['completed'] and steps[sid]['kind'] in ('active', 'attended')]
    if attended:
        state['current'] = attended[0]
        return
    due = [sid for sid in state['schedule']['frontier'] if not state['source'].get('target_at')
           or state['schedule']['slots'][sid]['start'] <= now + 0.001 or timer_released(state, steps[sid])]
    state['current'] = due[0] if due else None
    if (state['current'] and steps[state['current']]['kind'] in ('active', 'attended')
            and state['current'] not in state.get('awaiting_start', [])):
        state.get('step_delays', {}).pop(state['current'], None)
        started[state['current']] = now
        state['schedule'] = derive(state, now)
