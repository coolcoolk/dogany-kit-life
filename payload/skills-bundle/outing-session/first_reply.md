# First reply workflow

Use outing-session/SKILL.md for context and calendar rules. Research silently:
call tools without any assistant text before or between them. Do not narrate
status, schema repairs, candidate lists or partial itineraries. Every text
block reaches the owner as its own message. The final reply is the complete
rendered day, with at most ONE short lead line in the owner's language.
English belongs only inside tool code/commands for a Korean owner.

1. Read the day's calendar and the existing base place/address. Keep the
   owner's departure, transport, named stops and meal request. Read the latest
   related outing and avoid its already-visited stops; keep the new requested
   date. A day course
   has 3-5 named stops, including lunch, an activity, a cafe/walk, and dinner
   when the course extends into the dinner window. End at home with a time.
2. Find candidates with WebSearch, then READ each venue's official website,
   booking page or Naver place page. A search snippet, blog or aggregator
   is discovery only. Read the actual page with WebFetch or agent-browser.
   If a Python fetch fails in a rehearsal, the read-only browser remains
   available; do not stop at that failure. Browser calls are sequential.
   Capture date-applicable hours, closures, breaks, last order, booking policy
   and prices/entry fees where relevant. Missing facts are unknown, never
   invented. Swap unusable candidates and finish checking the replacements.
   For Naver discovery use the keyless browser path (no dining API key needed):

   python3 .claude/skills-bundle/outing-session/outing_venue.py --search "AREA FOOD/ACTIVITY"
   python3 .claude/skills-bundle/outing-session/outing_venue.py PLACE_ID --at "YYYY-MM-DD HH:MM"

   The second command reads the official page, returns its raw hours and text,
   and reuses the kit dining parser for the stated date. Save its JSON in
   files/tmp for each stop. Use official_page as the source URL. Read the
   actual returned fields. structured_page_facts_read=true means the helper
   opened THIS venue page THIS turn, verified its numeric identity, and read
   the page's embedded official data. Its hours, official_hours, menu and
   price_quotes are direct page evidence, even if official_text shows an
   access-limit overlay. Use those verified fields; do not mislabel them as
   search snippets or discard them because the visible body is restricted.
   An error or missing state is different: no facts were established.
   A restricted booking/ticket body cannot establish its package terms;
   choose a stop whose terms can be verified. A venue-body overlay says
   nothing about the route service: ALWAYS try outing_routes.py independently.
   Do not stop at a candidate list or promise to finish when a limit clears.
   Unknown booking is not a claim that booking is
   unnecessary. Do not spend the turn debugging iframe clicks or repeating
   an API search that already reported missing keys. Use the page facts to
   finish the plan, not a separate candidate-list reply.
   Read linked booking pages too: a booking URL on the home page is only a
   discovered link, not evidence of its contents. Use the helper's --url mode
   to READ official/booking/ticket pages (no clicks that reserve anything):

   python3 .claude/skills-bundle/outing-session/outing_venue.py --url "https://..."

   For Naver menus/info use PLACE_ID --at ... --page menu or --page information.
   Every cited fact URL must be opened successfully THIS turn, including a
   separate booking URL. Keep each page receipt. Search snippets do not count.
   If a paid attraction's fee/ticket rules cannot be read, replace the stop.
   A booking badge means only that booking is offered, not required or available.
   Read the package duration and slot rules before choosing a stay. A four-hour
   package needs four hours; do not label it a 90-minute lunch. Use its real
   slots in windows/fixed_at and reservation.required/status. Unknown required
   availability means swap the stop, not a suggestion to book it later.

   Price facts MUST be a separate exact page excerpt with item and listed unit,
   such as "2\uc778 \uc138\ud2b8 33,000\uc6d0". Preserve spelling, amount and
   unit, including people, set, weight, per-item and rental duration. NEVER
   divide a set price, multiply by party size, convert units or use a derived
   per_person amount. The helper suppresses derived per-person prices. Select each price by its price_quotes index in the saved receipt;
   if absent read the menu page. Never type a price into a fact. Each selected
   entry becomes its own line, with its original item, amount and unit.
   Do not append an unsourced budget, total or per-person calculation.

