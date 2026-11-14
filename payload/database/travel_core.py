"""Travel lifecycle service shared by every leg follower.

K is the existing kit facade: connections, clock, geometry and mutation ports.
It is passed explicitly so CLI (__main__), SDK imports and sandbox clocks use
the same instance. This module never opens a live database on import.
"""
import datetime
import json
import sqlite3
from datetime import timezone
from zoneinfo import ZoneInfo

TRAVEL_BLOCK_CLASS = "travel"
TRAVEL_ROLE_BUFFER = "buffer"
TRAVEL_ROLES = ("before_prep", "before", TRAVEL_ROLE_BUFFER, "after")
TRAVEL_PIN_BY_OWNER = "travel-move"
TRAVEL_DERIVER_BY = "travel-deriver"
TRAVEL_REGEN_BY = "travel-regen"
TRAVEL_SKIP_BY = "travel-skip"
TRAVEL_EXPIRE_BY = "travel-expire"   # legacy system retirement; permits replacement
TRAVEL_SKIP_CLEARED_BY = "travel-skip-cleared"   # dec-021: superseded skip
TRAVEL_ANCHOR_CANCELLED_BY = "travel-anchor-cancelled"
_TRAVEL_CLOCK_STATUSES = ("open", "expired")

MOVE_ROLES = ("before", "after")
SYSTEM_RETIRED_BY = (TRAVEL_REGEN_BY, TRAVEL_EXPIRE_BY,
                     TRAVEL_SKIP_CLEARED_BY, TRAVEL_ANCHOR_CANCELLED_BY)
OWNER_SKIP_BY = (TRAVEL_SKIP_BY, "gcal-owner-delete")
CLOCK_BY = ("roller-lapse",)

def is_live_leg(row, move_only=False):
    """An unsettled derived leg; the clock alone does not settle a row."""
    row = dict(row)
    return (row.get('block_class') == TRAVEL_BLOCK_CLASS
            and row.get('derived_role') in (MOVE_ROLES if move_only else TRAVEL_ROLES)
            and row.get('settled_at') is None)


def settlement_kind(row):
    """Classify a settlement, preserving unknown owner/agent judgments.

    An unsettled expired row is still live. A system retirement permits a
    replacement; a completed/skipped/clock-settled seat remains history.
    The outcome, not an agent name, distinguishes owner done from skipped.
    """
    row = dict(row)
    if row.get('settled_at') is None:
        return None
    by = row.get('settled_by')
    if by in SYSTEM_RETIRED_BY:
        return 'system_retired'
    if by in CLOCK_BY:
        return 'clock'
    if row.get('settled_outcome') == 'done':
        return 'owner_done'
    return 'owner_skipped'


def is_retired_by_system(row):
    return settlement_kind(row) == 'system_retired'


def seat_is_history(row):
    return settlement_kind(row) in ('owner_done', 'owner_skipped', 'clock')


def history_exists(conn, *, anchor_ulid=None, role=None, start_at=None):
    """Shared SQL equivalent of seat_is_history for indexed follower reads.

    Anchor/role scopes protect skipped derivations and home/prep geometry;
    start_at scopes protect a ride's old move seat. Bind settlers from the
    same vocabulary as the row predicate (never a hand-kept SQL tuple).
    """
    sql = ('SELECT 1 FROM timeblock WHERE block_class=? '
           'AND settled_at IS NOT NULL AND COALESCE(settled_by,\'\') NOT IN ('
           + ','.join('?' for _ in SYSTEM_RETIRED_BY) + ')')
    params = [TRAVEL_BLOCK_CLASS, *SYSTEM_RETIRED_BY]
    if anchor_ulid is not None:
        sql += ' AND derived_from=?'
        params.append(anchor_ulid)
    if role is not None:
        sql += ' AND derived_role=?'
        params.append(role)
    if start_at is not None:
        sql += ' AND start_at=? AND derived_role IN (' + ','.join('?' for _ in MOVE_ROLES) + ')'
        params.extend((start_at, *MOVE_ROLES))
    return conn.execute(sql + ' LIMIT 1', params).fetchone() is not None


def live_move_at(K, conn, start_at):
    """The first live move leg occupying a ride seat, with alert fields."""
    if not start_at or not K._travel_schema_ready(conn):
        return None
    row = K._travel_row(conn, 'SELECT * FROM timeblock WHERE block_class=? '
                        'AND start_at=? AND settled_at IS NULL '
                        'AND end_at IS NOT NULL AND derived_role IN (?, ?) '
                        'ORDER BY id LIMIT 1',
                        (TRAVEL_BLOCK_CLASS, start_at, *MOVE_ROLES))
    if row is None or not is_live_leg(row, move_only=True):
        return None
    return {key: row[key] for key in ('ulid', 'title', 'start_at', 'end_at',
                                     'derived_from', 'derived_role', 'notify_policy')}


# Synchronous events, not persisted messages or a second state machine.
# Each entry names the existing convergence operation. Alerts read its live
# rows, mirror consumes its tombstones, and rides re-seat after convergence.
# Explicit declarations alone open past geometry; time-passed never does.
EVENT_HANDLERS = {
    'anchor_added': 'travel_refresh',
    'anchor_moved': 'travel_refresh',
    'anchor_cancelled': '_travel_on_anchor_settled',
    'anchor_done': '_travel_on_anchor_settled',
    'leg_regen': 'travel_refresh',
    'leg_expire': 'travel_conformance_pass',
    'leg_skip': 'travel_skip',
    'buffer_rule_changed': 'refresh_buffers',
    'time_passed': 'travel_conformance_pass',
}
FOLLOWER_EVENTS = tuple(EVENT_HANDLERS)


def react(K, conn, event, **kwargs):
    """Run the shared operation for a follower event using existing ports."""
    handler = EVENT_HANDLERS[event]  # unknown events fail at the caller
    if event in ('anchor_cancelled', 'anchor_done'):
        kwargs['outcome'] = 'abandoned' if event == 'anchor_cancelled' else 'done'
    return globals()[handler](K, conn, **kwargs)


