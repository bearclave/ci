"""Reads a terraform plan in JSON and says in plain words what it changes.

One entry per resource the plan touches: an icon for the action, what kind of
thing it is (a dictionary of the resource types the organisation uses; any
other type is spelled out from its name), the name a person knows it by, and
one row per change that matters - "tag `owner`" from `a` to `b`, "setting
`public_network_access_enabled`", "setting `topics`: added `x`". The noise of a
plan is left out: values only known after apply, computed IDs, etags, URLs and
timestamps, outputs, data sources, and imports that change nothing (counted
instead).

The JSON holds sensitive values in clear text. Nothing marked sensitive by
terraform is ever printed, and the JSON itself never leaves the runner; the
entries (changes.json) hold only the text the comment shows.
"""
import json
import re

ICONS = {"create": "➕", "update": "✏️", "destroy": "🗑️", "replace": "♻️",
         "import": "📥", "move": "🚚", "forget": "⏏️"}
# The order of the tables: what cannot be undone first.
ORDER = {"destroy": 0, "replace": 1, "update": 2, "create": 3, "import": 4, "move": 5,
         "forget": 6}

KINDS = {
    # Azure
    "azurerm_resource_group": "Resource group",
    "azurerm_role_assignment": "Role assignment",
    "azurerm_pim_eligible_role_assignment": "Eligible role assignment",
    "azurerm_role_definition": "Role definition",
    "azurerm_key_vault": "Key Vault",
    "azurerm_key_vault_certificate": "Key Vault certificate",
    "azurerm_key_vault_secret": "Key Vault secret",
    "azurerm_log_analytics_workspace": "Log Analytics workspace",
    "azurerm_monitor_diagnostic_setting": "Diagnostic settings",
    "azurerm_monitor_aad_diagnostic_setting": "Entra diagnostic settings",
    "azurerm_monitor_action_group": "Action group",
    "azurerm_monitor_metric_alert": "Metric alert",
    "azurerm_monitor_scheduled_query_rules_alert_v2": "Log alert",
    "azurerm_monitor_data_collection_rule": "Data collection rule",
    "azurerm_monitor_data_collection_endpoint": "Data collection endpoint",
    "azurerm_management_lock": "Lock",
    "azurerm_management_group": "Management group",
    "azurerm_management_group_subscription_association": "Subscription placement",
    "azurerm_management_group_policy_assignment": "Policy assignment",
    "azurerm_resource_policy_exemption": "Policy exemption",
    "azurerm_resource_provider_registration": "Resource provider",
    "azurerm_security_center_subscription_pricing": "Defender plan",
    "azurerm_security_center_contact": "Defender contact",
    "azurerm_consumption_budget_subscription": "Budget",
    "azurerm_storage_account": "Storage account",
    "azurerm_storage_container": "Storage container",
    "azurerm_static_web_app": "Static Web App",
    "azurerm_static_web_app_custom_domain": "Custom domain",
    "azurerm_user_assigned_identity": "Managed identity",
    "azurerm_federated_identity_credential": "Federated credential",
    "azurerm_logic_app_workflow": "Logic App",
    "azurerm_application_insights": "Application Insights",
    "azurerm_application_insights_workbook": "Workbook",
    "azurerm_application_insights_standard_web_test": "Availability test",
    "azurerm_bot_service_azure_bot": "Bot",
    "azurerm_bot_channel_ms_teams": "Bot Teams channel",
    "azurerm_sentinel_log_analytics_workspace_onboarding": "Sentinel onboarding",
    "azurerm_sentinel_alert_rule_scheduled": "Analytic rule",
    "azurerm_sentinel_alert_rule_nrt": "Analytic rule (NRT)",
    "azurerm_sentinel_automation_rule": "Automation rule",
    "azurerm_sentinel_watchlist": "Watchlist",
    "azurerm_sentinel_watchlist_item": "Watchlist item",
    # Entra
    "azuread_group": "Entra group",
    "azuread_group_member": "Group member",
    "azuread_application": "App registration",
    "azuread_application_from_template": "App registration",
    "azuread_application_identifier_uri": "Application ID URI",
    "azuread_application_federated_identity_credential": "Federated credential",
    "azuread_application_certificate": "Application certificate",
    "azuread_service_principal": "Enterprise application",
    "azuread_service_principal_token_signing_certificate": "Token signing certificate",
    "azuread_service_principal_delegated_permission_grant": "Delegated permission grant",
    "azuread_app_role_assignment": "API permission grant",
    "azuread_directory_role": "Entra role",
    "azuread_directory_role_assignment": "Entra role assignment",
    "azuread_custom_directory_role": "Custom Entra role",
    "azuread_user": "User",
    "azuread_invitation": "Guest invitation",
    "azuread_administrative_unit": "Administrative unit",
    "azuread_administrative_unit_member": "Administrative unit member",
    "azuread_administrative_unit_role_member": "Administrative unit role",
    "msgraph_update_resource": "Graph setting",
    "msgraph_resource_action": "Graph action",
    "azapi_update_resource": "Azure setting",
    "azapi_resource_action": "Azure action",
    # GitHub
    "github_repository": "Repository",
    "github_repository_environment": "Environment",
    "github_repository_environment_deployment_policy": "Deployment branch policy",
    "github_repository_deploy_key": "Deploy key",
    "github_repository_dependabot_security_updates": "Dependabot security updates",
    "github_repository_custom_property": "Repository property",
    "github_repository_ruleset": "Repository ruleset",
    "github_organization_settings": "Organisation settings",
    "github_organization_ruleset": "Organisation ruleset",
    "github_organization_custom_properties": "Organisation property",
    "github_team": "Team",
    "github_team_members": "Team members",
    "github_team_repository": "Team access",
    "github_issue_labels": "Labels",
    "github_actions_variable": "Actions variable",
    "github_actions_secret": "Actions secret",
    "github_actions_organization_variable": "Organisation variable",
    "github_actions_organization_permissions": "Actions permissions",
    "github_actions_organization_workflow_permissions": "Workflow permissions",
    "github_actions_repository_access_level": "Actions access",
    "github_branch_protection": "Branch protection",
    # RouterOS (iac-home)
    "routeros_ip_address": "IP address",
    "routeros_ip_route": "Route",
    "routeros_ip_pool": "IP pool",
    "routeros_ip_dns": "DNS",
    "routeros_ip_dns_record": "DNS record",
    "routeros_ip_dns_adlist": "DNS block list",
    "routeros_ip_dhcp_server": "DHCP server",
    "routeros_ip_dhcp_server_network": "DHCP network",
    "routeros_ip_dhcp_server_lease": "DHCP lease",
    "routeros_ip_firewall_filter": "Firewall rule",
    "routeros_ip_firewall_nat": "NAT rule",
    "routeros_ip_firewall_addr_list": "Firewall address list",
    "routeros_ip_service": "Router service",
    "routeros_ip_ssh_server": "SSH server",
    "routeros_ip_settings": "IP settings",
    "routeros_ipv6_settings": "IPv6 settings",
    "routeros_ip_upnp": "UPnP",
    "routeros_ip_neighbor_discovery_settings": "Neighbour discovery",
    "routeros_interface_bridge": "Bridge",
    "routeros_interface_bridge_port": "Bridge port",
    "routeros_interface_bridge_vlan": "Bridge VLAN",
    "routeros_interface_vlan": "VLAN",
    "routeros_interface_list": "Interface list",
    "routeros_interface_list_member": "Interface list member",
    "routeros_interface_wireguard": "WireGuard interface",
    "routeros_interface_wireguard_peer": "WireGuard peer",
    "routeros_wifi": "Wi-Fi interface",
    "routeros_wifi_configuration": "Wi-Fi configuration",
    "routeros_wifi_security": "Wi-Fi security",
    "routeros_wifi_datapath": "Wi-Fi datapath",
    "routeros_wifi_channel": "Wi-Fi channel",
    "routeros_wifi_steering": "Wi-Fi steering",
    "routeros_wifi_access_list": "Wi-Fi access rule",
    "routeros_system_identity": "Router name",
    "routeros_system_clock": "Clock",
    "routeros_system_ntp_client": "NTP client",
    "routeros_system_ntp_server": "NTP server",
    "routeros_system_logging": "Logging rule",
    "routeros_system_logging_action": "Logging target",
    "routeros_system_certificate": "Router certificate",
    "routeros_tool_mac_server": "MAC server",
    "routeros_tool_mac_server_winbox": "MAC Winbox",
    "routeros_tool_bandwidth_server": "Bandwidth server",
    "routeros_move_items": "Rule order",
    "acme_registration": "ACME account",
    "acme_certificate": "Certificate",
    # Others
    "random_password": "Generated password",
    "terraform_data": "Data",
    "time_rotating": "Rotation timer",
    "time_sleep": "Wait",
}
# azapi_resource says what it is in its `type`.
AZAPI_KINDS = {
    "Microsoft.SecurityInsights/alertRules": "Analytic rule",
    "Microsoft.SecurityInsights/automationRules": "Automation rule",
    "Microsoft.SecurityInsights/watchlists": "Watchlist",
    "Microsoft.SecurityInsights/dataConnectors": "Data connector",
    "Microsoft.SecurityInsights/onboardingStates": "Sentinel onboarding",
    "Microsoft.SecurityInsights/settings": "Sentinel setting",
    "Microsoft.Insights/workbooks": "Workbook",
    "Microsoft.Insights/diagnosticSettings": "Diagnostic settings",
    "Microsoft.OperationalInsights/workspaces/tables": "Log Analytics table",
    "Microsoft.Logic/workflows": "Logic App",
    "Microsoft.Logic/workflows/triggers": "Logic App trigger",
    "Microsoft.Web/connections": "API connection",
}
# Where the name of a resource is, by type; the generic keys after that.
NAMES = {
    "github_repository_environment": ("repository", "environment"),
    "github_repository_environment_deployment_policy": ("repository", "environment", "branch_pattern"),
    "github_repository_custom_property": ("repository", "property_name"),
    "github_repository_deploy_key": ("repository", "title"),
    "github_actions_variable": ("repository", "variable_name"),
    "github_actions_secret": ("repository", "secret_name"),
    "github_actions_organization_variable": ("variable_name",),
    "github_organization_custom_properties": ("property_name",),
    "github_team_repository": ("repository",),
    "github_issue_labels": ("repository",),
    "github_repository_dependabot_security_updates": ("repository",),
    "github_actions_repository_access_level": ("repository",),
    "azurerm_security_center_subscription_pricing": ("resource_type",),
    "azurerm_resource_provider_registration": ("name",),
    "azuread_group_member": ("member_object_id",),
}
NAME_KEYS = ("display_name", "name", "user_principal_name", "variable_name", "title",
             "comment", "role_definition_name")
