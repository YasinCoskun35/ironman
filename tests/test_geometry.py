"""
Unit tests for the pure geometry/math helpers in mycode.py.

These deliberately avoid loading real images or running the full pipeline --
that is already exercised by hand with --dry-run on real designs. What this
suite catches is a future edit silently breaking one of the small formulas
the pipeline's safety checks are built on (tip tolerance, unit conversion,
polygon cleanup), without needing a real cut file to notice.

Run:
    .venv/bin/pytest tests/ -v
"""

import math
import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mycode as m


# ---------------------------------------------------------------------------
# sharp_tip_tolerance -- the corner-vs-defect math from the docstring table
# ---------------------------------------------------------------------------

class TestSharpTipTolerance:
    def test_90_degrees(self):
        # docstring: 90 deg -> 0.41 r
        assert m.sharp_tip_tolerance(90.0) == pytest.approx(0.4142, abs=1e-3)

    def test_60_degrees(self):
        # docstring: 60 deg -> 1.00 r
        assert m.sharp_tip_tolerance(60.0) == pytest.approx(1.0, abs=1e-3)

    def test_45_degrees(self):
        # docstring: 45 deg -> 1.61 r
        assert m.sharp_tip_tolerance(45.0) == pytest.approx(1.6131, abs=1e-3)

    def test_sharper_angle_needs_more_tolerance(self):
        # A sharper (smaller) allowed tip angle must always demand a *larger*
        # tolerance -- otherwise enforce_min_thickness would round off more
        # corners as the user asks it to preserve sharper ones, which is
        # backwards.
        assert m.sharp_tip_tolerance(12.0) > m.sharp_tip_tolerance(60.0)

    def test_clamped_to_sane_range(self):
        # Values outside 1-179 degrees must not raise or return nonsense.
        assert m.sharp_tip_tolerance(0.0) > 0
        assert m.sharp_tip_tolerance(1000.0) >= 0.05


# ---------------------------------------------------------------------------
# unit conversion
# ---------------------------------------------------------------------------

class TestUnitConversion:
    def test_mm_per_inch_constant(self):
        assert m.MM_PER_INCH == 25.4

    def test_parse_svg_length_mm_millimetres(self):
        assert m.parse_svg_length_mm("120mm") == pytest.approx(120.0)

    def test_parse_svg_length_mm_centimetres(self):
        assert m.parse_svg_length_mm("12cm") == pytest.approx(120.0)

    def test_parse_svg_length_mm_inches(self):
        assert m.parse_svg_length_mm("1in") == pytest.approx(25.4)

    def test_parse_svg_length_mm_bare_px_at_96dpi(self):
        assert m.parse_svg_length_mm("96") == pytest.approx(25.4, abs=1e-6)

    def test_parse_svg_length_mm_percent_is_unusable(self):
        assert m.parse_svg_length_mm("50%") is None

    def test_parse_svg_length_mm_garbage(self):
        assert m.parse_svg_length_mm("not-a-length") is None
        assert m.parse_svg_length_mm(None) is None
        assert m.parse_svg_length_mm("") is None


class TestParseCanvasPx:
    def test_valid(self):
        assert m.parse_canvas_px("4500x5100") == (4500, 5100)

    def test_case_insensitive_and_spaces(self):
        assert m.parse_canvas_px(" 100 X 200 ") == (100, 200)

    def test_rejects_garbage(self):
        with pytest.raises(Exception):
            m.parse_canvas_px("not-a-size")

    def test_rejects_zero(self):
        with pytest.raises(Exception):
            m.parse_canvas_px("0x100")


# ---------------------------------------------------------------------------
# resolve_physical_size -- explicit width > explicit height > SVG intrinsic
# ---------------------------------------------------------------------------

