-- kit_life unified schema.sql -- FRESH-MINT at user_version 23 (event->timeblock).
-- Generated from the v11 shared baseline + migrations 012..023 (.dump) so a
-- fresh install is BYTE-IDENTICAL to a migrated DB (compat-lint C3 parity):
--   fresh(schema.sql)  ==  migrated(baseline v11 + 012..023).
-- 023 renames the core table event -> timeblock (table + all table-refs, view
--   body repointed, event-table indexes -> idx_timeblock_*). COLUMN names
--   (event_id FKs, event_ulid, derived_from, placement_depends_on) are KEPT,
--   as are FK-table names (sub_event, event_persons) and the view NAME
--   (v_event_resolution). anchor_type='event' stays a semantic enum value.
-- Includes reference-table seed data (exercise/*, grip_*, ledger_guard,
-- ax_projection_rule, etc.).
-- Do NOT hand-edit: regenerate from migrations if the lineage changes.
-- PRAGMA user_version stamp is the LAST line of this file (== 117).

PRAGMA foreign_keys=OFF;
BEGIN TRANSACTION;
CREATE TABLE areas (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL UNIQUE,               -- 영역이름 (신체건강, 식습관…)
  domain      TEXT NOT NULL,                      -- 건강/재무/관계/일/취미/관리/외모/반려동물
  description TEXT,
  notion_id   TEXT,
  created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE workout_types (
  id       INTEGER PRIMARY KEY,
  category TEXT NOT NULL,   -- 대분류
  subtype  TEXT NOT NULL,   -- 세부분류
  sort     INTEGER NOT NULL DEFAULT 0,
  active   INTEGER NOT NULL DEFAULT 1, muscle_node TEXT REFERENCES muscle_node(node),
  UNIQUE(category, subtype)
);
CREATE TABLE intimacy_levels (
  id           INTEGER PRIMARY KEY,
  name         TEXT NOT NULL,            -- e.g. "5(편한친구)"
  cycle_months REAL,                     -- 권장 만남 주기(개월)
  criteria     TEXT,                     -- 기준
  notion_id    TEXT UNIQUE
);
CREATE TABLE persons (
  id              INTEGER PRIMARY KEY,
  name            TEXT NOT NULL,
  relation        TEXT,                  -- 관계 select (친구/비즈니스/연인…)
  intimacy_id     INTEGER REFERENCES intimacy_levels(id),
  birthday        TEXT,                  -- YYYY-MM-DD
  residence       TEXT,
  contact         TEXT,
  job             TEXT,                  -- 하는일
  mbti            TEXT,                  -- comma-joined multi_select
  groups          TEXT,                  -- 같이 속한 그룹, comma-joined
  manual_priority REAL,
  notion_id       TEXT,
  created_at      TEXT NOT NULL DEFAULT (datetime('now','localtime'))
, aliases TEXT, soge_pool INTEGER NOT NULL DEFAULT 0, soge_photo TEXT, soge_intro TEXT, soge_want_must TEXT, soge_want_opt TEXT, gender TEXT, display_name TEXT);
CREATE TABLE workout_classifications (
  workout_id INTEGER NOT NULL REFERENCES workouts(id) ON DELETE CASCADE,
  type_id    INTEGER NOT NULL REFERENCES workout_types(id),
  PRIMARY KEY (workout_id, type_id)
);
CREATE TABLE config (
  key TEXT PRIMARY KEY, value TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
-- timeblock: since 036 the storage atom is `block` (v3.2 execution
-- axis); the old vocabulary survives as the read-only compat VIEW named
-- timeblock at the end of this file.

CREATE TABLE sub_event (
    id           INTEGER PRIMARY KEY,
    ulid         TEXT NOT NULL UNIQUE,
    -- same-DB FK is INTEGER id (G9 rule: only cross-agent/host refs must be ulid).
    event_id     INTEGER NOT NULL REFERENCES block(id),
    owning_agent TEXT NOT NULL,
    kind         TEXT,
    ref          TEXT,                            -- L2 domain row ulid (e.g. workout session)
    done         INTEGER NOT NULL DEFAULT 0,
    tombstone    INTEGER NOT NULL DEFAULT 0,      -- delete != complete; invisible to derivation
    created_at   TEXT,
    settled_at   TEXT,
    CHECK (done IN (0,1)),
    CHECK (tombstone IN (0,1))
);
CREATE TABLE reschedule_requests (
    id              INTEGER PRIMARY KEY,
    ulid            TEXT NOT NULL UNIQUE,
    event_ulid      TEXT NOT NULL,                -- cross-agent ref = ulid
    requester_agent TEXT NOT NULL,
    proposed_start  TEXT,
    proposed_end    TEXT,
    reason          TEXT,
    status          TEXT NOT NULL DEFAULT 'queued',
    created_at      TEXT,
    resolved_at     TEXT,
    CHECK (status IN ('queued','claimed','applied','rejected','expired'))
);
CREATE TABLE event_persons (
    event_id  INTEGER NOT NULL REFERENCES block(id)   ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
    PRIMARY KEY (event_id, person_id)
);
CREATE TABLE projects (
    id         INTEGER PRIMARY KEY,
    ulid       TEXT NOT NULL UNIQUE,
    title      TEXT NOT NULL,
    status     TEXT,
    start_date TEXT,
    end_date   TEXT,
    note       TEXT,
    area_id    INTEGER REFERENCES areas(id),
    notion_id  TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE roller_log (
    id INTEGER PRIMARY KEY,
    ts TEXT NOT NULL,
    recurrence_id TEXT,
    category TEXT NOT NULL,
    detail TEXT
);
CREATE TABLE routine (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    style TEXT,
    goal_mode TEXT,
    start_date TEXT,
    end_date TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    note TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE session_type (
    id INTEGER PRIMARY KEY,
    routine_id INTEGER NOT NULL REFERENCES routine(id),
    code TEXT NOT NULL,
    display_name TEXT,
    sort INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    UNIQUE(routine_id, code)
);
CREATE TABLE session_muscle (
    session_type_id INTEGER NOT NULL REFERENCES session_type(id),
    type_id INTEGER NOT NULL REFERENCES workout_types(id),
    role TEXT NOT NULL,
    fatigue INTEGER NOT NULL DEFAULT 2,
    PRIMARY KEY(session_type_id, type_id)
);
CREATE TABLE travel_rule (
    id           INTEGER PRIMARY KEY,
    ulid         TEXT NOT NULL UNIQUE,
    anchor_type  TEXT NOT NULL,            -- 'def' (routine_def) | 'event'
    anchor_ulid  TEXT NOT NULL,            -- routine_def.ulid or event.ulid
    place_id     INTEGER REFERENCES place(id),   -- nullable
    before_min   INTEGER NOT NULL DEFAULT 0,     -- 0 = no before block
    after_min    INTEGER NOT NULL DEFAULT 0,     -- 0 = no after block
    prep_min     INTEGER NOT NULL DEFAULT 0,     -- prep before departure
                                                 -- (2.6; before block only)
    before_src   TEXT NOT NULL DEFAULT 'explicit',  -- 'place' | 'explicit'
    after_src    TEXT NOT NULL DEFAULT 'explicit',  -- (2.3 propagation)
    prep_src     TEXT NOT NULL DEFAULT 'explicit',
    status       TEXT NOT NULL DEFAULT 'active',
                 -- 'proposed' = deferred offer carrier (2.5, derives
                 --   nothing) | 'active' | 'retired'
    version      INTEGER NOT NULL DEFAULT 0,        -- CAS
    created_by   TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    CHECK (anchor_type IN ('def','event')),
    CHECK (status IN ('proposed','active','retired')),
    CHECK (before_src IN ('place','explicit','provisional')),
    CHECK (after_src IN ('place','explicit','provisional')),
    CHECK (prep_src IN ('place','explicit','provisional')),
    CHECK (place_id IS NOT NULL
           OR (before_src IN ('explicit','provisional')
               AND after_src IN ('explicit','provisional')
               AND prep_src IN ('explicit','provisional'))),
    CHECK (before_min >= 0 AND after_min >= 0 AND prep_min >= 0),
    CHECK (before_min % 15 = 0 AND after_min % 15 = 0
           AND prep_min % 15 = 0),
    CHECK (status <> 'active' OR before_min > 0 OR after_min > 0)
           -- proposed may carry zero minutes (unknown place, 2.5);
           -- active must derive at least one block; prep alone does
           -- not make a rule active (prep rides the before block)
);
CREATE TABLE place (
    id           INTEGER PRIMARY KEY,
    ulid         TEXT NOT NULL UNIQUE,
    name         TEXT NOT NULL,            -- canonical user-language name (mirror reads this)
    aliases      TEXT,                     -- JSON array, location-text match
    to_min       INTEGER,                  -- DEAD on v3.2 (single home: measure); kept for legacy INSERT shape
    from_min     INTEGER,                  -- DEAD on v3.2
    prep_min     INTEGER,                  -- DEAD on v3.2
    note         TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    is_base      INTEGER NOT NULL DEFAULT 0,
    granularity  TEXT,
    display_name TEXT,
    address      TEXT,
    -- v3.2 (036): ASCII stable key; the human string lives in label.
    token        TEXT NOT NULL DEFAULT '',
    -- v3.2 (036): disappearance is status, never DELETE (the dividing axis
    -- has soemyeol, not completion; past blocks / ax_machine.gym_id must
    -- keep resolving).
    status       TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','gone')),
    CHECK (to_min IS NULL OR to_min % 15 = 0),
    CHECK (from_min IS NULL OR from_min % 15 = 0),
    CHECK (prep_min IS NULL OR (prep_min >= 0 AND prep_min % 15 = 0))
);
-- 035: this positional seed INSERT is arity-locked to the column list above.
-- Adding a column to `place` REQUIRES adding a value here in the same edit --
-- the trailing NULL is address.
INSERT INTO place VALUES(1,'835194D0DCF31DFDE82F7FD2E0','집',NULL,NULL,NULL,NULL,NULL,'2026-08-09T03:53:49Z','2026-08-09T03:53:49Z',1,NULL,NULL,NULL,'place_1','active');
-- NEW (024): shared-core person-group display lookup keyed by group name.
-- v1 = display-lookup only; membership stays in persons.groups (comma-list).
-- R2 DEBT: normalize persons.groups -> a person_group_member junction FK.
CREATE TABLE person_group (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE,
    display_name TEXT,
    owning_agent TEXT NOT NULL DEFAULT 'ag',
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
, aliases TEXT, status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','gone')), emoji TEXT);
-- 115: nullable group emoji, display default 👥; membership stays in persons.groups.
-- 073: aliases (JSON array, place.aliases shape) + status (active|gone = retired,
-- history kept).  Participation: event_group (end of file).
-- DGN-623: one-way movement duration for an ORDERED place pair (spatial
-- awareness restore). See migrations/021_travel_edge.sql for rationale.
-- travel_edge: since 036 travel numbers live as measure rows on
-- place_link pairs; the old vocabulary survives as the read-only compat
-- VIEW named travel_edge at the end of this file.


CREATE TABLE spend_category (
    id               INTEGER PRIMARY KEY,
    name             TEXT NOT NULL UNIQUE,
                     -- 032: stable ASCII key (machine). The user-facing
                     -- label lives in display_name; every human render
                     -- projects COALESCE(display_name, name).
    is_fixed_default INTEGER NOT NULL DEFAULT 0,
                     -- category-level fixed-cost default; spend-add copies it
                     -- onto the row unless caller overrides (grill r1 M2)
    sort             INTEGER NOT NULL DEFAULT 0,
    active           INTEGER NOT NULL DEFAULT 1,
    kind             TEXT NOT NULL DEFAULT 'expense', display_name TEXT,  -- 'expense'|'income' (011)
    CHECK (is_fixed_default IN (0,1))
);
-- 032 seed reconciliation. This seed had drifted away from the live table:
-- schema.sql shipped 17 rows (식비/교통/구독/통신/급여 ...) that no live database
-- has ever held, while the live table carries a different 11-row set. A fresh
-- mint was therefore starting from a vocabulary the product does not use.
-- The live set is the truth, so the seed is rewritten to it, tokenized:
-- name = ASCII key, display_name = the label the owner sees.
-- The dropped rows (subscription/telecom/clothing/gift and ALL five income
-- categories) and the added rows (learning/travel/relationship) are itemised
-- in the 032a spend-category landing notes. Two consequences are
-- deliberate and recorded there, not accidents of copying: this seed no longer
-- ships any kind='income' category (live has none -- live income rows are
-- filed under 'etc'), and is_fixed_default is 0 everywhere (live has no
-- category-level fixed default).
INSERT INTO spend_category VALUES(1,'leisure',0,0,1,'expense','콘텐츠/취미');
INSERT INTO spend_category VALUES(2,'learning',0,0,1,'expense','학습');
INSERT INTO spend_category VALUES(3,'household',0,0,1,'expense','생활용품');
INSERT INTO spend_category VALUES(4,'housing',0,0,1,'expense','주거/인프라');
INSERT INTO spend_category VALUES(5,'etc',0,0,1,'expense','기타');
INSERT INTO spend_category VALUES(6,'health',0,0,1,'expense','건강');
INSERT INTO spend_category VALUES(7,'food',0,0,1,'expense','식생활');
INSERT INTO spend_category VALUES(8,'cafe_snack',0,0,1,'expense','카페/간식');
INSERT INTO spend_category VALUES(9,'transport',0,0,1,'expense','이동');
INSERT INTO spend_category VALUES(10,'travel',0,0,1,'expense','여행');
INSERT INTO spend_category VALUES(11,'relationship',0,0,1,'expense','관계');
CREATE TABLE spend_entry (
    id          INTEGER PRIMARY KEY,
    date        TEXT NOT NULL,          -- YYYY-MM-DD (KST), kit_life convention
    amount      INTEGER NOT NULL,       -- KRW; expense > 0, refund/cancel < 0
    name        TEXT NOT NULL,          -- item description, user language
    category_id INTEGER REFERENCES spend_category(id),
    method      TEXT,                   -- free text v1: card/account/cash label
    is_fixed    INTEGER NOT NULL DEFAULT 0,  -- row value; default copied from
                                             -- category is_fixed_default
    scope       TEXT NOT NULL DEFAULT 'personal',
                -- 'personal' | 'business' | 'unknown'
                -- business rows: recorded, EXCLUDED from personal reports,
                -- exportable when bizkit (DGN-464) lands (boundary = OQ 1).
                -- 'unknown' rows are swept at every C2 session (3.4).
    source      TEXT NOT NULL DEFAULT 'chat',
                -- v1 values: 'chat' | 'batch'. NO CHECK by design: sqlite
                -- cannot ALTER a CHECK, and v2 adapters add 'import'/'receipt'
                -- without a table rebuild. SDK/verb layer enforces the value
                -- set (007/008 "SDK enforces" precedent). (grill r1 m3)
    note        TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    kind        TEXT NOT NULL DEFAULT 'expense', class TEXT
    CHECK (class IS NULL OR class IN ('spend','income','saving','saving_back',
    'loan','own_transfer','topup','card_bill','pass_through',
    'meal_point')),
    pair_id INTEGER,
    import_key TEXT
    CHECK (import_key IS NULL OR TRIM(import_key) <> ''),
    stream_id INTEGER,
    at_time TEXT
    CHECK (at_time IS NULL OR at_time GLOB '[0-2][0-9]:[0-5][0-9]'),
    src_cat TEXT,
                -- 'expense' | 'income' (011). Income rows: amount POSITIVE.
                -- NO CHECK (source precedent); SDK/verb layer enforces.
    CHECK (scope IN ('personal','business','unknown')),
    CHECK (is_fixed IN (0,1))
);
CREATE TABLE persons_relations (
  id            INTEGER PRIMARY KEY,
  person_a      INTEGER NOT NULL REFERENCES persons(id),
  person_b      INTEGER NOT NULL REFERENCES persons(id),
  relation_type TEXT NOT NULL DEFAULT 'acquaintance',
  note          TEXT,
  created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  CHECK (person_a < person_b)
);
CREATE TABLE pay_method (
    id                INTEGER PRIMARY KEY,
    name              TEXT NOT NULL UNIQUE,
    kind              TEXT NOT NULL,
    aliases           TEXT,
    billing_day       INTEGER,
    linked_account_id INTEGER REFERENCES pay_method(id),
    created_at        TEXT NOT NULL DEFAULT (datetime('now','localtime'))
, layer TEXT
    CHECK (layer IS NULL OR layer IN ('account','card','wallet')), canonical_id INTEGER, scope_default TEXT NOT NULL DEFAULT 'personal'
    CHECK (scope_default IN ('personal','business')), dust INTEGER NOT NULL DEFAULT 0
    CHECK (dust IN (0,1)), purpose_tags TEXT
 CHECK (purpose_tags IS NULL OR CASE WHEN json_valid(purpose_tags)
 THEN json_type(purpose_tags)='array' AND json_array_length(purpose_tags)>0 ELSE 0 END),
 purpose_memo TEXT, purpose_origin TEXT
 CHECK (purpose_origin IS NULL OR purpose_origin IN ('owner-stated','learned','user')));
CREATE TABLE fixed_cost (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    amount      INTEGER NOT NULL,
    category_id INTEGER REFERENCES spend_category(id),
    next_due    TEXT,
    cycle       TEXT NOT NULL DEFAULT 'monthly',
    pay_mode    TEXT,
    method_id   INTEGER REFERENCES pay_method(id),
    direction   TEXT NOT NULL DEFAULT 'expense',
    auto_entry  INTEGER NOT NULL DEFAULT 0,
    source      TEXT NOT NULL DEFAULT 'interview',
    created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    class TEXT
        CHECK (class IS NULL OR class IN ('income','housing','loan','insurance',
        'utility','telecom','subscription','saving','tax','other')),
    current_amount INTEGER,
    last_seen TEXT,
    expected_next TEXT,
    state TEXT NOT NULL DEFAULT 'active'
        CHECK (state IN ('active','late','changed','stop_suspected','stopped')),
    changed_from INTEGER,
    changed_at TEXT,
    keep_class TEXT
        CHECK (keep_class IS NULL OR keep_class IN ('must','reducible','droppable')),
    keep_by TEXT
        CHECK (keep_by IS NULL OR keep_by IN ('engine','owner')),
    stopped_at TEXT,
    stopped_by TEXT
        CHECK ((stopped_by IS NULL OR stopped_by = 'owner')
        AND ((stopped_by IS NOT NULL) = (state = 'stopped'))), pay_mode_source TEXT
 CHECK (pay_mode_source IS NULL OR pay_mode_source IN ('inferred','owner')),
    CHECK (auto_entry IN (0,1))
);
CREATE TABLE ledger_guard (
    guard_key   TEXT NOT NULL,
    scope       TEXT NOT NULL DEFAULT 'global',
    value       INTEGER,
    unit        TEXT,
    enforcement TEXT NOT NULL,
    inviolable  INTEGER NOT NULL DEFAULT 0,
    source      TEXT,
    updated_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    PRIMARY KEY (guard_key, scope),
    CHECK (inviolable IN (0,1)),
    CHECK (enforcement IN ('block','hold','inform'))
);
INSERT INTO ledger_guard VALUES('auto_entry_enabled','global',0,'flag','block',0,'seed','2026-08-09 12:53:49');
INSERT INTO ledger_guard VALUES('entry_amount_ceiling','global',3000000,'KRW','hold',0,'seed-fallback','2026-08-09 12:53:49');
INSERT INTO ledger_guard VALUES('dup_window','global',NULL,'count','hold',0,'seed','2026-08-09 12:53:49');
INSERT INTO ledger_guard VALUES('budget_threshold','global',NULL,'KRW','inform',0,'seed','2026-08-09 12:53:49');
INSERT INTO ledger_guard VALUES('split_aggregation','global',0,'flag','inform',0,'seed','2026-08-09 12:53:49');
CREATE TABLE ledger_goal (
    id           INTEGER PRIMARY KEY,
    ulid         TEXT NOT NULL UNIQUE,
    layer        TEXT NOT NULL,
    parent_id    INTEGER REFERENCES ledger_goal(id),
    title        TEXT NOT NULL,
    detail       TEXT,
    status       TEXT NOT NULL DEFAULT 'proposed',
    starts_on    TEXT,
    ends_on      TEXT,
    resume_to_id INTEGER REFERENCES ledger_goal(id),
    source       TEXT NOT NULL,
    recorded_at  TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    version      INTEGER NOT NULL DEFAULT 0,
    -- NEW (028 goal-engine): the measurable-rung shape. All NULLABLE except
    -- focus_state -- a goal may legitimately have no metric yet. `rung_state`
    -- carries the current rung; the readiness gate refuses `levelup_eligible`
    -- while that rung is unmet. `focus_state` supersedes the old
    -- single-active-per-layer partial UNIQUE index (idx_goal_active_layer,
    -- DROPped by 028): focus is now an explicit column, not an index invariant.
    domain          TEXT,
    metric          TEXT,
    baseline_value  REAL,
    baseline_as_of  TEXT,
    target_value    REAL,
    target_dir      TEXT,
    horizon_date    TEXT,
    rung_state      TEXT,
    why_id          INTEGER REFERENCES life_why(id),
    owning_agent    TEXT,
    focus_state     TEXT NOT NULL DEFAULT 'backlog',
    priority_rank   INTEGER,
    -- NEW (031 commitment_kind): the SHAPE discriminator. `goal` has an
    -- absorbing state (an achievement predicate that retires the row); `rule`
    -- does not -- it is a standing condition evaluated per `rule_period`, and
    -- satisfying it this period does not consume it. DEFAULT 'goal' is what
    -- the ALTER-based migration backfills existing rows to. `rule_period` is
    -- NULL for goals. The two CHECKs below are fresh-mint-only: SQLite cannot
    -- ALTER ADD CONSTRAINT, so a MIGRATED db enforces the same enums app-side
    -- (_COMMITMENT_KINDS / _RULE_PERIODS in kit_life.py).
    commitment_kind TEXT NOT NULL DEFAULT 'goal',
    rule_period     TEXT,
    CHECK (layer IN ('long','mid','short','event_overlay')),
    CHECK (layer != 'event_overlay' OR ends_on IS NOT NULL),
    CHECK (layer != 'event_overlay' OR resume_to_id IS NOT NULL),
    CHECK (status IN ('proposed','active','suspended','completed',
                      'failed','superseded','archived')),
    CHECK (commitment_kind IN ('goal','rule')),
    CHECK (rule_period IS NULL OR rule_period IN ('day','week','month'))
);
CREATE TABLE ledger_preference (
    id          INTEGER PRIMARY KEY,
    ulid        TEXT NOT NULL UNIQUE,
    item        TEXT NOT NULL,
    polarity    TEXT NOT NULL,
    note        TEXT,
    status      TEXT NOT NULL DEFAULT 'active',
    source      TEXT NOT NULL,
    recorded_at TEXT NOT NULL, topic TEXT
    CHECK (topic IS NULL OR TRIM(topic) <> ''), detail TEXT
    CHECK (detail IS NULL OR (json_valid(detail)
                              AND json_type(detail) = 'object')),
    CHECK (polarity IN ('inviolable','negotiable'))
);
-- 074: topic / detail (preference-add) -- the text above is exactly what
-- 074's ADD COLUMN produces on a migrated database (parity).
CREATE TABLE ledger_resource (
    id          INTEGER PRIMARY KEY,
    ulid        TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL,
    name        TEXT NOT NULL,
    detail      TEXT,
    volatile    INTEGER NOT NULL DEFAULT 0,
    as_of       TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active',
    source      TEXT NOT NULL,
    recorded_at TEXT NOT NULL
, place_id INTEGER REFERENCES place(id));
-- 074: place_id = the kit place an env resource is (resource-add --kind env
-- / resource-place-link); ADD COLUMN text, parity with the migrated chain.
CREATE TABLE ledger_constraint (
    id          INTEGER PRIMARY KEY,
    ulid        TEXT NOT NULL UNIQUE,
    class       TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    goal_id     INTEGER REFERENCES ledger_goal(id),
    inviolable  INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'active',
    source      TEXT NOT NULL,
    recorded_at TEXT NOT NULL, care TEXT
    CHECK (care IS NULL OR (class = 'safety' AND json_valid(care)
                            AND json_type(care) = 'array')),
    CHECK (class IN ('nutrition','safety','time')),
    CHECK (inviolable IN (0,1))
);
-- 074: care = the structured drills for a safety site (constraint-care);
-- ADD COLUMN text, parity with the migrated chain.
CREATE TABLE ledger_audit (
    id        INTEGER PRIMARY KEY,
    ts        TEXT NOT NULL,
    kind      TEXT NOT NULL,
    goal_ulid TEXT,
    detail    TEXT
);
CREATE TABLE workout_sets (
    id            INTEGER PRIMARY KEY,
    session_id    INTEGER NOT NULL REFERENCES workouts(id) ON DELETE CASCADE,
    exercise_name TEXT NOT NULL,
    slot_num      INTEGER,
    set_seq       INTEGER NOT NULL,
    weight_kg     REAL,
    reps          INTEGER,
    rir           INTEGER,
    raw_input     TEXT,
    recorded_at   TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    -- migration 034 (user_version 34): every performed unit is a row here, and
    -- the KIND of unit is a column, not a place in the code. Tokens are ASCII;
    -- the Korean the owner sees is rendered, never stored.
    set_kind      TEXT NOT NULL DEFAULT 'working'
                  CHECK (set_kind IN ('prep','ramp','working','hold','post')),
    target_reps   INTEGER,          -- prescribed rep target FOR THIS LOAD; NULL = none of record
    hold_sec      INTEGER, drill_code TEXT, machine_ref TEXT,          -- `hold` sets only
    UNIQUE(session_id, exercise_name, set_seq)
);
-- 061 (user_version 61): slot_num NULL = a SESSION-level row (no program
-- slot); reps NULL = a prep/ramp done-tap (target_reps keeps the
-- prescription; working/hold still need reps -- trigger below); drill_code =
-- the prep.yaml drill a prep row performed (the key its observed load is read
-- back by).  The column text above is exactly what 061's ADD COLUMN + text
-- swap produce on a migrated database (parity).  WHY in migrations/061.
CREATE INDEX idx_workout_sets_drill
    ON workout_sets(drill_code, recorded_at) WHERE drill_code IS NOT NULL;
CREATE TRIGGER trg_workout_sets_reps_counted
BEFORE INSERT ON workout_sets
WHEN NEW.reps IS NULL AND NEW.set_kind NOT IN ('prep','ramp')
BEGIN
  SELECT RAISE(ABORT, 'workout_sets refused: reps is required for a working/hold set -- only a prep or ramp row may be a done-tap (reps NULL, target_reps = the prescription)');
END;
CREATE TRIGGER trg_workout_sets_reps_counted_upd
BEFORE UPDATE OF reps, set_kind ON workout_sets
WHEN NEW.reps IS NULL AND NEW.set_kind NOT IN ('prep','ramp')
BEGIN
  SELECT RAISE(ABORT, 'workout_sets refused: reps is required for a working/hold set -- only a prep or ramp row may be a done-tap (reps NULL, target_reps = the prescription)');
END;
-- 062 (user_version 62): machine_ref = the ax_machine a set was done on
-- (NULL = not recorded; the exercise's bound machine stays the default).
-- Plain TEXT + triggers, not REFERENCES: the pack's writers run with
-- foreign_keys off.  No machine is seeded (owner 2026-09-30: captured in
-- conversation).  WHY in migrations/062.
CREATE INDEX idx_workout_sets_machine
    ON workout_sets(machine_ref, recorded_at) WHERE machine_ref IS NOT NULL;
CREATE TRIGGER trg_workout_sets_machine_known
BEFORE INSERT ON workout_sets
WHEN NEW.machine_ref IS NOT NULL
 AND NOT EXISTS (SELECT 1 FROM ax_machine WHERE ulid = NEW.machine_ref)
BEGIN
  SELECT RAISE(ABORT, 'workout_sets refused: machine_ref names no ax_machine row -- register it first (kit_life.sh machine-register)');
END;
CREATE TRIGGER trg_workout_sets_machine_known_upd
BEFORE UPDATE OF machine_ref ON workout_sets
WHEN NEW.machine_ref IS NOT NULL
 AND NOT EXISTS (SELECT 1 FROM ax_machine WHERE ulid = NEW.machine_ref)
BEGIN
  SELECT RAISE(ABORT, 'workout_sets refused: machine_ref names no ax_machine row -- register it first (kit_life.sh machine-register)');
END;
CREATE TABLE exercise_muscle_map (
    exercise_name TEXT NOT NULL,
    muscle        TEXT NOT NULL,
    role          TEXT NOT NULL CHECK(role IN ('primary','secondary')),
    PRIMARY KEY (exercise_name, muscle)
);
-- SEED ROWS MOVED OUT (exercise_muscle_map): the 53 exercise_muscle_map rows this table used to receive at
-- fresh mint are health-trainer product knowledge, not kit baseline, and now
-- ship in that pack (payload/service/health/health_taxonomy_seed.sql, applied
-- idempotently by health_taxonomy_seed.py). The TABLE stays here: the kit owns
-- this DDL, it is an FK target, and a table that does not exist cannot be
-- seeded later. An instance that never trains is born with it EMPTY, not
-- missing. Existing installations keep the rows they already have -- there is
-- no migration and no user_version change.
-- 030 muscle-taxonomy: exercise_muscle is now a VIEW over the _map base table
-- so the muscle layer can be reshaped as DATA without breaking read callers.
CREATE VIEW exercise_muscle AS
    SELECT exercise_name, muscle, role FROM exercise_muscle_map;
CREATE TABLE exercise_subregion_map (
    exercise_name TEXT NOT NULL,
    subregion     TEXT NOT NULL,
    weight        REAL NOT NULL,
    grade         TEXT NOT NULL,
    PRIMARY KEY (exercise_name, subregion)
);
-- SEED ROWS MOVED OUT (exercise_subregion_map): the 98 exercise_subregion_map rows this table used to receive at
-- fresh mint are health-trainer product knowledge, not kit baseline, and now
-- ship in that pack (payload/service/health/health_taxonomy_seed.sql, applied
-- idempotently by health_taxonomy_seed.py). The TABLE stays here: the kit owns
-- this DDL, it is an FK target, and a table that does not exist cannot be
-- seeded later. An instance that never trains is born with it EMPTY, not
-- missing. Existing installations keep the rows they already have -- there is
-- no migration and no user_version change.
-- 030 muscle-taxonomy: same treatment as exercise_muscle above.
CREATE VIEW exercise_subregion AS
    SELECT exercise_name, subregion, weight, grade FROM exercise_subregion_map;
CREATE TABLE exercise_movement_grip (
    exercise_name TEXT PRIMARY KEY,
    movement      TEXT NOT NULL,
    equipment     TEXT,
    grip          TEXT
);
-- SEED ROWS MOVED OUT (exercise_movement_grip): the 31 exercise_movement_grip rows this table used to receive at
-- fresh mint are health-trainer product knowledge, not kit baseline, and now
-- ship in that pack (payload/service/health/health_taxonomy_seed.sql, applied
-- idempotently by health_taxonomy_seed.py). The TABLE stays here: the kit owns
-- this DDL, it is an FK target, and a table that does not exist cannot be
-- seeded later. An instance that never trains is born with it EMPTY, not
-- missing. Existing installations keep the rows they already have -- there is
-- no migration and no user_version change.
CREATE TABLE exercise_fatigue_cost (
    exercise_name TEXT PRIMARY KEY,
    cost          TEXT NOT NULL,
    source        TEXT NOT NULL
);
-- SEED ROWS MOVED OUT (exercise_fatigue_cost): the 34 exercise_fatigue_cost rows this table used to receive at
-- fresh mint are health-trainer product knowledge, not kit baseline, and now
-- ship in that pack (payload/service/health/health_taxonomy_seed.sql, applied
-- idempotently by health_taxonomy_seed.py). The TABLE stays here: the kit owns
-- this DDL, it is an FK target, and a table that does not exist cannot be
-- seeded later. An instance that never trains is born with it EMPTY, not
-- missing. Existing installations keep the rows they already have -- there is
-- no migration and no user_version change.
CREATE TABLE exercise_pattern (
    exercise_name TEXT PRIMARY KEY,
    pattern       TEXT NOT NULL
);
-- SEED ROWS MOVED OUT (exercise_pattern): the 34 exercise_pattern rows this table used to receive at
-- fresh mint are health-trainer product knowledge, not kit baseline, and now
-- ship in that pack (payload/service/health/health_taxonomy_seed.sql, applied
-- idempotently by health_taxonomy_seed.py). The TABLE stays here: the kit owns
-- this DDL, it is an FK target, and a table that does not exist cannot be
-- seeded later. An instance that never trains is born with it EMPTY, not
-- missing. Existing installations keep the rows they already have -- there is
-- no migration and no user_version change.
CREATE TABLE movement_muscle_base (
    movement TEXT NOT NULL,
    muscle   TEXT NOT NULL,
    role     TEXT NOT NULL CHECK(role IN ('primary','secondary')),
    PRIMARY KEY (movement, muscle)
);
-- SEED ROWS MOVED OUT (movement_muscle_base): the 25 movement_muscle_base rows this table used to receive at
-- fresh mint are health-trainer product knowledge, not kit baseline, and now
-- ship in that pack (payload/service/health/health_taxonomy_seed.sql, applied
-- idempotently by health_taxonomy_seed.py). The TABLE stays here: the kit owns
-- this DDL, it is an FK target, and a table that does not exist cannot be
-- seeded later. An instance that never trains is born with it EMPTY, not
-- missing. Existing installations keep the rows they already have -- there is
-- no migration and no user_version change.
CREATE TABLE grip_muscle_delta (
    grip     TEXT NOT NULL,
    muscle   TEXT NOT NULL,
    role     TEXT NOT NULL CHECK(role IN ('primary','secondary')),
    evidence TEXT,
    PRIMARY KEY (grip, muscle)
);
-- SEED ROWS MOVED OUT (grip_muscle_delta): the 1 grip_muscle_delta rows this table used to receive at
-- fresh mint are health-trainer product knowledge, not kit baseline, and now
-- ship in that pack (payload/service/health/health_taxonomy_seed.sql, applied
-- idempotently by health_taxonomy_seed.py). The TABLE stays here: the kit owns
-- this DDL, it is an FK target, and a table that does not exist cannot be
-- seeded later. An instance that never trains is born with it EMPTY, not
-- missing. Existing installations keep the rows they already have -- there is
-- no migration and no user_version change.
CREATE TABLE grip_vocab (
    grip TEXT PRIMARY KEY,
    note TEXT NOT NULL
);
-- SEED ROWS MOVED OUT (grip_vocab): the 5 grip_vocab rows this table used to receive at
-- fresh mint are health-trainer product knowledge, not kit baseline, and now
-- ship in that pack (payload/service/health/health_taxonomy_seed.sql, applied
-- idempotently by health_taxonomy_seed.py). The TABLE stays here: the kit owns
-- this DDL, it is an FK target, and a table that does not exist cannot be
-- seeded later. An instance that never trains is born with it EMPTY, not
-- missing. Existing installations keep the rows they already have -- there is
-- no migration and no user_version change.
-- meals: since 036 a meal is a record block (settled_by='meal-record')
-- + measure rows + a nutrition block_delta; kit_life reads it back
-- through its MEALS_V32 surface.

CREATE TABLE workouts (
  id         INTEGER PRIMARY KEY,
  date       TEXT NOT NULL,
  minutes    REAL NOT NULL DEFAULT 0,
  kcal       REAL,
  note       TEXT,
  area_id    INTEGER REFERENCES areas(id),
  notion_id  TEXT,
  owning_agent TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
, avg_hr REAL, session_type_id INTEGER REFERENCES session_type(id), kcal_origin TEXT
    CHECK (kcal_origin IS NULL OR (kcal IS NOT NULL AND kcal_origin IN
           ('measured','computed','default','declared','unknown','estimated'))));
CREATE TABLE metric_log (
  id INTEGER PRIMARY KEY, date TEXT NOT NULL, metric TEXT NOT NULL,
  value REAL NOT NULL, note TEXT,
  owning_agent TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  UNIQUE(date, metric)
);
CREATE TABLE IF NOT EXISTS "routine_def" (
    id            INTEGER PRIMARY KEY,
    ulid          TEXT NOT NULL UNIQUE,
    recurrence_id TEXT NOT NULL UNIQUE,
    title         TEXT NOT NULL,
    kind          TEXT NOT NULL DEFAULT 'task',
    cadence       TEXT,
    schedule_kind TEXT NOT NULL,
    time_of_day   TEXT,
    duration_min  INTEGER,
    exclusive     INTEGER NOT NULL DEFAULT 0,
    display_tz    TEXT NOT NULL DEFAULT 'Asia/Seoul',
    area_id       INTEGER REFERENCES areas(id),
    project_id    INTEGER REFERENCES projects(id),
    purpose       TEXT,
    status        TEXT NOT NULL DEFAULT 'active',
    start_date    TEXT NOT NULL,
    end_date      TEXT,
    valid_until   TEXT NOT NULL,
    rule_effective_from TEXT NOT NULL,
    anomaly_ack   TEXT,
    notify_policy   TEXT,
    notify_lead_min INTEGER,
    version       INTEGER NOT NULL DEFAULT 0,
    created_by    TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    place_id      INTEGER REFERENCES place(id),
    owning_agent  TEXT NOT NULL, retro_skip INTEGER NOT NULL DEFAULT 0
    CHECK (retro_skip IN (0,1)), fallback_at TEXT
    CHECK (fallback_at IS NULL OR TRIM(fallback_at) <> ''), ride_not_before TEXT
    CHECK (ride_not_before IS NULL
           OR ride_not_before GLOB '[0-2][0-9]:[0-5][0-9]'), ride_prefer TEXT
    CHECK (ride_prefer IS NULL OR ride_prefer IN ('morning','evening')), placement TEXT
    CHECK (placement IS NULL
           OR (placement = 'ride_travel' AND kind = 'task'
               AND schedule_kind = 'timed' AND place_id IS NULL
               AND fallback_at IS NOT NULL)), action_kind TEXT NOT NULL DEFAULT 'check'
    CHECK (action_kind IN ('check','workout','retro','session','other')),
    admit_cmd TEXT CHECK(admit_cmd IS NULL OR
        (json_valid(admit_cmd) AND json_type(admit_cmd)='array' AND json_array_length(admit_cmd)>0)),
    CHECK (kind IN ('task','appointment','presence')),
    CHECK (kind != 'presence' OR place_id IS NOT NULL),
    CHECK (schedule_kind IN ('timed','all_day')),
    CHECK (status IN ('active','paused','retired')),
    CHECK (schedule_kind != 'timed' OR time_of_day IS NOT NULL),
    CHECK (exclusive IN (0,1)),
    CHECK (cadence IS NOT NULL OR status = 'retired'),
    CHECK (notify_policy IS NULL OR notify_policy IN
           ('default','silent','start_only','custom')),
    CHECK (notify_lead_min IS NULL OR COALESCE(notify_policy,'') = 'custom'),
    CHECK (COALESCE(notify_policy,'') != 'custom'
           OR (notify_lead_min IS NOT NULL AND notify_lead_min >= 0))
);
-- v_daily_energy / v_event_resolution: defined at the end of this file
-- (they read the v3.2 block axis / the compat timeblock view).


CREATE INDEX idx_areas_domain ON areas(domain);
CREATE UNIQUE INDEX idx_areas_notion ON areas(notion_id) WHERE notion_id IS NOT NULL;
CREATE UNIQUE INDEX idx_persons_notion ON persons(notion_id) WHERE notion_id IS NOT NULL;
CREATE INDEX idx_wc_type ON workout_classifications(type_id);





-- 027 (timeblock-project-id): achievement axis. Partial -- linked rows only.


CREATE INDEX idx_sub_event_parent ON sub_event(event_id, tombstone);
CREATE INDEX idx_sub_event_owner ON sub_event(owning_agent, tombstone);
-- 053: live prep-item rows (kind='prep_item') must be unique per (event,
-- ref) and cheaply reverse-lookupable by ref. See migrations/053_carry_item.sql.
CREATE UNIQUE INDEX ux_sub_event_live_prep_item
  ON sub_event(event_id, ref)
  WHERE kind = 'prep_item' AND tombstone = 0;
CREATE INDEX ix_sub_event_prep_ref
  ON sub_event(ref)
  WHERE kind = 'prep_item' AND tombstone = 0;
CREATE INDEX idx_reschedule_status ON reschedule_requests(status);
CREATE INDEX idx_event_persons_person
    ON event_persons(person_id);
CREATE UNIQUE INDEX idx_projects_notion
    ON projects(notion_id) WHERE notion_id IS NOT NULL;


CREATE UNIQUE INDEX idx_travel_rule_anchor
    ON travel_rule(anchor_type, anchor_ulid) WHERE status != 'retired';
CREATE UNIQUE INDEX idx_place_base
    ON place(is_base) WHERE is_base = 1;
CREATE INDEX idx_spend_date ON spend_entry(date);
CREATE INDEX idx_spend_cat  ON spend_entry(category_id);
CREATE UNIQUE INDEX idx_persons_relations_pair
  ON persons_relations(person_a, person_b, relation_type);
CREATE INDEX idx_pay_method_name ON pay_method(name);
CREATE INDEX idx_fixed_next_due ON fixed_cost(next_due);
CREATE INDEX idx_fixed_direction ON fixed_cost(direction);
-- (028 goal-engine DROPped idx_goal_active_layer here; single-active-per-layer
--  is superseded by ledger_goal.focus_state. Deliberately not recreated.)
CREATE UNIQUE INDEX idx_overlay_single_active
  ON ledger_goal(layer)
  WHERE status = 'active' AND layer = 'event_overlay';
CREATE INDEX idx_workout_sets_session ON workout_sets(session_id);
CREATE INDEX idx_workout_sets_exercise ON workout_sets(exercise_name, recorded_at);


CREATE INDEX idx_workouts_date ON workouts(date);
CREATE UNIQUE INDEX idx_workouts_notion ON workouts(notion_id) WHERE notion_id IS NOT NULL;
CREATE INDEX idx_metric_log_metric_date ON metric_log(metric, date);
-- ============================================================================
-- AX/US data-model (migration 020, DGN-789). 7 health-domain ULID-PK tables +
-- 9 seeded ax_projection_rule rows (7 base + Track B). Byte-matched to 020_ax_us_datamodel.sql.
-- ============================================================================
CREATE TABLE ax_exercise (
    ulid          TEXT PRIMARY KEY,
    owning_agent  TEXT NOT NULL DEFAULT 'warg',
    exercise_name TEXT NOT NULL,
    movement      TEXT NOT NULL,
    measure       TEXT NOT NULL,
    load_type     TEXT NOT NULL,
    tempo         TEXT,
    hold_sec      INTEGER,
    machine_ref   TEXT REFERENCES ax_machine(ulid),
    status        TEXT NOT NULL DEFAULT 'active',
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL,
    CHECK (owning_agent <> '' AND owning_agent NOT GLOB '*[^A-Za-z0-9_-]*'),
    CHECK (movement  IN ('compound','isolation')),
    CHECK (measure   IN ('reps','hold_sec','tempo')),
    CHECK (load_type IN ('freeweight','machine','cable','bodyweight')),
    CHECK (status    IN ('active','retired')),
    CHECK (measure != 'hold_sec' OR hold_sec IS NOT NULL),
    CHECK (measure != 'tempo'    OR tempo    IS NOT NULL),
    CHECK (load_type NOT IN ('machine','cable') OR machine_ref IS NOT NULL),
    CHECK (load_type NOT IN ('freeweight','bodyweight') OR machine_ref IS NULL)
);
CREATE TABLE ax_machine (
    ulid            TEXT PRIMARY KEY,
    owning_agent    TEXT NOT NULL DEFAULT 'warg',
    gym_id          INTEGER REFERENCES place(id),
    label           TEXT NOT NULL,
    brand           TEXT,
    loading_method  TEXT,
    loading_side    TEXT,
    smith_type      TEXT,
    cable_lines     TEXT,
    available_plates TEXT,
    lattice_base    REAL,
    lattice_step    REAL,
    status          TEXT NOT NULL DEFAULT 'active',
    provisional     INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    CHECK (owning_agent <> '' AND owning_agent NOT GLOB '*[^A-Za-z0-9_-]*'),
    CHECK (loading_side IS NULL OR loading_side IN ('single','per_side','stack')),
    CHECK (smith_type   IS NULL OR smith_type   IN ('vertical','angled')),
    CHECK (cable_lines  IS NULL OR cable_lines  IN ('single','dual')),
    CHECK (status       IN ('active','retired')),
    CHECK (provisional  IN (0,1)),
    CHECK ((lattice_base IS NULL) = (lattice_step IS NULL))
);
CREATE TABLE ax_projection_rule (
    ulid          TEXT PRIMARY KEY,
    owning_agent  TEXT NOT NULL DEFAULT 'warg',
    code          TEXT NOT NULL UNIQUE,
    priority      INTEGER NOT NULL DEFAULT 100,
    match_movement   TEXT,
    match_load_type  TEXT,
    match_loading    TEXT,
    match_cable_lines TEXT,
    match_measure    TEXT,
    match_has_injury INTEGER,
    action_kind   TEXT NOT NULL,
    action_param  TEXT,
    status        TEXT NOT NULL DEFAULT 'active',
    created_at    TEXT NOT NULL,
    CHECK (owning_agent <> '' AND owning_agent NOT GLOB '*[^A-Za-z0-9_-]*'),
    CHECK (match_movement    IS NULL OR match_movement    IN ('compound','isolation')),
    CHECK (match_load_type   IS NULL OR match_load_type   IN ('freeweight','machine','cable','bodyweight')),
    CHECK (match_cable_lines IS NULL OR match_cable_lines IN ('single','dual')),
    CHECK (match_measure     IS NULL OR match_measure     IN ('reps','hold_sec','tempo')),
    CHECK (match_has_injury  IS NULL OR match_has_injury  IN (0,1)),
    CHECK (action_kind IN (
        'empty_bar_ramp',
        'injury_drill_include',
        'order_defer',
        'tut_progression',
        'plate_projection',
        'cable_line_specify',
        'lattice_round'
    )),
    CHECK (status IN ('active','superseded'))
);
CREATE TABLE ax_projection_slot (
    ulid            TEXT PRIMARY KEY,
    owning_agent    TEXT NOT NULL DEFAULT 'warg',
    step_ref        TEXT NOT NULL REFERENCES us_step(ulid) ON DELETE CASCADE,
    exercise_ref    TEXT NOT NULL REFERENCES ax_exercise(ulid),
    machine_ref     TEXT REFERENCES ax_machine(ulid),
    target_raw      REAL,
    realized_value  REAL,
    realized_detail TEXT,
    provisional     INTEGER NOT NULL DEFAULT 0,
    rule_ref        TEXT REFERENCES ax_projection_rule(ulid),
    created_at      TEXT NOT NULL,
    CHECK (owning_agent <> '' AND owning_agent NOT GLOB '*[^A-Za-z0-9_-]*'),
    CHECK (provisional  IN (0,1))
);
CREATE TABLE ax_tag (
    ulid          TEXT PRIMARY KEY,
    owning_agent  TEXT NOT NULL DEFAULT 'warg',
    tag_group     TEXT NOT NULL,
    value         TEXT NOT NULL,
    note          TEXT,
    status        TEXT NOT NULL DEFAULT 'active',
    created_at    TEXT NOT NULL,
    CHECK (owning_agent <> '' AND owning_agent NOT GLOB '*[^A-Za-z0-9_-]*'),
    CHECK (status IN ('active','retired'))
);
CREATE TABLE ax_exercise_tag (
    ulid          TEXT PRIMARY KEY,
    owning_agent  TEXT NOT NULL DEFAULT 'warg',
    exercise_ref  TEXT NOT NULL REFERENCES ax_exercise(ulid) ON DELETE CASCADE,
    tag_ref       TEXT NOT NULL REFERENCES ax_tag(ulid),
    created_at    TEXT NOT NULL,
    CHECK (owning_agent <> '' AND owning_agent NOT GLOB '*[^A-Za-z0-9_-]*')
);
CREATE TABLE us_step (
    ulid           TEXT PRIMARY KEY,
    owning_agent   TEXT NOT NULL DEFAULT 'warg',
    session_ref    TEXT NOT NULL,
    seq            INTEGER NOT NULL,
    exercise_ref   TEXT NOT NULL REFERENCES ax_exercise(ulid),
    step_type      TEXT NOT NULL,
    group_id       TEXT,
    display_source TEXT NOT NULL,
    display_key    TEXT,
    equipment_need TEXT NOT NULL,
    measure        TEXT NOT NULL,
    target         TEXT,
    rest_sec       INTEGER,
    suppress_alert INTEGER NOT NULL DEFAULT 0,
    next_hint      TEXT,
    created_at     TEXT NOT NULL,
    CHECK (owning_agent <> '' AND owning_agent NOT GLOB '*[^A-Za-z0-9_-]*'),
    CHECK (step_type      IN ('prep','warmup','ramp','working','hold')),
    CHECK (display_source IN ('matrix_ref','exercise_derived')),
    CHECK (equipment_need IN ('bodyweight','equipment')),
    CHECK (measure        IN ('reps','hold_sec','tempo')),
    CHECK (suppress_alert IN (0,1)),
    CHECK (display_source != 'matrix_ref' OR display_key IS NOT NULL),
    CHECK (step_type != 'prep' OR suppress_alert = 1)
);
-- SEED ROWS MOVED OUT (ax_projection_rule): the 9 ax_projection_rule rows this table used to receive at
-- fresh mint are health-trainer product knowledge, not kit baseline, and now
-- ship in that pack (payload/service/health/health_taxonomy_seed.sql, applied
-- idempotently by health_taxonomy_seed.py). The TABLE stays here: the kit owns
-- this DDL, it is an FK target, and a table that does not exist cannot be
-- seeded later. An instance that never trains is born with it EMPTY, not
-- missing. Existing installations keep the rows they already have -- there is
-- no migration and no user_version change.
CREATE UNIQUE INDEX idx_ax_exercise_name
    ON ax_exercise(owning_agent, exercise_name);
CREATE INDEX idx_ax_exercise_machine
    ON ax_exercise(machine_ref) WHERE machine_ref IS NOT NULL;
CREATE INDEX idx_ax_machine_gym
    ON ax_machine(gym_id) WHERE gym_id IS NOT NULL;
CREATE INDEX idx_ax_machine_label
    ON ax_machine(gym_id, label);
CREATE INDEX idx_ax_projection_step
    ON ax_projection_slot(step_ref);
CREATE UNIQUE INDEX idx_ax_tag_uni
    ON ax_tag(owning_agent, tag_group, value);
CREATE UNIQUE INDEX idx_ax_exercise_tag_uni
    ON ax_exercise_tag(exercise_ref, tag_ref);
CREATE INDEX idx_ax_exercise_tag_tag
    ON ax_exercise_tag(tag_ref);
CREATE UNIQUE INDEX idx_us_step_seq
    ON us_step(session_ref, seq);
CREATE INDEX idx_us_step_exercise
    ON us_step(exercise_ref);
CREATE INDEX idx_us_step_group
    ON us_step(group_id) WHERE group_id IS NOT NULL;

-- ============================================================================
-- Event(experience) overlay -- migration 026 (user_version 26). Optional
-- experience container ON TOP of timeblock (unchanged, DGN-783). Non-destructive
-- (A8): every FK ON DELETE CASCADE flows downward into junction rows only, so an
-- event/link delete never destroys the underlying timeblock/record. CREATE text
-- below is byte-matched to 026_event_experience_overlay.sql (C3 parity); full
-- rationale + tool-layer duties live in that migration header.
-- event.parent_id (trailing ", parent_id ..." line) is migration 040's ADD
-- COLUMN, written inline here in the exact text ALTER appends to the stored
-- CREATE so fresh == migrated sqlite_master (040 header has the rationale).
-- ============================================================================
CREATE TABLE event (
  id           INTEGER PRIMARY KEY,
  ulid         TEXT NOT NULL UNIQUE,
  title        TEXT NOT NULL,
  kind_hint    TEXT,            -- coarse optional hint; NOT timeblock.kind (B2)
  note         TEXT,
  reaction     TEXT,            -- subjective facet, NULLable; DGN-892 non-autonomy is a tool gate, schema only stores
  created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  owning_agent TEXT NOT NULL DEFAULT 'ag'
, parent_id INTEGER REFERENCES event(id) ON DELETE SET NULL, occurred_on TEXT, occurred_at TEXT, primary_facet TEXT, time_record_id INTEGER REFERENCES record(id) ON DELETE SET NULL);
CREATE TABLE record (
  id           INTEGER PRIMARY KEY,
  ulid         TEXT NOT NULL UNIQUE,
  source_table TEXT NOT NULL,   -- real table token: workouts | metric_log | spend_entry | artifact (Skull owns registry)
  source_id    INTEGER NOT NULL,
  ts           TEXT,
  owning_agent TEXT NOT NULL DEFAULT 'ag',
  UNIQUE (source_table, source_id)   -- find-first/upsert dedup (A3)
);
CREATE TABLE artifact (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,   -- AUTOINCREMENT: never reuse a deleted artifact id (grill MAJOR-1)
  ulid         TEXT NOT NULL UNIQUE,
  kind         TEXT NOT NULL,   -- 'photo' | 'note' | ...
  ref          TEXT,            -- path / uri / inline text ref
  ts           TEXT,
  owning_agent TEXT NOT NULL DEFAULT 'ag'
);
CREATE TABLE record_event (
  record_id INTEGER NOT NULL REFERENCES record(id) ON DELETE CASCADE,
  event_id  INTEGER NOT NULL REFERENCES event(id)  ON DELETE CASCADE,
  role      TEXT NOT NULL DEFAULT 'incidental' CHECK (role IN ('primary','incidental')),
  weight    REAL,
  source    TEXT,
  added_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  PRIMARY KEY (record_id, event_id)
);
CREATE INDEX idx_record_event_event ON record_event(event_id);
-- added_at DEFAULT is UTC-Z per 036's time idiom (rebuilt there; no reader
-- anywhere).  Comment kept outside the DDL for sqlite_master parity.
CREATE TABLE event_timeblock (
  event_id     INTEGER NOT NULL REFERENCES event(id) ON DELETE CASCADE,
  timeblock_id INTEGER NOT NULL REFERENCES block(id) ON DELETE CASCADE,
  added_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  PRIMARY KEY (event_id, timeblock_id)
);
CREATE INDEX idx_event_timeblock_tb ON event_timeblock(timeblock_id);
CREATE TABLE insight (
  id           INTEGER PRIMARY KEY,
  ulid         TEXT NOT NULL UNIQUE,
  text         TEXT NOT NULL,
  evidence     TEXT CHECK (evidence IS NULL OR json_valid(evidence)),  -- JSON array of event ulids (provenance)
  owning_agent TEXT NOT NULL DEFAULT 'ag',
  created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
COMMIT;

-- ============================================================================
-- Achievement axis -- migration 027 (user_version 27). Adds the NULLABLE
-- timeblock.project_id column (defined inline in the timeblock table above) +
-- its partial index. Additive superset of v26; the silent auto-link fills it
-- opportunistically. See 027_timeblock_project_id.sql for the full rationale.
-- ============================================================================

-- ---------------------------------------------------------------------------
-- Goal engine -- migration 028 (user_version 28). Life-direction & goal
-- progression: `life_why` (the why behind a goal), `goal_readiness_rule`
-- (declarative rules, each dispatched to a named deterministic evaluator),
-- `goal_metric_source` (where a metric is actually read from), and the
-- `hypothesis` / `hypothesis_evidence` pair (epistemics = behavior >> verbal,
-- with weekly decay). The `ledger_goal` extension columns are folded INLINE
-- above; idx_goal_active_layer is deliberately absent (see there).
--
-- Shared food cache -- migration 029 (user_version 29). GLOBAL (not per-agent)
-- `meal_food` / `meal_alias` / `meal_portion`.
--
-- Muscle taxonomy as DATA -- migration 030 (user_version 30). `muscle_node` +
-- `muscle_label`. exercise_muscle / exercise_subregion became VIEWS over their
-- _map base tables above.
--
-- Commitment kind -- migration 031 (user_version 31). `ledger_goal` gains the
-- goal|rule discriminator `commitment_kind` + `rule_period`; folded INLINE
-- above together with the two enum CHECKs the ALTER-based migration could not
-- express. idx_goal_kind is with the other goal indexes at the tail.
-- ---------------------------------------------------------------------------

-- life_why.label + managing_agent slug grammar -- migration 054 (user_version
-- 54). `label` is the SHORT slot-ready phrase ('튼튼한 코어') agreed with the
-- owner at capture (DESIGN-SYSTEM W12); `text` stays the raw utterance, the
-- source. NULL label = not agreed yet -> slot readers omit the aspiration.
-- ALTER TABLE ADD COLUMN appends `label` after updated_at, so the column sits
-- on that line here too (fresh-mint text == migrated text). The managing_agent
-- CHECK carries 049's slug grammar instead of the closed ('ag','warg','user').
CREATE TABLE life_why (
    id                    INTEGER PRIMARY KEY,
    ulid                  TEXT NOT NULL UNIQUE,
    text                  TEXT NOT NULL,              -- the why in user language
    scope                 TEXT NOT NULL,              -- module|life
    domain                TEXT,                       -- for module-why; NULL for life-why
    promoted_from_insight INTEGER REFERENCES insight(id),  -- provenance (SoT 1). NULLABLE
    spectrum              TEXT NOT NULL DEFAULT 'casual',   -- casual|aspiration
    status                TEXT NOT NULL DEFAULT 'active',   -- active|pivoted|retired
    superseded_by         INTEGER REFERENCES life_why(id),  -- pivot chain self-FK. NULLABLE
    confidence            REAL,                       -- 0..1, held by hypothesis substrate
    owner                 TEXT NOT NULL DEFAULT 'user',     -- ALWAYS 'user' -- the why is user-owned (D3)
    managing_agent        TEXT,                       -- ag|warg|.. steward (NOT owner). life->ag, module->domain agent
    created_at            TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at            TEXT NOT NULL DEFAULT (datetime('now','localtime')), label TEXT,
    CHECK (owner = 'user'),
    CHECK (scope    IN ('module','life')),
    CHECK (spectrum IN ('casual','aspiration')),
    CHECK (status   IN ('active','pivoted','retired')),
    CHECK (managing_agent IS NULL OR (managing_agent <> '' AND managing_agent NOT GLOB '*[^A-Za-z0-9_-]*'))
);

CREATE TABLE goal_readiness_rule (
    ulid            TEXT PRIMARY KEY,
    owning_agent    TEXT NOT NULL DEFAULT 'warg',
    code            TEXT NOT NULL UNIQUE,
    priority        INTEGER NOT NULL DEFAULT 100,   -- eval order (lower first)
    match_domain    TEXT,                           -- NULL=any (predicate)
    match_layer     TEXT,                           -- NULL=any, else short|mid|long
    match_metric    TEXT,                           -- NULL=any, else a metric token
    eval_kind       TEXT NOT NULL,                  -- named deterministic evaluator
    eval_param      TEXT,                           -- JSON params (may carry @rung.* bind tokens)
    verdict_on_pass TEXT NOT NULL,                  -- the verdict emitted on pass
    status          TEXT NOT NULL DEFAULT 'active', -- active|superseded
    created_at      TEXT NOT NULL,
    CHECK (owning_agent <> '' AND owning_agent NOT GLOB '*[^A-Za-z0-9_-]*'),
    CHECK (eval_kind IN (
        'completion_rate_gte',
        'volume_target_met',
        'streak_gte',
        'metric_pace_on_or_ahead',
        'metric_reached'
    )),
    CHECK (verdict_on_pass IN (
        'rung_met',
        'levelup_eligible',
        'retire_candidate',
        'downgrade_candidate'
    )),
    CHECK (status IN ('active','superseded'))
);
INSERT INTO goal_readiness_rule VALUES('01K3GOALRULE0000000000BASE1','warg','R_weekly_sessions_met',10,NULL,'short',NULL,'completion_rate_gte','{"freq_per_week":"@rung.target_value","min_rate":1.0}','rung_met','active','2026-08-20T00:00:00Z');
INSERT INTO goal_readiness_rule VALUES('01K3GOALRULE0000000000BASE2','warg','R_weekly_volume_met',20,'health',NULL,NULL,'volume_target_met','{"source":"workout_sets"}','rung_met','active','2026-08-20T00:00:00Z');
INSERT INTO goal_readiness_rule VALUES('01K3GOALRULE0000000000BASE3','warg','R_streak_intact',30,NULL,'short',NULL,'streak_gte','{"weeks":"@rung.detail.window_weeks"}','levelup_eligible','active','2026-08-20T00:00:00Z');
INSERT INTO goal_readiness_rule VALUES('01K3GOALRULE0000000000BASE4','warg','R_metric_on_pace',40,NULL,'mid',NULL,'metric_pace_on_or_ahead','{"pace":"date_continuous"}','levelup_eligible','active','2026-08-20T00:00:00Z');
INSERT INTO goal_readiness_rule VALUES('01K3GOALRULE0000000000BASE5','warg','R_metric_on_pace_long',41,NULL,'long',NULL,'metric_pace_on_or_ahead','{"pace":"date_continuous"}','levelup_eligible','active','2026-08-20T00:00:00Z');
INSERT INTO goal_readiness_rule VALUES('01K3GOALRULE0000000000BASE6','warg','R_metric_reached',50,NULL,NULL,NULL,'metric_reached','{}','levelup_eligible','active','2026-08-20T00:00:00Z');
INSERT INTO goal_readiness_rule VALUES('01K3GOALRULE0000000000BASE7','warg','R_no_action_stale',90,NULL,NULL,NULL,'completion_rate_gte','{"below":0.34,"stale_weeks":4}','retire_candidate','active','2026-08-20T00:00:00Z');

-- ── Metric AXIS taxonomy -- migration 033, reshaped by 055 ─────────────────
-- The axis list is DATA (033). Since 055 an axis row also says WHERE IT CAME
-- FROM: origin 'seed' (shipped) or 'user' (created from the user's own answer
-- during a goal interview, axis-add), and role 'input' (a readout of what goes
-- in / what happened) or 'picture' (a strand of what the user wants to become).
--
-- A FRESH MINT SHIPS NO PICTURE AXIS. 033 seeded four picture axes taken from
-- one person's words; carried into every install they read every new user
-- against someone else's picture (owner ruling 2026-09-26). Picture axes are
-- now created per user, in the user's words, by the goal interview. Only the
-- universal INPUT axes ship: they name records the kit itself writes (session
-- counts, calories/protein, money), not anyone's aspiration.
--
-- An existing install keeps its seeded picture rows (role='picture',
-- origin='seed'); readers skip them. This is the one place fresh-mint DATA
-- differs from a migrated DB on purpose -- the rows 055 refuses to delete.
CREATE TABLE metric_axis (
    axis_key      TEXT PRIMARY KEY,          -- stable ASCII machine key
    axis_rank     INTEGER NOT NULL,          -- offer order; also the readout order
    label         TEXT NOT NULL,             -- user-facing label (ko, owner's own word)
    domain        TEXT,                      -- health|spend|.. NULL = every domain
    owning_agent  TEXT NOT NULL DEFAULT 'warg',
    note          TEXT,                      -- why this axis exists, in one line
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')), origin TEXT NOT NULL DEFAULT 'seed' CHECK (origin IN ('seed','user')), role TEXT NOT NULL DEFAULT 'input' CHECK (role IN ('picture','input')), why_id INTEGER REFERENCES life_why(id) ON DELETE SET NULL, phase_order INTEGER,
    CHECK (owning_agent <> '' AND owning_agent NOT GLOB '*[^A-Za-z0-9_-]*')
);
INSERT INTO metric_axis VALUES('training_load',5,'수행량','health','warg','How much training actually happened. An input axis, not one of the four picture axes.','2026-08-24T00:00:00Z','seed','input',NULL,NULL);
INSERT INTO metric_axis VALUES('intake',6,'섭취','health','warg','What goes in: calories, protein.','2026-08-24T00:00:00Z','seed','input',NULL,NULL);
INSERT INTO metric_axis VALUES('spend',7,'지출','spend','ag','Money. Different domain and different owning agent; scoped out of a health consult.','2026-08-24T00:00:00Z','seed','input',NULL,NULL);

CREATE TABLE goal_metric_source (
    metric_token  TEXT PRIMARY KEY,   -- e.g. body_fat_pct, session_count, e1rm_deadlift, monthly_spend
    source_table  TEXT NOT NULL,      -- the real domain table current value reads from
    source_expr   TEXT,               -- column / aggregate expression
    domain        TEXT,               -- health|spend|..
    dir_hint      TEXT,               -- up|down|hold hint for the metric
    owning_agent  TEXT NOT NULL DEFAULT 'warg',
    created_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    axis_key      TEXT REFERENCES metric_axis(axis_key),  -- 033: which axis this metric feeds
    CHECK (owning_agent <> '' AND owning_agent NOT GLOB '*[^A-Za-z0-9_-]*'),
    CHECK (dir_hint IS NULL OR dir_hint IN ('up','down','hold'))
);
INSERT INTO goal_metric_source VALUES('weekly_sessions','workouts','count_sessions_trailing_7d','health','up','warg','2026-08-20T00:00:00Z','training_load');
INSERT INTO goal_metric_source VALUES('body_weight','metric_log','body_weight','health','down','warg','2026-08-20T00:00:00Z',NULL);
INSERT INTO goal_metric_source VALUES('body_fat_pct','metric_log','body_fat_pct','health','down','warg','2026-08-20T00:00:00Z',NULL);
INSERT INTO goal_metric_source VALUES('monthly_spend','spend_entry','sum_expense_month','spend','down','ag','2026-08-20T00:00:00Z','spend');
-- Skeletal muscle mass (033). source_expr is the metric_log.metric KEY actually
-- written by the InBody path ('skeletal_muscle_kg'). axis_key NULL on a fresh
-- mint (055): a user strand claims it via axis-add.
INSERT INTO goal_metric_source VALUES('skeletal_muscle_kg','metric_log','skeletal_muscle_kg','health','up','warg','2026-08-24T00:00:00Z',NULL);
-- Coordination sub-measures (055, pack_health E14). source_table 'program' =
-- computed by the health pack from program.yaml, not a kit table: the kit
-- reads no value for them (_metric_current_value -> None). A user strand
-- claims one via axis-add "<the user's word>=unilateral_share_sets".
INSERT INTO goal_metric_source VALUES('unilateral_share_sets','program','unilateral_share_sets','health','up','warg','2026-09-26T00:00:00Z',NULL);
INSERT INTO goal_metric_source VALUES('zero_load_control_share_sets','program','zero_load_control_share_sets','health','up','warg','2026-09-26T00:00:00Z',NULL);
-- 065: the InBody segmental lean readings (a shape strand -- the X-taper's
-- judged legs -- can own them) and the slow controlled-movement share (a
-- Pilates / connection strand's check; zero_load_control counts holds).
INSERT INTO goal_metric_source VALUES('lean_arm_l_kg','metric_log','lean_arm_l_kg','health','up','warg','2026-09-30T00:00:00Z',NULL);
INSERT INTO goal_metric_source VALUES('lean_arm_r_kg','metric_log','lean_arm_r_kg','health','up','warg','2026-09-30T00:00:00Z',NULL);
INSERT INTO goal_metric_source VALUES('lean_trunk_kg','metric_log','lean_trunk_kg','health','up','warg','2026-09-30T00:00:00Z',NULL);
INSERT INTO goal_metric_source VALUES('lean_leg_l_kg','metric_log','lean_leg_l_kg','health','up','warg','2026-09-30T00:00:00Z',NULL);
INSERT INTO goal_metric_source VALUES('lean_leg_r_kg','metric_log','lean_leg_r_kg','health','up','warg','2026-09-30T00:00:00Z',NULL);
INSERT INTO goal_metric_source VALUES('controlled_tempo_share_sets','program','controlled_tempo_share_sets','health','up','warg','2026-09-30T00:00:00Z',NULL);

CREATE TABLE hypothesis (
    id           INTEGER PRIMARY KEY,
    ulid         TEXT NOT NULL UNIQUE,
    text         TEXT NOT NULL,                 -- elimination-friendly phrasing (weak prior)
    subject_kind TEXT NOT NULL,                 -- insight|why|goal
    subject_id   INTEGER,                       -- soft ref into insight/life_why/ledger_goal by subject_kind
    confidence   REAL NOT NULL DEFAULT 0,       -- 0..1, starts LOW (weak prior)
    stance       TEXT NOT NULL DEFAULT 'open',  -- open|corroborated|falsified|retired
    domain       TEXT,                          -- for safety gating
    sensitivity  TEXT NOT NULL DEFAULT 'normal',-- normal|sensitive
    owning_agent TEXT NOT NULL DEFAULT 'warg',
    created_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (owning_agent <> '' AND owning_agent NOT GLOB '*[^A-Za-z0-9_-]*'),
    CHECK (subject_kind IN ('insight','why','goal')),
    CHECK (stance       IN ('open','corroborated','falsified','retired')),
    CHECK (sensitivity  IN ('normal','sensitive'))
);

CREATE TABLE hypothesis_evidence (
    id            INTEGER PRIMARY KEY,
    ulid          TEXT NOT NULL UNIQUE,
    hypothesis_id INTEGER NOT NULL REFERENCES hypothesis(id),
    record_id     INTEGER REFERENCES record(id),  -- the behavior evidence (real domain action). NULLABLE
    evidence_kind TEXT NOT NULL,                   -- behavior|verbal_affirm|verbal_deny
    weight        REAL NOT NULL DEFAULT 0,         -- confidence contribution (behavior >> verbal)
    observed_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (evidence_kind IN ('behavior','verbal_affirm','verbal_deny'))
);

CREATE TABLE meal_food (
    key          TEXT PRIMARY KEY,           -- normalized food name (lowercase, brackets/symbols stripped) = existing JSON key
    name         TEXT NOT NULL,              -- display name
    grams        REAL,                       -- the portion this row's macros are stated for
    kcal         REAL,
    carb         REAL NOT NULL DEFAULT 0,    -- JSON carbs -> carb (align with meals)
    protein      REAL NOT NULL DEFAULT 0,
    fat          REAL NOT NULL DEFAULT 0,
    fiber        REAL NOT NULL DEFAULT 0,
    sugar        REAL NOT NULL DEFAULT 0,
    sodium_mg    REAL NOT NULL DEFAULT 0,
    source       TEXT,                       -- "LLM estimate .." | "nutrition-label" | "barcode" etc.
    estimated    INTEGER NOT NULL DEFAULT 0, -- 0/1 (JSON bool -> INTEGER)
    updated_by   TEXT,                       -- provenance: 'ag' | 'warg' (audit, NOT a scope discriminator)
    updated_at   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
    CHECK (estimated IN (0,1))
);

CREATE TABLE meal_alias (
    alias        TEXT PRIMARY KEY,           -- "프로틴"
    canonical    TEXT NOT NULL,              -- "다이마타이즈 iso100" (= meal_food.key or name)
    updated_by   TEXT,                       -- provenance (audit)
    updated_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE meal_portion (
    key          TEXT PRIMARY KEY,           -- "김밥"
    grams        REAL NOT NULL,
    n            REAL NOT NULL DEFAULT 1,    -- how many servings the grams is stated for
    updated_by   TEXT,                       -- provenance (audit)
    updated_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
,
    unit         TEXT NOT NULL DEFAULT 'plate' CHECK (unit IN ('bowl','plate')),
    basis        TEXT NOT NULL DEFAULT 'registered');

-- 043: the diet skill's ordered lookup step leaves a RECEIPT here, and
-- meal_add refuses to record a food that has none (escape: allow_unlooked /
-- --no-lookup).  The order used to live only in SKILL.md prose, and prose
-- carries no execution order -- the model skipped the cache lookup and wrote
-- an LLM estimate over an already-measured value (DGN-928).
--
-- name_given is the caller's string VERBATIM, never normalized, so the
-- reader's comparison round-trips by construction.  Inferring "the lookup ran"
-- from meal_food presence instead does NOT round-trip -- 101 of 200 live rows
-- have a display name that normalizes away from their key -- and would refuse
-- half of the correctly cached foods.  norm_key is written for human
-- diagnostics only; nothing matches on it.  Full argument: migration 043.
--
-- resolved (048) records WHICH branch the lookup took: 'exact' (shared-cache
-- hit / barcode / label / public-DB answer -- the numbers are pinned) or
-- 'estimate' (the lookup ran and found nothing, so what follows is a guess).
-- meal_add asks the satiety question only on 'estimate'.  NULL = never asked,
-- which reads as permission, never as a refusal (fail-open, 043 discipline).
-- It sits after `ts` rather than in a tidier spot because that is exactly
-- where ALTER TABLE ADD COLUMN splices it into the stored statement text, and
-- the fresh-vs-migrated parity check compares that text byte for byte.
--
-- NO inline comments inside the CREATE below: sqlite_master stores the
-- statement text verbatim and the fresh-vs-migrated parity check compares it.
CREATE TABLE meal_lookup_receipt (
  name_given TEXT NOT NULL,
  norm_key   TEXT,
  mode       TEXT NOT NULL,
  ts         TEXT NOT NULL, resolved TEXT,
  PRIMARY KEY (name_given)
);

CREATE TABLE muscle_node (
    node      TEXT PRIMARY KEY,
    parent    TEXT REFERENCES muscle_node(node),
    kind      TEXT NOT NULL CHECK(kind IN ('group','muscle','subregion')),
    sort      INTEGER NOT NULL,
    band_unit TEXT
);
-- SEED ROWS MOVED OUT (muscle_node): the 33 muscle_node rows this table used to receive at
-- fresh mint are health-trainer product knowledge, not kit baseline, and now
-- ship in that pack (payload/service/health/health_taxonomy_seed.sql, applied
-- idempotently by health_taxonomy_seed.py). The TABLE stays here: the kit owns
-- this DDL, it is an FK target, and a table that does not exist cannot be
-- seeded later. An instance that never trains is born with it EMPTY, not
-- missing. Existing installations keep the rows they already have -- there is
-- no migration and no user_version change.

CREATE TABLE muscle_label (
    node TEXT NOT NULL REFERENCES muscle_node(node),
    lang TEXT NOT NULL,
    form TEXT NOT NULL CHECK(form IN ('long','short')),
    text TEXT NOT NULL,
    PRIMARY KEY (node, lang, form)
);
-- SEED ROWS MOVED OUT (muscle_label): the 47 muscle_label rows this table used to receive at
-- fresh mint are health-trainer product knowledge, not kit baseline, and now
-- ship in that pack (payload/service/health/health_taxonomy_seed.sql, applied
-- idempotently by health_taxonomy_seed.py). The TABLE stays here: the kit owns
-- this DDL, it is an FK target, and a table that does not exist cannot be
-- seeded later. An instance that never trains is born with it EMPTY, not
-- missing. Existing installations keep the rows they already have -- there is
-- no migration and no user_version change.

CREATE INDEX idx_life_why_scope
    ON life_why(scope, domain, status);

CREATE INDEX idx_goal_domain_layer
    ON ledger_goal(owning_agent, domain, layer, status);

CREATE INDEX idx_goal_focus
    ON ledger_goal(owning_agent, domain, focus_state, priority_rank);

CREATE INDEX idx_hypothesis_subject
    ON hypothesis(subject_kind, subject_id);

CREATE INDEX idx_hyp_evidence_hyp
    ON hypothesis_evidence(hypothesis_id);

CREATE INDEX idx_muscle_node_kind   ON muscle_node(kind, sort);

CREATE INDEX idx_muscle_node_parent ON muscle_node(parent);

CREATE INDEX idx_goal_kind
    ON ledger_goal(owning_agent, commitment_kind, status, focus_state);

-- ── Set kind / prescription axis -- migration 034 (user_version 34) ──────
-- `workout_sets` gains set_kind (prep|ramp|working|hold), target_reps and
-- hold_sec; the column definitions are inline in CREATE TABLE workout_sets
-- above (a fresh mint gets them at create time, no ALTER).
--
-- prep/ramp/working/hold are NOT separate concepts -- they are values of one
-- column, and everything that differs between them (table inclusion, keyboard,
-- volume inclusion, cue shape) is a table lookup, never an if/elif chain. Prep
-- used to live entirely outside the set pipeline, which is why a prep movement
-- produced no row and no load/rep cue at all.
--
-- The CHECK ships inline because set_kind is a NEW column: ALTER TABLE ADD
-- COLUMN carries it with no table rebuild. Cost of that choice, stated: a fifth
-- kind would need the 12-step rebuild. The roster is closed by spec.
--
-- target_reps is NULL for all history ON PURPOSE -- a retroactively invented
-- prescription would manufacture false "미달" verdicts, which is the exact
-- defect the column exists to end. hold_sec is likewise NULL for history: a
-- hold's duration lived in the program slot, never in this table.
-- Ref: SPEC 2026-08-25 session-hook harness A2.3.

-- ── v3.2 travel/block axis -- migration 036 (user_version 36) ────────────
-- The storage atom of the execution axis is `block` (+ block_attr for
-- module payload); travel knowledge is place_link pairs + measure rows
-- (dated, sourced, expirable -- v_place_link_measure serves the current
-- answer, F1 UTC-Z discipline); meals are record blocks.  The old
-- timeblock/travel_edge vocabularies remain as read-only compat views;
-- the consumed v35 tables are frozen verbatim under _l35_*.  See
-- migrations/036_travel_block_axis.sql for the full rationale.

-- ── place.address -- migration 035 (user_version 35) ─────────────────────
-- A place learns the string a MAP can resolve, separately from the name the
-- user calls it. The travel resolver used to hand the display name straight to
-- map autocomplete, so a nickname could never route and the user was asked for
-- the one-way minutes on every single appointment to that place. NULL keeps
-- today's name-routing; a stored address makes the leg derivable forever from
-- one answer. NULLABLE, no CHECK, no index, no backfill -- see
-- migrations/035_place_address.sql for the full rationale.

-- ── v3.2 travel/block axis -- migration 036 (user_version 36) ─────────────
-- The store the v3.2 code paths in kit_life.py actually use (the CODE is the
-- contract): block + block_attr (the execution-axis atom, replacing the
-- timeblock table), place_link + measure (travel knowledge as dated, sourced,
-- expirable measurements), plus the registries those FK into.  Authoring
-- source: the v3.2 authoring schema; objects the code does
-- not touch yet (domain/entity_attr/goal/... axes) deliberately do NOT ship.
-- entity and generator ship as dormant FK targets only (measure.entity_id,
-- block_delta.entity_id, block.generator_id would otherwise point at a
-- missing table and every DML would fail under foreign_keys=ON).
CREATE TABLE IF NOT EXISTS label (
  namespace   TEXT NOT NULL,
  token       TEXT NOT NULL,
  lang        TEXT NOT NULL,
  display     TEXT NOT NULL,
  PRIMARY KEY (namespace, token, lang)
);
CREATE TABLE IF NOT EXISTS resource (
  token         TEXT PRIMARY KEY,
  stock_bearing INTEGER NOT NULL DEFAULT 1 CHECK (stock_bearing IN (0,1)),
  default_unit  TEXT,
  origin        TEXT NOT NULL DEFAULT 'seed' CHECK (origin IN ('seed','module','user')),
  status        TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS unit (
  token       TEXT PRIMARY KEY,
  dimension   TEXT,
  origin      TEXT NOT NULL DEFAULT 'seed' CHECK (origin IN ('seed','module','user'))
);
CREATE TABLE IF NOT EXISTS block_outcome (
  token           TEXT PRIMARY KEY,
  counts_as_flow  INTEGER NOT NULL DEFAULT 0 CHECK (counts_as_flow IN (0,1)),
  origin          TEXT NOT NULL DEFAULT 'seed' CHECK (origin IN ('seed','module','user'))
);
CREATE TABLE IF NOT EXISTS block_kind (
  token    TEXT PRIMARY KEY,
  expires  INTEGER NOT NULL DEFAULT 1 CHECK (expires IN (0,1)),
  origin   TEXT NOT NULL DEFAULT 'seed' CHECK (origin IN ('seed','module','user'))
);
CREATE TABLE IF NOT EXISTS block_kind_behavior (
  token TEXT PRIMARY KEY REFERENCES block_kind(token),
  mirror_surface TEXT NOT NULL CHECK (mirror_surface IN ('calendar','tasks','none')),
  calendar_key TEXT,
  timed_calendar_key TEXT,
  overnight_calendar_key TEXT,
  reminder_lead_min INTEGER NOT NULL CHECK (reminder_lead_min >= 0),
  reminder_header_key TEXT NOT NULL,
  reminder_key_style TEXT NOT NULL CHECK (reminder_key_style IN ('appt','task')),
  persons_join INTEGER NOT NULL CHECK (persons_join IN (0,1)),
  travel_attach INTEGER NOT NULL CHECK (travel_attach IN (0,1)),
  presence_mode INTEGER NOT NULL CHECK (presence_mode IN (0,1)),
  requires_place INTEGER NOT NULL CHECK (requires_place IN (0,1)),
  adoptable INTEGER NOT NULL CHECK (adoptable IN (0,1)),
  adopt_path TEXT NOT NULL CHECK (adopt_path IN ('appt','task','presence','none')),
  location_mode TEXT NOT NULL CHECK (location_mode IN ('meta','place','required_place','none'))
);
CREATE TABLE IF NOT EXISTS settle_verdict (
  token  TEXT PRIMARY KEY,
  origin TEXT NOT NULL DEFAULT 'seed' CHECK (origin IN ('seed','module','user'))
);
CREATE TABLE IF NOT EXISTS provenance (
  token         TEXT PRIMARY KEY,
  is_estimate   INTEGER NOT NULL DEFAULT 0 CHECK (is_estimate IN (0,1)),
  recomputable  INTEGER NOT NULL DEFAULT 0 CHECK (recomputable IN (0,1)),
  writable      INTEGER NOT NULL DEFAULT 1 CHECK (writable IN (0,1)),
  origin        TEXT NOT NULL DEFAULT 'seed' CHECK (origin IN ('seed','module','user'))
);
-- Dormant FK target (no live write path yet; the entity axis has not landed).
CREATE TABLE IF NOT EXISTS entity (
  id          INTEGER PRIMARY KEY,
  ulid        TEXT NOT NULL UNIQUE,
  token       TEXT NOT NULL,
  owner_entity_id INTEGER REFERENCES entity(id) ON DELETE SET NULL,
  fungible    INTEGER NOT NULL DEFAULT 0 CHECK (fungible IN (0,1)),
  origin      TEXT NOT NULL DEFAULT 'user' CHECK (origin IN ('seed','module','user')),
  status      TEXT NOT NULL DEFAULT 'active'
              CHECK (status IN ('active','dormant','gone')),
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS ix_entity_owner ON entity(owner_entity_id);
-- Dormant FK target (routine_def/fixed_cost remain the live declaration
-- stores until the roller ports; blocks keep their recurrence_id attr).
CREATE TABLE IF NOT EXISTS generator (
  id          INTEGER PRIMARY KEY,
  ulid        TEXT NOT NULL UNIQUE,
  resource    TEXT NOT NULL REFERENCES resource(token),
  title       TEXT NOT NULL,
  recurrence  TEXT NOT NULL,
  spec        TEXT,
  version     INTEGER NOT NULL DEFAULT 1,
  effective_from TEXT, valid_until TEXT,
  supersedes  INTEGER REFERENCES generator(id),
  status      TEXT NOT NULL DEFAULT 'active'
              CHECK (status IN ('active','paused','retired')),
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
-- The execution-axis storage atom (B1: no stored status -- v_block_state
-- derives it; settlement is four-as-one, enforced by the paired CHECKs).
CREATE TABLE IF NOT EXISTS block (
  id           INTEGER PRIMARY KEY,
  ulid         TEXT NOT NULL UNIQUE,
  source_module TEXT NOT NULL,
  title        TEXT,
  start_at     TEXT, end_at TEXT,
  date         TEXT,
  settled_at      TEXT,
  settled_outcome TEXT REFERENCES block_outcome(token),
  settled_by      TEXT,
  settled_verdict TEXT REFERENCES settle_verdict(token),
  kind_token   TEXT REFERENCES block_kind(token),
  contained_by INTEGER REFERENCES block(id),
  derived_from INTEGER REFERENCES block(id),
  generator_id INTEGER REFERENCES generator(id),
  rec_date     TEXT,
  place_id     INTEGER REFERENCES place(id),
  external_id  TEXT, external_etag TEXT,
  created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')), retro_line TEXT,
  CHECK ((settled_at IS NULL) = (settled_outcome IS NULL)),
  CHECK ((settled_at IS NULL) = (settled_by      IS NULL)),
  CHECK ((settled_at IS NULL) = (settled_verdict IS NULL)),
  CHECK (start_at IS NULL OR date IS NULL)
);
CREATE INDEX IF NOT EXISTS ix_block_time    ON block(start_at);
CREATE INDEX IF NOT EXISTS ix_block_settled ON block(settled_at);
CREATE INDEX IF NOT EXISTS ix_block_module  ON block(source_module, date);
CREATE UNIQUE INDEX IF NOT EXISTS ux_block_gen_occurrence ON block(generator_id, rec_date)
  WHERE generator_id IS NOT NULL;
CREATE TABLE IF NOT EXISTS block_attr (
  block_id    INTEGER NOT NULL REFERENCES block(id) ON DELETE CASCADE,
  attr_token  TEXT NOT NULL,
  value       TEXT NOT NULL,
  source      TEXT NOT NULL DEFAULT 'user',
  PRIMARY KEY (block_id, attr_token, source)
);
CREATE TABLE IF NOT EXISTS metric_def (
  token        TEXT PRIMARY KEY,
  scope_kind   TEXT NOT NULL CHECK (scope_kind IN ('entity','domain','block','place_link')),
  accrual      TEXT NOT NULL CHECK (accrual IN ('accrued','observed','derived')),
  unit_token   TEXT REFERENCES unit(token),
  source_expr  TEXT,
  requires_entity_token TEXT,
  owner_module TEXT,
  origin       TEXT NOT NULL DEFAULT 'module' CHECK (origin IN ('seed','module','user')),
  status       TEXT NOT NULL DEFAULT 'active'
);
CREATE TABLE IF NOT EXISTS block_delta (
  id          INTEGER PRIMARY KEY,
  block_id    INTEGER NOT NULL REFERENCES block(id)      ON DELETE CASCADE,
  resource    TEXT    NOT NULL REFERENCES resource(token),
  entity_id   INTEGER          REFERENCES entity(id)     ON DELETE SET NULL,
  component   TEXT    NOT NULL DEFAULT '',
  amount      REAL    NOT NULL,
  unit        TEXT    NOT NULL REFERENCES unit(token),
  value_origin TEXT   NOT NULL REFERENCES provenance(token)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_block_delta ON block_delta(block_id, resource, component,
                                                 COALESCE(entity_id, -1));
CREATE INDEX IF NOT EXISTS ix_block_delta_entity ON block_delta(entity_id, resource);
CREATE TRIGGER IF NOT EXISTS trg_delta_time_has_no_entity
BEFORE INSERT ON block_delta
WHEN NEW.entity_id IS NOT NULL
 AND (SELECT stock_bearing FROM resource WHERE token = NEW.resource) = 0
BEGIN
  SELECT RAISE(ABORT, 'stock_bearing=0 resource cannot bear an entity (time is the block)');
END;
CREATE TRIGGER IF NOT EXISTS trg_delta_time_has_no_entity_upd
BEFORE UPDATE OF entity_id, resource ON block_delta
WHEN NEW.entity_id IS NOT NULL
 AND (SELECT stock_bearing FROM resource WHERE token = NEW.resource) = 0
BEGIN
  SELECT RAISE(ABORT, 'stock_bearing=0 resource cannot bear an entity (time is the block)');
END;
CREATE TRIGGER IF NOT EXISTS trg_delta_origin_writable_ins
BEFORE INSERT ON block_delta
WHEN (SELECT writable FROM provenance WHERE token = NEW.value_origin) = 0
BEGIN
  SELECT RAISE(ABORT, 'value_origin token is migration-only; a new write must state a real origin');
END;
CREATE TRIGGER IF NOT EXISTS trg_delta_origin_writable_upd
BEFORE UPDATE OF value_origin ON block_delta
WHEN (SELECT writable FROM provenance WHERE token = NEW.value_origin) = 0
BEGIN
  SELECT RAISE(ABORT, 'value_origin token is migration-only; a new write must state a real origin');
END;
-- Travel knowledge: the ordered pair is the identity; every number about the
-- pair is a measure row (single home; place.to_min/from_min/prep_min are dead).
CREATE TABLE IF NOT EXISTS place_link (
  id            INTEGER PRIMARY KEY,
  from_place_id INTEGER NOT NULL REFERENCES place(id),
  to_place_id   INTEGER NOT NULL REFERENCES place(id),
  created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  CHECK (from_place_id != to_place_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_place_link_pair ON place_link(from_place_id, to_place_id);
CREATE TABLE IF NOT EXISTS measure (
  id          INTEGER PRIMARY KEY,
  metric      TEXT NOT NULL REFERENCES metric_def(token),
  value       REAL NOT NULL,
  unit        TEXT REFERENCES unit(token),
  qualifier   TEXT,
  basis       TEXT NOT NULL DEFAULT 'actual' CHECK (basis IN ('actual','planned')),
  block_id    INTEGER REFERENCES block(id)  ON DELETE CASCADE,
  entity_id   INTEGER REFERENCES entity(id) ON DELETE CASCADE,
  place_link_id INTEGER REFERENCES place_link(id) ON DELETE CASCADE,
  seq         INTEGER,
  observed_at TEXT NOT NULL,
  source      TEXT NOT NULL,
  confidence  REAL,
  valid_until TEXT,
  note        TEXT,
  value_origin TEXT NOT NULL REFERENCES provenance(token),
  CHECK (block_id IS NOT NULL OR entity_id IS NOT NULL OR place_link_id IS NOT NULL)
);
CREATE TRIGGER IF NOT EXISTS trg_measure_origin_writable_ins
BEFORE INSERT ON measure
WHEN (SELECT writable FROM provenance WHERE token = NEW.value_origin) = 0
BEGIN
  SELECT RAISE(ABORT, 'value_origin token is migration-only; a new write must state a real origin');
END;
CREATE TRIGGER IF NOT EXISTS trg_measure_origin_writable_upd
BEFORE UPDATE OF value_origin ON measure
WHEN (SELECT writable FROM provenance WHERE token = NEW.value_origin) = 0
BEGIN
  SELECT RAISE(ABORT, 'value_origin token is migration-only; a new write must state a real origin');
END;
CREATE INDEX IF NOT EXISTS ix_measure_metric ON measure(metric, observed_at);
CREATE INDEX IF NOT EXISTS ix_measure_block  ON measure(block_id, metric);
CREATE INDEX IF NOT EXISTS ix_measure_entity ON measure(entity_id, metric, observed_at);
CREATE INDEX IF NOT EXISTS ix_measure_place_link ON measure(place_link_id, metric, observed_at);
CREATE UNIQUE INDEX IF NOT EXISTS ux_measure_row ON measure(
  COALESCE(block_id,-1), COALESCE(entity_id,-1), COALESCE(place_link_id,-1),
  metric, COALESCE(seq,-1), basis, observed_at);
-- The achievement-axis link the silent auto-link writes on v3.2
-- (kit_life project auto-link; the compat view reads project_id back from
-- here).  References projects(id) -- the live table on this estate; the
-- authoring `project` table has not landed.
CREATE TABLE IF NOT EXISTS project_block (
  project_id  INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  block_id    INTEGER NOT NULL REFERENCES block(id)    ON DELETE CASCADE,
  PRIMARY KEY (project_id, block_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_project_block_1n ON project_block(block_id);

-- v3.2 registry seeds (rows are vocabulary; users may rename/delete -- the
-- machinery reads flags, not tokens).
INSERT OR IGNORE INTO resource(token, stock_bearing, default_unit) VALUES
  ('time',      0, 'min'),
  ('money',     1, 'krw'),
  ('energy',    1, 'kcal'),
  ('nutrition', 1, 'kcal');
INSERT OR IGNORE INTO unit(token, dimension) VALUES
  ('min','time'), ('s','time'), ('krw','currency'), ('kcal','energy'),
  ('kg','mass'), ('g','mass'), ('count','count'), ('pct','ratio'), ('rir','scale');
INSERT OR IGNORE INTO unit(token, dimension, origin) VALUES ('mg','mass','module');
INSERT OR IGNORE INTO block_outcome(token, counts_as_flow) VALUES
  ('done',      1),
  ('abandoned', 0);
INSERT OR IGNORE INTO block_kind(token, expires) VALUES
  ('task',        1),
  ('appointment', 1),
  ('presence',    0),
  ('record',      0);
INSERT OR IGNORE INTO block_kind_behavior VALUES
  ('task','tasks','task','task',NULL,30,'remind.task','task',0,1,0,0,1,'task','place'),
  ('appointment','calendar','appt',NULL,NULL,120,'remind.appointment','appt',1,1,0,0,1,'appt','meta'),
  ('presence','calendar','travel','task','travel',30,'remind.presence','task',0,0,1,1,1,'presence','required_place'),
  ('record','none',NULL,NULL,NULL,30,'remind.record','task',0,0,0,0,0,'none','none');
INSERT OR IGNORE INTO settle_verdict(token) VALUES ('user'), ('machine'), ('unknown');
INSERT OR IGNORE INTO provenance(token, is_estimate, recomputable, writable, origin) VALUES
  ('measured', 0, 0, 1, 'seed'),
  ('declared', 0, 0, 1, 'seed'),
  ('computed', 0, 1, 1, 'seed'),
  ('default',  1, 1, 1, 'seed'),
  ('unknown',  1, 0, 0, 'seed');
INSERT OR IGNORE INTO label(namespace, token, lang, display) VALUES
  ('provenance','measured','ko','측정됨'), ('provenance','declared','ko','직접 입력'),
  ('provenance','computed','ko','계산됨'), ('provenance','default','ko','기본값'),
  ('provenance','unknown','ko','미상'),
  ('resource','time','ko','시간'), ('resource','money','ko','돈'),
  ('resource','energy','ko','에너지'), ('resource','nutrition','ko','영양');
-- Module metric cargo the live write paths FK into (measure.metric NOT NULL
-- REFERENCES metric_def): the travel lane re-registers these defensively on
-- write; the meal lane does not, so they must exist before the first meal.
INSERT OR IGNORE INTO metric_def(token, scope_kind, accrual, unit_token, owner_module, origin) VALUES
  ('travel_min',      'place_link', 'observed', 'min', 'life', 'module'),
  ('travel_prep_min', 'place_link', 'observed', 'min', 'life', 'module'),
  ('carb',          'block', 'accrued', 'g',  'diet', 'module'),
  ('protein',       'block', 'accrued', 'g',  'diet', 'module'),
  ('fat',           'block', 'accrued', 'g',  'diet', 'module'),
  ('fiber',         'block', 'accrued', 'g',  'diet', 'module'),
  ('sugar',         'block', 'accrued', 'g',  'diet', 'module'),
  ('alt_sugar',     'block', 'accrued', 'g',  'diet', 'module'),
  ('alcohol',       'block', 'accrued', 'g',  'diet', 'module'),
  ('aa_protein',    'block', 'accrued', 'g',  'diet', 'module'),
  ('sodium',        'block', 'accrued', 'mg', 'diet', 'module'),
  ('caffeine',      'block', 'accrued', 'mg', 'diet', 'module'),
  ('portion_grams', 'block', 'accrued', 'g',  'diet', 'module');

-- fresh-mint parity with 036: the seed base place gets its label row
-- (slug('집') degrades to '' -> deterministic fallback 'place_1').
INSERT OR IGNORE INTO label(namespace, token, lang, display) VALUES
  ('place','place_1','ko','집');

CREATE VIEW v_block_span AS
SELECT b.id AS block_id, b.start_at,
       CASE WHEN b.end_at IS NULL THEN '~~~~~~~~~~~~~~~~~~~~'
            WHEN b.end_at = b.start_at
              THEN strftime('%Y-%m-%dT%H:%M:%SZ', b.start_at, '+1 second')
            ELSE b.end_at END AS eff_end
FROM block b
WHERE b.start_at IS NOT NULL;
CREATE VIEW v_block_state AS
SELECT b.id AS block_id,
       CASE
         WHEN b.settled_at IS NOT NULL THEN b.settled_outcome
         WHEN COALESCE(k.expires, 0) = 1
              AND s.eff_end IS NOT NULL AND s.eff_end <> '~~~~~~~~~~~~~~~~~~~~'
              AND strftime('%Y-%m-%dT%H:%M:%SZ','now') >= s.eff_end THEN 'expired'
         ELSE 'open'
       END AS state_token
FROM block b
LEFT JOIN v_block_span s ON s.block_id = b.id
LEFT JOIN block_kind   k ON k.token    = b.kind_token;
CREATE VIEW v_place_link_measure AS
SELECT m.place_link_id, pl.from_place_id, pl.to_place_id,
       m.metric, m.value, m.unit, m.basis, m.value_origin, m.source,
       m.observed_at, m.valid_until
FROM measure m
JOIN place_link pl ON pl.id = m.place_link_id
JOIN place pf ON pf.id = pl.from_place_id AND pf.status = 'active'
JOIN place pt ON pt.id = pl.to_place_id  AND pt.status = 'active'
WHERE (m.valid_until IS NULL
       OR m.valid_until > strftime('%Y-%m-%dT%H:%M:%SZ','now'))
  AND m.id = (SELECT m2.id FROM measure m2
               WHERE m2.place_link_id = m.place_link_id
                 AND m2.metric = m.metric AND m2.basis = m.basis
                 AND (m2.valid_until IS NULL
                      OR m2.valid_until > strftime('%Y-%m-%dT%H:%M:%SZ','now'))
               ORDER BY m2.observed_at DESC, m2.id DESC LIMIT 1);
CREATE VIEW timeblock AS
SELECT b.id                AS id,
       b.ulid              AS ulid,
       b.title             AS title,
       b.start_at          AS start_at,
       b.end_at            AS end_at,
       b.kind_token        AS kind,
       s.state_token       AS status,
       b.settled_at        AS settled_at,
       b.settled_outcome   AS settled_outcome,
       b.settled_by        AS settled_by,
       b.place_id          AS place_id,
       b.rec_date          AS rec_date,
       b.created_at        AS created_at,
       (SELECT a.ulid FROM block a WHERE a.id = b.derived_from) AS derived_from,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'schedule_kind' LIMIT 1) AS schedule_kind,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'completion_rule' LIMIT 1) AS completion_rule,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'display_tz' LIMIT 1) AS display_tz,
       CAST((SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'slot_exclusive' LIMIT 1) AS INTEGER)
                                       AS slot_exclusive,
       CAST(COALESCE((SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'open_ended' LIMIT 1),
              CASE WHEN b.start_at IS NOT NULL AND b.end_at IS NULL
                   THEN 1 ELSE 0 END) AS INTEGER)
                                       AS open_ended,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'location' LIMIT 1) AS location,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'location_url' LIMIT 1) AS location_url,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'purpose' LIMIT 1) AS purpose,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'summary' LIMIT 1) AS summary,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'gtask_id' LIMIT 1) AS gtask_id,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'notify_policy' LIMIT 1) AS notify_policy,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'block_class' LIMIT 1) AS block_class,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'derived_role' LIMIT 1) AS derived_role,
       CAST(COALESCE((SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'derived_pinned' LIMIT 1), 0) AS INTEGER)
                                       AS derived_pinned,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'derived_delta' LIMIT 1) AS derived_delta,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'time_mode' LIMIT 1) AS time_mode,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'note' LIMIT 1) AS note,
       CAST(COALESCE((SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'version' LIMIT 1), 0) AS INTEGER)
                                       AS version,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'updated_at' LIMIT 1) AS updated_at,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'owning_agent' LIMIT 1) AS owning_agent,
       CAST((SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'notify_lead_min' LIMIT 1) AS INTEGER)
                                       AS notify_lead_min,
       (SELECT pb.project_id FROM project_block pb WHERE pb.block_id = b.id) AS project_id,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'recurrence_id' LIMIT 1) AS recurrence_id,
       CAST(COALESCE((SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'rec_exception' LIMIT 1), 0) AS INTEGER)
                                       AS rec_exception,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'created_by' LIMIT 1) AS created_by,
       CAST(COALESCE((SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'is_routine' LIMIT 1), 0) AS INTEGER)
                                       AS is_routine,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'gcal_event_id' LIMIT 1) AS gcal_event_id,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'gcal_etag' LIMIT 1) AS gcal_etag,
       (SELECT value FROM block_attr x WHERE x.block_id = b.id
               AND x.attr_token = 'gtask_etag' LIMIT 1) AS gtask_etag,
       NULL                AS area_id,  -- UNMAPPED(O-7); old readers may still name it
       b.retro_line        AS retro_line
FROM block b
LEFT JOIN v_block_state s ON s.block_id = b.id
WHERE b.ulid NOT LIKE 'x-%';
CREATE VIEW travel_edge AS
SELECT pl.id            AS id,
       pl.from_place_id AS from_place_id,
       pl.to_place_id   AS to_place_id,
       CAST(tm.value AS INTEGER) AS minutes,
       CAST(pm.value AS INTEGER) AS prep_min,
       pl.created_at    AS created_at,
       COALESCE(MAX(tm.observed_at, pm.observed_at), tm.observed_at) AS updated_at
FROM place_link pl
JOIN v_place_link_measure tm
     ON tm.place_link_id = pl.id AND tm.metric = 'travel_min'
    AND tm.basis = 'actual'
LEFT JOIN v_place_link_measure pm
     ON pm.place_link_id = pl.id AND pm.metric = 'travel_prep_min'
    AND pm.basis = 'actual';
CREATE VIEW v_daily_energy AS
SELECT
  d.date,
  COALESCE(m.intake_kcal, 0)  AS intake_kcal,
  COALESCE(m.protein_g,  0)   AS protein_g,
  COALESCE(m.carb_g,     0)   AS carb_g,
  COALESCE(m.fat_g,      0)   AS fat_g,
  COALESCE(w.burn_kcal,  0)   AS burn_kcal,
  COALESCE(w.workout_min,0)   AS workout_min
FROM
  (SELECT date FROM block WHERE settled_by = 'meal-record'
   UNION SELECT date FROM workouts) d
  LEFT JOIN (
    SELECT b.date AS date,
           SUM(COALESCE((SELECT value FROM measure WHERE block_id=b.id AND metric='kcal_intake' AND source='module:made-dish' LIMIT 1), ROUND(
               COALESCE((SELECT m.value FROM measure m WHERE m.block_id = b.id AND m.metric = 'protein' LIMIT 1),0)*4
             + COALESCE((SELECT m.value FROM measure m WHERE m.block_id = b.id AND m.metric = 'fat' LIMIT 1),0)*9
             + (COALESCE((SELECT m.value FROM measure m WHERE m.block_id = b.id AND m.metric = 'carb' LIMIT 1),0)
                - COALESCE((SELECT m.value FROM measure m WHERE m.block_id = b.id AND m.metric = 'fiber' LIMIT 1),0))*4
             + COALESCE((SELECT m.value FROM measure m WHERE m.block_id = b.id AND m.metric = 'alcohol' LIMIT 1),0)*7
           , 2))) AS intake_kcal,
           SUM(COALESCE((SELECT m.value FROM measure m WHERE m.block_id = b.id AND m.metric = 'protein' LIMIT 1),0)) AS protein_g,
           SUM(COALESCE((SELECT m.value FROM measure m WHERE m.block_id = b.id AND m.metric = 'carb' LIMIT 1),0))    AS carb_g,
           SUM(COALESCE((SELECT m.value FROM measure m WHERE m.block_id = b.id AND m.metric = 'fat' LIMIT 1),0))     AS fat_g
    FROM block b WHERE b.settled_by = 'meal-record' GROUP BY b.date
  ) m ON m.date = d.date
  LEFT JOIN (
    SELECT date, SUM(kcal) burn_kcal, SUM(minutes) workout_min
    FROM workouts GROUP BY date
  ) w ON w.date = d.date;
CREATE VIEW v_event_resolution AS
SELECT e.id, e.ulid, e.kind, e.time_mode,
  CASE
    WHEN e.settled_outcome = 'abandoned' THEN 'skipped'
    WHEN e.settled_outcome = 'done' THEN
         CASE WHEN e.time_mode = 'anchored' THEN 'happened' ELSE 'done' END
    WHEN e.time_mode = 'anchored'
         AND (CASE WHEN e.end_at IS NULL THEN '~~~~~~~~~~~~~~~~~~~~'
                   WHEN e.end_at = e.start_at THEN
                        strftime('%Y-%m-%dT%H:%M:%SZ', e.start_at, '+1 second')
                   ELSE e.end_at END)
             <= strftime('%Y-%m-%dT%H:%M:%SZ','now')
      THEN 'missed'
    ELSE 'open'
  END AS resolution
FROM timeblock e
WHERE e.kind != 'presence';

-- ── 036 park husks ───────────────────────────────────────────────────────
-- The in-place migration freezes the four consumed v35 sources under the
-- _l35_ prefix (verbatim rows; values with no seat on the new axis stay
-- recoverable there).  A fresh mint carries the same tables EMPTY so a
-- migrated database and a fresh one are structurally identical
-- (fresh == migrated, the 008/023 parity rule).
CREATE TABLE IF NOT EXISTS _l35_timeblock(
  id INT,
  ulid TEXT,
  kind TEXT,
  title TEXT,
  note TEXT,
  area_id INT,
  schedule_kind TEXT,
  start_at TEXT,
  end_at TEXT,
  display_tz TEXT,
  open_ended INT,
  slot_exclusive INT,
  status TEXT,
  completion_rule TEXT,
  completion_n INT,
  version INT,
  settled_at TEXT,
  settled_by TEXT,
  settled_outcome TEXT,
  owning_agent TEXT,
  created_by TEXT,
  earliest TEXT,
  latest TEXT,
  anchor TEXT,
  immovable INT,
  priority TEXT,
  seq REAL,
  is_routine INT,
  location TEXT,
  location_url TEXT,
  purpose TEXT,
  summary TEXT,
  recurrence_id TEXT,
  rec_date TEXT,
  rec_exception INT,
  notion_id TEXT,
  gcal_event_id TEXT,
  gtask_id TEXT,
  gcal_etag TEXT,
  gtask_etag TEXT,
  notify_policy TEXT,
  notify_lead_min INT,
  block_class TEXT,
  derived_from TEXT,
  derived_role TEXT,
  derived_pinned INT,
  derived_delta TEXT,
  time_mode TEXT,
  place_id INT,
  created_at TEXT,
  updated_at TEXT,
  exec_context TEXT,
  placement_rule TEXT,
  placement_fallback TEXT,
  placement_depends_on TEXT,
  attn_demand TEXT,
  body_demand TEXT,
  resolver_state TEXT,
  project_id INT
);
CREATE TABLE IF NOT EXISTS _l35_travel_edge(
  id INT,
  from_place_id INT,
  to_place_id INT,
  minutes INT,
  prep_min INT,
  created_at TEXT,
  updated_at TEXT
);
CREATE TABLE IF NOT EXISTS _l35_place(
  id INT,
  ulid TEXT,
  name TEXT,
  aliases TEXT,
  to_min INT,
  from_min INT,
  prep_min INT,
  note TEXT,
  created_at TEXT,
  updated_at TEXT,
  is_base INT,
  granularity TEXT,
  display_name TEXT,
  address TEXT
);
CREATE TABLE IF NOT EXISTS _l35_meals(
  id INT,
  date TEXT,
  meal TEXT,
  name TEXT,
  grams REAL,
  carb REAL,
  protein REAL,
  fat REAL,
  fiber REAL,
  sugar REAL,
  alt_sugar REAL,
  alcohol REAL,
  kcal REAL,
  area_id INT,
  notion_id TEXT,
  owning_agent TEXT,
  created_at TEXT,
  aa_protein REAL,
  sodium_mg REAL,
  caffeine_mg REAL
);

-- ── Judgment layer -- migration 037 (user_version 37) ─────────────────────
-- Managing a domain is an ABILITY (되비출 수 있는 상태), not the existence of
-- a goal row.  The judgement is SAYABILITY: can a reflection sentence be built
-- for this domain right now?  It needs three ingredients -- a criterion
-- (v_criterion: goal targets AND rules, each inside its validity window), a
-- value (an ACTUAL, still-in-force measure row on that metric), and freshness
-- (observed_at newer than that metric's declared arrival window in
-- metric_freshness).  Miss one and there is no sentence: no door -> 'unknown',
-- values but no criterion -> 'collecting', criterion + fresh value ->
-- 'managing'.
-- Freshness is NOT measure.valid_until -- that column is a SUCCESSION stamp,
-- read here only as "still in force"; reading it as freshness made a 2019 value
-- read as 요즘 on every one of the 6,060 live rows (they are all NULL).  How
-- stale is stale is the OWNER's call per metric, so it is DATA
-- (metric_freshness), and a metric with no declared interval is never fresh --
-- fail-CLOSED, silence rather than a lie.  The door table is `inflow`, never
-- `intake` (that word already means 섭취 here); kind/face_* are NULLABLE
-- because a back-declaring migration cannot know them and must not freeze the
-- undecided face vocabulary; BOTH criterion arms (goal_target and commitment)
-- carry the version/valid_from/valid_until/supersedes quad and a trigger that
-- refuses an in-place edit of the comparison numbers, so a target or rule
-- change cannot rewrite last week's verdict; v_criterion_span is the as-of read
-- path and v_criterion is that span at 'now'; v_criterion_span INNER JOINs
-- stage, so a stage-less goal is fail-CLOSED.  Storage stays split between
-- goal_target and commitment (the owner's 2026-08-24 goal/rule separation) --
-- only the read is unified.  The seeding at the end declares the domain roster
-- plus the doors that ARRIVAL evidence (created_at, never the claimed event
-- date) proves; no goal, stage, face token or freshness interval is seeded,
-- because those are the owner's.  The ledger_goal transfer is deliberately NOT
-- here.  See migrations/037_judgment_layer.sql for the full rationale.
-- 038 (grade clamp): 037's grade floor could rewrite a non-canonical
-- observed_at to a FUTURE instant (a local-grade stamp between 00:00 and
-- 09:00 local on a UTC+9 estate) -- negative age, passes every window, 9h/day
-- fail-OPEN.  v_domain_state now also clamps the graded instant to <= 'now';
-- see migrations/038_judgment_grade_clamp.sql.
-- 1. Seats.  stage before goal (FK), domain before inflow/domain_metric,
--    goal before goal_target/commitment/goal_thesis, thesis before goal_thesis,
--    metric_freshness after nothing (it FKs metric_def, which predates 037).
CREATE TABLE IF NOT EXISTS stage (
  token       TEXT PRIMARY KEY,
  lifecycle   TEXT NOT NULL CHECK (lifecycle IN ('open','held','closed','void')),
  sort_rank   INTEGER,
  origin      TEXT NOT NULL DEFAULT 'seed' CHECK (origin IN ('seed','module','user'))
);
CREATE TABLE IF NOT EXISTS domain (
  id           INTEGER PRIMARY KEY,
  ulid         TEXT NOT NULL UNIQUE,
  token        TEXT NOT NULL UNIQUE,
  module_token TEXT,
  note         TEXT,
  origin       TEXT NOT NULL DEFAULT 'seed' CHECK (origin IN ('seed','module','user')),
  -- regrill B7 / grill C-7: without this roster one typo ('actvie') drops the
  -- whole domain out of v_domain_state -- an unnamed fourth state, ABSENCE,
  -- which is worse than silence because silence is visible.
  status       TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
  created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE TABLE IF NOT EXISTS inflow (
  id           INTEGER PRIMARY KEY,
  ulid         TEXT NOT NULL UNIQUE,
  domain_id    INTEGER NOT NULL REFERENCES domain(id) ON DELETE CASCADE,
  kind         TEXT CHECK (kind IN ('auto','utterance')),
  module_token TEXT,
  face_when    TEXT,
  face_what    TEXT,
  face_how     TEXT,
  status       TEXT NOT NULL DEFAULT 'active'
               CHECK (status IN ('active','paused','retired')),
  declared_by  TEXT NOT NULL DEFAULT 'module'
               CHECK (declared_by IN ('module','user','migration')),
  created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_inflow_domain ON inflow(domain_id, status);
CREATE TABLE IF NOT EXISTS goal (
  id           INTEGER PRIMARY KEY,
  ulid         TEXT NOT NULL UNIQUE,
  title        TEXT NOT NULL,
  horizon_kind TEXT NOT NULL
               CHECK (horizon_kind IN ('thesis_bound','bounded','periodic')),
  horizon_date TEXT,
  stage_token  TEXT REFERENCES stage(token),
  focus_state  TEXT NOT NULL DEFAULT 'backlog'
               CHECK (focus_state IN ('focus','backlog','dormant')),
  priority_rank INTEGER,
  created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE TABLE IF NOT EXISTS goal_target (
  id           INTEGER PRIMARY KEY,
  goal_id      INTEGER NOT NULL REFERENCES goal(id) ON DELETE CASCADE,
  metric       TEXT    NOT NULL REFERENCES metric_def(token),
  baseline_value REAL,
  baseline_as_of TEXT,
  target_low   REAL,
  target_high  REAL,
  version      INTEGER NOT NULL DEFAULT 1,
  valid_from   TEXT,
  valid_until  TEXT,
  supersedes   INTEGER REFERENCES goal_target(id),
  applies_when TEXT,
  rung_state   TEXT NOT NULL DEFAULT 'intent'
               CHECK (rung_state IN ('intent','baseline_pending','target_proposed',
                                     'confirmed','tracking','met','retired')),
  CHECK (target_low IS NOT NULL OR target_high IS NOT NULL),
  CHECK (target_low IS NULL OR target_high IS NULL OR target_low <= target_high),
  -- regrill B6: the grade is enforced by the TABLE, not by a comment.  A
  -- local-time stamp compared against a '...T...Z' now reads as live until
  -- local midnight (' ' 0x20 < 'T' 0x54) -- up to 9h fail-OPEN in KST.
  CHECK (valid_from IS NULL OR valid_from GLOB
         '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
  CHECK (valid_until IS NULL OR valid_until GLOB
         '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
  CHECK (valid_from IS NULL OR valid_until IS NULL OR valid_from < valid_until)
);
CREATE INDEX IF NOT EXISTS ix_goal_target_goal ON goal_target(goal_id);
CREATE TABLE IF NOT EXISTS commitment (
  id          INTEGER PRIMARY KEY,
  ulid        TEXT NOT NULL UNIQUE,
  goal_id     INTEGER REFERENCES goal(id) ON DELETE SET NULL,
  title       TEXT NOT NULL,
  kind        TEXT NOT NULL CHECK (kind IN ('keep','limit','window')),
  metric      TEXT REFERENCES metric_def(token),
  lo          REAL,
  hi          REAL,
  period      TEXT CHECK (period IN ('day','week','month','quarter','year')),
  enforcement TEXT NOT NULL DEFAULT 'inform'
              CHECK (enforcement IN ('block','hold','inform')),
  spec        TEXT,
  status      TEXT NOT NULL DEFAULT 'active'
              CHECK (status IN ('active','paused','retired')),
  -- regrill B4: the rule arm gets the same four columns the goal arm has.  2 of
  -- the 3 criteria the owner tracks today are rules, and without these a rule
  -- revision was BY DEFINITION an in-place UPDATE that rewrote last week's
  -- verdict (measured: OVER -> ok on an already-measured day).
  version     INTEGER NOT NULL DEFAULT 1,
  valid_from  TEXT,
  valid_until TEXT,
  supersedes  INTEGER REFERENCES commitment(id),
  created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  CHECK (valid_from IS NULL OR valid_from GLOB
         '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
  CHECK (valid_until IS NULL OR valid_until GLOB
         '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'),
  CHECK (valid_from IS NULL OR valid_until IS NULL OR valid_from < valid_until)
);
CREATE TABLE IF NOT EXISTS domain_metric (
  domain_id   INTEGER NOT NULL REFERENCES domain(id) ON DELETE CASCADE,
  metric      TEXT    NOT NULL REFERENCES metric_def(token) ON DELETE CASCADE,
  PRIMARY KEY (domain_id, metric)
);
-- regrill B1.  "How stale is stale" is a per-metric PRODUCT judgement (체지방
-- arrives every ~9.8 days, a meal every day), so it is DATA, not a constant in
-- a view.  declared_by keeps a number nobody chose visibly a number nobody
-- chose.  A metric with NO row here is never fresh -- fail-CLOSED, so an
-- undeclared interval produces silence, never a false '관리 중'.
CREATE TABLE IF NOT EXISTS metric_freshness (
  metric            TEXT PRIMARY KEY REFERENCES metric_def(token) ON DELETE CASCADE,
  stale_after_hours REAL NOT NULL CHECK (stale_after_hours > 0),
  declared_by       TEXT NOT NULL DEFAULT 'module'
                    CHECK (declared_by IN ('module','user','migration')),
  note              TEXT,
  created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
-- 044 (criterion period).  The SEAT for period boundaries --
-- metric_freshness's shape, for the calendar.  Where a week starts and
-- which local midnight closes a day are the OWNER's clock, so they are
-- DATA declared once, never a constant in view SQL.  Shipped EMPTY: an
-- undeclared period keeps every criterion on it silent (fail-CLOSED).
-- See migrations/044_criterion_period.sql for the full rationale.
CREATE TABLE IF NOT EXISTS period_anchor (
  period          TEXT PRIMARY KEY
                  CHECK (period IN ('day','week','month','quarter','year')),
  -- The declared local-clock offset, minutes east of UTC (real offsets
  -- include :30/:45, so minutes, not hours).  This is the SAME value the
  -- instance declares as AGENT_TZ_OFFSET_HOURS -- landed here as data
  -- because view SQL cannot reach config/agent.conf (038 discipline).
  utc_offset_min  INTEGER NOT NULL CHECK (utc_offset_min BETWEEN -720 AND 840),
  -- Week start in strftime('%w') numbering (0=Sunday .. 6=Saturday) --
  -- SQLite's own day numbering, reused instead of minting a second one.
  -- Required for 'week' (refused at write, never guessed at read) and
  -- refused elsewhere (a month has no week_start; a stored one would be a
  -- dead knob that reads as if it did something).
  week_start      INTEGER CHECK (week_start BETWEEN 0 AND 6),
  declared_by     TEXT NOT NULL DEFAULT 'module'
                  CHECK (declared_by IN ('module','user','migration')),
  note            TEXT,
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  CHECK (period <> 'week' OR week_start IS NOT NULL),
  CHECK (period = 'week' OR week_start IS NULL)
);
CREATE TABLE IF NOT EXISTS thesis (
  id           INTEGER PRIMARY KEY,
  ulid         TEXT NOT NULL UNIQUE,
  text         TEXT NOT NULL,
  stated_on    TEXT,
  emerged_from INTEGER REFERENCES event(id),
  status       TEXT NOT NULL DEFAULT 'active'
               CHECK (status IN ('active','pivoted','retired')),
  superseded_by INTEGER REFERENCES thesis(id),
  owner        TEXT NOT NULL DEFAULT 'user' CHECK (owner = 'user'),
  created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE TABLE IF NOT EXISTS goal_thesis (
  goal_id   INTEGER NOT NULL REFERENCES goal(id)    ON DELETE CASCADE,
  thesis_id INTEGER NOT NULL REFERENCES thesis(id)  ON DELETE CASCADE,
  PRIMARY KEY (goal_id, thesis_id)
);
-- 2. The succession discipline, ENFORCED (regrill B4).  A criterion in force is
--    never edited: the comparison numbers are immutable, so a revision must
--    stamp valid_until on this row and INSERT a successor.  Stamping is an
--    UPDATE of valid_until (not listed below, so it passes), and rung_state /
--    status transitions pass too -- only the NUMBERS are frozen.
CREATE TRIGGER IF NOT EXISTS trg_goal_target_no_inplace_edit
BEFORE UPDATE OF metric, target_low, target_high ON goal_target
WHEN NEW.metric     IS NOT OLD.metric
  OR NEW.target_low IS NOT OLD.target_low
  OR NEW.target_high IS NOT OLD.target_high
BEGIN
  SELECT RAISE(ABORT, 'goal_target: a criterion in force is never edited in place -- stamp valid_until on this row and INSERT a successor (version+1, supersedes = this id)');
END;
CREATE TRIGGER IF NOT EXISTS trg_commitment_no_inplace_edit
BEFORE UPDATE OF metric, lo, hi ON commitment
WHEN NEW.metric IS NOT OLD.metric
  OR NEW.lo     IS NOT OLD.lo
  OR NEW.hi     IS NOT OLD.hi
BEGIN
  SELECT RAISE(ABORT, 'commitment: a rule in force is never edited in place -- stamp valid_until on this row and INSERT a successor (version+1, supersedes = this id)');
END;
-- 3. Stored time-string grade on the succession stamp (regrill B6).  measure
--    predates 037, so a CHECK would need a table rebuild; a trigger enforces the
--    same rule additively.  Safe: every live row is NULL here and the single
--    writer (kit_life _travel_v32_set) already emits now_utc().  observed_at is
--    deliberately NOT constrained -- it legitimately carries two grades today --
--    and the view floors the non-canonical grade instead (fail-CLOSED).
CREATE TRIGGER IF NOT EXISTS trg_measure_valid_until_grade_ins
BEFORE INSERT ON measure
WHEN NEW.valid_until IS NOT NULL
 AND NEW.valid_until NOT GLOB
     '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
BEGIN
  SELECT RAISE(ABORT, 'measure.valid_until must be a canonical UTC-Z instant (YYYY-MM-DDTHH:MM:SSZ): a local-time stamp compares as still-in-force until local midnight');
END;
CREATE TRIGGER IF NOT EXISTS trg_measure_valid_until_grade_upd
BEFORE UPDATE OF valid_until ON measure
WHEN NEW.valid_until IS NOT NULL
 AND NEW.valid_until NOT GLOB
     '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
BEGIN
  SELECT RAISE(ABORT, 'measure.valid_until must be a canonical UTC-Z instant (YYYY-MM-DDTHH:MM:SSZ): a local-time stamp compares as still-in-force until local midnight');
END;
-- 4. The four read windows.  v_criterion_span states every non-time rule ONCE
--    and applies no clock (the AS-OF path); v_criterion is that span at 'now';
--    v_domain_state judges the three states by asking whether a sentence can be
--    built; v_criterion_verdict (044) is the VALUE side -- the number and the
--    band comparison behind each sayable criterion, point and period alike.
--    DROP-then-CREATE, not IF NOT EXISTS: a name that already exists with a
--    stale definition must be REPLACED, not silently kept (regrill S3).
--    Views hold no data, so dropping one costs nothing.
DROP VIEW IF EXISTS v_criterion_verdict;
DROP VIEW IF EXISTS v_domain_state;
DROP VIEW IF EXISTS v_criterion;
DROP VIEW IF EXISTS v_criterion_span;
CREATE VIEW v_criterion_span AS
  SELECT 'goal' AS source, gt.id AS criterion_id, gt.metric AS metric,
         gt.target_low AS lo, gt.target_high AS hi, gt.goal_id AS goal_id,
         gt.version AS version, gt.valid_from AS valid_from,
         gt.valid_until AS valid_until, gt.supersedes AS supersedes,
         -- 044: the goal arm has no period column BY DESIGN -- the owner's
         -- 08-24 split keeps cadence on the rule arm; a goal target is a
         -- 조준점 (aiming point), so it projects NULL and is judged as a
         -- point criterion.
         NULL AS period
    FROM goal_target gt
    JOIN goal g   ON g.id = gt.goal_id
    JOIN stage s  ON s.token = g.stage_token
   WHERE gt.rung_state = 'tracking'
     AND g.focus_state <> 'dormant'
     AND s.lifecycle = 'open'
     AND (gt.target_low IS NOT NULL OR gt.target_high IS NOT NULL)
  UNION ALL
  SELECT 'rule', c.id, c.metric, c.lo, c.hi, c.goal_id,
         c.version, c.valid_from, c.valid_until, c.supersedes,
         -- 044: period was stored here since 037 and never projected -- the
         -- reason a per-period rule was judged as a point rule.
         c.period
    FROM commitment c
   WHERE c.status = 'active'
     AND c.metric IS NOT NULL
     AND (c.lo IS NOT NULL OR c.hi IS NOT NULL);
CREATE VIEW v_criterion AS
  SELECT source, criterion_id, metric, lo, hi, goal_id,
         version, valid_from, valid_until, supersedes, period
    FROM v_criterion_span
   WHERE (valid_from  IS NULL
          OR valid_from  <= strftime('%Y-%m-%dT%H:%M:%SZ','now'))
     AND (valid_until IS NULL
          OR valid_until >  strftime('%Y-%m-%dT%H:%M:%SZ','now'));
CREATE VIEW v_domain_state AS
WITH fresh_metric AS (
  -- A value you can say '요즘' about.  THREE independent conditions, none of
  -- them the one the first cut used:
  --   basis='actual'      a plan is not a reflection            (regrill B2)
  --   valid_until         still in force, i.e. not superseded / retracted.
  --                       This column is a SUCCESSION stamp and nothing else
  --                       (regrill B1) -- it is no longer read as freshness.
  --   observed_at + interval   the actual age test, against the metric's own
  --                       declared arrival window.  No declaration -> the JOIN
  --                       drops the metric -> never fresh (fail-CLOSED).
  -- GRADE FLOOR + FUTURE CLAMP (038): observed_at carries two grades in this
  -- estate (canonical instants from travel, date-only stamps from nutrition).
  -- Anything that is not a canonical instant is floored to 00:00:00Z of its
  -- own date -- and the graded instant must ALSO not exceed 'now'.  Without
  -- the clamp, a LOCAL-grade stamp floored to a FUTURE instant between 00:00
  -- and 09:00 local on a UTC+9 estate: negative age, passes every window,
  -- 9h/day fail-OPEN (the opposite of the sign 037's header promised).  The
  -- view cannot read the instance offset (config/agent.conf is unreachable
  -- from SQL) and hardcoding one would mint a fourth clock, so ambiguity
  -- fail-CLOSES: an instant claiming the future is not fresh evidence, and
  -- the residual error stays bounded and always toward STALE.
  SELECT DISTINCT m.metric AS metric
    FROM measure m
    JOIN metric_freshness mf ON mf.metric = m.metric
   WHERE m.basis = 'actual'
     AND (m.valid_until IS NULL
          OR m.valid_until > strftime('%Y-%m-%dT%H:%M:%SZ','now'))
     AND (CASE WHEN m.observed_at GLOB
                    '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
               THEN m.observed_at
               ELSE substr(m.observed_at, 1, 10) || 'T00:00:00Z' END)
         <= strftime('%Y-%m-%dT%H:%M:%SZ','now')
     AND (CASE WHEN m.observed_at GLOB
                    '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
               THEN m.observed_at
               ELSE substr(m.observed_at, 1, 10) || 'T00:00:00Z' END)
         > strftime('%Y-%m-%dT%H:%M:%SZ','now',
                    '-' || CAST(mf.stale_after_hours AS TEXT) || ' hours')
),
sayable AS (
  SELECT dm.domain_id AS domain_id,
         COUNT(DISTINCT c.metric) AS n_sayable_metrics
    FROM domain_metric dm
    JOIN v_criterion  c ON c.metric = dm.metric
   -- 044: the judgement READS period.  A POINT criterion (period NULL) is
   -- sayable exactly as before: its metric must be FRESH by its own B1
   -- window (a LEVEL decays -- an old reading stops estimating now).  A
   -- PERIOD criterion speaks about an AGGREGATE, so it is sayable only
   -- when that aggregate is constructible: the metric's rows must be
   -- increments (metric_def.accrual = 'accrued' -- summing a LEVEL like
   -- body fat is nonsense the kit refuses to compute, and which honest
   -- aggregate a level takes is a DOMAIN choice, not the kit's) AND the
   -- period's boundary must be DECLARED (period_anchor -- where a week
   -- starts is the owner's clock; no declaration -> silence, fail-CLOSED,
   -- the metric_freshness shape).
   -- 045: point-freshness is NOT a leg of the period shape any more.  An
   -- accrued period sum is a count over a CLOSED set of in-force rows
   -- inside a bounded window -- it does not decay the way a level does,
   -- so the age of the LATEST arrival has no bearing on whether the sum
   -- is correct (it is, exactly).  Under 044 the freshness leg deleted
   -- the sentence precisely when it said the most: a long recording gap
   -- read as silence instead of the true "0 this period" (measured on
   -- live: a 94-day session gap, ~87 of those days verdict-less under any
   -- week-scale cadence).  v_criterion_verdict restates these same legs
   -- on the value side and carries last_logged_at so a recording gap is
   -- SHOWN to the consumer, never judged by a kit-invented threshold.
   WHERE (c.period IS NULL
          AND EXISTS (SELECT 1 FROM fresh_metric f
                       WHERE f.metric = dm.metric))
      OR (c.period IS NOT NULL
          AND EXISTS (SELECT 1 FROM metric_def md
                       WHERE md.token = c.metric AND md.accrual = 'accrued')
          AND EXISTS (SELECT 1 FROM period_anchor pa
                       WHERE pa.period = c.period))
   GROUP BY dm.domain_id
)
SELECT d.id AS domain_id, d.token,
       (SELECT COUNT(*) FROM inflow f
         WHERE f.domain_id = d.id AND f.status = 'active')            AS n_inflows,
       COALESCE(sy.n_sayable_metrics, 0)                              AS n_sayable,
       (SELECT COUNT(DISTINCT c2.goal_id) FROM v_criterion c2
          JOIN domain_metric dm2 ON dm2.metric = c2.metric
          JOIN goal_thesis gth   ON gth.goal_id = c2.goal_id
         WHERE dm2.domain_id = d.id)                                  AS n_spiral_linked,
       CASE
         WHEN (SELECT COUNT(*) FROM inflow f
                WHERE f.domain_id = d.id AND f.status = 'active') = 0
              THEN 'unknown'
         WHEN COALESCE(sy.n_sayable_metrics, 0) = 0
              THEN 'collecting'
         ELSE 'managing'
       END                                                            AS state
FROM domain d
LEFT JOIN sayable sy ON sy.domain_id = d.id
WHERE d.status = 'active';
-- 3. The VALUE side of the judgement (044).  One row per live criterion
--    whose sentence is constructible RIGHT NOW; a criterion missing any leg
--    has no row (silence, never a guessed number).  This is the view that
--    makes a period rule and a point rule with identical lo/hi reach
--    DIFFERENT verdicts on the same data -- before 044 they could not.
CREATE VIEW v_criterion_verdict AS
WITH live_value AS (
  -- In-force actual rows, graded: the ONE value idiom of this layer
  -- (037 grade floor + 038 future clamp), restated per-row because this
  -- view needs the VALUES, not just the metric's existence.
  SELECT m.metric AS metric, m.value AS value,
         (CASE WHEN m.observed_at GLOB
                    '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'
               THEN m.observed_at
               ELSE substr(m.observed_at, 1, 10) || 'T00:00:00Z' END) AS graded_at
    FROM measure m
   WHERE m.basis = 'actual'
     AND (m.valid_until IS NULL
          OR m.valid_until > strftime('%Y-%m-%dT%H:%M:%SZ','now'))
),
fresh_point AS (
  -- The freshest fresh value per metric (B1: inside the declared arrival
  -- window; no metric_freshness row -> no row here -> fail-CLOSED).  The
  -- bare `value` column rides SQLite's documented MAX() row guarantee; on
  -- an exact graded_at tie the picked row is arbitrary, which is the same
  -- honest ambiguity the tie itself carries.
  SELECT lv.metric AS metric, lv.value AS value, MAX(lv.graded_at) AS graded_at
    FROM live_value lv
    JOIN metric_freshness mf ON mf.metric = lv.metric
   WHERE lv.graded_at <= strftime('%Y-%m-%dT%H:%M:%SZ','now')
     AND lv.graded_at > strftime('%Y-%m-%dT%H:%M:%SZ','now',
                                 '-' || CAST(mf.stale_after_hours AS TEXT) || ' hours')
   GROUP BY lv.metric
),
period_window AS (
  -- [start of the CURRENT period, now], computed from the DECLARED anchor
  -- only -- an undeclared period has no row here and every criterion on it
  -- stays silent.  The local day is found by shifting 'now' by the declared
  -- offset, the boundary is taken in local terms, and the boundary is
  -- shifted back to a canonical UTC-Z instant so it compares against graded
  -- observed_at with plain string ordering (036/038: one clock, one format).
  SELECT pa.period AS period,
         strftime('%Y-%m-%dT%H:%M:%SZ',
           datetime(CASE pa.period
             WHEN 'day'   THEN date(datetime('now', pa.utc_offset_min || ' minutes'))
             WHEN 'week'  THEN date(datetime('now', pa.utc_offset_min || ' minutes'),
                                    '-' || ((CAST(strftime('%w', datetime('now', pa.utc_offset_min || ' minutes')) AS INTEGER)
                                             - pa.week_start + 7) % 7) || ' days')
             WHEN 'month' THEN date(datetime('now', pa.utc_offset_min || ' minutes'),
                                    'start of month')
             WHEN 'quarter' THEN date(datetime('now', pa.utc_offset_min || ' minutes'),
                                    'start of month',
                                    '-' || ((CAST(strftime('%m', datetime('now', pa.utc_offset_min || ' minutes')) AS INTEGER) - 1) % 3) || ' months')
             WHEN 'year'  THEN date(datetime('now', pa.utc_offset_min || ' minutes'),
                                    'start of year')
           END, (-pa.utc_offset_min) || ' minutes')) AS win_start
    FROM period_anchor pa
),
period_value AS (
  -- The aggregate: SUM, and only over accrual='accrued' metrics (an
  -- accrued row IS an increment, so the period total is the sum by that
  -- column's own definition; for a level the honest aggregate is a domain
  -- choice the kit must not guess -- no row, silence).  045: fresh_point
  -- is deliberately NOT joined here any more.  The sum is exact over a
  -- CLOSED set of in-force rows inside the declared window, however old
  -- the latest arrival is -- point-freshness answers a LEVEL question
  -- ("does the last reading still estimate now?") that an accrued sum
  -- never asks.  044's stated fear (a Monday binge reading as a current
  -- weekly total all week) describes a TRUE statement -- four sessions on
  -- Monday IS the week's total until another arrives -- and the pending
  -- asymmetry below already keeps a partial sum from ever manufacturing
  -- 'under'.  The real residual (the owner keeps doing but stops LOGGING)
  -- is SURFACED, not judged: last_logged_at rides along so the consumer
  -- can tell "0 and the last log was 2 days ago" from "0 and the last log
  -- was 94 days ago"; which of those is bad is the owner's call and no
  -- kit threshold pre-empts it (the 042 discipline).  COALESCE 0: an
  -- empty window honestly means nothing was RECORDED this period --
  -- last_logged_at carries the context that says how far away the record
  -- last spoke.
  SELECT c.source AS source, c.criterion_id AS criterion_id,
         c.metric AS metric, c.lo AS lo, c.hi AS hi,
         c.period AS period, pw.win_start AS period_start,
         COALESCE((SELECT SUM(lv.value) FROM live_value lv
                    WHERE lv.metric = c.metric
                      AND lv.graded_at >= pw.win_start
                      AND lv.graded_at <= strftime('%Y-%m-%dT%H:%M:%SZ','now')),
                  0.0) AS value,
         (SELECT MAX(lv.graded_at) FROM live_value lv
           WHERE lv.metric = c.metric
             AND lv.graded_at <= strftime('%Y-%m-%dT%H:%M:%SZ','now'))
         AS last_logged_at
    FROM v_criterion c
    JOIN metric_def md ON md.token = c.metric AND md.accrual = 'accrued'
    JOIN period_window pw ON pw.period = c.period
   WHERE c.period IS NOT NULL
)
SELECT c.source AS source, c.criterion_id AS criterion_id, c.metric AS metric,
       c.lo AS lo, c.hi AS hi, NULL AS period, NULL AS period_start,
       fp.value AS value,
       -- POINT verdict: symmetric.  A level below the floor is genuinely
       -- under RIGHT NOW -- nothing about a point read is provisional.
       CASE WHEN c.lo IS NOT NULL AND fp.value < c.lo THEN 'under'
            WHEN c.hi IS NOT NULL AND fp.value > c.hi THEN 'over'
            ELSE 'within' END AS verdict,
       -- 045: the stamp behind the judged value -- the same fact the
       -- period arm carries, projected so both arms stay one shape.
       fp.graded_at AS last_logged_at
  FROM v_criterion c
  JOIN fresh_point fp ON fp.metric = c.metric
 WHERE c.period IS NULL
UNION ALL
SELECT source, criterion_id, metric, lo, hi, period, period_start, value,
       -- PERIOD verdict: asymmetric, by arithmetic -- an accrued sum only
       -- GROWS within its period.  Over the ceiling is definitive at any
       -- instant; short of the floor mid-period is 'pending', never
       -- 'under', or a weekly floor would read as failing every Monday
       -- (the manufactured-미달 class 034 removed).
       CASE WHEN hi IS NOT NULL AND value > hi THEN 'over'
            WHEN lo IS NOT NULL AND value < lo THEN 'pending'
            ELSE 'within' END AS verdict,
       last_logged_at
  FROM period_value;
-- 5. Seeding (SPEC §6.1).  Three inserts, each reading a fact this database
--    already holds.  Nothing about a goal, a stage, a face token or a freshness
--    interval is seeded -- those are the owner's, and a migration that minted
--    one would freeze a guess (SPEC §6.1f, §12-1).
-- 5a. The domain roster, unconditionally.  A domain with no door reads
--     'unknown' = "I do not know whether values reach me here" -- true, and
--     VISIBLE.  Seeding only the domains that have evidence would hide the ones
--     that do not, which is the absence this cut exists to end.
INSERT OR IGNORE INTO domain(ulid, token, origin, status, note) VALUES
  ('SEED-DOMAIN-TRAINING','training','seed','active','v3.2 seed roster (SPEC 6.1d)'),
  ('SEED-DOMAIN-DIET',    'diet',    'seed','active','v3.2 seed roster (SPEC 6.1d)'),
  ('SEED-DOMAIN-LEDGER',  'ledger',  'seed','active','v3.2 seed roster (SPEC 6.1d)'),
  ('SEED-DOMAIN-LIFE',    'life',    'seed','active','v3.2 seed roster (SPEC 6.1d)');
-- 5b. Doors, from ARRIVAL evidence only.  Arrival = created_at (when the row was
--     actually written), NEVER the event date the row claims (SPEC §6.1a): by
--     `date`, spend_entry looks like a year of daily arrivals; by created_at it
--     is 1,603 rows in 2 minutes 22 seconds on one day -- a batch import, which
--     proves a machine ran once, not that a door is open.  The discriminator is
--     therefore SHAPE (more than one arrival day), not recency -- so the N-day
--     window of SPEC §12-1 stays open and unguessed, and §6.2 (a door is closed
--     by a person, never by silence) keeps recency out of this decision.
--     kind / face_* stay NULL; declared_by='migration' marks the declaration
--     INCOMPLETE, to be completed by the owning module on first write.
--  b1. block rows, grouped by the module that wrote them (a stored fact).
INSERT OR IGNORE INTO inflow(ulid, domain_id, module_token, declared_by, status)
SELECT 'SEED-INFLOW-BLOCK-' || d.token, d.id, b.source_module, 'migration', 'active'
  FROM (SELECT source_module,
               COUNT(DISTINCT substr(created_at, 1, 10)) AS arrival_days
          FROM block
         WHERE created_at IS NOT NULL AND source_module IS NOT NULL
         GROUP BY source_module) b
  JOIN domain d ON d.token = b.source_module
 WHERE b.arrival_days > 1;
--  b2. workouts -> training.  Mapped by SOURCE TABLE, never by tag string
--      (SPEC §6.1d: the live `health` tag splits across two domains).
--      module_token stays NULL -- which module owns this lane is not a fact
--      this file holds, and inventing one would deepen grill G-2/S4.
INSERT OR IGNORE INTO inflow(ulid, domain_id, declared_by, status)
SELECT 'SEED-INFLOW-WORKOUTS-' || d.token, d.id, 'migration', 'active'
  FROM domain d
 WHERE d.token = 'training'
   AND (SELECT COUNT(DISTINCT substr(created_at, 1, 10))
          FROM workouts WHERE created_at IS NOT NULL) > 1;
--  b3. spend_entry -> ledger.  Written down deliberately and expected to insert
--      NOTHING on this estate: a lane that is offered to the rule and produces
--      no door is what proves the rule discriminates.  If spend ever starts
--      arriving conversationally, this same statement opens the door on the
--      next mint -- no code change.
INSERT OR IGNORE INTO inflow(ulid, domain_id, declared_by, status)
SELECT 'SEED-INFLOW-SPEND-' || d.token, d.id, 'migration', 'active'
  FROM domain d
 WHERE d.token = 'ledger'
   AND (SELECT COUNT(DISTINCT substr(created_at, 1, 10))
          FROM spend_entry WHERE created_at IS NOT NULL) > 1;
-- 5c. metric -> domain, read off metric_def.owner_module, a fact already
--     stored.  Body composition (metric_log's 26 tokens) has NO such fact --
--     SPEC §6.1d leaves that mapping to the owner -- so it is not attached, and
--     it holds 0 measure rows anyway (S1).
INSERT OR IGNORE INTO domain_metric(domain_id, metric)
SELECT d.id, md.token
  FROM metric_def md
  JOIN domain d ON d.token = md.owner_module
 WHERE md.status = 'active';

-- ── Resource x entity crossing -- migration 039 (user_version 39) ─────────
-- block_entity (the non-resource crossing) + the crossing/coverage views.
-- STRUCTURE ONLY on a fresh mint: entity rows are seeded by 039 from the
-- estate's own spend_entry method evidence, so an empty database honestly
-- mints ZERO entities (a new user's instruments are unknown, not guessed).
-- Time is DERIVED (v_block_time_delta), never stored.  NO balance view
-- ships anywhere: without an opening snapshot a cumulative sum would read
-- as a balance and lie (owner constraint 2026-09-07).
CREATE TABLE IF NOT EXISTS block_entity (
  block_id    INTEGER NOT NULL REFERENCES block(id)   ON DELETE CASCADE,
  entity_id   INTEGER NOT NULL REFERENCES entity(id)  ON DELETE CASCADE,
  role        TEXT NOT NULL DEFAULT 'about',
  weight      REAL NOT NULL DEFAULT 1.0 CHECK (weight >= 0.0),
  PRIMARY KEY (block_id, entity_id, role)
);
CREATE INDEX IF NOT EXISTS ix_block_entity_e ON block_entity(entity_id);
CREATE VIEW v_k5_duplicate_target AS
SELECT block_id, entity_id, COUNT(*) AS n_roles
FROM block_entity
GROUP BY block_id, entity_id
HAVING COUNT(*) > 1;
CREATE VIEW v_k6_weight_sum AS
SELECT block_id, SUM(weight) AS weight_sum
FROM block_entity
GROUP BY block_id
HAVING ABS(SUM(weight) - 1.0) > 1e-9;
CREATE VIEW v_block_time_delta AS
SELECT b.id AS block_id,
       b.source_module,
       COALESCE(b.date, substr(b.start_at, 1, 10)) AS date,
       'time' AS resource,
       ROUND((julianday(b.end_at) - julianday(b.start_at)) * 1440.0, 1)
         AS amount,
       'min' AS unit
FROM block b
WHERE b.start_at IS NOT NULL AND b.end_at IS NOT NULL
  AND julianday(b.end_at) > julianday(b.start_at);
CREATE VIEW v_entity_time_alloc AS
WITH pair AS (
  SELECT block_id, entity_id, SUM(weight) AS weight, COUNT(*) AS n_roles
  FROM block_entity
  GROUP BY block_id, entity_id
), gate AS (
  SELECT block_id
  FROM pair
  GROUP BY block_id
  HAVING MAX(n_roles) = 1 AND ABS(SUM(weight) - 1.0) <= 1e-9
)
SELECT t.block_id, t.date, p.entity_id,
       ROUND(t.amount * p.weight, 1) AS alloc_min
FROM v_block_time_delta t
JOIN pair p ON p.block_id = t.block_id
JOIN gate g ON g.block_id = t.block_id;
CREATE VIEW v_entity_flow AS
SELECT d.entity_id,
       e.token AS entity_token,
       d.resource,
       d.unit,
       substr(COALESCE(b.date, b.start_at), 1, 7) AS month,
       SUM(CASE WHEN d.amount > 0 THEN d.amount ELSE 0 END) AS inflow,
       SUM(CASE WHEN d.amount < 0 THEN -d.amount ELSE 0 END) AS outflow,
       COUNT(*) AS n_deltas
FROM block_delta d
JOIN entity e ON e.id = d.entity_id
JOIN block b  ON b.id = d.block_id
GROUP BY d.entity_id, d.resource, d.unit, month;
CREATE VIEW v_c2_coverage AS
SELECT d.resource,
       COUNT(*) AS n_deltas,
       SUM(d.entity_id IS NOT NULL) AS n_with_entity
FROM block_delta d
JOIN resource r ON r.token = d.resource
WHERE r.stock_bearing = 1
GROUP BY d.resource;

-- ============================================================================
-- Event parent + containment exception memory -- migration 040
-- (user_version 40).  The settled two-layer event model (owner 2026-09-08):
-- events are TWO layers (제주 여행 └─ 저녁); records are not a layer.
-- event.parent_id (inline in the event table above) is the one parent seat --
-- nullable self-reference, ON DELETE SET NULL so a deleted parent never
-- deletes a child's history.  Depth-2 + cycle guard live at the TOOL layer
-- (kit_life.py _parent_link_error), not here.  An event's SPAN is DERIVED
-- from its event_timeblock links (no span columns: a stored copy of the
-- block's own stamps would drift -- 036/039 "time is the block").
-- The two tables below are the owner's REMOVAL judgements ("빼는 게 오너
-- 판단"): containment sweeps are default-INCLUDE, and an excluded pair must
-- stay out across every re-sweep.  CASCADE both ways -- when either endpoint
-- dies, the memory dies with it.  Full rationale in the 040 header.
-- ============================================================================
CREATE INDEX idx_event_parent ON event(parent_id) WHERE parent_id IS NOT NULL;
CREATE TABLE event_record_exclusion (
  event_id  INTEGER NOT NULL REFERENCES event(id)  ON DELETE CASCADE,
  record_id INTEGER NOT NULL REFERENCES record(id) ON DELETE CASCADE,
  note      TEXT,
  added_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  PRIMARY KEY (event_id, record_id)
);
CREATE TABLE event_child_exclusion (
  parent_id INTEGER NOT NULL REFERENCES event(id) ON DELETE CASCADE,
  child_id  INTEGER NOT NULL REFERENCES event(id) ON DELETE CASCADE,
  note      TEXT,
  added_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  PRIMARY KEY (parent_id, child_id)
);

-- ── First criterion prerequisites -- migration 041 (user_version 41) ──────
-- STRUCTURAL SEED ONLY.  041 also lands owner data (an entity, six measure
-- rows, one goal + goal_target, a user-declared freshness window); none of it
-- belongs in a fresh database and none of it is repeated here.  What a NEW
-- install needs is the vocabulary those rows will later point at.
--
-- stage: v_criterion_span INNER-JOINs stage (WHERE lifecycle='open').  The
-- roster was EMPTY on the live estate, so every goal criterion was dropped
-- silently -- the criterion side could not return a row no matter what was
-- inserted into goal_target.  Four tokens cover the four lifecycles.
INSERT OR IGNORE INTO stage(token, lifecycle, sort_rank, origin) VALUES
  ('active',    'open',   1, 'seed'),
  ('paused',    'held',   2, 'seed'),
  ('done',      'closed', 3, 'seed'),
  ('abandoned', 'void',   4, 'seed');

-- body_fat_pct: the first non-nutrition metric in the roster.  scope_kind
-- 'entity' because it is a property of a body, not of a block (diet) or a
-- place_link (travel); accrual 'observed' because an InBody reading happens
-- at a moment and is never accrued across a period.
INSERT OR IGNORE INTO metric_def(token, scope_kind, accrual, unit_token,
                                 owner_module, origin, status)
VALUES ('body_fat_pct', 'entity', 'observed', 'pct', 'health', 'module', 'active');
INSERT OR IGNORE INTO domain_metric(domain_id, metric)
SELECT d.id, 'body_fat_pct' FROM domain d WHERE d.token = 'training';


-- ── Pain / form-fault events -- migration 047 (user_version 47) ───────────
-- The pain-triage store moves INTO the kit and joins workout_sets for real.
-- Measurement axis, not a domain module: health-log writes body NUMBERS to
-- metric_log, pain-triage writes body EVENTS here.  Full WHY (the owner's own
-- retirement condition for the separate store, the #1 SAFETY finding the split
-- feeds, why the promotion path surfaces but never writes, why the grouping
-- unit is the site and not the side) is in migrations/047_pain_event.sql.
-- Born EMPTY on a fresh mint by design: the owner's 33 carried events are his
-- history, not kit baseline (database/pain_event_carry.py).

-- A1: the event store.
-- A1: the event store.
CREATE TABLE pain_event (
    id             INTEGER PRIMARY KEY,
    ulid           TEXT NOT NULL UNIQUE,
    owning_agent   TEXT NOT NULL DEFAULT 'warg',
    -- when it hurt.  Same grade as every other health row the owner sees:
    -- 'YYYY-MM-DD HH:MM' localtime.  NOT normalised to UTC-Z here -- SQL cannot
    -- read the declared AGENT_TZ_OFFSET_HOURS and minting a fourth clock is
    -- forbidden (038, owner directive 2026-09-08 06:48).
    observed_at    TEXT NOT NULL,

    -- THE JOIN.  ON DELETE SET NULL on both: deleting a mis-logged set must
    -- never delete the record that his shoulder hurt.
    session_ref    INTEGER REFERENCES workouts(id)     ON DELETE SET NULL,
    set_ref        INTEGER REFERENCES workout_sets(id) ON DELETE SET NULL,
    -- NOTE: link_grade is NOT a column.  v_pain_event derives it from these two
    -- refs; see the header for why the stored form had to go.

    -- THE PROMOTION AXIS.  ascii snake_case token; free vocab by design.
    site           TEXT,
    -- how the site got here.  'stated' = the event itself named it in a
    -- structured field; 'carried' = transcribed from the row's own note text
    -- during the 047 carry.  A reader that must not act on an inference can
    -- tell the two apart; v_pain_promotion_candidate counts them separately.
    site_origin    TEXT,
    side           TEXT,
    pain           INTEGER NOT NULL,

    -- AS-REPORTED lane.  Forbidden once set_ref exists (see CHECK below):
    -- the set row is then the single source for load / sequence / RIR.
    exercise_label TEXT,
    load_kg        REAL,
    set_no         INTEGER,
    rir            INTEGER,

    rom_phase      TEXT,
    fault          TEXT,          -- free text on purpose (pain.py: "free text ok")
    action         TEXT,
    session_label  TEXT,          -- the owner's split name ('앞A' / '뒷B' / ...)
    note           TEXT,
    guardrail      TEXT,
    program_change TEXT,
    status         TEXT NOT NULL DEFAULT 'open',
    source         TEXT NOT NULL DEFAULT 'triage',   -- ledger_* convention
    recorded_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')), sensation TEXT NOT NULL DEFAULT 'pain' CHECK (sensation IN ('pain','stiff','tight','sore')),

    CHECK (owning_agent <> '' AND owning_agent NOT GLOB '*[^A-Za-z0-9_-]*'),
    CHECK (pain BETWEEN 0 AND 10),
    CHECK (status IN ('open','resolved')),
    CHECK (side IS NULL OR side IN ('L','R','both','NA')),
    CHECK (rom_phase IS NULL OR rom_phase IN
           ('setup','descent','bottom','ascent','lockout','other')),
    CHECK (action IS NULL OR action IN
           ('load_drop','substitute','stop','continue','form_cue')),
    CHECK (site_origin IS NULL OR site_origin IN ('stated','carried')),
    -- a site with no provenance, or a provenance with no site, is half a fact.
    CHECK ((site IS NULL) = (site_origin IS NULL)),
    -- a set implies its session: an event may not name a set while claiming no
    -- session, which is the one combination of the two refs that is incoherent.
    CHECK (set_ref IS NULL OR session_ref IS NOT NULL),
    -- one source for one fact (see header).
    CHECK (set_ref IS NULL OR (load_kg IS NULL AND set_no IS NULL AND rir IS NULL))
);

-- A2: the read surfaces need these three.  The first is the promotion scan
-- (site x side x status over a window), the second is the post-session raise
-- (open events, newest first), the third keeps ON DELETE SET NULL cheap.
CREATE INDEX idx_pain_event_site
    ON pain_event(site, side, status, observed_at);
CREATE INDEX idx_pain_event_open
    ON pain_event(owning_agent, status, observed_at);
CREATE INDEX idx_pain_event_set
    ON pain_event(set_ref) WHERE set_ref IS NOT NULL;

-- A3: THE reader.  Every consumer reads this, never the base table, so the
-- linked/unlinked split stays one column-set instead of a join every caller
-- has to remember to write.  exercise_name prefers the canonical slot name
-- (workout_sets) over the free label, which is what closes F34 for linked rows.
CREATE VIEW v_pain_event AS
SELECT
    pe.id,
    pe.ulid,
    pe.owning_agent,
    pe.observed_at,
    CASE WHEN pe.set_ref     IS NOT NULL THEN 'set'
         WHEN pe.session_ref IS NOT NULL THEN 'session'
         ELSE 'none'
    END                                           AS link_grade,
    pe.session_ref,
    pe.set_ref,
    w.date                                        AS session_date,
    COALESCE(ws.exercise_name, pe.exercise_label) AS exercise_name,
    pe.exercise_label,
    COALESCE(ws.weight_kg, pe.load_kg)            AS load_kg,
    COALESCE(ws.set_seq,   pe.set_no)             AS set_no,
    COALESCE(ws.rir,       pe.rir)                AS rir,
    ws.reps,
    ws.set_kind,
    pe.site,
    pe.site_origin,
    pe.side,
    pe.pain,
    pe.sensation,
    pe.rom_phase,
    pe.fault,
    pe.action,
    pe.session_label,
    pe.note,
    pe.guardrail,
    pe.program_change,
    pe.status,
    pe.source,
    pe.recorded_at,
    -- the ledger_constraint FAMILY these events belong to.  Derived, never
    -- stored: the day the owner renames a site the key follows, and a stored
    -- copy would not.  UNSIDED on purpose -- the sided form (injury_shoulder_r)
    -- is the owner's refinement of a part he chose to track per side, and an
    -- event cannot know which parts those are.  NULL when site is unknown: an
    -- unsited event cannot name a constraint, and saying so is the point.
    CASE WHEN pe.site IS NULL THEN NULL
         ELSE 'injury_' || pe.site
    END                                           AS constraint_family_key
FROM pain_event pe
LEFT JOIN workout_sets ws ON ws.id = pe.set_ref
LEFT JOIN workouts     w  ON w.id  = pe.session_ref;

-- A4: THE promotion surface.  Aggregate only -- it reports, it never writes.
--
-- One row per BODY PART (see the header on why not per side).  The 28d / 84d
-- windows are the two a program review actually asks about -- one mesocycle,
-- one block; an arbitrary window is the CLI's job
-- (`kit_life.sh pain-candidates --weeks N`), not a second view.
CREATE VIEW v_pain_promotion_candidate AS
SELECT
    v.owning_agent,
    v.site,
    v.constraint_family_key,
    COUNT(*)                                                   AS total_events,
    SUM(CASE WHEN v.observed_at >= datetime('now','localtime','-28 day')
             THEN 1 ELSE 0 END)                                AS events_28d,
    SUM(CASE WHEN v.observed_at >= datetime('now','localtime','-84 day')
             THEN 1 ELSE 0 END)                                AS events_84d,
    -- still unanswered.  A different question from recurrence, so its own column.
    SUM(CASE WHEN v.status = 'open' THEN 1 ELSE 0 END)         AS open_events,
    -- side BREAKDOWN, not a grouping key.  This is what tells the owner whether
    -- the part he is about to declare is a one-sided story or a both-sides one
    -- -- i.e. whether the row he writes should be injury_<site> or
    -- injury_<site>_r.  The view does not decide that for him.
    SUM(CASE WHEN v.side = 'L' THEN 1 ELSE 0 END)              AS side_l,
    SUM(CASE WHEN v.side = 'R' THEN 1 ELSE 0 END)              AS side_r,
    SUM(CASE WHEN v.side IS NULL OR v.side IN ('both','NA')
             THEN 1 ELSE 0 END)                                AS side_other,
    -- provenance split.  A candidate standing on transcribed sites is a weaker
    -- claim than one standing on stated ones, and the owner is entitled to see
    -- which he is being shown.
    SUM(CASE WHEN v.site_origin = 'stated'  THEN 1 ELSE 0 END) AS site_stated,
    SUM(CASE WHEN v.site_origin = 'carried' THEN 1 ELSE 0 END) AS site_carried,
    MAX(v.pain)                                                AS max_pain,
    ROUND(AVG(v.pain), 1)                                      AS avg_pain,
    MIN(v.observed_at)                                         AS first_seen,
    MAX(v.observed_at)                                         AS last_seen,
    COUNT(DISTINCT v.session_ref)                              AS sessions_hit,
    -- how much of this evidence reaches a SET.  A candidate with set-linked
    -- events can say "at this weight, on this set"; one without can only say
    -- "on these days", and the two are not the same claim to hand a program.
    SUM(CASE WHEN v.set_ref IS NOT NULL THEN 1 ELSE 0 END)     AS set_linked,
    -- the standing constraint(s) already covering this part, across the whole
    -- injury_<site>% family.  Empty = the events have no standing row to keep
    -- alive, which is the shape the #1 SAFETY finding is about.
    (SELECT COUNT(*) FROM ledger_constraint lc
      WHERE lc.class = 'safety' AND lc.status = 'active'
        AND (lc.key = v.constraint_family_key
             OR lc.key LIKE v.constraint_family_key || '\_%' ESCAPE '\')
    )                                                          AS constraint_active,
    (SELECT group_concat(lc.key, ',') FROM ledger_constraint lc
      WHERE lc.class = 'safety'
        AND (lc.key = v.constraint_family_key
             OR lc.key LIKE v.constraint_family_key || '\_%' ESCAPE '\')
    )                                                          AS constraint_keys
FROM v_pain_event v
WHERE v.site IS NOT NULL
  AND v.sensation = 'pain'
GROUP BY v.owning_agent, v.site, v.constraint_family_key;

-- ── Place / address / sub-place split -- migration 050 (user_version 50) ──
-- One `place` column was carrying four jobs (a name, a road address, a lot
-- number, a floor) and one physical venue was splitting into several place
-- ROWS -- so the same travel minutes had to be registered again per row.
-- Three layers now: PLACE is the terminus (place_link/measure attach here and
-- nowhere else), ADDRESS is a many-row table with one is_primary (road / lot /
-- building / map_url / legacy), SUB-PLACE is the room or floor an appointment
-- points at.
--
-- A SUB-PLACE IS STRUCTURALLY INCAPABLE OF BEING A TRAVEL TERMINUS, and that
-- is a machine, not a sentence: nothing references sub_place(id) (every travel
-- seat is an FK to place(id)), and trg_place_not_a_sub_place_* REFUSES a place
-- whose name is already a declared room -- in the bare form AND in the
-- composed "<parent> <room>" form a calendar entry actually produces.
--
-- place.address SURVIVES as a DERIVED MIRROR of the primary address row
-- (travel-resolve.sh reads it in raw SQL), maintained by the three mirror
-- triggers and made unwritable by the two refusal triggers -- two homes for
-- one fact is the drift 036 removed and this does not reintroduce it.
-- Full WHY: migrations/050_place_address_split.sql.

CREATE TABLE place_address (
  id         INTEGER PRIMARY KEY,
  place_id   INTEGER NOT NULL REFERENCES place(id) ON DELETE CASCADE,
  -- road     도로명 (road name + building number)
  -- lot      지번
  -- building building / POI name
  -- map_url  a shared map link (naver.me, kakao, google, apple)
  -- legacy   NOT YET CLASSIFIED.  An honest "this file could not tell", to be
  --          re-tagged by the next map resolution.  Never a guess.
  kind       TEXT NOT NULL CHECK (kind IN ('road','lot','building','map_url','legacy')),
  value      TEXT NOT NULL CHECK (TRIM(value) <> ''),
  is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0,1)),
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE UNIQUE INDEX ux_place_address_value ON place_address(place_id, value);
CREATE UNIQUE INDEX ux_place_address_primary ON place_address(place_id) WHERE is_primary = 1;
CREATE INDEX ix_place_address_place ON place_address(place_id);

CREATE TABLE sub_place (
  id         INTEGER PRIMARY KEY,
  place_id   INTEGER NOT NULL REFERENCES place(id) ON DELETE CASCADE,
  name       TEXT NOT NULL CHECK (TRIM(name) <> ''),
  token      TEXT NOT NULL CHECK (TRIM(token) <> ''),
  note       TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE UNIQUE INDEX ux_sub_place_name  ON sub_place(place_id, name);
CREATE UNIQUE INDEX ux_sub_place_token ON sub_place(place_id, token);

CREATE TRIGGER trg_place_address_mirror_ins AFTER INSERT ON place_address BEGIN
  UPDATE place
     SET address = (SELECT pa.value FROM place_address pa
                     WHERE pa.place_id = NEW.place_id AND pa.is_primary = 1)
   WHERE id = NEW.place_id;
END;
CREATE TRIGGER trg_place_address_mirror_upd AFTER UPDATE ON place_address BEGIN
  UPDATE place
     SET address = (SELECT pa.value FROM place_address pa
                     WHERE pa.place_id = NEW.place_id AND pa.is_primary = 1)
   WHERE id = NEW.place_id;
END;
CREATE TRIGGER trg_place_address_mirror_del AFTER DELETE ON place_address BEGIN
  UPDATE place
     SET address = (SELECT pa.value FROM place_address pa
                     WHERE pa.place_id = OLD.place_id AND pa.is_primary = 1)
   WHERE id = OLD.place_id;
END;

CREATE TRIGGER trg_place_address_is_derived_ins
BEFORE INSERT ON place
WHEN NEW.address IS NOT NULL
BEGIN
  SELECT RAISE(ABORT, 'place.address is DERIVED since 050 -- it mirrors place_address(is_primary=1). Insert the place with address NULL, then add the address row (place-address-add / travel-place-add address=...)');
END;
CREATE TRIGGER trg_place_address_is_derived_upd
BEFORE UPDATE OF address ON place
WHEN NEW.address IS NOT (SELECT pa.value FROM place_address pa
                          WHERE pa.place_id = NEW.id AND pa.is_primary = 1)
BEGIN
  SELECT RAISE(ABORT, 'place.address is DERIVED since 050 -- it mirrors place_address(is_primary=1). Write the address there (place-address-add / place-address-primary / travel-place-upd address=...), never here');
END;

CREATE TRIGGER trg_place_not_a_sub_place_ins
BEFORE INSERT ON place
WHEN EXISTS (SELECT 1 FROM sub_place sp JOIN place pp ON pp.id = sp.place_id
              WHERE TRIM(NEW.name) = sp.name
                 OR TRIM(NEW.name) = pp.name || ' ' || sp.name)
BEGIN
  SELECT RAISE(ABORT, 'place refused: that name is already declared as a SUB-PLACE (room/floor/hall) of an existing place. A sub-place is not a travel terminus -- point the appointment at the parent place and register the room with sub-place-add');
END;
CREATE TRIGGER trg_place_not_a_sub_place_upd
BEFORE UPDATE OF name ON place
WHEN EXISTS (SELECT 1 FROM sub_place sp JOIN place pp ON pp.id = sp.place_id
              WHERE sp.place_id <> NEW.id
                AND (TRIM(NEW.name) = sp.name
                     OR TRIM(NEW.name) = pp.name || ' ' || sp.name))
BEGIN
  SELECT RAISE(ABORT, 'place refused: that name is already declared as a SUB-PLACE (room/floor/hall) of another place. A sub-place is not a travel terminus -- point the appointment at the parent place and register the room with sub-place-add');
END;

-- ── Display name knob / level gate -- migration 051 (user_version 51) ─────
-- The domain speech register (kit 2.22.0 / pack_health 0.12.0) binds only
-- PROSE THE MODEL COMPOSES.  It cannot bind the set table, the set-logging
-- keyboard, the next-set hint or the floor warning, because those are
-- rendered by deterministic code and code cannot read a sentence -- so an
-- un-promoted user still met `RIR` in the set table.  The gate is therefore
-- DATA a renderer queries (D2: a rule that must hold is a structure).
--
--   ui_label  WHAT A THING IS CALLED.  label_key is the stable identifier;
--             `display` is the name the owner reads and it is a KNOB (owner's
--             frame: "서브에이전트 동생 displayname 처럼 취급해줘") -- renaming
--             RIR is one UPDATE, not ten code edits.  Its three column names
--             are dictated by the incumbent reader: the pack's
--             ui_labels._lookup() already issues
--             `SELECT display, gloss FROM ui_label WHERE label_key = ?`.
--   ui_gate   WHAT A SURFACE SHOWS AT A LEVEL.  One row per (surface, level);
--             the seed IS the whole beginner/intermediate difference and
--             there must never be a second place where a level changes a
--             surface.  Every `intermediate` row equals today's behaviour.
--
-- The level itself is the `config` key `workout_level` (the pack's live
-- CARD_LEVEL_KEYS searches it first; the other two names stay read-aliases).
-- NO migration and NO fresh mint writes that row: `config` is per-instance
-- OWNER data and an absent key already resolves to CARD_LEVEL_DEFAULT
-- 'intermediate'.  Set it with `kit_life.sh config-set workout_level=<level>`.
--
-- `explain` is NULL on every seeded row by OWNER RULING 2026-09-11 ("그냥
-- 빼버려"), which retired the RIR explanation the 2026-08-24 design pointed
-- it at.  The column ships because the design fixes the shape; it is empty
-- because the lane it names does not exist today.
-- Full WHY: migrations/051_ui_label_gate.sql.

CREATE TABLE ui_label (
  label_key  TEXT PRIMARY KEY,
  display    TEXT NOT NULL CHECK (TRIM(display) <> ''),
  gloss      TEXT,
  updated_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

INSERT INTO ui_label (label_key, display, gloss) VALUES
  ('rir', 'RIR', NULL);

CREATE TABLE ui_gate (
  -- the surface identifier the RENDERER looks itself up by.  Stable string,
  -- <module>.<surface>; it is an API between this table and the pack.
  surface   TEXT NOT NULL CHECK (TRIM(surface) <> ''),
  level     TEXT NOT NULL CHECK (level IN ('beginner','intermediate')),
  visible   INTEGER NOT NULL DEFAULT 1 CHECK (visible IN (0,1)),
  -- ui_label reference BY NAME (no FK -- see the header).  NULL = this
  -- surface shows nothing that needs a name at this level.
  label_key TEXT CHECK (label_key IS NULL OR TRIM(label_key) <> ''),
  -- which explanation lane, when one is approved.  EMPTY on every seeded row
  -- today by the 2026-09-11 owner ruling; the CHECK is what the column may
  -- ever hold, not what it does hold.
  explain   TEXT CHECK (explain IS NULL OR explain IN ('first_use','on_demand')),
  note      TEXT,
  PRIMARY KEY (surface, level)
);

INSERT INTO ui_gate (surface, level, visible, label_key, explain, note) VALUES
  ('set_table.rir_col',   'beginner',     0, NULL,  NULL,
   '세트표에서 RIR 컬럼 자체를 뺀다 -- 빈 칸을 남기지 않는다. 헤더/보더/행이 같은 튜플을 쓰므로 격자가 4칸으로 좁아진다.'),
  ('set_table.rir_col',   'intermediate', 1, 'rir', NULL,
   '컬럼 표시 = 현행. 설계(2026-08-24)는 여기 explain=on_demand (첫 표 메타줄 범례)였으나 2026-09-11 오너 룰링("그냥 빼버려")이 RIR 설명 경로를 폐기해 NULL 로 시드한다. 설명 레인이 다시 승인되면 UPDATE 한 줄.'),

  ('set_arm.step2_rir',   'beginner',     0, NULL,  NULL,
   '키보드가 1단계(반복수)만. 새 모양을 발명하지 말 것 -- 램프 1단계 arm 이 이미 그 모양이고 CONFIRM_FMT_SKIP 이 짝이다.'),
  ('set_arm.step2_rir',   'intermediate', 1, 'rir', NULL,
   '2단계(반복수 -> RIR) = 현행. 설계는 여기 explain=first_use (첫 세션 fold 펼침)였으나 그 fold 자체가 2026-09-11 오너 룰링으로 폐기됐다(타이밍: 값을 이미 기록한 뒤에 설명 / 대상 역전: 키보드를 통과한 사람만 도달). 이동이 아니라 제거다.'),

  ('set_arm.confirm_rir', 'beginner',     0, NULL,  NULL,
   '확인문구에서 RIR 절 생략 -- CONFIRM_FMT_SKIP.'),
  ('set_arm.confirm_rir', 'intermediate', 1, 'rir', NULL,
   '"… · RIR 2 기록" = 현행.'),

  ('spec_line.rir',       'beginner',     0, NULL,  NULL,
   'NEXT_HINT 스펙에서 RIR 괄호 생략. 리졸버가 빈 문자열을 내면 스펙 빌더가 이미 절을 생략한다 -- 정상 경로와 오버라이드 경로(F19/F21) 둘 다 같은 리졸버를 거쳐야 한다.'),
  ('spec_line.rir',       'intermediate', 1, 'rir', NULL,
   '"벤치프레스 60kg × 8 (RIR 1-2)" = 현행.'),

  ('set_add.floor_warn',  'beginner',     0, NULL,  NULL,
   '경고는 계속 낸다(안전). 숨기는 것은 RIR 수치뿐 -- 문장은 "다음 세트는 한 템포 일찍 끊고 무게를 올린다"로. visible=0 은 경고의 침묵이 아니라 RIR 어휘의 부재를 뜻한다. 설계 §5 미결: 안전 경고를 게이팅하는 것이 맞는지 재검토 대상.'),
  ('set_add.floor_warn',  'intermediate', 1, 'rir', NULL,
   '수치 포함 현행 문구. 이 표면은 치환 롤아웃이 건드리지 않아 날 것의 RIR 을 내고 있었다 -- 리졸버 경유로 통일되면 한 세션에 두 이름이 공존하던 불일치도 같이 없어진다.'),

  ('card.muscle_vocab',   'beginner',     1, 'muscle_coarse', NULL,
   '가슴/등/다리. 이 표면은 초급에서도 visible=1 이다 -- 감추는 게이트가 아니라 어휘를 고르는 게이트이기 때문. label_key 는 표시명이 아니라 어휘 MODE 라서 ui_label 행이 없다(FK 를 걸지 않은 이유, 헤더 참조).'),
  ('card.muscle_vocab',   'intermediate', 1, 'muscle_unit',   NULL,
   '가자미/대퇴사두 = 현행. 이 한 쌍만 이미 배선돼 있다(exercise-enter card_user_level) -- 나머지 다섯 표면은 팩 쪽 컷이 붙인다.');

-- ── 자원축: 들고 다니는 물건 + 포함관계 -- migration 053 (user_version 53) ──
-- 오너가 들고 다니는 물건(벨트/스트랩/악보/여권/가방)의 이름 / 현재 담긴 곳 /
-- 보유 3상태를 담는 표가 정본에 없었다 -- ledger_resource(계획 원장 주장) /
-- resource(흐름·재고 분류) / entity(휴면 실물 대상)는 셋 다 이 좁은 사실을
-- 안 담는다. possession 은 has/has_not/unknown 3상태 -- 널 불리언은 호출부가
-- NULL 을 false 로 접어버리는 사고를 부른다("모른다"가 "없다"로 새면 안 됨,
-- 운동 모듈 규칙이 이미 못박은 그 구분).  parent_id 는 지금 담긴 곳 하나뿐
-- (self-FK, ON DELETE SET NULL -- entity.owner_entity_id 선례) -- "usual
-- home"은 이 컷이 안 만든다. 순환 가드는 스키마 CHECK 가 못 보는 상위 체인을
-- 봐야 해서 도구층(kit_life.py carry_item_pack)의 일이다 -- event.parent_id
-- 의 깊이/순환 가드와 같은 자리(040). routine_prep_item 은 루틴의 기본 준비물
-- junction -- routine_def 에 prep_min 칸이 없어(실제 선언은
-- travel_rule.prep_min, anchor_type='def' 간접 연결) 빌려 쓸 스칼라가 없고,
-- 한 루틴이 여러 준비물을 가지므로 다대다다. 일정 위 준비물은 새 라벨 칸이
-- 아니라 sub_event(kind='prep_item', ref=carry_item.ulid, done) 재사용 -- ref
-- 는 이미 FK 없는 L2 도메인 ulid 이고 done 은 이미 불리언 CHECK 다. 시드 행
-- 없음 -- 물건 이름은 오너 데이터지 제품 어휘가 아니다. WHY 전문은
-- migrations/053_carry_item.sql.

CREATE TABLE carry_item (
  id               INTEGER PRIMARY KEY,
  ulid             TEXT NOT NULL UNIQUE,
  name             TEXT NOT NULL CHECK (TRIM(name) <> ''),
  item_kind        TEXT NOT NULL DEFAULT 'item'
                   CHECK (item_kind IN ('item','container')),
  parent_id        INTEGER REFERENCES carry_item(id) ON DELETE SET NULL,
  possession       TEXT NOT NULL DEFAULT 'unknown'
                   CHECK (possession IN ('has','has_not','unknown')),
  acquired_on      TEXT,
  released_on      TEXT,
  possession_as_of TEXT,
  source           TEXT NOT NULL,
  owning_agent     TEXT NOT NULL,
  status           TEXT NOT NULL DEFAULT 'active'
                   CHECK (status IN ('active','retired')),
  created_at       TEXT NOT NULL
                   DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  updated_at       TEXT NOT NULL
                   DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),

  CHECK (parent_id IS NULL OR possession = 'has'),
  CHECK (released_on IS NULL OR acquired_on IS NULL
         OR released_on >= acquired_on),
  CHECK (possession <> 'has' OR released_on IS NULL)
);

CREATE INDEX ix_carry_item_parent
  ON carry_item(parent_id);

CREATE INDEX ix_carry_item_state
  ON carry_item(status, possession);

CREATE TABLE routine_prep_item (
  routine_def_id INTEGER NOT NULL
                 REFERENCES routine_def(id) ON DELETE CASCADE,
  carry_item_id  INTEGER NOT NULL
                 REFERENCES carry_item(id),
  source         TEXT NOT NULL,
  created_at     TEXT NOT NULL
                 DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  PRIMARY KEY (routine_def_id, carry_item_id)
);

CREATE INDEX ix_routine_prep_item_item
  ON routine_prep_item(carry_item_id);


-- ── Muscle fatigue read model -- migration 056 (user_version 56) ──────────
-- Per-muscle fatigue is COMPUTED at read time (database/muscle_fatigue.py:
-- muscle-state / muscle-week), never stored.  This migration adds only what
-- cannot be computed: workout_types.muscle_node (the declared-region lane for
-- set-less sessions), pain_event.sensation (pain|stiff|tight|sore --
-- "햄스 뻣뻣" is a body signal, not an injury event; the promotion view counts
-- pain only), and the exercise-creation gate below.  A drafted map is an
-- exercise tag (map=draft), not a column.  Map corrections and the
-- new small-muscle nodes are DATA and ship in pack_health's taxonomy seed on a
-- fresh mint.  Full WHY: migrations/056_muscle_fatigue.sql.
CREATE TRIGGER trg_ax_exercise_requires_map BEFORE INSERT ON ax_exercise
WHEN NOT EXISTS (SELECT 1 FROM exercise_muscle_map
                  WHERE exercise_name = NEW.exercise_name AND role = 'primary')
  OR NOT EXISTS (SELECT 1 FROM exercise_fatigue_cost
                  WHERE exercise_name = NEW.exercise_name)
BEGIN
  SELECT RAISE(ABORT, 'ax_exercise refused: register the exercise with a primary muscle map and a fatigue cost first (kit_life.sh exercise-add)');
END;
CREATE TRIGGER trg_ax_exercise_requires_map_rename BEFORE UPDATE OF exercise_name ON ax_exercise
WHEN NOT EXISTS (SELECT 1 FROM exercise_muscle_map
                  WHERE exercise_name = NEW.exercise_name AND role = 'primary')
  OR NOT EXISTS (SELECT 1 FROM exercise_fatigue_cost
                  WHERE exercise_name = NEW.exercise_name)
BEGIN
  SELECT RAISE(ABORT, 'ax_exercise refused: register the exercise with a primary muscle map and a fatigue cost first (kit_life.sh exercise-add)');
END;


-- ── Ingest ledger (들어온 기록 장부) -- migration 057 (user_version 57) ──────
-- One consent row per (source, kind), written by a module's onboarding step
-- when the user approves that automation (ingest-consent) -- never by a
-- migration, so a fresh mint carries NO consent and ingests nothing.  One
-- ledger row per external event, keyed by (source, kind, ext_id): re-sent
-- events are SAME, overwritten ones (active_energy_day closed:false ->
-- closed:true) are CHANGED on the same row.  The triggers refuse a ledger row
-- without active consent or older than approved_at (NO BACKFILL) and a
-- backdated approval.  Full WHY: migrations/057_ingest_ledger.sql.
CREATE TABLE ingest_consent (
  source         TEXT NOT NULL CHECK (length(source) BETWEEN 1 AND 64 AND source GLOB '[a-z]*' AND source NOT GLOB '*[^a-z0-9_]*'),
  kind           TEXT NOT NULL CHECK (length(kind) BETWEEN 1 AND 64 AND kind GLOB '[a-z]*' AND kind NOT GLOB '*[^a-z0-9_]*'),
  module         TEXT CHECK (module IS NULL OR (module <> '' AND module NOT GLOB '*[^A-Za-z0-9_-]*')),
  approved_at    TEXT NOT NULL CHECK (julianday(approved_at) IS NOT NULL),
  revoked_at     TEXT CHECK (revoked_at IS NULL OR julianday(revoked_at) IS NOT NULL),
  last_pulled_at TEXT CHECK (last_pulled_at IS NULL OR julianday(last_pulled_at) IS NOT NULL),
  created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  PRIMARY KEY (source, kind)
);
CREATE TRIGGER trg_ingest_consent_no_backdate BEFORE INSERT ON ingest_consent
WHEN julianday(NEW.approved_at) < julianday('now') - 1.0/1440
BEGIN
  SELECT RAISE(ABORT, 'ingest_consent refused: approved_at is in the past -- consent starts now (kit_life.sh ingest-consent); a backdated approval would be a backfill');
END;
CREATE TRIGGER trg_ingest_consent_no_backdate_upd BEFORE UPDATE OF approved_at ON ingest_consent
WHEN julianday(NEW.approved_at) < julianday(OLD.approved_at)
  OR (julianday(NEW.approved_at) <> julianday(OLD.approved_at)
      AND julianday(NEW.approved_at) < julianday('now') - 1.0/1440)
BEGIN
  SELECT RAISE(ABORT, 'ingest_consent refused: approved_at can only move forward, to now -- a backdated approval would be a backfill');
END;

CREATE TABLE ingest_ref (
  source        TEXT NOT NULL,
  kind          TEXT NOT NULL,
  ext_id        TEXT NOT NULL CHECK (ext_id <> '' AND length(ext_id) <= 256),
  occurred_at   TEXT NOT NULL CHECK (julianday(occurred_at) IS NOT NULL),
  target_table  TEXT,
  target_id     TEXT,
  payload_hash  TEXT,
  first_seen    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
  revision      INTEGER NOT NULL DEFAULT 1 CHECK (revision >= 1),
  PRIMARY KEY (source, kind, ext_id),
  FOREIGN KEY (source, kind) REFERENCES ingest_consent(source, kind)
);
CREATE INDEX ix_ingest_ref_occurred ON ingest_ref(source, kind, occurred_at);
CREATE INDEX ix_ingest_ref_target ON ingest_ref(target_table, target_id);
CREATE TRIGGER trg_ingest_ref_gate BEFORE INSERT ON ingest_ref
WHEN NOT EXISTS (SELECT 1 FROM ingest_consent c
                  WHERE c.source = NEW.source AND c.kind = NEW.kind
                    AND c.revoked_at IS NULL
                    AND julianday(NEW.occurred_at) >= julianday(c.approved_at))
BEGIN
  SELECT RAISE(ABORT, 'ingest_ref refused: no active consent for this source/kind, or the event occurred before the user approved it (no backfill)');
END;
CREATE TRIGGER trg_ingest_ref_gate_upd BEFORE UPDATE ON ingest_ref
WHEN NOT EXISTS (SELECT 1 FROM ingest_consent c
                  WHERE c.source = NEW.source AND c.kind = NEW.kind
                    AND c.revoked_at IS NULL
                    AND julianday(NEW.occurred_at) >= julianday(c.approved_at))
BEGIN
  SELECT RAISE(ABORT, 'ingest_ref refused: no active consent for this source/kind, or the event occurred before the user approved it (no backfill)');
END;

INSERT OR IGNORE INTO unit(token, dimension) VALUES ('bpm', 'frequency');

-- 059_intake: kit-owned cross-resource intake (migrations/059_intake.sql).
-- Intake is evidence awaiting a landing in the existing resource model.
CREATE TABLE intake_source (
    key TEXT PRIMARY KEY,
    resource TEXT NOT NULL REFERENCES resource(token),
    owning_module TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0 CHECK(enabled IN (0,1)),
    last_read_at TEXT,
    value_origin TEXT NOT NULL REFERENCES provenance(token)
);
CREATE TABLE intake_match_rule (
    resource TEXT PRIMARY KEY REFERENCES resource(token),
    owning_module TEXT NOT NULL,
    rule_json TEXT NOT NULL CHECK(json_valid(rule_json)),
    enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1))
);
CREATE TABLE intake_item (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL REFERENCES intake_source(key),
    source_ref TEXT NOT NULL CHECK(source_ref <> ''),
    target TEXT NOT NULL CHECK(target IN ('block','measure')),
    raw_payload TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    data_json TEXT NOT NULL CHECK(json_valid(data_json)),
    state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','confirmed','excluded')),
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    UNIQUE(source, source_ref),
    UNIQUE(id, target)
);
CREATE TABLE intake_link (
    item_id INTEGER PRIMARY KEY,
    target TEXT NOT NULL CHECK(target IN ('block','measure')),
    block_id INTEGER REFERENCES block(id),
    measure_id INTEGER REFERENCES measure(id),
    reversal_of INTEGER REFERENCES block(id),
    FOREIGN KEY(item_id, target) REFERENCES intake_item(id, target),
    CHECK((target='block' AND block_id IS NOT NULL AND measure_id IS NULL)
       OR (target='measure' AND measure_id IS NOT NULL AND block_id IS NULL)),
    CHECK(reversal_of IS NULL OR (target='block' AND reversal_of <> block_id))
);
CREATE INDEX ix_intake_link_block ON intake_link(block_id);
CREATE UNIQUE INDEX ux_intake_reversal ON intake_link(reversal_of) WHERE reversal_of IS NOT NULL;
CREATE TRIGGER intake_measure_entity BEFORE INSERT ON intake_link
WHEN NEW.target='measure' AND NOT EXISTS(SELECT 1 FROM measure WHERE id=NEW.measure_id AND entity_id IS NOT NULL)
BEGIN SELECT RAISE(ABORT, 'state intake requires entity-scoped measure'); END;
CREATE TRIGGER intake_measure_entity_upd BEFORE UPDATE ON intake_link
WHEN NEW.target='measure' AND NOT EXISTS(SELECT 1 FROM measure WHERE id=NEW.measure_id AND entity_id IS NOT NULL)
BEGIN SELECT RAISE(ABORT, 'state intake requires entity-scoped measure'); END;
-- Deleting a landing block never dangles or blocks: every delete path
-- (spend-del, experience-delete-source, calendar _tb_delete, reconcilers)
-- goes through this trigger, so no helper can forget intake. The evidence
-- stays, excluded; a reversal whose original vanished returns to review.
CREATE TRIGGER intake_block_delete BEFORE DELETE ON block
WHEN EXISTS(SELECT 1 FROM intake_link WHERE block_id=OLD.id OR reversal_of=OLD.id)
BEGIN
  UPDATE intake_item SET state='excluded', reason='target_deleted'
   WHERE id IN (SELECT item_id FROM intake_link WHERE block_id=OLD.id);
  DELETE FROM intake_link WHERE block_id=OLD.id;
  UPDATE intake_item SET state='pending', reason='reversal_target_deleted'
   WHERE id IN (SELECT item_id FROM intake_link WHERE reversal_of=OLD.id);
  UPDATE intake_link SET reversal_of=NULL WHERE reversal_of=OLD.id;
END;
-- Editing a landing block keeps the link but records the divergence.
CREATE TRIGGER intake_block_edited AFTER UPDATE OF date, title ON block
WHEN OLD.date IS NOT NEW.date OR OLD.title IS NOT NEW.title
BEGIN
  UPDATE intake_item SET reason='target_edited' WHERE state='confirmed'
   AND id IN (SELECT item_id FROM intake_link WHERE block_id=NEW.id OR reversal_of=NEW.id);
END;
CREATE TRIGGER intake_delta_edited AFTER UPDATE OF amount, entity_id, unit, resource ON block_delta
WHEN 'money' IN (OLD.resource, NEW.resource) AND (OLD.amount IS NOT NEW.amount
  OR OLD.entity_id IS NOT NEW.entity_id OR OLD.unit IS NOT NEW.unit OR OLD.resource IS NOT NEW.resource)
BEGIN
  UPDATE intake_item SET reason='target_edited' WHERE state='confirmed'
   AND id IN (SELECT item_id FROM intake_link WHERE block_id=NEW.block_id OR reversal_of=NEW.block_id);
END;
CREATE TRIGGER intake_delta_added AFTER INSERT ON block_delta WHEN NEW.resource='money'
BEGIN
  UPDATE intake_item SET reason='target_edited' WHERE state='confirmed'
   AND id IN (SELECT item_id FROM intake_link WHERE block_id=NEW.block_id OR reversal_of=NEW.block_id);
END;
CREATE TRIGGER intake_delta_removed AFTER DELETE ON block_delta WHEN OLD.resource='money'
BEGIN
  UPDATE intake_item SET reason='target_edited' WHERE state='confirmed'
   AND id IN (SELECT item_id FROM intake_link WHERE block_id=OLD.block_id OR reversal_of=OLD.block_id);
END;
-- Candidate lookup is date range + signed amount, never a full scan.
CREATE INDEX ix_block_delta_amount ON block_delta(resource, amount);
-- spend_entry has no AUTOINCREMENT and cannot gain it without a rebuild, and
-- sqlite_sequence is ignored for such tables. A floor row records the highest
-- id ever deleted; an insert at or below it aborts, so archived/deleted ids
-- (and their x-spend-N shadows and dumps) are never reused. spend_add
-- allocates above the floor explicitly.
INSERT OR IGNORE INTO ledger_guard(guard_key,scope,value,unit,enforcement,inviolable,source)
VALUES('spend_id_floor','global',0,'id','block',1,'seed');
CREATE TRIGGER spend_entry_id_floor_track AFTER DELETE ON spend_entry
BEGIN
  UPDATE ledger_guard SET value=MAX(COALESCE(value,0), OLD.id)
   WHERE guard_key='spend_id_floor' AND scope='global';
END;
CREATE TRIGGER spend_entry_id_floor AFTER INSERT ON spend_entry
WHEN NEW.id <= COALESCE((SELECT value FROM ledger_guard WHERE guard_key='spend_id_floor' AND scope='global'), 0)
BEGIN SELECT RAISE(ABORT, 'spend_entry id at or below spend_id_floor; ids are never reused'); END;
CREATE TRIGGER spend_id_floor_monotonic BEFORE UPDATE ON ledger_guard
WHEN OLD.guard_key='spend_id_floor' AND OLD.scope='global'
 AND (NEW.guard_key IS NOT OLD.guard_key OR NEW.scope IS NOT OLD.scope OR NEW.value IS NULL OR NEW.value < OLD.value)
BEGIN SELECT RAISE(ABORT, 'spend_id_floor only rises'); END;
CREATE TRIGGER spend_id_floor_kept BEFORE DELETE ON ledger_guard
WHEN OLD.guard_key='spend_id_floor' AND OLD.scope='global'
BEGIN SELECT RAISE(ABORT, 'spend_id_floor cannot be removed'); END;
INSERT OR IGNORE INTO ledger_guard(guard_key,scope,value,unit,enforcement,source)
VALUES('import_auto_accept','global',0,'flag','block','seed');
-- Only the stock unset dup_window (NULL, non-day unit) is normalized. A
-- configured non-day value is left as found; intake refuses loudly on it.
UPDATE ledger_guard SET value=1,unit='day',updated_at=datetime('now','localtime')
 WHERE guard_key='dup_window' AND scope='global' AND value IS NULL AND unit IS NOT 'day';
INSERT OR IGNORE INTO ledger_guard(guard_key,scope,value,unit,enforcement,source)
VALUES('dup_window','global',1,'day','hold','seed');


-- 060_near_match_refusal: the owner's "not the same" answers to a near-match
-- question (migrations/060_near_match_refusal.sql, near_match.py).  071 widened
-- the domain CHECK with 'item' and 'category' (module_shopping); 073 with
-- 'group' and 'sub_place' (proper-noun registry).
CREATE TABLE near_match_refusal (
    domain       TEXT NOT NULL CHECK (domain IN
                   ('place','person','method','food','exercise','item','category','group','sub_place')),
    query_key    TEXT NOT NULL CHECK (query_key <> ''),
    existing_ref TEXT NOT NULL CHECK (existing_ref <> ''),
    query_text   TEXT NOT NULL,
    said         TEXT,
    refused_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    PRIMARY KEY (domain, query_key, existing_ref)
);

-- ── Muscle vocabulary bound -- migration 063 (user_version 63) ──────────
-- exercise_muscle_map.muscle / exercise_subregion_map.subregion /
-- workout_types.muscle_node must name a muscle_node, and a node in use cannot
-- be deleted or renamed -- triggers, so the binding holds on connections with
-- foreign_keys off.  Exemption: an EMPTY muscle_node (fresh mint, pack seed in
-- progress).  v_muscle_vocab_orphan lists any row that slipped through.  WHY
-- in migrations/063_muscle_vocab_bound.sql.
CREATE TRIGGER trg_exercise_muscle_map_node
BEFORE INSERT ON exercise_muscle_map
WHEN EXISTS (SELECT 1 FROM muscle_node)
 AND NOT EXISTS (SELECT 1 FROM muscle_node WHERE node = NEW.muscle)
BEGIN
  SELECT RAISE(ABORT, 'exercise_muscle_map refused: muscle is not a muscle_node -- use the taxonomy''s node key (kit_life.sh muscle-vocab)');
END;
CREATE TRIGGER trg_exercise_muscle_map_node_upd
BEFORE UPDATE OF muscle ON exercise_muscle_map
WHEN EXISTS (SELECT 1 FROM muscle_node)
 AND NOT EXISTS (SELECT 1 FROM muscle_node WHERE node = NEW.muscle)
BEGIN
  SELECT RAISE(ABORT, 'exercise_muscle_map refused: muscle is not a muscle_node -- use the taxonomy''s node key (kit_life.sh muscle-vocab)');
END;
CREATE TRIGGER trg_exercise_subregion_map_node
BEFORE INSERT ON exercise_subregion_map
WHEN EXISTS (SELECT 1 FROM muscle_node)
 AND NOT EXISTS (SELECT 1 FROM muscle_node WHERE node = NEW.subregion)
BEGIN
  SELECT RAISE(ABORT, 'exercise_subregion_map refused: subregion is not a muscle_node -- use the taxonomy''s node key (kit_life.sh muscle-vocab)');
END;
CREATE TRIGGER trg_exercise_subregion_map_node_upd
BEFORE UPDATE OF subregion ON exercise_subregion_map
WHEN EXISTS (SELECT 1 FROM muscle_node)
 AND NOT EXISTS (SELECT 1 FROM muscle_node WHERE node = NEW.subregion)
BEGIN
  SELECT RAISE(ABORT, 'exercise_subregion_map refused: subregion is not a muscle_node -- use the taxonomy''s node key (kit_life.sh muscle-vocab)');
END;
CREATE TRIGGER trg_workout_types_muscle_node
BEFORE INSERT ON workout_types
WHEN NEW.muscle_node IS NOT NULL
 AND NOT EXISTS (SELECT 1 FROM muscle_node WHERE node = NEW.muscle_node)
BEGIN
  SELECT RAISE(ABORT, 'workout_types refused: muscle_node is not a muscle_node');
END;
CREATE TRIGGER trg_workout_types_muscle_node_upd
BEFORE UPDATE OF muscle_node ON workout_types
WHEN NEW.muscle_node IS NOT NULL
 AND NOT EXISTS (SELECT 1 FROM muscle_node WHERE node = NEW.muscle_node)
BEGIN
  SELECT RAISE(ABORT, 'workout_types refused: muscle_node is not a muscle_node');
END;
CREATE TRIGGER trg_muscle_node_in_use_del
BEFORE DELETE ON muscle_node
WHEN EXISTS (SELECT 1 FROM exercise_muscle_map WHERE muscle = OLD.node)
  OR EXISTS (SELECT 1 FROM exercise_subregion_map WHERE subregion = OLD.node)
  OR EXISTS (SELECT 1 FROM workout_types WHERE muscle_node = OLD.node)
BEGIN
  SELECT RAISE(ABORT, 'muscle_node refused: the node is still named by exercise_muscle_map / exercise_subregion_map / workout_types -- move those rows first');
END;
CREATE TRIGGER trg_muscle_node_in_use_rename
BEFORE UPDATE OF node ON muscle_node
WHEN NEW.node IS NOT OLD.node
 AND (EXISTS (SELECT 1 FROM exercise_muscle_map WHERE muscle = OLD.node)
  OR EXISTS (SELECT 1 FROM exercise_subregion_map WHERE subregion = OLD.node)
  OR EXISTS (SELECT 1 FROM workout_types WHERE muscle_node = OLD.node))
BEGIN
  SELECT RAISE(ABORT, 'muscle_node refused: the node is still named by exercise_muscle_map / exercise_subregion_map / workout_types -- move those rows first');
END;

-- Every row whose muscle name is not a node, whenever it was written (the
-- empty-vocabulary window, a pre-063 row).  Empty = the vocabularies agree.
CREATE VIEW v_muscle_vocab_orphan AS
SELECT 'exercise_muscle_map' AS table_name, exercise_name AS row_key,
       muscle AS name
  FROM exercise_muscle_map
 WHERE muscle NOT IN (SELECT node FROM muscle_node)
UNION ALL
SELECT 'exercise_subregion_map', exercise_name, subregion
  FROM exercise_subregion_map
 WHERE subregion NOT IN (SELECT node FROM muscle_node)
UNION ALL
SELECT 'workout_types', CAST(id AS TEXT), muscle_node
  FROM workout_types
 WHERE muscle_node IS NOT NULL
   AND muscle_node NOT IN (SELECT node FROM muscle_node);


-- ── Workout kcal not-entered -- migration 064 (user_version 64) ─────────
-- workouts.kcal NULL = never recorded (no default); kcal_origin = the 0-D
-- provenance token.  An origin without a number is refused (CHECK above); a
-- number without an origin is stamped 'unknown' by these triggers.  WHY in
-- migrations/064_workouts_kcal_origin.sql.
CREATE TRIGGER trg_workouts_kcal_origin_ins
AFTER INSERT ON workouts
WHEN NEW.kcal IS NOT NULL AND NEW.kcal_origin IS NULL
BEGIN
  UPDATE workouts SET kcal_origin = 'unknown' WHERE id = NEW.id;
END;
CREATE TRIGGER trg_workouts_kcal_origin_upd
AFTER UPDATE OF kcal, kcal_origin ON workouts
WHEN NEW.kcal IS NOT NULL AND NEW.kcal_origin IS NULL
BEGIN
  UPDATE workouts SET kcal_origin = 'unknown' WHERE id = NEW.id;
END;

-- ── Estimated workout kcal -- migration 080 (user_version 80) ────────────
-- kcal_origin 'estimated' = closeout engine estimate (no watch value).  A
-- measured value always wins: estimated may not overwrite measured, measured
-- may overwrite estimated.  WHY in migrations/080_workouts_kcal_estimated.sql.
CREATE TRIGGER trg_workouts_estimated_not_over_measured
BEFORE UPDATE OF kcal, kcal_origin ON workouts
WHEN OLD.kcal_origin = 'measured' AND NEW.kcal_origin = 'estimated'
BEGIN
  SELECT RAISE(ABORT, 'kcal_origin: estimated cannot overwrite measured');
END;

-- ── Place coordinates -- migration 066 (user_version 66) ─────────────────
-- Where a place is on the map, once per place, written only by the Naver
-- route resolve (travel-resolve.sh -> place-coord-set).  Feeds the map
-- buttons on prep/travel alerts.  WHY in migrations/066_place_coord.sql.
CREATE TABLE place_coord (
  place_id   INTEGER PRIMARY KEY REFERENCES place(id) ON DELETE CASCADE,
  lat        REAL NOT NULL CHECK (lat BETWEEN -90 AND 90),
  lng        REAL NOT NULL CHECK (lng BETWEEN -180 AND 180),
  naver_ref  TEXT CHECK (naver_ref IS NULL OR TRIM(naver_ref) <> ''),
  source     TEXT NOT NULL DEFAULT 'naver_route' CHECK (TRIM(source) <> ''),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- -- Place opening hours -- migration 067 (user_version 67) ----------------
-- When a place is open, once per place, looked up by the agent on
-- PLACE_HOURS_NEEDED and stored via place-hours-set.  Read by place-hours
-- and the confirmation card.  WHY in migrations/067_place_hours.sql.
CREATE TABLE place_hours (
  place_id   INTEGER PRIMARY KEY REFERENCES place(id) ON DELETE CASCADE,
  hours      TEXT NOT NULL CHECK (json_valid(hours)),
  source_url TEXT CHECK (source_url IS NULL OR TRIM(source_url) <> ''),
  fetched_on TEXT NOT NULL
             CHECK (fetched_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

-- -- Stable profile facts -- migration 068 (user_version 68) ---------------
-- A config key named here is never archived (renamed) or deleted by a reset
-- (owner 2026-10-06: height_cm was lost to the 2026-09-27 health reset).
-- Its value changes the normal way.  WHY in migrations/068_profile_keep.sql.
CREATE TABLE profile_keep (
  key TEXT PRIMARY KEY CHECK (TRIM(key) <> '' AND key NOT LIKE 'archived.%'),
  why TEXT NOT NULL CHECK (TRIM(why) <> '')
);
INSERT INTO profile_keep (key, why) VALUES
  ('height_cm', 'stable profile fact (owner 2026-10-06): lost to the 2026-09-27 health reset');

CREATE TRIGGER trg_config_profile_keep_rename
BEFORE UPDATE OF key ON config
WHEN NEW.key <> OLD.key AND OLD.key IN (SELECT key FROM profile_keep)
BEGIN
  SELECT RAISE(ABORT, 'profile_keep: this config key is a stable profile fact (e.g. height_cm) and is never archived or renamed by a reset -- leave it out of the reset; change its value with config-set');
END;

CREATE TRIGGER trg_config_profile_keep_delete
BEFORE DELETE ON config
WHEN OLD.key IN (SELECT key FROM profile_keep)
BEGIN
  SELECT RAISE(ABORT, 'profile_keep: this config key is a stable profile fact (e.g. height_cm) and is never deleted by a reset -- leave it out of the reset; change its value with config-set');
END;

-- -- Map region per place / per leg -- migration 069 (user_version 69) -----
-- Which map app opens a move: Google by default; a region's local providers
-- are data (map_region), the place's region is looked up (place_region) or
-- read from its coordinates against the region boxes; the user's pick is
-- config map_provider_<CC>.  WHY in migrations/069_map_region.sql.
CREATE TABLE map_region (
  region     TEXT PRIMARY KEY CHECK (region GLOB '[A-Z][A-Z]'),
  label      TEXT CHECK (label IS NULL OR TRIM(label) <> ''),
  providers  TEXT NOT NULL DEFAULT '[]'
             CHECK (json_valid(providers) AND json_type(providers) = 'array'),
  lat_min    REAL, lat_max REAL, lng_min REAL, lng_max REAL,
  source     TEXT NOT NULL CHECK (TRIM(source) <> ''),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  CHECK ((lat_min IS NULL AND lat_max IS NULL AND lng_min IS NULL
          AND lng_max IS NULL)
      OR (lat_min IS NOT NULL AND lat_max IS NOT NULL
          AND lng_min IS NOT NULL AND lng_max IS NOT NULL
          AND lat_min >= -90 AND lat_max <= 90 AND lat_min < lat_max
          AND lng_min >= -180 AND lng_max <= 180 AND lng_min < lng_max))
);

CREATE TABLE place_region (
  place_id   INTEGER PRIMARY KEY REFERENCES place(id) ON DELETE CASCADE,
  region     TEXT NOT NULL CHECK (region GLOB '[A-Z][A-Z]'),
  source     TEXT NOT NULL CHECK (TRIM(source) <> ''),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

INSERT INTO map_region (region, label, providers, lat_min, lat_max, lng_min,
                        lng_max, source, updated_at)
VALUES ('KR', NULL, '["kakao","naver"]', 33.0, 38.7, 124.5, 132.0, 'seed',
        '2026-10-07T00:00:00Z');

-- -- module_shopping find-things -- migration 071 (user_version 71) -----
-- The find request is a ledger: hunt -> rounds -> shown cards.  Round shape
-- (3-4 distinct image-verified cards, coverage line, budget overrun) is
-- enforced in database/shop.py.  WHY in migrations/071_shop_find.sql.
CREATE TABLE shop_category (
  id              INTEGER PRIMARY KEY,
  name            TEXT NOT NULL UNIQUE
                  CHECK (name <> '' AND name NOT GLOB '*[^a-z0-9_]*'
                         AND name GLOB '[a-z]*'),
  display_name    TEXT CHECK (display_name IS NULL OR TRIM(display_name) <> ''),
  consumable      INTEGER NOT NULL DEFAULT 0 CHECK (consumable IN (0,1)),
  checks_required TEXT NOT NULL DEFAULT '["form","adult"]'
                  CHECK (json_valid(checks_required)
                         AND json_type(checks_required) = 'array'
                         AND json_array_length(checks_required) >= 1),
  active          INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
, spend_category_id INTEGER REFERENCES spend_category(id));

CREATE TABLE shop_item (
  id                  INTEGER PRIMARY KEY,
  ulid                TEXT NOT NULL UNIQUE,
  category_id         INTEGER NOT NULL REFERENCES shop_category(id),
  name                TEXT NOT NULL CHECK (TRIM(name) <> ''),
  brand               TEXT CHECK (brand IS NULL OR TRIM(brand) <> ''),
  variant             TEXT CHECK (variant IS NULL OR TRIM(variant) <> ''),
  url                 TEXT CHECK (url IS NULL OR TRIM(url) <> ''),
  image_ref           TEXT CHECK (image_ref IS NULL OR TRIM(image_ref) <> ''),
  aliases             TEXT NOT NULL DEFAULT '[]'
                      CHECK (json_valid(aliases) AND json_type(aliases) = 'array'),
  consumable_override INTEGER CHECK (consumable_override IS NULL
                                     OR consumable_override IN (0,1)),
  created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
CREATE INDEX ix_shop_item_category ON shop_item(category_id);
CREATE INDEX ix_shop_item_url ON shop_item(url) WHERE url IS NOT NULL;

CREATE TABLE shop_hunt (
  id            INTEGER PRIMARY KEY,
  ulid          TEXT NOT NULL UNIQUE,
  category_id   INTEGER NOT NULL REFERENCES shop_category(id),
  ask_verbatim  TEXT NOT NULL CHECK (TRIM(ask_verbatim) <> ''),
  for_person_id INTEGER REFERENCES persons(id),
  budget_max    INTEGER CHECK (budget_max IS NULL OR budget_max > 0),
  budget_hard   INTEGER NOT NULL DEFAULT 0 CHECK (budget_hard IN (0,1)),
  need_by       TEXT CHECK (need_by IS NULL
                            OR need_by GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'),
  state         TEXT NOT NULL DEFAULT 'open'
                CHECK (state IN ('open','picked','to_wish','abandoned','expired')),
  opened_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  closed_at     TEXT,
  CHECK (budget_hard = 0 OR budget_max IS NOT NULL),
  CHECK ((state = 'open') = (closed_at IS NULL) OR state = 'expired')
);
CREATE UNIQUE INDEX ux_shop_hunt_open ON shop_hunt(category_id,
  COALESCE(for_person_id, 0)) WHERE state = 'open';

CREATE TABLE shop_round (
  id              INTEGER PRIMARY KEY,
  hunt_id         INTEGER NOT NULL REFERENCES shop_hunt(id) ON DELETE CASCADE,
  round_no        INTEGER NOT NULL CHECK (round_no >= 1),
  sources_seen    TEXT NOT NULL
                  CHECK (json_valid(sources_seen)
                         AND json_type(sources_seen) = 'array'
                         AND json_array_length(sources_seen) >= 1),
  sources_blocked TEXT NOT NULL DEFAULT '[]'
                  CHECK (json_valid(sources_blocked)
                         AND json_type(sources_blocked) = 'array'),
  assumption      TEXT CHECK (assumption IS NULL OR TRIM(assumption) <> ''),
  partial_reason  TEXT CHECK (partial_reason IS NULL OR TRIM(partial_reason) <> ''),
  created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  UNIQUE (hunt_id, round_no)
);

CREATE TABLE shop_shown (
  id               INTEGER PRIMARY KEY,
  round_id         INTEGER NOT NULL REFERENCES shop_round(id) ON DELETE CASCADE,
  slot             INTEGER NOT NULL CHECK (slot BETWEEN 1 AND 4),
  rank             INTEGER NOT NULL CHECK (rank BETWEEN 1 AND 4),
  item_id          INTEGER NOT NULL REFERENCES shop_item(id),
  direction        TEXT NOT NULL CHECK (TRIM(direction) <> ''),
  price_shown      INTEGER CHECK (price_shown IS NULL OR price_shown >= 0),
  ship_by          TEXT CHECK (ship_by IS NULL
                               OR ship_by GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'),
  source           TEXT NOT NULL CHECK (TRIM(source) <> ''),
  checks           TEXT NOT NULL
                   CHECK (json_valid(checks) AND json_type(checks) = 'object'),
  reasons          TEXT NOT NULL DEFAULT '[]'
                   CHECK (json_valid(reasons) AND json_type(reasons) = 'array'
                          AND json_array_length(reasons) <= 3),
  review_count     INTEGER CHECK (review_count IS NULL OR review_count >= 0),
  verdict          TEXT CHECK (verdict IS NULL OR verdict IN
                     ('picked','rejected','accepted_default','void')),
  reject_scope     TEXT CHECK (reject_scope IS NULL OR reject_scope IN ('round','item')),
  verdict_verbatim TEXT,
  verdict_at       TEXT,
  closest          INTEGER NOT NULL DEFAULT 0 CHECK (closest IN (0,1)),
  UNIQUE (round_id, slot),
  UNIQUE (round_id, rank),
  UNIQUE (round_id, item_id),
  CHECK (reject_scope IS NULL OR verdict = 'rejected'),
  CHECK ((verdict IS NULL) = (verdict_at IS NULL)),
  CHECK (closest = 0 OR verdict = 'rejected')
);
CREATE INDEX ix_shop_shown_item ON shop_shown(item_id);

INSERT INTO shop_category (name, consumable, checks_required, created_at) VALUES
  ('apparel_top',      0, '["wearer","adult","form","colour"]', '2026-10-07T00:00:00Z'),
  ('apparel_bottom',   0, '["wearer","adult","form","colour"]', '2026-10-07T00:00:00Z'),
  ('apparel_onepiece', 0, '["wearer","adult","form","colour"]', '2026-10-07T00:00:00Z'),
  ('apparel_outer',    0, '["wearer","adult","form","colour"]', '2026-10-07T00:00:00Z'),
  ('shoes',            0, '["wearer","adult","form","colour"]', '2026-10-07T00:00:00Z'),
  ('haircare',         1, '["form","adult"]', '2026-10-07T00:00:00Z'),
  ('skincare',         1, '["form","adult"]', '2026-10-07T00:00:00Z'),
  ('supplement',       1, '["form","adult"]', '2026-10-07T00:00:00Z'),
  ('food_pantry',      1, '["form","adult"]', '2026-10-07T00:00:00Z'),
  ('gadget',           0, '["form","adult"]', '2026-10-07T00:00:00Z'),
  ('home_goods',       0, '["form","adult"]', '2026-10-07T00:00:00Z'),
  ('gift_misc',        0, '["form","adult"]', '2026-10-07T00:00:00Z');

-- -- module_shopping purchase log + taste -- migration 072 (user_version 72) --
-- taste_mark (stated taste per kind), owned_item (purchases; price has ONE
-- home: the linked spend_entry when the ledger link is on, else
-- owned_item.price), owned_item_measure (garment measurements, cm),
-- shop_category.spend_category_id (above), body measurement vocabulary
-- (metric_def + cm/mm units).  Verbs in database/shop.py.  WHY in
-- migrations/072_shop_log.sql.

CREATE TABLE taste_mark (
  id          INTEGER PRIMARY KEY,
  ulid        TEXT NOT NULL UNIQUE,
  category_id INTEGER NOT NULL REFERENCES shop_category(id),
  axis        TEXT NOT NULL CHECK (axis IN ('colour','fit','material','brand',
                                            'style','scent','price_band','size')),
  value       TEXT NOT NULL CHECK (TRIM(value) <> ''),
  polarity    TEXT NOT NULL CHECK (polarity IN ('like','avoid')),
  origin      TEXT NOT NULL DEFAULT 'stated'
              CHECK (origin IN ('stated','confirmed_tendency')),
  source_ref  TEXT CHECK (source_ref IS NULL OR TRIM(source_ref) <> ''),
  status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','retired')),
  recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  retired_at  TEXT,
  CHECK ((status = 'retired') = (retired_at IS NOT NULL))
);
CREATE UNIQUE INDEX ux_taste_mark_active ON taste_mark(category_id, axis,
  value, polarity) WHERE status = 'active';

CREATE TABLE owned_item (
  id                    INTEGER PRIMARY KEY,
  ulid                  TEXT NOT NULL UNIQUE,
  item_id               INTEGER NOT NULL REFERENCES shop_item(id),
  bought_on             TEXT NOT NULL
                        CHECK (bought_on GLOB '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'),
  store                 TEXT CHECK (store IS NULL OR TRIM(store) <> ''),
  link                  TEXT CHECK (link IS NULL OR TRIM(link) <> ''),
  hunt_id               INTEGER REFERENCES shop_hunt(id),
  spend_id              INTEGER REFERENCES spend_entry(id) ON DELETE SET NULL,
  price                 INTEGER CHECK (price IS NULL OR price >= 0),
  size_label            TEXT CHECK (size_label IS NULL OR TRIM(size_label) <> ''),
  satisfaction          TEXT CHECK (satisfaction IS NULL OR satisfaction IN
                                    ('liked','neutral','disliked')),
  satisfaction_verbatim TEXT,
  fit                   TEXT CHECK (fit IS NULL OR fit IN ('small','right','large')),
  status                TEXT NOT NULL DEFAULT 'in_use'
                        CHECK (status IN ('in_use','used_up','returned','gone')),
  status_on             TEXT CHECK (status_on IS NULL OR status_on GLOB
                                    '[0-9][0-9][0-9][0-9]-[0-1][0-9]-[0-3][0-9]'),
  created_at            TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  CHECK (spend_id IS NULL OR price IS NULL),
  CHECK ((status = 'in_use') = (status_on IS NULL))
);
CREATE INDEX ix_owned_item_item ON owned_item(item_id, bought_on);
CREATE INDEX ix_owned_item_spend ON owned_item(spend_id) WHERE spend_id IS NOT NULL;

CREATE TABLE owned_item_measure (
  id            INTEGER PRIMARY KEY,
  owned_item_id INTEGER NOT NULL REFERENCES owned_item(id) ON DELETE CASCADE,
  part          TEXT NOT NULL CHECK (part IN ('total_length','shoulder','chest_half',
                                              'waist_half','hip_half','thigh_half',
                                              'rise','hem_half','sleeve')),
  value_cm      REAL NOT NULL CHECK (value_cm > 0 AND value_cm < 300),
  recorded_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  UNIQUE (owned_item_id, part)
);

CREATE TRIGGER trg_spend_entry_owned_price
BEFORE DELETE ON spend_entry
WHEN EXISTS (SELECT 1 FROM owned_item WHERE spend_id = OLD.id)
BEGIN
  UPDATE owned_item
     SET spend_id = NULL,
         price = CASE WHEN OLD.amount >= 0 THEN OLD.amount ELSE NULL END
   WHERE spend_id = OLD.id;
END;

INSERT OR IGNORE INTO unit(token, dimension, origin) VALUES
  ('cm', 'length', 'module'),
  ('mm', 'length', 'module');

INSERT OR IGNORE INTO metric_def(token, scope_kind, accrual, unit_token,
                                 owner_module, origin, status) VALUES
  ('chest_cm',          'entity', 'observed', 'cm', 'health', 'module', 'active'),
  ('waist_cm',          'entity', 'observed', 'cm', 'health', 'module', 'active'),
  ('hip_cm',            'entity', 'observed', 'cm', 'health', 'module', 'active'),
  ('shoulder_width_cm', 'entity', 'observed', 'cm', 'health', 'module', 'active'),
  ('inseam_cm',         'entity', 'observed', 'cm', 'health', 'module', 'active'),
  ('arm_length_cm',     'entity', 'observed', 'cm', 'health', 'module', 'active'),
  ('foot_length_mm',    'entity', 'observed', 'mm', 'health', 'module', 'active');


-- -- proper-noun registry -- migration 073 (user_version 73) ---------------
-- event_group (groups attend blocks), noun_ask (typing-ask state of a span
-- that is not an entity), field_kind_gate (which entity kinds may fill which
-- field; frozen), ledger_guard noun_ask_daily_cap, room/kind triggers,
-- v_proper_noun (the dictionary view).  person_group.aliases/status and the
-- near_match_refusal CHECK are folded above.  Verbs in database/
-- noun_registry.py.  WHY in migrations/073_propernoun_registry.sql.

CREATE TABLE event_group (
    event_id  INTEGER NOT NULL REFERENCES block(id) ON DELETE CASCADE,
    group_id  INTEGER NOT NULL REFERENCES person_group(id) ON DELETE RESTRICT,
    PRIMARY KEY (event_id, group_id)
);
CREATE INDEX idx_event_group_group ON event_group(group_id);

CREATE TABLE noun_ask (
    query_key   TEXT PRIMARY KEY CHECK (query_key <> ''),
    query_text  TEXT NOT NULL,
    state       TEXT NOT NULL CHECK (state IN ('offered','auto_declined','not_a_name')),
    offered_n   INTEGER NOT NULL DEFAULT 0 CHECK (offered_n >= 0),
    field_token TEXT,
    said        TEXT,
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);

CREATE TABLE field_kind_gate (
    field_token TEXT NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('person','group','place','sub_place','project','method')),
    PRIMARY KEY (field_token, kind)
);
INSERT INTO field_kind_gate(field_token, kind) VALUES
  ('appt.place', 'place'),
  ('presence.place', 'place'),
  ('task.place', 'place'),
  ('appt.room', 'sub_place'),
  ('appt.participants', 'person'),
  ('appt.participants', 'group'),
  ('block.participants', 'person'),
  ('block.participants', 'group'),
  ('appt.origin', 'place'),
  ('travel.dest', 'place'),
  ('block.project', 'project');

CREATE TRIGGER trg_field_kind_gate_frozen_upd
BEFORE UPDATE ON field_kind_gate
BEGIN
  SELECT RAISE(ABORT, 'field_kind_gate is constitution data (which entity kinds may fill which field): it changes only through a kit migration');
END;
CREATE TRIGGER trg_field_kind_gate_frozen_del
BEFORE DELETE ON field_kind_gate
BEGIN
  SELECT RAISE(ABORT, 'field_kind_gate is constitution data (which entity kinds may fill which field): it changes only through a kit migration');
END;

INSERT OR IGNORE INTO ledger_guard(guard_key, scope, value, unit, enforcement,
                                   inviolable, source, updated_at)
  VALUES ('noun_ask_daily_cap', 'global', 3, 'day', 'hold', 0, 'seed',
          '2026-10-08 00:00:00');

-- A room may not carry the name of a group or a person (founding incident:
-- 피로물든방 is a band).  Compared on the spaceless, lower-cased form.
CREATE TRIGGER trg_sub_place_not_other_kind_ins
BEFORE INSERT ON sub_place
WHEN EXISTS (SELECT 1 FROM person_group g
              WHERE lower(replace(trim(g.name),' ','')) = lower(replace(trim(NEW.name),' ',''))
                 OR lower(replace(trim(COALESCE(g.display_name,'')),' ','')) = lower(replace(trim(NEW.name),' ',''))
                 OR (json_valid(g.aliases) AND EXISTS (SELECT 1 FROM json_each(g.aliases) a
                       WHERE lower(replace(trim(a.value),' ','')) = lower(replace(trim(NEW.name),' ','')))))
  OR EXISTS (SELECT 1 FROM persons p
              WHERE lower(replace(trim(p.name),' ','')) = lower(replace(trim(NEW.name),' ',''))
                 OR lower(replace(trim(COALESCE(p.display_name,'')),' ','')) = lower(replace(trim(NEW.name),' ',''))
                 OR instr(',' || lower(replace(COALESCE(p.aliases,''),' ','')) || ',',
                          ',' || lower(replace(trim(NEW.name),' ','')) || ',') > 0)
BEGIN
  SELECT RAISE(ABORT, 'CROSS_KIND sub_place refused: that name is registered as a group or a person, so it cannot be a room. Link the group/person as a participant (appt-group / block-person) and register the room under its own name');
END;
CREATE TRIGGER trg_sub_place_not_other_kind_upd
BEFORE UPDATE OF name ON sub_place
WHEN EXISTS (SELECT 1 FROM person_group g
              WHERE lower(replace(trim(g.name),' ','')) = lower(replace(trim(NEW.name),' ',''))
                 OR lower(replace(trim(COALESCE(g.display_name,'')),' ','')) = lower(replace(trim(NEW.name),' ',''))
                 OR (json_valid(g.aliases) AND EXISTS (SELECT 1 FROM json_each(g.aliases) a
                       WHERE lower(replace(trim(a.value),' ','')) = lower(replace(trim(NEW.name),' ','')))))
  OR EXISTS (SELECT 1 FROM persons p
              WHERE lower(replace(trim(p.name),' ','')) = lower(replace(trim(NEW.name),' ',''))
                 OR lower(replace(trim(COALESCE(p.display_name,'')),' ','')) = lower(replace(trim(NEW.name),' ',''))
                 OR instr(',' || lower(replace(COALESCE(p.aliases,''),' ','')) || ',',
                          ',' || lower(replace(trim(NEW.name),' ','')) || ',') > 0)
BEGIN
  SELECT RAISE(ABORT, 'CROSS_KIND sub_place refused: that name is registered as a group or a person, so it cannot be a room. Link the group/person as a participant (appt-group / block-person) and register the room under its own name');
END;

-- The room of a booking is a room OF that booking's place.
CREATE TRIGGER trg_block_attr_sub_place_ins
BEFORE INSERT ON block_attr
WHEN NEW.attr_token = 'sub_place_id'
 AND NOT EXISTS (SELECT 1 FROM sub_place sp JOIN block b ON b.id = NEW.block_id
                  WHERE CAST(sp.id AS TEXT) = NEW.value AND sp.place_id = b.place_id)
BEGIN
  SELECT RAISE(ABORT, 'E_ROOM_PARENT: block_attr sub_place_id must name a sub_place of the block''s own place (set the place first, or register the room under that place)');
END;
CREATE TRIGGER trg_block_attr_sub_place_upd
BEFORE UPDATE ON block_attr
WHEN NEW.attr_token = 'sub_place_id'
 AND NOT EXISTS (SELECT 1 FROM sub_place sp JOIN block b ON b.id = NEW.block_id
                  WHERE CAST(sp.id AS TEXT) = NEW.value AND sp.place_id = b.place_id)
BEGIN
  SELECT RAISE(ABORT, 'E_ROOM_PARENT: block_attr sub_place_id must name a sub_place of the block''s own place (set the place first, or register the room under that place)');
END;
CREATE TRIGGER trg_block_place_room_consistency
BEFORE UPDATE OF place_id ON block
WHEN EXISTS (SELECT 1 FROM block_attr ba JOIN sub_place sp ON CAST(sp.id AS TEXT) = ba.value
              WHERE ba.block_id = NEW.id AND ba.attr_token = 'sub_place_id'
                AND sp.place_id IS NOT NEW.place_id)
BEGIN
  SELECT RAISE(ABORT, 'E_ROOM_PARENT: this block carries a room of its current place; clear the room (appt-upd <id> room=) before moving it to another place');
END;
CREATE TRIGGER trg_sub_place_in_use_del
BEFORE DELETE ON sub_place
WHEN EXISTS (SELECT 1 FROM block_attr ba
              WHERE ba.attr_token = 'sub_place_id' AND ba.value = CAST(OLD.id AS TEXT))
BEGIN
  SELECT RAISE(ABORT, 'IN_USE sub_place: a booking still names this room (sub-place <place> rm <room> --into <other room> reassigns them)');
END;

-- The dictionary: one row per surface form.  kind / ref_id / surface /
-- surface_role (name|display_name|alias|token|composed) / parent_ref / status.
CREATE VIEW v_proper_noun AS
WITH RECURSIVE person_alias(id, rest, alias) AS (
    SELECT id, COALESCE(aliases, '') || ',', NULL FROM persons
  UNION ALL
    SELECT id, substr(rest, instr(rest, ',') + 1),
           trim(substr(rest, 1, instr(rest, ',') - 1))
      FROM person_alias WHERE rest <> ''
)
SELECT 'person' AS kind, id AS ref_id, name AS surface, 'name' AS surface_role,
       NULL AS parent_ref, 'active' AS status FROM persons
UNION ALL
SELECT 'person', id, display_name, 'display_name', NULL, 'active'
  FROM persons WHERE trim(COALESCE(display_name, '')) <> ''
UNION ALL
SELECT 'person', id, alias, 'alias', NULL, 'active'
  FROM person_alias WHERE alias IS NOT NULL AND alias <> ''
UNION ALL
SELECT 'group', id, name, 'name', NULL, status FROM person_group
UNION ALL
SELECT 'group', id, display_name, 'display_name', NULL, status
  FROM person_group WHERE trim(COALESCE(display_name, '')) <> ''
UNION ALL
SELECT 'group', g.id, a.value, 'alias', NULL, g.status
  FROM person_group g, json_each(CASE WHEN json_valid(g.aliases) THEN g.aliases ELSE '[]' END) a
 WHERE a.type = 'text' AND trim(a.value) <> ''
UNION ALL
SELECT 'place', id, name, 'name', NULL, status FROM place
UNION ALL
SELECT 'place', p.id, l.display, 'name', NULL, p.status
  FROM place p JOIN label l ON l.namespace = 'place' AND l.token = p.token AND l.lang = 'ko'
 WHERE l.display IS NOT p.name
UNION ALL
SELECT 'place', id, display_name, 'display_name', NULL, status
  FROM place WHERE trim(COALESCE(display_name, '')) <> ''
UNION ALL
SELECT 'place', p.id, a.value, 'alias', NULL, p.status
  FROM place p, json_each(CASE WHEN json_valid(p.aliases) THEN p.aliases ELSE '[]' END) a
 WHERE a.type = 'text' AND trim(a.value) <> ''
UNION ALL
SELECT 'sub_place', sp.id, sp.name, 'name', sp.place_id, p.status
  FROM sub_place sp JOIN place p ON p.id = sp.place_id
UNION ALL
SELECT 'sub_place', sp.id, sp.token, 'token', sp.place_id, p.status
  FROM sub_place sp JOIN place p ON p.id = sp.place_id
 WHERE sp.token IS NOT sp.name AND sp.token NOT LIKE 'sub\_%' ESCAPE '\'
UNION ALL
SELECT 'sub_place', sp.id, p.name || ' ' || sp.name, 'composed', sp.place_id, p.status
  FROM sub_place sp JOIN place p ON p.id = sp.place_id
UNION ALL
SELECT 'project', id, title, 'name', NULL,
       CASE WHEN status IN ('done','archived','cancelled','dropped') THEN 'gone' ELSE 'active' END
  FROM projects
UNION ALL
SELECT 'method', id, name, 'name', NULL, 'active' FROM pay_method
UNION ALL
SELECT 'method', m.id, a.value, 'alias', NULL, 'active'
  FROM pay_method m, json_each(CASE WHEN json_valid(m.aliases) THEN m.aliases ELSE '[]' END) a
 WHERE a.type = 'text' AND trim(a.value) <> '';

-- -- consult anchor -- migration 074 (user_version 74) ---------------------
-- availability_window (extra-day windows, availability-add) and
-- consult_answer (each consult answer -> the kit row that holds it,
-- consult-answer-add / consult-answers).  Verbs in database/consult_anchor.py.
-- WHY in migrations/074_consult_anchor.sql.
CREATE TABLE availability_window (
  id          INTEGER PRIMARY KEY,
  ulid        TEXT NOT NULL UNIQUE,
  domain      TEXT NOT NULL CHECK (TRIM(domain) <> ''),
  weekdays    TEXT NOT NULL CHECK (weekdays GLOB '[A-Z][A-Z][A-Z]*'),
  start_hm    TEXT CHECK (start_hm IS NULL OR start_hm GLOB '[0-2][0-9]:[0-5][0-9]'),
  end_hm      TEXT CHECK (end_hm IS NULL OR end_hm GLOB '[0-2][0-9]:[0-5][0-9]'),
  note        TEXT,
  status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','retired')),
  source      TEXT NOT NULL CHECK (TRIM(source) <> ''),
  recorded_at TEXT NOT NULL,
  retired_at  TEXT,
  CHECK ((start_hm IS NULL) = (end_hm IS NULL)),
  CHECK (start_hm IS NULL OR start_hm < end_hm),
  CHECK ((status = 'retired') = (retired_at IS NOT NULL))
);
CREATE UNIQUE INDEX ux_availability_window_active ON availability_window(
  domain, weekdays, COALESCE(start_hm, ''), COALESCE(end_hm, ''))
  WHERE status = 'active';

CREATE TABLE consult_answer (
  id            INTEGER PRIMARY KEY,
  ulid          TEXT NOT NULL UNIQUE,
  domain        TEXT NOT NULL CHECK (TRIM(domain) <> ''),
  why_id        INTEGER REFERENCES life_why(id),
  question      TEXT NOT NULL CHECK (question GLOB '[a-z]*'),
  words         TEXT,
  landed_table  TEXT NOT NULL CHECK (landed_table IN (
                  'ledger_resource','ledger_constraint','ledger_preference',
                  'availability_window','place','routine_def','config',
                  'life_why','ledger_goal','goal_target','metric_axis',
                  'pack_file')),
  landed_ref    TEXT NOT NULL CHECK (TRIM(landed_ref) <> ''),
  asked_by      TEXT NOT NULL CHECK (TRIM(asked_by) <> ''),
  status        TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active','superseded')),
  recorded_at   TEXT NOT NULL,
  superseded_at TEXT,
  CHECK ((status = 'superseded') = (superseded_at IS NOT NULL))
);
CREATE UNIQUE INDEX ux_consult_answer_active ON consult_answer(
  domain, question, landed_table, landed_ref) WHERE status = 'active';
CREATE INDEX ix_consult_answer_why ON consult_answer(why_id)
  WHERE why_id IS NOT NULL;

-- -- ledger v6 -- migration 075 (user_version 75) ---------------------------
-- (spend_entry / pay_method / fixed_cost columns are inline above)
-- ---- spend_entry ---------------------------------------------------------
CREATE UNIQUE INDEX ux_spend_import_key ON spend_entry(source, import_key)
  WHERE import_key IS NOT NULL;
CREATE INDEX ix_spend_pair ON spend_entry(pair_id) WHERE pair_id IS NOT NULL;
CREATE INDEX ix_spend_stream ON spend_entry(stream_id)
  WHERE stream_id IS NOT NULL;

-- ---- pay_method + pay_funding -------------------------------------------

CREATE TABLE pay_funding (
  wallet_id  INTEGER NOT NULL REFERENCES pay_method(id),
  source_id  INTEGER NOT NULL REFERENCES pay_method(id),
  sub        TEXT NOT NULL CHECK (sub IN ('balance','account','card','points','meal')),
  is_default INTEGER NOT NULL DEFAULT 0 CHECK (is_default IN (0,1)),
  first_seen TEXT,
  last_seen  TEXT,
  source_kind TEXT CHECK (source_kind IS NULL OR source_kind IN ('card','account')),
  PRIMARY KEY (wallet_id, source_id, sub),
  CHECK (wallet_id <> source_id)
);
CREATE UNIQUE INDEX ux_pay_funding_default ON pay_funding(wallet_id)
  WHERE is_default = 1;

-- ---- fixed_cost as a stream ---------------------------------------------

CREATE TABLE fixed_cost_key (
  fixed_id INTEGER NOT NULL REFERENCES fixed_cost(id) ON DELETE CASCADE,
  key      TEXT NOT NULL CHECK (TRIM(key) <> ''),
  UNIQUE (key)
);
CREATE INDEX ix_fixed_cost_key_fixed ON fixed_cost_key(fixed_id);
CREATE TRIGGER trg_fixed_cost_gone AFTER DELETE ON fixed_cost
BEGIN
  DELETE FROM fixed_cost_key WHERE fixed_id = OLD.id;
  UPDATE spend_entry SET stream_id = NULL WHERE stream_id = OLD.id;
END;

-- ---- spend <-> module entity ---------------------------------------------
CREATE TABLE spend_link_rule (
  id          INTEGER PRIMARY KEY,
  entity_kind TEXT NOT NULL CHECK (entity_kind IN ('person','venue','workout',
              'meal','supplement','gift','shop_item','owned_item')),
  entity_ref  TEXT NOT NULL CHECK (TRIM(entity_ref) <> ''),
  role        TEXT NOT NULL DEFAULT 'for' CHECK (role IN ('for','with')),
  match_json  TEXT NOT NULL CHECK (json_valid(match_json)
              AND json_type(match_json) = 'object'),
  said        TEXT,
  active      INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
  created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);
CREATE TABLE spend_link (
  spend_id    INTEGER NOT NULL,
  entity_kind TEXT NOT NULL CHECK (entity_kind IN ('person','venue','workout',
              'meal','supplement','gift','shop_item','owned_item')),
  entity_ref  TEXT NOT NULL CHECK (TRIM(entity_ref) <> ''),
  role        TEXT NOT NULL DEFAULT 'for' CHECK (role IN ('for','with')),
  source      TEXT NOT NULL CHECK (source IN ('owner','rule')),
  rule_id     INTEGER REFERENCES spend_link_rule(id),
  created_at  TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  PRIMARY KEY (spend_id, entity_kind, entity_ref),
  CHECK ((source = 'rule') = (rule_id IS NOT NULL))
);
CREATE INDEX ix_spend_link_entity ON spend_link(entity_kind, entity_ref);
-- a spend row that goes takes its links (foreign_keys is off on pack writers)
CREATE TRIGGER trg_spend_link_spend_gone AFTER DELETE ON spend_entry
BEGIN DELETE FROM spend_link WHERE spend_id = OLD.id; END;
-- a person that goes takes the links and standing rules that named them
CREATE TRIGGER trg_spend_link_person_gone AFTER DELETE ON persons
BEGIN
  DELETE FROM spend_link WHERE entity_kind = 'person'
     AND entity_ref = CAST(OLD.id AS TEXT);
  DELETE FROM spend_link_rule WHERE entity_kind = 'person'
     AND entity_ref = CAST(OLD.id AS TEXT);
END;

-- what each module reads: money spent on its domain.  Counts spend-class
-- money only (an owner's spend, whatever the link), never a transfer.
CREATE VIEW v_spend_link AS
SELECT l.entity_kind, l.entity_ref, l.role, l.source AS link_source,
       s.id AS spend_id, s.date, s.amount, s.name, s.method, s.scope,
       COALESCE(s.class, 'spend') AS class
  FROM spend_link l JOIN spend_entry s ON s.id = l.spend_id
 WHERE s.kind = 'expense';

-- 076: additive planning vocabulary; no reconciliation or reader cutover.
ALTER TABLE projects ADD COLUMN goal_id INTEGER REFERENCES goal(id) ON DELETE RESTRICT;
ALTER TABLE projects ADD COLUMN planning_state TEXT CHECK (planning_state IN ('draft','active','closed'));
ALTER TABLE projects ADD COLUMN closed_at TEXT;
ALTER TABLE projects ADD COLUMN close_reason TEXT CHECK (close_reason IN ('goal_reached','period_over','dropped'));
ALTER TABLE projects ADD COLUMN execution_grading TEXT NOT NULL DEFAULT 'count' CHECK (execution_grading IN ('count','weighted'));
ALTER TABLE goal ADD COLUMN lifecycle TEXT CHECK (lifecycle IN ('active','paused','achieved','failed','stopped','archived'));
ALTER TABLE goal ADD COLUMN closed_at TEXT;
ALTER TABLE goal ADD COLUMN close_reason TEXT;
ALTER TABLE goal ADD COLUMN domain TEXT;
ALTER TABLE goal ADD COLUMN managing_agent TEXT;
ALTER TABLE goal_target ADD COLUMN series_key TEXT;
ALTER TABLE goal_target ADD COLUMN judging_policy TEXT CHECK (judging_policy IN ('first_touch','window_average_in_band'));
ALTER TABLE goal_target ADD COLUMN window_days INTEGER CHECK (window_days > 0);
ALTER TABLE goal_target ADD COLUMN metric_class TEXT CHECK (metric_class IN ('exact','inbody','weight'));
ALTER TABLE goal_target ADD COLUMN policy_origin TEXT CHECK (policy_origin IN ('metric_default','owner'));
ALTER TABLE goal_target ADD COLUMN evidence_entity_id INTEGER REFERENCES entity(id);
ALTER TABLE goal_target ADD COLUMN evidence_domain TEXT;
ALTER TABLE goal_target ADD COLUMN unit_token TEXT REFERENCES unit(token);
ALTER TABLE commitment ADD COLUMN timezone TEXT;
ALTER TABLE commitment ADD COLUMN week_start INTEGER CHECK (week_start BETWEEN 0 AND 6);
ALTER TABLE commitment ADD COLUMN calendar_policy TEXT CHECK (calendar_policy IN ('every_period','weekdays'));
ALTER TABLE thesis ADD COLUMN scope TEXT CHECK (scope IN ('module','life'));
ALTER TABLE thesis ADD COLUMN domain TEXT;
ALTER TABLE thesis ADD COLUMN promoted_from_insight INTEGER REFERENCES insight(id);
ALTER TABLE thesis ADD COLUMN spectrum TEXT CHECK (spectrum IN ('casual','aspiration'));
ALTER TABLE thesis ADD COLUMN confidence REAL;
ALTER TABLE thesis ADD COLUMN managing_agent TEXT;
ALTER TABLE thesis ADD COLUMN label TEXT;
ALTER TABLE thesis ADD COLUMN updated_at TEXT;
ALTER TABLE thesis ADD COLUMN provenance TEXT;
ALTER TABLE thesis ADD COLUMN stated_on_provenance TEXT;

CREATE INDEX ix_projects_goal ON projects(goal_id);
CREATE UNIQUE INDEX ux_goal_target_series_version ON goal_target(goal_id, series_key, version)
 WHERE series_key IS NOT NULL;
CREATE UNIQUE INDEX ux_goal_target_series_current ON goal_target(goal_id, series_key)
 WHERE series_key IS NOT NULL AND valid_until IS NULL;

-- NULL planning_state/lifecycle/policy = unreconciled legacy, never guessed.
-- Existing projects are untouched. Only typed planning_state='active' is gated:
-- legacy project-add (status active, planning_state NULL, no goal/period) must
-- keep working until the writer cutover; that path is unreconciled legacy.
CREATE TRIGGER trg_project_planning_ins BEFORE INSERT ON projects
WHEN NEW.planning_state = 'active'
 AND (NEW.goal_id IS NULL OR NOT EXISTS (SELECT 1 FROM goal WHERE id=NEW.goal_id)
 OR NEW.start_date IS NULL OR NEW.end_date IS NULL
 OR date(NEW.start_date, '+0 days') IS NOT NEW.start_date
 OR date(NEW.end_date, '+0 days') IS NOT NEW.end_date OR NEW.end_date < NEW.start_date)
BEGIN SELECT RAISE(ABORT, 'active project requires goal and valid inclusive period'); END;
CREATE TRIGGER trg_project_planning_upd BEFORE UPDATE OF planning_state, goal_id, start_date, end_date, status ON projects
WHEN NEW.planning_state = 'active'
 AND (NEW.goal_id IS NULL OR NOT EXISTS (SELECT 1 FROM goal WHERE id=NEW.goal_id)
 OR NEW.start_date IS NULL OR NEW.end_date IS NULL
 OR date(NEW.start_date, '+0 days') IS NOT NEW.start_date
 OR date(NEW.end_date, '+0 days') IS NOT NEW.end_date OR NEW.end_date < NEW.start_date)
BEGIN SELECT RAISE(ABORT, 'active project requires goal and valid inclusive period'); END;
CREATE TRIGGER trg_project_closure_ins BEFORE INSERT ON projects
WHEN (NEW.closed_at IS NULL) <> (NEW.close_reason IS NULL)
 OR (NEW.planning_state = 'closed' AND NEW.closed_at IS NULL)
 OR (NEW.planning_state IS NOT 'closed' AND NEW.closed_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT, 'project closure requires state, time and reason'); END;
CREATE TRIGGER trg_goal_closure_ins BEFORE INSERT ON goal
WHEN (NEW.lifecycle IN ('achieved','failed','stopped','archived') AND NEW.close_reason IS NULL)
 OR (NEW.lifecycle IN ('active','paused') AND (NEW.closed_at IS NOT NULL OR NEW.close_reason IS NOT NULL))
BEGIN SELECT RAISE(ABORT, 'goal closure requires explicit lifecycle and reason'); END;
CREATE TRIGGER trg_target_policy_ins BEFORE INSERT ON goal_target
WHEN (NEW.judging_policy = 'window_average_in_band' AND
      (NEW.window_days IS NULL OR NEW.target_low IS NULL OR NEW.target_high IS NULL))
 OR (NEW.judging_policy = 'first_touch' AND NEW.window_days IS NOT NULL)
BEGIN SELECT RAISE(ABORT, 'invalid target judging policy'); END;
CREATE TRIGGER trg_commitment_goal_ins BEFORE INSERT ON commitment
WHEN NEW.status='active' AND (NEW.goal_id IS NULL OR NOT EXISTS (SELECT 1 FROM goal WHERE id=NEW.goal_id))
BEGIN SELECT RAISE(ABORT, 'active rule requires a goal'); END;
CREATE TRIGGER trg_project_closure_upd BEFORE UPDATE ON projects
WHEN (NEW.closed_at IS NULL) <> (NEW.close_reason IS NULL)
 OR (NEW.planning_state = 'closed' AND NEW.closed_at IS NULL)
 OR (NEW.planning_state IS NOT 'closed' AND NEW.closed_at IS NOT NULL)
BEGIN SELECT RAISE(ABORT, 'project closure requires state, time and reason'); END;
CREATE TRIGGER trg_goal_closure_upd BEFORE UPDATE ON goal
WHEN (NEW.lifecycle IN ('achieved','failed','stopped','archived') AND NEW.close_reason IS NULL)
 OR (NEW.lifecycle IN ('active','paused') AND (NEW.closed_at IS NOT NULL OR NEW.close_reason IS NOT NULL))
BEGIN SELECT RAISE(ABORT, 'goal closure requires explicit lifecycle and reason'); END;
CREATE TRIGGER trg_target_policy_upd BEFORE UPDATE ON goal_target
WHEN (NEW.judging_policy = 'window_average_in_band' AND
      (NEW.window_days IS NULL OR NEW.target_low IS NULL OR NEW.target_high IS NULL))
 OR (NEW.judging_policy = 'first_touch' AND NEW.window_days IS NOT NULL)
BEGIN SELECT RAISE(ABORT, 'invalid target judging policy'); END;
CREATE TRIGGER trg_commitment_goal_upd BEFORE UPDATE ON commitment
WHEN NEW.status='active' AND (NEW.goal_id IS NULL OR NOT EXISTS (SELECT 1 FROM goal WHERE id=NEW.goal_id))
BEGIN SELECT RAISE(ABORT, 'active rule requires a goal'); END;
CREATE TRIGGER trg_planning_goal_delete BEFORE DELETE ON goal
WHEN EXISTS (SELECT 1 FROM commitment WHERE goal_id=OLD.id)
 OR EXISTS (SELECT 1 FROM projects WHERE goal_id=OLD.id)
BEGIN SELECT RAISE(ABORT, 'goal is referenced by planning'); END;
DROP TRIGGER trg_goal_target_no_inplace_edit;
CREATE TRIGGER trg_goal_target_no_inplace_edit BEFORE UPDATE ON goal_target
WHEN NEW.goal_id IS NOT OLD.goal_id OR NEW.metric IS NOT OLD.metric OR NEW.target_low IS NOT OLD.target_low OR NEW.target_high IS NOT OLD.target_high OR NEW.baseline_value IS NOT OLD.baseline_value OR NEW.baseline_as_of IS NOT OLD.baseline_as_of OR NEW.series_key IS NOT OLD.series_key OR NEW.judging_policy IS NOT OLD.judging_policy OR NEW.window_days IS NOT OLD.window_days OR NEW.metric_class IS NOT OLD.metric_class OR NEW.policy_origin IS NOT OLD.policy_origin OR NEW.evidence_entity_id IS NOT OLD.evidence_entity_id OR NEW.evidence_domain IS NOT OLD.evidence_domain OR NEW.unit_token IS NOT OLD.unit_token OR NEW.applies_when IS NOT OLD.applies_when OR NEW.valid_from IS NOT OLD.valid_from OR NEW.version IS NOT OLD.version OR NEW.supersedes IS NOT OLD.supersedes
BEGIN SELECT RAISE(ABORT, 'goal_target: insert a successor version'); END;
DROP TRIGGER trg_commitment_no_inplace_edit;
CREATE TRIGGER trg_commitment_no_inplace_edit BEFORE UPDATE ON commitment
WHEN NEW.goal_id IS NOT OLD.goal_id OR NEW.metric IS NOT OLD.metric OR NEW.lo IS NOT OLD.lo OR NEW.hi IS NOT OLD.hi OR NEW.kind IS NOT OLD.kind OR NEW.period IS NOT OLD.period OR NEW.enforcement IS NOT OLD.enforcement OR NEW.spec IS NOT OLD.spec OR NEW.timezone IS NOT OLD.timezone OR NEW.week_start IS NOT OLD.week_start OR NEW.calendar_policy IS NOT OLD.calendar_policy OR NEW.valid_from IS NOT OLD.valid_from OR NEW.version IS NOT OLD.version OR NEW.supersedes IS NOT OLD.supersedes
BEGIN SELECT RAISE(ABORT, 'commitment: insert a successor version'); END;

CREATE TABLE commitment_evidence (
 commitment_id INTEGER NOT NULL REFERENCES commitment(id) ON DELETE RESTRICT,
 local_date TEXT NOT NULL CHECK (date(local_date, '+0 days') IS local_date),
 result TEXT NOT NULL CHECK (result IN ('kept','broken','not_logged','not_applicable')),
 source_ref TEXT NOT NULL CHECK (length(trim(source_ref)) > 0),
 evaluator_version TEXT NOT NULL CHECK (length(trim(evaluator_version)) > 0),
 recorded_at TEXT NOT NULL,
 PRIMARY KEY (commitment_id, local_date)
);
-- A commitment row IS its temporal version. For weekly quotas local_date is
-- the declared week start, never seven synthetic daily successes.
CREATE TRIGGER trg_commitment_evidence_immutable BEFORE UPDATE ON commitment_evidence
BEGIN SELECT RAISE(ABORT, 'evidence is immutable; conflicting retry needs reconciliation'); END;

CREATE TABLE task (
 block_id INTEGER PRIMARY KEY REFERENCES block(id) ON DELETE RESTRICT,
 checkable_result TEXT NOT NULL CHECK (length(trim(checkable_result)) > 0),
 estimated_minutes REAL CHECK (estimated_minutes > 0),
 estimate_origin TEXT CHECK (estimate_origin IN ('owner','history','agent')),
 estimated_at TEXT,
 estimate_version INTEGER NOT NULL DEFAULT 1 CHECK (estimate_version > 0),
 exec_state TEXT NOT NULL DEFAULT 'open' CHECK (exec_state IN ('open','doing','waiting')),
 waiting_reason TEXT, waiting_since TEXT, review_after TEXT,
 waiting_for TEXT,
 CHECK ((estimated_minutes IS NULL AND estimate_origin IS NULL AND estimated_at IS NULL)
     OR (estimated_minutes IS NOT NULL AND estimate_origin IS NOT NULL AND estimated_at IS NOT NULL)),
 CHECK ((exec_state='waiting' AND length(trim(waiting_reason)) > 0 AND waiting_reason IS NOT NULL AND waiting_since IS NOT NULL)
     OR (exec_state<>'waiting' AND waiting_reason IS NULL AND waiting_since IS NULL AND review_after IS NULL AND waiting_for IS NULL))
);
CREATE TABLE task_estimate_history (
 task_id INTEGER NOT NULL REFERENCES task(block_id),
 version INTEGER NOT NULL, estimated_minutes REAL, origin TEXT, estimated_at TEXT,
 PRIMARY KEY(task_id, version)
);
CREATE TRIGGER trg_task_estimate_insert AFTER INSERT ON task BEGIN
 INSERT INTO task_estimate_history VALUES (NEW.block_id, NEW.estimate_version, NEW.estimated_minutes, NEW.estimate_origin, NEW.estimated_at);
END;
CREATE TRIGGER trg_task_estimate_revision BEFORE UPDATE ON task
WHEN (NEW.estimated_minutes IS NOT OLD.estimated_minutes OR NEW.estimate_origin IS NOT OLD.estimate_origin OR NEW.estimated_at IS NOT OLD.estimated_at OR NEW.estimate_version IS NOT OLD.estimate_version)
 AND NEW.estimate_version <> OLD.estimate_version + 1
BEGIN SELECT RAISE(ABORT, 'estimate edit requires next version'); END;
CREATE TRIGGER trg_task_estimate_update AFTER UPDATE OF estimate_version ON task
WHEN NEW.estimate_version <> OLD.estimate_version BEGIN
 INSERT INTO task_estimate_history VALUES (NEW.block_id, NEW.estimate_version, NEW.estimated_minutes, NEW.estimate_origin, NEW.estimated_at);
END;
CREATE TABLE task_execution (
 task_id INTEGER NOT NULL REFERENCES task(block_id) ON DELETE RESTRICT,
 block_id INTEGER NOT NULL UNIQUE REFERENCES block(id) ON DELETE RESTRICT,
 PRIMARY KEY(task_id, block_id), CHECK (task_id <> block_id)
);
CREATE TRIGGER trg_task_execution_plan BEFORE INSERT ON task_execution
WHEN EXISTS (SELECT 1 FROM task WHERE block_id=NEW.block_id)
BEGIN SELECT RAISE(ABORT, 'task plan cannot be an execution session'); END;
CREATE TRIGGER trg_task_plan_execution BEFORE INSERT ON task
WHEN EXISTS (SELECT 1 FROM task_execution WHERE block_id=NEW.block_id)
BEGIN SELECT RAISE(ABORT, 'execution session cannot be a task plan'); END;
CREATE TRIGGER trg_task_execution_immutable BEFORE UPDATE ON task_execution
BEGIN SELECT RAISE(ABORT, 'execution identity is immutable'); END;

-- Explicit snapshots: later membership, weights or goal edits cannot rewrite
-- a saved retro. Estimates are never completion weights.
CREATE TABLE project_scope (
 id INTEGER PRIMARY KEY,
 project_id INTEGER NOT NULL REFERENCES projects(id),
 revision INTEGER NOT NULL CHECK (revision > 0),
 captured_at TEXT NOT NULL,
 sealed INTEGER NOT NULL DEFAULT 0 CHECK (sealed IN (0,1)),
 goal_id INTEGER REFERENCES goal(id),
 grading TEXT NOT NULL CHECK (grading IN ('count','weighted')),
 UNIQUE(project_id, revision)
);
CREATE TABLE project_scope_task (
 scope_id INTEGER NOT NULL REFERENCES project_scope(id),
 task_id INTEGER NOT NULL REFERENCES task(block_id),
 result_state TEXT NOT NULL CHECK (result_state IN ('done','open','doing','waiting','cancelled','removed')),
 weight REAL CHECK (weight > 0),
 PRIMARY KEY(scope_id, task_id)
);
CREATE TRIGGER trg_project_scope_immutable BEFORE UPDATE ON project_scope
WHEN OLD.sealed=1 OR NEW.sealed<>1 OR NEW.id IS NOT OLD.id OR NEW.project_id IS NOT OLD.project_id
 OR NEW.revision IS NOT OLD.revision OR NEW.captured_at IS NOT OLD.captured_at OR NEW.goal_id IS NOT OLD.goal_id OR NEW.grading IS NOT OLD.grading
BEGIN SELECT RAISE(ABORT, 'scope snapshots are immutable'); END;
CREATE TRIGGER trg_project_scope_task_immutable BEFORE UPDATE ON project_scope_task
BEGIN SELECT RAISE(ABORT, 'scope snapshots are immutable'); END;

CREATE TRIGGER trg_scope_member_sealed BEFORE INSERT ON project_scope_task
WHEN (SELECT sealed FROM project_scope WHERE id=NEW.scope_id)=1
BEGIN SELECT RAISE(ABORT, 'scope is sealed'); END;
CREATE TRIGGER trg_scope_seal_weights BEFORE UPDATE OF sealed ON project_scope
WHEN NEW.sealed=1 AND NEW.grading='weighted' AND EXISTS
 (SELECT 1 FROM project_scope_task WHERE scope_id=NEW.id AND weight IS NULL)
BEGIN SELECT RAISE(ABORT, 'weighted scope needs every weight'); END;
CREATE TRIGGER trg_target_series_insert BEFORE INSERT ON goal_target
WHEN NEW.series_key IS NOT NULL AND
 (length(trim(NEW.series_key))=0 OR NEW.judging_policy IS NULL OR NEW.valid_from IS NULL
 OR (NEW.evidence_entity_id IS NULL AND NEW.evidence_domain IS NULL)
 OR (NEW.supersedes IS NULL AND NEW.version<>1)
 OR (NEW.supersedes IS NOT NULL AND NOT EXISTS
    (SELECT 1 FROM goal_target p WHERE p.id=NEW.supersedes AND p.goal_id=NEW.goal_id
       AND p.series_key=NEW.series_key AND NEW.version=p.version+1
       AND p.valid_until=NEW.valid_from)))
BEGIN SELECT RAISE(ABORT, 'typed target requires scoped policy and contiguous successor'); END;
CREATE TRIGGER trg_target_closure_immutable BEFORE UPDATE OF valid_until ON goal_target
WHEN OLD.series_key IS NOT NULL AND OLD.valid_until IS NOT NULL AND NEW.valid_until IS NOT OLD.valid_until
BEGIN SELECT RAISE(ABORT, 'closed target span is immutable'); END;
CREATE TRIGGER trg_rule_closure_immutable BEFORE UPDATE OF valid_until ON commitment
WHEN OLD.timezone IS NOT NULL AND OLD.valid_until IS NOT NULL AND NEW.valid_until IS NOT OLD.valid_until
BEGIN SELECT RAISE(ABORT, 'closed rule span is immutable'); END;
CREATE TRIGGER trg_rule_status_version BEFORE UPDATE OF status ON commitment
WHEN OLD.timezone IS NOT NULL AND NEW.status IS NOT OLD.status
BEGIN SELECT RAISE(ABORT, 'typed rule pause/retire requires successor'); END;
CREATE TRIGGER trg_project_scope_no_delete BEFORE DELETE ON project_scope
BEGIN SELECT RAISE(ABORT, 'planning history cannot be deleted'); END;
CREATE TRIGGER trg_project_scope_task_no_delete BEFORE DELETE ON project_scope_task
BEGIN SELECT RAISE(ABORT, 'planning history cannot be deleted'); END;
CREATE TRIGGER trg_task_estimate_history_no_delete BEFORE DELETE ON task_estimate_history
BEGIN SELECT RAISE(ABORT, 'planning history cannot be deleted'); END;
CREATE TRIGGER trg_commitment_evidence_no_delete BEFORE DELETE ON commitment_evidence
BEGIN SELECT RAISE(ABORT, 'planning history cannot be deleted'); END;
CREATE TRIGGER trg_task_history_immutable BEFORE UPDATE ON task_estimate_history
BEGIN SELECT RAISE(ABORT, 'estimate history is immutable'); END;

-- Provenance is optional for old adapters. Missing evidence is never a
-- license to delete an owner row. Archive rows preserve SQLite value types.
CREATE TABLE ingest_effect (
  source TEXT NOT NULL,
  kind TEXT NOT NULL,
  ext_id TEXT NOT NULL,
  target_table TEXT NOT NULL,
  target_id TEXT NOT NULL,
  effect_json TEXT NOT NULL CHECK (json_valid(effect_json)),
  PRIMARY KEY (source, kind, ext_id),
  FOREIGN KEY (source, kind, ext_id) REFERENCES ingest_ref(source, kind, ext_id)
);
CREATE TABLE archive_ingest_reset_runs (
  reset_id TEXT PRIMARY KEY,
  source TEXT NOT NULL,
  kinds_json TEXT NOT NULL,
  archived_at TEXT NOT NULL,
  undone_at TEXT
);
CREATE TABLE archive_ingest_reset_rows (
  reset_id TEXT NOT NULL REFERENCES archive_ingest_reset_runs(reset_id),
  ordinal INTEGER NOT NULL,
  table_name TEXT NOT NULL,
  key_json TEXT NOT NULL,
  before_json TEXT NOT NULL,
  after_json TEXT,
  archive_rowid INTEGER NOT NULL,
  PRIMARY KEY (reset_id, ordinal)
);


-- 078: canonical live-session log, history remains legacy until explicit import.
CREATE TABLE training_policy_bundles (
 id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, version INTEGER NOT NULL,
 parent_defaults_version TEXT NOT NULL, effective_at INTEGER NOT NULL,
 digest TEXT NOT NULL, UNIQUE(owner_id,version)
);
CREATE TABLE training_policy_rules (
 bundle_id TEXT NOT NULL REFERENCES training_policy_bundles(id), key TEXT NOT NULL,
 value_type TEXT NOT NULL, value_json TEXT NOT NULL CHECK(json_valid(value_json)),
 scope TEXT NOT NULL CHECK(scope IN ('general','owner','session')),
 source_ref TEXT NOT NULL, source_date TEXT NOT NULL,
 approval_status TEXT NOT NULL CHECK(approval_status IN ('proposed','approved','rejected')),
 PRIMARY KEY(bundle_id,key,scope)
);
CREATE TABLE workout_lifts (
 id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES workouts(id),
 exercise_id TEXT NOT NULL, exercise_name TEXT NOT NULL, slot_num INTEGER,
 order_index INTEGER NOT NULL, status TEXT NOT NULL CHECK(status IN ('open','closed','skipped')),
 closed_reason TEXT, closed_revision INTEGER, execution_index INTEGER,
 variant_id TEXT NOT NULL, machine_ref TEXT, implement_resolution_id TEXT,
 UNIQUE(session_id,order_index), UNIQUE(id,session_id)
);
CREATE TABLE session_equipment_snapshots (
 id TEXT PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES workouts(id), catalog_version INTEGER NOT NULL,
 variant_id TEXT NOT NULL, machine_ref TEXT, grid_version TEXT NOT NULL, inventory_version TEXT NOT NULL,
 load_basis TEXT NOT NULL CHECK(load_basis IN ('total','per_hand','assistance')),
 implement_count INTEGER NOT NULL CHECK(implement_count>=0), mounted_load_g INTEGER CHECK(mounted_load_g>=0), evidence_id TEXT
);
CREATE TABLE session_commands (
 session_id INTEGER NOT NULL REFERENCES workouts(id), command_id TEXT NOT NULL,
 event_version INTEGER NOT NULL CHECK(event_version=2), actor TEXT NOT NULL CHECK(actor IN ('member','trainer','system')),
 expected_revision INTEGER NOT NULL, input_digest TEXT NOT NULL, event_type TEXT NOT NULL,
 input_json TEXT NOT NULL CHECK(json_valid(input_json)), committed_revision INTEGER NOT NULL,
 receipt_json TEXT NOT NULL CHECK(json_valid(receipt_json)), accepted_at INTEGER NOT NULL,
 source_message_id TEXT, undo_of TEXT, PRIMARY KEY(session_id,command_id)
);
CREATE TABLE session_trainer_approvals (
 id TEXT PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES workouts(id), base_revision INTEGER NOT NULL,
 view_digest TEXT NOT NULL, decision_json TEXT NOT NULL CHECK(json_valid(decision_json)), cue_ast TEXT NOT NULL CHECK(json_valid(cue_ast)),
 dependency_digest TEXT NOT NULL, policy_bundle_id TEXT NOT NULL REFERENCES training_policy_bundles(id),
 catalog_version INTEGER NOT NULL, expires_epoch INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('prepared','bound','invalidated','expired'))
);
CREATE TABLE session_outbox (
 session_id INTEGER NOT NULL REFERENCES workouts(id), revision INTEGER NOT NULL,
 frame_kind TEXT NOT NULL CHECK(frame_kind IN ('step','answer','closeout','timer','setup')),
 session_epoch INTEGER NOT NULL, rest_epoch INTEGER, view_digest TEXT NOT NULL,
 trainer_approval_id TEXT REFERENCES session_trainer_approvals(id), not_before_at INTEGER NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('waiting_trainer','ready','publishing','delivered','superseded','uncertain')),
 stage TEXT NOT NULL CHECK(stage IN ('retire_markup','neutralize_trainer','edit_table','edit_trainer','attach_keyboard','acknowledged')),
 attempts INTEGER NOT NULL DEFAULT 0, lease_fence INTEGER NOT NULL DEFAULT 0, last_error TEXT,
 PRIMARY KEY(session_id,revision,frame_kind)
);
CREATE TABLE session_surfaces (
 session_id INTEGER NOT NULL REFERENCES workouts(id),
 surface_kind TEXT NOT NULL CHECK(surface_kind IN ('table','trainer','pin','end_workout','end_fatigue','end_calories')),
 chat_id TEXT NOT NULL, message_id TEXT NOT NULL, published_revision INTEGER NOT NULL,
 body_digest TEXT NOT NULL, markup_digest TEXT NOT NULL,
 markup_state TEXT NOT NULL CHECK(markup_state IN ('clear','active','unknown')),
 delivery_key TEXT NOT NULL, receipt_id TEXT NOT NULL, PRIMARY KEY(session_id,surface_kind),
 CHECK(markup_state!='active' OR surface_kind='trainer')
);
CREATE TABLE session_evidence (
 id TEXT PRIMARY KEY, session_id INTEGER NOT NULL REFERENCES workouts(id), event_id TEXT NOT NULL,
 kind TEXT NOT NULL, value TEXT NOT NULL CHECK(json_valid(value)), unit TEXT,
 scope TEXT NOT NULL CHECK(scope IN ('entry','set','lift','session')), observed_at INTEGER NOT NULL,
 source_kind TEXT NOT NULL, source_ref TEXT NOT NULL
);
CREATE TABLE session_delivery_journal (
 journal_seq INTEGER PRIMARY KEY, operation_id TEXT NOT NULL, session_id INTEGER NOT NULL REFERENCES workouts(id),
 command_id TEXT NOT NULL, revision INTEGER NOT NULL, frame_kind TEXT NOT NULL,
 sender TEXT NOT NULL CHECK(sender='publisher'), surface_kind TEXT, message_id TEXT, callback_digest TEXT,
 session_epoch INTEGER NOT NULL, rest_epoch INTEGER, operation TEXT NOT NULL, request_digest TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('requested','acknowledged','unchanged','rejected','failed','uncertain','quarantined','reconciled')),
 acknowledged_result TEXT CHECK(acknowledged_result IS NULL OR json_valid(acknowledged_result)),
 latency_ms INTEGER NOT NULL CHECK(latency_ms>=0), error TEXT, artifact_digest TEXT NOT NULL,
 delivery_key TEXT NOT NULL, lease_fence INTEGER NOT NULL,
 CHECK((state IN ('acknowledged','unchanged','reconciled')) = (acknowledged_result IS NOT NULL))
);
ALTER TABLE workouts ADD COLUMN type TEXT;
ALTER TABLE workouts ADD COLUMN member_id TEXT;
ALTER TABLE workouts ADD COLUMN session_version INTEGER NOT NULL DEFAULT 1 CHECK(session_version IN (1,2));
ALTER TABLE workouts ADD COLUMN revision INTEGER NOT NULL DEFAULT 0 CHECK(revision>=0);
ALTER TABLE workouts ADD COLUMN phase TEXT CHECK(phase IN ('plan','prep','entry','ready','rest','lift_done','post','closed'));
ALTER TABLE workouts ADD COLUMN active_lift_id INTEGER REFERENCES workout_lifts(id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE workouts ADD COLUMN active_set_id INTEGER REFERENCES workout_sets(id) DEFERRABLE INITIALLY DEFERRED;
ALTER TABLE workouts ADD COLUMN action_token_hash TEXT;
ALTER TABLE workouts ADD COLUMN action_kind TEXT;
ALTER TABLE workouts ADD COLUMN input_stage TEXT;
ALTER TABLE workouts ADD COLUMN policy_bundle_id TEXT REFERENCES training_policy_bundles(id);
ALTER TABLE workouts ADD COLUMN catalog_version INTEGER;
ALTER TABLE workouts ADD COLUMN session_epoch INTEGER NOT NULL DEFAULT 0;
ALTER TABLE workouts ADD COLUMN rest_epoch INTEGER NOT NULL DEFAULT 0;
ALTER TABLE workouts ADD COLUMN rest_started_at INTEGER;
ALTER TABLE workouts ADD COLUMN rest_deadline_at INTEGER;
ALTER TABLE workouts ADD COLUMN rest_duration_ms INTEGER;
ALTER TABLE workouts ADD COLUMN closed_at INTEGER;
ALTER TABLE workouts ADD COLUMN close_reason TEXT;
ALTER TABLE workouts ADD COLUMN kcal_status TEXT NOT NULL DEFAULT 'pending' CHECK(kcal_status IN ('pending','measured','unmeasured'));
ALTER TABLE workout_sets ADD COLUMN lift_id INTEGER REFERENCES workout_lifts(id);
ALTER TABLE workout_sets ADD COLUMN row_status TEXT NOT NULL DEFAULT 'logged' CHECK(row_status IN ('planned','logged','void','omitted'));
ALTER TABLE workout_sets ADD COLUMN step_order INTEGER NOT NULL DEFAULT 0;
ALTER TABLE workout_sets ADD COLUMN target_load_g INTEGER CHECK(target_load_g>=0);
ALTER TABLE workout_sets ADD COLUMN target_rir_min INTEGER CHECK(target_rir_min BETWEEN 0 AND 4);
ALTER TABLE workout_sets ADD COLUMN target_rir_max INTEGER CHECK(target_rir_max BETWEEN 0 AND 4);
ALTER TABLE workout_sets ADD COLUMN target_rest_ms INTEGER CHECK(target_rest_ms>=0);
ALTER TABLE workout_sets ADD COLUMN target_unit TEXT NOT NULL DEFAULT 'reps' CHECK(target_unit IN ('reps','seconds','breaths','steps'));
ALTER TABLE workout_sets ADD COLUMN target_count INTEGER;
ALTER TABLE workout_sets ADD COLUMN target_hold_sec INTEGER;
ALTER TABLE workout_sets ADD COLUMN actual_hold_sec INTEGER;
ALTER TABLE workout_sets ADD COLUMN actual_unit TEXT CHECK(actual_unit IN ('reps','seconds','breaths','steps'));
ALTER TABLE workout_sets ADD COLUMN rir_is_lower_bound INTEGER NOT NULL DEFAULT 0 CHECK(rir_is_lower_bound IN (0,1));
ALTER TABLE workout_sets ADD COLUMN actual_source TEXT DEFAULT 'legacy_unknown' CHECK(actual_source IN ('tap_confirmed','typed','legacy_unknown'));
ALTER TABLE workout_sets ADD COLUMN prescribed_revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE workout_sets ADD COLUMN recorded_revision INTEGER;
ALTER TABLE workout_sets ADD COLUMN policy_bundle_id TEXT REFERENCES training_policy_bundles(id);
ALTER TABLE workout_sets ADD COLUMN basis_event_id TEXT;
ALTER TABLE workout_sets ADD COLUMN equipment_snapshot_id TEXT REFERENCES session_equipment_snapshots(id);
CREATE UNIQUE INDEX one_open_workout_per_member ON workouts(member_id) WHERE session_version=2 AND phase!='closed';
CREATE UNIQUE INDEX one_active_keyboard ON session_surfaces(session_id) WHERE markup_state='active';
CREATE UNIQUE INDEX one_active_keyboard_per_chat ON session_surfaces(chat_id) WHERE markup_state='active';
CREATE INDEX idx_v2_set_order ON workout_sets(session_id,lift_id,row_status,step_order);
CREATE VIEW v2_logged_workout_sets AS SELECT * FROM workout_sets WHERE row_status='logged';
-- Extra service metadata is not an alternative prescription store.
CREATE TABLE session_input_state (
 session_id INTEGER PRIMARY KEY REFERENCES workouts(id), member_id TEXT NOT NULL,
 chat_id TEXT NOT NULL, pending_completed_at INTEGER, started_at INTEGER NOT NULL,
 token_nonce TEXT,
 mode TEXT NOT NULL CHECK(mode IN ('regular','free')), split_id TEXT, claimed_split_id TEXT
);
CREATE TABLE session_import_audit (
 source_digest TEXT NOT NULL, session_id INTEGER NOT NULL REFERENCES workouts(id),
 resolution_json TEXT NOT NULL CHECK(json_valid(resolution_json)), accepted_at INTEGER NOT NULL,
 PRIMARY KEY(source_digest,session_id)
);
CREATE TRIGGER v2_validate_workouts_insert BEFORE INSERT ON workouts
WHEN NEW.session_version=2 AND (NEW.member_id IS NULL OR NEW.phase IS NULL OR NEW.policy_bundle_id IS NULL OR NEW.catalog_version IS NULL OR
 (NEW.phase='closed' AND (NEW.rest_deadline_at IS NOT NULL OR NEW.action_token_hash IS NOT NULL OR NEW.active_set_id IS NOT NULL OR NEW.closed_at IS NULL)) OR
 (NEW.rest_deadline_at IS NOT NULL AND (NEW.phase!='rest' OR NEW.rest_started_at IS NULL OR NEW.rest_duration_ms IS NULL OR NEW.rest_duration_ms<0 OR NEW.rest_deadline_at!=NEW.rest_started_at+NEW.rest_duration_ms)) OR
 (NEW.active_lift_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM workout_lifts WHERE id=NEW.active_lift_id AND session_id=NEW.id AND status='open')) OR
 (NEW.active_set_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM workout_sets WHERE id=NEW.active_set_id AND session_id=NEW.id AND row_status IN ('planned','void'))))
BEGIN SELECT RAISE(ABORT,'invalid v2 workouts'); END;
CREATE TRIGGER v2_validate_workouts_update BEFORE UPDATE ON workouts
WHEN NEW.session_version=2 AND (NEW.member_id IS NULL OR NEW.phase IS NULL OR NEW.policy_bundle_id IS NULL OR NEW.catalog_version IS NULL OR
 (NEW.phase='closed' AND (NEW.rest_deadline_at IS NOT NULL OR NEW.action_token_hash IS NOT NULL OR NEW.active_set_id IS NOT NULL OR NEW.closed_at IS NULL)) OR
 (NEW.rest_deadline_at IS NOT NULL AND (NEW.phase!='rest' OR NEW.rest_started_at IS NULL OR NEW.rest_duration_ms IS NULL OR NEW.rest_duration_ms<0 OR NEW.rest_deadline_at!=NEW.rest_started_at+NEW.rest_duration_ms)) OR
 (NEW.active_lift_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM workout_lifts WHERE id=NEW.active_lift_id AND session_id=NEW.id AND status='open')) OR
 (NEW.active_set_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM workout_sets WHERE id=NEW.active_set_id AND session_id=NEW.id AND row_status IN ('planned','void'))))
BEGIN SELECT RAISE(ABORT,'invalid v2 workouts'); END;
CREATE TRIGGER v2_validate_workout_sets_insert BEFORE INSERT ON workout_sets
WHEN (SELECT session_version FROM workouts WHERE id=NEW.session_id)=2 AND (
 NEW.actual_source IS 'legacy_unknown' OR NEW.policy_bundle_id IS NULL OR NEW.target_rest_ms IS NULL OR
 (NEW.lift_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM workout_lifts WHERE id=NEW.lift_id AND session_id=NEW.session_id)) OR
 (NEW.target_unit='reps' AND (NEW.target_reps IS NULL OR NEW.target_reps<=0 OR NEW.target_count IS NOT NULL)) OR
 (NEW.target_unit!='reps' AND (NEW.target_reps IS NOT NULL OR NEW.target_count IS NULL OR NEW.target_count<=0)) OR
 (NEW.actual_unit IS NOT NULL AND NEW.actual_unit!=NEW.target_unit) OR
 (NEW.row_status='logged' AND (NEW.actual_source IS NULL OR NEW.actual_unit IS NULL OR NEW.recorded_revision IS NULL OR NEW.recorded_at IS NULL)) OR
 (NEW.row_status='logged' AND NEW.set_kind IN ('working','ramp') AND (NEW.reps IS NULL OR NEW.reps<=0 OR NEW.weight_kg IS NULL)) OR
 (NEW.row_status='logged' AND NEW.target_unit='seconds' AND (NEW.actual_hold_sec IS NULL OR NEW.actual_hold_sec<0)) OR
 (NEW.rir_is_lower_bound=1 AND (NEW.rir IS NULL OR NEW.rir!=4)) OR
 (NEW.target_rir_min IS NOT NULL AND NEW.target_rir_max IS NOT NULL AND NEW.target_rir_min>NEW.target_rir_max))
BEGIN SELECT RAISE(ABORT,'invalid v2 workout_sets'); END;
CREATE TRIGGER v2_validate_workout_sets_update BEFORE UPDATE ON workout_sets
WHEN (SELECT session_version FROM workouts WHERE id=NEW.session_id)=2 AND (
 NEW.actual_source IS 'legacy_unknown' OR NEW.policy_bundle_id IS NULL OR NEW.target_rest_ms IS NULL OR
 (NEW.lift_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM workout_lifts WHERE id=NEW.lift_id AND session_id=NEW.session_id)) OR
 (NEW.target_unit='reps' AND (NEW.target_reps IS NULL OR NEW.target_reps<=0 OR NEW.target_count IS NOT NULL)) OR
 (NEW.target_unit!='reps' AND (NEW.target_reps IS NOT NULL OR NEW.target_count IS NULL OR NEW.target_count<=0)) OR
 (NEW.actual_unit IS NOT NULL AND NEW.actual_unit!=NEW.target_unit) OR
 (NEW.row_status='logged' AND (NEW.actual_source IS NULL OR NEW.actual_unit IS NULL OR NEW.recorded_revision IS NULL OR NEW.recorded_at IS NULL)) OR
 (NEW.row_status='logged' AND NEW.set_kind IN ('working','ramp') AND (NEW.reps IS NULL OR NEW.reps<=0 OR NEW.weight_kg IS NULL)) OR
 (NEW.row_status='logged' AND NEW.target_unit='seconds' AND (NEW.actual_hold_sec IS NULL OR NEW.actual_hold_sec<0)) OR
 (NEW.rir_is_lower_bound=1 AND (NEW.rir IS NULL OR NEW.rir!=4)) OR
 (NEW.target_rir_min IS NOT NULL AND NEW.target_rir_max IS NOT NULL AND NEW.target_rir_min>NEW.target_rir_max))
BEGIN SELECT RAISE(ABORT,'invalid v2 workout_sets'); END;
DROP TRIGGER trg_workout_sets_reps_counted;
CREATE TRIGGER trg_workout_sets_reps_counted
BEFORE INSERT ON workout_sets
WHEN (SELECT session_version FROM workouts WHERE id=NEW.session_id)=1 AND NEW.reps IS NULL AND NEW.set_kind NOT IN ('prep','ramp')
BEGIN
  SELECT RAISE(ABORT, 'workout_sets refused: reps is required for a working/hold set -- only a prep or ramp row may be a done-tap (reps NULL, target_reps = the prescription)');
END;
DROP TRIGGER trg_workout_sets_reps_counted_upd;
CREATE TRIGGER trg_workout_sets_reps_counted_upd
BEFORE UPDATE OF reps, set_kind ON workout_sets
WHEN (SELECT session_version FROM workouts WHERE id=NEW.session_id)=1 AND NEW.reps IS NULL AND NEW.set_kind NOT IN ('prep','ramp')
BEGIN
  SELECT RAISE(ABORT, 'workout_sets refused: reps is required for a working/hold set -- only a prep or ramp row may be a done-tap (reps NULL, target_reps = the prescription)');
END;
CREATE TRIGGER v2_logged_targets_frozen BEFORE UPDATE ON workout_sets
WHEN (SELECT session_version FROM workouts WHERE id=OLD.session_id)=2 AND OLD.row_status IN ('logged','void') AND (
 NEW.target_reps IS NOT OLD.target_reps OR NEW.target_load_g IS NOT OLD.target_load_g OR
 NEW.target_unit IS NOT OLD.target_unit OR NEW.target_count IS NOT OLD.target_count OR
 NEW.target_rir_min IS NOT OLD.target_rir_min OR NEW.target_rir_max IS NOT OLD.target_rir_max OR
 NEW.target_rest_ms IS NOT OLD.target_rest_ms OR NEW.target_hold_sec IS NOT OLD.target_hold_sec OR
 NEW.prescribed_revision IS NOT OLD.prescribed_revision OR NEW.policy_bundle_id IS NOT OLD.policy_bundle_id OR
 NEW.equipment_snapshot_id IS NOT OLD.equipment_snapshot_id OR NEW.basis_event_id IS NOT OLD.basis_event_id)
BEGIN SELECT RAISE(ABORT,'logged prescription is immutable'); END;
-- Other authority checks (pending RIR stage, event-bound actual correction,
-- revision increments, no direct v2 writers) are the kit service's transaction
-- contract; SQLite alone cannot enforce a service ACL.
CREATE TRIGGER v2_session_commands_update BEFORE UPDATE ON session_commands
BEGIN SELECT RAISE(ABORT,'append-only session_commands'); END;
CREATE TRIGGER v2_session_commands_delete BEFORE DELETE ON session_commands
BEGIN SELECT RAISE(ABORT,'append-only session_commands'); END;
CREATE TRIGGER v2_session_delivery_journal_update BEFORE UPDATE ON session_delivery_journal
BEGIN SELECT RAISE(ABORT,'append-only session_delivery_journal'); END;
CREATE TRIGGER v2_session_delivery_journal_delete BEFORE DELETE ON session_delivery_journal
BEGIN SELECT RAISE(ABORT,'append-only session_delivery_journal'); END;
CREATE TRIGGER v2_session_equipment_snapshots_update BEFORE UPDATE ON session_equipment_snapshots
BEGIN SELECT RAISE(ABORT,'append-only session_equipment_snapshots'); END;
CREATE TRIGGER v2_session_equipment_snapshots_delete BEFORE DELETE ON session_equipment_snapshots
BEGIN SELECT RAISE(ABORT,'append-only session_equipment_snapshots'); END;
CREATE TRIGGER v2_session_evidence_update BEFORE UPDATE ON session_evidence
BEGIN SELECT RAISE(ABORT,'append-only session_evidence'); END;
CREATE TRIGGER v2_session_evidence_delete BEFORE DELETE ON session_evidence
BEGIN SELECT RAISE(ABORT,'append-only session_evidence'); END;
CREATE TRIGGER v2_training_policy_bundles_update BEFORE UPDATE ON training_policy_bundles
BEGIN SELECT RAISE(ABORT,'append-only training_policy_bundles'); END;
CREATE TRIGGER v2_training_policy_bundles_delete BEFORE DELETE ON training_policy_bundles
BEGIN SELECT RAISE(ABORT,'append-only training_policy_bundles'); END;
CREATE TRIGGER v2_training_policy_rules_update BEFORE UPDATE ON training_policy_rules
BEGIN SELECT RAISE(ABORT,'append-only training_policy_rules'); END;
CREATE TRIGGER v2_training_policy_rules_delete BEFORE DELETE ON training_policy_rules
BEGIN SELECT RAISE(ABORT,'append-only training_policy_rules'); END;
CREATE TRIGGER v2_session_trainer_approvals_intent_frozen BEFORE UPDATE ON session_trainer_approvals
WHEN NEW.id IS NOT OLD.id OR
 NEW.session_id IS NOT OLD.session_id OR
 NEW.base_revision IS NOT OLD.base_revision OR
 NEW.view_digest IS NOT OLD.view_digest OR
 NEW.decision_json IS NOT OLD.decision_json OR
 NEW.cue_ast IS NOT OLD.cue_ast OR
 NEW.dependency_digest IS NOT OLD.dependency_digest OR
 NEW.policy_bundle_id IS NOT OLD.policy_bundle_id OR
 NEW.catalog_version IS NOT OLD.catalog_version OR
 NEW.expires_epoch IS NOT OLD.expires_epoch
BEGIN SELECT RAISE(ABORT,'immutable session_trainer_approvals intent'); END;
CREATE TRIGGER v2_session_outbox_intent_frozen BEFORE UPDATE ON session_outbox
WHEN NEW.session_id IS NOT OLD.session_id OR
 NEW.revision IS NOT OLD.revision OR
 NEW.frame_kind IS NOT OLD.frame_kind OR
 NEW.session_epoch IS NOT OLD.session_epoch OR
 NEW.rest_epoch IS NOT OLD.rest_epoch OR
 NEW.view_digest IS NOT OLD.view_digest OR
 NEW.not_before_at IS NOT OLD.not_before_at
BEGIN SELECT RAISE(ABORT,'immutable session_outbox intent'); END;

CREATE TABLE session_live_scopes (
 scope TEXT PRIMARY KEY CHECK(json_valid(scope)), session_id INTEGER NOT NULL REFERENCES workouts(id) DEFERRABLE INITIALLY DEFERRED,
 publisher_id TEXT NOT NULL, member_id TEXT NOT NULL, slots TEXT NOT NULL CHECK(json_valid(slots)),
 action_slot TEXT NOT NULL, UNIQUE(session_id,scope)
);
CREATE TABLE session_live_selectors (
 binding_id TEXT PRIMARY KEY, scope TEXT NOT NULL REFERENCES session_live_scopes(scope),
 handle TEXT NOT NULL, selector TEXT NOT NULL, revision INTEGER NOT NULL,
 slot TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('prepared','active','retired')),
 request TEXT NOT NULL CHECK(json_valid(request)), result TEXT NOT NULL CHECK(json_valid(result)),
 session_id INTEGER NOT NULL REFERENCES workouts(id),
 UNIQUE(scope,handle,selector), UNIQUE(handle,selector)
);
CREATE TABLE session_live_commands (
 binding_id TEXT PRIMARY KEY, scope TEXT NOT NULL REFERENCES session_live_scopes(scope),
 envelope_id TEXT NOT NULL, envelope_digest TEXT NOT NULL, command_digest TEXT NOT NULL,
 command_json TEXT NOT NULL CHECK(json_valid(command_json)), principal TEXT NOT NULL CHECK(json_valid(principal)),
 resolve_request TEXT CHECK(resolve_request IS NULL OR json_valid(resolve_request)),
 resolve_result TEXT CHECK(resolve_result IS NULL OR json_valid(resolve_result)),
 invocation_started INTEGER NOT NULL DEFAULT 0 CHECK(invocation_started IN (0,1)),
 state TEXT NOT NULL CHECK(state IN ('bound','committed','rejected')),
 acknowledgement TEXT, receipt TEXT, delivery TEXT,
 session_id INTEGER NOT NULL REFERENCES workouts(id),
 invocation_generation INTEGER NOT NULL DEFAULT 0,
 UNIQUE(scope,envelope_id), UNIQUE(scope,command_digest)
);
CREATE TABLE session_live_deliveries (
 outbox_id TEXT PRIMARY KEY, delivery_key TEXT NOT NULL UNIQUE,
 binding_id TEXT NOT NULL UNIQUE REFERENCES session_live_commands(binding_id),
 scope TEXT NOT NULL REFERENCES session_live_scopes(scope),
 typed_outbox TEXT NOT NULL UNIQUE CHECK(json_valid(typed_outbox)),
 receipt_digest TEXT NOT NULL, revision INTEGER NOT NULL,
 plan_digest TEXT, plan TEXT, frame_digest TEXT,
 holder_id TEXT, lease_fence INTEGER NOT NULL DEFAULT 0, lease_until_ms INTEGER NOT NULL DEFAULT 0,
 lease_history TEXT NOT NULL DEFAULT '[]' CHECK(json_valid(lease_history)),
 session_id INTEGER NOT NULL REFERENCES workouts(id),
 complete INTEGER NOT NULL DEFAULT 0 CHECK(complete IN (0,1)), completion TEXT
);
CREATE TABLE "session_live_stages" (
 stage_key TEXT PRIMARY KEY CHECK(json_valid(stage_key)),
 outbox_id TEXT NOT NULL REFERENCES session_live_deliveries(outbox_id), ordinal INTEGER NOT NULL,
 binding TEXT NOT NULL CHECK(json_valid(binding)), spec TEXT NOT NULL CHECK(json_valid(spec)),
 state TEXT NOT NULL CHECK(state IN ('pending','inflight','uncertain','acknowledged','satisfied_absent','satisfied_retained','satisfied_not_selected','waiting_retry')),
 next_attempt_at_ms INTEGER, seq INTEGER NOT NULL DEFAULT 0, attempt_fence INTEGER, receipt TEXT,
 session_id INTEGER NOT NULL REFERENCES workouts(id),
 rebound INTEGER NOT NULL DEFAULT 0 CHECK(rebound IN (0,1)), UNIQUE(outbox_id,ordinal)
);
CREATE TABLE session_live_events (
 event_id TEXT PRIMARY KEY, outbox_id TEXT NOT NULL REFERENCES session_live_deliveries(outbox_id),
 stage_key TEXT NOT NULL REFERENCES session_live_stages(stage_key),
 request TEXT NOT NULL CHECK(json_valid(request)), result TEXT NOT NULL CHECK(json_valid(result)),
 session_id INTEGER NOT NULL REFERENCES workouts(id),
 entry_id TEXT NOT NULL UNIQUE, attempt_fence INTEGER, acknowledgement_fence INTEGER NOT NULL
);
CREATE TABLE session_live_slots (
 scope TEXT NOT NULL REFERENCES session_live_scopes(scope), slot TEXT NOT NULL,
 message_id INTEGER NOT NULL, revision INTEGER NOT NULL, epoch INTEGER NOT NULL,
 protection TEXT NOT NULL CHECK(json_valid(protection)), markup_active INTEGER NOT NULL CHECK(markup_active IN (0,1)),
 session_id INTEGER NOT NULL REFERENCES workouts(id),
 PRIMARY KEY(scope,slot)
);
CREATE TABLE session_live_history (
 history_id TEXT PRIMARY KEY, scope TEXT NOT NULL REFERENCES session_live_scopes(scope),
 slot TEXT NOT NULL, message_id INTEGER NOT NULL, protection TEXT NOT NULL CHECK(json_valid(protection)),
 retirement_receipt TEXT, replacement_receipt TEXT, cleanup TEXT NOT NULL,
 session_id INTEGER NOT NULL REFERENCES workouts(id),
 deleted INTEGER NOT NULL DEFAULT 0 CHECK(deleted IN (0,1))
);

CREATE TABLE session_live_selections (
 outbox_id TEXT NOT NULL REFERENCES session_live_deliveries(outbox_id), group_id TEXT NOT NULL,
 selection_digest TEXT NOT NULL UNIQUE, selection TEXT NOT NULL CHECK(json_valid(selection)),
 session_id INTEGER NOT NULL REFERENCES workouts(id),
 PRIMARY KEY(outbox_id,group_id)
);
CREATE TABLE session_live_evidence (
 digest TEXT PRIMARY KEY, kind TEXT NOT NULL, value TEXT NOT NULL CHECK(json_valid(value)),
 outbox_id TEXT REFERENCES session_live_deliveries(outbox_id), binding_id TEXT REFERENCES session_live_commands(binding_id),
 session_id INTEGER NOT NULL REFERENCES workouts(id)
);
CREATE TRIGGER session_live_selections_immutable BEFORE UPDATE ON session_live_selections
BEGIN SELECT RAISE(ABORT,'immutable branch selection'); END;
CREATE TRIGGER session_live_selections_retained BEFORE DELETE ON session_live_selections
BEGIN SELECT RAISE(ABORT,'retained branch selection'); END;
CREATE TRIGGER session_live_evidence_immutable BEFORE UPDATE ON session_live_evidence
BEGIN SELECT RAISE(ABORT,'immutable delivery evidence'); END;
CREATE TRIGGER session_live_evidence_retained BEFORE DELETE ON session_live_evidence
BEGIN SELECT RAISE(ABORT,'retained delivery evidence'); END;

-- K1 shared-DB authorities (083).
CREATE TABLE session_pack_fences (
 pack TEXT PRIMARY KEY, token INTEGER NOT NULL CHECK(token>0),
 engine TEXT NOT NULL, generation INTEGER NOT NULL CHECK(generation>0),
 checkpoint TEXT NOT NULL CHECK(json_valid(checkpoint)),
 fence TEXT NOT NULL CHECK(json_valid(fence)),
 active_engine TEXT, active_generation INTEGER, closure TEXT CHECK(json_valid(closure))
);
CREATE TABLE session_pack_checkpoints (
 pack TEXT NOT NULL, token INTEGER NOT NULL CHECK(token>0),
 checkpoint TEXT NOT NULL CHECK(json_valid(checkpoint)),
 fence TEXT NOT NULL CHECK(json_valid(fence)), closure TEXT CHECK(json_valid(closure)),
 PRIMARY KEY(pack,token), UNIQUE(pack,checkpoint)
);
CREATE TRIGGER session_checkpoint_immutable BEFORE UPDATE ON session_pack_checkpoints
WHEN NEW.pack<>OLD.pack OR NEW.token<>OLD.token OR NEW.checkpoint<>OLD.checkpoint OR
 NEW.fence<>OLD.fence OR OLD.closure IS NOT NULL
BEGIN SELECT RAISE(ABORT,'immutable completed checkpoint'); END;
CREATE TRIGGER session_checkpoint_retained BEFORE DELETE ON session_pack_checkpoints
BEGIN SELECT RAISE(ABORT,'retained checkpoint'); END;
CREATE TABLE session_invocation_admissions (
 binding_id TEXT NOT NULL REFERENCES session_live_commands(binding_id),
 invocation_generation INTEGER NOT NULL CHECK(invocation_generation>0),
 attempt TEXT NOT NULL CHECK(json_valid(attempt)), command_digest TEXT NOT NULL,
 principal TEXT NOT NULL CHECK(json_valid(principal)),
 PRIMARY KEY(binding_id,invocation_generation)
);
CREATE TRIGGER session_admission_immutable BEFORE UPDATE ON session_invocation_admissions
BEGIN SELECT RAISE(ABORT,'immutable invocation admission'); END;
CREATE TRIGGER session_admission_retained BEFORE DELETE ON session_invocation_admissions
BEGIN SELECT RAISE(ABORT,'retained invocation admission'); END;
CREATE TABLE session_invocation_revocations (
 binding_id TEXT NOT NULL REFERENCES session_live_commands(binding_id),
 invocation_generation INTEGER NOT NULL CHECK(invocation_generation>0),
 command_digest TEXT NOT NULL, principal TEXT NOT NULL CHECK(json_valid(principal)),
 record TEXT NOT NULL CHECK(json_valid(record)),
 PRIMARY KEY(binding_id,invocation_generation)
);
CREATE TABLE session_transition_acks (
 pack TEXT NOT NULL, token INTEGER NOT NULL CHECK(token>0),
 target TEXT NOT NULL CHECK(json_valid(target)), ack_digest TEXT NOT NULL UNIQUE,
 acknowledgement TEXT NOT NULL CHECK(json_valid(acknowledgement)),
 PRIMARY KEY(pack,token,target)
);
CREATE TRIGGER session_revocation_immutable BEFORE UPDATE ON session_invocation_revocations
BEGIN SELECT RAISE(ABORT,'immutable invocation revocation'); END;
CREATE TRIGGER session_revocation_retained BEFORE DELETE ON session_invocation_revocations
BEGIN SELECT RAISE(ABORT,'retained invocation revocation'); END;
CREATE TRIGGER session_transition_ack_immutable BEFORE UPDATE ON session_transition_acks
BEGIN SELECT RAISE(ABORT,'immutable transition acknowledgement'); END;
CREATE TRIGGER session_transition_ack_retained BEFORE DELETE ON session_transition_acks
BEGIN SELECT RAISE(ABORT,'retained transition acknowledgement'); END;
CREATE TRIGGER session_pack_fence_monotonic BEFORE UPDATE ON session_pack_fences
WHEN NEW.token<OLD.token OR NEW.token>OLD.token+1 OR
 (NEW.token=OLD.token AND (NEW.fence<>OLD.fence OR NEW.checkpoint<>OLD.checkpoint))
BEGIN SELECT RAISE(ABORT,'nonmonotonic pack fence'); END;
CREATE TRIGGER session_pack_fence_retained BEFORE DELETE ON session_pack_fences
BEGIN SELECT RAISE(ABORT,'retained pack fence'); END;

-- Typed lossless program evidence (084); execution admission remains separate.
CREATE TRIGGER session_program_evidence_shape BEFORE INSERT ON session_evidence
WHEN NEW.kind IN ('program_plan/1','prescription_basis/2')
BEGIN
 SELECT CASE WHEN json_valid(NEW.value)<>1 THEN RAISE(ABORT,'invalid typed program evidence JSON') END;
 SELECT CASE WHEN json_extract(NEW.value,'$.schema') IS NOT NEW.kind
  THEN RAISE(ABORT,'typed program evidence discriminator mismatch') END;
 SELECT CASE WHEN NEW.kind='program_plan/1' AND (
  NEW.scope<>'session' OR
  json_type(NEW.value,'$.configuration_digest') IS NOT 'text' OR
  json_type(NEW.value,'$.program_digest') IS NOT 'text' OR
  json_type(NEW.value,'$.policy_digest') IS NOT 'text' OR
  json_type(NEW.value,'$.slots') IS NOT 'array' OR
  json_type(NEW.value,'$.blocks') IS NOT 'array' OR
  json_type(NEW.value,'$.calculators') IS NOT 'object')
  THEN RAISE(ABORT,'incomplete program plan evidence') END;
 SELECT CASE WHEN NEW.kind='prescription_basis/2' AND (
  NEW.scope<>'lift' OR
  json_type(NEW.value,'$.slot_id') IS NOT 'text' OR
  json_type(NEW.value,'$.calculator_ref') IS NOT 'text' OR
  json_type(NEW.value,'$.request_digest') IS NOT 'text' OR
  json_type(NEW.value,'$.response_digest') IS NOT 'text' OR
  json_type(NEW.value,'$.dependencies_digest') IS NOT 'text' OR
  json_type(NEW.value,'$.basis') IS NOT 'text' OR
  json_type(NEW.value,'$.evidence_digest') IS NOT 'text')
  THEN RAISE(ABORT,'incomplete prescription basis evidence') END;
END;
CREATE TRIGGER session_program_evidence_immutable BEFORE UPDATE ON session_evidence
WHEN OLD.kind IN ('program_plan/1','prescription_basis/2') OR
 NEW.kind IN ('program_plan/1','prescription_basis/2')
BEGIN SELECT RAISE(ABORT,'immutable typed program evidence'); END;
CREATE TRIGGER session_program_evidence_retained BEFORE DELETE ON session_evidence
WHEN OLD.kind IN ('program_plan/1','prescription_basis/2')
BEGIN SELECT RAISE(ABORT,'retained typed program evidence'); END;

-- 085: same trigger definitions as the migration.
DROP TRIGGER v2_validate_workout_sets_insert;
DROP TRIGGER v2_validate_workout_sets_update;
CREATE TRIGGER v2_validate_workout_sets_insert BEFORE INSERT ON workout_sets
WHEN (SELECT session_version FROM workouts WHERE id=NEW.session_id)=2 AND (
 NEW.actual_source IS 'legacy_unknown' OR NEW.policy_bundle_id IS NULL OR NEW.target_rest_ms IS NULL OR
 (NEW.lift_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM workout_lifts WHERE id=NEW.lift_id AND session_id=NEW.session_id)) OR
 (NEW.target_unit='reps' AND (NEW.target_reps IS NULL OR NEW.target_reps<=0 OR NEW.target_count IS NOT NULL)) OR
 (NEW.target_unit!='reps' AND (NEW.target_reps IS NOT NULL OR NEW.target_count IS NULL OR NEW.target_count<=0)) OR
 (NEW.actual_unit IS NOT NULL AND NEW.actual_unit!=NEW.target_unit) OR
 (NEW.row_status='logged' AND (NEW.actual_source IS NULL OR NEW.actual_unit IS NULL OR NEW.recorded_revision IS NULL OR NEW.recorded_at IS NULL)) OR
 (NEW.row_status='logged' AND NEW.set_kind IN ('working','ramp') AND (NEW.reps IS NULL OR NEW.reps<=0 OR (NEW.weight_kg IS NULL AND NOT EXISTS(
 SELECT 1 FROM session_evidence e, json_each(CASE e.kind WHEN 'program_plan/1'
 THEN json_extract(e.value,'$.rows') ELSE json_extract(e.value,'$.applied_rows') END) b
 WHERE e.session_id=NEW.session_id AND e.kind IN ('program_plan/1','prescription_basis/2')
 AND json_extract(b.value,'$.set_id')=NEW.id
 AND json_extract(b.value,'$.row.load_state')='unloaded'
 AND json_type(b.value,'$.row.load_g')='null' AND NEW.target_load_g IS NULL)))) OR
 (NEW.row_status='logged' AND NEW.target_unit='seconds' AND (NEW.actual_hold_sec IS NULL OR NEW.actual_hold_sec<0)) OR
 (NEW.rir_is_lower_bound=1 AND (NEW.rir IS NULL OR NEW.rir!=4)) OR
 (NEW.target_rir_min IS NOT NULL AND NEW.target_rir_max IS NOT NULL AND NEW.target_rir_min>NEW.target_rir_max))
BEGIN SELECT RAISE(ABORT,'invalid v2 workout_sets'); END;
CREATE TRIGGER v2_validate_workout_sets_update BEFORE UPDATE ON workout_sets
WHEN (SELECT session_version FROM workouts WHERE id=NEW.session_id)=2 AND (
 NEW.actual_source IS 'legacy_unknown' OR NEW.policy_bundle_id IS NULL OR NEW.target_rest_ms IS NULL OR
 (NEW.lift_id IS NOT NULL AND NOT EXISTS(SELECT 1 FROM workout_lifts WHERE id=NEW.lift_id AND session_id=NEW.session_id)) OR
 (NEW.target_unit='reps' AND (NEW.target_reps IS NULL OR NEW.target_reps<=0 OR NEW.target_count IS NOT NULL)) OR
 (NEW.target_unit!='reps' AND (NEW.target_reps IS NOT NULL OR NEW.target_count IS NULL OR NEW.target_count<=0)) OR
 (NEW.actual_unit IS NOT NULL AND NEW.actual_unit!=NEW.target_unit) OR
 (NEW.row_status='logged' AND (NEW.actual_source IS NULL OR NEW.actual_unit IS NULL OR NEW.recorded_revision IS NULL OR NEW.recorded_at IS NULL)) OR
 (NEW.row_status='logged' AND NEW.set_kind IN ('working','ramp') AND (NEW.reps IS NULL OR NEW.reps<=0 OR (NEW.weight_kg IS NULL AND NOT EXISTS(
 SELECT 1 FROM session_evidence e, json_each(CASE e.kind WHEN 'program_plan/1'
 THEN json_extract(e.value,'$.rows') ELSE json_extract(e.value,'$.applied_rows') END) b
 WHERE e.session_id=NEW.session_id AND e.kind IN ('program_plan/1','prescription_basis/2')
 AND json_extract(b.value,'$.set_id')=NEW.id
 AND json_extract(b.value,'$.row.load_state')='unloaded'
 AND json_type(b.value,'$.row.load_g')='null' AND NEW.target_load_g IS NULL)))) OR
 (NEW.row_status='logged' AND NEW.target_unit='seconds' AND (NEW.actual_hold_sec IS NULL OR NEW.actual_hold_sec<0)) OR
 (NEW.rir_is_lower_bound=1 AND (NEW.rir IS NULL OR NEW.rir!=4)) OR
 (NEW.target_rir_min IS NOT NULL AND NEW.target_rir_max IS NOT NULL AND NEW.target_rir_min>NEW.target_rir_max))
BEGIN SELECT RAISE(ABORT,'invalid v2 workout_sets'); END;
-- 086: owner-picked appointment settlement, additive / no personal seeds.
ALTER TABLE persons ADD COLUMN ledger_no_split INTEGER NOT NULL DEFAULT 0 CHECK (ledger_no_split IN (0,1));
CREATE TABLE ledger_split (
 spend_id INTEGER PRIMARY KEY REFERENCES spend_entry(id),
 appointment_id INTEGER NOT NULL REFERENCES block(id),
 state TEXT NOT NULL DEFAULT 'ask' CHECK(state IN ('ask','how','remainder','amounts','declined','skip','split')),
 owner_share INTEGER CHECK(owner_share >= 0),
 amounts_json TEXT CHECK(amounts_json IS NULL OR json_valid(amounts_json))
);
CREATE TABLE ledger_share (
 id INTEGER PRIMARY KEY,
 spend_id INTEGER NOT NULL REFERENCES ledger_split(spend_id),
 person_id INTEGER NOT NULL REFERENCES persons(id),
 amount INTEGER NOT NULL CHECK(amount >= 0),
 received INTEGER NOT NULL DEFAULT 0 CHECK(received >= 0 AND received <= amount),
 closed INTEGER NOT NULL DEFAULT 0 CHECK(closed IN (0,1)),
 UNIQUE(spend_id,person_id),
 FOREIGN KEY(spend_id) REFERENCES spend_entry(id)
);
CREATE TABLE ledger_settlement_match (
 transfer_id INTEGER PRIMARY KEY REFERENCES spend_entry(id),
 share_id INTEGER REFERENCES ledger_share(id),
 state TEXT NOT NULL CHECK(state IN ('ask','matched','confirmed','unrelated')),
 credited INTEGER NOT NULL DEFAULT 0 CHECK(credited >= 0),
 previous_closed INTEGER NOT NULL DEFAULT 0 CHECK(previous_closed IN (0,1)),
 shown INTEGER NOT NULL DEFAULT 0 CHECK(shown IN (0,1))
);
CREATE TABLE ledger_capture_notice (
 spend_id INTEGER PRIMARY KEY REFERENCES spend_entry(id),
 immediate_shown INTEGER NOT NULL DEFAULT 0 CHECK(immediate_shown IN (0,1))
);
CREATE VIEW v_ledger_share AS
 SELECT h.*, p.name AS person, p.display_name, s.date, b.title AS appointment,
 CASE WHEN h.closed=1 THEN 0 ELSE h.amount-h.received END AS remaining
 FROM ledger_share h JOIN persons p ON p.id=h.person_id
 JOIN spend_entry s ON s.id=h.spend_id JOIN ledger_split l ON l.spend_id=s.id
 JOIN block b ON b.id=l.appointment_id;
CREATE VIEW v_ledger_reimbursement AS
 SELECT spend_id, SUM(received) AS amount FROM ledger_share GROUP BY spend_id;
CREATE TRIGGER trg_ledger_split_spend_gone AFTER DELETE ON spend_entry BEGIN
 UPDATE ledger_share SET received=received-COALESCE((SELECT credited FROM ledger_settlement_match WHERE transfer_id=OLD.id),0),
 closed=0 WHERE id=(SELECT share_id FROM ledger_settlement_match WHERE transfer_id=OLD.id AND credited>0);
 DELETE FROM ledger_settlement_match WHERE transfer_id=OLD.id OR share_id IN (SELECT id FROM ledger_share WHERE spend_id=OLD.id);
 DELETE FROM ledger_share WHERE spend_id=OLD.id;
 DELETE FROM ledger_split WHERE spend_id=OLD.id;
 DELETE FROM ledger_capture_notice WHERE spend_id=OLD.id;
END;

-- 089: user-taught person presentation; NULL uses the shared default.
ALTER TABLE persons ADD COLUMN emoji TEXT;

-- 090: payment reconciliation and pantry quantities.
CREATE TABLE IF NOT EXISTS shop_capture (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES shop_item(id),
    purpose TEXT NOT NULL CHECK (TRIM(purpose) <> ''),
    event_id INTEGER REFERENCES event(id) ON DELETE SET NULL,
    hunt_id INTEGER REFERENCES shop_hunt(id) ON DELETE SET NULL,
    selected_on TEXT NOT NULL,
    size_label TEXT,
    offer_price INTEGER CHECK (offer_price IS NULL OR offer_price >= 0),
    store TEXT,
    link TEXT,
    status TEXT NOT NULL CHECK (status IN ('picked','in_cart','ordered','bought')),
    owned_id INTEGER REFERENCES owned_item(id) ON DELETE SET NULL,
    note TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE shop_capture_quantity (
 capture_id INTEGER PRIMARY KEY REFERENCES shop_capture(id) ON DELETE CASCADE,
 quantity REAL CHECK(quantity IS NULL OR quantity >= 0),
 unit TEXT NOT NULL DEFAULT 'pack',
 unit_size REAL CHECK(unit_size IS NULL OR unit_size > 0),
 size_unit TEXT
);
CREATE TABLE owned_stock (
 owned_id INTEGER PRIMARY KEY REFERENCES owned_item(id) ON DELETE CASCADE,
 quantity REAL CHECK(quantity IS NULL OR quantity >= 0),
 unit TEXT NOT NULL DEFAULT 'pack',
 unit_size REAL CHECK(unit_size IS NULL OR unit_size > 0),
 size_unit TEXT
);
CREATE TABLE stock_use (
 usage_key TEXT PRIMARY KEY,
 item_id INTEGER NOT NULL REFERENCES shop_item(id),
 quantity REAL,
 unit TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('done','unknown','short')),
 detail TEXT NOT NULL,
 asked INTEGER NOT NULL DEFAULT 0 CHECK(asked IN (0,1))
);
CREATE TABLE shop_payment (
 spend_id INTEGER PRIMARY KEY REFERENCES spend_entry(id) ON DELETE CASCADE,
 state TEXT NOT NULL CHECK(state IN ('matched','ask','ignored')),
 candidates TEXT NOT NULL,
 asked INTEGER NOT NULL DEFAULT 0 CHECK(asked IN (0,1))
);
CREATE TABLE shop_capture_payment (
 capture_id INTEGER PRIMARY KEY REFERENCES shop_capture(id) ON DELETE CASCADE,
 spend_id INTEGER NOT NULL REFERENCES spend_entry(id) ON DELETE CASCADE,
 owned_id INTEGER NOT NULL UNIQUE REFERENCES owned_item(id) ON DELETE CASCADE,
 amount INTEGER NOT NULL CHECK(amount >= 0)
);
CREATE INDEX ix_shop_capture_payment_spend ON shop_capture_payment(spend_id);
CREATE TRIGGER trg_stock_owned_status AFTER UPDATE OF status ON owned_item
 WHEN NEW.status IN ('used_up','returned','gone') BEGIN
 UPDATE owned_stock SET quantity=0 WHERE owned_id=NEW.id;
END;
CREATE TRIGGER trg_stock_owned_delete BEFORE DELETE ON owned_item BEGIN
 DELETE FROM owned_stock WHERE owned_id=OLD.id;
 DELETE FROM shop_capture_payment WHERE owned_id=OLD.id;
END;
CREATE TRIGGER trg_shop_payment_spend_delete BEFORE DELETE ON spend_entry BEGIN
 UPDATE owned_item SET price=(SELECT amount FROM shop_capture_payment WHERE owned_id=owned_item.id),spend_id=NULL
 WHERE id IN (SELECT owned_id FROM shop_capture_payment WHERE spend_id=OLD.id);
 DELETE FROM shop_capture_payment WHERE spend_id=OLD.id;
 DELETE FROM shop_payment WHERE spend_id=OLD.id;
END;
-- 091: made dishes, fractional meals and leftovers.
CREATE TABLE made_dish (
 id INTEGER PRIMARY KEY,
 owning_agent TEXT NOT NULL,
 session_id TEXT NOT NULL,
 recipe_id TEXT NOT NULL,
 name TEXT NOT NULL,
 aliases TEXT NOT NULL DEFAULT '[]',
 completed_at REAL NOT NULL,
 ingredients TEXT NOT NULL,
 nutrition TEXT NOT NULL,
 missing TEXT NOT NULL DEFAULT '[]',
 estimated INTEGER NOT NULL CHECK(estimated IN (0,1)),
 yield_quantity REAL NOT NULL CHECK(yield_quantity > 0),
 yield_unit TEXT NOT NULL,
 remaining REAL NOT NULL DEFAULT 1 CHECK(remaining >= 0 AND remaining <= 1),
 UNIQUE(owning_agent,session_id,recipe_id)
);
CREATE INDEX ix_made_dish_recall ON made_dish(owning_agent,completed_at);
CREATE TABLE made_dish_meal (
 block_id INTEGER PRIMARY KEY REFERENCES block(id) ON DELETE CASCADE,
 dish_id INTEGER NOT NULL REFERENCES made_dish(id),
 fraction REAL NOT NULL CHECK(fraction > 0 AND fraction <= 1),
 request_key TEXT NOT NULL,
 UNIQUE(dish_id,request_key)
);
CREATE TRIGGER trg_made_dish_meal_restore AFTER DELETE ON made_dish_meal BEGIN
 UPDATE made_dish SET remaining=MIN(1,remaining+OLD.fraction) WHERE id=OLD.dish_id;
END;
-- 092: atomic placement union; NULL/absent keeps all legacy placement logic.
CREATE TABLE IF NOT EXISTS routine_placement (
    routine_id INTEGER PRIMARY KEY REFERENCES routine_def(id) ON DELETE CASCADE,
    spec TEXT NOT NULL CHECK (json_valid(spec))
        CHECK (json_type(spec) = 'object')
        CHECK (COALESCE(json_type(spec, '$.alternatives') = 'array', 0))
        CHECK (json_array_length(spec, '$.alternatives') > 0)
);
CREATE TRIGGER IF NOT EXISTS trg_routine_placement_insert
BEFORE INSERT ON routine_placement BEGIN
    SELECT RAISE(ABORT, 'alternatives require a timed anywhere task without legacy ride fields')
    WHERE NOT EXISTS (SELECT 1 FROM routine_def WHERE id=NEW.routine_id
        AND kind='task' AND schedule_kind='timed' AND place_id IS NULL
        AND placement IS NULL AND fallback_at IS NULL
        AND ride_not_before IS NULL AND ride_prefer IS NULL);
END;
CREATE TRIGGER IF NOT EXISTS trg_routine_placement_update
BEFORE UPDATE ON routine_placement BEGIN
    SELECT RAISE(ABORT, 'alternatives require a timed anywhere task without legacy ride fields')
    WHERE NOT EXISTS (SELECT 1 FROM routine_def WHERE id=NEW.routine_id
        AND kind='task' AND schedule_kind='timed' AND place_id IS NULL
        AND placement IS NULL AND fallback_at IS NULL
        AND ride_not_before IS NULL AND ride_prefer IS NULL);
END;
CREATE TRIGGER IF NOT EXISTS trg_routine_placement_parent
BEFORE UPDATE ON routine_def WHEN EXISTS
    (SELECT 1 FROM routine_placement WHERE routine_id=OLD.id) BEGIN
    SELECT RAISE(ABORT, 'clear alternatives before changing placement kind')
    WHERE NEW.kind!='task' OR NEW.schedule_kind!='timed'
        OR NEW.place_id IS NOT NULL OR NEW.placement IS NOT NULL
        OR NEW.fallback_at IS NOT NULL OR NEW.ride_not_before IS NOT NULL
        OR NEW.ride_prefer IS NOT NULL;
END;
CREATE TRIGGER IF NOT EXISTS trg_routine_placement_delete
AFTER DELETE ON routine_def BEGIN
    DELETE FROM routine_placement WHERE routine_id=OLD.id;
END;

-- Receipt allocations are economic shares. For an owner payment, amount is
-- the friend's share; for a friend payment, amount is the owner's share.
-- The one cash transfer equals SUM(owner-paid shares)-SUM(friend-paid shares).
CREATE TABLE ledger_receipt_allocation (
 receipt_id INTEGER NOT NULL REFERENCES spend_entry(id),
 expense_id INTEGER NOT NULL REFERENCES spend_entry(id),
 amount INTEGER NOT NULL CHECK(typeof(amount)='integer' AND amount>0),
 payer TEXT NOT NULL DEFAULT 'owner' CHECK(payer IN ('owner','friend')),
 share_id INTEGER REFERENCES ledger_share(id),
 previous_closed INTEGER NOT NULL DEFAULT 0 CHECK(previous_closed IN (0,1)),
 PRIMARY KEY(receipt_id,expense_id),
 CHECK(receipt_id<>expense_id),
 CHECK(payer='owner' OR share_id IS NULL)
);
CREATE INDEX idx_ledger_receipt_expense ON ledger_receipt_allocation(expense_id);
CREATE INDEX idx_ledger_receipt_share ON ledger_receipt_allocation(share_id);
CREATE TRIGGER trg_receipt_allocation_insert BEFORE INSERT ON ledger_receipt_allocation BEGIN
 SELECT RAISE(ABORT,'allocation receipt missing') WHERE NOT EXISTS
  (SELECT 1 FROM spend_entry WHERE id=NEW.receipt_id AND amount>0 AND scope<>'business'
   AND kind='transfer' AND class='own_transfer');
 SELECT RAISE(ABORT,'allocation expense invalid') WHERE NOT EXISTS
  (SELECT 1 FROM spend_entry WHERE id=NEW.expense_id AND amount>=NEW.amount
   AND kind='expense' AND COALESCE(class,'spend')='spend' AND scope<>'business');
 SELECT RAISE(ABORT,'allocation share invalid') WHERE NEW.share_id IS NOT NULL AND NOT EXISTS
  (SELECT 1 FROM ledger_share WHERE id=NEW.share_id AND spend_id=NEW.expense_id);
END;
CREATE TRIGGER trg_receipt_allocation_update BEFORE UPDATE ON ledger_receipt_allocation BEGIN
 SELECT RAISE(ABORT,'replace receipt allocations atomically');
END;
CREATE TRIGGER trg_receipt_allocation_release AFTER DELETE ON ledger_receipt_allocation BEGIN
 UPDATE ledger_share SET received=received-OLD.amount,closed=OLD.previous_closed WHERE id=OLD.share_id;
END;
CREATE TRIGGER trg_receipt_evidence_delete BEFORE DELETE ON spend_entry WHEN 1 BEGIN
 UPDATE ledger_preference SET detail=json_set(detail,'$.allocation_revision',
  COALESCE(json_extract(detail,'$.allocation_revision'),0)+1,'$.allocation_status','released',
  '$.unallocated',COALESCE((SELECT amount FROM spend_entry WHERE id=CAST(item AS INTEGER)),0))
 WHERE topic='ledger_review_item' AND CAST(item AS INTEGER) IN
  (SELECT receipt_id FROM ledger_receipt_allocation WHERE receipt_id=OLD.id OR expense_id=OLD.id);
 DELETE FROM ledger_receipt_allocation WHERE receipt_id IN
  (SELECT receipt_id FROM ledger_receipt_allocation WHERE receipt_id=OLD.id OR expense_id=OLD.id);
END;
CREATE TRIGGER trg_receipt_evidence_edit BEFORE UPDATE OF amount,date,name,method,kind,class,scope,pair_id ON spend_entry WHEN OLD.amount IS NOT NEW.amount OR OLD.date IS NOT NEW.date OR OLD.name IS NOT NEW.name OR OLD.method IS NOT NEW.method OR OLD.kind IS NOT NEW.kind OR OLD.class IS NOT NEW.class OR OLD.scope IS NOT NEW.scope OR OLD.pair_id IS NOT NEW.pair_id BEGIN
 UPDATE ledger_preference SET detail=json_set(detail,'$.allocation_revision',
  COALESCE(json_extract(detail,'$.allocation_revision'),0)+1,'$.allocation_status','released',
  '$.unallocated',COALESCE((SELECT amount FROM spend_entry WHERE id=CAST(item AS INTEGER)),0))
 WHERE topic='ledger_review_item' AND CAST(item AS INTEGER) IN
  (SELECT receipt_id FROM ledger_receipt_allocation WHERE receipt_id=OLD.id OR expense_id=OLD.id);
 DELETE FROM ledger_receipt_allocation WHERE receipt_id IN
  (SELECT receipt_id FROM ledger_receipt_allocation WHERE receipt_id=OLD.id OR expense_id=OLD.id);
END;
CREATE TRIGGER trg_receipt_refund_insert AFTER INSERT ON spend_entry
 WHEN NEW.amount<0 AND NEW.pair_id IS NOT NULL BEGIN
 UPDATE ledger_preference SET detail=json_set(detail,'$.allocation_revision',
  COALESCE(json_extract(detail,'$.allocation_revision'),0)+1,'$.allocation_status','released',
  '$.unallocated',COALESCE((SELECT amount FROM spend_entry WHERE id=CAST(item AS INTEGER)),0))
 WHERE topic='ledger_review_item' AND CAST(item AS INTEGER) IN
  (SELECT a.receipt_id FROM ledger_receipt_allocation a JOIN spend_entry e ON e.id=a.expense_id
   WHERE e.pair_id=NEW.pair_id OR a.receipt_id IN (SELECT id FROM spend_entry WHERE pair_id=NEW.pair_id));
 DELETE FROM ledger_receipt_allocation WHERE receipt_id IN
  (SELECT a.receipt_id FROM ledger_receipt_allocation a JOIN spend_entry e ON e.id=a.expense_id
   WHERE e.pair_id=NEW.pair_id OR a.receipt_id IN (SELECT id FROM spend_entry WHERE pair_id=NEW.pair_id));
END;
CREATE TRIGGER trg_receipt_refund_edit AFTER UPDATE OF amount,pair_id ON spend_entry
 WHEN NEW.amount<0 AND NEW.pair_id IS NOT NULL BEGIN
 UPDATE ledger_preference SET detail=json_set(detail,'$.allocation_revision',
  COALESCE(json_extract(detail,'$.allocation_revision'),0)+1,'$.allocation_status','released',
  '$.unallocated',COALESCE((SELECT amount FROM spend_entry WHERE id=CAST(item AS INTEGER)),0))
 WHERE topic='ledger_review_item' AND CAST(item AS INTEGER) IN
  (SELECT a.receipt_id FROM ledger_receipt_allocation a JOIN spend_entry e ON e.id=a.expense_id
   WHERE e.pair_id=NEW.pair_id OR a.receipt_id IN (SELECT id FROM spend_entry WHERE pair_id=NEW.pair_id));
 DELETE FROM ledger_receipt_allocation WHERE receipt_id IN
  (SELECT a.receipt_id FROM ledger_receipt_allocation a JOIN spend_entry e ON e.id=a.expense_id
   WHERE e.pair_id=NEW.pair_id OR a.receipt_id IN (SELECT id FROM spend_entry WHERE pair_id=NEW.pair_id));
END;
CREATE TRIGGER trg_receipt_share_delete BEFORE DELETE ON ledger_share BEGIN
 UPDATE ledger_preference SET detail=json_set(detail,'$.allocation_revision',
  COALESCE(json_extract(detail,'$.allocation_revision'),0)+1,'$.allocation_status','released')
 WHERE topic='ledger_review_item' AND CAST(item AS INTEGER) IN
  (SELECT receipt_id FROM ledger_receipt_allocation WHERE share_id=OLD.id);
 DELETE FROM ledger_receipt_allocation WHERE receipt_id IN
  (SELECT receipt_id FROM ledger_receipt_allocation WHERE share_id=OLD.id);
END;
CREATE TRIGGER trg_receipt_preference_delete BEFORE DELETE ON ledger_preference
 WHEN OLD.topic='ledger_review_item' BEGIN
 DELETE FROM ledger_receipt_allocation WHERE receipt_id=CAST(OLD.item AS INTEGER);
END;

-- Contract terms survive financial stream resets and merges.
CREATE TABLE contract_record (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 owner TEXT NOT NULL CHECK(trim(owner)<>''),
 record_key TEXT NOT NULL CHECK(trim(record_key)<>''),
 kind TEXT NOT NULL DEFAULT 'contract',
 title TEXT NOT NULL CHECK(trim(title)<>''),
 provider TEXT,
 facts TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(facts)),
 sources TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(sources)),
 fixed_id INTEGER REFERENCES fixed_cost(id),
 owned_id INTEGER REFERENCES owned_item(id),
 revision INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
 updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
 UNIQUE(owner,record_key)
);
CREATE TABLE contract_evidence (
 owner TEXT NOT NULL,
 source_ref TEXT NOT NULL,
 source_slot TEXT NOT NULL DEFAULT '0',
 contract_id INTEGER NOT NULL REFERENCES contract_record(id),
 words TEXT,
 facts TEXT NOT NULL CHECK(json_valid(facts)),
 PRIMARY KEY(owner,source_ref,source_slot)
);
CREATE TABLE contract_proof (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 contract_id INTEGER NOT NULL REFERENCES contract_record(id),
 digest TEXT NOT NULL,
 ref TEXT NOT NULL,
 label TEXT NOT NULL,
 UNIQUE(contract_id,digest)
);
CREATE TABLE contract_reminder (
 contract_id INTEGER PRIMARY KEY REFERENCES contract_record(id),
 block_id INTEGER UNIQUE REFERENCES block(id),
 signature TEXT NOT NULL,
 event TEXT NOT NULL,
 due_at TEXT NOT NULL
);
CREATE INDEX idx_contract_owned ON contract_record(owned_id);
CREATE INDEX idx_contract_fixed ON contract_record(fixed_id);
CREATE TRIGGER trg_contract_fixed_delete BEFORE DELETE ON fixed_cost BEGIN
 UPDATE contract_record SET fixed_id=NULL WHERE fixed_id=OLD.id;
END;
CREATE TRIGGER trg_contract_owned_delete BEFORE DELETE ON owned_item BEGIN
 UPDATE contract_record SET owned_id=NULL,facts=json_set(facts,'$.status','disposed'),revision=revision+1 WHERE owned_id=OLD.id;
END;
CREATE TRIGGER trg_contract_block_delete BEFORE DELETE ON block BEGIN
 UPDATE contract_reminder SET block_id=NULL WHERE block_id=OLD.id;
END;
CREATE TRIGGER trg_contract_links_insert BEFORE INSERT ON contract_record BEGIN
 SELECT RAISE(ABORT,'contract stream missing') WHERE NEW.fixed_id IS NOT NULL
 AND NOT EXISTS(SELECT 1 FROM fixed_cost WHERE id=NEW.fixed_id);
 SELECT RAISE(ABORT,'contract possession missing') WHERE NEW.owned_id IS NOT NULL
 AND NOT EXISTS(SELECT 1 FROM owned_item WHERE id=NEW.owned_id);
END;
CREATE TRIGGER trg_contract_links_update BEFORE UPDATE OF fixed_id,owned_id ON contract_record BEGIN
 SELECT RAISE(ABORT,'contract stream missing') WHERE NEW.fixed_id IS NOT NULL
 AND NOT EXISTS(SELECT 1 FROM fixed_cost WHERE id=NEW.fixed_id);
 SELECT RAISE(ABORT,'contract possession missing') WHERE NEW.owned_id IS NOT NULL
 AND NOT EXISTS(SELECT 1 FROM owned_item WHERE id=NEW.owned_id);
END;
CREATE TRIGGER trg_contract_evidence_insert BEFORE INSERT ON contract_evidence BEGIN
 SELECT RAISE(ABORT,'contract owner mismatch') WHERE NOT EXISTS
 (SELECT 1 FROM contract_record WHERE id=NEW.contract_id AND owner=NEW.owner);
END;
CREATE TRIGGER trg_contract_reminder_insert BEFORE INSERT ON contract_reminder BEGIN
 SELECT RAISE(ABORT,'contract task mismatch') WHERE NOT EXISTS
 (SELECT 1 FROM contract_record c JOIN timeblock t ON t.id=NEW.block_id
  WHERE c.id=NEW.contract_id AND t.kind='task' AND t.owning_agent=c.owner);
END;
CREATE TRIGGER trg_contract_proof_insert BEFORE INSERT ON contract_proof BEGIN
 SELECT RAISE(ABORT,'contract missing') WHERE NOT EXISTS
 (SELECT 1 FROM contract_record WHERE id=NEW.contract_id);
END;
CREATE TRIGGER trg_contract_delete BEFORE DELETE ON contract_record BEGIN
 SELECT RAISE(ABORT,'retire contracts through contract capture; preserve evidence');
END;

-- 095: direct experience participants, separate from calendar attendees.
CREATE TABLE experience_person (
  event_id INTEGER NOT NULL REFERENCES event(id) ON DELETE CASCADE,
  person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
  added_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
  PRIMARY KEY (event_id, person_id)
);
CREATE INDEX idx_experience_person_person ON experience_person(person_id);

-- 096: kit-owned calorie safety and reset/wipe guards.
-- Kit-owned calorie safety policies. Insert only absent keys: never replace
-- an existing policy, its identity, provenance, or owner-adjusted value.
INSERT INTO ledger_constraint
    (ulid, class, key, value, goal_id, inviolable, status, source, recorded_at)
SELECT '01KAR4T9G00000000000000001', 'safety', 'max_deficit_cap', '750',
       NULL, 1, 'active', 'seed', strftime('%Y-%m-%dT%H:%M:%SZ','now')
WHERE NOT EXISTS (SELECT 1 FROM ledger_constraint WHERE key='max_deficit_cap');
INSERT INTO ledger_constraint
    (ulid, class, key, value, goal_id, inviolable, status, source, recorded_at)
SELECT '01KAR4T9G00000000000000002', 'safety', 'calorie_floor',
       '{"floor_female":1200,"floor_male":1500,"floor_undeclared":1500,"bmr_base_factor":0.8,"binding":"weekly_avg_effective"}',
       NULL, 1, 'active', 'seed', strftime('%Y-%m-%dT%H:%M:%SZ','now')
WHERE NOT EXISTS (SELECT 1 FROM ledger_constraint WHERE key='calorie_floor');

-- Every reset/wipe connection is covered, even with foreign_keys=OFF.
-- ABORT rolls back the whole DELETE statement, rather than silently skipping
-- policies and reporting a successful wipe. Callers must roll back their reset.
CREATE TRIGGER trg_constraint_safety_delete BEFORE DELETE ON ledger_constraint
WHEN OLD.class='safety' AND OLD.inviolable=1 BEGIN
 SELECT RAISE(ABORT, 'inviolable safety constraint cannot be deleted');
END;
CREATE TRIGGER trg_constraint_safety_keep
BEFORE UPDATE OF class, key, inviolable, status ON ledger_constraint
WHEN OLD.class='safety' AND OLD.inviolable=1
 AND (NEW.class IS NOT OLD.class OR NEW.key IS NOT OLD.key
      OR NEW.inviolable IS NOT OLD.inviolable OR NEW.status IS NOT OLD.status) BEGIN
 SELECT RAISE(ABORT, 'inviolable safety constraint cannot be demoted or retired');
END;

-- SQLite REPLACE can perform implicit deletes without firing DELETE triggers
-- when recursive_triggers is OFF (the default). Refuse its PK/ULID collision
-- before SQLite reaches that deletion, independent of connection PRAGMAs.
CREATE TRIGGER trg_constraint_safety_replace BEFORE INSERT ON ledger_constraint
WHEN EXISTS (SELECT 1 FROM ledger_constraint
             WHERE class='safety' AND inviolable=1
               AND (id=NEW.id OR ulid=NEW.ulid)) BEGIN
 SELECT RAISE(ABORT, 'inviolable safety constraint cannot be replaced');
END;

-- 098: capture identity is independent from unknown occurrence time.
CREATE TABLE experience_capture_key (
 capture_key TEXT PRIMARY KEY,
 event_id INTEGER NOT NULL REFERENCES event(id) ON DELETE CASCADE
);
CREATE INDEX ix_event_occurrence ON event(occurred_on);
CREATE TRIGGER trg_experience_made_dish_delete BEFORE DELETE ON made_dish BEGIN
 DELETE FROM record WHERE source_table='made_dish' AND source_id=OLD.id;
END;
CREATE TRIGGER trg_experience_owned_item_delete BEFORE DELETE ON owned_item BEGIN
 DELETE FROM record WHERE source_table='owned_item' AND source_id=OLD.id;
END;
CREATE TRIGGER trg_experience_owned_stock_delete BEFORE DELETE ON owned_stock BEGIN
 DELETE FROM record WHERE source_table='owned_stock' AND source_id=OLD.owned_id;
END;
CREATE TRIGGER trg_experience_shop_capture_delete BEFORE DELETE ON shop_capture BEGIN
 DELETE FROM record WHERE source_table='shop_capture' AND source_id=OLD.id;
END;
CREATE TRIGGER trg_experience_shop_item_delete BEFORE DELETE ON shop_item BEGIN
 DELETE FROM record WHERE source_table='shop_item' AND source_id=OLD.id;
END;
CREATE TRIGGER trg_experience_ledger_resource_delete BEFORE DELETE ON ledger_resource BEGIN
 DELETE FROM record WHERE source_table='ledger_resource' AND source_id=OLD.id;
END;
CREATE TRIGGER trg_experience_entity_delete BEFORE DELETE ON entity BEGIN
 DELETE FROM record WHERE source_table='entity' AND source_id=OLD.id;
END;
CREATE TRIGGER trg_experience_stock_use_delete BEFORE DELETE ON stock_use BEGIN
 DELETE FROM record WHERE source_table='stock_use' AND source_id=OLD.rowid;
END;
-- -- Instrument purpose -- migration 099 -------------------------------
CREATE TABLE pay_purpose_tag (
 layer TEXT NOT NULL CHECK (layer IN ('account','card','wallet')),
 tag TEXT NOT NULL CHECK (length(trim(tag))>0),
 sort_order INTEGER NOT NULL,
 PRIMARY KEY (layer, tag)
);
INSERT INTO pay_purpose_tag(layer,tag,sort_order) VALUES
 ('account','생활비',1),('account','저축·투자',2),('account','사업',3),
 ('card','주력',1),('card','고정비·구독',2),('card','혜택용',3),
 ('wallet','주력',1),('wallet','고정비·구독',2),('wallet','혜택용',3);
INSERT OR IGNORE INTO pay_purpose_tag(layer,tag,sort_order) VALUES
 ('account','월급·고정비',10),
 ('account','생활비',11),
 ('account','저축',12),
 ('account','사업',13),
 ('account','안 따짐',14),
 ('card','월급·고정비',10),
 ('card','생활비',11),
 ('card','저축',12),
 ('card','사업',13),
 ('card','안 따짐',14);
CREATE TRIGGER trg_pay_funding_kind_insert AFTER INSERT ON pay_funding
 WHEN NEW.source_kind IS NULL BEGIN
 UPDATE pay_funding SET source_kind=(
  SELECT CASE WHEN layer IN ('card','account') THEN layer
  WHEN kind IN ('credit_card','debit_card') THEN 'card'
  WHEN kind IN ('account','securities','savings','cash') THEN 'account' END
  FROM pay_method WHERE id=NEW.source_id)
 WHERE wallet_id=NEW.wallet_id AND source_id=NEW.source_id AND sub=NEW.sub;
END;
CREATE TABLE IF NOT EXISTS person_home (
 person_id INTEGER NOT NULL REFERENCES persons(id) ON DELETE CASCADE,
 place_id INTEGER NOT NULL REFERENCES place(id) ON DELETE CASCADE,
 PRIMARY KEY(person_id, place_id)
);
CREATE TABLE IF NOT EXISTS place_identity_ask (
 id INTEGER PRIMARY KEY, query TEXT NOT NULL, place_id INTEGER NOT NULL REFERENCES place(id) ON DELETE CASCADE,
 address TEXT, payload TEXT, state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','yes','no')),
 UNIQUE(query, place_id)
);
CREATE TABLE IF NOT EXISTS travel_leg_retry (
 id INTEGER PRIMARY KEY, anchor_ulid TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('before','after')),
 origin_id INTEGER NOT NULL REFERENCES place(id), dest_id INTEGER NOT NULL REFERENCES place(id),
 attempts INTEGER NOT NULL DEFAULT 0, next_at TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','done','failed','cancelled')),
 error TEXT, notice TEXT, notice_read INTEGER NOT NULL DEFAULT 0,
 receipt_token TEXT, -- Single reserved seam for Metal's future carrier contract; no promise text.
 UNIQUE(anchor_ulid,role,origin_id,dest_id)
);
-- 103: nullable caffeine with independent origin/source (102 reserved).
ALTER TABLE meal_food ADD COLUMN caffeine_mg REAL CHECK (caffeine_mg IS NULL OR caffeine_mg >= 0);
ALTER TABLE meal_food ADD COLUMN caffeine_origin TEXT CHECK (caffeine_origin IS NULL OR caffeine_origin IN ('published','label','estimated'));
ALTER TABLE meal_food ADD COLUMN caffeine_source TEXT;


-- The owner is one member across agents; agent names are provenance only.
CREATE TABLE satiety_answer (
 member_id TEXT NOT NULL CHECK(length(trim(member_id)) > 0),
 occasion TEXT NOT NULL,
 meal_date TEXT NOT NULL,
 slot TEXT NOT NULL,
 food_ids TEXT NOT NULL CHECK(json_valid(food_ids) AND json_type(food_ids)='array'),
 grams REAL CHECK(grams IS NULL OR grams > 0),
 kcal REAL CHECK(kcal IS NULL OR kcal >= 0),
 level TEXT CHECK(level IS NULL OR level IN
  ('snack','too_little','slightly_light','just_right','slightly_heavy','too_full','stuffed')),
 recorded_at TEXT NOT NULL,
 source_agent TEXT NOT NULL,
 state TEXT NOT NULL CHECK(json_valid(state) AND json_type(state)='object'),
 PRIMARY KEY(member_id, occasion)
);

-- 105: regimen history is separate from meals and satiety.
CREATE TABLE health_regimen (
 id INTEGER PRIMARY KEY,
 member_id TEXT NOT NULL CHECK(length(trim(member_id))>0),
 name TEXT NOT NULL CHECK(length(trim(name))>0),
 kind TEXT NOT NULL CHECK(kind IN ('supplement','medication')),
 amount TEXT NOT NULL CHECK(length(trim(amount))>0),
 unit TEXT NOT NULL CHECK(length(trim(unit))>0),
 schedule_kind TEXT NOT NULL CHECK(schedule_kind IN ('clock','meal','before_workout','after_workout','record_only')),
 clock_time TEXT CHECK(clock_time IS NULL OR (length(clock_time)=5 AND clock_time GLOB '[0-2][0-9]:[0-5][0-9]' AND clock_time<'24:00')),
 meal_slot TEXT,
 weekdays TEXT NOT NULL DEFAULT '0,1,2,3,4,5,6',
 timezone TEXT NOT NULL DEFAULT 'Asia/Seoul',
 created_at TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
 CHECK((schedule_kind='clock' AND clock_time IS NOT NULL AND meal_slot IS NULL)
    OR (schedule_kind='meal' AND clock_time IS NULL AND meal_slot IS NOT NULL)
    OR (schedule_kind IN ('before_workout','after_workout','record_only') AND clock_time IS NULL AND meal_slot IS NULL)),
 UNIQUE(member_id,name,schedule_kind,clock_time,meal_slot,weekdays)
);
CREATE TABLE health_regimen_reminder (
 id TEXT PRIMARY KEY,
 member_id TEXT NOT NULL,
 planned_at TEXT NOT NULL,
 due_at TEXT NOT NULL,
 timezone TEXT NOT NULL,
 check_mode INTEGER NOT NULL DEFAULT 0 CHECK(check_mode IN (0,1)),
 revision INTEGER NOT NULL DEFAULT 0,
 sent_revision INTEGER NOT NULL DEFAULT -1,
 sending_agent TEXT,
 message_id INTEGER,
 chat_id INTEGER CHECK(chat_id IS NULL OR chat_id>0),
 UNIQUE(member_id,planned_at)
);
CREATE TABLE health_regimen_taken (
 regimen_id INTEGER NOT NULL REFERENCES health_regimen(id),
 planned_at TEXT NOT NULL,
 reminder_id TEXT NOT NULL REFERENCES health_regimen_reminder(id),
 position INTEGER NOT NULL CHECK(position>0),
 name TEXT NOT NULL,
 amount TEXT NOT NULL,
 unit TEXT NOT NULL,
 taken_at TEXT,
 source TEXT CHECK(source IN ('button','words')),
 CHECK((taken_at IS NULL AND source IS NULL) OR (taken_at IS NOT NULL AND source IS NOT NULL)),
 PRIMARY KEY(regimen_id,planned_at),
 UNIQUE(reminder_id,position)
);
CREATE INDEX health_regimen_due ON health_regimen_reminder(due_at);

CREATE INDEX idx_spend_method_date_purpose ON spend_entry(method,date);

-- 107: photo records exposed through the kit record provider.
CREATE TABLE photo_note (
 record_id TEXT PRIMARY KEY CHECK(record_id GLOB 'lk_*'),
 photo_source TEXT NOT NULL, photo_kind TEXT NOT NULL, photo_id TEXT NOT NULL,
 note TEXT NOT NULL, place_id INTEGER REFERENCES place(id), episode_id TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT
);
CREATE INDEX photo_note_records ON photo_note(updated_at,record_id);
CREATE TABLE photo_results_sent (
 record_id TEXT PRIMARY KEY CHECK(record_id GLOB 'lk_*'),
 sent_at TEXT NOT NULL, query TEXT NOT NULL,
 photo_ids_json TEXT NOT NULL CHECK(json_valid(photo_ids_json) AND json_type(photo_ids_json)='array'),
 results_link TEXT NOT NULL, chat_ref TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted_at TEXT
);
CREATE INDEX photo_results_sent_records ON photo_results_sent(updated_at,record_id);

-- 108: one durable clock across both record types; reads never advance it.
CREATE TABLE photo_record_state (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 last_global_ms INTEGER NOT NULL CHECK(last_global_ms>=0),
 purged_through TEXT
);
INSERT INTO photo_record_state VALUES(1,0,NULL);

-- 109: cooking owned appliances and learned personal pace.
CREATE TABLE cooking_appliance (
 owning_agent TEXT NOT NULL, appliance_type TEXT NOT NULL CHECK(appliance_type IN ('oven','airfryer','microwave','burner')),
 modes_json TEXT NOT NULL CHECK(json_valid(modes_json) AND json_type(modes_json)='array'), model TEXT, owner_words TEXT NOT NULL,
 active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)), updated_at INTEGER NOT NULL, PRIMARY KEY(owning_agent,appliance_type)
);
CREATE TABLE cooking_pace (owning_agent TEXT NOT NULL, recipe_id TEXT NOT NULL, step_id TEXT NOT NULL, samples INTEGER NOT NULL CHECK(samples>0),
 avg_sec INTEGER NOT NULL CHECK(avg_sec>0), updated_at INTEGER NOT NULL, PRIMARY KEY(owning_agent,recipe_id,step_id));

ALTER TABLE ledger_resource ADD COLUMN owned_item_id INTEGER REFERENCES owned_item(id);
CREATE INDEX ix_ledger_resource_owned_item ON ledger_resource(owned_item_id) WHERE owned_item_id IS NOT NULL;

CREATE TABLE health_regimen_session_end (
 session_id INTEGER PRIMARY KEY REFERENCES workouts(id) ON DELETE CASCADE,
 member_id TEXT NOT NULL,
 closed_at TEXT NOT NULL
);

-- 111: per-gym exercise choice; see migration WHY.
CREATE TABLE ax_gym_exercise_machine (
 gym_id INTEGER NOT NULL REFERENCES place(id),
 exercise_code TEXT NOT NULL CHECK(length(trim(exercise_code))>0),
 machine_ref TEXT NOT NULL REFERENCES ax_machine(ulid),
 source TEXT NOT NULL CHECK(length(trim(source))>0),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL,
 PRIMARY KEY(gym_id,exercise_code)
);
CREATE INDEX idx_ax_gym_exercise_machine_ref
 ON ax_gym_exercise_machine(machine_ref);
CREATE TRIGGER trg_ax_gym_exercise_machine_active
BEFORE INSERT ON ax_gym_exercise_machine
WHEN NOT EXISTS(SELECT 1 FROM ax_machine WHERE ulid=NEW.machine_ref AND status='active')
BEGIN
 SELECT RAISE(ABORT, 'gym link refused: machine must exist and be active');
END;
CREATE TRIGGER trg_ax_gym_exercise_machine_active_upd
BEFORE UPDATE ON ax_gym_exercise_machine
WHEN NOT EXISTS(SELECT 1 FROM ax_machine WHERE ulid=NEW.machine_ref AND status='active')
BEGIN
 SELECT RAISE(ABORT, 'gym link refused: machine must exist and be active');
END;
CREATE TRIGGER trg_ax_gym_exercise_machine_gym
BEFORE INSERT ON ax_gym_exercise_machine
WHEN NOT EXISTS(SELECT 1 FROM place WHERE id=NEW.gym_id)
BEGIN
 SELECT RAISE(ABORT, 'gym link refused: gym must name a place');
END;
CREATE TRIGGER trg_ax_gym_exercise_machine_gym_upd
BEFORE UPDATE OF gym_id ON ax_gym_exercise_machine
WHEN NOT EXISTS(SELECT 1 FROM place WHERE id=NEW.gym_id)
BEGIN
 SELECT RAISE(ABORT, 'gym link refused: gym must name a place');
END;

-- 112: sent results retain delivery metadata and a structured query.
ALTER TABLE photo_results_sent ADD COLUMN channel TEXT NOT NULL DEFAULT 'telegram' CHECK(length(channel) BETWEEN 1 AND 128);
ALTER TABLE photo_results_sent ADD COLUMN query_text TEXT CHECK(query_text IS NULL OR length(query_text) BETWEEN 1 AND 200);
ALTER TABLE photo_results_sent ADD COLUMN total_matches INTEGER CHECK(total_matches IS NULL OR (typeof(total_matches)='integer' AND total_matches BETWEEN 0 AND 9007199254740991));

-- 114: event identity compatibility. No event backfill.
CREATE TABLE event_alias (
 old_id INTEGER PRIMARY KEY CHECK(old_id>0),
 old_ulid TEXT NOT NULL UNIQUE CHECK(length(old_ulid)>0),
 canonical_id INTEGER NOT NULL REFERENCES event(id) ON DELETE RESTRICT,
 CHECK(old_id<>canonical_id)
);
CREATE INDEX idx_event_alias_canonical ON event_alias(canonical_id);
-- An alias is always one hop. A live event is never simultaneously an alias.
-- These guards also work on import/restore connections with foreign_keys OFF.
CREATE TRIGGER trg_event_identity_alias_insert BEFORE INSERT ON event_alias BEGIN
 SELECT RAISE(ABORT, 'event alias identity is already registered') WHERE EXISTS(
  SELECT 1 FROM event_alias WHERE (old_id=NEW.old_id OR old_ulid=NEW.old_ulid)
  AND (old_id<>NEW.old_id OR old_ulid<>NEW.old_ulid OR canonical_id<>NEW.canonical_id));
 SELECT RAISE(ABORT, 'event alias identity is still live') WHERE EXISTS(
  SELECT 1 FROM event WHERE id=NEW.old_id OR ulid=NEW.old_ulid);
 SELECT RAISE(ABORT, 'event alias target must be canonical') WHERE NOT EXISTS(
  SELECT 1 FROM event WHERE id=NEW.canonical_id) OR EXISTS(
  SELECT 1 FROM event_alias WHERE old_id=NEW.canonical_id);
END;
CREATE TRIGGER trg_event_identity_alias_update BEFORE UPDATE ON event_alias BEGIN
 SELECT RAISE(ABORT, 'event alias identity is immutable')
  WHERE NEW.old_id<>OLD.old_id OR NEW.old_ulid<>OLD.old_ulid;
 SELECT RAISE(ABORT, 'event alias target must be canonical') WHERE NOT EXISTS(
  SELECT 1 FROM event WHERE id=NEW.canonical_id) OR EXISTS(
  SELECT 1 FROM event_alias WHERE old_id=NEW.canonical_id);
END;
CREATE TRIGGER trg_event_identity_insert BEFORE INSERT ON event BEGIN
 SELECT RAISE(ABORT, 'event id is reserved by alias high-water mark')
 WHERE EXISTS(SELECT 1 FROM event_alias) AND (CASE WHEN NEW.id=-1 THEN (SELECT COALESCE(MAX(id),0)+1 FROM event)
         ELSE NEW.id END) <= (SELECT COALESCE(MAX(old_id),0) FROM event_alias);
 SELECT RAISE(ABORT, 'event ULID is reserved by alias')
 WHERE EXISTS(SELECT 1 FROM event_alias WHERE old_ulid=NEW.ulid);
 SELECT RAISE(ABORT, 'cannot replace an alias target')
 WHERE EXISTS(SELECT 1 FROM event_alias a JOIN event e ON e.id=a.canonical_id
              WHERE e.id=NEW.id OR e.ulid=NEW.ulid);
END;
CREATE TRIGGER trg_event_identity_update BEFORE UPDATE OF id,ulid ON event BEGIN
 SELECT RAISE(ABORT, 'event id is reserved by alias high-water mark')
 WHERE EXISTS(SELECT 1 FROM event_alias) AND NEW.id<>OLD.id AND NEW.id <= (SELECT COALESCE(MAX(old_id),0) FROM event_alias);
 SELECT RAISE(ABORT, 'event ULID is reserved by alias')
 WHERE EXISTS(SELECT 1 FROM event_alias WHERE old_ulid=NEW.ulid);
 SELECT RAISE(ABORT, 'cannot replace an alias target')
 WHERE EXISTS(SELECT 1 FROM event_alias a JOIN event e ON e.id=a.canonical_id
              WHERE e.id<>OLD.id AND (e.id=NEW.id OR e.ulid=NEW.ulid));
 SELECT RAISE(ABORT, 'cannot change an alias target identity')
 WHERE (NEW.id<>OLD.id OR NEW.ulid<>OLD.ulid)
 AND EXISTS(SELECT 1 FROM event_alias WHERE canonical_id=OLD.id);
END;
CREATE TRIGGER trg_event_identity_delete BEFORE DELETE ON event
WHEN EXISTS(SELECT 1 FROM event_alias WHERE canonical_id=OLD.id) BEGIN
 SELECT RAISE(ABORT, 'redirect aliases before deleting canonical event');
END;
CREATE TRIGGER trg_event_time_record_insert BEFORE INSERT ON event
WHEN NEW.time_record_id IS NOT NULL AND NOT EXISTS(
 SELECT 1 FROM record WHERE id=NEW.time_record_id) BEGIN
 SELECT RAISE(ABORT, 'event time provenance record missing');
END;
CREATE TRIGGER trg_event_time_record_update BEFORE UPDATE OF time_record_id ON event
WHEN NEW.time_record_id IS NOT NULL AND NOT EXISTS(
 SELECT 1 FROM record WHERE id=NEW.time_record_id) BEGIN
 SELECT RAISE(ABORT, 'event time provenance record missing');
END;
CREATE TRIGGER trg_event_time_record_delete BEFORE DELETE ON record BEGIN
 UPDATE event SET time_record_id=NULL WHERE time_record_id=OLD.id;
END;

-- Migration 117: before/after evidence for reversible location cleanup.
CREATE TABLE place_location_117_log (
 table_name TEXT NOT NULL, row_key TEXT NOT NULL,
 before_json TEXT, after_json TEXT,
 PRIMARY KEY(table_name,row_key)
);
PRAGMA user_version = 117;