# Attributes whose change says nothing a reviewer acts on.
NOISE = re.compile(
    r"^(id|etag|node_id|repo_id|timeouts|output|version|resource_version|last_modified.*"
    r"|.*_url|.*_urls|url|created_at|updated_at|timestamp|.*_timestamp)$")
# What a resource is reached through: an azapi resource's settings live under
# `body`, a msgraph one's too.
WRAPPERS = ("body", "properties")
REASONS = {
    "delete_because_no_resource_config": "no longer in the code",
    "delete_because_no_module": "its module is no longer in the code",
    "delete_because_each_key": "its key is no longer in the `for_each`",
    "delete_because_count_index": "its index is beyond the `count`",
    "delete_because_wrong_repetition": "`count` and `for_each` swapped",
    "delete_because_no_move_target": "its `moved` target is gone",
    "replace_because_tainted": "tainted",
    "replace_because_cannot_update": "a setting cannot change in place",
    "replace_by_request": "replace requested",
    "replace_by_triggers": "a `replace_triggered_by` changed",
}
MAX_VALUE = 60
MAX_LIST = 4
NONE = "—"
RE_RESOURCE_ID = re.compile(r"/subscriptions/[^/\s]+(?:/resourceGroups/[^/\s]+)?(?:/providers/\S+)?"
                            r"|/providers/Microsoft\.\S+", re.I)
