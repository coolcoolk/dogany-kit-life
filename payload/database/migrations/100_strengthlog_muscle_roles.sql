-- 100_strengthlog_muscle_roles.sql (99 -> 100; rerun at 100 is a no-op)
-- reversible: restore the pre-100 database snapshot. There is no per-row
-- history, so reversing the whitelist later could overwrite subsequent edits.
-- Owner 2026-10-26: primary/secondary follows StrengthLog; back/front/box squat
-- and leg press glutes are primary. This adopts editorial role categories,
-- normalizing deltoids/lower back/obliques to shoulders/back/abs; it does not
-- claim measured activation ratios or change any numeric share/set credit.
-- Input: 2026-10-26-muscle-source-audit.md, 31 role differences + role conflicts.
-- No owner-override marker exists for roles in either table. The map/draft tag
-- records confirmation status, not a role override (056 M2); no source column
-- is added. Only existing rows with the audited old role are updated.
-- exercise_muscle is the read-through compatibility VIEW (030), not a table.
-- Pattern facts from compose_rank_rules.json are included below as conditional
-- movement_muscle_base corrections only. That health-trainer JSON and its seed
-- are absent from this repo; their required follow-up is documented in
-- sot/MUSCLE-ROLE-PROVENANCE.md. No missing row is inserted or inferred.
-- Each whitelist row cites the audit fact(s) and StrengthLog URL:
-- A0029 | movement_muscle_base ball_slam/back secondary -> primary | [S003] https://www.strengthlog.com/ball-slams/
-- A0128 | exercise_muscle_map bench_press/shoulders secondary -> primary | [S013] https://www.strengthlog.com/bench-press/
-- A0166 | exercise_muscle_map box_squat/glutes secondary -> primary | [S017] https://www.strengthlog.com/box-squat/
-- A0342 | exercise_muscle_map deadlift/hamstrings primary -> secondary | [S035] https://www.strengthlog.com/deadlift/
-- A0378 | movement_muscle_base dip/shoulders secondary -> primary | [S006] https://www.strengthlog.com/bar-dip/
-- A0381 | exercise_muscle_map dip/shoulders secondary -> primary | [S006] https://www.strengthlog.com/bar-dip/
-- A0395 | exercise_muscle_map dumbbell_bench_press/shoulders secondary -> primary | [S038] https://www.strengthlog.com/dumbbell-chest-press/
-- A0430 | exercise_muscle_map dumbbell_rdl/glutes secondary -> primary | [S043] https://www.strengthlog.com/dumbbell-romanian-deadlift/
-- A0493 | movement_muscle_base flat_press/shoulders secondary -> primary | [S013] https://www.strengthlog.com/bench-press/
-- A0510 | movement_muscle_base get_up/glutes secondary -> primary | [S143] https://www.strengthlog.com/turkish-get-up/
-- A0511 | movement_muscle_base get_up/shoulders secondary -> primary | [S143] https://www.strengthlog.com/turkish-get-up/
-- A0516 | exercise_muscle_map goblet_squat/glutes secondary -> primary | [S052] https://www.strengthlog.com/goblet-squat/
-- A0528 | exercise_muscle_map good_morning/glutes secondary -> primary | [S053] https://www.strengthlog.com/good-morning/
-- A0556 | movement_muscle_base hinge/hamstrings primary -> secondary | [S035] https://www.strengthlog.com/deadlift/
-- A0557 | movement_muscle_base hinge_bilateral/glutes secondary -> primary | [S043] https://www.strengthlog.com/dumbbell-romanian-deadlift/
-- A0559 | movement_muscle_base hip_abduction/glutes primary -> secondary | [S059] https://www.strengthlog.com/hip-abduction-machine/
-- A0561 | exercise_muscle_map hip_abduction_machine/glutes primary -> secondary | [S059] https://www.strengthlog.com/hip-abduction-machine/
-- A0570 | movement_muscle_base hip_hinge_deadlift/hamstrings primary -> secondary | [S035] https://www.strengthlog.com/deadlift/
-- A0594 | movement_muscle_base horizontal_pull/shoulders secondary -> primary | [S010] https://www.strengthlog.com/barbell-row/
-- A0596 | movement_muscle_base horizontal_push/shoulders secondary -> primary | [S013] https://www.strengthlog.com/bench-press/
-- A0600 | exercise_muscle_map incline_barbell_press/shoulders secondary -> primary | [S065] https://www.strengthlog.com/incline-bench-press/
-- A0626 | exercise_muscle_map incline_dumbbell_press/shoulders secondary -> primary | [S041] https://www.strengthlog.com/dumbbell-incline-press/
-- A0643 | movement_muscle_base incline_press/shoulders secondary -> primary | [S065] https://www.strengthlog.com/incline-bench-press/
-- A0649 | exercise_muscle_map incline_push_up/shoulders secondary -> primary | [S067] https://www.strengthlog.com/incline-push-up/
-- A0819 | exercise_muscle_map medicine_ball_slam/back secondary -> primary | [S003] https://www.strengthlog.com/ball-slams/
-- A0959 | exercise_muscle_map push_up/shoulders secondary -> primary | [S103] https://www.strengthlog.com/push-up/
-- A1078 | exercise_muscle_map side_plank/abs primary -> secondary | [S120] https://www.strengthlog.com/side-plank/
-- A1195 | movement_muscle_base squat/adductors secondary -> primary | [S123] https://www.strengthlog.com/squat/
-- A1234 | exercise_muscle_map sumo_deadlift/hamstrings primary -> secondary | [S137] https://www.strengthlog.com/sumo-deadlift/
-- A1300 | exercise_muscle_map turkish_get_up/glutes secondary -> primary | [S143] https://www.strengthlog.com/turkish-get-up/
-- A1301 | exercise_muscle_map turkish_get_up/shoulders secondary -> primary | [S143] https://www.strengthlog.com/turkish-get-up/
-- A0013 | exercise_muscle_map back_squat/glutes secondary -> primary | [S123] https://www.strengthlog.com/squat/
-- A0501 | exercise_muscle_map front_squat/glutes secondary -> primary | [S050] https://www.strengthlog.com/front-squat/
-- A0769 | exercise_muscle_map leg_press/glutes secondary -> primary | [S080] https://www.strengthlog.com/leg-press/
-- A1193, A1196 | movement_muscle_base squat/glutes secondary -> primary | [S123] https://www.strengthlog.com/squat/

