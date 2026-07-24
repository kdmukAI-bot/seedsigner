from PIL import Image, ImageDraw
from seedsigner.compat.threading import Lock

from seedsigner.hardware.displays.display_driver import ALL_DISPLAY_TYPES, DISPLAY_TYPE__ILI9341, DISPLAY_TYPE__ILI9486, DISPLAY_TYPE__ST7789, DisplayDriverFactory
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

        display_config = Settings.get_instance().get_value(SettingsConstants.SETTING__DISPLAY_CONFIGURATION, default_if_none=True)
        self.display_type = display_config.split("_")[0]
        if self.display_type not in ALL_DISPLAY_TYPES:
            raise Exception(f"Invalid display type: {self.display_type}")

        width, height = display_config.split("_")[1].split("x")

        if self.disp:
            # Existing instances might need to close resources like pwm
            self.disp.cleanup()

        self.disp = DisplayDriverFactory.instantiate_display_driver(self.display_type, width=int(width), height=int(height))

        if Settings.get_instance().get_value(SettingsConstants.SETTING__DISPLAY_COLOR_INVERTED, default_if_none=True) == SettingsConstants.OPTION__ENABLED:
            self.disp.invert()

        if self.display_type == DISPLAY_TYPE__ST7789:
            self.canvas_width = self.disp.width
            self.canvas_height = self.disp.height

        elif self.display_type in [DISPLAY_TYPE__ILI9341, DISPLAY_TYPE__ILI9486]:
            # Swap for the natively portrait-oriented displays
            self.canvas_width = self.disp.height
            self.canvas_height = self.disp.width

        self.canvas = Image.new('RGB', (self.canvas_width, self.canvas_height))
        self.draw = ImageDraw.Draw(self.canvas)

        self.lock.release()