RE_ADDRESS = re.compile(r'(?:^|\.)(?:data\.)?([a-z0-9]+_[a-z0-9_]+)\.([A-Za-z0-9_-]+)(\[.+\])?$')


def code(text) -> str:
    """An inline code span that survives backticks and pipes in the text."""
    text = str(text).replace("|", "\\|")
    tick = "``" if "`" in text else "`"
    pad = " " if tick == "``" else ""
    return f"{tick}{pad}{text}{pad}{tick}"


def kind(rtype: str, values=None) -> str:
    """What a person calls a resource of this type."""
    if rtype == "azapi_resource" and isinstance(values, dict) and values.get("type"):
        api = str(values["type"]).split("@")[0]
        if api in AZAPI_KINDS:
            return AZAPI_KINDS[api]
        last = api.rsplit("/", 1)[-1]
        words = re.sub(r"(?<!^)([A-Z])", r" \1", last).lower()
        return (words[:1].upper() + words[1:]).removesuffix("s")
    if rtype in KINDS:
        return KINDS[rtype]
    words = re.sub(r"^[a-z0-9]+?_", "", rtype).replace("_", " ")
    return words[:1].upper() + words[1:]


def short_id(value):
    """The last name in an Azure resource ID; anything else as it is."""
    if isinstance(value, str) and RE_RESOURCE_ID.fullmatch(value):
        return value.rstrip("/").rsplit("/", 1)[-1]
    return value


