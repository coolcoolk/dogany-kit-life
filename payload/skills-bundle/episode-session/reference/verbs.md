# episode-session -- verb signatures (kit_life.py, 2026-09-26; code wins)

Read when you need an argument order or an output shape.

```
experience-find       <title> [date]                   -> tier + rows
experience-list       [date_from] [date_to]            -> events; pending = reaction IS NULL
experience-enrich     <event_id> [reaction=<text>] [note=<text>]
                      [kind_hint=<text>] [title=<text>]
experience-merge      <source_event_id> <target_event_id>
                      relink record_event + event_timeblock to target, delete source row
experience-split      <event_id> <record_id,record_id,...> [new_title]
                      new event; listed record links move; timeblock links stay
experience-query      <objective|subjective> [date_from] [date_to]
experience-rollup     <event_id>                       spend / kcal / distance / duration / persons
experience-distill    <event_id,event_id,...> --text <text> [--agent <a>]
                      verb mints insight ulid; evidence = JSON array of EVENT ulids
experience-delete     <event_id> [--force]
                      insight references this event -> INSIGHT_REF <n> + rows, exit 3,
                      nothing changed. --force only after user confirms.
                      removes links + event row; record/timeblock/artifact survive
experience-detach-record     <event_id> <record_id>     link only
experience-detach-timeblock  <event_id> <timeblock_id>  link only
experience-delete-insight    <insight_id>
experience-delete-artifact   <artifact_id>             also removes its pointing record
                                                        (delete-source); id never reused
```

## Typed corrections (2.46.0)

```
experience-merge-undo SOURCE_ID       restore source, its links and target fields
experience-split-undo SOURCE_ID       restore moved links, remove unchanged new event
experience-person-upd EVENT OLD_PERSON_ID NEW_PERSON_ID
experience-detach-person EVENT PERSON_ID
experience-person-undo EVENT          reverse latest person correction
experience-parent-undo EVENT          reverse latest set-parent/clear
experience-insight-upd ID text=WORDS
experience-insight-undo ID            reverse latest text edit/deletion
experience-artifact-list              JSON id/ulid/kind/ref/ts/owning_agent
experience-artifact-upd ID kind=K ref=VALUE ts=ISO_DATE_OR_OFFSET_TIME
experience-artifact-undo ID           reverse latest artifact edit
```

All undo operations require a receipt created by these shipped verbs and refuse
later changes to owned fields or later dependent links. No ad-hoc SQL. Empty
artifact `ref=` / `ts=` clears; nonempty kind/text required. A corrected artifact
time also corrects its record timestamp. Parent changes use existing
`experience-set-parent CHILD PARENT|-`; exclusion memory still uses
`experience-exclude-child` / `experience-unexclude-child`. Existing
`experience-list-insights [EVENT_ULID]` reads corrected insight text.

`experience-enrich-undo EVENT` reverses the latest legacy or structured
retro enrichment. Reaction/note correction and clearing use existing
`experience-enrich EVENT reaction=WORDS note=WORDS` (empty clears). Artifact
undo also restores deletion, its record pointers and links. Person undo also
reverses an attachment made after this version shipped.
