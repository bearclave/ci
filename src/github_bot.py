"""GitHub API and the sign-in as bearclave-bot, for the workflows of this
repository that write to other repositories (deps.yml, ci-pins.yml,
housekeeping.yml, project-sync.yml).

The job signs in to Azure by OIDC as the identity of its environment
(docs/identities.md) and Bot reads the App's key, App ID and installation
ID from the Key Vault kv-githubbot-sec-weu-001. No secret in GitHub, nothing
printed. A dry run reads with GH_TOKEN instead.

The owner, whom the pull requests are assigned to, is the organisation
variable OWNER (the environment variable of the same name); without it they
are assigned to nobody.
"""
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

ORG = "bearclave"
VAULT = "kv-githubbot-sec-weu-001"
API = "https://api.github.com"

def owner() -> str:
    """The login pull requests and issues are assigned to (vars.OWNER), or ''."""
    return os.environ.get("OWNER", "").strip()


class NotFound(Exception):
    pass


class ApiError(Exception):
    pass


def request(method: str, url: str, token: str = "", payload=None, accept: str = "application/vnd.github+json",
            raw: bool = False):
    """JSON (or bytes with raw) of a call; a path is relative to the GitHub API."""
    if url.startswith("/"):
        url = API + url
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Accept": accept, "User-Agent": "bearclave-ci", "Content-Type": "application/json"}
    if url.startswith(API):
        headers["X-GitHub-Api-Version"] = "2022-11-28"
        if token:
            headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                body = resp.read()
                return body if raw else (json.loads(body) if body else None)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise NotFound(url) from None
            if e.code >= 500 and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            raise ApiError(f"{method} {url}: {e.code} {e.reason} {e.read().decode(errors='replace')[:500]}") from None
        except urllib.error.URLError as e:
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            raise ApiError(f"{method} {url}: {e.reason}") from None


def paginate(path: str, token: str, limit: int = 1000) -> list:
    """Every item of a list endpoint, up to limit."""
    items, page = [], 1
    sep = "&" if "?" in path else "?"
    while len(items) < limit:
        batch = request("GET", f"{path}{sep}per_page=100&page={page}", token)
        if isinstance(batch, dict):  # e.g. {"total_count":..., "items": [...]}
            batch = batch.get("items") or batch.get("repositories") or []
        items += batch
        if len(batch) < 100:
            break
        page += 1
    return items[:limit]


def file_text(repo: str, path: str, ref: str, token: str):
    """Text of a file at ref, or None when it does not exist."""
    try:
        quoted = urllib.parse.quote(path)
        return request("GET", f"/repos/{repo}/contents/{quoted}?ref={ref}", token,
                       accept="application/vnd.github.raw", raw=True).decode("utf-8")
    except NotFound:
        return None


def repositories(token: str) -> list:
    """The organisation's repositories that are not archived."""
    try:
        repos = paginate(f"/orgs/{ORG}/repos?type=all", token)
    except ApiError:
        # An installation token lists the repositories it was granted.
        repos = paginate("/installation/repositories", token)
    return sorted((r for r in repos if not r.get("archived")), key=lambda r: r["name"])


def commit_files(repo: str, token: str, base_sha: str, branch: str, files: dict, message: str) -> str:
    """One commit on top of base_sha with files {path: text} on branch.

    Made through the API, so GitHub signs it. The branch is created, or
    moved when it is left over from an earlier run (the caller checks that
    no pull request is open on it)."""
    base_tree = request("GET", f"/repos/{repo}/git/commits/{base_sha}", token)["tree"]["sha"]
    tree = []
    for path, text in sorted(files.items()):
        blob = request("POST", f"/repos/{repo}/git/blobs", token,
                       {"content": base64.b64encode(text.encode()).decode(), "encoding": "base64"})
        tree.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
    new_tree = request("POST", f"/repos/{repo}/git/trees", token, {"base_tree": base_tree, "tree": tree})
    commit = request("POST", f"/repos/{repo}/git/commits", token,
                     {"message": message, "tree": new_tree["sha"], "parents": [base_sha]})
    try:
        request("GET", f"/repos/{repo}/git/ref/heads/{branch}", token)
        request("PATCH", f"/repos/{repo}/git/refs/heads/{branch}", token, {"sha": commit["sha"], "force": True})
    except NotFound:
        request("POST", f"/repos/{repo}/git/refs", token, {"ref": f"refs/heads/{branch}", "sha": commit["sha"]})
    return commit["sha"]


def open_pull(repo: str, branch: str, token: str):
    """The open pull request from branch, or None."""
    pulls = request("GET", f"/repos/{repo}/pulls?state=open&head={ORG}:{branch}", token)
    return pulls[0] if pulls else None


def create_pull(repo: str, token: str, branch: str, base: str, title: str, body: str,
                labels=("type:deps",)) -> dict:
    """A pull request assigned to the owner, with its labels. Nobody approves
    or merges it but the owner."""
    pr = request("POST", f"/repos/{repo}/pulls", token, {"title": title, "head": branch, "base": base, "body": body})
    try:
        request("POST", f"/repos/{repo}/issues/{pr['number']}/labels", token, {"labels": list(labels)})
        if login := owner():
            request("POST", f"/repos/{repo}/issues/{pr['number']}/assignees", token, {"assignees": [login]})
        else:
            print(f"::warning::{repo}#{pr['number']}: no OWNER variable, nobody assigned")
    except ApiError as e:
        print(f"::warning::{repo}#{pr['number']}: labels or assignee not set: {e}")
    return pr


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


class Bot:
    """Installation tokens of bearclave-bot, each limited to what a step needs."""

    def __init__(self):
        self.app_id = secret("github-app-id")
        self.installation = secret("github-app-installation-id")
        self._pem = secret("github-app-key")

    def token(self, permissions: dict, repositories=None) -> str:
        payload = {"permissions": permissions}
        if repositories:
            payload["repositories"] = list(repositories)
        body = request("POST", f"/app/installations/{self.installation}/access_tokens",
                       jwt(self.app_id, self._pem), payload)
        return body["token"]


READ = {"contents": "read", "metadata": "read", "pull_requests": "read"}
# Writing a branch whose commit changes .github/workflows needs workflows too.
WRITE = {"contents": "write", "pull_requests": "write", "issues": "write", "workflows": "write", "metadata": "read"}


def say(line: str = "") -> None:
    print(line)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(line + "\n")
