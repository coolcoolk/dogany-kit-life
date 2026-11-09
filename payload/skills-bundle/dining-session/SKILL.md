---
name: dining-session
display_name: 식당 찾기
description: >-
  Find or choose a restaurant when the owner wants somewhere to eat for an appointment, party or area, supplies a
  group-chat capture with dining constraints, or answers an active restaurant-search round. Entry depends on dining
  intent and existing people/event/search state, including bare choices, exclusions, corrections and final selections in
  that round. Read registered people and upcoming events before asking for area, time or party. Answer with 3-4 checked
  cards of different kinds, one recommendation first, with real cuisine, per-person price, event-place walk, opening at
  the event time, booking facts and party fit. Runs in the main session (browser). Search only: never books, reserves or
  messages a restaurant.
---

Before answering about food, cooking, timing or the rest of today, run
`"${PROJECT_ROOT:-$(pwd)}/database/kit_life.sh" day-now` and use its local clock,
day type, logged meals, next eating occasion and remaining blocks. Never ask
which meal when day-now answers it; an afternoon food request after logged
lunch targets dinner unless the owner explicitly says otherwise. Never infer
attendees from a title; use only stored people/groups. Historical or explicitly
dated requests keep their stated date and meal; day-now supplies current context.


# dining-session -- find a restaurant, one card per candidate (module_dining v1)

`SK="${PROJECT_ROOT:-$(pwd)}/.claude/skills-bundle/dining-session"`,
`LKIT="${PROJECT_ROOT:-$(pwd)}/database/kit_life.sh"`.
No taste store yet: conditions come from this conversation, the capture and
the kit's people/events. The pick itself is recorded on the appointment (step 5).

## flow

0. context first -- BEFORE asking anything
   - collect from message / capture: person names, group / chat-room words, event words.
   - people: `$LKIT person-show <name>` (ABSENT = not registered).
   - events: `python3 $SK/context_events.py <word> [<word> ...]` with EVERY person name,
     group / chat-room word and event word. always pass the names, even if unsure.
     - it computes local today .. +14 days itself; never call event-window by hand, never narrow.
     - lines: `EVENT<TAB><local start><TAB><title><TAB><place><TAB><people><TAB>via=<word><TAB>id=<event id>`, then `CONTEXT<TAB>events=<n>...`.
     - "오늘 식당" with no names -> pass today's event words too (모임, 약속, the titles you see).
   - one EVENT -> open with one line naming it, take place / time / party from it. do not ask.
     shape: "<요일> <모임명> 얘기죠? <장소> 근처로 찾을게요"
   - several EVENT -> name them, ask which one. events=0 -> only then ask the area.
     a chat-room name is not a location.
   - the meal IS the event: hours are checked for the event start, the search date = EVENT date.

1. conditions -- read literally, per person, in the party's own words
   - "<X> 안 먹는데 <Y>는 또 먹어" = Y tolerated, NOT a favourite: never rank Y first,
     never build the list around Y ("마라는 또 먹어" does not make mala the pick).
   - "너무 <X>한 거 빼면" / "너무 매운 거 빼고" = drop only the strong end (a venue whose
     signature dish is very spicy), not every dish with chili. mild options at a venue pass.
   - "<X> 잘 안 먹음" = not the main of the pick; a venue may still list one such dish.
   - "<cuisine> 밴" = no venue of that kind (재석 "양식 밴" -> no pasta / steak / hamburg place).
   - owner corrections: narrowest reading, keep every other condition, one line saying it
     ("<dish>만 뺐어요"). "국물 빼줘, 나 쌀국수 먹었어" = drop that dish kind only.
   - recent meals: `$LKIT meal-day <YYYY-MM-DD>` for the 2 days BEFORE THE EVENT DATE; avoid them.
   - TPO words for the query: 데이트/분위기, 가족, 모임, 혼밥, 조용한/룸.

2. search -- one query at a time (the browser is a singleton; NEVER parallel)
   `python3 $SK/dining_search.py "<area> <kind> <tpo>"` -> JSON
   [{name, address, search_category, place_id, link}]. Run several kinds in sequence
   ("신논현역 샤브샤브 모임", then "신논현역 한식 모임", ...). `place_id` null = not verified:
   drop it. `search_category` is a filter hint only, never the card's cuisine.

3. facts -- per shortlisted venue, sequentially
   `python3 $SK/venue_facts.py <place_id> --at "<event YYYY-MM-DD HH:MM>" --walk-from "<event place>" > "$TMPDIR/venue_<place_id>.json"`
   (keep the file for the session: steps 3 and 5 read it)
   -> category + kind (from the place page + its representative menu), per_person (from the
   menu, only when the page states a per-person price), hours at the event time (break,
   last order), booking, walk minutes (kit route, place-view/route.sh), menu_flags
   (trait -> menu items). exit 5 = page unreadable -> drop the venue.
   - hours closed / break / past last order -> drop. unknown -> keep only as a non-first card.
   - read menu_flags against the conditions: a flag on the signature dish rules the venue out;
     a flag on one side dish goes into the fit line ("닭 메뉴는 닭볶음탕 하나").
   - food kind of a venue: `$LKIT food-kind <place_id> --facts "$TMPDIR/venue_<place_id>.json" --json`.
     always pass the saved file: the kit never reads a place page itself. exit 3 with
     `"new":"node-add"` -> pick one of its candidates, or
     `$LKIT node-add --parent <node> --label "<words>" --json` when none fits.

4. round -- 3-4 cards, different kinds, best first
   write the round JSON and render it:
   `python3 $SK/dining_card.py <round.json>` (format in the script header).
   - `fit`: why it fits THIS party, in concrete words naming the conditions
     ("맑은 육수 고를 수 있음 · 치킨·양식 아님"). never a vague line ("무난해요").
   - `why` (first card): the one-line reason it is the pick. a tolerated item never makes the pick.
   - REFUSE lines -> fix the round (drop / replace the card) and render again.
   - send the rendered block as is, after the one context line. it already ends on the decision
     (put the pick into the event / see others). never end on "원하시면 말씀해 주세요".

5. answers
   - "별로야" / "다른 데" -> new round of other kinds; never re-send a dropped venue.
   - "2번" / "여기로 할게" -> `$LKIT dine-choose <event id> --facts "$TMPDIR/venue_<place_id>.json"`:
     the venue becomes the event's place, travel is recomputed, a different meeting point stays
     in the event note. relay its card as the confirmation. exit 5 -> ask its one question
     (`--new-confirmed` when the owner says it is a different place). no event -> confirm the
     venue in one line, nothing is written.
   - capture / new condition -> re-check every shown card against it, then a new round.

## bounds
- search only: never book, reserve, pay, or message a restaurant.
- only verified `map.naver.com/p/entry/place/<id>` links; no coordinate links.
- every card field comes from a source (place page, menu, kit route); unknown -> the line is
  dropped (hours: "영업 확인 안 됨", never the first card). never guess a cuisine or a price.
- 0 results -> say so in one line and try a broader query yourself before asking.
- never expose script names, JSON, flags, or tool internals.
