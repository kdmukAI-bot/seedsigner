from unittest.mock import patch

from base import BaseTest
from seedsigner.models.settings_definition import SettingsConstants


class TestSettingsDefinition(BaseTest):
    @classmethod
    def setup_class(cls):
        super().setup_class()


    def test__get_detected_languages(self):
        """ Should auto-detect onboard languages based on the supported locales list """
        detected_languages = [lang_tuple[0] for lang_tuple in SettingsConstants.get_detected_languages()]

        # Find an unused language code; avoiding hard coding a language code to keep
        # this test future proof.
        absent_language_code = None
        for language_code in SettingsConstants.ALL_LOCALES.keys():
            if language_code not in detected_languages:
                absent_language_code = language_code
                break
        
        # Should only fail if we've absolutely crushed the global translations!!!
        assert absent_language_code is not None

        # get_detected_languages walks the fixed l10n/<locale>/LC_MESSAGES/*.mo layout
        # via os.listdir (os.walk is absent on MicroPython). Mock that traversal to
        # report a tree containing only "en" plus the otherwise-absent language code,
        # each with a .mo file present.
        def mocked_listdir(path):
            if path.endswith("l10n"):
                return ["en", absent_language_code]
            if path.endswith("LC_MESSAGES"):
                return ["messages.po", "messages.mo"]
            raise FileNotFoundError(path)

        with patch("os.listdir", side_effect=mocked_listdir):
            # Recheck w/our mocked dir listing:
            detected_languages = [lang_tuple[0] for lang_tuple in SettingsConstants.get_detected_languages()]
            assert absent_language_code in detected_languages
