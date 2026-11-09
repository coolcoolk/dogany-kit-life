"""Wire contracts for WP3. Protocols describe required M1/M2, not installed code."""
import re
from typing import Protocol
from .schema import *
from .commands import CommitReceipt, DELIVERY_STATES, OUTBOX_ID
from .trainer import TrainerDecision

SURFACE_KINDS = ("table", "trainer", "pin", "end_workout", "end_fatigue", "end_calories")
PUBLISH_STAGES = ("retire_markup", "neutralize_trainer", "edit_table", "edit_trainer", "attach_keyboard", "acknowledged")
JOURNAL_STATES = ("requested", "acknowledged", "unchanged", "rejected", "failed", "uncertain", "quarantined", "reconciled")
CALLBACK_STATUSES = ("ACCEPTED", "MOVED", "INVALID", "UNAVAILABLE", "DELIVERY_PENDING")
KEYBOARD = obj({"action_token": TOKEN, "input_stage": enum("load", "reps", "rir", "done", "setup", "stop", "choice"),
                "rows": array(array(obj({"label": STR, "callback_data": STR}), 1), 1)})
TIMER = obj({"deadline_at": INT, "epoch": INT, "kind": enum("rest", "activity"), "remaining_ms": INT})


class CallbackAcknowledgement(Contract):
    SCHEMA = obj({"schema": enum(2), "session_id": POS,
                  "status": enum(*CALLBACK_STATUSES), "receipt": nullable(CommitReceipt.SCHEMA),
                  "current_revision": INT, "diagnostic_key": nullable(STR),
                  "model_fallback": enum(False)})

    @classmethod
    def check(cls, value):
        accepted = value["status"] in ("ACCEPTED", "DELIVERY_PENDING")
        if accepted != (value["receipt"] is not None):
            raise ValidationError("callback status/receipt mismatch")
        if value["receipt"] is not None:
            receipt = CommitReceipt(value["receipt"])
            if receipt["session_id"] != value["session_id"]:
                raise ValidationError("callback receipt session mismatch")
            if receipt["revision"] > value["current_revision"]:
                raise ValidationError("callback receipt ahead of current state")


class TrainerApproval(Contract):
    SCHEMA = obj({"id": STR, "session_id": POS, "base_revision": INT, "view_digest": DIGEST,
                  "decision": TrainerDecision.SCHEMA, "cue_ast": TrainerDecision.SCHEMA["properties"]["speech"],
                  "dependency_digest": DIGEST, "policy_bundle_id": STR, "catalog_version": POS,
                  "expires_epoch": INT, "status": enum("prepared", "bound", "invalidated", "expired")})

    @classmethod
    def check(cls, value):
        decision = TrainerDecision(value["decision"])
        for key in ("session_id", "base_revision", "view_digest", "dependency_digest"):
            if decision[key] != value[key]:
                raise ValidationError("approval/decision binding mismatch")
        if value["cue_ast"] != decision["speech"]:
            raise ValidationError("cue AST differs from approved decision")


class EndCard(Contract):
    SCHEMA = obj({"surface_kind": enum("end_workout", "end_fatigue", "end_calories"),
                  "body": STR, "delivery_key": STR,
                  "media": nullable(obj({"kind": enum("image"), "artifact_ref": STR,
                                          "artifact_digest": DIGEST, "mime_type": enum("image/png", "image/jpeg")}))})


# Owner 2026-10-15 06:32 (R-SKIP1): warm-up/prep is skipped only by chat. Owner 06:37 (R-STUB1):
# the earlier cue/guidance text stays when the set table moves; no "latest table is below" stub.
SKIP_BUTTON = re.compile(u"\uac74\ub108|\uc2a4\ud0b5|\ud328\uc2a4|skip", re.IGNORECASE)
PREP_WORDS = re.compile(u"\uc6dc\uc5c5|\uc6cc\ubc0d|\uc900\ube44|\ub7a8\ud504|warm|prep|ramp", re.IGNORECASE)
STUB_LINE = re.compile(u"\ucd5c\uc2e0\\s*\uc138\ud2b8\ud45c|\uc138\ud2b8\ud45c\ub294\\s*\uc544\ub798|\u2b07")


