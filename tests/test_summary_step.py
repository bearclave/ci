"""The Summary step of the plan job must survive a run that plans nothing.

A change that selects no unit (only docs or .claude/** changed) starts no plan
shard, so no artifact is downloaded and the directory `parts` does not exist.
`find` on it fails and, under pipefail, so did the step (iac-home #55,
iac-sentinel #9).
"""
import pathlib
import re
import subprocess
import tempfile
import unittest

FRAGMENT = pathlib.Path(__file__).resolve().parent.parent / "templates/_fragments/terragrunt-jobs.yml"


def collect_step() -> str:
    text = FRAGMENT.read_text(encoding="utf-8")
    start = text.index("    - name: Summary\n      id: summary")
    block = text[start:]
    script = block[block.index("      run: |\n") + len("      run: |\n"):]
    lines = []
    for line in script.splitlines():
        if line.startswith("        ") or not line.strip():
            lines.append(line[8:])
        else:
            break
    body = "\n".join(lines)
    # Only what gathers the shares: up to the arguments of plan-summary.
    return body[:body.index("args=()")]


class SummaryStepTest(unittest.TestCase):
    def test_no_shard_downloaded(self):
        script = collect_step()
        self.assertIn("find parts", script)
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run(["bash", "-c", script], cwd=d, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(pathlib.Path(d, "plan.txt").read_text(), "")

    def test_shares_are_joined(self):
        script = collect_step()
        with tempfile.TemporaryDirectory() as d:
            for i in (2, 1):
                p = pathlib.Path(d, "parts", f"s{i}")
                p.mkdir(parents=True)
                (p / f"plan-{i}.txt").write_text(f"share {i}\n")
            r = subprocess.run(["bash", "-c", script], cwd=d, capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(pathlib.Path(d, "plan.txt").read_text(), "share 1\nshare 2\n")

    def test_every_fragment_user_has_it(self):
        self.assertEqual(len(re.findall(r"find parts", FRAGMENT.read_text())), 1)


if __name__ == "__main__":
    unittest.main()
