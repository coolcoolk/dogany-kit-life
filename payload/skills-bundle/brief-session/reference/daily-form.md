# daily retro -- message form (owner-approved 2026-09-21)

Read when composing any daily-lane message (stages 2-4).
Examples = DEFAULT preset illustration. Copy source = i18n bundle, not this file.

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
- bridge/formatting.py facts: contiguous `> ` run = ONE blockquote; blank
  line ends it (stray blank inside an event block = reads as second event).
  text after `> ` verbatim: two-space child indent survives; type `•`
  literally (`- ` in a quote stays a hyphen).
- inline code only in BARE item cards, never inside a blockquote (D20-정정4).

## dashboard (D23-정정, D24)

```
> **일간회고** 9/21(월)
> ✓ 할 일
>   ✓ apple tv 취소 · 완료
>   ✓ 자동차 보험 비교 · 나중에
> ▸ 오늘 일정 1/3
>   ▸ 회사
>   · 치과 검진
>   · 재즈방
> · 내일 일정
>   · 09:00 - 18:00 회사
>   · 19:00 - 21:00 민수랑 저녁
```

- glyphs: `✓` done / `▸` in progress / `·` upcoming.
- every stage expands children, upcoming included. no-material stage = no row (D7).
- count on IN-PROGRESS stage only, `이번 회차 / 전체`: `▸ 할 일 1/2 (전체 6)`
  when D11 compressed backlog. finished stage: no count.
- child line records the CHOICE (`· 완료`, `· 나중에`, `· 기록`, `· 스킵`).
- schedule children carry time: `회사 · 09:00 - 18:00`.
- shown from stage 2; redrawn on STAGE change only, not between items.
- no column alignment; `항목 · 결과` inline.

## stage 2 card

```
할 일부터 점검할게요.

**자동차 보험 비교**
`9/18(금) · 3일 지남`

어떻게 처리할까요?
[[OPTIONS: 완료 | 내일로 | 나중에 | 지우기]]
```

- today's item: NO date line (D23). attribute line only when there is something to say.
- no UI guidance line ("버튼을 선택하거나 직접 말씀해주세요"): D17 attaches it
  only to turns whose buttons do NOT cover the answer; daily flow has none.

## stage 3: event block (list of several events)

Each event = ONE blockquote, attributes = `•` bullets inside (D20-정정/정정4):

```
> **민수랑 저녁**
> • 19:00 - 21:00
> • 성수
> • 민수
> • 준비물 · 가방
> 지난주 성수에서 만난 뒤 2주 만이에요.
```

- attributes bulleted, memory line bare.
- missing attribute drops its LINE, never "없음" (D18).
- prep items (053) = `sub_event(kind='prep_item')` -> `carry_item` rows,
  never free text (D20-정정3). `prep-list` already collapsed ("가방" covers
  벨트·악보). item pulled from bag today shows individually again. check off:
  `prep-set <event> <item> 1`.
- context line ONLY when a record supports it.
- travel time NOT displayed (D20-정정4); input to stage-4 arrangement only.
- all of one event stays inside its block.

## stage 3: one-at-a-time card (D23-정정)

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

## stage 4: one message (D32, D15, D27)

```
> **일간회고** 9/21(월)
> ✓ 할 일
>   ✓ apple tv 취소 · 완료
> ✓ 오늘 일정
>   ✓ 치과 검진 · 기록
>   ✓ 회사 · 스킵
> ▸ 내일 일정
>   · 09:00 - 18:00 회사
>   · 19:00 - 21:00 민수랑 저녁

오늘 정리했습니다.
할 일 1건 완료 · 일정 1건 기록 · 잇몸치료 등록

📅 내일 일정
- 09:00-18:00 회사 · 사무실
- 19:00-21:00 민수랑 저녁 · 성수 · 가영

퇴근하고 치과, 바로 재즈방이라 저녁 먹을 틈이 없네요.

추가하거나 변경하고 싶으시면 말씀해주세요. 없다면 오늘 회고는 여기까지입니다.
```

- order: dashboard -> summary (today) -> tomorrow list -> arrangement line
  -> close line. NO stage notice here (`retro.daily.stage.tomorrow.notice`
  is not used): the `📅 내일 일정` header already opens the block; a notice
  line above the summary dangles (owner 2026-09-30).
- tomorrow = morning-brief list (D27): `시각 제목 · 장소 · 사람`, one line
  each, no context, no travel time. data: `appt-find <tomorrow>` (or
  `event-window` over tomorrow's local day when task blocks needed).
- body repeats tomorrow even though dashboard lists it (owner 14:15/14:18:
  dashboard = progress, body = what he judges). accepted.
- summary (D15) = chip keys: completed / later / dropped / recorded / newly added.
- arrangement line (D20): conflict that exists only in the sequence. travel
  time is an input. no collision -> no line.
- NO BUTTONS (D32). retro closes open.
