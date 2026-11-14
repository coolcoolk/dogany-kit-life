"""Owner item grouping: short items share a quote; large items keep theirs.

Callers supply complete items, including their continuation lines. Headings,
tables and prose are deliberately outside this helper's responsibility.
"""
import re

from html_envelope import quote_block

_MARKER = re.compile(r'^(?:• |\d+[.)] |\[[ v-]\] )')


def item_blocks(items):
    blocks, short = [], []
    for item in items:
        rows = str(item).split('\n')
        if len(rows) <= 2:
            if not _MARKER.match(rows[0]):
                rows[0] = '• ' + rows[0]
            short.extend(rows)
        else:
            if short:
                blocks.append('\n'.join(short))
                short = []
            blocks.append(str(item))
    if short:
        blocks.append('\n'.join(short))
    return blocks


def quote_items(items):
    """Renderer-owned HTML in, HTML quotes out; callers escape dynamic data."""
    return '\n'.join(quote_block(b) for b in item_blocks(items))


def markdown_items(items):
    blocks = item_blocks(items)
    return '\n'.join(('> ' if n == 0 else '>^ ') + block.replace('\n', '\n> ')
                     for n, block in enumerate(blocks))
