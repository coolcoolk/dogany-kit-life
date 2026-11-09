-- 029_food_cache.sql  (target user_version 29)
-- reversible: effectively YES (additive, new tables only). Creates 3 NEW GLOBAL
--   shared tables (meal_food, meal_alias, meal_portion) via CREATE ... IF NOT
--   EXISTS. No ALTER on any existing table, no data rewrite. Rollback = DROP the
--   3 tables. The per-instance food_cache.json files stay on disk as a read-only
--   backup until the backfill is verified, so a rollback loses nothing.
--
-- Source: the 2026-08-20 skill-topology SPEC (§4b) + food-cache
--   ticket 2026-08-20 food-cache-not-shared. Moves the per-instance
--   food_cache.json SHARED-KNOWLEDGE part (items/aliases/portions) out of local
--   JSON files and into the shared kit_life DB so a food registered on one instance
--   is instantly visible to the other (closes the divergence bug: one instance knew many
--   more foods than the other, which fell back to generic estimates for them).
--   Lane: kit_life (DGN-708) -- not a framework handoff.
--
-- SCOPE: 3 NEW tables, ALL GLOBAL scope (NO owning_agent column). Nutrition
--   (구운계란 50g = 70kcal) is a DOMAIN-AGNOSTIC fact -- the same truth whoever
--   registers it -- so the cache is shared REFERENCE knowledge, not an actor-audit
--   record. Unlike meals (which keeps owning_agent for one-writer audit), the
--   cache carries provenance ONLY via updated_by (metadata, NOT a scope
--   discriminator). Naming = `meal_` prefix (one roof with the meals table, diet
--   module owned). (§4a decision GLOBAL / §4b naming)
--
-- NOT IN SCOPE (stay local, §4c): food_cache.meal_times (user-behavior state,
--   deferred to a follow-up) and food_cache.settings (per-instance skill-tuning,
--   never shared). This migration creates ONLY the 3 shared-knowledge tables.
--
-- BACKFILL (SEPARATE apply-time step, NOT written here -- §4d): JSON is a file
--   outside the schema, so migration SQL cannot read it. This file creates the 3
--   EMPTY tables (lockstep-gate-passing, pure additive). A SEPARATE backfill
--   script (kit propagation-editor lane) then merges the two per-instance JSONs
--   (both instances) via upsert, dedup rule = latest `updated` date wins; on a
--   tie, estimated=0 (measured/label) wins; still tied, the origin instance with the
--   larger cache wins. JSON originals are backed up to
--   database/food_cache.json.bak-pre029 and kept read-only. The backfill
--   EXECUTOR is NOT part of this DDL file -- it is an apply-time step run under the
--   same lockstep. Column map: JSON carbs->carb (align with meals), estimated
--   bool->INTEGER 0/1, updated->updated_at + new updated_by provenance; kcal keeps
--   the stored JSON value (NOT GENERATED -- the cache preserves the raw label/
--   estimate; recompute happens at logging time on meals.kcal).
--
-- 028 was TAKEN by 028_goal_engine.sql (target user_version 28). Both this SPEC
--   and the goal-engine SPEC independently claimed 028; reconciled to a contiguous
--   sequence: goal-engine = 028, food-cache = 029. This migration is 029 /
--   user_version 29 and DEPENDS ON 028 (v28).
--
-- SAFETY: pure new-table adds -- invisible to running v28 code (lookup.py still
--   reads its JSON until it is switched to the SDK/DB path in a separate skill-
--   content step). No existing query is affected.
--
-- The 3 CREATE TABLE DDL below are byte-matched to the fresh-mint v29 schema.sql
--   so `fresh == migrated`.
--
-- LANDING (mandatory, same discipline as 025/026/027/028; NOT part of authoring
--   this file): apply on live ONLY via the migration runner + code-pin bump
--   (EXPECTED_USER_VERSION 28->29) in one coordinated lockstep run (shared DB =
--   both agents' pins move together). NEVER hand-run `sqlite3 live.db < 029...`.
--   Application + backfill + lockstep is the kit propagation-editor lane
--   (DGN-708 execution gate), done as a SEPARATE future step the kit lane decides + runs.
--
-- Atomicity wrapper (matches 023-028, DGN-871 NO-GO #1): `.bail on` aborts on the
--   first error; BEGIN IMMEDIATE .. COMMIT makes the 3 CREATEs + the user_version
--   stamp ONE atomic unit -> a mid-statement failure rolls back and leaves
--   user_version at 28 (no half-migrated laundering). Dot-commands are
--   sqlite3-CLI-only -- the runner is the CLI.
.bail on
.timeout 5000
BEGIN IMMEDIATE;

-- ============================================================================
-- 1. meal_food -- food nutrition cache (was food_cache.json "items"). GLOBAL
--    shared knowledge (NO owning_agent). PK = normalized food name (= existing
--    JSON key). provenance via updated_by only. (§4b)
-- ============================================================================
CREATE TABLE IF NOT EXISTS meal_food (
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

-- ============================================================================
-- 2. meal_alias -- alias -> canonical food name (was food_cache.json "aliases").
--    GLOBAL shared. e.g. "프로틴" -> "다이마타이즈 iso100". (§4b)
-- ============================================================================
CREATE TABLE IF NOT EXISTS meal_alias (
    alias        TEXT PRIMARY KEY,           -- "프로틴"
    canonical    TEXT NOT NULL,              -- "다이마타이즈 iso100" (= meal_food.key or name)
    updated_by   TEXT,                       -- provenance (audit)
    updated_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

-- ============================================================================
-- 3. meal_portion -- default serving grams (was food_cache.json "portions").
--    GLOBAL shared. e.g. "김밥" -> {grams:110, n:1}. (§4b)
-- ============================================================================
CREATE TABLE IF NOT EXISTS meal_portion (
    key          TEXT PRIMARY KEY,           -- "김밥"
    grams        REAL NOT NULL,
    n            REAL NOT NULL DEFAULT 1,    -- how many servings the grams is stated for
    updated_by   TEXT,                       -- provenance (audit)
    updated_at   TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

PRAGMA user_version = 29;

-- Close the atomicity wrapper. Any failure above -> .bail aborted, no COMMIT,
-- rollback, user_version stays 28 (no half-migrated laundering).
COMMIT;
