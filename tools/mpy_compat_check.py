#!/usr/bin/env python3
"""
MicroPython 1.27 compatibility checker for SeedSigner.

Scans Python source files for incompatibilities with MicroPython 1.27 using
two passes: mpy-cross compilation (syntax-level) and pattern scanning (16
categories from docs/micropython_compatibility.md plus third-party dep checks).

Usage:
  python tools/mpy_compat_check.py [OPTIONS] [FILES...]

Options:
  --json               Output JSON instead of Markdown
  --diff BASE_JSON     Compare against a base JSON report (diff mode)
  --output FILE        Write report to file (default: stdout)
  --no-mpy-cross       Skip mpy-cross compilation step
  --mpy-cross-path P   Path to mpy-cross binary (default: mpy-cross)
  --exclude DIR        Additional directory to exclude (repeatable)
  --verbose            Include per-file line-by-line detail in report
"""

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

MICROPYTHON_VERSION = "1.27"

DEFAULT_SCAN_ROOT = "src/seedsigner"

EXCLUDED_DIRS = [
    "src/seedsigner/gui",
    "src/seedsigner/hardware",
    # The seedsigner-translations submodule ships its own CI/tooling .py files
    # that are not part of the app and must not be scanned.
    "src/seedsigner/resources/seedsigner-translations",
]

EXCLUDED_FILES = [
    "src/seedsigner/helpers/qr.py",
    # CPython/Pi + builder-only module: a git-state explorer in local dev / the
    # SeedSigner OS builder and a version.json reader on the Pi image. It never runs on
    # MicroPython — the device's version is baked into the firmware at freeze time — and
    # is guarded off the device import path, so it is intentionally not held to the
    # MicroPython-compat contract.
    "src/seedsigner/helpers/version.py",
]

# Third-party dependency classification
KNOWN_COMPATIBLE = {"embit"}
BEING_REPLACED = {"PIL", "pyzbar", "qrcode", "Pillow"}

# Standard library modules available in MicroPython 1.27 (subset relevant to
# distinguishing stdlib from third-party). This is used by the third-party
# detector to avoid flagging stdlib imports — those are handled by categories
# 1-16 individually.
MICROPYTHON_STDLIB = {
    "array", "binascii", "builtins", "cmath", "collections", "errno",
    "gc", "hashlib", "heapq", "io", "json", "math", "os", "random",
    "re", "select", "socket", "struct", "sys", "time", "zlib",
    "_thread", "micropython", "machine", "network", "esp", "esp32",
    "neopixel", "btree", "framebuf", "uctypes", "uhashlib", "ubinascii",
    "uos", "usys", "utime", "ujson", "ure", "uio", "ucollections",
}

# CPython stdlib modules that we check in categories 1-16. We track these
# so category 17 doesn't double-flag them.
CHECKED_STDLIB = {
    "dataclasses", "typing", "threading", "enum", "pathlib",
    "gettext", "unicodedata", "importlib", "platform", "traceback",
    "base64", "hmac", "subprocess",
}

# Official micropython-lib packages that SeedSigner REQUIRES to be frozen into
# the firmware. These are NOT in MicroPython core, but are declared build
# dependencies (see docs/micropython_compatibility.md §3), so importing them is
# correct on both runtimes and is intentionally NOT flagged. `logging` is the
# first: a full logger is too core to hand-roll, so the firmware bundles the
# official micropython-lib `logging` and the app imports it directly.
REQUIRED_FROZEN_PACKAGES = {"logging"}

# Also skip these common stdlib modules that are fine or irrelevant
SKIP_STDLIB = {
    "abc", "argparse", "bisect", "codecs", "contextlib", "copy",
    "csv", "datetime", "decimal", "difflib", "email", "fnmatch",
    "fractions", "functools", "glob", "gzip", "html", "http",
    "inspect", "itertools", "json", "locale", "math", "multiprocessing",
    "numbers", "operator", "os", "pickle", "pprint", "queue",
    "random", "re", "secrets", "shutil", "signal", "socket",
    "sqlite3", "string", "struct", "sys", "tempfile", "textwrap",
    "time", "unittest", "urllib", "uuid", "warnings", "weakref",
    "xml", "zipfile", "io", "collections", "hashlib", "binascii",
    "array", "struct", "gc", "errno", "select", "heapq", "cmath",
    "zlib", "_thread",
}

# CPython stdlib modules that are ABSENT from MicroPython 1.27 *core* (some exist
# only as separately-installable micropython-lib packages, which do not count as
# core availability). Importing any of these raises ImportError on stock
# MicroPython. Flagged by category 20. Verified against
# https://docs.micropython.org/en/v1.27.0/library/index.html (omission) — see
# the compatibility guide for per-module citations.
ABSENT_STDLIB = {
    "datetime", "decimal", "functools", "itertools", "copy", "inspect",
    "warnings", "contextlib", "abc", "secrets", "uuid", "string", "queue",
}

# `collections` IS present but is a documented SUBSET: only deque, namedtuple,
# OrderedDict. These members are absent and must be flagged on `from collections
# import <member>`. https://docs.micropython.org/en/v1.27.0/library/collections.html
COLLECTIONS_ABSENT_MEMBERS = {"defaultdict", "Counter", "ChainMap", "UserDict",
                              "UserList", "UserString"}


# ---------------------------------------------------------------------------
# Category definitions
# ---------------------------------------------------------------------------

