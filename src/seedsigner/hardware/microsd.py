import logging
import os
import time

from seedsigner.compat import IS_MICROPYTHON
from seedsigner.models.singleton import Singleton
from seedsigner.models.threads import BaseThread

logger = logging.getLogger(__name__)


class MicroSD(Singleton, BaseThread):
    MOUNT_POINT = "/mnt/microsd"
    FIFO_PATH = "/tmp/mdev_fifo"
    FIFO_MODE = 0o600
    ACTION__INSERTED = "add"
    ACTION__REMOVED = "remove"


    @classmethod
    def get_instance(cls):
        # This is the only way to access the one and only instance
        if cls._instance is None:
            # Instantiate the one and only instance
            microsd = object.__new__(cls)
            cls._instance = microsd

            # explicitly call BaseThread __init__ since multiple class inheritance
            BaseThread.__init__(microsd)
    
        return cls._instance


    @classmethod
    def ensure_mounted(cls) -> bool:
        """Make the microSD available at its mount point and report whether it is.

        ESP32 only: nothing mounts the card this early in boot. Delegates to the frozen
        facade (``seedsigner_lvgl_screens.sd_ensure``), which is the single owner of the
        /sd mount lifecycle — so mount, unmount-on-remove and remount-on-insert all
        go through one authority instead of the app and the facade each holding a copy.
        Idempotent + fail-soft: a missing/unreadable card returns False (callers fall back
        to defaults or skip writes) and never raises. A no-op returning True on
        CPython/SeedSigner OS, where the card (if any) is mounted outside the app.
        """
        if not IS_MICROPYTHON:
            return True
        try:
            import seedsigner_lvgl_screens as _facade
            return _facade.sd_ensure()
        except Exception:
            return False


    @property
    def is_inserted(self):
        from seedsigner.models.settings import Settings  # Import here to avoid circular import issues

        if IS_MICROPYTHON:
            # ESP32: honest liveness probe via the facade (the single /sd owner). Unlike a
            # bare mount check — which stays True after a physical pull because the mount
            # registration lingers — sd_live() probes the card, so save() won't write to a
            # gone card and Persistent Settings reflects a genuinely-present card.
            try:
                import seedsigner_lvgl_screens as _facade
                return _facade.sd_live()
            except Exception:
                return False

        if Settings.HOSTNAME == Settings.SEEDSIGNER_OS:
            return os.path.exists(MicroSD.MOUNT_POINT)

        # CPython desktop dev: no real card — treat as always available.
        return True


    @classmethod
    def poll(cls):
        """ESP32 microSD hotplug tick. Called from the LVGL pump loop (~20ms); the bus
        probe itself is throttled inside the facade's sd_poll(), so calling every iteration
        is cheap. On an insert/remove transition, updates Settings (Persistent Settings
        availability + help text) and shows a state-change toast — mirroring the
        SeedSigner-OS mdev path (MicroSD.run), but driven by polling since the P4 wires no
        card-detect GPIO. A no-op off MicroPython."""
        if not IS_MICROPYTHON:
            return
        try:
            import seedsigner_lvgl_screens as _facade
            change = _facade.sd_poll()   # "inserted" | "removed" | None (self-throttled)
        except Exception:
            return
        if not change:
            return
        from seedsigner.controller import Controller
        from seedsigner.gui.toast import SDCardStateChangeToastManagerThread
        from seedsigner.models.settings import Settings
        action = cls.ACTION__INSERTED if change == "inserted" else cls.ACTION__REMOVED
        Settings.handle_microsd_state_change(action=action)
        Controller.get_instance().activate_toast(SDCardStateChangeToastManagerThread(action=action))


    def start_detection(self):
        self.start()


    def run(self):
        from seedsigner.controller import Controller
        from seedsigner.gui.toast import SDCardStateChangeToastManagerThread
        from seedsigner.models.settings import Settings  # Import here to avoid circular import issues
        action = ""
        
        # explicitly only microsd add/remove detection in seedsigner-os
        if Settings.HOSTNAME == Settings.SEEDSIGNER_OS:

            # at start-up, get current status and inform Settings
            Settings.handle_microsd_state_change(
                action=MicroSD.ACTION__INSERTED if self.is_inserted else MicroSD.ACTION__REMOVED
            )

            if os.path.exists(self.FIFO_PATH):
                os.remove(self.FIFO_PATH)
            
            os.mkfifo(self.FIFO_PATH, self.FIFO_MODE)

            while self.keep_running:
                with open(self.FIFO_PATH) as fifo:
                    action = fifo.read()
                    logger.info(f"fifo message: {action}")

                    Settings.handle_microsd_state_change(action=action)
                    Controller.get_instance().activate_toast(SDCardStateChangeToastManagerThread(action=action))

                time.sleep(0.1)
