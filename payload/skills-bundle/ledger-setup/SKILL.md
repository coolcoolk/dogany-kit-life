---
name: ledger-setup
display_name: 가계부 온보딩·등록
description: >-
  Own ledger onboarding and payline registration. Enter lane A when the owner wants to start or use the ledger or manage
  money while module onboarding is outstanding (any verdict except NO-OFFER complete), including when module management
  is off. Intent and kit state define entry regardless of wording; no item/amount is needed for setup and no generic
  capture question may replace this lane. If the onboarding state is not known, first run
  service/kit_life/ledger/onboarding verdict before selecting the onboarding lane. NO-OFFER complete must never run next
  or enable a fresh flow for ordinary use intent. For every verdict except NO-OFFER complete, including
  management-declined, declined-exhausted and recording-active, the owner's entry begins with database/kit_life.sh
  manage-set ledger on, then service/kit_life/ledger/onboarding next. A SessionStart onboarding signal instead requires
  verdict before the existing offer flow, after the owner's current request; NO-OFFER stays silent. An active
  onboarding's file, answers and corrections continue its current step. Completed onboarding routes ordinary use to
  ledger-log/session/view; only an explicit restart starts over. Other entry intents: register/remove a card, account,
  fixed cost or recurring income; change guards, collection automation or sweep timing; accept the registry's
  after-a-month proposal. Also any card/account identity, role, purpose, reimbursement or exclusion question ("이 카드
  뭐야", "법인카드야?", "비용처리돼?"): read purpose-get first. Owns source/history intake, evidence-backed merchant kinds, refunds, instrument roster,
  monthly history, reduction plan/goals, method-*/fixed-*/guard-*, stream-stop, spend-person* and funding-set. The
  service writes EVERY onboarding word and setup card; this skill adds no intro. NOT single spend capture or screenshots
  outside the onboarding (ledger-log) or the review (ledger-session).
---

# ledger-setup

Lanes: (A) module onboarding, run once when the module is turned on;
(B) payline registration, any time; (C) money facts in the owner's words.
All data access via kit verbs (`$PROJECT_ROOT/database/kit_life.sh`) or the
module's service (`$PROJECT_ROOT/service/kit_life/ledger/`). No raw SQL.
Any card/account identity, role, purpose, reimbursement or exclusion answer (any lane): read
`purpose-get` first; `instrument_facts` outrank memory notes. Rule: `reference/purpose-order.md`.

## A. module onboarding

The service runs the conversation: it decides the steps, renders every word,
the setup card and the buttons, and lands each answer in the kit when it is
recorded. You carry the owner's words into it. Do NOT introduce, summarize,
preface, re-ask or compute anything yourself -- every number the owner sees
comes from the service.

### steps

1. Which lane? First run `service/kit_life/ledger/onboarding verdict`.
   - Explicit intent to start over, during or after the flow: `onboarding restart`,
     then `onboarding next`; intent decides, not matching particular words.
   - The owner asks to start/use it: `NO-OFFER complete` (exit 3) -> hand to
     ledger-log / ledger-view / ledger-session as requested; no next or restart.
     Every other verdict (including management-declined, declined-exhausted and
     recording-active) -> `database/kit_life.sh manage-set ledger on`, then
     `service/kit_life/ledger/onboarding next` and go to 2. Captured facts return
     in the service messages; never ask for them again.
   - Offer lane (the "[ledger-onboarding]" SessionStart line, after the
     owner's own request of that turn is done):
     `service/kit_life/ledger/onboarding verdict` -> exit 3: stop, say nothing.
     exit 0: `onboarding offered`, then `onboarding next --offer` and go to 2.
     Its first message (`start`) IS the offer; never add your own.
2. New starts use the history/reduction flow: parts -> source (only without
   imported history) -> history intake -> refunds and grouped instrument
   roster -> remaining identification questions -> monthly history and
   conclusion -> reduction steps -> final confirmation and goals.
   `reference/purpose-order.md` is the current flow and lookup contract.
   Before relaying identification or plan cards, run
   `database/kit_life.sh ledger-merchant-kinds --today YYYY-MM-DD`, look up
   pending business identities, and save each evidence-backed kind with
   `ledger-merchant-kind-set NAME KIND --source web|owner --evidence EVIDENCE`.
   Web facts need a checked source URL; owner facts need original owner words.
   If lookup cannot decide, save `unresolved` with the query and reason, then
   retain the issued per-item ask. Re-read the plan and `next` after writes.
   Never infer a kind from imported categories or force golden numbers.
   Already-started legacy sessions may still issue the v6 review questions;
   answer only the actual issued ID. Ask ONE question per message. Loop:
   a. Run `onboarding next` -> `{question, step, of, done}`. `done: true` ->
      go to 3. `question: null` with `lookup` -> SILENT: run `lookup-set`
      (below), then `next` again. Never tell the owner you looked up or
      checked anything.
      - `lookup: {app, what, official}` -- owed only on the file step after
        an actual source app (`export` first; `how` after [how]). Look it up live
        in this turn: the app's official help first (`official` lists where
        it lives), else recent posts. `what: export` -> `onboarding lookup-set <app> export
        available|unavailable [--source official|posts] [--url URL]`; could
        not tell -> `... export failed`. `what: how` -> `onboarding
        lookup-set <app> how found --source official|posts --steps-json
        '["step", ...]' [--url URL]` (1..12 short steps in the owner's
        language, as the source says them; never invented), or `... how
        failed`. The verdict is kept 30 days; the message names its source
        and date itself. Another app newly offering an export, seen while
        looking: `onboarding product-note "<app>: <what> (<where seen>)"` --
        a note for the product, never offered to the owner.
   b. Before relaying `review_roster`, recall the owner's already-stated payment
      links from memory. For each actual statement, call the existing
      `database/kit_life.sh funding-set TARGET SOURCE --origin owner-stated`.
      Resolve names against returned methods/aliases; never guess a link from
      an issuer or bank name. This also supports a debit card TARGET without
      turning it into a wallet. Then run `onboarding next` again: the service
      refreshes the roster with those links. Show the full list first. After
      confirmation, relay each `review_funding_<method-id>` question from
      `next`, one per message. Funding questions show numbered card/account
      lists and have no keyboard: relay `question.ask` VERBATIM, ALONE, with
      no buttons. The owner may answer with numbers or names, including
      several sources and which is primary (e.g. 주로 1번, 가끔 7번).
      Evidence-linked items are skipped. Never save a guessed link until the
      owner answers. Other questions use only `question.choices` as buttons;
      never add your own (no exit hatch).
   c. Record with `onboarding answer <question.id> <value> [--said WORDS]
      [--fix N=...]` (`--said` = their words when they typed), then
      `onboarding next`. A tap -> the tapped choice's `value`. A question
      from the owner instead of an answer -> answer it in one or two
      sentences (a money question: rules below), then `onboarding next`
      (the same message comes back).
   Values:
   - `review_funding_<method-id>`: pass the owner's answer unchanged as
     the value and `--said WORDS`. Numbers refer to the displayed list,
     names resolve against `question.sources`, and 주로 marks the primary
     source. The service saves all resolved sources through the existing
     `funding-set TARGET SOURCE --origin owner-stated --said WORDS` path,
     with `--main` on the primary. `unknown` leaves it unlinked. An ambiguous
     answer stays pending; clarify with the same list. Carry later corrections
     through the same funding-set path after resolving the returned name.
   - `start`: yes | no (no = a decline; also `database/kit_life.sh
     manage-set ledger off`, recording goes on).
   - `parts`: `record` | `past` | `plan` | `all`.
   - `source`: the returned regional app token (such as `banksalad` or
     `toss`), `other`, or `none`; never the obsolete `app` value.
   - `aim` is only for an already-started legacy session, never a new start.
   - `file`: `how` (shows the export steps) or `new` (start without a file).
     The owner's FILE -> save it, then `onboarding import <path>` (not
     `answer`); the service returns the one ack line to send. A refusal
     comes back as the same message with the reason -- send it. The
     Banksalad mail zip, an xlsx or a CSV all go in as they are.
     `error: password` -> the message IS the password ask: send it; when the
     owner answers, run `onboarding import <path> --password-stdin` with the
     password as the only stdin line. NEVER put the password in an
     argument, a file, a note, memory or a reply; never repeat it back.
     `password_wrong` -> send the message again (one more ask). Once the
     import ran with it: there is no path from this kit to delete the
     owner's chat message, so add ONE short line to your next message
     saying the password was used only to open the file and was not stored,
     and that they can delete their message. If your channel gives you a
     delete for the owner's inbound message, delete it instead and say so
     in that line.
   - `confirm`: `company` or `mine`.
   - `review_roster`: `ok`, or JSON `{"corrections":[{"id":N,"name":"...","kind":"account"}]}`. Reuse returned funding links; owner corrections land through `funding-set --origin owner-stated`, followed by `onboarding next`. Do not survey them again.
   - `review_living`: `ok` applies the service's three-month proposals; JSON `{"one_off":[N],"subscriptions":[N]}` corrects them. Each subscription result returns its persisted `stream_id`. An already-issued living answer can be corrected immediately with JSON after advancing. Relay write failures; never say a stream was saved without the returned id.
   - `review_card_N`: its own `skip` leaves only that card unresolved; `capture` is optional. `captured` follows a saved installment capture. A statement's known dates go as JSON `{"start":"YYYY-MM-DD","end":"YYYY-MM-DD"}` (end exclusive); then show the revised card. Never call a residual an installment amount.
   - Owner insurance facts in `--said` return structured `insurance`; the service displays it only inside the card summary. Do not create an insurance notice or question. `pending`/`matched` is not deducted. Merchant-refund totals never establish that insurance was counted.
   - `diagnosis`: aim save_more: a lever id from `question.levers`
     (several: joined by `,`), `all`, `none` or `fix`. aim save_for / flow:
     `ok` or `fix`. The owner's corrections =
     `--fix N=stop|keep:must|reducible|droppable|amount:<won>|class:<c>|name:<text>|person:<name>`,
     one `--fix` each, N = the stream number on the card.
     `answer diagnosis fix --fix ...` returns the same card, corrected.
   - `target`: `onboarding answer target '{"item":"...","amount":WON}'`,
     or the owner's plain words (= the item).
   New starts: use only the values or JSON structure on the issued
   question. Legacy review value formats above apply only when that legacy
   question is actually issued. Never invent a question or amount.
