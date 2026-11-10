"""Typed cooking plan: one schema, a template and a validator the agent runs.

The agent writes PLAN.json without reading kit code: `cooking template` prints
the schema and a complete example, `cooking check PLAN.json` lists every
problem with its JSON path, and plan/bind/open refuse an invalid plan with the
same list. The schema below is the single source for both the printed doc and
the checks; deeper rules (cycles, kitchen capacity, placeholders) come from
the session compiler itself, so the check can never pass a plan that open
would refuse.
"""
import copy
import difflib
import json
from pathlib import Path
import re
import unicodedata

import agent_conf

SLUG = re.compile(r'[a-z0-9_-]{1,32}\Z')
MIDDLE_DOT = '\u00b7'
RECIPE_SCHEMA = 'cooking_recipe/1'
HEATS = ('none', 'low', 'medium', 'medium_high', 'high', 'oven')
OVEN_MODES = ('top_bottom', 'convection')
KINDS = ('active', 'attended', 'unattended')
PHASES = ('prep', 'cook')
BLOCKS = ('all', 'prep', 'cook')
YIELD_UNITS = ('whole', 'piece', 'serving', 'glass', 'cup', '\uac1c', '\uc794', '\uc778\ubd84')
CONCRETE_UNITS = ('g', 'gram', 'grams', 'ml', 'piece', 'pieces', '\uac1c')
VAGUE_AMOUNT = re.compile(r'\ud55c\s*\uc90c|\ud55c\s*\ucef5|\uc801\ub2f9\ud788|\uc870\uae08|\ub109\ub109\ud788')
HOUSEHOLD_AMOUNT = re.compile(r'(?:\d+(?:[.]\d+)?|\ud55c|\ub450|\uc138)\s*(?:\ucef5|\ud070\uc220|\uc791\uc740\uc220|\uc22b\uac08|\uc22b\uac00\ub77d|cups?|tbsp|tsp)(?![a-z])', re.I)
RESOURCES = ('hands', 'burner', 'oven', 'airfryer', 'microwave', 'board', 'pan')

