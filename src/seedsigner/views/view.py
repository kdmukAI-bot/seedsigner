import logging
from seedsigner.compat import IS_MICROPYTHON
from seedsigner.compat.l10n import gettext as _

from seedsigner.helpers.l10n import mark_for_translation as _mft
from seedsigner.gui.constants import SeedSignerIconConstants
from seedsigner.models.settings import Settings, SettingsConstants
from seedsigner.models.settings_definition import SettingsDefinition
from seedsigner.models.threads import BaseThread

logger = logging.getLogger(__name__)


# Must be huge numbers to avoid conflicting with the selected_button returned by the
#   screens with buttons.
RET_CODE__BACK_BUTTON = 1000
RET_CODE__POWER_BUTTON = 1001



class ButtonOption:
    """
    Note: The babel config in setup.cfg will extract the `button_label` string for translation
    """
    def __init__(self,
                 button_label: str,
                 icon_name: str = None,
                 icon_color: str = None,
                 right_icon_name: str = None,
                 button_label_color: str = None,
                 return_data = None,
                 active_button_label: str = None,  # Changes displayed button label when button is active
                 font_name: str = None,            # Optional override
                 font_size: int = None):           # Optional override
        self.button_label = button_label
        self.icon_name = icon_name
        self.icon_color = icon_color
        self.right_icon_name = right_icon_name
        self.button_label_color = button_label_color
        self.return_data = return_data
        self.active_button_label = active_button_label
        self.font_name = font_name
        self.font_size = font_size


    def __eq__(self, other):
        # Formerly a @dataclass; stock MicroPython has no `dataclasses` module. The
        # synthesized value-equality is load-bearing: the flow-test harness locates a
        # selection via `button_data.index(ButtonOption(...))`, comparing distinct
        # instances field-by-field. Defining __eq__ (and leaving __hash__ unset, which
        # makes instances unhashable) reproduces exactly what @dataclass gave us. The
        # class-identity guard keeps ButtonOptionWithoutTranslation distinct from
        # ButtonOption, as the per-class dataclass __eq__ did.
        if other.__class__ is not self.__class__:
            return NotImplemented
        return (
            self.button_label, self.icon_name, self.icon_color, self.right_icon_name,
            self.button_label_color, self.return_data, self.active_button_label,
            self.font_name, self.font_size,
        ) == (
            other.button_label, other.icon_name, other.icon_color, other.right_icon_name,
            other.button_label_color, other.return_data, other.active_button_label,
            other.font_name, other.font_size,
        )


    def to_lvgl(self):
        """Serialize this option for a native LVGL screen's button list.

        Text-only for now (the translated label); per-button icon support is
        pending — see the seedsigner-lvgl-screens ``button_list_screen`` TODO.
        Mirrors the PIL ``Button`` render path, which wraps the label in ``_()``.
        """
        return _(self.button_label)



class ButtonOptionWithoutTranslation(ButtonOption):
    """
    Same as ButtonOption but does NOT translate button_label or active_button_label.
    The labels are also not extracted for translation by babel.
    """

    def to_lvgl(self):
        # No translation, matching the PIL render path for this subclass.
        return self.button_label



class BackStackView:
    """
        Empty class that just signals to the Controller to pop the most recent View off
        the back_stack.
    """
    pass



