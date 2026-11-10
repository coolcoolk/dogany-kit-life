# routine-manage -- cadence + notify tables

Read when parsing a cadence from an utterance or mapping a notify answer.

## cadence (routine_projection.py parses; invalid -> loud error)

```
"every day" / "daily"          -> D
"weekdays"                     -> W:MON,TUE,WED,THU,FRI
"weekends"                     -> W:SAT,SUN
weekday list ("Mon/Wed/Fri")   -> W:MON,WED,FRI
"every month on the 12th"      -> M:12            ("매달 12일")
"last day of every month"      -> M:L
"2nd Monday of every month"    -> MW:2:MON        ("매달 둘째 주 월요일")
"last Friday of every month"   -> MW:L:FRI
"every year on Mar 5"          -> Y:03-05
"every year, lunar Aug 15"     -> YL:08-15        ("음력")
"every other day"              -> I:2@<first date>
"every N days"                 -> I:N@<first date>
"every 3 months"               -> IM:3@<first date>
                                  first = explicit date, else today
time mentioned                 -> time=HH:MM (timed); no duration -> engine default 30 min
no time                        -> all_day
```

Exclusion suffixes, `!`-joined after the base (base unchanged):
- 공휴일 제외 / 공휴일 쉬고 / 공휴일 빼고 / 명절 빼고 -> `!hol`
  (configured country's public holidays incl. 설날/추석 + substitute days)
- 매달/매월 마지막 금요일 / 마지막 주 금요일 쉬고/빼고 -> `!lastfri`
- chain: `W:MON,TUE,WED,THU,FRI!hol!lastfri`
- ONLY `hol` and `lastfri` exist (`_VALID_EXCL`). any other exclusion ->
  refuse; never invent a token.

## recurring = routine

- any recurring owner item (daily / weekly / monthly / Nth weekday / yearly /
  lunar) = `kit_life.sh routine add`; the cadence table covers all of them.
- NEVER hand-register a launchd/systemd plist, cron line or push script for a
  recurring owner item: not a routine (no calendar mirror, no pin/day card,
  no done tracking, no retro check).
- cadence not in the table -> say so in ONE line, offer the closest supported
  form ("매달 마지막 영업일은 없어서 말일(M:L)로 할까요?"), register that.
  never route around with a side mechanism.
- existing hand-made reminder unit for a recurring item -> migrate: register
  the routine, confirm its next row exists (`routine list active`), then
  `routines/lib/routine-ctl.sh disable <name>`.

## notify (confirm answer -> field; parse_notify_arg)

```
keep default            -> omit notify (or notify=default)
no alerts               -> notify=silent
start-time alert only   -> notify=start_only
"N minutes before"      -> notify=N   (lead alert + on-time alert)
reset to default        -> notify=    (empty)
```
`0` normalizes to start_only. anything else -> verb rejects loudly -> re-ask
with the 4 options.

Confirm line shape (task routine), owner's language:
"Adding '<title>' <cadence> at <time> in <area> -- with default alerts 30 min
before + on-time. Keep alerts, or adjust? (silent / start-time only / N min before)"
user already stated a notify preference -> restate it, do not ask again.

## ride=travel (anywhere routine, kit 070)

An OR time rule or a travel minimum independent of item duration uses
**Placement alternatives (kit 092)** below. Do not approximate it with the
legacy knobs in this section.

For an action that can be done anywhere (말해보카, 듀오링고, 팟캐스트,
단어장). The routine sits inside the day's first move leg long enough for
it; a day with no such leg gets the fallback time.

```
utterance                                   -> fields
"출퇴근길에" / "이동할 때" / "가는 길에"      -> ride=travel
"이동 없으면 9시에" / "없는 날은 21시"        -> fallback=21:00
"주말엔 아침 10시"                          -> fallback=MON,TUE,WED,THU,FRI=21:00;SAT,SUN=10:00
"아침 출근길만" / "퇴근길에"                  -> prefer=morning | prefer=evening
"7시 전엔 말고"                              -> not_before=07:00
length ("15분")                              -> duration=15 (the leg must be at least this long)
```
no fallback stated -> ask it in the confirm (the one question). never ask
about legs, prep or the alert: on a travel day the departure alert
keeps the routine line, and the routine sends its own alert at its placed
slot start. No-travel day = its own alert at the fallback. One own alert
per instance, even after re-seating (start-time only; do not ask notify).

There is NO default fallback time (owner 2026-10-07): never fill one in.
Confirm line (one question, owner's language), fallback not stated:
"'<title>' <N>분, 이동이 있는 날은 이동 중에 넣고 출발 알림에 같이 붙여요.
이동이 없는 날은 몇 시에 할까요? (평일/주말 따로도 돼요)"
fallback stated -> "... 없는 날은 <fallback>에 넣을게요. 이대로 할까요?"
after the write, reply with this week from the `RIDE` lines:
"평일은 출근길 07:45, 주말은 21:00" -- never invent a time the lines do not show.
`fallback has no time for <DAYS>` -> ask that one time, re-run.

## Placement alternatives (kit 092)

Accept the owner's stated placement condition first and execute it with the
existing matching routine. Keep the item duration and minimum travel length
as separate facts. Refuse an impossible fit or a fixed-event overlap once,
in plain owner language, with the closest available full-length time.

Example: "말해보카는 매일 15분, 8시 이후나 30분 이상 이동할 때 넣어 줘."
Use `routine update <existing def> 'alternatives=<JSON>'`; for a new routine,
use `routine add <title> D duration=15 'alternatives=<JSON>'`.

```json
{"alternatives":[{"type":"window","start_not_before":"08:00"},{"type":"travel","min_container_minutes":30}]}
```

- The alternatives form a union; ordinary OR selects the earliest fitting
  time across all alternatives. A qualifying early trip also satisfies the
  owner's rule. Keep `duration=15` in every alternative.
- Window: `start_not_before` required, `end_not_after` optional (finish by;
  `24:00` means local day end). Explicit bounds apply on the same local date.
  An open-ended window can finish after midnight, with next-date events checked.
- Travel: `min_container_minutes` is the full trip's minimum length. The item
  also needs its full duration in the remaining free time.
- Either type can have `weekdays:["MON","TUE"]` for that alternative's scope.
- An explicit "try X first, then Y" maps to `"priority":[0,1]`, listing every
  index in the owner's order. Ordinary OR uses the default union selection.
- An explicitly requested fallback maps to top-level `"fallback_at":"10:00"`.
  It is tried after the alternatives with the same fit/collision checks.
- Put the complete condition in `alternatives`; this replaces the old ride
  fields on opt-in. To return to a fixed time, update `alternatives= time=HH:MM`.
- Apply a changed rule from today when today's occurrence is unfinished and
  the new time is still ahead. Pass `effective_from=YYYY-MM-DD` for an explicit
  later start. Preserve completed records and deliberate owner edits.
- Restate the accepted condition in the owner's terms from `PLACEMENT_RULE`:
  "이렇게 넣을게요: 말해보카는 매일 15분, 8시 이후나 30분 이상 이동할 때.
  2026-10-21부터 적용해요." State the start date and the actual times from
  `RIDE` rows. This is the action reply, with the requested condition accepted.
- Relay `PLACEMENT_NOTE` once with its plain reason and closest alternative.
  For a 30-minute item and a 15-minute trip: "이동은 15분이라 30분을 다
  하기엔 짧아요. 가장 가까운 빈 시간은 06:30부터 30분이에요."
- When the owner says "그냥 넣어줘", honor the chosen date/time immediately:
  `routine insist <def> date=YYYY-MM-DD time=HH:MM`. Use the time the owner
  insisted on, keeping the full item duration. The command creates or updates
  that day's owner exception and replies with `PLACEMENT_OWNER`. This explicit
  override can extend beyond travel or overlap an appointment; the fixed event
  keeps its time. Later dates continue under the accepted alternatives rule.
  Reply "알겠어요. <날짜> <시각>부터 <제목> <분>분 넣었어요."
- Translate parse errors into the owner's stated times and conditions, then
  execute the closest faithful expression through these fields.

Legacy `ride=travel` keeps its first-fitting-trip/fallback behavior. Use
alternatives for an OR window, an independent minimum travel length or
weekday-scoped choices.
