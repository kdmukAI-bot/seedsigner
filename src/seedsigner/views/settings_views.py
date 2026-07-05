import logging
from seedsigner.compat.l10n import gettext as _

from seedsigner.gui.constants import GUIConstants, SeedSignerIconConstants, StatusType, ButtonStyle
from seedsigner.models.settings import Settings, SettingsConstants, SettingsDefinition

from .view import ButtonOption, Destination, MainMenuView, RET_CODE__BACK_BUTTON, View

logger = logging.getLogger(__name__)



class SettingsMenuView(View):
    ADVANCED = ButtonOption("Advanced", right_icon_name=SeedSignerIconConstants.CHEVRON_RIGHT)
    HARDWARE = ButtonOption("Hardware", right_icon_name=SeedSignerIconConstants.CHEVRON_RIGHT)
    IO_TEST = ButtonOption("I/O test")
    DONATE = ButtonOption("Donate")
    VERSION = ButtonOption("Version")

    def __init__(self, visibility: str = SettingsConstants.VISIBILITY__GENERAL, selected_attr: str = None, selected_button_option: ButtonOption = None, initial_scroll: int = 0):
        super().__init__()
        self.visibility = visibility
        self.selected_attr = selected_attr
        self.selected_button_option = selected_button_option

        # Used to preserve the rendering position in the list
        self.initial_scroll = initial_scroll


    def run(self):
        settings_entries = SettingsDefinition.get_settings_entries(
            visibility=self.visibility
        )
        button_data: list[ButtonOption] = [ButtonOption(e.display_name) for e in settings_entries]

        if self.visibility == SettingsConstants.VISIBILITY__GENERAL:
            title = _("Settings")

            # Set up the next nested level of menuing
            button_data.append(self.ADVANCED)
            next_destination = Destination(SettingsMenuView, view_args={"visibility": SettingsConstants.VISIBILITY__ADVANCED})

            button_data.append(self.IO_TEST)
            button_data.append(self.DONATE)
            button_data.append(self.VERSION)

        elif self.visibility == SettingsConstants.VISIBILITY__ADVANCED:
            title = _("Advanced")

            # The hardware options nest below "Advanced"
            button_data.append(self.HARDWARE)
            next_destination = Destination(SettingsMenuView, view_args={"visibility": SettingsConstants.VISIBILITY__HARDWARE})

        elif self.visibility == SettingsConstants.VISIBILITY__HARDWARE:
            title = _("Hardware")
            next_destination = None

        elif self.visibility == SettingsConstants.VISIBILITY__DEVELOPER:
            title = _("Dev Options")
            next_destination = None

        selected_button = 0
        if self.selected_button_option:
            selected_button = button_data.index(self.selected_button_option)
        elif self.selected_attr:
            for i, entry in enumerate(settings_entries):
                if entry.attr_name == self.selected_attr:
                    selected_button = i
                    break

        selected_menu_num = self.run_button_list_screen(
            title=title,
            is_button_text_centered=False,
            button_data=button_data,
            selected_button=selected_button,
        )

        # The native button_list_screen restores position via selected_button
        # (initial_selected_index); it exposes no PIL pixel-scroll to read back, so
        # the parent_initial_scroll plumbing below is inert on the native path.
        initial_scroll = 0

        if selected_menu_num == RET_CODE__BACK_BUTTON:
            if self.visibility == SettingsConstants.VISIBILITY__GENERAL:
                return Destination(MainMenuView)
            elif self.visibility == SettingsConstants.VISIBILITY__ADVANCED:
                return Destination(SettingsMenuView)
            else:
                return Destination(SettingsMenuView, view_args={"visibility": SettingsConstants.VISIBILITY__ADVANCED})
        
        if button_data[selected_menu_num] == self.ADVANCED:
            return next_destination

        elif button_data[selected_menu_num] == self.HARDWARE:
            return next_destination

        elif button_data[selected_menu_num] == self.IO_TEST:
            return Destination(IOTestView)

        elif button_data[selected_menu_num] == self.DONATE:
            return Destination(DonateView)
        
        elif button_data[selected_menu_num] == self.VERSION:
            return Destination(VersionView)

        elif settings_entries[selected_menu_num].attr_name == SettingsConstants.SETTING__LOCALE:
            return Destination(LocaleSelectionView)

        else:
            return Destination(SettingsEntryUpdateSelectionView, view_args=dict(attr_name=settings_entries[selected_menu_num].attr_name, parent_initial_scroll=initial_scroll))



