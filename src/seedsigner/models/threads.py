import logging
from seedsigner.compat.threading import Thread, Lock

logger = logging.getLogger(__name__)


class BaseThread(Thread):
    def __init__(self):
        super().__init__(daemon=True)
    
    def start(self):
        logger.debug(f"{self.__class__.__name__} STARTING")
        self.keep_running = True
        super().start()

    def stop(self):
        logger.debug(f"{self.__class__.__name__} EXITING")
        self.keep_running = False
    
    def run(self):
        while self.keep_running:
            # Do something
            raise Exception(f"Must implement run() in {self.__class__.__name__}")



class ModulePreloadThread(BaseThread):
    """Fire-and-forget import warmer, spawned at a flow's entry point.

    Pulls modules the flow is known to need later into sys.modules while the
    user is still reading the current screen (e.g. the UR/QR encode chain for
    the PSBT signing flow). Imports are idempotent, so re-entering a flow just
    re-walks cache hits.
    """
    def __init__(self, *module_names):
        super().__init__()
        self.module_names = module_names

    def run(self):
        for module_name in self.module_names:
            try:
                __import__(module_name)
            except Exception as e:
                # Preloading is purely an optimization; a genuine import
                # problem will surface at the real import site.
                logger.warning(f"Preload of {module_name} failed: {e}")



class ThreadsafeCounter:
    def __init__(self, initial_value: int = 0):
        self.count = initial_value
        self._lock = Lock()
    
    @property
    def cur_count(self):
        # Reads don't require the lock
        return self.count

    def increment(self, step: int = 1):
        # Updates must be locked
        with self._lock:
            self.count += step
    
    def set_value(self, value: int):
        with self._lock:
            self.count = value


