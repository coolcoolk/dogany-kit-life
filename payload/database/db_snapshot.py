#!/usr/bin/env python3
"""db_snapshot.py -- WAL-safe binary snapshot / restore of a kit SQLite DB.

WHY (ticket 2026-09-23-kitdb-index-corruption-mirror-down). The live kit DB
runs journal_mode=WAL: committed pages can sit in kit_life.db-wal for minutes
before a checkpoint folds them into kit_life.db, and kit_life.db-shm indexes
that WAL. A file-level copy of kit_life.db ALONE is therefore not a copy of
the database:
  * backup side: the copy silently misses every commit still in the WAL;
  * restore side: copying a .db over the live file while the live -wal/-shm
    stay behind pairs a new main file with an old WAL. The next reader replays
    frames that belong to a different database -> "database disk image is
    malformed" (wrong # of entries in index ...).
On 2026-09-23 the live DB was restored from a hand-made copy at 15:46 and the
mirror sweep hit a malformed index at 15:51. The kit had no snapshot/restore
verb for the shared crew DB (the framework's restore-data.sh targets the
agent-local lifekit.db), so every one of the ~150 *.bak-* files beside the
live DB was made by hand.

THE RULE this module implements: never move DB bytes with the filesystem.
Both directions go through SQLite's online backup API, which reads a
consistent snapshot (WAL included) and writes through the destination's pager
under its normal locks -- the destination's own WAL/SHM stay coherent because
SQLite itself is the writer. No checkpoint, no sidecar copy, no file swap.

  snapshot(src, out)          live -> new standalone file (journal_mode
                              DELETE: one file, nothing beside it to forget)
  restore(snapshot, live)     snapshot -> live, IN PLACE through the backup
                              API, after: integrity_check of the snapshot,
                              same user_version as live (schema drift is the
                              framework restore-data.sh catch-up's job, not
                              this verb's), and a safety snapshot of live.
                              integrity_check of live afterwards.

Every other connection keeps working across a restore: a writer holding the
DB makes the backup step return BUSY and we retry, we never force.

stdlib only. ASCII source.
"""

import argparse
import os
import sqlite3
import sys
import time

BUSY_RETRIES = 50
BUSY_SLEEP_S = 0.1


class SnapshotError(Exception):
    pass


def _ro_conn(path):
    """Read-only open that still sees the WAL of a live DB. A standalone
    snapshot (no -wal/-shm beside it) opens the same way."""
    if not os.path.isfile(path):
        raise SnapshotError("no such database: %s" % path)
    return sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=5)


def integrity(conn):
    row = conn.execute("PRAGMA integrity_check;").fetchone()
    return str(row[0]) if row else "no result"


def user_version(conn):
    return conn.execute("PRAGMA user_version;").fetchone()[0]


def _backup(src_conn, dst_conn):
    """Online backup with bounded BUSY retry (a concurrent writer on either
    side is waited for, never overridden)."""
    last = None
    for _ in range(BUSY_RETRIES):
        try:
            src_conn.backup(dst_conn)
            return
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if "locked" not in msg and "busy" not in msg:
                raise
            last = e
            time.sleep(BUSY_SLEEP_S)
    raise SnapshotError("database busy, backup not taken: %s" % last)


def snapshot(src_path, out_path):
    """WAL-safe copy of src_path into a NEW file out_path. Refuses to
    overwrite. The product is a single self-contained file (journal_mode
    DELETE) and is integrity-checked before it is reported."""
    if os.path.lexists(out_path):
        raise SnapshotError("refusing to overwrite: %s" % out_path)
    for side in ("-wal", "-shm", "-journal"):
        if os.path.lexists(out_path + side):
            raise SnapshotError("stale sidecar beside target: %s%s"
                                % (out_path, side))
    src = _ro_conn(src_path)
    tmp = out_path + ".partial"
    if os.path.lexists(tmp):
        os.unlink(tmp)
    try:
        dst = sqlite3.connect(tmp)
        try:
            _backup(src, dst)
            # The backup copies the source header, WAL mode included; a
            # snapshot must stand alone, so leave it in rollback mode.
            dst.execute("PRAGMA journal_mode=DELETE;")
            ok = integrity(dst)
            ver = user_version(dst)
        finally:
            dst.close()
    except Exception:
        for p in (tmp, tmp + "-wal", tmp + "-shm", tmp + "-journal"):
            if os.path.lexists(p):
                os.unlink(p)
        raise
    finally:
        src.close()
    if ok != "ok":
        os.unlink(tmp)
        raise SnapshotError("snapshot failed integrity_check: %s" % ok)
    os.replace(tmp, out_path)
    return {"path": out_path, "user_version": ver, "integrity": ok}


