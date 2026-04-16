#!/usr/bin/env python3
"""
Compare translation PR PO files against Transifex reference files.

Determines whether a translation PR in SeedSigner/seedsigner-translations is
ready to merge by diffing its PO file against the Transifex source of truth.

PREREQUISITES
    - GitHub CLI (`gh`) authenticated with access to SeedSigner/seedsigner-translations
    - Python `babel` package (included in the project's .venv)
    - A `transifex-reference/` directory in the project root containing the latest
      Transifex PO files, organized as `transifex-reference/<locale>/messages.po`.
      To populate it:
        1. Install the Transifex CLI (`tx`):
           https://developers.transifex.com/docs/cli
        2. cd into the seedsigner-translations submodule (has `.tx/config`):
           cd src/seedsigner/resources/seedsigner-translations
        3. Pull the latest translations:
           tx pull -f --all
        4. Copy PO files into transifex-reference/ at the project root,
           organized as transifex-reference/<locale>/messages.po

HOW IT WORKS
    For each PR number provided, the script:
    1. Queries the GitHub API for PR metadata (author, fork, branch, changed files)
    2. Auto-detects the locale(s) from changed `l10n/<locale>/LC_MESSAGES/messages.po`
       paths in the PR's file list
    3. Downloads the PO file from the contributor's fork branch
    4. Parses both PO files with Babel's read_po (msgid-keyed, so entry reordering
       in the PO file does not produce false diffs)
    5. Categorizes every msgid into: matching, differing, translated in one but
       not the other, or present in only one file

VERDICTS
    Ready to merge        -- PR matches Transifex exactly
    Ready to merge (PR has extras) -- PR has translations Transifex lacks (contributor
                             added strings not yet in TX)
    PR diverges from TX   -- Same msgids, but different translations
    PR behind TX          -- Transifex has newer translations the PR is missing
    Stale template        -- PR was built on an older POT; entire msgids are absent.
                             Needs rebase onto the current template before merging.

MULTI-LOCALE PRS
    Some PRs (especially early ones that bootstrapped the repo) touch PO files for
    many locales. By default, all detected locales are compared. Use --locale to
    focus on a single one.

USAGE
    # From the project root:
    python tools/compare_pr_vs_tx.py 83              # single PR, auto-detect locale
    python tools/compare_pr_vs_tx.py 83 --locale ko  # single PR, specific locale
    python tools/compare_pr_vs_tx.py 85 83 73 61     # multiple PRs
    python tools/compare_pr_vs_tx.py --all-open       # all open translation PRs

OUTPUT
    Progress messages go to stderr. The report goes to stdout, so you can redirect:
        python tools/compare_pr_vs_tx.py --all-open > report.txt
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import HTTPError

from babel.messages.pofile import read_po

PROJECT_ROOT = Path(__file__).parent.parent
TX_DIR = PROJECT_ROOT / "transifex-reference"
TRANSLATIONS_REPO = "SeedSigner/seedsigner-translations"


def gh_json(args: list[str]) -> dict | list:
    """Run a gh command and return parsed JSON."""
    result = subprocess.run(
        ["gh"] + args,
        capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"gh command failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


def fetch_pr_info(pr_num: int, locale_filter: str | None = None) -> list[dict]:
    """Fetch PR metadata and download PO file(s). Returns a list of dicts,
    one per locale found in the PR. Each dict has pr_num, author, locale,
    title, state, and a path to the downloaded PO file (or an error string).

    If locale_filter is set, only that locale is returned.
    """
    pr_data = gh_json([
        "pr", "view", str(pr_num),
        "--repo", TRANSLATIONS_REPO,
        "--json", "title,headRefName,headRepositoryOwner,headRepository,state,files",
    ])

    author = pr_data["headRepositoryOwner"]["login"]
    fork_repo = pr_data["headRepository"]["name"]
    branch = pr_data["headRefName"]
    title = pr_data["title"]
    state = pr_data["state"]

    # Detect all locales from changed files
    locale_files = {}
    for f in pr_data.get("files", []):
        m = re.match(r"l10n/([^/]+)/LC_MESSAGES/messages\.po$", f["path"])
        if m:
            locale_files[m.group(1)] = f["path"]

    if not locale_files:
        return [{
            "pr_num": pr_num,
            "author": author,
            "title": title,
            "state": state,
            "error": "Could not detect locale from PR changed files",
        }]

    if locale_filter:
        if locale_filter not in locale_files:
            return [{
                "pr_num": pr_num,
                "author": author,
                "locale": locale_filter,
                "title": title,
                "state": state,
                "error": f"Locale '{locale_filter}' not found in PR (has: {', '.join(sorted(locale_files))})",
            }]
        locale_files = {locale_filter: locale_files[locale_filter]}

    results = []
    for locale, po_file_path in sorted(locale_files.items()):
        url = f"https://raw.githubusercontent.com/{author}/{fork_repo}/{branch}/{po_file_path}"
        try:
            req = Request(url)
            with urlopen(req) as resp:
                po_content = resp.read()
        except HTTPError as e:
            results.append({
                "pr_num": pr_num,
                "author": author,
                "locale": locale,
                "title": title,
                "state": state,
                "error": f"Failed to download PO from fork: HTTP {e.code}",
            })
            continue

        tmp = tempfile.NamedTemporaryFile(
            mode="wb", suffix=".po", prefix=f"pr{pr_num}_{locale}_",
            delete=False,
        )
        tmp.write(po_content)
        tmp.close()

        results.append({
            "pr_num": pr_num,
            "author": author,
            "locale": locale,
            "title": title,
            "state": state,
            "po_path": tmp.name,
        })

    return results


def extract_pot_date(catalog):
    """Extract POT-Creation-Date from catalog metadata."""
    for key, val in catalog.mime_headers:
        if key == "POT-Creation-Date":
            return val
    return "unknown"


def get_msgstr(message):
    """Get the translation string, handling plurals."""
    if message.pluralizable:
        return tuple(message.string) if isinstance(message.string, (list, tuple)) else (message.string,)
    return message.string


def is_translated(message):
    """Check if a message has a non-empty translation."""
    s = message.string
    if isinstance(s, (list, tuple)):
        return any(part.strip() for part in s if part)
    return bool(s and s.strip())


def compare_po_files(tx_path: Path, pr_path: str) -> dict:
    """Compare a PR's PO file against the Transifex reference.

    Returns a dict with comparison results.
    """
    if not tx_path.exists():
        return {"error": f"TX reference file missing: {tx_path}"}

    with open(tx_path, "r", encoding="utf-8") as f:
        tx_catalog = read_po(f)
    with open(pr_path, "r", encoding="utf-8") as f:
        pr_catalog = read_po(f)

    tx_pot_date = extract_pot_date(tx_catalog)
    pr_pot_date = extract_pot_date(pr_catalog)

    # Build dicts keyed by msgid
    tx_msgs = {msg.id: msg for msg in tx_catalog if msg.id}
    pr_msgs = {msg.id: msg for msg in pr_catalog if msg.id}

    all_msgids = set(tx_msgs.keys()) | set(pr_msgs.keys())

    # Categorize each msgid
    match = []
    differs = []
    tx_translated_pr_untranslated = []
    pr_translated_tx_untranslated = []
    both_untranslated = []
    msgid_only_in_tx = []
    msgid_only_in_pr = []

    for msgid in sorted(all_msgids, key=str):
        in_tx = msgid in tx_msgs
        in_pr = msgid in pr_msgs

        if in_tx and not in_pr:
            msgid_only_in_tx.append(msgid)
            continue
        if in_pr and not in_tx:
            msgid_only_in_pr.append(msgid)
            continue

        tx_msg = tx_msgs[msgid]
        pr_msg = pr_msgs[msgid]
        tx_trans = is_translated(tx_msg)
        pr_trans = is_translated(pr_msg)

        if tx_trans and pr_trans:
            if get_msgstr(tx_msg) == get_msgstr(pr_msg):
                match.append(msgid)
            else:
                differs.append(msgid)
        elif tx_trans and not pr_trans:
            tx_translated_pr_untranslated.append(msgid)
        elif pr_trans and not tx_trans:
            pr_translated_tx_untranslated.append(msgid)
        else:
            both_untranslated.append(msgid)

    tx_translated_count = sum(1 for m in tx_msgs.values() if is_translated(m))
    pr_translated_count = sum(1 for m in pr_msgs.values() if is_translated(m))

    return {
        "tx_pot_date": tx_pot_date,
        "pr_pot_date": pr_pot_date,
        "tx_total_msgids": len(tx_msgs),
        "pr_total_msgids": len(pr_msgs),
        "tx_translated": tx_translated_count,
        "pr_translated": pr_translated_count,
        "match": len(match),
        "differs": len(differs),
        "differs_list": differs,
        "tx_translated_pr_untranslated": len(tx_translated_pr_untranslated),
        "tx_translated_pr_untranslated_list": tx_translated_pr_untranslated,
        "pr_translated_tx_untranslated": len(pr_translated_tx_untranslated),
        "pr_translated_tx_untranslated_list": pr_translated_tx_untranslated,
        "both_untranslated": len(both_untranslated),
        "msgid_only_in_tx": len(msgid_only_in_tx),
        "msgid_only_in_tx_list": msgid_only_in_tx,
        "msgid_only_in_pr": len(msgid_only_in_pr),
        "msgid_only_in_pr_list": msgid_only_in_pr,
    }


def determine_verdict(result: dict) -> str:
    """Determine the actionable verdict for a PR."""
    if "error" in result:
        return "ERROR"

    stale_template = result["tx_pot_date"] != result["pr_pot_date"]
    has_tx_only_msgids = result["msgid_only_in_tx"] > 0
    has_differs = result["differs"] > 0
    has_tx_translated_pr_not = result["tx_translated_pr_untranslated"] > 0
    has_pr_translated_tx_not = result["pr_translated_tx_untranslated"] > 0

    if stale_template and has_tx_only_msgids:
        return "Stale template"
    if has_tx_translated_pr_not or has_tx_only_msgids:
        return "PR behind TX"
    if has_differs:
        return "PR diverges from TX"
    if has_pr_translated_tx_not:
        return "Ready to merge (PR has extras)"
    return "Ready to merge"


def get_all_open_pr_numbers() -> list[int]:
    """Fetch all open PR numbers from the translations repo."""
    prs = gh_json([
        "pr", "list",
        "--repo", TRANSLATIONS_REPO,
        "--state", "open",
        "--json", "number,files",
        "--limit", "100",
    ])
    # Only include PRs that touch l10n/ PO files (skip docs-only PRs)
    translation_prs = []
    for pr in prs:
        for f in pr.get("files", []):
            if re.match(r"l10n/[^/]+/LC_MESSAGES/messages\.po$", f["path"]):
                translation_prs.append(pr["number"])
                break
    return sorted(translation_prs)


def print_report(entries: list[dict]):
    """Print the comparison report.

    Each entry has: pr_num, author, locale, title, state, and either
    an error or a comparison result dict.
    """
    print("=" * 120)
    print("TRANSLATION PR vs TRANSIFEX COMPARISON REPORT")
    print("=" * 120)
    print()

    # Summary table
    header = (
        f"{'Locale':<14} {'PR#':>4} {'Author':<18} {'POT Date (PR)':<22} "
        f"{'Trans PR':>9} {'Trans TX':>9} {'Untrans':>8} "
        f"{'Match':>6} {'Differ':>7} {'TX>PR':>6} {'PR>TX':>6} {'TX-only':>8} {'Verdict'}"
    )
    print(header)
    print("-" * len(header))

    for entry in entries:
        pr_num = entry["pr_num"]
        author = entry["author"]
        locale = entry.get("locale", "?")

        if "error" in entry:
            print(
                f"{locale:<14} {pr_num:>4} {author:<18} {'ERROR':<22} "
                f"{'-':>9} {'-':>9} {'-':>8} {'-':>6} {'-':>7} {'-':>6} {'-':>6} {'-':>8} "
                f"{entry['error']}"
            )
            continue

        r = entry["result"]
        if "error" in r:
            print(
                f"{locale:<14} {pr_num:>4} {author:<18} {'ERROR':<22} "
                f"{'-':>9} {'-':>9} {'-':>8} {'-':>6} {'-':>7} {'-':>6} {'-':>6} {'-':>8} "
                f"{r['error']}"
            )
            continue

        verdict = determine_verdict(r)
        pot_match = "same" if r["tx_pot_date"] == r["pr_pot_date"] else r["pr_pot_date"]

        print(
            f"{locale:<14} {pr_num:>4} {author:<18} {pot_match:<22} "
            f"{r['pr_translated']:>9} {r['tx_translated']:>9} "
            f"{r['both_untranslated']:>8} "
            f"{r['match']:>6} {r['differs']:>7} "
            f"{r['tx_translated_pr_untranslated']:>6} "
            f"{r['pr_translated_tx_untranslated']:>6} "
            f"{r['msgid_only_in_tx']:>8} "
            f"{verdict}"
        )

    print()
    print("Column legend:")
    print("  Trans PR/TX  = number of translated strings in PR / Transifex")
    print("  Untrans      = msgids present in both but untranslated in both")
    print("  Match        = msgid in both, same translation")
    print("  Differ       = msgid in both, different translation")
    print("  TX>PR        = translated in TX, untranslated in PR (same msgid exists in both)")
    print("  PR>TX        = translated in PR, untranslated in TX")
    print("  TX-only      = msgid exists in TX but is completely absent from PR (stale template)")
    print()

    # Detail sections for PRs needing attention
    details_needed = [
        e for e in entries
        if "error" not in e
        and "result" in e
        and "error" not in e["result"]
        and (not determine_verdict(e["result"]).startswith("Ready to merge")
             or e["result"].get("pr_translated_tx_untranslated", 0) > 0)
    ]

    if not details_needed:
        return

    print("=" * 120)
    print("DETAILS FOR PRs NEEDING ATTENTION")
    print("=" * 120)

    for entry in details_needed:
        r = entry["result"]
        pr_num = entry["pr_num"]
        author = entry["author"]
        locale = entry["locale"]
        verdict = determine_verdict(r)

        print()
        print(f"--- {locale} (PR #{pr_num}, {author}) — {verdict} ---")

        if r["tx_pot_date"] != r["pr_pot_date"]:
            print(f"  POT-Creation-Date: TX={r['tx_pot_date']}  PR={r['pr_pot_date']}")

        for label, key in [
            ("msgid(s) only in TX (absent from PR)", "msgid_only_in_tx"),
            ("msgid(s) with different translations", "differs"),
            ("msgid(s) translated in TX but empty in PR", "tx_translated_pr_untranslated"),
            ("msgid(s) translated in PR but empty in TX", "pr_translated_tx_untranslated"),
            ("msgid(s) only in PR (removed from current template)", "msgid_only_in_pr"),
        ]:
            count = r[key]
            items = r.get(f"{key}_list", [])
            if count > 0:
                print(f"  {count} {label}:")
                for msgid in items[:10]:
                    display = repr(msgid[:80]) if isinstance(msgid, str) else repr(msgid)
                    print(f"    - {display}")
                if count > 10:
                    print(f"    ... and {count - 10} more")


def main():
    parser = argparse.ArgumentParser(
        description="Compare translation PR PO files against Transifex reference.",
        epilog="Examples:\n"
               "  python compare_pr_vs_tx.py 83\n"
               "  python compare_pr_vs_tx.py 83 --locale ko\n"
               "  python compare_pr_vs_tx.py 85 83 73 61\n"
               "  python compare_pr_vs_tx.py --all-open\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "pr_numbers", nargs="*", type=int,
        help="PR number(s) to compare",
    )
    parser.add_argument(
        "--all-open", action="store_true",
        help="Compare all open translation PRs",
    )
    parser.add_argument(
        "--locale", type=str, default=None,
        help="Only compare this locale (useful for PRs that touch multiple locales)",
    )
    args = parser.parse_args()

    if not args.pr_numbers and not args.all_open:
        parser.print_help()
        sys.exit(1)

    if args.all_open:
        print("Fetching open translation PRs...", file=sys.stderr)
        pr_numbers = get_all_open_pr_numbers()
        print(f"Found {len(pr_numbers)} open translation PRs: {pr_numbers}", file=sys.stderr)
    else:
        pr_numbers = args.pr_numbers

    if not TX_DIR.exists():
        print(f"Error: Transifex reference directory not found: {TX_DIR}", file=sys.stderr)
        print(file=sys.stderr)
        print("This script compares PRs against a local copy of the Transifex PO files.", file=sys.stderr)
        print("To set it up:", file=sys.stderr)
        print("  1. Install the Transifex CLI: https://developers.transifex.com/docs/cli", file=sys.stderr)
        print("  2. cd src/seedsigner/resources/seedsigner-translations  (has .tx/config)", file=sys.stderr)
        print("  3. tx pull -f --all", file=sys.stderr)
        print("  4. Copy the PO files into transifex-reference/ at the project root,", file=sys.stderr)
        print("     organized as transifex-reference/<locale>/messages.po", file=sys.stderr)
        sys.exit(1)

    entries = []
    tmp_files = []

    for pr_num in pr_numbers:
        print(f"Fetching PR #{pr_num}...", file=sys.stderr)
        infos = fetch_pr_info(pr_num, locale_filter=args.locale)

        for info in infos:
            if "error" in info:
                print(f"  {info.get('locale', '?')}: {info['error']}", file=sys.stderr)
                entries.append(info)
                continue

            locale = info["locale"]
            print(f"  {locale} (author={info['author']}) — comparing...", file=sys.stderr)

            tx_path = TX_DIR / locale / "messages.po"
            result = compare_po_files(tx_path, info["po_path"])
            tmp_files.append(info["po_path"])

            entries.append({
                "pr_num": pr_num,
                "author": info["author"],
                "locale": locale,
                "title": info["title"],
                "state": info["state"],
                "result": result,
            })

    print(file=sys.stderr)

    # Sort by PR number descending
    entries.sort(key=lambda e: e["pr_num"], reverse=True)
    print_report(entries)

    # Clean up temp files
    for f in tmp_files:
        try:
            os.unlink(f)
        except OSError:
            pass


if __name__ == "__main__":
    main()
