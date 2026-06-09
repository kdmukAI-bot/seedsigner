# MicroPython 1.27.0 Migration Plan for SeedSigner

## Context

SeedSigner will be ported to run on MicroPython 1.27.0 to support new hardware targets. MicroPython implements Python 3.4 with select 3.5+ features, which means many CPython standard library modules and language features used throughout the codebase are unavailable. This plan identifies every category of required refactoring, provides a codebase example for each, and proposes a migration sequence where every PR leaves CPython production-ready.

**Out of scope:** The `gui/` rendering layer (being replaced), `hardware/buttons.py` (being replaced), and all Pillow/PIL usage (being replaced). The `embit` library is already MicroPython-compatible.

---

## Categories of Required Refactors

### 1. `@dataclass` removal

MicroPython has no `dataclasses` module. Convert all `@dataclass` classes to explicit `__init__` constructors.

**Scope:** ~15 non-gui dataclasses (encode_qr.py has 12, plus settings_definition.py, views/view.py, views/screensaver.py)

**Example** - `src/seedsigner/models/encode_qr.py:86`:
```python
# Before
@dataclass
class SeedQrEncoder(BaseStaticQrEncoder):
    mnemonic: List[str] = None
    wordlist_language_code: str = SettingsConstants.WORDLIST_LANGUAGE__ENGLISH
    def __post_init__(self):
        self.wordlist = Seed.get_wordlist(self.wordlist_language_code)
        super().__post_init__()

# After
class SeedQrEncoder(BaseStaticQrEncoder):
    def __init__(self, mnemonic=None,
                 wordlist_language_code=SettingsConstants.WORDLIST_LANGUAGE__ENGLISH, **kwargs):
        self.mnemonic = mnemonic
        self.wordlist_language_code = wordlist_language_code
        self.wordlist = Seed.get_wordlist(self.wordlist_language_code)
        super().__init__(**kwargs)
```

The `**kwargs` forwarding is critical in `encode_qr.py` because of multiple-inheritance diamonds (e.g., `UrXpubQrEncoder` inherits from both `BaseFountainQrEncoder` and `BaseXpubQrEncoder`).

**Classification:** Mechanical but labor-intensive. The pattern is consistent; the inheritance chains in encode_qr.py require careful `__init__` argument forwarding.


### 2. Type annotation syntax (PEP 604 / PEP 585)

MicroPython does not support `int | str` union syntax (PEP 604) or lowercase generic syntax like `list[str]` (PEP 585). The `typing` module itself is unavailable.

**Scope:** ~50+ instances across controller.py, views/view.py, models/settings_definition.py, models/settings.py, models/decode_qr.py, models/psbt_parser.py, helpers/mnemonic_generation.py, views/seed_views.py, views/screensaver.py

**Example** - `src/seedsigner/views/view.py:112`:
```python
# Before
def run_screen(self, Screen_cls: Type[BaseScreen], **kwargs) -> int | str:

# After - remove annotations entirely
def run_screen(self, Screen_cls, **kwargs):
```

**Strategy:** Strip all type annotations from function signatures and variable declarations. They have no runtime effect and MicroPython ignores them. This is the cleanest dual-compatible approach - no shim module needed. The `from typing import List` imports and `List[str]` usage throughout can simply be removed.

**Classification:** Purely mechanical. Could be scripted.


### 3. `logging` module replacement

MicroPython has no `logging` module. Every model, view, and helper file uses `import logging; logger = logging.getLogger(__name__)`.

**Scope:** 20+ files

**Example** - `src/seedsigner/models/seed.py:1,13`:
```python
# Before
import logging
logger = logging.getLogger(__name__)

# After
from seedsigner.compat.logging import getLogger
logger = getLogger(__name__)
```

The compat shim uses runtime detection (`try: import logging / except ImportError: <stub>`): on CPython, re-exports real `logging`; on MicroPython, provides a stub Logger with `debug()`, `info()`, `warning()`, `error()`, `exception()`, `critical()` methods that print to stdout (or no-op at lower levels). All compat shims use this same runtime detection pattern - single codebase, same .py files run on both runtimes.

**Classification:** Mechanical. Requires creating the compat shim first.


### 4. `threading` -> `_thread` migration

MicroPython has only the `_thread` module, not `threading`. No `Thread` class, and `_thread.allocate_lock()` lacks context manager support (`with lock:`).

