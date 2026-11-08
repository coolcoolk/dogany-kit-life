"""Frozen command shapes. Authorization/stage checks belong to the kit writer."""
from typing import Protocol
from .schema import (Contract, ValidationError, obj, enum, array, nullable,
                     STR, INT, POS, BOOL, DIGEST, TOKEN, UNIT, JSON)

SCOPE = enum("current_and_remaining", "current_only")
RIR = {"type": "integer", "minimum": 0, "maximum": 4}
ACTUALS = obj({"load_g": nullable(INT), "unit": UNIT, "count": POS},
              {"rir": RIR, "rir_is_lower_bound": BOOL, "hold_sec": INT})
SOURCE = obj({"kind": enum("callback", "text", "scheduler", "trainer", "import"),
              "ref": STR}, {"message_id": STR, "member_id": STR, "chat_id": STR})
# Every section 3.3 verb is explicit, including the final paragraph's parity verbs.
COMMAND_ARGS = {
    "set_logged": obj({"row_id": POS, "actuals": ACTUALS, "completed_at": INT,
                       "actual_source": enum("tap_confirmed", "typed")}),
    "select_load": obj({"row_id": POS, "load_g": INT}),
    "select_reps": obj({"row_id": POS, "actual_reps": POS, "load_g": INT,
                        "completed_at": INT, "confirm_selected": BOOL}),
    "select_rir": obj({"row_id": POS, "rir": RIR, "rir_is_lower_bound": BOOL}),
    "undo": obj({"row_id": POS, "recorded_revision": POS, "reopen": BOOL}),
    "correct": obj({"row_id": POS, "recorded_revision": POS, "reopen": BOOL,
                    "actuals": ACTUALS, "reason": STR}),
    "change_load": obj({"load_g": INT, "reason": STR}, {"scope": SCOPE, "target_reps": POS}),
    "change_reps": obj({"target_reps": POS, "reason": STR}, {"scope": SCOPE}),
    "close_lift": obj({"lift_id": POS, "reason": STR}),
    "skip_lift": obj({"lift_id": POS, "reason": STR}),
    "next_lift": obj({"reason": STR}, {"target_lift_id": POS}),
    "skip_warmup": obj({"reason": STR}),
    "stop_session": obj({"reason": STR}),
    "rest_end": obj({"session_id": POS, "rest_epoch": INT, "deadline": INT}),
    # decision schema is assigned after TrainerDecision is defined (no loose JSON).
    "approve_step": None,
    "start": obj({"member_id": STR, "mode": enum("regular", "free"),
                  "policy_bundle_id": STR, "catalog_version": POS, "capability_receipt_id": STR},
                 {"split_id": STR}),
    "choose_split": obj({"split_id": STR}),
    "free_lift": obj({"exercise_id": STR, "variant_id": STR}),
    "reorder": obj({"lift_ids": array(POS, 1)}),
    "substitute": obj({"lift_id": POS, "exercise_id": STR, "variant_id": STR, "reason": STR}),
    "missing_weight": obj({"lift_id": POS, "unavailable_load_g": INT, "available_loads_g": array(INT, 1), "evidence_id": STR}),
    "choose_implement": obj({"lift_id": POS, "variant_id": STR, "implement_count": INT, "load_basis": enum("total", "per_hand", "assistance")}),
    "choose_machine": obj({"lift_id": POS, "machine_ref": STR}),
    "photo_result": obj({"lift_id": POS, "evidence_id": STR, "machine_ref": nullable(STR), "observed_at": INT}),
    "pain": obj({"lift_id": POS, "evidence_id": STR, "scope": enum("entry", "set", "lift", "session"), "observed_at": INT, "description": STR, "alternative_exercise_id": nullable(STR)}),
    "reclassify": obj({"row_id": POS, "recorded_revision": POS, "set_kind": enum("prep", "ramp", "working", "hold", "post"), "reason": STR}),
    "choose_post": obj({"kind": enum("cardio", "interval", "stretch"), "duration_ms": POS}),
    "skip_post": obj({"reason": STR}),
    "claim_split": obj({"split_id": STR}),
    "measure_kcal": obj({"status": enum("measured", "unmeasured"), "kcal": nullable(INT), "evidence_id": nullable(STR), "observed_at": INT}),
}
TRAINER_COMMANDS = {"change_load", "change_reps", "close_lift", "skip_lift", "next_lift", "skip_warmup", "stop_session", "approve_step"}
COMMAND_ACTORS = {name: ("trainer" if name in TRAINER_COMMANDS else "system" if name == "rest_end" else "member") for name in COMMAND_ARGS}


