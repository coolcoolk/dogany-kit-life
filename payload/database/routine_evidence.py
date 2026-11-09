"""Routine evidence registry + settle-before-judge pass.

WHY THIS EXISTS
---------------
Originally routine_roller.lapse_pass settled only all_day instances; timed
instances were left unsettled ("expired graphite lifelog", spec 2.3). But
kit_life.routine_health._bucket used to read that same derived 'expired' as
MISSED (spec 6.1). So a timed routine with settlement lag reads as 0%
adherence, and the anomaly gate turns that into a push about the user's own
behaviour. Measured live: a gym routine was pushed as "missed N times
running" while the domain record (`workouts`) held a row for every one of
those dates -- the instance ledger was simply backfilled hours AFTER the
roller ran.

THE RULE
--------
  1  settle   if the kit holds evidence for this resource, settle timed
              instances from the domain record before judging anything
  2  judge    if evidence is still absent after settling -> then it counts
              as not done
  3  silence  if the kit does not observe that resource at all -> do not
              judge it and do not push about it

The trigger changes from "no stamp" to "we looked for evidence and found none".

TWO CAPABILITIES, NOT ONE
-------------------------
Measurement forced a split the one-line rule does not make. An evidence table
can be able to confirm a POSITIVE without being able to confirm a NEGATIVE:

  `workouts`   near-complete on measured deployments (recall >= the judge
               threshold), so ABSENCE is informative -> may settle AND may
               judge.
  `metric_log` weight rows land on only a fraction of completed weigh-in
               days. A present row still proves the weigh-in happened, but a
               MISSING row proves nothing -> may settle, may NOT judge.

So each entry carries `settles` and `judges` independently. `judges` is only
ever set from a measured recall (see measure_coverage below), never asserted.

FAIL-SAFE DIRECTION
-------------------
A routine absent from this registry is never judged or pushed about. An
explicit workout action can settle positive records for its owning domain;
other unregistered routines stay silent until their evidence is registered.
The failure mode of forgetting is silence, never a false accusation.

Keys are recurrence_ids (ASCII ULIDs), not titles -- titles are localized and
duplicated (two distinct routines can share the same display label).

REGISTRY ENTRIES ARE MEASUREMENTS, NOT POLICY
---------------------------------------------
The entries below are the recall measurements of the deployment this module
was extracted from, shipped as the working default so that installing the
pack over a live instance preserves its measured settle/judge capabilities
(shipping an empty registry would silently drop every routine to the rule-3
silence path on install day -- disabling a live behaviour is exactly the
reversion class an install must not cause). On any OTHER deployment these
recurrence_ids match no routine_def row, so every entry is inert and the
fail-safe silence path governs until that deployment measures and registers
its own routines. Re-derive or extend with `measure_coverage`.
"""

import datetime
from zoneinfo import ZoneInfo

# Minimum share of completed occurrences that must carry a matching evidence
# row before ABSENCE of evidence is allowed to count as "did not do it".
# Below this the source may still settle positives, but never judges.
JUDGE_RECALL_MIN = 0.80

# Minimum number of completed occurrences needed before a recall figure is
# trusted at all. Under this, the sample cannot support judging.
JUDGE_SAMPLE_MIN = 6

SETTLED_BY = "roller-evidence"


class EvidenceSource(object):
    """One domain table that can witness a routine occurrence on a date."""

    def __init__(self, table, date_col="date", where=None, params=()):
        self.table = table
        self.date_col = date_col
        self.where = where
        self.params = tuple(params)

    def dates(self, conn, lo, hi, owning_agent=None):
        """Set of dates in [lo, hi] for which the domain record has a row."""
        sql = 'SELECT DISTINCT "%s" FROM "%s" WHERE "%s" >= ? AND "%s" <= ?' % (
            self.date_col, self.table, self.date_col, self.date_col)
        params = [lo, hi]
        if self.where:
            sql += " AND (%s)" % self.where
            params.extend(self.params)
        if owning_agent is not None:
            sql += " AND owning_agent = ?"
            params.append(owning_agent)
        if self.table == "workouts":
            cols = {r[1] for r in conn.execute("PRAGMA table_info(workouts)")}
            if {"phase", "close_reason"} <= cols:
                sql += " AND (phase IS NULL OR (phase='closed' AND close_reason='completed'))"
        return {r[0] for r in conn.execute(sql + ";", params) if r[0]}

    def available(self, conn):
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?;",
            (self.table,)).fetchone()
        return row is not None

    def label(self):
        if not self.where:
            return self.table
        # render the bound params inline so the label reads as the actual
        # predicate rather than "metric = ?"
        text = self.where
        for value in self.params:
            text = text.replace("?", repr(value), 1)
        return "%s[%s]" % (self.table, text)


