#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Legacy meal-state verbs. Fullness reads/writes use the shared kit DB.

Eaten-at times, settings and retired anchor summaries remain local; calibration
answers are owned by kit_life meal-satiety. No second fullness writer exists.
"""
import json
import os
import re
import sys
import tempfile
import datetime

# Skill dir; PROJECT_ROOT is three levels up (.claude/skills/meal-log -> ROOT).
# Same derivation lookup.py uses -- one idiom, so a relocated skill dir moves
# both files together or neither.
_HERE = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
STATE_PATH = os.path.join(_PROJECT_ROOT, "database", "food_cache.json")

# The keys this module owns. Every other top-level key in the file is read,
# carried through a write untouched, and never inspected.
_OWNED = ("meal_times", "fullness_anchors")

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d$")

# The fullness scale is the owner's own 7-level LABEL ladder, verbatim
# (2026-09-11 16:55): asked for the middle ground -- "10% 4단계사이 절충안을
# 줘라. 4단계는 너무 단순하고 10%는 너무 세부적이고 의미설명을 추가로해야해서"
# -- he wrote these seven lines himself, and then closed the question of
# numbers for good (16:59): "근데 퍼센트말고 그냥 언어 레이블로하자고. 그
# 퍼센트로 계산할거아니잖어".  This SUPERSEDES the 10% numeric scale built on
# his 2026-09-10 statement: a later owner ruling replacing an earlier one, not
# a defect fix.  Consequence: no percentage exists anywhere in this store --
# not stored, not displayed, not mapped.  What the ladder keeps is ORDER: the
# tuple below runs lightest to heaviest, and the ordinal rank (tuple index) is
# internal only -- it answers "heavier or lighter than that", and is never
# surfaced to the owner as a number.  Tokens are the storage identifiers
# (ascii); the Korean is display-only, exactly as he wrote it.
# Unknown answers are refused rather than mapped: a guessed level is a value
# the user did not give, and this store exists to collect what he DID give.
FULLNESS_LEVELS = (
    ("snack",          "안먹은 수준이다(간식)"),
    ("too_little",     "허기만 달랬다"),
    ("slightly_light", "조금 여유롭게 부족하다"),
    ("just_right",     "딱 적당하다"),
    ("slightly_heavy", "조금 부담스럽다"),
    ("too_full",       "너무 배부르다"),
    ("stuffed",        "터질것같다"),
)
_LEVEL_RANK = {tok: i for i, (tok, _label) in enumerate(FULLNESS_LEVELS)}
_LEVEL_LABEL = dict(FULLNESS_LEVELS)


def _die(msg, code=2):
    print(json.dumps({"ok": False, "error": msg}, ensure_ascii=False))
    sys.exit(code)


def load_state(path=None):
    """Read the state file. A missing or unreadable file reads as empty -- this
    store is additive behaviour data, so a first run must not be an error."""
    p = path or STATE_PATH
    try:
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (IOError, OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_state(data, path=None):
    """Atomic replace, same directory (so os.replace stays on one filesystem)."""
    p = path or STATE_PATH
    d = os.path.dirname(p) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".food_cache.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def meal_key(date, meal):
    """The key shape already on disk: "2026-08-11_아침". Preserved exactly --
    a second shape would strand the existing row."""
    return "%s_%s" % (date, meal)


def _check_date(date):
    if not _DATE_RE.match(date or ""):
        _die("date must be YYYY-MM-DD, got %r" % date)


def time_set(date, meal, hhmm, source="user_stated", path=None):
    """Record the time the meal was EATEN.

    This is not the logging time.  Measured 2026-09-10 across 400+ live meal
    blocks, the recorded minute lands only on :00/:15/:30/:45 -- a 15-minute
    rounding of when the row was WRITTEN -- the recording time, not the
    eating time.  The eaten-at instant is a separate fact and only
    the user has it, so it is asked for and stored here rather than derived.
    """
    _check_date(date)
    if not meal:
        _die("meal slot required")
    if not _TIME_RE.match(hhmm or ""):
        _die("time must be HH:MM (24h), got %r" % hhmm)
    data = load_state(path)
    times = data.setdefault("meal_times", {})
    entry = times.setdefault(meal_key(date, meal), {})
    entry["eaten_at"] = hhmm
    entry["source"] = source or "user_stated"
    save_state(data, path)
    return {"ok": True, "key": meal_key(date, meal), "entry": entry}


def _kit():
    sys.path.insert(0, os.path.join(_PROJECT_ROOT, 'database'))
    import kit_life
    return kit_life


def fullness_set(date, meal, level, kcal=None, path=None):
    """Route the old verb through the same member/occasion writer as the CLI."""
    _check_date(date)
    if not meal or level not in _LEVEL_RANK:
        _die("meal slot and a valid fullness token required")
    if kcal is not None:
        try:
            if float(kcal) < 0:
                raise ValueError()
        except (TypeError, ValueError):
            _die("kcal must be a nonnegative number")
    kit = _kit()
    result = kit.meal_satiety(date, meal, level)
    entry = kit._satiety_state_load()['meal_times'].get(result['occasion'])
    return {"ok": True, "key": result['occasion'], "entry": entry,
            "anchor_key": level, "anchor": None}


def get(date, meal, path=None):
    _check_date(date)
    kit = _kit()
    conn = kit._satiety_read_conn()
    try:
        shared = kit._satiety_state_load(conn=conn)
        key, _ids = kit._satiety_context(conn, shared, date, meal)
        local = (load_state(path).get('meal_times') or {}).get(meal_key(date, meal), {})
        # A stale legacy fullness value must not mask an unanswered occasion.
        entry = {k: v for k, v in local.items() if k not in
                 ('fullness', 'grams', 'kcal', 'resolved', 'scaled', 'calib')}
        entry.update(shared['meal_times'].get(key, {}))
        return {"ok": True, "key": key, "entry": entry or None}
    finally:
        conn.close()


def anchors(path=None):
    """The anchor table as it stands.  Known level keys are returned in scale
    order, lightest first -- ordinal rank, internal only, never a number shown
    to the user.  Any other key (the legacy free-text row, or a numeric key
    from the superseded scale) is returned after them, sorted, never dropped."""
    data = load_state(path)
    tbl = data.get("fullness_anchors") or {}
    known = sorted((k for k in tbl if k in _LEVEL_RANK), key=_LEVEL_RANK.get)
    other = sorted(k for k in tbl if k not in _LEVEL_RANK)
    return {"ok": True,
            "anchors": [{"key": k,
                         **({"label": _LEVEL_LABEL[k]} if k in _LEVEL_LABEL
                            else {}),
                         **(tbl[k] if isinstance(tbl[k], dict)
                            else {"scalar": tbl[k]})}
                        for k in known + other],
            "level_n": len(known), "legacy_n": len(other)}


def setting(key, path=None):
    data = load_state(path)
    return {"ok": True, "key": key,
            "value": (data.get("settings") or {}).get(key)}


def main(argv):
    if len(argv) < 1:
        _die("usage: mealstate.py <time-set|fullness-set|get|anchors|setting> ...")
    mode = argv[0]
    try:
        if mode == "time-set":
            if len(argv) < 4:
                _die("time-set <date> <meal> <HH:MM> [source]")
            out = time_set(argv[1], argv[2], argv[3],
                           argv[4] if len(argv) > 4 else "user_stated")
        elif mode == "fullness-set":
            if len(argv) < 4:
                _die("fullness-set <date> <meal> <%s> [kcal]"
                     % "|".join(tok for tok, _l in FULLNESS_LEVELS))
            out = fullness_set(argv[1], argv[2], argv[3],
                               argv[4] if len(argv) > 4 else None)
        elif mode == "get":
            if len(argv) < 3:
                _die("get <date> <meal>")
            out = get(argv[1], argv[2])
        elif mode == "anchors":
            out = anchors()
        elif mode == "setting":
            if len(argv) < 2:
                _die("setting <key>")
            out = setting(argv[1])
        else:
            _die("unknown verb %r" % mode)
    except (IOError, OSError) as exc:
        _die("state write failed: %s" % exc, code=3)
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
