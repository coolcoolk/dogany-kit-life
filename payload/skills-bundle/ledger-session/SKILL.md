---
name: ledger-session
display_name: 가계부 정리
description: >-
  Review collected ledger evidence when the owner requests reconciliation or monthly closing, continues an active
  review, or the daily/weekly retro registry supplies its ledger section. Choose the lane from kit intake and review
  state. Daily retro reviews only automation-collected per-card items briefly; weekly retro runs the screenshot/export
  sweep and presents all pending items as one table with one answer. Group payment-intermediary identities into one
  same-line-answer block. The monthly session reviews the month, sweeps business-suspect rows and reclassifies. A bare
  start/use request while ledger onboarding is outstanding belongs to ledger-setup; completed onboarding alone never
  starts another onboarding. NOT single spend capture or reading screenshots/files (ledger-log) or setup (ledger-setup).
---

# ledger-session

All data via `$PROJECT_ROOT/service/kit_life/ledger/` and kit verbs
(`$PROJECT_ROOT/database/kit_life.sh`). No raw SQL.

## retro lane

Owner design (2026-09-24): every week one sweep (a screenshot or a Banksalad
file); the optional per-card SMS automation fills the days in between and is
checked in the daily retro. Business suspects are sorted "추가되는 즉시 혹은
일일회고 주간회고 통해서".

Placement: after the retro's task/event stages, before its close. One block,
never interleaved with event questions.

0. Unread forwarded SMS first (model lane), so they land in the same table:
   `service/kit_life/ledger/sms-stage list` -> for each ref: `sms-stage show <ref>`,
   read date / amount / merchant (/ 취소 = `"operation":"reversal"`), then
   `sms-stage stage <ref> --data '{"date":..,"amount":..,"merchant":..}'`.
   Not a payment (ad, 결제예정 안내) -> `sms-stage drop <ref>`.
   `rejected` = your reading is not in the text: read again once; still
   rejected -> leave it and say one line to the owner.
1. The kit invokes `retro-section` from the registered provider. It supplies
   the owner-facing Korean block (automation news, weekly sweep ask, and
   numbered table); if it supplies nothing, say NOTHING about the ledger.
1b. A line in the block ending in "이 번호를 등록할까요?" is the one sender
   question. "등록" / "응" -> `service/kit_life/ledger/sms-guide sender-confirm
   <sender as shown> yes` (its held SMS go in; run `retro-section` again for
   the table); "등록 안 함" / "아니" -> `... no`. Unclear -> nothing.
2. Weekly: if the block opened with the sweep ask, wait for the owner's
   screenshot / file -> ledger-log import lane (it stages and shows the table again
   with a new DIGEST). "이번 주는 없어" / "나중에" -> go on with the table as is.
3. ONE answer to the table. Map it:
   - "좋아/응/다 맞아" -> apply as proposed.
   - "3번 빼" / "3번 아니야" -> `--exclude 3`. "3번 나중에" -> `--defer 3`.
   - "5번 사업" -> `--business 5`; "5번 개인" -> `--personal 5`.
   - "2번은 따로야" (a "N번과 같은 결제" row that is really a second payment) -> `--new 2`.
   - rows that said "어느 기록/결제" -> owner names it; look it up with
     `spend-day <date>` and pass `--attach N=s<id>` / `--reversal N=s<id>`;
     unresolved -> `--defer N`.
   - methods the kit already resolved are never asked about; the review
     lists only truly unresolved rows. A row that says "어느 카드인지" ->
     owner names the card -> `method-upd` that card's aliases (ledger-setup
     rule), `--defer N` this time.
   - the grouped block "결제대행이라 뭘 샀는지 안 보이는 결제" (쿠페이,
     네이버페이, PG names) -> the owner's one line "2 휴지, 3 점심" ->
     `--what 2=휴지:생활용품 --what 3=점심:식생활` (one per row; name = the
     owner's words, category = one of 식생활 / 카페/간식 / 생활용품 / 이동 /
     건강 / 학습 / 콘텐츠/취미 / 주거/인프라 / 여행 / 관계 / 기타; unsure ->
     name only). Rows the owner skips: pass nothing. Never ask about them
     again yourself: daily -> the weekly retro asks once more; weekly -> the
     service writes 기타. Rows showing "지난달처럼 X" are filled from memory.
4. `service/kit_life/ledger/review apply --lane <lane> --expect <hex> [--today <date>] [flags]`
   (`--today` = the date retro-section was given, if any.)
   Error "pending list changed" -> show again, never guess.
5. One summary line: "가계부에 N건 적고, M건은 뺐어요." (+ "K건은 다음에 봐요.")
   Named intermediary rows: + "쿠페이 12,900원은 휴지로 적어뒀어요." Daily
   rows left unnamed: + "쿠페이 건은 주간회고 때 한 번 더 여쭐게요."
6. Weekly only: `spend-unknown`; rows -> one list, one answer ->
   `spend-upd <id> scope=business|personal`. Goal: unknown remainder 0.
7. The weekly retro's month block is `ledger-diagnosis` (first weekly retro
   of a month), already inside the retro section and `month-close`. Send it
   as it comes; corrections in the owner's words -> ledger-setup lane C.
8. A block line offering the after-a-month proposal -> ledger-setup
   "after the onboarding".

Rows marked "(문자를 제가 읽었어요)" and captures introduced by the single top
line "캡처에서 읽은 N건이에요." are model readings; the owner's answer is
their confirmation.

## session lane (on request)

1. `spend-week <monday>` -> "이번 주 이렇게 잡혔어요, 빠진 거 있어요?"
2. Missing entries -> `spend-add ... --source batch` (duplicate gate applies).
3. Run the retro lane (weekly) for anything pending.
4. Reclassify 기타 / (미분류) -> `spend-upd <id> category=<name>`.
5. First session of a month: `spend-month <prev YYYY-MM>`; compare
   `fixed-list` against the month's rows and name missed fixed costs.
   (fixed-due / fixed-paid / perf-status are not in kit 2.28 -- G8.)
   Then `service/kit_life/ledger/month-close <prev YYYY-MM>` and add its
   lines as they are (the month's diagnosis card, a subscription tier
   change said once, usage charges counted as living costs). Exit 3 =
   nothing to add.

## report format

- weekly: 총지출 / 카테고리별 / 전주 대비 / 건수.
- monthly: the diagnosis card (`ledger-diagnosis`) as the service renders it.
- commentary = observations on recorded data only; no finance advice.

## discipline

- the collector never pushes; this skill never opens the topic outside a
  retro, the owner's own request, or the sweep message the owner scheduled.
- intermediary payments are never asked at payment time (ledger-log and
  its import lane included): only the retro's grouped block asks.
- owner-facing text = Korean everyday words; never verbs, flags, source keys,
  "intake", "digest", item ids, SMS texts.
- copy lint: no internal words in labels or buttons ('업스트림', '제보',
  'intake', verb names).
- no outbound network in this skill; `--poll` and `sms-stage` read the
  host's receiver store read-only.

Before imported-history identification or plan cards, follow
`../ledger-setup/reference/purpose-order.md`. Read `ledger-merchant-kinds`,
check pending business identities and save evidence with
`ledger-merchant-kind-set --source web|owner`, then re-read the plan and
pending question. Keep unresolved businesses on the existing per-payment
ask. Imported categories and golden amounts are not evidence of kind.
