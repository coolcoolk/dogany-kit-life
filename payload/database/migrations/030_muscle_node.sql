-- 030_muscle_node.sql  (target user_version 30)
-- reversible: yes -- purely additive plus a symmetric rename. Rollback =
--   DROP VIEW exercise_muscle / exercise_subregion; rename exercise_muscle_map
--   and exercise_subregion_map back to their original names; DROP muscle_node
--   and muscle_label. No pre-existing row is rewritten or deleted, so no user
--   data is lost on the way back.
--
-- Muscle taxonomy: layer / parent / label become DATA.
--
-- WHY. The coarse 6-group axis lived in code -- routines/lib/health_metrics.py
-- MUSCLE_GROUP, whose own comment confessed: "There is no explicit coarse_group
-- column in the taxonomy tables, so fine muscles map by anatomy". The
-- anatomical parent of each sub-region lived in a second hardcoded dict
-- (exercise-enter.CARD_SUBREGION_PARENT), and the Korean labels in three more.
-- Every new grouping question (후면 하체, 밀기/당기기) therefore had to be paid
-- for with another hardcoded dict. This makes the LAYER data so that grouping
-- questions become queries.
--
-- WHAT THIS IS NOT. It does not add a front/back (or push/pull) axis. The owner
-- rejected that as ad-hoc and was right: picking one axis is arbitrary while the
-- layer itself is broken. Fix the layer; axes stay out.
--
-- SHAPE. One recursive node table. `kind` -- not depth -- is the closed-vocab
-- marker, because the tree is deliberately NOT uniform-depth.
--
--   * A COARSE GROUP IS A ROOT, not a node kind above every muscle. Building it
--     the naive way collided on the primary key: MUSCLE_GROUP maps chest->chest,
--     back->back, shoulders->shoulders -- identity mappings meaning "this muscle
--     IS its own coarse group". Only arms / legs / core are genuine aggregates,
--     and those alone are kind='group'. The 6 coarse groups are therefore the 6
--     rows with parent IS NULL. (Two separate dicts in code hid this collision;
--     one table surfaced it immediately.)
--   * 'quads' / 'hamstrings' / 'abs' / 'forearms' appear in exercise_subregion
--     AND in exercise_muscle. They are muscles with no sub-decomposition, so
--     they exist ONCE, as kind='muscle'. exercise_subregion rows referencing
--     them simply point at a muscle node. (This is exactly the "glute_max is a
--     real sub-region but quads is a mid-layer name, and they sit at the same
--     level" mixing the owner flagged.)
--   * 'adductors' has no coarse-muscle parent -- it is never a listed 주동근 --
--     so it attaches directly to the 'legs' group, skipping the muscle level.
--     Previously it was an orphan that mapped to itself.
--
-- band_unit. rx-calc.SUBREGION_GROUP is NOT a parent map -- it is the
-- prescription BANDING bucket, a separate axis that merely coincides with
-- anatomy most of the time (front_delt/side_delt/rear_delt each band
-- independently while all three parent to 'shoulders'). It is carried here as a
-- COLUMN, not collapsed into `parent`. Seeded to reproduce today's runtime
-- exactly, including SUBREGION_GROUP.get(sub, sub): 'abs_lower' has no entry in
-- that dict and so bands as itself rather than into 'abs'. That is a latent
-- inconsistency (the card rolls abs_lower into abs), preserved deliberately --
-- a refactor migration must not change prescription output. Fix it separately.
--
-- COMPATIBILITY. exercise_muscle / exercise_subregion keep their names as
-- VIEWS over renamed base tables, so all 15 existing read sites are untouched.
-- The W-lane seed (W03_exercise_muscle.sql) writes to the base table and is
-- updated in the same landing -- a plain view is not insertable.
--
-- Owner: kit_life lane (DGN-708). Shared DB: the health agent and the life agent both symlink this
-- file, so EXPECTED_USER_VERSION moves to 30 for BOTH instances in lockstep.

BEGIN IMMEDIATE;

CREATE TABLE muscle_node (
    node      TEXT PRIMARY KEY,
    parent    TEXT REFERENCES muscle_node(node),
    kind      TEXT NOT NULL CHECK(kind IN ('group','muscle','subregion')),
    sort      INTEGER NOT NULL,
    band_unit TEXT
);
CREATE INDEX idx_muscle_node_parent ON muscle_node(parent);
CREATE INDEX idx_muscle_node_kind   ON muscle_node(kind, sort);

CREATE TABLE muscle_label (
    node TEXT NOT NULL REFERENCES muscle_node(node),
    lang TEXT NOT NULL,
    form TEXT NOT NULL CHECK(form IN ('long','short')),
    text TEXT NOT NULL,
    PRIMARY KEY (node, lang, form)
);

INSERT INTO muscle_node (node,parent,kind,sort,band_unit) VALUES ('arms',NULL,'group','3',NULL);
INSERT INTO muscle_node (node,parent,kind,sort,band_unit) VALUES ('legs',NULL,'group','4',NULL);
INSERT INTO muscle_node (node,parent,kind,sort,band_unit) VALUES ('core',NULL,'group','5',NULL);
INSERT INTO muscle_node (node,parent,kind,sort,band_unit) VALUES ('chest',NULL,'muscle','0','chest');
INSERT INTO muscle_node (node,parent,kind,sort,band_unit) VALUES ('back',NULL,'muscle','1','back');
INSERT INTO muscle_node (node,parent,kind,sort,band_unit) VALUES ('shoulders',NULL,'muscle','2','shoulders');
INSERT INTO muscle_node (node,parent,kind,sort,band_unit) VALUES ('biceps','arms','muscle','306','biceps');
INSERT INTO muscle_node (node,parent,kind,sort,band_unit) VALUES ('forearms','arms','muscle','307','forearms');
INSERT INTO muscle_node (node,parent,kind,sort,band_unit) VALUES ('triceps','arms','muscle','308','triceps');
INSERT INTO muscle_node (node,parent,kind,sort,band_unit) VALUES ('calves','legs','muscle','409','calves');
INSERT INTO muscle_node (node,parent,kind,sort,band_unit) VALUES ('glutes','legs','muscle','410','glutes');
INSERT INTO muscle_node (node,parent,kind,sort,band_unit) VALUES ('hamstrings','legs','muscle','411','hamstrings');
INSERT INTO muscle_node (node,parent,kind,sort,band_unit) VALUES ('quads','legs','muscle','412','quads');
INSERT INTO muscle_node (node,parent,kind,sort,band_unit) VALUES ('abs','core','muscle','513','abs');
INSERT INTO muscle_node (node,parent,kind,sort,band_unit) VALUES ('abs_lower','abs','subregion','0','abs_lower');
INSERT INTO muscle_node (node,parent,kind,sort,band_unit) VALUES ('adductors','legs','subregion','1','adductors');
INSERT INTO muscle_node (node,parent,kind,sort,band_unit) VALUES ('back_erectors','back','subregion','2','back');
INSERT INTO muscle_node (node,parent,kind,sort,band_unit) VALUES ('back_lats','back','subregion','3','back');
INSERT INTO muscle_node (node,parent,kind,sort,band_unit) VALUES ('back_upper','back','subregion','4','back');
INSERT INTO muscle_node (node,parent,kind,sort,band_unit) VALUES ('biceps_distal','biceps','subregion','5','biceps');
INSERT INTO muscle_node (node,parent,kind,sort,band_unit) VALUES ('biceps_prox','biceps','subregion','6','biceps');
INSERT INTO muscle_node (node,parent,kind,sort,band_unit) VALUES ('calf_gastroc','calves','subregion','7','calves');
INSERT INTO muscle_node (node,parent,kind,sort,band_unit) VALUES ('calf_soleus','calves','subregion','8','calves');
INSERT INTO muscle_node (node,parent,kind,sort,band_unit) VALUES ('chest_lower','chest','subregion','9','chest');
INSERT INTO muscle_node (node,parent,kind,sort,band_unit) VALUES ('chest_mid','chest','subregion','10','chest');
INSERT INTO muscle_node (node,parent,kind,sort,band_unit) VALUES ('chest_upper','chest','subregion','11','chest');
INSERT INTO muscle_node (node,parent,kind,sort,band_unit) VALUES ('front_delt','shoulders','subregion','12','front_delt');
INSERT INTO muscle_node (node,parent,kind,sort,band_unit) VALUES ('glute_max','glutes','subregion','13','glutes');
INSERT INTO muscle_node (node,parent,kind,sort,band_unit) VALUES ('glute_med','glutes','subregion','14','glutes');
INSERT INTO muscle_node (node,parent,kind,sort,band_unit) VALUES ('rear_delt','shoulders','subregion','15','rear_delt');
INSERT INTO muscle_node (node,parent,kind,sort,band_unit) VALUES ('side_delt','shoulders','subregion','16','side_delt');
INSERT INTO muscle_node (node,parent,kind,sort,band_unit) VALUES ('triceps_lat_med','triceps','subregion','17','triceps');
INSERT INTO muscle_node (node,parent,kind,sort,band_unit) VALUES ('triceps_long','triceps','subregion','18','triceps');

INSERT INTO muscle_label (node,lang,form,text) VALUES ('arms','ko','long','팔');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('arms','ko','short','팔');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('legs','ko','long','다리');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('legs','ko','short','다리');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('core','ko','long','코어');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('core','ko','short','코어');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('chest','ko','long','가슴');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('chest','ko','short','가슴');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('back','ko','long','등');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('back','ko','short','등');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('shoulders','ko','long','어깨');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('shoulders','ko','short','어깨');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('biceps','ko','long','이두');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('biceps','ko','short','이두');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('triceps','ko','long','삼두');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('triceps','ko','short','삼두');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('forearms','ko','long','전완');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('forearms','ko','short','전완');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('quads','ko','long','대퇴사두');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('quads','ko','short','사두');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('hamstrings','ko','long','햄스트링');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('hamstrings','ko','short','햄스');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('glutes','ko','long','둔근');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('glutes','ko','short','둔근');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('calves','ko','long','종아리');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('calves','ko','short','종아리');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('abs','ko','long','복근');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('abs','ko','short','복근');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('abs_lower','ko','short','하복');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('adductors','ko','short','내전근');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('back_erectors','ko','short','척추');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('back_lats','ko','short','광배');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('back_upper','ko','short','승모');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('biceps_distal','ko','short','이두하');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('biceps_prox','ko','short','이두상');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('calf_gastroc','ko','short','비복');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('calf_soleus','ko','short','가자미');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('chest_lower','ko','short','하흉');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('chest_mid','ko','short','중흉');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('chest_upper','ko','short','상흉');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('front_delt','ko','short','앞삼각');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('glute_max','ko','short','대둔');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('glute_med','ko','short','중둔');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('rear_delt','ko','short','뒷삼각');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('side_delt','ko','short','옆삼각');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('triceps_lat_med','ko','short','외측두');
INSERT INTO muscle_label (node,lang,form,text) VALUES ('triceps_long','ko','short','장두');

-- ---- compatibility layer -------------------------------------------------
-- Base tables renamed; the old names survive as views. Read sites unchanged.
ALTER TABLE exercise_muscle    RENAME TO exercise_muscle_map;
ALTER TABLE exercise_subregion RENAME TO exercise_subregion_map;

CREATE VIEW exercise_muscle AS
    SELECT exercise_name, muscle, role FROM exercise_muscle_map;
CREATE VIEW exercise_subregion AS
    SELECT exercise_name, subregion, weight, grade FROM exercise_subregion_map;

PRAGMA user_version = 30;

COMMIT;
