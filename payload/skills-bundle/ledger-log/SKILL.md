---
name: ledger-log
display_name: 소비 기록
description: >-
  Capture an actual expense, refund or purchase/payment fact in the same turn, and answer ordinary
  category/day/week/month spend totals or fixed-spend status queries from the kit. Correct an existing payment class,
  category, card answer, intake decision, installment, balance reading or split; undo an earlier answer in-session.
  Record content, correction and query intent
  define entry, independently of module activation; confirmation uses ledger terms. A bare request to start/use the
  ledger or manage money with no item/amount/payment content, while ledger onboarding is outstanding (any verdict except
  NO-OFFER complete), hands to ledger-setup lane A even when management is off. Do not ask generic item, place, amount
  or payment-method fields for that setup intent. Once onboarding is complete, a general use request does not restart
  it. Import lane (was ledger-import, kit_life 2.46.0): Toss / Banksalad / Kakao Pay / Naver Pay / card app 이용내역 or the
  owner's named app, including Banksalad .csv exports. read reference/import.md first for spend-list screenshots,
  spending export files, historical intake requests or requests to inspect card messages; use ledger-setup instead when
  a file answers the outstanding onboarding step. Weekly sweep captures use the same import lane. NOT the retro review
  of collected SMS / screenshot / CSV items (ledger-session) or setup (ledger-setup). Actual spend facts continue to
  capture while setup is outstanding.
---

For start/use intent without payment content: run `service/kit_life/ledger/onboarding verdict`; every verdict except `NO-OFFER complete` -> ledger-setup lane A. Complete -> the requested normal read/capture, no next/restart. Actual spends skip this setup guard.

# ledger-log

C1 capture: owner says a spend -> `spend-add` now, 1 turn, 0 questions by
default. Also day/week/month totals.
All DB access via `$PROJECT_ROOT/database/kit_life.sh`. No raw SQL.
`LKIT="${PROJECT_ROOT:-$(pwd)}/database/kit_life.sh"`.

## record

Every money fact (spend, refund, recurring payment, card / balance /
installment fact) is recorded by the core-log ledger adapter:
`.claude/skills-bundle/core-log/reference/ledger.md` (amount / date /
scope / category / method parsing, `spend-add` and its duplicate gate,
`fixed-add --find-first`). Follow it; this skill adds the reads below.

## correct / undo

Owner corrects an existing row -> read it first, resolve its id, write in
this turn, read back. Never capture a second payment for a correction.
Read `../core-log/reference/ledger.md` "In-session corrections and the
audited verb index" for class rules, guards and other shipped verbs.
Calculation class differs from display category. Investment = `class=spend`
with existing verified investment destination evidence; never
`class=investment`. Category alone proves no destination. Get category
from kit facts, never invent one. Class edit: omit `kind`.
Examples below use resolved ids; `$LKIT` = the helper above.

- class: `$LKIT spend-upd 42 class=spend`; category: `$LKIT spend-upd 42 category=<kit_category>`.
- class undo: `$LKIT spend-class-undo 42`; restores prior owner marker and paired inflow.
- bill answer: `$LKIT ledger-card-answer 42 mine`; reverse: `$LKIT ledger-card-answer 42 undo` (also `company`).
- intake undo: `$LKIT intake-undo 7`; reopens confirm/exclude; follow its returned landing state.
- installment terms: `$LKIT installment-upd 3 total=120000 months=6 --json`.
- installment reopen: `$LKIT installment-reopen 3 --json`; same identity after done/cancel.
- reading delete: `$LKIT balance-del 9 --json`; exact measure id, never the instrument id.
- split read: `$LKIT ledger-split get --data '{"spend_id":42}'`.
- split reopen: `$LKIT ledger-split change --data '{"spend_id":42}'`.
- split replace: `$LKIT ledger-split answer --data '{"spend_id":42,"answer":"no"}'`; follow returned steps.
- split undo: `$LKIT ledger-split undo --data '{"spend_id":42}'`.
- receipt match: `$LKIT ledger-split transfer-answer --data '{"transfer_id":8,"answer":"unrelated"}'`.
- receipt-match undo: `$LKIT ledger-split undo --data '{"transfer_id":8}'`.
- weekly answer: `$LKIT ledger-split weekly-answer --data '{"today":"2026-11-07","shares":{"5":10000},"answer":"received"}'`; exact shown shares only.
- weekly undo: `$LKIT ledger-split undo --data '{"shares":{"5":10000}}'`.
- clear no-split: `$LKIT ledger-split no-split --data '{"person_id":2,"enabled":false}'`.

Undo refused after later accounting edits -> undo those first. Split with
receipt allocation -> follow allocation undo in the shared reference first.
Relay existing kit/service copy; no verb names, ids or flags to owner.

## insurance reimbursement

Insurance receipts are distinct from merchant refunds. Find the actual receipt and medical expense with `ledger-find` before linking. Record the owner's stated amount with `ledger-review-answer --month YYYY-MM --data '{"kind":"insurance","amount":WON,"said":"..."}'`; add `receipt_id` and `spend_id` only for confirmed matching rows (cross-month medical spend is supported). This stores a pending fact when the receipt is absent; it never invents an income row. Relay `status` and `counted`: only `linked` with `counted:true` permits saying the medical cost was reduced.

## totals

```
spend-week <monday> [--all] | spend-month <YYYY-MM> [--all] | spend-unknown
spend-del <id>
criterion-list ledger              REQUIRED beside any "목표 대비" answer
```

- aggregates default personal-only; `--all` includes business/unknown.

## 목표 대비 answers

Run the total AND `criterion-list ledger` in the same turn. No criterion ->
say so plainly and show the raw flow; never invent a target. Wording stays
observational.

## discipline

- capture never routes to generic memory; never "기억해둘게요".
- absence claims only after spend-day/spend-find.
- no outbound network in this skill.
- owner-facing text = Korean everyday words; never verbs/flags/tables.

## import lane (screenshot / Banksalad file / card SMS on demand)

Read reference/import.md when the owner sends a spend-list screenshot or an
export file, asks to backfill old rows, or asks to check card SMS now. It
reads, stages, reports ONE summary line; every decision waits for the review
table (ledger-session).

## siblings

retro review of collected items -> ledger-session. screenshot / CSV -> the
import lane above (reference/import.md).
cards/fixed costs/onboarding -> ledger-setup. per-card flow, balances,
cash on hand, card bills and "이번 달 카드값" -> ledger-view.

Before imported-history identification or plan cards, follow
`../ledger-setup/reference/purpose-order.md`. Read `ledger-merchant-kinds`,
check pending business identities and save evidence with
`ledger-merchant-kind-set --source web|owner`, then re-read the plan and
pending question. Keep unresolved businesses on the existing per-payment
ask. Imported categories and golden amounts are not evidence of kind.
