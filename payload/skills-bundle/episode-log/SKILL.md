---
name: episode-log
display_name: 경험 기록
description: >-
  Fires when the user narrates a lived experience, or when just-captured records
  (workout / meal / spend / metric) look like one episode; creates or matches an
  experience container and offers to bundle them. Examples: "어제 제주 흑돼지
  먹었어", "오늘 헬스 갔다왔어", "친구랑 저녁", "주말에 데이트했어". Also
  fires when records from a short window share a context that looks like one
  episode and an auto-cluster offer is warranted (trailing-line only, never push).
  Side effects: writes to event / record / event_timeblock in kit_life.db.
  NOTE: enrich / merge / split -> episode-session. config -> episode-setup.
---

# episode-log

Capture-moment handler for the event/experience overlay.
All DB access via `kit_life.sh`. No raw SQL.

Schema: event / record / artifact / record_event / event_timeblock / insight
(026 overlay; timeblock UNCHANGED -- 783 lock).

## paths

- helper: `$PROJECT_ROOT/database/kit_life.sh`
- `LKIT="${PROJECT_ROOT:-$(pwd)}/database/kit_life.sh"`; CWD = workspace root.

## SDK verbs (026 overlay -- LIVE in kit_life.sh dispatch as of uv27)

All verbs below are dispatched by `kit_life.sh`. Call them directly; NO raw SQL.

```
experience-create    <title> [kind_hint] [note] [--reaction <text>]  -> event id + ulid
experience-find      <title> [date]                 -> tier + rows (find-first)
experience-list      [date_from] [date_to]          -> events (pending flag)
experience-attach-record  <event_id> <source_table> <source_id> [role] [weight] [source]
experience-attach-timeblock  <event_id> <timeblock_id>
experience-detach-record  <event_id> <record_id>
experience-detach-timeblock  <event_id> <timeblock_id>
```

## hard guards (tool-layer; NOT prose)

1. **find-first / no blind insert**: before `experience-create`, run
   `experience-find <title> [date]` (or equivalent). Tier=EXACT/FUZZY match ->
   surface existing event, ask user to confirm reuse or create new. Only
   tier=NO_MATCH -> create.
2. **source_table whitelist**: `attach-record` accepts only
   `{workouts, metric_log, spend_entry, artifact}`. Any other token -> reject
   with error, zero mutation. Registry is additive (new source = new registry
   entry); never invent a token.
3. **on-utterance accrual (INSERT OR IGNORE)**: record pointer for a
   (source_table, source_id) pair is deduped at the UNIQUE constraint -- safe
   to attempt attach on every mention, no pre-check needed.
4. **DGN-892 non-autonomy gate**: when routing subjective facet (reaction /
   meaning) to memory, relationship / emotion / health inferences -> store
   verbatim utterance ONLY. No paraphrase, no inference-storage. Tool gate,
   not model discretion.

## procedure

### A. experience utterance (no prior record)

1. Parse: title (required), kind_hint (optional coarse: trip / meal / workout /
   outing / other), date/time context, note.
2. find-first gate: `experience-find <title> [date]`
   - EXACT/FUZZY hit -> show match, ask user: reuse or new?
   - NO_MATCH -> `experience-create <title> [kind_hint] [note]` -> capture event id.
3. If context includes timeblock(s) covering the episode period:
   `experience-attach-timeblock <event_id> <timeblock_id>` per block.
4. Objective facets (when / who / where / how much) -> kit_life.db record/
   existing module rows. Subjective facet (reaction, why) -> event.reaction
   column via enrich or note param at create time (brief, user's own words).
5. Routing split:
   - objective -> kit_life.db (record/facet columns)
   - subjective -> event.reaction + memory anchor (verbatim, DGN-892 gate)
6. Report to user: brief, module-term only. e.g. "하나의 경험으로 담아뒀어요".
   Never say "기억해둘게요" (generic memory term, A3 forbidden).

### B. just-captured records look bundlable (auto-cluster offer)

Trigger condition: driver module (workout-log / meal-log / spending-log etc.)
just stored a record AND there are 1+ other records within a short window
sharing context (same ~time window, same place, same person). Evaluate
AFTER the driver module completes -- never interrupt capture.

1. find-first: search existing events covering this window.
   - existing event found -> offer to attach the new record to it.
   - no event -> offer to create a new episode container.
2. Offer = **trailing-line only** on user-initiated turn. One line, one
   decision. e.g. "방금 것들 한 경험으로 묶어둘까요?"
3. k-decay: if user ignores (no response), increment offered_k. k=2 ->
   auto-decline. Only re-offer from a different trigger class.
4. On "예": execute find-first -> create-or-reuse event ->
   `experience-attach-record <event_id> <source_table> <source_id>
   role=<primary|incidental>`.

### C. attaching a record to an existing episode

User says: "그 경험에 운동 기록도 넣어줘" or similar.

1. find-first: resolve target event.
2. Identify source_table + source_id from context.
3. Whitelist gate on source_table.
4. `experience-attach-record <event_id> <source_table> <source_id> [role] [weight]`
5. Confirm: "그 경험에 같이 붙여뒀어요." (brief).

## cognitive budget

- auto-cluster offer: 1 decision / ask ("묶을까요?").
- round-trips: capture = 0 (auto store) / clustering = 1.
- never re-ask objective facts the driver module already captured.

## output

- brief, no bold/markdown table in chat reply.
- confirm in module terms: "하나의 경험으로 담아뒀어요" / "그 경험에 같이 붙여뒀어요" / "한 경험으로 묶어둘까요?"
- never expose DB ids, table names, or SQL.

## cross-skill pointers

- enrich / merge / split / distill -> episode-session.
- auto-cluster proximity threshold / lens default -> episode-setup.