def address_parts(address: str):
    """(type, name, index) of a resource address, without the modules."""
    m = RE_ADDRESS.search(address)
    if not m:
        return "", address, None
    index = m.group(3)
    if index:
        index = index[1:-1].strip('"')
    return m.group(1), m.group(2), index


def name_of(rc: dict) -> str:
    """A name a person would recognise, taken from the values of the resource."""
    change = rc.get("change") or {}
    values = change.get("after") or change.get("before") or {}
    sensitive = change.get("after_sensitive") or change.get("before_sensitive") or {}
    index = rc.get("index")
    rtype = rc.get("type", "")
    if not isinstance(values, dict):
        values = {}

    def plain(key):
        v = values.get(key)
        if isinstance(sensitive, dict) and sensitive.get(key):
            return None
        if isinstance(v, (str, int)) and not isinstance(v, bool) and str(v) != "":
            return str(short_id(v))
        return None

    if rtype in ("azurerm_role_assignment", "azurerm_pim_eligible_role_assignment"):
        role = plain("role_definition_name") or short_id(values.get("role_definition_id") or "")
        who = None
        if isinstance(index, str) and "/" in index:
            role, who = index.split("/", 1)
        who = who or plain("principal_id") or ""
        scope = plain("scope") or ""
        return f"{role} for {who}" + (f" on {scope}" if scope else "")
    if rtype == "azapi_resource":
        body = values.get("body") if isinstance(values.get("body"), dict) else {}
        props = body.get("properties") if isinstance(body.get("properties"), dict) else {}
        display = props.get("displayName") or props.get("display_name")
        if isinstance(display, str) and display:
            return display
    if rtype in NAMES:
        parts = [plain(k) for k in NAMES[rtype]]
        parts = [p for p in parts if p]
        if parts:
            return "/".join(parts)
    for key in NAME_KEYS:
        if v := plain(key):
            return v
    if index is not None:
        return str(index)
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


def fmt(value) -> str:
    """A value as a code span, short enough for a table cell."""
    if value is None:
        return NONE
    if isinstance(value, str):
        text = short_id(value)
    else:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    text = text.replace("\r", "").replace("\n", "⏎")
    if len(text) > MAX_VALUE:
        text = text[: MAX_VALUE - 1] + "…"
    return code(text)


def at(tree, path):
    """The value at a path in before/after or in a sensitive/unknown map."""
    for step in path:
        tree = as_json(tree)
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


def leaves(tree, path=()):
    if tree is True:
        yield path
    elif isinstance(tree, dict):
        for k, v in tree.items():
            yield from leaves(v, path + (k,))
    elif isinstance(tree, list):
        for i, v in enumerate(tree):
            yield from leaves(v, path + (i,))


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


def as_json(value):
    """A string that holds a JSON object or list, parsed; anything else as it is."""
    if isinstance(value, str) and value[:1] in "{[":
        try:
            return json.loads(value)
        except ValueError:
            pass
    return value


def diff(before, after, path=()):
    """Paths whose value differs, down to the leaves.

    A missing object counts as an empty one, so a tag that is added is a path
    of its own; lists of scalars and lists that change length stay whole.
    """
    before, after = as_json(before), as_json(after)
    if isinstance(before, dict) or isinstance(after, dict):
        if isinstance(before, (dict, type(None))) and isinstance(after, (dict, type(None))):
            b, a = before or {}, after or {}
            for key in sorted(set(b) | set(a), key=str):
                yield from diff(b.get(key), a.get(key), path + (key,))
            return
    if (isinstance(before, list) and isinstance(after, list) and not scalars(before)
            and not scalars(after) and len(before) == len(after)):
        for i, (b, a) in enumerate(zip(before, after)):
            yield from diff(b, a, path + (i,))
    elif before != after and not (before in (None, [], "") and after in (None, [], "")):
        yield path


def label(path, rtype: str = "") -> str:
    """How a person says which setting it is: tag `owner`, setting `a.b`."""
    steps = list(path)
    while steps and steps[0] in WRAPPERS and rtype.startswith(("azapi_", "msgraph_")):
        steps.pop(0)
    if len(steps) == 2 and steps[0] == "tags":
        return f"tag {code(steps[1])}"
    text = ""
    for step in steps:
        if isinstance(step, int):
            text += f"[{step}]" if step else ""
        else:
            text += ("." if text else "") + str(step)
    return f"setting {code(text or '(whole resource)')}"


