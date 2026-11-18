---
paths:
  - "**/*.sh"
---

# Bash

<!-- Standard of bearclave/governance (standards/code/shell.md). In other repositories this is a managed copy - change it in governance. -->

- `#!/usr/bin/env bash` and `set -euo pipefail` on the second line; a header
  comment with what the script does and its usage.
- Quote every expansion (`"$var"`, `"${arr[@]}"`); `[[ ]]` or `[ ]`
  consistently; `$(...)`, never backticks.
- Functions for anything used twice; `local` variables inside them.
- Temporary files with `mktemp`, removed by a `trap ... EXIT`.
- Downloads are checked against a sha256 before use.
- No secret on a command line or in `set -x` output; read secrets into a
  variable, mask them in a workflow (`::add-mask::`).
- shellcheck clean at `--severity=warning` (the `security / scan` check runs
  it over every `*.sh` and every `run:` block).
- A script longer than ~150 lines or with real logic becomes Python.
