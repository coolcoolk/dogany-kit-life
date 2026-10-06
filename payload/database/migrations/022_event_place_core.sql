-- 022_event_place_core.sql  (target user_version 22)
-- reversible: partial. ADD COLUMN is not cleanly reversible in older SQLite
--   without a table rebuild; the resolver_state back-fill is a pure UPDATE.
--   Rollback path = table rebuild dropping the new columns (their data lost).
-- Source: SPEC v2 event-place-placement core (kit_life).
--   Ticket: the 2026-08-11 event-place placement core spec
-- v21 -> v22.
--
-- SCOPE (F9): PURE-ADDITIVE new columns, ALL NULLABLE, DEFAULT NULL.
--   ALTER ADD COLUMN cannot carry a CHECK constraint -- token enums are
--   enforced by the SDK write-layer (same design as place.is_base @ DGN-579).
--   Tokens stay English (Gate3 i18n): display/render localizes, storage does not.
--
--   event (actionable / placement / concurrency / resolver):
--     exec_context         'fixed_place'|'home'|'workspace'|'anywhere'  (actionable only)
--     placement_rule       'anchored'|'floating'|'dependent'
--     placement_fallback   fallback rule when a dependent boarding fails
--     placement_depends_on parent event ulid (dependent placement)
--     attn_demand          'passive'|'light'|'full'   (attention channel, concurrency)
--     body_demand          'passive'|'light'|'full'   (body channel, concurrency)
--     resolver_state       'pending'|'resolved'|'deferred'
--   place:
--     granularity          'hub'|'region'  (region places are excluded from travel edges)
--
-- NOT IN THIS MIGRATION (deferred by design):
--   * kind enum shift task/appointment -> actionable: a CHECK rewrite means a
--     table rebuild; it lands in a later migration AFTER the mirror adapter is
--     pre-switched to read the new tokens (F8 pre-switch-then-cut order).
--   * DROP is_routine: only after the adapter no longer reads it (F8). The
--     column is LEFT IN PLACE here.
--
-- F9 legacy guard: every row that exists at migration time is pre-marked
--   resolver_state='deferred' so the resolver hook never re-interrogates the
--   backlog (no re-question storm). Rows created after this migration start
--   with resolver_state NULL and flow through the hook normally.
--
-- The ADD COLUMN order below is byte-matched to the fresh-mint v22 schema.sql
-- so `fresh == migrated`. Runner gates on user_version, so the non-idempotent
-- ALTERs run exactly once.

-- ============================================================================
-- event: actionable exec-context, placement rule, concurrency demands, resolver
-- ============================================================================
ALTER TABLE event ADD COLUMN exec_context         TEXT;
ALTER TABLE event ADD COLUMN placement_rule       TEXT;
ALTER TABLE event ADD COLUMN placement_fallback   TEXT;
ALTER TABLE event ADD COLUMN placement_depends_on TEXT;
ALTER TABLE event ADD COLUMN attn_demand          TEXT;
ALTER TABLE event ADD COLUMN body_demand          TEXT;
ALTER TABLE event ADD COLUMN resolver_state       TEXT;

-- ============================================================================
-- place: spatial granularity (region excluded from travel-edge modeling)
-- ============================================================================
ALTER TABLE place ADD COLUMN granularity          TEXT;

-- ----------------------------------------------------------------------------
-- F9: pre-mark the pre-existing backlog as resolver-deferred. Rows added after
-- this migration keep resolver_state NULL and flow through the resolver hook.
-- ----------------------------------------------------------------------------
UPDATE event SET resolver_state = 'deferred' WHERE resolver_state IS NULL;

PRAGMA user_version = 22;
