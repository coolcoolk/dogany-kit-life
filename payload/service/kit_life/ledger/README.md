# service/kit_life/ledger -- module_ledger machines

Installs to `<instance>/service/kit_life/ledger/` with the kit (kit_life
2.46.0; until then the separate pack_ledger shipped the same machines at
`<instance>/service/ledger/`). Stdlib Python only. Every ledger read/write
goes through `database/kit_life.sh` verbs; this module owns no table here and
ships no migration of its own.

| entry | what | writes |
|---|---|---|
| `shot-stage` | weekly sweep: rows the session model read off a spend-list screenshot -> re-validated -> always pending; `--app` = a `sources.json` app key or `other` | kit intake |
| `csv-import` | a Banksalad export (.csv / .xlsx / the mailed zip) via `ledger-import` (v6): the same reconcile as the onboarding, so an overlapping export never doubles a month | kit (landed rows) |
| `sms-poll` | read-only poll of the transporter receiver store for `card_sms` rows the Dogany app sent (iOS Shortcut -> 도가니로 보내기); registered senders only, first-seen senders held and asked once | kit intake, `files/ledger/sms-unread.jsonl`, `files/ledger/sms-cursor.json`, `files/ledger/automation.json`, `files/ledger/sms-poll.log` |
| `sms-stage` | model lane for SMS no template knows: list / show / stage (re-verified, always pending) / drop | kit intake, unread queue |
| `sms-guide` | per-card automation how-to (from issuer data), verification lines, the owner's 등록 / 등록 안 함 answer for a sender (`sender-confirm`), issuer data table | `files/ledger/automation.json`; kit intake (a 등록 replays that sender's held SMS) |
| `retro-section` | the ledger block of the daily (automated path only) / weekly (sweep + all) retro; `nudge` = the separate-time sweep message | automation announcements |
| `review` | pending items as one Korean table + one grouped block for payment-intermediary rows; apply one answer (`intake-resolve`, `--what N=name:category` -> `spend-upd` note/category), lane daily / weekly | kit intake links / spend rows, `files/ledger/opaque.json` |
| `onboarding` | new starts: parts → history intake → monthly table / facts → conclusion → three reduction steps → goal / extra-income decision → confirmed budget at the bottom of today’s pin (UI gate open; see docs/reports/2026-10-31-ledger-redesign-step3). Existing v6 states resume: `next` hands out ONE message (aim, source, file, [confirm], income, reimbursements, fixed costs, living expenses, card checks, ending), `answer` records AND lands it, `import FILE` is the file answer, `apply` closes (refused while a chosen action is unexecuted); lookups and the password lane unchanged | kit (review corrections, goals, why), `files/ledger/onboarding.json` |
| `benefit-compare --data JSON [--json]` | read-only comparison from confirmed benefit terms, complete history and explicit wallet funding; one evidence quote per method | none |
| `seed` | idempotent source + match-rule registration | kit sources/rule |

Data:
- `sources.json` -- where the weekly spend list comes from, by region
  (`regions.<CC>.match` on country / tz / lang from `config/agent.conf`;
  `screenshot` app keys, `csv` app keys, `ask_app`). KR: toss, banksalad,
  kakaopay, naverpay, card app; csv banksalad. Anything else: `default`
  (no app named, the sweep question asks which app or bank and the answer is
  kept as `LEDGER_SPEND_APP`). Code holds no app name: see `ledgerlib/sources.py`.
- owner-facing copy for onboarding and the sweep ask lives in the kit's
  i18n bundle (`../i18n/{ko,en}.json` = `config/i18n/<lang>.bundle.json`;
  `ledger.*` keys, app display names `ledger.app.<key>`). Language from
  `AGENT_LANG`, ko fallback. `ledgerlib/text.py`.
- `issuers/<issuer>.json` -- per card issuer: the SMS setup guide (every field
  `{value, status: verified|unverified, verified_at, source[]}`; a value is
  never written without a source), the SMS markers that recognise the
  issuer, and deterministic SMS templates (only from real samples --
  first: Hyundai approval, 2026-09-24; `tools/ledger-sms-template.py` in the kit repo
  writes them). Schema: `ledgerlib/issuers.py`.
