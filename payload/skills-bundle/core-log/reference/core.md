# core-log -- core adapter (profile, availability, resources, preferences)

```
config-set height_cm=<n> | config-set weight_kg=<n>        profile (number only)
availability-add --domain <D> --days MON,WED (--from HH:MM --to HH:MM | --anytime)
                 [--note "<words>"] [--replace] --source chat
resource-add --kind equipment --name "<tool>" [--detail '<json>'] --source chat
resource-add --kind env --name "<place>" --detail '{"venue":"gym|home|other","equipment":[...]}' --source chat
preference-add --topic <T> --item "<I>" [--polarity negotiable|inviolable] [--note "<words>"] --source chat
availability-list | resource-list | preference-list | body-brief      read first
```

- same (domain, days) window / same resource name / same (topic, item) ->
  the verb updates the row; `--replace` only when the owner said it
  replaces the old one.
- domain = the life area the owner named (study, workout, family ...);
  unsure -> `life`.
- No onboarding interview, no homework, no calendar block, no
  `onboard-next` / `onboard-set` for a casual fact.

## Typed chat facts

Read the owner message and choose a fact kind yourself. `fact-route WORDS`
alone is inert. Preserve WORDS as evidence and pass `--data` with one object:

- Stock/ingredient: `{"kind":"stock","item":"butter","quantity":500,"unit":"g"}`;
  ingredient weight uses `kind:ingredient` with the same fields.
- Equipment: `{"kind":"gear","item":"rotisserie"}`.
- Spending: `{"kind":"ledger","item":"lunch","amount":12000}`.
- Move a task: `{"kind":"schedule","task_id":ID,"date":"YYYY-MM-DD","at":"HH:MM"}`.
- Dishes: `{"kind":"dish","names":["dish A","dish B"]}`.

Use `--as-of YYYY-MM-DD` for the stated day, then `fact-landed --date` to read
back the anchors. No action, name, kind, date or amount is inferred from
WORDs. Ask only for a missing fact, never for specific wording.
