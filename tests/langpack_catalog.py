"""Resolve the language-catalog root for the test suite (shared helper).

The app is a PURE READER of language packs: it reads catalogs from ``src/lang-packs``
(where ``SettingsConstants.get_catalog_root()`` points on CPython), staged there by the
sibling ``seedsigner-language-packs`` checkout's builder:

    ../seedsigner-language-packs/scripts/build_packs.sh --out-dir "$PWD/src/lang-packs"

There is NO bundled-``.mo`` fallback. Tests that need real catalogs must SKIP when
``src/lang-packs`` is empty (a clean clone / an English-only build), using
``packs_available()`` — never fail. The separate English-only-without-packs invariant
test covers the empty case.
"""
import os

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
CATALOG_ROOT = os.path.join(_REPO_ROOT, "src", "lang-packs")


def packs_available():
    """True if ``src/lang-packs`` holds at least one ``<locale>/LC_MESSAGES/messages.mo``."""
    try:
        entries = os.listdir(CATALOG_ROOT)
    except OSError:
        return False
    for loc in entries:
        if os.path.isfile(os.path.join(CATALOG_ROOT, loc, "LC_MESSAGES", "messages.mo")):
            return True
    return False


def resolve_catalog_root():
    """Absolute path of the staged pack root (``src/lang-packs``). No fallback."""
    return CATALOG_ROOT
