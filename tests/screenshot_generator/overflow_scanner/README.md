# Translation Overflow Scanner

Detects layout overflow caused by translated text on SeedSigner's 240x240 screens. Runs as part of the screenshot generator with zero production code changes.

## What It Detects

- **Bounds overflow**: A component extends past the 240px canvas bottom
- **Body-vs-button collision**: Body content overlaps bottom-anchored buttons (only flagged when the overlap exceeds the English baseline)

## How It Works

After each screen is fully constructed by the screenshot generator, the scanner inspects the live component positions (`screen_x`, `screen_y`, `width`, `height`) to check for overflow. For fixed-height `TextArea` components where text can render past the allocated box, it recomputes the true rendered height from internal attributes.

For each flagged screen, a composite PNG is generated with:
- English screenshot on the left, translated on the right
- Red/yellow debug outlines on the overflow areas
- Annotation with the `msgid`, `msgstr`, and overflow amount in pixels

## Usage

### Single locale (via pytest)

```bash
PYTHONPATH=src .venv/bin/pytest tests/screenshot_generator/generator.py \
    --locale ja --overflow-scan -s
```

### Translation PR review (via shell script)

Scans one or more PRs from the `seedsigner-translations` repo. The locale is auto-detected from each PR's changed files (`l10n/<locale>/...`). Each PR is checked out, its `.mo` compiled, scanned, then reset.

```bash
# Single PR
bash tests/screenshot_generator/overflow_scanner/run_overflow_scan.sh 85

# Multiple PRs
bash tests/screenshot_generator/overflow_scanner/run_overflow_scan.sh 85 83 81
```

## Output

```
seedsigner-screenshots/
  reports/
    <locale>/
      <ScreenName>_overflow.png    # annotated side-by-side composite
      summary.txt                  # one line per issue
    all_locales_summary.txt        # combined summary across all scanned PRs
```

## Files

| File | Purpose |
|------|---------|
| `overflow.py` | Core scanner: `scan_for_overflow()`, composite image generator, `.po` reverse lookup, report writer |
| `run_overflow_scan.sh` | Shell script to scan translation PRs: fetches PR branch, compiles `.mo`, runs scan, resets |

## Known Limitations

- Translation PRs must be based on a commit that includes the `fonts/` directory in the translations submodule. Older PRs (pre-PR #65) need to be rebased onto `dev` first.
- The Norwegian (`no`) PR #38 has a translation bug (positional `{}` instead of named `{mnemonic_length}` placeholders) that causes a runtime error, not an overflow detection issue.
- `PSBTOpReturnView_raw_hex_data` overflow is caused by long hex data, not translation length. It appears in every locale identically.
