# Owner-stated supplements and medication

Use the kit's `database/kit_life.sh regimen` for an intake schedule or intake
completion. These are health records, separate from meals. Never call
`meal-add`, create a meal slot, or ask fullness for a supplement or medication.
The core-log health adapter and prompt hook share this path on base Lifekit
and on a health-pack instance; no pack_health installation is required.

Save only the owner's stated name, amount, unit and timing. Never propose a
dose or suggest stopping a prescribed medication. If amount is missing, ask for that fact. Timing is optional: a name and
owner-stated amount alone save a record-only regimen, checked at daily retro. An unspecified morning clock can be saved as a
breakfast anchor; never invent a clock time. Use `--kind medication` when the
owner calls it a medication. Medication names belong in private chat only.

Examples (the first word is the regimen action, after `kit_life.sh regimen`):

- `add --name 오메가3 --amount 1 --unit 알 --at 08:00` (repeat per named item)
- `add --name 크레아틴 --amount 3 --unit g --after-workout`
- `add --name 크레아틴 --amount 3 --unit g --record-only`
- `snapshot YYYY-MM-DD` (shared-safe supplement taken/total)
- `add --name 'owner-stated name' --kind medication --amount 1 --unit 정 --at 21:00 --days weekdays`
- `add --name 'owner-stated name' --amount 1 --unit 알 --meal 아침`
- `add --name 'owner-stated name' --amount 1 --unit 알 --before-workout`
- `take --ids REGIMEN_ID --said 'exact owner words'` (also for record-only intake).
- `take --group REMINDER_ID --positions 1,2 --said 'exact owner words'`.
- `input '' --group REMINDER_ID --action check|later` for an explicit checklist or snooze.
- An issued numeric checklist answer may use `input '1,2' --group REMINDER_ID`.
- `list`, `due`, `retire REGIMEN_ID` (stop the reminder, never a suggestion to
  stop taking a prescribed drug).

Relay the one-line registration receipt. For input, relay JSON `reply` only.
An `AMBIGUOUS` result asks which reminder; never mark every open group as taken.
The bridge's `opt:regimen:...` metadata binds the original button and group.
Unchecked items stay open, with their original list numbers. An already taken
item cannot create another log entry. The next scheduled day is independent.

Clock and meal/workout anchors share one member/time group. Meal anchors use
scheduled blocks' `meal_slot`, or the first actual meal report in that slot
when no timed meal exists; repeated dishes remain one occasion. Historical
meal backfills do not generate today's intake reminders. Workout anchors use `action_kind=workout` at
the workout start boundary, before execution. No made-up meal clock or
workout lead is stored. Weekdays accept `weekdays` or 0=Mon through 6=Sun.
Schedules use Asia/Seoul unless structured add supplies `--tz`.

The existing reminder heartbeat prepares and selects occurrences in P7's
health domain. The health agent's loaded scoped poller owns them, with the life agent as fallback.
Strict delivery explicitly targets the configured private owner id. Missing
private destination leaves the reminder unsent for retry; no ordinary shared
TSV fallback carries names. Shared day and diet cards show only a taken-item
count. v0.1 has no adherence statistics, refills or interaction advice.

For an owner-stated correction, use `edit REGIMEN_ID --name NAME --kind
supplement|medication --amount AMOUNT --unit UNIT --days weekdays --tz
Asia/Seoul --at HH:MM` (each field optional), or `--meal SLOT` /
`--before-workout` / `--after-workout` / `--record-only` for a different anchor.
`--record-only` removes proactive timing while retaining the definition.
Read `list` to identify an active regimen, or `list --all` for retired ones;
retired definitions can also be edited. This preserves identity and completed dose snapshots;
pending occurrences follow the corrected amount and schedule. Relay the
existing saved receipt. Do not retire/re-register to correct a regimen.

Correction and restoration verbs (after `kit_life.sh regimen`):

- `doses --id REGIMEN_ID` reads pending and completed dose snapshots, including
  their exact `planned_at`, `reminder_id`, position, amount, unit, intake time
  and source. Omit `--id` for all this member's doses, or use `--group ID`.
- `untake REGIMEN_ID --planned-at ISO_WITH_OFFSET` clears that one intake;
  the original numbered occurrence becomes pending. A stale intake button
  cannot override this owner correction. A new explicit `take` can record it.
- `dose-edit REGIMEN_ID --planned-at ISO_WITH_OFFSET [--name NAME]
  [--amount AMOUNT] [--unit UNIT] [--taken-at ISO_WITH_OFFSET]
  [--source button|words] [--move-to ISO_WITH_OFFSET]` corrects one snapshot,
  independently of the definition. `--move-to` corrects its planned instant,
  retaining its regimen identity; the destination group assigns its position.
  Use `untake` to clear intake time/source. Never invent an intake or source.
- `dose-delete REGIMEN_ID --planned-at ISO_WITH_OFFSET` removes exactly one
  pending or completed occurrence. Its owner marker prevents polling from
  recreating it. Other numbered doses retain their positions.
- `unretire REGIMEN_ID` reactivates the same definition; `retire REGIMEN_ID`
  only stops its reminders and keeps completed history.
- `delete REGIMEN_ID` removes a mistaken definition and its dose rows. This
  is a data correction, never advice to stop prescribed medication.
- `undo` reverses the latest changed registration, edit, retirement,
  reactivation, deletion, intake, checklist or snooze for this member. It
  restores deleted definitions/doses with their original ids and snapshots.
- `redo` restores the last undone change; `undo` reverses that redo again.
  A new mutation clears redo. Unchanged retries do not consume undo history.

All these calls are private and member-scoped (`--member MEMBER` before the
verb; default owner). For corrections, read the exact dose key first; never
guess an id or undo an unrelated latest action. Undo is guarded against later
fact edits, preserves other members and unrelated polling rows, and keeps new
pending snapshots consistent with the restored definition. History and owner
markers use existing config storage in the same transaction, with no schema
change. Offline rehearsal uses `--db COPY` before the verb.
New correction JSON is structured tool data; do not read status codes or raw
history to the owner or invent a new receipt/card. Existing add/edit/retire
receipts and intake `reply` retain their approved copy. Meal-card counts and
tracked supplement rows read the corrected intake facts, including undo.

After-workout uses the successful health session close, never its planned
end or start. Session close queues one nudge through the existing heartbeat;
retries keep the first close time and completed intake is not offered again
that day. At daily retro, untaken regimens reuse the approved single-item
reminder. Record-only has no proactive timing. For an intake report, interpret the owner message and call `regimen take`
with the relevant regimen or reminder ids; owner prose alone is inert. Use
`regimen retire ID` for a requested stop and `regimen edit ID` for corrections; never `task-done`, `routine add`,
or a replacement supplement task. A removed old supplement routine stays
removed. No routine/task conversion or deletion is performed automatically.

The health agent's supplement taken/total row is a separate OFF-by-default candidate
(`HEALTH_REGIMEN_PIN=off`). It reads the kit regimen snapshot. Existing diet
card copy is unchanged. Intake updates refresh the health pin through the
existing owner-resolved refresh path.
