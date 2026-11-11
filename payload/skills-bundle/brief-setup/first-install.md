# brief-setup -- first install: offer, try one group, start it

kit_life 2.37.0. The owner-approved onboarding (rehearsal 2026-10-01
06:33-08:05, FINAL flow A-E; three-group offer approved 2026-10-18
21:25). Replaces the 13-item walkthrough and the
time QUESTION. Every owner-facing line below is an i18n key in the kit
bundle (`config/i18n/<lang>.bundle.json`, byte-identical copy in
`service/kit_life/i18n/<lang>.json`): speak it VERBATIM, never paraphrase.
`[a | b]` = buttons, labels from the named keys.

Split (framework v2.7 AC11 hand-over): the framework skill
(`dogany-lifekit-setup`) is kit on/off + hand-over only -- LIFEKIT state,
symlinks, and the Google connect procedure this flow calls back to. This
flow owns everything after the hand-over: the copy, the group pick and the
group -> rows map, routine times, and routine on/off, through the
framework's ONE routine verb:

    bundle-routine-ctl.sh on|off <id> [time]

(`<id>` = a bundle.conf routine row; `[time]` = its schedule column or the
time the owner chose; the helper also swaps the generic brief per the
row's slot column). Never the bare `routine-ctl`.

- `$LKIT` = `database/kit_life.sh`
- `$BT` = `bash .claude/skills-bundle/brief-setup/briefing_times.sh --root "$PWD"`

## A. offer (all skills on; three start groups; brief/retro NOT on yet)

1. install: the framework switches on EVERY kit skill row (bundle.conf
   `kind=skill`), `LIFEKIT=on`. No routine row yet: brief/retro start only
   with 캘린더 관리 (a brief that arrives before any schedule is set up reads as wrong).
2. say `life_setup.offer`, then `[life_setup.group.calendar |
   life_setup.group.health | life_setup.group.ledger]`. The pick is what
   to TRY FIRST, not what to enable. Groups: `service/kit_life/offer.tsv` (calendar =
   module_schedule/place/routine/person/brief/episode, health =
   module_meal/workout/health, ledger = module_ledger). Experiences ride
   appointments in the calendar group. The extras line mentions shopping
   help and restaurant recommendations; neither is a start group.
3. pick `calendar` -> B. pick `health` -> C. pick `ledger` -> D.
   "later" / nothing -> say
   `$BT --closing` (the one usage-hint line) and stop; recording is on
   either way, no routine and no module management is switched on. A pick
   starts that group's management: `$LKIT manage-set <module> ... on` for
   the group's modules (offer.tsv) before B / C / D.

Calendar, health, and the "later" path end on a usage hint. Their last line is
`life_setup.closing` or the closing's one cross-mention
(`life_setup.closing.offer_<group>`, itself "say ... any time"), never a
question and never the goal interview. Ledger hands over to its existing
service conversation (D), whose questions and final `ready` line are owned
by that service; do not append a brief-setup closing or cross-mention.

## B. 캘린더 관리

- **B1 connect screen** -- machine preflight: `python3 mirror/calendar_control.py preflight`
  reads the framework calendar status; gws and cryptography apply only to
  `transport:gws-cli`. Then say `life_setup.calendar.connect`, then
  `[life_setup.calendar.connect.now | life_setup.calendar.connect.skip]`.
  - now -> `python3 mirror/calendar_connect.py start --origin "${PWD##*/}:<chat>"`.
    Relay its URL and `copy` array verbatim: locked C1 + C2 + C4, no added
    explanation or controls. Do not handle callback URLs; the bridge consumes
    those before model routing. Stop here until the trusted
    `[google-connect-result]` completion relay arrives for this origin.
    Run `python3 mirror/calendar_connect.py complete --origin "${PWD##*/}:<chat>" --relay-json '<result>'`;
    Convert the trusted relay fields `class`, `sub`, `origin` to JSON;
    only `ok:true` continues the existing calendar-name / engrave screens
    (`ensure_mirror_engraved`), then reconcile `--repair --drain`, then B2.
    A failed or expired relay does not advance or enable the mirror.
    After engraving succeeds and `MIRROR_MODULE=on` is saved, run
    `python3 routines/bundle/mirror-poll-ctl.py reconcile`. This is the kit's
    five-minute poll schedule; it calls the installed framework engine
    `routines/mirror-poll.sh`. Use it in place of the old framework setup
    step that loads a mirror-poll plist from `routines/` (fresh macOS
    instances no longer ship that plist). The manifest's PostToolUse hook
    also reconciles these same saved choices after connect, lifekit on/off,
    or Google disconnect; SessionStart and post-install repair drift. No
    routine menu item or owner-facing scheduling message is added. Leave
    the existing weekly reconcile schedule in its own setup path.
  - skip -> say `life_setup.calendar.skip_lead`, then B2, then (same
    message, last) `life_setup.calendar.connect_later`. Later, "캘린더
    연결" starts the same framework procedure.
- **B2 first input** -- say `life_setup.calendar.first_input` (question +
  예) lines: the format to type, not buttons). No time information here.