class LocaleSelectionView(View):
    def run(self):
        # The native language-selection picker renders each language's name in its own
        # script (live text for Latin, a pre-rendered endonym image for everything
        # else), so it supersedes the old per-button font override that the LVGL runner
        # could not forward. The runner wrappers degrade to no-ops off-device (dev/CI).
        from seedsigner.gui.lvgl_screen_runner import (
            discover_locale_packs,
            list_available_locales,
            set_locale_fonts,
        )

        cur_language_code = self.settings.get_value(SettingsConstants.SETTING__LOCALE)

        # Build the picker rows: the app's onboard (.mo-present) locales first, then
        # any SD-delivered packs the native layer discovers, deduped by code. Each row
        # carries a clean English + native name; the picker decides live-text vs.
        # endonym-image per row from the native name's script.
        rows = []
        seen = set()
        for language_code, _display_name in SettingsConstants.get_detected_languages():
            english, native = SettingsConstants.get_locale_names(language_code)
            rows.append({"code": language_code, "english": english, "native": native})
            seen.add(language_code)

        discover_locale_packs()
        for pack in list_available_locales():
            code = pack.get("code")
            if not code or code in seen:
                continue
            english, native = SettingsConstants.get_locale_names(code, endonym=pack.get("endonym"))
            rows.append({"code": code, "english": english, "native": native})
            seen.add(code)

        selected_index = self.run_locale_picker_screen(
            title=_(SettingsDefinition.get_settings_entry(attr_name=SettingsConstants.SETTING__LOCALE).display_name),
            active_locale=cur_language_code,
            rows=rows,
        )

        if selected_index == RET_CODE__BACK_BUTTON:
            return Destination(SettingsMenuView)

        # Persist + apply. set_value() already re-runs load_locale() (the text catalog:
        # LANGUAGE env on Pi / the .mo reader on device); set_locale_fonts() swaps the
        # native LVGL font pack so the new script actually renders.
        new_locale = rows[selected_index]["code"]
        self.settings.set_value(SettingsConstants.SETTING__LOCALE, new_locale)
        set_locale_fonts(new_locale)

        return Destination(SettingsMenuView)



class SettingsEntryUpdateSelectionView(View):
    """
        Handles changes to all selection-type settings (Multiselect, SELECT_1,
        Enabled/Disabled, etc).
    """
    def __init__(self, attr_name: str, parent_initial_scroll: int = 0, selected_button: int = None, blocking_view: View = None, unblocking_view: View = None):
        super().__init__()
        self.settings_entry = SettingsDefinition.get_settings_entry(attr_name)
        self.selected_button = selected_button
        self.parent_initial_scroll = parent_initial_scroll
        # If the setting remains unchanged, navigation should return to blocking_view (if set)
        self.blocking_view = blocking_view
        # unblocking_view is an optional target to navigate to once the setting actually changes.
        self.unblocking_view = unblocking_view


    def run(self):
        initial_value = self.settings.get_value(self.settings_entry.attr_name)
        button_data = []
        checked_buttons = []
        for i, value in enumerate(self.settings_entry.selection_options):
            if type(value) == tuple:
                value, display_name = value
            else:
                display_name = value
            button_data.append(ButtonOption(display_name))

            if (type(initial_value) == list and value in initial_value) or value == initial_value:
                checked_buttons.append(i)

                if self.selected_button is None:
                    # Highlight the selection (for multiselect highlight the first
                    # selected option).
                    self.selected_button = i
        
        if self.selected_button is None:
            self.selected_button = 0
            
        # Single-select settings render as a checked-selection list (one checkmark);
        # multiselect settings render as checkboxes (toggle several). The View owns the
        # single-vs-multi decision (derived from the SettingsEntry type).
        if self.settings_entry.type == SettingsConstants.TYPE__MULTISELECT:
            button_style = ButtonStyle.CHECKBOX
        else:
            button_style = ButtonStyle.CHECKED_SELECTION

        # The setting's display name (and optional help text) was rendered above the
        # options list by the PIL screen; forward it as the native intro text block.
        text = _(self.settings_entry.display_name)
        if self.settings_entry.help_text:
            text += "\n" + _(self.settings_entry.help_text)

        ret_value = self.run_button_list_screen(
            title=_("Settings"),
            text=text,
            button_data=button_data,
            selected_button=self.selected_button,
            checked_buttons=checked_buttons,
            button_style=button_style,
            is_bottom_list=True,
        )

        destination = None
        settings_menu_view_destination = Destination(
            SettingsMenuView,
            view_args={
                "visibility": self.settings_entry.visibility,
                "selected_attr": self.settings_entry.attr_name,
                "initial_scroll": self.parent_initial_scroll,
            }
        )

        if ret_value == RET_CODE__BACK_BUTTON:
            if self.settings_entry.type == SettingsConstants.TYPE__MULTISELECT:
                # After the user finishes toggling multiselect options, initial_value will
                # have their final selections when they hit BACK to exit. All current
                # multiselect settings require at least one option to be selected.
                if not initial_value:
                    return Destination(SettingsSelectionRequiredWarningView, view_args={"attr_name": self.settings_entry.attr_name})

            if self.blocking_view:
                return Destination(self.blocking_view, clear_history=True)
            return settings_menu_view_destination

        value = self.settings_entry.get_selection_option_value(ret_value)

        if self.settings_entry.type == SettingsConstants.TYPE__FREE_ENTRY:
            updated_value = ret_value
            destination = settings_menu_view_destination

        elif self.settings_entry.type == SettingsConstants.TYPE__MULTISELECT:
            updated_value = list(initial_value)
            if ret_value not in checked_buttons:
                # This is a new selection to add
                updated_value.append(value)
            else:
                # This is a de-select to remove
                updated_value.remove(value)

        else:
            # All other types are single selects (e.g. Enabled/Disabled, SELECT_1)
            if value == initial_value and not self.blocking_view:
                return settings_menu_view_destination
            else:
                updated_value = value

        self.settings.set_value(
            attr_name=self.settings_entry.attr_name,
            value=updated_value
        )

        if self.settings_entry.attr_name == SettingsConstants.SETTING__DISPLAY_CONFIGURATION:
            self.renderer.initialize_display()

        elif self.settings_entry.attr_name == SettingsConstants.SETTING__DISPLAY_COLOR_INVERTED:
            self.renderer.disp.invert(enabled=updated_value == SettingsConstants.OPTION__ENABLED)

        if destination:
            return destination
        
        # If this selection view was opened from a blocking flow (e.g. RemoveMicroSDWarningView),
        # prevent navigation away until the setting actually changes. If it hasn't changed,
        # return to the blocking view so it can re-evaluate the state.
        if self.blocking_view:
            current_value = self.settings.get_value(self.settings_entry.attr_name)
            if current_value == initial_value:
                return Destination(self.blocking_view, clear_history=True)
            elif self.unblocking_view:
                return Destination(self.unblocking_view, clear_history=True)

        # All selects stay in place; re-initialize where in the list we left off
        self.selected_button = ret_value

        return Destination(SettingsEntryUpdateSelectionView, view_args=dict(attr_name=self.settings_entry.attr_name, parent_initial_scroll=self.parent_initial_scroll, selected_button=self.selected_button, blocking_view=self.blocking_view, unblocking_view=self.unblocking_view), skip_current_view=True)



