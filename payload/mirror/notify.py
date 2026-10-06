# -*- coding: utf-8 -*-
"""
DGN-180 notification interface (grill-3 finding 8 / D180-2).
SANDBOX MODE: writes to notify_outbox table instead of pushing to Telegram.
Live cutover swaps deliver() to the routines/push.sh pattern; the interface
and the user-facing templates below are FINAL.

Templates are Korean by requirement (user-facing message content, not code).
Code itself is English/ASCII.
"""

from datetime import datetime, timezone

import mirror_i18n

# Final user-facing templates. The ko literals below are the FALLBACK (and the
# byte-identical zero-delta default when no locale file carries the key). At
# send time each kind is resolved through mirror_i18n by AGENT_LANG under the
# i18n key 'mirror.<kind>' (config/i18n/<lang>.json); the ko value here is used
# verbatim when the key/file is absent (DGN-268 S4).
TEMPLATES = {
    "outbox_exhausted": (
        u"동기화 보류: '{title}' 항목이 "
        u"반복 실패로 대기 상태입니다. "
        u"확인이 필요합니다."),
    "circuit_breaker": (
        u"캘린더에서 취소 {count}건이 "
        u"한꺼번에 감지되어 자동 반영을 "
        u"중단했습니다. 캘린더 상태 "
        u"확인이 필요합니다."),
    "inbound_cancel": (
        u"캘린더에서 '{title}' 일정이 "
        u"삭제되어 취소 처리했습니다. "
        u"되돌리시려면 알려주세요."),
    "task_deleted": (
        u"할 일 '{title}' 항목이 삭제되어 "
        u"취소 처리했습니다. 되돌리시려면 "
        u"알려주세요."),
    "inbound_adopted": (
        u"캘린더에 직접 등록하신 '{title}' "
        u"일정을 가져와 등록했습니다."),
    "recurring_skipped": (
        u"반복 일정 '{title}'은 아직 자동 "
        u"연동 대상이 아니라 캘린더에만 "
        u"둡니다. (반복 일정 지원 예정)"),
    "outbox_exhausted_agg": (
        u"동기화 보류 {count}건: 반복 실패로 "
        u"대기 중인 항목이 여러 건입니다. "
        u"확인이 필요합니다."),
    "overlap_notice": (
        u"'{title}' 일정을 적용한 자리에 "
        u"기존 일정과 겹침이 있습니다: "
        u"{detail}"),
    "repeated_failure": (
        u"동기화 실패 반복: '{title}' 항목이 "
        u"재시도 한도에 도달했습니다. "
        u"수동 확인이 필요합니다."),
    # 2026-08-25 inbound presence lane: an edit the one-presence-at-a-time
    # invariant cannot accept is REFUSED, the canonical block is re-pushed --
    # and the owner is told. The old behaviour dropped the edit and re-pushed
    # in total silence, which is how three calendar edits over two days
    # vanished with no signal at all.
    "presence_edit_refused": (
        u"캘린더에서 '{title}' 시간을 고치신 걸 "
        u"반영하지 못했습니다: {reason} "
        u"원래 시간으로 되돌려 두었습니다. "
        u"말씀해 주시면 제가 바꿔드릴게요."),
    # D3-2/D4 (2026-08-25): the NON-presence inbound lane refuses terminally
    # too -- a dead (settled) row, or a derived movement/buffer block that only
    # travel-move may move. Same three-part contract as presence: audit line,
    # canonical re-push, and a MESSAGE. A silent re-push over the owner is the
    # defect this exists to close.
    "schedule_edit_refused": (
        u"캘린더에서 '{title}' 시간을 고치신 걸 "
        u"반영하지 못했습니다: {reason} "
        u"원래 시간으로 되돌려 두었습니다. "
        u"말씀해 주시면 제가 바꿔드릴게요."),
    # 2026-09-09 instrument honesty: {mismatch} no longer includes the
    # unrepairable-past class (past rows DGN-840 forbids re-pushing whose
    # only delta is our own render having evolved after their last push).
    # That count rides its own clause -- separated, not silenced. An instance
    # i18n override without the {unrepairable} placeholder keeps formatting
    # (str.format ignores unused kwargs); it just omits the clause.
    "reconcile_report": (
        u"주간 동기화 점검: 총 {checked}건 "
        u"점검, 누락 {missing}건 재전송, "
        u"불일치 {mismatch}건, 관리 밖 항목 "
        u"{orphan}건, 삭제 감지 보류 "
        u"{deleted_held}건, 고칠 수 없는 과거 "
        u"차이 {unrepairable}건(재전송 범위 밖). "
        u"{verdict}"),
    # 2026-10-02 (ticket 2026-09-26 calendar-sync-report-routing): the
    # reconcile report now goes out ONLY when a finding is NEW against the
    # stored known-set, and names each new finding ({items}, built by
    # reconcile._finding_label). A separate kind from reconcile_report on
    # purpose: installed i18n bundles override 'mirror.reconcile_report' with
    # a counts-only string, which would drop the names silently. Machine
    # report (MACHINE_REPORT_KINDS): kit operator, never the owner.
    "reconcile_new_findings": (
        u"\ub3d9\uae30\ud654 \uc810\uac80: \uc0c8\ub85c \ubc1c\uacac\ub41c "
        u"\uc774\uc0c1 {count}\uac74 -- {items}. "
        u"\uc804\uccb4 \uc810\uac80 {checked}\uac74, \ubbf8\ud574\uacb0 "
        u"{open}\uac74(\ubd88\uc77c\uce58 {mismatch}, \uad00\ub9ac \ubc16 "
        u"{orphan}, \ub204\ub77d {missing}), "
        u"\uc790\ub3d9 \ud574\uc18c {resolved}\uac74, \uad00\ub9ac \ubc16 "
        u"\uc815\ub9ac {cleaned}\uac74, \uace0\uce60 \uc218 \uc5c6\ub294 "
        u"\uacfc\uac70 \ucc28\uc774 {unrepairable}\uac74. "
        u"\uc774\ubbf8 \uc54c\ub9b0 \ud56d\ubaa9\uc740 \ub2e4\uc2dc "
        u"\uc54c\ub9ac\uc9c0 \uc54a\uc2b5\ub2c8\ub2e4."),
    # 2026-10-02 (ticket 2026-09-23 kitdb-index-corruption-mirror-down): the
    # mirror logged cycle_step_persistent 22 times over 1h27m and nobody was
    # told. Operator-only (OPERATOR_ONLY_KINDS below): the owner cannot act on
    # a malformed DB, the steward can. One line per incident + one recovery
    # line, raised by adapter.persistent_failure_watch. Unicode-escaped so the
    # source stays ASCII.
    "mirror_persistent_failure": (
        u"\uce98\ub9b0\ub354 \ub3d9\uae30\ud654\uac00 {cycles}\ud68c "
        u"\uc5f0\uc18d \uc2e4\ud328\ud558\uace0 \uc788\uc2b5\ub2c8\ub2e4"
        u"(\uccab \uc2e4\ud328 \ud6c4 \uc57d {minutes}\ubd84). "
        u"\ub2e8\uacc4: {steps}. \uc624\ub958: {error}. "
        u"\ubcf5\uad6c\ub418\uba74 \ud55c \ubc88 \ub354 "
        u"\uc54c\ub9bd\ub2c8\ub2e4."),
    "mirror_persistent_recovered": (
        u"\uce98\ub9b0\ub354 \ub3d9\uae30\ud654\uac00 "
        u"\ubcf5\uad6c\ub418\uc5c8\uc2b5\ub2c8\ub2e4. "
        u"\uc5f0\uc18d \uc2e4\ud328 {cycles}\ud68c, \uc57d {minutes}\ubd84 "
        u"\ub9cc\uc785\ub2c8\ub2e4."),
}


