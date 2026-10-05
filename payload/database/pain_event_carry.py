#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pain_event_carry.py -- the 047 data carry, as a documented one-shot.

WHAT IT CARRIES.  The owner's 33 pain / form-fault events (2026-07-25 ..
2026-09-11) out of the pain-triage skill's private store (`pain_events.db`) and
into `pain_event`, the table migration 047 creates.

WHY IT IS NOT INSIDE THE MIGRATION.  The source file lives OUTSIDE this
database, in a retired skill's private store.  SQL could only reach it through a
hardcoded ATTACH path, and a migration that fails on a fresh mint because somebody else's
archive is missing is a migration that cannot be applied.  047 therefore ships
the SHAPE; this script ships the HISTORY, and a fresh instance runs 047 alone
and is born with pain_event empty -- which is correct, because one person's pain
history is that person's data, not kit baseline.

IDEMPOTENT AND RE-RUNNABLE, by construction.  Every carried row gets a
DETERMINISTIC ulid derived from its source id (see `_carry_ulid`), and `ulid` is
UNIQUE, so the second run inserts nothing.  It never UPDATEs a carried row
either -- re-running cannot walk over an edit the owner made after the first
run.  The one exception is opt-in and narrow: `--relink` fills set_ref /
session_ref on rows that are still UNLINKED, for the case where the workout
history is restored after the events were carried.  It never re-points a link
that already exists.

READ-ONLY AT THE SOURCE.  The archive is the owner's only copy.  It is opened
through a `file:...?mode=ro` URI, never plain, so a typo cannot create a
journal beside it.

DRY RUN BY DEFAULT.  `--apply` is required to write.  There is no default value
for --db or --source: a carry that defaults to a live path is a carry that lands
on a live path by accident, and the source lives outside the kit, at a path only
the operator knows.

Usage:
  pain_event_carry.py --db <kit_life.db> --source <pain_events.db>
                      [--apply] [--relink]