class CommandEnvelope(Contract):
    @classmethod
    def check(cls, value):
        args = value["args"]
        source = value["source_ref"]
        if source["kind"] == "callback" and ("action_token" not in value or not all(k in source for k in ("member_id", "chat_id", "message_id"))):
            raise ValidationError("callback requires token and authenticated member/chat/message context")
        if value["event_type"] == "rest_end" and args["session_id"] != value["session_id"]:
            raise ValidationError("rest_end session mismatch")
        if value["event_type"] == "reorder" and len(set(args["lift_ids"])) != len(args["lift_ids"]):
            raise ValidationError("duplicate lift in reorder")
        if value["event_type"] == "measure_kcal":
            measured = args["status"] == "measured"
            if measured != (args["kcal"] is not None and args["evidence_id"] is not None):
                raise ValidationError("measured kcal requires evidence and value")
            if not measured and (args["kcal"] is not None or args["evidence_id"] is not None):
                raise ValidationError("unmeasured kcal must be unknown")
        actual = args.get("actuals", args)
        if actual.get("rir_is_lower_bound") and actual.get("rir") != 4:
            raise ValidationError("only 4 supports RIR lower bound")
        if value["event_type"] == "approve_step":
            from .trainer import TrainerDecision
            decision = TrainerDecision(args["decision"])
            if (decision["session_id"], decision["base_revision"]) != (value["session_id"], value["expected_revision"]):
                raise ValidationError("approval envelope binding mismatch")


DELIVERY_STATES = ("waiting_trainer", "ready", "publishing", "delivered", "superseded", "uncertain")
OUTBOX_ID = obj({"session_id": POS, "revision": INT, "frame_kind": enum("step", "answer", "closeout", "timer", "setup")})


class CommitReceipt(Contract):
    SCHEMA = obj({"schema": enum(2), "session_id": POS, "command_id": STR,
                  "receipt_id": STR, "revision": INT, "view_digest": DIGEST,
                  "affected_row_ids": array(POS), "outbox_id": nullable(OUTBOX_ID),
                  "delivery_state": enum(*DELIVERY_STATES),
                  "commit_state": enum("committed", "no_state_change")})

    @classmethod
    def check(cls, value):
        outbox = value["outbox_id"]
        if outbox and (outbox["session_id"], outbox["revision"]) != (value["session_id"], value["revision"]):
            raise ValidationError("receipt outbox binding mismatch")
        if len(set(value["affected_row_ids"])) != len(value["affected_row_ids"]):
            raise ValidationError("duplicate affected row")


class CommandWriter(Protocol):
    def apply(self, conn, command: CommandEnvelope) -> CommitReceipt: ...


def finalize_command_schema(decision_schema):
    COMMAND_ARGS["approve_step"] = obj({"decision": decision_schema})
    CommandEnvelope.SCHEMA = {"oneOf": [obj({
        "schema": enum(2), "session_id": POS, "command_id": STR,
        "actor": enum(COMMAND_ACTORS[name]), "event_type": enum(name),
        "expected_revision": INT, "args": args, "source_ref": SOURCE,
    }, {"action_token": TOKEN}) for name, args in COMMAND_ARGS.items()]}
