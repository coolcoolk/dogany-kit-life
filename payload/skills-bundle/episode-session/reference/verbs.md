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
