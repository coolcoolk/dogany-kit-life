# goal-session: the onboarding card

Read when a rendered card looks off, or the user asks about a card slot.
Tool renders it (`_move_card`, kit_life.py); this describes it, never redraw.

- `card` field = the card. it opens `readback`, except on the first
  consult's `ask_direction`, where `readback[0]` is the `intro` lines and
  the card is `readback[1]` (speak readback in order). also on wrap; on the
  `--start` fork it opens `question` (spoken as is). quote block,
  three sections:
  - `**[지향]**`, then one `• strand` line per strand
  - `**[현재]**`: • 운동 / • 몸 / • 주의
  - `**[목표]**`: • 기한 / • 순서 / • 항목 (one "　–" line per strand) / • 측정
- filled from what is stored, `ㆍ` where still open, slot asked now in bold.
- bullets: "•" top, "　–" sub. never "-".
- just-filled slot = confirmation of last answer.
- strand lines by state: number range, "지키기" (maintain / hold), "첫 측정
  뒤" (measure_first), "운동마다 코어·소근육 보강 1개" (practice), "방향으로
  가져가기" (bare strand).
