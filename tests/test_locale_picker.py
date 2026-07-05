"""Tests for the B3 language-selection picker wiring.

Covers the pure pieces (baked-floor coverage, cfg row shaping, graceful-degrade
runner wrappers) plus the LocaleSelectionView row assembly + apply behavior.
"""
from unittest.mock import patch

# base sets up the sys.modules mocks that make seedsigner importable on the host.
from base import BaseTest

from seedsigner.gui.lvgl_screen_runner import (
    LOCALE_PACK_DIR,
    _assemble_cfg,
    _serialize_locale_row,
    discover_locale_packs,
    endonym_needs_image,
    list_available_locales,
    set_locale_fonts,
)


# ---------------------------------------------------------------------------
# Baked-floor coverage: live-text vs. endonym-image decision
# ---------------------------------------------------------------------------

LIVE_TEXT_NATIVES = [
    "English",
    "Español",            # ñ (Latin-1)
    "Čeština",            # Č (Latin Extended-A)
    "Türkçe",             # ü, ç (Latin-1)
    "Català",             # à (Latin-1)
    "Português do Brasil",
    "Norsk",
    "Bahasa Indonesia",
    "Deutsch",
    "Français",
]

IMAGE_NATIVES = [
    "日本語",              # CJK
    "한국어",              # Hangul
    "简体中文",            # CJK
    "Русский",            # Cyrillic
    "Ελληνικά",           # Greek
    "فارسی",              # Arabic
    "हिन्दी",              # Devanagari
    "ไทย",                # Thai
    "اردو",               # Arabic (Urdu)
    "Tiếng Việt",         # Latin, but ế/ệ live in Latin Extended Additional (not baked)
]


def test_live_text_natives_render_live():
    for native in LIVE_TEXT_NATIVES:
        assert endonym_needs_image(native) is False, native


def test_non_floor_natives_need_image():
    for native in IMAGE_NATIVES:
        assert endonym_needs_image(native) is True, native


def test_vietnamese_is_an_image_row():
    # The documented subtlety: Vietnamese's name is Latin but uses Latin Extended
    # Additional glyphs that aren't in the baked floor, so it's an image row.
    assert endonym_needs_image("Tiếng Việt") is True


# ---------------------------------------------------------------------------
# Picker cfg row shaping
# ---------------------------------------------------------------------------

def test_serialize_live_row_omits_image():
    row = _serialize_locale_row({"code": "es", "english": "Spanish", "native": "Español"})
    assert row == {"locale": "es", "english": "Spanish", "native": "Español"}
    assert "image" not in row


def test_serialize_image_row_sets_image_true():
    row = _serialize_locale_row({"code": "ja", "english": "Japanese", "native": "日本語"})
    assert row["locale"] == "ja"
    assert row["image"] is True


def test_assemble_cfg_builds_picker_shape():
    cfg = _assemble_cfg({
        "title": "Language",
        "show_back_button": True,
        "active_locale": "en",
        "rows": [
            {"code": "en", "english": "English", "native": "English"},
            {"code": "ru", "english": "Russian", "native": "Русский"},
        ],
    })
    assert cfg["top_nav"] == {"title": "Language", "show_back_button": True}
    assert cfg["active_locale"] == "en"
    assert cfg["font_dir"] == LOCALE_PACK_DIR
    assert cfg["rows"][0] == {"locale": "en", "english": "English", "native": "English"}
    assert cfg["rows"][1]["locale"] == "ru"
    assert cfg["rows"][1]["image"] is True


# ---------------------------------------------------------------------------
# Runner wrappers degrade gracefully with no native module (dev/CI host)
# ---------------------------------------------------------------------------

def test_discovery_wrappers_degrade_without_native_module():
    # No seedsigner_lvgl_screens on the host -> ImportError inside ensure_lvgl_runtime
    # -> the wrappers return their empty defaults rather than raising.
    assert discover_locale_packs() == 0
    assert list_available_locales() == []
    assert set_locale_fonts("es") is False


# ---------------------------------------------------------------------------
# LocaleSelectionView row assembly + apply
# ---------------------------------------------------------------------------

