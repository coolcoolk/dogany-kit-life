# core-log -- ledger adapter (money facts)

Before replying after capture, apply the common SETTLE ON EVIDENCE contract
from AGENT: saving facts alone does not finish an activity report. Read
`.claude/skills-bundle/schedule-log/reference/completion-chat.md` for the
shared completion check. A PLANNED item settled this turn: its single
completion reply overrides this adapter's capture-report/card instructions.
No open task/routine matches: no completion reply at all, hand over to
reference/episode.md. Tool work stays silent until the final line.

The record rules for money. ledger-log, ledger-setup and ledger-view point
here for capture; management (onboarding, sweeps, reviews, diagnosis,
automation) stays with them and only while `manage-state ledger` is on.

## Instrument purpose in later chat

When the owner describes what a card, account or wallet is used for, follow
`../../ledger-setup/reference/purpose-order.md` ("Instrument purpose answers,
including later chat"). Save only its fixed vocabulary labels through
`purpose-set ID --tags "LABEL,LABEL" --memo "OWNER WORDS" --origin owner-stated`.
The memo is internal evidence; never relay it as the purpose. If the kit's
lexicon cannot map the words, choose semantic `--tags` only from its returned
`normalized.vocabulary`; if still unclear, ask once with those label buttons
and keep the guessed purpose marked 추정. This describes the instrument,
not a new payment or a category allocation for old payments.

## Contract and warranty capture

For a contract, bill, policy, recurring plan or device warranty, read
`service/kit_life/contracts.md` and use the contract service in the same turn.
Save partial facts immediately and relay its Candidate A card and vertical
buttons. A contract creates no payment. A receipt keeps the ordinary purchase
and ledger review path, then enriches the existing possession as a warranty
contract without another purchase or stock refill. No warranty duration is
inferred from a receipt. Source attachments are retained by the kit proof store;
chat resend remains unsupported and must not be promised.

## Correct a receipt across two venues

An explicit second-venue correction, including after an
answered pairing or after onboarding, goes to
`service/kit_life/ledger/onboarding say "<original owner words>" [RECEIPT_ID]` before
ordinary capture. This issues a fresh question; it does not bypass the rule
that only an issued question may be answered. `onboarding revisit
[RECEIPT_ID]` opens the same editor later without restarting setup.

Deliver its `question.card` HTML and literal button rows unchanged. Relay a
callback with `onboarding tap CALLBACK_DATA`, or pass the full issued question
ID and resolved label to `answer`. Only its confirmation button writes money.
When the owner supplies different amounts or who paid a venue, resolve those
facts through `onboarding allocation RECEIPT_ID JSON` as documented in
`service/kit_life/ledger/README.md`; that operation previews another card.
A friend-paid venue missing from the owner's export is pending `expense`
evidence in that preview, and is created only by confirmation. Never capture
it first as a new owner payment, never manufacture a combined payment, and
never invent the payer or a transfer. The kit computes the shares and net.

An explicit undo uses the kit's `ledger-allocation show` and then `undo` with
that receipt's current revision and a fresh operation identifier. Refunds
and deletion retain the normal capture/deletion paths and release allocation
credit through the kit. All owner-facing copy comes from the service.

## spend: one payment made or money received back

```
spend-add <date> <amount> <name> [category] [method] [scope] [note] [--fixed 0|1] [--new] [--source chat|batch] [--kind expense|income]
  out: id<TAB>name<TAB>amount<TAB>category
spend-find <date> [--all] | spend-day <date> [--all]          read first
spend-upd <id> field=value [...]   fields: date name amount method note is_fixed scope source category kind class
spend-person <spend_id> <PERSON>   the owner paid for someone; the row stays the owner's spend
```

- amount = KRW integer. expense > 0; refund / cancel = ONE negative row on
  the refund day, the original row untouched.
- amount words: "5500" / "5,500 won" / thousand and ten-thousand units as
  spoken; rent context "<RENT> 65" = 650000. Truly ambiguous amount -> ask
  once (the only permitted question).
- date omitted -> today (local); "yesterday" -> yesterday.
- scope: `personal` default | `business` | `unknown`. Explicit business /
  work / client -> business. Business-suspect -> `unknown`, never asked now.
- category = the kit's category table (never invent, never enumerate
  aloud). Uncertain -> the "other" category + the original phrase in note.
  No subscription category: a subscription goes to its domain with
  `--fixed 1`.
