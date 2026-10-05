# brief-session -- verb signatures

All via `$PROJECT_ROOT/database/kit_life.sh`. No raw SQL. Output shapes below
match code (kit_life.py) as of 2026-09-26; code wins on any drift.

## weekly: goal engine

```
goal-list         [--owning-agent A] [--domain D] [--focus S] [--layer L]
                  [--status S] [--kind goal|rule] [--json]
                  -> TAB: id ulid layer domain focus status rung metric
                     target dir priority title kind
                  col 3 layer = horizon (long | mid | short | event_overlay)
                     = the field the cadence gate reads.
                  col 5 focus_state (focus | backlog | dormant).
goal-fitgap       <goal_id> [--json]
                  -> baseline/target/current/as_of ; FIT window_weeks/rate/
                     actual/expected ; GAP delta/expected_now/ahead/days_left
goal-readiness    <goal_id> [--json]
                  -> rung_met, levelup_eligible, levelup_blocked,
                     retire_candidate, downgrade_candidate + per-rule verdicts
goal-graduation   [--domain D] [--owning-agent A] [--json]
                  -> ONE verdict per short/maintained commitment:
                     graduate | stepdown | drift | hold | unevaluated,
                     + ready `proposal` sentence + next_cmd.
goal-focus        <goal_id> <focus|backlog|dormant>
                  (focus over cap -> FOCUSFULL, exit 3: demote one, retry)
goal-retire       <goal_id> [--reason R]      soft archive, never delete
goal-set          <goal_id> [--target v] [--rung-state S] ...
```

## weekly: hypothesis / freshness

```
hypothesis-add    --text T --subject-kind goal [--subject-id id]
                  [--domain D] [--sensitivity normal|sensitive]
hypothesis-evidence-add <hypothesis_id>
                  --kind behavior|verbal_affirm|verbal_deny [--record-id id]
freshness-report  [--all]
                  -> TAB: metric flag cadence_h last_observed age_h
                     owner_module declared_by
                  flag = stale | noarrival. fresh rows omitted (--all shows).
                  metric with no declared cadence = absent (fail-closed).
```

## both lanes: tasks / projects

```
task-add          <title> [due_date] [note] [--at HH:MM]
                  [--dur <min> | --end HH:MM] [--project <id|title>]
task-find         [date|all|keyword]
                  -> TAB: id title due state place   (state = done | todo)
                  keyword = title LIKE %keyword%. no note column.
task-done-between <from> <to>
task-overdue      [--owning-agent A]   same columns, oldest due first
task-done <id> / task-reschedule <id> <YYYY-MM-DD> [--at HH:MM] /
task-undate <id> / task-archive <id>
                  task-undate = clear due date -> dateless open task.
                  routine occurrence refuses undate (keeps its date).
project-list      [--status S] [--json]  -> TAB: id title status start end
tasks-by-project  [--open] [--since <YYYY-MM-DD>] [--project <id|title>]
                  -> TAB: project_id project_title task_id task_title
                     status link_source   (direct | routine)
                  ALWAYS pass --since in a retro (else full history).
                  rows with no project are absent (JOIN surface).
project-link      <task|event id|ulid> <project id|title>   re-link REPLACES
project-unlink    <task|event id|ulid>                       idempotent
```

## daily: what the stages read

```
task-find <YYYY-MM-DD> [--owning-agent A]
                  today's rows; still `todo` at retro time = went overdue today.
appt-find <date>  -> TAB: id start_local title location end_local people
                     retro_line  (col 7; empty = never asked)
                  appointment AND presence (routine day-block 회사 included).
event-window <from_utc> <to_utc> [kind]
                  -> TAB: ulid kind title start end status notify_policy
                     notify_lead_min place people retro_line
routine-for-event <event id|ulid>
                  -> TAB: id ulid title cadence schedule_kind status
                     retro_skip (col 7, 0|1). verb errors = one-off event.
prep-list <event id|ulid>
                  -> TAB: display_ulid display_name done required_count
                  already collapsed to shallowest satisfying unit.
routine-prep-list <routine>  -> TAB: item_ulid item_name
carry-list [has|has_not|unknown] [--all]
```

## daily: what the stages write

```
event-retro-set <event id|ulid> <line>   THE retro line (latest, not transcript)
routine-retro-skip <routine> <on|off>    permanent question skip
prep-set <event> <display item> <0|1>
```

## module sections (no DB)

```
retro-sections    --lane daily|weekly [--mode line|section] [--json]
                  -> blocks from service/<ns>/retro-sections.tsv;
                     section mode = max 3 lines per module; failing module
                     skipped; empty = nothing to show.
```
