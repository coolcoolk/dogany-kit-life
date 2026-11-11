"""Explicit adapters for ledger prose/table blocks (not item grouping)."""
from html_envelope import quote_block


def block(value):
    return quote_block(value)


def lines(values):
    return block('\n'.join(values))