- method: as stated, normalized through the card aliases; unknown -> the
  owner's words; easy-pay -> the wallet name.
- duplicate gate (tool): same (date, amount) -> `EXISTS`, exit 3, nothing
  written. Show the matched row in owner words; only when the owner says it
  is a second real spend, retry with `--new`. Never `--new` first.
- installment purchase: full amount, one row, purchase date, note
  `installment N months`; the debt line itself: `installment-add` below.
- foreign currency: the KRW billed amount; original in note.
- a forwarded card SMS or a later screenshot may hold the same payment as a
  pending item: fine, the review attaches it to this row. Never resolve
  intake items here.

## Reasons for variable recurring amounts

```
amount-note-add (--account <account-id|name|alias> | --fixed <stream-id>) "reason1" ["reason2" ...]
amount-note-remove (--account <account-id|name|alias> | --fixed <stream-id>) "reason1" ["reason2" ...]
```

Record the owner's freely worded reasons in the same turn, one quoted argument
per reason (for example `--account IBK "야근비" "추석상여금"`). Use the salary
account or an existing salary/fixed-cost stream; resolve its identity first.
Remove only reasons the owner retracts, using their stored wording. Repeating
an add/remove is safe. The kit preserves payday rules and other reasons in the
existing `ledger_payday` detail's `amount_note` list; old scalar notes remain
readable as one reason. It creates no payment and changes no amount.
The card marks variable amounts automatically even with no reasons; never ask
for reasons merely to enable that label. Reasons are optional chat facts.

## pet insurance identity

When a policy names a pet, record its identity/species through the pet section
of `core-log/reference/person.md` in the same capture. A category such as pet
insurance does not imply dog. Keep policy facts in the money capture below;
never use memory compression as the species store.

## recurring payment (insurance, subscription, rent, utilities, phone, membership, salary)

```
fixed-add <name> <amount> [category] --cycle monthly|quarterly|semiannual|yearly [--next-due YYYY-MM-DD]
          [--method ref] [--direction expense|income] --find-first --source chat
fixed-list [--direction ...] [--json]                          read first
fixed-correct <name|id> field=value [...] [--direction expense|income] [--json]
  fields: cycle amount next_due name; corrects an existing row only
fixed-upd <id> field=value | fixed-del <id>                    existing CRUD
```

- For a policy, contract, app or bill screen, the contract capture above owns
  the fixed-cost link. Do not also call fixed-add for that same document.
- Other stated recurring payments -> `fixed-add` in
  the same turn, management on or off. Name = the payee and product as
  shown (insurer + product, provider + plan).
- `EXISTS<TAB>id` (exit 3) = already recorded. When the stated amount,
  cycle or due date differs, a separate `CORRECTION<TAB>JSON` line gives
  `changes`, a relayable `say` explaining the difference, and `correction`
  arguments. The duplicate check writes nothing. Apply the owner's stated
  change with `fixed-correct <id> field=value [...] --json` in the same turn,
  then relay the tool's correction outcome (`say`) verbatim. A duplicate
  is a route to correction, never a reason to defer it or create another row.
- During onboarding, use `service/kit_life/ledger/onboarding fixed-correct
  <name|id> field=value [...] [--direction expense|income]` instead. Relay
  the tool's correction outcome (`say`), then deliver the returned
  `question` unchanged. The pending question stays pending and affected
  figures refresh; do not submit the correction as an onboarding answer.
- Name resolution may return `ambiguous` with candidates: ask only which
  existing row, then retry by id. `not_found` creates nothing. Unknown
  cycle tokens are rejected; use only monthly, quarterly, semiannual, yearly.
  See `ledger-setup/reference/corrections.md` for the full correction path.
- `--next-due` only when the owner said it or the document shows it; never
  inferred from a contract or start date. Monthly for N years = monthly.
