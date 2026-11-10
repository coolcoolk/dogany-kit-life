# -*- coding: utf-8 -*-
"""Per-user macro targets, track A (protein first). PURE: no DB, no clock,
no mutation of its inputs. Recovered from e5b124d4, with the landed daily
band policy delegated to macro_bands.compute_macro_bands.

macro_targets(profile, goal, deficit, style, day_type, eaten=None) -> one result
that every consumer (cards, CLI, preload, cooking line) reads.

What is decided here
  protein   explicit owner value ALWAYS wins (manual, or a stored legacy value
            of unproven origin); only with no stored value (or an explicit
            switch to auto) does the AUTO selector run, and only inside the
            warehouse evidence band.  A safety state withdraws the auto number.
  fat/carb  Compatibility points use the legacy fixed fat-ratio arithmetic,
            for every style.  No style has a warehouse rule yet, so a stored
            style reports status 'partial_unsupported' and never changes
            fat/carb.  Nothing here enables low_carb / keto / mediterranean
            numbers. Daily bands use the approved 20-35% kcal fat anchors
            and carb residual; automatic protein is never a lower anchor.

AUTO PROTEIN POLICY  name=dogany-protein-auto  version=1  (PRODUCT POLICY)
  band      cut  : 1.6..2.4 g/kg body weight   nutrition/protein-deficit-lean-
                   mass-target-062 (B), protein-deficit-trained-lifter-023
                   (cut-floor B, cut-band-high C)
            else : 1.4..2.0 g/kg               nutrition/protein-intake-002 (A)
  point     g/kg = lo + t*(hi-lo)        <- the choice of t is PRODUCT JUDGMENT
            t = 0.5*severity + 0.5*leanness            (cut)
            t = 0.5*leanness                           (maintain / gain)
            severity = clamp(deficit / (1100*weight_kg) / 0.01, 0, 1)
                       1100 kcal per body-weight fraction per week and the 1%
                       upper selectable rate are nutrition/goal-calorie-rules-
                       757 deficit-size-lean-or-training (C, constants D)
            leanness = clamp((0.32 - fat_mass/weight) / (0.32 - 0.12), 0, 1)
                       0.12 / 0.32 are product constants, sex-agnostic; fat
                       mass unknown -> 0.5 (neutral), stated in why
  result    round to the nearest 5 g, then clamp inside the rounded band
No warehouse row supplies a deficit/leanness -> g/kg function: the point is a
labelled judgment inside the evidence band, not a study-derived prescription.
"""
import math

POLICY_NAME = 'dogany-protein-auto'
POLICY_VERSION = 1
STYLES = ('high_protein', 'mediterranean', 'low_carb', 'keto')
# Styles with a ratified F/C rule.  EMPTY: the warehouse owns none (report 2).
SUPPORTED_STYLES = ()
GOALS = ('cut', 'maintain', 'gain')
DAY_TYPES = ('training', 'rest', 'unknown')
LEGACY_FAT_RATIO = 0.25

ITEM_CUT = 'nutrition/protein-deficit-lean-mass-target-062'
ITEM_CUT_2 = 'nutrition/protein-deficit-trained-lifter-023'
ITEM_GENERAL = 'nutrition/protein-intake-002'
ITEM_DEFICIT = 'nutrition/goal-calorie-rules-757'


def _num(x):
    return (not isinstance(x, bool) and isinstance(x, (int, float))
            and math.isfinite(x))


def _pos(x):
    return _num(x) and x > 0


def _truthy(v):
    return str(v or '').strip().lower() in ('1', 'true', 'yes', 'y', 'on')


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _round5(x):
    return int(math.floor(x / 5.0 + 0.5)) * 5


def safety_flags(profile, weight):
    """Withdrawal states from the STORED profile; unknown is not a flag."""
    flags = []
    renal = str(profile.get('renal_condition') or 'unknown').strip().lower()
    if renal not in ('', 'unknown', 'none', 'healthy', 'no', '0'):
        flags.append('renal')
    if _truthy(profile.get('pregnancy')):
        flags.append('pregnancy')
    if _truthy(profile.get('eating_disorder')):
        flags.append('eating_disorder')
    try:
        age = float(profile.get('age'))
    except (TypeError, ValueError):
        age = None
    if age is not None and math.isfinite(age) and 0 < age < 18:
        flags.append('minor')
    h = profile.get('height_cm')
    if _pos(weight) and _pos(h) and weight / (h / 100.0) ** 2 < 18.5:
        flags.append('underweight')
    return flags, age