"""
    Views contain the biz logic to handle discrete tasks, exactly analogous to a Flask
    request/response function or a Django View. Each page/screen displayed to the user
    should be implemented in its own View.

    In a web context, the View would prepare data for the html/css/js presentation
    templates. We have to implement our own presentation layer (implemented as `Screen`
    objects). For the sake of code cleanliness and separation of concerns, the View code
    should not know anything about pixel-level rendering.

    Sequences that require multiple pages/screens should be implemented as a series of
    separate Views. Exceptions can be made for complex interactive sequences, but in
    general, if your View is instantiating multiple Screens, you're probably putting too
    much functionality in that View.

    As with http requests, Views can receive input vars to inform their behavior. Views
    can also prepare the next set of vars to set up the next View that should be
    displayed (akin to Flask's `return redirect(url, param1=x, param2=y))`).

    Navigation guidance:
    "Next" - Continue to next step
    "Done" - End of flow, return to entry point (non-destructive)
    "OK/Close" - Exit current screen (non-destructive)
    "Cancel" - End task and return to entry point (destructive)
"""
class View:
    def _initialize(self):
        """
        Sets up the View's shared instance variables. A View that takes no constructor
        args inherits View.__init__() (which calls this directly); a View that takes args
        defines an explicit __init__() that assigns them and then calls __post_init__().
        __post_init__() is the override hook those subclasses extend (via super()) to run
        their own setup before delegating here.
        """
        # Import here to avoid circular imports
        from seedsigner.controller import Controller
        if IS_MICROPYTHON:
            # Stock MicroPython has no PIL and LVGL owns the panel natively, so
            # gui.renderer (PIL at module top) can't load. Use the PIL-free
            # stand-in instead.
            from seedsigner.gui.lvgl_renderer import LvglRenderer as Renderer
        else:
            from seedsigner.gui.renderer import Renderer

        self.controller: Controller = Controller.get_instance()
        self.settings = Settings.get_instance()

        # TODO: Pull all rendering-related code out of Views and into gui.screens implementations
        self.renderer = Renderer.get_instance()
        self.canvas_width = self.renderer.canvas_width
        self.canvas_height = self.renderer.canvas_height

        self.screen = None

        self._redirect: 'Destination' = None
        self.is_screensaver_allowed = True


    def __init__(self):
        self._initialize()


    def __post_init__(self):
        self._initialize()


    @property
    def has_redirect(self) -> bool:
        if not hasattr(self, '_redirect'):
            # Easy for a View to forget to call super().__init__()
            raise Exception(f"{self.__class__.__name__} did not call super().__init__()")
        return self._redirect is not None


    def set_redirect(self, destination: 'Destination'):
        """
        Enables early `__init__()` / `__post_init__()` logic to redirect away from the
        current View.

        Set a redirect Destination and then immediately `return` to exit `__init__()` or
        `__post_init__()`. When the `Destination.run()` is called, it will see the redirect
        and immediately return that new Destination to the Controller without running
        the View's `run()`.
        """
        # Always insure skip_current_view is set for a redirect
        destination.skip_current_view = True
        self._redirect = destination


    def get_redirect(self) -> 'Destination':
        return self._redirect


    def run_screen(self, screen, *, lvgl_cfg=None, allow_screensaver=True, **kwargs) -> int | str:
        """
            Dispatch to a Screen implementation, run its interactive display, and
            return the user's input.

            * A PIL Screen *class* (a ``type``) is instantiated with ``**kwargs``
              and displayed — the long-standing CPython path, unchanged for every
              existing call site.
            * An LVGL screen, identified by its native screen-function *name* (a
              ``str``), is run through the LVGL screen runner. ``lvgl_cfg`` is the
              JSON-style config dict handed to the native screen; ``allow_screensaver``
              lets a screen opt out of the idle screensaver (e.g. camera scanning).

            ``isinstance(screen, type)`` is the discriminator (MicroPython-safe):
            classes take the PIL branch, strings the LVGL branch.
        """
        if isinstance(screen, type):
            if IS_MICROPYTHON:
                # A PIL Screen *class* can't render on MicroPython (no PIL). Show
                # the recoverable not-implemented notice instead of instantiating
                # it, so a not-yet-migrated screen can't kill the session. This is
                # the defensive path; the dominant one is a View whose lazy PIL
                # import fails before run_screen is reached — caught by the
                # Controller and routed to NotYetImplementedView.
                from seedsigner.gui.lvgl_screen_runner import run_lvgl_screen
                return run_lvgl_screen(
                    self.renderer,
                    "large_icon_status_screen",
                    cfg=not_implemented_lvgl_cfg(_mft("This is still on our to-do list!")),
                    allow_screensaver=False,
                )
            self.screen = screen(**kwargs)
            return self.screen.display()

        # LVGL screen (by name). A plain ButtonOption menu (button_list_screen) is
        # dispatched with the same kwargs the PIL ButtonListScreen took; assemble its
        # native cfg here so Views never build it. The title+button_data signature is
        # the discriminator: main_menu_screen passes button_data (for the flow harness)
        # but no title, so it correctly stays cfg-less.
        if lvgl_cfg is None and "title" in kwargs and "button_data" in kwargs:
            lvgl_cfg = button_list_lvgl_cfg(**kwargs)

        # Imported lazily so Views never pull the native module in, and so this stays
        # out of the module-level import graph that must load on MicroPython.
        from seedsigner.gui.lvgl_screen_runner import run_lvgl_screen
        return run_lvgl_screen(self.renderer, screen, cfg=lvgl_cfg, allow_screensaver=allow_screensaver)


    def run(self, **kwargs) -> 'Destination':
        raise Exception("Must implement in the child class")