Exit: 0 = carried (or dry-run completed); 1 = refused (wrong user_version,
missing table, unreadable source).
"""
import argparse
import hashlib
import os
import sqlite3
import sys
from datetime import datetime

REQUIRED_USER_VERSION = 47
OWNING_AGENT = "warg"

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


# ---------------------------------------------------------------------------
# THE SITE ASSIGNMENT -- the one judgement in this file, written out row by row.
#
# pain.py never had a body-site column (see 047's header): it recorded the
# EXERCISE and the SIDE, never the joint.  `site` is the promotion axis, so
# carrying 33 events with site NULL would carry the data and leave the defect.
#
# So the site is assigned here, per row, from what THAT ROW ITSELF says --
# never from a keyword rule run over prose, and never from the exercise name
# ("back_squat" does not mean knee; the owner's own #31 is a rack collision).
# Two grades, and the table records which:
#
#   'stated'   the event named the site in a STRUCTURED field -- the logger
#              wrote `exercise = 'pre-session-checkin_<site>'`, which is a
#              stored convention, not an interpretation.  6 rows.
#   'carried'  transcribed from the row's own note/guardrail text, read and
#              assigned one at a time.  The `why` string below quotes the words
#              that decided it, so the judgement is auditable instead of
#              asserted.  27 rows.
#
# Nothing downstream treats the two as equal: v_pain_promotion_candidate counts
# site_stated and site_carried in separate columns, and the owner -- who
# confirms every promotion anyway -- sees which he is being shown.
#
# GUARD.  Each entry carries a fingerprint of the source row it was written
# against (ts, exercise, side, pain).  If the source file has changed since,
# the row carries with site NULL and a warning, rather than stamping this
# judgement onto a row it was never made about.
# ---------------------------------------------------------------------------
SITE = {
    1:  ("toe",            "carried", "2026-07-25 10:14|발가락(세션전 보고)|R|2",
         "제목이 부위다 -- '발가락(세션전 보고)'"),
    2:  ("knee",           "carried", "2026-08-01 14:34|백스쿼트|R|5",
         "note: '백스쿼트에서 슬개건 부위 불편'"),
    3:  ("shoulder",       "carried", "2026-08-03 13:21|incline_dumbbell_press|R|2",
         "note: '기존 어깨 제약 키의 비급성 프로파일과 일치'"),
    4:  ("hip",            "stated",  "2026-08-06 05:20|pre-session-checkin_hip|both|1",
         "exercise = pre-session-checkin_hip"),
    5:  ("plantar_fascia", "stated",  "2026-08-06 05:20|pre-session-checkin_plantar_fascia|R|1",
         "exercise = pre-session-checkin_plantar_fascia"),
    6:  ("knee",           "stated",  "2026-08-06 05:20|pre-session-checkin_knee|R|2",
         "exercise = pre-session-checkin_knee"),
    7:  ("shoulder",       "carried", "2026-08-06 06:48|incline_barbell_press|R|1",
         "guardrail: '견갑 후인·하강 유지 큐'"),
    8:  ("shoulder",       "carried", "2026-08-06 06:54|incline_barbell_press|R|1",
         "같은 사건의 연속 (#7 -> #9), 같은 가드레일"),
    9:  ("shoulder",       "carried", "2026-08-06 07:00|incline_barbell_press|R|3",
         "guardrail: '딥스트레치 최소화 원칙(어깨 제약 키) 적용'"),
    10: ("plantar_fascia", "carried", "2026-08-07 06:02|세션전_컨디션체크|R|2",
         "note: '족저근 뻐근함 1-2/10 유지'"),
    11: ("hip",            "carried", "2026-08-07 06:02|세션전_컨디션체크|R|2",
         "note: '골반 바깥쪽뼈 세션전 리포트'"),
    12: ("hip",            "carried", "2026-08-07 06:13|밴드니아웃프라이머(K2)|R|2",
         "note: '골반 바깥쪽 경미 지속'"),
    13: ("knee",           "carried", "2026-08-07 07:29|bulgarian_split_squat|R|3",
         "note: '무릎 외측 새 부위, 사진 확인'"),
    14: ("knee",           "carried", "2026-08-07 07:34|bulgarian_split_squat|R|3",
         "note: '몸통 비틀림+무릎외측 통증의 근본원인'"),
    15: ("knee",           "carried", "2026-08-10 06:28|세션 시작 전 컨디션 체크|R|3",
         "note: '세션 전반 무릎 안정적'"),
    16: ("shoulder",       "carried", "2026-08-10 06:56|덤벨 플라이 (슬롯1 프라이밍)|R|0",
         "note: '통증 없는 벤치성 클릭' + fault=impingement (어깨 충돌)"),
    17: ("shoulder",       "carried", "2026-08-10 07:05|인클라인 덤벨 프레스|R|2",
         "guardrail: '인클라인 프레스 바닥 지점 스트레치 깊이 제한'"),
    18: ("knee",           "carried", "2026-08-10 07:40|브이스쿼트|R|1",
         "prog: '무릎 통증 프로파일에 니-아웃 트래킹 소견 추가'"),
    19: ("knee",           "stated",  "2026-08-11 06:21|pre-session-checkin_knee|both|2",
         "exercise = pre-session-checkin_knee"),
    20: ("lower_back",     "stated",  "2026-08-13 06:22|pre-session-checkin_lower_back|NA|2",
         "exercise = pre-session-checkin_lower_back"),
    21: ("plantar_fascia", "stated",  "2026-08-13 06:22|pre-session-checkin_plantar_fascia|R|1",
         "exercise = pre-session-checkin_plantar_fascia"),
    22: ("shoulder",       "carried", "2026-08-14 12:00|overhead_press|R|2",
         "guardrail: '그립 어깨너비 살짝 바깥 + 팔꿈치 손목 아래 정렬'"),
    23: ("knee",           "carried", "2026-08-14 12:27|back_squat|R|3",
         "note: '안정근 피로로 무릎 컨트롤 무너짐'"),
    24: ("knee",           "carried", "2026-08-16 14:45|bulgarian_split_squat|L|2",
         "guardrail: '앞무릎 니-아웃 + 내전근 동시 조임 큐'"),
    25: ("lower_back",     "carried", "2026-08-19 06:08|deadlift|NA|1",
         "note: '세트3 락오프 시 경미한 허리 사용감'"),
    26: ("shoulder",       "carried", "2026-08-19 07:06|face_pull|R|1",
         "note: '우측 어깨 경미한 불편'"),
    27: ("knee",           "carried", "2026-08-22 15:15|single_leg_extension|L|3",
         "note: '왼쪽 무릎 이상 없음' (8/22 왼무릎 건의 마감 확인)"),
    28: ("shoulder",       "carried", "2026-08-24 07:13|incline_dumbbell_press|R|2",
         "guardrail: '펙덱 프라이머 + 길항근(후면삼각근/광배)' -- #30과 한 건"),
    29: ("knee",           "carried", "2026-08-27 17:40|back_squat|R|2",
         "guardrail: '스쿼트 프라이머를 내전근 아이소메트릭 -> 힙 어덕션 머신으로'"
         " (내전근 프라이머는 무릎 레인의 조치다, #2/#33 참조)"),
    30: ("shoulder",       "carried", "2026-09-02 05:59|incline_dumbbell_press|R|2",
         "note: '익일 우측 어깨+광배 뻐근함'"),
    31: ("knee",           "carried", "2026-09-04 13:05|back_squat|NA|4",
         "note: '외부 충돌(랙)' + guardrail '무릎 상태만 다음 세션 재확인'"),
    32: ("shoulder",       "carried", "2026-09-09 08:14|chest_press_machine|R|3",
         "guardrail: '체스트프레스 그립 위치·팔꿈치 각도 확인'"),
    33: ("knee",           "carried", "2026-09-11 06:47|bodyweight_squat|R|1",
         "guardrail: '백스쿼트/맨몸스쿼트 시 내전근 활성화 큐 유지'"),
}


def _encode(value, length):
    out = []
    for _ in range(length):
        out.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(out))


def _carry_ulid(src_id, observed_at):
    """A real 26-char Crockford ULID that is the SAME every run.

    Timestamp half: the event's own stamp, read as if UTC.  That is a SORT KEY,
    not a claim about the clock -- the stamp is localtime and this file will not
    mint a fourth one (038).  Random half: the top 80 bits of a sha256 over a
    namespaced source key, so it is stable across machines and runs.
    Precedent for a deterministic ulid: the 'x-spend-<id>' idiom (039).
    """
    dt = datetime.strptime(observed_at[:16], "%Y-%m-%d %H:%M")
    ms = int((dt - datetime(1970, 1, 1)).total_seconds() * 1000)
    digest = hashlib.sha256(
        ("kit_life/pain_event/carry/%s/%d" % (OWNING_AGENT, src_id)).encode()
    ).digest()
    tail = int.from_bytes(digest[:10], "big")
    return _encode(ms & ((1 << 48) - 1), 10) + _encode(tail, 16)


def _fingerprint(row):
    side = row["side"] if row["side"] is not None else ""
    return "%s|%s|%s|%d" % (row["ts"], row["exercise"], side, row["pain"])


# ---------------------------------------------------------------------------
# THE LINK RESOLVER.  047's whole reason for existing is the real join, so the
# carry has to actually attempt it rather than land 33 date-joined rows.
#
# Tiers, strictest first.  A tier is taken only when it yields EXACTLY ONE
# candidate; two candidates is ambiguity, and ambiguity resolves to no link.
#   T1  same date + set_seq + weight + exercise_name (exact)
#   T2  same date + set_seq + weight         -- this is the tier that rescues
#       the rows logged under a Korean label ("브이스쿼트" 125kg set2), which no
#       string join could ever have matched
#   T3  session only: the one workout on that date that HAS sets.  Dates with a
#       cardio row beside the weights row are common in this history, so
#       "the workout on that date" is resolved by WHICH ONE HAS SETS, and a
#       date with two set-bearing workouts stays unlinked rather than guessing.
# ---------------------------------------------------------------------------
def _resolve_link(kdb, ev):
    date = ev["ts"][:10]
    sess_rows = kdb.execute(
        "SELECT w.id FROM workouts w WHERE w.date = ? AND w.owning_agent = ? "
        "AND EXISTS (SELECT 1 FROM workout_sets s WHERE s.session_id = w.id) "
        "ORDER BY w.id", (date, OWNING_AGENT)).fetchall()

    if ev["set_no"] is not None and ev["load_kg"] is not None:
        for extra, args in (
            (" AND s.exercise_name = ?", (ev["exercise"],)),
            ("", ()),
        ):
            cand = kdb.execute(
                "SELECT s.id, s.session_id FROM workout_sets s "
                "JOIN workouts w ON w.id = s.session_id "
                "WHERE w.date = ? AND w.owning_agent = ? "
                "AND s.set_seq = ? AND s.weight_kg = ?" + extra,
                (date, OWNING_AGENT, ev["set_no"], ev["load_kg"]) + args
            ).fetchall()
            if len(cand) == 1:
                return "set", cand[0][1], cand[0][0]

    if len(sess_rows) == 1:
        return "session", sess_rows[0][0], None
    return "none", None, None


def _open_source(path):
    if not os.path.exists(path):
        print("carry refused: source not found: %s" % path, file=sys.stderr)
        sys.exit(1)
    uri = "file:%s?mode=ro" % os.path.abspath(path)
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _open_kit(path):
    if not os.path.exists(path):
        print("carry refused: kit DB not found: %s" % path, file=sys.stderr)
        sys.exit(1)
    conn = sqlite3.connect(path)
    v = conn.execute("PRAGMA user_version;").fetchone()[0]
    if v != REQUIRED_USER_VERSION:
        print("carry refused: %s is at user_version %d, need exactly %d "
              "(run migrations/047_pain_event.sql first). Nothing changed."
              % (path, v, REQUIRED_USER_VERSION), file=sys.stderr)
        sys.exit(1)
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                        "AND name='pain_event'").fetchone():
        print("carry refused: pain_event does not exist in %s" % path,
              file=sys.stderr)
        sys.exit(1)
    return conn


def carry(args):
    src = _open_source(args.source)
    kdb = _open_kit(args.db)
    kdb.execute("PRAGMA foreign_keys = ON;")

    events = src.execute("SELECT * FROM pain_events ORDER BY id").fetchall()
    rows, drift, stats = [], [], {"set": 0, "session": 0, "none": 0}

    for ev in events:
        sid = ev["id"]
        site = origin = None
        entry = SITE.get(sid)
        if entry is not None:
            want_site, want_origin, want_fp, _why = entry
            if _fingerprint(ev) == want_fp:
                site, origin = want_site, want_origin
            else:
                drift.append((sid, want_fp, _fingerprint(ev)))
        elif SITE:
            drift.append((sid, "(no entry)", _fingerprint(ev)))

        grade, sess_ref, set_ref = _resolve_link(kdb, ev)
        stats[grade] += 1
        rows.append({
            "ulid": _carry_ulid(sid, ev["ts"]),
            "observed_at": ev["ts"],
            "session_ref": sess_ref,
            "set_ref": set_ref,
            "_grade": grade,
            "site": site,
            "site_origin": origin,
            "side": ev["side"],
            "pain": ev["pain"],
            "exercise_label": ev["exercise"],
            # one source for one fact: when the set row is attached it IS the
            # record of load / sequence / RIR, and the table's CHECK enforces it.
            "load_kg": None if set_ref else ev["load_kg"],
            "set_no": None if set_ref else ev["set_no"],
            "rir": None if set_ref else ev["rir"],
            "rom_phase": ev["rom_phase"],
            "fault": ev["fault"],
            "action": ev["action"],
            "session_label": ev["session"],
            "note": ev["note"],
            "guardrail": ev["guardrail"],
            "program_change": ev["program_change"],
            "status": ev["status"],
        })

    print("source      : %s (%d events)" % (args.source, len(events)))
    print("target      : %s" % args.db)
    print("link grades : set=%d  session=%d  none=%d"
          % (stats["set"], stats["session"], stats["none"]))
    sited = sum(1 for r in rows if r["site"])
    print("site        : %d sited (%d stated / %d carried), %d unsited"
          % (sited,
             sum(1 for r in rows if r["site_origin"] == "stated"),
             sum(1 for r in rows if r["site_origin"] == "carried"),
             len(rows) - sited))
    for sid, want, got in drift:
        print("  WARN #%d source row changed since the site table was written "
              "-> carried with site NULL\n       expected %s\n       got      %s"
              % (sid, want, got), file=sys.stderr)

    if not args.apply:
        print("\nDRY RUN -- nothing written. Re-run with --apply.")
        if args.relink:
            print("(--relink also had no effect in a dry run.)")
        return 0

    # link_grade is DERIVED by v_pain_event, never stored (047) -- `_grade` is
    # this script's own bookkeeping and must not reach the INSERT.
    data_cols = [c for c in rows[0].keys() if not c.startswith("_")]
    cols = data_cols + ["owning_agent", "source", "recorded_at"]
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sql = ("INSERT OR IGNORE INTO pain_event (%s) VALUES (%s)"
           % (",".join(cols), ",".join("?" * len(cols))))
    inserted = 0
    with kdb:
        for r in rows:
            vals = [r[c] for c in data_cols] + [
                OWNING_AGENT, "carry-047", stamp]
            cur = kdb.execute(sql, vals)
            inserted += cur.rowcount

        relinked = 0
        if args.relink:
            # Narrow on purpose: fill a link that is MISSING, never re-point one
            # that exists.  This is the "workout history was restored after the
            # events were carried" case and nothing else.
            for r in rows:
                if r["_grade"] == "none":
                    continue
                sql = ("UPDATE pain_event SET session_ref=?, set_ref=?, "
                       "load_kg=NULL, set_no=NULL, rir=NULL "
                       if r["_grade"] == "set" else
                       "UPDATE pain_event SET session_ref=?, set_ref=? ")
                cur = kdb.execute(
                    sql + "WHERE ulid=? AND session_ref IS NULL "
                          "AND set_ref IS NULL",
                    (r["session_ref"], r["set_ref"], r["ulid"]))
                relinked += cur.rowcount

    print("\ninserted    : %d  (already present: %d)"
          % (inserted, len(rows) - inserted))
    if args.relink:
        print("relinked    : %d previously-unlinked rows" % relinked)
    total = kdb.execute("SELECT COUNT(*) FROM pain_event").fetchone()[0]
    print("pain_event  : %d rows" % total)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="pain_event_carry.py")
    p.add_argument("--db", required=True,
                   help="target kit_life.db (NO default: a carry that defaults "
                        "to a live path lands on a live path by accident)")
    p.add_argument("--source", required=True,
                   help="pain_events.db to read (opened read-only; NO default: "
                        "it is the owner's only copy and lives outside the kit)")
    p.add_argument("--apply", action="store_true",
                   help="actually write (default: dry run)")
    p.add_argument("--relink", action="store_true",
                   help="also fill set_ref/session_ref on rows carried earlier "
                        "while still UNLINKED; never re-points an existing link")
    return carry(p.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
