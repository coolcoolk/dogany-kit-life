"""Prepared snapshot, typed speech AST and context-sensitive validation.

Free prose is a lexical GUARD, NOT proof of factual correctness. Keep disabled
unless the pinned policy explicitly enables it. Reviewed templates remain the
only construction-based guarantee for assertion-bearing speech.
"""
import re
import json
from pathlib import Path
import unicodedata
import string
from typing import Protocol
from .schema import *
from .policy import CatalogEntry, EquipmentSnapshot, Evidence
from .commands import COMMAND_ARGS, SCOPE, RIR, finalize_command_schema

SLOT_TYPES = ("Load", "PlateDeltaPerSide", "PlateTotalPerSide", "Reps", "Count", "RIR", "Duration", "LiftName", "SetOrdinal", "EvidenceClaim")
SLOT_VALUES = {name: INT for name in SLOT_TYPES}
SLOT_VALUES.update({"Reps": POS, "SetOrdinal": POS, "LiftName": STR,
                    "Count": obj({"value": POS, "unit": COUNT_UNIT}),
                    "PlateDeltaPerSide": {"type": "integer"},
                    "RIR": obj({"value": RIR, "is_lower_bound": BOOL}),
                    "EvidenceClaim": obj({"evidence_id": STR, "scope": enum("entry", "set", "lift", "session"), "observed_at": INT, "claim_key": STR})})


class TypedSlot(Contract):
    SCHEMA = {"oneOf": [obj({"type": enum(name), "value": value, "receipt_id": STR}) for name, value in SLOT_VALUES.items()]}

    @classmethod
    def check(cls, value):
        if value["type"] == "RIR" and value["value"]["is_lower_bound"] and value["value"]["value"] != 4:
            raise ValidationError("RIR lower bound must be 4")


# Named constructors share the same discriminated wire shape.

class Load(TypedSlot):
    SCHEMA = obj({"type": enum("Load"), "value": SLOT_VALUES["Load"], "receipt_id": STR})

class PlateDeltaPerSide(TypedSlot):
    SCHEMA = obj({"type": enum("PlateDeltaPerSide"), "value": SLOT_VALUES["PlateDeltaPerSide"], "receipt_id": STR})

class PlateTotalPerSide(TypedSlot):
    SCHEMA = obj({"type": enum("PlateTotalPerSide"), "value": SLOT_VALUES["PlateTotalPerSide"], "receipt_id": STR})

class Reps(TypedSlot):
    SCHEMA = obj({"type": enum("Reps"), "value": SLOT_VALUES["Reps"], "receipt_id": STR})

class Count(TypedSlot):
    SCHEMA = obj({"type": enum("Count"), "value": SLOT_VALUES["Count"], "receipt_id": STR})

class Duration(TypedSlot):
    SCHEMA = obj({"type": enum("Duration"), "value": SLOT_VALUES["Duration"], "receipt_id": STR})

class LiftName(TypedSlot):
    SCHEMA = obj({"type": enum("LiftName"), "value": SLOT_VALUES["LiftName"], "receipt_id": STR})

class SetOrdinal(TypedSlot):
    SCHEMA = obj({"type": enum("SetOrdinal"), "value": SLOT_VALUES["SetOrdinal"], "receipt_id": STR})

class EvidenceClaim(TypedSlot):
    SCHEMA = obj({"type": enum("EvidenceClaim"), "value": SLOT_VALUES["EvidenceClaim"], "receipt_id": STR})


SLOT_REF = obj({"type": enum(*SLOT_TYPES), "ref": STR})
TEMPLATE_SEGMENT = obj({"mode": enum("template"), "template_id": STR, "slots": mapping(SLOT_REF)})
CUE_SEGMENT = obj({"mode": enum("template"), "cue_id": STR, "catalog_version": POS, "slots": mapping(SLOT_REF)})
FREE_SEGMENT = obj({"mode": enum("free"), "text": STR})
SPEECH = array({"oneOf": [TEMPLATE_SEGMENT, CUE_SEGMENT, FREE_SEGMENT]}, 1)
DECISION_OPS = {"keep": obj({"op": enum("keep")})}
for _name in ("change_load", "change_reps", "close_lift", "skip_lift", "next_lift", "skip_warmup", "stop_session"):
    _args = COMMAND_ARGS[_name]
    DECISION_OPS[_name] = obj({"op": enum(_name), **{k: _args["properties"][k] for k in _args["required"]}},
                             {k: v for k, v in _args["properties"].items() if k not in _args["required"]})

# Boundary-word English matching; conservative substring Korean matching also
# catches compound number words. False positives are intentional and documented.
_LEXICON = json.loads(Path(__file__).with_name("free_prose_guard.json").read_text())
ENGLISH_NUMBERS = _LEXICON["english_numbers"]
KOREAN_NUMBERS = _LEXICON["korean_numbers"]
CLAIM_WORDS = _LEXICON["claim_words"]


