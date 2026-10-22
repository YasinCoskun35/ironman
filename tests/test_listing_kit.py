"""
Unit tests for the pure helpers in listing_kit.py: size ladder, Etsy Size
values, the price formula and the SKU ledger.

The mockup and cutting stages are exercised by running the kit on a real
design; what this suite guards is the arithmetic a listing quotes to buyers.
"""

import datetime as dt
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import listing_kit as lk


class TestSizeLadder:
    def test_six_sizes_from_min_to_150(self):
        s = lk.size_ladder(45, 0.5, horizontal=True)
        assert [x.name for x in s] == lk.SIZE_NAMES_6
        assert s[0].long_cm == 45 and s[-1].long_cm == 150
        assert all(x.long_cm % 5 == 0 for x in s[1:-1])
        assert all(a.long_cm < b.long_cm for a, b in zip(s, s[1:]))

    def test_five_sizes_when_range_under_60(self):
        s = lk.size_ladder(100, 0.5, horizontal=True)
        assert [x.name for x in s] == lk.SIZE_NAMES_6[1:]

    def test_minimum_rounds_up_never_down(self):
        # 41.2 cm is the smallest size the design survives at; 40 would alter it.
        assert lk.size_ladder(41.2, 0.5, horizontal=True)[0].long_cm == 42

    def test_privacy_panels_go_to_240(self):
        assert lk.size_ladder(70, 0.5, horizontal=False, product="privacy")[-1].long_cm == 240

    def test_square_rule(self):
        s = lk.size_ladder(40, 0.98, horizontal=True)
        assert all(x.width_cm == x.height_cm for x in s)

    def test_orientation_sets_width_and_height(self):
        v = lk.size_ladder(60, 0.5, horizontal=False)[0]
        assert (v.width_cm, v.height_cm) == (30, 60)

    def test_no_two_sizes_share_inches(self):
        s = lk.size_ladder(30, 0.3, horizontal=True)
        assert len({(x.width_in, x.height_in) for x in s}) == len(s)

    def test_min_above_top_gives_one_size(self):
        assert len(lk.size_ladder(160, 0.5, horizontal=True)) == 1


class TestSizeValue:
    def test_fits_etsy_limit(self):
        for s in lk.size_ladder(30, 0.31, horizontal=True) + lk.size_ladder(70, 0.5, False, "privacy"):
            assert len(s.value) <= lk.SIZE_VALUE_MAX, s.value

    def test_keeps_spaces_when_short_enough(self):
        assert lk.size_value(12, 7, 30, 18) == '12" × 7" (30×18cm)'


class TestPrice:
    def test_known_value(self):
        # L=150, S=50: G=250, area=7500, no thresholds. Hand-computed from the v6 formula.
        cost, lst, shown, profit, _ = lk.price(150, 50)
        assert cost == pytest.approx(134.71)
        assert lst == pytest.approx(561.32)
        assert shown == pytest.approx(280.66)
        assert profit == pytest.approx(round(280.66 * 0.8 - 134.71, 2))

    def test_thresholds_add_cost(self):
        assert lk.price(151, 50)[0] - lk.price(150, 50)[0] > 39.655 * 1.32 - 1

    def test_roundup_does_not_overshoot_exact_values(self):
        assert lk.roundup(2.30) == 2.30
        assert lk.roundup(2.301) == 2.31

    def test_notes(self):
        assert "model doğrulanmadı" in lk.price(90, 28)[4]
        assert "eşik" in lk.price(150, 70)[4]


class TestSku:
    def test_sequence_per_day_and_reset(self, tmp_path):
        ledger = tmp_path / "sku_ledger.csv"
        day = dt.date(2026, 10, 21)
        assert lk.next_sku(ledger, "a", day) == "2026102101"
        assert lk.next_sku(ledger, "b", day) == "2026102102"
        assert lk.next_sku(ledger, "c", dt.date(2026, 10, 22)) == "2026102201"
        assert lk.next_sku(ledger, "d", day) == "2026102103"


def test_slugify():
    assert lk.slugify("Ocean Wave Bundle!") == "ocean-wave-bundle"


def test_order_cut_puts_screw_holes_in_one_size(tmp_path):
    import cv2
    import numpy as np
    img = np.full((420, 1600), 255, np.uint8)                       # white margin around the metal
    cv2.rectangle(img, (100, 300), (1500, 360), 0, -1)
    for cx in range(220, 1450, 240):
        cv2.ellipse(img, (cx, 300), (110, 200), 0, 180, 360, 0, 8)
    src = tmp_path / "topper.png"
    cv2.imwrite(str(src), img)
    logged = []
    rep = lk.order_cut(src, tmp_path / "kits", "Victorian Door Topper", "2026101801", 150,
                       {"screw_holes": -1, "work_res": 2.0}, log=logged.append)
    assert rep["status"] != "failed", rep["errors"]
    assert rep["screw_holes"]["placed"] == 4
    names = [Path(o).name for o in rep["outputs"]]
    assert any(n.startswith("2026101801_150x") and n.endswith("cm_screw.dxf") for n in names)
    assert any(n.endswith("_preview.png") for n in names)                 # repairs marked for a look
    assert any("görünüm korundu" in l or "GÖRÜNÜM DEĞİŞTİ" in l for l in logged)
    assert any("DXF çizgisi" in l for l in logged)
    xs = np.nonzero(img < 128)[1]
    ink_w = rep["size_mm"][0] * (xs.max() - xs.min() + 1) / img.shape[1]
    assert abs(ink_w - 1500) < 15                                    # the metal, not the canvas, is 150 cm
