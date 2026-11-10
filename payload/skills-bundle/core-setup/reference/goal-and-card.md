# core-setup -- goal stage forks, aspiration, card

Read when `onboard-next` returns `delegate`, a `ladder` fork, a
`why_label_ask`, or when you speak the reflect-back card.

## goal stage (delegated, direction FIRST)

- no goal bound -> `kind: delegate`, `next_cmd: goal-interview --next
  --domain <D> --json` -> run goal-session's move loop. first move =
  direction (owner-approved opening), then user's strands, then ONE goal
  for the current phase. direction on record rides as a `read_confirms` line.
- loop reaches `wrap` -> bind: `onboard-next --goal <id> --domain <D>`.
- onboarding registers ONE goal (current phase: multi-week phase or first
  habit, tool's pick). later phases named in the wrap, filled by weekly
  `goal-graduation`.
- upper goal stands, nothing current -> payload `ladder`:
  - `split: short_offer` -> one worded question ("...지금 할 것부터
    정할까요?"); yes -> run `next_cmd` template (title in owner's words).
  - `split: short_sustained` -> record already proves it; no question key:
    speak `read_confirms`, run `next_cmd` (registers `maintained` from
    MEASURED numbers).

## aspiration line (F13 / F6)

Direction captured first by the move loop (why-add: verbatim text + short
label) -> aspiration resolves as READ; no second line.

Legacy (goal bound, no direction): engine asks the ANCHORED question from
the goal title ("'주 4회 운동', 이걸로 이루고 싶은 건 뭐예요?"). store:
`onboard-set aspiration <owner's words> --label "<짧은 말>"`.
`why_label_ask` (direction stored before labels): fill template with a
phrase from `text`, speak that one line, yes -> run its `cmd`.
direction = no deadline, no metric; lives as the why, never a goal row.

## reflect-back card

`database/kit_life.sh onboard-card --json` -> speak `card_lines` as given.
attribution already computed:
- owner-stated values: just the value, never "제가 잡았습니다".
- proposals the owner approved -> "말씀하신 대로".
- agent-computed only: "제가 잡은 값" + explicitly changeable.

- no week-by-week numbers (F10): settled values + unchanging METHOD
  ("매주 컨디션 보고 다음 주 분량을 정합니다") + at most one feasibility line.
- calendar/notify line already conditional on MIRROR_MODULE: payload says
  notify -> never promise calendar entries.
- FIRST line = aspiration when captured. LAST line = `after_confirm` (F9):
  says what happens after confirming (pack-extended -> consult follows;
  kit-only -> registration only). speak as given; never extend with the next
  step's contents ("종목 5개" with nothing chosen = the F10 defect).
