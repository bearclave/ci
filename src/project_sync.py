#!/usr/bin/env python3
"""Adds every open issue and pull request of bearclave to the Project of its
repository's group.

The group is the repository's custom property `group`
(bearclave-infrastructure, applications, dev-infrastructure,
bearclave-knowhow), read from GitHub on every run, so a new repository is covered by
itself. Archived repositories and repositories without a group are skipped.
Items already in the Project are left alone and nothing is ever removed.

Usage: project_sync.py [--dry-run]

Signs in as the GitHub App bearclave-bot (github_bot.py): its key, App ID and
installation ID are read from the Key Vault kv-githubbot-sec-weu-001 with the
current az sign-in (in the workflow: sp-project-sync by OIDC on the
environment project-sync; locally: the owner, AZURE_CONFIG_DIR). The
installation token is narrowed to what the sync needs. Runs daily at 06:15
UTC (and on demand) from .github/workflows/project-sync.yml. Moved here from
"""
import argparse
import json
import os
import sys

import github_bot

ORG = github_bot.ORG
API = github_bot.API

# Group (custom property `group`) -> number of the organisation Project.
PROJECTS = {
    "bearclave-infrastructure": 1,
    "applications": 2,
    "dev-infrastructure": 3,
    "bearclave-knowhow": 4,
    # The old values, until the group property of every repository has
    # moved to the new ones (iac-github); then they go.
    "iac": 1,
    "app": 2,
    "template": 3,
    "platform": 4,
}

# What the installation token may do; the App itself may do more.
TOKEN_PERMISSIONS = {
    "organization_projects": "write",
    "issues": "read",
    "pull_requests": "read",
    "metadata": "read",
}


def installation_token() -> str:
    return github_bot.Bot().token(TOKEN_PERMISSIONS)


# --- GitHub ------------------------------------------------------------------

def request(method: str, url: str, token: str, payload=None):
    try:
        return github_bot.request(method, url, token, payload)
    except (github_bot.ApiError, github_bot.NotFound) as e:
        sys.exit(str(e))


class GitHub:
    def __init__(self, token: str):
        self.token = token

    def graphql(self, query: str, **variables) -> dict:
        body = request("POST", f"{API}/graphql", self.token, {"query": query, "variables": variables})
        if body.get("errors"):
            sys.exit(f"GraphQL: {json.dumps(body['errors'])[:1000]}")
        return body["data"]

    def rest(self, path: str):
        return request("GET", f"{API}/{path}", self.token)


def paged(gh, query: str, path: list, **variables):
    """Yields the nodes of a connection, following its cursor."""
    after = None
    while True:
        data = gh.graphql(query, after=after, **variables)
        for key in path:
            data = data[key]
        yield from data["nodes"]
        if not data["pageInfo"]["hasNextPage"]:
            return
        after = data["pageInfo"]["endCursor"]


Q_PROJECTS = """query($org: String!, $after: String) {
  organization(login: $org) { projectsV2(first: 50, after: $after) {
    nodes { id number title closed }
    pageInfo { hasNextPage endCursor } } } }"""

Q_ITEMS = """query($id: ID!, $after: String) {
  node(id: $id) { ... on ProjectV2 { items(first: 100, after: $after) {
    nodes { content { ... on Issue { id } ... on PullRequest { id } } }
    pageInfo { hasNextPage endCursor } } } } }"""

Q_REPOS = """query($org: String!, $after: String) {
  organization(login: $org) { repositories(first: 100, after: $after, orderBy: {field: NAME, direction: ASC}) {
    nodes { name isArchived }
    pageInfo { hasNextPage endCursor } } } }"""

Q_OPEN = """query($org: String!, $repo: String!, $after: String) {
  repository(owner: $org, name: $repo) { %s(states: OPEN, first: 100, after: $after) {
    nodes { id number }
    pageInfo { hasNextPage endCursor } } } }"""

M_ADD = """mutation($project: ID!, $content: ID!) {
  addProjectV2ItemById(input: {projectId: $project, contentId: $content}) { item { id } } }"""


def group_of(gh, repo: str):
    for p in gh.rest(f"repos/{ORG}/{repo}/properties/values"):
        if p["property_name"] == "group":
            return p["value"] or None
    return None


# --- sync --------------------------------------------------------------------

def sync(gh, dry_run: bool) -> dict:
    """Returns a summary: per project title the number of items added."""
    projects = {p["number"]: p for p in paged(gh, Q_PROJECTS, ["organization", "projectsV2"], org=ORG)}
    missing = sorted(n for n in PROJECTS.values() if n not in projects or projects[n]["closed"])
    if missing:
        sys.exit(f"Projects {missing} do not exist or are closed")

    present = {}
    for number in PROJECTS.values():
        items = paged(gh, Q_ITEMS, ["node", "items"], id=projects[number]["id"])
        present[number] = {i["content"]["id"] for i in items if i.get("content")}

    summary = {"added": {projects[n]["title"]: 0 for n in PROJECTS.values()},
               "present": 0, "skipped": [], "unknown_group": []}
    for repo in paged(gh, Q_REPOS, ["organization", "repositories"], org=ORG):
        name = repo["name"]
        if repo["isArchived"]:
            summary["skipped"].append(f"{name} (archived)")
            continue
        group = group_of(gh, name)
        if not group:
            summary["skipped"].append(f"{name} (no group)")
            continue
        if group not in PROJECTS:
            summary["unknown_group"].append(f"{name} ({group})")
            continue
        project = projects[PROJECTS[group]]
        for kind, label in (("issues", "issue"), ("pullRequests", "pull request")):
            for node in paged(gh, Q_OPEN % kind, ["repository", kind], org=ORG, repo=name):
                if node["id"] in present[project["number"]]:
                    summary["present"] += 1
                    continue
                print(f"{'would add' if dry_run else 'add'} {name}#{node['number']} ({label}) -> {project['title']}")
                if not dry_run:
                    gh.graphql(M_ADD, project=project["id"], content=node["id"])
                present[project["number"]].add(node["id"])
                summary["added"][project["title"]] += 1
    return summary


def report(summary: dict, dry_run: bool) -> str:
    verb = "Would add" if dry_run else "Added"
    lines = ["## project-sync", "", f"| Project | {verb} |", "|---|---|"]
    lines += [f"| {title} | {count} |" for title, count in summary["added"].items()]
    lines += ["", f"Already in their Project: {summary['present']}."]
    if summary["skipped"]:
        lines.append(f"Skipped: {', '.join(summary['skipped'])}.")
    if summary["unknown_group"]:
        lines.append(f"**Group without a Project:** {', '.join(summary['unknown_group'])}.")
    return "\n".join(lines) + "\n"


def main(argv: list) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", help="list what would be added, add nothing")
    args = parser.parse_args(argv)

    summary = sync(GitHub(installation_token()), args.dry_run)
    text = report(summary, args.dry_run)
    print(text)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as f:
            f.write(text)
    # A group with no Project fails the run, so it is noticed.
    return 1 if summary["unknown_group"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
