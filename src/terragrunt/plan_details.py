"""Reads a terraform plan in JSON and says in plain words what it changes.

plan-summary.py lists the resources of a unit from terraform's text output.
With the plan of the unit in JSON (`terragrunt run --all plan --json-out-dir`)
it uses this instead: one row per resource with what kind of thing it is, its
name, and for an update the attributes that change, from what to what.

The JSON holds sensitive values in clear text. Nothing marked sensitive by
terraform is ever printed, and the JSON itself never leaves the runner.
"""
import json
import re

# Shown instead of the resource type where the type alone says little.
KINDS = {
    "azurerm_resource_group": "resource group",
    "azurerm_role_assignment": "role assignment",
    "azurerm_pim_eligible_role_assignment": "eligible role assignment",
    "azurerm_role_definition": "role definition",
    "azurerm_key_vault": "Key Vault",
    "azurerm_key_vault_certificate": "Key Vault certificate",
    "azurerm_key_vault_secret": "Key Vault secret",
    "azurerm_log_analytics_workspace": "Log Analytics workspace",
    "azurerm_monitor_diagnostic_setting": "diagnostic setting",
    "azurerm_monitor_action_group": "action group",
    "azurerm_monitor_metric_alert": "metric alert",
    "azurerm_monitor_scheduled_query_rules_alert_v2": "log alert",
    "azurerm_management_lock": "lock",
    "azurerm_management_group": "management group",
    "azurerm_management_group_policy_assignment": "policy assignment",
    "azurerm_storage_account": "storage account",
    "azurerm_static_web_app": "Static Web App",
    "azurerm_user_assigned_identity": "managed identity",
    "azurerm_federated_identity_credential": "federated credential",
    "azurerm_logic_app_workflow": "Logic App",
    "azurerm_application_insights": "Application Insights",
    "azurerm_sentinel_alert_rule_scheduled": "analytic rule",
    "azurerm_sentinel_alert_rule_nrt": "analytic rule (NRT)",
    "azurerm_sentinel_automation_rule": "automation rule",
    "azurerm_sentinel_watchlist": "watchlist",
    "azurerm_sentinel_watchlist_item": "watchlist item",
    "azurerm_application_insights_workbook": "workbook",
    "azuread_group": "Entra group",
    "azuread_group_member": "group membership",
    "azuread_application": "application",
    "azuread_service_principal": "service principal",
    "azuread_application_federated_identity_credential": "federated credential",
    "azuread_application_certificate": "application certificate",
    "azuread_app_role_assignment": "API permission grant",
    "azuread_directory_role_assignment": "Entra role assignment",
    "azuread_user": "user",
    "azuread_invitation": "guest invitation",
    "azuread_administrative_unit": "administrative unit",
}
NAME_KEYS = ("display_name", "name", "user_principal_name", "role_definition_name")
# azapi_resource says what it is in its `type`; shown like the azurerm kinds.
AZAPI_KINDS = {
    "Microsoft.SecurityInsights/alertRules": "analytic rule",
    "Microsoft.SecurityInsights/automationRules": "automation rule",
    "Microsoft.SecurityInsights/watchlists": "watchlist",
    "Microsoft.SecurityInsights/dataConnectors": "data connector",
    "Microsoft.SecurityInsights/onboardingStates": "Sentinel onboarding",
    "Microsoft.SecurityInsights/settings": "Sentinel setting",
    "Microsoft.Insights/workbooks": "workbook",
    "Microsoft.Insights/diagnosticSettings": "diagnostic setting",
    "Microsoft.OperationalInsights/workspaces/tables": "Log Analytics table",
}
# Attributes azapi fills from the response; their change says nothing.
COMPUTED = {"azapi_resource": {"output"}}
VERBS = {
    ("create",): "create",
    ("update",): "update",
    ("delete",): "destroy",
    ("delete", "create"): "replace",
    ("create", "delete"): "replace",
    ("read",): "read",
    ("no-op",): None,
}
REASONS = {
    "delete_because_no_resource_config": "no longer in the code",
    "delete_because_no_module": "its module is no longer in the code",
    "delete_because_each_key": "its key is no longer in the for_each",
    "delete_because_count_index": "its index is beyond the count",
    "delete_because_wrong_repetition": "count and for_each swapped",
    "replace_because_tainted": "tainted",
    "replace_by_request": "replace requested",
    "replace_by_triggers": "a replace_triggered_by changed",
}
MAX_VALUE = 60
MAX_ATTRIBUTES = 8
RE_RESOURCE_ID = re.compile(r"/subscriptions/[^/\s]+(?:/resourceGroups/[^/\s]+)?/providers/\S+", re.I)


def code(text: str) -> str:
    tick = "``" if "`" in text else "`"
    pad = " " if tick == "``" else ""
    return f"{tick}{pad}{text}{pad}{tick}"


