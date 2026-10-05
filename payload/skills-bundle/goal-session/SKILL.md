---
name: goal-session
display_name: 목표 설정
description: Goal setting and goal revision for the user's own goals -- create, rebuild, adjust, add, or review goals. Fires on "목표설정하자", "목표설정 다시 하자", "목표 다시 잡자", "목표 새로 짜자", "목표 바꾸고 싶어", "목표 수정", "목표 좀 손보자", "새 목표 추가", "근력 목표 잡고 싶어", "체지방 목표 다시", "지출 목표 잡자", "저축 목표 세우자", "업무 목표 정리하자", "지금 목표 뭐야", "목표 확인", "let's redo my goals", "set a new goal", "change my target", "what are my goals". Also fires when the user asks to restart or redo a goal interview after one already completed, and when an onboarding flow delegates its goal stage here. Domain-neutral -- `--domain` parameterises it. The talk is driven by `goal-interview --next`: the tool returns ONE move per turn with the user-facing lines already rendered (direction -> the user's own strands -> what they do now and, in health, for how long -> body values (health: the latest InBody first, one ask per turn; older than 28 days -> one follow-up turn offering weigh-now carry-forward (lean mass held), can't-measure-now (goals from the old InBody, update offered when a new record lands), or a new InBody) -> in health, one safety check -> order for strands that pull apart -> the personal fact a rate needs (sex) -> the body proposals -> the measuring cadence they need -> the other strands (their starting value, a practice, the care) -> one wrap-up), each message opening with the onboarding card; the agent speaks those lines and runs the command the move names. Existing goals: the tool's rebuild / adjust / add fork comes first. Side effects: writes life_why, metric_axis (the user's strands), ledger_goal, metric_freshness, ledger_constraint (safety sites the user names), config (sex, interview notes).
---

# goal-session: tool picks the move + writes the words; you carry them

## trigger tier

BEST-EFFORT by description. no guaranteed entry nudge on a fresh instance.
instance MAY register its own `goal-entry-gate.py` UserPromptSubmit hook
(instance-provided; kit ships none). hook = instruction present; obeying =
still model behavior.

## entry

```bash
database/kit_life.sh goal-interview --start --domain <D> --json
```

- zero goals -> move loop directly.
- active goals -> speak its `question` (rebuild / adjust / add fork). on the
  pick, take the route below (not the choice's `next_cmd`):
  - rebuild -> move loop with `--stage` (rows land as proposed; direction
    asked fresh, stored one not reused). close with `goal-rebuild-commit
    --replacing <old ids> --with <new ids>`; refuses until every new row's
    walk is done, then retires old rows in one transaction. user never has
    zero active goals.
  - add -> move loop without `--stage`.
  - adjust -> `goal-interview <id> --revise <field> --json`, one field per
    turn; `goal-set` writes it.

## move loop

1. `database/kit_life.sh goal-interview --next --domain <D> --json` (add
   `--stage` in a rebuild) -> ONE move: `{kind, card, readback: [...], say,
   choices / on_yes / on_answer / on_proceed, ...}`. first move of an
   interview: read reference/moves.md (per-kind answer -> command table,
   strands for `derive_axes`).
2. speak `readback` lines, then `say`: verbatim, one message, blank line
   between. `readback[0]` IS the onboarding card. read reference/card.md
   when a card looks off or the user asks about a slot. `say: null` + no
   readback -> speak nothing, act.
3. wait for user. run the command the move names for their answer, exactly:
   `on_answer` / picked choice `cmd` / `on_yes`; `<...>` placeholders = their
   words byte-for-byte or their number; a list = run each; unknown ->
   `if_still_unknown`. `choices` -> buttons on the same message.
4. `--next` again. `kind: wrap` + `done: true` ends it.

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
