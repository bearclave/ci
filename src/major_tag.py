#!/usr/bin/env python3
"""Moves the major tag (v2) of bearclave/ci to a published release (v2.x.y).

Run by .github/workflows/major-tag.yml when a release is published, as the
GitHub App bearclave-bot: the job signs in to Azure as sp-ci-major-tag by
OIDC and this reads the App's key, App ID and installation ID from the Key
Vault kv-githubbot-sec-weu-001. No secret in GitHub, nothing printed.

The major tag moves only to a release that is published, not a pre-release,
named vX.Y.Z, tagged by a signed annotated tag (GitHub verified the
signature) and the newest release of its major. It becomes a lightweight
ref to the release's commit: the release tags stay the owner's signed tags,
the major tag only points. A major tag that already points there is left
alone, so a rerun is a no-op. A new major tag (v3) is created by the owner
with tools/move-major-tag.sh; only moving an existing one is automatic.

Usage: major_tag.py vX.Y.Z             move the major tag (bot token)
       major_tag.py --dry-run vX.Y.Z   only check and say what would happen,
                                       reading with GH_TOKEN (e.g. gh auth token)
"""
import base64
import fnmatch
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

REPO = "bearclave/ci"
VAULT = "kv-githubbot-sec-weu-001"
API = "https://api.github.com"
RELEASE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")

# What the installation token may do, on this repository only. Updating a ref
# to a commit that changes .github/workflows needs `workflows` as well.
TOKEN_PERMISSIONS = {"contents": "write", "workflows": "write", "metadata": "read"}


class Refused(Exception):
    """The release is not one the major tag may move to."""


class NotFound(Exception):
    pass


def version(tag: str):
    """(major, minor, patch) of a release tag vX.Y.Z, else None."""
    m = RELEASE.match(tag)
    return tuple(int(x) for x in m.groups()) if m else None


def check_release(tag: str, release: dict, ref: dict, tag_object: dict) -> str:
    """The commit of a release the major tag may move to, or Refused."""
    if version(tag) is None:
        raise Refused(f"not a release tag: {tag!r} (vX.Y.Z)")
    if release.get("tag_name") != tag:
        raise Refused(f"the release found is {release.get('tag_name')!r}, not {tag}")
    if release.get("draft"):
        raise Refused(f"{tag} is a draft")
    if release.get("prerelease"):
        raise Refused(f"{tag} is a pre-release")
    if ref["object"]["type"] != "tag":
        raise Refused(f"{tag} is a lightweight tag, not a signed annotated tag")
    if not tag_object.get("verification", {}).get("verified"):
        reason = tag_object.get("verification", {}).get("reason", "unsigned")
        raise Refused(f"{tag} is not a verified signed tag ({reason})")
    if tag_object["object"]["type"] != "commit":
        raise Refused(f"{tag} does not point at a commit")
    return tag_object["object"]["sha"]


def newest(tag: str, releases: list) -> str:
    """The newest published, non-pre-release release of tag's major."""
    major = version(tag)[0]
    tags = [r["tag_name"] for r in releases
            if not r.get("draft") and not r.get("prerelease")
            and version(r.get("tag_name", "")) and version(r["tag_name"])[0] == major]
    return max(tags + [tag], key=version)


def blocking(ref: str, rulesets: list) -> list:
    """Names of the active tag rulesets that forbid moving ref and that the
    caller cannot bypass. Every ruleset that matches a ref applies (repository
    and organisation alike); bypassing one of them is not enough."""
    found = []
    for rs in rulesets:
        if rs.get("target") != "tag" or rs.get("enforcement") != "active":
            continue
        cond = rs.get("conditions", {}).get("ref_name", {})
        if not any(fnmatch.fnmatchcase(ref, p) for p in cond.get("include", [])):
            continue
        if any(fnmatch.fnmatchcase(ref, p) for p in cond.get("exclude", [])):
            continue
        if not {r.get("type") for r in rs.get("rules", [])} & {"update", "non_fast_forward"}:
            continue
        if rs.get("current_user_can_bypass") != "always":
            found.append(f"{rs.get('name')} ({rs.get('source_type')} {rs.get('source')}, id {rs.get('id')})")
    return found


# --- GitHub --------------------------------------------------------------------