def validate_free_prose(text, enabled=False):
    if enabled is not True:
        raise ValidationError("trainer.free_prose is off (requires boolean true)")
    normalized = unicodedata.normalize("NFKC", text).casefold()
    if any(c.isnumeric() or unicodedata.category(c) == "Cf" for c in text + normalized):
        raise ValidationError("digits/hidden characters forbidden in free prose")
    words = ENGLISH_NUMBERS + [w for w in CLAIM_WORDS if w.isascii()]
    if re.search(r"(?<![a-z])(?:" + "|".join(map(re.escape, words)) + r")(?![a-z])", normalized):
        raise ValidationError("number or claim word in free prose")
    if any(w in normalized for w in KOREAN_NUMBERS + [w for w in CLAIM_WORDS if not w.isascii()]):
        raise ValidationError("Korean number or claim word in free prose")


class Template(Contract):
    SCHEMA = obj({"id": STR, "kind": enum("template", "cue"), "copy": STR,
                  "slots": mapping(enum(*SLOT_TYPES)), "variant_ids": array(STR),
                  "catalog_version": POS, "review_status": enum("proposed", "approved", "rejected"),
                  "cadence": enum("full", "tip", "any")})


    @classmethod
    def check(cls, value):
        try:
            fields = {field for _, field, spec, conversion in string.Formatter().parse(value["copy"]) if field is not None}
        except ValueError as error:
            raise ValidationError("invalid template placeholders") from error
        if fields != set(value["slots"]):
            raise ValidationError("copy placeholders must exactly match typed slots")
        if any(c.isnumeric() for c in value["copy"]):
            raise ValidationError("numeric copy must use a typed slot")


class TrainerDecision(Contract):
    SCHEMA = obj({"schema": enum(2), "session_id": POS, "base_revision": INT,
                  "view_digest": DIGEST, "dependency_digest": DIGEST,
                  "decision": {"oneOf": list(DECISION_OPS.values())}, "speech": SPEECH,
                  "encouragement_id": nullable(STR), "release": enum("immediate", "rest_end")})

    @classmethod
    def check(cls, value):
        # Context-free parsing does not authorize publishing free prose.
        for segment in value["speech"]:
            if segment["mode"] == "free":
                validate_free_prose(segment["text"], enabled=True)


finalize_command_schema(TrainerDecision.SCHEMA)

PRESCRIPTION = obj({"row_id": POS, "lift_id": nullable(POS), "lift_label": STR, "variant_id": STR,
                    "set_kind": enum("prep", "ramp", "working", "hold", "post"), "set_ordinal": POS,
                    "load_g": nullable(INT), "target_unit": UNIT, "target_reps": nullable(POS),
                    "target_count": nullable(POS), "target_hold_sec": nullable(INT),
                    "target_rir_min": nullable(RIR), "target_rir_max": nullable(RIR),
                    "target_rest_ms": INT, "prescribed_revision": INT, "basis_event_id": STR,
                    "policy_bundle_id": STR, "equipment_snapshot_id": STR})
DEPENDENCIES = obj({"next_row_id": nullable(POS), "prescription_digest": DIGEST,
                    "actual_effort_digest": DIGEST, "variant_id": nullable(STR),
                    "policy_bundle_id": STR, "catalog_version": POS, "cue_index": INT,
                    "session_epoch": INT, "rest_epoch": INT})
LAST_EFFORT = obj({"row_id": POS, "load_g": nullable(INT), "actual_reps": nullable(POS),
                   "rir": nullable(RIR), "rir_is_lower_bound": BOOL, "recorded_revision": POS})


