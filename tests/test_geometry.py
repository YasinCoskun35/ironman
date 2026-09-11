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
# auto_tune_smoothing -- the moon.png case study, pinned down as a test
# ---------------------------------------------------------------------------

class TestAutoTuneSmoothing:
    def test_low_res_source_gets_loosened(self):
        # moon.png: 550mm design, 2041px native width -> 0.2695mm/px, coarser
        # than the 0.125mm/px (8px/mm) working grid.
        cfg = m.Config(width_mm=550.0, work_res=8.0)
        info = m.auto_tune_smoothing(cfg, native_w_px=2041, w_mm=550.0)
        assert info["applied"] is True
        # smooth_mm is deliberately left untouched -- a global blur erases
        # thin intentional detail lines (see the Kadın.png neck-line case);
        # only simplify_mm (contour-local) is safe to boost automatically.
        assert cfg.smooth_mm == m.DEFAULT_SMOOTH_MM
        assert cfg.simplify_mm > m.DEFAULT_SIMPLIFY_MM
        assert cfg.simplify_mm == pytest.approx(0.8, abs=0.1)

    def test_high_res_source_untouched(self):
        # bearready.png: 550mm design, 6495px native width -> 0.0847mm/px,
        # already finer than the working grid -- nothing to fix.
        cfg = m.Config(width_mm=550.0, work_res=8.0)
        info = m.auto_tune_smoothing(cfg, native_w_px=6495, w_mm=550.0)
        assert info["applied"] is False
        assert cfg.smooth_mm == m.DEFAULT_SMOOTH_MM
        assert cfg.simplify_mm == m.DEFAULT_SIMPLIFY_MM

    def test_explicit_override_is_respected(self):
        cfg = m.Config(width_mm=550.0, work_res=8.0, simplify_mm=0.4)
        info = m.auto_tune_smoothing(cfg, native_w_px=2041, w_mm=550.0)
        assert info["applied"] is False
        assert info["skipped_explicit_override"] is True
        assert cfg.simplify_mm == 0.4  # untouched, exactly what the caller set

    def test_no_native_width_is_a_noop(self):
        cfg = m.Config(width_mm=550.0)
        info = m.auto_tune_smoothing(cfg, native_w_px=0, w_mm=550.0)
        assert info["applied"] is False


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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
