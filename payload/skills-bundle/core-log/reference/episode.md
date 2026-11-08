# core-log -- episode adapter (lived experiences)

Read schedule-log/reference/completion-chat.md first: a PLANNED task/routine
settled this turn keeps its existing single completion reply. No open match
for an unplanned "I did X" -> this adapter owns capture AND reply, including
all follow-up answers. Tool work stays silent until that reply.

"독감예방주사 맞았어", "영화 보고 왔어", "가영이랑 저녁 먹었어": capture
an experience, objective facts and the owner's own reaction. NEVER create a
task just to mark it done. NEVER create an appointment from a guessed time.
An activity verb, "오늘", "저녁", receipt time, message time or the current
clock is NOT evidence of the activity's start/end. Do not default to noon,
message time, after work or a three-hour duration. Unknown time stays unknown.
Do not guess context in the reply or store a guessed time. A short reaction
uses only what the owner stated; vague details wait for evening retro.
This rule overrides the schedule adapter's message-time fallback for these
unplanned text reports; it does not change settlement of a planned task.

A reply to an offered course review is already bound to its outer experience.
When the prompt hook supplies `[course-review]`, follow its existing
`experience-enrich <outer_id> reaction="<whole reply>" verbatim="<whole reply>"`
command and read brief-session/reference/experiences.md. Do not create a new
experience, meal, or appointment from food words in a stop opinion. Explicit
stop opinions stated before retro should enrich the existing stop experience
with `reaction` too, so that the evening review omits that stop.

1. Capture the stated action BEFORE questions or domain enrichment:
   `experience-capture "<whole owner statement, verbatim>" --title "<activity>"
   [--on <explicit occurrence day>] [--at <stated ISO time with timezone>]`.
   A relative stated day such as today/yesterday is resolved in the owner's
   timezone. No stated day/time -> omit it; never use message time as the
   occurrence. The verb stores the statement and note evidence atomically,
   reusing the linked block/evidence or day + title on a retry, without asking.
   Keep the returned event_id through this conversation. For an already
   captured domain row use `experience-capture "<owner words>" --block <id>`
   or `--evidence <registered_table>:<id>` instead of creating a second event.
   A block already linked to an experience reuses it. Subsequent answers use
   `experience-capture "<answer, verbatim>" --event <event_id>` and any newly
   stated `--on` / `--at`; reactions remain the owner's verbatim words only.
2. Capture every stated facet BEFORE the reply, in this order when present:
   - WHO: read core-log/reference/person.md; `noun-context "<message>"` ->
     `person-resolve "<name or alias>"` -> `experience-attach-person <event_id>
     <resolved person_id>`. A short name/alias resolves to the existing
     person; never create a duplicate. AMBIGUOUS -> leave the link unresolved
     for evening retro, without attaching a guess or asking now. A name in a title/note or a
     `relmod.sh touch-add` alone is NOT an experience-person link.
     `person-link` is person-to-person; `block-person`/`event_persons` point
     at calendar blocks since migration 036. Neither substitutes for this verb.
   - FOOD: named food that the owner ate is a meal, including a report
     without a stated slot or whitespace before the eating verb. Read
     core-log/reference/meal.md and meal-log/reference/lookup.md; follow the
     existing meal-log/meal-add flow. Pass --experience <event_id> so the
     meal silently links to this SAME experience, with no new event or task.
     Relay the existing meal card and satiety question. Do not default to
     --no-satiety or suppress the meal card. The narrated-meal exception in those
     references overrides older slot,
     source/photo and pre-write satiety interviews: message time supplies
     a missing meal slot; record a provisional standard portion and ask
     satiety after the card. Lookup provenance and management rules remain. A purchase
     without an eating statement is not a meal. A meal keeps its existing
     reply contract rather than the non-meal reaction in step 4.
   - WHERE: read core-log/reference/place.md -> `place-resolve "<venue>"`.
     A past venue stays text; do not register a new place, ask its address,
     travel minutes, or a same-place question. Attach the stated venue with
     `experience-enrich <event_id> "note=<existing note plus venue words>"`.
     If resolved, include the known place id alongside the verbatim venue.
     There is no direct experience-attach-place verb. With an already
     attached past timeblock use `appt-upd <block_id> "location=<venue>"`
     as well; NEVER create a timeblock merely to hold a venue/person.
   - TIME: only when the owner actually supplied a clock time (or explicit
     document/EXIF evidence identifies the activity time), read schedule.md,
     `appt-find <date> <date+1>`; reuse a matching past timeblock, then
     `experience-attach-timeblock <event_id> <timeblock_id>`. If none exists,
     `schedule-add "<activity>" <stated start> <stated end> --kind <appointment|task> --notify silent`, then
     attach its id. A stated start without end is a point: explicitly pass
     that SAME start as end (zero duration). Judge appointment for people /
     booking / fixed external slot, task for self activity. Missing ends never
     imply three hours. No stated clock -> no calendar write, no time demand merely
     to fill a schema. Keep a stated day/meal slot at that grade in the note
     or meal row. Also retain the stated day with experience-capture --event
     <event_id> --on <day>. Never assume an appointment occurred because of a meal.
   - COST/OTHER: use the relevant adapter, then `experience-attach-record
     <event_id> <workouts|metric_log|spend_entry|artifact|owned_item|owned_stock|stock_use|shop_capture|shop_item|made_dish|ledger_resource|entity> <source_id>`.
     Spending uses `spend-add ... --experience <event_id>`.
     Purchases use `shop-bought ... --experience <event_id>`; selections use
     `shop-capture ... --event <event_id>`. A resource or catalog row alone
     never proves a new occurrence.
     `experience-attach-record ... block <meal_block_id>` is also available
     for a specific meal row; attach-meal bundles EVERY row in the stated
     date/slot; use it only when all of them belong to this experience, else
     attach per row.