def travel_remind_view(K, conn, row):
    """Reminder face of a derived travel/prep block, or None for any other
    row. {stem, role, title, start_at, display_tz, location, travel_min,
    anchor_at, depart_at, depart_min, arrive_label, arrive_at, route_text,
    links}; start_at is the instant the body shows (the anchor's, for
    anchor-body roles). anchor_at is the anchor's start when the body names
    it (else None). A prep block also carries its outing's move leg
    (depart_at = the leg's start, depart_min = its length), so the prep
    alert names the next step; no live move leg -> both None, never
    invented.

    Owner 2026-10-06 07:22-07:28: every alert names the NEXT step and where
    it goes. arrive_label / arrive_at = what a move leg arrives at (the
    anchor and its start; the return leg: home and the leg's end).
    route_text = "origin -> destination" for the leg the step belongs to
    (_travel_leg_places); links = map buttons [{text, url}] that open
    directions with both ends filled (travel_map_links) -- [] when none can."""
    if row.get("block_class") != TRAVEL_BLOCK_CLASS:
        return None
    role = row.get("derived_role")
    spec = K.TRAVEL_ROLE_REMIND.get(role)
    if spec is None:
        return None
    stem, anchor_body, with_min = spec
    view = {"stem": stem, "role": role, "title": row["title"],
            "start_at": row["start_at"],
            "display_tz": row.get("display_tz"),
            "location": row.get("location") or "", "travel_min": None,
            "anchor_at": None, "depart_at": None, "depart_min": None,
            "arrive_label": None, "arrive_at": None, "route_text": "",
            "links": []}
    if with_min and row.get("start_at") and row.get("end_at"):
        view["travel_min"] = K._iso_diff_min(row["end_at"], row["start_at"])
    anchor = None
    if row.get("derived_from"):
        anchor = K._travel_row(conn, "SELECT * FROM timeblock WHERE ulid=?;",
                             (row["derived_from"],))
    if anchor_body and anchor is not None and anchor.get("start_at"):
        view.update(title=anchor["title"], start_at=anchor["start_at"],
                    display_tz=anchor.get("display_tz") or view["display_tz"],
                    location=anchor.get("location") or "",
                    anchor_at=anchor["start_at"],
                    arrive_label=anchor["title"],
                    arrive_at=anchor["start_at"])
    if role == "before_prep" and row.get("derived_from"):
        leg = conn.execute(
            "SELECT start_at, end_at FROM timeblock WHERE block_class=? "
            "AND derived_from=? AND derived_role='before' "
            "AND settled_at IS NULL AND start_at IS NOT NULL "
            "AND end_at IS NOT NULL ORDER BY start_at LIMIT 1;",
            (TRAVEL_BLOCK_CLASS, row["derived_from"])).fetchone()
        if leg is not None:
            view.update(depart_at=leg[0],
                        depart_min=K._iso_diff_min(leg[1], leg[0]))
    try:
        origin_id, dest_id = K._travel_leg_places(conn, role, anchor)
    except Exception:
        origin_id = dest_id = None      # a reminder never dies on a route
    if role == "after" and dest_id is not None and row.get("end_at"):
        dest = K.travel_place_get(conn, dest_id)
        if dest is not None:
            view.update(arrive_label=dest["name"], arrive_at=row["end_at"])
    if stem == "prep" and view["depart_at"] is None:
        return view       # no move step ahead (no leg, or the buffer after it)
    if dest_id is not None and origin_id != dest_id:
        base_id = K._travel_base_place_id(conn)
        dest_txt = K._travel_place_alert_text(conn, dest_id, base_id)
        origin_txt = (K._travel_place_alert_text(conn, origin_id, base_id)
                      if origin_id is not None else "")
        view["route_text"] = (u"%s \u2192 %s" % (origin_txt, dest_txt)
                              if origin_txt else dest_txt)
        view["links"] = K.travel_map_links(conn, origin_id, dest_id)
        view.update(K._travel_route_links(conn, origin_id, dest_id,
                                        view["links"], prep=stem == "prep"))
    return view


def _travel_home_return_verdict(K, conn, ev, departure):
    """Does the owner get home from stop `ev` by `departure`?
    True = provably home, False = provably not (still out and continuing),
    None = cannot tell. Callers read None as "home" for prep (owner
    2026-10-15: missing/unknown data => prep ON).

    Judged from ev's RULE AND GEOMETRY, not from whether its 'after' block has
    been written yet (ticket 2026-09-23 roll-order): the same inputs give the
    same answer whichever anchor is reconciled first.

    Order of evidence:
      1. a live PINNED 'after' leg: the owner placed the return; its end
         decides (pins are never re-derived).
      2. an owner-settled 'after' leg (travel-skip & co.) and no live one:
         the owner said there is no trip home -> False.
      3. ev holds an active rule and is eligible: the return leg
         travel_derive expects (_travel_expected_after) decides, if it lies
         in the future. A past expected leg is never materialized (C5), so
         only a live row can speak for it (4).
      4. any live 'after' leg: ending by `departure` -> True, else False.
      5. geometry: the stop's place -> home duration (stored edge, either
         direction of the pair). ret <= gap -> True; ret > gap -> False.
         A gap shorter than one grid unit fits no real trip home -> False.
         Nothing known -> None.
    Best-effort: an exception returns None (prep stays)."""
    try:
        legs = K._travel_rows(
            conn,
            "SELECT id, end_at, derived_pinned FROM timeblock "
            "WHERE settled_at IS NULL "
            "AND block_class IS 'travel' AND derived_from IS ? "
            "AND derived_role = 'after' AND end_at IS NOT NULL;",
            (ev["ulid"],))
        pinned = [r for r in legs if r["derived_pinned"]]
        if pinned:
            return any(r["end_at"] <= departure for r in pinned)
        skipped = history_exists(conn, anchor_ulid=ev["ulid"], role="after")
        if skipped and not legs:
            return False
        anchor = K._travel_row(conn, "SELECT * FROM timeblock WHERE ulid=?;",
                             (ev["ulid"],))
        rule = (K._travel_rule_for_anchor_row(conn, anchor)
                if anchor is not None else None)
        if rule is not None and K._travel_anchor_eligible(anchor):
            after = K._travel_expected_after(conn, rule, anchor)
            if after is not None and after[0] > K.now_utc():
                return after[1] <= departure
        if legs:
            return any(r["end_at"] <= departure for r in legs)
        end_at = ev.get("end_at") or (anchor or {}).get("end_at")
        if end_at is None:
            return None
        gap = K._iso_diff_min(departure, end_at)
        pid = K._travel_external_place_id(conn, ev)
        base_id = K._travel_base_place_id(conn)
        if pid is not None and pid == base_id:
            return True
        ret = None
        if (pid is not None and base_id is not None
                and K._travel_edge_schema_ready(conn)):
            e = (K._travel_edge_get(conn, pid, base_id)
                 or K._travel_edge_get(conn, base_id, pid))
            ret = int(e["minutes"]) if e is not None else None
        if ret is not None:
            return ret <= gap
        if gap < K.TRAVEL_GRID_MIN:
            return False
        return None
    except Exception:  # noqa: BLE001 -- never break derivation on a probe
        return None


