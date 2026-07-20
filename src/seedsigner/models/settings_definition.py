import os

from seedsigner.helpers.l10n import mark_for_translation as _mft

import logging
logger = logging.getLogger(__name__)



class SettingsConstants:
    # Basic defaults
    OPTION__ENABLED = "E"
    OPTION__DISABLED = "D"
    OPTION__PROMPT = "P"
    OPTION__REQUIRED = "R"
    OPTIONS__ENABLED_DISABLED = [
        (OPTION__ENABLED, _mft("Enabled")),
        (OPTION__DISABLED, _mft("Disabled")),
    ]
    OPTIONS__ONLY_DISABLED = [
        (OPTION__DISABLED, _mft("Disabled")),
    ]
    OPTIONS__PROMPT_REQUIRED_DISABLED = [
        (OPTION__PROMPT, _mft("Prompt")),
        (OPTION__REQUIRED, _mft("Required")),
        (OPTION__DISABLED, _mft("Disabled")),
    ]
    OPTIONS__ENABLED_DISABLED_REQUIRED = OPTIONS__ENABLED_DISABLED +[
        (OPTION__REQUIRED, _mft("Required")),
    ]
    OPTIONS__ENABLED_DISABLED_PROMPT = OPTIONS__ENABLED_DISABLED + [
        (OPTION__PROMPT, _mft("Prompt")),
    ]
    ALL_OPTIONS = OPTIONS__ENABLED_DISABLED_PROMPT + [
        (OPTION__REQUIRED, _mft("Required")),
    ]

    # User-facing selection options
    XPUB_QR_FORMAT__UR_CRYPTO_ACCOUNT = "urca"
    XPUB_QR_FORMAT__STATIC = "sta"
    XPUB_QR_FORMAT__SPECTER_LEGACY = "spl"
    ALL_XPUB_QR_FORMATS = [
        # TRANSLATOR_NOTE: QR code format option; "default" = this is the format most wallets use
        (XPUB_QR_FORMAT__UR_CRYPTO_ACCOUNT, _mft("Animated (default)")),

        # TRANSLATOR_NOTE: QR code format option (static = single frame, not animated)
        (XPUB_QR_FORMAT__STATIC, _mft("Static")),

        # TRANSLATOR_NOTE: QR code format option: old format that Specter Desktop used to use
        (XPUB_QR_FORMAT__SPECTER_LEGACY, _mft("Specter legacy")),
    ]

    # Over-specifying current and possible future locales to reduce/eliminate main repo
    # changes when adding/testing new languages.
    LOCALE__ARABIC = "ar"
    LOCALE__BENGALI = "bn"
    LOCALE__BULGARIAN = "bg"
    LOCALE__CATALAN = "ca"
    LOCALE__CHINESE_SIMPLIFIED = "zh_Hans_CN"
    LOCALE__CHINESE_TRADITIONAL = "zh_Hant_TW"
    LOCALE__CROATIAN = "hr"
    LOCALE__CZECH = "cs"
    LOCALE__DANISH = "da"
    LOCALE__DUTCH = "nl"
    LOCALE__EGYPTIAN = "ar_EG"
    LOCALE__ENGLISH = "en"
    LOCALE__ESTONIAN = "et"
    LOCALE__FINNISH = "fi"
    LOCALE__FRENCH = "fr"
    LOCALE__GAELIC = "gd"
    LOCALE__GERMAN = "de"
    LOCALE__GREEK = "el"
    LOCALE__GUJARATI = "gu"
    LOCALE__HAUSA = "ha"
    LOCALE__HEBREW = "he"
    LOCALE__HINDI = "hi"
    LOCALE__HUNGARIAN = "hu"
    LOCALE__INDONESIAN = "id"
    LOCALE__ITALIAN = "it"
    LOCALE__JAPANESE = "ja"
    LOCALE__JAVANESE = "jv"
    LOCALE__KOREAN = "ko"
    LOCALE__LAO = "lo"
    LOCALE__LATVIAN = "lv"
    LOCALE__LITHUANIAN = "lt"
    LOCALE__MALAY = "ms"
    LOCALE__MALTESE = "mt"
    LOCALE__MARATHI = "mr"
    LOCALE__NORWEGIAN = "no"
    LOCALE__PERSIAN = "fa"
    LOCALE__POLISH = "pl"
    LOCALE__PORTUGUESE_BR = "pt_BR"
    LOCALE__PORTUGUESE_PT = "pt_PT"
    LOCALE__PUNJABI = "pa"
    LOCALE__ROMANIAN = "ro"
    LOCALE__RUSSIAN = "ru"
    LOCALE__SLOVAK = "sk"
    LOCALE__SLOVENIAN = "sl"
    LOCALE__SPANISH = "es"
    LOCALE__SWEDISH = "sv"
    LOCALE__TAGALOG = "tl"
    LOCALE__TAMIL = "ta"
    LOCALE__TELUGU = "te"
    LOCALE__THAI = "th"
    LOCALE__TURKISH = "tr"
    LOCALE__UKRANIAN = "uk"
    LOCALE__URDU = "ur"
    LOCALE__VIETNAMESE = "vi"

    # Do not wrap for translation. Present each language in its native form (i.e. either
    # using its native chars or how they write it in Latin chars; e.g. Spanish is listed
    # and sorted as "Español").
    # Sort fully-vetted languages first, then beta languages, then the "placeholders /
    # coming soon" languages.
    # Sort by native form when written in Latin chars, otherwise sort by English name.
    # Include English name in parens for languages that don't use Latin chars.
    # Include region/country in parens for specific dialects (e.g. "Português (Brasil)").
    # Note that dicts preserve insertion order as of Python 3.7.
    ALL_LOCALES = {
        # --------- Fully supported languages -------------------------------------------
        LOCALE__CATALAN: "Català",
        LOCALE__GERMAN: "Deutsch",
        LOCALE__ENGLISH: "English",
        LOCALE__SPANISH: "Español",
        LOCALE__FRENCH: "Français",
        LOCALE__ITALIAN: "Italiano",
        LOCALE__DUTCH: "Nederlands",

        # --------- Beta languages ------------------------------------------------------
        LOCALE__CZECH: "(beta) Čeština",
        LOCALE__CHINESE_SIMPLIFIED: "(beta) 简体中文 (Chinese Simplified)",
        LOCALE__HINDI: "(beta) हिन्दी (Hindi)",
        LOCALE__INDONESIAN: "(beta) Indonesia (Indonesian)",
        LOCALE__JAPANESE: "(beta) 日本語 (Japanese)",
        LOCALE__KOREAN: "(beta) 한국어 (Korean)",
        LOCALE__PERSIAN: "(beta) فارسی (Persian)",
        LOCALE__POLISH: "(beta) Polski",
        LOCALE__PORTUGUESE_BR: "(beta) Português (Brasil)",
        LOCALE__THAI: "(beta) ไทย (Thai)",
        LOCALE__VIETNAMESE: "(beta) Tiếng Việt (Vietnamese)",

        # --------- In Progress Languages -----------------------------------------------
        LOCALE__GREEK: "(incomplete) Ελληνικά (Greek)",  # OpenSans includes Greek chars
        LOCALE__NORWEGIAN: "(incomplete) Norsk (Norwegian)",
        LOCALE__RUSSIAN: "(incomplete) русский (Russian)",  # OpenSans includes cyrillic chars
        LOCALE__TURKISH: "(incomplete) Türkçe (Turkish)",

        # --------- Placeholders / Coming soon ------------------------------------------
        # Commented out options require explicit additional font support.
        # -------------------------------------------------------------------------------
        LOCALE__ARABIC: "العربية (Arabic)",
        # LOCALE__BENGALI: "বাংলা (Bengali)",
        LOCALE__BULGARIAN: "български (Bulgarian)",  # OpenSans includes cyrillic chars
        # LOCALE__CHINESE_TRADITIONAL: "繁體中文 (Chinese Traditional)",
        LOCALE__DANISH: "Dansk",
        LOCALE__ESTONIAN: "Eesti",
        # LOCALE__EGYPTIAN: "مصرى (Egyptian)",
        LOCALE__GAELIC: "Gaeilge",
        # LOCALE__GUJARATI: "ગુજરાતી (Gujarati)",
        LOCALE__HAUSA: "Hausa",
        # LOCALE__HEBREW: "עברית (Hebrew)",
        LOCALE__CROATIAN: "Hrvatski",
        LOCALE__JAVANESE: "Jawa (Javanese)",
        # LOCALE__LAO: "ລາວ (Lao)",
        LOCALE__LATVIAN: "Latviešu",
        LOCALE__LITHUANIAN: "Lietuvių",
        LOCALE__HUNGARIAN: "Magyar",
        LOCALE__MALAY: "Melayu",
        LOCALE__MALTESE: "Malti",
        # LOCALE__MARATHI: "मराठी (Marathi)",
        LOCALE__PORTUGUESE_PT: "Português (Portugal)",
        # LOCALE__PUNJABI: "ਪੰਜਾਬੀ (Punjabi)",
        LOCALE__ROMANIAN: "Română",
        LOCALE__SLOVAK: "Slovenčina",
        LOCALE__SLOVENIAN: "Slovenščina",
        LOCALE__FINNISH: "Suomi",
        LOCALE__SWEDISH: "Svenska",
        LOCALE__TAGALOG: "Tagalog",
        # LOCALE__TAMIL: "தமிழ் (Tamil)",
        # LOCALE__TELUGU: "తెలుగు (Telugu)",
        LOCALE__UKRANIAN: "українська (Ukranian)",   # OpenSans includes cyrillic chars
        # LOCALE__URDU: "اردو (Urdu)",
    }


    # (english_name, native_name) per locale, for the language-selection picker's
    # "English | native" rows. Kept in sync with the single source of truth in
    # seedsigner-lvgl-screens/tools/i18n/supported_locales.json (which the font-pack +
    # endonym-image pipeline reads); duplicated here because that file isn't shipped to
    # the app. Unlike ALL_LOCALES (whose values fold in "(beta)"/parenthetical-English
    # decorations for the legacy list), these are the clean names the native picker
    # shows. The picker decides live-text vs. pre-rendered endonym image from the NATIVE
    # name's glyph coverage, not from this table.
    LOCALE_NAMES = {
        LOCALE__ENGLISH: ("English", "English"),
        LOCALE__PORTUGUESE_BR: ("Brazilian Portuguese", "Português do Brasil"),
        LOCALE__CATALAN: ("Catalan", "Català"),
        LOCALE__CZECH: ("Czech", "Čeština"),
        LOCALE__DUTCH: ("Dutch", "Nederlands"),
        LOCALE__FRENCH: ("French", "Français"),
        LOCALE__GERMAN: ("German", "Deutsch"),
        LOCALE__GREEK: ("Greek", "Ελληνικά"),
        LOCALE__HINDI: ("Hindi", "हिन्दी"),
        LOCALE__INDONESIAN: ("Indonesian", "Bahasa Indonesia"),
        LOCALE__ITALIAN: ("Italian", "Italiano"),
        LOCALE__JAPANESE: ("Japanese", "日本語"),
        LOCALE__KOREAN: ("Korean", "한국어"),
        LOCALE__NORWEGIAN: ("Norwegian", "Norsk"),
        LOCALE__PERSIAN: ("Persian", "فارسی"),
        LOCALE__POLISH: ("Polish", "Polski"),
        LOCALE__RUSSIAN: ("Russian", "Русский"),
        LOCALE__CHINESE_SIMPLIFIED: ("Simplified Chinese", "简体中文"),
        LOCALE__SPANISH: ("Spanish", "Español"),
        LOCALE__THAI: ("Thai", "ไทย"),
        LOCALE__TURKISH: ("Turkish", "Türkçe"),
        LOCALE__URDU: ("Urdu", "اردو"),
        LOCALE__VIETNAMESE: ("Vietnamese", "Tiếng Việt"),
    }


    @classmethod
    def get_locale_names(cls, locale, endonym=None):
        """Return ``(english_name, native_name)`` for `locale`, for a picker row.

        Sourced from ``LOCALE_NAMES``. For an SD-discovered pack not in that table,
        fall back to the pack manifest's ``endonym`` as the native name and the locale
        code as the English label.
        """
        entry = cls.LOCALE_NAMES.get(locale)
        if entry is not None:
            return entry
        return (locale, endonym or locale)


    @classmethod
    def get_catalog_root(cls):
        """Root under which per-locale translation catalogs live, as
        ``<root>/<locale>/LC_MESSAGES/messages.mo``.

        SINGLE SOURCE OF TRUTH shared by ``get_detected_languages()`` (the picker's
        catalog SCAN) and ``settings`` ``bindtextdomain()`` (the runtime LOOKUP), so
        detection and lookup always point at the SAME place — otherwise a locale whose
        ``.mo`` shipped could be missing from the picker (or vice-versa).

        The catalogs live in the DEPLOYED language packs — the SAME self-contained unit
        the fonts come from — so this matches ``LOCALE_PACK_DIR`` (the font seam's root):
        ``"lang-packs"`` relative to CWD on the Pi (packs deploy beside the ``.so``),
        ``"/sd"`` on ESP32 (the microSD pack root). A locale's font AND its ``.mo`` ship
        together, so a language is fully available or not at all — no packs means English
        only (baked floor), NEVER translated text with no font to render it (which is why
        there is no fall-back to the app's bundled ``seedsigner-translations``: those
        catalogs cover non-Latin languages the baked floor cannot draw).
        """
        from seedsigner.compat import IS_MICROPYTHON
        if IS_MICROPYTHON:
            return "/sd"
        return "lang-packs"


    @classmethod
    def get_detected_languages(cls) -> list[tuple[str, str]]:
        """
        Return a list of tuples of language codes and their native names.

        Scans the filesystem to autodiscover which language codes are onboard.
        """
        # Pre-load English since there's no "en" entry in the translations folder; also
        # it should always appear first in the list anyway.
        detected_languages = [(cls.LOCALE__ENGLISH, cls.ALL_LOCALES[cls.LOCALE__ENGLISH])]

        # Autodiscover onboard locales from the fixed <root>/<locale>/LC_MESSAGES/*.mo
        # layout (get_catalog_root() keeps this pointed at the same place as the runtime
        # gettext lookup). os.walk is absent on MicroPython; os.listdir exists on both, so
        # walk the two known levels explicitly instead.
        l10n_dir = cls.get_catalog_root()
        locales_present = set()
        try:
            locale_dirs = os.listdir(l10n_dir)
        except OSError:
            locale_dirs = []
        for locale_code in locale_dirs:
            lc_messages = "/".join([l10n_dir, locale_code, "LC_MESSAGES"])
            try:
                entries = os.listdir(lc_messages)
            except OSError:
                # Not a locale directory (e.g. a stray file at the l10n/ top level)
                continue
            if any(f.endswith(".mo") for f in entries):
                locales_present.add(locale_code)

        for locale in cls.ALL_LOCALES.keys():
            if locale in locales_present:
                detected_languages.append((locale, cls.ALL_LOCALES[locale]))

        return detected_languages


    BTC_DENOMINATION__BTC = "btc"
    BTC_DENOMINATION__SATS = "sats"
    BTC_DENOMINATION__THRESHOLD = "thr"
    BTC_DENOMINATION__BTCSATSHYBRID = "hyb"
    ALL_BTC_DENOMINATIONS = [
        (BTC_DENOMINATION__BTC, _mft("BTC")),
        (BTC_DENOMINATION__SATS, _mft("sats")),
        (BTC_DENOMINATION__THRESHOLD, _mft("Threshold at 0.01")),
        (BTC_DENOMINATION__BTCSATSHYBRID, _mft("BTC | sats hybrid")),
    ]

    CAMERA_ROTATION__0 = 0
    CAMERA_ROTATION__90 = 90
    CAMERA_ROTATION__180 = 180
    CAMERA_ROTATION__270 = 270
    ALL_CAMERA_ROTATIONS = [
        (CAMERA_ROTATION__0, _mft("0°")),
        (CAMERA_ROTATION__90, _mft("90°")),
        (CAMERA_ROTATION__180, _mft("180°")),
        (CAMERA_ROTATION__270, _mft("270°")),
    ]

    # QR code density: integer pixels-per-module (2-6), the readability knob for animated
    # QRs. Lower number = smaller modules = more data per frame (fewer frames) but harder for
    # a camera to scan; higher number = bigger modules, easier to scan, less data per frame.
    # The per-frame byte budget is derived from this via models/qr_density.py. Stored as ints
    # (like ALL_CAMERA_ROTATIONS) so SettingsQR round-trips them; replaces the old Low/Medium/
    # High tiers. 2 is the extreme (densest) step — the biggest QR that exists; fine on a large
    # panel but marginal on a small one, so it stays opt-in and is never a default.
    # See docs/qr-density-redesign-instructions.md.
    DENSITY__2 = 2
    DENSITY__3 = 3
    DENSITY__4 = 4
    DENSITY__5 = 5
    DENSITY__6 = 6
    DENSITY__DEFAULT = DENSITY__5
    # TRANSLATOR_NOTE: QR density option; the parenthetical warns that this densest setting is
    # the hardest for a camera to scan (large screens only). "2" is the pixels-per-module value.
    density_2 = _mft("2 (extreme density — hardest to scan)")
    # TRANSLATOR_NOTE: QR density option; the parenthetical warns that this densest setting is
    # harder for a camera to scan. "3" is the pixels-per-module value and is not translated.
    density_3 = _mft("3 (very high density — harder to scan)")
    # TRANSLATOR_NOTE: QR density option — a bare pixels-per-module number, kept untranslated.
    density_4 = _mft("4")
    # TRANSLATOR_NOTE: QR density option — a bare pixels-per-module number, kept untranslated.
    density_5 = _mft("5")
    # TRANSLATOR_NOTE: QR density option; the parenthetical notes this sparsest setting is
    # easier to scan but carries less data. "6" is the pixels-per-module value, not translated.
    density_6 = _mft("6 (low density — easier to scan, less data)")
    ALL_DENSITIES = [
        (DENSITY__2, density_2),
        (DENSITY__3, density_3),
        (DENSITY__4, density_4),
        (DENSITY__5, density_5),
        (DENSITY__6, density_6),
    ]
    # Legacy Low/Medium/High density values that predate the px/module model. Mapped to
    # DENSITY__DEFAULT on read (see migrate_legacy_qr_density) so an upgrading user's
    # settings.json and any older SettingsQR backups keep loading instead of erroring.
    LEGACY_DENSITIES = ("L", "M", "H")

    # Seed-related constants
    MAINNET = "M"
    TESTNET = "T"
    REGTEST = "R"
    ALL_NETWORKS = [
        (MAINNET, _mft("Mainnet")),
        (TESTNET, _mft("Testnet")),
        (REGTEST, _mft("Regtest"))
    ]

    @classmethod
    def map_network_to_embit(cls, network) -> str:
        # Note these are `embit` constants; do not wrap for translation
        if network == SettingsConstants.MAINNET:
            return "main"
        elif network == SettingsConstants.TESTNET:
            return "test"
        if network == SettingsConstants.REGTEST:
            return "regtest"

    @classmethod
    def migrate_legacy_qr_density(cls, value):
        """Map a legacy Low/Medium/High density value to the current px/module default.

        The QR density model moved from named tiers ("L"/"M"/"H") to integer pixels-per-
        module (3-6). A settings.json or SettingsQR written before that switch carries a
        legacy tier; map any of them to DENSITY__DEFAULT so the value stays valid. Anything
        already in the new model (or anything unrecognized) passes through unchanged.
        """
        if value in cls.LEGACY_DENSITIES:
            return cls.DENSITY__DEFAULT
        return value
    
    PERSISTENT_SETTINGS__SD_INSERTED__HELP_TEXT = _mft("Store Settings on SD card")
    PERSISTENT_SETTINGS__SD_REMOVED__HELP_TEXT = _mft("Insert SD card to enable")

    SINGLE_SIG = "ss"
    MULTISIG = "ms"
    ALL_SIG_TYPES = [
        (SINGLE_SIG, _mft("Single Sig")),
        (MULTISIG, _mft("Multisig")),
    ]

    LEGACY_P2PKH = "leg"
    NATIVE_SEGWIT = "nat"
    NESTED_SEGWIT = "nes"
    TAPROOT = "tr"
    CUSTOM_DERIVATION = "cus"
    ALL_SCRIPT_TYPES = [
        (NATIVE_SEGWIT, _mft("Native Segwit")),
        (NESTED_SEGWIT, _mft("Nested Segwit")),
        (LEGACY_P2PKH, _mft("Legacy")),
        (TAPROOT, _mft("Taproot")),
        (CUSTOM_DERIVATION, _mft("Custom Derivation")),
    ]

    MICROSD_TOAST_TIMER_DISABLED = "D"
    MICROSD_TOAST_TIMER_FIVE_SECONDS = "E"
    MICROSD_TOAST_TIMER_FOREVER = "inf"
    ALL_MICROSD_TOAST_TIMERS = [
        (MICROSD_TOAST_TIMER_DISABLED, _mft("Disabled")),
        # TRANSLATOR_NOTE: MicroSD notification duration setting - Display notification for 5 seconds
        (MICROSD_TOAST_TIMER_FIVE_SECONDS, _mft("5 seconds")),
        # TRANSLATOR_NOTE: MicroSD notification duration setting - Display notification until the SD card is removed
        (MICROSD_TOAST_TIMER_FOREVER, _mft("Until SD removed"))
    ]

    WORDLIST_LANGUAGE__ENGLISH = "en"
    WORDLIST_LANGUAGE__CHINESE_SIMPLIFIED = "zh_Hans_CN"
    WORDLIST_LANGUAGE__CHINESE_TRADITIONAL = "zh_Hant_TW"
    WORDLIST_LANGUAGE__FRENCH = "fr"
    WORDLIST_LANGUAGE__ITALIAN = "it"
    WORDLIST_LANGUAGE__JAPANESE = "jp"
    WORDLIST_LANGUAGE__KOREAN = "kr"
    WORDLIST_LANGUAGE__PORTUGUESE = "pt"
    ALL_WORDLIST_LANGUAGES = [
        (WORDLIST_LANGUAGE__ENGLISH, "English"),
        # (WORDLIST_LANGUAGE__CHINESE_SIMPLIFIED, "简体中文"),
        # (WORDLIST_LANGUAGE__CHINESE_TRADITIONAL, "繁體中文"),
        # (WORDLIST_LANGUAGE__FRENCH, "Français"),
        # (WORDLIST_LANGUAGE__ITALIAN, "Italiano"),
        # (WORDLIST_LANGUAGE__JAPANESE, "日本語"),
        # (WORDLIST_LANGUAGE__KOREAN, "한국어"),
        # (WORDLIST_LANGUAGE__PORTUGUESE, "Português"),
    ]

    # Individual SettingsEntry attr_names
    # Note: attr_names are internal constants; do not wrap for translation
    SETTING__LOCALE = "locale"
    SETTING__WORDLIST_LANGUAGE = "wordlist_language"
    SETTING__PERSISTENT_SETTINGS = "persistent_settings"
    SETTING__XPUB_QR_FORMAT = "xpub_qr"
    SETTING__BTC_DENOMINATION = "denomination"

    SETTING__DISPLAY_CONFIGURATION = "display_config"
    SETTING__DISPLAY_COLOR_INVERTED = "color_inverted"

    SETTING__NETWORK = "network"
    SETTING__QR_DENSITY = "qr_density"
    SETTING__SIG_TYPES = "sig_types"
    SETTING__SCRIPT_TYPES = "script_types"
    SETTING__XPUB_DETAILS = "xpub_details"
    SETTING__PASSPHRASE = "passphrase"
    SETTING__CAMERA_ROTATION = "camera_rotation"
    SETTING__COMPACT_SEEDQR = "compact_seedqr"
    SETTING__BIP85_CHILD_SEEDS = "bip85_child_seeds"
    SETTING__ELECTRUM_SEEDS = "electrum_seeds"
    SETTING__MESSAGE_SIGNING = "message_signing"
    SETTING__PRIVACY_WARNINGS = "privacy_warnings"
    SETTING__DIRE_WARNINGS = "dire_warnings"
    SETTING__QR_BRIGHTNESS_TIPS = "qr_brightness_tips"
    SETTING__PARTNER_LOGOS = "partner_logos"
    SETTING__MICROSD_TOAST_TIMER = "microsd_toast_timer"

    SETTING__DEBUG = "debug"


    # Hardware config settings
    DISPLAY_CONFIGURATION__ST7789__240x240 = "st7789_240x240"  # default; original Waveshare 1.3" display hat
    DISPLAY_CONFIGURATION__ST7789__320x240 = "st7789_320x240"    # natively portrait dimensions; we apply a 90° rotation
    DISPLAY_CONFIGURATION__ILI9341__320x240 = "ili9341_320x240"  # natively portrait dimensions; we apply a 90° rotation
    DISPLAY_CONFIGURATION__ILI9486__480x320 = "ili9486_480x320"  # natively portrait dimensions; we apply a 90° rotation
    ALL_DISPLAY_CONFIGURATIONS = [
        (DISPLAY_CONFIGURATION__ST7789__240x240, "st7789 240x240"),
        (DISPLAY_CONFIGURATION__ST7789__320x240, "st7789 320x240"),
        (DISPLAY_CONFIGURATION__ILI9341__320x240, "ili9341 320x240 (beta)"),
        # (DISPLAY_CONFIGURATION__ILI9486__320x480, "ili9486 480x320"),  # TODO: Enable when ili9486 driver performance is improved
    ]


    # Hidden settings
    SETTING__QR_BRIGHTNESS = "qr_background_color"


    # Structural constants
    # TODO: Not using these for display purposes yet (ever?)
    CATEGORY__SYSTEM = "system"
    CATEGORY__DISPLAY = "display"
    CATEGORY__WALLET = "wallet"
    CATEGORY__FEATURES = "features"

    VISIBILITY__GENERAL = "general"
    VISIBILITY__ADVANCED = "advanced"
    VISIBILITY__HARDWARE = "hardware"
    VISIBILITY__DEVELOPER = "developer"
    VISIBILITY__HIDDEN = "hidden"   # For data-only (e.g. custom_derivation), not configurable by the user

    # TODO: Is there really a difference between ENABLED and PROMPT?
    TYPE__ENABLED_DISABLED = "enabled_disabled"
    TYPE__ENABLED_DISABLED_PROMPT = "enabled_disabled_prompt"
    TYPE__ENABLED_DISABLED_PROMPT_REQUIRED = "enabled_disabled_prompt_required"
    TYPE__SELECT_1 = "select_1"
    TYPE__MULTISELECT = "multiselect"
    TYPE__FREE_ENTRY = "free_entry"

    ALL_ENABLED_DISABLED_TYPES = [
        TYPE__ENABLED_DISABLED,
        TYPE__ENABLED_DISABLED_PROMPT,
        TYPE__ENABLED_DISABLED_PROMPT_REQUIRED,
    ]

    # Electrum seed constants
    ELECTRUM_SEED_STANDARD = "01"
    ELECTRUM_SEED_SEGWIT = "100"
    ELECTRUM_SEED_2FA = "101"
    ELECTRUM_PBKDF2_ROUNDS=2048

    # Label strings
    LABEL__BIP39_PASSPHRASE = _mft("BIP-39 Passphrase")
    # TRANSLATOR_NOTE: Terminology used by Electrum seeds; equivalent to BIP-39 passphrase
    custom_extension = _mft("Custom Extension")
    LABEL__CUSTOM_EXTENSION = custom_extension