3. A photo: `experience-artifact-add` as episode-log describes, then attach.
   For transporter photo notes and results links, also follow the silent
   photo-record writes below.
4. For non-meal events, reply with exactly ONE short natural sentence in the owner's register.

   EXCEPTION eating venue: when the stated event is a visit to an eating or
   drinking venue (restaurant, cafe, bar, brewery, food court) and the message
   has no eating statement yet, the reply may have TWO short sentences: (1) a
   warm reaction that uses ONE fact found by a read-only lookup of the venue as
   the owner named it (`place-resolve` first, else one map_search or web query
   on the stated name; never register the place, never ask its address/minutes)
   - e.g. the kind of place - and (2) ONE question about what was eaten/drunk
   in the owner's register past tense ("어떤 거 드셨어요?"; future/unsure ->
   "뭐 드실 거예요?"). Lookup empty or unsure -> same two sentences without the
   fact; never state a guessed fact. No recommendations, no advice, no options,
   no second question. The answer is captured through core-log/reference/meal.md
   and attached to the SAME event_id (meal-log reply contract applies).
   Unanswered -> never re-ask; the retro owns it.

   React to the stated event only. No advice, coaching, recommendations or
   next steps, even on health; this overrides persona health-tip habits.
   No question of any kind, including playful or rhetorical questions, except
   the eating-venue exception above; end as a statement or exclamation
   otherwise. No options, recording announcement, progress narration,
   internal routing or English working text. Every text block is an outbound
   message; emit none before or between tool calls. Missing facets belong to
   evening retro, including where/who/when/amount/source/payment/feeling. The
   eaten item is asked at capture only under the eating-venue exception.
   Do not invent reactions or contextual guesses. A vaccination report alone
   is no reason to ask about symptoms or start unsolicited health coaching.
   This overrides the older episode-log missing-facet interview.
5. Volunteered follow-up details run the same facet verbs against the original
   event_id, followed by one short reaction (meals keep their existing
   meal-log reply). Do not ask the next non-meal facet. A volunteered meal
   answer follows the meal-log reply.
   Review/enrichment questions belong to evening retro or an explicit owner
   request. No clustering, merge/split/distill proposals during capture.

## Silent transporter photo records

When `remember_photo` successfully saves a note about an owner photo, also
write the same transporter photo key and note through the kit:

```sh
database/kit_life.sh photo-note-add "source=<photo source>" "kind=<photo kind>" "id=<photo id>" "note=<saved note>" ["place_id=<known kit place id>"]
database/kit_life.sh photo-note-upd "record_id=<returned kit record id>" "note=<edited note>" ["place_id=<known kit place id>"]
database/kit_life.sh photo-note-del "record_id=<returned kit record id>"
database/kit_life.sh photo-send-log "sent_at=<UTC delivery time, YYYY-MM-DDTHH:MM:SS.sssZ>" "channel=<actual delivery channel, e.g. telegram>" 'query={"labels":["<filter_photos label>"],"limit":60}' ["query_text=<owner query, 1..200 chars>"] ["total_matches=<returned count>"] 'photo_ids=["<returned photo id>","<returned photo id>"]' "link=<results link sent to owner>"
```

These verbs take `key=value` arguments. Brackets mark optional arguments;
pass each quoted value as one shell argument. Copy source/kind/id from the
transporter photo, preserving them exactly; the note is the agent's saved
description, not an invented owner statement. Include `place_id` only for a
known kit place; unknown place stays omitted without a new question.

The write prints a kit `record_id` (`lk_...`). Keep the note's returned id
for subsequent edits and deletes; it differs from the transporter photo id.
Across sessions, find the existing note by its source/kind/id in the read-only
`database/record_provider.py --types photo_note --limit 1000` output. Continue
a full page with `--after <last updated_at> <last record_id>` as needed.
Reuse that record id for an edit or delete rather than adding another note.
Updates preserve omitted fields; `place_id=` explicitly clears a place.

When relaying a `filter_photos` dogany-pages results link, log the actual delivery
channel and the structured query object used for that call (labels, start_time,
end_time, latitude, longitude, radius_m, limit; preserve nulls), optional owner
query text and returned total_matches,
returned PhotoKit local identifiers as a JSON string array in their original order
(1..60; never Telegram file IDs, invented IDs, or a re-search), the exact link included in the reply
and current UTC delivery time with `photo-send-log`. Its returned id belongs
to the send receipt. Keep all these writes silent and keep the owner's
existing reply text, link, buttons and cards exactly as before.

For results-link receipts, the photo-link helper preserves these values directly
from the saved MCP response and query arguments. After successful delivery only,
use a fresh receipt SESSION (never reuse a photo memo session):

```sh
python3 "$PROJECT_ROOT/.claude/skills-bundle/photo-link/photo_link.py" --session "$SESSION" results-sent --query-file "$QUERY_FILE" --response-file "$RESPONSE_FILE" --channel telegram --sent-at "$UTC_DELIVERY_TIME" --kit "$PROJECT_ROOT/database/kit_life.sh"
```

Use the actual channel if different; optional `--query-text` preserves the owner's
wording. The helper records only the link's ordered Photos IDs (at most 60),
stores the original structured filter arguments and returned total, and emits
no Telegram actions. It performs no delivery. A repeated session is refused;
reconcile the receipt ID after an uncertain write instead of using a new session.