def _travel_never_mirrored(K, conn, state_conn, row):
    """0.1 pin: reach bookkeeping truth = push_snapshot; reach predicate =
    bookkeeping cols NULL AND no snapshot AND no pending outbox; state db
    unavailable -> False (tombstone path, never DELETE). A state db that
    cannot prove reach (no mirror targets engraved: missing, empty, or a
    non-owner instance's local copy) -> False too (2026-09-27 ghosts, see
    _state_db_proves_reach)."""
    if row.get("gcal_event_id") is not None or row.get("gtask_id") is not None:
        return False
    if not K._state_db_proves_reach(state_conn):
        return False
    try:
        snap = state_conn.execute(
            "SELECT 1 FROM push_snapshot WHERE event_ulid=? LIMIT 1;",
            (row["ulid"],)).fetchone()
        if snap:
            return False
        pending = state_conn.execute(
            "SELECT 1 FROM mirror_outbox WHERE event_ulid=? "
            "AND status IN ('queued','claimed') LIMIT 1;",
            (row["ulid"],)).fetchone()
        return pending is None
    except sqlite3.Error:
        return False


def _travel_tombstone_block(K, conn, state_conn, row, by=TRAVEL_REGEN_BY):
    """Regen-branch tombstone (3.1): PROVEN never-mirrored -> hard DELETE,
    else cancel(abandoned, by) + outbox tombstone. Returns
    'deleted'|'cancelled'|'skipped'.

    The cancel reaches the calendar whichever instance runs this: the
    abandoned row stays in the shared crew DB, and the mirror owner's poll
    sweep (adapter.sweep_step DGN-302 scan: abandoned + live snapshot ->
    enqueue) pushes it from ITS outbox. The local enqueue below is the fast
    path when this instance is the owner or has MIRROR_ENQUEUE_TO."""
    if K._travel_never_mirrored(conn, state_conn, row):
        conn.execute("BEGIN IMMEDIATE;")
        try:
            # v3.2 \uc758 timeblock \uc740 \ubdf0\ub2e4 -- \uc0ad\uc81c\ub3c4 \ub77c\uc6b0\ud130 \uc2dc\uc784\uc73c\ub85c \uac04\ub2e4(F2).
            n_del = K._tb_delete(conn, row["id"],
                               where_extra=" AND settled_at IS NULL",
                               cas_version=row["version"])
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        if n_del == 1:
            if state_conn is not None:
                state_conn.execute(
                    "DELETE FROM mirror_outbox WHERE event_ulid=? "
                    "AND status IN ('queued','claimed');", (row["ulid"],))
                state_conn.commit()
            return "deleted"
        return "skipped"       # CAS moved under us -> converge next cycle
    res = K.cancel(conn, row["id"], row["version"], by)
    if res == K.MutationResult.APPLIED:
        K._travel_outbox(state_conn, row["ulid"])
        return "cancelled"
    return "skipped"


def travel_refresh(K, conn, state_conn, anchor_ulid, carry=None, now=None,
                   _chain=True, declared=False):
    """Reconcile the blocks of ONE anchor instance against derive()
    (3.1 algorithm, deterministic, zero discretion). Returns a summary
    {result, regen, created, tombstoned, moved}.

    Lifelog guard: rows whose start_at <= now are never touched (started /
    past blocks are lifelog; C5 pins future-only creation -- include_day
    inheritance), and no expected block materializes at a past instant.

    declared=True is the OWNER's own add/edit of an appointment on this
    anchor's local day (travel_rechain_declared, owner 2026-10-05 21:16).
    The guard instant then drops to that day's local midnight, so the day's
    past legs re-chain like future ones -- with three protections kept: a
    pinned row and a row carrying recorded actuals (status beyond the
    clock states, a note or a retro line) are never moved or retired, settled
    rows are never read at all, and a leg minted at a past instant is
    'silent' (no push for a moment that is gone). An ineligible anchor
    (settled, untimed) keeps the plain guard. Background sweeps never pass
    declared, so they stay future-only."""
    now = now or K.now_utc()
    summary = {"result": "ok", "regen": 0, "created": 0,
               "tombstoned": 0, "moved": 0, "ride": 0}
    if not K._travel_schema_ready(conn):
        summary["result"] = "schema_not_ready"   # M2: v7 no-op
        return summary
    owns_state = state_conn is None
    state_conn = K._travel_state_conn(state_conn)
    try:
        return _travel_refresh_on_state(K, conn, state_conn, anchor_ulid, carry,
                                        now, _chain, declared, summary)
    finally:
        if owns_state and state_conn is not None:
            state_conn.close()


