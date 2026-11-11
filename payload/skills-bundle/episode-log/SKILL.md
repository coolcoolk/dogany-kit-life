---
name: episode-log
display_name: 경험 기록
description: >-
  Capture an actual lived experience the owner narrates, and match or create its experience container using existing kit
  evidence. After workout, meal, spend or metric capture, evaluate related records as one episode only when shared
  context and episode-management state warrant an auto-cluster proposal; offers are trailing-line only, never push.
  Capture remains available while management is off. A bare wish to begin keeping or managing experiences, without an
  experience to record and with episode setup outstanding, hands to episode-setup before any generic capture question.
  Writes event, record and event_timeblock in kit_life.db. NOTE: enrich / merge / split -> episode-session. config ->
  episode-setup.
---

# episode-log

Capture-moment handler for the event/experience overlay.
All DB access via `kit_life.sh`. No raw SQL.

Schema: event / record / artifact / record_event / event_timeblock / insight
(026 overlay; timeblock UNCHANGED -- 783 lock).

## paths

- helper: `$PROJECT_ROOT/database/kit_life.sh`
- `LKIT="${PROJECT_ROOT:-$(pwd)}/database/kit_life.sh"`; CWD = workspace root.

## SDK verbs (026 overlay -- LIVE in the kit dispatch table (kit_life.sh) as of uv27)

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
   `experience-find <title> [date]` (or equivalent). Tier=EXACT -> reuse it
   (enrich), no question. Tier=FUZZY -> surface the existing event, ask once
   whether it is the same one. Only tier=NO_MATCH -> create.
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

1. Before parsing/asking for a title: for start/use intent with no experience
   content, run `kit_life.sh manage-state episode` (MANAGE<TAB>episode<TAB>on|off<TAB>reason).
   Off -> episode-setup; on -> ordinary episode use. Actual experiences capture
   either way. Otherwise parse title (required), optional kind_hint (trip / meal /
   workout / outing / other), date/time context and note.
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
6. Reply to user (chat-first, unplanned "I did X"): one warm reaction line +
   ONE question on the most useful missing facet (what / who / where / when /
   cost / how it felt), owner's register. Never several questions, never
   "기록했어요" / "완료" wording. Answers go through their adapters and attach
   to the event; next facet only while the owner keeps engaging. Shape and
   rules: core-log/reference/episode.md.
   Never say "기억해둘게요" (generic memory term, A3 forbidden).

### B. just-captured records look bundlable (auto-cluster offer)

Episode management only: run B only while `kit_life.sh manage-state
episode` is on. Off -> capture (A) and stop; no offer line.

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
- confirm in module terms only for non-chat paths (C attach): "그 경험에 같이 붙여뒀어요"; the
  bundling question "한 경험으로 묶어둘까요?" only from section B.
- never expose DB ids, table names, or SQL.

## cross-skill pointers

- enrich / merge / split / distill -> episode-session.
- auto-cluster proximity threshold / lens default -> episode-setup.