- `guides/ios-shortcut.json` -- the Shortcut steps (content-only message
  automation -> the app's 도가니로 보내기 action with 문자 본문 + 보낸 사람)
  and the app facts they rely on (action / parameter names, app >= 0.4).
- `intermediaries.json` -- payment intermediaries (쿠페이, 네이버페이, PG
  names ...): key, display name, normalised match patterns (substring of the
  merchant). A matching row is asked "what was it" in the retro instead of
  landing as an unexplained 기타. Schema: `ledgerlib/opaque.py`.

Instance state under `files/ledger/` is module-owned (unchanged from the pack) and holds no ledger data
and no SMS text: decisions, the automation check and SMS sender list
(`automation.json` `sms_senders`: registered key -> issuer, refused keys,
held receiver event ids), the receiver cursor (`sms-cursor.json`: last
received_at/id + text hashes for double-fire dedup), the text-free unread
queue, a counts-only poll log, and `opaque.json` (which intermediary
payments were asked in which retro, and the owner's names for monthly
repeats keyed by intermediary + amount).

Receiver store: `LEDGER_RECEIVER_DB` (default `~/.transporter/events.sqlite3`),
opened with sqlite `mode=ro`, never written. The SMS text stays there; the
model lane (`sms-stage show`) reads it back by event id.

Integrated-kit seams used here: G1 `intake-add --hold`, G2 archive dry-run,
G3 JSON method listing and alias add, G4 candidates / spend bridge, G5 scope
on confirmation, G6 method entity creation, and G7 `retro-sections.tsv`.
G11 (spend-by-method) remains unavailable, so onboarding offers registered
cards without a spend ranking. The retro row is
`service/kit_life/retro-sections.tsv` `module_ledger -> ledger/retro-section --poll`.

Routines (`<instance>/routines/bundle/`, kit-owned; both ship OFF and only the
module onboarding's answers schedule them through `routines/lib/routine-ctl.sh`):
- `ledger-sms-poll.sh` -- the card-SMS collector's scheduled leg. Scheduled
  only on the card SMS yes (`onboarding answer card_sms yes`, or after
  completion `onboarding apply --auto ...` ->
  `routine-ctl.sh enable ledger-sms-poll routines/bundle/ledger-sms-poll.sh <LEDGER_SMS_POLL_AT>`,
  default 21:40); skipping it runs `routine-ctl.sh disable ledger-sms-poll`.
  Silent, fail-open.
- `ledger-sweep.sh` -- the weekly sweep message at a weekday/time the owner
  picked instead of the weekly retro (`answer sweep <dow>@HH:MM`, or after
  completion `apply --sweep <dow>@HH:MM`). One push, text
  from `retro-section nudge`.
- `ledger-session-start.py` -- SessionStart hook, declared in the kit's
  `pack-manifest.json hooks[]` and wired by `pack_install.sh` (DGN-1496).
  Read-only, fail-open, silent unless the ledger-setup skill is linked
  and the onboarding gate says OFFER. Bundle activation is not required:
  an outstanding setup can be signalled while management is still off.
  The signal does not enable management; explicit declines remain silent.


Card SMS intake requires active `shortcut/card_sms` consent, recorded by
onboarding completion for `module_ledger` and revoked on opt-out. Receipt and
event timestamps must both reach the approval floor, including sender replay
and model staging. The old `LEDGER_SMS_SCAN_DAYS` setting is ignored; an empty
cursor starts at consent. Successful intake writes the shared `ingest_ref`
before saving its local cursor. Missing senders remain quarantined by receiver
ID, with ISO8601 UTC warning lines in the poll log and stderr. CLI diagnostics
carry an `at` timestamp. See `docs/ledger/consent-activation.md` in the source
repository for the owner answer flow and deployment verification commands.


## Onboarding screens (2026-10-17)

For an owner correction to an entered recurring fact during any pending
screen, use `onboarding fixed-correct <name|id> cycle=yearly` (or `amount=N`,
`next_due=YYYY-MM-DD`, `name=TEXT`; optional `--direction expense|income`).
Relay the tool's correction outcome (`say`) verbatim, then deliver its
returned question and buttons unchanged. This command routes to the kit's
`fixed-correct`, refreshes affected figures and retains the same pending
question and previous answers. A correction does not advance onboarding.
`ambiguous` returns candidates to choose by id; `not_found` and validation
failures write nothing. See `ledger-setup/reference/corrections.md` in the
skills bundle. Only monthly, quarterly, semiannual and yearly are accepted.

The current `onboarding next` payload is authoritative, including for an older
setup skill: use the returned choice values and lookup command. `parse_mode`
is HTML. Single-choice screens supply `button_rows`; numbered screens leave
that field and `card.buttons` empty.

Person and reimbursement-pair reviews also return `question.card` with literal
Telegram `text`, `parse_mode` and `buttons` (rows of `text`/`callback_data`).
Deliver those rows as supplied, one button per row; do not rebuild them with
the bridge's generic numbered OPTIONS renderer. That renderer can replace the
whole keyboard with number handles when a date/merchant/amount label is wide.
Do not add a choice list or a `선택:` line to the message body.

Relay a raw review callback to `onboarding tap CALLBACK_DATA`. The existing
bridge may instead resolve it to the full button text; that text is accepted
by `onboarding answer QUESTION_ID LABEL --said "exact owner answer"` for
person/pair questions. Values
`yes`/`no` and spend ids remain supported. The callback is bound to the issued
question and its choices and is checked again before any kit write.

Purpose toggles return the updated literal `card` with `✓ ` on selected labels
and `card_update: {action: edit_current, fallback: resend_once}`. Edit the
callback message with this card after each tap. If the bridge cannot edit,
re-send the returned question once, preserving its marked keyboard. Only
`done` commits the selected purposes. An item without qualifying transaction
evidence opens this multi-select directly, with no guess sentence.

Pair suggestions are up to three share-fitting personal expenses in the preceding
14 days, including the previous month, ordered by date proximity then row id.
Effective fixed costs (including active housing/tax streams) are excluded.
Without a share fit, no spend is suggested; manual lookup retains the broader
eligibility. Existing credits reduce eligibility. Only an owner selection
creates a reimbursement link. `type` retains the question with free input;
`YYYY-MM-DD / merchant` must identify exactly one eligible prior spend.
`none` returns to the person question without classifying or moving any money.
Both person and pair cards offer `skip` and `skip_all`. A skip records
`{"skipped":true}` under the question id, clears pending input, and leaves
ledger classifications and reimbursement links untouched. `skip_all` also
omits the remaining received-money questions in this flow; fixed costs,
living expenses, cards and diagnosis still follow. Skipped answers survive
refresh and are never recorded as confirmed income.

1. `aim`: the approved opener, a blank line, and the aim question with
   save_more / save_for / flow. The offer lane uses this same message;
   there is no separate start/later question.
2. `source`: region screenshot app keys plus none; the default region instead
   accepts a free-text app name and saves LEDGER_SPEND_APP.
3. `file`: example monthly money flow. Recording is already active. Choices
   are capture:<selected app> (except an app with its own CSV path), followed
   by file:<app> for each region CSV provider. The default region captures
   through other. No defer button. An outstanding history task is created
   once and completed on import; leaving the screen unanswered does not stop
   ordinary recording or weekly review. The first weekly review mentions it
   once, with preview reads leaving the reminder unconsumed.
4. The selected method requests `lookup` with what=capture or how. Search
   official help first and record with the supplied `lookup.answer` command.
   Verified official steps require the source URL and date. Cache TTL is 30
   days. Recent posts retain their own source label; a failed lookup uses generic wording and asks for the
   current screen if it differs. There is no bundled app menu path.
5. File: `onboarding import FILE` (existing password-stdin handling) reads
   only the last three full calendar months. The card uses the most recent
   full month and its latest recurring amounts; all three months feed the
   recurring-cost and stopped-subscription checks. Two matching charges in
   a known fixed-cost class can establish a stream when the third month is
   missing; later diagnosis retains its canonical key and latest amount.
   Screenshot: `onboarding capture --since D --until D --rows JSON [--card NAME]`,
   capture the whole previous month (multiple images are supported). Show
   `review show --json` using its `card.text` and `card.buttons`; the accept
   button carries action `apply` and the displayed plan's `expect` digest
   and `lane` as data. Pass those to `review apply --expect DIGEST --lane LANE`.
   Numbered exclusions still use `--exclude`. Finally
   `onboarding answer file captured`.
   This last command verifies the captured items landed; it cannot bypass
   review. A repeat screenshot remains deduplicated.
6. The monthly income and spending card names its basis period and uses kit
   diagnosis numbers, followed by
   subscription review and a proposed budget (income - fixed costs - savings).
   Existing installment charges appear separately and reduce available money.
   No installment planning or balance interview is added. Existing diagnosis
   corrections, saving goals, lever actions and apply guards remain supported.

The service retains old app/how/new callbacks for already-sent messages;
new messages never display the old file-first/defer choices. An older pending
v6 screen is rebuilt without deleting its imported rows or recorded answers.

### Appointment settlement (078)

`settlement <action> --data '<JSON>'` is the conversation entry point. It
returns `card.text` and `card.buttons` (one button per row); pass a tapped
button's `action` and `data` back to this executable. `--plain` renders a
review transcript without sending it. All sentences live in `ledger.split.*`
in both shipped i18n bundles.

- `link`: `{spend_id, appointment_id}` creates the spend/appointment link and
  asks before creating debt. Participants come from kit `event_persons`;
  the owner is implicit. An appointment where all others have
  `persons.ledger_no_split=1` produces no question. In a mixed group, an
  equal split keeps each participant's original equal amount; the owner
  covers the no-split person's amount.
- `answer`: split / no / equal / different / remainder / amount / always_no /
  once. Remainders require a named payer. An amount card carries a `reply`
  object: add the owner's integer KRW answer to its `data.amount` and call
  its `action`. The kit rejects negative amounts and a wrong completed sum.
- `transfer` / `transfer-answer`: exact named inbound transfers tentatively
  settle one unique open share; the owner confirms or undoes it. Different
  amounts wait for an answer. Multiple candidate debts require an explicit
  share choice. Unknown names and outgoing transfers never auto-settle.
- `weekly` with `{today}` returns shares strictly older than three days,
  grouped by person with each date/appointment. Both weekly buttons carry
  the exact shown share IDs/remaining amounts; stale receipt marking fails.
- `sweep` with `{since, until}` links untracked payments to the sole
  appointment on their local date, asks when there are several, and matches
  imported friend transfers. Imports and screenshot review call this seam.
- `setting` with `{enabled: false}` disables immediate clear-capture notices
  (`config.ledger_immediate_notice`; absent means on). `capture` writes a
  weekly roll-up entry regardless of that setting. The SMS collector calls
  it only for clear, confirmed payments; intermediary payments retain their
  existing review question. The runtime SMS routine relays returned notices and posts split questions
  with a strict inline keyboard. `sms-poll --notifications-json` renders those
  delivery envelopes without sending. A callback is handed to
  `settlement dispatch --data '{"token":"<callback_data>"}'`; it resolves
  to the same action/data protocol.

The additive migration is `database/migrations/078_ledger_split.sql`; new
instances carry the same schema and `EXPECTED_USER_VERSION=78`. No personal
preferences are seeded into this distributable kit. A known owner's standing
preference is applied through `no-split {person_id, enabled: true}` in that
owner's kit; the scratch replay uses synthetic people only.

Accounting follows the owner ruling at 12:04 in
`docs/ledger/onboarding-v6-spec.md` and migration 075's `v_spend_link`: money
spent for another person remains the owner's spend. Shares are receivables,
not income. `v_ledger_reimbursement` folds actual received amounts into the
original spend's month, category, and method in `spend-month`, category
summaries, and the diagnosis. The raw payment and transfer rows stay intact.
Closing a short payment waives its remainder without inventing a receipt;
undo restores the open balance and month totals.

Replay without notifications or live data:
`python3 tools/ledger-split-replay.py --out /tmp/ledger-split-walk/walk.md`.

Last-month review answers use the issued question id. Confirm income/fixed
screens with `ok`; person receipts use `yes`/`no`, followed by a spend id
when reimbursed. Odd fixed rows ask their payment cycle: `monthly`, `quarterly`,
`semiannual`, or `yearly`, saved through `ledger-review-answer` to `fixed_cost.cycle`.
Transit corrections accept `transit` or `ok`.
Living answers are `ok` or JSON with `one_off` and `subscriptions` spend-id
lists, resolved from `question.review`. Card checks accept `capture` (hold),
`captured` (after matching card installment records are saved), or `skip`.
The flow aim ends with confirmation only. Save aims retain goal/lever actions
and budget from income less monthly equivalent fixed costs, savings, and
existing installment charges. The ending equation uses full actual paid fixed
costs; a separate monthly equivalent line divides each non-monthly payment by
its cycle months. Usual living expenses remain visible separately. All imported rows are
kept; onboarding explicitly selects the last complete calendar month.


## Second-venue receipt correction (schema 093)

Route “2차도 있었어” to `onboarding say "2차도 있었어" [RECEIPT_ID]`.
An issued person/pair question also accepts that phrase through `answer`.
After an answer, later in setup, or after completion, `onboarding revisit
[RECEIPT_ID]` opens the same editor. Receipt lookup includes already classified
receipts; it does not depend on unresolved income. If context does not identify
one receipt, the service issues receipt choices. Several plausible pairs also
produce choices; a suggestion never writes money.

Deliver the returned `question.card` unchanged, including its three Candidate A
buttons, HTML and one button per row. Relay callbacks through `onboarding tap`,
or use the exact issued question ID and resolved full label with `answer`.
Correction question IDs contain a fresh issuance; retain the full ID. Both
paths validate the receipt revision. Opening an identical card issues new
callbacks. While it is open, other onboarding answers and apply are suspended.

`지출 다시 선택` toggles the displayed expenses, then previews the selected
pair. `금액 수정` holds a free reply. Resolve the owner's named expenses,
payers and integer amounts into:

```
onboarding allocation RECEIPT_ID '{"allocations":[{"expense_id":1,"amount":32500,"payer":"owner"},{"expense_id":2,"amount":12000,"payer":"friend"}]}'
```

This previews the card; it never confirms it. Candidate A requires two venues.
If the friend's payment is absent from the bank export, its row may instead be
`{"amount":12000,"payer":"friend","expense":{"date":"2026-09-09","name":"Example Venue","amount":24000,"category_id":202,"method":"Friend card"}}`.
These are explicit owner-supplied facts. Preview creates no persistent venue;
confirmation creates that individual venue and its payer fact in the kit
transaction. Never create an artificial combined payment.

For existing split obligations,
include the corresponding `share_id`. `amount` means the friend's share at an
owner-paid venue, and the owner's share at a friend-paid venue. The signed
sum must equal the one receipt (32,500 minus 12,000 = 20,500). `partial:true`
retains an explicit remainder. The entry point accepts dates and payment
methods outside the suggestion window after an explicit owner association.
A bare pair of space-separated integer amounts also edits the two displayed
shares. Do not infer a payer from a different payment method.

Only the first Candidate A button confirms. Confirmation replaces the old
credit atomically, refreshes financial screens and resumes the suspended
cursor. After completion it leaves completion in place. A crash after the kit
commit is recovered by the recorded DB operation when `next` runs again.

Kit-level inspection and cleanup use `ledger-allocation show|undo --data
'{"receipt_id":3,"revision":1,"operation":"NEW_ISSUANCE"}'`. Undo retains the
receipt as an unallocated transfer and retains the fact that a friend paid a
venue. Payment/receipt deletion and paired refunds release allocations through
schema triggers. These commands never send Telegram or change service state
outside the selected instance. See `docs/ledger/receipt-allocation-contract.md`
for the accounting and revision contract.


### Owner-selected onboarding screens (0.7.14)

The executable layouts and funding provenance contract are specified in
`docs/ledger/onboarding-v6-spec.md` (Owner selections, 2026-10-24). Roster `next`
refreshes after `funding-set --origin owner-stated`; debit cards keep their kind.
Insurance is displayed only in the returned-money group of the card summary.
The living proposal never appends settlement details. The skill recall change
is supplied to the maintainer as `docs/ledger/ledger-setup-owner-funding.patch`.

Benefit comparison facts and supported-rule limits are documented in
`docs/reports/2026-10-26-dev58-ledger/REPORT.md` in the source repository.
AI target and unmatched-deposit previews remain draft-only pending owner copy picks.

### Bank evidence before account questions (0.7.17)

Before issuing `review_bank_ID`, onboarding reads `kit_life.sh bank-evidence ID`
across that account's full history, including canonical variants and aliases.
`database/ledger_bank_markers.json` carries product patterns, eligible row kinds,
linked-account terms and official source URLs. A unique bank proved by those
products is renamed through the existing roster correction verb and included
in the separate auto-link confirmation list. Retail purchases containing a product word do not prove a bank.
Card funding edges, own-transfer counterpart banks and monthly interest dates
supply suggestions; a card issuer or a transfer destination may use another
bank. An owner salary-bank fact (`ledger_preference`, topic `salary_bank`, item
bank name, source `chat`, `owner`, or `owner-stated`) counts as evidence only when this account has
actual salary rows. Register it with `preference-add --topic salary_bank --item BANK`
(the default source is `chat`). A conflict with product proof produces the clue question.

Unresolved accounts get one bank question with their recorded balance when
available, three recent rows, and observed salary/fixed incoming/outgoing flags.
Guesses appear first; `unknown` retains the `(은행 미확인)` display. Relay these
facts and the issued question directly; do not explain a label with a generic
account definition. Newly imported proof can replace an already pending bank
question. Completed onboarding remains closed.

An issued `merge:TARGET_ID` choice explicitly confirms that two names identify
the same account. It calls `kit_life.sh method-merge SOURCE TARGET`. The roster
correction also accepts `{"merges":[{"id":SOURCE,"into":TARGET}]}` after the
owner confirms. The transaction moves entries, fixed-cost references, card and
wallet links, purpose tags/memos, balance readings and money deltas; the other
name remains an alias. Different kinds/scopes or colliding balance readings
refuse the whole merge. Bank inference never merges two accounts automatically.

`tools/replay-ledger-bank-inference.py --source-db DB --account-id ID --out JSON`
backs up a read-only source into scratch instances and replays the life agent's product
proof and an unknown-bank question using the imported rows. `--former-label`
reconstructs orphan method strings from an intervening manual rename on the
scratch copy only. No routines, notifications or source database writes occur.


### Owner provenance, funding inference and numbered selections (dev.58)

Every `onboarding answer` requires a nonempty `--said "exact owner utterance"`
or a valid issued callback relayed through `onboarding tap CALLBACK_DATA`.
This covers aim/source/file, all review screens and the ending. A bare command,
including `answer review_purpose skip`, fails before changing state or data;
the error instructs the agent to show the issued screen. Never fill in an answer
or invent quoted words for the owner. Successful answers retain their provenance
in the flow. The purpose screen offers no skip button; an explicit owner request
to skip still passes with its quoted words.

Before any card funding question, `funding-evidence CARD_ID` reads unique
normalized product matches, a sole same-bank account for debit cards, and the
latest matching card-bill rows. Conflicting evidence remains a question.
Onboarding saves a proven edge as `learned`, includes it in the roster,
and omits that question. Existing funding edges and owner facts survive.
Product matching can distinguish a personal account from another business
account at the same bank. Account merges still require owner confirmation.

Easy-pay funding returns `multi=true`, structured `choices`, one numbered
list grouped under bold card/account headings, and numbered buttons followed
by “다 골랐어요”. Relay `ask` as HTML with its blockquotes intact. Selected
buttons start with `✓ `; each toggle returns the same edit-current/resend-once
card contract as purpose. Typed `1, 3`, `1 3`, and `1,3` select displayed list
positions rather than method ids. These selections remain staged until done;
done saves all sources and continues to purpose without another funding
question. A single selected source is also saved as main. Invalid positions
or a mix of sources and navigation controls fail before writing. Full issued
labels and explicit `source:METHOD_ID` values retain owner provenance.
An explicit owner “unknown” remains accepted without an extra button.

`kit_life.sh ledger-restart --dry-run` reports affected config paths.
`ledger-restart --yes` archives the original bytes before removing
`LEDGER_SPEND_APP` from `config/kit_life.conf` and legacy `pack_ledger.conf`, as
well as resetting the ledger and onboarding state. Other config keys remain.
A restart interrupted after data/state removal can finish the config cleanup.

The roster is the only confirmation of all imported/registered instruments.
Groups have bold titles outside one blockquote each, with continuous item
numbers across cards, accounts, savings/vaults and easy-pay. Proven bank clues
appear as a suffix on the account itself. There is no resolved-list screen.
A correction such as `onboarding answer review_roster '1 토스뱅크 통장'
--said '1 토스뱅크 통장'` changes that numbered card’s paying account and
reissues the roster. JSON corrections may use `number` for the displayed
position or `id` for an explicit method id. Existing merge operations remain.

After confirming the roster, `next` asks funding for unresolved cards/easy-pays
only. The roster confirmation confirms links already displayed, including
imported/learned wallet sources; they are never asked again. Once funding is
complete, `next` returns the purpose introduction with
`question.auto_advance=true`, no choices and no buttons. Relay its `ask` as
one message and call `next` immediately, without an owner answer. This is an
informational screen, not a confirmation gate. The next call returns the first
instrument question. Ordinary unanswered questions still repeat unchanged.

`purpose-evidence --today YYYY-MM-DD` is a read-only kit verb. It resolves all
canonical names/aliases, counts actual transactions from the same calendar date
three months earlier through today (inclusive, with end-of-month clamping), and
sums absolute amounts. Savings, vaults, installment savings and housing-subscription
holdings are excluded regardless of activity. Other accounts/cards qualify when
count > 10 **or** moved > 50,000 KRW; wallets and cash are excluded. Dust methods
stay outside the roster/review. Evidence covers each guessed purpose with up to
three real transactions, prefers distinct canonical merchants, and displays the
selected rows newest first. Nothing is invented to fill an evidence line. The
scope is frozen at purpose entry for stable progress.

Observed account card-payment outflows (`card_bill` or `pass_through`) inherit
the roster card's transaction purpose shares, weighted by the amount paid.
Exact card names/aliases resolve directly; issuer-only settlement descriptions
require a unique card linked to that paying account. A stored link without a
current outflow supplies no purpose. Inherited evidence retains the actual card
merchant row, method and id. Wallet funding keeps its existing current-charge
majority requirement and caps inherited spending at the observed charges.
Explicit method/transaction business scope carries through both paths.
`ledger-card-answer company` marks that settlement's scope as business; its
reimbursement stays excluded and the paying account's default scope is retained.
Automatically paired pass-throughs and account product names establish no
business scope.

Each `review_purpose` message has one instrument, an activity-based guess and
its recent transactions, then “맞아요” / “다르게 써요”. Confirming stores the
guess with `origin=user`. “다르게 써요” opens five toggle buttons (월급·고정비,
생활비, 저축, 사업, 안 따짐) and “다 골랐어요”; done stores all selections with
`origin=user`. 안 따짐 is exclusive. Selection changes do not write until done.
Purpose writes use the ten labels in `database/ledger_purpose_lexicon.json`
on every layer. `user` remains an accepted input origin and is saved as
`owner-stated`. Cards without own answers still inherit their paying account's
current purpose internally. Old SDK tags remain readable; display consumers
use `purpose-get.normalized`, which never returns arbitrary tags as copy.

Stored instrument purposes remain readable through `purpose-get` and plan
views, including older evidence notes normalized on read without a rewrite.
Later owner statements can still use `purpose-set ID --tags LABEL,LABEL --memo WORDS`.
Onboarding no longer asks an instrument's purpose or offers label clarification.

Every observed wallet funding source is shown as a link, newest first.
Recency is the latest of `pay_funding.last_seen` and stored payment/top-up pairs.
No transaction rows or amounts are deleted. Schema 106 widens the purpose origin
constraint and adds the shared choices, preserving legacy data and vocabulary.
The framework requirement is unchanged. The ledger-setup skill is deliberately
not edited by this worker; the CLI contract above documents the informational
screen’s automatic continuation.

### Redesigned onboarding: retained owner picks (audit L17–L19)

`history_v1` retains the grouped roster and balances, then proceeds directly
to deposits and month analysis. Owner pick 2026-11-04 removes the per-instrument
purpose interview from onboarding and weekly review. Saved instrument cards,
resume targets, callbacks and instrument-specific capture routes are retired;
stored purpose data and historical answers remain readable. The flow retains
only the retired interview's historical payment IDs as recall omissions, so
removing its skip buttons does not create a replacement payment interview.
This writes no owner answer or purpose fact. The existing
payment review continues to handle payment-level questions. No new forward
payment questioning is introduced by this change.

After six idle hours, a saved history question opens with the existing resume
sentence and 이어서/처음부터. Continue retains the issued question and callback;
restart archives the conversation and keeps landed payments. Existing phase
labels remain pending owner approval; no phase labels were newly authored here.

Eligible unmatched own-transfer deposits across the imported months reuse
`review_deposit_<id>`, exact amount formatting, receiver account, 기타 수입,
and direct input. Redesigned reconciliation opts into `--review-deposits` so
raw unpaired incoming transfers reach that question. Matched reimbursements and
the reconciliation verb's default behavior retain their previous contract.
Recording-only starts do not enter any of these imported-history interviews.


### Onboarding result and completion contract (2026-10-27)

After card-bill checks, the flow aim gets `review_aim`: the real three-month
category shares and monthly averages, using the opening example's bar card.
`review_nature` follows for all aims, before any budget choice: registered
fixed-cost links, categories with population CV <= 0.2, then other categories.
Missing calendar months remain zero and prevent a claim of stable spending.
Unlinked housing/subscription costs carry a fixed-like warning. Kit calculations
own amounts, shares, variation, monthly equivalents and budget starting points.

When `next.done` is true, the completion screen has been prepared. Call
`apply`, then send **only `ready`, verbatim, as one standalone message**, as
step 3 of the installed ledger-setup skill already requires. No skill change
is needed. This screen contains fixed costs (cycle and monthly equivalent),
actual payments separately, unresolved recurring identities, Sunday 21:00
review instructions, what to send, local collection status, and deferred
choices. It ends with a usage sentence. The service does not claim a reminder
job is installed.

Applying before `next` has prepared this screen is refused. Successful apply
always emits the full completion through `ready` with `standalone=true`;
`summary` and `guide` are null, so there is one owner-facing completion
message. Only this emission sets `completed_at` and `completion_emitted_at`.
The machine does not send to Telegram itself: the installed relay delivers
`ready`. Actual Telegram receipt cannot be verified by the service response.

Multiple subscription charges linked to one stream remain unresolved, with
basis-month charges listed separately and excluded from the confirmed total.
Variable subscription totals use their three-month monthly average.

Card differences use full-width indented subbullets and list individual
cancellations (date, merchant, method, amount). A named card cancellation is
classified from its evidence. An unidentified deposit can be answered as
insurance once. Installments are recorded only from explicit owner terms:
choose a numbered larger purchase plus months, state purchase/date/total/months
in words, or use the existing reviewed screenshot intake. Missing facts cause
another input screen; listing a purchase never creates an installment.

Rehearse without live writes:
`python3 -B tools/replay-ledger-onboarding-closing.py --out "$DOGANY_RUN_SCRATCH/replay"`
(from the repository root). To use owner-shaped data, first make a stable
read-only file copy of the database into scratch, then pass
`--database-copy "$DOGANY_RUN_SCRATCH/copy.db"`. The replay accepts only a
scratch file, copies it again, uses CLI taps, checks every next message and
import deduplication, and writes approximate 440 px text screens. Its simulated
owner choices are evidence for the flow, not new live owner decisions.

## Merchant business lookup before identification asks

Classify the business kind first, then let `ledger_plan.KIND_BUCKETS` select
our bucket. Imported categories, payment amounts/times and a similar name
are not business evidence. Product rules contain generic business words,
widely used chains and subscription/bill/payment-processor rails. A local
business identity belongs to this instance's `ledger_preference` facts
(topic `ledger_merchant_kind`), never to a shipped regex or alias seed.

Before relaying an identification ask or the plan overview, the agent reads:

```sh
database/kit_life.sh ledger-merchant-kinds --today YYYY-MM-DD
database/kit_life.sh ledger-merchant-kind-set "MERCHANT" restaurant --source web --evidence "https://business-source.example/branch"
```

`pending` (also `ledger-plan.merchant_lookups`) lists unknown businesses,
largest net amount first, with payment IDs. Look up each business on the web,
check the branch/business identity, and save its kind with the source URL.
Use the returned `kinds` vocabulary. `--source owner --evidence "original
owner words"` records an explicit owner identification or correction. Re-read
the plan after writes and before issuing the next card; an already displayed
plan round keeps its existing frozen basis. These are agent work instructions
and machine data, with no new owner-facing ask text.

When lookup cannot distinguish the business, record `unresolved` with
`--source web --evidence "query and reason it could not decide"`. It remains
unknown and leaves the pending lookup queue; only then use the existing
per-item identification/review ask, preserving its issued ID and wording.
Payment processors are already a known kind with an unknown spending bucket:
a PG's identity cannot establish what a particular payment bought. Their
existing per-item ask and category answer path stay in use. A one-payment
answer must not become a merchant-wide business fact.

Instance cache facts precede generic rules. Explicit per-item and owner
category facts retain higher priority, and the large-payment partition still
runs afterward. Keys normalize case and whitespace only, preserving branches,
punctuation and intermediary identities. Corrections upsert the same fact
with fresh provenance. Cache facts survive ledger reset/restart with the
existing preference store. There is no migration in this change; migration
111 remains reserved for kit-gym-link and 112 remains available.

## Agent-supplied merchant display names

`ledger-merchant-names` reads the shared presentation facts. Unnamed merchant
rows in ledger payloads carry `display_name_missing: true`; their payload also
supplies `display_name_resolution` with the batch verb and agent instructions.
Resolve these names from the agent's own understanding before relaying the
card, save once, then render again. The ledger setup skill's existing
`reference/purpose-order.md` link contains this workflow.

```
database/kit_life.sh ledger-merchant-names-set --data '{"items":[{"name":"RAW RECEIPT TEXT","display_name":"spoken name","source":"agent_inferred"}]}'
```

Display facts use `ledger_preference.topic=ledger_merchant_display_name`, an
exact case/whitespace-normalized receipt identity, a `display_name` in `detail`,
and `source=agent_inferred|owner_said`. No translation vocabulary is embedded
in code. Existing explicit merchant merges, subscription identities, stream
links and fixed aliases resolve to the same facts. Existing owner-taught plan
names are already named. Raw receipt text remains the matching key.

For a correction, supply `source=owner_said` and `said` with the owner's words
through the same batch verb. Owner names take precedence; inference never
replaces an existing name. Existing `fixed-correct name=...` and
`stream-set ... name ...` corrections also update this source. A held card
refreshes its presentation while preserving its issued identity and money
basis. Missing-name metadata is for the agent and adds no owner-facing UI.
