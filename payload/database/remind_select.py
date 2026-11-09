#!/usr/bin/env python3
"""
DGN-273 remind selection engine -- the single testable home of the alert
selection that routines/remind.sh sends. The shell script stays a thin pipe:
this module decides WHICH alerts are due; the script dedups (sent markers)
and pushes.

Selection contract (per event row, timed + unsettled only):
    notify_policy NULL / 'default'
        -> lead alert at (start - default lead) + on-time alert at start
           default lead: task = 30 min, appointment = 120 min (legacy remind
           behavior, byte-compatible with the pre-007 fixed windows)
    'silent'     -> no alerts
    'start_only' -> on-time alert only
    'custom'     -> lead alert at (start - notify_lead_min) + on-time alert
An alert is DUE when its instant falls within +/- SLACK_SEC of `now`
(SLACK_SEC = 300 matches the 5-minute poll cadence: the legacy 30-min task
window [now+25m, now+35m] is exactly |start-30m - now| <= 5m).

Errands (a no-time task at a place, errand.py, owner 2026-10-06/07) are
all-day rows, so they get their own pass: the same task policy, timed off
the LATEST DEPARTURE (closing time - margin - travel), kind "errand", keys
errand:<ulid>:<departure> / errand_start:..., bullets
"**leave by HH:MM**" / "N min left" / "<title> closes HH:MM" / route. No
departure known (closed, travel unknown) -> no alert.

Dedup keys (emitted for the sent-marker file) preserve the live shapes so a
cutover never double-fires:
    appt lead   -> appt:<id>:<local_iso>          appt start -> appt_start:...
    task lead   -> task:<ulid>:<start_at_utc>     task start -> task_start:...

Backward compatibility: on a pre-007 DB (no notify columns) every row is
treated as 'default' -- identical alerts to today.

CLI: remind_select.py [--db PATH] [--now EPOCH] [--body]
Output: TSV  key kind alert header_time local_hhmm title location purpose persons
        header_time = lead minutes, or unpadded M/D for dated_<weekday>.
        eve/dated tokens affect presentation only; dedup stays lead-shaped.
        (kind = the header stem remind.sh reads as remind.<kind>_<alert>:
        the block kind, or travel/prep for a derived helper, whose body then
        names its anchor -- kit_life.TRAVEL_ROLE_REMIND)
        Movement keeps its bullet lines (owner 2026-10-07): next step,
        arrival, route, optional directions. Prep uses one Markdown quote
        (owner 2026-10-21): anchor title, departure and countdown, optional
        ride lines. Its linked origin -> destination and arrival sit below
        the quote; directions are inline URL buttons. With no move leg,
        prep shows the anchor time and never invents a departure or route.
        Bold "**...**", links "[t](url)" and quote lines "> ..." pass through
        push.sh's sanitizer into Telegram HTML.
        Lines need the framework's remind.sh --body (files/remind-sh-body-
        lines.framework.patch); the legacy columns carry the same facts on
        ONE line (remind.sh joins title and hhmm with its " . ").
        --body (opt-in, for a transport that renders lines): TSV
        key kind alert header_time body buttons -- body = the lines joined by
        BODY_SEP, for every row (a plain row without a person brief equals
        the legacy join); buttons = Telegram inline URL-button rows as JSON
        ([[{"text": ..., "url": ...}, ...]]) for a helper with map buttons
        the text does not already link, else empty
        (kit_life._travel_route_links).
        A task/routine START alert is not a row when the kit posted it
        itself: start_card.py sends the three-button card (snooze / help
        me start / done) through push.sh --strict --buttons-json and keeps
        the message_id for the tap verb (kit_life.sh start-tap). No strict
        push.sh, or a refused send -> the row, as before.

English/ASCII only.
"""

import argparse
from clock_parts import utc_datetime, local_datetime
import datetime
import json
import os
import sqlite3
import sys
from datetime import timezone
from pathlib import Path
from zoneinfo import ZoneInfo

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

import kit_life  # noqa: E402
import agent_conf  # noqa: E402
import kind_policy  # noqa: E402
import reminder_owner  # noqa: E402

# Per-instance tunable reminder lead (config/agent.conf: LIFEKIT_LEAD_TASK_MIN /
# LIFEKIT_LEAD_APPOINTMENT_MIN); blank/absent -> 30 / 120 (zero-delta).
DEFAULT_LEAD_MIN = {
    token: agent_conf.conf_int(
        {"task": "LIFEKIT_LEAD_TASK_MIN",
         "appointment": "LIFEKIT_LEAD_APPOINTMENT_MIN"}.get(token, ""),
        policy["reminder_lead_min"])
    for token, policy in kind_policy._rows(kind_policy._default_conn()).items()
}

SLACK_SEC = 300          # +/- band around each alert instant (5-min poll)
BODY_SEP = "\x1f"        # --body line separator (never in a title/place)