# (field, type, required, doc). Types: str, str0 (empty allowed), slug, num, num0 (zero allowed),
# int, bool, list[str], list[slug], dict, enum:<a|b>, iso, list[obj].
PLAN_FIELDS = [
    ('prep_mode', 'enum:joined|separate', False, 'owner choice from the one planning question; use cooking prep-mode, never infer from words in code'),
    ('plan_id', 'slug', False, 'optional meal identity shared by separate prep and cook files; use a new id for the next meal'),
    ('eaters', 'list[str]', False, 'owner and stated companions; never infer equal portions from this list'),
    ('recipes', 'list[recipe]', True, 'one recipe per dish, 1 to 8 dishes cooked together'),
    ('block', 'enum:all|prep|cook', False, 'which phase this session runs; default all'),
    ('target_at', 'iso', False, 'meal time with timezone, e.g. 2026-10-24T18:30:00+09:00; steps are back-scheduled to it'),
    ('target_source', 'enum:owner|agent', False, 'owner when the owner stated target_at; an owner target is never changed or dropped by a re-plan without the owner'),
    ('guest_arrival_at', 'iso', False, 'guest arrival time with timezone; keep this separate from user_slack_sec'),
    ('user_slack_sec', 'num0', False, 'owner-chosen seconds after guest arrival; never reduce or consume this implicitly'),
    ('timezone', 'str', False, 'IANA zone for clock labels, e.g. Asia/Seoul'),
    ('resources', 'dict', False, 'kitchen capacity: hands (always 1), burner, oven, airfryer, microwave, board, pan -> count; required for every appliance a step uses when target_at is set'),
    ('gear', 'list[str]', False, 'equipment the menu needs; missing gear goes on the shopping message'),
    ('owned_gear', 'list[str]', False, 'equipment the owner said is at hand'),
    ('changes', 'dict', False, 'kit-managed ingredient changes; leave out'),
]
RECIPE_FIELDS = [
    ('schema', 'enum:' + RECIPE_SCHEMA, True, 'always ' + RECIPE_SCHEMA),
    ('id', 'slug', True, 'short ascii id, a-z 0-9 _ -, unique in the plan'),
    ('name', 'str', True, 'full dish name as the owner says it, no abbreviation, no middle dot'),
    ('emoji', 'str', False, 'agent chooses a distinct marker for each dish before starting; stored with the session and reused on every overview update; never use a dish-to-emoji lookup table'),
    ('base_amount', 'num', True, 'amount the ingredient quantities are written for'),
    ('amount', 'num', True, 'amount cooked today; quantities scale by amount / base_amount'),
    ('anchor', 'slug', True, 'id of the main ingredient (the one an owner amount like 500g refers to)'),
    ('ingredients', 'list[ingredient]', True, 'every ingredient the steps use'),
    ('steps', 'list[step]', True, 'the steps in cooking order for this dish'),
    ('yield', 'dict', False, '{"quantity": N, "unit": whole|piece|serving|glass|cup}; yield for base_amount; use serving when servings are known, scaled by amount/base_amount on the overview'),
    ('serve_after_sec', 'num0', False, 'resting/serving buffer after the last step, seconds'),
    ('service_style', 'enum:immediate|hold', False, 'immediate serves on completion; hold stays warm and keeps its last-step resource'),
    ('hold_limit_sec', 'num0', False, 'soft quality-warning boundary for a hold dish; it never forces completion'),
]
INGREDIENT_FIELDS = [
    ('food_type', 'enum:meat|other', False, 'typed food category; mark meat for automatic roast/sear preparation'),
    ('weight_package_units', 'num', False, 'owner-stated number of pieces in the total package weight'),
    ('weight_basis', 'enum:per_piece|total', False, 'owner-confirmed meaning of an ambiguous package weight'),
    ('weight_owner_words', 'str', False, 'exact owner words confirming per-piece or whole-package weight'),
    ('id', 'slug', True, 'ascii id, unique in the recipe; steps refer to it'),
    ('name', 'str', True, 'ingredient name as on the shopping list'),
    ('quantity', 'num0', True, 'amount for base_amount'),
    ('unit', 'str', True, 'g, ml, piece (count) or a unit stated by the owner with owner_amount evidence'),
    ('owner_amount', 'str', False, 'exact quantity phrase stated by the owner for THIS ingredient; only evidence for keeping a nonstandard unit or vague wording'),
    ('display_unit', 'str', False, 'unit word shown instead of unit, e.g. bird'),
    ('type_hint', 'str', False, 'which kind to buy, e.g. thick-cut'),
    ('aliases', 'list[str]', False, 'other words the owner uses for it'),
    ('removable', 'bool', False, 'true when the dish works without it'),
    ('substitutions', 'list[obj]', False, 'reviewed replacements: ingredient-shaped objects'),
    ('grams_per_unit', 'num', False, 'grams in one unit, for nutrition'),
    ('density_g_ml', 'num', False, 'grams per ml, for nutrition'),
    ('abv', 'num0', False, 'alcohol fraction 0..1'),
    ('food_name', 'str', False, 'nutrition lookup name'),
]
STEP_FIELDS = [
    ('shared', 'bool', False, 'true for a shared session step such as plating all dishes; keep it in one recipe with cross-dish prerequisites; omit its dish marker on the overview'),
    ('ahead', 'enum:required|allowed|never', False, 'typed prep timing; missing=never. required is automatic, allowed asks the owner, never stays before cooking'),
    ('ahead_min_sec', 'num', False, 'required prep: minimum seconds from completion to the next step'),
    ('hold_max_sec', 'num', False, 'allowed prep: maximum seconds before its cook starts; never schedule earlier than cook start minus this limit'),
    ('prep_action', 'enum:fetch|thaw|measure|other', False, 'ingredient preparation operation; fetch takes out/gathers ingredients once per plan; set thaw/measure/other for subsequent work. Legacy ingredient_prep/step_type preparation defaults to fetch'),
    ('ingredient_prep', 'bool', False, 'true ONLY for take out/thaw/measure/gather without knife or heat; phase stays prep; explicit duration and dependencies determine its alert time'),
    ('step_type', 'enum:ingredient_prep', False, 'ingredient preparation tied to its heat step, outside the shared prep-ahead block'),
    ('technique', 'enum:roast|sear|other', False, 'typed cooking technique; never inferred from names or instructions'),
    ('tempering_lead_sec', 'num0', False, 'meat roast/sear preparation lead, 1800..3600 seconds; default 2700; 0 only when owner says meat is already out'),
    ('start_at', 'iso', False, 'explicit step start with timezone; pending work starts no earlier than now'),
    ('id', 'slug', True, 'ascii id, unique in the recipe'),
    ('name', 'str', True, 'short step title shown on cards, checklist and pin'),
    ('phase', 'enum:prep|cook', True, 'prep = knife/marinate/dry-brine/soak without heat; mark take out/thaw/measure/gather as ingredient_prep=true; cook = heat and finishing steps'),
    ('action', 'str', True, 'one how-to sentence; {ingredient_id} is replaced by the ingredient name'),
    ('done_cue', 'str0', False, 'physical completion cue only when the action alone does not explain when to stop; omit or leave empty for self-evident actions'),
    ('duration_sec', 'num0', True, 'expected seconds, 0..86400'),
    ('heat', 'enum:' + '|'.join(HEATS), True, 'heat level; oven steps need resources.oven'),
    ('temp_c', 'num', False, 'oven set temperature in Celsius; only with heat=oven; fill whenever the recipe specifies a temperature'),
    ('oven_mode', 'enum:' + '|'.join(OVEN_MODES), False, 'typed oven mode; only with heat=oven; use stored appliance modes and fill whenever the recipe specifies a mode'),
    ('ingredients', 'list[slug]', True, 'ingredient ids used in this step ([] when none)'),
    ('gear', 'str', False, 'vessel holding the food in this step, in the owner language (e.g. pot or pan); used on check buttons'),
    ('kind', 'enum:active|attended|unattended', False, 'active/attended = hands busy; unattended = a timer runs (oven, simmer); default active'),
    ('requires', 'enum:none|' + '|'.join(RESOURCES[1:]), False, 'appliance the step occupies; default from heat'),
    ('prereq', 'list[str]', False, 'step ids that must finish first; "dish_id:step_id" for another dish; default the previous step'),
    ('instructions', 'list[str]', False, 'numbered how-to lines shown on the step card (replaces action/heat lines)'),
    ('warning', 'str', False, 'one safety line'),
    ('timer_name', 'str', False, 'label on the timer button'),
    ('check_at_sec', 'num', False, 'mid-step check for an unattended step (needs check_cue, less than duration_sec)'),
    ('check_cue', 'str', False, 'what to check at check_at_sec'),
    ('wait_after_sec', 'num0', False, 'rest before the next step may start'),
    ('omit_if_missing', 'list[slug]', False, 'drop this step when one of these ingredients is missing'),
    ('prep_ahead', 'bool', False, 'false keeps a prep step off the prep-ahead message'),
]
TABLES = dict(plan=PLAN_FIELDS, recipe=RECIPE_FIELDS, ingredient=INGREDIENT_FIELDS, step=STEP_FIELDS)

