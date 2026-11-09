# ledger-view: balances, cash, card bills, installments

Before card identity, purpose or reimbursement answers, read
`LEDGER_ROOT="${PROJECT_ROOT:-$(pwd)}" database/kit_life.sh purpose-get ID`
and use its `instrument_facts`: registered kind, owner roles/purpose, scope
and linked account. Follow `../../ledger-setup/reference/purpose-order.md`
for authority and ambiguous memory evidence. A business-use card is not an
employer-reimbursed company card. An issuer-only note never changes spending
or establishes a payment-level settlement.

Detail for hard rules 1 and 2 of SKILL.md. Same authority as SKILL.md.

## verbs

```
balance-set <instrument> <amount> [--as-of YYYY-MM-DD]   owner said a balance
      (hand). --auto --source <app> only when an app/export read it.
cash-set <amount> [--as-of D]                  cash on hand (hand)
card-bill-set <card> <amount> --due YYYY-MM-DD [--as-of statement date]
balance-show --say                             owner-ready lines, verbatim
card-bill-month [YYYY-MM] --say                "이번 달 카드값", verbatim
installment-add <card> <total> <months> [--first YYYY-MM] [--rate %]
      [--name N] [--date YYYY-MM-DD]          owner said an installment buy.
      credit cards only. --first = the first billed month (default: the
      month after the purchase). --rate only when the owner says it is not
      무이자. EXISTS-style refusal "already recorded" -> confirm it is a
      second real purchase, then retry with --new.
installment-list --say [--month YYYY-MM] [--all]
      per item n/N개월째, 이번 달, 남은 금액, 끝나는 달 + per-card 할부 합계,
      verbatim
installment-done <ref> | installment-cancel <ref>    finished / paid off |
      purchase cancelled. ref = the name from the list or #id
installment-detect --say                       candidates, verbatim, with
      their two buttons
installment-confirm <spend:N|intake:N> [--months N] [--first YYYY-MM]
installment-dismiss <spend:N|intake:N>
```

## rule 1 -- BALANCES ARE READINGS, NEVER DERIVED

a balance exists only as a reading the owner (or an app) gave: balance-set /
cash-set / card-bill-set.

- a balance question -> `balance-show --say`, relay verbatim. a line
  says 잔액 기록 없음 -> say there is no balance on record for it and
  offer to note the amount; never 0, never a sum of flows.
- every amount keeps its date (기준 date) and its provenance (직접 입력
  vs 자동); never merge the two, never drop the date of an old value.
- "지금 추정" is reading + recorded flow since: say 추정, never as the
  balance. own-account transfers are not spend or income and are not in
  it (the verb says so; keep that sentence).
- the owner states a balance -> `balance-set` (cash: `cash-set`) in the
  same turn, as-of today unless they name a day. a debit card's money is
  its account (the verb refuses and names it: record there).
- a card statement -> `card-bill-set <card> <amount> --due <date>`; the
  bill is not the sum of spend.
- "이번 달 카드값" -> `card-bill-month --say`, verbatim: a card with a bill
  reading answers with the bill; one without answers with recorded spend
  and says it is not the bill. never call recorded spend 카드값/청구액.

never sum in-out into a balance-looking figure, never a lifetime net.

## rule 2 -- INSTALLMENTS ARE DEBT LINES, NOT SPEND

one installment = one purchase on a credit card: total, months, rate
(무이자 = 0%), first billed month.

- the owner says they bought on 할부 ("24개월 할부로 샀어") -> the
  core-log ledger adapter records the purchase (full amount, note
  '할부 N개월'); card and months known -> `installment-add` in the same
  turn, no question. only when they are not known: `installment-detect
  --say` and offer its item. never ask the monthly amount: the verb
  computes it.
- "할부 얼마 남았어 / 뭐 있어 / 몇 개월째" -> `installment-list --say`,
  verbatim. keep the rounding sentence (remainder on the first month is
  an assumption of card practice) and, when shown, the fee sentence.
  남은 금액 is after this month's charge; say so if asked.
- detection only PROPOSES. `installment-detect --say` items go to the
  owner with their buttons ([할부로 적기] [아니야]); confirm ->
  `installment-confirm <ref>`, no -> `installment-dismiss <ref>`. never
  confirm on the owner's behalf, never batch-confirm.
- "다 갚았어 / 끝났어" -> `installment-done`; "취소했어 / 환불" ->
  `installment-cancel`. a name two items share -> the verb refuses with
  #ids: ask which.
- "이번 달 / 다음 달 카드값" -> `card-bill-month [YYYY-MM] --say`,
  verbatim. a card with a bill reading answers with the bill (installments
  are inside it already). without one, the line is an ESTIMATE = recorded
  one-off spend + the installment charges due that month: say 추정, never
  청구액. the installment purchase itself is not counted at full amount.
- an installment is a 0% debt line: in any money advice its monthly
  charge is a minimum payment that comes first; never suggest skipping it.
