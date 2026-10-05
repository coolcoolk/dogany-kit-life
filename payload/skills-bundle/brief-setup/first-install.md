# brief-setup -- first install: offer, try one group, start it

kit_life 2.37.0. The owner-approved onboarding (rehearsal 2026-10-01
06:33-08:05, FINAL flow A-E). Replaces the 13-item walkthrough and the
time QUESTION. Every owner-facing line below is an i18n key in the kit
bundle (`config/i18n/<lang>.bundle.json`, byte-identical copy in
`service/kit_life/i18n/<lang>.json`): speak it VERBATIM, never paraphrase.
`[a | b]` = buttons, labels from the named keys.

Split: the framework (`dogany-lifekit-setup`, DGN-1809) keeps LIFEKIT
state, symlinks, `routine-ctl`, the brief-slot swap and the Google connect
procedure; the kit owns the copy, the group -> rows map and every step
after the pick.

- `$LKIT` = `database/kit_life.sh`
- `$BT` = `bash .claude/skills-bundle/brief-setup/briefing_times.sh --root "$PWD"`

## A. offer (both groups on; brief/retro NOT on yet)

1. install: the framework switches on EVERY kit skill row (bundle.conf
   `kind=skill`), `LIFEKIT=on`. No routine row yet: brief/retro start only
   with 캘린더 관리 (a brief that arrives before any schedule is set up reads as wrong).
2. say `life_setup.offer`, then `[life_setup.group.calendar |
   life_setup.group.health]`. The pick is what to TRY FIRST, not what to
   enable. Groups: `service/kit_life/offer.tsv` (calendar =
   module_schedule/place/routine/person/brief, health =
   module_meal/workout/health).
3. pick `calendar` -> B. pick `health` -> C. "later" / nothing -> stop;
   everything stays on, no routine is switched on.

## B. 캘린더 관리

- **B1 connect screen** -- say `life_setup.calendar.connect`, then
  `[life_setup.calendar.connect.now | life_setup.calendar.connect.skip]`.
  - now -> the framework's existing Google connect procedure
    (`dogany-lifekit-setup` "Connect Google", unchanged in 2.37.0; easy
    login is ticket 2026-10-01-calendar-connect-easy-google), then B2.
  - skip -> say `life_setup.calendar.skip_lead`, then B2, then (same
    message, last) `life_setup.calendar.connect_later`. Later, "캘린더
    연결" starts the same framework procedure.
- **B2 first input** -- say `life_setup.calendar.first_input` (question +
  예) lines: the format to type, not buttons). No time information here.
- **B3 result** -- write what the owner said with the usual verbs
  (`$LKIT appt-add` / task / `routine add`). Relay each write's CARD block
  as its CARD_RELAY says, grouped under one header line per kind that
  appears: `life_setup.result.schedule` (📅 일정), `life_setup.result.task`
  (✅ 할 일), `life_setup.result.routine` (🔁 루틴). A place-less
  appointment prints PLACE_ASK (existing): ask it; "미정" ->
  `$LKIT appt-upd <id> location=미정`, never asked again.
