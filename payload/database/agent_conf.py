"""Per-instance kit_life tuning knobs, read from config/agent.conf.

Home is the INSTANCE-LOCAL config/agent.conf (NOT the shared kit_life.db). Ag and
Warg share one kit_life DB but keep separate config/agent.conf, so each instance
can carry different tuning values. Every knob is config-first with a hardcoded
default: a blank/absent key is byte-identical to the pre-config constant
(zero-delta). Same file + reader shape as holiday_source._conf_val.

English/ASCII only; stdlib only; no external deps.
"""

import os

_CONF = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)),
                 "..", "config", "agent.conf"))


def _unquote(val):
    """Remove ONE matched surrounding quote pair, if present.

    config/agent.conf has two reader conventions. The framework side SOURCES
    the file, so a value containing a space MUST be quoted there or the shell
    runs its second token as a command. This module takes the raw text after
    '=', so without this step a quoted value arrives WITH its quotes -- and
    the failure is silent, not loud: conf_int('"9"') hits ValueError and
    returns the hardcoded default, which is exactly the "invent a 0 offset"
    behaviour migration 038 forbids. Stripping here makes quoted and unquoted
    values read identically, which is what lets the conf value be quoted.

    Only a MATCHED pair at both ends is removed, so a value that legitimately
    contains a quote, and a lone quote character, are left untouched.
    """
    if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
        return val[1:-1]
    return val


def _raw(key):
    """First 'key=value' value in config/agent.conf, else None. Best-effort.

    Whitespace is trimmed first, then a surrounding quote pair -- that order
    matters, or a value written as `"x" ` would keep its quotes.
    """
    try:
        with open(_CONF) as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(key + "="):
                    return _unquote(line.split("=", 1)[1].strip())
    except OSError:
        pass
    return None


def conf_int(key, default):
    """int for key from env (override) or config/agent.conf; default on
    absence / blank / parse error. default preserves the old constant."""
    val = os.environ.get(key)
    if val is None:
        val = _raw(key)
    if val is None or val == "":
        return default
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


def conf_str(key, default, allowed=None):
    """Lowercased, stripped string for key (env override, then
    config/agent.conf); default on absence / blank. When `allowed` (a set of
    lowercased tokens) is given, a value outside it falls back to default --
    so a typo'd knob is byte-identical to leaving it unset (fail-open)."""
    val = os.environ.get(key)
    if val is None:
        val = _raw(key)
    if val is None or val.strip() == "":
        return default
    val = val.strip().lower()
    if allowed is not None and val not in allowed:
        return default
    return val


def conf_csv(key, default=None):
    """Set of lowercased, stripped tokens from a comma-separated key (env
    override, then config/agent.conf); default (a set, or None -> empty set) on
    absence / blank. A blank/absent key yields the default set, so leaving the
    knob unset is zero-delta."""
    val = os.environ.get(key)
    if val is None:
        val = _raw(key)
    if val is None or val == "":
        return set(default) if default else set()
    return {t.strip().lower() for t in val.split(",") if t.strip()}