CATEGORIES = {
    1: {
        "name": "@dataclass",
        "description": "MicroPython has no `dataclasses` module.",
        "fix": "Convert to explicit `__init__` with manual attribute assignment.",
    },
    2: {
        "name": "Type annotations (typing import / executed generics)",
        "description": (
            "The `typing` module is not built-in (import fails). Bare "
            "annotations are PARSED-AND-IGNORED and need NO change; only "
            "PEP 585/604 generics in an EXECUTED position break — a class base "
            "(`class X(list[Y])`) or a type-alias/RHS (`A = list[int]`)."
        ),
        "fix": (
            "Remove `from typing import …`; replace a runtime generic base with a "
            "plain container (`class X(list)`). Leave ordinary signature/variable "
            "annotations untouched — MicroPython discards them."
        ),
    },
    4: {
        "name": "threading",
        "description": (
            "MicroPython has only `_thread`, not `threading`. "
            "No `Thread` class; locks lack context manager support."
        ),
        "fix": "Use a compat shim: `from seedsigner.compat.threading import Thread, Lock`.",
    },
    5: {
        "name": "enum.IntEnum",
        "description": "MicroPython has no `enum` module.",
        "fix": "Replace with plain class constants.",
    },
    6: {
        "name": "pathlib",
        "description": "MicroPython has no `pathlib` module.",
        "fix": "Replace `pathlib.Path` with `os.path` equivalents.",
    },
    7: {
        "name": "gettext",
        "description": "MicroPython has no `gettext` module.",
        "fix": "Use a compat shim: `from seedsigner.compat.l10n import gettext as _`.",
    },
    8: {
        "name": "unicodedata",
        "description": (
            "MicroPython has no `unicodedata` module. "
            "NFKD normalization is **critical for BIP-39 seed derivation**."
        ),
        "fix": (
            "embit does NOT normalize (mnemonic_to_seed expects a normalized "
            "string), and uhashlib has no normalization — so this is app-owned. "
            "Provide NFKD/NFC (pure-Python or a c-module) and validate against "
            "BIP-39 test vectors, including non-ASCII passphrases."
        ),
        "severity": "high",
    },
    9: {
        "name": "re counted repetitions",
        "description": (
            "MicroPython's `re` does not support `{n}`, `{m,n}` counted "
            "repetitions, named groups, or non-capturing groups."
        ),
        "fix": "Expand `{4}` to four repeated character classes; use `+` with len() for ranges.",
    },
    10: {
        "name": "importlib",
        "description": "MicroPython has no `importlib` module.",
        "fix": "Replace `importlib.import_module('x')` with `__import__('x')`.",
    },
    11: {
        "name": "os.fsync",
        "description": "MicroPython has no `os.fsync`.",
        "fix": "Guard with `if hasattr(os, 'fsync'): os.fsync(...)`.",
    },
    12: {
        "name": "platform",
        "description": "MicroPython has no `platform` module.",
        "fix": "Use `os.uname()` as fallback.",
    },
    13: {
        "name": "traceback",
        "description": "MicroPython's `traceback` is minimal; `format_exc()` may not work.",
        "fix": "Use `sys.print_exception()` with `io.StringIO` buffer.",
    },
    14: {
        "name": "base64",
        "description": "MicroPython has no `base64` module (no base32 functions).",
        "fix": "Implement pure-Python base32 or add to seedsigner-c-modules.",
    },
    15: {
        "name": "hmac",
        "description": "MicroPython has no `hmac` module.",
        "fix": (
            "embit's BIP32 already uses `hmac` on MicroPython, so the embit "
            "stack provides it — rely on the same module rather than "
            "reimplementing; verify it is present on the 1.27 build."
        ),
    },
    16: {
        "name": "subprocess",
        "description": "MicroPython has no `subprocess` module.",
        "fix": "Remove or replace with platform-appropriate alternative.",
    },
    17: {
        "name": "Third-party dependency",
        "description": (
            "Third-party package that may not be available or verified "
            "for MicroPython."
        ),
        "fix": "Verify package works on MicroPython 1.27 or find alternative.",
    },
    # --- Categories 18-22: surfaced by the v1.27.0 documentation review ---
    18: {
        "name": "os.path / os.walk / os.environ",
        "description": (
            "MicroPython's `os` has no `path` submodule, no `walk`, and no "
            "`environ`. NOTE: `os.path` is NOT a valid replacement for "
            "`pathlib` (category 6) — it is equally absent."
        ),
        "fix": (
            "Build paths with string ops + `os.getcwd()`/`__file__`; reimplement "
            "directory walks over `os.ilistdir()`; use `os.getenv`/`os.putenv`."
        ),
    },
    19: {
        "name": "Extended slices (step != 1)",
        "description": (
            "MicroPython does not implement subscripting with a step other than "
            "1 for str/bytes/list/tuple, so `seq[::-1]`, `seq[a:b:2]` fail."
        ),
        "fix": "Use `reversed(seq)` for reversal, or build the result explicitly.",
    },
    20: {
        "name": "Absent stdlib module",
        "description": (
            "Standard-library module absent from MicroPython 1.27 core (some "
            "exist only as separately-installable micropython-lib packages, "
            "which do not ship on the device). `collections` is present but "
            "lacks `defaultdict`/`Counter`."
        ),
        "fix": "Avoid the module, or vendor a pure-Python implementation into the app.",
    },
    21: {
        "name": "Builtin/method gap",
        "description": (
            "A builtin method missing or behaving differently on MicroPython: "
            "`int.bit_length()`, `int.to_bytes(..., signed=)`, `hash.hexdigest()`, "
            "`str.ljust/rjust`, `str.removeprefix/removesuffix`."
        ),
        "fix": (
            "bit_length: precompute; to_bytes: avoid `signed=`/keyword byteorder; "
            "hexdigest: `binascii.hexlify(h.digest())`; ljust/rjust: `%`-format; "
            "removeprefix/suffix: slice with a length check."
        ),
    },
    22: {
        "name": "Multiple-inheritance MRO (review)",
        "description": (
            "MicroPython's MRO is not C3-compliant and `super()` may call only "
            "ONE base in a diamond — cooperative `super().__init__()` chains can "
            "silently skip a base. This is a HEURISTIC flag (class with >=2 "
            "bases), not a definite break; validate on real MicroPython."
        ),
        "fix": (
            "Validate the class on stock MicroPython against known vectors; if it "
            "misbehaves, linearize init manually instead of relying on cooperative super()."
        ),
    },
}


# ---------------------------------------------------------------------------
# Published-doc citations + metadata, merged into every category so the report
# is self-documenting (the checker IS the migration guide). doc_url/doc_quote
# are verbatim from the MicroPython v1.27.0 docs. `static` = detectable by this
# checker (False => requires runtime validation, reported as an advisory). Full
# prose + before/after fixes live in the MicroPython Compatibility Guide.
# ---------------------------------------------------------------------------

DOC_BASE = "https://docs.micropython.org/en/v1.27.0/"
GUIDE_REF = "docs/micropython_compatibility.md"

