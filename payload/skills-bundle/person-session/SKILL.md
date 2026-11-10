---
name: person-session
display_name: 관계 케어
description: >-
  Care for relationships when the owner requests pre-meeting context, relationship review, contact-gap suggestions, a check-in or touch capture/recall, or continues an active care exchange. Use people, appointments, stored touches, management consent and the current retro/alert batch as the entry state. Actual calls/messages/meetings capture independently of management. A bare request to begin relationship care without touch content enters the management/readback path rather than a generic person/date question. Outputs: pre-meeting brief without questions; retro questions capped at two per day total; contact-gap suggestions and at most one casual check-in inside one batch/week via alert-pick only; touch log in one turn. NOT person identity, aliases, family facts or groups (person-manage), or a scheduling write (schedule-log).
---

Before answering about food, cooking, timing or the rest of today, run
`"${PROJECT_ROOT:-$(pwd)}/database/kit_life.sh" day-now` and use its local clock,
day type, logged meals, next eating occasion and remaining blocks. Never ask
which meal when day-now answers it; an afternoon food request after logged
lunch targets dinner unless the owner explicitly says otherwise. Never infer
attendees from a title; use only stored people/groups. Historical or explicitly
dated requests keep their stated date and meal; day-now supplies current context.


# person-session

Every person-related action: pre-meeting brief, retro enrichment,
contact-gap suggestions + check-in, touch log, snooze, level/mode changes,
first-activation onboarding.

Tools: `$PROJECT_ROOT/database/kit_life.sh` (person-find, person-add,
appointments) and `$PROJECT_ROOT/database/relmod.sh` (all relationship
verbs). resolve first: `kit_life.sh person-find <name>` -> id; relmod takes
ids, not names. Model: sonnet (brief assembly); haiku for one-line touch logs.

Budget (declare if asked):
- retro questions <= 2 per DAY total, across all persons.
- proactive gap suggestions <= 1 batch/week (2-3 persons), gated by
  `alert-pick`; check-in rides inside that batch (code cap).
- touch log: 1 turn. brief: 0 questions.

Honorific gate: every person mention (confirmations, acted lines, nudges)
passes person-manage/SKILL.md honorific rule: always "님", never echo the
user's casual register. "길동이 연락완료" -> "길동님 연락 완료했습니다".

## Context before person or gift answers

First run `kit_life.sh noun-context "<current message + known recipient/occasion from this conversation>"` using `${PROJECT_ROOT:-$(pwd)}/database/kit_life.sh` from the agent workspace. Read PERSON / PERSON_REL / PERSON_EVENT and EVENT before answering or asking who/where/when; no EVENT does not mean no person facts. An EVENT line answers who/where/when: ask nothing.
An unresolved nickname: `person-resolve`, then repeat noun-context with `--span "<resolved canonical name>"`; NEAR / AMBIGUOUS is not a resolved person. For missing details use `person-show <id>`.
Kit person facts override agent memory. A memory fact phrased as a future expectation whose date has passed must not be asserted: check the kit, or ask once if still unknown. A recorded child birth is past, never an upcoming delivery; do not infer medical/recovery facts.
Product "찾아줘" / "다 찾아줘", gift purchase sources, and packaging searches go straight to shopping-session's search flow and concrete product cards, including follow-ups without a repeated recipient. Never answer those with generic advice or an offer to search later.

## brief (meeting someone / asking about a person)

`relmod.sh brief <id>` -> say it, zero questions: profile line (relation,
level, birthday if within 30 days), last contact + kind, recent facts (<= 5),
upcoming meetings from kit_life.

Questions about the last meeting ("나 OO 마지막에 언제 만났지", "OO 마지막으로 본 게 언제야", "when did I last see OO") route here. Resolve the person, then run `relmod.sh brief <id>` and answer from LAST_MEETING (date + occasion). LAST_CONTACT may be a call or message; do not present it as a meeting. If LAST_MEETING is absent, say no meeting is recorded. Do not infer a meeting from a future appointment or ask a follow-up when the person is resolved.

Capture (touch log, facts heard) runs management on or off:
`.claude/skills-bundle/core-log/reference/person.md`. Retro enrichment,
contact gap / check-in, level change and onboarding below run only while
`kit_life.sh manage-state person` is on.

## retro enrichment (daily retro / casual day review)

1. `relmod.sh retro-candidates [--date YYYY-MM-DD]`; NONE -> skip silently.
2. pick fewest facts (fact-count column) or oldest fact date.
3. 1-2 light questions for the WHOLE day: "오늘 만난 OO님 근황 하나만, 요즘
   뭐 하신대요?"
4. answer -> `relmod.sh fact-add <id> "<fact>" --source retro`. brush-off ->
   drop, never nag.

## contact gap + check-in + snooze

Read reference/contact-gap.md before rendering `alert-pick` output or
handling a "temporarily away / back" statement.

## touch log

"OO랑 통화했어", "OO한테 DM했어", "met X", "지난달에 OO 만났었어":
`person-find` -> `relmod.sh touch-add <id> <call|message|dm|other|meet> [--date YYYY-MM-DD] [--note "..."]`
-> one line. `meet` = recalled unlogged meeting, approximate date ok; exact
date + occasion -> prefer schedule-log. no follow-up questions.

## level change (closer / more distant)

1. check `relmod.sh drift-list` for an existing UPGRADE suggestion.
2. SUGGEST, never auto-apply. confirmed -> `relmod.sh fact-add <id> "<change>" --source chat`.
   no verb changes intimacy level; do not edit it.
downgrade only on explicit distance signal + repeated dismisses; never from numbers alone.

## remote friend

"멀리 살아" / only DMs -> `relmod.sh set-mode <id> contact` (touches are
the channel; meetings not expected).

## onboarding (first activation, one turn)

`kit_life.sh manage-set person on`, then `kit_life.sh capture-readback
person`: say the people already recorded first; ask family / partner /
pets only for who is missing. `kit_life.sh person-add <name> <relation>`,
base facts `relmod.sh fact-add <id> "<fact>" --source onboarding`. never
re-ask what persons already holds.