class SettingsEntry:
    """
        Defines all the parameters for a single settings entry.

        * category: Mostly for organizational purposes when displaying options in the
            SettingsQR UI. Potentially an additional sub-level breakout in the menus
            on the device itself, too.

        * selection_options: May be specified as a List(Any) or List(tuple(Any, str)).
            The tuple form is to provide a human-readable display_name. Probably all
            entries should shift to using the tuple form.
    """
    # TODO: Handle multi-language `display_name` and `help_text`
    def __init__(self,
                 category: str,
                 attr_name: str,
                 display_name: str,
                 abbreviated_name: str = None,
                 visibility: str = SettingsConstants.VISIBILITY__GENERAL,
                 type: str = SettingsConstants.TYPE__ENABLED_DISABLED,
                 help_text: str = None,
                 selection_options: list = None,
                 default_value: object = None):
        self.category = category
        self.attr_name = attr_name
        self.display_name = display_name
        self.abbreviated_name = abbreviated_name
        self.visibility = visibility
        self.type = type
        self.help_text = help_text
        self.selection_options = selection_options
        self.default_value = default_value
        self.__post_init__()

    def __post_init__(self):
        if self.type == SettingsConstants.TYPE__ENABLED_DISABLED:
            self.selection_options = SettingsConstants.OPTIONS__ENABLED_DISABLED

        elif self.type == SettingsConstants.TYPE__ENABLED_DISABLED_PROMPT:
            self.selection_options = SettingsConstants.OPTIONS__ENABLED_DISABLED_PROMPT

        elif self.type == SettingsConstants.TYPE__ENABLED_DISABLED_PROMPT_REQUIRED:
            self.selection_options = SettingsConstants.ALL_OPTIONS

        # Account for List[tuple] and tuple formats as default_value        
        if type(self.default_value) == list and type(self.default_value[0]) == tuple:
            self.default_value = [v[0] for v in self.default_value]
        elif type(self.default_value) == tuple:
            self.default_value = self.default_value[0]

        if not self.abbreviated_name:
            self.abbreviated_name = self.attr_name


    @property
    def selection_options_display_names(self) -> list[str]:
        if type(self.selection_options[0]) == tuple:
            return [v[1] for v in self.selection_options]
        else:
            # Always return a copy so the original can't be altered
            return list(self.selection_options)


    def get_selection_option_value(self, i: int):
        """ Returns the value of the selection option at index `i` """
        value = self.selection_options[i]
        if type(value) == tuple:
            value = value[0]
        return value

    
    def get_selection_option_display_name_by_value(self, value) -> str:
        for option in self.selection_options:
            if type(option) == tuple:
                option_value = option[0]
                display_name = option[1]
            else:
                option_value = option
                display_name = option
            if option_value == value:
                return _mft(display_name)


    def get_selection_option_value_by_display_name(self, display_name: str):
        for option in self.selection_options:
            if type(option) == tuple:
                option_value = option[0]
                option_display_name = option[1]
            else:
                option_value = option
                option_display_name = option
            if option_display_name == display_name:
                return option_value


    def to_dict(self) -> dict:
        if self.selection_options:
            selection_options = []
            for option in self.selection_options:
                if type(option) == tuple:
                    value = option[0]
                    display_name = option[1]
                else:
                    display_name = option
                    value = option
                selection_options.append({
                    "display_name": display_name,
                    "value": value
                })
        else:
            selection_options = None

        return {
            "category": self.category,
            "attr_name": self.attr_name,
            "abbreviated_name": self.abbreviated_name,
            "display_name": self.display_name,
            "visibility": self.visibility,
            "type": self.type,
            "help_text": self.help_text,
            "selection_options": selection_options,
            "default_value": self.default_value,
        }



