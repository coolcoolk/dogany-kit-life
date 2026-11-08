# core-log -- health adapter (body readings, standing limits)

Before replying after capture, apply the common SETTLE ON EVIDENCE contract
from AGENT: saving facts alone does not finish an activity report. Read
`.claude/skills-bundle/schedule-log/reference/completion-chat.md` for the
shared completion check. A PLANNED item settled this turn: its single
completion reply overrides this adapter's capture-report/card instructions.
No open task/routine matches: no completion reply at all, hand over to
reference/episode.md. Tool work stays silent until the final line.

- InBody sheet / body-composition screen: read
  `.claude/skills-bundle/health-log/SKILL.md` steps 1-4 and follow them
  (every readable field, its own date, `log-metric` per field, profile
  config). An unreadable value -> ask that one, after saving the rest.
- One reading stated ("weight 72.4 this morning"): `log-metric <date>
  weight_kg 72.4 "<source words>"`; `body-state` then `config-set
  weight_kg=<value>` when it differs (health-log step 4).
- Body sizes (waist, foot) -> the shopping adapter's "sizes" rule
  (`log-metric`).
- A standing injury, pain that keeps coming, or a limit -> the "Shared
  body" rule of the kit fragment (`constraint-add --class safety ...`).
- `GOAL-BASIS-UPDATE` / `CADENCE-ASK` lines appear only while health
  management is on (health-log owns them). Absent -> no goal or cadence
  talk.

`log-metric` stores the supplied numeric value and echoes the InBody panel
on the sheet's grid; a key outside that panel keeps the legacy integer echo,
which alone does not mean a decimal was lost. Do
not rewrite a successful reading or start a user-facing progress explanation
just because its echo is rounded. Any necessary verification stays silent,
then the common activity-completion check still runs before the final reply.

- Supplement/medication schedules, intake corrections, restoration, or “먹었어” about an open intake reminder:
  read `.claude/skills-bundle/health-log/reference/regimen.md`; use
  `kit_life.sh regimen add|take|edit|retire|unretire|delete|doses|untake|dose-edit|dose-delete|undo|redo`.
  Use `list --all` for retired definitions and the exact `planned_at` from
  `doses` for a dose correction. `undo` restores deleted definitions/doses;
  read the regimen reference before choosing it. These are never meals or fullness
  answers. Keep medication names private; save only owner-stated doses.

Correct an existing observation with `metric-edit <date> <metric> <value>
[note]`. It preserves identity, owner, observation date and an omitted note;
a missing observation is refused. Use `log-metric` for a new observation.

For an owner request to link/import health data, interpret the intent and run
`ingest-link`; the prompt hook never routes by health/link/negative words.
Read its live status and returned handoff; do not claim a connection from a phrase.

For body corrections, withdrawal, pain events, body signals, profile values,
and macro-target undo, first read
`.claude/skills-bundle/health-log/reference/corrections.md`. It registers the
typed correction/readback/undo verbs; use those within this session.