- Amount or cycle unknown -> ask that one value once. Never zero, never the
  verb's default cycle for an unknown one.
- A recurring payment is an obligation, not a payment made: no `spend-add`
  and no past payments for it unless the owner reports one.
- Stopped ("switched", "no longer pay"): `stream-stop <id> --said "<their
  words>"`, only on the owner's words.

## cards, accounts, wallets, balances, installments

```
method-add <name> <kind> [aliases] [--billing-day N] [--link <account>]   find-first on name
method-link <card> <account> | funding-set <wallet> <source> [--default]
spend-person-rule-add --person <P> --method <W> --weekday 1-5 --time HH:MM-HH:MM --said "<words>"
balance-set <instrument> <amount> [--as-of YYYY-MM-DD] | cash-set <amount> [--as-of D]
card-bill-set <card> <amount> --due YYYY-MM-DD
installment-add <card> <total> <months> [--first YYYY-MM] [--rate %] [--name N] [--date D]
```

- A balance is a reading with its date; never derived, never 0 for unknown.
- The owner names card + months for a purchase -> `spend-add` then
  `installment-add` in the same turn; never ask the monthly amount (the verb
  computes it).

## screenshot or file of payments

- One clearly readable payment (a receipt, one approval screen) ->
  `spend-add ... --source chat` like a typed one.
- A list (card app history, Toss / Banksalad screen, an export file) -> the
  import lane: `.claude/skills-bundle/ledger-log/reference/import.md`
  (staged rows are recorded evidence). Management off -> one summary line,
  no review table, no automation offer.

## never

- never ask a category, business vs personal, who shared it, or a budget at
  capture time; never offer SMS automation, a sweep or the ledger setup.
- never "I'll remember it": the confirm line says it is in the ledger.


## In-session corrections and the audited verb index

Use the kit in the same session when the owner corrects a value. A category
is a display grouping; `class` controls calculation. Investment payments use
`class=spend` with the investment category/destination, not an invented class.
Allowed classes: `spend income saving saving_back loan own_transfer topup
card_bill pass_through meal_point`. Class changes preserve the payment's
money direction, update card funding pairs, and set owner precedence over
reconciliation. Undo refuses later accounting edits; undo those edits first.
All commands below are agent tools; relay existing service copy only.

