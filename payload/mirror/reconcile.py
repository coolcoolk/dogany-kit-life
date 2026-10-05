# -*- coding: utf-8 -*-
"""
DGN-180 W12 weekly reconcile job (X10b items 5+6) -- promoted from
fulldiff.py.

- Tombstone-aware: a cancelled calendar item whose SoT row is routed to
  tasks (routing-flip residue) or settled abandoned (our cancel push) is
  EXPECTED, not an orphan.
- Auto-repair safe classes only: missing-on-surface -> outbox re-push
  (idempotent sync). Field mismatches and orphans -> report only (a blind
  re-push could clobber an unseen owner edit; orphans need owner policy
  dec-009).
- Failed-row retry (item 6): failed outbox rows get bounded re-attempts
  (requeues < MAX_REQUEUES -> back to queued with reset attempts); at the
  cap -> repeated_failure notification. No infinite silent dead rows.
- Summary lands on the notification interface (sandbox notify_outbox).
- DGN-294/DGN-364 (V15): run_reconcile takes the cal_ids DICT (string shim
  accepted); the scan set covers ALL engraved category calendars; per-ulid
  LISTS + calendar_dup detection; wrong-calendar drift attention.
- Instrument honesty (2026-09-09): title-only deltas on rows behind the
  repaint boundary whose surface still equals the last-pushed snapshot are
  reported as unrepairable_past, separated from mismatched and excluded
  from the verdict (DGN-840 forbids re-pushing history, so there is nothing
  a human can do about them). Counted every run, never silenced.

- Ticket 2026-09-26 calendar-sync-report-routing (owner: "the owner sees
  only what needs a decision"): with repair on, a field mismatch is
  resolved instead of reported -- surface edited after our last push ->
  adopt it into the SoT through the poll's own 3-way apply; surface
  untouched since our push -> re-push the SoT (today-onward only, DGN-840).
  Future ghosts (below) are cancelled in the same run behind the SoT-absence
  guard. What is left is compared with the stored known-set and reported
  ONLY when something is new, naming it; chronic findings are never
  re-alerted. The report is a machine report (notify.MACHINE_REPORT_KINDS:
  kit operator, never the owner chat).

- Future ghost repair (dec-009 owner-approved class, 2026-09-30):
  --cancel-ghosts lists future confirmed calendar objects that carry our
  stamped ulid, have a push_snapshot here and no kit row; --apply cancels
  them. Past orphans are never touched.

Usage: python3 reconcile.py [--state <state_db>] [--repair] [--drain]
       python3 reconcile.py [--state <state_db>] --cancel-ghosts [--apply]
English/ASCII only.
"""

import json
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
import adapter as A
import mirror_i18n
import notify as notify_mod
import sdk_bridge

MAX_REQUEUES = 3

# DGN-268 S1 (H7) -> S4: user-facing strings route through a single seam. The
# ko literal is the fallback; the live value is resolved by AGENT_LANG through
# mirror_i18n (i18n key 'mirror.reconcile_verdict_attention'). notify.py owns
# its own TEMPLATES dict; this covers the one literal composed here.
TEXT = {
    "verdict_attention": u"확인 필요",
    # Finding labels for reconcile_new_findings {items} (i18n keys
    # mirror.reconcile_class.<key>, mirror.reconcile_item, mirror.reconcile_more).
    "class_mismatch": u"\ubd88\uc77c\uce58",
    "class_orphan": u"\uad00\ub9ac \ubc16",
    "class_missing": u"\ub204\ub77d",
    "class_drift": u"\ucde8\uc18c \ubbf8\ubc18\uc601",
    "class_cleanup_failed": u"\uc815\ub9ac \uc2e4\ud328",
    "class_cleanup_held": u"\uc815\ub9ac \ubcf4\ub958",
    "item": u"'{title}' {date} {cls}",
    "more": u" \uc678 {n}\uac74",
}

# Ticket 2026-09-26: the set of unresolved findings the operator was already
# told about (mirror_state KV, JSON list of finding keys). A full run
# replaces it -- a finding that resolved drops out, and alerts again only if
# it comes back; a scoped run only adds to it.
KNOWN_FINDINGS_KEY = "reconcile_known_findings"
# Names spelled out per report; the rest ride as "+N".
FINDING_NAME_CAP = 5
# Auto-cleanup ceiling. Above it NOTHING is cancelled and one cleanup_held
# finding goes to the operator: a mass "row vanished" reading is more likely
# a wrong/empty SoT than a mass deletion. 34 = the largest real ghost set
# measured (2026-09-27 travel legs).
ORPHAN_AUTOCLEAN_MAX = 50
# _apply_calendar_3way / _apply_tasks_3way results after which the surface
# and the SoT converge (applied, or refused + canonical re-push enqueued).
_ADOPT_RESOLVED = ("applied(", "presence_refused(", "schedule_refused(",
                   "presence_reechoed", "persons_reechoed")


def _fetch_all_calendar(cal_id):
    items, tok = [], None
    while True:
        params = {"calendarId": cal_id, "maxResults": 250,
                  "singleEvents": True, "showDeleted": True}
        if tok:
            params["pageToken"] = tok
        resp = A.gws("calendar", "events", "list", "--params", json.dumps(params))
        items.extend(resp.get("items", []))
        tok = resp.get("nextPageToken")
        if not tok:
            return items


def _fetch_all_tasks(tl_id):
    items, tok = [], None
    while True:
        params = {"tasklist": tl_id, "maxResults": 100, "showHidden": True,
                  "showCompleted": True, "showDeleted": True}
        if tok:
            params["pageToken"] = tok
        resp = A.gws("tasks", "tasks", "list", "--params", json.dumps(params))
        items.extend(resp.get("items", []))
        tok = resp.get("nextPageToken")
        if not tok:
            return items


