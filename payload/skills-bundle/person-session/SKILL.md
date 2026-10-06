---
name: person-session
display_name: 관계 케어
description: >-
  관계 케어 -- 사람 관련 모든 행동: 만남 전 브리핑, 레트로 후 관계 기록,
  연락 텀 알림, 가벼운 안부 체크인, 터치 로깅. 트리거(KO): "OO 만나기 전에 뭐 알아야 해",
  "오늘 만난 사람", "OO랑 통화했어", "OO한테 DM했어", "연락 안 한 사람 있어?",
  "OO 요즘 어때", "관계 케어", "누구 연락해야 해", "지난달에 OO 만났었어".
  트리거(EN): "pre-meeting brief", "who should I contact", "log a touch",
  "relationship retro". Outputs: brief (0 questions), retro questions
  (max 2/day total), contact-gap suggestions + at most one casual check-in
  (all inside 1 batch/week via alert-pick only), touch log (1 turn).
---

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

## brief (meeting someone / asking about a person)

`relmod.sh brief <id>` -> say it, zero questions: profile line (relation,
level, birthday if within 30 days), last contact + kind, recent facts (<= 5),
upcoming meetings from kit_life.

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

ask family / partner / pets only. `kit_life.sh person-add <name> <relation>`,
base facts `relmod.sh fact-add <id> "<fact>" --source onboarding`. never
re-ask what persons already holds.
