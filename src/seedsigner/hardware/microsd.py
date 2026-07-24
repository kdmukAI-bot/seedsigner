import logging
import os
import time

from seedsigner.compat import IS_MICROPYTHON
from seedsigner.models.settings_definition import SettingsConstants
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

        ESP32 only: nothing mounts the card this early in boot, so mount it here.
        Idempotent — a mount already done by a prior call or by the frozen facade is
        detected via os.stat and left alone. Fail-soft: a missing or unreadable card
        returns False (callers fall back to defaults or skip writes) and never raises.
        A no-op returning True on CPython/SeedSigner OS, where the card (if any) is
        mounted outside the app.
        """
        if not IS_MICROPYTHON:
            return True
        mount = SettingsConstants.MICROSD_MOUNT
        try:
            os.stat(mount)             # already mounted (facade import or a prior call)?
            return True
        except OSError:
            pass
        try:
            import machine
            import vfs
            sd = machine.SDCard(slot=0, width=4)   # slot 0 = IOMUX; VDD via LDO_VO4
            vfs.mount(vfs.VfsFat(sd), mount)
            return True
        except Exception:
            return False


    @property
    def is_inserted(self):
        from seedsigner.models.settings import Settings  # Import here to avoid circular import issues

        if IS_MICROPYTHON:
            # ESP32: the card is the microSD mount; report whether it is really
            # accessible so save() won't write to an unmounted card and Persistent
            # Settings reflects a genuinely-present card (parity with SeedSigner OS).
            return MicroSD.ensure_mounted()

        if Settings.HOSTNAME == Settings.SEEDSIGNER_OS:
            return os.path.exists(MicroSD.MOUNT_POINT)

        # CPython desktop dev: no real card — treat as always available.
        return True


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