def retry_failed(state_conn, src_conn, max_requeues=MAX_REQUEUES):
    """Bounded re-attempts for failed outbox rows. At the requeue cap the
    row gets ONE repeated_failure notification and then goes terminal
    (dead=1, g5-4): no infinite silent dead rows, no weekly re-alerts."""
    requeued, capped, dead_skipped = 0, 0, 0
    rows = state_conn.execute(
        "SELECT * FROM mirror_outbox WHERE status='failed'").fetchall()
    for r in rows:
        if r["dead"]:
            dead_skipped += 1
            continue
        if r["requeues"] < max_requeues:
            try:
                state_conn.execute(
                    "UPDATE mirror_outbox SET status='queued', attempts=0, "
                    "requeues=requeues+1, updated_at=? WHERE id=?",
                    (A._now_iso(), r["id"]))
                state_conn.commit()
                requeued += 1
            except sqlite3.IntegrityError:
                state_conn.rollback()  # another pending row exists -> skip
        else:
            capped += 1
            notify_mod.notify(
                state_conn, "repeated_failure", r["event_ulid"],
                title=A._title_of(src_conn, r["event_ulid"]))
            state_conn.execute(
                "UPDATE mirror_outbox SET dead=1, updated_at=? WHERE id=?",
                (A._now_iso(), r["id"]))
            state_conn.commit()
    return {"requeued": requeued, "capped": capped,
            "dead_skipped": dead_skipped}


def run_reconcile(state_conn, src_conn, cal_ids, tl_id, repair=True,
                  scope_ulids=None):
    """scope_ulids: when provided, only these SoT ulids are reconciled
    (targeted reconcile / sample-scale test). None = full DB (cutover job).
    g6-12: classification runs under the single mirror lock (poll/drain
    excluded while we read surfaces + apply dec-011).
    cal_ids (V15): the category dict; a legacy single id string is accepted
    via the _cal_ids_dict shim."""
    if not A._acquire_drain_lock(state_conn):
        return {"verdict": "LOCKED", "status": "locked"}
    try:
        return _run_reconcile_locked(state_conn, src_conn, cal_ids, tl_id,
                                     repair, scope_ulids)
    finally:
        A._release_drain_lock(state_conn)