class Frame(Contract):
    SCHEMA = obj({"schema": enum(2), "session_id": POS, "revision": INT, "receipt_id": STR,
                  "view_digest": DIGEST, "session_epoch": INT, "rest_epoch": nullable(INT),
                  "sender": enum("publisher"), "frame_kind": enum("step", "answer", "closeout", "timer", "setup"),
                  "trainer_approval_id": nullable(STR), "table": STR, "trainer_text": STR,
                  "keyboard": nullable(KEYBOARD), "timer_projection": nullable(TIMER),
                  "surface_ids": mapping(STR), "delivery_key": STR, "phase": PHASE},
                 {"pin_text": nullable(STR), "end_cards": array(EndCard.SCHEMA)})

    @classmethod
    def check(cls, value):
        cards = value.get("end_cards", [])
        if len({card["surface_kind"] for card in cards}) != len(cards):
            raise ValidationError("duplicate end-card surface")
        if cards and value["frame_kind"] != "closeout":
            raise ValidationError("end cards require closeout frame")
        if set(value["surface_ids"]) - set(SURFACE_KINDS):
            raise ValidationError("unknown surface")
        if value["phase"] == "closed" and (value["keyboard"] is not None or value["timer_projection"] is not None):
            raise ValidationError("closed frame cannot retain actions/timer")
        timer = value["timer_projection"]
        if timer and timer["epoch"] != value["rest_epoch"]:
            raise ValidationError("timer epoch mismatch")
        for text in (value["table"], value["trainer_text"]):
            if STUB_LINE.search(text):
                raise ValidationError("stub pointer line replaces earlier guidance")
        keyboard = value["keyboard"]
        if keyboard:
            for row in keyboard["rows"]:
                for button in row:
                    label = button["label"]
                    if SKIP_BUTTON.search(label) and (value["phase"] == "prep" or PREP_WORDS.search(label)):
                        raise ValidationError("warm-up/prep skip is chat-only, never a button")
            expected = "flow2|" + keyboard["action_token"]
            for row in keyboard["rows"]:
                for button in row:
                    data = button["callback_data"]
                    if len(data.encode("utf-8")) >= 64 or not data.startswith(expected + "|") or re.fullmatch(r"[A-Za-z0-9_-]{1,24}", data[len(expected)+1:]) is None:
                        raise ValidationError("invalid callback handle/payload")


class Surface(Contract):
    SCHEMA = obj({"session_id": POS, "surface_kind": enum(*SURFACE_KINDS), "chat_id": STR,
                  "message_id": STR, "published_revision": INT, "body_digest": DIGEST,
                  "markup_digest": DIGEST, "markup_state": enum("clear", "active", "unknown"),
                  "delivery_key": STR, "receipt_id": STR})

    @classmethod
    def check(cls, value):
        if value["surface_kind"] != "trainer" and value["markup_state"] == "active":
            raise ValidationError("only trainer surface owns action rail")


class OutboxIntent(Contract):
    SCHEMA = obj({"id": OUTBOX_ID, "session_epoch": INT, "rest_epoch": nullable(INT),
                  "view_digest": DIGEST, "trainer_approval_id": nullable(STR), "not_before_at": INT,
                  "state": enum(*DELIVERY_STATES), "stage": enum(*PUBLISH_STAGES),
                  "attempts": INT, "lease_fence": INT, "last_error": nullable(STR), "row_ids": array(POS)})


class DeliveryJournalEntry(Contract):
    SCHEMA = obj({"operation_id": STR, "session_id": POS, "command_id": STR, "revision": INT,
                  "frame_kind": enum("step", "answer", "closeout", "timer", "setup"),
                  "sender": enum("publisher"), "surface_kind": nullable(enum(*SURFACE_KINDS)),
                  "message_id": nullable(STR), "callback_digest": nullable(DIGEST),
                  "session_epoch": INT, "rest_epoch": nullable(INT),
                  "operation": enum("send", "edit", "delete", "clear_markup", "attach_keyboard", "model_rejection", "callback_validation", "reconcile"),
                  "request_digest": DIGEST, "state": enum(*JOURNAL_STATES),
                  "acknowledged_result": nullable(obj({"receipt_id": STR, "message_id": STR, "body_digest": DIGEST, "markup_digest": DIGEST})),
                  "latency_ms": INT, "error": nullable(STR), "artifact_digest": DIGEST,
                  "delivery_key": STR, "lease_fence": INT})

    @classmethod
    def check(cls, value):
        acknowledged = value["state"] in ("acknowledged", "unchanged", "reconciled")
        if acknowledged != (value["acknowledged_result"] is not None):
            raise ValidationError("journal state and acknowledgement disagree")