- **B3 result** -- write what the owner said with the usual verbs
  (`$LKIT schedule-add ... --kind appointment|task` / task / `routine add`). Relay each write's CARD block
  as its CARD_RELAY says, grouped under one header line per kind that
  appears: `life_setup.result.schedule` (📅 일정), `life_setup.result.task`
  (✅ 할 일), `life_setup.result.routine` (🔁 루틴). A place-less
  appointment prints PLACE_ASK (existing): ask it; "미정" ->
  `$LKIT appt-upd <id> location=미정`, never asked again.
- **B4 brief/retro ON + told time** -- switch on `morning-brief` and
  `daily-retro` at the bundle.conf time: `bundle-routine-ctl.sh on
  morning-brief 06:00`, `bundle-routine-ctl.sh on daily-retro 22:00` (the
  schedule column; the helper does the brief-slot swap; units must be
  loaded before the next call). Then:

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
  - then, same turn, the weekly catch-up setting (owner 2026-10-11), one
    closed choice: the `$BT` call above already printed it when unanswered
    (`WEEKLY_CATCHUP_ASK` + `ASK_OPTIONS`; `$BT --catchup-ask` prints it on
    demand). Ask `retro.weekly.catchup.ask` verbatim with its buttons
    [`retro.weekly.catchup.on` | `retro.weekly.catchup.off`] ("주간회고를
    일요일에 못 하면 다음 날 아침에 이어서 할까요?" [다음 날 아침에 하기]
    [그냥 넘기기]) -> `$BT --catchup on|off` -> its `SAY` line. Stored as
    kit conf `WEEKLY_CATCHUP`; asked once (an empty value is asked again at
    the next briefing-time change, and behaves as on meanwhile).
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
  of 식단과 운동).

## C. 식단과 운동

- **C1 prompt** -- say `life_setup.health.prompt` (ends "오늘 먹은 것부터
  하나 보내 보시겠어요?"). No routine is switched on.
- **C2 meal record** -- meal-log as usual (`$LKIT meal-add ...`). Its
  output now ends with the kit meal card (`MEAL_CARD_BEGIN..END` +
  MEAL_CARD_RELAY): one short line with the ESTIMATE reason, then the card
  verbatim in one code block. The kit card always prints; a pack hook may post
  it instead (then the output / hook context says `CARD_POSTED` -> only the
  short line, no second card).
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

## D. 가계부

- The group pick is the owner's request to start. First run
  `service/kit_life/ledger/onboarding verdict`. `NO-OFFER complete` (exit 3)
  -> ordinary ledger-log / ledger-view / ledger-session; no next/restart.
  Every other verdict (including management-declined, declined-exhausted,
  recording-active) -> `$LKIT manage-set ledger on`, then ledger-setup's
  owner-asked lane: `service/kit_life/ledger/onboarding next`.
  Explicit intent to start over, during or after the flow -> ledger-setup's
  restart lane; never infer it from a particular phrase.
- Relay the returned `question.ask` verbatim, alone, with only the service's
  `question.choices` buttons. The service opener is the introduction.
  Continue ledger-setup's existing answer/next/apply loop; `done: true`
  leads to `onboarding apply`, then send its `ready` line alone.
- No extra introduction, start question, or consent question. `next --offer`
  and `offered` belong only to the unsolicited SessionStart offer lane.
  Do not restart an in-progress onboarding without explicit start-over intent.
- No Google connection, briefing-time setup, or routine activation here.

## later

Resolve later starts by intent and state, regardless of wording. For non-ledger
management use `$LKIT manage-state <module>`:
`MANAGE<TAB>module<TAB>on|off<TAB>reason`; off means setup outstanding.

- Calendar/briefing start intent: read brief management plus actual routine/slot
  state; outstanding -> group management on per offer.tsv, B from B1
  (this is when brief/retro go on); complete -> normal schedule/brief use.
- Meal/diet/workout/health start intent without a real record: read each
  requested module’s manage-state; off -> `$LKIT manage-set meal workout health on`, group C from C1;
  on -> normal use. Actual food/workout/measurement content captures immediately,
  even with health off; never replace it with C1 or a setup question.
- Ledger start/use intent -> D's verdict-first rule, including completed and
  declined states. Only explicit start-over intent selects its restart lane.
- change-time requests -> SKILL.md entry B.
- Never re-run the whole flow unprompted; never offer the goal interview.

## role line (framework stamp-role)

The framework stamps the agent's one-line role from the kit bundle in the
instance language (AGENT_LANG): key `life_setup.role` in
`config/i18n/<lang>.bundle.json` (ko, en; byte-identical copy in
`service/kit_life/i18n/<lang>.json`). Never spoken in this flow.

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

For schedule captures during setup:

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