# User-facing {reason} fragments for presence_edit_refused, keyed by the
# terminal verb result. i18n-resolved like the templates above.
PRESENCE_REFUSAL_REASONS = {
    "overlap_reject": u"그 시간대에 이미 다른 재실 일정이 있습니다.",
    "rejected_untimed": u"재실 블록은 종일 일정으로 바꿀 수 없습니다.",
    "rejected_kind_change": u"재실 블록은 종일 일정으로 바꿀 수 없습니다.",
    "rejected_unbounded": u"시작 시각과 종료 시각이 모두 있어야 합니다.",
    "rejected_not_presence": u"블록 종류가 맞지 않습니다.",
    "rejected_settled": u"이미 취소된 일정이라 반영할 수 없습니다.",
}
_PRESENCE_REFUSAL_FALLBACK = u"반영할 수 없는 형태의 수정입니다."


# User-facing {reason} fragments for schedule_edit_refused (the non-presence
# inbound lane), keyed by the terminal verb result. i18n-resolved exactly like
# the presence fragments above.
SCHEDULE_REFUSAL_REASONS = {
    # Kept short: the template already closes with the offer to do it for
    # him, so the fragment only has to explain WHY it bounced.
    "rejected_travel_block": (
        u"이동·버퍼 블록은 약속 시간에 맞춰 자동으로 "
        u"계산되는 부분이라 캘린더에서 직접 옮길 수 "
        u"없습니다."),
    "rejected_settled": u"이미 취소된 일정이라 반영할 수 없습니다.",
    "rejected_untimed": u"이 일정은 종일 일정으로 바꿀 수 없습니다.",
    "rejected_presence": u"재실 블록은 이 경로로 옮길 수 없습니다.",
}
_SCHEDULE_REFUSAL_FALLBACK = u"반영할 수 없는 형태의 수정입니다."