def _run_reconcile_locked(state_conn, src_conn, cal_ids, tl_id, repair,
                          scope_ulids):
    # DGN-294/DGN-364 (V15): reconcile = full scan of ALL engraved category
    # calendars. The scan set comes from the same 2.2 dict-shape rule as
    # poll_cycle (_calendar_scan_set): a legacy/all-identical dict collapses
    # to ONE fetch with no category attribution (no drift attention in
    # legacy mode). Track WHICH calendar each item came from for drift
    # attention (multi mode only).
    cal_ids = A._cal_ids_dict(cal_ids)
    cal_items = []
    item_cal_key = {}
    item_cal_id = {}            # item id -> calendar id it was read from
    for cid, key in A._calendar_scan_set(cal_ids):
        for it in _fetch_all_calendar(cid):
            cal_items.append(it)
            if it.get("id"):
                item_cal_id[it["id"]] = cid
            if key is not None and it.get("id"):
                item_cal_key[it["id"]] = key
    task_items = _fetch_all_tasks(tl_id)

    # grill m1: per-ulid LISTS -- a dict would silently collapse the same
    # ulid appearing on two calendars (exactly the drift/duplication case
    # reconcile exists to catch).
    cal_by_ulid, foreign_cal = {}, 0
    for it in cal_items:
        u = A._extract_ulid(it)
        if u is None:
            foreign_cal += 1
        else:
            cal_by_ulid.setdefault(u, []).append(it)
    # grill-5 finding 2: keep the deleted-tombstone id set (matched by the
    # gtask_id bookkeeping column, NOT by sentinel -- tombstone notes
    # preservation is unproven, finding 8).
    task_by_ulid, foreign_task = {}, 0
    deleted_task_ids = set()
    for it in task_items:
        if it.get("deleted"):
            if it.get("id"):
                deleted_task_ids.add(it["id"])
            continue  # tombstone = not a live surface object
        m = A._SENTINEL_RE.search(it.get("notes", "") or "")
        if m:
            task_by_ulid[m.group(1)] = it
        else:
            foreign_task += 1

    missing, mismatched, expected_tombstones = [], [], []
    # DGN-302: abandoned rows whose gcal event is still confirmed (the cancel
    # push was never enqueued). Tracked separately so repair can enqueue a
    # sync via the normal outbox path (push_calendar projects abandoned ->
    # cancelled). Foreign-item guard is unchanged: we only reach here via
    # cal_by_ulid, which only contains our-ulid items.
    abandoned_gcal_drift = []
    owner_deleted = []
    # 2026-09-09 instrument honesty: rows the policy forbids us to fix must
    # not be counted as defects. Measured: mismatched=1041, nearly all of it
    # title-only deltas on PAST calendar events ("헬스 ..." on the surface vs
    # "✓헬스 ..." projected) -- the done-decoration (and the SPEC 2026-08-14
    # render model) arrived AFTER those rows' last push, and DGN-840 rules
    # that repaint never re-pushes history. Counting the same 1041 every week
    # buried the 4 missing + 45 orphans that ARE actionable, and made
    # ATTENTION a constant nobody reads. unrepairable_past is that class:
    # reported every run on its own line (never zeroed, never capped), but
    # excluded from mismatched and from the verdict.
    #
    # Boundary choice (why the repaint gate and not pushed_at vs settled_at):
    # "can't fix" is DEFINED by the re-push policy, so the boundary must be
    # the same predicate repaint() uses for skipped_past --
    # _local_date_of(start_at) < _now_local_date(). One shared criterion, the
    # two sites cannot diverge. A push-time/settle-time comparison would (a)
    # also match TODAY-ONWARD rows, which repaint/poll can still legitimately
    # re-push (those must stay actionable), and (b) miss the render-model
    # cause entirely, where settled_at plays no role. The settle-after-push
    # intuition is instead captured -- more strongly -- by the snapshot
    # evidence check at the classification site below.
    unrepairable_past = []
    repaint_boundary = A._now_local_date()
    checked = 0
    sot_total = 0
    sot_rows = {}
    # Field-diff mismatches the auto-resolve may close:
    # ulid -> (surface, item, enriched ev).
    resolvable = {}
    for r in src_conn.execute("SELECT * FROM timeblock").fetchall():
        sot_total += 1
        ev = dict(r)
        # DGN-579 bundle + SPEC 2026-08-14: enrich BEFORE the scope check and
        # the projection compare. The surface holds the place-resolved
        # location + persons with-line (F1), AND the 집쉼 HIDDEN gate in
        # in_mirror_scope reads the enriched place render -- so scope must see
        # the enriched dict too.
        ev = A.enrich_event(src_conn, ev)
        if not A.in_mirror_scope(ev):
            continue
        if scope_ulids is not None and ev["ulid"] not in scope_ulids:
            continue
        sot_rows[ev["ulid"]] = ev
        checked += 1
        target = A.route_surface(ev)
        abandoned = (ev.get("settled_outcome") == "abandoned"
                     or ev.get("status") == "abandoned")

        cal_list = cal_by_ulid.pop(ev["ulid"], [])
        if len(cal_list) > 1:
            # m1: same ulid on >1 calendar = duplication drift -> report,
            # never silently pick-and-hide. Classification continues with
            # the copy on the EXPECTED calendar when present.
            homes = sorted(item_cal_key.get(i.get("id"), "?")
                           for i in cal_list)
            A.mirror_log(state_conn, "calendar_dup", ev["ulid"],
                         "copies=%s" % ",".join(homes))
            mismatched.append(("calendar_dup", ev["ulid"],
                               {"copies": homes}))
        want_key = A.route_cal_key(ev)
        cal_it = None
        for i in cal_list:
            if item_cal_key.get(i.get("id")) == want_key:
                cal_it = i
                break
        if cal_it is None and cal_list:
            cal_it = cal_list[0]
        task_it = task_by_ulid.pop(ev["ulid"], None)

        if abandoned:
            # Expected: calendar tombstone-or-absent, task absent.
            # DGN-302 (V15 re-graft): consider ALL calendar copies for this
            # ulid -- if ANY copy is still non-cancelled the cancel push
            # never landed = drift. With repair=True we enqueue a sync so
            # the drain pushes status=cancelled (ONE entry per ulid, never
            # per copy; classified separately from mismatched so the repair
            # path is targeted and the report is informative). All copies
            # cancelled stays the expected-tombstone case.
            if cal_list:
                if any(i.get("status") != "cancelled" for i in cal_list):
                    abandoned_gcal_drift.append(ev["ulid"])
                else:
                    expected_tombstones.append(("abandoned-cal", ev["ulid"]))
            if task_it is not None and not task_it.get("deleted"):
                mismatched.append(("tasks", ev["ulid"],
                                   {"expected": "deleted",
                                    "got": task_it.get("status")}))
            continue

        if target == "calendar":
            if task_it is not None and not task_it.get("deleted"):
                mismatched.append(("tasks", ev["ulid"],
                                   {"expected": "absent (calendar-routed)",
                                    "got": task_it.get("status")}))
            if cal_it is None:
                missing.append(("calendar", ev["ulid"]))
                continue
            # V15 drift attention: found in a calendar route() does not map
            # this row to -> ONE report line, no silent move.
            found_key = item_cal_key.get(cal_it.get("id"))
            if found_key is not None and found_key != want_key:
                A.mirror_log(state_conn, "calendar_drift", ev["ulid"],
                             "found_in=%s expected=%s"
                             % (found_key, want_key))
                mismatched.append(("calendar_drift", ev["ulid"],
                                   {"found_in": found_key,
                                    "expected": want_key}))
                continue
            if cal_it.get("status") == "cancelled":
                # live row but tombstoned surface = drift (owner cancel
                # awaiting decision, or W3 revive pending) -> report only.
                mismatched.append(("calendar", ev["ulid"],
                                   {"expected": "confirmed",
                                    "got": "cancelled (owner decision or "
                                           "revive pending)"}))
                continue
            p_ev = A.calendar_projection_from_event(ev)
            p_it = A.calendar_projection_from_item(cal_it)
            if p_ev != p_it:
                diffs = {k: (p_ev.get(k), p_it.get(k)) for k in p_ev
                         if p_ev.get(k) != p_it.get(k)}
                # Owner decision 2026-07-14: cosmetic surface appearance
                # (color markers) quietly accepts the Google side as truth.
                # color_id drift is NOT a mismatch and never repaired.
                diffs.pop("color_id", None)
                if not diffs:
                    continue
                # unrepairable_past classification. THREE conditions, all
                # required:
                #   1. title is the ONLY differing field. Any other field
                #      (time/location/note/status/persons) differing may be
                #      real drift -- an owner retro-edit of a past event must
                #      keep firing ATTENTION, so multi-field rows are never
                #      folded into this class.
                #   2. the row sits BEFORE the repaint boundary (the exact
                #      skipped_past predicate from repaint()): DGN-840 forbids
                #      re-pushing it, so no sanctioned mechanism can repair
                #      the delta. Today-onward rows with the same delta stay
                #      mismatched (repaint CAN fix those).
                #   3. the surface title equals the title we LAST PUSHED
                #      (push_snapshot 3-way base). This is the evidence that
                #      the calendar side is untouched since our own write and
                #      the delta is purely OUR projection having evolved after
                #      that push (decoration armed later / render change) --
                #      i.e. "the settle/render change postdates the last
                #      push", proven per-row instead of inferred from
                #      timestamps. If the snapshot is missing or the surface
                #      deviates from it, an owner edit may be buried under the
                #      title delta -> stays mismatched.
                # Not sticky: no ack flag is stored -- the class is recomputed
                # from live evidence every run, so the moment the surface
                # changes (owner edit) or the owner deliberately rewrites
                # history (--repaint --since <past> --drain) these rows fall
                # back into mismatched / disappear on their own. Nothing is
                # marked "seen" and forgotten.
                if (set(diffs) == {"title"}
                        and A._local_date_of(ev.get("start_at"))
                        < repaint_boundary):
                    _h, snap = A.load_push_snapshot(state_conn, ev["ulid"])
                    if snap.get("title") == p_it.get("title"):
                        unrepairable_past.append(
                            ("calendar", ev["ulid"], diffs))
                        continue
                mismatched.append(("calendar", ev["ulid"], diffs))
                resolvable[ev["ulid"]] = ("calendar", cal_it, ev)
        else:
            # Tasks-routed: a cancelled calendar leftover = flip tombstone.
            if cal_it is not None:
                if cal_it.get("status") == "cancelled":
                    expected_tombstones.append(("flip-tombstone", ev["ulid"]))
                else:
                    mismatched.append(("calendar", ev["ulid"],
                                       {"expected": "cancelled flip tombstone",
                                        "got": cal_it.get("status")}))
            if task_it is None:
                # dec-011 (cutover round): a live SoT row whose surface task
                # was DELETED by the owner -> cancel verb (abandoned) +
                # bookkeeping clear + one confirm notification. Never
                # resurrected by auto-repair.
                gt = A.bk_get(state_conn, src_conn, ev["ulid"], "gtask_id")
                if gt and gt in deleted_task_ids:
                    res = A._run_verb(lambda: sdk_bridge.cancel_abandoned(
                        src_conn, ev["ulid"], "gtasks-owner-delete"))
                    if res in A.VERB_OK:
                        A.bk_set(state_conn, src_conn, ev["ulid"],
                                 "gtask_id", "")
                        A.bk_set(state_conn, src_conn, ev["ulid"],
                                 "gtask_etag", "")
                        state_conn.execute(
                            "DELETE FROM push_snapshot WHERE event_ulid=?",
                            (ev["ulid"],))
                        state_conn.commit()
                        notify_mod.notify(
                            state_conn, "task_deleted", ev["ulid"],
                            title=ev.get("title") or ev["ulid"])
                    owner_deleted.append(ev["ulid"])
                    continue
                missing.append(("tasks", ev["ulid"]))
                continue
            p_ev = A.tasks_projection_from_event(ev)
            p_it = A.tasks_projection_from_item(task_it)
            if p_ev != p_it:
                diffs = {k: (p_ev.get(k), p_it.get(k)) for k in p_ev
                         if p_ev.get(k) != p_it.get(k)}
                mismatched.append(("tasks", ev["ulid"], diffs))
                resolvable[ev["ulid"]] = ("tasks", task_it, ev)

    # Leftovers: ours-marked surface items with no live SoT row. In scoped
    # mode (grill-5 finding 7), out-of-scope leftovers are simply rows we
    # did not check this run -- not orphans.
    # A leftover whose every calendar copy is already cancelled is a deleted
    # object (Google trash), not something to clean up -- counted as an
    # expected tombstone, not an orphan.
    for u in list(cal_by_ulid):
        if all(i.get("status") == "cancelled" for i in cal_by_ulid[u]):
            expected_tombstones.append(("orphan-tombstone", u))
            del cal_by_ulid[u]
    leftovers = ([("calendar", u) for u in cal_by_ulid]
                 + [("tasks", u) for u in task_by_ulid])
    if scope_ulids is not None:
        leftovers = [(sfc, u) for sfc, u in leftovers if u in scope_ulids]
    orphans = leftovers

    # g6-8: a ulid with a terminal-dead outbox row must NOT be re-enqueued
    # by repair (dead-revival loop). Reported once as its own class.
    dead_ulids = {r["event_ulid"] for r in state_conn.execute(
        "SELECT DISTINCT event_ulid FROM mirror_outbox WHERE dead=1"
    ).fetchall()}
    dead_held = [(sfc, u) for sfc, u in missing if u in dead_ulids]
    missing = [(sfc, u) for sfc, u in missing if u not in dead_ulids]
    repaired = 0
    if repair:
        for _surface, ulid in missing:
            if A.outbox_enqueue(state_conn, ulid):
                repaired += 1

    # DGN-302: enqueue a sync for each abandoned-gcal-drift row so the drain
    # pushes the cancel. Dead-row guard applies here too (no revival loop).
    abandoned_cancel_enqueued = 0
    if repair:
        for ulid in abandoned_gcal_drift:
            if ulid not in dead_ulids:
                if A.outbox_enqueue(state_conn, ulid):
                    abandoned_cancel_enqueued += 1

    retry = retry_failed(state_conn, src_conn)

    # Ticket 2026-09-26: resolve field mismatches instead of reporting them.
    resolved = {"adopted": 0, "repushed": 0}
    if repair and resolvable:
        still = []
        for m in mismatched:
            cand = resolvable.get(m[1]) if m[0] in ("calendar", "tasks") \
                else None
            how = None
            if cand is not None and isinstance(m[2], dict) \
                    and m[0] == cand[0]:
                try:
                    how = _auto_resolve(state_conn, src_conn, cand, tl_id,
                                        repaint_boundary, dead_ulids)
                except Exception as e:      # noqa: BLE001 -- stays reported
                    A.mirror_log(state_conn, "reconcile_resolve_failed",
                                 m[1], str(e)[:160])
            if how:
                resolved[how] += 1
            else:
                still.append(m)
        mismatched = still
        if resolved["adopted"]:
            # Deferred overlap notices from the adopted schedule edits (the
            # poll cycle's batch-end recheck, same contract).
            A.overlap_flush(state_conn, src_conn)

    # Ticket 2026-09-26 orphan cleanup: future ghosts of hard-deleted rows,
    # cancelled behind the SoT-absence guard.
    cleanup = _clean_orphans(state_conn, src_conn, cal_by_ulid, item_cal_key,
                             item_cal_id, orphans, repair, scope_ulids,
                             sot_total)
    if cleanup["cleaned"]:
        cleaned = set(cleanup["cleaned_ulids"])
        orphans = [(sfc, u) for sfc, u in orphans
                   if not (sfc == "calendar" and u in cleaned)]

    # An owner-deleted task was cancelled in the SoT above (dec-011), and an
    # adopted surface edit is an SoT write too: inbound writes that never
    # pass kit_life main(), so refresh the pin.
    if owner_deleted or resolved["adopted"]:
        A._refresh_day_card()

    # owner_deleted is auto-handled (dec-011 applied + notified) -> it does
    # not require attention by itself. unrepairable_past is likewise excluded:
    # ATTENTION must mean "a human has something to do", and by construction
    # there is nothing sanctioned to do about those rows (DGN-840). The count
    # itself still lands in the summary below on its own line -- separated,
    # not silenced.
    verdict = ("CLEAN" if not missing and not mismatched and not orphans
               and not abandoned_gcal_drift
               else "ATTENTION")
    summary = {
        "checked": checked,
        "missing": len(missing),
        "repaired_enqueued": repaired,
        "mismatched": len(mismatched),
        "unrepairable_past": len(unrepairable_past),
        "unrepairable_past_detail": unrepairable_past[:20],
        "orphans": len(orphans),
        "owner_deleted": len(owner_deleted),
        "dead_held": len(dead_held),
        "dead_held_detail": dead_held[:20],
        "expected_tombstones": len(expected_tombstones),
        "abandoned_gcal_drift": len(abandoned_gcal_drift),
        "abandoned_cancel_enqueued": abandoned_cancel_enqueued,
        "abandoned_gcal_drift_detail": abandoned_gcal_drift[:20],
        "foreign": {"calendar": foreign_cal, "tasks": foreign_task},
        "retry": retry,
        "verdict": verdict,
        "missing_detail": missing[:20],
        "mismatch_detail": mismatched[:20],
        "orphan_detail": orphans[:20],
        "owner_deleted_detail": owner_deleted[:20],
        "auto_resolved": resolved["adopted"] + resolved["repushed"],
        "auto_adopted": resolved["adopted"],
        "auto_repushed": resolved["repushed"],
        "orphans_cleaned": cleanup["cleaned"],
        "orphan_cleanup_failed": len(cleanup["failed"]),
        "orphan_cleanup_held": cleanup["held"],
    }
    # g6-13 (framework ruling): CLEAN = silent. Ticket 2026-09-26: and a
    # finding the operator was already told about is silent too -- only NEW
    # findings produce a report, each named. An unchanged chronic state (the
    # weekly job, or a second run right after a kit update) sends nothing.
    findings = _findings(missing if not repair else [], mismatched, orphans,
                         [u for u in abandoned_gcal_drift
                          if not repair or u in dead_ulids],
                         cleanup)
    known = set(json.loads(A.get_state(state_conn, KNOWN_FINDINGS_KEY)
                           or "[]"))
    new = [k for k in sorted(findings) if k not in known]
    A.set_state(state_conn, KNOWN_FINDINGS_KEY, json.dumps(sorted(
        set(findings) if scope_ulids is None else known | set(findings))))
    summary["new_findings"] = len(new)
    summary["new_findings_detail"] = new[:20]
    if new:
        notify_mod.notify(
            state_conn, "reconcile_new_findings", None, dedup=False,
            count=len(new),
            items=_finding_items(new, findings, sot_rows, cal_by_ulid,
                                 task_by_ulid),
            checked=checked, open=len(findings),
            mismatch=_count(findings, "mismatch"),
            orphan=_count(findings, "orphan"),
            missing=_count(findings, "missing"),
            resolved=resolved["adopted"] + resolved["repushed"],
            cleaned=cleanup["cleaned"],
            unrepairable=len(unrepairable_past))
    return summary