def _travel_refresh_on_state(K, conn, state_conn, anchor_ulid, carry, now,
                             _chain, declared, summary):
    anchor = K._travel_row(conn, "SELECT * FROM timeblock WHERE ulid=?;",
                         (anchor_ulid,))
    if anchor is None:
        summary["result"] = "anchor_not_found"
        return summary
    attendance_repair(K, conn, anchor, state_conn, now)
    anchor = K.appt_snapshot(conn, anchor_ulid) or anchor
    rule = K._travel_rule_for_anchor_row(conn, anchor)
    if rule and rule.get('before_src') == K.TRAVEL_SRC_PROVISIONAL and rule.get('place_id'):
        probe = dict(rule, before_src='place', before_min=0)
        minutes, _prep, status, _origin = K._travel_resolve_before_leg(conn, probe, anchor)
        if status != K.TRAVEL_EDGE_MISSING:
            K._travel_provisional_upgrade(conn, rule, rule['place_id'], minutes, now=now)
            rule = K._travel_rule_for_anchor_row(conn, anchor)
    eligible = rule is not None and K._travel_anchor_eligible(anchor)
    expected = K.travel_derive(conn, rule, anchor, now=now) if eligible else {}
    carry = carry or {}
    guard = now
    if declared and eligible:
        guard = min(now, K._travel_local_day_window(anchor["start_at"])[0])

    # DGN-305: 'before_prep' is the prep block, emitted only when prep_eff>0;
    # 'before' is the travel-only leg. D2: 'buffer' is the named arrival
    # margin, emitted only when a move leg exists on an appointment anchor.
    for role in TRAVEL_ROLES:
        exp = expected.get(role)
        if role == "before_prep" and not K._travel_prep_enabled(conn):
            # Disabling derivation is not an order to delete existing prep --
            # but it must not strand it either (owner 2026-10-27: the prep
            # stayed at the old hour when its anchor moved). A live unpinned
            # future prep keeps its length and follows the outbound leg; it
            # goes only when that leg is gone. Nothing is ever minted.
            kept = K._travel_rows(
                conn, "SELECT * FROM timeblock WHERE block_class='travel' "
                "AND derived_from=? AND derived_role='before_prep' "
                "AND settled_at IS NULL AND derived_pinned=0 "
                "AND start_at > ?;", (anchor_ulid, guard))
            if not kept:
                continue
            leg = expected.get("before")
            exp = None
            if leg is not None and kept[0]["end_at"] and kept[0]["start_at"]:
                span = K._iso_diff_min(kept[0]["end_at"], kept[0]["start_at"])
                exp = {"start_at": K._iso_shift_min(leg["start_at"], -span),
                       "end_at": leg["start_at"],
                       "exclusive": int(kept[0]["slot_exclusive"]),
                       "notify_policy": kept[0].get("notify_policy")
                       or "start_only"}
        # Background sweeps never mint past legs. An explicit anchor edit
        # opens its day via declared=True and retains elapsed legs silently.
        if exp is not None and exp["start_at"] <= guard:
            exp = None
        if exp is not None and exp["start_at"] <= now:
            # declared retro leg: the moment is gone, so it never pings.
            exp = dict(exp, notify_policy="silent")
        all_live = K._travel_rows(
            conn, "SELECT * FROM timeblock WHERE block_class='travel' "
            "AND derived_from=? AND derived_role=? AND settled_at IS NULL;",
            (anchor_ulid, role))
        # started/past rows: lifelog -- never tombstoned/regen-ed. The
        # unique live index means a past live row and a future live row
        # cannot coexist, so `live` and `past_live` are exclusive (F1).
        live = [r for r in all_live if r["start_at"] is not None
                and r["start_at"] > guard]
        past_live = [r for r in all_live if r["start_at"] is not None
                     and r["start_at"] <= guard]
        suppressed = history_exists(conn, anchor_ulid=anchor_ulid, role=role)

        live.sort(key=lambda r: (not r["derived_pinned"], r["id"]))
        if len(live) > 1:
            # Preserve the first identity; tombstone duplicate derivations
            # through the same calendar-aware verb as ordinary regeneration.
            for duplicate in live[1:]:
                if duplicate["derived_pinned"] or K._travel_row_has_actuals(duplicate):
                    continue
                if K._travel_tombstone_block(conn, state_conn, duplicate) != "skipped":
                    summary["tombstoned"] += 1
        if live:
            row = live[0]         # unique live index guarantees <= 1
            retro = row["start_at"] <= now     # only under declared=True
            if K._travel_row_has_actuals(row) or (retro and row["derived_pinned"]):
                continue          # the owner's own record of that move
            if row["derived_pinned"]:
                if row["derived_delta"] is None:
                    continue      # frozen transition -- delta engrave owns it
                if not eligible:
                    if K._travel_tombstone_block(conn, state_conn, row) != "skipped":
                        summary["tombstoned"] += 1
                    continue
                try:
                    delta = json.loads(row["derived_delta"])
                    off = int(delta["offset_min"])
                    dur = int(delta["duration_min"])
                except (ValueError, KeyError, TypeError):
                    continue      # unreadable delta: skip, never guess
                want_s = K._iso_shift_min(anchor["start_at"], off)
                want_e = K._iso_shift_min(want_s, dur)
                if (row["start_at"], row["end_at"]) != (want_s, want_e):
                    # in-place CAS move (bypass semantics, no slot predicate;
                    # CAS fail -> skip, next cycle converges). Preserves the
                    # row = gcal event identity (4.1).
                    conn.execute("BEGIN IMMEDIATE;")
                    try:
                        n_pin_move = K._tb_update(
                            conn, row["id"],
                            {'start_at': want_s, 'end_at': want_e,
                             'updated_at': now},
                            where_extra=" AND settled_at IS NULL",
                            bump_version=True,
                            cas_version=row["version"])
                        if n_pin_move == 1:
                            K._recompute_and_store(conn, row["id"], now=now)
                        conn.commit()
                    except Exception:
                        conn.rollback()
                        raise
                    if n_pin_move == 1:
                        summary["moved"] += 1
                        K._travel_outbox(state_conn, row["ulid"])
                continue
            # unpinned live row
            if exp is None:
                if K._travel_tombstone_block(conn, state_conn, row) != "skipped":
                    summary["tombstoned"] += 1
                    summary["regen"] += 1
                continue
            # DGN-305: conformance check includes notify_policy so a row
            # minted under an older per-role policy is regen-ed into the
            # current one (2026-10-06: the travel-only 'before' went from
            # 'silent' back to 'start_only').
            # DGN-314: include title in the conformance tuple so an after block
            # carrying the old anchor-based title ("\uc774\ub3d9: \ud68c\uc0ac") regens to the
            # new fixed home title ("\uc774\ub3d9: \uc9d1").
            row_np = row.get("notify_policy") or "start_only"
            exp_np = exp.get("notify_policy", "start_only")
            preserve_silence = (role == "before" and row_np == "silent"
                                and not K._travel_notify_policy_untouched(row))
            if preserve_silence:
                # An owner metadata write bumps version. Unknown edits are
                # equally unsafe to reinterpret as old deriver silence.
                # Keep the override even if GEOMETRY requires a replacement.
                exp_np = row_np
                exp = dict(exp, notify_policy=row_np)
            if retro:
                # a started leg's alert already fired or never will; its
                # policy is not a reason to rewrite the row.
                exp_np = row_np
            row_title = row.get("title") or ""
            # compute expected title for this role to compare
            exp_title = K._travel_role_title(anchor, role)
            conforms = ((row["start_at"], row["end_at"],
                         int(row["slot_exclusive"]), row_np, row_title)
                        == (exp["start_at"], exp["end_at"],
                            int(exp["exclusive"]), exp_np, exp_title))
            if conforms:
                continue
            # regen: tombstone + recreate at expected.
            if K._travel_tombstone_block(conn, state_conn, row) == "skipped":
                continue
            summary["regen"] += 1
            eid = K._travel_create_block(
                conn, state_conn, anchor, role,
                exp["start_at"], exp["end_at"], exp["exclusive"], now=now,
                notify_policy=exp.get("notify_policy", "start_only"),
                notify_policy_preserved=preserve_silence)
            if eid is not None:
                summary["created"] += 1
            continue

        # An elapsed window alone must never abandon a leg. Anchor edits
        # reconcile past geometry through the declared-day path; an ordinary
        # sweep keeps the record until then (or the anchor is cancelled).
        if exp is None or suppressed:
            continue              # settled row suppresses recreate (skip
                                  # semantics; travel-retired legs use the
                                  # carve-outs above, including anchor cancel)
        if past_live:
            # Replace stale planned geometry only when a replacement exists.
            # Owner records and pins are protected even in a background pass.
            stale = past_live[0]
            if stale["derived_pinned"] or K._travel_row_has_actuals(stale):
                continue
            if K._travel_tombstone_block(conn, state_conn, stale) == "skipped":
                continue
            summary["tombstoned"] += 1
            summary["regen"] += 1
        pin = carry.get(role)
        if pin:
            pinned_flag, delta_json = int(pin[0] or 0), pin[1]
            pin_exclusive = int(pin[2]) if len(pin) > 2 else 1
            if pinned_flag and delta_json:
                try:
                    delta = json.loads(delta_json)
                    ps = K._iso_shift_min(anchor["start_at"],
                                        int(delta["offset_min"]))
                    pe = K._iso_shift_min(ps, int(delta["duration_min"]))
                except (ValueError, KeyError, TypeError):
                    ps, pe, delta_json = exp["start_at"], exp["end_at"], None
                if ps > now:
                    eid = K._travel_create_block(
                        conn, state_conn, anchor, role, ps, pe,
                        pin_exclusive,      # M4: engrave-time value kept
                        pinned=1, delta_json=delta_json, now=now,
                        notify_policy=exp.get("notify_policy", "start_only"))
                    if eid is not None:
                        summary["created"] += 1
                continue
            if pinned_flag:
                # pinned but delta unknown (pre-clean hit a frozen row):
                # recreate at expected, keep the pin (M4: engrave-time
                # exclusive kept); lazy engrave computes the delta at
                # cycle end / nightly.
                eid = K._travel_create_block(
                    conn, state_conn, anchor, role,
                    exp["start_at"], exp["end_at"], pin_exclusive,
                    pinned=1, delta_json=None, now=now,
                    notify_policy=exp.get("notify_policy", "start_only"))
                if eid is not None:
                    summary["created"] += 1
                continue
        eid = K._travel_create_block(
            conn, state_conn, anchor, role,
            exp["start_at"], exp["end_at"], exp["exclusive"], now=now,
            notify_policy=exp.get("notify_policy", "start_only"))
        if eid is not None:
            summary["created"] += 1
    # v3.2: the return leg is derive-time for EVERY origin (no baked
    # after_min copy), so THIS anchor appearing/moving can flip a chain
    # PREDECESSOR's tail-ness -- e.g. its stale return leg must retire when
    # a back-to-back follow-up lands. One level only (a predecessor's own
    # tail-ness depends on ITS followers, which this refresh did not change),
    # guarded by _chain so the neighbor refresh cannot recurse.
    if _chain and K._is_v32(conn) and anchor.get("start_at") is not None:
        lo = K._iso_shift_min(anchor["start_at"], -K.TRAVEL_CHAIN_WINDOW_MIN)
        for prev in K._travel_rows(
                conn,
                "SELECT * FROM timeblock WHERE settled_at IS NULL "
                "AND end_at IS NOT NULL AND end_at >= ? AND end_at <= ? "
                "AND id != ? AND block_class IS NULL;",
                (lo, anchor["start_at"], anchor["id"])):
            if (K._travel_external(conn, prev)
                    and K._travel_rule_for_anchor_row(conn, prev) is not None):
                K._travel_summary_add(summary, K.travel_refresh(
                    conn, state_conn, prev["ulid"], now=now, _chain=False))
        # the same for a predecessor chained from past the window (the trip
        # home no longer fits now that this anchor follows it).
        far = K._travel_far_chain_neighbour(conn, anchor, "prev")
        if far is not None:
            prev = K._travel_row(conn, "SELECT * FROM timeblock WHERE id=?;",
                               (far["id"],))
            if (prev is not None
                    and K._travel_rule_for_anchor_row(conn, prev) is not None):
                K._travel_summary_add(summary, K.travel_refresh(
                    conn, state_conn, prev["ulid"], now=now, _chain=False))
    if _chain:
        # Rule 1: a leg landing next to a leg of its own bundle changes that
        # neighbour too -- the previous leg loses its return home, the next
        # one loses its before leg -- however far apart in time they are.
        for _dir in ("prev", "next"):
            sib = K._travel_bundle_neighbour(conn, anchor, _dir)
            if (sib is not None
                    and K._travel_rule_for_anchor_row(conn, sib) is not None):
                K._travel_summary_add(summary, K.travel_refresh(
                    conn, state_conn, sib["ulid"], now=now, _chain=False))
        # slice3: this anchor appearing/moving can take the day's single prep
        # block from a later anchor, or hand it back to one.
        K._travel_refresh_day_peers(conn, state_conn, anchor.get("start_at"),
                                  anchor["id"], now=now, agg=summary)
        # 070: a ride routine on this anchor's day follows its legs.
        if anchor.get("start_at"):
            summary["ride"] = len(K._ride_resolve_best_effort(
                conn, state_conn, now=now,
                dates={K._travel_local_date(anchor["start_at"],
                                          anchor.get("display_tz"))}))
    if eligible and expected.get("before"):
        import appointment_buffer
        appointment_buffer.applied(K, conn, anchor, now)
    return summary


