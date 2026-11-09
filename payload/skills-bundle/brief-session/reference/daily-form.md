# daily retro -- message form (owner-approved 2026-09-21)

For walk kind `experience`, read [experiences.md](experiences.md) first.
The lived-day list and missing-field queue own those rows; calendar recap
rules below apply only to the remaining calendar items.

Read when composing any daily-lane message (stages 2-4).
Examples = DEFAULT preset illustration. Copy source = i18n bundle, not this file.

## Bind the session date before any daily read

The `[daily-retro]` UserPromptSubmit context or `start-tap` result owns
`retro_date=D` and `tomorrow=D+1`. Keep these through every turn and close.
All `<today>` / "today" placeholders in the daily SKILL mean D, including
the header, task refreshes, events, facts and disposal answers. The clock
does not choose a card's retro date, even when it is answered next morning.

Without that context, run `retro-start "<exact action or full bridge reply
context>"`. It accepts `retro:daily:YYYY-MM-DD`, the dated callback metadata,
or a typed reply whose bridge context quotes the daily card. Pass the full
reply context, including the original card, not just "회고 시작". `ASK_DATE`
means ask once which date; never silently substitute the current day.
For an unbound request, interpret the owner intent and resolve the requested day,
then run `retro-start --date YYYY-MM-DD`. Code never matches a retro start phrase. Weekly entry remains
the weekly lane; never bind a weekly card as daily.

Use the returned `commands`: `day-record-summary D`, `task-find D`,
`task-read D`, `task-overdue --before D` and `retro-items D`. D11 is D's
still-todo tasks plus the two oldest tasks strictly before D, deduplicated
by id. Exclude the session's own retro task. Refresh the same D before
each question; skip settled rows. Do not use unbounded `task-overdue` for
a dated session, since it defaults to the current day.