def _count(findings, cls):
    return sum(1 for k in findings if k.startswith(cls + ":"))


def _utc_seconds(v):
    """'YYYY-MM-DDTHH:MM:SS[.fff]Z' (Google 'updated', our pushed_at) ->
    epoch seconds; None when absent or not UTC (undecidable, never
    guessed)."""
    if not v or not (v.endswith("Z") or v.endswith("+00:00")):
        return None
    try:
        return (datetime.strptime(v[:19], "%Y-%m-%dT%H:%M:%S")
                - datetime(1970, 1, 1)).total_seconds()
    except ValueError:
        return None


def _edited_side(state_conn, ulid, surface, item):
    """Who moved since our last push? 'google' when the surface differs
    from the push_snapshot AND Google's 'updated' is after our pushed_at;
    'ours' when the surface still equals the snapshot (the SoT moved);
    None when undecidable (no snapshot, no usable timestamps)."""
    snap_hash, _snap = A.load_push_snapshot(state_conn, ulid)
    if snap_hash is None:
        return None
    proj = (A.calendar_projection_from_item(item) if surface == "calendar"
            else A.tasks_projection_from_item(item))
    if A.projection_hash(proj) == snap_hash:
        return "ours"
    row = state_conn.execute(
        "SELECT pushed_at FROM push_snapshot WHERE event_ulid=?",
        (ulid,)).fetchone()
    upd = _utc_seconds(item.get("updated"))
    pushed = _utc_seconds(row["pushed_at"] if row else None)
    if upd is not None and pushed is not None and upd > pushed:
        return "google"
    return None


