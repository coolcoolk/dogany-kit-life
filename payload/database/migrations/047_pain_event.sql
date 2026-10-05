-- 047_pain_event.sql  (target user_version 47)
--
-- reversible: YES, losslessly.  Rollback =
--   DROP VIEW v_pain_promotion_candidate; DROP VIEW v_pain_event;
--   DROP TABLE pain_event;  PRAGMA user_version = 46;
-- One new table, two new views, three new indexes.  NOT ONE existing row is
-- inserted-over, deleted, re-keyed or read-repointed: no ALTER, no trigger on
-- a neighbour, no seed into ledger_constraint / workout_sets / workouts.  A
-- FAILED apply needs no rollback either -- guard first, single transaction,
-- stamp last (023 discipline).
--
-- ==========================================================================
-- WHY -- THE BODY IS STORED IN TWO PLACES THAT DO NOT KNOW EACH OTHER.
--
-- `ledger_constraint` holds the STANDING body facts (injury_<site> keys,
-- some unsided, some per-side -- class 'safety').
-- `pain_events.db`, a private SQLite file beside the pain-triage skill, holds
-- the IN-SESSION events (33 rows, 2026-07-25 .. 2026-09-11).  Neither can see
-- the other, so two questions that are obviously one question have no answer:
--
--     "요즘 무릎이 얼마나 자주 아팠지"   -- the constraint side cannot say.
--     "무릎 때문에 뭘 피해야 하지"       -- the event side cannot say.
--
-- The separation was a DELIBERATE owner call, and its own retirement condition
-- is written into the file it created (pain.py docstring, verbatim):
--
--     "Kept OUT of the shared kit_life DB on purpose (owner choice: start
--      independent, migrate to kit_life later IF JOIN-WITH-SETS ANALYSIS IS
--      NEEDED).  Loose join to workouts = by date."
--
-- That condition is met, and the owner discharged the call on 2026-09-12 10:27.
-- Two measurements drove it.  The second is the sharper one: the morning batch
-- audit named the knee-safety family the #1 SAFETY finding -- the rule keys on
-- a `ledger_constraint` row that nothing in the estate populates, so it passes
-- silently as "not applicable".  A safety rule that cannot fail is not a safety
-- rule.  Part of that shape is exactly this: the evidence that WOULD create and
-- keep alive an injury_* row is sitting in a file the kit cannot read.
--
-- ==========================================================================
-- WHAT THIS IS, ON THE MAP.  MEASUREMENT AXIS, NOT A DOMAIN MODULE.
--
--     [측정축] metric
--         health-log   체성분  -- periodic measurement -> metric_log
--         pain-triage      통증 사건 -- recorded when it happens -> pain_event
--             └ repeats -> promotable to ledger_constraint
--
-- Same character as health-log: a skill converses, and the deterministic
-- write lands in the kit through kit_life.sh.  The one difference is the shape
-- of what is recorded -- inbody records NUMBERS, triage records EVENTS -- and
-- that is why this is a table of its own and not another metric_log token.
--
-- ==========================================================================
-- THE JOIN.  THE POINT OF THE WHOLE EXERCISE.
--
-- The owner's stated condition for moving was join-with-sets analysis, so a
-- second date-join would have been the move without the reason for it.
-- `pain_event.set_ref` is a REAL FK to `workout_sets(id)` -- a clean one:
-- workout_sets has an INTEGER PK and already carries
-- UNIQUE(session_id, exercise_name, set_seq), so the natural key a human says
-- out loud ("08-06, 인클라인 바벨, 3세트") resolves DETERMINISTICALLY to one
-- row.  ON DELETE SET NULL, never CASCADE: deleting a mis-logged set must not
-- delete the record that the owner's shoulder hurt.
--
-- With that key wired, "it hurt at THIS weight, on THIS set" reaches program
-- design as data instead of as prose in a note field.  Measured on the owner's
-- own history (the pre-wipe copy of the shared DB, 82 workouts / 907 sets):
-- 12 of the 33 events name a set that exists, and 3 of those 12 were logged
-- under a KOREAN exercise label ("인클라인 덤벨 프레스", "브이스쿼트") that no
-- string join could ever have matched.  They resolve anyway, because the match
-- is on (date, set_seq, weight) -- see pain_event_carry.py.
--
-- link_grade names WHICH of the three honest links a row has, so a reader can
-- never mistake 'we could not find it' for 'it did not happen in a session':
--     'set'      -- set_ref: this set, this load.  The full claim.
--     'session'  -- session_ref only: the day's session, no set identified.
--     'none'     -- no workout row exists (pre-session check-in, a non-session
--                   report, a day the owner never logged).  NOT a failure --
--                   "발가락(세션전 보고)" is a legitimately unlinked event.
-- It is a VIEW EXPRESSION, not a column.  The first cut of 047 stored it with
-- a both-sided CHECK tying it to the two refs, and the test suite promptly
-- showed what that costs: ON DELETE SET NULL nulls set_ref, the stored grade
-- still said 'set', the CHECK fired, and deleting a mis-logged set became
-- IMPOSSIBLE while any event referenced it -- SET NULL behaving as RESTRICT.
-- The deeper fault is the one this file already argues two paragraphs down:
-- the grade is DERIVABLE from the two refs, so storing it is the second clock.
-- Derived, a deleted set degrades the event from 'set' to 'session' by itself,
-- which is exactly the true statement about what is left.
--
-- ONE SOURCE FOR ONE FACT.  load_kg / set_no / rir are the AS-REPORTED lane
-- and a CHECK forbids them the moment set_ref is present: when the set row
-- exists it IS the record of load/sequence/RIR, and a stored copy beside it is
-- the second clock this codebase keeps paying for (038, 039, 040).  v_pain_event
-- COALESCEs the two so every reader sees one number either way.
--
-- Same discipline for the exercise name.  pain.py's F34 comment records that a
-- Korean/English mix for one movement SPLITS the audit group and hides the
-- recurring pattern the audit exists to find -- and the owner's own 33 rows
-- contain both spellings for incline dumbbell press.  For a linked row that
-- defect is now structurally gone: v_pain_event.exercise_name reads
-- workout_sets.exercise_name, the canonical slot name, and falls back to the
-- free label only when there is nothing to fall back FROM.
--
-- ==========================================================================
-- THE PROMOTION PATH.  WHAT SURFACES, AND WHAT REFUSES TO WRITE.
--
-- "Events accumulate into a standing constraint" is the natural flow and today
-- there is no path for it -- a human has to restate it in a consult.  This
-- migration builds the SURFACING half and deliberately not the writing half:
--
--     v_pain_promotion_candidate -> "이 부위에 N주 안에 M건, 최대 통증 P"
--     + the exact ledger_constraint key those events would land under
--     + whether such a row ALREADY exists, and its status.
--
-- It does NOT insert.  No trigger, no auto-promote, no "helpful" upsert, and
-- this migration ships NO ledger_constraint write verb either.  A standing
-- constraint is SAFETY material: it silently reshapes every future program
-- (workout-program reads injury_* to pick substitutions and depth caps), so an
-- automatic row would be the machine writing its own safety licence.  The
-- owner confirms, in a consult, in his own words -- exactly as the existing
-- rows record they were made (source='interview', source='chat').
--
-- THE PROMOTION AXIS IS `site`, AND IT IS NEW.
-- pain.py had no body-site column at all: it recorded the EXERCISE and the
-- SIDE, never the joint.  An event that does not say WHERE it hurt can never
-- become injury_knee no matter how many times it repeats -- which is precisely
-- why 33 events produced zero constraint rows in seven weeks.  `site` is the
-- column that was missing.
--
-- site is FREE TEXT with an index, NOT a CHECK and NOT a token table.
-- DATA-MODEL.md's rule names this exact vocabulary: "Open vocab (muscle / grip
-- / INJURY / brand / area) -> open tag registry, never a CHECK."  And the
-- promotion TARGET settles it -- ledger_constraint.key is itself free text with
-- no roster, so a roster on the source side would be a roster the target cannot
-- honour, and a body part the owner has never hurt before would be rejected at
-- the moment he is trying to report it.  The naming contract (ascii
-- snake_case, matching the `injury_<site>` suffix) is enforced where enforcement
-- belongs -- `pain-add` normalises -- which is the 034/SDK write-layer idiom.
--
-- KNOWN NAMING SEAM, STATED RATHER THAN PAPERED OVER.  The owner's standing
-- row is `injury_foot`; his pre-session check-ins call the same complaint
-- `plantar_fascia`.  The view reports the DERIVED key and an EXACT-match lookup
-- only; for that pair it will honestly report "no standing constraint" beside
-- three events.  No fuzzy matcher is shipped -- a matcher that guessed foot ==
-- plantar_fascia would also guess wrong somewhere it matters, and this is the
-- one case per body part where a human is already in the loop.  Resolving the
-- two names is a consult act, and the surface is what makes it visible at all.
--
-- THE GROUPING UNIT IS THE SITE, NOT (SITE, SIDE).  This was got wrong once
-- while building 047 and the owner's own data caught it, so it is written down.
-- The incumbent keys mix unsided (`injury_<site>`) and sided
-- (`injury_<site>_<side>`) forms -- so WHETHER a body part is
-- tracked per-side is the owner's per-part decision and is NOT derivable from
-- an event.  Grouping by (site, side) split one site's events into four keys
-- (right, left, and two unsided spellings), three of
-- which "have no standing constraint" -- a fabricated false negative on the
-- single row the #1 SAFETY finding is about.  The view therefore groups by
-- SITE, reports side as a BREAKDOWN, and looks the constraint up across the
-- whole `injury_<site>%` FAMILY -- the same prefix-family idiom 033 uses for
-- `e1rm_*` / `max_reps_*`, and the estate's own documented naming convention,
-- not a fuzzy match.
--
-- RECURRENCE IS THE SIGNAL; `status` IS RESPONSE BOOKKEEPING.  The windowed
-- counts are over ALL events, not open ones.  `resolve` in this lane means "a
-- guardrail was attached", not "the joint is fine" -- all 33 carried events are
-- resolved and every one of them is evidence.  Counting only open events would
-- give a promotion surface that can never fire on real history.  open_events
-- stays as its own column because "still unanswered" is a different question.
--
-- ==========================================================================
-- WHAT THIS MIGRATION DOES NOT DO
--   * NO data carry.  The 33 events live in a file outside this database
--     (the retired Warg tree).  SQL cannot reach it without a hardcoded ATTACH
--     path, and a migration that fails on a fresh mint because an archive is
--     missing is a migration that cannot be applied.  The carry is
--     database/pain_event_carry.py -- idempotent, re-runnable, --dry-run by
--     default.  A fresh instance runs the migration and is born with an EMPTY
--     pain_event, which is correct: it is the owner's history, not kit baseline.
--   * NO ledger_constraint row, of any kind.  See above.
--   * NO body-site token table and NO seed.  See above.
--   * NO retirement of pain_events.db.  The archive is READ-ONLY, the owner's
--     only copy, and the carry never opens it for write.
--
-- ==========================================================================
-- GUARD.  Both-sided, 037/040/044/045/046 discipline: refused unless
-- user_version is EXACTLY 46, BEFORE any transaction or write -- exit != 0,
-- database untouched (a re-apply refused; a skipped 021~046 chain refused).
.bail on
.timeout 5000
CREATE TEMP TABLE _guard_047(seen_user_version);
CREATE TEMP TRIGGER _guard_047_refuse BEFORE INSERT ON _guard_047 BEGIN
  SELECT RAISE(FAIL, 'migration 047 refused: this database is not at user_version 46 -- either 047 was already applied (>= 47) or the 021~046 chain has not run (< 46). Nothing changed');
END;
INSERT INTO _guard_047
  SELECT user_version FROM pragma_user_version WHERE user_version <> 46;
DROP TABLE temp._guard_047;
BEGIN IMMEDIATE;

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
    recorded_at    TEXT NOT NULL DEFAULT (datetime('now','localtime')),

    CHECK (owning_agent IN ('ag','warg','user')),
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
GROUP BY v.owning_agent, v.site, v.constraint_family_key;

-- Lockstep stamp.  MUST be the last statement before COMMIT: if anything above
-- failed, .bail aborted this run, the transaction rolled back, and user_version
-- stays 46.
PRAGMA user_version = 47;
COMMIT;
