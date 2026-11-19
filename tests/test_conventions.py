#!/usr/bin/env python3
"""Tests of src/conventions.py: the HCL reader and every rule.

Each test writes a small repository to a temporary directory (the rules read
the files git tracks) and runs the checker over it.

Run: python3 -m unittest discover -s tests
"""
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import conventions  # noqa: E402

TERRAGRUNT = {
    "units": ["mg-corp/**/terragrunt.hcl", "tenant/**/terragrunt.hcl"],
    "rules": {
        "module-source": {"level": "error"},
        "dependencies-in-common": {"level": "error", "dir": "_common/deps"},
        "mock-outputs": {"level": "error", "dir": "_common/deps", "mocks_file": "_common/mocks.hcl",
                         "forbidden_commands": ["apply", "destroy"]},
        "tags-required": {"level": "error", "allowed_values": "_common/tags.hcl",
                          "files": {"subscription.hcl": ["entity", "environment"],
                                    "rg.hcl": ["criticality", "data_classification"]}},
        "no-hardcoded-ids": {"level": "error", "id_files": ["account.hcl", "**/subscription.hcl"]},
        "no-hardcoded-location": {"level": "error", "locations": ["westeurope", "northeurope"]},
        "no-envcommon": {"level": "error"},
        "lock-none-needs-reason": {"level": "warn", "criticality": ["high"]},
    },
}
SUB = "11111111-2222-3333-4444-555555555555"
SOURCE = 'terraform {\n  source = "${include.root.locals.modules}//platform/resource-group?ref=${include.root.locals.modules_ref}"\n}\n'
GOOD_TREE = {
    "account.hcl": f'locals {{\n  tenant_id = "99999999-8888-7777-6666-555555555555"\n}}\n',
    "_common/tags.hcl": 'locals {\n  allowed = {\n    entity = ["shared", "sro"]\n'
                        '    environment = ["prod"]\n    criticality = ["high", "low"]\n'
                        '    data_classification = ["internal", "confidential"]\n  }\n}\n',
    "_common/mocks.hcl": 'locals {\n  read_only_commands = ["validate", "plan", "import"]\n}\n',
    "_common/deps/groups.hcl": 'locals {\n  mocks = read_terragrunt_config("x").locals\n}\n'
                               'dependency "groups" {\n  config_path = "../tenant/groups"\n'
                               '  mock_outputs = { ids = {} }\n'
                               '  mock_outputs_allowed_terraform_commands = local.mocks.read_only_commands\n}\n',
    "mg-corp/sub-a/subscription.hcl": f'locals {{\n  subscription_id = "{SUB}"\n  entity = "sro"\n  environment = "prod"\n}}\n',
    "mg-corp/sub-a/weu/rg-x/rg.hcl": 'locals {\n  criticality = "high"\n  data_classification = "internal"\n}\n',
    "mg-corp/sub-a/weu/rg-x/terragrunt.hcl": SOURCE + 'inputs = {\n  # Nothing in it worth keeping.\n  lock = "none"\n}\n',
    "mg-corp/sub-a/weu/rg-x/kv/terragrunt.hcl": SOURCE + 'inputs = {\n  lock = "CanNotDelete"\n'
                                                '  description = <<-DESC\n    a heredoc with "quotes" and westeurope\n  DESC\n'
                                                '  name = "kv-${local.x}-{literal}"\n}\n',
    "tenant/groups/terragrunt.hcl": SOURCE + 'inputs = {}\n',
}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, files):
        for path, text in files.items():
            p = self.root / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        subprocess.run(["git", "add", "-A"], cwd=self.root, check=True)

    def check(self, config):
        violations, errors = conventions.check(config, str(self.root))
        return violations, errors

    def rules_hit(self, config):
        violations, errors = self.check(config)
        self.assertEqual(errors, [])
        return sorted((v["rule"], v["path"], v["line"]) for v in violations if not v["exception"])