def summary(value) -> str:
    """A block of a list (a log category, a rule) told by its first scalars."""
    if isinstance(value, dict):
        parts = [f"{k}={v}" for k, v in value.items()
                 if isinstance(v, (str, int, float, bool)) and v not in ("", None)]
        return ", ".join(parts[:3]) or json.dumps(value, ensure_ascii=False)
    return str(value)


def attribute_rows(change: dict, rtype: str = "") -> list:
    """(what, from, to) for every change of an update that matters."""
    before, after = change.get("before") or {}, change.get("after") or {}
    bs, as_ = change.get("before_sensitive") or {}, change.get("after_sensitive") or {}
    unknown = change.get("after_unknown") or {}
    out = []
    for path in diff(before, after):
        if any(isinstance(s, str) and NOISE.match(s) for s in path):
            continue
        if flagged(unknown, path):
            continue
        what = label(path, rtype)
        if anything_flagged(bs, path) or anything_flagged(as_, path):
            out.append((what, "(sensitive)", "(sensitive)"))
            continue
        b, a = as_json(at(before, path)), as_json(at(after, path))
        if isinstance(b, list) or isinstance(a, list):
            b, a = b or [], a or []
            added = [x for x in a if x not in b]
            removed = [x for x in b if x not in a]
            if not added and not removed:
                out.append((f"{what}: order", NONE, NONE))
                continue
            for verb, items in (("added", added), ("removed", removed)):
                shown = items[:MAX_LIST]
                for x in shown:
                    text = x if not isinstance(x, (dict, list)) else summary(x)
                    out.append((f"{what}: {verb} {fmt(text)}", NONE, NONE))
                if len(items) > MAX_LIST:
                    out.append((f"{what}: {len(items) - MAX_LIST} more {verb}", NONE, NONE))
            continue
        if isinstance(b, str) and isinstance(a, str):
            b, a = crop(b, a)
        out.append((what, fmt(b), fmt(a)))
    return out


def diagnostic(values) -> str:
    """Logs and metrics of a diagnostic setting, and where they go."""
    if not isinstance(values, dict):
        return ""
    body = values.get("body") if isinstance(values.get("body"), dict) else {}
    values = {**values, **(body.get("properties") or {})}
    logs, metrics = [], []
    for key in ("enabled_log", "log", "logs"):
        for item in values.get(key) or []:
            if isinstance(item, dict) and item.get("enabled", True):
                name = item.get("category") or item.get("categoryGroup") or item.get("category_group")
                if name:
                    logs.append(name)
    for key in ("enabled_metric", "metric", "metrics"):
        for item in values.get(key) or []:
            if isinstance(item, dict) and item.get("enabled", True) and item.get("category"):
                metrics.append(item["category"])
    target = (values.get("log_analytics_workspace_id") or values.get("workspaceId")
              or values.get("storage_account_id") or values.get("storageAccountId")
              or values.get("eventhub_name"))
    parts = []
    if logs:
        parts.append("logs " + ", ".join(code(x) for x in logs[:MAX_LIST])
                     + (f" and {len(logs) - MAX_LIST} more" if len(logs) > MAX_LIST else ""))
    if metrics:
        parts.append("metrics " + ", ".join(code(x) for x in metrics[:MAX_LIST]))
    text = "; ".join(parts) or "nothing"
    if target:
        where = "workspace" if "workspace" in str(target).lower() or values.get(
            "log_analytics_workspace_id") else "destination"
        text += f" → {where} {code(short_id(target))}"
    return text


def verb_of(rc: dict):
    change = rc.get("change") or {}
    actions = tuple(change.get("actions") or [])
    if actions == ("forget",):
        return "forget"
    verb = {("create",): "create", ("update",): "update", ("delete",): "destroy",
            ("delete", "create"): "replace", ("create", "delete"): "replace",
            ("read",): "read"}.get(actions)
    if verb is None:
        if change.get("importing"):
            return "import"
        if rc.get("previous_address") and rc["previous_address"] != rc["address"]:
            return "move"
    return verb


