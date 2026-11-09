---
name: outing-session
display_name: 나들이 코스
description: >-
  Plan a day outing, date course or drive when the owner wants an itinerary or destination ideas, supplies
  departure/party/stops/constraints, or edits or selects an active course. Resolve new versus continuing course from the
  conversation and kit's people/calendar state; bare numbered picks or changed stops/times continue the current plan.
  Check people and the day's calendar first. The FIRST answer is a timed itinerary: named stops in order, each checked
  on its official or booking page for hours, breaks, last order and booking; time every leg with the kit route lookup
  and name at most one unresolved fact. Runs in the main session (browser and web). Search only: never books, reserves
  or messages a place.
---

Before answering about food, cooking, timing or the rest of today, run
`"${PROJECT_ROOT:-$(pwd)}/database/kit_life.sh" day-now` and use its local clock,
day type, logged meals, next eating occasion and remaining blocks. Never ask
which meal when day-now answers it; an afternoon food request after logged
lunch targets dinner unless the owner explicitly says otherwise. Never infer
attendees from a title; use only stored people/groups. Historical or explicitly
dated requests keep their stated date and meal; day-now supplies current context.


# outing-session -- one timed course, checked, first reply (module_outing)

`SK="${PROJECT_ROOT:-$(pwd)}/.claude/skills-bundle/outing-session"`,
`DS="${PROJECT_ROOT:-$(pwd)}/.claude/skills-bundle/dining-session"`,
`LKIT="${PROJECT_ROOT:-$(pwd)}/database/kit_life.sh"`,
`ROUTE="${PROJECT_ROOT:-$(pwd)}/.claude/skills-bundle/place-view/route.sh"`.

Goal: first reply = itinerary the owner can leave with. Not categories, not a
list of ideas, not "please check hours yourself". Do the checking.

## flow

0. context -- before asking anything
   - read the ask: area, date, depart time, means (car / transit), party, fixed
     stops ("<place>에서 점심"), mood (데이트, 가족, 조용한), budget words.
   - people: `$LKIT person-show <name>` for each named person (ABSENT = not registered).
   - date unstated ("주말에") -> next Saturday; also look at Sunday. pick the free one.
   - calendar: `$LKIT event-day <YYYY-MM-DD>` for each candidate day. every row with a
     time = busy block (the day card, travel and prep legs included). the course
     never overlaps one; if the day is full say so in one line and offer the other day.
     `python3 $DS/context_events.py <names/words>` finds a related event; one hit ->
     take date/party from it, do not ask.
   - recent outings / repeats: do not repeat a place the owner named as visited.
   - ask ONE question only when the area or the day cannot be derived. never a menu of
     questions. missing party / budget / means -> assume the common reading, say it in
     the one context line ("차로, 두 분 기준으로 짰어요").
   - origin = home (empty origin in route.sh) unless the owner said another.

1. anchors first
   - owner named a stop -> it is fixed. find its official page / booking page / Naver place:
     `python3 $DS/dining_search.py "<name> <area>"` (place_id null = not verified, try the
     web search for the official site), then
     `python3 $DS/venue_facts.py <place_id> --at "<date HH:MM>" > "$TMPDIR/venue_<place_id>.json"`
     for a restaurant / cafe. for anything else read the official page with web fetch.
   - facts needed per stop: open hours that day, regular closed day, break, last order
     (meal), booking (needed? how? full?), entry fee / ticket if any, stay length.
     each fact has a source page; no source = not a fact.
   - hours closed / break / past last order at the planned arrival -> move the stop's time,
     or swap the stop. never keep it and warn.

2. fill the day around anchors -- named places only
   - 3-5 stops: meal anchor(s), one main activity, one cafe / walk. pick by area + mood.
   - web search the area ("<area> 당일 코스", "<area> 데이트", the activity words); take
     candidates that have an official page or Naver place. one blog line is not a source.
   - check each candidate the same way as step 1. drop the ones that fail; do not list them.
   - order by geography, then by hours (meal in its window, closing-early places first).
   - lunch window 11:30-14:00, dinner 17:30-20:00, cafe after a meal, outdoor before dusk.