class SettingsDefinition:
    """
        Master list of all settings, their possible options, their defaults, on-device
        display strings, and enriched SettingsQR UI options.

        Used to auto-build the Settings UI menuing with no repetitive boilerplate code.

        Defines the on-disk persistent storage structure and can read that format back
        and validate the values.

        Used to generate a master json file that documents all these params which can
        then be read in by the SettingsQR UI to auto-generate the necessary html inputs.
    """
    # Increment if there are any breaking changes; write migrations to bridge from
    # incompatible prior versions.
    version: int = 1

    settings_entries: list[SettingsEntry] = [
        # General options

        SettingsEntry(category=SettingsConstants.CATEGORY__SYSTEM,
                      attr_name=SettingsConstants.SETTING__LOCALE,
                      abbreviated_name="lang",
                      display_name=_mft("Language"),
                      type=SettingsConstants.TYPE__SELECT_1,
                      selection_options=SettingsConstants.get_detected_languages(),
                      default_value=SettingsConstants.LOCALE__ENGLISH),

        # TODO: Support other BIP-39 wordlist languages! Until then, type == HIDDEN
        SettingsEntry(category=SettingsConstants.CATEGORY__SYSTEM,
                      attr_name=SettingsConstants.SETTING__WORDLIST_LANGUAGE,
                      abbreviated_name="wordlist_lang",
                      display_name=_mft("Mnemonic language"),
                      type=SettingsConstants.TYPE__SELECT_1,
                      visibility=SettingsConstants.VISIBILITY__HIDDEN,
                      selection_options=SettingsConstants.ALL_WORDLIST_LANGUAGES,
                      default_value=SettingsConstants.WORDLIST_LANGUAGE__ENGLISH),

        SettingsEntry(category=SettingsConstants.CATEGORY__SYSTEM,
                      attr_name=SettingsConstants.SETTING__PERSISTENT_SETTINGS,
                      abbreviated_name="persistent",
                      display_name=_mft("Persistent settings"),
                      help_text=SettingsConstants.PERSISTENT_SETTINGS__SD_INSERTED__HELP_TEXT,
                      default_value=SettingsConstants.OPTION__DISABLED),

        SettingsEntry(category=SettingsConstants.CATEGORY__SYSTEM,
                      attr_name=SettingsConstants.SETTING__BTC_DENOMINATION,
                      abbreviated_name="denom",
                      display_name=_mft("Denomination display"),
                      type=SettingsConstants.TYPE__SELECT_1,
                      selection_options=SettingsConstants.ALL_BTC_DENOMINATIONS,
                      default_value=SettingsConstants.BTC_DENOMINATION__THRESHOLD),
     

        # Advanced options
        SettingsEntry(category=SettingsConstants.CATEGORY__FEATURES,
                      attr_name=SettingsConstants.SETTING__NETWORK,
                      display_name=_mft("Bitcoin network"),
                      type=SettingsConstants.TYPE__SELECT_1,
                      visibility=SettingsConstants.VISIBILITY__ADVANCED,
                      selection_options=SettingsConstants.ALL_NETWORKS,
                      default_value=SettingsConstants.MAINNET),

        SettingsEntry(category=SettingsConstants.CATEGORY__FEATURES,
                      attr_name=SettingsConstants.SETTING__QR_DENSITY,
                      display_name=_mft("QR density (pixels per module)"),
                      type=SettingsConstants.TYPE__SELECT_1,
                      visibility=SettingsConstants.VISIBILITY__ADVANCED,
                      selection_options=SettingsConstants.ALL_DENSITIES,
                      default_value=SettingsConstants.DENSITY__DEFAULT),

        SettingsEntry(category=SettingsConstants.CATEGORY__FEATURES,
                      attr_name=SettingsConstants.SETTING__SIG_TYPES,
                      abbreviated_name="sigs",
                      display_name=_mft("Sig types"),
                      type=SettingsConstants.TYPE__MULTISELECT,
                      visibility=SettingsConstants.VISIBILITY__ADVANCED,
                      selection_options=SettingsConstants.ALL_SIG_TYPES,
                      default_value=SettingsConstants.ALL_SIG_TYPES),

        SettingsEntry(category=SettingsConstants.CATEGORY__FEATURES,
                      attr_name=SettingsConstants.SETTING__SCRIPT_TYPES,
                      abbreviated_name="scripts",
                      display_name=_mft("Script types"),
                      type=SettingsConstants.TYPE__MULTISELECT,
                      visibility=SettingsConstants.VISIBILITY__ADVANCED,
                      selection_options=SettingsConstants.ALL_SCRIPT_TYPES,
                      default_value=[SettingsConstants.NATIVE_SEGWIT, SettingsConstants.NESTED_SEGWIT, SettingsConstants.TAPROOT]),

        SettingsEntry(category=SettingsConstants.CATEGORY__FEATURES,
                      attr_name=SettingsConstants.SETTING__XPUB_QR_FORMAT,
                      display_name=_mft("Xpub QR format"),
                      visibility=SettingsConstants.VISIBILITY__ADVANCED,
                      type=SettingsConstants.TYPE__MULTISELECT,
                      selection_options=SettingsConstants.ALL_XPUB_QR_FORMATS,
                      default_value=[
                            SettingsConstants.XPUB_QR_FORMAT__UR_CRYPTO_ACCOUNT,
                            SettingsConstants.XPUB_QR_FORMAT__STATIC,
                      ]),

        SettingsEntry(category=SettingsConstants.CATEGORY__FEATURES,
                      attr_name=SettingsConstants.SETTING__XPUB_DETAILS,
                      display_name=_mft("Show xpub details"),
                      visibility=SettingsConstants.VISIBILITY__ADVANCED,
                      default_value=SettingsConstants.OPTION__ENABLED),

        SettingsEntry(category=SettingsConstants.CATEGORY__FEATURES,
                      attr_name=SettingsConstants.SETTING__PASSPHRASE,
                      display_name=_mft("BIP-39 passphrase"),
                      type=SettingsConstants.TYPE__SELECT_1,
                      visibility=SettingsConstants.VISIBILITY__ADVANCED,
                      selection_options=SettingsConstants.OPTIONS__ENABLED_DISABLED_REQUIRED,
                      default_value=SettingsConstants.OPTION__ENABLED),

        SettingsEntry(category=SettingsConstants.CATEGORY__FEATURES,
                      attr_name=SettingsConstants.SETTING__CAMERA_ROTATION,
                      abbreviated_name="camera",
                      display_name=_mft("Camera rotation"),
                      type=SettingsConstants.TYPE__SELECT_1,
                      visibility=SettingsConstants.VISIBILITY__ADVANCED,
                      selection_options=SettingsConstants.ALL_CAMERA_ROTATIONS,
                      # The native camera engines rotate clockwise and already apply a 90°
                      # sensor-mount base, which is correct alignment for the default build;
                      # this setting is the delta added on top, so the default is no delta.
                      # (The retired PIL path rotated counter-clockwise, where the
                      # equivalent default was 180.)
                      default_value=SettingsConstants.CAMERA_ROTATION__0),

        SettingsEntry(category=SettingsConstants.CATEGORY__FEATURES,
                      attr_name=SettingsConstants.SETTING__COMPACT_SEEDQR,
                      display_name=_mft("Compact SeedQR"),
                      visibility=SettingsConstants.VISIBILITY__ADVANCED,
                      default_value=SettingsConstants.OPTION__ENABLED),

        SettingsEntry(category=SettingsConstants.CATEGORY__FEATURES,
                      attr_name=SettingsConstants.SETTING__BIP85_CHILD_SEEDS,
                      abbreviated_name="bip85",
                      display_name=_mft("BIP-85 child seeds"),
                      visibility=SettingsConstants.VISIBILITY__ADVANCED,
                      default_value=SettingsConstants.OPTION__DISABLED),

        SettingsEntry(category=SettingsConstants.CATEGORY__FEATURES,
                      attr_name=SettingsConstants.SETTING__ELECTRUM_SEEDS,
                      abbreviated_name="electrum",
                      display_name=_mft("Electrum seeds"),
                      help_text=_mft("Native Segwit only"),
                      visibility=SettingsConstants.VISIBILITY__ADVANCED,
                      default_value=SettingsConstants.OPTION__DISABLED),
        
        SettingsEntry(category=SettingsConstants.CATEGORY__FEATURES,
                      attr_name=SettingsConstants.SETTING__MICROSD_TOAST_TIMER,
                      display_name=_mft("MicroSD notification duration"),
                      type=SettingsConstants.TYPE__SELECT_1,
                      visibility=SettingsConstants.VISIBILITY__ADVANCED,
                      selection_options=SettingsConstants.ALL_MICROSD_TOAST_TIMERS,
                      default_value=SettingsConstants.MICROSD_TOAST_TIMER_FIVE_SECONDS),

        SettingsEntry(category=SettingsConstants.CATEGORY__FEATURES,
                      attr_name=SettingsConstants.SETTING__MESSAGE_SIGNING,
                      display_name=_mft("Message signing"),
                      visibility=SettingsConstants.VISIBILITY__ADVANCED,
                      default_value=SettingsConstants.OPTION__DISABLED),

        SettingsEntry(category=SettingsConstants.CATEGORY__FEATURES,
                      attr_name=SettingsConstants.SETTING__PRIVACY_WARNINGS,
                      abbreviated_name="priv_warn",
                      display_name=_mft("Show privacy warnings"),
                      visibility=SettingsConstants.VISIBILITY__ADVANCED,
                      default_value=SettingsConstants.OPTION__ENABLED),

        SettingsEntry(category=SettingsConstants.CATEGORY__FEATURES,
                      attr_name=SettingsConstants.SETTING__DIRE_WARNINGS,
                      abbreviated_name="dire_warn",
                      display_name=_mft("Show dire warnings"),
                      visibility=SettingsConstants.VISIBILITY__ADVANCED,
                      default_value=SettingsConstants.OPTION__ENABLED),

        SettingsEntry(category=SettingsConstants.CATEGORY__FEATURES,
                      attr_name=SettingsConstants.SETTING__QR_BRIGHTNESS_TIPS,
                      display_name=_mft("Show QR brightness tips"),
                      visibility=SettingsConstants.VISIBILITY__ADVANCED,
                      default_value=SettingsConstants.OPTION__ENABLED),

        SettingsEntry(category=SettingsConstants.CATEGORY__FEATURES,
                      attr_name=SettingsConstants.SETTING__PARTNER_LOGOS,
                      abbreviated_name="partners",
                      display_name=_mft("Show partner logos"),
                      visibility=SettingsConstants.VISIBILITY__ADVANCED,
                      default_value=SettingsConstants.OPTION__ENABLED),


        # Hardware config
        SettingsEntry(category=SettingsConstants.CATEGORY__SYSTEM,
                      attr_name=SettingsConstants.SETTING__DISPLAY_CONFIGURATION,
                      abbreviated_name="disp_conf",
                      # TRANSLATOR_NOTE: Hardware settings option to specify the screen driver (e.g. st7789 vs ili9341)
                      display_name=_mft("Display type"),
                      type=SettingsConstants.TYPE__SELECT_1,
                      visibility=SettingsConstants.VISIBILITY__HARDWARE,
                      selection_options=SettingsConstants.ALL_DISPLAY_CONFIGURATIONS,
                      default_value=SettingsConstants.DISPLAY_CONFIGURATION__ST7789__240x240),

        SettingsEntry(category=SettingsConstants.CATEGORY__SYSTEM,
                      attr_name=SettingsConstants.SETTING__DISPLAY_COLOR_INVERTED,
                      abbreviated_name="rgb_inv",
                      # TRANSLATOR_NOTE: Hardware settings option to invert how the screen driver displays colors.
                      display_name=_mft("Invert colors"),
                      type=SettingsConstants.TYPE__ENABLED_DISABLED,
                      visibility=SettingsConstants.VISIBILITY__HARDWARE,
                      default_value=SettingsConstants.OPTION__DISABLED),


        # Developer options
        # TODO: No real Developer options needed yet. Disable for now.
        # SettingsEntry(category=SettingsConstants.CATEGORY__SYSTEM,
        #               attr_name=SettingsConstants.SETTING__DEBUG,
        #               display_name="Debug",
        #               visibility=SettingsConstants.VISIBILITY__DEVELOPER,
        #               default_value=SettingsConstants.OPTION__DISABLED),
        
        # "Hidden" settings with no UI interaction
        SettingsEntry(category=SettingsConstants.CATEGORY__SYSTEM,
                      attr_name=SettingsConstants.SETTING__QR_BRIGHTNESS,
                      abbreviated_name="qr_brightness",
                      display_name=_mft("QR background color"),
                      type=SettingsConstants.TYPE__FREE_ENTRY,
                      visibility=SettingsConstants.VISIBILITY__HIDDEN,
                      default_value=62),
    ]


    @classmethod
    def get_settings_entries(cls, visibility: str = SettingsConstants.VISIBILITY__GENERAL) -> list[SettingsEntry]:
        entries = []
        for entry in cls.settings_entries:
            if entry.visibility == visibility:
                entries.append(entry)
        return entries
    

    @classmethod
    def get_settings_entry(cls, attr_name) -> SettingsEntry:
        for entry in cls.settings_entries:
            if entry.attr_name == attr_name:
                return entry


    @classmethod
    def get_settings_entry_by_abbreviated_name(cls, abbreviated_name: str) -> SettingsEntry:
        for entry in cls.settings_entries:
            if abbreviated_name in [entry.abbreviated_name, entry.attr_name]:
                return entry


    @classmethod
    def get_defaults(cls, use_abbreviated_name: bool = False, skip_hidden: bool = False) -> dict:
        """
        * use_abbreviated_name: Only used by the test suite.
        * skip_hidden: Only used by the test suite.
        """
        as_dict = {}
        for entry in SettingsDefinition.settings_entries:
            if skip_hidden and entry.visibility == SettingsConstants.VISIBILITY__HIDDEN:
                continue
            if not use_abbreviated_name:
                attr_name = entry.attr_name
            else:
                attr_name = entry.abbreviated_name
            if type(entry.default_value) == list:
                # Must copy the default_value list, otherwise we'll inadvertently change
                # defaults when updating these attrs
                as_dict[attr_name] = list(entry.default_value)
            else:
                as_dict[attr_name] = entry.default_value
        return as_dict


    @classmethod
    def to_dict(cls) -> dict:
        output = {
            "settings_entries": [],
        }
        for settings_entry in cls.settings_entries:
            output["settings_entries"].append(settings_entry.to_dict())
        
        return output
