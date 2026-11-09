"""Block-kind behavior from the kit database. No kind-name decisions live here."""

from functools import lru_cache
import os
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

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
        import ro_open  # noqa: E402
        conn = ro_open.open_ro(path)
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


TRAVEL_CALENDAR_ENABLED_KEY = "travel_calendar_enabled"


def travel_calendar_enabled(conn):
    """User preference, read live from the kit; absent preserves default ON."""
    try:
        row = conn.execute("SELECT value FROM config WHERE key=?",
                           (TRAVEL_CALENDAR_ENABLED_KEY,)).fetchone()
    except sqlite3.OperationalError as exc:
        if "no such table: config" not in str(exc):
            raise
        return True  # minimal/old routing fixtures have no config store
    return row is None or str(row[0]).strip() != "0"


def calendar_key(event, conn=None):
    """Shared category decision for the settings writer and mirror router."""
    policy = for_event(event, conn)
    if event.get("block_class") == "travel" and not policy["persons_join"]:
        return "travel"
    if policy["presence_mode"]:
        if presence_crosses_night(event):
            return policy["overnight_calendar_key"]
        if event.get("schedule_kind") == "timed":
            return policy["timed_calendar_key"]
    return policy["calendar_key"]


def presence_crosses_night(event):
    """Compare local dates; malformed bounds keep the ordinary category."""
    start, end = event.get("start_at"), event.get("end_at")
    if not start or not end:
        return False
    try:
        tz = ZoneInfo(event.get("display_tz") or "Asia/Seoul")
        s = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        e = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        return s.astimezone(tz).date() != e.astimezone(tz).date()
    except Exception:  # noqa: BLE001 -- routing must never raise
        return False
