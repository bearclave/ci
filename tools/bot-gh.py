#!/usr/bin/env python3
"""Runs gh as the GitHub App bearclave-bot.

Pull requests in bearclave are opened by this App, so that the owner can
approve them (nobody can approve their own pull request). The commits stay
the owner's: push the branch with git as usual, then open the pull request
through this wrapper.

Usage: bot-gh.py <gh arguments...>

    git push -u origin feat/my-change
    bot-gh pr create --draft --head feat/my-change \\
        --title "feat(x): ..." --body-file /tmp/pr.md
    bot-gh pr merge 12 --squash        # once the owner approved

pr / check assigns a pull request of the App to the owner (vars.OWNER).
Installed once as ~/.local/bin/bot-gh, a link to this file (README of
bearclave/ci, docs/organisation-workflows.md).

The App's key, App ID and installation ID are read from the Key Vault
kv-githubbot-sec-weu-001 with the current az sign-in (AZURE_CONFIG_DIR,
default ~/.azure-bearclave-matej), the key is turned into a JWT, the JWT into
an installation token (valid one hour), and gh runs with GH_TOKEN set to it.
Nothing of it is printed and the key leaves the disk before gh starts.
"""
import base64
import concurrent.futures
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

VAULT = "kv-githubbot-sec-weu-001"
DEFAULT_AZURE_CONFIG = pathlib.Path.home() / ".azure-bearclave-matej"


def secret(name: str, env: dict) -> str:
    r = subprocess.run(["az", "keyvault", "secret", "show", "--vault-name", VAULT, "--name", name,
                        "--query", "value", "-o", "tsv"], capture_output=True, text=True, env=env)
    if r.returncode != 0:
        # az prints no secret value on failure, only the reason.
        sys.exit(f"bot-gh: cannot read {name} from {VAULT} (az login? member of "
                 f"sg-azure-vault-githubbot-readers?)\n{r.stderr.strip()}")
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


def installation_token(app_jwt: str, installation: str) -> str:
    req = urllib.request.Request(
        f"https://api.github.com/app/installations/{installation}/access_tokens", method="POST",
        headers={"Authorization": f"Bearer {app_jwt}", "Accept": "application/vnd.github+json",
                 "X-GitHub-Api-Version": "2022-11-28"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)["token"]
    except urllib.error.HTTPError as e:
        sys.exit(f"bot-gh: GitHub refused the installation token: {e.code} {e.reason}")


def main(argv: list) -> int:
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    for tool in ("az", "gh", "openssl"):
        if not shutil.which(tool):
            sys.exit(f"bot-gh: {tool} is not installed")
    env = dict(os.environ)
    if "AZURE_CONFIG_DIR" not in env and DEFAULT_AZURE_CONFIG.is_dir():
        env["AZURE_CONFIG_DIR"] = str(DEFAULT_AZURE_CONFIG)
    with concurrent.futures.ThreadPoolExecutor(3) as pool:
        key, app_id, installation = pool.map(
            lambda n: secret(n, env), ["github-app-key", "github-app-id", "github-app-installation-id"])
    token = installation_token(jwt(app_id, key), installation)
    del key
    env.pop("GITHUB_TOKEN", None)
    env["GH_TOKEN"] = token
    os.execvpe("gh", ["gh", *argv], env)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