3. `done: true` -> run `service/kit_life/ledger/onboarding apply`, then send
   `ready`. Nothing else.

### after the onboarding

- The weekly retro may carry one line offering the after-a-month proposal.
  Owner says yes -> `onboarding propose`, then the same loop from 2.
- Card payment SMS automation is NOT part of the onboarding. Only when the
  owner explicitly asks for it AND the Dogany app connection is present:
  `onboarding apply --auto <issuer[,issuer]>`; point them to the app's own
  automation setup. Opt-out ("문자 자동 기록 꺼줘"): `onboarding apply` with no
  `--auto`. Android: not yet.
- Sweep time change: `onboarding apply --sweep <mon..sun>@HH:MM | retro`.
- Friend-channel / non-owner instance: never offer the automation (D7).

### rules

- The service refuses an answer for any id it did not hand out; never add,
  merge or skip a step.
- Hard boundary: never open `kit_life.db`, run `sqlite3`, `unzip` or
  hand-parse a sheet, never sum anything yourself. Every number comes from
  the service or a kit verb.
- A money question mid-flow ("<ITEM> 어디 갔어?"): run `kit_life.sh
  ledger-find <text> [--month M]` first and answer in one sentence from it.
- Never hand the owner work you can do (no "앱에서 확인해 보세요"). Ask only
  for a fact only the owner can see, as one value.

