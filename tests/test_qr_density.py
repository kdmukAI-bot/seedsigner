"""Tests for the resolution-aware animated-QR density lookup (models/qr_density.py)."""
import importlib.util
import os

import pytest

from seedsigner.models.qr_density import (
    QR_DENSITY_BY_RESOLUTION,
    QR_PX_PER_MODULE_MIN,
    QR_PX_PER_MODULE_MAX,
    max_fragment_len_for,
    nearest_resolution,
)


# The worksheet (tools/qr_density_worksheet.py) is the table's source of truth, but it is an
# untracked dev tool that may be absent on a fresh checkout / CI. Load it lazily and skip the
# cross-check when it's missing, rather than erroring at collection time.
_WORKSHEET_PATH = os.path.join(
    os.path.dirname(__file__), "..", "tools", "qr_density_worksheet.py"
)


def _load_worksheet():
    spec = importlib.util.spec_from_file_location("qr_density_worksheet", _WORKSHEET_PATH)
    worksheet = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worksheet)
    return worksheet


BAND = list(range(QR_PX_PER_MODULE_MIN, QR_PX_PER_MODULE_MAX + 1))  # [2, 3, 4, 5, 6]
RESOLUTIONS = sorted(QR_DENSITY_BY_RESOLUTION)


def test_band_is_two_through_six():
    assert (QR_PX_PER_MODULE_MIN, QR_PX_PER_MODULE_MAX) == (2, 6)


def test_every_resolution_covers_the_full_band():
    for res, table in QR_DENSITY_BY_RESOLUTION.items():
        assert sorted(table) == BAND, f"resolution {res} must define exactly px {BAND}"


@pytest.mark.parametrize("res", RESOLUTIONS)
def test_resolver_returns_table_values_for_listed_resolutions(res):
    for px in BAND:
        assert max_fragment_len_for(res, px) == QR_DENSITY_BY_RESOLUTION[res][px]


@pytest.mark.parametrize("res", RESOLUTIONS)
def test_bigger_px_per_module_means_fewer_or_equal_bytes(res):
    # Readability monotonicity: larger modules (higher px/module) => no more data per frame.
    # Strictly fewer for every step except the 720px/2px alias (see the dedicated test below).
    for smaller_px, bigger_px in zip(BAND, BAND[1:]):
        assert max_fragment_len_for(res, bigger_px) <= max_fragment_len_for(res, smaller_px)


def test_px2_is_a_distinct_densest_step_except_on_720():
    # 2 px/module is the extreme step. On 240/320/480 it's strictly denser than 3 px/module;
    # on 720 the biggest QR (v40) already renders at 3 px/module, so 2 px/module is unreachable
    # and its cell aliases to the 3 px/module value.
    for res in (240, 320, 480):
        assert QR_DENSITY_BY_RESOLUTION[res][2] > QR_DENSITY_BY_RESOLUTION[res][3]
    assert QR_DENSITY_BY_RESOLUTION[720][2] == QR_DENSITY_BY_RESOLUTION[720][3]


@pytest.mark.parametrize(
    "unlisted, expected",
    [
        (200, 240),   # below the lowest supported
        (300, 320),   # closer to 320 than 240
        (500, 480),   # just above 480
        (700, 720),   # closer to 720 than 480
        (1000, 720),  # above the highest supported
    ],
)
def test_nearest_resolution_snaps_unlisted_panels(unlisted, expected):
    assert nearest_resolution(unlisted) == expected


def test_resolver_falls_back_to_nearest_for_unlisted_resolution():
    # A 500px panel isn't in the table; it must resolve via the 480px row.
    for px in BAND:
        assert max_fragment_len_for(500, px) == QR_DENSITY_BY_RESOLUTION[480][px]


@pytest.mark.skipif(
    not os.path.exists(_WORKSHEET_PATH),
    reason="tools/qr_density_worksheet.py (untracked source-of-truth tool) not present",
)
def test_baked_table_matches_worksheet_generator():
    # The worksheet is the source of truth; the baked constant must never drift from it.
    worksheet = _load_worksheet()
    for res in RESOLUTIONS:
        for px in BAND:
            version = worksheet.selected_version_for(px, res)
            if version is None:
                # No QR renders at exactly this px on this panel (720px/2px: v40 is the densest
                # QR and already renders at 3px). That cell is a documented alias to the nearest
                # reachable denser step; the worksheet can't derive it, so it's covered by
                # test_px2_is_a_distinct_densest_step_except_on_720 instead.
                continue
            expected = worksheet.max_density_for_version(version)
            assert QR_DENSITY_BY_RESOLUTION[res][px] == expected, (
                f"table[{res}][{px}]={QR_DENSITY_BY_RESOLUTION[res][px]} "
                f"but worksheet derives {expected} (v{version}); regenerate the table"
            )
