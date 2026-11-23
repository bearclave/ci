#!/usr/bin/env python3
"""Validates data against a JSON Schema, with the Python standard library only.

Usage: schema_check.py <schema.json> <data.json> [<data.json> ...]
       schema_check.py --self-test

The runners have no `jsonschema` package and the pipelines install nothing
from PyPI: the reference implementation pulls in attrs, referencing and the
compiled rpds-py, so it cannot be vendored as one pinned file. The schemas
of the YAML collections need a small part of JSON Schema 2020-12, and this
file implements exactly that part. A schema that uses anything else is
refused (`schema_errors`), so a keyword never passes by being ignored.

Supported keywords:

  structure   $schema $id $comment $defs definitions $ref (#, #/$defs/<name>,
              #/definitions/<name>, any JSON pointer into the same file)
  any type    type enum const allOf anyOf oneOf not if then else
  object      properties required additionalProperties patternProperties
              propertyNames minProperties maxProperties dependentRequired
  array       items prefixItems minItems maxItems uniqueItems contains
              minContains maxContains
  string      minLength maxLength pattern (Python re, which reads the
              ECMA-262 patterns schemas use the same way for the usual
              classes and anchors)
  number      minimum maximum exclusiveMinimum exclusiveMaximum multipleOf
  annotations title description markdownDescription default examples
              deprecated readOnly writeOnly format (an annotation in 2020-12)

Every object in a collection schema is closed: `additionalProperties` is
false, or a schema for a map whose keys are data (`closed_errors`).
"""
import json
import math
import re
import sys

ANNOTATIONS = {"$schema", "$id", "$comment", "title", "description", "markdownDescription", "default",
               "examples", "deprecated", "readOnly", "writeOnly", "format"}
CONTAINERS = {"$defs", "definitions"}
SUBSCHEMA = {"not", "if", "then", "else", "additionalProperties", "propertyNames", "items", "contains"}
SUBSCHEMA_LIST = {"allOf", "anyOf", "oneOf", "prefixItems"}
SUBSCHEMA_MAP = {"properties", "patternProperties"}
ASSERTIONS = {"$ref", "type", "enum", "const", "required", "minProperties", "maxProperties",
              "dependentRequired", "minItems", "maxItems", "uniqueItems", "minContains", "maxContains",
              "minLength", "maxLength", "pattern", "minimum", "maximum", "exclusiveMinimum",
              "exclusiveMaximum", "multipleOf"}
KNOWN = ANNOTATIONS | CONTAINERS | SUBSCHEMA | SUBSCHEMA_LIST | SUBSCHEMA_MAP | ASSERTIONS
TYPES = {"object", "array", "string", "number", "integer", "boolean", "null"}


def pointer(parts) -> str:
    """A JSON pointer of the path, `/` for the document itself."""
    return "/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def resolve(root, ref: str):
    if ref == "#":
        return root
    if not ref.startswith("#/"):
        raise KeyError(ref)
    node = root
    for raw in ref[2:].split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        node = node[int(part)] if isinstance(node, list) else node[part]
    return node


def walk(schema, path=()):
    """Every subschema with its path, the schema itself first."""
    yield path, schema
    if not isinstance(schema, dict):
        return
    for k, v in schema.items():
        if k in SUBSCHEMA:
            yield from walk(v, path + (k,))
        elif k in SUBSCHEMA_LIST and isinstance(v, list):
            for i, s in enumerate(v):
                yield from walk(s, path + (k, i))
        elif (k in SUBSCHEMA_MAP or k in CONTAINERS) and isinstance(v, dict):
            for name, s in v.items():
                yield from walk(s, path + (k, name))