## C. money facts in the owner's words (any time)

Recorded by the core-log ledger adapter, management on or off:
`.claude/skills-bundle/core-log/reference/ledger.md` (recurring
payments, stream stops, person rules, wallet and card links). Never delete
a stream or drop it from a total yourself.

## B. payline verb surface

```
method-add <name> <kind> [aliases] [--billing-day N] [--link <account>]
  find-first on name. kind: credit_card|debit_card|account|cash|easy_pay.
  debit_card -> --link required.
method-list | method-upd <id> field=value | method-link <card> <account> | method-del <id>
fixed-add <name> <amount> [category] [--next-due D] [--cycle monthly|yearly]
          [--method ref] [--direction expense|income] [--confirm] --find-first
  --find-first: same direction + same normalized name exists ->
  EXISTS<TAB>id (exit 3): revise it with fixed-upd instead.
fixed-list [--direction ...] [--json] | fixed-upd <id> field=value | fixed-del <id>
guard-get <key> [scope] | guard-set <key> <value> [scope]
methods-seen | fixed-detect | flow-picture | ledger-records   (read, JSON)
```

- `method-add` creates or repairs the card's entity and Korean display label
  in the same kit transaction.
- card BILL payment is never a spend (double count); transfers are excluded.
- guards (read from table, never hardcode): `import_auto_accept` (keep 0),
  `dup_window`, `entry_amount_ceiling`, `irregular_floor` (when set).

## display discipline

Never show the owner: verb names, flags, table/field names, source keys,
"intake", "파싱", "배치", "guard", `key=value`. Korean everyday words only.
