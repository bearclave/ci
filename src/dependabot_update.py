#!/usr/bin/env python3
"""Whether a Dependabot pull request may merge itself: only patch and minor.

Dependabot writes what it updates into the commit message, one entry per
dependency (a group has several):

    updated-dependencies:
    - dependency-name: actions/checkout
      dependency-version: 7.0.2
      dependency-type: direct:production
      update-type: version-update:semver-patch

The largest update type of all entries decides. An entry without an update
type (a security update of an indirect dependency, a digest bump) or a major
update keeps the pull request for the owner.

Reads the commit message on stdin, prints the update type and exits 0 when
the pull request is eligible, 1 when it is not.

Usage: dependabot_update.py < message
       dependabot_update.py --self-test
"""
import re
import sys

ALLOWED = ("version-update:semver-patch", "version-update:semver-minor")
ENTRY = re.compile(r"^- dependency-name: ", re.M)
UPDATE = re.compile(r"^\s+update-type: (\S+)\s*$", re.M)


def update_type(message: str) -> str:
    """The largest update type, or a reason why there is none."""
    entries = ENTRY.split(message)[1:]
    if not entries:
        return "no updated-dependencies in the commit message"
    kinds = []
    for entry in entries:
        m = UPDATE.search(entry)
        if not m:
            return "an update without update-type"
        kinds.append(m.group(1))
    for kind in kinds:
        if kind not in ALLOWED:
            return kind
    return ALLOWED[1] if ALLOWED[1] in kinds else ALLOWED[0]


def eligible(message: str) -> bool:
    return update_type(message) in ALLOWED


def self_test() -> int:
    one = ("deps: bump x\n\n---\nupdated-dependencies:\n- dependency-name: actions/checkout\n"
           "  dependency-version: 7.0.2\n  dependency-type: direct:production\n"
           "  update-type: version-update:semver-patch\n...\n")
    group = one.replace("...\n", "- dependency-name: a/b\n  update-type: version-update:semver-minor\n...\n")
    major = one.replace("semver-patch", "semver-major")
    assert update_type(one) == ALLOWED[0] and eligible(one)
    assert update_type(group) == ALLOWED[1] and eligible(group)
    assert not eligible(major) and update_type(major) == "version-update:semver-major"
    assert not eligible(one.replace("  update-type: version-update:semver-patch\n", ""))
    assert not eligible("deps: bump x")
    print("self-test ok")
    return 0


if __name__ == "__main__":
    if "--self-test" in sys.argv:
        sys.exit(self_test())
    text = sys.stdin.read()
    print(update_type(text))
    sys.exit(0 if eligible(text) else 1)
