# goal-session: corrections mid-interview

Read when the user changes a proposed value, says what a strand is FOR, or
replaces the whole direction.

- a correction with a direction ("좀 더 빨리", "기간 짧게"): never ask
  which one -- note it, then `--next` re-proposes the options with that ONE
  variable moved (fewer than three when the muscle floor leaves fewer):
  ```bash
  database/kit_life.sh goal-move-note --why-id N --correct <axis_key> --toward <faster|slower|longer|shorter|narrower|wider|higher|lower> [--toward <2nd>]
  ```
  two directions: pass both; the tool moves the one the strand can take and
  names the other in one line.
- a held range "too wide" (`--toward narrower` on it): no menu, nothing
  narrows; the next move's `readback` says in one line what the width is.
- their own number ("400으로 할래"): `--correct <axis_key> --value 400`; the
  next move shows it next to two computed options with its impact. picked
  again -> stored as theirs (`target_from: user`). on the lift strand
  (per-session progression) the number is a stretch note on the strand, the
  per-session way stays, one line rides the next `readback`; no menu.
- says what a strand is FOR ("풀업만 말고, 몸이 연결되게, 다치지 않게"):
  ```bash
  database/kit_life.sh axis-direction <axis_key> --text "<their words>" --label "<짧은 말>"
  ```
  its line rides next move's `readback`, right under the card. new direction
  changes what to measure -> re-run `axis-add --why-id N --axis "<same
  strand>=<metric>"` (strand's links become exactly that list). then `--next`
  rebuilds the proposal.
- new overall direction: `why-pivot <old_id> --text ... --label ...`, then
  `--next` (starts from the strands of the new direction).
