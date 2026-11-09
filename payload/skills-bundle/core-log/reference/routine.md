# core-log -- routine adapter (habits, cadences, completions)

A stated habit is a fact, not a registration. Future occurrences,
reminders and ride placement come from routine-manage, only on an explicit
request.

## a habit or cadence the owner states

"every weekday 20 minutes of <ACTIVITY>", "I usually <ACTIVITY> Mon/Wed":

```
preference-add --topic habit --item "<activity, owner words>"
  --detail '{"cadence":"<daily|weekdays|weekends|MON,WED,...>","minutes":N,"time":"HH:MM"}'
  --note "<the sentence, verbatim>" --source chat
preference-list --topic habit                                   read first
```

- detail keys only for what was said; none said -> no `--detail`.
- same activity again -> the same verb updates the row (find-first on
  topic + item). A changed cadence -> same call with the new detail.
- no `routine add`, no notification question, no ride offer, no
  confirmation question.
- an explicit request to put it on the calendar or be reminded ("register
  it", "remind me") -> `kit_life.sh manage-set routine on`, then
  routine-manage's register flow (`.claude/skills-bundle/routine-manage/SKILL.md`).

## a completion

"done with today's <ACTIVITY>" / a done-screen photo -> the schedule
adapter's completion rule (`task-done-today` first). No matching routine or
task -> the schedule adapter's NOT_FOUND rule.