def request(method: str, path: str, token: str, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise NotFound(path) from None
        sys.exit(f"{method} {path}: {e.code} {e.reason} {e.read().decode(errors='replace')[:500]}")


def peel(token: str, ref: dict) -> str:
    """The commit a tag ref points at, through annotated tags."""
    obj = ref["object"]
    while obj["type"] == "tag":
        obj = request("GET", f"/repos/{REPO}/git/tags/{obj['sha']}", token)["object"]
    return obj["sha"]


# --- sign-in as bearclave-bot ---------------------------------------------------

def secret(name: str) -> str:
    r = subprocess.run(["az", "keyvault", "secret", "show", "--vault-name", VAULT, "--name", name,
                        "--query", "value", "-o", "tsv"], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"cannot read {name} from {VAULT}: {r.stderr.strip()}")
    return r.stdout.strip()


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def jwt(app_id: str, pem: str) -> str:
    now = int(time.time())
    head = b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    body = b64(json.dumps({"iat": now - 60, "exp": now + 540, "iss": app_id}).encode())
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "key.pem")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(pem)
        signature = subprocess.run(["openssl", "dgst", "-sha256", "-sign", path],
                                   input=f"{head}.{body}".encode(), capture_output=True, check=True).stdout
    return f"{head}.{body}.{b64(signature)}"


def bot_token() -> str:
    app_id, installation, pem = secret("github-app-id"), secret("github-app-installation-id"), secret("github-app-key")
    app_jwt = jwt(app_id, pem)
    del pem
    body = request("POST", f"/app/installations/{installation}/access_tokens", app_jwt,
                   {"repositories": [REPO.split("/")[1]], "permissions": TOKEN_PERMISSIONS})
    return body["token"]


# --- main ----------------------------------------------------------------------

def say(line: str) -> None:
    print(line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def main(argv: list) -> int:
    dry_run = "--dry-run" in argv
    args = [a for a in argv if a != "--dry-run"]
    if len(args) != 1:
        sys.exit(__doc__)
    tag = args[0]
    if version(tag) is None:
        sys.exit(f"refused: not a release tag: {tag!r} (vX.Y.Z)")
    major = f"v{version(tag)[0]}"

    if dry_run:
        token = os.environ.get("GH_TOKEN") or sys.exit("--dry-run reads with GH_TOKEN")
    else:
        token = bot_token()

    try:
        release = request("GET", f"/repos/{REPO}/releases/tags/{tag}", token)
        ref = request("GET", f"/repos/{REPO}/git/ref/tags/{tag}", token)
        tag_object = (request("GET", f"/repos/{REPO}/git/tags/{ref['object']['sha']}", token)
                      if ref["object"]["type"] == "tag" else {})
    except NotFound as e:
        sys.exit(f"refused: no published release {tag} ({e})")
    try:
        commit = check_release(tag, release, ref, tag_object)
    except Refused as e:
        sys.exit(f"refused: {e}")

    releases = request("GET", f"/repos/{REPO}/releases?per_page=100", token)
    top = newest(tag, releases)
    if top != tag:
        say(f"{major} stays: {top} is newer than {tag}.")
        return 0

    try:
        current = peel(token, request("GET", f"/repos/{REPO}/git/ref/tags/{major}", token))
    except NotFound:
        sys.exit(f"refused: {major} does not exist yet; the owner creates it with tools/move-major-tag.sh {tag}")
    if current == commit:
        say(f"{major} already points at {tag} ({commit[:12]}); nothing to do.")
        return 0
    # Rulesets as the caller sees them: current_user_can_bypass is the
    # caller's own (the bot's in a real run, the reader's in a dry run).
    listed = request("GET", f"/repos/{REPO}/rulesets?includes_parents=true&targets=tag", token)
    rulesets = [request("GET", f"/repos/{REPO}/rulesets/{rs['id']}", token) for rs in listed]
    blockers = blocking(f"refs/tags/{major}", rulesets)
    if blockers:
        sys.exit(f"refused: the caller cannot bypass {', '.join(blockers)}, which forbid moving {major}; "
                 f"the owner adds bearclave-bot to its bypass list or deletes it if it is a leftover")
    if dry_run:
        say(f"would move {major} from {current[:12]} to {tag} ({commit[:12]}).")
        return 0

    request("PATCH", f"/repos/{REPO}/git/refs/tags/{major}", token, {"sha": commit, "force": True})
    say(f"{major} moved from {current[:12]} to {tag} ({commit[:12]}).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