def _auto_resolve(state_conn, src_conn, cand, tl_id, boundary, dead_ulids):
    """One field mismatch -> 'adopted' | 'repushed' | None (left reported).
    Owner edit wins: a surface edited after our last push is adopted into
    the SoT through the poll's own 3-way apply (same verbs, same refusal
    contract). Otherwise the SoT is re-pushed -- never a past calendar row
    (DGN-840: history is not re-pushed) and never a dead outbox row (g6-8)."""
    surface, item, ev = cand
    ulid = ev["ulid"]
    side = _edited_side(state_conn, ulid, surface, item)
    if side == "google":
        if surface == "calendar":
            action = A._apply_calendar_3way(item, state_conn, src_conn)
        else:
            action = A._apply_tasks_3way(item, tl_id, state_conn, src_conn)
        A.mirror_log(state_conn, "reconcile_adopt_surface", ulid,
                     "surface=%s action=%s" % (surface, action))
        return "adopted" if action.startswith(_ADOPT_RESOLVED) else None
    if side == "ours":
        if surface == "calendar" and \
                A._local_date_of(ev.get("start_at")) < boundary:
            return None
        if ulid in dead_ulids:
            return None
        A.outbox_enqueue(state_conn, ulid)  # False = already pending: fine
        A.mirror_log(state_conn, "reconcile_repush", ulid,
                     "surface=%s sot changed after last push" % surface)
        return "repushed"
    return None


