# goal-session: per move, what the answer turns into

Read at the first move of an interview. Generic rule (SKILL step 3) covers
most rows; this table holds the per-kind detail.

Every data ask's `say` is a why line (what the answer is used for), then
ONE question -- speak it as rendered, never add a second ask or an "if not"
fallback; the fallback is the next move.

Every command a move carries (choice `cmd`, `on_answer`, `on_yes`, ...) is
an argv for THIS kit's CLI: `database/kit_life.sh <argv...>` (the move says
so in `cmd_runner`). Never run it as a file under a pack's `service/`.

| kind | say | user answers -> you run |
|---|---|---|
| `ask_direction` | opening question, then the one free-input line ("버튼을 선택하거나 직접 말씀해 주세요."); the 3 candidate answers are `choices` (buttons, one per axis: state / event / habit) when the copy ships labels (health pack, spend). without labels (the neutral pair): 3 examples ("예)" + "• " lines) instead, no `choices`. zero goals, no restart: `readback` = [`intro` (domain pack's 지향/목표 lines; absent without one), empty card] -- the first message is intro, card, question (+ free-input line), buttons attached | typed -> `why-add --text "<their words, byte-for-byte>" --label "<short name from their words, <= 20자>" --domain <D>`. tap -> picked choice `cmd` (the same argv with the label as their words; fill `--label`), no confirm |
| `derive_axes` | none; act, then `--next` | see "strands" below |
| `strand_picture` | none (silent): the strand's first move (its pace or its practice menu) needs its picture and none is stored. `label` = the qualified name, `clause` = its clause of the direction | run `on_proceed` with your phrase in `--words` (what the strand looks like as a long-term picture, plain noun form, <= 30자; e.g. `어깨·등은 넓고 허리는 좁은 역삼각형 몸`); `--qualified` present = the clause could not be cut, add the strand's name with its qualifier (`크로스핏식 기능성`); then `--next` |
| `confirm_direction` | first move of a consult round on a direction stated before the round (a gap over 6 h between `--next` calls starts a new round): transition line + the user's own words quoted + the strands, then "이 방향 그대로 갈까요, 바꿀까요?". never skipped: nothing is asked or proposed before it | keep -> picked choice `cmd` (`goal-move-note --why-id N --confirm-why keep`; not asked again this round). change -> `--confirm-why change`, then `--next` gives `ask_direction_adjust` with `field: "why"`. anything else (a strand to add, a clause they miss) = change |
| `confirm_activity` | after a restart: what they do + how long, as known; one tap | picked choice `cmd` (`--facts keep` / `--facts changed`; changed -> `ask_activity` follows) |
| `ask_activity` | health: calendar + record lead ("캘린더에는 평일 아침 '헬스'가 걸려 있고, 최근 4주 운동 기록은 주 평균 N회예요."), then the question + the free-input line, the 3 answer shapes as `choices` (pack labels; a pack without labels: example lines, no `choices`) | typed -> `goal-move-note --why-id N --activity "<their words>" --card "<your short phrase>"`; tap -> picked choice `cmd` (the same argv with the label as their words; fill `--card`), no confirm. the answer already says how long -> add the `if_years_said` flags to the SAME call (`--training .. --years-band .. --history ..`); `ask_training` is then skipped |
| `ask_training` | how long they trained like this; the three years bands are `choices` (buttons: 1년 안쪽 / 1~3년 / 3년 넘게) (follow-up inside 운동 slot; health only; skipped when a years band is stored) | tap -> picked choice `cmd`, replacing the `--history` placeholder with the kind read from the activity answer. typed answer -> `on_answer`: `goal-move-note --why-id N --training "<their words>" --card "<your short phrase>" --years-band <lt1\|1to3\|3plus> --history <continuous\|returner\|beginner>`. band from their words (6개월 안쪽 -> lt1, 1~3년 -> 1to3, 3년 넘게 / 9년 -> 3plus). history: training now -> continuous, resting now after training before -> returner, never trained -> beginner. stored once; pack never asks again |
| `ask_baseline` | body step, one value per message. no body reading on record: `field: inbody` = the latest InBody only (photo or numbers), then `field: height`, `field: weight` only without one. InBody older than 28 days (health): `field: inbody_stale` = weigh now / cannot now / new InBody, one question; after "지금 잴게요" `field: carry_weight` = today's weight. a performance value is never asked | InBody -> `health-log` skill, then `--next` (it judges the date). no InBody -> the `none` choice. `inbody_stale` -> picked choice `cmd`; the weight already in that answer -> `if_weight_said` instead. `carry_weight` -> `on_answer` with the kg (writes the carried fat mass / body fat as `computed`). value -> `on_answer` with it. list of commands -> run each. unknown -> `if_still_unknown` |
| `ask_safety` | one check before order + any number. safety rows on record (`narrowed: true`): "조심할 부위로 ... 적혀 있어요. 지금도 그대로인가요?" [그대로예요 \| 달라졌어요]; none on record: anything that hurts? [없어요 \| 있어요] | same -> `--safety records` choice `cmd`. nothing -> `none` choice. changed / site -> that choice `cmd` with their words + `--card`, then `on_site` once per site |
| `order_choice` | one proposed cut, recomp, lean bulk, or bulk direction in four sections with fixed marks: what the phase is; how much the daily intake moves (the slowest..fastest option the next pace move builds, the limit line only when a cap or the muscle floor removed an option); why (body fat against the declared sex baseline and training years, each with what it changed, and the wish when there is one); what comes after. The orientation is not quoted (the card shows it). Without body data the amount says it cannot be computed yet, the reason quotes the aim words, and there is no after section. The two buttons accept or adjust; a free-text short-term wish can also be recorded with `goal-move-note --direction-wish` | accept runs the picked `axis-order --mode` command, which writes the order and held values; adjust runs the picked command and then the next move asks for the wish |
| `ask_direction_adjust` | one question for the desired change and date after the adjust button. `field: "why"` = after "바꿀래요" on `confirm_direction`: how they want the direction changed | send the user's words through `on_answer` (`--direction-wish`); the next `order_choice` re-proposes direction and the pace options use the wish. `field: "why"`: `on_answer` is `why-pivot N --text "<their words>" --label ..`; `--next` then starts from the new direction's strands |
| `ask_demographics` | without an InBody lean-mass reading, one value per message: `field: age`, then `field: sex` (two choices) | age -> `on_answer` with it; sex -> picked choice `cmd`; then `--next` |
| `ask_sex` | why the rate needs it, one question | picked choice `cmd` |
| `ask_experience` | `say: null`, `delegate: pack_health` (muscle rate has no years band). card still rides `readback`; pack's question speaks it | pack's program flow asks its one training-history question + stores answer; then `--next` |
| `propose_pace` | the strand's FIRST number opens with its framing (`frame`): the picture line ("피지크식 x테이퍼는 ... 역삼각형 몸을 말해요."), then the picture as the long-term direction + this phase's first goal + why (`frame_basis.why`: body fat vs the sex baseline, the aim words, the chosen order, or the plain default); then held line, lift progression line + cadence stated, the current value (body fat in kg, % alongside), 3 pace options (one variable; none past the muscle floor), recommendation + noise line, one question. Protein follows the chosen pace on the next card. `correction` set = a re-proposal after `--correct` | picked choice `cmd` (a command list: the goal, the cadence, on a correction the old goal retired). anything with a direction -> `--correct` (corrections.md), never a question back |
| `correct_held` | a held strand the user wants more of (a number): 3 options + recommendation | picked choice `cmd` |
| `measure_first` | none (silent) | run `on_proceed`, `--next`. next card's strand line shows it ("첫 측정 뒤") |
| `ask_feasibility` | non-health only: ideal cadence + reason + "can you?" | yes -> `on_yes`. other cadence -> `freshness-set <metric> <daily\|weekly\|biweekly\|monthly\|hours>`. none -> `goal-move-note --why-id N --cadence-declined <metric>` |
| `propose_practice` | strand with no number: the picture line first, then why these options (the strand's clause), then the menu -- the STRAND's own (`practice_set`, routed on its qualifier, then its clause, then the bare name): `functional` (크로스핏/체력/파워 = work capacity: power / mixed circuit / short conditioning, core-activation line, recommendation: any caution on record -> conditioning, else the words) or `connection` (연결/필라테스/흐름/가동성/협응/균형: breath+trunk / mobility flow / whole-body linking, recommendation from the words); 3 buttons, the single options only (no pair button; no `(추천)` on a button -- the recommendation is in `say`). two strands of one direction never share a pick key. `practice_set: null` = no set fits (or another strand already has it): `choices` absent, `say` is one open ask in the strand's own words | a tapped button -> its choice `cmd` (`--practice <axis> --words <label> --pick <one> --card ..`). any answer in words ("1이랑 2", "호흡이랑 가동성", "2번", "셋 다") -> `on_answer` with their exact words (`--pick-said`): the kit notes every option named, any number, all three included (no limit: never refuse more picks); refused = the words name no option (their own way -> `--words` + `--card`; unclear -> ask again). open ask -> `on_answer` with their words + your `--card` phrase. the user complains about / repeats another strand's answer AND names no option of THIS strand -> NOT an answer: re-ask this strand (one line, see "mixed answer + side question"). the user CHOOSES the same move another strand already has -> note it as said: goal-move-note writes it (never refused) and the next move carries one line that the same move counts for both strands (`prac_same`); `--allow-same-practice` is accepted and changes nothing |
| `wrap` | ONE message: full card (every slot filled) + one wrap line (care filled by the tool, next check date) + with pack_health the transition line. `handoff.append == "program_first_question"` -> the pack's first program question goes in the SAME message after a blank line (pack path: its question's buttons, kit `choices` dropped). kit spoken directly -> `choices` `go` is the one button (never end on the bare transition line) | `go` (`then: pack_next`) -> hand the turn to the domain pack's next move (health: `program-plan next`, its first program question + card). the program card opens from the next message |

## mixed answer + side question (any move with `on_answer`)

- pick in words ("셋 다", "1이랑 2", "다 할래") IS an answer even with a
  question or pushback attached. record FIRST: `on_answer` with their exact
  words (`--pick-said "<their words>"` on `propose_practice`). then answer the
  side question in 1-2 lines inside the NEXT move's message.
- side question alone (no pick): answer in 1-2 lines, then re-ask in ONE line.
  never re-send the card, the readback, or the option list. never ask them to
  re-type a pick they already said.
- "complains about / repeats another strand's answer = not an answer" applies
  only when the message names NO option of THIS strand.

## wrap handoff (the pack reads, never re-asks)

`handoff`: `calendar_line` (A4 sentence from active workout routines, null
when none), `calendar` (`[{id, title, weekdays 0-6, start, end, since}]`),
`order_line` ("덜어내기 먼저"), `goal_mode` (cut/recomp/lean_bulk/bulk),
`diet` (calories and protein or pending), `first_axis`, `horizon` (ISO), `safety_card`
(the card's 주의 phrase), `practice` (`{axis_key: pick | [pick, second]}`), `activity`
(`{words, card}`), `training_band`, `measure` (body metrics with a goal),
`progression` (`{axis_key: {label, metric, goal_id, rule: per_session,
baseline, baseline_as_of, deficit, success: hold|progress, stretch}}` --
the lift strands the pack raises a little every session; `deficit` true =
holding the numbers counts as success).
`care_filled` lists the care strands the tool filled at the wrap.
`restart_commit` is set when this wrap swapped a restart's goals in.

## restart ("다시 하자", "처음부터")

```bash
database/kit_life.sh goal-interview --restart --domain health [--from goal|program] --json
```

the ONE path. unfinished interview -> archived (state key renamed, staged
goals archived, direction retired). finished goals stay active until the
new wrap swaps them in one transaction. returns `next` (the first move, the
restart line in front of its question). facts come back as
`confirm_activity`; safety as the records re-check. `--from program`: kit
ledger untouched, `say` = the program line.

## strands (`derive_axes`)

Move carries `source_text` (direction, verbatim) + `metrics[]` (registered
metrics, Korean labels). one `--axis` per strand the user's words name,
named with THEIR word (verb refuses > 12자 / sentence endings):

```bash
database/kit_life.sh axis-add --why-id N --axis "볼륨=skeletal_muscle_kg" \
    --axis "체지방 줄이기=body_fat_pct" --axis "수행=lift_capacity_kg" \
    --axis "기능=@practice" --axis "건강=@care" \
    --picture "볼륨=어깨와 등이 꽉 찬 몸" --picture "체지방 줄이기=복근 선이 보이는 몸" \
    --picture "수행=무거운 무게를 안정적으로 드는 몸" \
    --picture "기능=오래 버티고 빠르게 힘을 내는 몸" --picture "건강=아프지 않고 오래 가는 몸"
```

- `=<metric>` only when a metric in `metrics[]` measures that strand.
- no metric measures it: `=@practice` when about how the body moves (기능,
  연결, 협응; carried as a practice every session keeps), `=@care` when about
  health / not getting hurt (건강; filled by the tool at the wrap). health
  refuses a bare strand (every strand is a metric, @practice or @care --
  `kind_map` on the move). other domains: bare = carried as a direction.
  never invent a metric (verb refuses unregistered).
- one `--picture "<strand name>=<phrase>"` per strand: what the strand
  looks like as a long-term picture, read WITH its qualifier in the user's
  clause (크로스핏터같은 기능성 = work capacity, not balance). plain words,
  noun form, <= 30자, no hype. the kit opens the strand's first move with it
  (missing -> the silent `strand_picture` move asks later).
- the kit keeps the qualifier itself: the clause that names the strand is
  stored with it and the card / moves show `피지크식 x테이퍼` /
  `크로스핏식 기능성` / `필라테스식 연결` (the `--axis` name stays the bare word).
- single-strand direction ("10km 완주") = one `--axis`.
- verb speaks nothing (`say: null`); strands show on next move's card
  ([지향] line). `carried` = that line, logs only.
- `why_label` null on the move -> `why-label-set <id> "<short name>"` first
  (axis-add refuses until it is set).

## card phrase (`--card`)

The card shows YOUR short phrase, never the user's raw sentence: facts only,
noun form, no sentence endings, no hedges ("~한 듯", "..?"). Raw words still
go in `--activity` / `--training` / `--safety` (the record). Examples:
"주 3~4회 하고있어. 헬스장에서" -> `주 3~4회 · 헬스장`; "9년쯤된듯..?" -> `9년`;
knee/shoulder/plantar answer -> `우측 무릎 · 우측 어깨 · 우측 족저근` (+ what
happens when short enough). Already stored without one: re-run the same
note with the same raw words + `--card` (overwrites, no step change).
