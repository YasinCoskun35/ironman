"""
Screw holes for wall mounting: balanced on the centre of mass, inside solid
metal, never cutting the part apart, and present in the exported geometry.
"""

import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import mycode as cnc


def _door_topper(path: Path):
    """A wide arch of thin scrolls standing on a solid bottom bar, like a door topper."""
    img = np.full((420, 1500), 255, np.uint8)
    cv2.rectangle(img, (40, 330), (1460, 380), 0, -1)                 # solid base bar
    for cx in range(160, 1400, 240):
        cv2.ellipse(img, (cx, 330), (110, 220), 0, 180, 360, 0, 6)     # thin scroll arches
    cv2.imwrite(str(path), img)


def _cfg(**kw):
    return cnc.Config(width_mm=1500.0, work_res=2.0, min_thickness_mm=1.8, kerf_mm=1.8,
                      floating="remove", write_png=False, **kw)


def test_holes_go_into_the_bar_and_keep_one_piece(tmp_path):
    src = tmp_path / "topper.png"
    _door_topper(src)
    rep = cnc.process_file(src, tmp_path / "out", _cfg(screw_holes=-1, screw_hole_mm=5.0))
    assert rep["status"] != "failed", rep["errors"]
    holes = rep["screw_holes"]["holes"]
    assert rep["screw_holes"]["placed"] == 4 == len(holes)              # 150 cm wide: four
    xs = sorted(h["x_mm"] for h in holes)
    assert xs[0] < 300 and xs[-1] > 1200                                  # spread to both ends
    assert all(b - a > 300 for a, b in zip(xs, xs[1:]))                   # evenly, not bunched at one end
    com_x = rep["screw_holes"]["center_of_mass_mm"][0]
    assert abs((xs[0] + xs[-1]) / 2 - com_x) < 5 and abs((xs[1] + xs[2]) / 2 - com_x) < 5   # mirrored on it
    assert len({h["y_mm"] for h in holes}) == 1                           # level
    assert any("below the centre of mass" in w for w in rep["warnings"])   # bar is low: said so
    bar_top, bar_bottom = 330 * 1500 / 1500, 380 * 1500 / 1500            # the bar, in mm
    assert all(bar_top < h["y_mm"] < bar_bottom for h in holes)           # only the bar has room
    topo = rep["vector"]["topology"]                                       # (IoU is coarse at this test resolution)
    assert topo["components_exported"] == 1 and not topo["components_split"]
    plain = cnc.process_file(src, tmp_path / "plain", _cfg())
    assert rep["vector"]["holes"] == plain["vector"]["holes"] + 4          # four extra cut-outs in the file


def test_no_holes_by_default_and_warning_when_nothing_fits(tmp_path):
    src = tmp_path / "topper.png"
    _door_topper(src)
    plain = cnc.process_file(src, tmp_path / "a", _cfg())
    assert plain["screw_holes"]["holes"] == []
    thin = np.full((400, 1000), 255, np.uint8)
    cv2.ellipse(thin, (500, 380), (400, 300), 0, 180, 360, 0, 6)       # no solid spot anywhere
    cv2.imwrite(str(tmp_path / "thin.png"), thin)
    rep = cnc.process_file(tmp_path / "thin.png", tmp_path / "b", _cfg(screw_holes=2, screw_hole_mm=8.0))
    assert rep["screw_holes"]["placed"] < 2
    assert any("screw holes fit" in w for w in rep["warnings"])


def test_holes_go_above_the_centre_of_mass_when_there_is_metal(tmp_path):
    img = np.full((800, 800), 255, np.uint8)
    cv2.circle(img, (400, 400), 360, 0, 40)                              # solid ring frame
    cv2.rectangle(img, (390, 60), (410, 740), 0, -1)                    # a spine joining it
    cv2.circle(img, (520, 560), 90, 0, -1)                               # heavy on the lower right
    cv2.imwrite(str(tmp_path / "ring.png"), img)
    cfg = cnc.Config(width_mm=500.0, work_res=2.0, min_thickness_mm=1.8, kerf_mm=1.8,
                     floating="remove", write_png=False, screw_holes=2, screw_hole_mm=5.0)
    rep = cnc.process_file(tmp_path / "ring.png", tmp_path / "out", cfg)
    holes, com = rep["screw_holes"]["holes"], rep["screw_holes"]["center_of_mass_mm"]
    assert len(holes) == 2 and all(h["above_center_of_mass"] for h in holes)
    assert abs((holes[0]["x_mm"] + holes[1]["x_mm"]) / 2 - com[0]) < 3   # centred on the mass, not the frame
    assert holes[0]["y_mm"] == holes[1]["y_mm"] < com[1]
    assert not any("below the centre of mass" in w for w in rep["warnings"])