def _travel_row_has_actuals(K, row):
    """True when a derived travel row carries something the owner recorded
    about the move itself -- a note or retro line was written on it, or its
    status moved to something the clock cannot produce. Such a row is
    lifelog even on a re-chained day.

    'expired' is NOT a record (live 2026-10-05, the stale 15:00 trip home
    kept on the life agent): v_block_state derives it for every unsettled row whose end
    is behind the wall clock, so reading it as "recorded" froze every past
    leg the moment its hour passed -- exactly the legs a retro re-chain
    exists to fix. A calendar mirror id is not a record either: a mirrored
    leg is still derived, and its retirement cancels the calendar copy
    through the outbox (_travel_tombstone_block). Settled and pinned rows are
    protected by their own branches."""
    if (row.get("status") or "open") not in _TRAVEL_CLOCK_STATUSES:
        return True
    return bool(row.get("note")) or bool(row.get("retro_line"))


def travel_rechain_declared(K, conn, state_conn, anchor_ulid, now=None,
                            instant=None):
    """The owner declared (added / edited) the appointment `anchor_ulid`:
    re-chain every rule-holding anchor of its LOCAL day, past legs included
    (owner 2026-10-05 21:16: an appointment logged at 21:10 for 16:30 got no
    legs, and the 15:00 leg before it still went home).

    Same derivation as future planning -- only the C5 guard is opened, and
    only for this day and only on this verb (travel_refresh declared=True).
    Two passes in start order: a leg's suppression / tail-ness reads its
    neighbours' rows, so the first pass can leave a role the second settles;
    on converged input the second pass writes nothing. A day wholly in the
    future returns at once -- the ordinary refresh already covered it.
    instant (optional) names the day instead of the anchor's start: the day
    an edited appointment LEFT, whose neighbours lost a stop.
    Returns the aggregate summary."""
    now = now or K.now_utc()
    agg = {"regen": 0, "created": 0, "tombstoned": 0, "moved": 0,
           "anchors": 0}
    if not K._travel_schema_ready(conn):
        return agg
    if instant is None:
        anchor = K._travel_row(conn, "SELECT * FROM timeblock WHERE ulid=?;",
                             (anchor_ulid,))
        if (anchor is None or anchor.get("start_at") is None
                or anchor.get("schedule_kind") != "timed"):
            return agg
        instant = anchor["start_at"]
    lo, hi = K._travel_local_day_window(instant)
    if lo > now:
        return agg
    state_conn = K._travel_state_conn(state_conn)
    for _pass in range(2):
        for peer in K._travel_rows(
                conn,
                "SELECT * FROM timeblock WHERE settled_at IS NULL "
                "AND block_class IS NULL AND schedule_kind='timed' "
                "AND start_at >= ? AND start_at < ? ORDER BY start_at, id;",
                (lo, hi)):
            if (K._travel_rule_for_anchor_row(conn, peer) is None
                    or not K._travel_anchor_eligible(peer)):
                continue
            s_ = K.travel_refresh(conn, state_conn, peer["ulid"], now=now,
                                _chain=False, declared=True)
            for k in ("regen", "created", "tombstoned", "moved"):
                agg[k] += s_.get(k, 0)
            if _pass == 0:
                agg["anchors"] += 1
    return agg