EXAMPLE = {
    'target_at': '2026-10-24T18:30:00+09:00',
    'timezone': 'Asia/Seoul',
    'resources': {'hands': 1, 'burner': 2, 'oven': 1},
    'gear': ['pot'],
    'recipes': [
        {
            'schema': RECIPE_SCHEMA, 'id': 'chicken', 'name': 'roast chicken', 'emoji': '\U0001f357',
            'base_amount': 1, 'amount': 1, 'anchor': 'bird', 'serve_after_sec': 600,
            'ingredients': [
                {'id': 'bird', 'name': 'whole chicken', 'quantity': 1, 'unit': 'piece', 'display_unit': 'bird'},
                {'id': 'butter', 'name': 'butter', 'quantity': 20, 'unit': 'g'},
            ],
            'steps': [
                {'id': 'season', 'name': 'butter and truss the chicken', 'phase': 'prep', 'kind': 'active',
                 'heat': 'none', 'duration_sec': 600, 'ingredients': ['bird', 'butter'],
                 'action': 'Rub {butter} over {bird} and tie the legs.',
                 'done_cue': 'Butter covers the skin and the legs are tied.'},
                {'id': 'roast', 'name': 'roast', 'phase': 'cook', 'kind': 'unattended', 'heat': 'oven',
                 'temp_c': 200, 'oven_mode': 'top_bottom',
                 'requires': 'oven', 'gear': 'roasting pan', 'duration_sec': 2400, 'ingredients': ['bird'], 'prereq': ['season'],
                 'action': 'Roast {bird} at 200C.', 'done_cue': 'Thigh reads 75C and the juices run clear.',
                 'timer_name': 'roast chicken'},
            ],
        },
        {
            'schema': RECIPE_SCHEMA, 'id': 'mash', 'name': 'mashed potato', 'emoji': '\U0001f954',
            'base_amount': 600, 'amount': 600, 'anchor': 'potato',
            'ingredients': [
                {'id': 'potato', 'name': 'potato', 'quantity': 600, 'unit': 'g'},
                {'id': 'salt', 'name': 'salt', 'quantity': 3, 'unit': 'g'},
            ],
            'steps': [
                {'id': 'peel', 'name': 'peel and cut the potatoes', 'phase': 'prep', 'kind': 'active',
                 'heat': 'none', 'duration_sec': 480, 'ingredients': ['potato'],
                 'action': 'Peel {potato} and cut it into 3 cm cubes.', 'done_cue': 'All cubes are even.'},
                {'id': 'boil', 'name': 'boil the potatoes', 'phase': 'cook', 'kind': 'unattended',
                 'heat': 'medium', 'requires': 'burner', 'gear': 'pot', 'duration_sec': 1200, 'ingredients': ['potato', 'salt'],
                 'action': 'Boil {potato} in salted water.', 'done_cue': 'A chopstick slides in with no resistance.'},
            ],
        },
    ],
}