def kind(rtype: str, values=None) -> str:
    if rtype == "azapi_resource" and isinstance(values, dict) and values.get("type"):
        api = str(values["type"]).split("@")[0]
        if api in AZAPI_KINDS:
            return AZAPI_KINDS[api]
        last = api.rsplit("/", 1)[-1]
        return re.sub(r"(?<!^)([A-Z])", r" \1", last).lower().rstrip("s")
    if rtype in KINDS:
        return KINDS[rtype]
    return re.sub(r"^(azurerm|azuread|azapi|random|time|terraform)_", "", rtype).replace("_", " ")


def short_id(value: str) -> str:
    """The last name in an Azure resource ID; the ID itself otherwise."""
    if isinstance(value, str) and value.startswith("/") and "/" in value[1:]:
        return value.rstrip("/").rsplit("/", 1)[-1]
    return value


def name_of(rc: dict) -> str:
    """A name a person would recognise, taken from the values of the resource."""
    values = rc["change"].get("after") or rc["change"].get("before") or {}
    sensitive = rc["change"].get("after_sensitive") or rc["change"].get("before_sensitive") or {}
    index = rc.get("index")
    if rc["type"] in ("azurerm_role_assignment", "azurerm_pim_eligible_role_assignment") \
            and isinstance(index, str) and "/" in index:
        role, principal = index.split("/", 1)
        scope = short_id(values.get("scope", "")) if isinstance(values, dict) else ""
        return f"{code(role)} for {code(principal)}" + (f" on {code(scope)}" if scope else "")
    if rc["type"] == "azapi_resource" and isinstance(values, dict):
        props = ((values.get("body") or {}).get("properties") or {}) \
            if isinstance(values.get("body"), dict) else {}
        display = props.get("displayName") or props.get("display_name")
        if isinstance(display, str) and display:
            return code(display)
    if isinstance(values, dict):
        for key in NAME_KEYS:
            v = values.get(key)
            if isinstance(v, str) and v and not (isinstance(sensitive, dict) and sensitive.get(key)):
                return code(v)
    if isinstance(index, str):
        return code(index)
    return ""


def crop(before: str, after: str):
    """Two long strings cut to where they start to differ."""
    if len(before) <= MAX_VALUE and len(after) <= MAX_VALUE:
        return before, after
    same = 0
    while same < min(len(before), len(after)) and before[same] == after[same]:
        same += 1
    start = max(0, same - 20)
    if start == 0:
        return before, after
    return "…" + before[start:], "…" + after[start:]


def fmt(value, sensitive=False, unknown=False) -> str:
    if sensitive:
        return "(sensitive)"
    if unknown:
        return "(known after apply)"
    if value is None:
        return "null"
    if isinstance(value, str):
        text = short_id(value) if RE_RESOURCE_ID.fullmatch(value) else value
    else:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    text = text.replace("\n", "⏎")
    if len(text) > MAX_VALUE:
        text = text[: MAX_VALUE - 1] + "…"
    return code(text)


def at(tree, path):
    """The value at a path in before/after or in a sensitive/unknown map."""
    for step in path:
        if isinstance(tree, dict):
            tree = tree.get(step)
        elif isinstance(tree, list) and isinstance(step, int) and step < len(tree):
            tree = tree[step]
        else:
            return None
    return tree


def flagged(tree, path) -> bool:
    """True when the path or anything above it is marked in a sensitive/unknown map."""
    if tree is True:
        return True
    for step in path:
        if isinstance(tree, dict):
            tree = tree.get(step)
        elif isinstance(tree, list) and isinstance(step, int) and step < len(tree):
            tree = tree[step]
        else:
            return False
        if tree is True:
            return True
    return False


def anything_flagged(tree, path) -> bool:
    """True when the path, anything above it or anything below it is marked.

    A sensitive map can mark single elements of a list or keys of an object,
    so a value is shown only when nothing inside it is sensitive either.
    """
    if flagged(tree, path):
        return True
    return any(True for _ in leaves(at(tree, path) or {}))


def scalars(values) -> bool:
    return isinstance(values, list) and all(not isinstance(v, (dict, list)) for v in values)


def diff(before, after, path=()):
    """Paths whose value differs, down to the leaves; lists of scalars stay whole."""
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(set(before) | set(after)):
            yield from diff(before.get(key), after.get(key), path + (key,))
    elif (isinstance(before, list) and isinstance(after, list) and not scalars(before)
          and not scalars(after) and len(before) == len(after)):
        for i, (b, a) in enumerate(zip(before, after)):
            yield from diff(b, a, path + (i,))
    elif before != after:
        yield path


def label(path) -> str:
    if len(path) == 2 and path[0] == "tags":
        return f"tag {code(path[1])}"
    text = ""
    for step in path:
        text += f"[{step}]" if isinstance(step, int) else (("." if text else "") + step)
    return code(text)