3. legs -- every one timed by the kit
   - `bash $ROUTE "<from>" "<to>" car` (or `transit`) per leg, in sequence (the browser is a
     singleton, NEVER parallel). measure every directed pair the order could use: home -> each
     stop, stop -> stop both ways, each stop -> home (max 5 stops). the engine picks the order.
   - use the road address when a name is ambiguous; a leg whose end point is not the stop
     (other city, other branch) is wrong -- re-run with the address, never keep it.
   - take the recommended line's minutes. means = the owner's words, else the recorded
     `travel_mode`, else car for a day trip. live traffic is as of now: say so only if the
     owner leaves in the next hour, otherwise it is a normal-day figure.
   - exit 3 -> retry once with a clearer name (add 동/구, the road address). still failing ->
     that leg is the one unresolved fact (below). never estimate minutes by hand.
   - route.sh is read-only. do not run travel-resolve.sh (it writes place edges).

4. gate -- write the plan, run it
   `python3 $SK/outing_plan.py <plan.json>` (format in the script header)
   - `leg_min` from step 3, `hours` from step 1-2, `busy` from step 0, `fixed_at` for owner
     times, `link` + `src` per stop.
   - prints STOP rows with computed depart / arrive / leave, END = home time.
   - REFUSE lines -> fix the course (move a time, swap a stop, shorten a stay), run again.
     never send a course that REFUSEs.
   - UNRESOLVED lines = the facts you could not source. at most ONE may stay.
   - required booking (table, ticket, entry, PARKING / vehicle slot) = a fact. full or not
     open for the date -> swap the stop or the date; never "예약하세요" as the plan.
   then order + render: `python3 $SK/outing_course.py <plan.json> --db "$LDB" --format reply`
   (`LDB="${PROJECT_ROOT:-$(pwd)}/database/kit_life.db"`, opened read-only; plan = stops with
   id, address, facts {text, source, date}, windows, reservation; legs = the measured matrix;
   origin = home). it tries every order, keeps the shortest measured one that fits the
   windows, and adds the owner's map-app link per stop and leg. exit != 0 -> stdout empty:
   fix the plan (missing leg, closed window) and run again. never relay OUTING_BLOCKED,
   stderr or any tool error to the owner.

5. reply -- one message, ends on the decision
   - the course block = outing_course.py --format reply stdout, verbatim (order reason,
     stops, legs, sources, map links, back home). do not retype or reorder it.
   - above it one context line (day, means, party, any assumption, calendar fact).
   - one unresolved fact at most, named plainly with what was tried
     ("<place> 휴무일은 공식 페이지에 안 나와 있어요 (안내 페이지 두 곳 확인)").
   - end on a choice the owner can answer in one word: "이 코스로 일정에 넣을까요, 아니면 <!-- estate-scrub:allow(product-authored example choice, not an owner quote) -->
     <stop>만 바꿔 볼까요?" never "확인해 보세요", "알아보세요", "참고하세요".
   - bold only the stop names if at all; no tables; no tool names, JSON, flags.

6. answers
   - "2번 빼고" / "카페 말고 산책" / "한 시간 늦게" -> change only that, redo steps 1-4 for
     the touched stops and the legs next to them, send the new full timeline.
   - "이걸로 해줘" -> owner asked to put it in the calendar: `python3 $SK/outing_course.py
     <plan.json> --db "$LDB" --format calendar` -> one `$LKIT schedule-add "<title>" <start> <end>
     "<place>" --kind <appointment|task>` per row (one row per stop, its own times and place; never one whole-day row
     bound to one venue). Judge appointment for people / booking / fixed external
     slot; task for self activity. Pass --attendance-mode online|in_person|unknown
     and any --meeting-url in the same call.
     Relay the existing cards. Nothing booked. No ask -> write nothing.
   - new place the owner names -> step 1 for it.

## bounds
- search only: never book, reserve, pay, call, or message a place or person.
- every hour, price, booking and closed-day line comes from the place's official page,
  booking page or Naver place page. unknown -> that line says unknown (counts toward the one
  allowed unresolved fact) or the stop is swapped.
- every minute between stops comes from route.sh. no guessed drive times.
- no category-only stops ("맛집", "카페 한 곳"). a stop is a named place with an address.
- date of a fact matters: hours are for the course date. holiday / season closures checked.
- 0 usable candidates in the area -> widen the area yourself once, then say so in one line.
- never hand the check to the owner. the first answer is the checked answer.
