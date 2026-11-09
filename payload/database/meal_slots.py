"""Closed meal-slot vocabulary, shared by capture and presentation.

Korean labels remain the stored values. English keys are typed aliases,
never owner copy. Unknown historical values are preserved for machine reads
and edits, but cannot escape through label().
"""

SLOTS = (
    ('breakfast', '\uc544\uce68'),
    ('lunch', '\uc810\uc2ec'),
    ('dinner', '\uc800\ub141'),
    ('snack', '\uac04\uc2dd'),
    ('workout', '\uc6b4\ub3d9'),
)
LABELS = tuple(label for _key, label in SLOTS)
RANK = {label: rank for rank, label in enumerate(LABELS)}
_VALUES = {value: label for key, label in SLOTS for value in (key, label)}


def canonical(value):
    """Validate a slot before any capture, pending question or stock debit."""
    token = str(value or '').strip().casefold()
    if token not in _VALUES:
        raise ValueError('meal slot must be one of %s (aliases: %s); got %r' % (
            '|'.join(LABELS), '|'.join(key for key, _label in SLOTS), value))
    return _VALUES[token]


def stored(value):
    """Normalize only enum members; leave unrelated legacy facts intact."""
    return _VALUES.get(str(value or '').strip().casefold(), value)


def label(value):
    """Never print an untyped stored slot on an owner surface."""
    return _VALUES.get(str(value or '').strip().casefold(), '?')


def occasion_title(title):
    """Normalize the slot suffix of a typed, legacy cooking occasion on read."""
    prefix, separator, slot = title.rpartition(' ')
    return prefix + separator + stored(slot) if separator else stored(title)


def stored_sql(expression):
    """Read-side enum normalization without changing the database or row shape."""
    # Values come exclusively from the closed enum above, not caller input.
    cases = ' '.join("WHEN '%s' THEN '%s'" % (key, label)
                     for key, label in _VALUES.items())
    return '(CASE LOWER(TRIM(%s)) %s ELSE %s END)' % (
        expression, cases, expression)