class Destination:
    """
        Basic struct to pass back to the Controller to tell it which View the user should
        be presented with next.
    """
    def __init__(self,
                 View_cls: type[View],               # The target View to route to
                 view_args: dict = None,             # The input args required to instantiate the target View
                 skip_current_view: bool = False,    # The current View is just forwarding; omit current View from history
                 clear_history: bool = False):       # Optionally clears the back_stack to prevent "back"
        self.View_cls = View_cls
        self.view_args = view_args
        self.skip_current_view = skip_current_view
        self.clear_history = clear_history


    def __repr__(self):
        if self.View_cls is None:
            out = "None"
        else:
            out = self.View_cls.__name__
        if self.view_args:
            out += f"({self.view_args})"
        else:
            out += "()"
        if self.clear_history:
            out += f" | clear_history: {self.clear_history}"
        return out


    def _instantiate_view(self):
        if not self.view_args:
            # Can't unpack (**) None so we replace with an empty dict
            self.view_args = {}

        # Instantiate the `View_cls` with the `view_args` dict
        self.view = self.View_cls(**self.view_args)
    

    def _run_view(self):
        if self.view.has_redirect:
            return self.view.get_redirect()
        return self.view.run()


    def run(self):
        self._instantiate_view()
        return self._run_view()


    def __eq__(self, obj):
        """
            Equality test IGNORES the skip_current_view and clear_history options
        """
        return (isinstance(obj, Destination) and 
            obj.View_cls == self.View_cls and
            obj.view_args == self.view_args)
    

    def __ne__(self, obj):
        return not obj == self



#########################################################################################
#
# Root level Views don't have a sub-module home so they live at the top level here.
#
#########################################################################################
class MainMenuView(View):
    SCAN = ButtonOption("Scan", SeedSignerIconConstants.SCAN)
    SEEDS = ButtonOption("Seeds", SeedSignerIconConstants.SEEDS)
    TOOLS = ButtonOption("Tools", SeedSignerIconConstants.TOOLS)
    SETTINGS = ButtonOption("Settings", SeedSignerIconConstants.SETTINGS)

    def run(self):
        button_data = [self.SCAN, self.SEEDS, self.TOOLS, self.SETTINGS]
        # LVGL screen, dispatched by name: the native main_menu_screen owns its own
        # 2x2 grid labels/icons (and their localization), so no title/cfg is forwarded.
        # button_data stays for the index -> Destination mapping below (and the flow
        # harness), not for the native screen.
        selected_menu_num = self.run_screen("main_menu_screen", button_data=button_data)

        if selected_menu_num == RET_CODE__POWER_BUTTON:
            return Destination(PowerOptionsView)

        if button_data[selected_menu_num] == self.SCAN:
            from seedsigner.views.scan_views import ScanView
            return Destination(ScanView)
        
        elif button_data[selected_menu_num] == self.SEEDS:
            from seedsigner.views.seed_views import SeedsMenuView
            return Destination(SeedsMenuView)

        elif button_data[selected_menu_num] == self.TOOLS:
            from seedsigner.views.tools_views import ToolsMenuView
            return Destination(ToolsMenuView)

        elif button_data[selected_menu_num] == self.SETTINGS:
            from seedsigner.views.settings_views import SettingsMenuView
            return Destination(SettingsMenuView)