def _clean_orphans(state_conn, src_conn, cal_by_ulid, item_cal_key,
                   item_cal_id, orphans, repair, scope_ulids, sot_total,
                   now=None):
    """Cancel the calendar orphans that are future ghosts (_ghost_of: our
    stamped ulid, confirmed, future, pushed by us, no kit row). SoT-absence
    guard -- nothing is cancelled unless ALL hold: repair run, full (not
    scoped) run, the SoT read returned rows, the ghost count is within
    ORPHAN_AUTOCLEAN_MAX, and the per-ulid kit-row SELECT (re-run in
    _ghost_of) finds nothing. A failed SoT or calendar read never gets here
    (the run raises first). Past orphans are never touched (DGN-840)."""
    out = {"cleaned": 0, "cleaned_ulids": [], "failed": [], "held": 0}
    if not repair or scope_ulids is not None or sot_total == 0:
        return out
    now = now or A._now_iso()
    cal_orphans = {u for sfc, u in orphans if sfc == "calendar"}
    ghosts = []
    for u in sorted(cal_orphans):
        for it in cal_by_ulid.get(u, []):
            g = _ghost_of(state_conn, src_conn, it,
                          item_cal_id.get(it.get("id")),
                          item_cal_key.get(it.get("id")), now)
            if g is not None:
                ghosts.append(g)
    if len(ghosts) > ORPHAN_AUTOCLEAN_MAX:
        out["held"] = len(ghosts)
        A.mirror_log(state_conn, "orphan_cleanup_held", None,
                     "ghosts=%d > max=%d; nothing cancelled"
                     % (len(ghosts), ORPHAN_AUTOCLEAN_MAX))
        return out
    for g in ghosts:
        if _cancel_ghost(state_conn, g):
            out["cleaned"] += 1
            out["cleaned_ulids"].append(g["ulid"])
        else:
            out["failed"].append(g["ulid"])
    # A ulid is cleaned only when every confirmed copy went.
    out["cleaned_ulids"] = [u for u in set(out["cleaned_ulids"])
                            if u not in out["failed"]]
    out["cleaned"] = len(out["cleaned_ulids"])
    return out


def _findings(missing, mismatched, orphans, drift, cleanup):
    """Unresolved findings after this run's auto-handling, as stable keys
    'class:surface:ulid' (the known-set unit)."""
    keys = set()
    for sfc, u in missing:
        keys.add("missing:%s:%s" % (sfc, u))
    for m in mismatched:
        keys.add("mismatch:%s:%s" % (m[0], m[1]))
    for sfc, u in orphans:
        keys.add("orphan:%s:%s" % (sfc, u))
    for u in drift:
        keys.add("drift:calendar:%s" % u)
    for u in cleanup["failed"]:
        keys.add("cleanup_failed:calendar:%s" % u)
    if cleanup["held"]:
        keys.add("cleanup_held:calendar:%d" % cleanup["held"])
    return keys


