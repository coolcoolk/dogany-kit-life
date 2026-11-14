"""Pure date presentation parts; parsing and validation belong to adapters."""


def month_day(value):
    """Read parts from an already validated date-like value."""
    return value.month, value.day


def slash_md(month, day):
    """Render supplied parts without adding validation or a year policy."""
    return '%d/%d' % (month, day)
