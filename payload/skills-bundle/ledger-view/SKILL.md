---
name: ledger-view
display_name: 자원×대상 흐름
description: >
  Read-only render of the resource x entity crossing. Fires on per-target /
  per-instrument flow questions -- "현대카드로 이번 달 얼마 나갔어",
  "토스뱅크 이번 달 들어온 거", "카드별로 정리해줘", "어느 수단으로 제일
  많이 썼어" -- and on time-per-target questions -- "부모님한테 올해 시간
  얼마나 썼어", "OO한테 시간 얼마나 쓰고 있어". ALSO fires on any balance
  question ("이 통장에 얼마 남았어", "잔액 얼마야") -- to REFUSE it with
  the reason (no opening snapshot; balance is not derivable). NOT for spend
  capture -- the ledger capture/session/setup skill surface is detached
  (2026-09-08, re-onboarding later); plain category/day/week/month spend
  totals are served by the kit verbs spend-day/spend-week/spend-month
  directly, not by this skill.
---

# ledger-view

Read-only surface of the resource x entity crossing: money per payment
instrument (v_entity_flow), time per target (v_entity_time_alloc),
attribution coverage. writes nothing, ever. all via
`LKIT="${PROJECT_ROOT:-$(pwd)}/database/kit_life.sh"`; no raw SQL, no network.

## verbs

```
entity-list                                   -> id token display status n_deltas
entity-flow <YYYY-MM|YYYY> [target] [--resource <token>]   (default money)
      per-target inflow/outflow + 무귀속 line (always) + coverage +
      domain/state + criterion= lines
entity-time <YYYY-MM|YYYY> [target]
      allocated minutes per target (blocks x weight); zero links ->
      "대상 연결 0건: 시간 귀속 데이터 없음"; excluded= broken-weight blocks
criterion-list [domain]                       criteria + domain state
```
target = entity-list token | ko display | id, exact. 0 / ambiguous -> verb
refuses with candidates: relay, never pick silently. period YYYY-MM or YYYY only.

## hard rules

1. NO BALANCE ("얼마 남았어", "잔액", "잔고"). no opening snapshot was ever
   given (owner 2026-09-07); cumulative flow is not a balance. refuse the
   number, say why, offer period flow:
   "잔액은 계산할 수 없어요. 통장 시작 잔액을 받은 적이 없어서, 기록된 건
   흐름(들어온/나간 돈)뿐이에요. 이번 달 흐름은 보여드릴 수 있어요: [entity-flow 결과]"
   never sum in-out into a balance-looking figure, never a lifetime net.
   owner offers an opening snapshot -> new lane (owner decision + write
   path); record nothing here.
2. E-5: every number carries its criterion or its absence.
   - entity-flow / entity-time already emit `criterion=` lines.
   - plain spend totals (`spend-day/week/month`) emit none -> comparison
     ask ("목표 대비 어때", "기준 안 넘었어?") = run the total verb AND
     `criterion-list ledger` in the same turn.
   - `criterion=none` -> say no criterion is declared, frame as raw flow:
     "이번 달 카페 지출은 132,000원이에요. 아직 기준(목표/한도)을 정해둔 게
     없어서 원자료 흐름만 보여드려요. 기준을 정하시면 다음부터 대비로 말씀드릴 수 있어요."
   - criterion present -> quote it next to the number ("한도 15만원으로
     잡으셨는데 이번 달 132,000원이에요"). observational; no 달성/미달 of
     your own.
   - never invent / estimate / suggest a criterion value. proposing that the
     owner declare one is fine.
3. 무귀속 (deltas with no entity) stays visible whenever non-zero ("수단
   기록이 없는 지출 10건 359,250원은 따로예요"). never fold, drop, or rename
   it (기타 is a category, 무귀속 is not).
4. wording follows the verb's domain state: "관리 중" ONLY when
   `state=managing`; unknown / collecting -> neutral ("기록상", "장부 기준").
5. `대상 연결 0건` = no time data: "아직 대상별 시간 기록이 없어요", stop;
   never estimate from calendar memory, never "0분". non-zero `excluded=` ->
   mention some blocks were excluded for broken weights.

## report

- money: period first, per-target 유출/유입 lines, 무귀속 when non-zero,
  criterion sentence. simple table -> fenced code block.
- time: minutes -> hours (70분 -> 1시간 10분).
- user language only; never verbs / flags / views / exit codes / tokens
  (say 현대카드M, not its token).

## siblings

- ledger capture / weekly ledger session / card + fixed-cost setup skills
  are DETACHED (pack 2.3.0, owner 2026-09-08; re-onboarded later). data and
  verbs remain: plain totals = `spend-day/spend-week/spend-month`
  (+ `spend-unknown` sweep). never hand off to ledger-log / ledger-session /
  ledger-setup.
- who is OO -> person-manage. persons are not crossing targets yet (no
  person entities).
