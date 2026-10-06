# schedule-log -- verbs, resolve verdicts, errors

Read when you need an argument order, an output shape, or an error's meaning.
All via `kit_life.sh`. `task.sh` in this skill dir = 1:1 delegator to
`task-*` (add find done undone reschedule archive overdue); same thing.

## families by kind

```
kind=task        task-add task-find task-done task-undone task-reschedule
                 task-retitle task-archive task-overdue task-untimed-open
                 tasks-by-project task-done-between
kind=appointment appt-add appt-find appt-upd appt-show appt-cancel
                 appt-travel-attach
kind=presence    presence-add presence-move presence-cancel presence-revive
any kind         event-day event-window event-notify
                 block-person block-person-rm (appt-person / appt-person-rm =
                 the same verbs under their old names)
```

## signatures

```
person-resolve <name_or_alias>
    RESOLVED<TAB>id<TAB>name<TAB>relation<TAB>aliases
    AMBIGUOUS<TAB>n  + n rows (id name relation aliases)
    ABSENT<TAB><query>                         (every verdict exits 0)
place-resolve <name_or_alias>
    RESOLVED<TAB>id<TAB>name<TAB>has_address(0|1)<TAB>address
    VIA_ANCHOR<TAB>id<TAB>name<TAB>has_address<TAB>address<TAB>anchor
        query was a ROUTINE anchor ('헬스'); field 3 = real place name
    ABSENT<TAB><query>                         (every verdict exits 0)
appt-find    <date_from> [date_to]
    -> id start_local title location end_local people retro_line
       appointment AND presence rows (052); abandoned excluded
appt-add     <title> <start> [end location purpose summary] [--new]
             [--notify default|silent|start_only|<lead-min>] [--project <id|title>]
appt-upd     <id> field=value ...
             (title start_at end_at location location_url purpose summary)
    location 미정 (also "장소 미정") = the owner said the place is undecided:
    recorded as-is, card shows 장소 미정, never asked again, no travel.
    output (appt-add / appt-upd / appt-person / event-card) may carry, before the card,
    PLACE_UNSET<TAB><id> + PLACE_RELAY + PLACE_ASK<TAB><id><TAB><question>
    -> do what PLACE_RELAY says (ask that question; answer -> appt-upd).
block-person <block_id> <person_id>... /  block-person-rm <block_id> <person_id>
    appointment, presence or task; all-or-nothing on unknown ids; prints the
    card with the people. derived travel/prep legs and records are refused.
appt-show    <id>                      1 appointment + persons, local time
appt-cancel  <id>                      settle 'abandoned'
appt-travel-attach <eid>               derive prep/travel; overwrites provisional stopgap
place-gap    [--all] [<from> [<to>]]   READ: coming appts with no travel time
                                       (link_only|no_place|no_duration)
travel-link-gap [--all]                READ: place pairs with no travel minutes
                                       (absent|expired); gap lines carry no number
travel-sweep [<from> [<to>]]           summary scanned/attached/exists/needs/
                                       retired/other/provisional (provisional ⊂ needs)
presence-add "<title>" "<place>" <start> <end> [--new]
    two dates = all-day span; two datetimes = timed span (keeps the stated
    departure/return). a timed span ending on a later date renders as
    "11/27(금) 10:00 ~ 11/29(일) 12:00".
presence-move <id|ulid> <start> <end>  shape-preserving
presence-cancel <id|ulid>              settle 'abandoned'
task-add     <title> [due] [note] [--place N] [--at HH:MM] [--dur N | --end HH:MM]
             [--project <id|title>]
task-find    [date|all|keyword]        -> id title due state place
task-done | task-undone <id>
task-reschedule <id> YYYY-MM-DD [--at HH:MM | --all-day]
task-archive <id>                      soft delete
task-overdue                           todos due before today
event-day    <date>                    day view, all kinds incl. presence band
```

Empty positional -> "" placeholder: `appt-add "AI Day" "2026-07-05T12:00:00+09:00" "" "venue"`.

## stay participants and family links

Create a stay with `presence-add`, then pass its returned block ID and resolved
person IDs to `block-person <block_id> <person_id>...` in the same turn. This
works for timed and all-day stays; participants use the existing `event_persons`
links and remain attached after `presence-move`. Repeating an ID adds no duplicate.
Read them with `event-card <block_id>`, `day-card --date <date>`, or
`appt-find <date_from> [date_to]` (which also lists stays). The reverse lookup is
`person-blocks <name_or_alias> [days]`. `appt-show` is appointment-only.
Remove a participant with `block-person-rm <block_id> <person_id>`.

`person-link <a> <b> <type>` means **a is type of b**. Both targets accept a
person ID or an unambiguous name. For example, `person-link Child Parent child`
and `person-link Parent Child parent` describe the same link, regardless of
which person was created first. Two parents require two links. The existing
`persons_relations` table stores the smaller ID first and inverts parent/child
when necessary; no separate kinship table is needed.

`person-show <id|name>` lists the **other person's role**: the child's list
shows the parent as `parent`, and the parent's list shows the child as `child`.
`person-unlink <a> <b> [type]` uses the same a-is-type-of-b direction as the
write. Repeating a link is idempotent; an opposite direction conflicts (exit 4)
and requires `--replace` after confirming the correction. A family link does
not itself change either person's relation to the user (`person-set` owns that).

## time shapes

- date-only `YYYY-MM-DD` -> all-day (two dates = multi-day span).
- datetime `YYYY-MM-DDThh:mm[:ss[.fff]][Z|+hh:mm]` -> timed; naive = Asia/Seoul.
- all-day appt: slot_exclusive=0 (containing context, does not block timed).
- timed appt: slot_exclusive=1. end omitted = the kit's +3h default block
  (owner ruling 2026-09-30). pass an end ONLY when the user stated one; never
  pick one yourself -- the default is the kit's, say it is a default.
- appt-upd on all-day row with date-only value: start only = shift whole
  range (length kept); end only = keep start; both = each own date.

## errors (loud, non-zero, zero mutation)

- mixed shapes (date-only + datetime); malformed time; reversed interval.
- slot occupied (timed exclusive conflict).
- shape mismatch on appt-upd / presence-move (schedule_kind transition is an
  SDK verb, not appt-upd).
- wrong kind (task id to appt verb or reverse; block-person takes any of the
  three kinds); unknown person_id; empty title=.
- presence: unbounded span; overlap -> `PRESENCE_CONFLICT <n>` + blocking rows.
- exception: resolve verbs exit 0 on ABSENT (not a failure).
