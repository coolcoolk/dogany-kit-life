---
name: brief-setup
display_name: 생활 설정
description: The kit's own setup step. Owns the briefing-time question (morning brief / daily retro) and every later change-time request ("브리핑 시간 바꿔줘", "아침 브리핑 7시로", "회고 시간 바꿔줘", "change my morning briefing time"), the kit's first-install offer (pick one group, switch just that on), and the walkthrough's closing line. Fires when the framework walkthrough (dogany-lifekit-setup) has just switched morning-brief and/or daily-retro on, and on any change-time request. Calls the framework setter (routines/set-briefing-times.sh) through briefing_times.sh and reports only what actually fires; never moves a clock itself. Kit activation/deactivation and the per-item walkthrough stay in dogany-lifekit-setup.
---

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
- B. any later change-time request. Weekly is not ours: the kit ships no
  weekly unit -- say so if asked, never pass a weekly time.

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
