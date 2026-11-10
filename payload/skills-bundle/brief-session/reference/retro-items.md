# Daily stage 3: retro items

For walk kind `experience`, read [experiences.md](experiences.md) first.
The lived-day list and missing-field queue own those rows; calendar recap
rules below apply only to the remaining calendar items.

Read before the first stage 3 item.

2. stage 3 `오늘 일정`. source `retro-items <today>` (JSON; refresh before
   each question). One item = one outer event with what happened inside it
   (outing bundle legs, contained events, attached meals, the stay that
   continues the visit): ONE dashboard line, ONE turn, ONE question. Never
   ask a child separately; never re-split an item. Only returned items may
   be asked about; ongoing visits are omitted and left unsettled.
   `RETRO_IN_PROGRESS` on a write means refresh and continue.
   carried=true (a visit that began on an earlier day and ended today):
   never under `오늘 일정 점검할게요.`, never called today's schedule; its
   card opens with its own lead line. Send the notice only before the first
   non-carried item.
   per item, BEFORE building its turn: `routine-for-event <item id>`.
   - verb errors -> one-off: the item's card, no buttons.
   - `retro_skip=1` -> no question, no buttons, no turn; still spoken,
     dashboard child `· 스킵`. checked per item, so a skip set tonight holds.
   - `retro_skip=0` -> the card with
     `[[OPTIONS: 직접 입력 | 이번엔 스킵 | 이 루틴 항상 스킵]]`;
     항상 스킵 -> `routine-retro-skip <routine> on` (reversible `off`; this is
     the only exclusion registry, D8).
   turn = the item's `card` VERBATIM (lead line, title, span, place, the one
   flow-recap question, and at most one module rating row). Do not add a
   second question, do not reword the recap, do not list the children.
   A card that carries a rating row gets no other buttons.
   answer -> `retro-item-set <item id> "<his line>"`, say nothing. It lands
   on the outer event and on every child that has no line of its own.
   rating label tapped -> run the item's `rating.record` with `{label}` =
   the tapped label (a module record, not the retro line), say nothing; the
   typed answer to the question is still the retro line. No `record` ->
   keep the tap as part of the retro line.
   small talk (`talk` set, a visit with people; owner 2026-10-16): no
   buttons, never a quiz.
   - item unanswered: the card's question IS `talk.line`. answer ->
     `retro-item-set <item id> "<his line>"` as always, then
     `visit-talk-reply <item id> "<his words>" --data '<typed capture JSON>' --json`. Say nothing more.
   - item already answered (`retro_line` set): send `talk.line` alone, once,
     as its own turn. answer -> `visit-talk-reply <item id> "<his words>"
     --data '<typed capture JSON>' --json` only.
   Whatever he says back ("응", "응 좋았어", something else) is accepted and
   closes it: no follow-up question, no reaction to what was captured, next
   item. `talk` null -> the ordinary card.
   line holds a follow-up action -> `task-add` (no time, D5), one notice line.
   owner NAMED the project -> `task-add ... --project <that project>`; not
   named -> omit flag, never guess, never ask.

Read the visit reply yourself. `visit-talk-reply` never classifies its words.
Supply `--data` with `verdicts:[{holder,verdict}]` (liked|neutral|disliked),
`traits:[{target:"kit_life:venue:mood:quiet"}]`, and/or
`notes:[{holder:"person:kit_life:ID"}]`. Choose people from this visit's party;
use the exact party group id for a group verdict. Optional `quote` is a verbatim
substring; otherwise the full reply is evidence. Empty data captures nothing.
Never ask for specific words. See reference/verbs.md for this verb.

Before rendering visit small talk, use `visit-talk-context ITEM --data JSON --json`
for facts the owner already stated: `joined:[PERSON_ID]`,
`event:{destination_kind:"home"|"person_home"|"venue",first_round:1,who:NAME,where:NAME}`
(`who` only for person_home, `where` only for venue), and optionally
`life_facts:[{person_id:ID,fact_id:ID}]`. These are typed semantic facts from
reading the owner's account. The kit never scans retro-line or note words to
pick a joined person, a later-round destination, or a life fact.
