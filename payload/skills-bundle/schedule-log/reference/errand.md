# schedule-log -- errands (verbatim detail moved from SKILL.md)

Read when adding a place task with a closing-time deadline.

## kind gate (section 1)

Read the owner's meaning and choose the write verb: a pickup, return, issue
or drop-off with no booked visit is `task-add ... --place`; an actual booked
appointment is `schedule-add ... --kind appointment`. Title words never veto or change the chosen verb.
For an explicit read-only classification, supply the semantic enum yourself:
`errand-classify "<title>" --verb-class pickup|return|issue|drop_off --place <p>
[--at HH:MM] [--booked]`. With no verb class it does not infer one from words.
Ask a missing booking fact once only when the meaning remains unclear.

## task add (section 5)

an errand: `task-add <title> <date> --place <place>`, no --at
(no visit time is picked; PLACE_HOURS_DEFAULT is not printed for
it). ERRAND line: reply names the deadline only ("18:30 마감 전에
다녀오시면 돼요"); the latest departure and the time to spare
only when the line ends in tight / late. never mention a buffer.

The agent judges kind from the conversation before writing. Pass
`--kind appointment` for meeting people, a booking, or a fixed external slot;
pass `--kind task` for a self activity. Use `schedule-add <title> <start>
[end location purpose summary] --kind <judgment>`. A matching task routine
supplies its duration/place/notification policy. For a timed appointment with
no stated end or sourced runtime, leave the end argument empty so the kit
uses start +3h (owner 2026-10-01). For a task/self activity with no known
duration, keep the end unknown. `--kind auto` is only for genuinely unresolved
meaning; exit 9 `SCHEDULE_NEEDED kind` tells YOU which typed judgment to pass.
Resolve that from context and retry silently; do not relay kit flags or a
classification refusal to the owner. Ask only a genuinely missing owner fact.
Kind and attendance mode are independent; preserve attendance when supplied.

## commute stop (퇴근길 / 출근길 + place)

"퇴근길에 <place> 들러서 <X>" = 회사 -> place -> 집; owner: shopping stop ~15 min.
1. `place-resolve <place>` (ABSENT -> travel-leg.md lookup chain, then place-manage).
2. office end = that day's 회사 presence end (`event-day <day>`). No presence -> ask nothing
   new: plain errand (no --at).
3. travel 회사->place minutes = the saved edge (travel-leg.md lookup if missing).
4. `task-add <X> <day> --place <place> --at <office end + travel> --dur 15`.
   The kit derives the legs 회사->place and place->집 (replaces the direct leg).
   Live 2026-11-11: 18:00 end, 30 min -> 18:30-18:45, direct 회사->집 leg dropped.
5. 출근길 = mirror: 집 -> place -> 회사, stop ends so arrival = office start
   (--at = office start - travel - 15, --dur 15).
Reply = the card verbatim. Owner gave only the word, never a clock: the clock is derived.
