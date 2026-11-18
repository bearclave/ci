#!/usr/bin/env python3
"""Releases state locks that a killed GitHub job left behind.

Terraform locks a state by taking a lease on its blob, and that lease never
expires. A job that is cancelled or runs out of time while terraform holds it
leaves the lock behind, and every later plan and apply of that unit fails on
"Error acquiring the state lock" until someone runs force-unlock.

A lock is released only when a GitHub runner took it ("Who" is runner@...) and
it is older than any job is allowed to run, so the job that took it is gone. A
lock taken on a workstation, or by a job still running, stays.

A lock whose ID is given on the command line is released whatever its age: for
a lock known to be dead, such as one left by a run cancelled a moment ago.

Usage: release-stale-locks.py <storage-account> <container> <minutes> [<lock-id>...]
"""
import base64
import datetime
import json
import subprocess
import sys


def az(*args: str) -> str:
    return subprocess.run(
        ["az", *args, "--auth-mode", "login", "--only-show-errors"],
        capture_output=True, text=True, check=True,
    ).stdout


def main(account: str, container: str, minutes: int, dead: tuple = ()) -> int:
    where = ["--account-name", account, "--container-name", container]
    leased = json.loads(az(
        "storage", "blob", "list", *where, "--num-results", "*",
        "--query", "[?properties.lease.state=='leased'].name", "--output", "json",
    ))
    now = datetime.datetime.now(datetime.timezone.utc)
    for name in leased:
        metadata = json.loads(az(
            "storage", "blob", "metadata", "show", *where, "--name", name, "--output", "json",
        )) or {}
        raw = next((v for k, v in metadata.items() if k.lower() == "terraformlockid"), None)
        try:
            info = json.loads(base64.b64decode(raw))
            # Go writes nanoseconds, which strptime does not take.
            created = datetime.datetime.strptime(
                info["Created"][:19], "%Y-%m-%dT%H:%M:%S",
            ).replace(tzinfo=datetime.timezone.utc)
        except (TypeError, ValueError, KeyError):
            print(f"{name}: leased without terraform lock info "
                  f"(metadata keys: {', '.join(metadata) or 'none'}), left alone")
            continue
        age = int((now - created).total_seconds() // 60)
        who = info.get("Who", "")
        named = info.get("ID") in dead
        if not named and (not who.startswith("runner@") or age < minutes):
            print(f"{name}: locked by {who} {age} min ago, left alone")
            continue
        az("storage", "blob", "lease", "break", *where, "--blob-name", name)
        print(f"{name}: released lock {info.get('ID')} ({info.get('Operation')}) "
              f"taken by {who} {age} min ago" + (", named as dead" if named else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2], int(sys.argv[3]), tuple(sys.argv[4:])))
