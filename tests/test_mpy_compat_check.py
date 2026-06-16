"""Tests for the MicroPython 1.27 compatibility checker (`tools/mpy_compat_check.py`).

The checker gates CI via a per-category ratchet, so it needs its own tests: if a
detector (regex or AST visitor) silently stops matching, a regression could slip
past a category that has been flipped to `fail`. Three things are verified here:

  1. Detection      — each category flags a known-incompatible snippet.
  2. No false flags  — a compatible snippet of the same shape is NOT flagged.
  3. Ratchet logic   — `evaluate_severity` blocks on a `fail` category and only
                       on a `fail` category; `load_baseline` defaults to `warn`.

Plus two data-driven invariants tied to the real baseline + business-logic tree,
so every stage's `warn`->`fail` flip is automatically verified as the stack grows:

  4. Every category marked `fail` has zero occurrences in `src/seedsigner`.
  5. No category that still has occurrences is marked `fail` (you cannot enforce a
     category while the tree is still dirty).

The checker is loaded by file path (it lives in `tools/`, not an importable
package). All scans run with `use_mpy_cross=False`, so no toolchain is required.
"""

import functools
import importlib.util
import os
import pathlib

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
CHECKER_PATH = REPO_ROOT / "tools" / "mpy_compat_check.py"
REAL_BASELINE = REPO_ROOT / "tools" / "mpy_compat_baseline.json"


