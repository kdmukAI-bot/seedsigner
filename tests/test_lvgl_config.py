"""Unit tests for `seedsigner.gui.lvgl_config` — the MicroPython-safe cfg builders
(`top_nav`, `btc_amount`) and the integer-only amount formatters that feed the native
PSBT screens.

The formatters are a faithful port of `gui.components.BtcAmount`'s denomination logic,
done with integer/string math (no `Decimal`, no `/1e8`) so both platforms format
identically and a satoshi value never touches single-precision float. These tests pin the
per-denomination output so a regression in that port is caught here rather than on-device.
"""
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("seedsigner.hardware.buttons", MagicMock())

from seedsigner.gui.lvgl_config import (
    top_nav, btc_amount, btc_amount_from_sats,
    format_sats, format_btc, _group_thousands, _lvgl_color,
)
from seedsigner.models.settings import SettingsConstants

M = SettingsConstants.MAINNET
T = SettingsConstants.TESTNET
R = SettingsConstants.REGTEST
BTC = SettingsConstants.BTC_DENOMINATION__BTC
SATS = SettingsConstants.BTC_DENOMINATION__SATS
THR = SettingsConstants.BTC_DENOMINATION__THRESHOLD
HYB = SettingsConstants.BTC_DENOMINATION__BTCSATSHYBRID


# ---------------------------------------------------------------------------
# integer/string formatters
# ---------------------------------------------------------------------------

def test_group_thousands():
    assert _group_thousands(0) == "0"
    assert _group_thousands(999) == "999"
    assert _group_thousands(1000) == "1,000"
    assert _group_thousands(841234) == "841,234"
    assert _group_thousands(1000000) == "1,000,000"
    assert _group_thousands(2046767903) == "2,046,767,903"
    assert _group_thousands(-1234567) == "-1,234,567"


def test_format_sats():
    assert format_sats(272) == "272"
    assert format_sats(841234) == "841,234"
    assert format_sats(1990245069) == "1,990,245,069"


def test_format_btc_is_full_eight_decimals_via_integer_math():
    # Parity with the PIL math screen's f"{sats/1e8:,.8f}" — but integer divmod, no float.
    assert format_btc(0) == "0.00000000"
    assert format_btc(100000000) == "1.00000000"
    assert format_btc(2046767903) == "20.46767903"
    assert format_btc(123456789012) == "1,234.56789012"


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------

def test_top_nav_omits_unset_and_maps_icon_color():
    assert top_nav() == {}
    assert top_nav(title="X") == {"title": "X"}
    assert top_nav(title="X", show_back_button=False) == {"title": "X", "show_back_button": False}
    assert top_nav(icon="fingerprint", icon_color="red") == {"icon": "fingerprint", "icon_color": "#ff0000"}


def test_btc_amount_builder_shape():
    assert btc_amount("0.02", "btc", network="M") == {
        "primary": "0.02", "unit": "btc", "network": "M",
    }
    # secondary + primary_small only appear when truthy.
    assert btc_amount("1.23", "sats", secondary="456,789", network="M", primary_small=True) == {
        "primary": "1.23", "unit": "sats", "network": "M",
        "secondary": "456,789", "primary_small": True,
    }


def test_lvgl_color_maps_names_and_passes_hex():
    assert _lvgl_color("red") == "#ff0000"
    assert _lvgl_color("blue") == "#0000ff"
    assert _lvgl_color("#30D158") == "#30D158"
    assert _lvgl_color(None) is None


# ---------------------------------------------------------------------------
# btc_amount_from_sats - denomination parity (BtcAmount port)
# ---------------------------------------------------------------------------

def test_sats_denomination():
    assert btc_amount_from_sats(841234, SATS, M) == {
        "primary": "841,234", "unit": "sats", "network": "M",
    }
    # Above 1e9 sats -> smaller primary font.
    got = btc_amount_from_sats(2000000000, SATS, M)
    assert got["primary"] == "2,000,000,000"
    assert got["primary_small"] is True
    assert got["unit"] == "sats"


def test_btc_denomination_full_and_truncated():
    # Full 8 decimals.
    assert btc_amount_from_sats(123456789, BTC, M) == {
        "primary": "1.23456789", "unit": "btc", "network": "M",
    }
    # Whole btc -> single trailing decimal place.
    assert btc_amount_from_sats(100000000, BTC, M)["primary"] == "1.0"
    # Bottom six digits zero -> two decimal places.
    assert btc_amount_from_sats(150000000, BTC, M)["primary"] == "1.50"
    # Too wide -> whole + two decimals + ellipsis.
    assert btc_amount_from_sats(123456789012, BTC, M)["primary"] == "1,234.56..."


def test_threshold_denomination_switches_at_one_million_sats():
    # Below 1,000,000 sats -> sats.
    assert btc_amount_from_sats(999999, THR, M)["unit"] == "sats"
    assert btc_amount_from_sats(999999, THR, M)["primary"] == "999,999"
    # At/above 1,000,000 sats -> btc.
    got = btc_amount_from_sats(2000000, THR, M)
    assert got["unit"] == "btc"
    assert got["primary"] == "0.02"


def test_hybrid_denomination():
    # >= 1e6 and not a round million -> hybrid: 2-decimal btc primary + trailing sats.
    got = btc_amount_from_sats(123456789, HYB, M)
    assert got == {
        "primary": "1.23", "unit": "sats", "network": "M",
        "secondary": "456,789", "primary_small": True,
    }
    # A round-million hybrid value collapses to pure btc (BtcAmount branch 1).
    assert btc_amount_from_sats(2000000, HYB, M) == {
        "primary": "0.02", "unit": "btc", "network": "M",
    }
    # Below 1e6 -> sats.
    assert btc_amount_from_sats(500000, HYB, M)["unit"] == "sats"


def test_force_btc_above_ten_billion_sats():
    # Above 10,000,000,000 sats forces btc even under the sats denomination.
    got = btc_amount_from_sats(10000000001, SATS, M)
    assert got["unit"] == "btc"


def test_testnet_and_regtest_units_and_network_code():
    tn = btc_amount_from_sats(841234, THR, T)
    assert tn["unit"] == "tSats"
    assert tn["network"] == "T"
    rt = btc_amount_from_sats(2000000, THR, R)
    assert rt["unit"] == "tBtc"
    assert rt["network"] == "R"


def test_reads_settings_when_denomination_and_network_omitted(monkeypatch):
    fake_settings = MagicMock()
    fake_settings.get_value.side_effect = lambda key: {
        SettingsConstants.SETTING__BTC_DENOMINATION: SATS,
        SettingsConstants.SETTING__NETWORK: M,
    }[key]
    monkeypatch.setattr(
        "seedsigner.models.settings.Settings.get_instance",
        lambda: fake_settings,
    )
    assert btc_amount_from_sats(841234) == {
        "primary": "841,234", "unit": "sats", "network": "M",
    }