class PowerOptionsView(View):
    RESET = ButtonOption("Restart", SeedSignerIconConstants.RESTART)
    POWER_OFF = ButtonOption("Power off", SeedSignerIconConstants.POWER)

    def run(self):
        from seedsigner.gui.screens.screen import LargeButtonScreen

        button_data = [self.RESET, self.POWER_OFF]
        selected_menu_num = self.run_screen(
            LargeButtonScreen,
            title=_("Reset / Power"),
            show_back_button=True,
            button_data=button_data
        )

        if selected_menu_num == RET_CODE__BACK_BUTTON:
            return Destination(BackStackView)
        
        elif button_data[selected_menu_num] == self.RESET:
            return Destination(RestartView)
        
        elif button_data[selected_menu_num] == self.POWER_OFF:
            return Destination(PowerOffView)


class RestartView(View):

    def run(self):
        from seedsigner.gui.screens.screen import ResetScreen

        if not self.renderer.is_screenshot_generator:
            # We don't want the screenshot generator to actually try to do the restart
            RestartView.DoResetThread().start()

        self.run_screen(ResetScreen)


    class DoResetThread(BaseThread):
        def run(self):
            import os
            import sys
            import time

            logger.info("Restarting SeedSigner")
            # Give the screen just enough time to display the reset message before
            # exiting.
            time.sleep(0.25)

            # Flush any buffered data.
            sys.stdout.flush() 
            sys.stderr.flush()

            # Replace the current process with a new one.
            os.execv(sys.executable, [sys.executable] + sys.argv)



class PowerOffView(View):
    def run(self):
        from seedsigner.gui.screens.screen import PowerOffNotRequiredScreen
        self.run_screen(PowerOffNotRequiredScreen)
        return Destination(BackStackView)



def not_implemented_lvgl_cfg(text: str) -> dict:
    """Config for the native ``large_icon_status_screen`` rendered as the
    recoverable not-implemented notice on MicroPython.

    Shared by ``NotYetImplementedView`` and ``View.run_screen``'s MicroPython
    guard. Mirrors the PIL ``WarningScreen`` wording so the strings come from the
    same translation catalog entries.
    """
    return {
        "top_nav": {
            "title": _("Work In Progress"),
            "show_back_button": False,
            "show_power_button": False,
        },
        "status_type": "warning",
        # A not-yet-migrated screen is informational, not a hazard — keep the
        # warning icon/headline but suppress the pulsing colored edge overlay
        # (which "warning" enables by default).
        "warning_edges": False,
        "status_headline": _("Not Yet Implemented"),
        "text": text,
        "button_list": [_("Back to main menu")],
    }


def button_list_lvgl_cfg(
    *,
    title: str,
    button_data: list,
    show_back_button: bool = True,
    is_bottom_list: bool = False,
    selected_button: int = 0,
    is_button_text_centered: bool = None,   # accepted for call-site parity; the native
                                            # button_list_screen has no per-screen text
                                            # centering control yet — currently a no-op.
    scroll_y_initial_offset: int = None,    # PIL pixel-scroll; the native screen restores
                                            # position via selected_button -> initial_selected_index.
) -> dict:
    """Assemble a native ``button_list_screen`` config from the kwargs a View passed
    to ``run_screen`` (the same arguments the PIL ``ButtonListScreen`` took).

    Called by ``View.run_screen``, NOT by Views — Views stay free of the native
    config shape. This nests the ``TopNav`` fields the native schema requires
    (``_(title)`` mirrors the PIL ``TopNav``'s ``_(self.title)``) and maps
    ``selected_button -> initial_selected_index``. The ``button_data`` options are
    left as-is here; ``run_lvgl_screen`` serializes them (``ButtonOption.to_lvgl()``)
    just before the native call, screen-agnostically, so every screen's button list
    gets the same treatment. PIL-only kwargs have no native equivalent yet (ignored).
    """
    cfg = {
        "top_nav": {"title": _(title), "show_back_button": show_back_button},
        "button_list": list(button_data),   # raw ButtonOptions; serialized in run_lvgl_screen
        "is_bottom_list": is_bottom_list,
    }
    if selected_button:
        cfg["initial_selected_index"] = selected_button
    return cfg