def schedule_refusal_reason(result):
    """Resolve the user-facing reason fragment for a terminal inbound-schedule
    refusal on a non-presence row. Unknown results fall back to a generic
    (never empty) sentence -- a refusal must never reach the owner blank."""
    key = "mirror.schedule_refusal.%s" % result
    fallback = SCHEDULE_REFUSAL_REASONS.get(result,
                                            _SCHEDULE_REFUSAL_FALLBACK)
    return mirror_i18n.t(key, fallback)


def presence_refusal_reason(result):
    """Resolve the user-facing reason fragment for a terminal inbound-presence
    refusal. Unknown results fall back to a generic (never empty) sentence --
    a refusal must never reach the owner as a blank explanation."""
    key = "mirror.presence_refusal.%s" % result
    fallback = PRESENCE_REFUSAL_REASONS.get(result,
                                            _PRESENCE_REFUSAL_FALLBACK)
    return mirror_i18n.t(key, fallback)


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def notify(state_conn, kind, event_ulid=None, dedup=True, **kwargs):
    """Queue a user notification. SANDBOX: insert into notify_outbox
    (delivered=0). Dedup: skip when an undelivered row for the same
    (kind, event_ulid) already exists (poll-repeat spam guard).
    Returns True if queued, False if deduped."""
    if kind not in TEMPLATES:
        raise ValueError("unknown notify kind: %r" % kind)
    # i18n resolve: locale bundle by AGENT_LANG, else the ko literal fallback
    # (zero-delta when no locale file carries 'mirror.<kind>').
    message = mirror_i18n.t("mirror.%s" % kind, TEMPLATES[kind], **kwargs)
    if dedup:
        row = state_conn.execute(
            "SELECT 1 FROM notify_outbox WHERE kind=? AND delivered=0 AND "
            "COALESCE(event_ulid,'')=COALESCE(?,'')",
            (kind, event_ulid)).fetchone()
        if row:
            return False
    state_conn.execute(
        "INSERT INTO notify_outbox(ts, kind, event_ulid, message, delivered) "
        "VALUES(?,?,?,?,0)", (_now(), kind, event_ulid, message))
    state_conn.commit()
    return True


# Machine reports (the kit's own status/ops findings; the owner cannot act on
# them). ONE routing rule for all of them -- ticket 2026-09-26
# calendar-sync-report-routing, owner ruling 2026-09-26 20:24: the operator
# of the kit's machine reports is the kit steward (its operator), not the owner and
# not the framework steward. Destination order (push_sh_deliver):
#   1. kit conf MIRROR_OPS_ENV_FILE (the kit's own operator channel), when
#      set and the file exists: push.sh --env <file>.
#   2. else the framework audience axis: push.sh --audience operator
#      --no-owner-fallback (DGN-1585/DGN-1808, STEWARD_ENV_FILE).
#   3. else NOTHING is sent. NEVER the owner chat. push.sh exits 9 for
#      "operator unresolved"; that is consumed as handled-by-design (one
#      mirror_log 'notify_unrouted' line + one stderr line) because retrying
#      cannot resolve a config gap, and a stuck row would hold every later
#      owner notice hostage in deliver_pending's id order.
# The old OPS_KINDS persona fallback (reconcile_report landing in the owner
# chat when MIRROR_OPS_ENV_FILE was unset) is the defect this closes.
OPS_KINDS = frozenset(["reconcile_report", "reconcile_new_findings"])
OPERATOR_ONLY_KINDS = frozenset(["mirror_persistent_failure",
                                 "mirror_persistent_recovered"])
MACHINE_REPORT_KINDS = OPS_KINDS | OPERATOR_ONLY_KINDS
PUSH_RC_OPERATOR_UNRESOLVED = 9
# push_sh_deliver's return value for a machine report that reached nobody.
UNROUTED = "unrouted"

# Ops prefix: i18n key mirror.ops.prefix parameterized with the agent label.
# The label SOURCE is the S1 conf key DOGANY_AGENT_LABEL (R2-2: the named
# parameter source -- no other conf key resolves the label). The ko fallback
# below is unicode-escaped (source stays ASCII, A7):
# "[<label> calendar sync] ".
_OPS_PREFIX_FALLBACK = u"[{label} \uce98\ub9b0\ub354 \ub3d9\uae30\ud654] "