def protein_band(goal, weight):
    lo, hi = (1.6, 2.4) if goal == 'cut' else (1.4, 2.0)
    return lo, hi, [int(math.ceil(weight * lo - 1e-9)),
                    int(math.floor(weight * hi + 1e-9))]


def auto_protein(goal, deficit, weight, fat_mass):
    """(grams, detail) -- the named policy.  weight must be a positive number."""
    lo, hi, rounded_band = protein_band(goal, weight)
    sev = 0.0
    if goal == 'cut' and _pos(deficit):
        sev = _clamp(deficit / (1100.0 * weight) / 0.01, 0.0, 1.0)
    bf = None
    if _pos(fat_mass) and fat_mass < weight:
        bf = fat_mass / weight
    lean = 0.5 if bf is None else _clamp((0.32 - bf) / (0.32 - 0.12), 0.0, 1.0)
    t = 0.5 * sev + 0.5 * lean
    gkg = lo + t * (hi - lo)
    grams = _clamp(_round5(weight * gkg), rounded_band[0], rounded_band[1])
    return grams, dict(g_per_kg_raw=round(gkg, 4),
                       g_per_kg_chosen=round(grams / weight, 3),
                       band_g_per_kg=[lo, hi], band_g=rounded_band,
                       severity=round(sev, 4), leanness=round(lean, 4),
                       body_fat_fraction=None if bf is None else round(bf, 4),
                       t=round(t, 4))


