"""Block-kind behavior from the kit database. No kind-name decisions live here."""

from functools import lru_cache
import os
import sqlite3

FIELDS = ("mirror_surface", "calendar_key", "timed_calendar_key",
          "overnight_calendar_key", "reminder_lead_min",
          "reminder_header_key", "reminder_key_style", "persons_join",
          "travel_attach", "presence_mode", "requires_place", "adoptable",
          "adopt_path", "location_mode")


@lru_cache(maxsize=32)
def _rows(conn):
    try:
        return {row[0]: dict(zip(FIELDS, row[1:])) for row in conn.execute(
            "SELECT token, %s FROM block_kind_behavior" % ", ".join(FIELDS))}
    except sqlite3.OperationalError:
        return {}


def get(conn, token):
    """A cached read per connection. A missing kind is an explicit error."""
    try:
        return _rows(conn)[token]
    except KeyError:
        raise ValueError("Unknown block kind: %r" % token)


@lru_cache(maxsize=1)
def _default_conn():
    # The mirror's pure route helpers have no connection argument. Their
    # production fallback reads the same SoT database via a read-only handle.
    path = os.path.join(os.path.dirname(__file__), "kit_life.db")
    if os.path.isfile(path):
        conn = sqlite3.connect("file:%s?mode=ro" % path, uri=True)
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name=?",
                        ("block_kind_behavior",)).fetchone():
            return conn
        conn.close()
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE block_kind(token TEXT PRIMARY KEY)")
    conn.executemany("INSERT INTO block_kind VALUES (?)",
                     [(x,) for x in ("task", "appointment", "presence", "record")])
    conn.execute("PRAGMA user_version=57")
    migration = os.path.join(os.path.dirname(__file__), "migrations",
                             "058_block_kind_behavior.sql")
    with open(migration, encoding="utf-8") as source:
        conn.executescript(source.read())
    return conn


def for_event(event, conn=None):
    if conn is None or not _rows(conn):
        conn = _default_conn()
    return get(conn, event["kind"])
