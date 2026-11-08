"""Shared meal history and food-cache name/alias search. Standard library only."""
import collections
import json
import re
import unicodedata


NUTRIENTS = ('kcal', 'carb', 'protein', 'fat', 'fiber', 'sugar', 'alcohol')


def _base(life, name):
    if name.endswith(life.PLANNED_SUFFIX):
        name = name[:-len(life.PLANNED_SUFFIX)]
    return re.sub(r'\s*\(\s*\d+(?:\.\d+)?\s*g\s*\)$', '', name, flags=re.I)


def _compact(life, name):
    return life.normalize_food_key(unicodedata.normalize('NFKC', name)).replace(' ', '')


def _foods(life, conn):
    foods = {}
    for row in conn.execute('SELECT key, name FROM meal_food'):
        foods[row[0]] = set(row)
    for alias, canonical in conn.execute('SELECT alias, canonical FROM meal_alias'):
        key = life.normalize_food_key(canonical)
        # Canonical may be the cache's display name rather than its key.
        target = next((k for k, names in foods.items() if canonical in names), key)
        foods.setdefault(target, {canonical}).add(alias)
    return foods


def _identity(life, name, foods):
    base = _base(life, name)
    key = life.normalize_food_key(base)
    # Exact cache name takes priority over a conflicting alias.
    if key in foods:
        return key
    for canonical, names in foods.items():
        if any(_compact(life, n) == _compact(life, base) for n in names):
            return canonical
    return key


def meal_search(life, conn, query):
    if not query or not _compact(life, query):
        raise ValueError('food name must contain searchable characters')
    foods = _foods(life, conn)
    needle = _compact(life, query)
    keys = {key for key, names in foods.items()
            if any(needle in _compact(life, n) for n in names)}
    cols = ('id', 'date', 'meal', 'name', 'kcal', 'carb', 'protein', 'fat',
            'fiber', 'sugar', 'alcohol', 'grams')
    rows = [dict(zip(cols, row)) for row in conn.execute(
        'SELECT ' + ','.join(cols) + ' FROM ' + life.MEALS_V32 +
        ' ORDER BY date DESC, id DESC')]
    counts = collections.Counter()
    for row in rows:
        row['key'] = _identity(life, row['name'], foods)
        row['planned'] = row['name'].endswith(life.PLANNED_SUFFIX)
        if not row['planned']:
            counts[row['key']] += 1
    # One row per item: its newest occurrence (rows are newest first), carrying
    # the eaten count; items rank by count, then recency, so a dominant single
    # candidate is the first line.
    latest = {}
    for row in rows:
        if row['key'] in keys or needle in _compact(life, row['name']):
            held = latest.get(row['key'])
            if held is None or (held['planned'] and not row['planned']):
                latest[row['key']] = row
    out = sorted(latest.values(), key=lambda r: (r['date'], r['id']), reverse=True)
    out.sort(key=lambda r: -counts[r['key']])
    for row in out:
        row.update(count=counts[row['key']], source='history')
    seen = set(latest)
    # Cache-only matches have no invented meal date.
    for key in sorted(keys - seen):
        food = life.foodcache_lookup(key, conn=conn)
        if food.get('found'):
            row = {k: food.get(k) for k in NUTRIENTS + ('grams', 'name')}
            row.update(id=None, date=None, meal=None, key=key, count=0,
                       planned=False, source='cache')
            out.append(row)
    return out


def alias_candidate(life, conn, ref, name):
    """(alias, canonical) to store when a meal is logged from the free-text
    `ref` and `name` is an item already known (cache, alias target or eaten
    history); None for a new item, a ref that already resolves, or one that
    would shadow a different food. Call BEFORE the meal is written."""
    ref = (ref or '').strip()
    if not ref or not _compact(life, ref):
        return None
    foods = _foods(life, conn)
    key = _identity(life, name, foods)
    known = key in foods
    if not known:
        for (old,) in conn.execute('SELECT name FROM ' + life.MEALS_V32):
            if not old.endswith(life.PLANNED_SUFFIX) and _identity(life, old, foods) == key:
                known = True
                break
    if not known:
        return None
    compact = _compact(life, ref)
    for k, names in foods.items():
        if any(_compact(life, n) == compact for n in names):
            return None        # already resolves (this item or another)
    row = conn.execute('SELECT name FROM meal_food WHERE key=?', (key,)).fetchone()
    return ref, (row[0] if row and row[0] else _base(life, name))


def search_cli(life, argv):
    as_json = '--json' in argv
    args = [a for a in argv if a != '--json']
    if len(args) != 1:
        life._err('usage: kit_life.sh meal-search <food-or-alias> [--json]')
    conn = life.get_conn()
    try:
        try:
            rows = meal_search(life, conn, args[0])
            for row in rows:
                if row.get('meal') is not None:
                    row['meal'] = life.meal_slots.label(row['meal'])
        except ValueError as exc:
            life._err('meal-search: %s' % exc)
        if as_json:
            print(json.dumps(rows, ensure_ascii=False))
        else:
            for row in rows:
                fields = ('date', 'meal', 'name', 'kcal', 'carb', 'protein',
                          'fat', 'fiber', 'sugar', 'alcohol', 'grams', 'count',
                          'id', 'source', 'planned')
                print('\t'.join('' if row.get(k) is None else str(row[k]) for k in fields))
    finally:
        conn.close()