def _notify_conf():
    """Minimal shell-style conf reader (DGN-364 2.8: must stay
    circular-import-free -- adapter imports notify, so notify never imports
    adapter). Same sources + precedence as adapter._load_conf:
    ../config/kit_life.conf first, then ../.instance.conf; earlier source
    wins; missing files are no-ops."""
    import os
    conf = {}
    base = os.path.dirname(os.path.abspath(__file__))
    for parts in (("..", "config", "kit_life.conf"), ("..", ".instance.conf")):
        path = os.path.normpath(os.path.join(base, *parts))
        try:
            with open(path, "r") as fh:
                lines = fh.readlines()
        except (OSError, IOError):
            continue
        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            if not key or key in conf:
                continue
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                val = val[1:-1]
            conf[key] = val
    return conf


def _ops_prefix(conf):
    """Resolved ops prefix: i18n mirror.ops.prefix with {label} from the
    DOGANY_AGENT_LABEL conf value ('agent' keeps the string well-formed on a
    bare checkout)."""
    label = conf.get("DOGANY_AGENT_LABEL") or "agent"
    return mirror_i18n.t("mirror.ops.prefix", _OPS_PREFIX_FALLBACK,
                         label=label)


def push_sh_deliver(message, kind=None):
    """PRODUCTION delivery (patch 03): route through the instance's own
    push.sh (self-locating: mirror/ -> ../routines/push.sh). Raises on a
    non-zero exit so the row stays undelivered and retries next cycle.
    Machine-report kinds (MACHINE_REPORT_KINDS) follow the destination order
    in the comment on that set, carry the agent-labeled ops prefix, and never
    reach the owner chat; when no operator channel resolves they return
    UNROUTED (nothing sent) instead of raising. Owner kinds return None."""
    import os
    import subprocess
    import sys
    push = os.path.normpath(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "routines", "push.sh"))
    cmd = [push, "--text", message]
    tolerated = (0,)
    if kind in MACHINE_REPORT_KINDS:
        conf = _notify_conf()
        text = _ops_prefix(conf) + message
        ops_env = conf.get("MIRROR_OPS_ENV_FILE") or ""
        if ops_env and os.path.isfile(ops_env):
            cmd = [push, "--env", ops_env, "--text", text]
        else:
            if ops_env:
                sys.stderr.write("[mirror notify] MIRROR_OPS_ENV_FILE not a "
                                 "file; trying the framework operator\n")
            cmd = [push, "--text", text,
                   "--audience", "operator", "--no-owner-fallback"]
            tolerated = (0, PUSH_RC_OPERATOR_UNRESOLVED)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if r.returncode not in tolerated:
        raise RuntimeError("push.sh exit %d: %s" % (r.returncode,
                                                    r.stderr[:200]))
    if r.returncode == PUSH_RC_OPERATOR_UNRESOLVED:
        sys.stderr.write("[mirror notify] %s: no operator channel "
                         "(MIRROR_OPS_ENV_FILE / STEWARD_ENV_FILE) -> not "
                         "sent\n" % kind)
        return UNROUTED
    return None


def deliver_pending(state_conn, deliver_fn=None):
    """Cutover seam: drain undelivered notifications through deliver_fn
    (live = routines/push.sh wrapper). SANDBOX: no-op unless a deliver_fn
    is supplied. DGN-306/DGN-364 2.8: the row's kind is passed to a 2-arg
    deliver_fn so ops kinds can route to the operator channel; a 1-arg
    deliver_fn stays backward compatible (sandbox unaffected)."""
    if deliver_fn is None:
        return 0
    import inspect
    try:
        multi_arg = len(inspect.signature(deliver_fn).parameters) >= 2
    except (TypeError, ValueError):
        multi_arg = False
    rows = state_conn.execute(
        "SELECT id, kind, event_ulid, message FROM notify_outbox "
        "WHERE delivered=0 ORDER BY id"
    ).fetchall()
    n = 0
    for r in rows:
        if multi_arg:
            res = deliver_fn(r["message"], r["kind"])
        else:
            res = deliver_fn(r["message"])
        if res == UNROUTED:
            # Machine report with no operator channel: handled, not sent.
            # Audit line so the gap is findable; the row is consumed below.
            state_conn.execute(
                "INSERT INTO mirror_log(ts, event_ulid, category, detail) "
                "VALUES(?,?,?,?)", (_now(), r["event_ulid"],
                                    "notify_unrouted", "kind=%s" % r["kind"]))
        # g10: commit PER ROW -- a failure at row k must not redeliver
        # rows 1..k-1 on the next cycle (duplicate flood guard).
        state_conn.execute(
            "UPDATE notify_outbox SET delivered=1 WHERE id=?", (r["id"],))
        state_conn.commit()
        n += 1
    return n
