---
name: workout-log
display_name: 운동 기록
description: >
  The kit's workout-capture skill: ONE finished workout session per record,
  plus that day's / that week's plain workout query. Session level only --
  kind, minutes, active kcal, heart rate, note.
  (1) Log: fires when the user reports a workout that is DONE. "헬스 40분
  했어", "5km 뛰었어", "PT 받았어", "요가 1시간 했어", "가슴운동 했어",
  "등산 다녀왔어", "크로스핏 했어", "운동 끝 400kcal", "did 40 min at the gym",
  "ran 5km today", "had a PT session" -- any completed-workout utterance with
  exercise kind, duration, calories, distance or heart rate.
  (2) Query: "오늘 운동 뭐 했지", "오늘 얼마나 태웠어", "이번 주 운동 몇 번
  했어", "what workout did I do today", "how many kcal did I burn today" --
  answered as plain text from kit_life.sh workout-find / agg-day / agg-week.
  Does NOT run a live session (starting a session, the next set, a rest
  timer -- "세션 시작", "다음 세트", "타이머" are the health pack's
  workout-session lane), does NOT design a program ("프로그램 짜줘" is
  workout-program), does NOT record or edit individual SETS, and does NOT
  render a card or compute remaining calories (the health pack's diet-status).
  Output: ONE short Korean confirmation, no card.
---

# workout-log

Capture-only, kit side (per-instance copy, `sharing_mode: own`; the DB is
shared). Owner says a workout is done -> ONE `workouts` row (+
classifications) -> one short Korean reply. no card, no coaching.
Sets, session tags, live session, program, cards = health pack; installed
they ADD, absent capture still works and says nothing about them.

All via `$PROJECT_ROOT/database/kit_life.sh`. no raw SQL. cwd = PROJECT_ROOT;
never cd into the skill dir.

## boundary

- this skill = SESSION record (kind, minutes, active kcal, avg_hr, note, date).
- sets / set fixes / abort / session_type tag / live session ("세션 시작",
  "다음 세트", "타이머") -> `workout-session`. program -> `workout-program`.
  card / remaining kcal / coaching -> `diet-status`.
- peer identity + lane edge: `database/crew-agents.md` ("lane-edge rule").
  never an errand, never a time claim.
- two agents may hold this skill; `workout-add` (date, category) refusal is
  the ONLY dedup. invent no other.
- `owning_agent` is stamped by the kit. never pass it, never voice it.

## steps

1. extract (category, subtype), minutes, kcal, avg_hr, extras (distance ->
   note). mapping + kcal estimate: read reference/types.md when the kind is
   not a plain dictionary word or kcal is missing.
2. kcal = ACTIVE only. watch "활동 / 총" -> 활동; total may go in note as
   "총 Nkcal". kit refuses kcal equal to note's 총 when 활동 is also there
   (DGN-724) -> re-run with 활동.
3. `$PROJECT_ROOT/database/kit_life.sh workout-add "<YYYY-MM-DD>" "<category>" "<subtype>" <minutes> <kcal> "<note>" [avg_hr]`
   unknown numbers 0, text "", avg_hr omit. out: `id<TAB>category<TAB>subtype<TAB>kcal<TAB>avg_hr`.
   - several body parts ("가슴+삼두") = ONE workout: add first part, then
     `workout-classify <id> <category> <subtype>` per other part. minutes /
     kcal once, whole session.
   - `EXISTS <n>` + rows (exit 3), nothing written:
     same session (usual: "헬스 끝났어" after the session opened, or a repeat
     mention) -> `workout-upd <id>` with what is new, normal confirm.
     genuinely second session -> same `workout-add` + `--new` (only when the
     words say so; unsure -> ask once). several ids -> pick by utterance or ask.
4. `bash .claude/skills/workout-log/close_task.sh [YYYY-MM-DD]` (after add or
   EXISTS-upd):
   - `CLOSED<TAB><name>` -> add line "Tasks의 '<name>'도 완료처리했어요".
   - `NONE` -> say nothing. `MULTI` + `id<TAB>name` lines -> nothing closed;
     show, ask which.
   script may hand the session end to an installed health engine; never
   mention it.
5. reply, no bold/markdown, address per persona:
   ```
   헬스 40분, 운동 기록 완료
   소모 240kcal
   Tasks의 '헬스'도 완료처리했어요
   ```
   estimated kcal -> "(추정)" at end of kcal line. Tasks line only on CLOSED.

## card order without a card

`workout-add` success fires framework `card-followup` ("render + send the
card"), keyed on the CLI. `.claude/skills/diet-status/card.py` or
`.claude/skills-bundle/diet-status/card.py` exists -> follow it this turn.
neither -> the confirmation IS the answer; never fabricate, narrate or apologise.

## query (plain text, read live)

```
$PROJECT_ROOT/database/kit_life.sh workout-find "<YYYY-MM-DD>"   # id category subtype minutes kcal
$PROJECT_ROOT/database/kit_life.sh agg-day "<YYYY-MM-DD>"        # workout_cnt workout_min burn_kcal (+ intake, balance)
$PROJECT_ROOT/database/kit_life.sh agg-week "<monday>"           # workout_cnt workout_min burn_total (+ prev week diff)
```
- "오늘 운동 뭐 했지" -> one line per session: "근력 가슴, 삼두 40분 240kcal".
- "오늘 얼마나 태웠어" -> `burn_kcal` + `workout_min`. "이번 주 몇 번" ->
  `workout_cnt` / `burn_total`.
- finality (DGN-669): past date `closed=0` -> append `(잠정, HH:MM 기준)`
  (HH:MM = `as_of`); today = "so far", never claim final. `workout_cnt=0` =
  NO RECORDS, never "rest day".
- card / remaining / target asks -> `diet-status` if installed.

## correct / delete (session level)

- `workout-upd <id> field=value ...` (date minutes kcal note avg_hr);
  classification via `workout-classify`.
- `workout-del <id>`: session with per-set rows -> refused (exit 3) with the
  census. never force; relay the printed Korean line; set cleanup = live
  session lane. `--confirm-destroy-sets <n>` only on explicit user OK,
  echoing the exact count.