class PreparedNext(Contract):
    SCHEMA = obj({"schema": enum(2), "session_id": POS, "base_revision": INT, "receipt_id": STR,
                  "view_digest": DIGEST, "dependency_digest": DIGEST, "dependencies": DEPENDENCIES,
                  "phase": PHASE, "row_ids": array(POS), "last_effort": nullable(LAST_EFFORT),
                  "next": nullable(PRESCRIPTION), "available_loads_g": array(INT),
                  "plate_total_per_side_g": nullable(INT), "plate_delta_per_side_g": nullable({"type": "integer"}),
                  "rest": obj({"elapsed_ms": INT}),
                  "rest_deadline_at": nullable(INT), "lifts": array(obj({"id": POS, "order_index": INT, "status": enum("open", "closed", "skipped")})),
                  "equipment": nullable(EquipmentSnapshot.SCHEMA), "catalog": nullable(CatalogEntry.SCHEMA),
                  "cue_candidates": array(STR), "cue_cadence": enum("full", "tip"),
                  "policy_bundle_id": STR, "policy_provenance": array(STR, 1),
                  "evidence": array(Evidence.SCHEMA), "slots": mapping(TypedSlot.SCHEMA)})

    @classmethod
    def check(cls, value):
        rx = value["next"]
        deps = value["dependencies"]
        if len(value["row_ids"]) != len(set(value["row_ids"])):
            raise ValidationError("duplicate prepared row")
        if value["policy_bundle_id"] != deps["policy_bundle_id"]:
            raise ValidationError("policy dependency mismatch")
        if value["phase"] == "closed" and (rx is not None or value["rest_deadline_at"] is not None):
            raise ValidationError("closed view cannot prepare a next step or rest")
        if rx is None and deps["next_row_id"] is not None:
            raise ValidationError("next dependency without a prescription")
        for evidence in value["evidence"]:
            if evidence["session_id"] != value["session_id"]:
                raise ValidationError("evidence from another session")
        equipment, catalog = value["equipment"], value["catalog"]
        if equipment and equipment["session_id"] != value["session_id"]:
            raise ValidationError("equipment from another session")
        if rx:
            if rx["policy_bundle_id"] != value["policy_bundle_id"] or rx["prescribed_revision"] > value["base_revision"]:
                raise ValidationError("prescription not bound to prepared policy/revision")
            if rx["variant_id"] != deps["variant_id"]:
                raise ValidationError("variant dependency mismatch")
            if rx["lift_id"] is not None and not any(l["id"] == rx["lift_id"] and l["status"] == "open" for l in value["lifts"]):
                raise ValidationError("next prescription requires an open lift")
            if equipment and (equipment["id"] != rx["equipment_snapshot_id"] or equipment["variant_id"] != rx["variant_id"]):
                raise ValidationError("prescription equipment mismatch")
            if catalog and (catalog["variant_id"] != rx["variant_id"] or catalog["catalog_version"] != deps["catalog_version"]):
                raise ValidationError("prescription catalog mismatch")
            if equipment and catalog and any(equipment[k] != catalog[k] for k in ("implement_count", "load_basis", "catalog_version")):
                raise ValidationError("equipment semantics mismatch")
            if (rx["target_unit"] == "reps") != (rx["target_reps"] is not None):
                raise ValidationError("rep dose must use target_reps")
            if (rx["target_unit"] == "reps") != (rx["target_count"] is None):
                raise ValidationError("target_count only for non-rep units")
            if rx["row_id"] not in value["row_ids"] or rx["row_id"] != value["dependencies"]["next_row_id"]:
                raise ValidationError("next row dependency mismatch")
            lo, hi = rx["target_rir_min"], rx["target_rir_max"]
            if lo is not None and hi is not None and lo > hi:
                raise ValidationError("contradictory RIR range")
        for slot in value["slots"].values():
            TypedSlot(slot)
            if slot["receipt_id"] != value["receipt_id"]:
                raise ValidationError("slot not bound to snapshot receipt")
            if slot["type"] == "EvidenceClaim":
                claim = slot["value"]
                if not any(e["id"] == claim["evidence_id"] and e["scope"] == claim["scope"] and e["observed_at"] == claim["observed_at"] for e in value["evidence"]):
                    raise ValidationError("claim lacks scoped evidence")


def validate_decision(decision, prepared, templates, *, free_prose=False, encouragement_ids=()):
    """Structural + current-snapshot binding. Kit still validates op/policy at commit.

    Template map values are validated Template objects/dicts. Caller supplies the
    pinned catalog; this function never fetches or invents reviewed copy.
    """
    d, p = TrainerDecision(dict(decision)), PreparedNext(dict(prepared))
    for key in ("session_id", "base_revision", "view_digest", "dependency_digest"):
        if d[key] != p[key]:
            raise ValidationError(f"stale/mismatched {key}")
    if d["encouragement_id"] is not None and d["encouragement_id"] not in encouragement_ids:
        raise ValidationError("unknown encouragement")
    for segment in d["speech"]:
        if segment["mode"] == "free":
            validate_free_prose(segment["text"], free_prose)
            continue
        key = segment.get("template_id", segment.get("cue_id"))
        if key not in templates:
            raise ValidationError("unknown template/cue")
        template = Template(dict(templates[key]))
        if template["id"] != key or template["catalog_version"] != p["dependencies"]["catalog_version"]:
            raise ValidationError("template identity/catalog mismatch")
        if template["review_status"] != "approved":
            raise ValidationError("unreviewed template/cue")
        is_cue = "cue_id" in segment
        if template["kind"] != ("cue" if is_cue else "template"):
            raise ValidationError("wrong reference kind")
        if is_cue:
            if key not in p["cue_candidates"] or segment["catalog_version"] != p["dependencies"]["catalog_version"] or template["catalog_version"] != segment["catalog_version"]:
                raise ValidationError("cue not in prepared catalog")
        if template["variant_ids"] and (not p["next"] or p["next"]["variant_id"] not in template["variant_ids"]):
            raise ValidationError("wrong variant")
        if template["cadence"] not in ("any", p["cue_cadence"]):
            raise ValidationError("wrong cue cadence")
        if segment["slots"].keys() != template["slots"].keys():
            raise ValidationError("template slot names mismatch")
        for name, ref in segment["slots"].items():
            slot = p["slots"].get(ref["ref"])
            if slot is None or slot["type"] != ref["type"] or slot["type"] != template["slots"][name]:
                raise ValidationError("unknown or wrongly typed slot")
    return d


class TrainerPreparer(Protocol):
    def prepare_next(self, session_id: int, revision: int) -> PreparedNext: ...


class RIR(TypedSlot):
    SCHEMA = obj({"type": enum("RIR"), "value": SLOT_VALUES["RIR"], "receipt_id": STR})
