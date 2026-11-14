# Body corrections (typed kit verbs)

Use the existing kit renderers after writing; do not invent confirmation copy.
Never correct these stores through SQL, an ad-hoc script, or another database.

- `metric-list [--date YYYY-MM-DD] [--metric TOKEN]` returns exact stored rows,
  including note, identity and owner. `trend TOKEN`, `body-brief --json` and
  `body-segment` remain the presentation reads.
- `log-metric DATE TOKEN VALUE [NOTE]` creates/upserts; `log-body-sheet DATE
  NOTE TOKEN=VALUE ...` captures an atomic sheet. `metric-edit DATE TOKEN VALUE
  [NOTE]` corrects an existing observation, retaining an omitted note.
- `metric-del --date DATE --metric TOKEN [--source inbody] [--owning-agent A]`
  removes one reading. Omit `--metric` only for an explicitly withdrawn dated
  record set. Other readings remain. Profile copies follow the existing
  newest-reading fallback contract.
- `metric-undo` reverses the latest metric capture, edit or deletion (a sheet
  counts as one operation), including profile/derived target changes.
- `pain-list`, `pain-recent`, `pain-show --id ID` read pain evidence.
  `pain-add` retains its existing flags. `pain-edit --id ID` accepts any of
  `--pain 0..10 --site TOKEN --side L|R|both|NA --exercise LABEL --load KG
  --set-no N --rir N --rom PHASE --fault TEXT --action ACTION --session LABEL
  --ts 'YYYY-MM-DD HH:MM' --note TEXT --guardrail TEXT --program-change TEXT`.
  Empty optional text clears that field. Edits preserve status. Correct a
  linked event's exercise/load/time only with explicit `--unlink`; otherwise
  use `pain-link --id ID --set SET_ID` to select the correct existing set.
  The set remains the single source of load/sequence/RIR while linked.
- `pain-del --id ID` withdraws an incorrect pain event. `pain-undo` reverses
  the latest pain add/edit/delete/resolve/link, preserving the original row
  identity and evidence. `pain-resolve --id ID [--reopen]` remains supported.
- `body-signal-list [--with-pain]` reads non-pain signals. Existing
  `body-signal-add`, `body-signal-resolve --id ID`, `body-signal-del --id ID`
  are undoable. `body-signal-edit --id ID` takes the same optional fields as
  pain-edit, plus `--sensation stiff|tight|sore`. Lane changes to/from pain are
  refused. `body-signal-reopen --id ID` restores open status explicitly.
  `body-signal-undo` reverses the latest signal operation, including a refresh
  of an existing signal by add.
- `body-profile-set KEY=VALUE ...`, `body-profile-clear KEY ...` and
  `body-profile-undo` handle stored profile/fallback values: weight_kg,
  height_cm, age, sex, skeletal_muscle_kg, fat_mass_kg, lean_mass_kg, avg_steps,
  other_neat_kcal, fat_ratio, goal_mode, target_weight_kg, diet_bmr_method.
  A measured weight takes precedence over the fallback: correct its metric
  row with metric-edit. A kept profile key is retained with an unknown value
  when explicitly cleared or when its first capture is undone; reset's
  delete/rename protections stay in force. Existing `config-set` calls containing only body fields
  also participate in body-profile-undo.
- `diet-target-set --deficit N|--surplus N [--protein N]
  [--style high_protein|mediterranean|low_carb|keto]
  [--protein-mode manual|auto]` corrects the standing target and its ledger
  copy. A settled consult's calorie target follows the corrected balance
  against its stored maintenance. `meal-target-set` remains undoable using
  `diet-target-undo`. `diet-target-clear` withdraws target config (including
  the settled consult base) and retires its nutrition constraint rows.
  `diet-target-undo` restores both stores together. Owner target changes and
  clears take precedence over automatic derived target persistence.
- Read back with `body-state`, `targets`, `macro-targets --json`, `meal-card`
  and the installed health dashboard/pin provider where available.

`body-undo` reverses the most recent operation across these body families.
Family undo verbs take no arguments and reverse the latest operation in that
family. Undo checks all changed fields and row identity; a later edit to an
owned field refuses the entire undo without discarding history. Undo the later
edit first. Unrelated fields/rows are preserved. History and
`owner_corrected` markers use the existing config store; no migration.
