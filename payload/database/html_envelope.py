"""Pure HTML-envelope primitives for renderer-owned text.

These helpers deliberately do not escape HTML entities: callers pass the
same sanitizer input they passed before this module existed.
"""
import re

_TAG_OPENER = re.compile(r"<(?=[/A-Za-z])")


def defuse_tag_openers(text):
    """Prevent data from forming an HTML tag without entity-escaping it."""
    return _TAG_OPENER.sub(u"<\u2060", text)


def quote_block(body):
    """Wrap one complete renderer-owned body in an HTML blockquote."""
    return "<blockquote>" + body + "</blockquote>"