_CLASS_TEXT = {"mismatch": "class_mismatch", "orphan": "class_orphan",
               "missing": "class_missing", "drift": "class_drift",
               "cleanup_failed": "class_cleanup_failed",
               "cleanup_held": "class_cleanup_held"}


def _finding_label(key, sot_rows, cal_by_ulid, task_by_ulid):
    """One named finding: '<title>' <local date> <class>. Title/date from
    the SoT row when there is one, else from the surface object."""
    cls, sfc, ulid = key.split(":", 2)
    cls_txt = mirror_i18n.t("mirror.reconcile_class.%s" % cls,
                            TEXT[_CLASS_TEXT.get(cls, "class_mismatch")])
    if cls == "cleanup_held":
        return u"%s %s" % (ulid, cls_txt)
    title, date = "", ""
    ev = sot_rows.get(ulid)
    if ev is not None:
        title = ev.get("title") or ""
        date = A._local_date_of(ev.get("start_at"))
    else:
        it = (cal_by_ulid.get(ulid) or [None])[0] if sfc == "calendar" \
            else task_by_ulid.get(ulid)
        if it:
            title = it.get("summary") or it.get("title") or ""
            date = A._local_date_of(_item_start_utc(it)) if sfc == "calendar" \
                else (it.get("due") or "")[:10]
    return mirror_i18n.t("mirror.reconcile_item", TEXT["item"],
                         title=title or ulid[-6:], date=date or "-",
                         cls=cls_txt).replace("  ", " ")


def _finding_items(new, findings, sot_rows, cal_by_ulid, task_by_ulid):
    names = [_finding_label(k, sot_rows, cal_by_ulid, task_by_ulid)
             for k in new[:FINDING_NAME_CAP]]
    text = u", ".join(names)
    if len(new) > FINDING_NAME_CAP:
        text += mirror_i18n.t("mirror.reconcile_more", TEXT["more"],
                              n=len(new) - FINDING_NAME_CAP)
    return text


def _item_start_utc(item):
    """Canonical UTC start instant of a calendar item ('' when unreadable).
    All-day items resolve to local midnight through the same conversion the
    projection uses."""
    s, _e = A.gcal_datetime_to_utc_instants(
        item.get("start", {}) or {}, item.get("end", {}) or {})
    return s or ""


def find_future_ghosts(state_conn, src_conn, cal_ids, now=None):
    """dec-009 orphan policy, owner-approved class (2026-09-30): a calendar
    object that
      1. carries OUR stamped ulid (extendedProperties -- never the 32-hex id
         shape, which the Google web UI mints for owner events too),
      2. is still confirmed,
      3. starts strictly in the FUTURE (past orphans are history, DGN-840),
      4. has a push_snapshot in THIS state db (we pushed it), and
      5. has no kit row at all (hard-deleted source)
    is a ghost of a deleted row -- e.g. the 34 travel legs a non-owner
    instance's rule-regen hard-deleted on 2026-09-27. Read-only.
    Returns [{date, start_at, title, event_id, cal_id, cal_key, ulid}]
    ordered by start."""
    now = now or A._now_iso()
    ghosts = []
    for cid, key in A._calendar_scan_set(A._cal_ids_dict(cal_ids)):
        for it in _fetch_all_calendar(cid):
            g = _ghost_of(state_conn, src_conn, it, cid, key, now)
            if g is not None:
                ghosts.append(g)
    ghosts.sort(key=lambda g: (g["start_at"], g["event_id"] or ""))
    return ghosts


def _ghost_of(state_conn, src_conn, it, cid, key, now):
    """The dec-009 ghost predicate for ONE calendar item (see
    find_future_ghosts) -> ghost dict, or None. The kit-row check is a
    direct per-ulid SELECT -- the SoT-absence guard both callers share."""
    if it.get("status") != "confirmed" or not cid:
        return None
    ulid = A._stamped_ulid(it)
    if not ulid:
        return None
    start = _item_start_utc(it)
    if not start or start <= now:
        return None
    snap_hash, _snap = A.load_push_snapshot(state_conn, ulid)
    if snap_hash is None:
        return None
    if src_conn.execute("SELECT 1 FROM timeblock WHERE ulid=?",
                        (ulid,)).fetchone():
        return None
    return {"date": A._local_date_of(start), "start_at": start,
            "title": it.get("summary", "") or "",
            "event_id": it.get("id"), "cal_id": cid, "cal_key": key,
            "ulid": ulid, "_item": it}


def _cancel_ghost(state_conn, g):
    """Retire one ghost through the mirror's seam and echo-mark it (the W10
    mass-cancel breaker excludes snapshot-cancelled items). Caller holds the
    drain lock. True on success; a GwsError is logged and returns False."""
    try:
        A._retire_calendar_surface(g["cal_id"], g["event_id"])
    except A.GwsError as e:
        g["error"] = str(e)
        A.mirror_log(state_conn, "ghost_cancel_failed", g["ulid"],
                     "event_id=%s %s" % (g["event_id"], e))
        return False
    proj = A.calendar_projection_from_item(
        dict(g["_item"], status="cancelled"))
    A.store_push_snapshot(state_conn, g["ulid"], "calendar", proj,
                          calendar_id=g["cal_id"])
    A.mirror_log(state_conn, "ghost_cancel", g["ulid"],
                 "event_id=%s date=%s title=%s"
                 % (g["event_id"], g["date"], g["title"]))
    return True


