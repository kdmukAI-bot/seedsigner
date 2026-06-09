# MicroPython Compatibility Checker

A static analysis tool that scans SeedSigner Python source files for incompatibilities with MicroPython 1.27. It uses two passes — `mpy-cross` compilation for syntax-level errors and pattern scanning for 17 categories of known incompatibilities — and produces a detailed Markdown or JSON report.

## Why This Exists

SeedSigner is being ported to run on MicroPython 1.27 (ESP32-S3). MicroPython implements Python 3.4 with select 3.5+ features, which means many CPython standard library modules and language features are unavailable. This tool:

- **During migration**: Tracks progress as incompatibilities are fixed, with a compatibility score
- **Post-migration**: Guards against regressions via CI — ensures all future PRs maintain compatibility

The 16 stdlib incompatibility categories come from [docs/micropython_migration.md](micropython_migration.md), plus a 17th category for third-party dependency verification.

## Quick Start

### Running locally (without Docker)

```bash
# Install mpy-cross (one-time)
pip install mpy-cross==1.27.0.post2

# Run full scan
python tools/mpy_compat_check.py

# Scan specific files
python tools/mpy_compat_check.py src/seedsigner/models/seed.py src/seedsigner/controller.py

# Skip mpy-cross (if not installed)
python tools/mpy_compat_check.py --no-mpy-cross
```

### Running with Docker

```bash
# Build the image (one-time)
docker build -f docker/Dockerfile.mpy-compat -t seedsigner-mpy-compat .

# Run full scan
docker run --rm -v "$(pwd):/repo" seedsigner-mpy-compat

# Run with options
docker run --rm -v "$(pwd):/repo" seedsigner-mpy-compat --json
docker run --rm -v "$(pwd):/repo" seedsigner-mpy-compat --verbose
docker run --rm -v "$(pwd):/repo" seedsigner-mpy-compat src/seedsigner/models/seed.py
```

## CLI Reference

```
python tools/mpy_compat_check.py [OPTIONS] [FILES...]
```

| Option | Description |
|--------|-------------|
| `FILES...` | Specific files or directories to scan (default: `src/seedsigner/`) |
| `--json` | Output JSON instead of Markdown |
| `--diff BASE_JSON` | Compare against a base JSON report (diff mode) |
| `--output FILE`, `-o` | Write report to file (default: stdout) |
| `--no-mpy-cross` | Skip mpy-cross compilation step |
| `--mpy-cross-path PATH` | Path to mpy-cross binary (default: `mpy-cross`) |
| `--exclude DIR` | Additional directory to exclude (repeatable) |
| `--verbose`, `-v` | Include per-file line-by-line detail in Markdown report |

## What Gets Scanned

**Included:** All `.py` files under `src/seedsigner/` (business logic: models, views, helpers, controller)

**Excluded by default:**
| Directory/File | Reason |
|---------------|--------|
| `src/seedsigner/gui/` | Being replaced by LVGL screens from seedsigner-c-modules |
| `src/seedsigner/hardware/` | Being replaced by c-modules hardware drivers |
| `src/seedsigner/helpers/qr.py` | PIL-entangled, being superseded |
| `tests/` | Test suite stays CPython-only (pytest, unittest.mock) |

These exclusions are hardcoded defaults. Use `--exclude` to add more. When specific files are passed as arguments, only those files are scanned (exclusions do not apply).

## Detection Categories

The checker scans for 17 categories of incompatibility:

### Pass 1: mpy-cross (syntax)

Compiles each file with `mpy-cross` to catch syntax that MicroPython cannot parse: PEP 604 union types (`X | Y`), match/case statements, walrus operator (`:=`), f-string `=` debug syntax, etc.

### Pass 2: Pattern scanning (17 categories)

