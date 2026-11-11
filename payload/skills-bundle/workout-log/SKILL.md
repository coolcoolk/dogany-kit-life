---
name: workout-log
display_name: 운동 기록
description: >-
  Capture ONE finished workout session when the owner reports completed exercise with kind, duration, calories, distance
  or heart-rate evidence, or answer plain day/week workout-history and calorie queries from kit_life.sh workout-find /
  agg-day / agg-week. Capture works independently of module activation. Record session-level kind, minutes, active kcal,
  heart rate and note only. A bare wish to begin exercising or managing workouts, with no completed-workout content and
  module management off, hands to brief-setup first-install.md ## later group C (C1), never a generic workout-log
  question. Does NOT run a live session (starting a session, the next set, a rest timer are the health pack's
  workout-session lane), does NOT design a program (workout-program), does NOT record or edit individual SETS, and does
  NOT render a card or compute remaining calories (the health pack's diet-status). Output: ONE short Korean
  confirmation, no card.
---

Before answering about food, cooking, timing or the rest of today, run
`"${PROJECT_ROOT:-$(pwd)}/database/kit_life.sh" day-now` and use its local clock,
day type, logged meals, next eating occasion and remaining blocks. Never ask
which meal when day-now answers it; an afternoon food request after logged
lunch targets dinner unless the owner explicitly says otherwise. Never infer
attendees from a title; use only stored people/groups. Historical or explicitly
dated requests keep their stated date and meal; day-now supplies current context.


Before record fields, only for start/use intent without actual record content: `database/kit_life.sh manage-state workout` (MANAGE<TAB>workout<TAB>on|off<TAB>reason). Off -> brief-setup first-install.md `## later` C1; on -> normal use. Actual records capture either way.

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
   EXISTS-upd): only the optional session-engine handoff; it settles no
   task. never mention it. then the SETTLE ON EVIDENCE completion check
   (schedule-log/reference/completion-chat.md; `EXISTS` too): compare the
   workout's meaning with today's open tasks, never its category or a title
   pattern. one fulfilled row -> `task-done-today` with
   `--record-evidence "workout-add:<id>"` or `--said`; several -> one
   done-button question; none -> no task-add + done; the workout itself is already recorded by workout-add.
5. reply, no bold/markdown, address per persona:
   - a task settled in step 4 -> its completion line only
     ("헬스 완료로 체크했어요."), nothing else.
   - otherwise:
   ```
   헬스 40분, 운동 기록 완료
   소모 240kcal
   ```
   estimated kcal -> "(추정)" at end of kcal line.

## card after workout-add

output carries a `MEAL_CARD_BEGIN..END` block -> relay it exactly as
`MEAL_CARD_RELAY` says. output / hook context says `CARD_POSTED` -> only the one
short confirm line. never render a card or an image. no block printed -> the
confirmation IS the answer; never fabricate, narrate or apologise.

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
