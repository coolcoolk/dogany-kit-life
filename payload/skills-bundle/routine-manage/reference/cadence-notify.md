# routine-manage -- cadence + notify tables

Read when parsing a cadence from an utterance or mapping a notify answer.

## cadence (routine_projection.py parses; invalid -> loud error)

```
"every day" / "daily"          -> D
"weekdays"                     -> W:MON,TUE,WED,THU,FRI
"weekends"                     -> W:SAT,SUN
weekday list ("Mon/Wed/Fri")   -> W:MON,WED,FRI
"every other day"              -> I:2@<first date>
"every N days"                 -> I:N@<first date>
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
