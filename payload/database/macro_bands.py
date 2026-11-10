"""Pure daily gram bands and their projection onto the remaining kcal budget.

Owner policy (2026-10-29/30): P 1.6-2.4 g/kg, F 20-35% kcal,
C residual. Round P/F anchors before deriving C, as in the approved example.
No style selector or automatic protein point is introduced here.
"""
import math

ENERGY = dict(protein=4, carb=4, fat=9)


def _amount(value, name):
    if isinstance(value, bool):
        raise ValueError(name + ' must be a finite nonnegative number')
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError(name + ' must be a finite nonnegative number')
    return value


def compute_macro_bands(kcal, weight_kg, protein_g=None, eaten=None):
    """Return {bands: {macro: {min,max}}, status, issues, remaining_kcal}.

    The base anchors are whole grams (Python round, the kit convention).
    Projection uses unrounded arithmetic: for nutrient i, reserve all other
    remaining minima before capping i; reserve all other remaining maxima
    before raising i's minimum. Actual eaten kcal is authoritative, so fiber
    or declared calorie differences are not silently replaced by 4P+4C+9F.
    Bands describe final daily amounts, including intake already recorded.
    An impossible intersection is explicit (min > max, status=infeasible).
    Manual protein outside the weight band is reported, never clamped or
    persisted; only an in-band value becomes the protein lower anchor.
    """
    kcal = _amount(kcal, 'kcal')
    weight = _amount(weight_kg, 'weight_kg')
    if not kcal or not weight:
        raise ValueError('kcal and weight_kg must be positive')
    pmin, pmax = round(1.6 * weight), round(2.4 * weight)
    issues = []
    if protein_g is not None:
        manual = _amount(protein_g, 'protein_g')
        if 1.6 * weight <= manual <= 2.4 * weight:
            pmin = manual
        else:
            issues.append('manual_protein_outside_band')
    fmin, fmax = round(kcal * .20 / 9), round(kcal * .35 / 9)
    base = dict(protein=dict(min=pmin, max=pmax),
                fat=dict(min=fmin, max=fmax),
                carb=dict(min=max(0, round((kcal - 4*pmax - 9*fmax) / 4)),
                          max=round((kcal - 4*pmin - 9*fmin) / 4)))
    eaten = eaten or {}
    actual = {key: _amount(eaten.get(key, 0), key) for key in ENERGY}
    used = _amount(eaten.get('kcal', sum(actual[k] * ENERGY[k] for k in ENERGY)),
                   'eaten kcal')
    remaining = kcal - used
    bands = {}
    for key, energy in ENERGY.items():
        others = [k for k in ENERGY if k != key]
        reserve_min = sum(max(0, base[k]['min'] - actual[k]) * ENERGY[k]
                          for k in others)
        reserve_max = sum(max(0, base[k]['max'] - actual[k]) * ENERGY[k]
                          for k in others)
        bands[key] = dict(
            min=max(base[key]['min'], actual[key],
                    actual[key] + (remaining - reserve_max) / energy),
            max=min(base[key]['max'],
                    actual[key] + (remaining - reserve_min) / energy))
    feasible = remaining >= 0 and all(b['min'] <= b['max'] for b in bands.values())
    if not feasible:
        issues.append('intake_or_minima_exceed_budget')
    return dict(bands=bands, base_bands=base, remaining_kcal=remaining,
                status='infeasible' if not feasible else 'conflict' if issues else 'ok',
                issues=issues)