def macro_targets(profile, goal, deficit, style, day_type, eaten=None):
    """Pure kernel.  profile: stored snapshot (see kit_life._macro_profile);
    goal cut|maintain|gain; deficit signed daily balance (+cut, 0 keep, -gain);
    style None (nothing chosen) or STYLES; day_type training|rest|unknown.
    Raises ValueError on contradictory / invalid input."""
    if goal not in GOALS:
        raise ValueError('unknown goal')
    if day_type not in DAY_TYPES:
        raise ValueError('unknown day type')
    if style is not None and style not in STYLES:
        raise ValueError('unknown style')
    if not _num(deficit):
        raise ValueError('invalid deficit')
    if (goal == 'cut' and deficit <= 0 or goal == 'maintain' and deficit != 0
            or goal == 'gain' and deficit >= 0):
        raise ValueError('goal/balance conflict')
    kcal = profile.get('eff_goal')
    if not _num(kcal):
        raise ValueError('invalid effective kcal')
    weight = profile.get('weight_kg')
    mode = profile.get('protein_mode')           # manual|stored_legacy|auto|default
    explicit = profile.get('explicit_protein_g')
    ratio = profile.get('fat_ratio', LEGACY_FAT_RATIO)
    if not _num(ratio):
        raise ValueError('invalid fat ratio')
    if mode in ('manual', 'stored_legacy') and not _pos(explicit):
        raise ValueError('invalid explicit protein; do not replace with default')

    why = [dict(field='kcal', kind='existing_code',
                source='kit_life.targets_for_date -> compute_targets',
                value=kcal, reason='energy envelope resolved by the adapter')]
    out = dict(protein=None, fat=None, carb=None, kcal=kcal, why=why,
               style=style, goal=goal, day_type=day_type,
               protein_source=None, protein_policy=None, safety=[],
               bands={}, semantics={'protein': 'target', 'fat': 'target',
                                    'carb': 'target',
                                    'kcal': 'budget' if goal == 'cut' else 'target'},
               recommended=False,
               allocation='legacy_fixed_fat_ratio',
               status='legacy_default')

    flags, age = safety_flags(profile, weight)
    out['safety'] = flags
    have_weight = _pos(weight) and not profile.get('weight_is_default')
    if have_weight:
        lo, hi, band_g = protein_band(goal, weight)
        out['bands']['protein_body_weight'] = band_g
        why.append(dict(
            field='protein_band', kind='evidence_range',
            item=ITEM_CUT if goal == 'cut' else ITEM_GENERAL,
            also=ITEM_CUT_2 if goal == 'cut' else None,
            grade='B' if goal == 'cut' else 'A',
            inputs={'weight_kg': weight, 'g_per_kg': [lo, hi]},
            reason='exercising-adult range; deficit band assumes regular '
                   'resistance training (training status not asserted)'))
        if goal == 'cut' and _pos(profile.get('lean_mass_kg')) \
                and not profile.get('ffm_is_default'):
            out['bands']['protein_ffm_context'] = [
                round(profile['lean_mass_kg'] * x) for x in (2.3, 3.1)]
    if age is not None and age >= 65:
        why.append(dict(field='protein_band', kind='caveat', item=ITEM_CUT,
                        grade='B', reason='older adult: athletic cut band is an '
                        'extrapolation, not a tested target'))

    if 'renal' in flags:
        out['protein_source'] = 'withdrawn'
        why.append(dict(field='protein', kind='withdrawn', item=ITEM_CUT,
                        grade='A (renal refraction note; item B)',
                        reason='known renal condition: clinician sets target; '
                               'stored record preserved, not recommended'))
    elif mode in ('manual', 'stored_legacy'):
        out['protein'] = explicit
        out['protein_source'] = mode
        why.append(dict(field='protein', kind='explicit_user',
                        grade='N/A (owner value)', value=explicit,
                        provenance=mode,
                        reason='explicit stored value wins; not an evidence claim'))
        if have_weight and not (band_g[0] <= explicit <= band_g[1]):
            why.append(dict(field='protein', kind='outside_band_notice',
                            value=explicit, band_g=band_g,
                            reason='owner value kept; outside the evidence band'))
    elif mode == 'default':
        out['protein'] = explicit               # legacy code default, labelled
        out['protein_source'] = 'default'
        why.append(dict(field='protein', kind='code_default', value=explicit,
                        reason='no stored body data: placeholder, not the '
                               "owner's number"))
    else:                                       # auto
        blocked = [f for f in flags if f != 'renal']
        if blocked:
            out['protein_source'] = 'withdrawn'
            why.append(dict(field='protein', kind='withdrawn',
                            item=ITEM_DEFICIT, grade='D (product screen)',
                            reason='safety state %s: no automatic protein '
                                   'number' % ','.join(blocked)))
        elif not have_weight:
            out['protein_source'] = 'unavailable'
            why.append(dict(field='protein', kind='unavailable',
                            reason='no stored body weight to compute from'))
        else:
            grams, d = auto_protein(goal, deficit, weight,
                                    profile.get('fat_mass_kg')
                                    if not profile.get('fat_is_default') else None)
            out['protein'] = grams
            out['protein_source'] = 'auto'
            out['protein_policy'] = dict(name=POLICY_NAME, version=POLICY_VERSION,
                                         kind='product_judgment', **d)
            why.append(dict(
                field='protein', kind='product_policy',
                policy='%s/v%d' % (POLICY_NAME, POLICY_VERSION),
                item=ITEM_CUT if goal == 'cut' else ITEM_GENERAL,
                also=[ITEM_CUT_2, ITEM_DEFICIT] if goal == 'cut' else None,
                grade='B/C evidence band; chosen point = product judgment',
                inputs={'weight_kg': weight, 'deficit_kcal': deficit,
                        'fat_mass_kg': profile.get('fat_mass_kg')},
                detail=d, value=grams,
                reason='point inside the supported band from deficit severity '
                       'and leanness; not a study-derived exact g/kg'))

    p = out['protein']
    out['fat'] = int(round(kcal * ratio / 9))
    out['carb'] = (None if p is None
                   else int(round((kcal - p * 4 - kcal * ratio) / 4)))
    # Python round() on the same operands in the same order as the legacy
    # arithmetic -> identical integers (checked by the test over a grid).
    if style is None:
        why.append(dict(field='fat/carb', kind='legacy_allocation',
                        reason='fixed fat ratio %s, carb residual (unchanged)'
                               % ratio))
    elif style not in SUPPORTED_STYLES:
        out['status'] = 'partial_unsupported'
        for nutrient in ('fat', 'carb'):
            why.append(dict(field=nutrient, kind='UNSUPPORTED',
                            inputs=dict(goal=goal, deficit=deficit, style=style,
                                        day_type=day_type),
                            reason='no warehouse style/goal/day allocation; '
                                   'legacy arithmetic kept unchanged'))
    if out['protein_source'] == 'withdrawn':
        out['status'] = 'withdrawn'
    # Approved daily display policy (2026-10-29/30), distinct from the
    # compatibility point above. An auto point must never become a manual
    # floor. Keep the landed rounding and intake projection in one helper.
    from macro_bands import compute_macro_bands
    out['macro_bands'] = None
    if have_weight and kcal > 0:
        manual = explicit if mode in ('manual', 'stored_legacy') else None
        out['macro_bands'] = compute_macro_bands(kcal, weight, manual, eaten)
    return out