def entry(rc: dict, unit: str):
    """What the comment says about one resource change, or None to leave it out."""
    if rc.get("mode") == "data":
        return None
    verb = verb_of(rc)
    if verb in (None, "read"):
        return None
    change = rc.get("change") or {}
    rtype = rc.get("type", "")
    values = change.get("after") or change.get("before")
    imported = bool(change.get("importing"))
    moved = rc.get("previous_address") and rc["previous_address"] != rc["address"]
    item = {"unit": unit, "address": rc["address"], "action": verb, "icon": ICONS[verb],
            "kind": kind(rtype, values), "name": name_of(rc) or address_parts(rc["address"])[1],
            "rows": [], "why": "", "imported": imported}
    is_diag = item["kind"].endswith("Diagnostic settings")
    if verb == "create":
        text = "created"
        if is_diag:
            text = "diagnostic settings added: " + diagnostic(change.get("after"))
        item["rows"] = [(text, NONE, NONE)]
    elif verb in ("update", "import", "move"):
        rows = attribute_rows(change, rtype)
        if is_diag and rows and verb == "update":
            rows = [(f"diagnostic settings: {r[0]}", r[1], r[2]) for r in rows]
        if verb == "import":
            rows = [("imported", NONE, NONE)] + rows
        if verb == "move":
            rows = [(f"moved from {code(rc['previous_address'])}", NONE, NONE)] + rows
        if verb == "update" and not rows:
            rows = [("updated; the new values are known only after apply", NONE, NONE)]
        item["rows"] = rows
    elif verb == "forget":
        item["rows"] = [("removed from Terraform, left in place", NONE, NONE)]
    elif verb == "destroy":
        item["why"] = REASONS.get(rc.get("action_reason", ""), "")
        item["rows"] = [("destroyed", NONE, NONE)]
    elif verb == "replace":
        paths = [tuple(p) for p in change.get("replace_paths") or []]
        forced = [r for r in attribute_rows({**change, "after_unknown": {}}, rtype)
                  if any(r[0].startswith(label(p, rtype)) for p in paths)]
        if forced:
            item["why"] = "; ".join(
                f"{r[0]} forces it" + (f": {r[1]} → {r[2]}" if r[1] != NONE or r[2] != NONE else "")
                for r in forced)
        elif paths:
            item["why"] = ", ".join(label(p, rtype) for p in paths) + " forces it"
        else:
            item["why"] = REASONS.get(rc.get("action_reason", ""), "")
        item["rows"] = [("replaced", NONE, NONE)]
    if moved and verb != "move":
        item["rows"].append((f"moved from {code(rc['previous_address'])}", NONE, NONE))
    if imported and verb not in ("import",):
        item["rows"].append(("imported", NONE, NONE))
    # An import that changes nothing is counted, not listed.
    item["noop_import"] = verb == "import" and len(item["rows"]) == 1
    return item


def natural(text: str) -> list:
    """A sort key that puts x[2] before x[10]."""
    return [(0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.split(r"(\d+)", text)]


def entries(plan: dict, unit: str) -> list:
    out = [e for rc in plan.get("resource_changes") or [] if (e := entry(rc, unit))]
    out.sort(key=lambda e: (ORDER.get(e["action"], 9), natural(e["address"])))
    return out


TEXT_VERBS = {
    "will be created": "create",
    "will be updated in-place": "update",
    "will be destroyed": "destroy",
    "must be replaced": "replace",
    "is tainted, so must be replaced": "replace",
    "will be replaced, as requested": "replace",
    "will be imported": "import",
}


def text_entry(address: str, what: str, unit: str, imported: bool = False):
    """The same entry from the text of the plan alone, for a unit without JSON."""
    if what.startswith("has moved to "):
        verb, prev, address = "move", address, what[len("has moved to "):]
    elif what.startswith("will no longer be managed"):
        verb, prev = "forget", None
    else:
        verb, prev = TEXT_VERBS.get(what), None
    if verb is None:
        return None
    rtype, name, index = address_parts(address)
    rows = {"create": "created", "update": "updated, see the details", "destroy": "destroyed",
            "replace": "replaced", "import": "imported",
            "forget": "removed from Terraform, left in place"}
    text = f"moved from {code(prev)}" if verb == "move" else rows[verb]
    item = {"unit": unit, "address": address, "action": verb, "icon": ICONS[verb],
            "kind": kind(rtype) if rtype else "Resource", "name": index or name,
            "rows": [(text, NONE, NONE)], "imported": imported,
            "why": "tainted" if "tainted" in what else "", "noop_import": verb == "import"}
    if imported and verb != "import":
        item["rows"].append(("imported", NONE, NONE))
    return item


def load(path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
