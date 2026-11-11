"""Pure numeric mechanics; callers own coercion, units and display policy."""


def comma(value):
    """Group the supplied number without changing its type or precision."""
    return format(value, ',')


def split_man(value):
    """Absolute ten-thousand units and remainder after caller-owned coercion."""
    return divmod(abs(value), 10000)


def man_one_decimal(value):
    """Divide with the supplied numeric type; retain a grouped fixed decimal."""
    return format(value / 10000, ',.1f')


def man_whole(value):
    """Divide with the supplied numeric type, then use Python's round."""
    return comma(round(value / 10000))


def dining_man(value):
    """Dining's float division and round, with a stripped one-decimal result."""
    return ('%.1f' % round(value / 10000.0, 1)).rstrip('0').rstrip('.')


def float_whole_man(value):
    """Whole-man display for callers that explicitly divide through a float."""
    return comma(int(round(value / 10000.0)))


def decimal_man(value):
    """Exact Decimal units; integral amounts retain comma grouping."""
    from decimal import Decimal
    return (format(Decimal(value) / 10000, 'f').rstrip('0').rstrip('.')
            if value % 10000 else comma(value // 10000))
