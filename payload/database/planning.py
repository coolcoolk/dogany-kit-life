"""Additive canonical planning API (076), deliberately not wired to legacy CLI.

No default connection, file IO, live imports, clock reads or automatic closure.
Callers supply a foreign-key-enabled connection and explicit effective instants.
Each write is a savepoint: compose several writes inside the caller transaction.
Scores are pure and individual per target; no thesis score or goal composite.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from math import isfinite
import sqlite3
from typing import Literal, Sequence
from zoneinfo import ZoneInfo

Policy = Literal['first_touch', 'window_average_in_band']
Result = Literal['kept', 'broken', 'not_logged', 'not_applicable']
State = Literal['open', 'doing', 'waiting']


def instant(value: str) -> datetime:
    parsed = datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc)
    if parsed.strftime('%Y-%m-%dT%H:%M:%SZ') != value:
        raise ValueError('canonical UTC-Z instant required')
    return parsed


def local_date(value: str) -> date:
    parsed = date.fromisoformat(value)
    if parsed.isoformat() != value:
        raise ValueError('ISO local date required')
    return parsed


def finite(value: float | None) -> None:
    if value is not None and not isfinite(value):
        raise ValueError('finite number required')


@dataclass(frozen=True)
class JudgingPolicy:
    kind: Policy
    window_days: int | None = None
    origin: Literal['metric_default', 'owner'] = 'owner'

    def __post_init__(self):
        if self.kind not in ('first_touch', 'window_average_in_band'):
            raise ValueError('unsupported judging policy')
        if self.origin not in ('owner', 'metric_default'):
            raise ValueError('invalid policy origin')
        if self.kind == 'window_average_in_band':
            if not isinstance(self.window_days, int) or self.window_days < 1:
                raise ValueError('window length required')
        elif self.window_days is not None:
            raise ValueError('first touch has no averaging window')


def metric_default(metric_class: Literal['exact', 'inbody', 'weight']) -> JudgingPolicy:
    """Explicit class supplied by caller; never guess class from a metric token.

    InBody defaults to 14 days (owner may choose 14-28 or another window);
    weight uses a 7-day average. These are defaults, not non-overridable rules.
    """
    days = {'exact': None, 'inbody': 14, 'weight': 7}[metric_class]
    return JudgingPolicy('first_touch' if days is None else 'window_average_in_band',
                         days, 'metric_default')


@dataclass(frozen=True)
class Target:
    baseline: float | None
    low: float | None
    high: float | None
    policy: JudgingPolicy
    target_id: int | None = None
    version: int = 1

    def __post_init__(self):
        for value in (self.baseline, self.low, self.high):
            finite(value)
        if self.low is None and self.high is None:
            raise ValueError('endpoint required')
        if self.low is not None and self.high is not None and self.low > self.high:
            raise ValueError('reversed band')
        if self.policy.kind == 'window_average_in_band' and (self.low is None or self.high is None):
            raise ValueError('window judging requires a band')

    def contains(self, value: float) -> bool:
        return (self.low is None or value >= self.low) and (self.high is None or value <= self.high)


@dataclass(frozen=True)
class GoalScore:
    raw_pct: float | None
    display_pct: float | None
    reached: bool | None
    in_band: bool | None
    regression_pp: float | None
    overshoot_pp: float | None
    reason: str | None
    target_id: int | None
    version: int
    evidence_date: str | None
    pace_per_day: float | None = None


def goal_percent(target: Target, current: float | None, *, evidence_date: str | None,
                 fresh: bool = True, units_match: bool = True,
                 window_complete: bool = False, first_touched: bool = False,
                 as_of: str | None = None, deadline: str | None = None) -> GoalScore:
    """current is the scoped actual value, or declared full-window average.

    A window policy is unmeasurable until its aggregate is complete. A prior
    first-touch attainment persists independently from today's band membership.
    Deadline controls pace only; this function cannot mutate lifecycle.
    """
    finite(current)
    reason = ('missing_baseline' if target.baseline is None else
              'missing_evidence' if current is None or evidence_date is None else
              'stale_evidence' if not fresh else 'unit_conversion_required' if not units_match else
              'incomplete_window' if target.policy.kind == 'window_average_in_band' and not window_complete else None)
    if reason:
        return GoalScore(None, None, None, None, None, None, reason,
                         target.target_id, target.version, evidence_date)
    local_date(evidence_date)
    baseline = target.baseline
    inside = target.contains(current)
    reached = inside or (target.policy.kind == 'first_touch' and first_touched)
    if target.contains(baseline):
        return GoalScore(100., 100., reached, inside, 0., 0., 'already_at_target',
                         target.target_id, target.version, evidence_date)
    endpoint = target.low if target.low is not None and baseline < target.low else target.high
    raw = 100. * (current - baseline) / (endpoint - baseline)
    pace = None
    if deadline is not None and as_of is not None:
        remaining = (local_date(deadline) - local_date(as_of)).days
        if remaining > 0:
            pace = (endpoint - current) / remaining
    return GoalScore(raw, max(0., min(100., raw)), reached, inside,
                     max(0., -raw), max(0., raw - 100.), None,
                     target.target_id, target.version, evidence_date, pace)


@dataclass(frozen=True)
class Observation:
    local_date: str
    value: float


def judge_observations(target: Target, observations: Sequence[Observation], *, as_of: str,
                       coverage_start: str, fresh_after: str) -> GoalScore:
    """Caller supplies scoped, unit-normalized actual evidence and coverage.

    coverage_start is the earliest date whose collection is known complete,
    not inferred from presence of a reading. No missing observation becomes 0.
    """
    end = local_date(as_of)
    rows = sorted((o for o in observations if local_date(o.local_date) <= end), key=lambda o: o.local_date)
    for row in rows:
        finite(row.value)
    if not rows:
        return goal_percent(target, None, evidence_date=None)
    fresh = rows[-1].local_date >= fresh_after
    if target.policy.kind == 'first_touch':
        return goal_percent(target, rows[-1].value, evidence_date=rows[-1].local_date,
                            fresh=fresh, first_touched=any(target.contains(o.value) for o in rows))
    start = end - timedelta(days=target.policy.window_days - 1)
    window = [o.value for o in rows if local_date(o.local_date) >= start]
    return goal_percent(target, sum(window)/len(window) if window else None,
                        evidence_date=rows[-1].local_date, fresh=fresh,
                        window_complete=local_date(coverage_start) <= start)


@dataclass(frozen=True)
class RuleScore:
    unit: str
    kept: int
    broken: int
    not_logged: int
    not_applicable: int
    eligible: int
    headline_pct: float | None
    logging_rate_pct: float | None
    observed_pct: float | None
    upper_bound_pct: float | None


def rule_percent(results: Sequence[Result], *, unit: Literal['day', 'week']) -> RuleScore:
    if unit not in ('day', 'week') or any(r not in ('kept','broken','not_logged','not_applicable') for r in results):
        raise ValueError('native day/week unit and typed results required')
    k, b, n, x = (results.count(r) for r in ('kept','broken','not_logged','not_applicable'))
    eligible, logged = k+b+n, k+b
    return RuleScore(unit, k, b, n, x, eligible, 100*k/eligible if eligible else None,
                     100*logged/eligible if eligible else None, 100*k/logged if logged else None,
                     100*(k+n)/eligible if eligible else None)


def judge_rule_value(value: float | None, *, lo: float | None, hi: float | None,
                     applicable: bool = True, due: bool = True, fresh: bool = True) -> Result:
    """Judge a native-period aggregate; absent/stale logs are never a breach."""
    for number in (value,lo,hi): finite(number)
    if not applicable or not due: return 'not_applicable'
    if value is None or not fresh: return 'not_logged'
    if lo is None and hi is None: raise ValueError('numeric rule needs a bound')
    if lo is not None and hi is not None and lo > hi: raise ValueError('reversed bounds')
    return 'kept' if (lo is None or value >= lo) and (hi is None or value <= hi) else 'broken'


@dataclass(frozen=True)
class TaskResult:
    task_id: int
    state: Literal['done','open','doing','waiting','cancelled','removed']
    weight: float | None = None


@dataclass(frozen=True)
class ProjectScore:
    pct: float | None
    completed: int
    committed: int
    cancelled: int
    removed: int
    grading: str


def project_execution_percent(tasks: Sequence[TaskResult], *, grading: Literal['count','weighted'] = 'count') -> ProjectScore:
    if grading not in ('count','weighted') or len({t.task_id for t in tasks}) != len(tasks):
        raise ValueError('invalid grading or duplicate task')
    for t in tasks:
        if t.state not in ('done','open','doing','waiting','cancelled','removed'):
            raise ValueError('invalid task result state')
        finite(t.weight)
        if grading == 'weighted' and (t.weight is None or t.weight <= 0):
            raise ValueError('weighted project requires positive weights for every result')
    # The snapshot denominator retains cancelled/removed commitments. A future
    # scope may omit them; an old scope is never recalculated against that set.
    weights = [1. if grading == 'count' else t.weight for t in tasks]
    total = sum(weights)
    done = sum(w for w, t in zip(weights, tasks) if t.state == 'done')
    return ProjectScore(100*done/total if total else None,
                        sum(t.state == 'done' for t in tasks), len(tasks),
                        sum(t.state == 'cancelled' for t in tasks),
                        sum(t.state == 'removed' for t in tasks), grading)


@dataclass(frozen=True)
class Effect:
    percentage_points: float | None
    start_date: str | None
    end_date: str | None
    target_id: int | None
    version: int
    label: str = 'observed change during project; not causal attribution'
    reason: str | None = None


def project_effect(start: GoalScore, end: GoalScore) -> Effect:
    if start.target_id is None or start.target_id != end.target_id or start.version != end.version:
        raise ValueError('target revision: compare separate same-version segments')
    if start.evidence_date and end.evidence_date and start.evidence_date > end.evidence_date:
        raise ValueError('effect evidence dates reversed')
    change = None if start.display_pct is None or end.display_pct is None else end.display_pct-start.display_pct
    return Effect(change, start.evidence_date, end.evidence_date, start.target_id,
                  start.version, reason='missing_score' if change is None else None)


def task_state(execution_state: State, settled_outcome: str | None) -> str:
    return settled_outcome if settled_outcome is not None else execution_state


def split_proposed(estimated_minutes: float | None, *, threshold: float = 60,
                   owner_override: bool = False) -> bool:
    finite(estimated_minutes)
    finite(threshold)
    if threshold <= 0 or (estimated_minutes is not None and estimated_minutes <= 0):
        raise ValueError('positive estimate and threshold required')
    return estimated_minutes is not None and estimated_minutes > threshold and not owner_override


@dataclass(frozen=True)
class ExecutionEvidence:
    block_id: int
    logged_minutes: float | None = None
    confirmed_start: str | None = None
    confirmed_end: str | None = None


def actual_minutes(sessions: Sequence[ExecutionEvidence]) -> dict:
    """Only explicit execution evidence; scheduled task intervals never enter.

    Logged actual wins over confirmed actual endpoints. Duplicate identical
    evidence is a retry; conflicting evidence is rejected instead of summed.
    """
    unique = {}
    for row in sessions:
        if row.block_id in unique and unique[row.block_id] != row:
            raise ValueError('conflicting execution evidence')
        unique[row.block_id] = row
    values = []
    for row in unique.values():
        finite(row.logged_minutes)
        if row.logged_minutes is not None:
            value = row.logged_minutes
        elif row.confirmed_start is not None and row.confirmed_end is not None:
            value = (instant(row.confirmed_end)-instant(row.confirmed_start)).total_seconds()/60
        else:
            continue
        if value < 0:
            raise ValueError('negative actual duration')
        values.append(value)
    return {'actual_minutes': sum(values) if values else None, 'logged_sessions': len(values),
            'sessions': len(unique), 'coverage': len(values)/len(unique) if unique else None}


class PlanningService:
    """Typed writes only. Legacy projection/read paths remain untouched."""
    def __init__(self, conn: sqlite3.Connection):
        if conn.execute('PRAGMA foreign_keys').fetchone()[0] != 1:
            raise ValueError('planning requires foreign_keys=ON')
        self.conn = conn

    @contextmanager
    def atomic(self):
        self.conn.execute('SAVEPOINT planning_write')
        try:
            yield
            self.conn.execute('RELEASE planning_write')
        except Exception:
            self.conn.execute('ROLLBACK TO planning_write')
            self.conn.execute('RELEASE planning_write')
            raise

    def _insert(self, table: str, values: dict) -> int:
        # Identifiers originate only in this module, values always bind.
        sql = f"INSERT INTO {table} ({','.join(values)}) VALUES ({','.join('?' for _ in values)})"
        return self.conn.execute(sql, tuple(values.values())).lastrowid

    def _row(self, table: str, row_id: int) -> dict:
        cur = self.conn.execute(f'SELECT * FROM {table} WHERE id=?', (row_id,))
        row = cur.fetchone()
        if row is None:
            raise ValueError(f'{table} not found')
        return dict(zip((c[0] for c in cur.description), row))

    def create_goal(self, *, ulid: str, title: str, at: str,
                    deadline: str | None = None, domain: str | None = None,
                    managing_agent: str | None = None) -> int:
        instant(at)
        if deadline:
            local_date(deadline)
        if not title.strip():
            raise ValueError('goal title required')
        with self.atomic():
            return self._insert('goal', dict(ulid=ulid, title=title, created_at=at,
                horizon_kind='bounded' if deadline else 'thesis_bound', horizon_date=deadline,
                lifecycle='active', stage_token='active', domain=domain, managing_agent=managing_agent))

    def close_goal(self, goal_id: int, *, lifecycle: Literal['achieved','failed','stopped','archived'],
                   reason: str, at: str) -> None:
        instant(at)
        if lifecycle not in ('achieved','failed','stopped','archived') or not reason.strip():
            raise ValueError('explicit closure lifecycle and reason required')
        with self.atomic():
            self._row('goal', goal_id)
            # Stage is the old coarse visibility roster (done means closed,
            # not achieved). Keep it coherent without changing any reader;
            # the explicit lifecycle retains archived/failed/achieved identity.
            stage = 'abandoned' if lifecycle == 'stopped' else 'done'
            self.conn.execute("UPDATE goal SET lifecycle=?, closed_at=?, close_reason=?, stage_token=?, focus_state='dormant' WHERE id=?",
                              (lifecycle, at, reason, stage, goal_id))

    def create_target(self, goal_id: int, *, series_key: str, metric: str, target: Target,
                      baseline_as_of: str | None, at: str, metric_class: str = 'exact',
                      entity_id: int | None = None, domain: str | None = None,
                      unit: str | None = None) -> int:
        instant(at)
        if baseline_as_of:
            local_date(baseline_as_of)
        if not series_key.strip() or (entity_id is None and not domain):
            raise ValueError('target series and evidence scope required')
        if target.policy.origin == 'metric_default' and target.policy != metric_default(metric_class):
            raise ValueError('default policy must match its declared metric class')
        with self.atomic():
            return self._insert('goal_target', dict(goal_id=goal_id, metric=metric,
                series_key=series_key, baseline_value=target.baseline, baseline_as_of=baseline_as_of,
                target_low=target.low, target_high=target.high, valid_from=at,
                judging_policy=target.policy.kind, window_days=target.policy.window_days,
                metric_class=metric_class, policy_origin=target.policy.origin,
                evidence_entity_id=entity_id, evidence_domain=domain, unit_token=unit,
                rung_state='confirmed'))

    def revise_target(self, target_id: int, *, target: Target, baseline_as_of: str | None, at: str,
                      metric_class: str | None = None) -> int:
        instant(at)
        if baseline_as_of:
            local_date(baseline_as_of)
        with self.atomic():
            old = self._row('goal_target', target_id)
            if old['valid_until'] is not None or not old['series_key']:
                raise ValueError('only current typed target can be revised; legacy needs reconciliation')
            if instant(at) <= instant(old['valid_from']):
                raise ValueError('successor must start after predecessor')
            chosen_class = metric_class or old['metric_class']
            if target.policy.origin == 'metric_default' and target.policy != metric_default(chosen_class):
                raise ValueError('default policy must match its declared metric class')
            self.conn.execute('UPDATE goal_target SET valid_until=? WHERE id=?', (at,target_id))
            old.pop('id')
            old.update(baseline_value=target.baseline, baseline_as_of=baseline_as_of,
                       target_low=target.low, target_high=target.high, judging_policy=target.policy.kind,
                       window_days=target.policy.window_days, policy_origin=target.policy.origin,
                       metric_class=chosen_class, version=old['version']+1, supersedes=target_id, valid_from=at, valid_until=None)
            return self._insert('goal_target', old)

    def target_as_of(self, goal_id: int, series_key: str, at: str) -> dict | None:
        instant(at)
        row = self.conn.execute('SELECT id FROM goal_target WHERE goal_id=? AND series_key=? '
            'AND valid_from<=? AND (valid_until IS NULL OR ?<valid_until)',
            (goal_id,series_key,at,at)).fetchone()
        return self._row('goal_target',row[0]) if row else None

    def create_project(self, *, ulid: str, title: str, goal_id: int,
                       start_date: str, end_date: str, at: str,
                       grading: Literal['count','weighted'] = 'count') -> int:
        instant(at)
        if goal_id is None or local_date(end_date) < local_date(start_date):
            raise ValueError('active project needs goal and ordered period')
        with self.atomic():
            return self._insert('projects', dict(ulid=ulid, title=title, goal_id=goal_id,
                start_date=start_date,end_date=end_date,created_at=at,
                planning_state='active', execution_grading=grading))

    def close_project(self, project_id: int, *, reason: Literal['goal_reached','period_over','dropped'], at: str) -> None:
        instant(at)
        with self.atomic():
            self._row('projects',project_id)
            self.conn.execute("UPDATE projects SET planning_state='closed', closed_at=?, close_reason=? WHERE id=?",
                              (at,reason,project_id))

    def create_rule(self, *, ulid: str, goal_id: int, title: str,
                    period: Literal['day','week'], timezone_name: str, at: str,
                    kind: Literal['keep','limit','window'] = 'keep', metric: str | None = None,
                    lo: float | None = None, hi: float | None = None, week_start: int | None = None,
                    calendar_policy: Literal['every_period','weekdays'] = 'every_period') -> int:
        instant(at)
        ZoneInfo(timezone_name)
        finite(lo); finite(hi)
        if period not in ('day','week') or (period == 'week' and week_start not in range(7)):
            raise ValueError('native unit and weekly start (Monday=0) required')
        if period == 'day' and week_start is not None:
            raise ValueError('daily rule has no week start')
        if period == 'week' and calendar_policy != 'every_period':
            raise ValueError('weekdays applicability only applies to daily rules')
        if lo is not None and hi is not None and lo > hi:
            raise ValueError('reversed rule bounds')
        with self.atomic():
            return self._insert('commitment', dict(ulid=ulid,goal_id=goal_id,title=title,
                period=period,timezone=timezone_name,valid_from=at,created_at=at,kind=kind,metric=metric,
                lo=lo,hi=hi,week_start=week_start,calendar_policy=calendar_policy))

    def revise_rule(self, rule_id: int, *, ulid: str, at: str,
                    lo: float | None, hi: float | None,
                    status: Literal['active','paused','retired'] = 'active') -> int:
        instant(at)
        finite(lo); finite(hi)
        if lo is not None and hi is not None and lo > hi:
            raise ValueError('reversed bounds')
        with self.atomic():
            old = self._row('commitment',rule_id)
            if old['valid_until'] is not None or not old['timezone'] or instant(at) <= instant(old['valid_from']):
                raise ValueError('only a current typed rule can have a later successor')
            self.conn.execute('UPDATE commitment SET valid_until=? WHERE id=?',(at,rule_id))
            old.pop('id')
            old.update(ulid=ulid,lo=lo,hi=hi,status=status,version=old['version']+1,supersedes=rule_id,
                       valid_from=at,valid_until=None,created_at=at)
            return self._insert('commitment',old)

    def record_rule_result(self, rule_id: int, *, on: str, result: Result,
                           source_ref: str, evaluator_version: str, at: str) -> None:
        instant(at)
        day = local_date(on)
        with self.atomic():
            rule = self._row('commitment',rule_id)
            tz = ZoneInfo(rule['timezone'])
            start = datetime.combine(day, datetime.min.time(),tzinfo=tz)
            # Day version is the version at local midnight. Weekly version is
            # the one at week start: a midweek revision takes over next week.
            if start < instant(rule['valid_from']) or (rule['valid_until'] and start >= instant(rule['valid_until'])):
                raise ValueError('wrong version for local period start')
            if rule['period']=='week' and day.weekday()!=rule['week_start']:
                raise ValueError('weekly evidence must use declared week start')
            if rule['period'] not in ('day','week'):
                raise ValueError('unsupported native unit')
            if (rule['status'] != 'active' or (rule['calendar_policy']=='weekdays' and day.weekday()>=5)) and result!='not_applicable':
                raise ValueError('paused/inapplicable period is excluded')
            duration = timedelta(days=7 if rule['period']=='week' else 1)
            if instant(at) < start+duration and result!='not_applicable':
                raise ValueError('period not yet due')
            values=(rule_id,on,result,source_ref,evaluator_version,at)
            old=self.conn.execute('SELECT * FROM commitment_evidence WHERE commitment_id=? AND local_date=?',(rule_id,on)).fetchone()
            if old:
                if tuple(old)[2:5] != values[2:5]:
                    raise ValueError('conflicting evidence retry')
                return
            self.conn.execute('INSERT INTO commitment_evidence VALUES (?,?,?,?,?,?)', values)

    def rule_report(self, latest_rule_id: int, *, start: str, end: str, as_of: str) -> RuleScore:
        """Fill unlogged eligible native periods; use version at period start.

        Caller chooses a series via its latest version. Pauses are successor
        versions, so today's status never changes an earlier period's result.
        """
        now=instant(as_of)
        first,last=local_date(start),local_date(end)
        if last < first: raise ValueError('reversed reporting dates')
        versions=[]
        row=self._row('commitment',latest_rule_id)
        while True:
            versions.append(row)
            if row['supersedes'] is None: break
            row=self._row('commitment',row['supersedes'])
        latest=versions[0]
        if latest['period'] not in ('day','week') or not latest['timezone']:
            raise ValueError('legacy rule needs typed native calendar policy')
        tz=ZoneInfo(latest['timezone'])
        if any(v['period']!=latest['period'] or v['timezone']!=latest['timezone'] or v['week_start']!=latest['week_start'] for v in versions):
            raise ValueError('calendar revisions require separate report segments')
        results=[]
        day=first
        while day<=last:
            if latest['period']=='week' and day.weekday()!=latest['week_start']:
                day+=timedelta(days=1)
                continue
            boundary=datetime.combine(day,datetime.min.time(),tzinfo=tz)
            version=next((v for v in versions if instant(v['valid_from'])<=boundary and
                          (v['valid_until'] is None or boundary<instant(v['valid_until']))),None)
            duration=timedelta(days=7 if latest['period']=='week' else 1)
            if version is None or boundary+duration>now or version['status']!='active' or (version['calendar_policy']=='weekdays' and day.weekday()>=5):
                results.append('not_applicable')
            else:
                evidence=self.conn.execute('SELECT result FROM commitment_evidence WHERE commitment_id=? AND local_date=?',(version['id'],day.isoformat())).fetchone()
                results.append(evidence[0] if evidence else 'not_logged')
            day+=duration
        return rule_percent(results,unit=latest['period'])

    def create_task(self, block_id: int, *, result: str, estimated_minutes: float | None = None,
                    origin: Literal['owner','history','agent'] | None = None, at: str | None = None) -> int:
        finite(estimated_minutes)
        if at:
            instant(at)
        with self.atomic():
            return self._insert('task',dict(block_id=block_id,checkable_result=result,
                estimated_minutes=estimated_minutes,estimate_origin=origin,estimated_at=at))

    def revise_estimate(self, task_id: int, *, minutes: float | None,
                        origin: Literal['owner','history','agent'] | None, at: str | None) -> None:
        finite(minutes)
        if at: instant(at)
        with self.atomic():
            cur=self.conn.execute('UPDATE task SET estimated_minutes=?, estimate_origin=?, estimated_at=?, estimate_version=estimate_version+1 WHERE block_id=?',
                                  (minutes,origin,at,task_id))
            if cur.rowcount!=1: raise ValueError('task not found')

    def link_goal_thesis(self, goal_id: int, thesis_id: int) -> None:
        with self.atomic():
            self.conn.execute('INSERT INTO goal_thesis(goal_id,thesis_id) VALUES (?,?) ON CONFLICT DO NOTHING',(goal_id,thesis_id))

    def set_task_state(self, task_id: int, *, state: State, reason: str | None = None,
                       since: str | None = None, review_after: str | None = None,
                       waiting_for: str | None = None) -> None:
        if since: instant(since)
        if review_after: instant(review_after)
        with self.atomic():
            cur=self.conn.execute('UPDATE task SET exec_state=?, waiting_reason=?, waiting_since=?, review_after=?, waiting_for=? WHERE block_id=?',
                              (state,reason,since,review_after,waiting_for,task_id))
            if cur.rowcount != 1: raise ValueError('task not found')

    def link_execution(self, task_id: int, block_id: int) -> None:
        with self.atomic():
            old=self.conn.execute('SELECT task_id FROM task_execution WHERE block_id=?',(block_id,)).fetchone()
            if old and old[0]!=task_id: raise ValueError('session belongs to another task')
            if not old: self._insert('task_execution',dict(task_id=task_id,block_id=block_id))

    def task_status(self, task_id: int) -> str:
        row=self.conn.execute('SELECT t.exec_state,b.settled_outcome FROM task t JOIN block b ON b.id=t.block_id WHERE t.block_id=?',(task_id,)).fetchone()
        if row is None: raise ValueError('task not found')
        return task_state(row[0],row[1])

    def snapshot_project(self, project_id: int, tasks: Sequence[TaskResult], *, at: str) -> int:
        instant(at)
        with self.atomic():
            project=self._row('projects',project_id)
            if project['planning_state'] is None:
                raise ValueError('legacy project needs planning before scoring')
            # Membership/removal is declared; completion and waiting come from
            # canonical block/task evidence, never a caller's guessed grade.
            captured=[]
            for task in tasks:
                state=self.task_status(task.task_id)
                if state=='abandoned': state='cancelled'
                if task.state=='removed': state='removed'
                captured.append(TaskResult(task.task_id,state,task.weight))
            tasks=captured
            project_execution_percent(tasks, grading=project['execution_grading'])
            revision=self.conn.execute('SELECT COALESCE(MAX(revision),0)+1 FROM project_scope WHERE project_id=?',(project_id,)).fetchone()[0]
            scope=self._insert('project_scope',dict(project_id=project_id,revision=revision,captured_at=at,
                goal_id=project['goal_id'],grading=project['execution_grading']))
            for task in tasks:
                self._insert('project_scope_task',dict(scope_id=scope,task_id=task.task_id,result_state=task.state,weight=task.weight))
            self.conn.execute('UPDATE project_scope SET sealed=1 WHERE id=?',(scope,))
            return scope

    def project_score(self, scope_id: int) -> ProjectScore:
        scope=self._row('project_scope',scope_id)
        if not scope['sealed']: raise ValueError('scope snapshot is not sealed')
        tasks=[TaskResult(*r) for r in self.conn.execute('SELECT task_id,result_state,weight FROM project_scope_task WHERE scope_id=?',(scope_id,))]
        return project_execution_percent(tasks,grading=scope['grading'])

    def create_thesis(self, *, ulid: str, text: str, at: str, scope: str,
                      domain: str | None = None, managing_agent: str | None = None,
                      label: str | None = None, promoted_from_insight: int | None = None,
                      spectrum: str = 'casual', confidence: float | None = None,
                      provenance: str | None = None) -> int:
        instant(at)
        finite(confidence)
        with self.atomic():
            return self._insert('thesis',dict(ulid=ulid,text=text,created_at=at,updated_at=at,
                scope=scope,domain=domain,managing_agent=managing_agent,label=label,
                promoted_from_insight=promoted_from_insight,spectrum=spectrum,
                confidence=confidence,provenance=provenance))
