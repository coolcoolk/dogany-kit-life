# Experience details inside the existing retro

Owner decision 2026-10-24: capture stores first, evening retro fills vague
parts in conversation. This rule takes precedence over the older blanket
weekly rule against field questions for this block only. The course question uses the owner-selected copy. No new menu, no immediate capture interview.

Read `experience-retro-list <retro_date>` (JSON). `block` is the complete
list, including unclocked reports, child visits and complete experiences.
Relay that list once. A dateless report is included by its capture-context day (date_basis=report_context),
without filling occurred_on. Unknown time is explicitly unknown; its capture order
is a fallback, never an assertion of occurrence time. Never omit a report
because it has no clock, calendar block, place or rating. A correction such
as "already went to the bakery" remains in the list.

Daily: the composer supplies walk kind `experience`, id = the outer
experience id. These ids are event containers, NOT calendar block ids.
Read this list again before every question. Ask only the current root's
`next_question.text`, one turn at a time. It carries root_id, event_id and
field; enrich that event_id, including when the question targets a child.
A complete experience is listed only, unless its outer course has a pending per-stop review. Never run routine-for-event or
retro-item-set using an experience id. The composer removes linked calendar
recaps, so do not ask them again. Once no pending details remain, advance
that root as `recorded`; explicit owner skip may advance as `skipped`.
Do not mark recorded/done just because one answer landed: refresh first.

Answers: `experience-enrich <next_question.event_id> field=value ...
verbatim="<exact owner words>"`. Parse only what the owner actually states:

- time: occurred_at=ISO_WITH_OFFSET (same occurrence date). A date correction
  also gives occurred_on=DATE. Never guess an exact hour from a vague reply.
  If the owner cannot recall, time=unknown preserves that fact without
  repeating the question. An approximate answer can be kept verbatim and
  resolved in conversation; never silently turn "afternoon" into 14:00.
- place: resolve with place-find/place-resolve, then place_id=ID. Do not mint
  a place merely to fill a hole. place=unknown accepts an explicit unknown.
- people: resolve existing people, then person_id=ID (one call per person);
  people=alone or people=unknown are explicit answers too.
- amount: amount=INTEGER, including zero. With multiple linked spends,
  supply spend_id=ID for the one the owner meant. amount=none or
  amount=unknown closes an explicit no-payment/unknown answer. The kit uses
  the ledger writer and updates its shadow in the same transaction.

Verbatim words are preserved by the existing capture helper; retries do not
create another experience. New actual detail evidence is a settled record,
never a new scheduled task. Calendar plans are not rescheduled by an answer.
Do not write an outer answer onto child facts unless the owner states those
facts apply to that child; target child ids explicitly. Re-read after writes.

Sunday: this block is `workspace.experiences` and a single `experiences`
step inside the weekly marker. Read the live list before relaying that
step. Claim a pending course question as described below; its text already
occurs once inside the block, so do not append it again. Otherwise ask the
first missing detail. Refresh the same date and exhaust the root queues before the
next weekly step. Do not start a second daily retro. If the block has no
questions, relay it and continue, without asking a generic reflection.
Retro slot off means no retro output; capture remains available. A completed course can still receive its single separate review question from the existing reminder heartbeat.

Course review is part of this same conversation. A course is an outer
experience with at least two nested stop experiences. The kit supplies
`course_review` and a `next_question` with `field=reaction`, outer event_id,
and the exact question plus unreviewed stop names. Relay that copy exactly;
do not add a child menu or ask separately about each stop. Stops with an
existing reaction are omitted. A course that is still running is deferred
until its last stop ends. Sunday uses the weekly retro.

Daily: the question is already printed inside the card's experience list.
Saving the daily walk claims it once. Do not repeat that question during the
walk; accept the reply with `experience-enrich <outer_id>
reaction="<exact whole reply>" verbatim="<exact whole reply>"`, then refresh
for any remaining missing details. If the owner sends a course review while
another detail is current, use the course's outer id for that reaction.

Weekly or a typed conversation without a saved daily card: immediately
before relaying a live pending course question, run
`experience-review-ask <retro_date> <outer_id>`. Relay the returned `text`
only if that claim succeeds. A repeated/stale claim is refused: refresh the
live queue and continue without repeating the old workspace question. The
workspace's snapshot does not override the live question state.

The same enrichment command handles replies to a standalone course question;
use the `[course-review]` prompt-hook binding to the offered outer id and
its original day, including a reply after midnight. Without that binding,
resolve the exact course and stop list before writing; do not guess a new day. Never call retro-item-set with these ids. Unique explicit stop subjects
(including short venue names) route verbatim fragments to the corresponding
stop reactions. Ambiguous or unmatched text stays verbatim on the course.
Do not guess an unmatched venue or paraphrase opinions. Unmentioned stops
stay untouched, without another opinion question. Explicit opinions stated
before retro should be enriched on those stop ids when captured, so they are
not requested again. No taste table is written; framework 2.7 integration
is the named `course_review.taste_map_write` TODO hook.

### Correct or undo an answer

`experience-enrich ID reaction=WORDS note=WORDS` replaces the legacy subjective
fields; empty `reaction=` / `note=` clears them. `experience-enrich-undo ID`
reverses the latest enrichment, including dated/detail answers and their linked
record or money shadow, and refuses changed owned fields or later links. For
structured course replies, undo the last enrichment before supplying the
corrected reply so that stop routing does not retain the mistaken answer.
Person correction/removal uses `experience-person-upd EVENT OLD_ID NEW_ID` /
`experience-detach-person EVENT PERSON_ID`, reversible with
`experience-person-undo EVENT`. Read episode-session/reference/verbs.md for
parent, merge/split, insight and artifact corrections.
