#!/usr/bin/env python3
"""Tests of src/schema_check.py: the JSON Schema subset the collections use.

Run: python3 -m unittest discover -s tests
"""
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import schema_check  # noqa: E402


def errors(schema, data):
    assert schema_check.schema_errors(schema) == [], schema_check.schema_errors(schema)
    return schema_check.validate(schema, data)


class Keywords(unittest.TestCase):
    def test_types(self):
        self.assertEqual(errors({"type": "integer"}, 3), [])
        self.assertEqual(errors({"type": "integer"}, 3.0), [])
        self.assertEqual(errors({"type": "integer"}, True), ["/: is boolean, expected integer"])
        self.assertEqual(errors({"type": "number"}, 1), [])
        self.assertEqual(errors({"type": ["string", "null"]}, None), [])
        self.assertEqual(errors({"type": "object"}, []), ["/: is array, expected object"])

    def test_enum_and_const_compare_as_json(self):
        self.assertEqual(errors({"enum": [1, "a"]}, 1.0), [])
        self.assertEqual(errors({"enum": [1]}, True), ["/: true is not one of 1"])
        self.assertEqual(errors({"const": {"a": [1]}}, {"a": [1]}), [])

    def test_object_keywords(self):
        schema = {"type": "object", "additionalProperties": False, "required": ["id"],
                  "properties": {"id": {"type": "string"}, "a": {}, "b": {}},
                  "patternProperties": {"^x-": {"type": "integer"}},
                  "dependentRequired": {"a": ["b"]}, "minProperties": 1, "maxProperties": 3}
        self.assertEqual(errors(schema, {"id": "k", "x-one": 1}), [])
        self.assertEqual(sorted(errors(schema, {"a": 1, "x-two": "no", "z": 0})), [
            "/: 'a' needs 'b' too",
            "/: required key 'id' is missing",
            "/: unknown key 'z' (allowed: a, b, id)",
            "/x-two: is string, expected integer",
        ])

    def test_map_with_value_schema_and_key_names(self):
        schema = {"type": "object", "additionalProperties": {"type": "string"},
                  "propertyNames": {"pattern": "^[A-Z]"}}
        self.assertEqual(schema_check.closed_errors(schema), [])
        self.assertEqual(errors(schema, {"Upn": "a"}), [])
        self.assertEqual(sorted(errors(schema, {"upn": 1})), [
            "/: key 'upn': \"upn\" does not match ^[A-Z]",
            "/upn: is integer, expected string",
        ])

    def test_array_keywords(self):
        schema = {"type": "array", "prefixItems": [{"const": "first"}], "items": {"type": "integer"},
                  "minItems": 2, "maxItems": 3, "contains": {"const": 7}, "maxContains": 1}
        self.assertEqual(errors(schema, ["first", 7]), [])
        self.assertEqual(errors(schema, ["first", 7, 7, 1]), [
            "/: has 4 items, at most 3 allowed",
            "/: 2 items match contains, expected 1 to 1",
        ])
        self.assertEqual(errors({"type": "array", "uniqueItems": True}, [1, 1.0]),
                         ["/1: 1.0 is repeated, the items are unique"])

    def test_string_and_number(self):
        self.assertEqual(errors({"type": "string", "pattern": "^[0-9]+[mhd]$", "maxLength": 3}, "15m"), [])
        self.assertEqual(errors({"type": "string", "pattern": "^[0-9]+[mhd]$"}, "PT1H"),
                         ['/: "PT1H" does not match ^[0-9]+[mhd]$'])
        self.assertEqual(errors({"minimum": 0, "exclusiveMaximum": 5, "multipleOf": 0.5}, 2.5), [])
        self.assertEqual(errors({"exclusiveMaximum": 5, "multipleOf": 2}, 5),
                         ["/: 5 is not below 5", "/: 5 is not a multiple of 2"])

    def test_combinators(self):
        one = {"oneOf": [{"type": "string"}, {"type": "string", "minLength": 1}]}
        self.assertEqual(errors(one, ""), [])
        self.assertEqual(errors(one, "a"), ["/: matches 2 of the oneOf alternatives, exactly one is allowed"])
        self.assertEqual(errors({"anyOf": [{"type": "integer"}, {"type": "string", "minLength": 2}]}, "a"),
                         ["/: is 1 characters long, at least 2 expected (closest of the anyOf alternatives)"])
        self.assertEqual(errors({"not": {"const": "All"}}, "All"),
                         ["/: matches a schema it must not match (not)"])
        cond = {"type": "object", "additionalProperties": False,
                "properties": {"kind": {"type": "string"}, "query": {"type": "string"}},
                "if": {"properties": {"kind": {"const": "Scheduled"}}},
                "then": {"required": ["query"]}}
        self.assertEqual(errors(cond, {"kind": "Fusion"}), [])
        self.assertEqual(errors(cond, {"kind": "Scheduled"}), ["/: required key 'query' is missing"])

    def test_references_and_boolean_schemas(self):
        schema = {"$defs": {"node": {"type": "object", "additionalProperties": False,
                                     "properties": {"next": {"$ref": "#/$defs/node"}, "v": True, "x": False}}},
                  "$ref": "#/$defs/node"}
        self.assertEqual(errors(schema, {"v": 1, "next": {"next": {}}}), [])
        self.assertEqual(errors(schema, {"next": {"x": 1}}), ["/next/x: no value is allowed here"])


class Schemas(unittest.TestCase):
    def test_unusable_schemas(self):
        self.assertEqual(schema_check.schema_errors(
            {"type": "map", "properties": {"a": {"$ref": "#/$defs/missing"}, "b": {"pattern": "("}},
             "unevaluatedProperties": False}), [
            "/: type 'map' is not one of array, boolean, integer, null, number, object, string",
            "/: keyword 'unevaluatedProperties' is not supported by this validator",
            "/properties/a: $ref '#/$defs/missing' does not lead to a schema in this file",
            "/properties/b: pattern '(' does not compile (missing ), unterminated subpattern at position 0)",
        ])

    def test_open_objects(self):
        self.assertEqual(schema_check.closed_errors(
            {"type": "object", "additionalProperties": False,
             "properties": {"a": {"type": "object"}, "b": {"properties": {}, "additionalProperties": True}},
             "$defs": {"c": {"type": ["object", "null"], "additionalProperties": False}}}),
            ["/properties/a: an object without additionalProperties: false (or a schema for the values of a map)",
             "/properties/b: an object without additionalProperties: false (or a schema for the values of a map)"])


class Cli(unittest.TestCase):
    def test_files_and_self_test(self):
        with tempfile.TemporaryDirectory() as tmp:
            schema = pathlib.Path(tmp, "s.json")
            good, bad = pathlib.Path(tmp, "good.json"), pathlib.Path(tmp, "bad.json")
            schema.write_text(json.dumps({"type": "object", "additionalProperties": False,
                                          "properties": {"a": {"type": "string"}}}))
            good.write_text('{"a": "x"}')
            bad.write_text('{"b": 1}')
            run = [sys.executable, str(ROOT / "src" / "schema_check.py")]
            self.assertEqual(subprocess.run(run + [str(schema), str(good)]).returncode, 0)
            out = subprocess.run(run + [str(schema), str(bad)], capture_output=True, text=True)
            self.assertEqual(out.returncode, 1)
            self.assertIn("unknown key 'b'", out.stdout)
        self.assertEqual(subprocess.run(run + ["--self-test"], capture_output=True).returncode, 0)


if __name__ == "__main__":
    unittest.main()