def schema_lines():
    """The printed schema: one line per field, grouped by object."""
    lines = []
    for title, rows in (('PLAN (the file)', PLAN_FIELDS), ('RECIPE (each item of recipes)', RECIPE_FIELDS),
                        ('INGREDIENT (each item of ingredients)', INGREDIENT_FIELDS),
                        ('STEP (each item of steps)', STEP_FIELDS)):
        lines.append(title)
        for name, kind, required, doc in rows:
            lines.append('  %s: %s%s -- %s' % (name, kind, ' (required)' if required else '', doc))
    lines.append('RULES: choose and store a distinct recipe emoji for every dish before session start; '
                 'set yield with unit=serving when servings are known; '
                 'mark shared final steps shared=true, without duplicating them per dish; '
                 'names, actions and cues in the owner language; middle dots are allowed in step titles only; '
                 'every dish has at least one prep step when it needs knife/wash/measure work; '
                 'oven temperature and mode belong in temp_c and oven_mode, never only in action text; '
                 'done_cue is optional: supply a physical cue only when the action alone cannot tell when it is done; '
                 'leave it empty for self-evident actions such as taking out, measuring or plating; amounts use g, ml or piece; '
                 'no vague or spoon/cup amounts unless quoted in that ingredient owner_amount; '
                 'fetch each ingredient once across all dishes, grouping items needed together; '
                 'keep required tempering fetches early and use only the remaining items in later fetches; '
                 'mark retrieval prep_action=fetch and later thaw/measure work with its own prep_action.')
    lines.append('ADD-STEP: cooking add-step STEP.json accepts one STEP object plus dish (recipe id) '
                 'and exactly one of before (pending local step id), after (existing local step id), '
                 'or start_at (timezone-aware ISO time). Existing step ids and callbacks stay valid. '
                 'Retry delivery with cooking show; never cancel/reopen for one step.')
    return lines


def template(dishes=None, appliance=None):
    out = dict(status='TEMPLATE', schema=schema_lines(), example=copy.deepcopy(EXAMPLE),
               appliance=copy.deepcopy(appliance or {}),
               next='Write PLAN.json in this shape (one recipe per dish, owner-language names), '
                    'retaining the chosen dishes, target and companions from the conversation. '
                    'Set eaters to owner plus the stated companions and size recipes for that group. '
                    'Read appliance.type and appliance.modes returned here before any oven question; '
                    'use stored modes without asking again. If type or modes are missing, '
                    'cooking check PLAN.json owns the existing appliance question. '
                    'Fill temp_c and oven_mode for oven temperatures and modes; the plan card never extracts them from action text. '
                    'Use stated quantities directly and reasonable adjustable defaults for unstated amounts. '
                    'Do not delay the plan to ask quantities or meal time; omit target_at if unstated. '
                    'Then run cooking check PLAN.json and fix every listed path before cooking plan PLAN.json '
                    '(the kit posts the plan; never substitute a freehand timeline). '
                    'For target plans wait for the kit stage Start buttons, including any allowed-prep choice; '
                    'without a target run cooking open PLAN.json.')
    if dishes:
        out['dishes_today'] = dishes
    return out


def is_fetch(step):
    """Typed operation only; legacy ingredient preparation means gathering."""
    if 'prep_action' in step:
        return step['prep_action'] == 'fetch'
    return bool(step.get('ingredient_prep') or step.get('step_type') == 'ingredient_prep')