.bail on
.timeout 5000
BEGIN IMMEDIATE;
CREATE TEMP TABLE _guard_100(v);
CREATE TEMP TRIGGER _guard_100_refuse BEFORE INSERT ON _guard_100 BEGIN
 SELECT RAISE(FAIL, 'migration 100 requires user_version 99 or 100');
END;
INSERT INTO _guard_100 SELECT user_version FROM pragma_user_version
 WHERE user_version NOT IN (99,100);
DROP TABLE temp._guard_100;

UPDATE movement_muscle_base SET role='primary'
 WHERE movement='ball_slam' AND muscle='back' AND role='secondary'
   AND (SELECT user_version FROM pragma_user_version)=99;

UPDATE exercise_muscle_map SET role='primary'
 WHERE exercise_name='bench_press' AND muscle='shoulders' AND role='secondary'
   AND (SELECT user_version FROM pragma_user_version)=99;

UPDATE exercise_muscle_map SET role='primary'
 WHERE exercise_name='box_squat' AND muscle='glutes' AND role='secondary'
   AND (SELECT user_version FROM pragma_user_version)=99;

UPDATE exercise_muscle_map SET role='secondary'
 WHERE exercise_name='deadlift' AND muscle='hamstrings' AND role='primary'
   AND (SELECT user_version FROM pragma_user_version)=99;

UPDATE movement_muscle_base SET role='primary'
 WHERE movement='dip' AND muscle='shoulders' AND role='secondary'
   AND (SELECT user_version FROM pragma_user_version)=99;

UPDATE exercise_muscle_map SET role='primary'
 WHERE exercise_name='dip' AND muscle='shoulders' AND role='secondary'
   AND (SELECT user_version FROM pragma_user_version)=99;

UPDATE exercise_muscle_map SET role='primary'
 WHERE exercise_name='dumbbell_bench_press' AND muscle='shoulders' AND role='secondary'
   AND (SELECT user_version FROM pragma_user_version)=99;

UPDATE exercise_muscle_map SET role='primary'
 WHERE exercise_name='dumbbell_rdl' AND muscle='glutes' AND role='secondary'
   AND (SELECT user_version FROM pragma_user_version)=99;

UPDATE movement_muscle_base SET role='primary'
 WHERE movement='flat_press' AND muscle='shoulders' AND role='secondary'
   AND (SELECT user_version FROM pragma_user_version)=99;

UPDATE movement_muscle_base SET role='primary'
 WHERE movement='get_up' AND muscle='glutes' AND role='secondary'
   AND (SELECT user_version FROM pragma_user_version)=99;

UPDATE movement_muscle_base SET role='primary'
 WHERE movement='get_up' AND muscle='shoulders' AND role='secondary'
   AND (SELECT user_version FROM pragma_user_version)=99;

UPDATE exercise_muscle_map SET role='primary'
 WHERE exercise_name='goblet_squat' AND muscle='glutes' AND role='secondary'
   AND (SELECT user_version FROM pragma_user_version)=99;

UPDATE exercise_muscle_map SET role='primary'
 WHERE exercise_name='good_morning' AND muscle='glutes' AND role='secondary'
   AND (SELECT user_version FROM pragma_user_version)=99;

