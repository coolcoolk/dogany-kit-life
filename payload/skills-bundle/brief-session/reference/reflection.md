# weekly retro -- cadence, staleness, reflection steps

Read at weekly session start (cadence + staleness) and when running a
reflection-mode goal (steps 1-5).

## horizon cadence (owner 2026-08-22 Q3; time axis 2026-08-18)

No single "missed N weeks -> retire" threshold. Interval = property of goal `layer`.

```
셀   = 주 (실행·커밋)          -> the week itself
short = 주 (계획 조정)          -> JUDGE every weekly retro
mid   = 월 (측정확정 + 조정)     -> weekly SHOWS, monthly JUDGES
long  = 분기 (재협상)           -> weekly does not touch
```

| layer | Step 2 compare | Step 3 judge | lifecycle guard |
|---|---|---|---|
| `short` | yes | YES, adjust plan or goal on a miss, this week | active |
| `mid` | progress line only | NO (monthly) | monthly only |
| `long` | not touched | NO (quarterly) | quarterly only |
| `event_overlay` | yes | YES (treat as short while active) | active |

- `horizon_date` = deadline, independent of cadence. never derive interval from it.
- monthly gate (last weekly retro of month) and quarterly gate (last of
  Mar/Jun/Sep/Dec): read from pending marker `workspace.mid_judge_due`,
  `workspace.quarter_renegotiate_due`, `workspace.goals.{judge, show,
  background}` (written by `weekly-review.sh`). never recompute.
- short miss: adjust this session, no streak wait.
- retire/downgrade = first-class guard. `goal-retire` = soft archive;
  achievements kept; re-ignition path always exists.

## staleness line (owner 2026-09-07)

`freshness-report` once at session start (with `goal-list`).

- only `stale` rows raisable. collapse rows sharing `owner_module` into ONE
  line; any metric of that module fresh (absent from default report) ->
  skip module entirely.
- observation + confirmed cadence in the sentence:
  "체지방 마지막 측정이 3주 전이에요. 주 1회로 보기로 하셨었죠."
- `noarrival` -> silent (usually unported lane). absent metric -> silent.
- cap ONE line per retro; spends session question budget.
- cadence confirmation = touchpoint skill's job, not here.

## Step 1 -- anchor (react-not-recall)

ONE concrete fragment + ONE question. no omnibus opener, no blank "한 주 어땠어요?".
- life agent: done task titles, appointments, episode fragments.
  "지난주에 대파크림파스타 하셨던 것 봤어요. 직접 만드셨나요?"
- health agent: session/volume actuals.
  "이번 주 세션 4/4 채우셨네요. 컨디션은 어떠셨어요?"

Step 1-1 episode slot (inside anchor): show silently-clustered episodes for
confirm/correct. "지난주 목요일에 합주 다녀오신 걸로 잡혀 있는데 맞을까요?"
corrections = delta only (reconcile-before-write). merge/split/enrich -> episode-session.

Loop closure (silent, before anchor question):
```
task-find "#rp<last_week_marker>"     # -> id title due state place
```
- executed + attribution unambiguous -> `hypothesis-evidence-add --kind behavior`.
  "지난주 넣은 등 세션 다 하셨네요. 좋아요."
- not executed -> Step 3 input (downgrade/retire). observation, not interrogation:
  "지난주 등 세션은 결국 못 하셨네요. 요일이 안 맞았을까요, 아니면 목표를 조정할까요?"
- KNOWN GAP: marker encodes WEEK only; goal back-ref sits in `note`, which
  `task-find` does not print. several goals placed same week -> attribution
  not machine-readable -> report result, SKIP evidence write. never guess.
  marker is visible to user on calendar (accepted cost).

Project collector: RETIRED (2026-10-05: the weekly never makes the owner fill fields).
The weekly never asks which project a finished task belonged to and never
proposes a project to work on. A link is made only when the owner names the
project unprompted (`project-link <task> <project>`); `link_source=routine`
rows are left alone (the routine derives them live).

## Step 2 -- compare (reflection-mode goals only)

`goal-fitgap <goal_id>`. FIT + GAP as observation. self-report vs measured
diverge -> MEASURED is evidence, still observation. mid = progress line, no verdict.
- health agent: "가슴은 주간 목표 볼륨 다 채우셨는데, 등이 목표의 60% 정도네요. 이번 주 등 세션이 하나 빠졌더라고요."
- life agent: "이번 달 목표가 주3회 요리였는데 지난주는 1회였어요. 바빴던 주였나요?"
max 1 question.

## Step 3 -- judge / revise

`goal-readiness <goal_id>`. cadence gate first (table above):
short/active event_overlay -> judge now; mid -> "이건 월말에 같이 확정할게요", skip;
long -> skip.

- met / under / over vs current rung.
- level-up ONLY when `rung_met=1 AND levelup_eligible=1 AND levelup_blocked=0`.
- lifecycle guard (`retire_candidate=1` / `downgrade_candidate=1`) -> raise one:
  - retire: `goal-retire <goal_id>`.
    "못 이루는 목표를 억지로 끌고 가기보다 지금은 내려놓고, 나중에 다시 불 지필 수 있어요."
  - downgrade: `goal-set <goal_id> --target <lower>` or `goal-focus <goal_id> backlog`.
    "현실적인 선으로 낮춰볼까요? 나중에 여유 생기면 다시 올리면 돼요."
- declined/deferred -> `goal-focus <goal_id> dormant`, say once "이건 당분간
  접어둘게요." never re-raise weekly (dormant excluded; resurfaces at its
  layer cadence).
- graduation verdicts: speak tool `proposal`. graduate -> `goal-set <id>
  --rung-state maintained` (never retire for success). next rung shape /
  lowered number = owner's, in conversation.
- misses = behavior evidence, never character verdict. goals are mutable.
- domain-priority conflict -> life-agent-led priority consult; no domain auto-wins.

## Step 4 -- placement (owner 2026-08-22 Q1; scope 2026-10-05)

STRAIGHT into task/timeblock store; visible on next week's calendar right
after retro. no buffer, no retro table.
Place only what is grounded in the owner's own week: a carried-over task
(overdue, set aside as 나중에, last retro's undone placement), a goal
adjustment the owner just agreed to, or something the owner asks for. never
propose a slot for a goal or a project on its own (owner 2026-10-05: that
fills the calendar for the data's sake).

- life agent: `task-add "<action> #rp<YYYYWww>" <due_date> "retro-placement: goal=<goal_ulid> week=<YYYY-Www>" --at HH:MM [--dur <min>] [--project <id|title>]`
- health agent: workout scheduling path; `task-add --at` timeblock as fallback.
- `--project` only when the owner named the project (same txn). exact
  id/title from `project-list`; unresolvable refuses loudly. not named ->
  omit; never ask which project it is.
- `--at` is load-bearing: timed task -> mirror `route_surface=calendar` ->
  CAL_TASK. no `--at` -> Google Tasks, NOT calendar. always propose day AND
  time. owner refuses time -> place untimed and SAY it sits in the to-do
  list. mirror push-only, appears next cycle.
- 1 confirm per placement: "다음주 목요일 저녁 8시에 등 세션 하나 넣어둘게요, 괜찮으세요?"

## Step 5 -- loop closure

Not a UI step. = next retro's Step 1 silent pull (above).

## Life agent / health agent branch

Skeleton identical. Branch only in: anchor source (life agent life fragments / health agent
session+volume), compare measure (life agent life-weaving + project goals / health agent
per-muscle volume vs target/ceiling), placement entity (life agent task/timeblock /
health agent session slot). health agent goal == life agent project (same rung mechanism).
