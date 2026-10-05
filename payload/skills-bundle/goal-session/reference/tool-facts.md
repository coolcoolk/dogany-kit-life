# goal-session: what the tool settles + other verbs

Read when the user asks why a number / order / cadence, asks for progress,
wants a goal kind flipped, or wants a metric that is not registered. Answer
from the move's own basis / `provenance` fields; never compute a number.

## settled by code (do not restate as your own rule)

- goal vs standing commitment: tool commands write `--kind`. later reversal
  = `goal-set <id> --kind <goal|rule>`.
- direction = a why, never a parent goal row (tool writes `parent_declined`).
- which strand gets a number now = ORDER (`axis-order`). later strand gets
  no number now; named as next phase (card 순서 line, else wrap line).
- proposed numbers: `personal_rate` (rate model, kit cargo; sex / training
  band from records or config first, missing sex -> `ask_sex`), three
  paces (`propose_pace`); a pace over the standing `max_deficit_cap` or
  past the muscle floor (loss <= 0.7% of body weight a week AND intake >=
  the latest InBody `bmr_kcal`; no BMR -> said unverified) is never made;
  protein is calculated after a pace is chosen, from its phase and the latest
  lean mass (or bodyweight); it is absent from the pace options. metric with no rate
  model -> three conservative options, said so. the lift strand
  (`lift_capacity_kg`, e1RM) gets no number and no turn: a per-session
  progression, stated in one line. definition first -> the order answer
  holds the muscle strand (range) at once.
- number shape: `metric_judged` (mean of last 4 readings, person's noise,
  90 % band -> "변화 없음 범위"; goal-progress reads the same; a muscle
  reading that moved with body water weighs half once 3 water readings
  exist). the user hears "요즘", never the reading count or narrowing.
  `latest_check` / move `reading_notes`: the newest reading judged alone
  (off its trend, or "수분 영향 가능"); its line rides `readback` once. goal =
  deadline + range of that mean; deadline stretched when pace sits inside
  noise (cap 52 weeks). every proposed number carries `provenance`
  (records / user / rate_model / noise_model / calendar).
- strand on a measure the health pack computes from the program (source
  `program`, e.g. unilateral_share_sets): no kit reading, no baseline ask,
  no number here; unless `@care` it is a practice (`propose_practice`).
- which practice menu a strand gets: `_PRACTICE_SETS` (kit cargo, data):
  its linked measure, else its qualifier, else its own clause of the
  direction, else its bare name. one set per strand per direction, and no
  pick key shared across strands; no fit -> one open ask. the picks the
  wrap hands the pack (`handoff.practice`) are that set's
  (`power|mixed_circuit|conditioning` or `breath_trunk|mobility_flow|whole_body`;
  `unilateral|core` = legacy answers, still valid on a re-note).
- a strand's clause / qualifier / picture live in move state `strand`
  (`{axis_key: {clause, qualifier, clean, picture[, qlabel]}}`); a strand
  stored without them derives clause + qualifier from the direction on read.
- the framing's reason is `_fat_basis` (the same comparison the order is
  chosen by), exposed on the pace move as `frame_basis` with its numbers.
- a direction stated before this consult round is confirmed first
  (`confirm_direction`); the confirmation is move state
  (`why_confirmed`), the round is `round.start/seen` (6 h gap = new round).
- every argv in a move is a `database/kit_life.sh` verb (`cmd_runner`).
- measuring cadence: `_CADENCE_GUIDED` (kit cargo). health body metrics:
  filled by the order answer / the pace pick and stated (weekday is the
  program's); other domains via `ask_feasibility`.
- calorie numbers: health pack calculators + safety gates (e.g. pack_health
  G6-b), in the pack's own flow, after this one. safety answer given here =
  the one the pack reads (`constraint-list --class safety`).

## other verbs

```bash
database/kit_life.sh goal-interview <goal_id> [--revise <field>] --json   # per-goal walk (gate for rebuild-commit)
database/kit_life.sh goal-set <goal_id> --metric .. --target .. --horizon .. --kind ..
database/kit_life.sh goal-set <goal_id> --metric-pending                  # words-born goal, review date only
database/kit_life.sh goal-progress <goal_id> [--json]                     # goal readout
database/kit_life.sh rule-adherence <goal_id> [--json]                    # standing-commitment readout
database/kit_life.sh axis-list --why-id N --json                          # user's strands for one direction
database/kit_life.sh metric-judged <metric> [--json]                      # noise-aware read of one body metric
database/kit_life.sh metric-del --date YYYY-MM-DD [--source inbody] [--owning-agent A]  # take back one dated record set
```

- the user takes back an InBody ("그 인바디 지워줘", a wrong photo): `metric-del
  --date <its date> --source inbody`. It prints every row removed (`ROW`),
  rows of that date it kept (`KEPT`, e.g. a tape measure), profile keys put
  back (`CONFIG weight_kg 79 -> 80`: a profile weight copied from that
  record falls back to the newest remaining reading, else unset) and any
  goal whose baseline was that record (`GOAL-BASELINE`, the goal itself
  unchanged). Say what was removed in plain words. Nothing matching -> exit
  1 naming what IS on that date; say that, never retry another date on your
  own. No `--source` = every row of the date.

- metric not in `goal_metric_source` cannot be linked. registering = one row
  (metric_token, source_table, source_expr, domain); no code edit.
- goal labels positive-form (`goal-add` / `goal-set` refuse negation shapes).
- met goal -> `maintained`; graduation / step-down at weekly review
  (`goal-graduation`).