For example, the 10/21 card answered at 2026-10-22 11:12 KST opens
`일간회고 10/21(수)`, reads 10/21 tasks/events and the 10/20 carry;
`tomorrow=2026-10-22`. A 10/22 20:00 task belongs only in that tomorrow
preview, never the 10/21 leftover questions. On a past-day done answer,
use `task-done <the displayed id>`, never `task-done-today` (which resolves
today's occurrence). `내일로` uses `task-reschedule <id> D+1`, not clock+1.
The close reads `appt-find D+1` and `task-read D+1`; reader failure still
prevents a complete close. Ordinary same-day and routine-skip rules remain.
Read and display D+1's schedule only at stage 4, including dashboard
children. Stages 2-3 list D's tasks/events and older carry only; an answered
10/21 card must not open by listing the not-yet-happened 10/22 rehearsal.

## copy and layout keys (D13 / D21)

Resolution order = `kit_life.py._i18n` (DGN-1789): the framework's
`routines/lib/i18n_lookup.py` merged_bundle (kit layers
`config/packs/<id>.i18n.<lang>.json` in dependency order > framework default
`config/i18n/<lang>.json`); old framework without that helper: kit copy
`service/kit_life/i18n/<lang>.json` > `config/i18n/<lang>.json`. `AGENT_LANG`
first, `en` fallback. Kit keys ship in `config/i18n/<lang>.bundle.json`.

- copy: `retro.daily.*` (title, notices, questions, button labels, summary
  chips, close line).
- layout: `retro.daily.layout.<preset>.<slot>`. preset = `RETRO_DAILY_LAYOUT`
  in `config/agent.conf`, default `default`; missing slot in custom preset ->
  `default`. slots: `dashboard.head|stage|item|count|result`,
  `card.title|attr`, `event_block.head|attr|context`, `list.*`, `opener`.
- shared with morning brief: `schedule.header.today|tomorrow`,
  `schedule.time_range`, `schedule.line.sep` (tomorrow block D27 renders
  from same constants).
- new user-facing sentence -> add a key. never write Korean into skill/script.

## Daily opener replies

The offered card starts with one evidenced visit/event, otherwise the unchecked
count. There is no start button. A reply is already the conversation: do not ask
whether to start or require a start label.
After opening D, read its saved `opener` identity. Interpret an event-opener
answer as that event's reaction; record it with `retro-item-set ID` or
`experience-enrich ID reaction=... verbatim=...` as appropriate, keeping the
owner words. Then resume the same ordered walk; keep its item asks and close gate.
Do not invent an experience from a task or an untyped capture note.
A count opener binds no individual task. A general reply enters the first task
ask (`current.question`); resolve a named task before recording its disposition.

With neither opener and with walk items, `entry_mode=automatic`: the sender
posts the existing first step immediately after the card. When
`first_step_sent=true`, act on the answer to that current item and advance it;
do not resend the first step. An empty day remains silent.

## Kit-owned ordered card walk

After binding D, run `database/kit_life.sh retro-walk D open` before
questioning. For an ordinary task card, first relay `current.question` (whether it was done).
Unchecked means unknown. Record a completed answer by id; only if the owner says
it was not done ask the existing disposal question. Keep wait-specific asks.
At disposal, relay `current.options` verbatim as its last line. Its four
labels come from i18n and override the menu example in the top-level skill.
Relay `current` only, in the returned `items` order, including
untimed and module rows. Use each item's `kind` and `id`, never a title
match. Preserve merged `signals` on that item; do not open a second section
for them. For a module row, use the named provider's section only for that
current item. Do not append a separate sweep of unlisted module sections.

After the owner's answer and the usual kit disposition/record verb succeeds,
run `retro-walk D advance KIND ID RESULT` (done, recorded, skipped, deferred,
discussed, or removed). Relay its next `current` and wait for the owner again.
Do not advance an unanswered item, rebuild the list on refresh, or mark a
question complete merely because it was displayed. `remaining=0` permits
stage 4; close with `retro-walk D finish`, which refuses unfinished walks.
Only an explicit owner stop uses `retro-walk D stop "owner words"`; that
preserves all unwalked items. The kit owns selection, dedupe, age, cursor and
close eligibility; this reference only relays its output.

## message anatomy (stages 2, 3)

One message per item, five slots:

```
<dashboard>        blockquote, state panel        (D23-정정)
<stage notice>     one line, first item of the stage only   (D28)
<item card>        bare text, bold title + inline-code attribute lines
<question>         one line, the verb of this turn (D25, D30)
<buttons>          [[OPTIONS: a | b | c]] on its own LAST line, only at
                   disposal / skip positions (bridge/options.py builds the
                   keyboard from labels; no numbered list in body)
```

- hierarchy from FORM: dashboard quoted, item bare. both in one message
  (never an empty shell). buttons same message.
- On the verified HTML whitelist delivery path (routine push / pin), use
  one `<blockquote>...</blockquote>` per item, joined by one newline with
  NO blank line between item blocks, as the life agent's daily pin does. Keep all
  attributes of an item inside its block; section gaps may remain.
- Agent replies use the bridge markdown path: contiguous `> ` lines merge
  into ONE blockquote. Separate adjacent item blocks without blank lines
  require a bridge feature; do not invent HTML markup in agent replies.
  Keep the markdown examples below until that feature is available.
  Text after `> ` is verbatim: two-space child indent survives; type `•`
  literally (`- ` in a quote stays a hyphen).
- inline code only in BARE item cards, never inside a blockquote (D20-정정4).

## dashboard (D23-정정, D24)

```
> **일간회고** 9/21(월)
> ✓ 할 일
>   ✓ apple tv 취소 (완료)
>   ✓ 자동차 보험 비교 (나중에)
> ▸ 오늘 일정 1/3
>   ▸ 회사
>   · 치과 검진
>   · 재즈방
> · 내일 일정
>   · 09:00 - 18:00 회사
>   · 19:00 - 21:00 민수랑 저녁
```

- glyphs: `✓` done / `▸` in progress / `·` upcoming.
- stages expand D's children, upcoming included; expand tomorrow's children
  only at stage 4. no-material stage = no row (D7).
- count on IN-PROGRESS stage only, `이번 회차 / 전체`: `▸ 할 일 1/2 (전체 6)`
  when D11 compressed backlog. finished stage: no count.
- child line records the CHOICE (`(완료)`, `(나중에)`, `(기록)`, `(스킵)`).
- schedule children carry time: `회사: 09:00 - 18:00`.
- shown from stage 2; redrawn on STAGE change only, not between items.
- no column alignment; `항목 (결과)` inline.

## stage 2 card

```
할 일부터 점검할게요.

**자동차 보험 비교**
`9/18(금) (3일 지남)`

어떻게 처리할까요?
[[OPTIONS: 완료 | 내일로 | 날짜 없이 두기 | 지우기]]
```

- today's item: NO date line (D23), but always show a stored timed task's
  local start-end clock. attribute line only when there is something to say.
- no UI guidance line ("버튼을 선택하거나 직접 말씀해주세요"): D17 attaches it
  only to turns whose buttons do NOT cover the answer; daily flow has none.

## undated block (stage 1)

- Stage 1 may carry a `날짜 안 잡힌 할 일` block: loose undated to-dos
  (`-> <project>?` = unconfirmed guess, never state it as fact) and one
  project step stalled > 3 days as `<project>: <step> (N일째)`.
- offer each for a date in the retro conversation (today / tomorrow / a day,
  or drop); never list them again under overdue/today.

## stage 3: event block (list of several events)

Each event = ONE blockquote, attributes = `•` bullets inside (D20-정정/정정4):

```
> **민수랑 저녁**
> • 19:00 - 21:00
> • 성수
> • 민수
> • 준비물 / 가방
> 지난주 성수에서 만난 뒤 2주 만이에요.
```

- attributes bulleted, memory line bare.
- missing attribute drops its LINE, never "없음" (D18).
- prep items (053) = `sub_event(kind='prep_item')` -> `carry_item` rows,
  never free text (D20-정정3). `prep-list` already collapsed ("가방" covers
  벨트/악보). item pulled from bag today shows individually again. check off:
  `prep-set <event> <item> 1`.
- context line ONLY when a record supports it.
- travel time NOT displayed (D20-정정4); input to stage-4 arrangement only.
- all of one event stays inside its block.

## stage 3: one-at-a-time card (D23-정정)

Read `appt-find <today> --retro` for this walk (and refresh before each
question). It excludes ongoing appointments and stays, including an ended
appointment extended by a same-place stay. Do not ask about or settle an
excluded item. Unanswered overnight visits return on their ending day;
the actual appointment and stay times remain unchanged. Tomorrow's preview
still uses ordinary `appt-find <tomorrow>`.

```
오늘 일정 점검할게요.

**치과 검진**
`19:00 - 20:00`
`성수`

치과에서 기억나는 일 있어요?
```

- question always names its subject (D30). one sentence fits every event
  kind: no per-kind wording, no mood buttons (D19).
- routine event: `[[OPTIONS: 직접 입력 | 이번엔 스킵 | 이 루틴 항상 스킵]]`.
- module-typed event may add kind-specific buttons; kitless event = open line.
- follow-up task notice (one line): "잇몸치료를 다음 주 할 일로 잡아뒀어요."
  keep owner detail in title ("잇몸치료 (아래쪽)").

## stage 3: nested item = one card (retro batch, owner 2026-10-09)

Source: `retro-items <today>` (same gate as `appt-find --retro`). An outer
event with things inside it (outing bundle legs, contained_by children,
attached meals, a continuing same-place stay) is ONE item: one list line,
one card, one question with a flow recap of the children in time order
(places/visits/meals, max 4 names, never travel/buffer). Send the item's
`card` verbatim. Answer -> `retro-item-set <item id> "<his line>"`: lands on
the outer and on every child without its own line.

```
오늘 일정 점검할게요.

**잠실 데이트**
`14:00 - 20:00`
`석촌호수`

잠실 데이트 어땠어요? 카페, 모모식당 들르신 것 같은데요.
모모식당
[[OPTIONS: 또 갈래 | 무난 | 별로]]
```

- the rating row exists only when a module that rates its own children
  (dining / place taste, `service/<ns>/retro-child-rating.tsv`) claims one
  child; one row, same message, never its own turn. A tap is that module's
  answer, not the retro line.
- carried item (visit that started on an earlier day, asked on its ending
  day): NOT under "오늘 일정 점검할게요."; its card opens with its own lead:

```
어제 수정연준 집들이는 숙박이 오늘 끝나서 지금 여쭤볼게요.

**수정연준 집들이**
`10/8(목) 19:00 - 10/9(금) 09:45`

수정연준 집들이 어땠어요? 자고 오신 것 같은데요.
```

## stage 4: one message (D32, D15, D27)

```
> **일간회고** 9/21(월)
> ✓ 할 일
>   ✓ apple tv 취소 (완료)
> ✓ 오늘 일정
>   ✓ 치과 검진 (기록)
>   ✓ 회사 (스킵)
> ▸ 내일 일정
>   · 09:00 - 18:00 회사
>   · 19:00 - 21:00 민수랑 저녁

오늘 정리했습니다.
할 일 1건 완료 / 일정 1건 기록 / 잇몸치료 등록

📅 내일 일정
- 09:00-18:00 회사 / 사무실
- 19:00-21:00 민수랑 저녁 / 성수 / 가영

퇴근하고 치과, 바로 재즈방이라 저녁 먹을 틈이 없네요.

추가하거나 변경하고 싶으시면 말씀해주세요. 없다면 오늘 회고는 여기까지입니다.
```

- order: dashboard -> summary (today) -> tomorrow list -> arrangement line
  -> close line. NO stage notice here (`retro.daily.stage.tomorrow.notice`
  is not used): the `📅 내일 일정` header already opens the block; a notice
  line above the summary dangles (owner 2026-09-30).
- tomorrow = morning-brief list (D27): `시각 제목 / 장소 / 사람`, one line
  each, no context, no travel time. data: `appt-find <tomorrow>` plus
  `task-read <tomorrow>`, the validated local-day task reader. A failed read
  is not an empty result and cannot produce a complete close. A timed
  item before the morning brief (e.g. 05:00) MUST be listed with its time:
  remind_select drops the standalone night-before ("eve") alert while this
  retro (or the Sunday weekly review) is still to run, so this list is where
  the owner meets it.
- body repeats tomorrow even though dashboard lists it (owner 14:15/14:18:
  dashboard = progress, body = what he judges). accepted.
- summary (D15) = chip keys: completed / later / dropped / recorded / newly added.
- arrangement line (D20): conflict that exists only in the sequence. travel
  time is an input. no collision -> no line.
- NO BUTTONS (D32). retro closes open.
- day-record-summary (interactive start): linked meal stays inside its outer
  event; unlinked meal shown once as a standalone fact. recorded workouts
  same way, no health coaching.
- task-read: stored start/end + local clock; no date line does not hide the
  clock.

## task answers (stage 2)

- 완료 -> `task-done <id>`
- 내일로 -> `task-reschedule <id> <tomorrow>`. no time question (D5).
- 날짜 없이 두기 (also a typed 나중에) -> `task-undate <id>` (dateless backlog; weekly D26 places it).
  never fake with a far date. routine occurrence refuses -> treat as 내일로 or 지우기.
- 지우기 -> `task-archive <id>`
- worded answer beats nearest button. A stated clock goes through
  `task-postpone <id> "<owner time text>"`: at 21:18, `9:30으로미뤄줘`
  means today 21:30; at 07:00 it means today 09:30. `내일 9시반` means
  tomorrow 09:30; `오후 9시반` means 21:30. Never default a bare clock to
  tomorrow or make a replacement with task-add; move the existing id.
- The third button says `날짜 없이 두기`, using `retro.daily.btn.later` (en:
  `Unschedule`), and keeps task-undate behavior. The former `나중에` label
  is a bridge defer word and is dropped from buttons.

An owner-reported arrival or completion resolves an existing wait with
`plan-wait-resolve ID --said "exact owner words"`; select the existing wait id
yourself. The words never select or authorize a wait. For corrections to a
visited stop use `experience-enrich EVENT_ID verbatim=WORDS`; for requested
clock moves use `task-postpone TASK_ID WHEN`. Keep the current retro id/date.

### Correct a retro in session

Use `event-retro-clear` / `event-retro-undo` for one line, or
`retro-item-clear` / `retro-item-undo` for the outer answer and inherited
children. Existing set verbs replace the answer; independent child lines stay.
`retro-walk D back` returns to the last disposed item; `reopen` reactivates a
finished/stopped walk; `undo` reverses the latest cursor transition. These never
post, repeat a provider command, or undo a task/module disposition. Correct that
value using its own module verb too. See verbs.md for guards and readback.
