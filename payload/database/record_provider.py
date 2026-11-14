#!/usr/bin/env python3
"""Payload-relative DGN-2344 provider; local metadata contract in the provider doc."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

TYPES = ("photo_note", "photo_results_sent")
EPOCH = "1970-01-01T00:00:00.000Z"


def ms_stamp(value):
    return (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=value)).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")

def stamp(value):
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z", value):
        raise ValueError("expected UTC milliseconds")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def open_read(db_path):
    """Read without changing data, including WAL stores with absent sidecars."""
    path = Path(db_path).resolve()
    uri = path.as_uri()
    conn = None
    try:
        conn = sqlite3.connect(uri + "?mode=ro", uri=True)
        # Some SQLite versions defer opening the WAL until the first query.
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        return conn
    except sqlite3.OperationalError:
        if conn is not None:
            conn.close()
        if not path.is_file():
            raise
    # Older macOS SQLite needs write access to initialize WAL/SHM. mode=rw
    # also refuses creation if the store disappears after the existence check.
    conn = sqlite3.connect(uri + "?mode=rw", uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        return conn
    except Exception:
        conn.close()
        raise


def purge(conn):
    """Explicit maintenance only: advance the watermark with actual purged rows."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")
    conn.execute("BEGIN IMMEDIATE")
    watermark = conn.execute("SELECT purged_through FROM photo_record_state WHERE singleton=1").fetchone()[0]
    count = 0
    for table in TYPES:
        condition = "deleted_at IS NOT NULL AND deleted_at<? AND updated_at<?"
        maximum = conn.execute("SELECT MAX(updated_at) FROM " + table + " WHERE " + condition,
                               (cutoff, cutoff)).fetchone()[0]
        if maximum is not None:
            watermark = max(watermark or EPOCH, maximum)
            count += conn.execute("DELETE FROM " + table + " WHERE " + condition,
                                  (cutoff, cutoff)).rowcount
    conn.execute("UPDATE photo_record_state SET purged_through=? WHERE singleton=1", (watermark,))
    conn.commit()
    return {"purged": count, "purged_through": watermark}


TEXT_MAX = 2000


def envelope(conn, typ, row):
    refs = []
    if row["deleted_at"]:
        body = None
    elif typ == "photo_note":
        photo = {"source": row["photo_source"], "kind": row["photo_kind"], "id": row["photo_id"]}
        refs = [photo]
        # lifekit-read.v1.md line 111: photo_note body field is `text` (1..2000 code points).
        # The photo ref travels in photo_refs; no other body field is emitted.
        body = {"text": row["note"][:TEXT_MAX]}
    else:
        # This writer stores PhotoKit local identifiers, just like photo-note-add.
        # Preserve their order and values; never re-search or manufacture IDs.
        refs = [{"source": "photos", "kind": "photo", "id": photo_id}
                for photo_id in json.loads(row["photo_ids_json"])]
        try:
            query = json.loads(row["query"])
        except ValueError:
            query = None
        legacy = not isinstance(query, dict)
        body = {"sent_at": row["sent_at"], "channel": row["channel"],
                "link": row["results_link"],
                "query": {"text": row["query"]} if legacy else query}
        text = row["query"] if legacy else row["query_text"]
        if text:
            body["query_text"] = text[:200]
        if row["total_matches"] is not None:
            body["total_matches"] = row["total_matches"]
    result = {"type": typ, "record_id": row["record_id"], "created_at": row["created_at"],
              "updated_at": row["updated_at"], "deleted": row["deleted_at"] is not None,
              "deleted_at": row["deleted_at"], "photo_refs": refs, "body": body}
    if len(encode(result).encode()) > 8192 and typ == "photo_note" and body is not None:
        note = body["text"]
        low, high = 1, len(note)
        while low < high:
            mid = (low + high + 1) // 2
            body["text"] = note[:mid]
            if len(encode(result).encode()) <= 8192:
                low = mid
            else:
                high = mid - 1
        body["text"] = note[:low]
    if len(encode(result).encode()) > 8192:
        raise ValueError(row["record_id"])
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(allow_abbrev=False)
    ap.add_argument("--types")
    ap.add_argument("--since")
    ap.add_argument("--after", nargs=2, metavar=("UPDATED_AT", "RECORD_ID"))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--purge", action="store_true")
    ns = ap.parse_args(argv)
    try:
        if ns.purge:
            if any(value is not None for value in (ns.types, ns.since, ns.after, ns.limit)):
                raise ValueError("purge cannot be combined with read arguments")
            requested = []
        else:
            requested = ns.types.split(",") if ns.types else []
            if (not requested or set(requested) - set(TYPES) or len(set(requested)) != len(requested)
                    or ns.limit is None or not 1 <= ns.limit <= 1000):
                raise ValueError("invalid types or limit")
            if ns.since is not None:
                stamp(ns.since)
            if ns.after:
                stamp(ns.after[0])
                if not ns.after[1]:
                    raise ValueError("empty record id")
    except ValueError as exc:
        print(encode({"error": "invalid_request", "detail": str(exc)}))
        return 2
    db_path = os.environ.get("KIT_LIFE_DB", os.path.join(os.path.dirname(__file__), "kit_life.db"))
    # Both paths refuse to create a missing store. Only explicit purge may write.
    connection = (sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=rw", uri=True)
                  if ns.purge else open_read(db_path))
    with closing(connection) as conn:
        conn.row_factory = sqlite3.Row
        try:
            if ns.purge:
                print(encode(purge(conn)))
                return 0
            conn.execute("BEGIN")
            state = conn.execute("SELECT last_global_ms,purged_through FROM photo_record_state WHERE singleton=1").fetchone()
            meta = {"checkpoint": ms_stamp(state[0]), "purged_through": state[1]}
            # The epoch is the engine's fresh-pull sentinel, even after a purge.
            if ns.since and ns.since != EPOCH and state[1] and ns.since < state[1]:
                print(encode({"error": "since_too_old", "purged_through": state[1]}))
                return 3
            rows = []
            after = tuple(ns.after) if ns.after else ("", "")
            for typ in requested:
                query = ("SELECT * FROM " + typ + " WHERE updated_at>=? AND "
                         "(updated_at>? OR (updated_at=? AND record_id>?)) "
                         "ORDER BY updated_at,record_id LIMIT ?")
                for row in conn.execute(query, (ns.since or EPOCH, after[0], after[0], after[1], ns.limit)):
                    rows.append((row["updated_at"], row["record_id"], typ, row))
            output = []
            for _, _, typ, row in sorted(rows)[:ns.limit]:
                try:
                    output.append(envelope(conn, typ, row))
                except ValueError:
                    print(encode({"error": "record_too_large", "record_id": row["record_id"]}))
                    return 2
            # Build the entire batch before emitting, so errors are a single line.
            for item in [meta, *output]:
                print(encode(item))
            return 0
        finally:
            conn.rollback()


if __name__ == "__main__":
    sys.exit(main())
