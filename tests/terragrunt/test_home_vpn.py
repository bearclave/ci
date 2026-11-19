#!/usr/bin/env python3
"""Runs home-vpn.sh up against a small iac-home tree with stand-ins for curl,
az, sudo and terragrunt on PATH, and checks the WireGuard config and hosts.

Run: python3 tests/terragrunt/test_home_vpn.py
"""
import os
import pathlib
import subprocess
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "src" / "terragrunt" / "home-vpn.sh"

NETWORK = """locals {
  wireguard = {
    listen_port = 51820
    endpoint    = "198.51.100.7"
    peers = {
      ci = { address = "172.16.99.30", access = "ci", public_key = "x" }
    }
  }
  hosts = {
    mikrotik = { vlan = "trusted", address = "172.16.10.1", mac = null, names = ["mikrotik.example.cz"] }
    pracovna = { vlan = "trusted", address = "172.16.10.2", mac = null, names = ["pracovna.example.cz"] }
  }
  webfig_port = 8443
}
"""
ACCOUNT = """locals {
  key_vault = {
    subscription_id = "00000000-0000-0000-0000-000000000000"
    name            = "kv-home"
  }
}
"""


def device(host, managed, ca="AAAA"):
    return (f'locals {{\n  name = "{host}"\n  host = "{host}"\n  managed = {str(managed).lower()}\n'
            f'  ca_bundle = <<-EOT\n    -----BEGIN CERTIFICATE-----\n    {ca}\n'
            '    -----END CERTIFICATE-----\n  EOT\n}\n')


STUBS = {
    # The OIDC token, then every REST API answers.
    "curl": '#!/bin/sh\ncase "$*" in *ACTIONS*) echo \'{"value":"t"}\';; *) echo "$*" >>"$LOG/curl";; esac\n',
    "az": '#!/bin/sh\ncase "$*" in *"secret show"*) echo "secret-of-$(echo "$*" | sed "s/.*--name \\([^ ]*\\).*/\\1/")";; esac\n',
    "sudo": '#!/bin/sh\necho "$*" >>"$LOG/sudo"\ncase "$1" in tee) cat >>"$LOG/tee-$(basename "$3$2")";; esac\n',
    "terragrunt": '#!/bin/sh\necho "SERVERKEY"\n',
}


class HomeVpn(unittest.TestCase):
    def run_up(self, devices):
        with tempfile.TemporaryDirectory() as tmp:
            root, bin_, log = (pathlib.Path(tmp, n) for n in ("repo", "bin", "log"))
            for d in (root, bin_, log):
                d.mkdir()
            (root / "_common").mkdir()
            (root / "_common/network.hcl").write_text(NETWORK)
            (root / "account.hcl").write_text(ACCOUNT)
            for name, text in devices.items():
                (root / "infrastructure" / name / "wireguard").mkdir(parents=True)
                (root / "infrastructure" / name / "device.hcl").write_text(text)
            for name, text in STUBS.items():
                (bin_ / name).write_text(text)
                (bin_ / name).chmod(0o755)
            env = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", LOG=str(log), RUNNER_TEMP=tmp,
                       ARM_CLIENT_ID="c", ARM_TENANT_ID="t", ACTIONS_ID_TOKEN_REQUEST_TOKEN="x",
                       ACTIONS_ID_TOKEN_REQUEST_URL="https://token/ACTIONS?a=b")
            out = subprocess.run(["bash", str(SCRIPT), "up"], cwd=root, env=env, capture_output=True, text=True)
            files = {p.name: p.read_text() for p in log.iterdir()}
            cas = {p.name: p.read_text() for p in pathlib.Path(tmp).glob("ca-*.crt")}
            return out, files, cas

    def test_one_managed_device(self):
        out, files, cas = self.run_up({"router": device("mikrotik", True),
                                       "access-point": device("pracovna", False)})
        self.assertEqual(out.returncode, 0, out.stderr)
        conf = next(v for k, v in files.items() if "wg-home.conf" in k)
        self.assertIn("AllowedIPs = 172.16.10.1/32\n", conf)
        self.assertIn("Endpoint = 198.51.100.7:51820", conf)
        self.assertIn("PrivateKey = secret-of-wg-key-ci", conf)
        self.assertIn("PublicKey = SERVERKEY", conf)
        self.assertIn("172.16.10.1 mikrotik.example.cz", next(v for k, v in files.items() if "hosts" in k))
        self.assertEqual(cas["ca-router.crt"], "-----BEGIN CERTIFICATE-----\nAAAA\n-----END CERTIFICATE-----\n")
        self.assertIn("https://mikrotik.example.cz:8443/", files["curl"])
        self.assertNotIn("pracovna", files["curl"])

    def test_every_managed_device(self):
        out, files, _ = self.run_up({"router": device("mikrotik", True),
                                     "access-point": device("pracovna", True)})
        self.assertEqual(out.returncode, 0, out.stderr)
        conf = next(v for k, v in files.items() if "wg-home.conf" in k)
        self.assertIn("AllowedIPs = 172.16.10.2/32,172.16.10.1/32\n", conf)
        self.assertIn("https://pracovna.example.cz:8443/", files["curl"])

    def test_no_managed_device_fails(self):
        out, _, _ = self.run_up({"router": device("mikrotik", False)})
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("managed = true", out.stdout)


if __name__ == "__main__":
    unittest.main()