def travel_skip(K, conn, block_token, state_conn=None):
    """This-occurrence opt-out (3.3): cancel(abandoned, by='travel-skip') +
    outbox tombstone if reached. The settled row suppresses recreation at
    (anchor, role) -- skip semantics; distinct from the travel-regen
    carve-out."""
    row = K._travel_block_resolve(conn, block_token)
    if row is None:
        return {"result": "not_found"}
    if row.get("block_class") != TRAVEL_BLOCK_CLASS:
        raise ValueError("travel-skip: %r is not a travel block" % block_token)
    if row.get("settled_at") is not None:
        return {"result": "already_settled"}
    res = K.cancel(conn, row["id"], row["version"], TRAVEL_SKIP_BY)
    if res != K.MutationResult.APPLIED:
        return {"result": "cas_fail"}
    K._travel_outbox(K._travel_state_conn(state_conn), row["ulid"])
    return {"result": "applied"}


def _travel_clear_skips(K, conn, anchor_ulid, now=None):
    """dec-021 (OQ-8 owner pin, 2026-07-14): a skip is a judgment about
    THAT occurrence at THAT time -- any schedule change to the anchor
    instance supersedes it. Mechanism: bookkeeping rewrite of settled_by
    ('travel-skip' and its inbound twin 'gcal-owner-delete') to
    'travel-skip-cleared' -- the settled row and its surface tombstone
    stay untouched (no deletes, mirror discipline kept, audit line in
    roller_log carries the original settler); the suppression predicate
    carve-out ignores cleared rows so the next refresh re-derives at the
    new position. Deterministic code only. Returns cleared count."""
    now = now or K.now_utc()
    rows = K._travel_rows(
        conn, "SELECT id, ulid, version, settled_by, derived_role "
        "FROM timeblock WHERE block_class='travel' AND derived_from=? "
        "AND settled_at IS NOT NULL "
        "AND settled_by IN (" + ','.join('?' for _ in OWNER_SKIP_BY) + ');',
        (anchor_ulid, *OWNER_SKIP_BY))
    n = 0
    for r in rows:
        conn.execute("BEGIN IMMEDIATE;")
        try:
            changed = K._tb_update(conn, r["id"],
                       {'settled_by': TRAVEL_SKIP_CLEARED_BY, 'updated_at': now},
                       bump_version=True, cas_version=r["version"])
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        if changed == 1:
            n += 1
            K._travel_log(conn, "travel_skip_cleared", None,
                        "block=%s role=%s was=%s (anchor schedule change)"
                        % (r["ulid"], r["derived_role"], r["settled_by"]),
                        now=now)
    return n


def _travel_on_anchor_settled(K, conn, eid, settle_ts, outcome=None):
    """After an anchor settles: tombstone every unsettled derived block with
    start_at > settle instant (a travel not yet started will not happen).
    FUTURE blocks are cleaned for BOTH outcomes (done AND abandoned) -- the
    judgment key is block start vs settle instant. Started/past blocks are
    normally kept (lifelog: the anchor happened).

    G3 -- ANCHOR CANCELLED (outcome='abandoned'): the anchor did NOT happen, so
    its prep/travel 'never mattered'. Two extra cleanups, cancel-only:
      (1) settle any STARTED (start_at <= settle instant) unsettled derived
          block OFF-surface with settled_by='travel-anchor-cancelled' -- it
          drops from the calendar, and the distinct settler distinguishes it
          from a genuine past outing's lifelog.
      (2) RETIRE the anchor's own event-direct active travel_rule, so an
          orphan rule never outlives the cancelled anchor. Only the
          anchor_type='event' rule bound to THIS ulid is retired; a def-rule
          (governs every routine instance) is left untouched.
    A genuinely-completed (done) outing keeps its started/past blocks (no
    off-surface settle, rule untouched) -- lifelog preserved, no regression.
    After cancellation, re-seat that day's ride routines even when there is
    no follower to refresh; the conformance pass also repairs stale seats.
    No-op for block rows and rule-less rows."""
    if not K._travel_schema_ready(conn):
        return 0                       # M2: pre-008 DB -> no-op
    row = K._travel_row(conn, "SELECT * FROM timeblock WHERE id=?;", (eid,))
    if row is None or row.get("block_class") is not None:
        return 0
    state_conn = K._travel_state_conn(None)
    try:
        return _travel_anchor_settled_apply(K, conn, row, settle_ts, outcome, state_conn)
    finally:
        if state_conn is not None:
            state_conn.close()


def _travel_anchor_settled_apply(K, conn, row, settle_ts, outcome, state_conn):
    n = 0
    # future blocks: unchanged for both outcomes (a not-yet-started travel).
    for b in K._travel_rows(
            conn, "SELECT * FROM timeblock WHERE block_class='travel' "
            "AND derived_from=? AND settled_at IS NULL AND start_at > ?;",
            (row["ulid"], settle_ts)):
        if K._travel_tombstone_block(conn, state_conn, b) != "skipped":
            n += 1
    if outcome != "abandoned":
        return n                       # done: started/past = lifelog, keep
    # G3 (1): started-but-cancelled derived blocks -> off-surface, distinct by.
    for b in K._travel_rows(
            conn, "SELECT * FROM timeblock WHERE block_class='travel' "
            "AND derived_from=? AND settled_at IS NULL AND start_at <= ?;",
            (row["ulid"], settle_ts)):
        res = K.cancel(conn, b["id"], b["version"], TRAVEL_ANCHOR_CANCELLED_BY)
        if res == K.MutationResult.APPLIED:
            K._travel_outbox(state_conn, b["ulid"])   # drop it from the surface
            n += 1
    # G3 (2): retire the anchor's own event-direct active rule (orphan guard).
    orphan = K._travel_row(
        conn, "SELECT * FROM travel_rule WHERE anchor_type='event' "
        "AND anchor_ulid=? AND status='active';", (row["ulid"],))
    if orphan is not None:
        K.travel_rule_retire(conn, orphan["id"], state_conn=state_conn)
    # A cancelled stop leaves the chain: whoever departed FROM it now departs
    # from the stop before it (or home). Re-derive the stops that followed
    # it -- the next leg of its bundle and the chain stops starting inside
    # the window after it -- now, not at the nightly pass.
    # Best-effort: the cancel itself has already committed, and the nightly
    # conformance pass converges anything this misses.
    try:
        _followers = []
        _nxt = K._travel_bundle_neighbour(conn, row, "next")
        if _nxt is not None:
            _followers.append(_nxt)
        if row.get("end_at") is not None:
            _followers.extend(K._travel_rows(
                conn, "SELECT * FROM timeblock WHERE settled_at IS NULL "
                "AND block_class IS NULL AND start_at IS NOT NULL "
                "AND start_at >= ? AND start_at <= ? AND id != ?;",
                (row["end_at"], K._iso_shift_min(row["end_at"],
                                               K.TRAVEL_CHAIN_WINDOW_MIN),
                 row["id"])))
        _seen = set()
        for f in _followers:
            if f["ulid"] in _seen:
                continue
            _seen.add(f["ulid"])
            if K._travel_rule_for_anchor_row(conn, f) is not None:
                K.travel_refresh(conn, state_conn, f["ulid"])
        # slice3: a cancelled anchor that held the day's prep hands it to the
        # next earliest anchor of that day.
        K._travel_refresh_day_peers(conn, state_conn, row.get("start_at"),
                                  row["id"])
        # No follower refresh is guaranteed (including a day with no legs
        # left). Re-seat after cancellation commits; reuse the guarded pass.
        if n and row.get("start_at"):
            K._ride_resolve_best_effort(
                conn, state_conn,
                dates={K._travel_local_date(row["start_at"],
                                          row.get("display_tz"))})
    except Exception:  # noqa: BLE001 -- never fail a cancel on a re-derive
        pass
    return n


