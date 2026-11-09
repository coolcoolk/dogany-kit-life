#!/usr/bin/env python3
"""Calendar preflight, explicit target switching, and read-only feed reads."""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import adapter as A


def preflight():
    try:
        from service import calendar
    except ImportError:
        return {"transport": "gws-cli", "class": "backend_not_available",
                "legacy": {"gws": A._gws_bin(), "cryptography": "required"}}
    result = calendar.status(A._INSTANCE_ROOT)
    mode = os.environ.get("CALENDAR_TRANSPORT", A._load_conf().get("CALENDAR_TRANSPORT", "auto"))
    legacy = mode == "gws-cli" or (mode == "auto" and not result["connected"]
                                  and not result.get("class")
                                  and result.get("health") in (None, "needs_consent"))
    if mode == "auto" and legacy:
        binary = A._gws_bin()
        legacy = bool(os.access(binary, os.X_OK) or A.shutil.which(binary))
    result["transport"] = "gws-cli" if legacy else "compat"
    result["legacy"] = {"gws": A._gws_bin(), "cryptography": "required"} if legacy else None
    return result


def re_engrave(state, source, target):
    """The only target switch. Preserve old namespace, never delete remotely.

    Pin the requested identity before provisioning; a failed provisioning is
    resumable with the same explicit command. Old bindings remain archived.
    """
    if not target or ":" not in target:
        raise ValueError("--to requires backend:account")
    if not A._acquire_drain_lock(state):
        return {"status": "locked"}
    try:
        old = A.get_state(state, "mirror_target")
        A.set_state(state, "mirror_target", target)
        try:
            conn = A._calendar_connector()
            if conn is None:
                raise A.CalendarPaused("backend_not_available")
            A._check_connector_target(conn, state)
            if conn.account_key != target:
                raise A.CalendarPaused("target_mismatch")
        except A.CalendarPaused as exc:
            A.set_state(state, "mirror_target", old or "")
            return A.calendar_pause(state, exc.cls)
        if old and old != target:
            archive = [dict(row) for row in state.execute("SELECT * FROM push_snapshot")]
            A.set_state(state, "snapshots:" + old, json.dumps(archive))
            if not A.get_state(state, "mirror_legacy_target") and old.startswith("google:"):
                A.set_state(state, "mirror_legacy_target", old)
        if old != target:
            state.execute("DELETE FROM push_snapshot")
            state.execute("DELETE FROM mirror_outbox WHERE status IN ('queued','claimed')")
            archived = A.get_state(state, "snapshots:" + target)
            if archived:
                columns = [row[1] for row in state.execute("PRAGMA table_info(push_snapshot)")]
                for saved in json.loads(archived):
                    state.execute("INSERT INTO push_snapshot(%s) VALUES(%s)" % (
                        ",".join(columns), ",".join("?" for _ in columns)),
                        [saved.get(column) for column in columns])
            state.commit()
        targets = A.ensure_mirror_engraved(state)
        queued = A.outbox_backfill(state, source)
        return {"status": "ok", "target": target, "targets": targets, "queued": queued}
    finally:
        A._release_drain_lock(state)


def read_subscriptions():
    """Preserve the complete iCalendar document, including TZ/recurrence.

    A separate read surface: no SoT mutation, mirror adoption or outbox path.
    Consumers receive unfolded component properties plus original text; they
    must not interpret an occurrence missing from a feed as a cancellation.
    """
    from service import calendar
    results = []
    for feed in calendar.sources(A._INSTANCE_ROOT):
        try:
            raw = feed.read()
            unfolded = raw.replace("\r\n ", "").replace("\r\n\t", "").replace("\n ", "").replace("\n\t", "")
            events, current = [], None
            for line in unfolded.splitlines():
                if line == "BEGIN:VEVENT":
                    current = []
                elif line == "END:VEVENT":
                    if current is not None:
                        events.append(current)
                    current = None
                elif current is not None and ":" in line:
                    name, value = line.split(":", 1)
                    current.append({"property": name, "value": value})
            results.append({"id": feed.id, "backend": "ics", "read_only": True,
                            "icalendar": raw, "events": events})
        except A.GwsError:
            results.append({"id": feed.id, "backend": "ics", "read_only": True,
                            "class": "subscription_unavailable"})
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "subscriptions", "re-engrave"))
    parser.add_argument("--to")
    parser.add_argument("--state")
    args = parser.parse_args()
    if args.state:
        A.STATE_DB_PATH = args.state
        os.environ["MIRROR_STATE_DB"] = args.state
    if args.command == "preflight":
        result = preflight()
    elif args.command == "subscriptions":
        result = read_subscriptions()
    else:
        if not args.to:
            parser.error("re-engrave requires --to backend:account")
        state, source = A.open_state_db(), A.get_src_conn()
        try:
            result = re_engrave(state, source, args.to)
        finally:
            source.close()
            state.close()
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