class Parser(unittest.TestCase):
    def test_blocks_attributes_and_labels(self):
        root = conventions.parse(conventions.tokenize(
            'variable "a" {\n  type = string\n  default = "x"\n}\n'
            'resource "t" "n" {\n  nested {\n    k = 1\n  }\n}\n')[0])
        self.assertEqual([b.kind for b in root.blocks], ["variable", "resource"])
        self.assertEqual(root.blocks[0].labels, ["a"])
        self.assertEqual(root.blocks[0].attrs["default"].line, 3)
        self.assertEqual(root.blocks[1].labels, ["t", "n"])
        self.assertEqual(root.blocks[1].blocks[0].attrs["k"].line, 7)

    def test_strings_with_interpolation_braces_and_quotes(self):
        tokens, _ = conventions.tokenize('a = "x-${ {b = "}"}.b }-y"\nc = 1\n')
        root = conventions.parse(tokens)
        self.assertEqual(root.attrs["a"].string(), 'x-${ {b = "}"}.b }-y')
        self.assertIn("c", root.attrs)
        self.assertEqual(root.attrs["c"].line, 2)

    def test_heredoc_keeps_line_numbers(self):
        tokens, _ = conventions.tokenize('a = <<-EOT\n  one\n  "two"\n  EOT\nb = "x"\n')
        root = conventions.parse(tokens)
        self.assertEqual(root.attrs["b"].line, 5)

    def test_comments_are_not_code_and_are_remembered(self):
        tokens, comments = conventions.tokenize(
            '# default = 1\nvariable "a" {\n  // default = 2\n  type = string /* default */\n}\n')
        root = conventions.parse(tokens)
        self.assertNotIn("default", root.blocks[0].attrs)
        self.assertEqual(comments, {1, 3, 4})

    def test_multiline_value(self):
        root = conventions.parse(conventions.tokenize('a = {\n  b = [\n    1,\n  ]\n}\nc = 2\n')[0])
        self.assertEqual(sorted(root.attrs), ["a", "c"])

    def test_literal(self):
        tokens, _ = conventions.tokenize('a = { x = ["p", "q"], y = "s", z = true }\n')
        root = conventions.parse(tokens)
        self.assertEqual(conventions.literal(root.attrs["a"].value),
                         {"x": ["p", "q"], "y": "s", "z": True})

    def test_glob(self):
        self.assertTrue(conventions.matches("a/b/c.tf", "**/*.tf"))
        self.assertTrue(conventions.matches("c.tf", "**/*.tf"))
        self.assertFalse(conventions.matches("a/b/c.tf", "*.tf"))
        self.assertTrue(conventions.matches("mg-corp/x/terragrunt.hcl", "mg-corp/**/terragrunt.hcl"))
        # **/ spans zero directories too, as in .gitignore.
        self.assertTrue(conventions.matches("mg-corp/terragrunt.hcl", "mg-corp/**/terragrunt.hcl"))


class Modules(Base):
    CONFIG = {"rules": {"variable-no-default": {"level": "error"},
                        "variable-documented": {"level": "warn"}}}

    def test_clean_module(self):
        self.write({"m/variables.tf": 'variable "a" {\n  description = "A."\n  type = string\n}\n'})
        self.assertEqual(self.rules_hit(self.CONFIG), [])

    def test_default_and_optional_default(self):
        self.write({"m/variables.tf":
                    'variable "a" {\n  description = "A."\n  type = string\n  default = "x"\n}\n'
                    'variable "b" {\n  description = "B."\n  type = object({\n'
                    '    p = optional(string)\n    q = optional(map(string), {})\n  })\n}\n'})
        self.assertEqual(self.rules_hit(self.CONFIG), [
            ("variable-no-default", "m/variables.tf", 4),
            ("variable-no-default", "m/variables.tf", 10),
        ])

    def test_undocumented(self):
        self.write({"m/variables.tf": 'variable "a" {\n  type = string\n}\n'})
        violations, _ = self.check(self.CONFIG)
        self.assertEqual([(v["rule"], v["level"]) for v in violations], [("variable-documented", "warn")])


