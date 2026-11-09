-- 039_resource_entity_crossing.sql  (target user_version 39)
--
-- reversible: NO down-migration ships.  Additive only -- one new table
--   (block_entity), six new views, entity/label seed rows, and the
--   spend_entry -> block/block_delta backfill.  Hand-rollback is: drop the
--   six views + block_entity, delete block/block_delta rows whose block
--   ulid LIKE 'x-spend-%', delete the seeded entity/label rows, and
--   PRAGMA user_version = 38.  A FAILED apply needs no rollback at all:
--   single transaction with .bail on, stamp last (023 discipline).
--
-- WHAT THIS LANDS (cut 2 -- the resource x entity crossing actually turns).
-- Measured 2026-09-08 on the live estate copy (uv 38): block_delta 557 rows,
-- ALL resource='nutrition', entity_id NON-NULL count 0; entity 0 rows;
-- spend_entry 1,603 rows sitting outside the axis entirely.  The crossing
-- machinery (block_delta.entity_id, 036) exists and nothing has ever
-- crossed.  Three moves:
--
--   MONEY   every spend_entry row becomes a 'record' block (deterministic
--           ulid 'x-spend-<id>', the 036 'x-meal-<id>' idiom: manufactured
--           identity, filtered out of the timeblock compat view) plus ONE
--           money delta.  Sign per canon: inflow + / outflow -.  kind
--           'expense' -> -amount (a negative expense amount is a refund and
--           correctly flips to inflow), 'income' -> +amount.  The row's own
--           method string names the payment instrument the money moved
--           through -- that is DECLARED source data, not retroactive
--           inference, so attributing the delta to that instrument does not
--           violate the no-backfill rule (which forbids INVENTING a 통장
--           the data never named).  spend_entry stays the ledger lane's
--           store of record; every spend_* reader keeps reading it.
--   ENTITY  minimal seed: ONLY the payment instruments the data proves --
--           each entity row is guarded by EXISTS(rows with that exact
--           method string), so an estate without that instrument seeds
--           nothing.  8 instruments measured live.  The 16 method-NULL rows
--           (an import window that carried no instrument column + bank
--           interest rows) get NO entity and NO catch-all: their deltas
--           keep entity_id NULL and are EXCLUDED from every crossing view,
--           counted honestly by v_c2_coverage (fail-CLOSED, C2 discipline:
--           low coverage is a state, not a defect to paper over).
--           No 몸/사람 entities: nothing in block_delta/measure references
--           them yet, so seeding them here would be rows with no evidence.
--   TIME    NOT stored.  A block's time cost is derivable from its own
--           start_at/end_at ("time is the block" -- the 036 trigger), so a
--           stored time delta would be a second copy that can drift.
--           v_block_time_delta DERIVES minutes where BOTH stamps exist and
--           the span is strictly positive (2,990 of 3,595 live blocks; the
--           8 zero-length legacy spans and the 14 start-only rows are not
--           evidence of spent time and are excluded, fail-CLOSED).
--
-- BALANCE IS NOT DERIVABLE AND NOT SERVED.  The owner has never given an
-- opening snapshot ("이 통장에 얼마 남았어는 내 통장 스냅샷을 한번도
-- 안줬으니 모르잖어", 2026-09-07).  Cumulative flow without an opening
-- stock is not a balance, so NO view here ships a balance or a lifetime
-- net: v_entity_flow serves per-month inflow/outflow only.  The authoring
-- schema's v_entity_balance is deliberately NOT landed.
--
-- BLOCK_ENTITY (the non-resource crossing: which targets a block touched).
-- Lands per the authoring schema with weight (allocation, owner-confirmed
-- 2026-09-02: allocated time = block time x weight, default equal split,
-- per-block weight sum == 1.0).  The PK keeps role, so K-5 (one block names
-- the same target ONCE) cannot be a structural constraint -- K-5/K-6 are
-- VERIFIER invariants, served as views (v_k5_duplicate_target /
-- v_k6_weight_sum must be empty) and pinned by tests.  v_entity_time_alloc
-- refuses to allocate any block that violates either invariant
-- (fail-CLOSED: a broken weight vector yields silence, not a wrong number).
-- No write path populates block_entity this cut -- the person/body fold
-- into entity is its own lane and needs owner decisions.
--
-- GUARD.  Both-sided, 037 discipline: refused unless user_version is
-- EXACTLY 38, BEFORE any transaction or write -- exit != 0, database
-- untouched.  A second guard refuses any spend_entry.kind outside
-- 'expense'/'income': the sign rule above is only defined for those two,
-- and silently bucketing an unknown kind would be exactly the catch-all
-- this file refuses everywhere else (live census: expense 1,540 /
-- income 63, nothing else).
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_039(seen_user_version);
CREATE TEMP TRIGGER _guard_039_refuse BEFORE INSERT ON _guard_039 BEGIN
  SELECT RAISE(FAIL, 'migration 039 refused: this database is not at user_version 38 -- either 039 was already applied (>= 39) or the 021~038 chain has not run (< 38). Nothing changed');
END;
INSERT INTO _guard_039
  SELECT user_version FROM pragma_user_version WHERE user_version <> 38;
DROP TABLE temp._guard_039;
CREATE TEMP TABLE _guard_039_kind(kind);
CREATE TEMP TRIGGER _guard_039_kind_refuse BEFORE INSERT ON _guard_039_kind BEGIN
  SELECT RAISE(FAIL, 'migration 039 refused: spend_entry.kind outside expense/income exists -- the money sign rule is undefined for it and this file does not guess. Nothing changed');
END;
INSERT INTO _guard_039_kind
  SELECT DISTINCT kind FROM spend_entry WHERE kind NOT IN ('expense','income');
DROP TABLE temp._guard_039_kind;
BEGIN IMMEDIATE;

-- 1. block_entity -- the non-resource crossing (authoring schema §3).
--    role is a free module token (no CHECK, rule B).  weight >= 0.0; the
--    sum==1.0 invariant (K-6) and the once-per-target invariant (K-5) are
--    verifier-level (PK keeps role; what structure cannot enforce is not
--    hidden, it is served by the two violation views below).
CREATE TABLE IF NOT EXISTS block_entity (
  block_id    INTEGER NOT NULL REFERENCES block(id)   ON DELETE CASCADE,
  entity_id   INTEGER NOT NULL REFERENCES entity(id)  ON DELETE CASCADE,
  role        TEXT NOT NULL DEFAULT 'about',
  weight      REAL NOT NULL DEFAULT 1.0 CHECK (weight >= 0.0),
  PRIMARY KEY (block_id, entity_id, role)
);
CREATE INDEX IF NOT EXISTS ix_block_entity_e ON block_entity(entity_id);

-- 2. Verifier invariants as views (each MUST be empty; tests pin it).
--    K-5: one block names the same target once -- role must not multiply it.
DROP VIEW IF EXISTS v_k5_duplicate_target;
CREATE VIEW v_k5_duplicate_target AS
SELECT block_id, entity_id, COUNT(*) AS n_roles
FROM block_entity
GROUP BY block_id, entity_id
HAVING COUNT(*) > 1;
--    K-6: per-block weight sum == 1.0 (float tolerance 1e-9).
DROP VIEW IF EXISTS v_k6_weight_sum;
CREATE VIEW v_k6_weight_sum AS
SELECT block_id, SUM(weight) AS weight_sum
FROM block_entity
GROUP BY block_id
HAVING ABS(SUM(weight) - 1.0) > 1e-9;

-- 3. Time is DERIVED, never stored (the delta would be a drifting second
--    copy of start_at/end_at).  Strictly positive spans only: a zero or
--    negative span is not evidence of spent time (fail-CLOSED), and
--    julianday() returns NULL on garbage so a non-parseable stamp is
--    excluded by the same comparison.
DROP VIEW IF EXISTS v_block_time_delta;
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

-- 4. Allocated time per target (canon: allocated = block time x weight).
--    A block that violates K-5 or K-6 is refused wholesale -- a broken
--    weight vector yields NO rows for that block, never a wrong number.
DROP VIEW IF EXISTS v_entity_time_alloc;
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

-- 5. Per-month flow per entity x resource.  FLOW ONLY: no balance column,
--    no lifetime net -- without an opening snapshot a cumulative sum would
--    read as a balance and lie (owner constraint 2026-09-07).  Rows whose
--    delta carries no entity are EXCLUDED (the JOIN is the exclusion) --
--    they are counted by v_c2_coverage instead, never bucketed.
DROP VIEW IF EXISTS v_entity_flow;
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

-- 6. C2 coverage -- what fraction of each stock-bearing resource's deltas
--    can say WHOSE they are.  A number report, not a judgment (DESIGN §4.3:
--    low coverage is the current state, not a defect).
DROP VIEW IF EXISTS v_c2_coverage;
CREATE VIEW v_c2_coverage AS
SELECT d.resource,
       COUNT(*) AS n_deltas,
       SUM(d.entity_id IS NOT NULL) AS n_with_entity
FROM block_delta d
JOIN resource r ON r.token = d.resource
WHERE r.stock_bearing = 1
GROUP BY d.resource;

-- 7. Entity seed -- ONLY instruments the data proves, each row guarded by
--    the exact method string existing in spend_entry.  token is the english
--    stable key (canon), the ko display the owner types lives in label.
--    origin='user': the instrument is the user's own declaration carried in
--    their rows; the migration is the carrier, not the author.
--    owner_entity_id stays NULL -- no 'self' entity exists and this file
--    does not invent one (owner's lane).  fungible=0: each instrument is an
--    individual, not a counted total.
INSERT OR IGNORE INTO entity(ulid, token, fungible, origin, status)
SELECT 'x-ent-hyundai_card_m', 'hyundai_card_m', 0, 'user', 'active'
 WHERE EXISTS (SELECT 1 FROM spend_entry WHERE method = '현대카드M');
INSERT OR IGNORE INTO entity(ulid, token, fungible, origin, status)
SELECT 'x-ent-naverpay_account', 'naverpay_account', 0, 'user', 'active'
 WHERE EXISTS (SELECT 1 FROM spend_entry WHERE method = '네이버페이계좌');
INSERT OR IGNORE INTO entity(ulid, token, fungible, origin, status)
SELECT 'x-ent-kakaopay_securities', 'kakaopay_securities', 0, 'user', 'active'
 WHERE EXISTS (SELECT 1 FROM spend_entry WHERE method = '카카오페이증권');
INSERT OR IGNORE INTO entity(ulid, token, fungible, origin, status)
SELECT 'x-ent-toss_bank', 'toss_bank', 0, 'user', 'active'
 WHERE EXISTS (SELECT 1 FROM spend_entry WHERE method = '토스뱅크');
INSERT OR IGNORE INTO entity(ulid, token, fungible, origin, status)
SELECT 'x-ent-ibk_account', 'ibk_account', 0, 'user', 'active'
 WHERE EXISTS (SELECT 1 FROM spend_entry WHERE method = 'IBK계좌');
INSERT OR IGNORE INTO entity(ulid, token, fungible, origin, status)
SELECT 'x-ent-naverpay_hana_check', 'naverpay_hana_check', 0, 'user', 'active'
 WHERE EXISTS (SELECT 1 FROM spend_entry WHERE method = '네이버페이하나체크');
INSERT OR IGNORE INTO entity(ulid, token, fungible, origin, status)
SELECT 'x-ent-samsung_id_global', 'samsung_id_global', 0, 'user', 'active'
 WHERE EXISTS (SELECT 1 FROM spend_entry WHERE method = '삼성iD글로벌');
INSERT OR IGNORE INTO entity(ulid, token, fungible, origin, status)
SELECT 'x-ent-moha_toss_bank', 'moha_toss_bank', 0, 'user', 'active'
 WHERE EXISTS (SELECT 1 FROM spend_entry WHERE method = '모하토스뱅크');
-- The ko display = the method string the owner's rows actually carry; it is
-- also the RESOLUTION key (write path + backfill both resolve method ->
-- entity through this label, exactly-one match or NULL).
INSERT OR IGNORE INTO label(namespace, token, lang, display)
SELECT 'entity', e.token, 'ko',
       CASE e.token
         WHEN 'hyundai_card_m'      THEN '현대카드M'
         WHEN 'naverpay_account'    THEN '네이버페이계좌'
         WHEN 'kakaopay_securities' THEN '카카오페이증권'
         WHEN 'toss_bank'           THEN '토스뱅크'
         WHEN 'ibk_account'         THEN 'IBK계좌'
         WHEN 'naverpay_hana_check' THEN '네이버페이하나체크'
         WHEN 'samsung_id_global'   THEN '삼성iD글로벌'
         WHEN 'moha_toss_bank'      THEN '모하토스뱅크'
       END
FROM entity e
WHERE e.ulid LIKE 'x-ent-%'
  AND e.token IN ('hyundai_card_m','naverpay_account','kakaopay_securities',
                  'toss_bank','ibk_account','naverpay_hana_check',
                  'samsung_id_global','moha_toss_bank');

-- 8. spend_entry -> record blocks.  The 036 meal idiom exactly:
--    deterministic 'x-spend-<id>' ulid (manufactured identity; the compat
--    timeblock view already filters 'x-%'), date-only block (the ledger row
--    has no span), settled as a machine-verdict record at the row's own
--    created_at (localtime grade converted to UTC-Z, the 036 conversion).
--    The migration-only provenance token opens here and closes in step 10.
UPDATE provenance SET writable = 1 WHERE token = 'unknown';
INSERT OR IGNORE INTO block(ulid, source_module, title, date, kind_token,
    settled_at, settled_outcome, settled_by, settled_verdict, created_at)
SELECT 'x-spend-' || s.id, 'ledger', s.name, s.date, 'record',
    c.s_at,
    CASE WHEN c.s_at IS NOT NULL THEN 'done' END,
    CASE WHEN c.s_at IS NOT NULL THEN 'spend-record' END,
    CASE WHEN c.s_at IS NOT NULL THEN 'machine' END,
    s.created_at
FROM spend_entry s
JOIN (SELECT id,
             CASE WHEN created_at LIKE '%Z' AND instr(created_at, 'T') > 0
                  THEN created_at
                  ELSE strftime('%Y-%m-%dT%H:%M:%SZ', created_at, 'utc') END
               AS s_at
      FROM spend_entry) c ON c.id = s.id;

-- 9. One money delta per spend block.  entity resolves through the label
--    seeded above: EXACTLY ONE active entity carrying that display, else
--    NULL (fail-CLOSED -- ambiguity and absence both stay unattributed).
--    value_origin='unknown': migrated values carry the migration token
--    (D-5), the amount itself is the source row's own number.
INSERT OR IGNORE INTO block_delta(block_id, resource, entity_id, amount,
                                  unit, component, value_origin)
SELECT b.id, 'money',
       CASE WHEN em.n = 1 THEN em.eid END,
       CASE s.kind WHEN 'income' THEN s.amount ELSE -s.amount END,
       'krw', '', 'unknown'
FROM spend_entry s
JOIN block b ON b.ulid = 'x-spend-' || s.id
LEFT JOIN (SELECT l.display AS display, MIN(e.id) AS eid,
                  COUNT(DISTINCT e.id) AS n
           FROM entity e
           JOIN label l ON l.namespace = 'entity' AND l.token = e.token
           WHERE e.status = 'active'
           GROUP BY l.display) em ON em.display = s.method;

-- 10. Close the migration-only provenance token (D-5): from here the
--     schema's own triggers refuse any new 'unknown' delta write.
UPDATE provenance SET writable = 0 WHERE token = 'unknown';

-- Lockstep stamp.  MUST be the last statement before COMMIT: if anything
-- above failed, .bail aborted this run, the transaction rolled back, and
-- user_version stays 38.
PRAGMA user_version = 39;
COMMIT;