- `spend-upd ID class=CLASS` corrects the calculation class and derived kind; omit `kind` in that call.
- `spend-class-undo ID` restores the previous class, paired inflow and owner marker; repeat for earlier class choices.
- `ledger-card-answer ID company|mine|undo` changes/reverses an answer on the same bill and restores its paired funding inflow.
- `intake-undo ID` reopens an exclude/confirm; removes a newly created unchanged landing or restores an attached spend's scope; legacy answers detach evidence and retain the spend for `spend-upd`/`spend-del`.
- `installment-upd REF field=value ... [--json]` edits total, months, rate, first, name, date, spend (empty clears), or card in place; keeps the installment identity.
- `installment-reopen REF [--json]` reverses done/cancel on the same installment; superseded items require the replacement's correction path.
- `balance-del MEASURE_ID [--json]` removes exactly one balance/cash/card-bill reading returned by set/show, leaving other dates/provenances intact.
- `ledger-split get --data '{"spend_id":ID}'` reads the current split and shares before correction.
- `ledger-split link --data '{"spend_id":ID,"appointment_id":ID}'` links a positive payment to an appointment's participants.
- `ledger-split answer --data '{"spend_id":ID,"answer":"split|no|always_no|once|equal|different|amount|remainder"}'` follows the returned step; amount/remainder also supply person and amount as needed.
- `ledger-split change --data '{"spend_id":ID}'` reopens the split at ask, releasing its derived shares/matches so ordinary answers can replace them; release share-linked receipt allocations through `ledger-allocation undo` first.
- `ledger-split undo --data '{"spend_id":ID}'` restores the prior answer/change, participant preference, shares and receipt credits without changing raw payment evidence.
- `ledger-split transfer-answer --data '{"transfer_id":ID,"answer":"yes|unrelated|finish|remaining|choose"}'` answers a receipt match; choose also supplies share_id.
- `ledger-split undo --data '{"transfer_id":ID}'` restores the previous receipt-match answer and credit.
- `ledger-split weekly --data '{"today":"YYYY-MM-DD"}'` reads outstanding shares eligible for follow-up.
- `ledger-split weekly-answer --data '{"today":"YYYY-MM-DD","shares":{"ID":AMOUNT},"answer":"received|keep"}'` records the exact shown amounts.
- `ledger-split undo --data '{"shares":{"ID":AMOUNT}}'` undoes the latest weekly answer, refusing later changed shares/matches.
- `ledger-split no-split --data '{"person_id":ID,"enabled":false}'` clears a standing no-split preference explicitly.
- `source-set KEY --resource money --module module_ledger --provenance measured --enabled 0|1` registers/updates an intake channel.
- `source-list [--resource money]` reads intake channels.
- `match-rule-set money --module module_ledger --rule JSON [--enabled 0|1]` registers/updates the kit intake match policy.
- `match-rule-list` reads registered intake match policies.
- `intake-add --source KEY --source-ref REF --data JSON [--hold]` stores incoming evidence; hold requires review before landing.
- `intake-list [--pending] [--resource money]` reads evidence, landing ids and candidates.
- `intake-count [--pending] [--resource money]` counts evidence without computing money in the agent.
- `intake-resolve ID confirm|exclude [--spend-id ID|--block-id ID] [--scope personal|business|unknown] [--new] [--reversal-of ID]` reviews an intake item.
- `intake-forget --source KEY [--source-ref REF] [--archived]` forgets only eligible excluded/unlinked or archived evidence.
- `spend-block --spend-id ID` reads the canonical shadow block and intake provenance for a spend.
- `import-csv --source KEY --file PATH [--password-stdin]` imports through the registered intake source.
- `ledger-archive-reset --dry-run` previews archive/reset; the existing explicit count-confirmed apply contract still governs it.
- `method-alias-add ID ALIAS` adds one alias to a payment method.
- `method-scope REF personal|business` corrects a method's default scope.
- `method-merge ID INTO` merges duplicate methods; destructive merge still has no unmerge.
- `funding-get` reads wallet funding edges and their default source.
- `purpose-set REF --tags TAGS --memo WORDS --origin owner-stated` replaces instrument purpose evidence.
- `purpose-get` reads instrument purpose facts.
- `stream-list [--all]` reads recurring money streams including stopped rows.
- `stream-set ID amount|name|class|state|pay_mode VALUE` corrects the chosen stream field; state active reopens a stopped stream.
- `stream-keep ID must|reducible|droppable` records the owner's keep classification.
- `stream-merge ID INTO` merges recurring streams; destructive merge still has no unmerge.
- `stream-link ID KIND REF [--role for|with]` applies spend links to a stream's existing payments.
- `sub-tiers [--month YYYY-MM]` reads shipped subscription tier facts.
- `ledger-plan-answer --data JSON` changes a previously stated plan/subscription fact through its typed kind.
- `ledger-plan-commit --data JSON` publishes the computed plan using the existing commit contract.
- `ledger-plan-project --data JSON [--today YYYY-MM-DD]` reads the plan projection from kit facts.
- `ledger-history [--today YYYY-MM-DD]` reads historical months and the money-flow card's computed numbers.
- `ledger-history-reconcile [--today YYYY-MM-DD]` reconciles imported history while preserving owner class choices.
- `spend-link ID KIND REF [--role ROLE]` upserts a spend/entity relationship.
- `spend-unlink ID KIND REF` removes one spend/entity relationship.
- `spend-link-rule-add --kind KIND --ref REF [--merchant NAME] [--method REF] [--class CLASS] [--min KRW] [--max KRW] [--said WORDS]` registers a typed automatic spend-link rule.
- `spend-person-rule-add --person PERSON [--merchant NAME] [--method REF] [--said WORDS]` registers a paid-for-person rule.
- `spend-link-rule-list` reads spend/person link rules.
- `spend-link-rule-off ID` disables a rule and drops its derived links.
- `contract list|get|capture|update|rearm` manages the existing contract facts; use `service/kit_life/contracts.md` for structured arguments and revisions.