class Terragrunt(Base):
    def test_good_tree(self):
        self.write(GOOD_TREE)
        self.assertEqual(self.rules_hit(TERRAGRUNT), [])

    def with_changes(self, changes):
        self.write({**GOOD_TREE, **changes})
        return self.rules_hit(TERRAGRUNT)

    def test_module_source_from_a_local_path(self):
        self.assertEqual(self.with_changes({
            "tenant/groups/terragrunt.hcl": 'terraform {\n  source = "../../modules/x"\n}\n'}),
            [("module-source", "tenant/groups/terragrunt.hcl", 2)])

    def test_module_source_from_the_context(self):
        for ctx in ("include.root.locals.ctx", "local.ctx"):
            self.assertEqual(self.with_changes({
                "tenant/groups/terragrunt.hcl":
                    f'terraform {{\n  source = "${{{ctx}.modules}}//entra/groups?ref=${{{ctx}.modules_ref}}"\n}}\n'}),
                [], ctx)
        self.assertEqual(self.with_changes({
            "tenant/groups/terragrunt.hcl":
                'terraform {\n  source = "${local.other.modules}//entra/groups?ref=${local.other.modules_ref}"\n}\n'}),
            [("module-source", "tenant/groups/terragrunt.hcl", 2)])

    def test_dependency_in_a_unit(self):
        self.assertEqual(self.with_changes({
            "tenant/groups/terragrunt.hcl": SOURCE + 'dependency "x" {\n  config_path = "../x"\n}\n'}),
            [("dependencies-in-common", "tenant/groups/terragrunt.hcl", 4)])

    def test_mocks_missing_or_reaching_apply(self):
        hits = self.with_changes({"_common/deps/users.hcl":
                                  'dependency "users" {\n  config_path = "../u"\n}\n'
                                  'dependency "other" {\n  config_path = "../o"\n  mock_outputs = {}\n'
                                  '  mock_outputs_allowed_terraform_commands = ["plan", "apply"]\n}\n'})
        self.assertEqual(hits, [
            ("mock-outputs", "_common/deps/users.hcl", 1),
            ("mock-outputs", "_common/deps/users.hcl", 1),
            ("mock-outputs", "_common/deps/users.hcl", 7),
        ])

    def test_mocks_file_allowing_destroy(self):
        hits = self.with_changes({"_common/mocks.hcl":
                                  'locals {\n  read_only_commands = ["plan", "destroy"]\n}\n'})
        self.assertEqual(hits, [("mock-outputs", "_common/deps/groups.hcl", 7)])

    def test_tags_missing_and_not_allowed(self):
        hits = self.with_changes({"mg-corp/sub-a/weu/rg-x/rg.hcl": 'locals {\n  criticality = "extreme"\n}\n'})
        self.assertEqual(hits, [("tags-required", "mg-corp/sub-a/weu/rg-x/rg.hcl", 1),
                                ("tags-required", "mg-corp/sub-a/weu/rg-x/rg.hcl", 2)])

    def test_tags_skip_a_common_unit_of_the_same_name(self):
        self.assertEqual(self.with_changes({"_common/units/subscription.hcl": "inputs = {}\n"}), [])

    def test_hardcoded_ids(self):
        hits = self.with_changes({"tenant/groups/terragrunt.hcl": SOURCE +
                                  f'inputs = {{\n  a = "{SUB}"\n'
                                  f'  b = "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/x"\n'
                                  '  c = "12345678-1234-1234-1234-123456789012"\n}\n'})
        self.assertEqual(hits, [("no-hardcoded-ids", "tenant/groups/terragrunt.hcl", 5),
                                ("no-hardcoded-ids", "tenant/groups/terragrunt.hcl", 6)])

    def test_hardcoded_location_but_not_in_a_heredoc_sentence(self):
        hits = self.with_changes({"tenant/groups/terragrunt.hcl": SOURCE + 'inputs = {\n  l = "westeurope"\n}\n'})
        self.assertEqual(hits, [("no-hardcoded-location", "tenant/groups/terragrunt.hcl", 5)])

    def test_envcommon(self):
        self.assertEqual(self.with_changes({"_envcommon/x.hcl": "locals {}\n"}),
                         [("no-envcommon", "_envcommon/x.hcl", 1)])

    def test_unlocked_without_a_reason(self):
        self.write({**GOOD_TREE, "mg-corp/sub-a/weu/rg-x/terragrunt.hcl": SOURCE + 'inputs = {\n  lock = "none"\n}\n'})
        violations, _ = self.check(TERRAGRUNT)
        self.assertEqual([(v["rule"], v["level"], v["line"]) for v in violations],
                         [("lock-none-needs-reason", "warn", 5)])

    def test_unlocked_in_low_criticality_is_fine(self):
        self.assertEqual(self.with_changes({
            "mg-corp/sub-a/weu/rg-x/rg.hcl": 'locals {\n  criticality = "low"\n  data_classification = "internal"\n}\n',
            "mg-corp/sub-a/weu/rg-x/terragrunt.hcl": SOURCE + 'inputs = {\n  lock = "none"\n}\n'}), [])


