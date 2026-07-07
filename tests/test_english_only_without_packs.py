"""Invariant: with NO language packs present, the app runs ENGLISH-ONLY.

The finalized pure-reader model requires that an app with an empty ``src/lang-packs``
starts and renders English on every target — never an error, never "translated text
with no font." That is what makes a streamlined English-only (zero-pack) build a
supported config and keeps the clean-clone dev path pristine.

These force an empty catalog root (overriding the suite's staged-packs root) and assert:
no locale but English is detected, gettext passes English through even with a non-English
locale selected, and the language picker offers English only.
"""
from gettext import gettext as _
from unittest.mock import patch

from base import BaseTest
from seedsigner.models.settings import Settings
from seedsigner.models.settings_definition import SettingsConstants
from seedsigner.views import settings_views
from seedsigner.views.view import RET_CODE__BACK_BUTTON, View


def _empty_root(tmp_path):
    """Patch get_catalog_root() to an empty dir (no packs)."""
    return patch.object(
        SettingsConstants, "get_catalog_root",
        classmethod(lambda cls: str(tmp_path)),
    )


class TestEnglishOnlyWithoutPacks(BaseTest):
    def test_only_english_is_detected(self, tmp_path):
        with _empty_root(tmp_path):
            detected = SettingsConstants.get_detected_languages()
        assert detected == [
            (SettingsConstants.LOCALE__ENGLISH,
             SettingsConstants.ALL_LOCALES[SettingsConstants.LOCALE__ENGLISH]),
        ]

    def test_gettext_passes_english_through(self, tmp_path):
        with _empty_root(tmp_path):
            # Re-init Settings so gettext's localedir binds to the empty root.
            BaseTest.reset_settings()
            settings = Settings.get_instance()
            settings.set_value(SettingsConstants.SETTING__LOCALE, SettingsConstants.LOCALE__SPANISH)
            # No es catalog to load -> English passes through despite Spanish selected.
            assert _("Home") == "Home"

    def test_picker_offers_english_only(self, tmp_path):
        captured = {}

        def fake_run_screen(view, screen, **kwargs):
            captured.update(kwargs)
            return RET_CODE__BACK_BUTTON  # back out; we only inspect the rows

        with _empty_root(tmp_path), \
             patch.object(View, "run_screen", autospec=True, side_effect=fake_run_screen), \
             patch("seedsigner.gui.lvgl_screen_runner.discover_locale_packs", return_value=0), \
             patch("seedsigner.gui.lvgl_screen_runner.list_available_locales", return_value=[]):
            settings_views.LocaleSelectionView().run()

        assert [row["code"] for row in captured["rows"]] == [SettingsConstants.LOCALE__ENGLISH]
