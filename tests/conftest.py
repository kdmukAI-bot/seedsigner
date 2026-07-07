"""Test-suite catalog wiring.

The app is a pure reader: it resolves translation catalogs from the pack root
(``SettingsConstants.get_catalog_root()`` → ``"lang-packs"`` / ``"/sd"``), staged into
``src/lang-packs`` by the sibling ``seedsigner-language-packs`` builder. That
CWD-relative root doesn't line up with the pytest working dir, so this points
``get_catalog_root()`` at the absolute staged pack root. There is NO bundled-``.mo``
fallback: with no packs staged, the real-catalog tests SKIP (via
``langpack_catalog.packs_available``) and the English-only invariant test covers the
empty case.

Session-scoped + autouse so it is active before the first ``Settings`` init, which
binds gettext's ``localedir`` to ``get_catalog_root()``.
"""
import pytest

from seedsigner.models.settings_definition import SettingsConstants

from langpack_catalog import resolve_catalog_root


_CATALOG_ROOT = resolve_catalog_root()


@pytest.fixture(scope="session", autouse=True)
def _catalog_root_for_tests():
    """Point the catalog root at the resolved test catalog root (packs / submodule)."""
    original = SettingsConstants.get_catalog_root.__func__
    SettingsConstants.get_catalog_root = classmethod(lambda cls: _CATALOG_ROOT)
    try:
        yield
    finally:
        SettingsConstants.get_catalog_root = classmethod(original)