def fetch_errors(source, original=None):
    """Check retrievals across dishes, including compiler-added tempering.

    Ingredient ids are recipe-local. Equal display names identify the same food
    across dishes; unrelated ingredients with the same local id stay distinct.
    Original paths survive automatic insertion, so the model can edit its file.
    """
    errors, seen = [], {}
    original = source if original is None else original
    for ri, recipe in enumerate(source['recipes']):
        original_steps = original['recipes'][ri]['steps']
        locations = {step['id']: '$.recipes[%d].steps[%d]' % (ri, si)
                     for si, step in enumerate(original_steps)}
        ingredients = {ing['id']: ing for ing in recipe['ingredients']}
        for step in recipe['steps']:
            if not is_fetch(step):
                continue
            location = locations.get(step['id'])
            if location is None:
                # Auto temper_<heat-id> belongs to the heat step in PLAN.json.
                heat = next(s for s in original_steps
                            if ('temper_' + s['id'])[:32] == step['id'])
                location = locations[heat['id']]
                field = '.tempering_lead_sec'
            else:
                field = '.ingredients'
            label = '%s:%s (%s)' % (recipe['id'], step['id'], location)
            for iid in dict.fromkeys(step['ingredients']):
                ing = ingredients[iid]
                name = ''.join(unicodedata.normalize('NFKC', ing['name']).casefold().split())
                # Local references remain equal even if display names differ.
                keys = [('id', recipe['id'], iid), ('name', name)]
                previous = next((seen[k] for k in keys if k in seen), None)
                if previous and previous != label:
                    errors.append(dict(path=location + field,
                        problem='duplicate ingredient fetch for %s (%s): %s and %s. '
                        'Merge ingredients needed together into one fetch step; keep required '
                        'tempering lead and prerequisites on the early fetch and remove that '
                        'ingredient from later fetch ingredients and action/instructions. '
                        'For later thaw/measure work set prep_action=thaw/measure.'
                        % (ing['name'], iid, previous, label)))
                for key in keys:
                    seen.setdefault(key, label)
    return errors


def meat_prep(source):
    """Expand typed meat/technique facts once; no interpretation of prose."""
    for recipe in source['recipes']:
        meat = {i['id'] for i in recipe['ingredients'] if i.get('food_type') == 'meat'}
        prepared = set()
        original = recipe['steps']
        expanded = []
        for index, step in enumerate(original):
            if is_fetch(step) and step.get('tempering_lead_sec'):
                prepared.update(step['ingredients'])
            used = [i for i in step['ingredients'] if i in meat and i not in prepared]
            if used and step.get('technique') in ('roast', 'sear'):
                lead = step.setdefault('tempering_lead_sec', 2700)
                if lead and not 1800 <= lead <= 3600:
                    raise ValueError('tempering_lead_sec must be 0 or 1800..3600')
                if step['heat'] == 'none':
                    raise ValueError('roast/sear meat preparation needs a heat step')
                if lead:
                    sid = ('temper_' + step['id'])[:32]
                    if any(s['id'] == sid for s in original + expanded):
                        raise ValueError('automatic meat preparation id conflicts: ' + sid)
                    # Preserve the original implicit chain when inserting a sibling.
                    step.setdefault('prereq', [original[index - 1]['id']] if index else [])
                    lang = agent_conf.conf_str('AGENT_LANG', 'en').split('_')[0].split('-')[0]
                    bundle = Path(__file__).parent.parent / 'service/kit_life/i18n' / (lang + '.json')
                    title = json.loads(bundle.read_text())['cooking.meat_prep'].format(
                        meat=', '.join('{' + i + '}' for i in used))
                    prep = dict(id=sid, name=title, phase='prep',
                                step_type='ingredient_prep', prep_action='fetch', kind='active', heat='none',
                                duration_sec=60, ingredients=used, prereq=[], prep_ahead=False,
                                tempering_lead_sec=lead,
                                action=title, done_cue='', wait_after_sec=lead - 60)
                    expanded.append(prep)
                    step['prereq'] = list(dict.fromkeys(step['prereq'] + [sid]))
                prepared.update(used)
            expanded.append(step)
        recipe['steps'] = expanded
    return source