def schema_errors(schema) -> list:
    """What makes the schema unusable here: unknown keywords, bad values, dangling references."""
    out = []
    for path, s in walk(schema):
        where = pointer(path)
        if isinstance(s, bool):
            continue
        if not isinstance(s, dict):
            out.append(f"{where}: a schema is an object or a boolean")
            continue
        for k, v in s.items():
            if k not in KNOWN:
                out.append(f"{where}: keyword {k!r} is not supported by this validator")
            elif k == "type":
                names = v if isinstance(v, list) else [v]
                if not names or any(n not in TYPES for n in names):
                    out.append(f"{where}: type {v!r} is not one of {', '.join(sorted(TYPES))}")
            elif k == "$ref":
                try:
                    resolve(schema, v)
                except (KeyError, IndexError, ValueError, TypeError):
                    out.append(f"{where}: $ref {v!r} does not lead to a schema in this file")
            elif k == "pattern" or (k == "patternProperties" and isinstance(v, dict)):
                for p in [v] if k == "pattern" else list(v):
                    try:
                        re.compile(p)
                    except (re.error, TypeError) as e:
                        out.append(f"{where}: pattern {p!r} does not compile ({e})")
            elif k == "required" and not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
                out.append(f"{where}: required is a list of names")
            elif k == "enum" and not isinstance(v, list):
                out.append(f"{where}: enum is a list")
            elif k in SUBSCHEMA_LIST and not isinstance(v, list):
                out.append(f"{where}: {k} is a list of schemas")
            elif k in SUBSCHEMA_MAP | CONTAINERS and not isinstance(v, dict):
                out.append(f"{where}: {k} is an object of schemas")
    return out


def in_condition(path) -> bool:
    """A subschema of if/then/else/not tests or adds to an object declared elsewhere; it is not one itself."""
    for i, k in enumerate(path):
        named = i > 0 and path[i - 1] in SUBSCHEMA_MAP | CONTAINERS
        if not named and k in ("if", "then", "else", "not"):
            return True
    return False


def closed_errors(schema) -> list:
    """Objects that accept keys nobody declared: a typo would pass and Terraform drop it silently."""
    out = []
    for path, s in walk(schema):
        if not isinstance(s, dict) or in_condition(path):
            continue
        types = s.get("type")
        is_object = "properties" in s or types == "object" or (isinstance(types, list) and "object" in types)
        extra = s.get("additionalProperties", True)
        if is_object and (extra is True or extra == {}):
            out.append(f"{pointer(path)}: an object without additionalProperties: false "
                       "(or a schema for the values of a map)")
    return out


