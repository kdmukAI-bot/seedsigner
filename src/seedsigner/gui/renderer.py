from PIL import Image, ImageDraw
from seedsigner.compat.threading import Lock

from seedsigner.hardware.displays.display_driver import DISPLAY_TYPE__ST7789, DisplayDriverFactory
from seedsigner.models.settings import Settings
from seedsigner.models.settings_definition import SettingsConstants
from seedsigner.models.singleton import ConfigurableSingleton



class Renderer(ConfigurableSingleton):
    buttons = None
    canvas_width = 0
    canvas_height = 0
    canvas: Image.Image = None
    draw: ImageDraw.ImageDraw = None
    disp = None
    lock = Lock()


    @property
    def is_screenshot_generator(self) -> bool:
        return False


    @classmethod
    def configure_instance(cls):
        # Instantiate the one and only Renderer instance
        renderer = object.__new__(cls)
        cls._instance = renderer

        renderer.initialize_display()


    def initialize_display(self):
        # May be called while already running with a previous display driver; must
        # prevent any other screen writes while we're changing the display driver.
        self.lock.acquire()

        # Native LVGL owns the panel; the PIL canvas only needs to match its dimensions so
        # the blended-pump geometry — and the QR-density lookup that keys off canvas_height
        # (via View.canvas_height) — stays correct. ST7789 is the only display the native
        # build drives, so resolution is the one live knob; the driver chip is fixed.
        resolution = Settings.get_instance().get_value(SettingsConstants.SETTING__DISPLAY_RESOLUTION, default_if_none=True)
        width, height = (int(dimension) for dimension in resolution.split("x"))
        self.display_type = DISPLAY_TYPE__ST7789

        if self.disp:
            # Existing instances might need to close resources like pwm
            self.disp.cleanup()

        self.disp = DisplayDriverFactory.instantiate_display_driver(self.display_type, width=width, height=height)

        self.canvas_width = self.disp.width
        self.canvas_height = self.disp.height

        self.canvas = Image.new('RGB', (self.canvas_width, self.canvas_height))
        self.draw = ImageDraw.Draw(self.canvas)

        self.lock.release()