CAPABILITIES = obj({"kit_schema": POS, "frame_gate_api": POS, "scheduler_api": POS,
                    "edit_acknowledgement": enum(True), "delivery_receipt_reconciliation": enum(True),
                    "epoch_cancellation": enum(True), "silent_countdown": enum(True), "no_model_fallback": enum(True)})


class CallbackCapability(Contract):
    SCHEMA = obj({"schema": enum(2), "capability": enum("session_flow_v2"), "protocol": enum(2),
                  "callback_prefix": enum("flow2|"), "command_argv": array(STR, 2),
                  "FLOW_TAP_COMMAND": array(STR, 2), "compatibility_prefix": nullable(enum("flow|")),
                  "kit_schema_floor": POS, "frame_gate_api_version": POS, "scheduler_api_version": POS,
                  "required": CAPABILITIES})

    @classmethod
    def check(cls, value):
        argv = ["service/health/session-command", "tap"]
        if value["command_argv"] != argv or value["FLOW_TAP_COMMAND"] != argv:
            raise ValidationError("callback argv must name v2 tap entry")
        if value["kit_schema_floor"] < 2:
            raise ValidationError("v2 schema floor required")
        for actual, required in (("kit_schema_floor", "kit_schema"), ("frame_gate_api_version", "frame_gate_api"), ("scheduler_api_version", "scheduler_api")):
            if value[actual] != value["required"][required]:
                raise ValidationError("capability version declaration mismatch")


class CapabilityChallenge(Contract):
    SCHEMA = obj({"schema": enum(2), "kind": enum("session_flow_v2.challenge"), "nonce": TOKEN,
                  "no_mutation": enum(True), "required": CAPABILITIES})


class CapabilityReceipt(Contract):
    SCHEMA = obj({"schema": enum(2), "kind": enum("session_flow_v2.receipt"), "nonce": TOKEN,
                  "receipt_id": STR, "handler": enum("native_callback"), "no_mutation": enum(True),
                  "capabilities": CAPABILITIES, "config_digest": DIGEST, "installed_root": STR,
                  "artifacts": obj({"pack": DIGEST, "kit": DIGEST, "framework": DIGEST, "installer": DIGEST}),
                  "checked_at": INT})


def validate_capability_response(challenge, receipt):
    c, r = CapabilityChallenge(dict(challenge)), CapabilityReceipt(dict(receipt))
    if c["nonce"] != r["nonce"]:
        raise ValidationError("capability challenge nonce mismatch")
    for name, needed in c["required"].items():
        if r["capabilities"][name] < needed:
            raise ValidationError(f"missing capability {name}")
    return r


def validate_frame(frame, receipt, *, current_revision, session_epoch):
    """Binding helper; not M1 authorization or proof that text was compiled.

    The real gate must additionally authenticate publisher, verify approval and
    lease, drain older sends, and hold all model bytes before transport.
    """
    f, r = Frame(dict(frame)), CommitReceipt(dict(receipt))
    for key in ("session_id", "revision", "receipt_id", "view_digest"):
        if f[key] != r[key]:
            raise ValidationError(f"frame receipt mismatch: {key}")
    if f["revision"] != current_revision or f["session_epoch"] != session_epoch:
        raise ValidationError("stale frame")
    if f["frame_kind"] in ("step", "answer", "setup") and f["trainer_approval_id"] is None:
        raise ValidationError("trainer approval required")
    return f


class LiveSessionFrameGate(Protocol):
    def validate(self, frame: Frame, receipt: CommitReceipt, approval: TrainerApproval) -> Frame: ...
    def publish(self, frame: Frame, lease_fence: int) -> DeliveryJournalEntry: ...
    def reject_unframed(self, session_id: int, origin_turn_id: str, payload_digest: str) -> DeliveryJournalEntry: ...
    def fence(self, session_id: int, session_epoch: int) -> None: ...
    def reconcile(self, delivery_key: str) -> DeliveryJournalEntry: ...


class SessionPublisher(Protocol):
    def publish(self, intent: OutboxIntent) -> DeliveryJournalEntry: ...


class SessionScheduler(Protocol):
    def schedule(self, session_id: int, epoch: int, deadline_at: int, kind: str) -> None: ...
    def cancel(self, session_id: int, epoch: int) -> None: ...


class CallbackBridge(Protocol):
    def challenge(self, challenge: CapabilityChallenge) -> CapabilityReceipt: ...
    def tap(self, callback_data: str, member_id: str, chat_id: str, message_id: str) -> CallbackAcknowledgement: ...