**Scope:** `models/threads.py` (BaseThread, ThreadsafeCounter), `controller.py` (BackgroundImportThread), `hardware/microsd.py`, `hardware/pivideostream.py`, plus 25+ BaseThread subclasses in gui/ (excluded)

**Example** - `src/seedsigner/models/threads.py:2,7`:
```python
# Before
from threading import Thread, Lock
class BaseThread(Thread):
    def __init__(self):
        super().__init__(daemon=True)

# After
from seedsigner.compat.threading import Thread, Lock
class BaseThread(Thread):
    def __init__(self):
        super().__init__(daemon=True)
```

The compat shim: on CPython, re-exports `threading.Thread` and `threading.Lock`. On MicroPython, provides a `Thread` wrapper around `_thread.start_new_thread()` and a `Lock` wrapper that adds `__enter__`/`__exit__`.

**Design note:** MicroPython `_thread` has no daemon concept - threads die when main exits, which matches SeedSigner's usage (all threads are daemon=True). `is_alive()` must be manually tracked via a flag.

**Classification:** Requires design for the compat shim. Migration of consuming code is mechanical.


### 5. `enum.IntEnum` replacement

MicroPython has no `enum` module.

**Scope:** 1 usage - `models/decode_qr.py:27-35` (`DecodeQRStatus`)

**Example** - `src/seedsigner/models/decode_qr.py:27`:
```python
# Before
from enum import IntEnum
class DecodeQRStatus(IntEnum):
    PART_COMPLETE = 1
    PART_EXISTING = 2
    COMPLETE = 3
    FALSE = 4
    INVALID = 5

# After
class DecodeQRStatus:
    PART_COMPLETE = 1
    PART_EXISTING = 2
    COMPLETE = 3
    FALSE = 4
    INVALID = 5
```

Verified: no code uses IntEnum-specific features (`.name`, `.value`, iteration). All comparisons are `==` against class constants.

**Classification:** Purely mechanical, trivial.


### 6. `pathlib.Path` replacement

MicroPython has no `pathlib`. Used for resolving the directory of `__file__`.

**Scope:** 2 usages - `models/settings.py:43`, `models/settings_definition.py:197-198`

**Example** - `src/seedsigner/models/settings.py:43`:
```python
# Before
import pathlib
l10n_dir = os.path.join(pathlib.Path(__file__).parent.resolve().parent.resolve(), "resources", ...)

# After
l10n_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "resources", ...)
```

**Classification:** Purely mechanical.


### 7. `gettext` i18n replacement

MicroPython has no `gettext` module. Used for `_()` translation function throughout views and in `settings.py` for locale loading.

**Scope:** 10+ files import `gettext` or use `_()` from it. `settings.py` has locale initialization logic using `gettext.translation()`, `gettext.bindtextdomain()`, and `os.environ['LANGUAGE']`.

**Example** - `src/seedsigner/views/seed_views.py` (typical view pattern):
```python
# Before
from gettext import gettext as _

# After
from seedsigner.compat.l10n import gettext as _
```

The compat shim: on CPython, delegates to real `gettext`. On MicroPython, `_()` is a passthrough (returns the input string). The existing `helpers/l10n.py:mark_for_translation` is already MicroPython-compatible.

The `os.environ['LANGUAGE']` usage in `settings.py` also needs attention - MicroPython has no `os.environ` (must use `os.putenv()`).

**Classification:** Mechanical for import swaps. Minor design for the `os.environ` -> `os.putenv` change.


### 8. `unicodedata.normalize` replacement (Bitcoin-critical)

MicroPython has no `unicodedata` module. This is used for BIP-39 compliant mnemonic and passphrase normalization - **incorrect normalization will produce wrong seed derivations**.

**Scope:** `models/seed.py` (6 usages), `helpers/mnemonic_generation.py` (1 usage)

**Example** - `src/seedsigner/models/seed.py:30`:
```python
self._mnemonic = unicodedata.normalize("NFKD", " ".join(mnemonic).strip()).split()
```

**Two forms used:**
- `NFKD` - Compatibility decomposition (critical for seed derivation correctness per BIP-39 spec)
- `NFC` - Canonical composition (used for display of mnemonics/passphrases, lines 73/78/93)

