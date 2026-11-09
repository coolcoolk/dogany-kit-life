"""Water mixed into food is swallowed mass, separate from nutrient weight.

Plain side water has no metadata and never enters an eating occasion. Milk is
logged as its own food; choosing milk here must not add another liquid mass.
"""
import json
import math
import re

ATTR = 'satiety_mix_water'
# Manufacturer label, NIH DSLD label 302413, directions: 150-180 ml per scoop.
# The midpoint is an explicit estimate of what was actually mixed, not a fact.
ISO100_LABEL = 'https://dsld.od.nih.gov/label/302413'


def volume(value):
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError('mix water ml must be finite and nonnegative')
    return result


def prepare(life, conn, name, grams, shake=False, water_ml=None,
            liquid=None, reference=None, default_ml=None, source=None):
    context = name or ''
    if liquid is not None and liquid not in ('water', 'milk', 'none'):
        raise ValueError('mix liquid must be water, milk or none')
    if liquid in ('milk', 'none'):
        if water_ml is not None and volume(water_ml) > 0:
            raise ValueError('milk/none cannot also have mix water; log milk as food')
        return None
    if water_ml is not None:
        return dict(ml=volume(water_ml), origin='stated', source='owner stated mix water')
    if not shake and liquid != 'water':
        return None
    if default_ml is not None:
        if not source:
            raise ValueError('directed mix volume requires its label/source')
        return dict(ml=volume(default_ml), origin='estimated', source=source)
    if re.search(r'iso[\s·-]*100|아이소\s*100', name, re.I):
        count = re.search(r'(\d+(?:\.\d+)?)\s*(?:scoops?|스쿱)', context, re.I)
        scoops = float(count.group(1)) if count else None
        if scoops is None:
            food = life.foodcache_lookup(name, conn=conn)
            if not food.get('found'):
                food = life.foodcache_lookup(life._meal_receipt_base(name), conn=conn)
            base = food.get('base_grams')
            if base and grams is not None:
                scoops = float(grams)/float(base)
        if scoops is not None and math.isfinite(scoops) and scoops > 0:
            return dict(ml=165*scoops, origin='estimated',
                        source=ISO100_LABEL+'; directed 150-180ml/scoop; midpoint estimate')
    # Preserve unknown volume, so a dry powder cannot become an exact sample.
    return dict(ml=None, origin='unknown', source='directed mix volume needed')


def read(conn, mid):
    row = conn.execute('SELECT value FROM block_attr WHERE block_id=? '
                       'AND attr_token=? AND source=?', (mid, ATTR, 'module:diet')).fetchone()
    return json.loads(row[0]) if row else None


def save(conn, mid, mixture):
    if mixture is not None:
        conn.execute('INSERT OR REPLACE INTO block_attr(block_id,attr_token,value,source) '
                     'VALUES(?,?,?,?)', (mid, ATTR, json.dumps(mixture, ensure_ascii=False), 'module:diet'))


def water(conn, mid):
    return float((read(conn, mid) or {}).get('ml') or 0)


def mass(life, conn, mid, name, dry_grams):
    grade = life.satiety_weight_grade(conn, name, dry_grams, mid=mid)
    mixture = read(conn, mid)
    if not mixture:
        return dry_grams, grade
    if mixture['ml'] is None:
        return None, None
    # Assumed mixing water cannot teach exact-weight calibration, and cannot
    # authorize changing a label-measured powder serving's nutrients.
    if grade == 'exact' and mixture['origin'] != 'stated':
        grade = None
    return (None if dry_grams is None else dry_grams+mixture['ml']), grade