class NotYetImplementedView(View):
    """
        Temporary View to use during dev.
    """
    def __init__(self, text: str = _mft("This is still on our to-do list!")):
        self.text = text
        self.__post_init__()


    def run(self):
        if IS_MICROPYTHON:
            # PIL screens (WarningScreen) can't render on MicroPython; show the
            # native LVGL status screen by name instead.
            self.run_screen(
                "large_icon_status_screen",
                lvgl_cfg=not_implemented_lvgl_cfg(self.text),
                # A transient notice has no idle screensaver; it also keeps the
                # native call to a single positional cfg arg (no wait_timeout_ms).
                allow_screensaver=False,
            )
        else:
            from seedsigner.gui.screens.screen import WarningScreen

            self.run_screen(
                WarningScreen,
                title=_("Work In Progress"),
                status_headline=_("Not Yet Implemented"),
                text=self.text,
                button_data=[ButtonOption("Back to main menu")],
            )

        return Destination(MainMenuView)



class ErrorView(View):
    def __init__(self,
                 title: str = _mft("Error"),
                 show_back_button: bool = True,
                 status_icon_name: str = SeedSignerIconConstants.ERROR,
                 status_headline: str = None,
                 text: str = None,
                 button_text: str = None,
                 next_destination: Destination = None):
        self.title = title
        self.show_back_button = show_back_button
        self.status_icon_name = status_icon_name
        self.status_headline = status_headline
        self.text = text
        self.button_text = button_text
        self.next_destination = next_destination
        self.__post_init__()

    def run(self):
        from seedsigner.gui.screens.screen import ErrorScreen

        self.run_screen(
            ErrorScreen,
            title=self.title,
            status_icon_name=self.status_icon_name,
            status_headline=self.status_headline,
            text=self.text,
            button_data=[ButtonOption(self.button_text)],
            show_back_button=self.show_back_button,
        )
        return self.next_destination if self.next_destination else Destination(MainMenuView, clear_history=True)



class NetworkMismatchErrorView(ErrorView):
    def __init__(self, derivation_path: str = None):
        self.derivation_path = derivation_path
        # ErrorView.__init__ lays down the inherited field defaults, then calls
        # self.__post_init__() (this override) — preserving "all fields set, then post-init".
        super().__init__()

    def __post_init__(self):
        from seedsigner.views.settings_views import SettingsEntryUpdateSelectionView

        # TRANSLATOR_NOTE: The network setting (mainnet/testnet/regtest) doesn't match the provided derivation path
        self.title = _("Network Mismatch")
        self.status_icon_name = SeedSignerIconConstants.WARNING
        self.show_back_button = False

        # TRANSLATOR_NOTE: Button option to alter a setting
        self.button_text = _("Change Setting")
        self.next_destination = Destination(SettingsEntryUpdateSelectionView, view_args=dict(attr_name=SettingsConstants.SETTING__NETWORK), clear_history=True)
        super().__post_init__()

        network = _(self.settings.get_value_display_name(SettingsConstants.SETTING__NETWORK))

        # TRANSLATOR_NOTE: "network" will be mainnet/testnet/regtest.
        self.text = _("Current network setting ({network}) doesn't match {derivation_path}.").format(
            network=network,
            derivation_path=self.derivation_path,
        )



class UnhandledExceptionView(View):
    def __init__(self, error: list[str]):
        self.error = error
        self.__post_init__()

    def __post_init__(self):
        from seedsigner.hardware.camera import CameraConnectionError
        super().__post_init__()

        # Camera errors bubble up to here. Reroute to their custom error View.
        if self.error[0] == CameraConnectionError.__name__:
            self.set_redirect(
                Destination(
                    CameraConnectionErrorView,
                    skip_current_view=True,
                )
            )


    def run(self):
        from seedsigner.gui.screens.screen import ErrorScreen

        self.run_screen(
            ErrorScreen,
            title=_("System Error"),
            status_headline=self.error[0],
            text=self.error[1] + "\n" + self.error[2],
            button_data=[ButtonOption("Back to Main Menu")],
        )
        
        return Destination(MainMenuView, clear_history=True)



class CameraConnectionErrorView(View):
    def run(self):
        from seedsigner.gui.screens.screen import ErrorScreen

        self.run_screen(
            ErrorScreen,
            title=_("Hardware Error"),
            status_headline=_("Cannot access camera"),
            text=_("Disconnect power and check for a loose camera connection."),
            button_data=[ButtonOption("Back to Main Menu")],
            show_back_button=False,
        )

        return Destination(MainMenuView, clear_history=True)


