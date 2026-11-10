"""DGN-2344 writer verbs for kit-owned photo records."""
import json
import os
import re
import secrets
import sqlite3
import sys
import time
from datetime import datetime, timezone, timedelta


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def stamp_ms(value):
    parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    return int((parsed - datetime(1970, 1, 1, tzinfo=timezone.utc)) // timedelta(milliseconds=1))


def ms_stamp(value):
    return (datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=value)).isoformat(
        timespec="milliseconds").replace("+00:00", "Z")


def _issue(conn):
    # Caller holds BEGIN IMMEDIATE: issuance and the record commit atomically.
    last = conn.execute("SELECT last_global_ms FROM photo_record_state WHERE singleton=1").fetchone()[0]
    issued = max(stamp_ms(_now()), last + 1)
    conn.execute("UPDATE photo_record_state SET last_global_ms=? WHERE singleton=1", (issued,))
    return ms_stamp(issued)


def _record_id():
    # A 48-bit millisecond timestamp plus 80 random bits, encoded as ULID base32.
    value = (int(time.time() * 1000) << 80) | int.from_bytes(secrets.token_bytes(10), "big")
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    out = ""
    for _ in range(26):
        out = alphabet[value & 31] + out
        value >>= 5
    return "lk_" + out


def _pairs(values):
    result = {}
    for value in values:
        if "=" not in value:
            raise ValueError("expected key=value: %s" % value)
        key, val = value.split("=", 1)
        result[key] = val
    return result


def _validate_query(query):
    # lifekit-read.v1.md: only these structured filter fields are interpreted.
    for key, value in query.items():
        if value is None:
            continue
        if key == "labels":
            if (not isinstance(value, list) or len(value) > 100 or
                    any(not isinstance(label, str) or not 1 <= len(label) <= 128 for label in value)):
                raise ValueError("invalid query labels")
        elif key in ("start_time", "end_time"):
            if (not isinstance(value, str) or not 20 <= len(value) <= 40 or
                    datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None):
                raise ValueError("query time requires timezone")
        elif key == "limit":
            if type(value) is not int or not 1 <= value <= 500:
                raise ValueError("invalid query limit")
        elif key in ("latitude", "longitude", "radius_m"):
            low, high = {"latitude": (-90, 90), "longitude": (-180, 180),
                         "radius_m": (0, 20100000)}[key]
            if type(value) not in (int, float) or not low <= value <= high:
                raise ValueError("invalid query coordinate")


def cli(db_path, verb, argv):
    try:
        args = _pairs(argv)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        now = _issue(conn)
        if verb == "photo-note-add":
            required = ("source", "kind", "id", "note")
            if any(k not in args for k in required):
                raise ValueError("photo-note-add requires source= kind= id= note=")
            rid = _record_id()
            conn.execute("INSERT INTO photo_note VALUES (?,?,?,?,?,?,?,?,?,NULL)",
                         (rid, args["source"], args["kind"], args["id"], args["note"],
                          args.get("place_id"), args.get("episode_id"), now, now))
        elif verb == "photo-note-upd":
            rid = args.pop("record_id", None)
            allowed = {"source": "photo_source", "kind": "photo_kind", "id": "photo_id",
                       "note": "note", "place_id": "place_id", "episode_id": "episode_id"}
            if not rid or not args or set(args) - set(allowed):
                raise ValueError("photo-note-upd requires record_id= and note=/photo/place fields")
            sets, values = [], []
            for key, value in args.items():
                sets.append(allowed[key] + "=?")
                values.append(None if key in ("place_id", "episode_id") and value == "" else value)
            values += [now, rid]
            cur = conn.execute("UPDATE photo_note SET %s,updated_at=?,deleted_at=NULL WHERE record_id=?" % ",".join(sets), values)
            if not cur.rowcount:
                raise ValueError("photo note not found")
        elif verb == "photo-note-del":
            rid = args.get("record_id")
            if not rid or len(args) != 1:
                raise ValueError("photo-note-del requires record_id=")
            cur = conn.execute("UPDATE photo_note SET deleted_at=?,updated_at=? WHERE record_id=? AND deleted_at IS NULL", (now, now, rid))
            if not cur.rowcount:
                raise ValueError("photo note not found or already deleted")
        elif verb == "photo-send-log":
            required = ("sent_at", "query", "photo_ids", "link")
            if any(k not in args for k in required):
                raise ValueError("photo-send-log requires sent_at= query= photo_ids=<JSON> link=")
            photo_ids = json.loads(args["photo_ids"])
            if not isinstance(photo_ids, list) or not all(isinstance(x, str) for x in photo_ids):
                raise ValueError("photo_ids must be a JSON string array")
            if not 1 <= len(photo_ids) <= 60 or any(
                    not 8 <= len(value) <= 100 or not re.fullmatch(
                        r"[A-Za-z0-9-]{8,64}(?:/[A-Za-z0-9]{1,16}){0,3}", value)
                    for value in photo_ids):
                raise ValueError("photo_ids requires 1..60 PhotoKit local identifiers")
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{3}Z", args["sent_at"]):
                raise ValueError("sent_at requires UTC milliseconds")
            stamp_ms(args["sent_at"])
            # Prior callers are the Telegram-only results-link adapter. Keep its
            # text form compatible, but persist structured JSON for all new rows.
            channel = args.get("channel", "telegram")
            if not 1 <= len(channel) <= 128 or not 1 <= len(args["link"]) <= 4096:
                raise ValueError("invalid channel or link length")
            try:
                query = json.loads(args["query"])
            except ValueError:
                query = None
            query_text = args.get("query_text")
            if not isinstance(query, dict):
                query_text = query_text if query_text is not None else args["query"]
                query = {"text": args["query"]}
            if query_text is not None and not 1 <= len(query_text) <= 200:
                raise ValueError("query_text requires 1..200 characters")
            # Reject non-finite JSON before it reaches the receiver.
            _validate_query(query)
            query_json = json.dumps(query, ensure_ascii=False, allow_nan=False)
            total = int(args["total_matches"]) if "total_matches" in args else None
            if total is not None and not 0 <= total <= (1 << 53) - 1:
                raise ValueError("invalid total_matches")
            rid = _record_id()
            body = {"sent_at": args["sent_at"], "channel": channel,
                    "link": args["link"], "query": query}
            if query_text is not None:
                body["query_text"] = query_text
            if total is not None:
                body["total_matches"] = total
            public = {"record_id": rid, "type": "photo_results_sent", "created_at": now,
                      "updated_at": now, "deleted": False, "deleted_at": None, "body": body,
                      "photo_refs": [{"source": "photos", "kind": "photo", "id": value} for value in photo_ids]}
            # Include refs/metadata and the receiver's ASCII JSON serialization.
            if len(json.dumps(public, allow_nan=False).encode()) > 8192:
                raise ValueError("record exceeds 8 KiB")
            conn.execute("INSERT INTO photo_results_sent "
                         "(record_id,sent_at,query,photo_ids_json,results_link,chat_ref,created_at,updated_at,channel,query_text,total_matches) "
                         "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (rid, args["sent_at"], query_json, json.dumps(photo_ids, ensure_ascii=False),
                          args["link"], args.get("chat_ref"), now, now, channel, query_text, total))
        else:
            raise ValueError("unknown photo record verb")
        conn.commit()
        print(rid)
        return 0
    except (ValueError, json.JSONDecodeError, sqlite3.Error) as exc:
        conn.rollback()
        print("%s: %s" % (verb, exc), file=sys.stderr)
        return 2
    finally:
        conn.close()
