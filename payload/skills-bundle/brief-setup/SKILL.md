---
name: brief-setup
display_name: 생활 설정
description: >-
  Configure the kit's morning briefing, daily retro and weekly review when the owner wants to begin using them, accepts
  the first-install offer, changes their timing, or continues the walkthrough after their routines were enabled. Entry
  depends on owner intent and the actual routine/slot state, regardless of wording. If the module is off or its setup is
  outstanding, use the first-install/walkthrough entry before asking about a record; ask timing only for enabled slots.
  Owns the briefing-time question, one-group first-install offer and walkthrough closing line. Calls the framework
  setter through briefing_times.sh and reports only what actually fires; never moves a clock itself. Kit
  activation/deactivation and the per-item walkthrough stay in dogany-lifekit-setup. Never start the goal interview from
  a briefing setup.
---

Before answering about food, cooking, timing or the rest of today, run
`"${PROJECT_ROOT:-$(pwd)}/database/kit_life.sh" day-now` and use its local clock,
day type, logged meals, next eating occasion and remaining blocks. Never ask
which meal when day-now answers it; an afternoon food request after logged
lunch targets dinner unless the owner explicitly says otherwise. Never infer
attendees from a title; use only stored people/groups. Historical or explicitly
dated requests keep their stated date and meal; day-now supplies current context.


# brief-setup -- briefing times and the walkthrough close

Split (2026-10-01): dogany-lifekit-setup switches items on/off; this
skill asks WHEN and says what fires. The clock mover is the framework's
`set-briefing-times.sh`; `briefing_times.sh` (this dir) calls it and turns its
exit code into finished lines. Speak the `SAY`/`ASK` lines verbatim -- the copy
lives in i18n `life_setup.*`, never improvise it.

## entry

- A. right after the walkthrough switched `morning-brief` and/or `daily-retro`
  on (units must be loaded first; asked earlier every slot comes back dead),
  before its summary.
- O. first install: the kit offer (`life_setup.offer`) and the one-group
  pick. The flow and the group -> bundle-row map are in
  `first-install.md` (this dir); it ends in entry A when the picked group
  holds a briefing routine.
- B. any later change-time request. Weekly too: `--weekly "<Day> HH:MM"`
  (Day = Sun..Sat). `DEAD weekly` = weekly program missing -> say the weekly
  could not be scheduled, never "set". Catch-up: `--catchup on|off`; when
  output carries `WEEKLY_CATCHUP_ASK`, ask it with its buttons first.

## steps

1. which slots are on, and the question:

   ```bash
   bash .claude/skills-bundle/brief-setup/briefing_times.sh --root "$PWD" --slots
   ```

   `SLOT` rows: slot, routine id, on|off, default (bundle.conf), current.
   Ask ONLY when an `ASK` line came back: speak it verbatim, one message,
   covering only the on slots. No `ASK` = nothing is on -> skip to step 4
   (entry A) or offer to switch the routine on via dogany-lifekit-setup
   (entry B). In B, if the owner already named a time, skip the question.
2. owner answers:
   - a time per slot -> `--morning HH:MM` / `--retro HH:MM` (24h; "7시 반" =
     07:30, "밤 10시" = 22:00; ambiguous am/pm -> ask once).
   - "그대로" / skip -> no time flag (the setter keeps the live times).

   ```bash
   bash .claude/skills-bundle/brief-setup/briefing_times.sh --root "$PWD" [--morning HH:MM] [--retro HH:MM]
   ```

3. exit code -> reply (speak every `SAY` line, nothing else):
   - `0` -> the one confirmation line. A weekly-only NO EMITTER is already
     folded into 0: expected on every kit instance, say nothing about it.
     `SAVED <slot>` rows (Linux) come with the "saved, not moved yet" line
     (`life_setup.saved_not_moved`): say exactly that, never that it moved.
   - `4` -> `DEAD <slot>`: that routine has no loaded unit, nothing fires at
     that time. The `SAY` line says so and offers to switch it on; yes ->
     dogany-lifekit-setup turns the routine on, then run step 2 again.
     NEVER say it moved.
   - `2` -> the time was not understood: `SAY` re-asks; ask once more.
   - `1` / `3` -> the change did not happen: say so with the `SAY` line;
     do not retry silently, do not claim success.
4. entry A only, after the walkthrough summary: the closing line, verbatim
   (i18n `life_setup.closing`), then stop:

   ```bash
   bash .claude/skills-bundle/brief-setup/briefing_times.sh --root "$PWD" --closing
   ```

   Never offer or start the goal interview here: the kit does not ask for a
   goal top-down (owner 2026-10-01). core-setup starts only when the
   owner asks for it; its defer nudge is for an interview they started.

## rules

- Defaults: bundle.conf's schedule column is the one kit source. Never quote
  a default from memory or from a description string.
- Linux: the setter moves launchd units only, so the new time is saved but
  the timer keeps the old one until framework DGN-1810 lands. Report the
  `SAVED` line as given. NEVER run routine-ctl (or anything else) to enable,
  move or restart a unit -- one writer only, the framework setter.
- No test fire, no extra confirmation turn after the confirmation line.
