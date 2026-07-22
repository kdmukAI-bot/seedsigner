"""
MicroPython-safe builder functions for the reusable LVGL screen-config sub-objects
(``top_nav``, ``btc_amount``) plus the integer-only amount formatters that feed them.

Two kinds of structured JSON recur across many native screens: the ``top_nav`` object
(every top-nav screen has one) and the ``btc_amount`` readout (the PSBT overview / detail
screens). Rather than inline/duplicate their shape at each call site, they are built here
so there is one definition of each. These are plain functions returning the wire-format
``dict`` — not ``@dataclass`` structs — because this module runs on **both** CPython 3.10
(Pi Zero) and MicroPython 1.27 (ESP32-S3), and MicroPython has no ``dataclasses`` /
``typing`` / ``decimal`` (see ``docs/micropython_compatibility.md``).

The formatting split follows the rest of the screen contract: the native ``btc_amount``
component is a **pure renderer** (it only lays out the pieces), so the host formats every
value here and passes the finished strings. **All amount math is integer/string only** —
never float. On the ESP32 build ``float`` is single-precision (32-bit), so a satoshi value
held as float is corrupt, and a Bitcoin amount crossing the JSON contract is too important
to risk any float rounding. ``btc_amount_from_sats`` is a faithful integer-math port of
``gui.components.BtcAmount``'s denomination logic so both platforms format identically.

See ``docs/architecture/view-to-screen-json-contract.md`` (the runner contract).
"""
from seedsigner.compat.l10n import gettext as _

# Satoshi arithmetic constants — integer literals only (no 1e6 / 1e8 float forms, which
# are not exactly representable in single-precision float on the ESP32).
SATS_PER_BTC = 100_000_000
# BtcAmount's denomination thresholds: show btc at/above 1,000,000 sats (threshold mode),
# force btc above 10,000,000,000 sats regardless of setting, and drop the sats digits to a
# smaller font above 1,000,000,000 sats.
_BTC_THRESHOLD_SATS = 1_000_000
_BTC_FORCE_SATS = 10_000_000_000
_SATS_SMALL_FONT_SATS = 1_000_000_000


# Pillow accepted CSS color *names* (e.g. "red", "blue") wherever a PIL screen took a fill
# color; the native LVGL screens parse only 6-digit hex. Translate the names the app
# actually uses for button/icon styling. Values already in "#rrggbb" form pass through
# (every GUIConstants color is 6-digit hex).
_LVGL_NAMED_COLORS = {
    "red": "#ff0000",
    "blue": "#0000ff",
}


def _lvgl_color(color):
    """Map a PIL color name/hex to the 6-digit hex the native screens require.

    None/empty -> None (caller omits the key, native applies its default).
    """
    if not color:
        return None
    if color.startswith("#"):
        return color
    return _LVGL_NAMED_COLORS.get(color.lower(), color)


def top_nav(title=None, show_back_button=None, show_power_button=None,
            icon=None, icon_color=None):
    """Build the native ``top_nav`` cfg sub-object from the flat top-nav attrs.

    Only the sub-keys the caller set are present (``None`` == omit; the native side then
    applies its own default). ``icon_color`` is color-mapped via ``_lvgl_color``.
    """
    cfg = {}
    if title is not None:
        cfg["title"] = title
    if show_back_button is not None:
        cfg["show_back_button"] = show_back_button
    if show_power_button is not None:
        cfg["show_power_button"] = show_power_button
    if icon is not None:
        cfg["icon"] = icon
    if icon_color is not None:
        cfg["icon_color"] = _lvgl_color(icon_color)
    return cfg


def btc_amount(primary, unit, secondary=None, network="mainnet", primary_small=False):
    """Build a native ``btc_amount`` cfg sub-object from already-formatted pieces.

    ``primary`` (the amount digits) and ``unit`` are host-formatted strings. ``secondary``
    is the btcsatshybrid trailing-sats run (drawn after a "|" separator; omitted when
    falsy). ``network`` is the SettingsConstants network code ("M"/"T"/"R", which the
    native side maps to the icon color; the long names "mainnet"/... are also accepted).
    ``primary_small`` renders the primary digits one step smaller (very large sats amounts).
    """
    cfg = {"primary": primary, "unit": unit, "network": network}
    if secondary:
        cfg["secondary"] = secondary
    if primary_small:
        cfg["primary_small"] = True
    return cfg


def _group_thousands(value):
    """Comma-group an integer's digits: 1234567 -> "1,234,567". Integer/string math only
    (MicroPython's format mini-language has no "," grouping option)."""
    negative = value < 0
    s = str(-value if negative else value)
    groups = []
    while len(s) > 3:
        groups.append(s[-3:])
        s = s[:-3]
    groups.append(s)
    grouped = ",".join(reversed(groups))
    return "-" + grouped if negative else grouped