class Config(Base):
    def test_exception_needs_a_reason(self):
        self.write(GOOD_TREE)
        _, errors = self.check({**TERRAGRUNT, "exceptions": [{"rule": "no-envcommon", "path": "**"}]})
        self.assertEqual(len(errors), 1)
        self.assertIn("no reason", errors[0])

    def test_unknown_rule_and_level(self):
        self.write(GOOD_TREE)
        _, errors = self.check({"rules": {"nope": {}, "no-envcommon": {"level": "loud"}}})
        self.assertEqual(len(errors), 2)

    def test_exception_by_path_and_match(self):
        self.write({**GOOD_TREE, "tenant/groups/terragrunt.hcl": SOURCE +
                    'inputs = {\n  l = "westeurope"\n  m = "northeurope"\n}\n'})
        config = {**TERRAGRUNT, "exceptions": [{"rule": "no-hardcoded-location", "path": "tenant/**",
                                                "match": "westeurope", "reason": "test"}]}
        violations, errors = self.check(config)
        self.assertEqual(errors, [])
        self.assertEqual(sorted((v["line"], bool(v["exception"])) for v in violations),
                         [(5, True), (6, False)])

    def test_off(self):
        self.write({**GOOD_TREE, "_envcommon/x.hcl": "locals {}\n"})
        config = {**TERRAGRUNT, "rules": {**TERRAGRUNT["rules"], "no-envcommon": {"level": "off"}}}
        self.assertEqual(self.rules_hit(config), [])

    def test_exit_code_and_annotations(self):
        self.write({**GOOD_TREE, "_envcommon/x.hcl": "locals {}\n"})
        cfg = self.root / "c.json"
        cfg.write_text(json.dumps(TERRAGRUNT))
        env = {k: v for k, v in os.environ.items() if k != "GITHUB_STEP_SUMMARY"}
        proc = subprocess.run([sys.executable, str(ROOT / "src" / "conventions.py"), str(cfg), str(self.root)],
                              capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("::error file=_envcommon/x.hcl,line=1,title=no-envcommon::", proc.stdout)


def unit(module, extra=""):
    """A unit of the standard: root and its _common/units/<module>.hcl."""
    return ('include "root" {\n  path   = find_in_parent_folders("root.hcl")\n  expose = true\n}\n\n'
            f'include "unit" {{\n  path           = "${{get_repo_root()}}/_common/units/{module}.hcl"\n'
            '  merge_strategy = "deep"\n}\n\n' + extra + "inputs = {}\n")


def common_unit(module):
    return ('locals {\n  account = read_terragrunt_config(find_in_parent_folders("account.hcl")).locals\n}\n'
            f'terraform {{\n  source = "${{local.account.modules}}//azure/{module}?ref=${{local.account.modules_ref}}"\n}}\n')


I = "infrastructure/mg-corp"
STANDARD = {
    "units": ["infrastructure/**/terragrunt.hcl"],
    "rules": {"module-source": {}, "unit-assets": {}, "scope-layout": {}, "folder-dictionary": {}},
}
STANDARD_TREE = {
    **{f"_common/units/{m}.hcl": common_unit(m) for m in (
        "management-group", "subscription", "resource-group", "policy-assignments",
        "key-vault", "logic-app-workflow", "data-collection-rule")},
    f"{I}/mg.hcl": "locals {}\n",
    f"{I}/terragrunt.hcl": unit("management-group"),
    f"{I}/.terraform.lock.hcl": "# lock\n",
    f"{I}/global/policy-assignments/terragrunt.hcl": unit("policy-assignments"),
    f"{I}/mg-corp-platform/mg.hcl": "locals {}\n",
    f"{I}/mg-corp-platform/terragrunt.hcl": unit("management-group"),
    f"{I}/mg-corp-platform/sub-a/subscription.hcl": "locals {}\n",
    f"{I}/mg-corp-platform/sub-a/terragrunt.hcl": unit("subscription"),
    f"{I}/mg-corp-platform/sub-a/weu/region.hcl": "locals {}\n",
    f"{I}/mg-corp-platform/sub-a/weu/rg-web/rg.hcl": "locals {}\n",
    f"{I}/mg-corp-platform/sub-a/weu/rg-web/terragrunt.hcl": unit("resource-group"),
    f"{I}/mg-corp-platform/sub-a/weu/rg-web/key-vault/terragrunt.hcl": unit("key-vault"),
    f"{I}/mg-corp-platform/sub-a/weu/rg-web/data-collection-rule-syslog/terragrunt.hcl":
        unit("data-collection-rule"),
    f"{I}/mg-corp-platform/sub-a/weu/rg-web/logic-app-workflow/terragrunt.hcl":
        unit("logic-app-workflow", 'locals {\n  workflow = file("${get_terragrunt_dir()}/definition.json")\n}\n\n'),
    f"{I}/mg-corp-platform/sub-a/weu/rg-web/logic-app-workflow/definition.json": "{}\n",
}
WEB = f"{I}/mg-corp-platform/sub-a/weu/rg-web"


class Standard(Base):
    """The live repository after code/terragrunt.md: infrastructure/, _common/units/, scopes."""

    def hits(self, changes=None, drop=(), config=STANDARD):
        tree = {k: v for k, v in {**STANDARD_TREE, **(changes or {})}.items() if k not in drop}
        self.write(tree)
        return self.rules_hit(config)

    def test_standard_tree(self):
        self.assertEqual(self.hits(), [])

    def test_source_only_in_common_units(self):
        self.assertEqual(self.hits({f"{WEB}/key-vault/terragrunt.hcl": "inputs = {}\n"}),
                         [("module-source", f"{WEB}/key-vault/terragrunt.hcl", 1)])
        self.assertEqual(self.hits({"_common/units/key-vault.hcl":
                                    'terraform {\n  source = "../modules/key-vault"\n}\n'}),
                         [("module-source", "_common/units/key-vault.hcl", 2)])

    def test_unit_assets(self):
        self.assertEqual(self.hits({
            f"{WEB}/data-collection-rule-syslog/transform.kql": "Syslog\n",
            f"{WEB}/key-vault/definition.json": "{}\n",
            f"{I}/README.md": "# x\n",
        }, config={**STANDARD, "rules": {"unit-assets": {}}}), [
            ("unit-assets", f"{I}/README.md", 1),
            ("unit-assets", f"{WEB}/data-collection-rule-syslog/transform.kql", 1),
            ("unit-assets", f"{WEB}/key-vault/definition.json", 1),
        ])

    def test_dependency_in_a_common_unit_needs_its_mocks(self):
        config = {**STANDARD, "rules": {"dependencies-in-common": {"dir": "_common/deps"},
                                        "mock-outputs": {"dir": "_common/deps"}}}
        self.assertEqual(self.hits({"_common/units/key-vault.hcl": common_unit("key-vault")
                                    + 'dependency "rg" {\n  config_path = "../"\n}\n'}, config=config),
                         [("mock-outputs", "_common/units/key-vault.hcl", 7),
                          ("mock-outputs", "_common/units/key-vault.hcl", 7)])

    def test_unit_assets_outside_the_infrastructure_folder(self):
        self.assertEqual(self.hits({"tenant/apps/terragrunt.hcl": unit("key-vault"),
                                    "tenant/apps/cert.pem": "x\n"}),
                         [("unit-assets", "tenant/apps/cert.pem", 1)])

    def test_unit_assets_allowlist(self):
        config = {**STANDARD, "rules": {"unit-assets": {"allowed": ["definition.json", "ca.crt"]}}}
        self.assertEqual(self.hits({f"{WEB}/key-vault/ca.crt": "x\n",
                                    f"{WEB}/key-vault/terragrunt.hcl": unit("key-vault", '# ca.crt\n')},
                                   config=config), [])

    def test_scope_layout(self):
        self.assertEqual(self.hits({
            f"{I}/policy-assignments/terragrunt.hcl": unit("policy-assignments"),
            f"{I}/mg-corp-platform/sub-a/weu/storage/terragrunt.hcl": unit("key-vault"),
            f"{I}/mg-corp-platform/sub-a/global/notes.txt": "x\n",
            f"{I}/mg-corp-platform/sub-a/neu/rg-x/rg.hcl": "locals {}\n",
            f"{WEB}/key-vault/certificate/terragrunt.hcl": unit("key-vault"),
        }, config={**STANDARD, "rules": {"scope-layout": {}}}), [
            ("scope-layout", f"{I}/mg-corp-platform/sub-a/global/notes.txt", 1),
            ("scope-layout", f"{I}/mg-corp-platform/sub-a/neu/", 1),
            ("scope-layout", f"{WEB}/key-vault/", 1),
            ("scope-layout", f"{I}/mg-corp-platform/sub-a/weu/storage/", 1),
            ("scope-layout", f"{I}/policy-assignments/", 1),
        ])

    def test_scope_without_its_context_file(self):
        self.assertEqual(self.hits(drop=(f"{WEB}/rg.hcl",), config={**STANDARD, "rules": {"scope-layout": {}}}),
                         [("scope-layout", f"{WEB}/", 1)])

    def test_device_folder(self):
        tree = {"infrastructure/router/device.hcl": "locals {}\n",
                "infrastructure/router/dns/terragrunt.hcl": unit("key-vault"),
                "infrastructure/router/ca-bundle.crt": "x\n",
                "infrastructure/router/dns/static/terragrunt.hcl": unit("key-vault")}
        self.assertEqual(self.hits(tree, config={**STANDARD, "rules": {"scope-layout": {}}}), [
            ("scope-layout", "infrastructure/router/ca-bundle.crt", 1),
            ("scope-layout", "infrastructure/router/dns/", 1),
        ])

    def test_folder_dictionary(self):
        self.assertEqual(self.hits({
            f"{WEB}/kv/terragrunt.hcl": unit("key-vault"),
            f"{WEB}/key-vault-apps/terragrunt.hcl": unit("key-vault"),
            "_common/units/vault.hcl": common_unit("key-vault"),
            f"{WEB}/key-vault-old/terragrunt.hcl": unit("vault"),
        }, config={**STANDARD, "rules": {"folder-dictionary": {}}}), [
            ("folder-dictionary", "_common/units/vault.hcl", 1),
            ("folder-dictionary", f"{WEB}/kv/terragrunt.hcl", 1),
        ])

    def test_folder_dictionary_collection_folder(self):
        config = {**STANDARD, "rules": {"folder-dictionary": {}}}
        self.assertEqual(self.hits({"_common/units/repository.hcl": common_unit("repository"),
                                    "infrastructure/repositories/iac-home/terragrunt.hcl": unit("repository"),
                                    "infrastructure/repos/ci/terragrunt.hcl": unit("repository")},
                                   config=config),
                         [("folder-dictionary", "infrastructure/repos/ci/terragrunt.hcl", 1)])

    def test_folder_dictionary_with_a_folder_of_its_own(self):
        config = {**STANDARD, "rules": {"folder-dictionary": {"folders": {"key-vault": ["kv"]}}}}
        self.assertEqual(self.hits({f"{WEB}/kv/terragrunt.hcl": unit("key-vault")}, config=config), [])


def collection_unit(module):
    return (common_unit(module).replace("//azure/", "//azure/")
            + 'locals {\n  items = { for f in fileset(get_terragrunt_dir(), "**/*.yaml") :\n'
              '    trimsuffix(basename(f), ".yaml") => yamldecode(file("${get_terragrunt_dir()}/${f}")) }\n}\n')


RULE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "severity", "query"],
    "properties": {
        "name": {"type": "string", "minLength": 1},
        "severity": {"enum": ["Informational", "Low", "Medium", "High"]},
        "enabled": {"type": "boolean"},
        "query": {"type": "string"},
        "tactics": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
        "entityMappings": {"type": "array", "maxItems": 10, "items": {"$ref": "#/$defs/mapping"}},
    },
    "$defs": {"mapping": {"type": "object", "additionalProperties": False, "required": ["entityType"],
                          "properties": {"entityType": {"type": "string"}}}},
}
WS = "infrastructure/law-security"
RULE = "name: Break glass sign-in\nseverity: High\nenabled: true\nquery: |\n  SigninLogs\n  | take 1\n"
COLLECTION_TREE = {
    "_common/units/sentinel-analytic-rules.hcl": collection_unit("sentinel-analytic-rules"),
    "_common/units/logic-app-standard.hcl": common_unit("logic-app-standard")
    + 'locals {\n  workflows = fileset(get_terragrunt_dir(), "workflows/*/workflow.json")\n}\n',
    ".github/schemas/sentinel-analytic-rules.schema.json": json.dumps(RULE_SCHEMA),
    f"{WS}/analytic-rules/terragrunt.hcl": unit("sentinel-analytic-rules"),
    f"{WS}/analytic-rules/entra-id/break-glass-sign-in.yaml": RULE,
    f"{WS}/analytic-rules/home-network/nas-login-failures.yaml": RULE.replace("High", "Medium"),
    f"{WS}/logic-apps-standard/la-soar/terragrunt.hcl": unit("logic-app-standard"),
    f"{WS}/logic-apps-standard/la-soar/workflows/revoke-sessions/workflow.json": "{}\n",
}
COLLECTIONS = {"units": ["infrastructure/**/terragrunt.hcl"],
               "rules": {"unit-assets": {}, "collection-schema": {}}}