def _type_ok(kind, value):
    if kind == 'str0':
        return isinstance(value, str)
    if kind == 'str':
        return isinstance(value, str) and value.strip() != ''
    if kind == 'slug':
        return isinstance(value, str) and bool(SLUG.match(value))
    if kind in ('num', 'num0'):
        return (not isinstance(value, bool) and isinstance(value, (int, float)) and value == value
                and value not in (float('inf'), float('-inf')) and (value > 0 or (kind == 'num0' and value == 0)))
    if kind == 'bool':
        return isinstance(value, bool)
    if kind == 'dict':
        return isinstance(value, dict)
    if kind == 'iso':
        return isinstance(value, (str, int, float)) and not isinstance(value, bool)
    if kind.startswith('enum:'):
        return value in kind[5:].split('|')
    if kind == 'list[str]':
        return isinstance(value, list) and all(isinstance(v, str) and v for v in value)
    if kind == 'list[slug]':
        return isinstance(value, list) and all(isinstance(v, str) and SLUG.match(v) for v in value)
    if kind.startswith('list['):
        return isinstance(value, list) and all(isinstance(v, dict) for v in value)
    return True


def _expect(kind):
    if kind.startswith('enum:'):
        return 'one of ' + ', '.join(kind[5:].split('|'))
    return dict(str='a non-empty string', str0='a string (may be empty)', slug='an ascii id (a-z 0-9 _ -, up to 32)',
                num='a number above 0', num0='a number 0 or above', bool='true or false',
                dict='an object', iso='an ISO datetime with timezone').get(
        kind, 'a list of ' + kind[5:-1] + (' objects' if kind[5:-1] in TABLES or kind[5:-1] == 'obj' else 's'))


def _fields(obj, table, path, errors):
    known = {name for name, _, _, _ in table}
    for key in obj:
        if key not in known:
            near = difflib.get_close_matches(key, known, 1, 0.75)
            errors.append(dict(path=path + '.' + key, problem='unknown field' + (
                ' (did you mean %s?)' % near[0] if near else '')))
    for name, kind, required, _ in table:
        if name not in obj:
            if required:
                errors.append(dict(path=path + '.' + name, problem='missing; expected ' + _expect(kind)))
            continue
        if not _type_ok(kind, obj[name]):
            errors.append(dict(path=path + '.' + name, problem='expected ' + _expect(kind)))


def _dots(value, path, errors):
    if isinstance(value, str) and MIDDLE_DOT in value:
        errors.append(dict(path=path, problem='middle dot is not allowed in cooking text'))


def _amount_text(value, path, errors, owner_amounts=()):
    """Check displayed text, keeping only locally attributed owner quantities."""
    if isinstance(value, str):
        quotes = [re.sub(r'\s+', '', q) for q in owner_amounts if isinstance(q, str)]
        for match in list(VAGUE_AMOUNT.finditer(value)) + list(HOUSEHOLD_AMOUNT.finditer(value)):
            phrase = re.sub(r'\s+', '', match.group())
            if not any(phrase in q for q in quotes):
                errors.append(dict(path=path, problem='use a concrete g/ml/piece amount instead of %r; '
                                   'only an owner-stated amount for this ingredient may keep it' % match.group()))
    elif isinstance(value, list):
        for n, item in enumerate(value):
            _amount_text(item, '%s[%d]' % (path, n), errors, owner_amounts)


def _ingredient_amounts(ing, path, errors):
    quote = ing.get('owner_amount', '')
    evidence = [quote] if isinstance(quote, str) else []
    for field in ('name', 'unit', 'display_unit', 'type_hint', 'aliases'):
        _amount_text(ing.get(field), path + '.' + field, errors, evidence)
    display = ing.get('display_unit')
    if isinstance(display, str) and display not in CONCRETE_UNITS + ('bird', '\ub9c8\ub9ac', '\uc54c', '\uc7a5', '\uc870\uac01'):
        if not isinstance(quote, str) or display not in quote:
            errors.append(dict(path=path + '.display_unit', problem='display unit must keep a concrete amount or the owner-stated unit'))
    unit = ing.get('unit')
    if isinstance(unit, str) and unit not in CONCRETE_UNITS:
        if not isinstance(quote, str) or unit not in quote:
            errors.append(dict(path=path + '.unit', problem='use g, ml or piece; another unit needs '
                               'the exact owner_amount phrase for this ingredient'))
    for n, sub in enumerate(ing.get('substitutions', []) if isinstance(ing.get('substitutions'), list) else []):
        if isinstance(sub, dict):
            _ingredient_amounts(sub, '%s.substitutions[%d]' % (path, n), errors)