**Approach:** First investigate whether `embit.bip39.mnemonic_to_seed()` already handles NFKD normalization internally (it's MicroPython-compatible, so it likely does). If so, some explicit normalize calls may be redundant. For any remaining cases, implement NFKD/NFC normalization in the `seedsigner-c-modules` C extension - correctness cannot be compromised on a Bitcoin signing device. Must validate against BIP-39 test vectors.

**Classification:** Requires design decision and careful validation. Highest risk item.


### 9. `re` counted repetitions

MicroPython's `re` does not support `{m,n}` counted repetitions or `{n}` exact counts. Also missing: named groups `(?P<name>...)`, non-capturing groups `(?:...)`, `\b` word boundaries.

**Scope:** 4 regex patterns in `models/decode_qr.py`, 1 in `views/scan_views.py`

**Example** - `src/seedsigner/models/decode_qr.py:366`:
```python
# Before
re.search(r"^B\$[2HZ]P[0-9A-Z]{4}", s)

# After - expand the repetition
re.search(r"^B\$[2HZ]P[0-9A-Z][0-9A-Z][0-9A-Z][0-9A-Z]", s)
```

For range repetitions like `\d{48,96}` (line 386) and `[...]{25,62}` (line 519), replace with `\d+` / `[...]+` followed by a `len()` check on the match.

**Classification:** Mechanical with minor design for the range-check replacements.


### 10. `importlib.import_module` replacement

MicroPython has no `importlib`.

**Scope:** 1 usage - `controller.py:57`

**Example** - `src/seedsigner/controller.py:57`:
```python
# Before
from importlib import import_module
import_module('embit')

# After
__import__('embit')
```

`__import__()` is a builtin available on both runtimes.

**Classification:** Purely mechanical, trivial.


### 11. `os.fsync` conditional

MicroPython has no `os.fsync`.

**Scope:** 1 usage - `models/settings.py:138`

**Example:**
```python
# Before
os.fsync(settings_file.fileno())

# After
if hasattr(os, 'fsync'):
    os.fsync(settings_file.fileno())
```

**Classification:** Purely mechanical.


### 12. `platform.uname()` replacement

MicroPython has no `platform` module but has `os.uname()`.

**Scope:** 1 usage - `models/settings.py:6,22`

**Example:**
```python
# Before
import platform
HOSTNAME = platform.uname()[1]

# After
try:
    import platform
    HOSTNAME = platform.uname()[1]
except ImportError:
    import os
    HOSTNAME = getattr(os.uname(), 'nodename', 'seedsigner')
```

**Classification:** Purely mechanical.


### 13. `traceback` replacement

MicroPython's `traceback` module is minimal - `print_exc()` may work but `format_exc()` likely does not.

**Scope:** `controller.py` (3 usages), `helpers/ur2/fountain_decoder.py` (1 debug-only usage)

**Example** - `src/seedsigner/controller.py`:
```python
# Before
import traceback
traceback.format_exc()

# After
import sys, io
buf = io.StringIO()
sys.print_exception(sys.exc_info()[1], buf)
error_str = buf.getvalue()
```

**Classification:** Small design decision for the compat approach.


### 14. `base64` module (no MicroPython equivalent for base32)

MicroPython has `binascii.a2b_base64`/`b2a_base64` but no `base64` module and no base32 functions anywhere.

**Scope:** `models/decode_qr.py:1,15` - uses `base64.b32encode`, `base64.b32decode`, and also `binascii.a2b_base64`/`b2a_base64`

**Example** - `src/seedsigner/models/decode_qr.py:15`:
```python
from base64 import b32encode, b32decode
```

Base32 is used for BECH32/address handling. Will need a pure-Python base32 implementation or inclusion in `seedsigner-c-modules`.

**Classification:** Requires a small implementation or dependency addition.


### 15. `hmac` module

MicroPython has no `hmac` module.

**Scope:** `models/seed.py:4` - used for Electrum seed detection

**Example** - `src/seedsigner/models/seed.py:4,207`:
```python
import hmac
hmac.new(b"Seed version", passphrase_bytes, 'sha512').hexdigest()
```

`hmac` can be implemented in pure Python using `hashlib` (which MicroPython has). Alternatively, check if embit provides HMAC.

**Classification:** Small implementation needed - pure-Python HMAC is straightforward.


### 16. `subprocess` replacement

MicroPython has no `subprocess`.

**Scope:** `helpers/qr.py:5,99-107` - calls the `qrencode` CLI tool

This file is deeply entangled with PIL/Pillow for QR image generation. Since the rendering layer is being replaced, this file will likely be superseded entirely. **No action needed unless the file survives the gui replacement.**

**Classification:** Likely superseded.


---

## Third-Party Dependencies (Status Pending)

These libraries need MicroPython compatibility verification but are **not being investigated now**:

| Library | Usage | Notes |
|---------|-------|-------|
| `urtypes` | UR type encoding (PSBT, Account, Output, Bytes) | Pure Python; needs testing |
| `pyzbar` | QR code reading from camera images | C library; will be replaced with gui layer |
| `qrcode` | QR code generation | Pure Python; likely replaced with gui layer |

---

## Test Suite (Separate Section)

The test suite runs on CPython only and uses `pytest`, `unittest.mock` (MagicMock, Mock, patch), and `dataclasses` - none available in MicroPython.

**What changes as production code migrates:**
- As production imports switch to compat shims, test imports follow automatically (tests import production modules)
- `FlowStep` dataclass in `tests/base.py` should be converted alongside production dataclass removal (Stage 3) for consistency
- No changes needed to mock infrastructure - it stays CPython-only

**What does NOT need to change:**
- pytest fixture patterns, `@patch` decorators, `MagicMock` usage - all CPython-only test infrastructure
- `conftest.py` in screenshot_generator

**Future consideration:** A separate `tests/micropython/` directory could hold minimal tests runnable on MicroPython directly (no pytest, no mock). These would focus on Bitcoin-critical correctness: seed derivation, Unicode normalization, QR encoding/decoding. This is out of scope for the initial migration.

---

## Recommended Migration Sequence

Each PR is independently mergeable and leaves CPython production-ready.

### Stage 1: Zero-risk mechanical changes (no new modules needed)

These PRs touch only the code that's changing and have no dependency on compat shims.

| Order | What | Risk | Files |
|-------|------|------|-------|
| 1.1 | Strip type annotations (PEP 604/585 syntax, `typing` imports) | None | ~15 files across views/, models/, helpers/, controller.py |
| 1.2 | Replace `IntEnum` with plain class constants | None | models/decode_qr.py |
| 1.3 | Replace `pathlib.Path` with `os.path` | None | models/settings.py, models/settings_definition.py |
| 1.4 | Replace `importlib.import_module` with `__import__` | None | controller.py |
| 1.5 | Add `os.fsync` guard | None | models/settings.py |
| 1.6 | Add `platform.uname` fallback | None | models/settings.py |
| 1.7 | Expand `re` counted repetitions | Low | models/decode_qr.py, views/scan_views.py |

### Stage 2: Compat shim infrastructure + shim-dependent migrations

| Order | What | Risk | Files |
|-------|------|------|-------|
| 2.1 | Create `seedsigner/compat/` package with logging, threading, l10n shims | Low | New: compat/__init__.py, compat/logging.py, compat/threading.py, compat/l10n.py |
| 2.2 | Migrate all `import logging` to compat shim | Low | 20+ files |
| 2.3 | Migrate `gettext` imports to compat l10n shim | Low | 10+ files across views/, models/settings.py |
| 2.4 | Migrate threading to compat shim | Medium | models/threads.py, controller.py, hardware/microsd.py |
| 2.5 | Replace `traceback` usage with compat approach | Low | controller.py, helpers/ur2/fountain_decoder.py |

### Stage 3: `@dataclass` removal (largest effort)

| Order | What | Risk | Files |
|-------|------|------|-------|
| 3.1 | Convert `Destination` and View dataclasses | Medium | views/view.py |
| 3.2 | Convert `SettingsEntry` dataclass | Medium | models/settings_definition.py |
| 3.3 | Convert `encode_qr.py` dataclasses (12 classes, multiple inheritance) | Medium | models/encode_qr.py |
| 3.4 | Convert remaining dataclasses (screensaver, etc.) | Low | views/screensaver.py |
| 3.5 | Convert test `FlowStep` dataclass | Low | tests/base.py |

### Stage 4: Bitcoin-critical and design-decision items

| Order | What | Risk | Files |
|-------|------|------|-------|
| 4.1 | Implement `base64.b32encode`/`b32decode` replacement | Low | models/decode_qr.py + new compat or c-module |
| 4.2 | Implement `hmac` replacement | Low | models/seed.py + new compat module or c-module |
| 4.3 | Implement `unicodedata.normalize` replacement | **HIGH** | models/seed.py, helpers/mnemonic_generation.py. Must validate against BIP-39 test vectors. Likely requires C extension in seedsigner-c-modules |

---

## Verification

At every stage:
- `pytest` - full test suite must pass (CPython production correctness)
- `pytest --cov=seedsigner` - coverage should not decrease
- For Stage 4.3 specifically: run BIP-39 test vectors with non-ASCII passphrases to validate Unicode normalization correctness