def restore(snap_path, live_path, safety_path, allow_version_change=False):
    """Replace the CONTENT of live_path with snap_path through the backup
    API. The live file, its -wal/-shm and any symlink pointing at it are
    never touched by the filesystem."""
    snap = _ro_conn(snap_path)
    try:
        s_ok = integrity(snap)
        s_ver = user_version(snap)
    except sqlite3.DatabaseError as e:
        snap.close()
        raise SnapshotError("snapshot unreadable: %s" % e)
    if s_ok != "ok":
        snap.close()
        raise SnapshotError("snapshot failed integrity_check: %s" % s_ok)
    if not os.path.isfile(live_path):
        snap.close()
        raise SnapshotError("no live database at %s (first install is not a "
                            "restore)" % live_path)
    try:
        lv = _ro_conn(live_path)
        try:
            l_ver = user_version(lv)
        finally:
            lv.close()
        if s_ver != l_ver and not allow_version_change:
            raise SnapshotError(
                "user_version mismatch: snapshot %s, live %s -- restoring "
                "another schema version under this code is a migration "
                "problem; use the framework restore path with catch-up"
                % (s_ver, l_ver))
        # Safety first: the live DB as it is right now. A live DB too broken
        # to back up stops the restore -- never overwrite without a copy.
        try:
            safety = snapshot(live_path, safety_path)
        except (SnapshotError, sqlite3.DatabaseError) as e:
            raise SnapshotError("safety snapshot of live failed (%s); "
                                "restore not attempted" % e)
        live = sqlite3.connect(live_path, timeout=5)
        try:
            live.execute("PRAGMA busy_timeout=5000;")
            _backup(snap, live)
            l_ok = integrity(live)
            new_ver = user_version(live)
        finally:
            live.close()
    finally:
        snap.close()
    if l_ok != "ok":
        raise SnapshotError("live failed integrity_check AFTER restore: %s "
                            "(safety snapshot: %s)" % (l_ok, safety["path"]))
    return {"live": live_path, "user_version": new_ver, "integrity": l_ok,
            "safety": safety["path"]}


def _stamp():
    return time.strftime("%Y%m%d-%H%M%S")


def default_snapshot_path(db_path, label):
    tag = "".join(c if (c.isalnum() or c in "-_") else "-" for c in label)
    return "%s.snap-%s-%s" % (os.path.realpath(db_path), tag or "manual",
                              _stamp())


def cli(db_path, cmd, argv):
    """kit_life.sh snapshot [--label L] [--out PATH] [--db PATH]
       kit_life.sh snapshot-restore <snapshot> --confirm [--db PATH]
                                    [--allow-version-change]"""
    if cmd == "snapshot":
        p = argparse.ArgumentParser(prog="kit_life.sh snapshot")
        p.add_argument("--db", default=db_path)
        p.add_argument("--label", default="manual")
        p.add_argument("--out")
        ns = p.parse_args(argv)
        out = ns.out or default_snapshot_path(ns.db, ns.label)
        try:
            r = snapshot(ns.db, out)
        except (SnapshotError, sqlite3.DatabaseError) as e:
            print("snapshot=FAIL (%s)" % e, file=sys.stderr)
            return 1
        print("snapshot=%s" % r["path"])
        print("user_version=%s" % r["user_version"])
        print("integrity=%s" % r["integrity"])
        return 0
    if cmd == "snapshot-restore":
        p = argparse.ArgumentParser(prog="kit_life.sh snapshot-restore")
        p.add_argument("snapshot")
        p.add_argument("--db", default=db_path)
        p.add_argument("--confirm", action="store_true")
        p.add_argument("--allow-version-change", action="store_true")
        ns = p.parse_args(argv)
        if not ns.confirm:
            print("snapshot-restore replaces the live DB content; rerun with "
                  "--confirm", file=sys.stderr)
            return 2
        safety = default_snapshot_path(ns.db, "pre-restore")
        try:
            r = restore(ns.snapshot, ns.db, safety,
                        allow_version_change=ns.allow_version_change)
        except (SnapshotError, sqlite3.DatabaseError) as e:
            print("restore=FAIL (%s)" % e, file=sys.stderr)
            return 1
        print("restore=ok")
        print("user_version=%s" % r["user_version"])
        print("integrity=%s" % r["integrity"])
        print("safety=%s" % r["safety"])
        return 0
    print("unknown snapshot command: %s" % cmd, file=sys.stderr)
    return 1


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: db_snapshot.py <db> snapshot|snapshot-restore ...",
              file=sys.stderr)
        sys.exit(1)
    sys.exit(cli(sys.argv[1], sys.argv[2], sys.argv[3:]))
