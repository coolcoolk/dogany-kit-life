# -*- coding: utf-8 -*-
"""record_notice.py -- the ONE frame every record notification uses.

Owner 2026-10-27 09:06 ("라이프킷 디자인 시스템"): record notices share one
shape, like the schedule-change ask:

    <title line: one emoji + what was received / recorded>
    > 항목: 값            (ONE quote block, one "label: value" per line,
    > 항목: 값             only the fields that have a value)
    <blank>
    <question>            (only when a question exists; buttons go below it)

This module is the single code path. It owns no wording: the caller passes
the already-localized title, ordered (label, value) pairs and the optional
question, so ko and en differ only in the strings the caller resolved. The
quote form is the one the schedule-change ask uses ("> " per row, which the
channel merges into one blockquote); HTML openers in a value are defused the
same way so user data cannot close or fake the block.

Stdlib only: loaded by kit_life.py (database) and by the ledger service.
"""
import re

from html_envelope import defuse_tag_openers

QUOTE = "> "
SEP = ": "
_WS_RE = re.compile(r"\s+")


def _defuse(text):
    return defuse_tag_openers(text)


def _clean(value):
    """One line, or None when there is nothing to print."""
    if value is None:
        return None
    s = _WS_RE.sub(" ", str(value)).strip()
    return s or None


def render(title, rows, question=None):
    """title: the localized first line. rows: ordered (label, value) pairs;
    a pair whose label or value is empty is dropped (never an empty field).
    question: localized plain line below the block, or None/"" for none."""
    out = [_defuse(_clean(title) or "")]
    block = []
    for label, value in rows or ():
        label, value = _clean(label), _clean(value)
        if label and value:
            block.append(QUOTE + _defuse(label) + SEP + _defuse(value))
    out.extend(block)
    q = _clean(question)
    if q:
        out.append("")
        out.append(q)
    return "\n".join(out)
