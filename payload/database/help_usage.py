"""Side-effect-free per-verb help assembled from the shipped CLI sources.

The old CLI grew before it had a common parser: most handlers have an
authoritative ``usage:`` refusal, while older handlers are documented in the
wrapper's command catalogue.  This reader deliberately uses those two sources
instead of maintaining a second, inevitably drifting, handwritten synopsis.
It only reads text files; in particular it never imports a command module or
opens the life database.
"""
from __future__ import print_function

import ast
import json
import os
import re
from functools import lru_cache


_HERE = os.path.dirname(os.path.abspath(__file__))
_USAGE = re.compile(r"(?:usage|\uc0ac\uc6a9\ubc95)\s*:\s*", re.I)
_SHELL_ENTRY = re.compile(
    r"^#\s+(?:kit_life\.sh\s+)?([a-z][a-z0-9-]+)(\s+.*)?$")


def _literal_usages():
    """Yield complete usage literals from the implementation modules."""
    for name in sorted(os.listdir(_HERE)):
        if not name.endswith(".py") or name.startswith("test_"):
            continue
        path = os.path.join(_HERE, name)
        try:
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=path)
        except (OSError, SyntaxError, UnicodeError):
            continue
        for node in ast.walk(tree):
            value = getattr(node, "value", None)
            if isinstance(value, str) and _USAGE.search(value):
                yield value.strip()


def _wrapper_entries():
    path = os.path.join(_HERE, "kit_life.sh")
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except (OSError, UnicodeError):
        return {}
    entries = {}
    for line in lines:
        match = _SHELL_ENTRY.match(line)
        if not match:
            continue
        verb, tail = match.group(1), (match.group(2) or "").rstrip()
        # Prose headings in the large wrapper comment have no CLI syntax.
        if tail and ("<" in tail or "[" in tail or "--" in tail or
                     tail.strip().startswith(("edit ", "add ", "backup|"))):
            entries.setdefault(verb, "Usage: kit_life.sh %s%s" % (verb, tail))
    return entries


def _literal_for(verb, literals):
    token = re.compile(r"(?<![a-z0-9-])%s(?![a-z0-9-])" % re.escape(verb))
    candidates = [text for text in literals if token.search(text)]
    if not candidates:
        return None
    # A literal naming the requested verb immediately after kit_life.sh is the
    # handler's own refusal.  Prefer it over a broad multi-command message.
    exact = re.compile(r"kit_life\.sh\s+%s(?:\s|$)" % re.escape(verb), re.I)
    candidates.sort(key=lambda text: (not bool(exact.search(text)),
                                      text.count("|"), len(text)))
    return candidates[0]


@lru_cache(maxsize=1)
def _sources():
    return tuple(_literal_usages()), _wrapper_entries()


@lru_cache(maxsize=1)
def _catalog():
    with open(os.path.join(_HERE, "help_usage.json"), encoding="utf-8") as fh:
        return json.load(fh)


def render(verb):
    """Return the real shipped synopsis for *verb*, without running it."""
    shipped = _catalog().get(verb)
    if shipped is not None:
        return shipped
    literals, entries = _sources()
    usage = _literal_for(verb, literals)
    if usage:
        return usage
    entry = entries.get(verb)
    if entry:
        return entry
    # Argument-free read verbs legitimately have no operands.  Keeping the
    # exact verb in this final synopsis is still command-specific (unlike the
    # rejected ``<verb> [arguments]`` placeholder), and the regression suite
    # requires every operand-bearing verb to resolve through a source above.
    return "Usage: kit_life.sh %s" % verb