# cid: (doc_path, verbatim_quote, statically_detectable)
_CITATIONS = {
    1:  ("library/index.html", "library/index.html does not list a `dataclasses` module (micropython-lib only).", True),
    2:  ("differences/python_35.html", "The MicroPython parser correctly ignores all type hints. However, the typing module is not built-in.", True),
    4:  ("library/index.html", "library/index.html lists `_thread` but no high-level `threading` module.", True),
    5:  ("library/index.html", "library/index.html does not list an `enum` module.", True),
    6:  ("library/index.html", "library/index.html does not list a `pathlib` module.", True),
    7:  ("library/index.html", "library/index.html does not list a `gettext` module.", True),
    8:  ("library/index.html", "library/index.html does not list a `unicodedata` module.", True),
    9:  ("library/re.html", "Counted repetitions ({m,n}), named groups ((?P<name>...)), non-capturing groups ((?:...)), more advanced assertions (\\b, \\B) ... are not supported. Only the re.DEBUG flag is documented.", True),
    10: ("library/index.html", "library/index.html does not list an `importlib` module; use the `__import__` builtin.", True),
    11: ("library/os.html", "os documents os.sync() (sync all filesystems); there is no per-fd os.fsync().", True),
    12: ("library/index.html", "No `platform` module; use sys.platform / sys.implementation / os.uname().", True),
    13: ("library/sys.html", "sys.print_exception(exc, file) is provided; there is no `traceback` module and no sys.exc_info().", True),
    14: ("library/binascii.html", "binascii provides a2b_base64/b2a_base64; there is no `base64` module and no base32 functions.", True),
    15: ("library/index.html", "library/index.html does not list an `hmac` module (micropython-lib only).", True),
    16: ("library/index.html", "library/index.html does not list a `subprocess` module.", True),
    17: ("library/index.html", "Third-party package not in the known-compatible set; verify on MicroPython 1.27.", True),
    18: ("library/os.html", "os documents listdir/ilistdir/uname/urandom/sync/stat/getcwd but no `path` submodule and no `walk`; genrst/modules.html: the environ attribute is not implemented, use getenv, putenv, and unsetenv instead.", True),
    19: ("genrst/builtin_types.html", "Subscript with step != 1 is not yet implemented (str); Bytes subscription with step != 1 not implemented.", True),
    20: ("library/index.html", "Module is absent from the MicroPython library index; collections.html documents only deque/namedtuple/OrderedDict.", True),
    21: ("genrst/builtin_types.html", "bit_length method is not implemented; to_bytes does not implement the signed parameter; hash.hexdigest is NOT implemented (use binascii.hexlify); str.ljust()/rjust() not implemented.", True),
    22: ("genrst/core_language.html", "Method Resolution Order (MRO) is not compliant with CPython. When inheriting from multiple classes super() only calls one class.", False),
}
for _cid, (_path, _quote, _static) in _CITATIONS.items():
    CATEGORIES[_cid]["doc_url"] = DOC_BASE + _path
    CATEGORIES[_cid]["doc_quote"] = _quote
    CATEGORIES[_cid]["static"] = _static
    CATEGORIES[_cid]["migration_ref"] = f"{GUIDE_REF} §{_cid}"


# ---------------------------------------------------------------------------
# Issue dataclass (plain class for MicroPython irony-avoidance)
# ---------------------------------------------------------------------------

class Issue:
    __slots__ = (
        "file_path", "line_number", "category_id", "category_name",
        "severity", "message", "source_line",
    )

    def __init__(self, file_path, line_number, category_id, message,
                 source_line="", severity="pattern_match"):
        self.file_path = file_path
        self.line_number = line_number
        self.category_id = category_id
        self.category_name = CATEGORIES[category_id]["name"]
        self.severity = severity
        self.message = message
        self.source_line = source_line.rstrip()

    def to_dict(self):
        return {
            "file": self.file_path,
            "line": self.line_number,
            "category_id": self.category_id,
            "category_name": self.category_name,
            "severity": self.severity,
            "message": self.message,
            "source_line": self.source_line,
        }


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

def discover_files(scan_root, extra_excludes=None, explicit_files=None):
    """Find all .py files in scan_root, respecting exclusions."""
    if explicit_files:
        files = []
        for f in explicit_files:
            if os.path.isfile(f) and f.endswith(".py"):
                files.append(f)
            elif os.path.isdir(f):
                files.extend(_walk_dir(f, extra_excludes or []))
        return sorted(files)

    return sorted(_walk_dir(scan_root, extra_excludes or []))


def _walk_dir(root, extra_excludes):
    """Walk directory tree, respecting exclusions."""
    all_excludes = EXCLUDED_DIRS + extra_excludes
    files = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Normalize for comparison
        norm_dirpath = os.path.normpath(dirpath)

        # Skip excluded directories
        skip = False
        for exc in all_excludes:
            if norm_dirpath == os.path.normpath(exc) or \
               norm_dirpath.startswith(os.path.normpath(exc) + os.sep):
                skip = True
                break
        if skip:
            dirnames.clear()
            continue

        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(dirpath, fname)
            norm_fpath = os.path.normpath(fpath)

            # Skip excluded files
            if any(norm_fpath == os.path.normpath(ef) for ef in EXCLUDED_FILES):
                continue

            files.append(fpath)

    return files


# ---------------------------------------------------------------------------
# Pass 1: mpy-cross compilation
# ---------------------------------------------------------------------------

def run_mpy_cross(file_path, mpy_cross_path="mpy-cross"):
    """Compile a file with mpy-cross and return any syntax errors as Issues."""
    issues = []
    try:
        result = subprocess.run(
            [mpy_cross_path, file_path],
            capture_output=True, text=True, timeout=30,
        )
    except FileNotFoundError:
        # mpy-cross not installed — caller should handle --no-mpy-cross
        return None
    except subprocess.TimeoutExpired:
        issues.append(Issue(
            file_path=file_path,
            line_number=0,
            category_id=2,
            message="mpy-cross timed out (30s)",
            severity="syntax_error",
        ))
        return issues

    if result.returncode != 0:
        for line in result.stderr.strip().splitlines():
            # Format: "file.py:22: error: invalid syntax" or similar
            match = re.match(r".*?:(\d+):\s*(.*)", line)
            if match:
                lineno = int(match.group(1))
                msg = match.group(2).strip()
                # Read the source line for context
                src_line = _get_source_line(file_path, lineno)
                issues.append(Issue(
                    file_path=file_path,
                    line_number=lineno,
                    category_id=2,  # Usually type annotation syntax
                    message=f"mpy-cross: {msg}",
                    source_line=src_line,
                    severity="syntax_error",
                ))
            elif line.strip():
                issues.append(Issue(
                    file_path=file_path,
                    line_number=0,
                    category_id=2,
                    message=f"mpy-cross: {line.strip()}",
                    severity="syntax_error",
                ))

    # Clean up .mpy output file if created
    mpy_file = file_path.replace(".py", ".mpy")
    if os.path.exists(mpy_file):
        os.remove(mpy_file)

    return issues


