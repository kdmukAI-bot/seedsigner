import logging
import time
from seedsigner.compat.l10n import gettext as _

from seedsigner.gui.constants import GUIConstants, SeedSignerIconConstants
from seedsigner.models.threads import BaseThread

logger = logging.getLogger(__name__)


class BaseToastOverlayManagerThread(BaseThread):
    """
    Drives one native LVGL toast: a bottom-pinned banner composited on the display's TOP
    layer, over whatever screen is live. The native overlay (seedsigner-lvgl-screens
    ``overlay_manager``) owns display, auto-dismiss (``duration``), dismissal on any input,
    one-at-a-time replacement, AND screensaver coexistence ("new toasts break out of the
    screensaver"; screensaver activation is suppressed while a toast shows). The host
    contract for the native call lives in ``gui.lvgl_screen_runner.show_toast()``.

    So this thread does NOT render or coordinate the Renderer.lock (the native overlay does
    all of that). It only supplies the host policy — text, icon, colors, duration — and the
    one behavior the native overlay has no notion of: an optional pre-show activation delay
    (cancelled if the user interacts first). After ``show_toast`` the thread simply ends;
    the native duration timer owns teardown (there is no cross-thread native dismiss).

    Subclasses set the policy fields below (``label_text`` per-instance; ``icon_name`` /
    colors typically as class attributes). Controller sets ``keep_running = False`` (via
    ``stop()``) to end a toast so a replacement / app exit can proceed.
    """
    # Policy (subclasses override). Colors are PIL color strings; the runner maps them to
    # the 6-digit hex the native layer parses. icon_name is a SeedSignerIconConstants name
    # (or None for a text-only banner).
    label_text: str = None
    icon_name: str = None
    outline_color: str = GUIConstants.NOTIFICATION_COLOR
    font_color: str = GUIConstants.NOTIFICATION_COLOR

    def __init__(self,
                 activation_delay: int = 0,  # seconds before the toast is shown
                 duration: int = 3,          # seconds the toast stays up (0 = until replaced/dismissed)
                 ):
        from seedsigner.hardware.buttons import HardwareButtons
        super().__init__()
        self.activation_delay: int = activation_delay
        self.duration: int = duration

        self.hw_inputs = HardwareButtons.get_instance()

        # Special case when the screensaver is running: allow a toast to register even mid
        # -screensaver (matches the pre-native behavior).
        self.hw_inputs.override_ind = True

    def run(self):
        from seedsigner.gui.lvgl_screen_runner import show_toast

        logger.info(f"{self.__class__.__name__}: started")
        start = time.time()

        # Pre-show activation delay; a button press before the toast appears cancels it
        # (the user is still interacting, so the tip is unwanted).
        while time.time() - start < self.activation_delay:
            if self.hw_inputs.has_any_input():
                logger.info(f"{self.__class__.__name__}: canceled before showing (user input)")
                return
            time.sleep(0.1)

        if not self.keep_running:
            return

        # Fire-and-forget: the native overlay owns everything from here — auto-dismiss on
        # the duration timer, dismissal on any input, one-at-a-time replacement, and
        # screensaver coexistence. There is no host render loop and no cross-thread dismiss
        # (the native dismiss is LVGL-thread only), so the thread ends right after the push.
        logger.info(f"{self.__class__.__name__}: showing toast")
        show_toast(
            label_text=self.label_text,
            icon_name=self.icon_name,
            outline_color=self.outline_color,
            font_color=self.font_color,
            duration_ms=self.duration * 1000,
        )



class RemoveSDCardToastManagerThread(BaseToastOverlayManagerThread):
    icon_name = SeedSignerIconConstants.MICROSD

    def __init__(self, activation_delay: int = 3, duration: int = 5):
        """
            * activation_delay: seconds to wait after boot before showing the tip (so it
                doesn't interrupt a user who is already interacting).
            * duration: seconds the tip stays up (the native duration timer owns teardown).
        """
        super().__init__(
            activation_delay=activation_delay,
            duration=duration,
        )
        self.label_text = _("You can remove\nthe SD card now")



class SDCardStateChangeToastManagerThread(BaseToastOverlayManagerThread):
    icon_name = SeedSignerIconConstants.MICROSD

    def __init__(self, action: str, *args, **kwargs):
        # Note: we could just directly detect the MicroSD status here, but passing it in
        # via `action` lets us simulate the state we want in the screenshot generator.
        from seedsigner.hardware.microsd import MicroSD
        if action not in [MicroSD.ACTION__INSERTED, MicroSD.ACTION__REMOVED]:
            raise Exception(f"Invalid MicroSD action: {action}")
        self.label_text = _("SD card removed") if action == MicroSD.ACTION__REMOVED else _("SD card inserted")

        super().__init__(*args, **kwargs)


"""****************************************************************************
    Messaging toasts
****************************************************************************"""


class DefaultToast(BaseToastOverlayManagerThread):
    outline_color = GUIConstants.BODY_FONT_COLOR
    font_color = GUIConstants.BODY_FONT_COLOR

    def __init__(self, label_text="This is a notification toast", activation_delay=0, duration=3):
        # Note: activation_delay is configurable so the screenshot generator can get the
        # toast to immediately render.
        super().__init__(
            activation_delay=activation_delay,  # seconds
            duration=duration,                  # seconds
        )
        self.label_text = label_text



class InfoToast(DefaultToast):
    icon_name = SeedSignerIconConstants.INFO
    outline_color = GUIConstants.INFO_COLOR
    font_color = GUIConstants.BODY_FONT_COLOR



class SuccessToast(DefaultToast):
    icon_name = SeedSignerIconConstants.SUCCESS
    outline_color = GUIConstants.SUCCESS_COLOR
    font_color = GUIConstants.BODY_FONT_COLOR



class WarningToast(DefaultToast):
    icon_name = SeedSignerIconConstants.WARNING
    outline_color = GUIConstants.WARNING_COLOR
    font_color = GUIConstants.BODY_FONT_COLOR



class DireWarningToast(DefaultToast):
    icon_name = SeedSignerIconConstants.WARNING
    outline_color = GUIConstants.DIRE_WARNING_COLOR
    font_color = GUIConstants.BODY_FONT_COLOR



class ErrorToast(DefaultToast):
    icon_name = SeedSignerIconConstants.ERROR
    outline_color = GUIConstants.ERROR_COLOR
    font_color = GUIConstants.BODY_FONT_COLOR
