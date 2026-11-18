#!/usr/bin/env python3
"""Runs release-stale-locks.py against a stand-in for the az CLI.

The stand-in answers "storage blob list" and "storage blob metadata show" with
the leased blobs given below and records every "storage blob lease break", so
the test sees exactly which locks the script would release, without touching
Azure.

Run: python3 tests/terragrunt/test_release_stale_locks.py
"""
import base64
import datetime
import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "src" / "terragrunt" / "release-stale-locks.py"

FAKE_AZ = """#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
blobs = json.load(open(os.environ["FAKE_BLOBS"]))
if args[:3] == ["storage", "blob", "list"]:
    print(json.dumps(list(blobs)))
elif args[:4] == ["storage", "blob", "metadata", "show"]:
    print(json.dumps(blobs[args[args.index("--name") + 1]]))
elif args[:4] == ["storage", "blob", "lease", "break"]:
    with open(os.environ["FAKE_BROKEN"], "a") as fh:
        fh.write(args[args.index("--blob-name") + 1] + "\\n")
else:
    sys.exit("unexpected az call: " + " ".join(args))
"""


def lock(who: str, minutes_ago: int) -> str:
    created = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(
        minutes=minutes_ago)
    info = {
        "ID": f"id-{who}-{minutes_ago}", "Operation": "OperationTypePlan", "Who": who,
        "Version": "1.15.8", "Path": "tfstate/x/terraform.tfstate", "Info": "",
        "Created": created.strftime("%Y-%m-%dT%H:%M:%S.123456789Z"),
    }
    return base64.b64encode(json.dumps(info).encode()).decode()


class ReleaseStaleLocks(unittest.TestCase):
    def run_with(self, blobs: dict, *dead: str) -> tuple:
        with tempfile.TemporaryDirectory() as tmp:
            tmp = pathlib.Path(tmp)
            az = tmp / "az"
            az.write_text(FAKE_AZ)
            az.chmod(0o755)
            (tmp / "blobs.json").write_text(json.dumps(blobs))
            (tmp / "broken").touch()
            env = {**os.environ, "PATH": f"{tmp}{os.pathsep}{os.environ['PATH']}",
                   "FAKE_BLOBS": str(tmp / "blobs.json"), "FAKE_BROKEN": str(tmp / "broken")}
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "sttfstate", "tfstate", "70", *dead],
                capture_output=True, text=True, env=env, check=False,
            )
            return proc, (tmp / "broken").read_text().split()

    def test_only_old_runner_locks_are_released(self):
        proc, broken = self.run_with({
            "a/terraform.tfstate": {"terraformlockid": lock("runner@runnervmtr4k5", 71)},
            "b/terraform.tfstate": {"terraformlockid": lock("runner@runnervm0001", 30)},
            "c/terraform.tfstate": {"terraformlockid": lock("admin@laptop", 600)},
            "d/terraform.tfstate": {"owner": "someone"},
        })
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(broken, ["a/terraform.tfstate"])
        self.assertIn("a/terraform.tfstate: released lock id-runner@runnervmtr4k5-71 "
                      "(OperationTypePlan) taken by runner@runnervmtr4k5 71 min ago",
                      proc.stdout)
        self.assertIn("b/terraform.tfstate: locked by runner@runnervm0001 30 min ago, "
                      "left alone", proc.stdout)
        self.assertIn("c/terraform.tfstate: locked by admin@laptop 600 min ago, left alone",
                      proc.stdout)
        self.assertIn("d/terraform.tfstate: leased without terraform lock info "
                      "(metadata keys: owner), left alone", proc.stdout)

    def test_lock_named_as_dead_goes_whatever_its_age(self):
        proc, broken = self.run_with({
            "a/terraform.tfstate": {"terraformlockid": lock("runner@runnervmtr4k5", 45)},
            "b/terraform.tfstate": {"terraformlockid": lock("runner@runnervm0001", 45)},
        }, "id-runner@runnervmtr4k5-45")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(broken, ["a/terraform.tfstate"])
        self.assertIn("taken by runner@runnervmtr4k5 45 min ago, named as dead", proc.stdout)
        self.assertIn("b/terraform.tfstate: locked by runner@runnervm0001 45 min ago, "
                      "left alone", proc.stdout)

    def test_nothing_leased(self):
        proc, broken = self.run_with({})
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual((proc.stdout, broken), ("", []))


if __name__ == "__main__":
    unittest.main(verbosity=2)