def travel_conformance_pass(K, conn, state_conn=None, today=None, now=None):
    """Nightly travel conformance (V10, roller-called at pass end).
    Internal order (3.3, pinned): (0) attach sweep, (1) delta engrave pass,
    (2) notification policy repair (even if frozen), (3) reconcile.
    Churn breaker: same rule regen-ed on 2 consecutive nightly runs ->
    freeze + retro flag (key = travel_rule.ulid; the roller's
    recurrence_id key is NOT reused). Release = travel-rule-upd only.

    Pass (0) is new and goes FIRST for the same reason the engrave goes
    before the judging: this pass converges the blocks of rules that exist,
    so an appointment with no rule was invisible to the whole convergence
    entrypoint. Attaching first means the rule minted this run gets
    converged this run. It is the same idempotent deriver (an already-armed
    anchor is EXISTS -> no write), so ordering it first costs nothing when
    there is nothing to attach."""
    now = now or K.now_utc()
    today = today or (datetime.datetime.strptime(now, "%Y-%m-%dT%H:%M:%SZ")
                      .replace(tzinfo=timezone.utc)
                      .astimezone(ZoneInfo(K.TRAVEL_DISPLAY_TZ))
                      .strftime("%Y-%m-%d"))
    run_id = K._conformance_run_start(conn, "travel_conformance", now)
    log_floor = conn.execute(
        "SELECT COALESCE(MAX(id), 0) FROM roller_log;").fetchone()[0]
    state_conn = K._travel_state_conn(state_conn)
    sweep = K.travel_attach_sweep(conn, now=now)
    engraved = K.travel_delta_engrave_pass(conn, now=now)
    total_regen = 0
    frozen = 0
    for rule in K._travel_rows(
            conn, "SELECT * FROM travel_rule WHERE status='active';", ()):
        K._travel_notify_policy_repair(conn, rule, now)
        if K._travel_frozen(conn, rule):
            frozen += 1
            continue
        agg = K.travel_refresh_rule(conn, state_conn, rule, now=now)
        if agg["regen"] > 0:
            K._travel_log(conn, "travel_conformance_regen", rule["ulid"],
                        "regen=%d" % agg["regen"], now=now)
            total_regen += agg["regen"]
            if K._travel_regen_on_previous_run(conn, rule, run_id):
                K._travel_log(conn, "travel_conformance_frozen", rule["ulid"],
                            "regen=%d" % agg["regen"], now=now)
    # non-active rules: converge leftover live blocks (retire backstop).
    for rule in K._travel_rows(
            conn, "SELECT * FROM travel_rule WHERE status='retired';", ()):
        K._travel_rule_group_cleanup(conn, state_conn, rule, now=now)
    # 070: last, once the legs have converged -- ride routines re-seat on
    # them (nightly and every mirror-poll travel-refresh). A refresh above
    # may already have re-seated a day inline (travel_refresh's own hook),
    # so the report reads the run's ride_resolved rows, not just this call.
    K._ride_resolve_best_effort(conn, state_conn, now=now)
    ride = []
    for (detail,) in conn.execute(
            "SELECT detail FROM roller_log WHERE category='ride_resolved' "
            "AND id > ? ORDER BY id;", (log_floor,)):
        d, _sp, span = (detail or "").partition(" ")
        old_sa, _arrow, new_sa = span.partition("->")
        ride.append((d[2:], old_sa, new_sa))
    K._conformance_run_end(conn, "travel_conformance", run_id, now)
    return {"engraved": engraved, "regen": total_regen, "frozen": frozen,
            "attached": sweep["attached"], "needs": sweep["needs"],
            "sweep": sweep, "ride": ride}


def refresh_buffers(K, conn, now=None, bid=None):
    """Converge future appointments only; reuse pin and past-leg protection."""
    now = now or K.now_utc()
    rows = conn.execute("SELECT id,ulid FROM timeblock WHERE kind='appointment' "
                        "AND block_class IS NULL AND settled_at IS NULL "
                        "AND start_at > ? AND (? IS NULL OR id=?) "
                        "ORDER BY start_at,id", (now, bid, bid)).fetchall()
    if not rows:
        return 0
    state = K._travel_state_conn()
    try:
        for _pass in range(2):
            for _, ulid in rows:
                K.travel_refresh(conn, state, ulid, now=now, _chain=False)
        dates = {K._travel_local_date(r[0]) for r in conn.execute(
            "SELECT start_at FROM timeblock WHERE kind='appointment' AND "
            "settled_at IS NULL AND start_at>? AND (? IS NULL OR id=?)",
            (now, bid, bid))}
        if dates:
            K._ride_resolve_best_effort(conn, state, now=now, dates=dates)
    finally:
        if state is not None:
            state.close()
    return len(rows)


# Attendance metadata and physical destination reconciliation.

ATTENDANCE_MODES = ('online', 'in_person', 'unknown')
ATTENDANCE_FIELDS = ('attendance_mode', 'meeting_url')


def attendance_validate(fields):
    mode = fields.get('attendance_mode')
    if mode is not None and mode not in ATTENDANCE_MODES:
        raise ValueError('attendance_mode must be online, in_person or unknown')
    url = fields.get('meeting_url')
    if url and not str(url).startswith(('https://', 'http://')):
        raise ValueError('meeting_url must be an http(s) URL')


