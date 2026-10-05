---
name: episode-session
display_name: 경험 돌아보기
description: >-
  Fires during a review or reflection session -- fills pending-enrichment blanks
  on episodes, merges or splits episodes, queries episodes by two lenses
  (objective resources vs subjective meaning), rolls up derived facets, and
  distills insights on demand. Trigger phrases: "경험 돌아보기", "지난 경험
  정리", "기록 채우기", "경험 합쳐", "Insight 뽑아줘", "어떤 경험이 있었나",
  "묶은 경험 조회". Not loaded every session -- fires only on explicit
  review/reflection intent. Side effects: writes to event / insight in
  kit_life.db; detach-only for junctions.
---

# episode-session

Review-session handler for the event/experience overlay (event / record /
artifact / record_event / event_timeblock / insight). All via
`LKIT="${PROJECT_ROOT:-$(pwd)}/database/kit_life.sh"`; no raw SQL. Verb
shapes: read reference/verbs.md when you need an arg order.

## hard rules

1. deletes are non-destructive by code: links go, record / timeblock
   originals never touched.
2. `experience-delete` refuses (INSIGHT_REF, exit 3) when an insight cites
   the event -> ask "이 경험을 근거로 한 인사이트가 있어요. 그래도 지울까요?";
   `--force` only on explicit yes.
3. distill = pull-only: never auto-trigger; user must ask. verb mints the
   insight ulid and builds evidence from event ids.
4. zero-hit gate: before "no episodes found" run `experience-list` + memory
   search; confirm absence only on true 0 hits.
5. relationship / emotion / health (DGN-892): store verbatim utterance only,
   no inference; distill needs verbatim evidence present.

## procedures

- enrich: `experience-list` -> events with blank reaction / key facets ->
  ask only the blanks (typ. 사람 / 감상, cap ~2 per event) ->
  `experience-enrich <event_id> reaction=<user text>`. when / where / how
  much come from `experience-rollup`; never re-ask.
- merge (duplicates / over-split): find both first ->
  `experience-merge <source> <target>` -> "두 경험 하나로 합쳐뒀어요."
- split (over-bundled): user picks records ->
  `experience-split <event_id> <record_id,...> [new_title]` -> "따로 떼서 새
  경험으로 나눠뒀어요. 이름 붙여줄까요?"
- query, two lenses (default per episode-setup, else objective):
  objective "어떤 자원이 묶였나" (`experience-query objective ...`),
  subjective "어떤 의미였나" (`experience-query subjective ...`).
  compact; no markdown tables; tabular -> code block or image card.
- distill (asked): identify events -> `experience-distill <id,id> --text
  "<insight>"` -> "여기서 얻은 인사이트 하나 남겨뒀어요." + insight text.
- delete event: `experience-delete <event_id>` (rule 2) -> "경험 묶음만
  지웠어요. 안에 있던 기록들은 그대로 남아 있어요."
- detach one link: `experience-detach-record` / `experience-detach-timeblock`.
- delete artifact (no other source) / insight: confirm first ->
  `experience-delete-artifact <id>` / `experience-delete-insight <id>`.

Budget: enrich = blank count per event (cap ~2); merge/split 1 confirm
each; distill 0 auto. output: brief, no DB ids or table names.

Pointers: create-event / attach / auto-cluster -> episode-log. lens default
/ cluster threshold -> episode-setup.
