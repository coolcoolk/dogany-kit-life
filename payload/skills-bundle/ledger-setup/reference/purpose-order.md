# Current ledger onboarding order

New starts and explicit restarts use the history/reduction flow. The old
v6 aim/source/review flow applies only to an already-started legacy session.
Do not start that legacy sequence for a new ledger.

A typed reply matching an issued button label is an answer to that question,
including its displayed number (for example "5. 지금은 건너뛰기"). Resolve it
against the issued question's choices and call `onboarding answer ID VALUE
--said "exact owner words"`; use the returned choice value, never invent a
callback. Full labels are also accepted by the relay. Do not send the owner
back to press the button. If cooking is active, apply this explicit answer
but retain the next onboarding question for after cooking; do not interleave
an unrelated money card into the live step flow.

1. Run `service/kit_life/ledger/onboarding next` and relay its returned
   question and keyboard unchanged, one question per message. `parts`
   selects `record`, `past`, `plan`, or `all`.
2. An empty history asks `source` before intake. Pass the actual app token
   from the returned choices (for example `banksalad` or `toss`), `other`,
   or `none`; `app` is not an app token. Use the service's region choices.
   Existing imported history skips this question. File/capture controls
   follow the source answer. `how` requests dated export instructions;
   when the tool returns `lookup`, perform that lookup and save its result
   with `onboarding lookup-set`, then call `next` again. Never invent steps.
3. Import the owner's file through `onboarding import <path>`, or stage
   screenshots through the existing capture service. Relay its returned
   acknowledgment. Preserve duplicate detection and password-stdin handling.
4. Before relaying any identification question or plan card, resolve
   merchant business kinds as described below, then call `next` again.
5. Follow the issued history/refund questions, known payment matches,
   grouped credit/debit/account/easy-pay roster, unknown wallet purpose,
   recent opaque payments, transfers, obligations, monthly history and
   conclusion. Only questions still needed are issued. Keep payment rows
   dated and numbered; never replace them with monthly average counts.
6. A reduction plan proceeds through the overview, large purchases and
   remaining installments, future cap, subscriptions, habits, income/debt
   comparison, shopping/subscription revisit, and goal confirmation. Relay
   each issued card rather than constructing figures. Only public final
   confirmation persists the plan and goals; then render the November pin
   through the kit day-card path.

## Typed replies to an open deposit review card

For an answer to the issued card: `service/kit_life/ledger/onboarding answer ISSUED_QUESTION_ID 'EXACT OWNER WORDS' --said 'EXACT OWNER WORDS'`.
The owner-turn hook supplies this exact command for the pending
`review_deposit_*` card, including grouped deposits, during onboarding or
monthly review. An explicit reply to that card or an exact issued button
label binds strongly. Other text receives only a conditional hint when
earlier routes are silent; a pending card alone never authorizes recording.
Determine whether the message answers that card before using the hinted
command; otherwise handle the turn normally. Cooking, regimen intake,
waits, course reviews and retro routes retain precedence. During cooking,
only an explicit reply to the deposit card receives a deposit command.
Then run `service/kit_life/ledger/onboarding next` and relay
its literal card text and buttons. If cooking is active, retain the next
ledger card until after cooking. A refusal keeps the current card open.

Pass purpose words such as “합주실 맞다” unchanged: the existing answer path
records `custom`, preserving the utterance. Do not infer `received` from
“맞다”, search source, or ask the owner to tap. An exact issued button label
uses the same answer verb and resolves to that button's value. Without an
open deposit card this route does nothing; keep the normal owner turn.

## Opaque-payment answers link a 사용처 to a module

For `reduce_methods`, follow the question's `modules`, `instructions` and
`answer_format`. Each issued row or explicitly named stream takes `module`:
any current `database/ledger_roster_modules.py` `MODULES` value, `self_transfer`,
or `unknown`. Use roster display names such as 외식, 쇼핑, 업무 and 생활관리.
Never ask the owner to choose food/cafe categories or say an option is missing.
Use 사용처 in Korean.

Interpret the owner's product or purpose directly. 음식점/카페 maps to
`module_dining`; a Mac hub cable maps to `module_shopping`, with
`area_module: "module_work"` as the likely life-area link. Mark a guessed
primary link `inferred: true` and a guessed area `area_inferred: true`.
The roster reuses its existing (추정) marker. The second area is stored as
link evidence; the spending amount belongs to the primary module once.
Do not infer that work use means employer reimbursement or exclude the
purchase from personal spending.

Money moved to the owner's own account or wallet (for example 토스페이충전)
is `self_transfer`, excluded from spending. Use `unknown` only when the
meaning is still unknown. Keep each answer on its issued `number`; use
`scope: "stream"` only when the owner clearly identified the whole stream.
Pass the original utterance intact with `--said`, then run `onboarding next`.
Existing budget categories are derived internally from modules.

## Merchant-kind lookup before identification or plan cards

Read the pending queue before showing those cards:

```sh
database/kit_life.sh ledger-merchant-kinds --today YYYY-MM-DD
```

Use its returned `kinds` vocabulary. Look up each pending business, check
its exact identity and branch, and record the business kind with evidence:

```sh
database/kit_life.sh ledger-merchant-kind-set "MERCHANT" restaurant --source web --evidence "https://business-source.example/branch"
database/kit_life.sh ledger-merchant-kind-set "MERCHANT" restaurant --source owner --evidence "original owner words"
```

Use `web` only for an actual lookup and retain its source URL. Use `owner`
only for an explicit owner identification or correction. Never infer a kind
from imported categories, amount, time, a similar business name, or golden
numbers. Never ship a user's merchant names as generic rules.

If a lookup cannot distinguish the business, save `unresolved` with
`--source web --evidence "query and reason it could not decide"`. Then use
its existing per-payment identification ask and unknown control. A payment
processor identifies the payment rail, not what the purchase bought.
A per-payment answer must not become a merchant-wide fact.

Re-read `ledger-plan --today YYYY-MM-DD` and `onboarding next` after cache
writes and before showing the card. An already displayed plan round keeps
its frozen basis. Preserve the issued question ID and the owner's answers.

Use `reference/corrections.md` for in-conversation recurring corrections.
The service README's "Merchant business lookup before identification asks"
section is the detailed cache contract.

## Merchant display names before relaying a card

Ledger payloads carry `display_name_missing` and `display_name_resolution`.
Before sending the returned card, resolve each missing merchant from your own
understanding and batch-save its spoken name through this single kit verb:

```
database/kit_life.sh ledger-merchant-names-set --data '{"items":[{"name":"RAW RECEIPT TEXT","display_name":"spoken name","source":"agent_inferred"}]}'
```

Use the exact raw `name` from the payload as the identity. Do not edit receipt
text or build word lists in code. Read `ledger-plan` and `onboarding next`
again after saving, then relay their literal card. Named merchants reuse the
same source in subscription lists, excluded charges and later cards. Naming
does not answer the pending question or change the plan's payment basis.

When the owner corrects a name, use the same batch verb with
`source: "owner_said"` and `said: "the owner's exact words"`. Resolve the
correction to the original raw merchant identity using the issued payload or
`ledger-merchant-names`; never save the old display label as a new merchant.
Owner names override inference, including future agent writes. Ask only when
the merchant or the intended correction is ambiguous.

## Instrument purpose answers, including later chat

Before answering any card/account identity, purpose or reimbursement question,
read `LEDGER_ROOT="${PROJECT_ROOT:-$(pwd)}" database/kit_life.sh purpose-get ID`
(omit ID for the whole roster). The root identifies the calling agent's
memory directory even when its database belongs to a shared crew.
`methods-seen` and `ledger-plan` instruments carry the same `instrument_facts`;
the reduction roster question also carries them outside its display text.
Use the canonical ID, recorded kind, owner roles/purpose, linked account and
scope in those facts as the authority. Owner-set purpose/roles outrank a
profile guess; a linked account guess is not an owner confirmation.

`instrument_facts.memory_evidence` is evidence only. A brand-only name such
as Samsung card is `ambiguous_instrument`, even if the current roster has just
one Samsung card: the company card may not have a record yet. Never apply
that note to every card of the issuer, change a card's role/scope, delete its
payments or exclude spending from it. A missing record stays missing; resolve
the exact instrument before saving any correction the owner actually gives.
An exact old note also cannot override a stored owner fact.

Business use (`사업`) does not mean employer reimbursement. A registered
`corporate_card` expresses an employer-reimbursed company card, separately
from `credit_card` with business purpose. `scope_default` remains the recorded
accounting scope; receipts/allocations determine actual reimbursement math.
Do not manufacture payment-level settlements from a card kind or memory.
For an explicit owner correction use the existing `purpose-set`,
`method-upd ID kind=corporate_card` or `method-scope` verbs as appropriate,
then re-read the facts. Never silently migrate memory into those fields.

Purpose labels come from one fixed kit vocabulary (`purpose-get` returns
`normalized.vocabulary`): 생활비, 월급·고정비, 저축, 사업, 외식·카페,
장보기·간편식, 취미·여가, 송금, 온라인 결제, 섞여 있어요.
Never put an owner's sentence or a new category name in `--tags`. Raw words
are internal evidence in `pay_method.purpose_memo`, never display copy.

For an issued instrument question, pass the exact utterance with `--said`.
The kit's `database/ledger_purpose_lexicon.json` maps spacing variants and
common typos to these labels. When that lexicon cannot map the answer but
its meaning is clear, choose labels only from this vocabulary and pass
`onboarding answer instrument_ID note --said "OWNER WORDS" --tags "LABEL,LABEL"`.
Keep the original utterance intact; do not replace it with your summary.
For later chat, use
`database/kit_life.sh purpose-set ID --tags "LABEL,LABEL" --memo "OWNER WORDS" --origin owner-stated`.
`--tags "" --memo "OWNER WORDS"` also runs the same deterministic mapper.

If the meaning still cannot be assigned to a label, do not invent tags or
repeat the raw sentence. Preserve the tool's guessed purpose (추정) and relay
its one clarification question with the returned label buttons. The
`purpose_action: ask_once_with_label_buttons` response keeps the same
instrument pending. A second unmapped answer leaves the guess and advances;
do not ask again. Later chat's `normalized.needs_clarification` requests the
same single clarification using `normalized.vocabulary` and existing copy
keys. Re-read the roster after saving; relay the kit's purpose labels.