class TestResolvePhysicalSize:
    def _cfg(self, width_mm=None, height_mm=None):
        return m.Config(width_mm=width_mm, height_mm=height_mm)

    def test_width_wins(self):
        cfg = self._cfg(width_mm=400.0)
        w, h = m.resolve_physical_size(cfg, aspect=0.5, intrinsic=(999.0, 999.0))
        assert w == 400.0
        assert h == pytest.approx(200.0)

    def test_height_used_when_no_width(self):
        cfg = self._cfg(height_mm=200.0)
        w, h = m.resolve_physical_size(cfg, aspect=0.5, intrinsic=None)
        assert h == 200.0
        assert w == pytest.approx(400.0)

    def test_intrinsic_used_when_native_size(self):
        cfg = self._cfg()  # both None, i.e. --native-size
        w, h = m.resolve_physical_size(cfg, aspect=1.0, intrinsic=(120.0, 80.0))
        assert (w, h) == (120.0, 80.0)

    def test_raises_when_nothing_available(self):
        cfg = self._cfg()
        with pytest.raises(ValueError):
            m.resolve_physical_size(cfg, aspect=1.0, intrinsic=None)


# ---------------------------------------------------------------------------
# polygon helpers used by the vectoriser
# ---------------------------------------------------------------------------

class TestDedupeClosed:
    def test_removes_consecutive_duplicates(self):
        pts = np.array([[0, 0], [0, 0], [1, 0], [1, 1], [1, 1], [0, 1]], dtype=np.float64)
        out = m.dedupe_closed(pts)
        assert len(out) == 4

    def test_removes_wraparound_duplicate(self):
        pts = np.array([[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]], dtype=np.float64)
        out = m.dedupe_closed(pts)
        assert len(out) == 4
        assert not np.allclose(out[0], out[-1])

    def test_leaves_clean_polygon_alone(self):
        pts = np.array([[0, 0], [10, 0], [10, 10], [0, 10]], dtype=np.float64)
        out = m.dedupe_closed(pts)
        assert len(out) == 4


class TestTurnAnglesDeg:
    def test_square_is_all_right_angles(self):
        square = np.array([[0, 0], [10, 0], [10, 10], [0, 10]], dtype=np.float64)
        angles = m.turn_angles_deg(square)
        assert angles == pytest.approx([90.0, 90.0, 90.0, 90.0], abs=1e-6)

    def test_straight_line_point_is_zero(self):
        # A vertex sitting exactly on the line between its neighbours turns 0deg.
        pts = np.array([[0, 0], [5, 0], [10, 0], [10, 10]], dtype=np.float64)
        angles = m.turn_angles_deg(pts)
        assert angles[1] == pytest.approx(0.0, abs=1e-6)


# ---------------------------------------------------------------------------
# mask helpers
# ---------------------------------------------------------------------------

class TestAsMask:
    def test_bool_array(self):
        a = np.array([True, False, True])
        out = m.as_mask(a)
        assert out.dtype == np.uint8
        assert list(out) == [255, 0, 255]

    def test_numeric_array_thresholds_at_zero(self):
        a = np.array([0, 1, 255, 127])
        out = m.as_mask(a)
        assert list(out) == [0, 255, 255, 255]


class TestDisk:
    def test_odd_square_shape(self):
        k = m.disk(3)
        assert k.shape == (7, 7)  # 2*3 + 1

    def test_minimum_radius_is_one(self):
        k = m.disk(0)
        assert k.shape == (3, 3)  # radius clamped to >= 1