class SettingsSelectionRequiredWarningView(View):
    def __init__(self, attr_name: str):
        super().__init__()
        self.settings_entry = SettingsDefinition.get_settings_entry(attr_name)


    def run(self):
        # TRANSLATOR_NOTE: Title of a warning dialog when configuring a setting that requires at least one option to be selected.
        title = _("Selection Required")

        # TRANSLATOR_NOTE: The name of the setting being configured (e.g. "Script types") will be inserted.
        text = _("At least one option must be selected for \"{}\".").format(self.settings_entry.display_name)

        # TRANSLATOR_NOTE: Text for the button that returns the user to the setting configuration screen.
        button_text = _("Return to setting")

        self.run_status_screen(
            status_type=StatusType.WARNING,
            title=title,
            show_back_button=False,
            text=text,
            button_data=[ButtonOption(button_text)],
        )

        return Destination(SettingsEntryUpdateSelectionView, view_args=dict(attr_name=self.settings_entry.attr_name))



class SettingsIngestSettingsQRView(View):
    def __init__(self, data: str):
        from seedsigner.hardware.microsd import MicroSD
        super().__init__()

        # May raise an Exception which will bubble up to the Controller to display to the
        # user.
        self.config_name, settings_update_dict = Settings.parse_settingsqr(data)

        changes_display_driver = (
            SettingsConstants.SETTING__DISPLAY_CONFIGURATION in settings_update_dict and
            self.settings.get_value(SettingsConstants.SETTING__DISPLAY_CONFIGURATION) != settings_update_dict[SettingsConstants.SETTING__DISPLAY_CONFIGURATION])
            
        self.settings.update(settings_update_dict)

        if changes_display_driver:
            self.renderer.initialize_display()

        if MicroSD.get_instance().is_inserted and self.settings.get_value(SettingsConstants.SETTING__PERSISTENT_SETTINGS) == SettingsConstants.OPTION__ENABLED:
            self.status_message = _("Persistent Settings enabled. Settings saved to SD card.")
        else:
            self.status_message = _("Settings updated in temporary memory")


    def run(self):
        text = ""
        if self.config_name:
            # User-supplied name from the scanned SettingsQR; don't translate.
            text = f'"{self.config_name}"\n\n'
        # status_message was already localized in __init__.
        text += self.status_message

        self.run_button_list_screen(
            title=_("Settings QR"),
            text=text,
            button_data=[ButtonOption("Home")],
            show_back_button=False,
            is_bottom_list=True,
        )

        # Only one exit point
        return Destination(MainMenuView)



"""****************************************************************************
    Misc
****************************************************************************"""
class IOTestView(View):
    def run(self):
        from seedsigner.gui.screens import settings_screens

        self.run_screen(settings_screens.IOTestScreen)

        return Destination(SettingsMenuView)



class DonateView(View):
    def run(self):
        from seedsigner.gui.screens import settings_screens

        self.run_screen(settings_screens.DonateScreen)

        return Destination(SettingsMenuView)



class VersionView(View):
    def run(self):
        from seedsigner.helpers.version import Version

        version_fork = Version.get_version_fork()
        short_commit_hash = Version.get_short_commit_hash()

        if Version.is_release_image():
            # Don't display fork name or commit hash for release images
            version_fork = None
            short_commit_hash = None

        self.run_screen(
            settings_screens.VersionScreen,
            version_name=Version.get_version_name(),
            version_fork=version_fork,
            version_timestamp=Version.get_version_timestamp(),
            short_commit_hash=short_commit_hash,
        )

        return Destination(SettingsMenuView)