def _load_checker():
    spec = importlib.util.spec_from_file_location("mpy_compat_check", CHECKER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mpy = _load_checker()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _scan(tmp_path, code):
    """Write `code` to a temp .py file and return the checker's Issues for it."""
    f = tmp_path / "snippet.py"
    f.write_text(code)
    return mpy.scan_file(str(f), use_mpy_cross=False)


def _categories(issues):
    return {i.category_id for i in issues}


def _result(tmp_path, code):
    """Build a checker result dict (the shape `evaluate_severity` consumes)."""
    f = tmp_path / "snippet.py"
    f.write_text(code)
    issues = mpy.scan_file(str(f), use_mpy_cross=False)
    return str(f), mpy.build_result([str(f)], issues)


@functools.lru_cache(maxsize=1)
def _real_tree_issues():
    """Scan the real business-logic tree once, exactly as the CLI does.

    The checker's EXCLUDED_DIRS (gui/, hardware/, ...) are repo-root-relative, so
    the scan must run from the repo root for them to match — otherwise gui/ and
    hardware/ leak in. Reuse the checker's own scan root + exclusions rather than
    duplicating them here.
    """
    prev_cwd = os.getcwd()
    os.chdir(REPO_ROOT)
    try:
        files = mpy.discover_files(mpy.DEFAULT_SCAN_ROOT)
        issues = []
        for f in files:
            issues.extend(mpy.scan_file(f, use_mpy_cross=False))
        return tuple(issues)
    finally:
        os.chdir(prev_cwd)


# ---------------------------------------------------------------------------
# 1. Detection — each category flags an incompatible snippet
# ---------------------------------------------------------------------------

# (label, category_id, incompatible_snippet)
DETECT_CASES = [
    ("dataclasses_import",     1,  "from dataclasses import dataclass\n"),
    ("dataclass_decorator",    1,  "@dataclass\nclass X:\n    a = 0\n"),
    ("typing_import",          2,  "from typing import List\n"),
    ("executed_generic_base",  2,  "class C(list[int]):\n    pass\n"),
    ("type_alias_generic",     2,  "Alias = list[int]\n"),
    ("threading_import",       4,  "import threading\n"),
    ("enum_import",            5,  "from enum import IntEnum\n"),
    ("pathlib_import",         6,  "import pathlib\n"),
    ("gettext_import",         7,  "from gettext import gettext\n"),
    ("unicodedata_import",     8,  "import unicodedata\n"),
    ("re_counted_repetition",  9,  're.search(r"a{2,4}", s)\n'),
    ("re_ignorecase_flag",     9,  "re.search(pat, s, re.IGNORECASE)\n"),
    ("re_findall_function",    9,  "re.findall(pat, s)\n"),
    ("importlib_import",       10, "from importlib import import_module\n"),
    ("os_fsync_call",          11, "os.fsync(f.fileno())\n"),
    ("platform_import",        12, "import platform\n"),
    ("traceback_import",       13, "import traceback\n"),
    ("base64_import",          14, "import base64\n"),
    ("hmac_import",            15, "import hmac\n"),
    ("subprocess_import",      16, "import subprocess\n"),
    ("os_path_attr",           18, "if os.path.exists(p):\n    pass\n"),
    ("os_environ",             18, 'os.environ["LANGUAGE"] = "en"\n'),
    ("absent_stdlib_queue",    20, "import queue\n"),
    ("extended_slice",         19, "y = data[::-1]\n"),
    ("bit_length_call",        21, "n = x.bit_length()\n"),
    ("multiple_inheritance",   22, "class C(A, B):\n    pass\n"),
]


@pytest.mark.parametrize("label,cat,code", DETECT_CASES, ids=[c[0] for c in DETECT_CASES])
def test_detects_incompatibility(tmp_path, label, cat, code):
    assert cat in _categories(_scan(tmp_path, code)), \
        f"checker failed to flag category {cat} for: {code!r}"


# ---------------------------------------------------------------------------
# 2. No false positives — a compatible snippet must NOT trip the category
# ---------------------------------------------------------------------------

# (label, category_id, compatible_snippet)
CLEAN_CASES = [
    ("explicit_init",          1,  "class X:\n    def __init__(self):\n        self.a = 0\n"),
    # bare annotations are parsed-and-discarded by MicroPython — the key guarantee
    ("bare_generic_annotation", 2, "def f(x: list[str]) -> dict[str, int]:\n    y: list[int] = []\n    return {}\n"),
    ("no_threading",           4,  "def run():\n    pass\n"),
    ("plain_constants",        5,  "class Status:\n    A = 1\n    B = 2\n"),
    ("string_path_ops",        6,  'here = __file__.rsplit("/", 1)[0]\n'),
    ("compat_gettext_import",  7,  "from seedsigner.compat.l10n import gettext\n"),
    ("no_unicodedata",         8,  "s = ' '.join(words)\n"),
    ("plain_regex",            9,  're.search(r"abc", s)\n'),
    ("dunder_import",          10, "mod = __import__('embit')\n"),
    ("flush_not_fsync",        11, "f.flush()\n"),
    ("os_uname_not_platform",  12, "host = os.uname().nodename\n"),
    ("sys_print_exception",    13, "sys.print_exception(e)\n"),
    ("binascii_not_base64",    14, "from binascii import b2a_base64\n"),
    ("no_hmac",                15, "from embit import hashes\n"),
    ("os_stat_not_path",       18, "os.stat(p)\n"),
    ("simple_slice",           19, "y = data[1:5]\n"),
    ("no_bit_length",          21, "n = len(bin(x)) - 2\n"),
    ("single_base",            22, "class C(Base):\n    pass\n"),
]


@pytest.mark.parametrize("label,cat,code", CLEAN_CASES, ids=[c[0] for c in CLEAN_CASES])
def test_no_false_positive(tmp_path, label, cat, code):
    assert cat not in _categories(_scan(tmp_path, code)), \
        f"checker wrongly flagged category {cat} for compatible code: {code!r}"


# ---------------------------------------------------------------------------
# 3. Ratchet logic — evaluate_severity / load_baseline
# ---------------------------------------------------------------------------

def test_warn_severity_never_blocks(tmp_path):
    _, result = _result(tmp_path, "from typing import List\n")
    exit_code, annotations = mpy.evaluate_severity(result, {"categories": {"2": {"severity": "warn"}}})
    assert exit_code == 0
    assert annotations and "::warning" in annotations[0]


def test_fail_severity_blocks(tmp_path):
    _, result = _result(tmp_path, "from typing import List\n")
    exit_code, annotations = mpy.evaluate_severity(result, {"categories": {"2": {"severity": "fail"}}})
    assert exit_code == 1
    assert any("::error" in a for a in annotations)


def test_fail_severity_respects_allowed_files(tmp_path):
    path, result = _result(tmp_path, "from typing import List\n")
    baseline = {"categories": {"2": {"severity": "fail", "allowed_files": [path]}}}
    exit_code, _ = mpy.evaluate_severity(result, baseline)
    assert exit_code == 0


def test_unknown_category_defaults_to_warn(tmp_path):
    _, result = _result(tmp_path, "from typing import List\n")
    exit_code, _ = mpy.evaluate_severity(result, {"categories": {}})
    assert exit_code == 0


def test_missing_baseline_file_defaults_to_warn(tmp_path):
    baseline = mpy.load_baseline(str(tmp_path / "does_not_exist.json"))
    assert baseline == {"categories": {}}


# ---------------------------------------------------------------------------
# 4 & 5. Ratchet/tree consistency against the real baseline (auto-covers each flip)
# ---------------------------------------------------------------------------

def _real_severities():
    return mpy.load_baseline(str(REAL_BASELINE)).get("categories", {})


def test_failed_categories_have_no_occurrences_in_tree():
    """A category may only be `fail` once the tree is clean of it (the ratchet's
    promise). As each stage flips its category, this asserts the flip is earned."""
    severities = _real_severities()
    failed = {cid for cid, e in severities.items() if e.get("severity") == "fail"}
    counts = {}
    for issue in _real_tree_issues():
        cid = str(issue.category_id)
        if cid in failed:
            counts[cid] = counts.get(cid, 0) + 1
    assert counts == {}, f"categories marked `fail` but still present in tree: {counts}"


def test_dirty_categories_are_not_marked_fail():
    """Inverse guard: a category with occurrences in the tree must stay `warn`."""
    severities = _real_severities()
    offenders = []
    for issue in _real_tree_issues():
        cid = str(issue.category_id)
        if severities.get(cid, {}).get("severity") == "fail":
            offenders.append(cid)
    assert not offenders, f"categories still present in tree but marked `fail`: {sorted(set(offenders))}"


# ---------------------------------------------------------------------------
# Per-stage enforcement proof — mpy/01: type annotations (category 2)
#
# Each stage that flips a category to `fail` adds a focused test like this: the
# baseline enforces the category, the tree is clean of it, and a fresh violation
# would block CI. Copy it for stages 02-08, swapping the category id and snippet.
# ---------------------------------------------------------------------------

def test_cat2_type_annotations_enforced(tmp_path):
    assert _real_severities()["2"]["severity"] == "fail"
    # the business-logic tree is clean of typing imports / executed generics
    assert [i for i in _real_tree_issues() if i.category_id == 2] == []
    # a fresh category-2 violation would block CI under the shipped baseline
    _, result = _result(tmp_path, "from typing import List\n")
    exit_code, _ = mpy.evaluate_severity(result, mpy.load_baseline(str(REAL_BASELINE)))
    assert exit_code == 1


# ---------------------------------------------------------------------------
# Per-stage enforcement proof — mpy/02: stdlib swaps (categories 5, 6, 10, 11, 12)
#
# Same shape as the mpy/01 cat-2 proof, parametrized over the categories this
# stage drives to zero and flips to `fail` (enum, pathlib, importlib, os.fsync,
# platform): the baseline enforces each, the tree is clean of it, and a fresh
# violation would block CI. Category 18 (os.path/os.walk/os.environ) is NOT here
# — its os.environ occurrences are resolved with the gettext swap in mpy/05, so
# it stays `warn` until then (covered by the data-driven invariants above).
# ---------------------------------------------------------------------------

# (category_id, fresh_violation_snippet)
MPY02_ENFORCED = [
    (5,  "from enum import IntEnum\n"),
    (6,  "import pathlib\n"),
    (10, "from importlib import import_module\n"),
    (11, "os.fsync(f.fileno())\n"),
    (12, "import platform\n"),
]


@pytest.mark.parametrize("cat,violation", MPY02_ENFORCED,
                         ids=[f"cat{c}" for c, _ in MPY02_ENFORCED])
def test_mpy02_stdlib_category_enforced(tmp_path, cat, violation):
    cid = str(cat)
    assert _real_severities()[cid]["severity"] == "fail"
    # the business-logic tree is clean of this category
    assert [i for i in _real_tree_issues() if i.category_id == cat] == []
    # a fresh violation of this category would block CI under the shipped baseline
    _, result = _result(tmp_path, violation)
    exit_code, _ = mpy.evaluate_severity(result, mpy.load_baseline(str(REAL_BASELINE)))
    assert exit_code == 1


# ---------------------------------------------------------------------------
# Per-stage enforcement proof — mpy/02b: re features (9) + extended slices (19)
#
# Same shape as the mpy/02 proof, for the two categories this stage drives to
# zero and flips to `fail`: every flavor the re rework removed (counted reps,
# IGNORECASE / other flags, findall) is one detector under category 9, and
# `seq[::-1]` is category 19. The baseline enforces each, the tree is clean of
# it, and a fresh violation would block CI.
# ---------------------------------------------------------------------------

# (category_id, fresh_violation_snippet)
MPY02B_ENFORCED = [
    (9,  're.search(r"a{2,4}", s)\n'),                   # counted repetition
    (9,  "re.search(pat, s, re.IGNORECASE)\n"),          # unsupported flag
    (9,  "re.findall(pat, s)\n"),                        # absent function
    (19, "y = data[::-1]\n"),                            # extended slice
]


@pytest.mark.parametrize("cat,violation", MPY02B_ENFORCED,
                         ids=[f"cat{c}_{n}" for n, (c, _) in enumerate(MPY02B_ENFORCED)])
def test_mpy02b_re_and_slice_category_enforced(tmp_path, cat, violation):
    cid = str(cat)
    assert _real_severities()[cid]["severity"] == "fail"
    # the business-logic tree is clean of this category
    assert [i for i in _real_tree_issues() if i.category_id == cat] == []
    # a fresh violation of this category would block CI under the shipped baseline
    _, result = _result(tmp_path, violation)
    exit_code, _ = mpy.evaluate_severity(result, mpy.load_baseline(str(REAL_BASELINE)))
    assert exit_code == 1
