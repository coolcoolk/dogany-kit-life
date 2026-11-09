-- 016_payline.sql  (target user_version 16)
-- reversible: yes
-- Source: Ag 015_payline (src .sql archived; DDL reconstructed from live Ag DB
-- per DGN-791 O-4; renumbered, file-number == target uv).
-- pay_method + fixed_cost + ledger_guard (+indexes, +guard seed). v15 -> v16.

CREATE TABLE IF NOT EXISTS pay_method (
    id                INTEGER PRIMARY KEY,
    name              TEXT NOT NULL UNIQUE,
    kind              TEXT NOT NULL,
    aliases           TEXT,
    billing_day       INTEGER,
    linked_account_id INTEGER REFERENCES pay_method(id),
    created_at        TEXT NOT NULL DEFAULT (datetime('now','localtime'))
);

CREATE TABLE IF NOT EXISTS fixed_cost (
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
    CHECK (auto_entry IN (0,1))
);

CREATE TABLE IF NOT EXISTS ledger_guard (
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

CREATE INDEX IF NOT EXISTS idx_pay_method_name ON pay_method(name);
CREATE INDEX IF NOT EXISTS idx_fixed_next_due ON fixed_cost(next_due);
CREATE INDEX IF NOT EXISTS idx_fixed_direction ON fixed_cost(direction);

-- guard defaults (measure-first seed; INSERT OR IGNORE = idempotent).
INSERT OR IGNORE INTO ledger_guard
  (guard_key, scope, value, unit, enforcement, inviolable, source) VALUES
  ('auto_entry_enabled','global',0,'flag','block',0,'seed'),
  ('entry_amount_ceiling','global',3000000,'KRW','hold',0,'seed-fallback'),
  ('dup_window','global',NULL,'count','hold',0,'seed'),
  ('budget_threshold','global',NULL,'KRW','inform',0,'seed'),
  ('split_aggregation','global',0,'flag','inform',0,'seed');

PRAGMA user_version = 16;