class EvidenceRule(object):
    def __init__(self, rid, source, settles=True, judges=False, note=""):
        self.rid = rid
        self.source = source
        self.settles = settles
        self.judges = judges
        self.note = note


# ---------------------------------------------------------------------------
# The registry. Every entry below is backed by a measurement; re-run
# `measure_coverage` to re-derive the recall figures.
# ---------------------------------------------------------------------------
REGISTRY = {
    # Gym, weekly Monday slot. workouts recall on completed days = 7/7.
    "rd:01KXFQ61MHTEAA2QH4M946QBFH": EvidenceRule(
        "rd:01KXFQ61MHTEAA2QH4M946QBFH",
        EvidenceSource("workouts"),
        settles=True, judges=True,
        note="gym W:MON -> workouts; recall 7/7 on completed days"),

    # Gym, Tue-Fri slot. workouts recall on completed days = 21/22.
    "rd:01KXFQ61PPY4EWD8RQKH35MWBH": EvidenceRule(
        "rd:01KXFQ61PPY4EWD8RQKH35MWBH",
        EvidenceSource("workouts"),
        settles=True, judges=True,
        note="gym W:TUE-FRI -> workouts; recall 21/22 on completed days"),

    # Weigh-in, weekly. A logged weight proves the weigh-in happened, but the
    # user logs only a fraction of them (recall 1/5), so absence proves
    # nothing and this entry must never judge.
    "rd:01KXFQ271NGW1KG0E74KS88BZD": EvidenceRule(
        "rd:01KXFQ271NGW1KG0E74KS88BZD",
        EvidenceSource("metric_log", where="metric = ?", params=("weight_kg",)),
        settles=True, judges=False,
        note="weigh-in -> metric_log[weight_kg]; recall 1/5, settle only"),

    # DELIBERATELY ABSENT -- presence routines. The kit holds no observation
    # of where the user physically was; `place` is a location registry, not a
    # log. kit_life.force_settle also refuses outcome 'done' on
    # kind='presence' (spec A3: being somewhere is not a completion), so a
    # done-rate over presence is a category error, not just an unmeasured
    # one. Silence path, rule 3.
}

# Legacy check-kind definitions measured on the shared deployment. Positive
# evidence only: changing the program's routine ID must not drop settlement,
# and a translated/renamed title must not change what the evidence means.
for _rid in ("rd:01M4APTQAM1JH180RVWF0RNS39",
             "rd:01M4APTR70DY4WQ87GV6GCFNSZ"):
    REGISTRY[_rid] = EvidenceRule(_rid, EvidenceSource("workouts"),
                                  note="legacy health workout; settle only")
for _rid in ("rd:01M4AK82FT4BEZE36ZT27BC1ME",):
    REGISTRY[_rid] = EvidenceRule(
        _rid, EvidenceSource("metric_log", where="metric = ?",
                             params=("weight_kg",)),
        note="legacy InBody; settle only")


def rule_for(rid):
    return REGISTRY.get(rid)


def judgeable(rid):
    """May the anomaly gate speak about this routine at all? (rule 3)"""
    rule = REGISTRY.get(rid)
    return rule is not None and rule.judges


# ---------------------------------------------------------------------------
# pass 1: settle from domain evidence. MUST run before any health read.
# ---------------------------------------------------------------------------

def _unsettled_instances(conn, rid):
    """Unsettled tasks, including moved occurrences with positive evidence.

    The actual local schedule day is the evidence day; rec_date remains the
    original recurrence seat after a move. Missing evidence never settles
    or judges an exception.

    presence is excluded: kit_life.force_settle refuses outcome 'done' on
    kind='presence' by design (spec A3 -- being somewhere is not a
    completion), which is also why no presence routine can be judged.
    """
    return [dict(zip(("id", "version", "rec_date", "status", "start_at",
                      "display_tz", "owning_agent"), row))
            for row in conn.execute(
                "SELECT id, version, rec_date, status, start_at, display_tz, "
                "owning_agent FROM timeblock "
                "WHERE recurrence_id=? AND rec_date IS NOT NULL "
                "AND settled_at IS NULL "
                "AND kind = 'task' "
                "ORDER BY rec_date;", (rid,))]