def _cell(value):
    """One TSV cell on one physical line: remind.sh reads rows with `read`,
    so an embedded newline (a place stored as "label\naddress") split one
    alert into a second, header-only message (Ag 2026-10-29 18:58)."""
    return (str(value or "").replace("\r\n", " ").replace("\r", " ")
            .replace("\n", " ").replace("\t", " "))
LINE_JOIN = " \u00b7 "    # Legacy English column join.
_FMT = "%Y-%m-%dT%H:%M:%SZ"

# Optional exclusions supplement domain routing; blank needs no live config edit.
EXCLUDE_OWNERS = agent_conf.conf_csv("REMIND_EXCLUDE_OWNERS")


def policy_alerts(kind, notify_policy, notify_lead_min, default_lead=None):
    """[(alert, lead_min)] for one row. alert = 'lead' | 'start'.
    Unknown future policy values fall back to 'default' (fail-open to the
    legacy behavior rather than silently dropping alerts)."""
    if notify_policy == "silent":
        return []
    if notify_policy == "start_only":
        return [("start", 0)]
    if notify_policy == "custom" and notify_lead_min is not None:
        return [("lead", int(notify_lead_min)), ("start", 0)]
    lead = default_lead if default_lead is not None else DEFAULT_LEAD_MIN.get(kind, 30)
    return [("lead", lead), ("start", 0)]


def _dur(minutes):
    """Minutes as the reminder reads them: 50 -> "50 min", 60 -> "1h",
    70 -> "1h 10min" (remind.dur_m / _h / _hm; ko reads N-bun / N-sigan)."""
    h, m = divmod(int(minutes), 60)
    if not h:
        return kit_life._i18n("remind.dur_m", fallback="{m} min").format(m=m)
    if not m:
        return kit_life._i18n("remind.dur_h", fallback="{h}h").format(h=h)
    return kit_life._i18n("remind.dur_hm",
                          fallback="{h}h {m}min").format(h=h, m=m)


def _local(ts, tz):
    return local_datetime(utc_datetime(ts, _FMT), ZoneInfo(tz or "Asia/Seoul"))


def _config_dir():
    return Path(reminder_owner.instance_root(kit_life)) / "config"
RETRO_DEFAULT_HHMM = "22:00"


def _kv(path, key):
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    return None


def _hhmm_min(text, default=RETRO_DEFAULT_HHMM):
    """'HH:MM' or 'Sun HH:MM' -> minutes since midnight (default on junk)."""
    for cand in ((text or "").split()[-1:] + [default]):
        try:
            h, m = cand.split(":")
            if 0 <= int(h) < 24 and 0 <= int(m) < 60:
                return int(h) * 60 + int(m)
        except ValueError:
            continue
    return 0


def retro_covers_eve(now_ts, tz):
    """True when tonight's retro (daily; the weekly review on Sunday) is on for
    this instance and has not run yet, so its tomorrow section carries the
    item and a standalone 'eve' alert would only front-run it. Off, or its
    time already passed (= it ran) -> False: the alert stays standalone."""
    now = datetime.datetime.fromtimestamp(now_ts, ZoneInfo(tz or "Asia/Seoul"))
    sunday = now.weekday() == 6
    cfg = _config_dir()
    kit, agent = cfg / "kit_life.conf", cfg / "agent.conf"
    if sunday:
        on = _kv(kit, "BUNDLE_WEEKLY_REVIEW") == "on"
        at = _kv(agent, "BRIEF_TIME_WEEKLY")
    else:
        on = _kv(kit, "BUNDLE_DAILY_RETRO") == "on"
        at = _kv(agent, "BRIEF_TIME_RETRO")
    return on and now.hour * 60 + now.minute < _hhmm_min(at)


def _calendar_alert(alert, local, now_ts, lead_min):
    """Presentation only: keep the policy alert for keys and body decisions.

    remind.sh always duration-formats column four for {time}, but also
    substitutes it verbatim for {min}. Dated headers use only {min}, with an
    unpadded M/D (valid shell arithmetic) and a localized weekday in the key.
    lead_min stays numeric internally; header_time is the wire-only value.
    Compare the actual poll date in the item's timezone, including midnight
    slack and DST, rather than assuming a lead duration implies tomorrow.
    """
    if alert == "lead":
        today = datetime.datetime.fromtimestamp(now_ts, local.tzinfo).date()
        days = (local.date() - today).days
        if days == 1:
            return "eve", lead_min
        if days > 1:
            weekday = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
            return ("dated_" + weekday[local.weekday()],
                    "%d/%d" % (local.month, local.day))
    return alert, lead_min


