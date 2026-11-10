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
task-overdue      [--before YYYY-MM-DD] [--owning-agent A]
                  same columns, oldest due first; daily retro MUST pass D.
                  omitted --before retains the current-day cutoff.
task-postpone <id> "<owner time text>" (next clock after now; explicit day wins) /
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

`retro-start "<action or full bridge reply context>"` is read-only JSON:
`READY` carries `retro_date`, `tomorrow`, `commands`, `tomorrow_commands`;
`ASK_DATE` requires a date question before reads. Dated callbacks are
`opt:retro:daily:YYYY-MM-DD`. Typed starts must include the quoted reply
card. The prompt hook supplies the same binding automatically. Every
`<today>` below and in SKILL.md is that session date, never the tap's day.

```
task-find <YYYY-MM-DD> [--owning-agent A]
                  today's rows; still `todo` at retro time = went overdue today.
appt-find <date>  -> TAB: id start_local title location end_local people
                     retro_line  (col 7; empty = never asked)
                  appointment AND presence (routine day-block 회사 included).
appt-find <date> [date_to] --retro
                  SAME columns; daily question walk and opening card source.
                  Excludes visits whose end is still ahead, including a
                  covering/contiguous same-place stay. Unanswered overnight
                  visits return on their ending day. Read again before each
                  question: a stay can be extended during the conversation.
                  No row = no question and no settlement. The ordinary read
                  above remains the source for tomorrow's schedule preview.
retro-items <date> [--no-rating]
                  -> JSON list, one ITEM per outer event (same gate as
                     appt-find --retro). Nested events fold in: outing bundle
                     legs, contained_by children, meals attached to the
                     bundle, a continuing same-place stay. Travel/buffer never.
                  item: id ulid kind title span start_day retro_line carried
                     lead stay stops question rating talk children card people
                  talk = {angle kind line ...} for a visit with people, or
                     null: ONE small-talk line (person > event > restaurant).
                     Unanswered -> it IS question/card. Answered -> line is
                     the one follow-up.
                  card = the rendered turn (lead line when carried, title,
                     span, place, ONE recap question, at most one module
                     rating row as `name` + [[OPTIONS: ...]]). Send verbatim.
                  carried=true -> never under the today notice; its lead
                     line opens its card.
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
                  RETRO_IN_PROGRESS refuses an ongoing visit without a write;
                  refresh appt-find <date> --retro and continue with its rows.
retro-item-set <item id|ulid> <line>    the answer on the outer event AND
                  every child without its own line (one txn).
                  -> JSON {id, line, copied, kept}. RETRO_IN_PROGRESS as above.
visit-talk-context <item id|ulid> --data JSON --json
                  typed opener facts: joined:[PERSON_ID], event:{destination_kind:
                  home|person_home|venue,first_round:N,who:NAME,where:NAME},
                  life_facts:[{person_id:ID,fact_id:ID}]. See retro-items.md.
visit-talk-reply <item id|ulid> "<his words>" --data JSON --json
                  agent supplies verdicts:[{holder,verdict}], traits:[{target}],
                  notes:[{holder}], with optional verbatim quote on each row.
                  verdict enum: liked|neutral|disliked; holder: owner, an existing
                  party person:kit_life:ID, or the exact party group id (verdicts).
                  traits use existing kit_life:venue:<trait>:<value> ids.
                  Words alone capture nothing; no keyword or acknowledgement
                  classifier. follow_up=null.
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

## both lanes: lived-day experience review

```
experience-retro-list YYYY-MM-DD [--render] [--run-day YYYY-MM-DD]
    --run-day later than the day: the block header names its weekday, not today.
    JSON: date, block, items (outer experiences with children, pending,
    next_question={root_id,event_id,field,title,text}, linked block_ids).
    Selects occurred_on, never created_at. No write or capture inference.
experience-enrich ID field=value ...
    Existing reaction/note/kind_hint/title remain supported.
    New: occurred_on, occurred_at (ISO with offset), place_id, person_id,
    people=alone|unknown, time=unknown, place=unknown,
    amount=INTEGER|none|unknown, spend_id (when several linked), verbatim.
    Actual detail record + linked ledger shadow update atomically;
    no new task or scheduled appointment. Exact words preserved on capture.
```

Read [experiences.md](experiences.md) for the one-question conversation,
complete-row silence, child targeting and the outing-review seam.

## Typed retro corrections (2.46.0)

```
event-retro-clear ID          clear one event answer
event-retro-clear-undo ID     undo that clear (refuses a later answer)
event-retro-undo ID           undo latest event set/clear
retro-item-clear ID           clear outer + still-inherited child answers
retro-item-undo ID            undo latest item set/clear, including capture changes
weekly-retro-reopen SUNDAY    DONE -> STARTED; keep week identity
weekly-retro-clear SUNDAY     remove this agent/week marker
weekly-retro-undo SUNDAY      undo latest start/done/reopen/clear
retro-schedule-show daily|weekly        JSON definitions, including retired
retro-schedule-clear daily|weekly       retire schedule and future open occurrences
retro-schedule-undo daily|weekly        undo latest set/clear
retro-task daily|weekly YYYY-MM-DD      existing scheduled task ID
retro-walk D back             return to last disposed item; no delivery
retro-walk D reopen           closed/stopped -> active; no delivery
retro-walk D undo             undo latest cursor/session transition; no delivery
```

Re-answer with existing `event-retro-set` / `retro-item-set`. Item replacement
updates only children it previously copied; an explicit child answer (even
identical words) takes ownership. Clear preserves independent child answers.
Pre-upgrade child answers have no inheritance receipt: treat them as independent
and correct each explicitly, rather than guessing from matching text.

Undo checks changed fields and refuses later edits or dependent links. Undo is
atomic and keeps unrelated fields. Receipts use existing config; no raw storage
writes. `retro-walk back/undo` restores conversation progress only: disposition
writes belong to their module and must use that module's inverse as well. A
provider answer is not replayed or sent again by any correction verb.

For retro answers captured as experiences, `experience-enrich-undo EVENT`
reverses the latest enrichment. Subjective replacement/clear uses existing
`experience-enrich`; read experiences.md before correcting a course answer.