@unittest.skipUnless(shutil.which("yq"), "yq reads the YAML, as on the runner")
class Collections(Base):
    """One object per <key>.yaml, checked against the schema of the module (code/terragrunt.md)."""

    def hits(self, changes=None, drop=(), config=COLLECTIONS):
        tree = {k: v for k, v in {**COLLECTION_TREE, **(changes or {})}.items() if k not in drop}
        self.write(tree)
        return self.rules_hit(config)

    def messages(self, changes=None, config=COLLECTIONS):
        self.write({**COLLECTION_TREE, **(changes or {})})
        violations, errors = self.check(config)
        self.assertEqual(errors, [])
        return [v["message"] for v in violations]

    def test_collection_tree(self):
        self.assertEqual(self.hits(), [])

    def test_unknown_key_is_reported_on_its_line(self):
        path = f"{WS}/analytic-rules/entra-id/break-glass-sign-in.yaml"
        self.assertEqual(self.hits({path: RULE + "severty: Low\n"}), [("collection-schema", path, 7)])
        self.assertIn("/: unknown key 'severty' (allowed: enabled, entityMappings, name, query, "
                      "severity, tactics)", self.messages({path: RULE + "severty: Low\n"}))

    def test_values_against_the_schema(self):
        path = f"{WS}/analytic-rules/entra-id/break-glass-sign-in.yaml"
        bad = RULE.replace("High", "Critical").replace("enabled: true", "enabled: maybe") \
            + "tactics: [InitialAccess, InitialAccess]\nentityMappings:\n  - entityType: IP\n    columnName: x\n"
        self.assertEqual(sorted(self.messages({path: bad})), [
            "/enabled: is string, expected boolean",
            "/entityMappings/0: unknown key 'columnName' (allowed: entityType)",
            '/severity: "Critical" is not one of "Informational", "Low", "Medium", "High"',
            '/tactics/1: "InitialAccess" is repeated, the items are unique',
        ])

    def test_required_key(self):
        path = f"{WS}/analytic-rules/entra-id/break-glass-sign-in.yaml"
        self.assertEqual(self.messages({path: "name: x\nseverity: Low\n"}), ["/: required key 'query' is missing"])

    def test_collection_without_its_schema(self):
        self.assertEqual(self.hits(drop=(".github/schemas/sentinel-analytic-rules.schema.json",)),
                         [("collection-schema", f"{WS}/analytic-rules/terragrunt.hcl", 1)])

    def test_schema_folder_from_the_config(self):
        config = {**COLLECTIONS, "rules": {"collection-schema": {"schemas": "schemas"}}}
        self.assertEqual(self.hits({"schemas/sentinel-analytic-rules.schema.json": json.dumps(RULE_SCHEMA)},
                                   drop=(".github/schemas/sentinel-analytic-rules.schema.json",),
                                   config=config), [])

    def test_open_object_in_the_schema(self):
        schema = dict(RULE_SCHEMA, **{"$defs": {"mapping": {"type": "object"}}})
        path = ".github/schemas/sentinel-analytic-rules.schema.json"
        self.assertEqual(self.hits({path: json.dumps(schema)}), [("collection-schema", path, 1)])

    def test_unsupported_keyword_in_the_schema(self):
        schema = dict(RULE_SCHEMA, unevaluatedProperties=False)
        path = ".github/schemas/sentinel-analytic-rules.schema.json"
        self.assertEqual(self.messages({path: json.dumps(schema)}),
                         ["/: keyword 'unevaluatedProperties' is not supported by this validator"])

    def test_key_is_unique_across_subfolders(self):
        dup = f"{WS}/analytic-rules/home-network/break-glass-sign-in.yaml"
        self.assertEqual(self.hits({dup: RULE}), [
            ("collection-schema", f"{WS}/analytic-rules/entra-id/break-glass-sign-in.yaml", 1),
            ("collection-schema", dup, 1),
        ])

    def test_key_spelling_and_extension(self):
        self.assertEqual(self.hits({f"{WS}/analytic-rules/Break_Glass.yaml": RULE,
                                    f"{WS}/analytic-rules/other.yml": RULE}), [
            ("collection-schema", f"{WS}/analytic-rules/Break_Glass.yaml", 1),
            ("collection-schema", f"{WS}/analytic-rules/other.yml", 1),
            ("unit-assets", f"{WS}/analytic-rules/other.yml", 1),
        ])

    def test_one_document_per_file(self):
        path = f"{WS}/analytic-rules/entra-id/break-glass-sign-in.yaml"
        self.assertEqual(self.messages({path: RULE + "---\n" + RULE}),
                         ["holds more than one YAML document; one object is one file"])
        self.assertTrue(self.messages({path: "name: [x\n"})[0].startswith("does not parse as YAML"))

    def test_yaml_a_unit_does_not_read(self):
        path = f"{WS}/logic-apps-standard/la-soar/settings.yaml"
        self.assertEqual(self.hits({path: "a: 1\n"}, config={**COLLECTIONS, "rules": {"unit-assets": {}}}),
                         [("unit-assets", path, 1)])

    def test_logic_app_files(self):
        config = {**COLLECTIONS, "rules": {"unit-assets": {}}}
        la = f"{WS}/logic-apps-standard/la-soar"
        self.assertEqual(self.hits({f"{la}/workflows/revoke-sessions/connections.json": "{}\n",
                                    f"{la}/host.json": "{}\n"}, config=config),
                         [("unit-assets", f"{la}/host.json", 1),
                          ("unit-assets", f"{la}/workflows/revoke-sessions/connections.json", 1)])

    def test_catalogue_folder_outside_units(self):
        config = {"rules": {"collection-schema": {"collections": [
            {"path": "content/analytic-rules", "schema": "sentinel-analytic-rules"}]}}}
        self.assertEqual(self.hits({"content/analytic-rules/entra-id/ok.yaml": RULE,
                                    "content/analytic-rules/entra-id/bad.yaml": "name: x\n"},
                                   drop=[k for k in COLLECTION_TREE if k.startswith("infrastructure/")],
                                   config=config),
                         [("collection-schema", "content/analytic-rules/entra-id/bad.yaml", 1)] * 2)

    def test_exception_with_a_reason(self):
        path = f"{WS}/analytic-rules/entra-id/break-glass-sign-in.yaml"
        config = {**COLLECTIONS, "exceptions": [{"rule": "collection-schema", "path": path,
                                                 "reason": "Imported as it is; fixed in the next pull request."}]}
        self.assertEqual(self.hits({path: RULE + "severty: Low\n"}, config=config), [])

    def test_folder_without_the_platform_prefix(self):
        config = {**COLLECTIONS, "rules": {"folder-dictionary": {
            "prefixes": ["sentinel-"], "folders": {"logic-app-standard": ["la-*"]}}}}
        self.assertEqual(self.hits(config=config), [])
        self.assertEqual(self.hits({f"{WS}/rules/terragrunt.hcl": unit("sentinel-analytic-rules")}, config=config),
                         [("folder-dictionary", f"{WS}/rules/terragrunt.hcl", 1)])


