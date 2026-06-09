#!/usr/bin/env python3
"""
MicroPython 1.27 compatibility checker for SeedSigner.

Scans Python source files for incompatibilities with MicroPython 1.27 using
two passes: mpy-cross compilation (syntax-level) and pattern scanning (16
categories from docs/micropython_migration.md plus third-party dep checks).

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
]

EXCLUDED_FILES = [
    "src/seedsigner/helpers/qr.py",
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
    "dataclasses", "typing", "logging", "threading", "enum", "pathlib",
    "gettext", "unicodedata", "importlib", "platform", "traceback",
    "base64", "hmac", "subprocess",
}

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


# ---------------------------------------------------------------------------
# Category definitions
# ---------------------------------------------------------------------------

CATEGORIES = {
    1: {
        "name": "@dataclass",
        "description": "MicroPython has no `dataclasses` module.",
        "fix": "Convert to explicit `__init__` with manual attribute assignment.",
        "migration_ref": "docs/micropython_migration.md §1",
    },
    2: {
        "name": "Type annotations",
        "description": (
            "MicroPython does not support PEP 604 (`X | Y`), "
            "PEP 585 (`list[str]`), or the `typing` module."
        ),
        "fix": "Remove type annotations from signatures and variable declarations.",
        "migration_ref": "docs/micropython_migration.md §2",
    },
    3: {
        "name": "logging",
        "description": "MicroPython has no `logging` module.",
        "fix": "Use a compat shim: `from seedsigner.compat.logging import getLogger`.",
        "migration_ref": "docs/micropython_migration.md §3",
    },
    4: {
        "name": "threading",
        "description": (
            "MicroPython has only `_thread`, not `threading`. "
            "No `Thread` class; locks lack context manager support."
        ),
        "fix": "Use a compat shim: `from seedsigner.compat.threading import Thread, Lock`.",
        "migration_ref": "docs/micropython_migration.md §4",
    },
    5: {
        "name": "enum.IntEnum",
        "description": "MicroPython has no `enum` module.",
        "fix": "Replace with plain class constants.",
        "migration_ref": "docs/micropython_migration.md §5",
    },
    6: {
        "name": "pathlib",
        "description": "MicroPython has no `pathlib` module.",
        "fix": "Replace `pathlib.Path` with `os.path` equivalents.",
        "migration_ref": "docs/micropython_migration.md §6",
    },
    7: {
        "name": "gettext",
        "description": "MicroPython has no `gettext` module.",
        "fix": "Use a compat shim: `from seedsigner.compat.l10n import gettext as _`.",
        "migration_ref": "docs/micropython_migration.md §7",
    },
    8: {
        "name": "unicodedata",
        "description": (
            "MicroPython has no `unicodedata` module. "
            "NFKD normalization is **critical for BIP-39 seed derivation**."
        ),
        "fix": (
            "Implement NFKD/NFC in seedsigner-c-modules C extension. "
            "Must validate against BIP-39 test vectors."
        ),
        "severity": "high",
        "migration_ref": "docs/micropython_migration.md §8",
    },
    9: {
        "name": "re counted repetitions",
        "description": (
            "MicroPython's `re` does not support `{n}`, `{m,n}` counted "
            "repetitions, named groups, or non-capturing groups."
        ),
        "fix": "Expand `{4}` to four repeated character classes; use `+` with len() for ranges.",
        "migration_ref": "docs/micropython_migration.md §9",
    },
    10: {
        "name": "importlib",
        "description": "MicroPython has no `importlib` module.",
        "fix": "Replace `importlib.import_module('x')` with `__import__('x')`.",
        "migration_ref": "docs/micropython_migration.md §10",
    },
    11: {
        "name": "os.fsync",
        "description": "MicroPython has no `os.fsync`.",
        "fix": "Guard with `if hasattr(os, 'fsync'): os.fsync(...)`.",
        "migration_ref": "docs/micropython_migration.md §11",
    },
    12: {
        "name": "platform",
        "description": "MicroPython has no `platform` module.",
        "fix": "Use `os.uname()` as fallback.",
        "migration_ref": "docs/micropython_migration.md §12",
    },
    13: {
        "name": "traceback",
        "description": "MicroPython's `traceback` is minimal; `format_exc()` may not work.",
        "fix": "Use `sys.print_exception()` with `io.StringIO` buffer.",
        "migration_ref": "docs/micropython_migration.md §13",
    },
    14: {
        "name": "base64",
        "description": "MicroPython has no `base64` module (no base32 functions).",
        "fix": "Implement pure-Python base32 or add to seedsigner-c-modules.",
        "migration_ref": "docs/micropython_migration.md §14",
    },
    15: {
        "name": "hmac",
        "description": "MicroPython has no `hmac` module.",
        "fix": "Implement pure-Python HMAC using `hashlib`, or check if embit provides it.",
        "migration_ref": "docs/micropython_migration.md §15",
    },
    16: {
        "name": "subprocess",
        "description": "MicroPython has no `subprocess` module.",
        "fix": "Remove or replace with platform-appropriate alternative.",
        "migration_ref": "docs/micropython_migration.md §16",
    },
    17: {
        "name": "Third-party dependency",
        "description": (
            "Third-party package that may not be available or verified "
            "for MicroPython."
        ),
        "fix": "Verify package works on MicroPython 1.27 or find alternative.",
        "migration_ref": "docs/micropython_migration.md (third-party deps table)",
    },
}


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

    # Category 3: logging
    (re.compile(r"^\s*import\s+logging\b"), 3,
     "`logging` module import"),
    (re.compile(r"^\s*from\s+logging\s+import"), 3,
     "`logging` module import"),

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
    """Detect PEP 604 unions and PEP 585 lowercase generics in annotations."""

    def __init__(self, file_path, lines):
        self.file_path = file_path
        self.lines = lines
        self.issues = []
        self._in_annotation = False

    def _check_annotation(self, node):
        """Visit a node in annotation context."""
        if node is None:
            return
        old = self._in_annotation
        self._in_annotation = True
        self.visit(node)
        self._in_annotation = old

    def visit_FunctionDef(self, node):
        # Check return annotation
        self._check_annotation(node.returns)
        # Check argument annotations
        all_args = (
            node.args.args
            + node.args.posonlyargs
            + node.args.kwonlyargs
        )
        if node.args.vararg:
            self._check_annotation(node.args.vararg.annotation)
        if node.args.kwarg:
            self._check_annotation(node.args.kwarg.annotation)
        for arg in all_args:
            self._check_annotation(arg.annotation)
        self.generic_visit(node)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_AnnAssign(self, node):
        self._check_annotation(node.annotation)
        self.generic_visit(node)

    def visit_ClassDef(self, node):
        # Check base classes for PEP 585 generics like list[Destination]
        for base in node.bases:
            self._check_base(base, node.lineno)
        self.generic_visit(node)

    def _check_base(self, node, class_lineno):
        """Check class bases for PEP 585 lowercase generics."""
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name):
            if node.value.id in ("list", "dict", "tuple", "set", "frozenset", "type"):
                src = self.lines[node.lineno - 1] if node.lineno <= len(self.lines) else ""
                self.issues.append(Issue(
                    file_path=self.file_path,
                    line_number=node.lineno,
                    category_id=2,
                    message=f"PEP 585 lowercase generic `{node.value.id}[...]` in class base",
                    source_line=src,
                ))

    def visit_BinOp(self, node):
        if self._in_annotation and isinstance(node.op, ast.BitOr):
            src = self.lines[node.lineno - 1] if node.lineno <= len(self.lines) else ""
            self.issues.append(Issue(
                file_path=self.file_path,
                line_number=node.lineno,
                category_id=2,
                message="PEP 604 union type `X | Y` in annotation",
                source_line=src,
            ))
        self.generic_visit(node)

    def visit_Subscript(self, node):
        if self._in_annotation and isinstance(node.value, ast.Name):
            if node.value.id in ("list", "dict", "tuple", "set", "frozenset", "type"):
                src = self.lines[node.lineno - 1] if node.lineno <= len(self.lines) else ""
                self.issues.append(Issue(
                    file_path=self.file_path,
                    line_number=node.lineno,
                    category_id=2,
                    message=f"PEP 585 lowercase generic `{node.value.id}[...]` in annotation",
                    source_line=src,
                ))
        self.generic_visit(node)


def scan_ast_annotations(file_path, content, lines):
    """Use AST to find PEP 604/585 annotation issues."""
    try:
        tree = ast.parse(content, filename=file_path)
    except SyntaxError:
        return []  # mpy-cross will catch syntax errors
    visitor = AnnotationVisitor(file_path, lines)
    visitor.visit(tree)
    return visitor.issues


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
    issues.extend(scan_ast_annotations(file_path, content, lines))
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

    # Category fix reference
    md.append("## Fix Reference\n")
    for cid_str in sorted(result["categories"].keys(), key=int):
        cid = int(cid_str)
        cat = CATEGORIES[cid]
        md.append(f"**{cid}. {cat['name']}** — {cat['description']}")
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
    return parser.parse_args(argv)


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

    return 0


if __name__ == "__main__":
    sys.exit(main())