def cancel_future_ghosts(state_conn, src_conn, cal_ids, apply=False,
                         now=None):
    """One-shot repair for find_future_ghosts. apply=False (default) is a
    dry run: nothing is written anywhere -- no lock, no log, no calendar
    call beyond the list reads. apply=True flips each ghost to 'cancelled'
    through the mirror's own retire seam (_retire_calendar_surface) under
    the drain lock, and records the cancel in push_snapshot so the next
    poll reads it as our own echo (the W10 mass-cancel breaker excludes
    snapshot-cancelled items; 34 un-echoed cancels would trip it).
    Returns {'mode', 'ghosts': [...], 'cancelled', 'failed'}."""
    if apply and not A._acquire_drain_lock(state_conn):
        return {"mode": "apply", "verdict": "LOCKED", "ghosts": [],
                "cancelled": 0, "failed": 0}
    try:
        ghosts = find_future_ghosts(state_conn, src_conn, cal_ids, now=now)
        cancelled = failed = 0
        if apply:
            for g in ghosts:
                if _cancel_ghost(state_conn, g):
                    cancelled += 1
                else:
                    failed += 1
        for g in ghosts:
            g.pop("_item", None)
        return {"mode": "apply" if apply else "dry-run", "ghosts": ghosts,
                "cancelled": cancelled, "failed": failed}
    finally:
        if apply:
            A._release_drain_lock(state_conn)


def repaint(state_conn, src_conn, since_date=None):
    """SPEC 2026-08-14 FORCED RE-RENDER. A render-RULE change (the new
    행위·장소·동행 model) does NOT dirty DB rows, so poll/reconcile never
    re-push existing calendar events -- the new render would stay invisible
    on already-pushed rows. repaint enqueues every in-scope, calendar-routed
    row from `since_date` (local date, inclusive) ONWARD into the mirror
    outbox; the normal drain then re-pushes each with the new render (push is
    idempotent -- an unchanged projection is an echo, only genuinely
    re-rendered rows produce a surface update).

    DGN-840 scope: TODAY-ONWARD only. Past calendar events are left untouched
    (a render change must not rewrite history). since_date defaults to the
    local today. Tasks-routed rows are skipped (Google Tasks surface is out
    of the render-model scope). 집쉼 hidden presence is dropped by
    in_mirror_scope (enriched first). Returns a summary dict."""
    if since_date is None:
        since_date = A._now_local_date()
    scanned = enqueued = skipped_past = skipped_tasks = skipped_scope = 0
    for r in src_conn.execute("SELECT * FROM timeblock").fetchall():
        ev = A.enrich_event(src_conn, dict(r))
        scanned += 1
        if not A.in_mirror_scope(ev):
            skipped_scope += 1
            continue
        if A.route_surface(ev) != "calendar":
            skipped_tasks += 1
            continue
        # today-onward gate: compare the LOCAL date of start_at (display tz)
        # against since_date. Calendar-routed rows always carry start_at
        # (untimed rows route to tasks and are skipped above). Past rows are
        # left untouched (a render change must not rewrite history).
        if A._local_date_of(ev.get("start_at")) < since_date:
            skipped_past += 1
            continue
        if A.outbox_enqueue(state_conn, ev["ulid"]):
            enqueued += 1
    return {"repaint_since": since_date, "scanned": scanned,
            "enqueued": enqueued, "skipped_past": skipped_past,
            "skipped_tasks": skipped_tasks, "skipped_scope": skipped_scope}


if __name__ == "__main__":
    # DGN-364: target resolution goes through the single resolver -- no raw
    # state-key reads in entry points (the DGN-363 anti-pattern is dead).
    args = sys.argv[1:]
    if "--state" in args:
        A.STATE_DB_PATH = args[args.index("--state") + 1]
    state = A.open_state_db()
    src = A.get_src_conn()
    targets = A.get_mirror_targets(state)
    # dec-009 one-shot ghost repair: --cancel-ghosts lists (dry run);
    # --cancel-ghosts --apply cancels. Runs alone -- no reconcile, no drain.
    if "--cancel-ghosts" in args:
        res = cancel_future_ghosts(state, src, targets["cal_ids"],
                                   apply="--apply" in args)
        for g in res["ghosts"]:
            print("%s\t%s\t%s\t%s%s" % (
                g["date"], g["title"], g["event_id"], g["cal_key"] or "-",
                ("\tERROR " + g["error"]) if g.get("error") else ""))
        print("mode: %s" % res["mode"])
        print("ghosts: %d" % len(res["ghosts"]))
        if res.get("verdict"):
            print("verdict: %s" % res["verdict"])
        print("cancelled: %d" % res["cancelled"])
        print("failed: %d" % res["failed"])
        src.close()
        state.close()
        sys.exit(1 if res["failed"] or res.get("verdict") == "LOCKED" else 0)
    # SPEC forced re-render: --repaint enqueues today-onward calendar rows for
    # re-push (optionally --since YYYY-MM-DD). Runs BEFORE the optional drain
    # so the same invocation can enqueue + push in one pass.
    if "--repaint" in args:
        since = None
        if "--since" in args:
            since = args[args.index("--since") + 1]
        summary = repaint(state, src, since_date=since)
        if "--drain" in args:
            summary["drain"] = A.outbox_drain(state, src, targets["cal_ids"],
                                              targets["checklist_id"])
        for k, v in summary.items():
            print("%s: %s" % (k, v))
        src.close()
        state.close()
        sys.exit(0)
    summary = run_reconcile(state, src, targets["cal_ids"],
                            targets["checklist_id"],
                            repair="--repair" in args)
    if "--drain" in args:
        summary["drain"] = A.outbox_drain(state, src, targets["cal_ids"],
                                          targets["checklist_id"])
    for k, v in summary.items():
        print("%s: %s" % (k, v))
    src.close()
    state.close()