TD = "TO" + "DO"


class EveryRepository(Base):
    """todo-issue and markdown-links: on without being named."""

    def hits(self, *rules, config=None):
        violations, errors = conventions.check(config or {}, str(self.root), only=list(rules))
        self.assertEqual(errors, [])
        return sorted((v["rule"], v["path"], v["line"]) for v in violations if not v["exception"])

    def test_todo_needs_its_issue(self):
        self.write({"a.py": f"# {TD}: later\n# {TD}(#12): drop once fixed\nx = 'todo'\n",
                    "b.sh": f"echo {TD}S\n", "README.md": f"{TD}: words in prose\n",
                    ".claude/rules/x.sh": f"# {TD}: managed copy\n"})
        self.assertEqual(self.hits("todo-issue"), [("todo-issue", "a.py", 1)])

    def test_todo_off_and_exceptions(self):
        self.write({"a.py": f"# {TD}: later\n"})
        self.assertEqual(self.hits("todo-issue", config={"rules": {"todo-issue": {"level": "off"}}}), [])
        cfg = {"exceptions": [{"rule": "todo-issue", "path": "a.py", "reason": "vendored"}]}
        self.assertEqual(self.hits("todo-issue", config=cfg), [])

    def test_links(self):
        self.write({
            "README.md": "# Title\n\n## How it fits\n\n"
                         "[ok](docs/setup.md) [dir](docs/) [anchor](#how-it-fits) [up](/docs/setup.md#first-step)\n"
                         "[web](https://example.com) [mail](mailto:x@example.com) ![img](docs/a.png)\n"
                         "[missing](docs/nope.md) [bad anchor](docs/setup.md#nothing) [out](../other/README.md)\n"
                         "`[code](not/checked.md)`\n\n```\n[fenced](not/checked.md)\n```\n"
                         "<!-- [commented](not/checked.md) -->\n[ref]: docs/gone.md\n",
            "docs/setup.md": "# Setup\n\n## First step\n",
            "docs/a.png": "png\n",
            ".claude/rules/x.md": "[managed](templates/x.md)\n",
        })
        self.assertEqual(self.hits("markdown-links"), [
            ("markdown-links", "README.md", 7), ("markdown-links", "README.md", 7),
            ("markdown-links", "README.md", 7), ("markdown-links", "README.md", 14)])

    def test_anchor_of_a_heading_with_code(self):
        self.write({"a.md": "# A\n\n## `_common/`\n\n[x](#_common)\n"})
        self.assertEqual(self.hits("markdown-links"), [])

    def test_slug_of_a_heading(self):
        self.assertEqual(conventions.slug("Plan and apply comments"), "plan-and-apply-comments")
        self.assertEqual(conventions.slug("`docs/` and [links](x.md)"), "docs-and-links")
        self.assertEqual(conventions.slug("Terragrunt: validate and verify"), "terragrunt-validate-and-verify")

    def test_only_and_skip(self):
        self.write({"a.py": f"# {TD}: later\n", "_envcommon/x.hcl": "locals {}\n"})
        config = {"rules": {"no-envcommon": {"level": "error"}}}
        violations, _ = conventions.check(config, str(self.root), skip=conventions.DEFAULT_ON)
        self.assertEqual({v["rule"] for v in violations}, {"no-envcommon"})
        violations, _ = conventions.check(config, str(self.root), only=["todo-issue"])
        self.assertEqual({v["rule"] for v in violations}, {"todo-issue"})


if __name__ == "__main__":
    unittest.main()