def _helper_lines(helper, alert_ts, title, hhmm, extra_lines=()):
    """Prep: one quote containing the anchor title, next action and optional
    ride lines, followed by a linked route and arrival time. Directions are
    inline URL buttons; a known-unlocated place keeps its search notice.
    Without a move leg, show the anchor time without inventing a departure.
    Movement alerts retain their existing one-bullet-per-fact format.
    Countdowns always use the alert's own instant, including custom leads.
    """
    tz = helper["display_tz"]
    if helper["stem"] == "prep":
        quoted = ["**%s**" % title]
        left = 0
        if helper["depart_at"]:
            next_at = _local(helper["depart_at"], tz)
            step = _step(next_at, alert_ts)[0]
            left = (next_at.timestamp() - alert_ts) // 60
        else:
            step = "**%s**" % hhmm
            if helper["anchor_at"]:
                left = (_local(helper["anchor_at"], "UTC").timestamp()
                        - alert_ts) // 60
        if left > 0:
            step = kit_life._i18n(
                "remind.prep_next", fallback="{step} ({dur} before)").format(
                    step=step, dur=_dur(left))
        quoted.append(step)
        route = helper.get("route_md") or helper.get("route_text")
        if helper["depart_at"] and not route:
            quoted.append(_arrive_text("", hhmm).strip())
        quoted.extend(x.removeprefix(_bullet("")) for x in extra_lines)
        out = ["> " + x for line in quoted for x in line.splitlines()]
        if route:
            out.append(kit_life._i18n(
                "remind.prep_arrival", fallback="{route} - arrive {hhmm}")
                .format(route=route, hhmm=hhmm))
        d = helper.get("directions")
        if d:
            out.append(kit_life._md_link(d["text"], d["url"]))
        return out
    route = _route_lines(helper)
    # a move leg (outbound or the way back): arrives at arrive_label
    arrive = _arrive_text(
        helper.get("arrive_label") or title,
        (_local(helper["arrive_at"], tz).strftime("%H:%M")
         if helper.get("arrive_at") else hhmm))
    step = _step(_local(helper["leg_start"], tz), alert_ts)
    return [_bullet(x) for x in step] + [_bullet(arrive)] + route + list(extra_lines)


def _bullet(text):
    return kit_life._i18n("remind.line", fallback="- {text}").format(
        text=text)


def _arrive_text(what, hhmm):
    return kit_life._i18n("remind.arrive", fallback="{what} {hhmm}").format(
        what=what, hhmm=hhmm)


def _route_lines(helper):
    """The route bullet (linked place names) and the directions bullet."""
    out = []
    route = helper.get("route_md") or helper.get("route_text") or ""
    if route:
        out.append(_bullet(route))
    d = helper.get("directions")
    if d:
        out.append(_bullet(kit_life._md_link(d["text"], d["url"])))
    return out


def _left(minutes):
    return kit_life._i18n("remind.left", fallback="{dur} left").format(
        dur=_dur(minutes))


def _step(depart, alert_ts):
    """The next-step facts for a departure at `depart` (local dt): ["**leave
    now**"] when it is due at this alert, else ["**leave HH:MM**", "<n>
    left"] -- one fact per line."""
    left = (depart.timestamp() - alert_ts) // 60
    if left > 0:
        return [kit_life._i18n(
            "remind.next_depart", fallback="**Leave {hhmm}**").format(
                hhmm=depart.strftime("%H:%M")), _left(left)]
    return [kit_life._i18n("remind.depart_now", fallback="**Leave now**")]


def _ride_rids(conn):
    """recurrence_id -> title of the ride_travel routines (070); {} on a
    pre-070 store."""
    try:
        out = dict(conn.execute(
            "SELECT recurrence_id, title FROM routine_def "
            "WHERE placement='ride_travel';").fetchall())
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name='routine_placement'").fetchone():
            out.update(conn.execute("SELECT r.recurrence_id,r.title FROM routine_def r "
                                    "JOIN routine_placement p ON p.routine_id=r.id"))
        return out
    except sqlite3.Error:
        return {}


def _ride_carrier(conn, leg):
    """ulid of the helper whose ON-TIME alert announces this move leg's
    departure, or None when nothing alerts for it: the leg itself unless it
    is silent; a silent outbound leg (the DGN-305 prep split) is announced
    by its prep block's alert ("**07:45 leave** . 30 min left")."""
    if leg["notify_policy"] != "silent":
        return leg["ulid"]
    if leg["derived_role"] != "before":
        return None
    prep = conn.execute(
        "SELECT ulid, notify_policy FROM timeblock WHERE block_class=? "
        "AND derived_from=? AND derived_role='before_prep' "
        "AND settled_at IS NULL ORDER BY start_at LIMIT 1;",
        (kit_life.TRAVEL_BLOCK_CLASS, leg["derived_from"])).fetchone()
    if prep is None or prep[1] == "silent":
        return None
    return prep[0]


