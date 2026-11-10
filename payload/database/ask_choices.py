"""ask_choices.py -- every closed-choice *_ASK token carries its choices.

Owner 2026-10-08 07:23 (docs/specs/2026-10-08-ask-choices-buttons.md): "good
question, but give it as buttons -- and do not hardcode them."  ORIGIN_ASK
asked "straight from the office, or home first?" as prose and the owner typed
the answer.

THE CONTRACT (one shape for every kit ask whose answer set is closed):

    <TOKEN><TAB>...the token's own fields...<TAB>choices=<key>=<label>|...
    ASK_OPTIONS<TAB><TOKEN><TAB><ref><TAB>[[OPTIONS: <label> | <label>]]

  * choices are DERIVED FROM DATA (the stay's place, the map apps of the
    region, the ledger categories, the kinds a field accepts ...), never a
    literal list in a skill.  key = the machine answer (an ASCII token or a
    ref like place:12), label = the owner-facing button text, resolved from
    the kit copy table.
  * labels are neutral phrases: no polite sentence endings ("straight
    from the office", never "I will go straight from the office") -- a button is a choice, not a sentence the
    owner says.  lint_label() refuses one.
  * the ASK_OPTIONS line is the EXACT [[OPTIONS: ...]] marker the bridge
    renders as buttons; the agent relays it verbatim on its own line after the
    question.  Free text is still accepted: a typed answer is mapped the same
    way a tap is.

ASK_TOKENS is the registry: every *_ASK token the kit prints is listed with
whether its answer set is closed.  The lint (test_ask_choices_buttons.py)
fails when a *_ASK string appears in kit source without a row here, and when
a closed token's emitter does not print choices.
"""
import ast
import re

OPTIONS_LINE = "ASK_OPTIONS"

# token -> (closed?, where the choices come from / why it is open)
ASK_TOKENS = {
    # closed: the answer is one of a data-derived set
    "CLASH_ASK": (True, "cancel this occurrence | a free slot outside busy bands | keep both"),
    "ORIGIN_ASK": ("gap", "the last stay's place + home (place registry); "
                          "the 'none' variant asks for a place and an "
                          "address -- open"),
    "MAP_PROVIDER_ASK": (True, "google + the region's local map apps "
                               "(map_region.providers)"),
    "ERRAND_ASK": (True, "booked time | before closing (errand classes)"),
    "SPEND_CATEGORY_ASK": (True, "active expense spend_category rows"),
    "DEL_ASK": (True, "delete | keep (confirmation)"),
    "TYPE_ASK": (True, "kinds the target field accepts first "
                       "(field_kind_gate), then the rest, then not-a-name"),
    "PARENT_ASK": (True, "places with rooms + recent appointment places "
                         "(max 4) + another place"),
    "AMBIGUOUS_KIND_ASK": (True, "the refs that share the name + neither"),
    "ROOM_ASK": (True, "the rooms of the booking's place (sub_place)"),
    "SATIETY_ASK": (True, "the owner's 7-level fullness ladder "
                          "(kit_life.SATIETY_LEVELS) + skip on an exact meal"),
    "MEAL_CONFIRM_ASK": (True, "yes | other -- confirm a history-resolved food before recording"),
    "MEAL_TIME_ASK": (True, "owner-approved now | manual; manual continuation accepts a typed clock"),
    "WEEKLY_CATCHUP_ASK": (True, "next morning | skip -- the weekly catch-up "
                                 "setting (kit conf WEEKLY_CATCHUP, "
                                 "weekly_catchup.py)"),
    "DEFER_ASK": (True, "move (change) | keep -- one confirm for the slot workout-defer "
                        "derived (the owner's usual time for the weekday "
                        "class, or the row's own time)"),
    "END_ASK": (True, "until the related event's eve | keep going -- or "
                      "keep going | pick a date (routine add, no end given)"),
    "RETIRE_ASK": (True, "retire | keep -- every future occurrence of a "
                         "routine was deleted in the calendar"),
    "PLANNED_MEAL_ASK": (True, "ate | did not eat | ate something else -- one ask per "
                               "unconfirmed planned meal row of the day (meal-planned-ask); "
                               "the planned row is the open state, no task backs it"),
    # open: the answer is a free value
    "PLANNED_SWAP_ASK": (False, "yes | no -- one JSON row per planned meal "
                                "the eaten meal may replace (meal-planned-swap); "
                                "the relay maps the answer, no ASK_OPTIONS"),
    "PLACE_IDENTITY_ASK": (False, "yes | no -- the existing ANSWER line runs "
                                  "place-identity-answer with the pending ask id; "
                                  "the relay accepts the answer, no ASK_OPTIONS"),
    "SATIETY_PORTION_ASK": (False, "confirm or correct the proposed food portion; "
                                   "SATIETY_PORTION_RELAY uses the existing portion "
                                   "question and accepts a free-form amount"),
    "PLACE_ASK": (False, "a venue name (any place, or \ubbf8\uc815)"),
    "PREP_ASK": (False, "a number of minutes"),
    "STOCK_ASK": (False, "a quantity and its unit, including pack size"),
    "SHOP_PAYMENT_ASK": (False, "one or more capture refs or receipt facts; "
                               "a free-form purchase subset, not one exclusive choice"),
    "TARGET_ASK": (False, "height / weight / age / sex / direction values"),
}