class OptionDisabledView(View):
    UPDATE_SETTING = ButtonOption("Update setting")
    DONE = ButtonOption("Back to Main Menu")

    def __init__(self, settings_attr: str):
        self.settings_attr = settings_attr
        self.__post_init__()

    def __post_init__(self):
        super().__post_init__()
        self.settings_entry = SettingsDefinition.get_settings_entry(self.settings_attr)

        # TRANSLATOR_NOTE: Inserts the name of a settings option (e.g. "Persistent Settings" is currently...)
        self.error_msg = _("\"{}\" is currently disabled in Settings.").format(
            _(self.settings_entry.display_name),
        )


    def run(self):
        from seedsigner.gui.screens.screen import WarningScreen

        button_data = [self.UPDATE_SETTING, self.DONE]
        selected_menu_num = self.run_screen(
            WarningScreen,
            title=_("Option Disabled"),
            status_headline=None,
            text=self.error_msg,
            button_data=button_data,
            show_back_button=False,
        )

        if button_data[selected_menu_num] == self.UPDATE_SETTING:
            from seedsigner.views.settings_views import SettingsEntryUpdateSelectionView
            return Destination(SettingsEntryUpdateSelectionView, view_args=dict(attr_name=self.settings_attr), clear_history=True)
        else:
            return Destination(MainMenuView, clear_history=True)



class RemoveMicroSDWarningView(View):
    CONTINUE = ButtonOption("Continue")
    SETTINGS = ButtonOption("Settings")

    def run(self):
        from seedsigner.gui.screens.screen import WarningScreen

        button_data = [self.CONTINUE, self.SETTINGS]
        selected_menu_num = self.run_screen(
            WarningScreen,
            title=_("Action Required"),
            status_icon_name=SeedSignerIconConstants.MICROSD,
            status_headline=None,
            text=_("You must remove the\nMicroSD card to continue."),
            show_back_button=False,
            button_data=button_data,
        )

        if button_data[selected_menu_num] == self.CONTINUE:
            from seedsigner.hardware.microsd import MicroSD
            if not MicroSD.get_instance().is_inserted:
                return Destination(MainMenuView, clear_history=True)
            else:
                return Destination(RemoveMicroSDWarningView, clear_history=True)

        elif button_data[selected_menu_num] == self.SETTINGS:
            from seedsigner.views.settings_views import SettingsEntryUpdateSelectionView
            return Destination(
                SettingsEntryUpdateSelectionView,
                view_args=dict(
                    attr_name=SettingsConstants.SETTING__MICROSD_TOAST_TIMER,
                    blocking_view=RemoveMicroSDWarningView,
                    unblocking_view=MainMenuView
                )
            )



class OpeningSplashView(View):
    def __init__(self, force_partner_logos: bool | None = None):
        self.force_partner_logos = force_partner_logos
        self.__post_init__()

    def run(self):
        from seedsigner.gui.screens.screen import OpeningSplashScreen
        self.run_screen(
            OpeningSplashScreen,
            force_partner_logos=self.force_partner_logos
        )



class Screensaver:
    """
    Lightweight, controller-driven manager for the screensaver. Not a View: it never goes
    on the back stack and is driven directly via start()/stop()/is_running.

    Holds a single persistent ScreensaverScreen instance so that a cross-thread stop()
    (e.g. an SD-card toast interrupt) reaches the same instance the main thread is running.
    """
    def __init__(self):
        self.screen = None

    @property
    def is_running(self) -> bool:
        return self.screen is not None and self.screen.is_running

    def start(self):
        if self.screen is None:
            # Lazy/late import + instantiation to reduce Controller initial startup time
            # (and to keep this module PIL-free at import).
            from seedsigner.gui.screens.screen import ScreensaverScreen
            from seedsigner.hardware.buttons import HardwareButtons
            self.screen = ScreensaverScreen(HardwareButtons.get_instance())
        self.screen.start()

    def stop(self):
        if self.screen is not None:
            self.screen.stop()