def _get_source_line(file_path, lineno):
    """Read a specific line from a file."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                if i == lineno:
                    return line.rstrip()
    except (OSError, UnicodeDecodeError):
        pass
    return ""


# ---------------------------------------------------------------------------
# Pass 2: Pattern scanner — import-based categories
# ---------------------------------------------------------------------------

# Regex patterns for categories 1, 3-8, 10, 12-16
# Each tuple: (compiled_regex, category_id, message_template)
IMPORT_PATTERNS = [
    # Category 1: dataclasses
    (re.compile(r"^\s*from\s+dataclasses\s+import"), 1,
     "`dataclasses` import"),
    (re.compile(r"^\s*@dataclass"), 1,
     "`@dataclass` decorator"),

    # Category 2: typing imports
    (re.compile(r"^\s*from\s+typing\s+import"), 2,
     "`typing` module import"),
    (re.compile(r"^\s*import\s+typing\b"), 2,
     "`typing` module import"),

    # Category 4: threading
    (re.compile(r"^\s*import\s+threading\b"), 4,
     "`threading` module import"),
    (re.compile(r"^\s*from\s+threading\s+import"), 4,
     "`threading` module import"),

    # Category 5: enum
    (re.compile(r"^\s*from\s+enum\s+import"), 5,
     "`enum` module import"),

    # Category 6: pathlib
    (re.compile(r"^\s*import\s+pathlib\b"), 6,
     "`pathlib` module import"),
    (re.compile(r"^\s*from\s+pathlib\s+import"), 6,
     "`pathlib` module import"),

    # Category 7: gettext
    (re.compile(r"^\s*import\s+gettext\b"), 7,
     "`gettext` module import"),
    (re.compile(r"^\s*from\s+gettext\s+import"), 7,
     "`gettext` module import"),

    # Category 8: unicodedata
    (re.compile(r"^\s*import\s+unicodedata\b"), 8,
     "`unicodedata` module import"),

    # Category 10: importlib
    (re.compile(r"^\s*from\s+importlib\s+import"), 10,
     "`importlib` module import"),

    # Category 11: os.fsync
    (re.compile(r"os\.fsync\s*\("), 11,
     "`os.fsync()` call"),

    # Category 12: platform
    (re.compile(r"^\s*import\s+platform\b"), 12,
     "`platform` module import"),

    # Category 13: traceback
    (re.compile(r"^\s*import\s+traceback\b"), 13,
     "`traceback` module import"),

    # Category 14: base64
    (re.compile(r"^\s*import\s+base64\b"), 14,
     "`base64` module import"),
    (re.compile(r"^\s*from\s+base64\s+import"), 14,
     "`base64` module import"),

    # Category 15: hmac
    (re.compile(r"^\s*import\s+hmac\b"), 15,
     "`hmac` module import"),

    # Category 16: subprocess
    (re.compile(r"^\s*import\s+subprocess\b"), 16,
     "`subprocess` module import"),
    (re.compile(r"^\s*from\s+subprocess\s+import"), 16,
     "`subprocess` module import"),

    # Category 18: os.path / os.walk / os.environ (absent; NOT a pathlib fix)
    (re.compile(r"\bos\.path\."), 18, "`os.path` is absent on MicroPython"),
    (re.compile(r"\bos\.walk\s*\("), 18, "`os.walk()` is absent on MicroPython"),
    (re.compile(r"\bos\.environ\b"), 18,
     "`os.environ` is absent (use os.getenv/os.putenv)"),

    # Category 21: builtin/method gaps (heuristic — see guide §21)
    (re.compile(r"\.bit_length\s*\("), 21, "`int.bit_length()` not implemented"),
    (re.compile(r"\.hexdigest\s*\("), 21,
     "`hash.hexdigest()` not implemented (use binascii.hexlify(h.digest()))"),
    (re.compile(r"\.(ljust|rjust)\s*\("), 21, "`str.ljust()/rjust()` not implemented"),
    (re.compile(r"\.(removeprefix|removesuffix)\s*\("), 21,
     "`str.removeprefix/removesuffix` not implemented (PEP 616)"),
    (re.compile(r"\.to_bytes\s*\([^)]*\bsigned\s*="), 21,
     "`int.to_bytes(signed=...)` not supported"),
]


def scan_import_patterns(file_path, lines):
    """Scan file lines for import-based incompatibilities."""
    issues = []
    for lineno, line in enumerate(lines, 1):
        for pattern, cat_id, msg in IMPORT_PATTERNS:
            if pattern.search(line):
                issues.append(Issue(
                    file_path=file_path,
                    line_number=lineno,
                    category_id=cat_id,
                    message=msg,
                    source_line=line,
                ))
    return issues


# ---------------------------------------------------------------------------
# Pass 2: Category 9 — re counted repetitions
# ---------------------------------------------------------------------------

# Match re.search/match/compile/findall calls and capture the first string arg
RE_CALL_PATTERN = re.compile(
    r"""re\.(search|match|compile|findall)\(\s*(r?(?P<q>['\"]))(.*?)(?P=q)""",
    re.DOTALL,
)

# Match counted repetitions in regex patterns: {n}, {m,n}, {n,}
COUNTED_REP_PATTERN = re.compile(r"\{(\d+)(?:,(\d*))?\}")


def scan_re_counted_repetitions(file_path, content, lines):
    """Detect {n} and {m,n} counted repetitions in regex pattern strings."""
    issues = []

    for match in RE_CALL_PATTERN.finditer(content):
        pattern_str = match.group(4)  # The regex pattern content
        pattern_start = match.start()

        # Find line number of this match
        lineno = content[:pattern_start].count("\n") + 1

        # Check for counted repetitions in the pattern
        for rep_match in COUNTED_REP_PATTERN.finditer(pattern_str):
            rep_text = rep_match.group(0)

            # Skip if inside a character class [...] — simplified check
            before = pattern_str[:rep_match.start()]
            open_brackets = before.count("[") - before.count("\\[")
            close_brackets = before.count("]") - before.count("\\]")
            if open_brackets > close_brackets:
                continue

            source_line = lines[lineno - 1] if lineno <= len(lines) else ""
            issues.append(Issue(
                file_path=file_path,
                line_number=lineno,
                category_id=9,
                message=f"Counted repetition `{rep_text}` in regex pattern",
                source_line=source_line,
            ))

    return issues


# ---------------------------------------------------------------------------
# Pass 2: Category 2b/2c — AST-based type annotation checks
# ---------------------------------------------------------------------------

class AnnotationVisitor(ast.NodeVisitor):
    """Detect PEP 585/604 generics/unions in *executed* positions only.

    Per the v1.27.0 docs, bare annotations are parsed-and-discarded
    (differences/python_35.html PEP 484 footnote: "The MicroPython parser
    correctly ignores all type hints"; differences/python_36.html PEP 526 =
    Complete), so `def f(x: int) -> str:` and `x: list[int] = []` are NOT breaks
    and must NOT be flagged (that was a false positive). Only generics/unions
    that actually EXECUTE break on MicroPython 1.27:
      - a class base:       `class C(list[int]): ...`   (executes list[int])
      - a type-alias / RHS: `Alias = list[int]`         (executes the subscript)
    The `typing` import itself is caught separately by IMPORT_PATTERNS. We do NOT
    recurse into annotation slots, so signature/variable annotations are ignored.
    """

    _GENERIC_NAMES = ("list", "dict", "tuple", "set", "frozenset", "type")

    def __init__(self, file_path, lines):
        self.file_path = file_path
        self.lines = lines
        self.issues = []

    def _flag_generic(self, node, where):
        """Flag a `builtin[...]` subscript that executes at runtime."""
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) \
                and node.value.id in self._GENERIC_NAMES:
            src = self.lines[node.lineno - 1] if node.lineno <= len(self.lines) else ""
            self.issues.append(Issue(
                file_path=self.file_path,
                line_number=node.lineno,
                category_id=2,
                message=f"PEP 585 generic `{node.value.id}[...]` in {where} (executes at runtime)",
                source_line=src,
            ))

    def visit_ClassDef(self, node):
        for base in node.bases:
            self._flag_generic(base, "class base")
        self.generic_visit(node)

    def visit_Assign(self, node):
        # `Alias = list[int]` — type alias / RHS executes the subscript.
        self._flag_generic(node.value, "assignment value")
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        # Only the assigned VALUE executes; the annotation itself is discarded.
        if node.value is not None:
            self._flag_generic(node.value, "assignment value")
        self.generic_visit(node)


def scan_ast_annotations(file_path, content, lines):
    """Use AST to find PEP 604/585 generics in executed positions."""
    try:
        tree = ast.parse(content, filename=file_path)
    except SyntaxError:
        return []  # mpy-cross will catch syntax errors
    visitor = AnnotationVisitor(file_path, lines)
    visitor.visit(tree)
    return visitor.issues


# ---------------------------------------------------------------------------
# Pass 2: Category 9 (extended) — re flags / functions / pattern features
# https://docs.micropython.org/en/v1.27.0/library/re.html
# ---------------------------------------------------------------------------

# Only re.DEBUG is supported by MicroPython's re.
RE_FLAG_PATTERN = re.compile(
    r"\bre\.(IGNORECASE|MULTILINE|DOTALL|VERBOSE|ASCII|LOCALE|UNICODE)\b"
)
# findall/finditer/fullmatch/subn are not provided.
RE_FUNC_PATTERN = re.compile(r"\bre\.(findall|finditer|fullmatch|subn)\s*\(")
# Unsupported constructs *inside* a regex pattern string.
RE_PATTERN_FEATURES = [
    (re.compile(r"\(\?P<"), "named group `(?P<...>)`"),
    (re.compile(r"\(\?P="), "named backreference `(?P=...)`"),
    (re.compile(r"\(\?:"), "non-capturing group `(?:...)`"),
    (re.compile(r"\(\?<[=!]"), "lookbehind `(?<=...)`/`(?<!...)`"),
    (re.compile(r"\(\?[=!]"), "lookahead `(?=...)`/`(?!...)`"),
    (re.compile(r"\\[bB]"), "word-boundary assertion `\\b`/`\\B`"),
]


def scan_re_features(file_path, content, lines):
    """Detect unsupported re flags, functions, and in-pattern constructs."""
    issues = []
    for lineno, line in enumerate(lines, 1):
        for m in RE_FLAG_PATTERN.finditer(line):
            issues.append(Issue(
                file_path, lineno, 9,
                f"Unsupported re flag `re.{m.group(1)}` (only re.DEBUG exists)",
                source_line=line))
        m2 = RE_FUNC_PATTERN.search(line)
        if m2:
            issues.append(Issue(
                file_path, lineno, 9,
                f"Unsupported re function `re.{m2.group(1)}()`",
                source_line=line))
    for match in RE_CALL_PATTERN.finditer(content):
        pattern_str = match.group(4)
        lineno = content[:match.start()].count("\n") + 1
        src = lines[lineno - 1] if lineno <= len(lines) else ""
        for rx, label in RE_PATTERN_FEATURES:
            if rx.search(pattern_str):
                issues.append(Issue(
                    file_path, lineno, 9,
                    f"Unsupported regex feature: {label}", source_line=src))
    return issues


# ---------------------------------------------------------------------------
# Pass 2: Category 19 — extended slices (step != 1)
# ---------------------------------------------------------------------------

class _SliceVisitor(ast.NodeVisitor):
    def __init__(self, file_path, lines):
        self.file_path = file_path
        self.lines = lines
        self.issues = []

    def visit_Subscript(self, node):
        sl = node.slice
        if isinstance(sl, ast.Slice) and sl.step is not None:
            is_one = isinstance(sl.step, ast.Constant) and sl.step.value == 1
            if not is_one:
                src = self.lines[node.lineno - 1] if node.lineno <= len(self.lines) else ""
                self.issues.append(Issue(
                    self.file_path, node.lineno, 19,
                    "Extended slice (step != 1) — not implemented on MicroPython",
                    source_line=src))
        self.generic_visit(node)


def scan_extended_slices(file_path, content, lines):
    try:
        tree = ast.parse(content, filename=file_path)
    except SyntaxError:
        return []
    v = _SliceVisitor(file_path, lines)
    v.visit(tree)
    return v.issues


# ---------------------------------------------------------------------------
# Pass 2: Category 22 — multiple-inheritance MRO review (heuristic)
# ---------------------------------------------------------------------------

def _base_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _base_name(node.value) + "[...]"
    return "?"


class _MROVisitor(ast.NodeVisitor):
    def __init__(self, file_path, lines):
        self.file_path = file_path
        self.lines = lines
        self.issues = []

    def visit_ClassDef(self, node):
        bases = [b for b in node.bases
                 if not (isinstance(b, ast.Name) and b.id == "object")]
        if len(bases) >= 2:
            src = self.lines[node.lineno - 1] if node.lineno <= len(self.lines) else ""
            names = ", ".join(_base_name(b) for b in bases)
            self.issues.append(Issue(
                self.file_path, node.lineno, 22,
                f"Multiple inheritance ({names}) — verify MRO/super() on MicroPython",
                source_line=src))
        self.generic_visit(node)


def scan_mro_review(file_path, content, lines):
    try:
        tree = ast.parse(content, filename=file_path)
    except SyntaxError:
        return []
    v = _MROVisitor(file_path, lines)
    v.visit(tree)
    return v.issues


# ---------------------------------------------------------------------------
# Pass 2: Category 20 — absent stdlib modules (and partial collections members)
# ---------------------------------------------------------------------------

def scan_absent_stdlib(file_path, lines):
    issues = []
    for lineno, line in enumerate(lines, 1):
        m = IMPORT_RE.match(line)
        if not m:
            continue
        module_path = m.group(1) or m.group(2)
        if not module_path or module_path.startswith("."):
            continue
        top = module_path.split(".")[0]
        if top in ABSENT_STDLIB:
            issues.append(Issue(
                file_path, lineno, 20,
                f"`{top}` is absent from MicroPython 1.27 core", source_line=line))
        elif top == "collections" and m.group(1):  # `from collections import X`
            imported = line.split("import", 1)[1] if "import" in line else ""
            for member in COLLECTIONS_ABSENT_MEMBERS:
                if re.search(r"\b" + member + r"\b", imported):
                    issues.append(Issue(
                        file_path, lineno, 20,
                        f"`collections.{member}` is absent (collections is a subset)",
                        source_line=line))
    return issues


# ---------------------------------------------------------------------------
# Pass 2: Category 17 — Third-party dependency detection
# ---------------------------------------------------------------------------

# Match import statements and extract the top-level module
IMPORT_RE = re.compile(
    r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))"
)


