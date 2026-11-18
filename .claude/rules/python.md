---
paths:
  - "**/*.py"
---

# Python

<!-- Standard of bearclave/governance (standards/code/python.md). In other repositories this is a managed copy - change it in governance. -->

- Python 3.12+. Pipeline and repository scripts use the **standard library
  only**, so they run on a bare runner; applications declare dependencies in
  `pyproject.toml` with a lock file (`uv`).
- Module docstring saying what the script does and how to call it
  (`Usage:`), `if __name__ == "__main__": sys.exit(main())`.
- Type hints on functions; `pathlib` for paths; `argparse` for arguments;
  `subprocess.run([...], check=True)` with a list, never `shell=True`.
- `ruff check` and `ruff format` clean (applications); scripts follow the
  same style.
- Tests with `unittest` (scripts) or `pytest` (applications), next to the
  code in `tests/`; every script that decides what gets planned, applied or
  deployed has tests.
- Errors as messages a person can act on; in a workflow as
  `::error file=...::` annotations.
- Run with `python3 -I` when reading files from an untrusted place.
