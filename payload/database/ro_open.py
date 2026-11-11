"""ro_open -- the one read-only open for the shared kit DB.

`file:...?mode=ro` cannot open a WAL database whose -shm sidecar does not
exist yet (no other connection is open): SQLite must create the -shm, and
fails with "unable to open database file". open_ro() tries mode=ro with a
probe query and, on OperationalError, opens a normal connection pinned
`PRAGMA query_only=ON` (still never writes a row). Stdlib only.
"""
import os
import sqlite3
from urllib.parse import quote


def open_ro(path, timeout=5.0):
    """Read-only sqlite3 connection to `path`; see module docstring."""
    path = str(path)
    conn = None
    try:
        conn = sqlite3.connect("file:%s?mode=ro" % quote(path), uri=True,
                               timeout=timeout)
        conn.execute("SELECT count(*) FROM sqlite_master;").fetchone()
        return conn
    except sqlite3.OperationalError:
        if conn is not None:
            conn.close()
        # A missing file is NOT the -shm case: a plain connect would create an
        # empty database. Re-raise so callers see "no database" as before.
        if not os.path.isfile(path):
            raise
    conn = sqlite3.connect(path, timeout=timeout)
    conn.execute("PRAGMA query_only=ON;")
    return conn