UPDATE movement_muscle_base SET role='secondary'
 WHERE movement='hinge' AND muscle='hamstrings' AND role='primary'
   AND (SELECT user_version FROM pragma_user_version)=99;

UPDATE movement_muscle_base SET role='primary'
 WHERE movement='hinge_bilateral' AND muscle='glutes' AND role='secondary'
   AND (SELECT user_version FROM pragma_user_version)=99;

UPDATE movement_muscle_base SET role='secondary'
 WHERE movement='hip_abduction' AND muscle='glutes' AND role='primary'
   AND (SELECT user_version FROM pragma_user_version)=99;

UPDATE exercise_muscle_map SET role='secondary'
 WHERE exercise_name='hip_abduction_machine' AND muscle='glutes' AND role='primary'
   AND (SELECT user_version FROM pragma_user_version)=99;

UPDATE movement_muscle_base SET role='secondary'
 WHERE movement='hip_hinge_deadlift' AND muscle='hamstrings' AND role='primary'
   AND (SELECT user_version FROM pragma_user_version)=99;

UPDATE movement_muscle_base SET role='primary'
 WHERE movement='horizontal_pull' AND muscle='shoulders' AND role='secondary'
   AND (SELECT user_version FROM pragma_user_version)=99;

UPDATE movement_muscle_base SET role='primary'
 WHERE movement='horizontal_push' AND muscle='shoulders' AND role='secondary'
   AND (SELECT user_version FROM pragma_user_version)=99;

UPDATE exercise_muscle_map SET role='primary'
 WHERE exercise_name='incline_barbell_press' AND muscle='shoulders' AND role='secondary'
   AND (SELECT user_version FROM pragma_user_version)=99;

UPDATE exercise_muscle_map SET role='primary'
 WHERE exercise_name='incline_dumbbell_press' AND muscle='shoulders' AND role='secondary'
   AND (SELECT user_version FROM pragma_user_version)=99;

UPDATE movement_muscle_base SET role='primary'
 WHERE movement='incline_press' AND muscle='shoulders' AND role='secondary'
   AND (SELECT user_version FROM pragma_user_version)=99;

UPDATE exercise_muscle_map SET role='primary'
 WHERE exercise_name='incline_push_up' AND muscle='shoulders' AND role='secondary'
   AND (SELECT user_version FROM pragma_user_version)=99;

UPDATE exercise_muscle_map SET role='primary'
 WHERE exercise_name='medicine_ball_slam' AND muscle='back' AND role='secondary'
   AND (SELECT user_version FROM pragma_user_version)=99;

UPDATE exercise_muscle_map SET role='primary'
 WHERE exercise_name='push_up' AND muscle='shoulders' AND role='secondary'
   AND (SELECT user_version FROM pragma_user_version)=99;

UPDATE exercise_muscle_map SET role='secondary'
 WHERE exercise_name='side_plank' AND muscle='abs' AND role='primary'
   AND (SELECT user_version FROM pragma_user_version)=99;

UPDATE movement_muscle_base SET role='primary'
 WHERE movement='squat' AND muscle='adductors' AND role='secondary'
   AND (SELECT user_version FROM pragma_user_version)=99;

UPDATE exercise_muscle_map SET role='secondary'
 WHERE exercise_name='sumo_deadlift' AND muscle='hamstrings' AND role='primary'
   AND (SELECT user_version FROM pragma_user_version)=99;

UPDATE exercise_muscle_map SET role='primary'
 WHERE exercise_name='turkish_get_up' AND muscle='glutes' AND role='secondary'
   AND (SELECT user_version FROM pragma_user_version)=99;

UPDATE exercise_muscle_map SET role='primary'
 WHERE exercise_name='turkish_get_up' AND muscle='shoulders' AND role='secondary'
   AND (SELECT user_version FROM pragma_user_version)=99;

UPDATE exercise_muscle_map SET role='primary'
 WHERE exercise_name='back_squat' AND muscle='glutes' AND role='secondary'
   AND (SELECT user_version FROM pragma_user_version)=99;

UPDATE exercise_muscle_map SET role='primary'
 WHERE exercise_name='front_squat' AND muscle='glutes' AND role='secondary'
   AND (SELECT user_version FROM pragma_user_version)=99;

UPDATE exercise_muscle_map SET role='primary'
 WHERE exercise_name='leg_press' AND muscle='glutes' AND role='secondary'
   AND (SELECT user_version FROM pragma_user_version)=99;

UPDATE movement_muscle_base SET role='primary'
 WHERE movement='squat' AND muscle='glutes' AND role='secondary'
   AND (SELECT user_version FROM pragma_user_version)=99;

PRAGMA user_version = 100;
COMMIT;
