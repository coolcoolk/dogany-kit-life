---
name: ledger-view
display_name: 자원×대상 흐름
description: >-
  Read resource-by-entity flows when the owner asks how money or time crossed a target or instrument, including
  per-person spending/time, per-card/account incoming or outgoing flows, recurring flows or where an item went. Also
  read or capture stated balances, cash holdings and card bills and manage installment facts and remaining installments
  from the kit's stored instruments and obligations. Also any card/account identity, role, purpose, reimbursement or
  exclusion question ("이 카드 뭐야", "법인카드야?"): read purpose-get first. Entry depends on requested breakdown and
  available facts, not wording. A content-free request to begin ledger use while onboarding is outstanding hands to
  ledger-setup; completed setup alone does not restart it. NOT for spend capture or plain category/day/week/month spend totals (ledger-log), the
  retro review of collected items (ledger-session) or card / fixed-cost setup (ledger-setup) -- the ledger module's
  siblings, back in the kit since 2.46.0.
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

## money questions (ledger v6 reads)

- "OO한테 얼마 썼어" -> `spend-link-sum person <PERSON> --month M`, or
  `ledger-diagnosis` for the month block.
- "정기 지출 얼마야" -> `ledger-diagnosis`.
- "<ITEM> 어디 갔어" -> `ledger-find <text> [--month M]`.

## hard rules

1. BALANCES ARE READINGS, NEVER DERIVED. a balance exists only as a reading
   the owner (or an app) gave (balance-set / cash-set / card-bill-set);
   never 0, never a sum of flows, never a lifetime net; keep date + source.
2. INSTALLMENTS ARE DEBT LINES, NOT SPEND. one installment = one credit-card purchase (total,
   months, rate, first billed month); detection only PROPOSES; "카드값" is the bill or a labelled 추정.
   read reference/balances-installments.md when the owner asks or states a balance, cash on hand, a card bill, '이번 달 카드값', or an installment (할부)
   (it holds balance-set, cash-set, card-bill-set, balance-show, card-bill-month, installment-* verbs + full rules 1-2).
3. E-5: every number carries its criterion or its absence.
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
4. 무귀속 (deltas with no entity) stays visible whenever non-zero ("수단
   기록이 없는 지출 10건 359,250원은 따로예요"). never fold, drop, or rename
   it (기타 is a category, 무귀속 is not).
5. wording follows the verb's domain state: "관리 중" ONLY when
   `state=managing`; unknown / collecting -> neutral ("기록상", "장부 기준").
6. `대상 연결 0건` = no time data: "아직 대상별 시간 기록이 없어요", stop;
   never estimate from calendar memory, never "0분". non-zero `excluded=` ->
   mention some blocks were excluded for broken weights.

## report

- money: period first, per-target 유출/유입 lines, 무귀속 when non-zero,
  criterion sentence. simple table -> fenced code block.
- time: minutes -> hours (70분 -> 1시간 10분).
- user language only; never verbs / flags / views / exit codes / tokens
  (say 현대카드M, not its token).
- installments: name, card, n/N개월째, 이번 달 금액, 남은 금액, 끝나는 달;
  per-card 할부 합계 last. 무이자 said as 무이자, a rate as 연 N%.

## siblings

- spend capture, screenshots / files and plain totals
  (`spend-day/spend-week/spend-month`) -> ledger-log; retro review of
  collected items, `spend-unknown` sweep, monthly close -> ledger-session;
  cards / fixed costs / module onboarding -> ledger-setup (module_ledger
  siblings, back in the kit since 2.46.0).
- who is OO -> person-manage. persons are not crossing targets yet (no person entities).

Before card/account identity, role, purpose, reimbursement or exclusion answers (`purpose-get` first;
`instrument_facts` outrank memory) or imported-history identification or plan cards, follow
`../ledger-setup/reference/purpose-order.md`. Read `ledger-merchant-kinds`,
check pending business identities and save evidence with
`ledger-merchant-kind-set --source web|owner`, then re-read the plan and
pending question. Keep unresolved businesses on the existing per-payment
ask. Imported categories and golden amounts are not evidence of kind.