| # | Category | What's Detected |
|---|----------|----------------|
| 1 | `@dataclass` | `from dataclasses import`, `@dataclass` decorator |
| 2 | Type annotations | `from typing import`, PEP 604 `X \| Y` in annotations (AST), PEP 585 `list[str]` (AST) |
| 3 | `logging` | `import logging` |
| 4 | `threading` | `import threading`, `from threading import` |
| 5 | `enum.IntEnum` | `from enum import` |
| 6 | `pathlib` | `import pathlib` |
| 7 | `gettext` | `import gettext`, `from gettext import` |
| 8 | `unicodedata` | `import unicodedata` — **Bitcoin-critical** (BIP-39 seed derivation) |
| 9 | `re` repetitions | `{n}`, `{m,n}` counted repetitions inside `re.search/match/compile/findall` patterns |
| 10 | `importlib` | `from importlib import` |
| 11 | `os.fsync` | `os.fsync()` calls |
| 12 | `platform` | `import platform` |
| 13 | `traceback` | `import traceback` |
| 14 | `base64` | `import base64`, `from base64 import` |
| 15 | `hmac` | `import hmac` |
| 16 | `subprocess` | `import subprocess` |
| 17 | Third-party deps | Imports not in the known-compatible whitelist (`embit`) |

## Understanding the Report

### Summary Table

The report starts with a summary showing:
- **Files scanned / with issues / clean** — how many files have zero incompatibilities
- **Total issues** — broken down by syntax errors (mpy-cross) and pattern matches
- **Compatibility score** — percentage of files with zero issues
- **Issue density** — issues per 1000 lines of code

### Issues by Category

A table showing each category, how many times it was found, and which files are affected. This gives you an at-a-glance sense of the scale of each incompatibility type.

### Fix Reference

For each detected category, the report includes:
- **What's wrong** — why MicroPython can't handle this
- **How to fix** — the recommended approach
- **Where to learn more** — reference to the migration doc section

### Per-File Details (verbose mode)

When run with `--verbose`, the report includes a collapsible section for each file showing every issue at its exact line number with the flagged source code. This is most useful later in the migration when the list is short, or when scanning specific files.

## Diff Mode (PR Comparisons)

Diff mode compares two scans and reports what changed:

```bash
# Generate base report
git checkout main
python tools/mpy_compat_check.py --json --output base.json

# Generate head report
git checkout my-branch
python tools/mpy_compat_check.py --json --output head.json

# Generate diff
python tools/mpy_compat_check.py --diff base.json --output diff.md
```

The diff report shows:
- **Delta summary** — issues in base vs head, with +/- per metric
- **Changes by category** — which categories improved or regressed
- **New issues introduced** — full detail (file, line, code, fix guidance)
- **Issues fixed** — what was resolved
- **Verdict** — pass/fail: "No new incompatibilities introduced" or "N new in M files"

## CI Behavior

The `.github/workflows/micropython-compat.yml` workflow:

1. **Triggers** on pull requests that change Python files in `src/seedsigner/`
2. **Runs the checker** on both the PR head and the base branch
3. **Posts a sticky comment** on the PR with the diff report
4. **Updates the comment** on subsequent pushes (doesn't create duplicates)
5. **Informational only** — does not block merging

### Making it a required check

To enforce compatibility as a merge gate, add a step to the workflow that fails on new issues:

```yaml
- name: Fail on new issues
  run: |
    python -c "
    import json
    base = json.load(open('base.json'))
    head = json.load(open('head.json'))
    base_keys = {(i['file'], i['line'], i['category_id']) for i in base['issues']}
    head_keys = {(i['file'], i['line'], i['category_id']) for i in head['issues']}
    new = head_keys - base_keys
    if new:
        print(f'::error::{len(new)} new MicroPython incompatibilities introduced')
        exit(1)
    "
```

## Third-Party Dependencies (Category 17)

The checker maintains a whitelist of packages known to work on MicroPython:
- **`embit`** — MicroPython-compatible (may need version update for 1.27)

Packages being replaced during migration (`PIL`, `pyzbar`, `qrcode`) are flagged as informational. Any other third-party import is flagged as "needs verification" — this doesn't mean it's broken, just that it hasn't been tested on MicroPython.

To update the whitelist, edit the `KNOWN_COMPATIBLE` set in `tools/mpy_compat_check.py`.