def scan_third_party_imports(file_path, lines):
    """Flag third-party imports that may not work on MicroPython."""
    issues = []
    seen_modules = set()

    for lineno, line in enumerate(lines, 1):
        m = IMPORT_RE.match(line)
        if not m:
            continue

        module_path = m.group(1) or m.group(2)

        # Skip relative imports (start with .)
        if module_path.startswith("."):
            continue

        top_module = module_path.split(".")[0]

        # Skip empty (shouldn't happen after relative import check)
        if not top_module:
            continue

        # Skip seedsigner's own imports
        if top_module == "seedsigner":
            continue

        # Skip modules handled by categories 1-16
        if top_module in CHECKED_STDLIB:
            continue

        # Skip required-frozen micropython-lib packages (declared dependencies)
        if top_module in REQUIRED_FROZEN_PACKAGES:
            continue

        # Skip known stdlib modules
        if top_module in SKIP_STDLIB or top_module in MICROPYTHON_STDLIB:
            continue

        # Skip private/dunder modules
        if top_module.startswith("_"):
            continue

        # Skip known compatible
        if top_module in KNOWN_COMPATIBLE:
            continue

        # Avoid duplicate flags for the same module in the same file
        if top_module in seen_modules:
            continue
        seen_modules.add(top_module)

        if top_module in BEING_REPLACED:
            issues.append(Issue(
                file_path=file_path,
                line_number=lineno,
                category_id=17,
                message=f"`{top_module}` — being replaced during migration",
                source_line=line,
                severity="info",
            ))
        else:
            issues.append(Issue(
                file_path=file_path,
                line_number=lineno,
                category_id=17,
                message=f"`{top_module}` — not yet verified for MicroPython",
                source_line=line,
                severity="warning",
            ))

    return issues


