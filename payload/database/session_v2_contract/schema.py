"""Small strict JSON-schema subset; no dependency on installed site packages."""
import json
import re
from collections.abc import Mapping

# Additive interface revision; the v2 command/receipt wire discriminator stays 2.
INTERFACE_VERSION = "2.2"
CATALOG_VERSION = 5


class ValidationError(ValueError):
    pass


def obj(required, optional=None):
    return {"type": "object", "properties": {**required, **(optional or {})},
            "required": list(required), "additionalProperties": False}


def enum(*values):
    return {"enum": list(values)}


def array(items, minimum=0):
    return {"type": "array", "items": items, "minItems": minimum}


def nullable(schema):
    return {"oneOf": [schema, {"type": "null"}]}


def mapping(values):
    return {"type": "object", "additionalProperties": values}


STR = {"type": "string", "minLength": 1}
INT = {"type": "integer", "minimum": 0}
POS = {"type": "integer", "minimum": 1}
BOOL = {"type": "boolean"}
DIGEST = {"type": "string", "pattern": r"^sha256:[0-9a-f]{64}$"}
TOKEN = {"type": "string", "pattern": r"^[0-9a-f]{32}$"}
UNIT = enum("reps", "seconds", "breaths", "steps")
COUNT_UNIT = enum("breaths", "steps")
PHASE = enum("plan", "prep", "entry", "ready", "rest", "lift_done", "post", "closed")
JSON = {}  # JSON only, never Python objects, NaN or infinity


def validate(schema, value, path="$"):
    def fail(message):
        raise ValidationError(f"{path}: {message}")
    if "oneOf" in schema:
        matches = 0
        for option in schema["oneOf"]:
            try:
                validate(option, value, path)
                matches += 1
            except ValidationError:
                pass
        if matches != 1:
            fail("expected exactly one union variant")
        return value
    if "enum" in schema and not any(type(value) is type(v) and value == v for v in schema["enum"]):
        fail("not an allowed value")
    kind = schema.get("type")
    types = {"object": dict, "array": list, "integer": int, "string": str,
             "boolean": bool, "null": type(None)}
    if kind and type(value) is not types[kind]:
        fail(f"expected {kind}")
    if kind == "object":
        props = schema.get("properties", {})
        missing = set(schema.get("required", [])) - value.keys()
        if missing:
            fail(f"missing {sorted(missing)}")
        for key, item in value.items():
            if not isinstance(key, str):
                fail("non-string object key")
            if key in props:
                validate(props[key], item, f"{path}.{key}")
            elif schema.get("additionalProperties", True) is False:
                fail(f"unknown field {key}")
            elif isinstance(schema.get("additionalProperties"), dict):
                validate(schema["additionalProperties"], item, f"{path}.{key}")
    if kind == "array":
        if len(value) < schema.get("minItems", 0):
            fail("too few items")
        for index, item in enumerate(value):
            validate(schema["items"], item, f"{path}[{index}]")
    if kind == "integer" and value < schema.get("minimum", value):
        fail("below minimum")
    if kind == "integer" and value > schema.get("maximum", value):
        fail("above maximum")
    if kind == "string":
        if len(value) < schema.get("minLength", 0):
            fail("empty string")
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            fail("invalid format")
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError):
        fail("not finite JSON")
    return value


class Contract(Mapping):
    """Validated immutable wire value. Access fields with [] or to_dict()."""
    SCHEMA = None

    def __init__(self, value):
        validate(self.SCHEMA, value)
        self.check(value)
        object.__setattr__(self, "_json", json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False))

    def __setattr__(self, name, value):
        raise AttributeError("wire contracts are immutable")

    @classmethod
    def check(cls, value):
        pass

    @classmethod
    def from_dict(cls, value):
        return cls(value)

    @classmethod
    def from_json(cls, value):
        def pairs(items):
            result = {}
            for key, item in items:
                if key in result:
                    raise ValidationError(f"duplicate key {key}")
                result[key] = item
            return result
        try:
            decoded = json.loads(value, object_pairs_hook=pairs)
        except json.JSONDecodeError as error:
            raise ValidationError(str(error)) from error
        return cls(decoded)

    def to_dict(self):
        return json.loads(self._json)

    def to_json(self):
        return self._json

    def __getitem__(self, key):
        return self.to_dict()[key]

    def __iter__(self):
        return iter(self.to_dict())

    def __len__(self):
        return len(self.to_dict())
