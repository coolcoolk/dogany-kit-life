---
name: goal-session
display_name: 목표 설정
description: Create, rebuild, adjust, add, inspect or review the owner's goals when that is the owner's intent, when an active goal interview receives an answer/correction, or when onboarding delegates its goal stage. An explicit wish to begin goal management while the module is off or its goal interview is outstanding enters here; do not replace it with a generic record-field question. State decides whether to open, resume, revise or read the completed goals, never an exact phrase list. Begin with manage-set goal on (also health for the health domain), then goal-interview --start --said with the owner's original opening words and the domain. The service supplies the current-card wrap, staged rebuild, revise/add/keep decision or zero-goal opening and ONE rendered move per turn. It owns direction, strands, existing practice, body evidence and freshness choices, health safety check, conflicting priorities, personal facts, proposals, measuring cadence and wrap. Speak the onboarding card and returned lines and run the returned command. Domain-neutral via --domain. Writes life_why, metric_axis, ledger_goal, metric_freshness, ledger_constraint and interview config. NOT bare aspiration capture (core-log's goal adapter) or domain prescription/program design.
---

Before answering about food, cooking, timing or the rest of today, run
`"${PROJECT_ROOT:-$(pwd)}/database/kit_life.sh" day-now` and use its local clock,
day type, logged meals, next eating occasion and remaining blocks. Never ask
which meal when day-now answers it; an afternoon food request after logged
lunch targets dinner unless the owner explicitly says otherwise. Never infer
attendees from a title; use only stored people/groups. Historical or explicitly
dated requests keep their stated date and meal; day-now supplies current context.


# goal-session: tool picks the move + writes the words; you carry them

## trigger tier

BEST-EFFORT by description. no guaranteed entry nudge on a fresh instance.
instance MAY register its own `goal-entry-gate.py` UserPromptSubmit hook
(instance-provided; kit ships none). hook = instruction present; obeying =
still model behavior.

## entry

Read `database/kit_life.sh manage-state goal` (MANAGE<TAB>goal<TAB>on|off<TAB>reason); off = setup outstanding, on = use the existing interview/goals state. The owner asked for a goal consult: `database/kit_life.sh manage-set goal on`
(health domain: also `manage-set health on`) first. Sentences captured
earlier come back as `whys` / the card readback; never ask for them again.

Always run `database/kit_life.sh goal-interview --start --said "<the user's
opening words, byte-for-byte>" --domain <D> --json`. The tool reads the
words and decides; never pick the intent yourself:
- `kind: wrap` (keep words, full card) -> the card's wrap: move-loop step 4.
- other `kind` (fresh-start words, goals exist) -> a staged rebuild walk is
  open: speak it via step 2, continue with `--next --stage`. No fork.
- `field: opening` (zero goals) -> move loop directly.
- `question` (the fork) -> speak it verbatim (card + count-free fork). Never
  replace the card with a goal/rule/keep count. On the pick:
  - rebuild -> `goal-interview --start --scope rebuild --domain <D> --json`
    (opens the walk, returns its first move), then the move loop with
    `--stage`. Never `--next --stage` before it.
  - add / keep -> move loop without `--stage` (keep: its wrap, no write).
  - adjust -> `goal-interview <id> --revise <field> --json`, one field per
    turn; `goal-set` writes it.
rebuild walk wrap, commit, or a refusal: read reference/rebuild.md.

## move loop

1. `database/kit_life.sh goal-interview --next --domain <D> --json` (add
   `--stage` in a rebuild) -> ONE move: `{kind, card, readback: [...], say,
   choices / on_yes / on_answer / on_proceed, ...}`. first move of an
   interview: read reference/moves.md (per-kind answer -> command table,
   strands for `derive_axes`).
2. speak `readback` lines, then `say`: verbatim, one message, blank line
   between. `readback[0]` IS the onboarding card. read reference/card.md
   when a card looks off or the user asks about a slot. `say: null` + no
   readback -> speak nothing, act. no confirmation/praise lines between strands; strand-review relay + `measure_details`: reference/moves.md.
3. wait for user. run the command the move names for their answer, exactly:
   `on_answer` / picked choice `cmd` / `on_yes`; `<...>` placeholders = their
   words byte-for-byte or their number; a list = run each; unknown ->
   `if_still_unknown`. `choices` -> buttons on the same message.
4. `--next` again. `kind: wrap` + `done: true` ends it.
5. every answer lands in the kit through the command the move names; a
   command carrying `--anchor <domain>:<question>` records where it landed
   -- run it exactly as given. pack consults in the same domain anchor their
   answers the same way. `database/kit_life.sh consult-answers --domain <D>
   --json` = each answer, where it landed, held true/false; read it before
   re-asking any consult question.

user corrects a value, a strand's purpose, or the whole direction -> read
reference/corrections.md.
user asks why a number / order / cadence, asks progress, or wants another
goal verb -> read reference/tool-facts.md.

## hard rules

- rendered lines = the whole user-facing text of the interview. own words
  fill one slot only: a short answer to a side question the user asks.
- send as given: no code block, no emoji, no re-drawn bullets ("•" and
  "　–" only, never "-"), no reformatting.
- card's just-filled slot = confirmation of last answer. never add your own
  ("적어둘게요"), never speak one as its own message, never add a closing
  line after `say`.
- question always last; example answers under it = an "예)" line + one
  "• " line each. every spoken move ends on the next decision.
- pick in words + side question = an answer: record it first, answer the
  question in 1-2 lines in the next move. side question alone: 1-2 line
  answer + ONE-line re-ask; never re-send card / readback / options, never
  ask for a re-typed pick (reference/moves.md "mixed answer + side question").
- every number and date is already inside the command. never compute,
  round, or invent one.
- a consult answer never lives only in memory or a pack file. no kit verb
  takes it -> say it is not stored yet; never park it in another table.