def _ride_lines(conn, ride, helper_ulid, leg_start, scope):
    """070: the "on the way: <routine> <dur>" lines a helper's on-time alert
    carries -- one per ride routine sitting in the leg this helper
    announces. This is the ride routine's one daily alert; an attached ride
    does not also emit its own start ping."""
    if not ride or not leg_start:
        return []
    leg = kit_life._roller_mod().ride_leg_at(conn, leg_start)
    if leg is None or _ride_carrier(conn, leg) != helper_ulid:
        return []
    out = []
    for title, sa, ea, owner, rid, rd in conn.execute(
            "SELECT title, start_at, end_at, owning_agent, recurrence_id, rec_date FROM timeblock "
            "WHERE recurrence_id IN (%s) AND settled_at IS NULL "
            "AND rec_exception=0 "
            "AND (start_at=? OR (start_at>=? AND end_at<=?)) "
            "AND end_at IS NOT NULL ORDER BY id;"
            % ",".join("?" * len(ride)), list(ride) + [leg_start, leg_start, leg['end_at']]):
        seat = kit_life._roller_mod().occurrence_leg(conn, rid, rd, sa, ea)
        if seat is None or seat['ulid'] != leg['ulid']:
            continue
        if not scope.accepts(owner):
            continue
        mins = (_local(ea, "UTC").timestamp()
                - _local(sa, "UTC").timestamp()) // 60
        out.append(_bullet(kit_life._i18n(
            "remind.ride_along", fallback="On the way: {title} {dur}").format(
                title=title, dur=_dur(mins))))
    return out


def _ride_start_line(conn, title, sa, ea, rid=None, rd=None):
    """The routine's own short cue, independent of its travel carrier."""
    minutes = (_local(ea, "UTC").timestamp()
               - _local(sa, "UTC").timestamp()) // 60
    line = kit_life._i18n(
        "remind.routine_start", fallback="Time for {title} {dur}").format(
            title=title, dur=_dur(minutes))
    if (kit_life._roller_mod().occurrence_leg(conn, rid, rd, sa, ea)
            if rid else kit_life._roller_mod().ride_leg_at(conn, sa)) is not None:
        line += LINE_JOIN + kit_life._i18n(
            "remind.routine_in_transit", fallback="On the way")
    return line


def _buttons_json(links):
    """Telegram inline-keyboard rows for the map links (one row), or ''."""
    if not links:
        return ""
    return json.dumps([[{"text": l["text"], "url": l["url"]} for l in links]],
                      ensure_ascii=False, separators=(",", ":"))


def _utc_str(ts):
    return datetime.datetime.fromtimestamp(ts, tz=timezone.utc).strftime(_FMT)


def _has_notify_cols(conn):
    cols = {r[1] for r in conn.execute("PRAGMA table_info(timeblock);")}
    return "notify_policy" in cols


def _get_participants(conn, event_id):
    """Return comma-joined participant names for an event (empty string if none)."""
    import person_labels
    return ", ".join(person_labels.participants(conn, event_id))


def _brief_date(value, today):
    import relmod
    return relmod.brief_date(value, today, kit_life._i18n)


def _appointment_brief_lines(conn, event_id, now_ts, tz, db_path=None):
    """At most three attendees, one contiguous blockquote; no writes.
    Resolve the optional sidecar beside THIS life DB (including --db), never
    fall back to another instance. relmod owns all relationship data logic.
    """
    try:
        people = conn.execute(
            "SELECT p.id, p.name, p.birthday FROM persons p "
            "JOIN event_persons ep ON ep.person_id=p.id "
            "WHERE ep.event_id=? ORDER BY p.name, p.id", (event_id,)).fetchall()
    except sqlite3.Error:
        return []
    if not people:
        return []
    import relmod
    conn.create_function("lk_local_day", 2, relmod.local_day)
    rel = None
    try:
        path = db_path or next((r[2] for r in conn.execute("PRAGMA database_list")
                                if r[1] == "main"), "")
        if path:
            # Preserve the selected agent path; SQLite resolves DB symlinks.
            sidecar = Path(path).absolute().with_name("relationship.db")
            try:
                rel = sqlite3.connect(sidecar.as_uri() + "?mode=ro", uri=True)
                rel.row_factory = sqlite3.Row
            except sqlite3.Error:
                pass
        today = datetime.datetime.fromtimestamp(
            now_ts, ZoneInfo(tz or relmod.DEFAULT_DISPLAY_TZ)).date().isoformat()
        items = []
        import person_labels
        for pid, name, birthday in people[:3]:
            name = person_labels.label(conn, pid)
            try:
                data = relmod.brief_data(conn, rel, pid, birthday, today,
                                         before=_utc_str(now_ts))
            except sqlite3.Error:
                # Optional sidecar unavailable/old: keep core event history.
                try:
                    data = relmod.brief_data(conn, None, pid, birthday, today,
                                             before=_utc_str(now_ts))
                except sqlite3.Error:
                    continue
            items.append("\n".join(relmod.brief_lines(data, name, today, kit_life._i18n)))
        from item_grouping import markdown_items
        return markdown_items(items).splitlines()
    finally:
        if rel is not None:
            rel.close()