def evidence_settle_pass(conn, kit_life, today=None, log=None, now=None,
                         enqueue=None):
    """Settle elapsed/current-day tasks that the owning domain can witness.

    Returns (settled_count, per_routine_detail). Pure convergence: it only
    ever turns an unresolved instance into 'done'. It never marks anything
    missed and never un-settles an existing stamp, so re-running is safe and
    a race with an inbound settle just loses the CAS harmlessly.
    """
    today = today or datetime.date.today().isoformat()
    now = now or kit_life.now_utc()
    settled = 0
    detail = {}
    rules = dict(REGISTRY)
    # An explicit workout action identifies the resource on any deployment.
    # It admits positives only; judgeability still requires measured recall.
    for rid, in conn.execute("SELECT recurrence_id FROM routine_def "
                             "WHERE action_kind='workout' AND kind='task'"):
        rules.setdefault(rid, EvidenceRule(rid, EvidenceSource("workouts")))
    for rid, rule in sorted(rules.items()):
        if not rule.settles or not rule.source.available(conn):
            continue
        pending = _unsettled_instances(conn, rid)
        for row in pending:
            row["day"] = (datetime.datetime.fromisoformat(
                row["start_at"].replace("Z", "+00:00")).astimezone(
                    ZoneInfo(row["display_tz"] or "Asia/Seoul")).date().isoformat()
                if row["start_at"] else row["rec_date"])
        pending = [r for r in pending if r["day"] <= today
                   and (not r["start_at"] or r["start_at"] <= now)]
        if not pending:
            continue
        lo = min(r["day"] for r in pending)
        hi = max(r["day"] for r in pending)
        witnessed = {owner: rule.source.dates(conn, lo, hi, owning_agent=owner)
                     for owner in {r["owning_agent"] for r in pending}}
        hits = 0
        for row in pending:
            if row["day"] not in witnessed[row["owning_agent"]]:
                continue
            res = kit_life.force_settle(conn, row["id"], row["version"],
                                        SETTLED_BY)
            if res == kit_life.MutationResult.APPLIED:
                hits += 1
                settled += 1
                if enqueue is not None:
                    enqueue(conn.execute("SELECT ulid FROM timeblock WHERE id=?",
                                         (row["id"],)).fetchone()[0])
                if log is not None:
                    log(conn, "evidence_settled", rid, row["rec_date"])
        if hits:
            detail[rid] = hits
    return settled, detail


# ---------------------------------------------------------------------------
# measurement: derive `judges` from data instead of asserting it
# ---------------------------------------------------------------------------

def measure_coverage(conn, rid, source, as_of=None):
    """Recall of an evidence source over occurrences the ledger calls done.

    If the user completed an occurrence and the domain table has no row for
    that date, the table is an incomplete record of the resource -- and then
    its silence cannot be read as "did not do it".

    Returns dict(done, witnessed, recall, sample_ok, judges_ok).
    """
    as_of = as_of or datetime.date.today().isoformat()
    done_dates = {r[0] for r in conn.execute(
        "SELECT DISTINCT rec_date FROM timeblock WHERE recurrence_id=? "
        "AND rec_date IS NOT NULL AND rec_date < ? "
        "AND settled_outcome='done';", (rid, as_of)) if r[0]}
    if not done_dates:
        return {"done": 0, "witnessed": 0, "recall": None,
                "sample_ok": False, "judges_ok": False}
    witnessed = source.dates(conn, min(done_dates), max(done_dates))
    hit = len(done_dates & witnessed)
    recall = hit / float(len(done_dates))
    sample_ok = len(done_dates) >= JUDGE_SAMPLE_MIN
    return {"done": len(done_dates), "witnessed": hit, "recall": recall,
            "sample_ok": sample_ok,
            "judges_ok": sample_ok and recall >= JUDGE_RECALL_MIN}
