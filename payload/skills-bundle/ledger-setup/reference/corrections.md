# Correct an entered recurring fact in the same conversation

When the owner corrects an amount, cycle, next due date or name, apply it in
that turn and relay the tool's correction outcome. Existing facts remain
correctable while onboarding asks another question.

During onboarding, run:

```
service/kit_life/ledger/onboarding fixed-correct <name|id> field=value [...] [--direction expense|income]
```

Fields: `cycle`, `amount`, `next_due`, `name`. Cycle tokens: `monthly`,
`quarterly`, `semiannual`, `yearly`. For example, an owner correcting
property tax from semiannual to yearly needs only `cycle=yearly`; keep the
amount as recorded. A missing field is preserved. `next_due=` clears a due
date; otherwise use YYYY-MM-DD.

On success, relay `say` verbatim, then deliver the returned `question.ask`
and its buttons unchanged. The tool lands the correction, refreshes affected
figures, and resumes the same pending question. Keep its previous answers;
the correction does not answer the pending question. Do not restart or defer
onboarding for a correction. A correction can be repeated safely: an already
matching row returns `unchanged` and a finished outcome.

Outside onboarding, run:

```
database/kit_life.sh fixed-correct <name|id> field=value [...] [--direction expense|income] --json
```

Relay the tool's correction outcome (`say`). It supplies `before`, `after`
and `changes`; every owner-facing correction sentence comes from the ko/en
catalog. The plain-text form returns that same outcome without JSON.

Resolution uses an id, normalized name or recorded bank alias; a partial
name is accepted only when it identifies one row. `--direction` narrows
income versus expense. `ambiguous` returns candidate ids, names, amounts,
directions and cycles: ask which existing row, then retry by id. `not_found`
creates nothing: relay that outcome and resolve the intended existing name
with the owner. Unknown cycle tokens and invalid dates are rejected before
any field changes. Do not claim success for either failure.

`fixed-add --find-first` keeps its original first line `EXISTS<TAB>id` and
exit 3. If the explicit cycle, amount or due date differs, its separated
second line `CORRECTION<TAB>JSON` gives the differences, a relayable `say`,
and ready `correction` arguments. This is a duplicate check, not an update.
Route the owner's stated changes to the correction command in the same turn
and relay its completed outcome; do not ask permission again for a change
the owner already requested. During onboarding use the onboarding command
above so the pending screen refreshes. An identical duplicate retains the
original single-line output. An omitted cycle or due date is not a request
to reset one.

Owner prose is evidence, never a routing key. Interpret a second-venue correction
and call `service/kit_life/ledger/onboarding revisit RECEIPT_ID` (or `say WORDS
RECEIPT_ID`); no particular words are required. Never route an ordinary answer
into the allocation editor because of a phrase.

For a monthly AI target use `ledger-review-answer --month YYYY-MM --data
'{"kind":"monthly_cap","amount":600000,"said":"exact owner words"}'`.
In a pending living screen, answer with `{"monthly_cap":{"amount":600000}}`;
it saves the target and keeps that screen pending. Insurance amounts use the
same kit verb with `{"kind":"insurance","amount":N,"said":"..."}`.
For `reduce_subscriptions`, a kept roster with a dated change is typed JSON:
`{"action":"keep","facts":[{"key":"codex","amount_change":{"amount":100,"currency":"USD","effective_from":"YYYY-MM","scope":"vendor"}}]}`.
Select only issued keys and stated values. A billing comparison uses
`{"action":"next_bill","key":"codex","plan":"owner-stated plan name"}`;
`--said` supplies evidence without imposing a word-matching veto on typed picks.

For loan answers supply `repayment_type` as `equal_payment`, `equal_principal`,
`bullet`, `credit_line`, or `unknown`; `said` and `repayment_words` never override
that typed value. Use the issued loan identity.

Fixed-cost type corrections use `ledger-review-answer --data` with
`kind:"fixed", id:ID, answer:"utility"` (or another issued class enum), and
`said` holding the original words. Code never extracts a class from `said`
or a free sentence in `answer`. Supply the cycle separately as before.
