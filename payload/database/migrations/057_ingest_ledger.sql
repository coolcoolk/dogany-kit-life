-- 057_ingest_ledger.sql  (target user_version 57)
--
-- reversible: YES, losslessly for every row that existed before.
--   Rollback = DROP TRIGGER trg_ingest_consent_no_backdate,
--   trg_ingest_consent_no_backdate_upd, trg_ingest_ref_gate,
--   trg_ingest_ref_gate_upd; DROP TABLE ingest_ref; DROP TABLE ingest_consent;
--   DELETE FROM unit WHERE token='bpm' (only when no metric_def row points at
--   it); PRAGMA user_version = 56.  Nothing pre-existing is touched: two new
--   tables, four triggers, one unit row.  No row of any existing table moves.
--
-- ==========================================================================
-- WHAT. The kit's shared "들어온 기록 장부" (ingest ledger): one row per
-- external event a module has turned into kit rows, keyed by the sender's own
-- identity (source, kind, ext_id), plus one CONSENT row per (source, kind).
--
-- The Dogany app pushes events to a local transporter receiver
-- (~/.transporter/events.sqlite3; kinds healthkit steps / heart_rate / sleep /
-- workout / active_energy_day, shortcut card_sms, ...).  Modules (health,
-- ledger/spend) each read their own kinds, interpret them and decide when to
-- pull.  What they share -- and what this file ships -- is:
--
--   ingest_consent  (source, kind) -> approved_at / revoked_at / module /
--                   last_pulled_at.  The row is written by the module's
--                   ONBOARDING step when the user approves that automation
--                   (kit verb ingest-consent), never by an operator and never
--                   by a migration.  This file creates the table EMPTY.
--   ingest_ref      (source, kind, ext_id) -> occurred_at, target_table,
--                   target_id, payload_hash.  Idempotent by the key: a
--                   re-sent event (duplicate card_sms delivery) is SAME, an
--                   overwritten one (active_energy_day closed:false ->
--                   closed:true, same id = the local date) is CHANGED and
--                   updates the one row.  Never a second row.
--
-- OWNER RULES (2026-09-27) THIS FILE MAKES STRUCTURAL, NOT PROSE:
--   1. NO BACKFILL.  trg_ingest_ref_gate refuses any ledger row whose
--      occurred_at is before its (source, kind) approved_at, or whose
--      (source, kind) has no consent or a revoked one -- on INSERT and on
--      UPDATE.  Re-approving after --off moves approved_at to the new moment,
--      so the off-period is never back-filled either.
--   2. CONSENT CANNOT BE BACKDATED.  trg_ingest_consent_no_backdate refuses an
--      approved_at more than one minute before the wall clock on insert, and
--      any update that moves approved_at into the past.  "Approve as of last
--      spring" -- the one move that would turn consent into a backfill -- is
--      not expressible.
--   3. IDEMPOTENT.  PRIMARY KEY (source, kind, ext_id).
--
-- The gate on the ledger row is the backstop.  A module writes its domain row
-- (workout-add / log-metric / spend-add) through its own verb, so it asks
-- first (ingest-check, or the cursor from ingest-get) and only then writes;
-- ingest-put refuses the same things this trigger refuses.
--
-- Timestamps: UTC ISO-8601 with 'Z'.  The verbs normalise to
-- 'YYYY-MM-DDTHH:MM:SS.fffZ' (the transporter's own format); the triggers
-- compare through julianday() so a differently-shaped but valid stamp still
-- compares by time, never by string.
--
-- NAMES.  source / kind follow the transporter kind rule
-- ^[a-z][a-z0-9_]{0,63}$ (<means>_<path>).  ingest_ref's column names are the
-- ones pack_health's hk-ingest (dsp/healthkit-ingest) already reads in-process
-- (SELECT kind, ext_id, target_table, target_id, payload_hash FROM ingest_ref).
--
-- ALSO HERE: unit 'bpm'.  pack_health registers resting_hr_bpm / hr_avg_bpm
-- through the new metric-register verb with unit 'bpm', and metric_def.unit_token
-- REFERENCES unit(token).
-- ==========================================================================

.bail on
.timeout 5000

CREATE TEMP TABLE _guard_057(seen_user_version);
CREATE TEMP TRIGGER _guard_057_refuse BEFORE INSERT ON _guard_057 BEGIN
  SELECT RAISE(FAIL, 'migration 057 refused: this database is not at user_version 56 -- either 057 was already applied (>= 57) or the chain up to 056 has not run (< 56). Nothing changed');
END;
INSERT INTO _guard_057
  SELECT user_version FROM pragma_user_version WHERE user_version <> 56;
DROP TABLE temp._guard_057;

BEGIN IMMEDIATE;

CREATE TEMP TABLE _guard_057_pre(seen_count);
CREATE TEMP TRIGGER _guard_057_pre_refuse BEFORE INSERT ON _guard_057_pre BEGIN
  SELECT RAISE(FAIL, 'migration 057 refused: an ingest_consent / ingest_ref object already exists -- this is not the shape this file was written against. Nothing changed');
END;
INSERT INTO _guard_057_pre
  SELECT COUNT(*) FROM sqlite_master
   WHERE name IN ('ingest_consent', 'ingest_ref')
  HAVING COUNT(*) <> 0;
DROP TABLE temp._guard_057_pre;

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

PRAGMA user_version = 57;
COMMIT;