def attendance_details(conn, row):
    values = {key: row.get(key) for key in ATTENDANCE_FIELDS}
    try:
        for key, value in conn.execute(
                "SELECT attr_token,value FROM block_attr WHERE block_id=? "
                "AND attr_token IN ('attendance_mode','meeting_url') "
                "AND source='user'", (row.get('id'),)):
            values[key] = value
    except sqlite3.OperationalError:
        pass  # legacy schemas have no block_attr
    values["attendance_mode"] = values.get("attendance_mode") or "unknown"
    return values


def attendance_physical_place(k, conn, place):
    """Saved places are physical unless their resolver explicitly failed.

    Legacy intake minted names before looking them up. A failed lookup with
    no address, coordinates or measured route is not a physical destination.
    Keep the dictionary row; only stop deriving travel from it.
    """
    if not place:
        return False
    pid = place['id']
    if not k._config_stored(conn, k._PLACE_COORD_MISSING_KEY % pid):
        return True
    if (place.get('address') or k._travel_place_map_string(conn, pid)
            or k.place_coord_get(conn, pid) is not None):
        return True
    return conn.execute("SELECT 1 FROM travel_edge WHERE minutes IS NOT NULL "
                        "AND (from_place_id=? OR to_place_id=?) LIMIT 1",
                        (pid, pid)).fetchone() is not None


def attendance_online(k, conn, row):
    info = attendance_details(conn, row)
    if info.get('meeting_url') or info.get('attendance_mode') == 'online':
        return True
    # An unresolved event link is a remote venue. Map links retain the
    # existing place-resolver flow; no host identifies a meeting platform.
    return any(k._location_link_kind(row.get(key) or '') == 'virtual'
               for key in ('location', 'location_url'))


def attendance_clear_travel(k, conn, row, state_conn=None, now=None):
    """Remove derived planning through the kit's tombstone verbs.

    A mode/place change invalidates even pinned travel. Settled history is
    preserved. Auto-retired rule versions are recorded separately from an
    owner's opt-out, so a later physical venue can attach again.
    """
    if row.get('kind') not in ('appointment', 'task', 'presence') or row.get('block_class') is not None:
        return False
    rules = k._travel_rows(conn, "SELECT * FROM travel_rule WHERE "
                           "anchor_type='event' AND anchor_ulid=? "
                           "AND status!='retired'", (row['ulid'],))
    blocks = k._travel_rows(conn, "SELECT * FROM timeblock WHERE "
                            "block_class='travel' AND derived_from=? "
                            "AND settled_at IS NULL", (row['ulid'],))
    changed = bool(rules or blocks or row.get('place_id') is not None)
    if not changed:
        return False
    owns_state = state_conn is None
    state_conn = k._travel_state_conn(state_conn)
    try:
        for block in blocks:
            if not k._travel_row_has_actuals(block):
                k._travel_tombstone_block(conn, state_conn, block)
        retired = []
        for rule in rules:
            result = k.travel_rule_retire(conn, rule['id'], state_conn=state_conn, now=now)
            if result.get('result') != 'applied':
                raise ValueError('could not retire stale travel rule')
            saved = k.travel_rule_get(conn, rule['id'])
            retired.append('%s:%s' % (saved['id'], saved['version']))
        if changed:
            conn.execute('BEGIN IMMEDIATE')
            try:
                k._tb_update(conn, row['id'], {'place_id': None})
                for token in retired:
                    conn.execute("INSERT OR REPLACE INTO block_attr "
                                 "(block_id,attr_token,value,source) VALUES(?,?,?,'user')",
                                 (row['id'], 'travel_auto_retired_' + token.split(':')[0], token))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return changed
    finally:
        if owns_state and state_conn is not None:
            state_conn.close()


def attendance_owner_retired(conn, row, rule):
    """An explicit later retire changes the version and is still an opt-out."""
    try:
        marker = conn.execute("SELECT value FROM block_attr WHERE block_id=? "
                              "AND attr_token=? AND source='user'",
                              (row['id'], 'travel_auto_retired_' + str(rule['id']))).fetchone()
        return not marker or marker[0] != '%s:%s' % (rule['id'], rule['version'])
    except sqlite3.OperationalError:
        return True


def attendance_repair(k, conn, row, state_conn=None, now=None):
    if not row or row.get('kind') not in ('appointment', 'task') or row.get('block_class'):
        return False
    location = row.get('location')
    is_online = attendance_online(k, conn, row)
    # Explicit physical rules with no venue text remain valid (SDK callers).
    # A stale FK/provisional rule with cleared text is derived metadata.
    rule = k._travel_rule_for_anchor_txn(conn, row['ulid'], None)
    marker = conn.execute("SELECT 1 FROM block_attr WHERE block_id=? "
                          "AND attr_token='location_cleared' AND value='1'",
                          (row['id'],)).fetchone() if k._is_v32(conn) else None
    cleared = k._location_is_undecided(location) or (not location and (
        marker or (rule and rule.get('before_src') == k.TRAVEL_SRC_PROVISIONAL
                   and rule.get('place_id') is None)))
    unresolved = bool(location and (k._location_link_kind(location) or
                                   not k._resolve_location_place(conn, location)))
    edited = conn.execute("SELECT 1 FROM block_attr WHERE block_id=? "
                          "AND attr_token='location_edited' AND value='1'",
                          (row['id'],)).fetchone() if k._is_v32(conn) else None
    stale = unresolved and edited and (row.get('place_id') is not None or rule is not None)
    unlocated = (row.get('place_id') is not None and
                 not attendance_physical_place(k, conn, k.travel_place_get(conn, row['place_id'])))
    if is_online or cleared or stale or unlocated:
        return attendance_clear_travel(k, conn, row, state_conn, now)
    return False


def attendance_touch(k, conn, ids=None, since=None, until=None, now=None):
    """Repair selected schedule rows before a read/card loads its rows."""
    if conn.in_transaction:
        return  # a reader must not commit another writer's transaction
    sql = "SELECT * FROM timeblock WHERE kind IN ('appointment','task') AND block_class IS NULL"
    args = []
    if ids is not None:
        ids = list(ids)
        if not ids:
            return
        sql += ' AND id IN (' + ','.join('?' for _ in ids) + ')'
        args.extend(ids)
    else:
        sql += ' AND start_at>=? AND start_at<?'
        args.extend((since, until))
    for row in k._travel_rows(conn, sql, args):
        try:
            if attendance_repair(k, conn, row, now=now) and row.get('start_at'):
                k._travel_refresh_day_all(conn, None, row['start_at'], now=now)
        except (sqlite3.Error, ValueError):
            # Read-only connections cannot repair persisted rows. Rendering
            # still respects attendance; the next writable touch converges.
            continue