# Polite / sentence endings a button label must not carry (ko), and the en
# question mark.  Corpus literals, not copy.
_POLITE_ENDINGS = ("\uc694", "\ub2c8\ub2e4", "\ub2c8\uae4c", "\uc138\uc694", "\uae4c", "\uc8e0", "?")


def is_closed(token, variant=None):
    closed = ASK_TOKENS.get(token, (None,))[0]
    if closed is True:
        return True
    if isinstance(closed, str):
        return variant == closed
    return False


def lint_label(label):
    """The label, stripped; raises ValueError for an empty label or one that
    ends like a sentence (a polite ending)."""
    s = " ".join(str(label or "").split())
    if not s:
        raise ValueError("ask choice label is empty")
    if s.endswith(_POLITE_ENDINGS):
        raise ValueError("ask choice label %r ends like a sentence -- a "
                         "button label is a neutral phrase" % s)
    return s


def _clean(label):
    # '|' and ']' would break the marker; a label never needs them.  Runtime
    # never raises on a label (a data row may end in any syllable); the copy
    # keys used as labels are linted by the test suite through lint_label.
    s = " ".join(str(label or "").split()) or "-"
    return s.replace("|", "/").replace("]", ")")


def choices_field(choices):
    """'choices=<key>=<label>|...' for the token line (a key may itself
    carry a colon: place:12)."""
    return "choices=" + "|".join(
        "%s=%s" % (k, _clean(l)) for k, l in choices)


def options_marker(choices):
    return "[[OPTIONS: %s]]" % " | ".join(_clean(l) for _k, l in choices)


def options_line(token, ref, choices, **context):
    """The button marker (empty for open asks), and its executable answer."""
    import ask_relay
    marker = options_marker(choices) if choices else ''
    return "%s\t%s\t%s\t%s\tanswer=%s" % (
        OPTIONS_LINE, token, ref, marker,
        ask_relay.command(token, ref, choices, **context))


RELAY = (
    "ASK_OPTIONS_RELAY After a question, put the [[OPTIONS: ...]] field of "
    "its ASK_OPTIONS line verbatim on its own line (the buttons); a typed "
    "answer maps to the choice it names. One question per message: any "
    "[[OPTIONS]] menu owns that reply; defer every other *_ASK or question. "
    "PLACE_ASK and other open questions wait for a reply without a menu. "
    "Never print ASK_OPTIONS, choices= "
    "or this line. Run the ask\'s answer command with the chosen value; a refused write is never reported as done.")


_TOKEN_RE = re.compile(r"([A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*_ASK)\b")


def tokens_in_source(text):
    """Protocol-shaped string literals, excluding raised error diagnostics.

    Parse Python so comments, docstrings and exception messages cannot pose
    as emitters. Unknown tokens in ordinary literals still fail the registry
    lint; no token name (including INVALID_ASK) is exempted.
    """
    tokens = set()

    class Visitor(ast.NodeVisitor):
        def visit_Constant(self, node):
            if isinstance(node.value, str):
                match = _TOKEN_RE.match(node.value)
                if match:
                    tokens.add(match.group(1))

        def visit_Expr(self, node):
            if not isinstance(node.value, ast.Constant):
                self.generic_visit(node)

        def _visit_exception(self, exc):
            if isinstance(exc, ast.Call):
                # Constructor literals are diagnostics. Calls evaluated inside
                # their arguments may still emit asks, so scan those normally.
                self.visit(exc.func)
                args = exc.args + [kw.value for kw in exc.keywords]
                for arg in args:
                    for child in ast.walk(arg):
                        if isinstance(child, ast.Call):
                            self.visit(child)
            elif exc is not None:
                self.visit(exc)

        def visit_Raise(self, node):
            self._visit_exception(node.exc)
            self._visit_exception(node.cause)

    Visitor().visit(ast.parse(text or ""))
    return tokens
