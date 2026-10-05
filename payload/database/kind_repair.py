#!/usr/bin/env python3
"""Audit and optionally repair calendar kind drift on inbound-created blocks."""

import argparse
import os
import re
import sqlite3
import sys

import kit_life

MIRROR_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "mirror"))
sys.path.insert(0, MIRROR_DIR)
import adapter  # noqa: E402


def _ro(path):
    return sqlite3.connect("file:%s?mode=ro" % path, uri=True)


def proposed(source, state):
    ids = {r[0][len("cal_id_"):]: r[1] for r in state.execute(
        "SELECT key,value FROM mirror_state WHERE key IN "
        "('cal_id_appt','cal_id_task','cal_id_travel')")}
    reverse = {value: key for key, value in ids.items()}
    for row in source.execute(
            "SELECT id, ulid, kind, title, schedule_kind, start_at, end_at "
            "FROM timeblock WHERE created_by='gcal-inbound-create' "
            "AND settled_at IS NULL ORDER BY id"):
        eid, ulid, old, title, shape, start, end = row
        log = state.execute(
            "SELECT detail FROM mirror_log WHERE event_ulid=? "
            "AND category='calendar_drift' ORDER BY id DESC LIMIT 1",
            (ulid,)).fetchone()
        match = re.search(r"found_in=(appt|task|travel)", log[0] or "") if log else None
        key = match.group(1) if match else None
        if key is None:
            snap = state.execute(
                "SELECT calendar_id FROM push_snapshot WHERE event_ulid=?",
                (ulid,)).fetchone()
            key = reverse.get(snap[0]) if snap and snap[0] else None
        if key is None:
            continue
        new = adapter.adopt_kind_for_cal(key, shape, start, end, conn=source)
        if new != old and adapter.route_cal_key(
                {"kind": new, "schedule_kind": shape, "start_at": start,
                 "end_at": end, "block_class": None}, source) == key:
            yield eid, ulid, old, new, key, title


def apply_one(source, state, row):
    """Re-adopt a new row and rebind its surface; kind is insert-immutable."""
    import json

    eid, old_ulid, old, new, key, title = row
    current = source.execute(
        "SELECT * FROM timeblock WHERE id=?", (eid,)).fetchone()
    if (current is None or current["kind"] != old or
            current["created_by"] != "gcal-inbound-create" or
            current["settled_at"] is not None):
        raise RuntimeError("candidate changed during repair: %s" % old_ulid)
    cal_row = state.execute(
        "SELECT value FROM mirror_state WHERE key=?", ("cal_id_" + key,)).fetchone()
    cal_id = cal_row[0] if cal_row else None
    surface_id = adapter.bk_get(state, source, old_ulid, "gcal_event_id")
    if not cal_id or not surface_id:
        raise RuntimeError("calendar binding missing: %s" % old_ulid)
    new_id, new_ulid, _, _ = kit_life.mirror_adopt_block(
        source, new, title, current["schedule_kind"], current["start_at"],
        current["end_at"], current["owning_agent"] or "ag",
        "gcal-inbound-create", note=current["note"],
        location=current["location"])
    if new_id is None:
        raise RuntimeError("target kind conflicts with a presence: %s" % old_ulid)
    try:
        patched = adapter.gws(
            "calendar", "events", "patch", "--params",
            json.dumps({"calendarId": cal_id, "eventId": surface_id}),
            body={"extendedProperties": {"private": {
                "ulid": new_ulid, "version": "0"}}})
    except Exception:
        adapter.sdk_bridge.cancel_abandoned(source, new_ulid,
                                            "gcal-rekind-repair")
        raise
    adapter.bk_set(state, source, old_ulid, "gcal_event_id", None)
    adapter.bk_set(state, source, old_ulid, "gcal_etag", None)
    adapter.bk_set(state, source, new_ulid, "gcal_event_id", surface_id)
    if patched.get("etag"):
        adapter.bk_set(state, source, new_ulid, "gcal_etag", patched["etag"])
    adapter.store_push_snapshot(
        state, new_ulid, "calendar",
        adapter.calendar_projection_from_item(patched), calendar_id=cal_id)
    adapter.sdk_bridge.cancel_abandoned(source, old_ulid,
                                        "gcal-rekind-repair")
    adapter.mirror_log(state, "rekind_repaired", new_ulid,
                       "from=%s kind=%s to=%s calendar=%s" %
                       (old_ulid, old, new, key))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--state-db", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    for path in (args.db, args.state_db):
        if not os.path.isfile(path):
            parser.error("database does not exist: %s" % path)
    source = sqlite3.connect(args.db) if args.apply else _ro(args.db)
    state = sqlite3.connect(args.state_db) if args.apply else _ro(args.state_db)
    source.row_factory = sqlite3.Row
    state.row_factory = sqlite3.Row
    try:
        if source.execute("PRAGMA user_version").fetchone()[0] != 58:
            parser.error("source database must be migrated to version 58")
        rows = list(proposed(source, state))
        for eid, ulid, old, new, key, title in rows:
            print("%s\t%s\t%s -> %s\t%s\t%s" %
                  (eid, ulid, old, new, key, title))
        if args.apply:
            for row in rows:
                apply_one(source, state, row)
        print("%s: %d row(s)" % ("applied" if args.apply else "dry-run", len(rows)))
    finally:
        state.close()
        source.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
