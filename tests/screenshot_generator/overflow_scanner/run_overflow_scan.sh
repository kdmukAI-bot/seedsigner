#!/bin/bash
# Run overflow scans for open translation PRs.
# The locale is auto-detected from each PR's changed files (l10n/<locale>/...).
#
# Usage:
#   bash tests/screenshot_generator/overflow_scanner/run_overflow_scan.sh PR_NUMBER [PR_NUMBER ...]
#
# Examples:
#   bash tests/screenshot_generator/overflow_scanner/run_overflow_scan.sh 85
#   bash tests/screenshot_generator/overflow_scanner/run_overflow_scan.sh 85 83 82

set -e

if [ $# -eq 0 ]; then
    echo "Usage: $0 PR_NUMBER [PR_NUMBER ...]"
    echo "Example: $0 85 83 82"
    exit 1
fi

SEEDSIGNER_ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
TRANSLATIONS_DIR="$SEEDSIGNER_ROOT/src/seedsigner/resources/seedsigner-translations"
BASE_BRANCH="dev"

declare -a PR_NUMBERS=("$@")

cd "$SEEDSIGNER_ROOT"

# Generate EN screenshots first (needed for composites)
echo "========================================="
echo "Generating EN screenshots..."
echo "========================================="
PYTHONPATH=src .venv/bin/python3 -m pytest tests/screenshot_generator/generator.py --locale en -s 2>&1 | tail -3

echo ""
echo "========================================="
echo "Running overflow scans for ${#PR_NUMBERS[@]} translation PRs"
echo "========================================="

SUMMARY_FILE="$SEEDSIGNER_ROOT/seedsigner-screenshots/reports/all_locales_summary.txt"
mkdir -p "$(dirname "$SUMMARY_FILE")"
echo "Translation Overflow Scan Results" > "$SUMMARY_FILE"
echo "==================================" >> "$SUMMARY_FILE"
echo "" >> "$SUMMARY_FILE"

for PR_NUM in "${PR_NUMBERS[@]}"; do
    # Detect locale from the PR's changed files
    cd "$TRANSLATIONS_DIR"
    LOCALE=$(gh pr diff "$PR_NUM" --name-only 2>&1 | grep '^l10n/' | head -1 | cut -d'/' -f2)

    if [ -z "$LOCALE" ]; then
        echo ""
        echo "-----------------------------------------"
        echo "PR #$PR_NUM — SKIPPED: no l10n/ changes found"
        echo "-----------------------------------------"
        echo "PR #$PR_NUM  SKIPPED: no l10n/ changes found" >> "$SUMMARY_FILE"
        echo "" >> "$SUMMARY_FILE"
        continue
    fi

    echo ""
    echo "-----------------------------------------"
    echo "PR #$PR_NUM — locale: $LOCALE"
    echo "-----------------------------------------"

    # Clean any .mo files left from prior iteration before switching branches
    find l10n -name '*.mo' -delete 2>/dev/null
    gh pr checkout "$PR_NUM" --force 2>&1 | tail -1

    # Compile .mo for this locale only
    cd "$SEEDSIGNER_ROOT"
    .venv/bin/pybabel compile -d "$TRANSLATIONS_DIR/l10n" -l "$LOCALE" --use-fuzzy 2>&1 | tail -1

    # Run overflow scan (don't abort on test failure — handled below)
    RESULT=$(PYTHONPATH=src .venv/bin/python3 -m pytest tests/screenshot_generator/generator.py --locale "$LOCALE" --overflow-scan -s 2>&1) || true
    ISSUES=$(echo "$RESULT" | grep "Overflow issues found:" | grep -o '[0-9]*' || echo "0")
    COMPOSITES=$(echo "$RESULT" | grep "Composite images generated:" | grep -o '[0-9]*' || echo "0")
    NO_ISSUES=$(echo "$RESULT" | grep "No overflow issues detected" || true)

    # Stamp the per-locale summary with the PR number
    LOCALE_SUMMARY="$SEEDSIGNER_ROOT/seedsigner-screenshots/reports/$LOCALE/summary.txt"
    if [ -f "$LOCALE_SUMMARY" ]; then
        sed -i "s/Overflow Report for locale: $LOCALE/Overflow Report for PR #$PR_NUM ($LOCALE)/" "$LOCALE_SUMMARY"
    fi

    if [ -n "$NO_ISSUES" ]; then
        echo "  ✓ No overflow issues"
        echo "PR #$PR_NUM ($LOCALE): 0 issues" >> "$SUMMARY_FILE"
    else
        echo "  ⚠ $ISSUES overflow issues, $COMPOSITES composites"
        echo "PR #$PR_NUM ($LOCALE): $ISSUES issues" >> "$SUMMARY_FILE"
        # Append per-locale detail
        if [ -f "$LOCALE_SUMMARY" ]; then
            sed 's/^/    /' "$LOCALE_SUMMARY" >> "$SUMMARY_FILE"
        fi
    fi
    echo "" >> "$SUMMARY_FILE"

    # Check for test failures
    if echo "$RESULT" | grep -q "FAILED"; then
        FAILURE_MSG=$(echo "$RESULT" | grep "FAILED" | head -1)
        echo "  ✗ TEST FAILED: $FAILURE_MSG"
        echo "  FAILED: $FAILURE_MSG" >> "$SUMMARY_FILE"
    fi

    # Reset submodule for next iteration
    cd "$TRANSLATIONS_DIR"
    find l10n -name '*.mo' -delete 2>/dev/null
    git checkout "$BASE_BRANCH" --quiet 2>/dev/null || git checkout --detach "origin/$BASE_BRANCH" --quiet
done

echo ""
echo "========================================="
echo "All scans complete. Summary at:"
echo "$SUMMARY_FILE"
echo "========================================="
cat "$SUMMARY_FILE"
