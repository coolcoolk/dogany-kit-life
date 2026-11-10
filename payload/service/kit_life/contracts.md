# Contracts and warranties / existing contracts domain

This service implements the existing default-on module_contracts domain. It
ships through module_core, alongside always-on capture, without a new skill
or install unit. Contract and warranty share this one service and reminder.

Use `service/kit_life/contracts ACTION --data JSON` (or the identical
`database/kit_life.sh contract ACTION --data JSON`). Relay `card.text` as HTML
and `card.buttons` as supplied, one button per row. The command does not send.
`dispatch {"token":"opt:contract cost 1 2"}` resolves a literal button tap.
Never bind a bare label to the newest record. IDs/revisions are routing data,
not owner-facing text. A stale tap returns the current overview, without edits.

Capture always works with management off. The owner asked to track these
records and arm a reminder by default. Saving the supplied facts happens
before proof copying or reminder scheduling. Relay any `enrichment_errors`
as a short owner-language failure, retain the partial record, and retry the
same id. Never claim that proof or a reminder was saved when its enrichment
failed. `reconcile {id}` retries schedule enrichment without another record.
The existing default-on module_contracts management preference is reused. An
explicit module decline suppresses automatic checks while preserving capture;
a specific reminder_enabled:true request can still arm that individual check.
No new recurring service, routine or alert sender is needed.

## Capture and correction

Read the source, then `list {query:"provider or title"}` before choosing an
existing record. `capture` accepts:

```
{"title":"Basic line","provider":"Example telecom","kind":"telecom",
 "record_key":"phone-line-ending-1234",
 "source":"screen","source_ref":"sha256:original-content-digest","source_slot":"0",
 "words":"original visible text, not a fabricated transcription",
 "facts":{"amount":33000,"cycle":"monthly","starts_on":"2025-11-15",
          "ends_on":"2026-11-14","auto_renew":true,"renews_on":"2026-11-15",
          "cancel_by":"2026-11-01"},
 "proof_path":"/path/to/received/original.png","proof_label":"Bill"}
```

Do not require this complete object: **title alone is enough**. Null/omitted
facts stay unknown. An omitted field on update preserves its value; explicit
null clears it. Preserve the original cancellation wording as `cancel_terms`.
Use dates visible in the source, not today's date for contract signing.
`source` is owner/document/screen; field overrides use `sources:{field:source}`.
Derived dates use calculated. Cards label facts [본인]/[서류]/[화면]/[계산].

An identical original must keep its `source_ref` even when forwarded with a
new message ID; compute its content digest when available. Sentence captures
use channel/chat/message identity. `source_slot` distinguishes subjects on
one receipt. Repeating a source preserves later corrections. A new page or
source updates the existing `id`, never a new provider-only record. Use a
stable `record_key` for a resolved account/line/device, never just the provider.
Ambiguous lines/devices need one distinguishing question; save a separate
partial record first rather than overwriting either existing record. Later
merging an ambiguous duplicate is not currently automated.

`update {id,revision,facts:{...}}` is the correction reply. `edit` and
`reminder` return a bound `reply.data` for that reply. Use only corrected fields.
`get`, `cost`, `proof`, `resolve-proof`, `reconcile` take `{id}`; list is read-only.
Cost queries do not cancel contracts or stop financial streams.

A bill/contract creates no payment. With an amount and cycle, capture links
an exact unclaimed recurring stream or creates one transactionally. Explicit
`fixed_id` reuses a known stream whose display name differs. Provider name
alone never matches financial lines. Agreed amount is separate from the latest
observed bill. Merge repoints links; ledger reset detaches them and preserves
contracts, evidence, proofs and reminders.

## A purchased device is a warranty contract

Run the existing shopping capture first (including its normal payment/review
policy), then pass its **existing** `owned_id`, `kind:"warranty"`, and title.
No second purchase, payment or stock refill. A receipt with no warranty wording
establishes no coverage duration. Record it and store the original anyway.

```
{"title":"Vacuum","kind":"warranty","owned_id":12,
 "source":"owner","record_key":"owned:12:manufacturer",
 "facts":{"term_months":24,"bought_on":"2026-10-21","start_basis":"purchase"}}
```

Only an explicit purchase start basis uses purchase day. A known delivery/start
date uses starts_on. Printed/stated ends_on wins. Calendar arithmetic uses the
inclusive final day (2026-10-21 + 24 months - 1 day = 2028-10-20), clamping a
month-end anniversary before subtracting a day. The card marks this [계산],
not as manufacturer-verified eligibility. Separate extended coverage gets a
separate contract key linked to the same possession.

## Reminder and cost policy

One normal nonexclusive `task`, start_only, at 09:00 in AGENT_TZ (Seoul default).
Choose cancel_by before renews_on before ends_on. Default lead: cancellation
7 days, end/renewal 30 days. **Provisional owner preference**: owner still needs
to choose the lead. Override with reminder_days and reminder_at; false
reminder_enabled cancels the pending check. No known date means no made-up task.
Late capture schedules one check next minute. The normal due selector owns its
five-minute delivery window; no new catch-up guarantee is made.

Date edits move the same open task; unchanged capture preserves snooze. Done,
cancelled and deleted checks stay closed rather than silently reappearing.
The reminder editor offers an explicit rearm button for a closed/deleted check.
It creates one new current check while preserving the previous task history.
Completing a check never cancels a contract. Contract cancellation/return must
be supplied as `facts.status:cancelled|returned|disposed`, which closes a pending
check. Purchase date corrections update calculated coverage; return/disposal through
the shopping verbs closes the check. Stock use never changes warranty dates.

Cost: `amount` and cycle show ongoing monthly reduction only for monthly
contracts. This month's cash effect stays 미확인 unless effective_on, quote_on
(in the current month), fee, refund, avoidable and unavoidable are all explicit.
Integers may be zero; unknown is null. The calculation is avoidable + refund -
fee - unavoidable. Never infer refunds from annual prepayment or derive fees
from unsupplied provider rules. Record a new dated quote when terms change.

## Proof storage and the delivery boundary

Original bytes are kept under files/contracts/<owner-hash>/<sha256>.<extension>,
mode 0600 in an owner-only directory. contract_proof.ref is relative to the kit
instance root. Shared receipts may link to multiple records. Copy + fsync precedes
reference creation; identical bytes do not duplicate a record's proof. Backups
must retain this files directory alongside the database. Restore both under a
new instance root; relative refs need no rebasing. No automatic file deletion.

`resolve-proof` validates owner scope, path containment and digest and returns
machine-only attachments. It does **not** resend a document. `proof` is truthful
about that limitation, and never presents a filesystem path as a clickable URL.
See docs/handoffs/contract-proof-delivery.md for the exact shared-code request.

Choose contract capture/update from the owner intent yourself; contract words
no longer route the prompt hook. Use the explicit actions and typed data above.
The hook only binds issued callback tokens and bridge attachment metadata.
