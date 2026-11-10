---
name: core-log
display_name: 기록
description: >-
  Always-on capture when the owner states, shows or reports actual life facts about money, purchases, people, places,
  schedule, habits, meals, workouts, body, aspirations, experiences, profile or availability. Captures text, photos,
  screenshots, PDFs, receipts and app screens independently of module activation or setup. Includes product selections,
  cart/order/delivery updates and screenshot/link follow-ups; cooking/menu requests, dish or finish-time choices and
  ingredient/leftover context use reference/cooking.md. A request to start or use a module with no record content is
  setup intent, not a partial record: when that module's onboarding is outstanding, hand to its setup entry; do not ask
  generic record fields. Writes actual facts in the same turn through kit_life.sh verbs, never to memory only, never
  asks whether to save it, never pitches a module or its setup after capture. Management (targets, diagnosis, sweeps,
  coaching, reminders) stays with each module's own skills and only after its onboarding.
---

Before answering about food, cooking, timing or the rest of today, run
`"${PROJECT_ROOT:-$(pwd)}/database/kit_life.sh" day-now` and use its local clock,
day type, logged meals, next eating occasion and remaining blocks. Never ask
which meal when day-now answers it; an afternoon food request after logged
lunch targets dinner unless the owner explicitly says otherwise. Never infer
attendees from a title; use only stored people/groups. Historical or explicitly
dated requests keep their stated date and meal; day-now supplies current context.


# core-log -- record every kit fact, quietly

`LKIT="${PROJECT_ROOT:-$(pwd)}/database/kit_life.sh"` (cwd = workspace
root). Every write and read goes through `$LKIT`; no raw SQL, no memory-only
fact.

## route

Read each adapter before its first write; capture all modules in a message.
Unplanned "I did X" and its answers: episode.md owns capture and reply;
read it before person/meal/schedule sub-adapters (including their exceptions).

| the user states / shows / reports | adapter |
|---|---|
| a payment, refund, recurring payment, balance, card bill, installment, card or account fact | reference/ledger.md |
| a product pick from a screenshot/link, cart/order/delivery status, purchase, return, owned thing, garment measurement, taste, a want | reference/shopping.md |
| a fact about a person, a call / message / meeting with someone | reference/person.md |
| an address, a place nickname, a venue visited | reference/place.md |
| an appointment, a stay, a task, a PLANNED item done (an open task/routine matches) | reference/schedule.md |
| a habit or cadence ("every weekday", "usually Mon/Wed") | reference/routine.md |
| food eaten (text, label, barcode, product or dish photo) | reference/meal.md |
| a finished workout session | reference/workout.md |
| a body reading (InBody, weight, measurement), a standing injury or limit | reference/health.md |
| an aspiration or direction ("someday I want to ...") | reference/goal.md |
| a lived experience and how it felt; an unplanned "I did X" with no open task/routine match ("독감주사 맞았어", "영화 보고 왔어", "가영이랑 저녁 먹었어") | reference/episode.md |
| a finished piece of work, a decision taken in a meeting | reference/brief.md |
| height, weight, availability windows, equipment, a standing preference | reference/core.md |

## rules (every adapter)

1. Write in the same turn before replying; claiming a save without a
   successful write drops the fact. No text before/between tool calls:
   each text block is sent to the owner as its own message.
2. Find-first: use the adapter's find verb/flag. `EXISTS` (exit 3): no
   duplicate; still run rule 6, nothing settled -> say so in one line.
   A changed fact updates the existing row (`*-upd` / `--replace`).
3. Record only what was said or shown. Never invent a date, an amount, a
   due day, a cadence or a past payment. A value the verb cannot go without
   and that is not in the message or the kit -> ask for that one value, once.
4. Never ask whether to save, add or register. No save / register / defer
   buttons. No module, setup, onboarding, automation or coaching pitch after
   a capture.
5. Management stays off unless the module's management is on:
   `$LKIT manage-state <module>` (MANAGE ... on). Off -> skip every
   management step the module's own skills describe (targets, sweeps,
   reviews, clustering offers, enrichment questions, reminders). The engine
   already withholds its own offers while off; relay what it prints, add
   none.
6. Before replying run SETTLE ON EVIDENCE (AGENT;
   `schedule-log/reference/completion-chat.md`). A PLANNED task settled this
   turn -> only its completion line. Unplanned "I did X" -> episode adapter,
   curious follow-up; no `task-add` + done, no "완료" or "기록했어요".
   Otherwise confirm in one short line in the owner's language and module's
   terms (ledger, schedule, meal record), never "I'll remember it".
7. Nothing for it in the kit (no verb takes it) -> say plainly it cannot be
   stored yet; never park it in memory or an unrelated table.
8. A stated fact that may be a lifekit resource or change (what was bought,
   an item's size/weight, equipment at hand, a moved appointment, stock,
   spending) and no adapter row above fits -> `$LKIT fact-route "<words>"
   [--kind stock|ingredient|gear|dish|schedule|ledger]` in the same turn.
   `NO_TARGET` -> ask the owner once; nothing else is stored.

## read before ask

Before asking about, or saying there is no, kit fact: the adapter's read
verb first (`spend-find`, `fixed-list`, `person-show`, `place-resolve`,
`meal-day`, `body-brief`, `preference-list`, ...).
A new session or restart mid-talk: `$LKIT fact-landed` first -- facts already stated today; never re-ask them.
Cooking/prep start or ingredient/amount question: shopping.md `shop-capture-find` (dish/event, ingredient) first, before asking any amount.

## cooking

Cooking/menu request, finish-time target, or stated dish set -> read reference/cooking.md.
It owns day-now, next-meal menu choice, quantities, session entry and side effects.

## later setup

Start/use intent with no record content: read reference/setup-entry.md before
asking any record field. It resolves setup state and the owning entry.
A module's onboarding or setup starts with what is already recorded:
`$LKIT capture-readback <module>` -> show those rows (what, when, from
where) first, then ask only the management decisions still missing. Never
re-ask a recorded fact. The owner explicitly starting a module's setup or
management = `$LKIT manage-set <module> on`; declining it =
`$LKIT manage-set <module> off`. Recording continues either way.