# ---------------------------------------------------------------------------
# Main scanning logic
# ---------------------------------------------------------------------------

def scan_file(file_path, use_mpy_cross=True, mpy_cross_path="mpy-cross"):
    """Run all checks on a single file. Returns list of Issues."""
    issues = []

    # Read file content
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
    except (OSError, UnicodeDecodeError) as e:
        issues.append(Issue(
            file_path=file_path,
            line_number=0,
            category_id=2,
            message=f"Could not read file: {e}",
            severity="error",
        ))
        return issues

    lines = content.splitlines()

    # Pass 1: mpy-cross
    if use_mpy_cross:
        mpy_issues = run_mpy_cross(file_path, mpy_cross_path)
        if mpy_issues is None:
            # mpy-cross not available — skip silently
            pass
        else:
            issues.extend(mpy_issues)

    # Pass 2: pattern scanning
    issues.extend(scan_import_patterns(file_path, lines))
    issues.extend(scan_re_counted_repetitions(file_path, content, lines))
    issues.extend(scan_re_features(file_path, content, lines))
    issues.extend(scan_ast_annotations(file_path, content, lines))
    issues.extend(scan_extended_slices(file_path, content, lines))
    issues.extend(scan_absent_stdlib(file_path, lines))
    issues.extend(scan_mro_review(file_path, content, lines))
    issues.extend(scan_third_party_imports(file_path, lines))

    # Deduplicate: if mpy-cross flagged a line and pattern scanning also
    # flagged it, keep only the mpy-cross issue (more authoritative for syntax)
    return _deduplicate_issues(issues)


def _deduplicate_issues(issues):
    """Remove pattern_match issues if same file:line has a syntax_error."""
    syntax_lines = set()
    for iss in issues:
        if iss.severity == "syntax_error":
            syntax_lines.add((iss.file_path, iss.line_number))

    deduped = []
    for iss in issues:
        if iss.severity == "pattern_match" and \
           (iss.file_path, iss.line_number) in syntax_lines:
            continue
        deduped.append(iss)
    return deduped


def count_lines(file_path):
    """Count lines in a file."""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return sum(1 for _ in f)
    except (OSError, UnicodeDecodeError):
        return 0


# ---------------------------------------------------------------------------
# Report generation — JSON
# ---------------------------------------------------------------------------

def build_result(files, all_issues):
    """Build structured result dict for JSON output or diff comparison."""
    total_lines = sum(count_lines(f) for f in files)
    files_with_issues = len({iss.file_path for iss in all_issues})
    files_clean = len(files) - files_with_issues

    syntax_errors = sum(1 for i in all_issues if i.severity == "syntax_error")
    pattern_matches = sum(1 for i in all_issues if i.severity == "pattern_match")
    info_issues = sum(1 for i in all_issues if i.severity == "info")
    warning_issues = sum(1 for i in all_issues if i.severity == "warning")

    compat_score = (files_clean / len(files) * 100) if files else 0
    density = (len(all_issues) / total_lines * 1000) if total_lines else 0

    # Per-category counts
    cat_counts = {}
    cat_files = {}
    for iss in all_issues:
        cid = iss.category_id
        cat_counts[cid] = cat_counts.get(cid, 0) + 1
        if cid not in cat_files:
            cat_files[cid] = set()
        cat_files[cid].add(iss.file_path)

    categories = {}
    for cid in sorted(cat_counts.keys()):
        categories[str(cid)] = {
            "name": CATEGORIES[cid]["name"],
            "count": cat_counts[cid],
            "files": sorted(cat_files[cid]),
        }

    return {
        "metadata": {
            "micropython_version": MICROPYTHON_VERSION,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "scan_root": DEFAULT_SCAN_ROOT,
            "exclusions": EXCLUDED_DIRS + EXCLUDED_FILES,
            "total_files": len(files),
            "total_lines": total_lines,
        },
        "summary": {
            "files_with_issues": files_with_issues,
            "files_clean": files_clean,
            "total_issues": len(all_issues),
            "syntax_errors": syntax_errors,
            "pattern_matches": pattern_matches,
            "info_issues": info_issues,
            "warning_issues": warning_issues,
            "compatibility_score_pct": round(compat_score, 1),
            "issues_per_1000_lines": round(density, 1),
        },
        "categories": categories,
        "issues": [iss.to_dict() for iss in all_issues],
    }


# ---------------------------------------------------------------------------
# Report generation — Markdown (absolute mode)
# ---------------------------------------------------------------------------