def select_alerts(conn, now_ts, db_path=None, owner=None):
    """All due alerts at `now_ts` (epoch seconds). Returns a list of dicts:
    {key, kind, alert, lead_min, hhmm, title, location, purpose}. Pure read;
    dedup against already-sent keys is the caller's job."""
    scope = reminder_owner.NotificationScope(kit_life, conn, EXCLUDE_OWNERS, owner)
    has_notify = _has_notify_cols(conn)
    if has_notify:
        max_custom = conn.execute(
            "SELECT COALESCE(MAX(notify_lead_min), 0) FROM timeblock "
            "WHERE settled_at IS NULL;").fetchone()[0]
    else:
        max_custom = 0
    # Old pre-058 databases retain the same seeded policy in read-only memory.
    policies = (kind_policy._rows(conn) or
                kind_policy._rows(kind_policy._default_conn()))
    def lead_for(kind):
        default = policies.get(kind, {}).get("reminder_lead_min", 30)
        knob = {"task": "LIFEKIT_LEAD_TASK_MIN",
                "appointment": "LIFEKIT_LEAD_APPOINTMENT_MIN"}.get(kind)
        return agent_conf.conf_int(knob, default) if knob else default
    max_lead_min = max([int(max_custom or 0)] +
                       [lead_for(k) for k in policies])
    lo = _utc_str(now_ts - SLACK_SEC)
    hi = _utc_str(now_ts + max_lead_min * 60 + SLACK_SEC + 1)
    notify_sel = ("e.notify_policy, e.notify_lead_min" if has_notify
                  else "NULL, NULL")
    travel_sel = ("e.block_class, e.derived_from, e.derived_role, e.end_at"
                  if kit_life._travel_schema_ready(conn)
                  else "NULL, NULL, NULL, e.end_at")
    rows = conn.execute(
        "SELECT e.id, e.ulid, e.kind, e.title, e.start_at, e.display_tz, "
        "e.location, e.purpose, e.owning_agent, "
        + notify_sel + ", " + travel_sel + ", e.recurrence_id "
        "FROM timeblock e "
        "WHERE e.settled_at IS NULL AND e.schedule_kind='timed' "
        "AND e.start_at >= ? AND e.start_at <= ? "
        "ORDER BY e.start_at, e.id;", (lo, hi)).fetchall()
    ride = _ride_rids(conn)
    try:
        weekly_rids = {r[0] for r in conn.execute(
            "SELECT recurrence_id FROM routine_def WHERE purpose='retro:weekly'")}
    except sqlite3.OperationalError:
        weekly_rids = set()  # pre-routine schema
    out = []
    for (eid, ulid, kind, title, sa, tz, loc, purpose, owner, np, nl,
         bclass, dfrom, drole, ea, rid) in rows:
        if not scope.accepts(owner, dfrom):
            continue
        new_placement = False
        owner_exception = False
        if rid in ride:
            defn = kit_life.routine_def_get(conn, rid)
            rr = kit_life._roller_mod()
            new_placement = rr.rp.is_alternatives(defn)
            owner_exception = conn.execute('SELECT rec_exception FROM timeblock WHERE id=?', (eid,)).fetchone()[0]
            if new_placement and not owner_exception:
                rd = _local(sa, tz or 'Asia/Seoul').date().isoformat()
                if rr.stored_placement(conn, defn, rd, sa, ea) is None:
                    continue
        start_dt = utc_datetime(sa, _FMT)
        start_ts = int(start_dt.timestamp())
        local = local_datetime(start_dt, ZoneInfo(tz or "Asia/Seoul"))
        if rid in weekly_rids and owner:
            end = kit_life.weekly_retro_week_end(local.date().isoformat())
            if kit_life.weekly_retro_state(conn, end, owner).get("state") in ("done", "started"):
                continue
        persons = ""
        policy = policies.get(kind)
        if policy is None:
            continue
        # A ride seated in a live leg is announced by that leg's departure
        # alert (one ping a day); only an unattached ride (fallback / window
        # seat, or a leg nothing alerts for) keeps its own start alert.
        ride_leg = (rr.occurrence_leg(
                        conn, rid,
                        _local(sa, tz or 'Asia/Seoul').date().isoformat(),
                        sa, ea) if rid in ride else None)
        # An owner move (including start-card snooze) detaches this occurrence
        # from placement. It must alert at the chosen time in its own card.
        ride_attached = (not owner_exception and ride_leg is not None
                         and _ride_carrier(conn, ride_leg) is not None)
        due = [(alert, lead_min) for alert, lead_min
               in ([] if ride_attached
                   else [("start", 0)] if rid in ride and not new_placement
                   and np != "silent"
                   else policy_alerts(kind, np, nl, lead_for(kind)))
               if now_ts - SLACK_SEC <= start_ts - lead_min * 60
               <= now_ts + SLACK_SEC]
        if not due:
            continue    # nothing to say: skip the body work (route, links)
        if policy["persons_join"]:
            persons = _get_participants(conn, eid)
        # A derived travel/prep helper keeps its kind (task: lead, dedup key)
        # but alerts under its own header stem, and its body names the anchor.
        stem, header_key = kind, policy["reminder_header_key"]
        body_title, body_local, body_loc = title, local, loc
        body_purpose = purpose
        helper = kit_life.travel_remind_view(conn, {
            "block_class": bclass, "derived_role": drole,
            "derived_from": dfrom, "title": title, "start_at": sa,
            "end_at": ea, "display_tz": tz, "location": loc})
        if helper is not None:
            stem = helper["stem"]
            header_key = "remind." + stem
            body_title, body_loc = helper["title"], helper["location"]
            body_local = _local(helper["start_at"], helper["display_tz"])
            body_purpose = ""
            helper["leg_start"] = sa
        for alert, lead_min in due:
            alert_ts = start_ts - lead_min * 60
            if policy["reminder_key_style"] == "appt":
                # live marker shape: appt[<_start>]:<id>:<local_iso>
                prefix = "appt" if alert == "lead" else "appt_start"
                key = "%s:%s:%s" % (prefix, eid, local.isoformat())
            else:
                prefix = "task" if alert == "lead" else "task_start"
                key = "%s:%s:%s" % (prefix, ulid, sa)
            if rid in ride and not owner_exception:
                # A re-seated instance must not ping again. The normal sent
                # marker pipeline owns delivery; the key owns the instance.
                # Owner exceptions use the scheduled-start key above: a
                # snooze promises another alert even with the same ULID.
                key = ("task_start:%s:ride" % ulid if alert == 'start'
                       else "task:%s:placement" % ulid)
            hhmm = body_local.strftime("%H:%M")
            if helper is not None:
                extra_lines = []
                if alert == "start":
                    extra_lines = _ride_lines(
                        conn, ride, ulid,
                        helper["depart_at"] if drole == "before_prep"
                        else sa, scope)
                lines = _helper_lines(helper, alert_ts, body_title, hhmm,
                                      extra_lines)
            elif rid in ride and alert == "start":
                lines = [_ride_start_line(conn, title, sa, ea, rid, _local(sa, tz or "Asia/Seoul").date().isoformat())]
                body_title, body_loc, body_purpose = lines[0], "", ""
                hhmm = ""  # legacy TSV also carries the same short line
            else:
                if agent_conf.conf_str("AGENT_LANG", "en") == "ko":
                    head = body_title + (": " + hhmm if hhmm else "")
                    lines = [" / ".join(
                        p for p in (head, body_loc, body_purpose, persons) if p)]
                else:
                    lines = [LINE_JOIN.join(
                        p for p in (body_title, hhmm, body_loc, body_purpose,
                                    persons) if p)]
            display_alert, header_time = _calendar_alert(
                alert, local, now_ts, lead_min)
            if display_alert == "eve" and retro_covers_eve(now_ts, tz):
                continue    # tonight's retro lists it; never a lone 'eve' ahead
            message = None
            if helper is None and kind == "appointment" and alert == "start":
                import appointment_reminder
                try:
                    message = appointment_reminder.render_start(
                        conn, eid, tz, kit_life._i18n)
                except sqlite3.Error:
                    # Keep legacy alerts on pre-migration read-only databases.
                    message = None
                if message:
                    # Direct delivery owns its header. The shell fallback
                    # supplies the same localized header above these body lines.
                    lines = message.splitlines()[1:]
            if (helper is None and kind == "appointment" and alert == "lead"
                    and lead_min == 120 and display_alert == "lead"):
                import appointment_reminder
                try:
                    message = appointment_reminder.render(
                        conn, eid, now_ts, tz, db_path, kit_life._i18n)
                except sqlite3.Error:
                    # Pre-migration read-only selectors retain their old alert.
                    message = None
                if message:
                    lines = message.splitlines()
            if (message is None and helper is None and kind == "appointment"
                    and alert == "lead"):
                lines += _appointment_brief_lines(conn, eid, now_ts, tz, db_path)
            out.append({
                "key": key, "kind": stem, "alert": display_alert,
                "message": message,
                "header_time": header_time,
                "lead_min": lead_min, "hhmm": hhmm,
                "title": body_title, "location": body_loc or "",
                "purpose": body_purpose or "",
                "persons": persons,
                "header_key": header_key,
                "header": kit_life._i18n(header_key, fallback=stem),
                "helper": helper is not None,
                "lines": lines,
                # map buttons minus what the text already links (the
                # directions line, the place pages) -- kit_life.
                # _travel_route_links
                "links": (helper.get("buttons", helper["links"])
                          if helper is not None else (
                              kit_life._event_card_map_buttons(conn, [eid], destination_fallback=True)
                              if message and alert == "lead" and lead_min == 120 else [])),
                # the start card's binding (start_card.py): this occurrence
                "block_id": eid, "ulid": ulid, "start_at": sa, "end_at": ea,
                "recurrence_id": rid, "title_raw": title,
                "ride": rid in ride,
            })
    out.extend(_errand_alerts(conn, now_ts, lead_for("task"), has_notify, scope))
    import health_regimen
    out.extend(health_regimen.alerts(kit_life, conn, now_ts, scope))
    import meal_time
    out.extend(meal_time.alerts(kit_life, conn, now_ts, scope))
    return out


