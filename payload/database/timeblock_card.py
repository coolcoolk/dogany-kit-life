"""Reusable state and time-block mutation card.

ONE card answers a verb that changed a time block: a state line on top, then
the approved appointment card A body (date header + the time-block row) that
event_cards / day_render already draw. The module owns only what is new: the
envelope, the state-line table and the outcome -> state mapping. The body is
delegated to the shared day renderer; the undated body is shared below.

Owner pick 2026-10-29 08:25 (layout A):

    ✅ 완료
    📅 **10/29(목)**
    > 07:45-08:00 말해보카

Completion, reopening, creation, time/title changes, skipping and cancellation
share this envelope. Labels come from existing copy keys. Undated tasks use
the owner's title line followed by “· 날짜미정” (2026-10-29 16:36).
Functions take the kit module as K, like start_card.py.
"""
from dataclasses import dataclass
from typing import Optional, Tuple

# Compatibility table of the shipped Korean labels. Rendering uses the
# existing locale keys below, so a mutation introduces no new copy.
STATE_LINES = {
    "done": "✅ 완료",
    "open": "미실행",
    "changed": "변경",
    "created": "{title} 등록",
    "postponed": "미루기",
    "cancelled": "취소",
    "skipped": "건너뜀",
}
LIVE_STATES = frozenset(STATE_LINES)

# Reuse the shipped copy for the additional mutation states.
STATE_KEYS = {
    "done": ("remind.start.done_mark", "✅ Done"),
    "open": ("retro.weekly.data.loop_todo", "Not done"),
    "changed": ("retro.weekly.routine.changed", "Changed"),
    "created": ("retro.daily.chip.added", "{title} added"),
    "cancelled": ("remind.start.btn.cancel", "Cancel"),
    "postponed": ("remind.start.btn.snooze", "Snooze"),
    "skipped": ("retro.weekly.routine.skipped", "Skipped"),
}

# settled_outcome (the settle result in the DB) -> state. The state follows the
# outcome the settle wrote, never the block's position on the timeline.
OUTCOME_STATE = {"done": "done", "abandoned": "cancelled"}

RELAY = (
    "CARD_RELAY The lines between CARD_BEGIN and CARD_END are the ALREADY "
    "RENDERED card of the task this write changed: a state line, then the "
    "card. Your whole reply IS that card, relayed EXACTLY ONCE and VERBATIM "
    "-- keep the state line, the date header and the leading '> ' of the "
    "row (or the title and date-undecided lines of an undated task). "
    "Add no success line, no sentence about it, no second copy and no "
    "button. Never print CARD_BEGIN, CARD_END or this CARD_RELAY line itself.")

# The JSON `next` of a verb that printed the card: points at the block, says it
# once (the CARD_RELAY line carries the full rule).
NEXT = ("the mutation card is the CARD block below: relay it exactly once, "
        "with no success line of your own")


@dataclass(frozen=True)
class TimeblockMutationResult:
    """What a verb changed: the block, the verb, the state it ended in.
    applied is False for a refused / failed / no-op write (no card)."""
    verb: str
    block_id: int
    state: Optional[str]
    applied: bool = True
    ids: Tuple[int, ...] = ()


def from_settle(conn, verb, block_id, applied=True):
    """Envelope for a settle, with the state read from the settled outcome."""
    row = conn.execute("SELECT settled_at, settled_outcome, settled_by FROM timeblock "
                       "WHERE id=?;", (int(block_id),)).fetchone()
    state = OUTCOME_STATE.get(row[1]) if row and row[0] is not None else None
    if state == 'cancelled' and row[2] == 'travel-skip':
        state = 'skipped'
    return TimeblockMutationResult(verb, int(block_id), state,
                                   bool(applied and state))


def undated_body(K, title):
    """The literal owner-picked body, shared by mutation and grouped cards."""
    return title + '\n· ' + K._i18n('mutation.card.undated', 'Date undecided')


def render(K, conn, result, now=None):
    """The saved mutation card, or '' for a refusal/missing row/unknown state.
    Read only; a confirmation must never fail a committed write."""
    if not (result and result.applied and result.state in LIVE_STATES):
        return ""
    try:
        row = conn.execute('SELECT title,start_at,kind FROM timeblock WHERE id=?',
                           (result.block_id,)).fetchone()
        if not row:
            return ''
        cards = K.event_cards(conn, [result.block_id], now=now, card_a=True)
        key, fallback = STATE_KEYS[result.state]
        label = K._i18n(key, fallback).format(title=row[0])
    except Exception:
        return ""
    if len(cards) != 1:
        return ""
    return label + "\n" + cards[0]


def emit(K, card):
    """Print the CARD block (the write-card contract) for a rendered card."""
    if card:
        K._emit_card_block([card], relay=RELAY)