def generate_markdown_report(result, verbose=False):
    """Generate Markdown report from structured result."""
    md = []
    meta = result["metadata"]
    summ = result["summary"]

    md.append(f"# MicroPython {meta['micropython_version']} Compatibility Report\n")
    md.append(
        f"**Target:** MicroPython {meta['micropython_version']}  |  "
        f"**Scan root:** `{meta['scan_root']}/`"
    )
    excl = ", ".join(
        f"`{e.replace(meta['scan_root'] + '/', '')}`"
        for e in meta["exclusions"]
    )
    md.append(f"**Exclusions:** {excl}\n")

    # Summary table
    md.append("## Summary\n")
    md.append("| Metric | Value |")
    md.append("|--------|-------|")
    md.append(f"| Files scanned | {meta['total_files']} |")
    md.append(f"| Files with issues | {summ['files_with_issues']} |")
    md.append(f"| Files clean | {summ['files_clean']} |")
    md.append(f"| Total issues | {summ['total_issues']} |")
    md.append(f"| Syntax errors (mpy-cross) | {summ['syntax_errors']} |")
    md.append(f"| Pattern matches | {summ['pattern_matches']} |")
    if summ["info_issues"]:
        md.append(f"| Info (being replaced) | {summ['info_issues']} |")
    if summ["warning_issues"]:
        md.append(f"| Warnings (unverified deps) | {summ['warning_issues']} |")
    md.append(
        f"| **Compatibility score** | "
        f"**{summ['compatibility_score_pct']}% clean files** |"
    )
    md.append(f"| Issue density | {summ['issues_per_1000_lines']} per 1000 lines |")
    md.append("")

    # Category breakdown
    md.append("## Issues by Category\n")
    md.append("| # | Category | Count | Files | Description |")
    md.append("|---|----------|-------|-------|-------------|")
    for cid_str, cat_data in sorted(
        result["categories"].items(), key=lambda x: int(x[0])
    ):
        cid = int(cid_str)
        cat_meta = CATEGORIES[cid]
        short_files = ", ".join(
            os.path.basename(f) for f in cat_data["files"][:4]
        )
        if len(cat_data["files"]) > 4:
            short_files += f" (+{len(cat_data['files']) - 4} more)"
        # First sentence, with backticks escaped for Markdown table
        desc = cat_meta["description"].split(".")[0].replace("`", "")
        md.append(
            f"| {cid} | {cat_meta['name']} | {cat_data['count']} "
            f"| {short_files} | {desc} |"
        )
    md.append("")

    # Category fix reference — self-documenting (embeds the MicroPython citation)
    md.append("## Fix Reference\n")
    for cid_str in sorted(result["categories"].keys(), key=int):
        cid = int(cid_str)
        cat = CATEGORIES[cid]
        md.append(f"**{cid}. {cat['name']}** — {cat['description']}")
        if cat.get("doc_quote"):
            md.append(f"  **MicroPython 1.27 docs:** “{cat['doc_quote']}”")
            md.append(f"  <{cat.get('doc_url', '')}>")
        if not cat.get("static", True):
            md.append("  **Detection:** heuristic/advisory — confirm at runtime (not a guaranteed break).")
        md.append(f"  **Fix:** {cat['fix']}")
        md.append(f"  **See:** {cat['migration_ref']}\n")

    # Per-file details (verbose only)
    if verbose:
        md.append("## Per-File Details\n")
        issues_by_file = {}
        for iss in result["issues"]:
            fpath = iss["file"]
            if fpath not in issues_by_file:
                issues_by_file[fpath] = []
            issues_by_file[fpath].append(iss)

        for fpath in sorted(issues_by_file.keys()):
            file_issues = issues_by_file[fpath]
            md.append(
                f"<details>\n"
                f"<summary><code>{fpath}</code> — "
                f"{len(file_issues)} issue{'s' if len(file_issues) != 1 else ''}"
                f"</summary>\n"
            )
            md.append("| Line | Category | Code |")
            md.append("|------|----------|------|")
            for iss in sorted(file_issues, key=lambda i: i["line"]):
                code = iss["source_line"].strip()
                # Escape pipe characters in code for Markdown tables
                code = code.replace("|", "\\|")
                # Truncate long lines
                if len(code) > 80:
                    code = code[:77] + "..."
                md.append(
                    f"| {iss['line']} | {iss['category_name']} | `{code}` |"
                )
            md.append("\n</details>\n")

    return "\n".join(md)


# ---------------------------------------------------------------------------
# Report generation — Markdown (diff mode)
# ---------------------------------------------------------------------------