- **B4 brief/retro ON + told time** -- the framework switches on
  `morning-brief` and `daily-retro` at the bundle.conf time (routine-ctl +
  brief-slot swap; units must be loaded before the next call). Then:

  ```bash
  $BT --slots
  ```

  speak the `ASK` rows joined by line breaks, as one message
  (`life_setup.tell.both`, spoken times: "아침 6시, 밤 10시에 보내
  드릴게요...") with ONE button, the `BUTTON` row
  (`life_setup.keep_button`, [이 시간 그대로]).
  - button / "그대로" -> `$BT` (no time flag) -> `life_setup.done.both`
    ("아침 6시 브리핑, 밤 10시 일간회고로 맞춰 뒀어요.").
  - another time -> `$BT --morning HH:MM [--retro HH:MM]` -> the same
    confirmation with the new times. Exit codes as SKILL.md step 3 (4 =
    not loaded, never "moved"; Linux = saved-not-moved).
- **B5 origin** -- an appointment with a place whose origin the kit cannot
  know prints `ORIGIN_ASK` at appt-add / appt-upd (kit mechanism, see
  ORIGIN_RELAY). In onboarding, hold it until after the B4 confirmation,
  then ask it verbatim (`travel.origin.ask`: "<요일> <제목>, 출발하는 곳과
  주소를 대략 알려 주세요..."). Answer ->
  `$LKIT appt-origin <id> "<place>" address="<rough address>"` (home ->
  `$LKIT appt-origin <id> home [address="<rough address>"]`; a rough
  address given for home teaches the bare seeded base). It prints `ORIGIN_LINE`s
  (`travel.origin.remembered`, `travel.origin.depart_alert` once the leg
  is derived from a real duration), `TRAVEL_PLACE_NEEDED` when the new
  origin -> venue minutes are unknown (resolve it the usual way, then
  `appt-travel-attach`), and the card.
- **B6 work presence (optional extra)** -- the first non-home origin
  also prints `PRESENCE_OFFER` (`travel.presence.offer`, 예) 월-금 9시-6시)
  with one button `PRESENCE_BUTTON` (`travel.presence.later`, [나중에
  할게요]). Days + hours -> `$LKIT routine add "<place>" "<days>"
  kind=presence place=<place> time=HH:MM duration=<min>`; the button ->
  `travel.presence.later_ack`. Once only (config
  `onboard_presence_offer_done`).
- **B7 closing** -- `$BT --closing --mention calendar`:
  `life_setup.closing` + `life_setup.closing.offer_health` (the one mention
  of 식사·운동·인바디).

## C. 식사·운동·인바디

- **C1 prompt** -- say `life_setup.health.prompt` (ends "오늘 먹은 것부터
  하나 보내 보시겠어요?"). No routine is switched on.
- **C2 meal record** -- meal-log as usual (`$LKIT meal-add ...`). Its
  output now ends with the kit meal card (`MEAL_CARD_BEGIN..END` +
  MEAL_CARD_RELAY): one short line with the ESTIMATE reason, then the card
  verbatim in one code block. Suppressed when the health pack's
  diet-status card.py is installed (that card owns the turn).
- **C3 simple target, once** -- the first meal-add also prints
  `TARGET_OFFER` (`meal.target.offer`) + two `TARGET_BUTTON`s
  (`meal.target.offer.set` | `meal.target.offer.skip`). 목표 정하기 ->
  `$LKIT meal-target-ask` (`meal.target.ask`: 키/몸무게/나이/성별/방향,
  inbody photo alternative) -> `$LKIT meal-target-set height= weight= age=
  sex=male|female direction=lose|keep|gain` -> `meal.target.done` + the
  card with bars. 기록만 할게요 -> C4. Never offered again (config
  `meal_target_offered`), never when a target exists or the pack card is
  installed.
- **C4 closing** -- `$BT --closing --mention health`: `life_setup.closing`
  + `life_setup.closing.offer_calendar`. No brief/retro talk.

## later

- "캘린더 관리 해볼래" -> B from B1 (this is when brief/retro go on).
- "식단 기록 해볼래" -> C1.
- change-time requests -> SKILL.md entry B.
- Never re-run the whole flow unprompted; never offer the goal interview.

## framework lines that must change (not edited here)

Against dogany-agent `dev` @ 0b447718:

- `skills/dogany-lifekit-setup/SKILL.md` activate procedure (`:51-88`):
  install = every kit SKILL row on + `LIFEKIT=on`, NO routine row; then
  hand off to this file (A). Routine rows (morning-brief, daily-retro,
  day-card) go on only at B4 (calendar group start), at the bundle.conf
  time. No per-item walk (`:9-11`), no "different time" option (`:57-58`),
  no one-screen summary (`:87`), no change-time triggers (`:7-9`,
  `:30-32` -> brief-setup).
- `skills/dogany-lifekit-setup/SKILL.md:27` and
  `agents/.template/routines/onboarding-check.py:866-869`: offer wording
  = kit key `life_setup.offer` + this file, not `lifekit.offer`.
- `agents/.template/config/i18n/{ko,en}.json:8` `lifekit.offer`: retire.
- `skills/dogany-lifekit-setup/SKILL.md` "Connect Google" step 8: also run
  the outbound backlog once after enabling the mirror
  (`python3 mirror/reconcile.py --repair --drain`), otherwise items created
  before connecting only reach Google at the weekly reconcile -- and only
  if that cron runs with `--repair`. B1 promises "나중에 연결하면 그동안
  넣은 것도 같이 올라가요". (Verified: reconcile --repair enqueues every
  in-scope kit row missing on the surface; the enqueue at write time is a
  no-op while the mirror is off.)
- `agents/.template/routines/card-followup.py:95-104` ORDER: after every
  meal-add / workout-add it orders `.claude/skills/diet-log/card.py`
  (a path no current install has) unconditionally, with a "report the
  numbers as text" fallback. It should order the diet-status card only when
  `.claude/skills{,-bundle}/diet-status/card.py` exists, and stay silent
  otherwise: the kit card is already in the meal-add output, and a second,
  text-only "card" competes with it.
- bridge (DGN-1813): single-button messages ([이 시간 그대로], [나중에
  할게요]) without "1." numbering, buttons on the body message.
- `routines/set-briefing-times.sh:133-137` `is_loaded` probes launchctl
  only: Linux timers never move (DGN-1810; the kit says saved-not-moved).