class TestGrowPreservingTaper:
    """
    The repair that stamps a constant r_min disk along a thin region's medial
    axis beads every tapered spike, because the axis of a wedge runs into its
    point and a disk stamped at a point is a disk. These lock in the two
    properties that stop that happening.
    """

    @staticmethod
    def _wedge(length=80, half_height=9):
        """A solid triangle: thick at the left edge, tapering to a point."""
        mask = np.zeros((2 * half_height + 21, length + 20), np.uint8)
        for x in range(length):
            h = max(0, int(round(half_height * (1.0 - x / length))))
            cy = mask.shape[0] // 2
            mask[cy - h: cy + h + 1, 10 + x] = 255
        return mask

    def test_tip_stays_narrower_than_the_base(self):
        mask = self._wedge()
        thin, _ = m.analyze_thin_features(mask, r_min_px=6.0,
                                          tip_tol=m.sharp_tip_tolerance(60.0))
        grown = m.grow_preserving_taper(thin, mask, r_min_px=6.0)
        out = np.maximum(mask, grown)
        # Column heights must still increase from tip to base: a beaded repair
        # puts a constant-diameter blob on the tip, which inverts this.
        heights = (out > 0).sum(axis=0)
        near_tip = heights[10 + 70: 10 + 78].max()
        near_base = heights[10 + 2: 10 + 10].max()
        assert near_tip < near_base

    def test_never_grows_past_the_minimum(self):
        mask = self._wedge()
        r_min = 6.0
        thin, _ = m.analyze_thin_features(mask, r_min_px=r_min,
                                          tip_tol=m.sharp_tip_tolerance(60.0))
        grown = m.grow_preserving_taper(thin, mask, r_min_px=r_min)
        # The factor is picked from the region's widest point, so the repair
        # itself lands on r_min and cannot bulldoze past it into neighbouring
        # detail. (The wedge's own base is already thicker than r_min; that is
        # untouched material, so measure what the repair added, not the union.)
        if grown.any():
            assert cv2.distanceTransform(grown, cv2.DIST_L2, 5).max() <= r_min + 1.0
        out = np.maximum(mask, grown)
        before = cv2.distanceTransform(mask, cv2.DIST_L2, 5).max()
        assert cv2.distanceTransform(out, cv2.DIST_L2, 5).max() <= max(before, r_min) + 1.0

    def test_uniform_hairline_still_reaches_the_minimum(self):
        """The one thing the old repair did well has to survive the change."""
        mask = np.zeros((60, 120), np.uint8)
        mask[29:32, 10:110] = 255          # 3px wide, uniform
        r_min = 5.0
        thin, _ = m.analyze_thin_features(mask, r_min_px=r_min,
                                          tip_tol=m.sharp_tip_tolerance(60.0))
        grown = m.grow_preserving_taper(thin, mask, r_min_px=r_min)
        out = np.maximum(mask, grown)
        mid = (out[:, 60] > 0).sum()
        assert mid >= 2 * r_min - 2

    def test_empty_input_is_a_no_op(self):
        mask = np.zeros((40, 40), np.uint8)
        assert not m.grow_preserving_taper(mask, mask, r_min_px=4.0).any()


class TestJoinsTwoCores:
    """
    Tip tolerance answers 'is this sliver deep?', which is the wrong question
    for a neck: however short it is, it is where the part snaps. These check
    the two cases are told apart.
    """

    @staticmethod
    def _analyse(mask, r_min=6.0):
        core = cv2.morphologyEx(mask, cv2.MORPH_OPEN, m.disk(int(math.ceil(r_min))))
        residue = cv2.bitwise_and(mask, cv2.bitwise_not(core))
        n, labels, _, _ = cv2.connectedComponentsWithStats(residue, connectivity=8)
        return m.joins_two_cores(residue, labels, n, core), labels, n

    def test_neck_between_two_lumps_is_flagged(self):
        mask = np.zeros((80, 160), np.uint8)
        mask[20:60, 10:50] = 255           # lump
        mask[20:60, 110:150] = 255         # lump
        mask[38:42, 50:110] = 255          # 4px neck joining them
        flag, _, n = self._analyse(mask)
        assert n > 1 and flag.any()

    def test_free_spike_off_one_lump_is_not_flagged(self):
        mask = np.zeros((80, 160), np.uint8)
        mask[20:60, 10:50] = 255           # one lump only
        mask[38:42, 50:110] = 255          # spike hanging off it, dead end
        flag, _, _ = self._analyse(mask)
        assert not flag.any()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