def attribute_changes(change: dict, skip=()) -> list:
    """`attribute: from → to` for every attribute an update changes."""
    before, after = change.get("before") or {}, change.get("after") or {}
    bs, as_ = change.get("before_sensitive") or {}, change.get("after_sensitive") or {}
    unknown = change.get("after_unknown") or {}
    out = []
    paths = list(diff(before, after))
    # An attribute only known after apply has no value yet in `after`.
    paths += [p for p in leaves(unknown) if p not in paths and at(before, p) is not None]
    paths = [p for p in paths if not p or p[0] not in skip]
    for path in sorted(paths, key=lambda p: [str(x) for x in p]):
        b, a = at(before, path), at(after, path)
        sens = anything_flagged(bs, path) or anything_flagged(as_, path)
        unk = flagged(unknown, path)
        if sens:
            out.append(f"{label(path)} changes (sensitive)")
        elif scalars(b) and scalars(a) and not unk:
            added = [x for x in a if x not in b]
            removed = [x for x in b if x not in a]
            parts = [f"+{fmt(x)}" for x in added] + [f"−{fmt(x)}" for x in removed]
            out.append(f"{label(path)}: " + (", ".join(parts) if parts else "order changes"))
        elif b is None and not unk:
            out.append(f"{label(path)}: set to {fmt(a)}")
        elif a is None and not unk:
            out.append(f"{label(path)}: {fmt(b)} removed")
        elif isinstance(b, str) and isinstance(a, str):
            b, a = crop(b, a)
            out.append(f"{label(path)}: {fmt(b)} → {fmt(a)}")
        else:
            out.append(f"{label(path)}: {fmt(b)} → {fmt(a, unknown=unk)}")
    if len(out) > MAX_ATTRIBUTES:
        rest = len(out) - MAX_ATTRIBUTES
        out = out[:MAX_ATTRIBUTES] + [f"and {rest} more, see the attribute diff"]
    return out


def leaves(tree, path=()):
    if tree is True:
        yield path
    elif isinstance(tree, dict):
        for k, v in tree.items():
            yield from leaves(v, path + (k,))
    elif isinstance(tree, list):
        for i, v in enumerate(tree):
            yield from leaves(v, path + (i,))


def rows(plan: dict) -> list:
    """(verb, what, how) for every resource the plan touches, destroys first."""
    order = {"destroy": 0, "replace": 1, "update": 2, "create": 3, "import": 4,
             "move": 5, "forget": 6, "read": 7}
    out = []
    for rc in plan.get("resource_changes", []):
        change = rc["change"]
        actions = tuple(change.get("actions", []))
        verb = VERBS.get(actions, "/".join(actions))
        if actions == ("forget",):
            verb = "forget"
        moved = rc.get("previous_address") and rc["previous_address"] != rc["address"]
        imported = bool(change.get("importing"))
        if verb is None:
            if imported:
                verb = "import"
            elif moved:
                verb = "move"
            else:
                continue
        if rc.get("mode") == "data" and verb == "read":
            continue
        name = name_of(rc)
        values = change.get("after") or change.get("before")
        what = kind(rc["type"], values) + (f" {name}" if name else "")
        skip = COMPUTED.get(rc["type"], set())
        notes = []
        if verb == "update" or (verb in ("import", "move") and change.get("before") != change.get("after")):
            notes += attribute_changes(change, skip)
        if verb == "replace":
            paths = [tuple(p) for p in change.get("replace_paths") or []]
            if paths:
                notes.append("because " + ", ".join(label(p) for p in paths) + " changes")
            notes += [n for n in attribute_changes(change, skip)
                      if any(n.startswith(label(p)) for p in paths)]
        reason = REASONS.get(rc.get("action_reason", ""))
        if reason and verb in ("destroy", "replace"):
            notes.append(reason)
        if moved:
            notes.append(f"moved from {code(rc['previous_address'])}")
        if imported and verb != "import":
            notes.append("imported")
        out.append((verb, what, rc["address"], notes))
    out.sort(key=lambda r: (order.get(r[0], 9), r[2]))
    return out


def outputs(plan: dict) -> list:
    return sorted(k for k, v in (plan.get("output_changes") or {}).items()
                  if v.get("actions") not in (["no-op"], None))


def table(plan: dict) -> list:
    """Markdown lines for one unit: a row per resource, then the outputs."""
    lines = ["| | Resource | What changes |", "|---|---|---|"]
    for verb, what, address, notes in rows(plan):
        cell = "<br>".join(n.replace("|", "\\|") for n in notes)
        lines.append(f"| {verb} | {what.replace('|', chr(92) + '|')}<br><sub>{code(address)}</sub> | {cell} |")
    if len(lines) == 2:
        lines = []
    if outs := outputs(plan):
        lines += ["", "Outputs that change: " + ", ".join(code(o) for o in outs)]
    return lines


def load(path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
