---
name: health-log
display_name: 인바디 기록
description: >
  Handles InBody body-composition capture: when 사용자 sends an InBody result (a photo
  of an InBody sheet - InBody370S etc. - OR types the values), extract EVERY field on
  the sheet and deterministically save the full panel to the kit_life metric_log
  time-series. Fires on "인바디", "인바디 결과", "인바디 찍었어", "체성분", "체성분 분석",
  "인바디 쟀어", "체지방 쟀어", any InBody-sheet photo, or 사용자 giving body-composition
  numbers (weight / skeletal muscle / body fat / lean mass / segmental / visceral fat /
  BMR / BMI / SMI / WHR / InBody score / protein / mineral / TBW). Also fires on
  body-composition history queries: "인바디 추이", "체성분 변화", "지난 인바디랑 비교",
  "근육량 추이", "체지방 추이", "inbody trend". HARD RULE: every present value is
  persisted via kit_life.sh log-metric - NEVER save "reference/trend only" and skip.
  Also updates the profile weight (config weight_kg) when weight changed. Output =
  concise Korean summary of what was saved + trend delta vs previous measurement.
---

# health-log

InBody result arrives -> extract EVERY field -> write ALL via `kit_life.sh
log-metric` into `metric_log` (shared with meal-log / workout-log).
no raw SQL. cwd = workspace root; never cd into the skill dir. exists
because ad-hoc capture saved 11 / 4 / 0 fields on different days: every
value is longitudinal data.

## key vocabulary (FIXED; exact snake_case, never a synonym)

established: `weight_kg` 체중, `skeletal_muscle_kg` 골격근량, `fat_mass_kg`
체지방량, `body_fat_pct` 체지방률, `lean_mass_kg` 제지방량, `lean_arm_l_kg`
`lean_arm_r_kg` `lean_trunk_kg` `lean_leg_l_kg` `lean_leg_r_kg` 부위별 제지방,
`visceral_fat_level` 내장지방 레벨.

raw panel: `protein_kg` 단백질, `mineral_kg` 무기질, `tbw_l` 체수분, `bmr_kcal`
기초대사량, `bmi`, `smi` 골격근지수, `whr` 복부지방률, `inbody_score` 점수,
`slm_kg` 근육량(soft lean), `fat_arm_l_kg` `fat_arm_r_kg` `fat_trunk_kg`
`fat_leg_l_kg` `fat_leg_r_kg` 부위별 체지방.

segment % of standard (printed under each segment kg, e.g. `9.8kg / 88.5%`):
`lean_arm_l_pct` `lean_arm_r_pct` `lean_trunk_pct` `lean_leg_l_pct`
`lean_leg_r_pct`. save BOTH kg and pct, same date, pct as number only
(`88.5`). `body-segment --json` shows `standard_pct` only when captured the
same day as the kg; the kit never derives a standard.

field not listed -> still capture: consistent snake_case + unit suffix
(`_kg` / `_l` / `_pct` / `_kcal`, bare for level/score/ratio). same name
every time. do not edit this shipped file to add it.

## steps

1. measurement date + time from the sheet header. use THAT date for every
   call (backfill old sheets under their own date). note =
   `InBody<model> <HH:MM>` (e.g. `InBody370S 05:36`); unreadable ->
   `InBody <HH:MM>` or `InBody`. never block the save on it.
2. extract every present field -> key. missing field -> skip that key (no
   null / 0).
3. one call per field (idempotent upsert on (date, metric)):
   `$PROJECT_ROOT/database/kit_life.sh log-metric "<YYYY-MM-DD>" <metric_key> <value> "InBody<model> <HH:MM>"`
   value = number only (`83.4`, not `83.4kg`).
4. profile: read `kit_life.sh body-state`; weight differs ->
   `kit_life.sh config-set weight_kg=<value>`; sheet also gives
   skeletal_muscle_kg / fat_mass_kg / lean_mass_kg -> config-set those too
   (fresh BMR / target math). step 3 always happens regardless.

HARD RULE: never skip a value as "참고용 / 추세용이라 기록 생략". illegible
value -> ASK for that one; save the rest first.

## goal-basis update

`log-metric` output may carry a line `GOAL-BASIS-UPDATE\t<why_id>\t<command>\t<Korean line>`:
a goal was set on a stale InBody and this record is newer. speak the last
field verbatim, once. user says yes -> run the command in field 3.

## report (Korean)

- count saved ("인바디 22개 항목 기록 완료").
- deltas vs PREVIOUS measurement for 체중 / 골격근량 / 체지방률 / 체지방량
  with arrows: `kit_life.sh trend <weight_kg|skeletal_muscle_kg|body_fat_pct|fat_mass_kg> --json`.
- movers only; no raw dump. say 체중 / 골격근량 / 체지방률, never key names
  or verb names.

## trend query

"인바디 추이", "체성분 변화", "지난 인바디랑 비교", "근육량 추이" ->
`kit_life.sh trend <metric> [--window N] [--json]` for the relevant keys,
Korean summary, no write. metric not in the trend registry -> verb lists
the registered ones; say that one has no trend view yet.

## test

`test.sh` (this dir): full fixture panel under throwaway date `2099-01-01`,
verifies count, cleans up.
