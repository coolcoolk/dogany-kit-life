"""Versioned provenance-bearing policy/catalog wire contracts."""
from .schema import *

PREDICATE = {"oneOf": [
    obj({"field": enum("multi_joint", "free_weight"), "operator": enum("eq"), "value": BOOL}),
    obj({"field": enum("exercise_id", "caution_area"), "operator": enum("eq"), "value": STR}),
    obj({"field": enum("exercise_id", "caution_area"), "operator": enum("in"), "value": array(STR, 1)}),
    obj({"field": enum("relative_load_basis_points"), "operator": enum("gte"), "value": INT}),
]}

BAND = obj({"upper_basis_points": POS, "inclusive": BOOL, "reps": POS})
POLICY_KEYS = {
    "session.order": ("enum", enum("lifts_first", "cardio_first")),
    "session.watch_usage": ("enum", enum("watch", "no_watch")),
    "compound.heavy_selector": ("predicate[]", array(PREDICATE, 1)),
    "compound.heavy_rep_cap": ("int", POS),
    "progression.mode": ("enum", enum("double_progression")),
    "effort.floor": ("int", INT), "history.lookback": ("int", POS),
    "warmup.taper": ("bands", array(BAND, 1)),
    "warmup.rest_ms": ("duration", enum(0)),
    "warmup.actual_mode": ("enum", enum("selected_reps")),
    "rest.by_lift": ("duration", mapping(INT)), "rest.setup_lead_ms": ("duration", INT),
    "activation.rules": ("predicate[]", array(PREDICATE)),
    "venue.grid": ("load[]", array(INT, 1)), "plate.inventory": ("map", mapping(INT)),
    "ui.rir_question": ("copy_key", STR), "ui.rir_keys": ("enum[]", array(enum("0", "1", "2", "3", "4+"), 5)),
    "trainer.free_prose": ("bool", BOOL),
}


class PolicyRule(Contract):
    SCHEMA = {"oneOf": [obj({"bundle_id": STR, "key": enum(key), "value_type": enum(kind),
                             "value": shape, "scope": enum("general", "owner", "session"),
                             "source_ref": STR, "source_date": {"type": "string", "pattern": r"\d{4}-\d{2}-\d{2}"},
                             "approval_status": enum("proposed", "approved", "rejected")})
                        for key, (kind, shape) in POLICY_KEYS.items()]}

    @classmethod
    def check(cls, value):
        if value["key"] == "ui.rir_keys" and value["value"] != ["0", "1", "2", "3", "4+"]:
            raise ValidationError("RIR keys must retain order and lower-bound label")
        if value["key"] == "warmup.taper":
            bands = value["value"]
            bounds = [b["upper_basis_points"] for b in bands]
            if bounds != sorted(set(bounds)) or bounds[-1] != 10000 or bands[-1]["inclusive"]:
                raise ValidationError("taper must ascend to exclusive working-load boundary")


class PolicyBundle(Contract):
    SCHEMA = obj({"id": STR, "owner_id": STR, "version": POS, "parent_defaults_version": STR,
                  "effective_at": INT, "digest": DIGEST, "rules": array(PolicyRule.SCHEMA, 1)})

    @classmethod
    def check(cls, value):
        keys = []
        for rule in value["rules"]:
            PolicyRule(rule)
            if rule["bundle_id"] != value["id"]:
                raise ValidationError("rule belongs to another bundle")
            keys.append((rule["key"], rule["scope"]))
        if len(keys) != len(set(keys)):
            raise ValidationError("duplicate policy layer")


class CatalogEntry(Contract):
    SCHEMA = obj({"exercise_id": STR, "variant_id": STR, "movement": STR, "grip": STR,
                  "implement_kind": enum("barbell", "dumbbell", "machine", "bodyweight", "band", "kettlebell", "other"),
                  "implement_count": INT, "load_basis": enum("total", "per_hand", "assistance"),
                  "laterality": enum("bilateral", "unilateral", "alternating"),
                  "compatible_machine_capabilities": array(STR), "cue_version": POS,
                  "catalog_version": POS, "review_status": enum("proposed", "approved", "rejected"),
                  "multi_joint": BOOL, "free_weight": BOOL, "display_name": STR},
                 {"top_hold_ms": POS})


class EquipmentSnapshot(Contract):
    SCHEMA = obj({"id": STR, "session_id": POS, "catalog_version": POS, "variant_id": STR,
                  "machine_ref": nullable(STR), "grid_version": STR, "inventory_version": STR,
                  "load_basis": enum("total", "per_hand", "assistance"), "implement_count": INT,
                  "mounted_load_g": nullable(INT), "evidence_id": nullable(STR)})


class Evidence(Contract):
    SCHEMA = obj({"id": STR, "session_id": POS, "event_id": STR, "kind": STR,
                  "value": JSON, "unit": nullable(STR), "scope": enum("entry", "set", "lift", "session"),
                  "observed_at": INT, "source_kind": STR, "source_ref": STR})
