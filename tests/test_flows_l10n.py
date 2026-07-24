from gettext import gettext as _

import pytest

# Must import test base before the Controller
from base import FlowTest, FlowStep

from langpack_catalog import packs_available
from seedsigner.views.view import RET_CODE__BACK_BUTTON, ButtonOption
from seedsigner.models.settings_definition import SettingsConstants, SettingsDefinition
from seedsigner.views import settings_views
from seedsigner.views.view import MainMenuView



class TestL10nFlows(FlowTest):
    # Selecting a non-English locale needs its pack catalog staged in src/lang-packs.
    @pytest.mark.skipif(
        not packs_available(),
        reason="no packs staged in src/lang-packs "
               "(build: ../seedsigner-language-packs/scripts/build_packs.sh --out-dir src/lang-packs)",
    )
    def test_change_locale(self):
        settings_entry = SettingsDefinition.get_settings_entry(SettingsConstants.SETTING__LOCALE)

        # Initially we get English
        assert _(MainMenuView.SCAN.button_label) == "Scan"

        self.run_sequence([
            FlowStep(MainMenuView, button_data_selection=MainMenuView.SETTINGS),
            FlowStep(settings_views.SettingsMenuView, button_data_selection=ButtonOption(settings_entry.display_name)),
            FlowStep(settings_views.LocaleSelectionView, screen_return_value=1),  # Any index > 0 (English)
        ])

        # Now we don't get English
        assert _(MainMenuView.SCAN.button_label) != "Scan"
