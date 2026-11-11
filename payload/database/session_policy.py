"""Pure v2 load/repetition policy. No DB, render, model, or adapter defaults."""
from session_command import CommandError, require


def matches(predicates, catalog, *, relative_load_basis_points=None, caution_area=None):
    facts=dict(catalog, relative_load_basis_points=relative_load_basis_points, caution_area=caution_area)
    for predicate in predicates:
        actual=facts.get(predicate['field']); wanted=predicate['value']; op=predicate['operator']
        if actual is None or not (actual==wanted if op=='eq' else actual in wanted if op=='in' else actual>=wanted):
            return False
    return True


def effective_range(program_range, catalog, policy, **evidence):
    require(len(program_range)==2 and all(type(n) is int and n>0 for n in program_range),'POLICY','program rep_range must be two positive integers')
    lo,hi=program_range
    selector=policy.get('compound.heavy_selector')
    require(selector is not None,'POLICY','heavy selector must be explicitly pinned')
    if matches(selector,catalog,**evidence):
        require('compound.heavy_rep_cap' in policy,'POLICY','heavy cap missing')
        hi=min(hi,policy['compound.heavy_rep_cap'])
    require(lo<=hi,'POLICY_INTERSECTION','program rep_range and heavy cap do not intersect')
    return lo,hi


def working_target(*, catalog, policy, program_range, available_loads_g,
                   previous_load_g, history, required_sets, selected_load_g=None, selected_reps=None,
                   calibration_load_g=None, **evidence):
    """Machine-specific logged history must be supplied by the caller.

    A no-history load requires an explicitly labeled calibration input. An
    increased challenge resets reps to the bottom (assistance reverses load).
    The result includes the basis; it is never a physiological certainty claim.
    """
    lo,hi=effective_range(program_range,catalog,policy,**evidence)
    grid=sorted(set(available_loads_g))
    require(grid and all(type(n) is int and n>=0 for n in grid),'EQUIPMENT','known feasible grid required')
    require(required_sets>0,'POLICY','working-set count required')
    require(policy.get('progression.mode')=='double_progression','POLICY','unsupported progression mode')
    require('effort.floor' in policy and 'history.lookback' in policy,'POLICY','effort/history policy missing')
    history=[r for r in history if r['row_status']=='logged' and r['set_kind']=='working'][-policy['history.lookback']:]
    assistance=catalog['load_basis']=='assistance'
    basis='logged_history' if history else 'calibration_estimate'
    load=selected_load_g if selected_load_g is not None else previous_load_g
    if not history and selected_load_g is None:
        require(calibration_load_g is not None,'CALIBRATION_REQUIRED','no historical load; explicit calibration needed')
        load=calibration_load_g
    require(load in grid,'EQUIPMENT','selected load is unavailable')
    changed=previous_load_g is not None and load!=previous_load_g
    target=lo
    if history and not changed:
        recent=history[-required_sets:]
        complete=len(recent)==required_sets and all(r['load_g']==load and r['reps'] is not None and r['reps']>=hi and r['rir'] is not None and r['rir']>=policy['effort.floor'] for r in recent)
        if complete and selected_load_g is None:
            candidates=[n for n in grid if n<load] if assistance else [n for n in grid if n>load]
            if candidates:load=max(candidates) if assistance else min(candidates)
            else:target=hi
        else:
            same=[r['reps'] for r in recent if r['load_g']==load and r['reps'] is not None]
            if same:target=max(lo,min(hi,min(same)+1))
    if selected_reps is not None:
        require(lo<=selected_reps<=hi,'POLICY','requested reps outside effective range')
        target=selected_reps
    return {'load_g':load,'target_reps':target,'basis':basis,'effective_range':[lo,hi]}


def taper(ramp_loads_g, working_load_g, bands):
    require(working_load_g>0,'POLICY','ramp needs positive working load')
    result=[]
    for load in sorted(set(ramp_loads_g)):
        require(type(load) is int and load>=0,'UNIT','ramp load must be integer grams')
        for band in bands:
            left=load*10000;right=working_load_g*band['upper_basis_points']
            if left<right or band['inclusive'] and left==right:
                result.append({'load_g':load,'target_reps':band['reps']});break
    return result


def plates(*, from_load_g, to_load_g, bar_g, inventory_per_side):
    """Return one feasible plate total and delta, all in integer grams."""
    require(from_load_g>=bar_g and to_load_g>=bar_g and (to_load_g-bar_g)%2==0 and (from_load_g-bar_g)%2==0,'EQUIPMENT','load does not fit a symmetric bar')
    target=(to_load_g-bar_g)//2
    reachable={0:[]}
    for plate,count in sorted(inventory_per_side.items(),reverse=True):
        require(type(plate) is int and plate>0 and type(count) is int and count>=0,'EQUIPMENT','invalid plate inventory')
        for _ in range(count):
            for total,stack in list(reachable.items()):
                if total+plate<=target:reachable.setdefault(total+plate,stack+[plate])
    require(target in reachable,'EQUIPMENT','target cannot be loaded from known inventory')
    return {'plate_total_per_side_g':target,'plate_delta_per_side_g':(to_load_g-from_load_g)//2,'plates_per_side_g':reachable[target]}
