#!/usr/bin/env python3
"""Tests of src/deps.py without the network: versions, cooldown and the edits.

Run: python3 tests/test_deps.py
"""
import datetime
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
import deps  # noqa: E402

NOW = datetime.datetime(2026, 12, 1, tzinfo=datetime.timezone.utc)


def rel(version, days_ago, **extra):
    return {"version": version, "published": NOW - datetime.timedelta(days=days_ago), **extra}


ENV = """# Pinned versions
TERRAFORM_VERSION=1.15.8

TERRAGRUNT_VERSION=1.1.3
TERRAGRUNT_SHA256=d5da6a66741f4ee752aa3b502b57e47fd6d5c178942861b2507f14f083e7606e
"""

YAML = """    env:
      GITLEAKS_VERSION: "8.30.1"
      GITLEAKS_SHA256: 551f6fc83ea457d62a0d98237cbad105af8d557003051f41f3e7ca7b3f2470eb
    steps:
      - run: fetch "$gh_dl/gitleaks/releases/download/v$GITLEAKS_VERSION/x.tgz"
"""

ROOT_HCL = """locals {
  account = read_terragrunt_config("account.hcl").locals

  versions = {
    azurerm = "5.4.0"
    azapi   = "2.12.0"

    # Only a unit that needs Microsoft Graph.
    msgraph = "0.6.0"
  }
}

generate "versions" {
  contents = templatefile("versions.tf.tmpl", local.versions)
}
"""

ACCOUNT_HCL = """locals {
  versions = {
    terraform   = ">= 1.13"
    routeros_os = "7.25"
    routeros    = "1.99.1"
  }
}
"""

TMPL = """terraform {
  required_providers {
    azurerm     = { source = "hashicorp/azurerm", version = "${azurerm}" }
    github = {
      source  = "integrations/github"
      version = "${github}"
    }
  }
}
"""

OLD = "3d3c42e5aac5ba805825da76410c181273ba90b1"
NEW = "a" * 40
WORKFLOW = f"""steps:
  - uses: actions/checkout@{OLD} # v7.0.1
  - uses: actions/checkout@{OLD} # v7.0.1 # zizmor: ignore[artipacked]
  - uses: actions/cache/restore@{"b" * 40} # v6.1.0
  - uses: bearclave/ci/.github/workflows/security.yml@v2
  - uses: bearclave/other/action@{"c" * 40} # v1.0.0
"""