3. Write files/tmp/outing-plan.json using the outing_course.py schema. Set
   whole_day=true, map_provider=naver and party as a positive integer headcount
   (default 2, never a name or form of address). Origin is {id,name,address}; stops have id,name,address,
   kind,stay_min,hours,booking,link,src,facts. Every fact MUST reference the saved
   page JSON with receipt, relative to the plan file (for example lunch.json).
   Non-price fact: {"receipt":"lunch.json","text":"<collected hours or policy>"}.
   Price fact: {"receipt":"lunch-menu.json","price_index":0}. Add another fact
   with price_index=1 for the next selected menu entry. Read price_quotes to
   select indices. Do not supply text/source/date on price facts. Never join
   two menu entries, recalculate an amount, or paraphrase a price label/unit.
   The renderer loads the selected entry and its official_page URL itself.
   For every other fact it also supplies the URL from that fact's receipt.
   Keep different pages in different receipts, even at the same stop. For meals add windows
   covering the FULL VISIT. The exact window schema is
   windows:[{"start":"11:30","end":"15:30"}] for lunch, or
   windows:[{"start":"17:30","end":"21:00"}] for dinner, with
   meal=lunch|dinner. Window keys are start/end, never open/close. Aim to arrive by 14:00 for lunch
   and 20:00 for dinner. Allow 75-90 minutes for barbecue and about an hour
   for dinner when the opened page has no longer package duration; never squeeze a barbecue meal into 30-35 minutes to make a
   window fit. Choose a nearer lunch anchor if the drive makes it too late.
   Include
   reservation.required/status if booking is mandatory; unverified availability
   cannot be called available. Choose a geographic order that fits the windows.
   All facts sent to the owner must appear in facts, with their opened source.
   Keep operational claims out of the context line. For each cited page check
   hours, last order, price unit, required booking and full-visit duration
   against the saved receipt before measuring. A URL alone proves no fact.
4. Collect measured legs in ONE foreground call:

   python3 .claude/skills-bundle/outing-session/outing_routes.py \
     files/tmp/outing-plan.json files/tmp/outing-measured.json

   This calls the kit route.sh once per directed leg, sequentially, including
   home -> first and last -> home. It saves the raw map receipts, map URLs and
   parsed recommended minutes. It never invents a reverse edge or travel time.
   One complete measured order is sufficient; the optimizer reports its scope.
   Do not spend the turn measuring every permutation before completing a day.
   If a leg fails, use its diagnostic to repair the address/environment and
   retry; do not pass errors, tool names or incomplete candidates to the owner.
   If a lookup selects the wrong place, put its verified name AND address in
   the plan's address field and remeasure. Keep that same address in the
   measured output. Never restore a different address after measurement,
   transplant legs between plans, or edit route_receipts to hide a mismatch.
5. Render the measured plan:

   python3 .claude/skills-bundle/outing-session/outing_course.py \
     files/tmp/outing-measured.json --db database/kit_life.db --format reply

   Fix any refusal and render again. Missing legs, fewer than three stops,
   missing lunch/dinner, source gaps or invalid hours do not finish a day.
   The successful renderer owns the complete course reply, including every
   fact, price entry and citation. Relay its ENTIRE stdout VERBATIM as the
   final course; do not compose the course from your research notes.
   Keep its date/mode/headcount/route-reason context line, even if another
   line already says the same thing. Never delete that line to avoid a
   repetition. Keep every header, stop label, fact, source link, measured
   leg, home time and closing decision exactly as printed. Do not retype
   a cleaner version, substitute labels, add buttons, or use a code fence.
   If needed, put ONE brief calendar/unresolved-fact line above the stdout.
   Do not add a separate course introduction repeating date, departure or
   party: those are already in the renderer. The renderer should be the last
   tool call before the final reply. End on its decision, with nothing after it.
   Do not ask whether to continue research, ask the owner to check facts, or
   propose calendar registration before the complete checked course exists.

All actions above are read-only on the kit and external sites. Author only
scratch plans/receipts. Calendar writes require the owner's later instruction.