def generate_diff_report(base_result, head_result):
    """Generate diff Markdown report comparing base and head results."""
    md = []
    base_summ = base_result["summary"]
    head_summ = head_result["summary"]
    meta = head_result["metadata"]

    md.append(f"# MicroPython {meta['micropython_version']} Compatibility: PR Diff\n")

    # Delta summary
    md.append("## Delta\n")
    md.append("| Metric | Base | Head | Delta |")
    md.append("|--------|------|------|-------|")

    for key, label in [
        ("total_issues", "Total issues"),
        ("files_with_issues", "Files with issues"),
        ("syntax_errors", "Syntax errors"),
        ("pattern_matches", "Pattern matches"),
    ]:
        base_val = base_summ.get(key, 0)
        head_val = head_summ.get(key, 0)
        delta = head_val - base_val
        delta_str = f"+{delta}" if delta > 0 else str(delta)
        if delta < 0:
            delta_str = f"**{delta_str}** :white_check_mark:"
        elif delta > 0:
            delta_str = f"**{delta_str}** :x:"
        else:
            delta_str = "0"
        md.append(f"| {label} | {base_val} | {head_val} | {delta_str} |")

    # Compatibility score
    base_score = base_summ.get("compatibility_score_pct", 0)
    head_score = head_summ.get("compatibility_score_pct", 0)
    score_delta = round(head_score - base_score, 1)
    score_delta_str = f"+{score_delta}%" if score_delta > 0 else f"{score_delta}%"
    md.append(
        f"| **Compatibility score** | {base_score}% | {head_score}% "
        f"| **{score_delta_str}** |"
    )
    md.append("")

    # Per-category changes
    md.append("## Changes by Category\n")
    all_cats = set(base_result["categories"].keys()) | set(head_result["categories"].keys())
    if all_cats:
        md.append("| Category | Base | Head | Delta |")
        md.append("|----------|------|------|-------|")
        for cid_str in sorted(all_cats, key=int):
            cid = int(cid_str)
            cat_name = CATEGORIES.get(cid, {}).get("name", f"Category {cid}")
            base_count = base_result["categories"].get(cid_str, {}).get("count", 0)
            head_count = head_result["categories"].get(cid_str, {}).get("count", 0)
            delta = head_count - base_count
            if delta < 0:
                delta_str = f"**{delta}** :white_check_mark:"
            elif delta > 0:
                delta_str = f"**+{delta}** :x:"
            else:
                delta_str = "0"
            md.append(f"| {cat_name} | {base_count} | {head_count} | {delta_str} |")
        md.append("")

    # Find new issues and fixed issues
    base_issue_keys = {
        (i["file"], i["line"], i["category_id"]) for i in base_result["issues"]
    }
    head_issue_keys = {
        (i["file"], i["line"], i["category_id"]) for i in head_result["issues"]
    }

    new_keys = head_issue_keys - base_issue_keys
    fixed_keys = base_issue_keys - head_issue_keys

    # Build lookup for full issue data
    head_issue_map = {}
    for iss in head_result["issues"]:
        key = (iss["file"], iss["line"], iss["category_id"])
        head_issue_map[key] = iss

    base_issue_map = {}
    for iss in base_result["issues"]:
        key = (iss["file"], iss["line"], iss["category_id"])
        base_issue_map[key] = iss

    # New issues introduced — grouped by category with fix guidance
    md.append("## New Issues Introduced")
    if new_keys:
        md.append(f" ({len(new_keys)} issue{'s' if len(new_keys) != 1 else ''}"
                  f" in {len({k[0] for k in new_keys})} file"
                  f"{'s' if len({k[0] for k in new_keys}) != 1 else ''})\n")

        # Group by category
        new_by_cat = {}
        for key in new_keys:
            cid = key[2]
            if cid not in new_by_cat:
                new_by_cat[cid] = []
            new_by_cat[cid].append(head_issue_map.get(key, {
                "file": key[0], "line": key[1],
                "category_id": cid, "source_line": "",
            }))

        for cid in sorted(new_by_cat.keys()):
            cat = CATEGORIES[cid]
            issues = new_by_cat[cid]
            md.append(
                f"### {cat['name']} ({len(issues)} new) — "
                f"{cat['description']}"
            )
            md.append(f"**Fix:** {cat['fix']}")
            md.append(f"**Reference:** {cat['migration_ref']}\n")
            md.append("| File | Line | Code |")
            md.append("|------|------|------|")
            for iss in sorted(issues, key=lambda i: (i["file"], i["line"])):
                code = iss.get("source_line", "").strip().replace("|", "\\|")
                if len(code) > 80:
                    code = code[:77] + "..."
                md.append(f"| `{iss['file']}` | {iss['line']} | `{code}` |")
            md.append("")
    else:
        md.append("\n:white_check_mark: **None — no new incompatibilities introduced**\n")

    # Issues fixed
    md.append("## Issues Fixed")
    if fixed_keys:
        md.append(f" ({len(fixed_keys)} issue{'s' if len(fixed_keys) != 1 else ''})\n")
        md.append("| File | Line | Category |")
        md.append("|------|------|----------|")
        for key in sorted(fixed_keys):
            iss = base_issue_map.get(key, {})
            cat_name = CATEGORIES.get(key[2], {}).get("name", f"#{key[2]}")
            md.append(f"| `{key[0]}` | {key[1]} | {cat_name} |")
        md.append("")
    else:
        md.append("\n(none)\n")

    # Verdict
    md.append("## Verdict\n")
    if not new_keys:
        md.append(":white_check_mark: **No new incompatibilities introduced**")
    else:
        n_files = len({k[0] for k in new_keys})
        md.append(
            f":x: **{len(new_keys)} new incompatibilit"
            f"{'ies' if len(new_keys) != 1 else 'y'} "
            f"in {n_files} file{'s' if n_files != 1 else ''}**"
        )

    return "\n".join(md)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=f"MicroPython {MICROPYTHON_VERSION} compatibility checker for SeedSigner",
    )
    parser.add_argument(
        "files", nargs="*", default=[],
        help="Specific files or directories to scan (default: src/seedsigner/)",
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output JSON instead of Markdown",
    )
    parser.add_argument(
        "--diff", metavar="BASE_JSON", dest="diff_base",
        help="Compare against a base JSON report (diff mode)",
    )
    parser.add_argument(
        "--output", "-o", metavar="FILE",
        help="Write report to file (default: stdout)",
    )
    parser.add_argument(
        "--no-mpy-cross", action="store_true",
        help="Skip mpy-cross compilation step",
    )
    parser.add_argument(
        "--mpy-cross-path", default="mpy-cross",
        help="Path to mpy-cross binary (default: mpy-cross)",
    )
    parser.add_argument(
        "--exclude", action="append", default=[],
        help="Additional directory to exclude (repeatable)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Include per-file line-by-line detail in Markdown report",
    )
    parser.add_argument(
        "--baseline", metavar="JSON", default=DEFAULT_BASELINE,
        help=f"Per-category severity config (default: {DEFAULT_BASELINE})",
    )
    parser.add_argument(
        "--ci", action="store_true",
        help="CI mode: emit GitHub ::warning::/::error:: annotations and set "
             "exit code from `fail`-severity categories (the ratchet).",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# CI ratchet — per-category severity gate
# ---------------------------------------------------------------------------

DEFAULT_BASELINE = "tools/mpy_compat_baseline.json"


def load_baseline(path):
    """Load the severity config. Missing file => every category 'warn'."""
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"categories": {}}


def evaluate_severity(result, baseline):
    """Return (exit_code, annotations). A category set to `fail` (with any
    occurrence outside its allowed_files) blocks CI; `warn` never blocks.
    Monotonic tightening (warn->fail, shrink allowed_files) is review-enforced."""
    cfg = baseline.get("categories", {})
    annotations = []
    exit_code = 0
    for iss in result["issues"]:
        cid = str(iss["category_id"])
        entry = cfg.get(cid, {})
        severity = entry.get("severity", "warn")
        allowed = entry.get("allowed_files", [])
        if severity == "fail" and iss["file"] not in allowed:
            level = "error"
            exit_code = 1
        else:
            level = "warning"
        annotations.append(
            f"::{level} file={iss['file']},line={iss['line']}::"
            f"[mpy:{cid} {iss['category_name']}] {iss['message']}"
        )
    return exit_code, annotations


def main(argv=None):
    args = parse_args(argv)

    use_mpy_cross = not args.no_mpy_cross

    # Discover files
    explicit = args.files if args.files else None
    scan_root = DEFAULT_SCAN_ROOT if not explicit else None

    if explicit:
        files = discover_files(None, args.exclude, explicit_files=explicit)
    else:
        files = discover_files(scan_root, args.exclude)

    if not files:
        print("No files found to scan.", file=sys.stderr)
        sys.exit(1)

    # Scan all files
    all_issues = []
    for fpath in files:
        if args.verbose:
            print(f"  Scanning {fpath}...", file=sys.stderr)
        file_issues = scan_file(
            fpath,
            use_mpy_cross=use_mpy_cross,
            mpy_cross_path=args.mpy_cross_path,
        )
        all_issues.extend(file_issues)

    # Build result
    result = build_result(files, all_issues)

    # Generate output
    if args.diff_base:
        # Diff mode
        with open(args.diff_base, "r") as f:
            base_result = json.load(f)

        if args.json_output:
            output = json.dumps({"base": base_result, "head": result}, indent=2)
        else:
            output = generate_diff_report(base_result, result)
    else:
        # Absolute mode
        if args.json_output:
            output = json.dumps(result, indent=2)
        else:
            output = generate_markdown_report(result, verbose=args.verbose)

    # Write output
    if args.output:
        with open(args.output, "w") as f:
            f.write(output)
            f.write("\n")
        print(
            f"Report written to {args.output} "
            f"({result['summary']['total_issues']} issues found)",
            file=sys.stderr,
        )
    else:
        print(output)

    # CI ratchet: emit annotations and set exit code from `fail` categories.
    # (Diff mode keeps its own pass/fail; this gates absolute scans.)
    if args.ci and not args.diff_base:
        baseline = load_baseline(args.baseline)
        exit_code, annotations = evaluate_severity(result, baseline)
        for a in annotations:
            print(a, file=sys.stderr)
        return exit_code

    return 0


if __name__ == "__main__":
    sys.exit(main())