def type_of(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    return "object"


def is_type(value, name: str) -> bool:
    actual = type_of(value)
    if name == "number":
        return actual in ("integer", "number")
    if name == "integer":
        return actual == "integer" or (actual == "number" and math.isfinite(value) and value.is_integer())
    return actual == name


def equal(a, b) -> bool:
    """JSON equality: 1 == 1.0, but true is not 1."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
    return type(a) is type(b) and a == b


def short(value) -> str:
    text = json.dumps(value, ensure_ascii=False)
    return text if len(text) <= 60 else text[:57] + "..."


class Validator:
    def __init__(self, schema):
        self.root = schema

    def errors(self, data) -> list:
        """Every place the data breaks the schema, as `<JSON pointer>: <what>`."""
        return [f"{pointer(p)}: {m}" for p, m in self._check(self.root, data, (), 0)]

    def _check(self, s, value, path, depth):
        if depth > 64:
            return [(path, "the schema refers to itself too deeply")]
        if s is True or s == {}:
            return []
        if s is False:
            return [(path, "no value is allowed here")]
        out = []
        if "$ref" in s:
            out += self._check(resolve(self.root, s["$ref"]), value, path, depth + 1)
        if "type" in s:
            names = s["type"] if isinstance(s["type"], list) else [s["type"]]
            if not any(is_type(value, n) for n in names):
                return out + [(path, f"is {type_of(value)}, expected {' or '.join(names)}")]
        if "enum" in s and not any(equal(value, e) for e in s["enum"]):
            out.append((path, f"{short(value)} is not one of {', '.join(short(e) for e in s['enum'])}"))
        if "const" in s and not equal(value, s["const"]):
            out.append((path, f"{short(value)} is not {short(s['const'])}"))
        for sub in s.get("allOf", []):
            out += self._check(sub, value, path, depth + 1)
        if "anyOf" in s:
            branches = [self._check(sub, value, path, depth + 1) for sub in s["anyOf"]]
            if all(branches):
                out += self._none_of("anyOf", branches, path)
        if "oneOf" in s:
            branches = [self._check(sub, value, path, depth + 1) for sub in s["oneOf"]]
            passing = sum(1 for b in branches if not b)
            if passing == 0:
                out += self._none_of("oneOf", branches, path)
            elif passing > 1:
                out.append((path, f"matches {passing} of the oneOf alternatives, exactly one is allowed"))
        if "not" in s and not self._check(s["not"], value, path, depth + 1):
            out.append((path, "matches a schema it must not match (not)"))
        if "if" in s:
            branch = "then" if not self._check(s["if"], value, path, depth + 1) else "else"
            if branch in s:
                out += self._check(s[branch], value, path, depth + 1)
        if isinstance(value, dict):
            out += self._object(s, value, path, depth)
        elif isinstance(value, list):
            out += self._array(s, value, path, depth)
        elif isinstance(value, str):
            out += self._string(s, value, path)
        elif type_of(value) in ("integer", "number"):
            out += self._number(s, value, path)
        return out

    @staticmethod
    def _none_of(keyword, branches, path):
        """The alternative that came closest, so that the message says what to fix.

        One whose type fits beats one of another type, then the fewest errors.
        """
        def wrong_type(branch):
            return any(p == path and m.startswith("is ") and ", expected " in m for p, m in branch)

        best = min(branches, key=lambda b: (wrong_type(b), len(b)))
        if len(best) == 1:
            p, m = best[0]
            return [(p, f"{m} (closest of the {keyword} alternatives)")]
        return [(path, f"matches none of the {keyword} alternatives")]

    def _object(self, s, value, path, depth):
        out = []
        props = s.get("properties", {})
        for name in s.get("required", []):
            if name not in value:
                out.append((path, f"required key {name!r} is missing"))
        for name, deps in s.get("dependentRequired", {}).items():
            if name in value:
                out += [(path, f"{name!r} needs {d!r} too") for d in deps if d not in value]
        if "minProperties" in s and len(value) < s["minProperties"]:
            out.append((path, f"has {len(value)} keys, at least {s['minProperties']} expected"))
        if "maxProperties" in s and len(value) > s["maxProperties"]:
            out.append((path, f"has {len(value)} keys, at most {s['maxProperties']} allowed"))
        patterns = [(re.compile(p), sub) for p, sub in s.get("patternProperties", {}).items()]
        extra = s.get("additionalProperties", True)
        for name, item in value.items():
            where = path + (name,)
            if "propertyNames" in s:
                out += [(path, f"key {name!r}: {m}") for _, m in self._check(s["propertyNames"], name, where, depth + 1)]
            matched = False
            if name in props:
                matched = True
                out += self._check(props[name], item, where, depth + 1)
            for rx, sub in patterns:
                if rx.search(name):
                    matched = True
                    out += self._check(sub, item, where, depth + 1)
            if matched:
                continue
            if extra is False:
                allowed = ", ".join(sorted(props)) or "none"
                out.append((path, f"unknown key {name!r} (allowed: {allowed})"))
            elif isinstance(extra, dict):
                out += self._check(extra, item, where, depth + 1)
        return out

    def _array(self, s, value, path, depth):
        out = []
        if "minItems" in s and len(value) < s["minItems"]:
            out.append((path, f"has {len(value)} items, at least {s['minItems']} expected"))
        if "maxItems" in s and len(value) > s["maxItems"]:
            out.append((path, f"has {len(value)} items, at most {s['maxItems']} allowed"))
        prefix = s.get("prefixItems", [])
        for i, item in enumerate(value):
            sub = prefix[i] if i < len(prefix) else s.get("items", True)
            out += self._check(sub, item, path + (i,), depth + 1)
        if s.get("uniqueItems"):
            for i in range(len(value)):
                if any(equal(value[i], value[j]) for j in range(i)):
                    out.append((path + (i,), f"{short(value[i])} is repeated, the items are unique"))
        if "contains" in s:
            n = sum(1 for item in value if not self._check(s["contains"], item, path, depth + 1))
            low, high = s.get("minContains", 1), s.get("maxContains")
            if n < low or (high is not None and n > high):
                out.append((path, f"{n} items match contains, expected {low}" + (f" to {high}" if high else " or more")))
        return out

    @staticmethod
    def _string(s, value, path):
        out = []
        n = len(value)
        if "minLength" in s and n < s["minLength"]:
            out.append((path, f"is {n} characters long, at least {s['minLength']} expected"))
        if "maxLength" in s and n > s["maxLength"]:
            out.append((path, f"is {n} characters long, at most {s['maxLength']} allowed"))
        if "pattern" in s and not re.search(s["pattern"], value):
            out.append((path, f"{short(value)} does not match {s['pattern']}"))
        return out

    @staticmethod
    def _number(s, value, path):
        out = []
        if "minimum" in s and value < s["minimum"]:
            out.append((path, f"{value} is below the minimum {s['minimum']}"))
        if "maximum" in s and value > s["maximum"]:
            out.append((path, f"{value} is above the maximum {s['maximum']}"))
        if "exclusiveMinimum" in s and value <= s["exclusiveMinimum"]:
            out.append((path, f"{value} is not above {s['exclusiveMinimum']}"))
        if "exclusiveMaximum" in s and value >= s["exclusiveMaximum"]:
            out.append((path, f"{value} is not below {s['exclusiveMaximum']}"))
        if "multipleOf" in s:
            q = value / s["multipleOf"]
            if not math.isclose(q, round(q), abs_tol=1e-9):
                out.append((path, f"{value} is not a multiple of {s['multipleOf']}"))
        return out


def validate(schema, data) -> list:
    return Validator(schema).errors(data)


SELF_TEST = [
    ({"type": "object", "properties": {"a": {"type": "integer"}}, "additionalProperties": False},
     {"a": 1}, 0),
    ({"type": "object", "properties": {"a": {"type": "integer"}}, "additionalProperties": False},
     {"a": 1, "b": 2}, 1),
    ({"type": "string", "enum": ["Low", "High"]}, "Medium", 1),
    ({"$defs": {"n": {"type": "number", "minimum": 0}}, "type": "array", "items": {"$ref": "#/$defs/n"}},
     [1, -1, 2.5], 1),
    ({"oneOf": [{"type": "string"}, {"type": "integer"}]}, True, 1),
    ({"type": "integer"}, 2.0, 0),
    ({"type": "integer"}, True, 1),
]


def self_test() -> int:
    failed = 0
    for schema, data, expected in SELF_TEST:
        problems = schema_errors(schema)
        got = validate(schema, data)
        if problems or len(got) != expected:
            failed += 1
            print(f"FAIL {json.dumps(schema)} {json.dumps(data)}: {problems or got}")
    print(f"{len(SELF_TEST) - failed} of {len(SELF_TEST)} passed")
    return 1 if failed else 0


def main(argv: list) -> int:
    if argv[:1] == ["--self-test"]:
        return self_test()
    if len(argv) < 2:
        sys.exit(__doc__)
    with open(argv[0], encoding="utf-8") as fh:
        schema = json.load(fh)
    problems = schema_errors(schema)
    for p in problems:
        print(f"{argv[0]}: {p}")
    if problems:
        return 1
    failed = 0
    for path in argv[1:]:
        with open(path, encoding="utf-8") as fh:
            for e in validate(schema, json.load(fh)):
                failed += 1
                print(f"{path}: {e}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
