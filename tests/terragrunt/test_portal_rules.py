#!/usr/bin/env python3
"""Tests of portal-rules.py without a workspace: what counts as managed.

Run: python3 tests/terragrunt/test_portal_rules.py
"""
import importlib.util
import pathlib
import unittest

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("portal_rules", pathlib.Path(__file__).resolve().parents[2] / "src" / "terragrunt" / "portal-rules.py")
portal_rules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(portal_rules)

WS = "/subscriptions/0/resourceGroups/rg/providers/Microsoft.OperationalInsights/workspaces/law"
PLAN = {"planned_values": {"root_module": {"child_modules": [{"resources": [
    {"type": "azapi_resource", "values": {"type": "Microsoft.SecurityInsights/alertRules@2025-10-01-preview",
                                          "name": "aaa", "parent_id": WS}},
    {"type": "azapi_resource", "values": {"type": "Microsoft.SecurityInsights/automationRules@2024-09-01",
                                          "name": "zzz", "parent_id": WS}},
]}], "resources": [
    {"type": "azapi_resource", "values": {"type": "Microsoft.SecurityInsights/alertRules@2025-10-01-preview",
                                          "name": "bbb", "parent_id": WS}},
]}}}


class PortalRules(unittest.TestCase):
    def test_managed_rules_and_workspace(self):
        workspace, names = portal_rules.managed(PLAN)
        self.assertEqual(workspace, WS)
        self.assertEqual(names, {"aaa", "bbb"})

    def test_unmanaged(self):
        rules = [{"name": "aaa", "kind": "Scheduled", "properties": {"displayName": "Mine"}},
                 {"name": "ccc", "kind": "Fusion", "properties": {"displayName": "Advanced Multistage", "enabled": True}},
                 {"name": "ddd", "kind": "Scheduled", "properties": {"displayName": "Clicked together", "enabled": False}}]
        # Fusion is built in: Sentinel creates and keeps it by itself.
        self.assertEqual(portal_rules.unmanaged(rules, {"aaa", "bbb"}), [
            ("ddd", "Scheduled", "Clicked together", False),
        ])


if __name__ == "__main__":
    unittest.main()