def _errand_alerts(conn, now_ts, lead, has_notify, scope):
    """Errands (a no-time task at a place, errand.py) alert at their LATEST
    DEPARTURE -- deadline - margin - travel -- with the task's own notify
    policy: a lead alert and an on-time one. The selection above reads timed
    rows only; an errand is all-day, so it has its own pass. No departure
    (closing time or travel unknown) -> no alert, never a guessed one."""
    try:
        em = kit_life._errand_mod()
        ids = em.due_rows(kit_life, conn, now_ts)
    except Exception:
        return []
    out = []
    now_iso = _utc_str(now_ts)
    for eid in ids:
        np = nl = owner = None
        try:
            r = conn.execute(
                "SELECT %s, owning_agent FROM timeblock WHERE id=?;"
                % ("notify_policy, notify_lead_min" if has_notify
                   else "NULL, NULL"), (eid,)).fetchone()
            np, nl, owner = r
        except (sqlite3.Error, TypeError):
            pass
        if not scope.accepts(owner):
            continue
        try:
            p = em.plan(kit_life, conn, eid, now=now_iso)
        except Exception:
            p = None
        if p is None or not p.get("depart_at"):
            continue
        depart_ts = int(_local(p["depart_at"], "UTC").timestamp())
        for alert, lead_min in policy_alerts("task", np, nl, lead):
            alert_ts = depart_ts - lead_min * 60
            if not (now_ts - SLACK_SEC <= alert_ts <= now_ts + SLACK_SEC):
                continue
            lines, links = em.alert_lines(kit_life, conn, p, alert_ts,
                                          _bullet, _dur)
            prefix = "errand" if alert == "lead" else "errand_start"
            display_alert, header_time = _calendar_alert(
                alert, _local(p["depart_at"], kit_life.TRAVEL_DISPLAY_TZ),
                now_ts, lead_min)
            out.append({
                "key": "%s:%s:%s" % (prefix, p["ulid"], p["depart_at"]),
                "kind": "errand", "alert": display_alert, "lead_min": lead_min,
                "header_time": header_time,
                "hhmm": p["depart_hm"], "title": p["title"],
                "location": p["place"], "purpose": "", "persons": "",
                "header_key": "remind.errand",
                "header": kit_life._i18n("remind.errand", fallback="errand"),
                "helper": True, "lines": lines, "links": links,
            })
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="DGN-273 remind selection")
    ap.add_argument("--db", default=None, help="kit_life.db path override")
    ap.add_argument("--now", type=int, default=None,
                    help="epoch seconds override (tests)")
    ap.add_argument("--body", action="store_true",
                    help="emit key kind alert header_time body (lines joined "
                         "by \\x1f) for a transport that renders lines")
    ap.add_argument("--owner", choices=("health",), default=None,
                    help="only health-domain alerts, on the resolved owning instance")
    ap.add_argument("--preview", action="store_true",
                    help="prepare occurrences and render only; never call any delivery carrier")
    ap.add_argument("--json", action="store_true", help="preview alert objects as JSON")
    args = ap.parse_args(argv)
    if args.json and not args.preview:
        ap.error("--json requires --preview")
    now_ts = args.now if args.now is not None else int(
        datetime.datetime.now(tz=timezone.utc).timestamp())
    db_path = args.db or kit_life.DB_PATH
    # Fail-open: if the DB does not exist yet, produce no alerts rather than
    # creating a phantom empty file (which would open with no rows and break
    # the version assert on subsequent update.sh runs).
    if not os.path.isfile(db_path):
        return 0
    # Open read-only via URI to ensure no phantom creation on a missing path
    # that slips through the check above (e.g. race). assert_version=False:
    # a pre-007 DB must keep alerting with the legacy defaults, not go dark.
    import ro_open  # noqa: E402
    conn = ro_open.open_ro(db_path)
    # busy_timeout is safe read-only; WAL and foreign_keys are write-level
    # pragmas but are silently ignored (or succeed) under mode=ro.
    conn.execute("PRAGMA busy_timeout = 5000;")
    import health_regimen
    # A read-only/older DB still serves all previously working alerts when
    # regimen materialization is unavailable. mode=rw never creates a DB.
    if health_regimen.ready(conn):
        try:
            with sqlite3.connect(Path(db_path).absolute().as_uri() + "?mode=rw",
                                 uri=True, timeout=5) as regimen_conn:
                scope = reminder_owner.NotificationScope(kit_life, regimen_conn, EXCLUDE_OWNERS, args.owner)
                if scope.accepts("health"):
                    regimen_conn.execute("BEGIN IMMEDIATE")
                    health_regimen.prepare(regimen_conn, now_ts, scope)
        except sqlite3.Error:
            pass
    try:
        if args.preview:
            selected = select_alerts(conn, now_ts, db_path, owner=args.owner)
            if args.json:
                print(json.dumps(selected, ensure_ascii=False))
            else:
                for alert in selected:
                    print("\n".join(alert["lines"]))
                    if alert.get("buttons"):
                        print(" ".join("[" + b["text"] + "]" for row in alert["buttons"] for b in row))
            return 0
        # Refresh an existing cooking card on the ordinary reminder heartbeat.
        # Older/minimal kit copies need no activity module or config table.
        try:
            import cooking_session
            if args.owner is None:
                cooking_session.tick(kit_life, conn, db_path, now_ts)
        except (ImportError, sqlite3.Error, ValueError, KeyError):
            pass
        # Per-stop reviews use this existing heartbeat after the last stop;
        # an enabled daily/weekly retro reserves courses that ended in time.
        try:
            import course_review
            course_review.tick(kit_life, conn, db_path, now_ts)
        except (ImportError, sqlite3.Error, ValueError, KeyError, OSError):
            pass
        cards = kit_life._start_card_mod()
        now_iso = _utc_str(now_ts)
        for a in select_alerts(conn, now_ts, db_path, owner=args.owner):
            if a.get('kind') == 'meal_satiety':
                import meal_time
                meal_time.deliver(kit_life, db_path, a, now_iso)
                continue
            if a.get("kind") == "regimen":
                # Never fall back to a shared/default chat with private names.
                try:
                    health_regimen.deliver(kit_life, db_path, a, now_iso)
                except (sqlite3.Error, ValueError, OSError):
                    pass
                continue
            # A lead tap owns the occurrence, even before a snooze choice.
            # Suppress before fallback: no card OR plain row at the old start.
            if cards.eligible(a) and cards.suppresses_start(
                    kit_life, conn, a, now_iso):
                continue
            # The kit posts lead/start cards; handled cards emit no plain row.
            if cards.eligible(a) and cards.deliver(kit_life, db_path, a,
                                                   now_iso, _dur):
                continue
            if cards.eligible(a) and cards.already_posted(kit_life, conn, a, now_iso):
                continue
            if a.get("message"):
                import appointment_reminder
                if appointment_reminder.deliver(kit_life, db_path, a, now_iso):
                    continue
            lines = [_cell(ln) for ln in a["lines"]]
            if args.body:
                sys.stdout.write("%s\t%s\t%s\t%s\t%s\t%s\n" % (
                    _cell(a["key"]), a["kind"], a["alert"], a["header_time"],
                    BODY_SEP.join(lines), _cell(_buttons_json(a["links"]))))
            elif (a["helper"] or len(lines) > 1) and a["title"]:
                # remind.sh renders title . hhmm . ... on one line, and its
                # tab IFS collapses empty columns (a blank hhmm would shift
                # the rest). A pre---body remind.sh cannot break lines, so
                # the bullets ride one line: every line but the last in the
                # title column, the last (bullet dropped -- remind.sh's own
                # " . " stands in for it) in the hhmm column.
                bullet = _bullet("")
                head = " ".join(lines[:-1])
                tail = lines[-1]
                if bullet and tail.startswith(bullet):
                    tail = tail[len(bullet):]
                if not head:
                    head, tail = tail, a["hhmm"]
                sys.stdout.write("%s\t%s\t%s\t%s\t%s\t%s\t\t\t\n" % (
                    _cell(a["key"]), a["kind"], a["alert"], a["header_time"],
                    tail, head))
            else:
                sys.stdout.write(
                    "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n"
                    % (_cell(a["key"]), a["kind"], a["alert"], a["header_time"],
                       _cell(a["hhmm"]), _cell(a["title"]), _cell(a["location"]),
                       _cell(a["purpose"]), _cell(a.get("persons", ""))))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
