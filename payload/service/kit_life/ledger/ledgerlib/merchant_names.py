"""Refresh presentation fields in frozen card evidence through the kit only."""
from . import kit


def refresh(plan):
    catalog = kit.run('ledger-merchant-names')
    names = {row['name']: row for row in catalog['items']}
    changed = False

    def visit(value):
        nonlocal changed
        if isinstance(value, list):
            for row in value:
                visit(row)
        elif isinstance(value, dict):
            raw = value.get('last', {}).get('name') if isinstance(value.get('last'), dict) else None
            raw = raw or value.get('name') or value.get('merchant')
            fact = names.get(raw) if isinstance(raw, str) else None
            if value.get('kind') in ('account', 'credit_card', 'corporate_card', 'debit_card', 'easy_pay', 'cash'):
                fact = None
            if fact and ('display_name' in value or not fact['display_name_missing']):
                if not fact['display_name_missing'] and value.get('display_name') != fact['display_name']:
                    changed = True
                for field in ('display_name', 'display_name_missing', 'display_name_source'):
                    if field != 'display_name' or not fact['display_name_missing']:
                        value[field] = fact[field]
            elif isinstance(value.get('merchant'), str):
                value.setdefault('display_name', value['merchant'])
                value['display_name_missing'] = True
                value['display_name_source'] = None
            for field, row in list(value.items()):
                if field not in ('facts', 'display_name_resolution', 'display_name_missing'):
                    visit(row)

    visit(plan)
    # Refresh pending metadata without changing the frozen payment population.
    if 'display_name_missing' in plan:
        plan['display_name_missing'] = [row for row in plan['display_name_missing']
                                        if names.get(row['name'], {}).get('display_name_missing', True)]
    plan['display_name_resolution'] = catalog['display_name_resolution']
    return changed


def label(row):
    return row.get('display_name') or row.get('name') or row.get('merchant')


def resolve(raw):
    row = dict(merchant=raw, display_name=raw)
    refresh({'items': [row]})
    return label(row)


def attach(question, rows, plan):
    missing = []
    def visit(value):
        if isinstance(value, list):
            for row in value:
                visit(row)
        elif isinstance(value, dict):
            if value.get('display_name_missing') is True:
                last = value.get('last')
                raw = (last.get('name') if isinstance(last, dict) else None) or value.get('name') or value.get('merchant')
                if raw and raw not in [r['name'] for r in missing]:
                    missing.append(dict(name=raw, display_name_missing=True))
            for field, row in value.items():
                if field not in ('display_name_missing', 'display_name_resolution', 'facts'):
                    visit(row)
    visit(rows)
    question['display_name_missing'] = missing
    question['display_name_resolution'] = plan.get('display_name_resolution', {})
    return question