class Versions(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(deps.parse("v1.2.3"), (1, 2, 3))
        self.assertEqual(deps.parse("1.26.902.1"), (1, 26, 902, 1))
        self.assertIsNone(deps.parse("v1.2.3-rc1"))
        self.assertIsNone(deps.parse("nightly"))

    def test_cooldown(self):
        same, major = deps.pick("1.0.0", [rel("1.1.0", 6), rel("1.0.5", 7)], NOW)
        self.assertEqual(same["version"], "1.0.5")
        self.assertIsNone(major)

    def test_same_major_and_newer_major(self):
        releases = [rel("1.2.0", 30), rel("2.0.0", 20), rel("2.1.0", 10), rel("1.3.0", 8), rel("0.9.0", 90)]
        same, major = deps.pick("1.1.0", releases, NOW)
        self.assertEqual(same["version"], "1.3.0")
        self.assertEqual(major["version"], "2.1.0")

    def test_nothing_newer(self):
        self.assertEqual(deps.pick("v7.0.1", [rel("v7.0.1", 100), rel("v6.1.0", 100)], NOW), (None, None))

    def test_prerelease_and_unknown_current(self):
        self.assertEqual(deps.pick("1.0.0", [rel("1.1.0-beta", 30)], NOW), (None, None))
        self.assertEqual(deps.pick("latest", [rel("1.1.0", 30)], NOW), (None, None))

    def test_when(self):
        self.assertEqual(deps.when("2026-11-05T12:50:25Z").year, 2026)
        self.assertEqual(deps.when("2026-11-19T09:21:39.1234567").tzinfo, datetime.timezone.utc)


class Tools(unittest.TestCase):
    def test_pins(self):
        self.assertEqual(deps.tool_pins(ENV), {"TERRAFORM": "1.15.8", "TERRAGRUNT": "1.1.3"})
        self.assertEqual(deps.tool_pins(YAML), {"GITLEAKS": "8.30.1"})

    def test_set_env(self):
        out = deps.set_tool(ENV, "TERRAGRUNT", "1.2.2", "f" * 64)
        self.assertIn("TERRAGRUNT_VERSION=1.2.2\n", out)
        self.assertIn("TERRAGRUNT_SHA256=" + "f" * 64 + "\n", out)
        self.assertIn("TERRAFORM_VERSION=1.15.8\n", out)
        out = deps.set_tool(ENV, "TERRAFORM", "1.17.2")
        self.assertIn("TERRAFORM_VERSION=1.17.2\n", out)
        self.assertEqual(out.count("\n"), ENV.count("\n"))

    def test_set_yaml_keeps_quotes_and_uses(self):
        out = deps.set_tool(YAML, "GITLEAKS", "8.31.0", "e" * 64)
        self.assertIn('GITLEAKS_VERSION: "8.31.0"\n', out)
        self.assertIn("GITLEAKS_SHA256: " + "e" * 64 + "\n", out)
        self.assertIn("v$GITLEAKS_VERSION/x.tgz", out)

    def test_missing_key(self):
        with self.assertRaises(KeyError):
            deps.set_tool(ENV, "TFLINT", "1.0.0", "a" * 64)

    def test_sha256_from_digest(self):
        up = deps.Upstream("")
        release = {"version": "1.2.2", "assets": [
            {"name": "terragrunt_linux_amd64", "digest": "sha256:" + "d" * 64, "browser_download_url": "x"}]}
        self.assertEqual(up.sha256("TERRAGRUNT", release), "d" * 64)
        self.assertIsNone(up.sha256("TERRAFORM", release))
        with self.assertRaises(LookupError):
            up.sha256("TRIVY", release)


class Providers(unittest.TestCase):
    def test_pins_exact_only(self):
        self.assertEqual(deps.provider_pins(ROOT_HCL), {"azurerm": "5.4.0", "azapi": "2.12.0", "msgraph": "0.6.0"})
        self.assertEqual(deps.provider_pins(ACCOUNT_HCL), {"routeros": "1.99.1"})
        self.assertEqual(deps.provider_pins("locals {}"), {})

    def test_set_provider(self):
        out = deps.set_provider(ROOT_HCL, "azurerm", "5.13.0")
        self.assertIn('azurerm = "5.13.0"', out)
        self.assertIn('azapi   = "2.12.0"', out)
        self.assertEqual(len(out), len(ROOT_HCL) + 1)
        with self.assertRaises(KeyError):
            deps.set_provider(ROOT_HCL, "github", "6.0.0")

    def test_sources(self):
        self.assertEqual(deps.provider_sources(TMPL), {"azurerm": "hashicorp/azurerm", "github": "integrations/github"})

    def test_module_bounds(self):
        text = 'terraform {\n  required_providers {\n    azurerm = { source = "hashicorp/azurerm", version = ">= 5.0" }\n' \
               '    azapi = {\n      source  = "Azure/azapi"\n      version = "~> 2.12"\n    }\n  }\n}\n'
        self.assertEqual(deps.module_bounds(text),
                         [("azurerm", "hashicorp/azurerm", ">= 5.0"), ("azapi", "Azure/azapi", "~> 2.12")])


class Actions(unittest.TestCase):
    def test_pins(self):
        self.assertEqual(deps.action_pins(WORKFLOW), {("actions/checkout", OLD, "v7.0.1"),
                                                      ("actions/cache", "b" * 40, "v6.1.0")})

    def test_set_action(self):
        out = deps.set_action(WORKFLOW, "actions/checkout", OLD, NEW, "v7.0.2")
        self.assertEqual(out.count(f"actions/checkout@{NEW} # v7.0.2"), 2)
        self.assertIn("v7.0.2 # zizmor: ignore[artipacked]", out)
        self.assertIn(f"actions/cache/restore@{'b' * 40} # v6.1.0", out)
        out = deps.set_action(WORKFLOW, "actions/cache", "b" * 40, NEW, "v6.2.0")
        self.assertIn(f"actions/cache/restore@{NEW} # v6.2.0", out)


class PlanRepo(unittest.TestCase):
    class FakeUp:
        def tool(self, name):
            return {"TERRAFORM": [rel("1.17.2", 20), rel("2.0.0", 30)],
                    "TERRAGRUNT": [rel("1.2.2", 10, assets=[
                        {"name": "terragrunt_linux_amd64", "digest": "sha256:" + "9" * 64}])]}[name]

        def sha256(self, name, release):
            return deps.Upstream.sha256(self, name, release)

        def provider(self, source):
            return {"hashicorp/azurerm": [rel("5.13.0", 8), rel("5.14.0", 2)],
                    "Azure/azapi": [rel("2.12.0", 100)],
                    "microsoft/msgraph": [rel("1.0.0", 30)]}[source]

    def test_tools_and_providers(self):
        files = {".github/tool-versions.env": ENV, "root.hcl": ROOT_HCL,
                 "_common/templates/versions.tf.tmpl": TMPL.replace('azurerm     = {', 'azapi = { source = "Azure/azapi" }\n    azurerm     = {')}
        plan, originals = deps.plan_repo("iac-x", files.get, "tools", self.FakeUp(), NOW)
        changed = plan.changed(originals)
        self.assertEqual(sorted(changed), [".github/tool-versions.env", "root.hcl"])
        self.assertIn("TERRAFORM_VERSION=1.17.2", changed[".github/tool-versions.env"])
        self.assertIn("TERRAGRUNT_SHA256=" + "9" * 64, changed[".github/tool-versions.env"])
        self.assertIn('azurerm = "5.13.0"', changed["root.hcl"])
        self.assertIn('msgraph = "0.6.0"', changed["root.hcl"])
        self.assertEqual([m[2] for m in plan.majors], ["2.0.0", "1.0.0"])
        body = deps.body(plan, "tools", [])
        for section in ("Why", "What changes", "Impact", "Verification", "Review", "Links"):
            self.assertIn(f"## {section}\n", body)
        self.assertLessEqual(len(deps.TITLES["tools"]), 72)
        self.assertLessEqual(len(deps.TITLES["actions"]), 72)

    def test_actions_only_in_ci(self):
        plan, originals = deps.plan_repo("iac-x", {}.get, "actions", self.FakeUp(), NOW, lambda: [])
        self.assertEqual(plan.changed(originals), {})


if __name__ == "__main__":
    unittest.main()
