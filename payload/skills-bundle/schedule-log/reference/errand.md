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
supplies its duration/place/notification policy. An unstated end stays unknown;
never invent three hours. `--kind auto` is only for genuinely unresolved
meaning; exit 9 `SCHEDULE_NEEDED kind` tells YOU which typed judgment to pass.
Resolve that from context and retry silently; do not relay kit flags or a
classification refusal to the owner. Ask only a genuinely missing owner fact.
Kind and attendance mode are independent; preserve attendance when supplied.
