# MicroPython 1.27 Compatibility Guide

Developer reference for writing SeedSigner business-logic code (models, views,
helpers, controller) that runs on **both** CPython (Pi Zero) **and** stock
**MicroPython 1.27** (ESP32). It is also the specification for the automated
checker `tools/mpy_compat_check.py` — every numbered section below is one checker
category, and `tools/mpy_compat_check.py` cites the same MicroPython docs.

> **Scope.** Applies to MicroPython-targeted code under `src/seedsigner/`,
> excluding `gui/` (PIL, being replaced by LVGL), `hardware/` (being replaced),
> and `helpers/qr.py`. Those keep CPython-only code legitimately.

## Why these differences exist

Production code targets **Python 3.10** (minimum/default; 3.12 optional).
MicroPython 1.27 implements *"Python 3.4 and some select features of Python 3.5
and above"* ([Differences from CPython](https://docs.micropython.org/en/v1.27.0/genrst/index.html)).
The incompatibilities are exactly the post-3.4 features and standard-library
modules the 3.10 source uses that 1.27 doesn't provide. Authoritative sources:
[syntax](https://docs.micropython.org/en/v1.27.0/genrst/syntax.html) ·
[core language](https://docs.micropython.org/en/v1.27.0/genrst/core_language.html) ·
[builtin types](https://docs.micropython.org/en/v1.27.0/genrst/builtin_types.html) ·
[modules](https://docs.micropython.org/en/v1.27.0/genrst/modules.html) ·
[library index](https://docs.micropython.org/en/v1.27.0/library/index.html) ·
per-version tables [3.5](https://docs.micropython.org/en/v1.27.0/differences/python_35.html)–[3.10](https://docs.micropython.org/en/v1.27.0/differences/python_310.html).

## How to use this guide

- **Writing code:** skim the catalog; the ❌/✅ pattern in each section shows the
  compatible form. Run `python tools/mpy_compat_check.py <your files>` to get a
  personalized list of what to fix.
- **The catalog is the checker.** Section §N ↔ checker category N. "Detection"
  tells you whether the checker catches it **statically** or whether it's a
  **runtime hazard** you must verify by running on MicroPython.
- **CI ratchet:** the checker reports every category as a non-blocking warning;
  once a category is fully migrated, its PR flips it to blocking in
  `tools/mpy_compat_baseline.json` so it can't regress. See [Checker & ratchet](#checker--ratchet).

---

# Catalog of incompatibilities

Grouped for reading; the **§N** is the checker category id. Each entry: the rule,
the MicroPython-doc basis, and the compatible pattern.

## A. Absent standard-library modules (import / attribute fails at runtime)

A module (or attribute) that does not exist in MicroPython 1.27 *core*. Note:
many exist only as separately-installable **micropython-lib** packages, which do
**not** ship on the device — they do not count as available.

### §1 `dataclasses` — *Detection: static*
**Why:** no `dataclasses` module ([library index](https://docs.micropython.org/en/v1.27.0/library/index.html) omits it).
```python
# ❌
from dataclasses import dataclass
@dataclass
class Destination:
    view: object
    kwargs: dict = None
# ✅ explicit __init__
class Destination:
    def __init__(self, view, kwargs=None):
        self.view = view
        self.kwargs = kwargs if kwargs is not None else {}
```
`@dataclass` does not overwrite an explicitly-defined `__eq__`/`__repr__`, so
those carry over when you convert. For multiple-inheritance encoders see [§22](#22-multiple-inheritance--mro-detection-runtime-hazard).

### §3 `logging` — *required frozen dependency, NOT a checker category*
**Why:** `logging` is not in MicroPython core — it exists only as an optional
`micropython-lib` package. A full logger is too core to hand-roll, so rather than
shim it, SeedSigner **requires the official `micropython-lib` `logging` to be
frozen into the firmware** (a declared build dependency). `import logging` is
therefore correct on both runtimes and is *not* flagged — there is no checker
category 3.
```python
# ✅  import logging
#     logger = logging.getLogger(__name__)
```
**Convention (so shared code behaves on both):** use `getLogger(__name__)` plus
the level methods only. `micropython-lib`'s `logging` is **flat** — no logger
hierarchy/propagation, and its `Formatter` supports only
`levelname`/`name`/`message`/`msecs`/`asctime` (no `funcName`/`lineno`). Keep any
reliance on propagation or rich format fields in the per-platform bootstrap
(`src/main.py` on CPython), not in shared business logic.

### §4 `threading` — *Detection: static*
**Why:** MicroPython has only low-level `_thread`; no `Thread` class, and locks
lack context-manager support ([library index](https://docs.micropython.org/en/v1.27.0/library/index.html) lists `_thread`, not `threading`).
Treat as **high-care** — design a real wrapper and validate concurrency; do not
paper over daemon/lock semantics.

### §5 `enum` — *Detection: static*
**Why:** no `enum` module.
```python
# ❌  from enum import IntEnum
#     class Status(IntEnum): A = 1
# ✅  class Status:           # plain constants
#         A = 1
```
Valid only if you don't use `.name`/`.value`/iteration (SeedSigner doesn't).

### §6 `pathlib` — *Detection: static*
**Why:** no `pathlib`. **And `os.path` is NOT a valid replacement** — it is also
absent (see §18). Build paths from `__file__`/`os.getcwd()` and string ops.
```python
# ❌  l10n = pathlib.Path(__file__).parent.parent / "resources"
# ✅  here = __file__.rsplit("/", 1)[0]
#     l10n = here.rsplit("/", 1)[0] + "/resources"
```

### §7 `gettext` — *Detection: static*
**Why:** no `gettext` module.
```python
# ❌  from gettext import gettext as _
# ✅  from seedsigner.compat.l10n import gettext as _
```
On MicroPython `_()` is a passthrough; real `gettext` on CPython. Also replace
`os.environ['LANGUAGE'] = …` with `os.putenv` / `os.getenv` (see §18).

### §10 `importlib` — *Detection: static*
**Why:** no `importlib`.
```python
# ❌  from importlib import import_module ; import_module('embit')
# ✅  __import__('embit')          # builtin on both runtimes
```

### §11 `os.fsync` — *Detection: static*
**Why:** `os` has `os.sync()` (sync all filesystems) but **no per-fd `os.fsync`**
([os](https://docs.micropython.org/en/v1.27.0/library/os.html)).
```python
# ✅  if hasattr(os, "fsync"): os.fsync(f.fileno())
```

### §12 `platform` — *Detection: static*
**Why:** no `platform` module; use `os.uname()` / `sys.platform` / `sys.implementation`.
```python
# ❌  import platform ; host = platform.uname()[1]
# ✅  try: import platform; host = platform.uname()[1]
#     except ImportError: import os; host = getattr(os.uname(), "nodename", "seedsigner")
```

### §13 `traceback` — *Detection: static*
**Why:** no `traceback` module; `sys.print_exception(exc, file)` is provided, and
**`sys.exc_info()` is absent** ([sys](https://docs.micropython.org/en/v1.27.0/library/sys.html)).
```python
# ❌  import traceback ; s = traceback.format_exc()
# ✅  import sys, io
#     buf = io.StringIO(); sys.print_exception(exc, buf); s = buf.getvalue()
```
Use the caught exception object directly — do **not** rely on `sys.exc_info()[1]`.

### §16 `subprocess` — *Detection: static*
**Why:** no process spawning. (Only user is the excluded `helpers/qr.py`.)

### §18 `os.path` / `os.walk` / `os.environ` — *Detection: static*
**Why:** `os` provides `listdir`, `ilistdir`, `uname`, `urandom`, `sync`, `stat`,
`getcwd`, `mkdir`, `remove`, `rename` — but **no `os.path` submodule, no
`os.walk`, no `os.environ`** ([os](https://docs.micropython.org/en/v1.27.0/library/os.html);
[modules](https://docs.micropython.org/en/v1.27.0/genrst/modules.html): *"the environ
attribute is not implemented … use getenv, putenv, and unsetenv"*).
```python
# ❌  if os.path.exists(p): ...
#     for root, dirs, files in os.walk(d): ...
#     os.environ["LANGUAGE"] = code
# ✅  try: os.stat(p); exists = True
#     except OSError: exists = False
#     # walk via os.ilistdir(d) recursively
#     os.putenv("LANGUAGE", code)
```

### §20 other absent stdlib modules — *Detection: static*
**Why:** absent from MicroPython core (micropython-lib only):
`datetime, decimal, functools, itertools, copy, inspect, warnings, contextlib,
abc, secrets, uuid, string, queue`. Also **`collections` is a documented subset**
([collections](https://docs.micropython.org/en/v1.27.0/library/collections.html):
only `deque`, `namedtuple`, `OrderedDict` — **no `defaultdict`/`Counter`**).
Avoid these, or vendor a small pure-Python equivalent into the app. *(None are
used in current in-scope code — this category is a regression guard.)*
Security-sensitive swaps: `secrets` → `os.urandom(n)`; never use `random` for keys.

**`zlib` — RESOLVED via `compat.zlib`.** MicroPython removed the `zlib` module;
**1.27 ships `deflate`, not `zlib`** (so `import zlib` raises at runtime — a gap
the static AST scan missed because the checker had wrongly listed `zlib` as
core; the MicroPython import smoke surfaced it). The only use is BBQR's raw-DEFLATE
decompression in `models/decode_qr.py`, now reached through
`seedsigner/compat/zlib.py` — `zlib.decompressobj` on CPython, `deflate.DeflateIO`
on-device, both via `__import__(...)` so the guarded refs stay checker-invisible.

## B. Language & syntax differences

### §2 Type annotations — `typing` import & executed generics — *Detection: static*
**The nuance that matters:** MicroPython **parses and discards** annotations
([python_35](https://docs.micropython.org/en/v1.27.0/differences/python_35.html),
PEP 484 footnote: *"The MicroPython parser correctly ignores all type hints.
However, the typing module is not built-in"*; PEP 526 variable annotations =
Complete). So **ordinary annotations need no change**:
```python
def run_screen(self, cls, **kw) -> int | str:   # ✅ fine — annotation is ignored
self.mnemonic: list[str] = []                    # ✅ fine — annotation is ignored
```
Only two things break, both because they **execute**:
```python
from typing import List          # ❌ ImportError — typing isn't built-in
class BackStack(list[Destination]):  # ❌ TypeError — list[...] runs at class def
T = list[int]                    # ❌ TypeError — generic subscript runs on the RHS
# ✅
class BackStack(list):  ...      # plain base
```
The checker flags **only** `typing` imports and runtime-executed generics — not
annotation slots. Do **not** strip ordinary annotations; they are free.

### §19 Extended slices (`step != 1`) — *Detection: static*
**Why:** subscripting with a step other than 1 is unimplemented for
str/bytes/list/tuple ([builtin types](https://docs.micropython.org/en/v1.27.0/genrst/builtin_types.html):
*"Subscript with step != 1 is not yet implemented"*).
```python
# ❌  for c in data[::-1]: ...
# ✅  for c in reversed(data): ...
```

### Compile-level syntax (caught by `mpy-cross`, not a pattern category)
These fail to **compile** on MicroPython 1.27, so `mpy-cross` (Pass 1) catches them:
- **`match`/`case`** statements — not implemented ([python_310](https://docs.micropython.org/en/v1.27.0/differences/python_310.html)). Use `if/elif`.
- **`\N{NAME}`** unicode-name escapes — `NotImplementedError` ([syntax](https://docs.micropython.org/en/v1.27.0/genrst/syntax.html)). Use `\uXXXX`.
- **f-string** `!a` conversion, concatenation with an adjacent brace-literal, and
  unbalanced nested braces ([core language](https://docs.micropython.org/en/v1.27.0/genrst/core_language.html)).
- **Supported, do NOT avoid:** walrus `:=` and the f-string `=` debug form are
  **Complete** in 1.27 — they are fine to use.

## C. Builtin & type gaps

### §21 Builtin/method gaps — *Detection: static (heuristic; single-line comments ignored)*
**Why** ([builtin types](https://docs.micropython.org/en/v1.27.0/genrst/builtin_types.html),
[builtins](https://docs.micropython.org/en/v1.27.0/library/builtins.html)):
| Avoid | Use |
|---|---|
| `n.bit_length()` | precompute, or `len(bin(n)) - 2` style helper |
| `h.hexdigest()` | `binascii.hexlify(h.digest())` |
| `s.ljust(n)` / `s.rjust(n)` | `"%-Ns" % s` / `"%Ns" % s` |
| `s.removeprefix(p)` / `removesuffix` | slice with an explicit length check |
| `int.to_bytes(n, "big", signed=True)` | `signed=` unsupported — avoid; pass byteorder positionally |
| `s.zfill(n)` | `"{:0N}".format(x)` / `"%0Nd" % x` / manual pad |
| `except UnicodeDecodeError` / `UnicodeEncodeError` | catch/raise their `UnicodeError` base (the subclasses are absent) |

### Builtin runtime hazards (NOT statically checked — verify on device)
These **run without error but can be wrong** — audit by hand / test on MicroPython:
- **`int.from_bytes(buf, "big", signed=True)`** is silently treated as **unsigned**
  (`signed` not implemented) — a negative value decodes as a large positive. Avoid signed byte ints.
- **`float` is single-precision (32-bit)** on the ESP32 build (`MICROPY_FLOAT_IMPL_FLOAT`)
  — never hold satoshi/BTC amounts as float; keep integer satoshis.
- **`dict.keys()` is not set-like** (no `&`/`|` on the view) — wrap in `set()` first.
- **`int.to_bytes` does NOT silently truncate** — it raises `OverflowError` when the
  width is too small (same as CPython), so length errors are loud (good).

## D. Regular expressions

### §9 `re` — flags, functions, and pattern features — *Detection: static*
**Why** ([re](https://docs.micropython.org/en/v1.27.0/library/re.html)): MicroPython's
`re` is a small subset. Unsupported (the checker flags all of these):
- **Counted repetitions** `{n}`, `{m,n}` → expand or use `+`/`*` with a `len()` check.
- **Flags** `IGNORECASE`/`MULTILINE`/`DOTALL`/… → only `re.DEBUG` exists. For
  case-insensitivity, normalize case first or expand the character class.
- **Functions** `findall`/`finditer`/`fullmatch`/`subn` → not provided; loop with
  `search`/`match`, or `compile(...).split()`.
- **Named groups** `(?P<name>)`, **non-capturing** `(?:...)`, **lookarounds**
  `(?=)/(?!)/(?<=)/(?<!)`, **word boundaries** `\b`/`\B` → unsupported.
- **Supported:** plain capturing groups `(...)` + `.group(n)`, anchors, classes,
  `search`/`match`/`sub`/`compile`/`split`.
```python
# ❌  re.search(r"^B\$[2HZ]P[0-9A-Z]{4}", s)
# ✅  re.search(r"^B\$[2HZ]P[0-9A-Z][0-9A-Z][0-9A-Z][0-9A-Z]", s)
# ❌  re.findall(r'[^,\s]+', s)                  # findall absent
# ❌  re.search(r'^bitcoin:', s, re.IGNORECASE)  # IGNORECASE absent
```
SeedSigner's QR decoder (`models/decode_qr.py`) relies heavily on `IGNORECASE`
and counted reps — this is the most behavior-sensitive category; cover it with tests.

## E. Bitcoin-critical primitives (cryptography)

MicroPython core `hashlib` provides only `sha256`/`sha1`/`md5` and **no
`ripemd160`, `sha512`, `pbkdf2`, `hmac`** ([hashlib](https://docs.micropython.org/en/v1.27.0/library/hashlib.html)).
**But most of this is already solved by the embit ecosystem** — do not reinvent it:

| Primitive | Provided by | Notes |
|---|---|---|
| `ripemd160`, `hash160` | **embit** (`embit.hashes`) | pure-Python fallback when hashlib lacks ripemd160 — works on stock MicroPython |
| `sha512`, `pbkdf2_hmac_sha512` | **[diybitcoinhardware/uhashlib](https://github.com/diybitcoinhardware/uhashlib)** native module | the c-module the embit MicroPython stack uses; the firmware bundles it (same mechanism as the LVGL c-module) |
| `hmac` (HMAC-SHA512, BIP-32) | embit MicroPython stack (Python `hmac`) | uhashlib lists optimized hmac as TODO; a Python `hmac` covers it. Verify present on the 1.27 build. |
| `sha256` family | MicroPython `hashlib` | present in core |

So the only genuinely **app-owned** cryptography work is:

### §8 `unicodedata` NFKD/NFC — *Detection: static (import) — RESOLVED by removal*
**Why it existed:** no `unicodedata` anywhere (MicroPython core, embit, or uhashlib),
and **embit does NOT normalize** — `bip39.mnemonic_to_seed` does
`mnemonic.encode("utf-8")` and expects an already-normalized string, so SeedSigner
used to NFKD/NFC itself in `models/seed.py`.
**Resolution — removal, not a shim.** SeedSigner's input domain is ASCII: the BIP-39
passphrase keyboard offers only ASCII characters, and the only supported wordlist is
English (ASCII). Unicode normalization is the **identity transform on ASCII**, so the
`unicodedata.normalize` calls in `models/seed.py` and `helpers/mnemonic_generation.py`
are deleted. `Seed._require_ascii()` backstops the seed-derivation boundary, raising on
any non-ASCII passphrase or mnemonic — without normalization, a non-ASCII input would
silently derive the *wrong* keys, so this fails loud instead. No NFKD table or c-module
is shipped. If extended-charset passphrases or a non-ASCII wordlist are ever added,
normalization becomes mandatory again and belongs in **embit** (the BIP-39 library that
punted it to the caller), not the app — see the integration tracking doc.

### §14 `base64` / base32 — *Detection: static — RESOLVED via `compat.base64`*
**Why:** no `base64` module; base64 *encode/decode* is available via
`binascii.a2b_base64`/`b2a_base64`, but **base32 is absent** everywhere
([binascii](https://docs.micropython.org/en/v1.27.0/library/binascii.html)).
**Resolution:** `seedsigner/compat/base64.py` — `b64encode`/`b64decode` over `binascii`
(stripping the trailing newline `b2a_base64` adds) plus a small pure-Python `b32decode`
for the one call site (BBQR in `decode_qr.py`; `b32encode` was unused and dropped). The
real `base64` is used on CPython, so Pi Zero is byte-for-byte unchanged.

### §15 `hmac` — *Detection: static — RESOLVED via `compat.hmac`*
**Why:** no `hmac` module in core. embit's BIP-32 already uses `hmac` on MicroPython, so
the embit stack provides it — rely on the same module rather than reimplementing.
**Resolution:** `seedsigner/compat/hmac.py` — a one-shot `digest(key, msg, digestmod)`
that delegates to the real `hmac.digest()` on CPython and falls back to
`hmac.new(...).digest()` (embit's port may expose only the constructor). **Verify embit's
`hmac` is importable on the 1.27 firmware build** (its compat may predate 1.27).

## F. Third-party dependencies

### §17 unverified third-party imports — *Detection: static (heuristic)*
Any import not in the known-compatible set (`embit`) is flagged for verification.
`PIL`/`pyzbar`/`qrcode` are informational (being replaced). `urtypes` is pure
Python and needs a MicroPython smoke test.

---

# Runtime hazards that the static checker cannot catch

These run without error and may be **silently wrong**. They require validation on
real MicroPython (the project's empirical-validation step), not just CPython tests.

### §22 Multiple inheritance / MRO — *Detection: heuristic flag only*
**Why:** MicroPython's MRO is **not C3-compliant**, and *"when inheriting from
multiple classes `super()` only calls one class"* ([core language](https://docs.micropython.org/en/v1.27.0/genrst/core_language.html)).
A cooperative `super().__init__(**kwargs)` chain through a diamond can silently
**skip a base**, leaving attributes unset. SeedSigner's QR encoders
(`models/encode_qr.py`: `StaticXpubQrEncoder`, `SpecterLegacyXPubQrEncoder`,
`UrXpubQrEncoder`) are real diamonds. The checker flags classes with ≥2 bases as a
**review** item — it is not a guaranteed break. **Validate the converted encoders on
stock MicroPython against known QR vectors**; if a base is skipped, linearize the
`__init__` manually instead of relying on cooperative `super()`.

Other non-checkable hazards (audit if used): descriptors are inert unless
`MICROPY_PY_DESCRIPTORS` is built in; `__slots__` is a no-op; `__init_subclass__`
is not auto-called; private name-mangling (`self.__x`) is absent; a generator-based
context manager may not run `__exit__` if abandoned early; `locals()`/`eval()` do
not see local variables; exception chaining (`raise X from Y`) metadata is not
retained (degraded tracebacks, not a correctness break).

---

# Checker & ratchet

- **Run:** `python tools/mpy_compat_check.py [files]` (Markdown), `--json`,
  `--verbose`, `--diff base.json`. Add `--ci` for GitHub annotations + a ratchet
  exit code.
- **`mpy-cross` (Pass 1)** compiles each file to catch compile-level syntax
  (§ "Compile-level syntax"); **pattern/AST scan (Pass 2)** detects categories 1–22.
- **Ratchet:** `tools/mpy_compat_baseline.json` holds a per-category `severity`.
  `warn` = reported, CI green. `fail` = blocks CI on any occurrence outside that
  category's `allowed_files`. Each migration PR flips its category `warn → fail`
  in the same diff that drives its count to zero, so the gate only tightens and a
  fixed category can never silently regress. Categories migrated file-by-file
  shrink `allowed_files` before the final flip.
- **Detection ≠ guarantee.** Categories marked *runtime hazard* / *heuristic*
  (notably §22 and the builtin runtime hazards) require validation on real
  MicroPython; the static flag is a prompt to check, not a verdict.

---
*Validated against the MicroPython v1.27.0 documentation. Keep citations in sync
with `tools/mpy_compat_check.py` (the checker and this guide must not drift).*