def check(payload, compile_plan=None):
    """-> (errors, warnings); each error {path, problem}. Never raises."""
    errors, warnings = [], []
    if not isinstance(payload, dict):
        return [dict(path='$', problem='the plan file must be a JSON object')], warnings
    _fields(payload, PLAN_FIELDS, '$', errors)
    for field in ('gear', 'owned_gear'):
        _amount_text(payload.get(field), '$.' + field, errors)
    recipes = payload.get('recipes') if isinstance(payload.get('recipes'), list) else []
    if isinstance(payload.get('recipes'), list) and not 1 <= len(recipes) <= 8:
        errors.append(dict(path='$.recipes', problem='supply one to eight recipes'))
    resources = payload.get('resources')
    if isinstance(resources, dict):
        for key, value in resources.items():
            if key not in RESOURCES or isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 16:
                errors.append(dict(path='$.resources.' + key, problem='expected one of %s with a count 0..16' % ', '.join(RESOURCES)))
        if resources.get('hands', 1) != 1:
            errors.append(dict(path='$.resources.hands', problem='this cooking frame plans for one cook: hands must be 1'))
    recipe_ids, has_prep = set(), False
    for r_index, recipe in enumerate(recipes):
        rpath = '$.recipes[%d]' % r_index
        if not isinstance(recipe, dict):
            continue
        _fields(recipe, RECIPE_FIELDS, rpath, errors)
        _dots(recipe.get('name'), rpath + '.name', errors)
        _amount_text(recipe.get('name'), rpath + '.name', errors)
        if recipe.get('id') in recipe_ids:
            errors.append(dict(path=rpath + '.id', problem='duplicate recipe id'))
        recipe_ids.add(recipe.get('id'))
        if 'emoji' not in recipe:
            warnings.append(dict(path=rpath + '.emoji', problem='no dish emoji; the default pan icon is used for every dish'))
        ingredients = recipe.get('ingredients') if isinstance(recipe.get('ingredients'), list) else []
        ingredient_ids = set()
        for i_index, ing in enumerate(ingredients):
            ipath = '%s.ingredients[%d]' % (rpath, i_index)
            if not isinstance(ing, dict):
                continue
            _fields(ing, INGREDIENT_FIELDS, ipath, errors)
            _ingredient_amounts(ing, ipath, errors)
            _dots(ing.get('name'), ipath + '.name', errors)
            if ing.get('id') in ingredient_ids:
                errors.append(dict(path=ipath + '.id', problem='duplicate ingredient id'))
            ingredient_ids.add(ing.get('id'))
            if isinstance(ing.get('abv'), (int, float)) and not 0 <= ing['abv'] <= 1:
                errors.append(dict(path=ipath + '.abv', problem='ABV must be a fraction from 0 to 1'))
        if 'anchor' in recipe and recipe.get('anchor') not in ingredient_ids:
            errors.append(dict(path=rpath + '.anchor', problem='must be one of the ingredient ids: ' + ', '.join(sorted(map(str, ingredient_ids)))))
        if isinstance(recipe.get('yield'), dict):
            y = recipe['yield']
            if not _type_ok('num', y.get('quantity')) or y.get('unit') not in YIELD_UNITS:
                errors.append(dict(path=rpath + '.yield', problem='expected {"quantity": N, "unit": one of %s}' % ', '.join(YIELD_UNITS)))
        steps = recipe.get('steps') if isinstance(recipe.get('steps'), list) else []
        step_ids = set()
        for s_index, step in enumerate(steps):
            spath = '%s.steps[%d]' % (rpath, s_index)
            if not isinstance(step, dict):
                continue
            _fields(step, STEP_FIELDS, spath, errors)
            for field in ('temp_c', 'oven_mode'):
                if field in step and step.get('heat') != 'oven':
                    errors.append(dict(path=spath + '.' + field, problem='only valid with heat=oven'))
            if step.get('ahead', 'never') != 'never' and step.get('phase') != 'prep':
                errors.append(dict(path=spath + '.ahead', problem='only prep steps can be ahead'))
            required_field = {'required': 'ahead_min_sec', 'allowed': 'hold_max_sec'}.get(step.get('ahead'))
            if required_field and required_field not in step:
                errors.append(dict(path=spath + '.' + required_field, problem='required for ahead=' + step['ahead']))
            used = step.get('ingredients', [])
            quotes = [i.get('owner_amount') for i in ingredients if isinstance(i, dict)
                      and isinstance(used, list) and i.get('id') in used]
            for field in ('name', 'action', 'done_cue', 'warning', 'instructions',
                          'gear', 'timer_name', 'check_cue'):
                _amount_text(step.get(field), spath + '.' + field, errors, quotes)
            for field in ('action', 'done_cue', 'warning'):
                _dots(step.get(field), spath + '.' + field, errors)
            for n, line in enumerate(step.get('instructions', []) if isinstance(step.get('instructions'), list) else []):
                _dots(line, '%s.instructions[%d]' % (spath, n), errors)
            if step.get('id') in step_ids:
                errors.append(dict(path=spath + '.id', problem='duplicate step id'))
            step_ids.add(step.get('id'))
            has_prep = has_prep or step.get('phase') == 'prep'
            if isinstance(step.get('ingredients'), list):
                unknown = [i for i in step['ingredients'] if i not in ingredient_ids]
                if unknown:
                    errors.append(dict(path=spath + '.ingredients', problem='unknown ingredient ids: ' + ', '.join(map(str, unknown))))
            if isinstance(step.get('duration_sec'), (int, float)) and step['duration_sec'] > 86400:
                errors.append(dict(path=spath + '.duration_sec', problem='at most 86400 seconds'))
            if 'check_at_sec' in step and (step.get('kind') != 'unattended' or not step.get('check_cue')
                                           or not isinstance(step.get('duration_sec'), (int, float))
                                           or not isinstance(step.get('check_at_sec'), (int, float))
                                           or step['check_at_sec'] >= step['duration_sec']):
                errors.append(dict(path=spath + '.check_at_sec', problem='needs kind unattended, a check_cue and a value below duration_sec'))
        for s_index, step in enumerate(steps):
            if not isinstance(step, dict) or not isinstance(step.get('prereq'), list):
                continue
            for ref in step['prereq']:
                if not isinstance(ref, str):
                    continue
                dish, _, sid = ref.rpartition(':')
                if not dish and sid not in step_ids:
                    errors.append(dict(path='%s.steps[%d].prereq' % (rpath, s_index), problem='unknown step id %s in this dish' % ref))
    for r_index, recipe in enumerate(recipes):
        for s_index, step in enumerate(recipe.get('steps', []) if isinstance(recipe, dict) and isinstance(recipe.get('steps'), list) else []):
            for ref in step.get('prereq', []) if isinstance(step, dict) and isinstance(step.get('prereq'), list) else []:
                if isinstance(ref, str) and ':' in ref:
                    dish, _, sid = ref.partition(':')
                    other = next((r for r in recipes if isinstance(r, dict) and r.get('id') == dish), None)
                    if not other or sid not in {s.get('id') for s in other.get('steps', []) if isinstance(s, dict)}:
                        errors.append(dict(path='$.recipes[%d].steps[%d].prereq' % (r_index, s_index), problem='unknown step %s' % ref))
    if recipes and not has_prep and payload.get('block', 'all') == 'all':
        warnings.append(dict(path='$.recipes', problem='no prep step: knife, wash and measure work without heat '
                             'belongs in phase prep so it shows on the prep checklist'))
    if not errors:
        try:
            expanded = meat_prep(copy.deepcopy(payload))
            errors.extend(fetch_errors(expanded, payload))
        except (ValueError, TypeError) as exc:
            errors.append(dict(path='$', problem=str(exc)))
    if not errors and compile_plan is not None:
        try:
            compile_plan(payload)
        except KeyError as exc:
            errors.append(dict(path='$', problem='unknown {placeholder} %s: use an ingredient id of the same dish' % exc))
        except (ValueError, TypeError) as exc:
            errors.append(dict(path='$', problem=str(exc)))
    return errors, warnings


def report(errors, warnings, payload=None):
    if errors:
        return dict(status='PLAN_INVALID', errors=errors, warnings=warnings, schema=schema_lines(),
                    next='Fix PLAN.json at every listed path (cooking template prints a complete example), '
                         'then rerun the same command. Do not cook from chat while the plan is invalid.')
    dishes = [r['name'] for r in payload['recipes']] if payload else []
    return dict(status='PLAN_OK', dishes=dishes, warnings=warnings,
                steps=sum(len(r['steps']) for r in payload['recipes']) if payload else 0,
                next='Run cooking plan PLAN.json.' if not warnings else
                     'Fix the warnings if they apply, then run cooking plan PLAN.json.')


def load(path):
    """-> (payload, error dict or None) for a plan path."""
    from pathlib import Path
    try:
        return json.loads(Path(path).read_text(encoding='utf-8')), None
    except FileNotFoundError:
        return None, dict(path='$', problem='no plan file at %s; run cooking template for the shape' % path)
    except (OSError, ValueError) as exc:
        return None, dict(path='$', problem='not valid JSON: %s' % exc)