def format_sats(total_sats):
    """Format a satoshi amount as comma-grouped digits: 841234 -> "841,234"."""
    return _group_thousands(total_sats)


def format_btc(total_sats):
    """Format a satoshi amount as a full 8-decimal BTC string with the whole part
    comma-grouped: 2046767903 -> "20.46767903". Integer divmod only (parity with the PIL
    ``f"{sats/1e8:,.8f}"`` without the float step)."""
    whole = total_sats // SATS_PER_BTC
    frac = total_sats % SATS_PER_BTC
    return "{}.{:08d}".format(_group_thousands(whole), frac)


def btc_amount_from_sats(total_sats, denomination=None, network=None):
    """Build a ``btc_amount`` cfg from a raw satoshi amount, per the user's
    SETTING__BTC_DENOMINATION + SETTING__NETWORK (read from Settings when not passed).

    A faithful integer-math port of ``gui.components.BtcAmount``'s denomination logic
    (btc / sats / threshold / btcsatshybrid), producing the pre-formatted ``primary`` /
    ``secondary`` / ``unit`` strings the pure-renderer native component draws. No float.
    """
    from seedsigner.models.settings import Settings, SettingsConstants
    if denomination is None:
        denomination = Settings.get_instance().get_value(SettingsConstants.SETTING__BTC_DENOMINATION)
    if network is None:
        network = Settings.get_instance().get_value(SettingsConstants.SETTING__NETWORK)

    # Units follow the network: mainnet uses "btc"/"sats", testnet and regtest both use the
    # "tBtc"/"tSats" testnet units (only the icon color differs between them, and that is the
    # native side's job via `network`). Same msgids as BtcAmount so translations carry over.
    if network == SettingsConstants.MAINNET:
        # TRANSLATOR_NOTE: Abbreviation for Bitcoin
        btc_unit = _("btc")
        # TRANSLATOR_NOTE: Abbreviation for satoshis
        sats_unit = _("sats")
    else:
        # TRANSLATOR_NOTE: Testnet bitcoin
        btc_unit = _("tBtc")
        # TRANSLATOR_NOTE: Testnet sats
        sats_unit = _("tSats")

    s = str(total_sats)

    # --- Which denomination gets displayed (mirrors BtcAmount's branch conditions). ---
    show_btc = (
        denomination == SettingsConstants.BTC_DENOMINATION__BTC
        or (denomination == SettingsConstants.BTC_DENOMINATION__THRESHOLD
            and total_sats >= _BTC_THRESHOLD_SATS)
        or (denomination == SettingsConstants.BTC_DENOMINATION__BTCSATSHYBRID
            and total_sats >= _BTC_THRESHOLD_SATS and s[-6:] == "000000")
        or total_sats > _BTC_FORCE_SATS
    )

    if show_btc:
        whole = total_sats // SATS_PER_BTC
        frac8 = "{:08d}".format(total_sats % SATS_PER_BTC)
        if s[-8:] == "00000000":
            # Only whole btc units; a single trailing decimal place (BtcAmount: quantize 0.1).
            decimals = "0"
        elif s[-6:] == "000000":
            # Bottom six digits all zero; two decimal places (BtcAmount: quantize 0.12).
            decimals = frac8[:2]
        else:
            decimals = frac8
        text = "{}.{}".format(_group_thousands(whole), decimals)
        if len(text) >= 12:
            # Too wide to fit; keep the whole part + two decimals, then an ellipsis.
            int_part = text.split(".")[0]
            frac_part = text.split(".")[-1]
            text = int_part + "." + frac_part[:2] + "..."
        return btc_amount(primary=text, unit=btc_unit, network=network)

    if (denomination == SettingsConstants.BTC_DENOMINATION__BTCSATSHYBRID
            and total_sats >= _BTC_THRESHOLD_SATS):
        # Hybrid: two-decimal btc primary + the trailing (comma-grouped) sats run, drawn
        # smaller, with the native side inserting the "|" separator.
        whole = total_sats // SATS_PER_BTC
        frac8 = "{:08d}".format(total_sats % SATS_PER_BTC)
        primary = "{}.{}".format(_group_thousands(whole), frac8[:2])
        secondary = _group_thousands(total_sats)[-7:]
        while secondary and secondary[0] == "0":
            secondary = secondary[1:]
        return btc_amount(primary=primary, unit=sats_unit, secondary=secondary,
                          network=network, primary_small=True)

    # Sats: comma-grouped digits; very large amounts drop to the smaller font.
    return btc_amount(
        primary=_group_thousands(total_sats),
        unit=sats_unit,
        network=network,
        primary_small=total_sats > _SATS_SMALL_FONT_SATS,
    )