class TestLocaleSelectionView(BaseTest):
    def test_builds_rows_and_applies_selection(self):
        from seedsigner.views import settings_views
        from seedsigner.views.view import View
        from seedsigner.models.settings_definition import SettingsConstants

        captured = {}

        def fake_run_screen(view, screen, **kwargs):
            captured["screen"] = screen
            captured.update(kwargs)
            return 1  # pick row index 1 (first non-English)

        with patch.object(View, "run_screen", autospec=True, side_effect=fake_run_screen), \
             patch("seedsigner.gui.lvgl_screen_runner.discover_locale_packs", return_value=0), \
             patch("seedsigner.gui.lvgl_screen_runner.list_available_locales", return_value=[]), \
             patch("seedsigner.gui.lvgl_screen_runner.set_locale_fonts") as mock_fonts:
            dest = settings_views.LocaleSelectionView().run()

        assert captured["screen"] == "locale_picker_screen"
        rows = captured["rows"]
        # English is always the first row; the current locale is handed to the picker.
        assert rows[0]["code"] == SettingsConstants.LOCALE__ENGLISH
        assert captured["active_locale"] == SettingsConstants.LOCALE__ENGLISH
        # Each row carries clean english + native names.
        assert set(rows[0].keys()) == {"code", "english", "native"}

        # The selected row's locale is persisted and its font pack applied.
        new_code = rows[1]["code"]
        assert self.settings.get_value(SettingsConstants.SETTING__LOCALE) == new_code
        mock_fonts.assert_called_once_with(new_code)
        assert dest.View_cls is settings_views.SettingsMenuView

    def test_back_button_returns_to_settings_menu(self):
        from seedsigner.views import settings_views
        from seedsigner.views.view import View
        from seedsigner.gui.screens.screen import RET_CODE__BACK_BUTTON
        from seedsigner.models.settings_definition import SettingsConstants

        before = self.settings.get_value(SettingsConstants.SETTING__LOCALE)

        def fake_run_screen(view, screen, **kwargs):
            return RET_CODE__BACK_BUTTON

        with patch.object(View, "run_screen", autospec=True, side_effect=fake_run_screen), \
             patch("seedsigner.gui.lvgl_screen_runner.discover_locale_packs", return_value=0), \
             patch("seedsigner.gui.lvgl_screen_runner.list_available_locales", return_value=[]), \
             patch("seedsigner.gui.lvgl_screen_runner.set_locale_fonts") as mock_fonts:
            dest = settings_views.LocaleSelectionView().run()

        # Backing out changes nothing and does not touch the font pack.
        assert self.settings.get_value(SettingsConstants.SETTING__LOCALE) == before
        mock_fonts.assert_not_called()
        assert dest.View_cls is settings_views.SettingsMenuView

    def test_sd_discovered_pack_is_added_and_deduped(self):
        from seedsigner.views import settings_views
        from seedsigner.views.view import View
        from seedsigner.models.settings_definition import SettingsConstants

        captured = {}

        # A brand-new SD locale not baked into the app, plus a duplicate of an onboard
        # one (es) that must be deduped.
        fake_packs = [
            {"code": "es", "endonym": "Español", "image": None, "has_image": False},
            {"code": "xx", "endonym": "Xhosa-ish", "image": None, "has_image": False},
        ]

        def fake_run_screen(view, screen, **kwargs):
            captured.update(kwargs)
            return 0  # pick English (index 0); this test only inspects the rows

        with patch.object(View, "run_screen", autospec=True, side_effect=fake_run_screen), \
             patch("seedsigner.gui.lvgl_screen_runner.discover_locale_packs", return_value=len(fake_packs)), \
             patch("seedsigner.gui.lvgl_screen_runner.list_available_locales", return_value=fake_packs), \
             patch("seedsigner.gui.lvgl_screen_runner.set_locale_fonts"):
            settings_views.LocaleSelectionView().run()

        codes = [r["code"] for r in captured["rows"]]
        # es appears exactly once (onboard), xx is appended with its manifest endonym.
        assert codes.count("es") == 1
        assert "xx" in codes
        xx_row = next(r for r in captured["rows"] if r["code"] == "xx")
        assert xx_row["native"] == "Xhosa-ish"  # falls back to the manifest endonym
