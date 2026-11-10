# core-log -- goal adapter (aspirations, directions)

"someday I want to run 10 km comfortably" is the owner's direction, kept
verbatim:

```
why-add --text "<the owner's sentence, byte-for-byte>" --spectrum aspiration [--domain <D>]
why-list [--domain D]                                           read first
```

- `EXISTS` (exit 3) = the same sentence is already kept.
- no `--label`, no target, no cadence, no goal interview, no label
  question; `WHY-LABEL-ASK` lines appear only while goal management is on.
- `--domain health` when the sentence is about body, fitness, diet or
  training (the health goal consult reads only its domain's sentences);
  another plainly single domain -> that domain; unsure -> omit.
- A later goal consult (goal-session) starts from this sentence; it is never
  asked again.
