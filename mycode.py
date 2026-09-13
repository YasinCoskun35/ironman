#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cnc_prep.py — Vector optimisation pipeline for CNC plasma / laser cut metal wall art.

Takes raw black-on-white artwork (PNG or SVG), and produces cut-ready SVG + DXF
with the geometry hardened against the physical realities of thermal cutting.

PIPELINE
--------
  1.  LOAD          PNG (any mode, alpha flattened onto white) or SVG (rasterised).
  2.  BINARISE      Otsu threshold, auto-detect inverted artwork, force pure 0/255.
  3.  DENOISE       Drop speckle islands and pinhole holes below a physical area
                    threshold (mm^2). Morphological open/close to kill single-pixel
                    fuzz from AI upscalers / JPEG ringing.
  4.  RESAMPLE      Normalise to a known working resolution (px/mm) so every
                    downstream threshold is expressed in millimetres, not pixels.
  5.  DEJAG         Gaussian + re-threshold: removes stair-stepping on diagonals
                    without eating real detail.
  6.  FLOATERS      Connected-component analysis. Anything not attached to the main
                    body will physically fall out of the sheet. Warn / remove /
                    auto-bridge with a tab of configurable width.
  7.  THICKEN       Medial-axis (skeleton) + distance transform locate every place
                    where the local stroke width drops below --min-thickness-mm
                    (hairlines, narrow bridges, needle tips). Those axis points are
                    dilated back up to the minimum radius. Thick areas are untouched,
                    so the artistic silhouette survives.
  8.  GAPS          Narrow negative space (slots thinner than the kerf will simply
                    burn away and merge). Detected by morphological closing;
                    warned about, or filled on request.
  9.  KERF          Optional global offset by kerf/2 (most CAM does this itself —
                    default is 'none').
 10.  VECTORISE     Contour trace -> Douglas-Peucker node reduction -> corner-
                    preserving cubic Bezier fit. Sharp artistic corners are detected
                    by turn angle and kept as hard nodes; everything else is smoothed
                    so the machine head does not stutter.
 11.  EXPORT        SVG (mm units, even-odd fill), DXF R12 (closed POLYLINEs, mm),
                    QC preview PNG, and a JSON report.

USAGE
-----
    python cnc_prep.py -i ./raw_designs -o ./cnc_ready --width-mm 600
    python cnc_prep.py -i ./raw -o ./out --min-thickness-mm 2.5 --floating bridge
    python cnc_prep.py -i ./raw -o ./out --dry-run --preview     # QC audit only

DEPENDENCIES
------------
    pip install "opencv-contrib-python>=4.8" numpy Pillow
    pip install cairosvg          # only needed for SVG *input*
    # cairosvg additionally needs the native Cairo lib:
    #   Debian/Ubuntu : sudo apt-get install libcairo2 libpango-1.0-0 libgdk-pixbuf-2.0-0
    #   macOS         : brew install cairo pango gdk-pixbuf libffi
    #   Windows       : easiest route is `conda install -c conda-forge cairosvg`
    #
    # opencv-contrib-python is preferred over plain opencv-python: it ships
    # cv2.ximgproc.thinning, which is ~50x faster than the pure-python fallback.
    # Optional accelerator, used automatically if present: scikit-image.

Author: written for a production metal-art cutting workflow. No placeholders.
Licence: MIT.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import xml.etree.ElementTree as ET
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict, replace as dataclass_replace
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

try:
    import cv2
except ImportError:  # pragma: no cover
    sys.stderr.write(
        "FATAL: OpenCV is required.\n"
        "       pip install opencv-contrib-python\n"
    )
    raise

try:
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = None  # these are big art files, not decompression bombs
except ImportError:  # pragma: no cover
    sys.stderr.write("FATAL: Pillow is required.  pip install Pillow\n")
    raise


# ---------------------------------------------------------------------------
# Optional dependency probing (done once, lazily reported)
# ---------------------------------------------------------------------------

def _probe_cairosvg():
    try:
        import cairosvg  # noqa: F401
        return True
    except Exception:
        return False


def _probe_ximgproc() -> bool:
    return hasattr(cv2, "ximgproc") and hasattr(cv2.ximgproc, "thinning")


def _probe_skimage() -> bool:
    try:
        from skimage.morphology import skeletonize  # noqa: F401
        return True
    except Exception:
        return False


HAS_CAIROSVG = _probe_cairosvg()
HAS_XIMGPROC = _probe_ximgproc()
HAS_SKIMAGE = _probe_skimage()

RASTER_EXT = {".png", ".bmp", ".tif", ".tiff", ".jpg", ".jpeg", ".webp"}
VECTOR_EXT = {".svg"}
SUPPORTED_EXT = RASTER_EXT | VECTOR_EXT

FG = 255   # foreground == metal that stays
BG = 0     # background == material burned away


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class Config:
    """Every geometric threshold is in millimetres of *finished part*."""

    # --- physical scale -----------------------------------------------------
    width_mm: Optional[float] = 600.0     # target finished width
    height_mm: Optional[float] = None     # alternative to width_mm
    work_res: float = 8.0                 # working resolution, pixels per mm
    max_dim_px: int = 12000               # hard guard against absurd rasters
    svg_dpi_cap: float = 1200.0           # cap when rasterising SVG input

    # --- binarisation / denoise --------------------------------------------
    invert: str = "auto"                  # auto | yes | no
    threshold: Optional[int] = None       # None => Otsu
    min_feature_area_mm2: float = 4.0     # kill islands smaller than this
    min_hole_area_mm2: float = 2.0        # fill holes smaller than this
    despeckle_mm: float = 0.35            # median filter radius (auto-capped)

    # --- edge treatment -----------------------------------------------------
    smooth_mm: float = 0.25               # raster de-jag sigma

    # --- structural rules ---------------------------------------------------
    min_thickness_mm: float = 2.0         # thinnest surviving metal
    sharp_tip_deg: float = 60.0           # sharpest tip angle the torch may attempt
    thicken_passes: int = 3               # convergence iterations
    kerf_mm: float = 1.2                  # torch/beam kerf (plasma ~1.2, laser ~0.15)
    gap_factor: float = 1.5               # slots below kerf*factor are flagged
    fill_narrow_gaps: bool = False        # auto-close doomed slots
    kerf_compensate: str = "none"         # none | outward | inward

    # --- floating elements --------------------------------------------------
    floating: str = "warn"                # warn | bridge | remove | keep
    bridge_width_mm: Optional[float] = None   # defaults to min_thickness_mm
    max_bridges: int = 24                 # sanity cap per design

    # --- vectorisation ------------------------------------------------------
    simplify_mm: float = 0.15             # Douglas-Peucker epsilon
    corner_deg: float = 48.0              # turn angle kept as a hard corner
    bezier_tension: float = 0.85          # 0 = polyline, 1 = full Catmull-Rom
    bezier_flatten_mm: float = 0.05       # DXF curve flattening tolerance

    # --- output -------------------------------------------------------------
    write_svg: bool = True
    write_dxf: bool = True
    write_png: bool = True
    png_dpi: float = 300.0                # raster DPI for the exported PNG
    canvas_px: Optional[Tuple[int, int]] = None   # force an exact (w, h) PNG canvas
    preview: bool = False
    dry_run: bool = False
    strict: bool = False
    suggest_size: bool = False    # search for & report the minimum safe finished size
    suggest_jobs: int = 4          # parallel workers for the --suggest-size search

    # --- derived (filled at runtime) ---------------------------------------
    mm_per_px: float = field(default=0.0, init=False)

    # ---- helpers -----------------------------------------------------------
    def px(self, mm: float) -> float:
        """Millimetres -> working pixels."""
        return mm / self.mm_per_px if self.mm_per_px else 0.0

    def px_int(self, mm: float, minimum: int = 1) -> int:
        return max(minimum, int(round(self.px(mm))))

    def mm2_to_px2(self, mm2: float) -> float:
        return mm2 / (self.mm_per_px ** 2) if self.mm_per_px else 0.0


# ---------------------------------------------------------------------------
# Small morphology helpers
# ---------------------------------------------------------------------------

def disk(radius_px: int) -> np.ndarray:
    """Elliptical structuring element approximating a disk of the given radius."""
    r = max(1, int(round(radius_px)))
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))


def as_mask(a: np.ndarray) -> np.ndarray:
    """Coerce anything boolean-ish into a clean uint8 0/255 mask."""
    if a.dtype == bool:
        return (a.astype(np.uint8)) * 255
    m = a.astype(np.uint8)
    return np.where(m > 0, np.uint8(255), np.uint8(0))


def fill_all_holes(mask: np.ndarray) -> np.ndarray:
    """Return the solid silhouette (every interior hole filled)."""
    cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = np.zeros_like(mask)
    if cnts:
        cv2.drawContours(out, cnts, -1, FG, thickness=cv2.FILLED)
    return out


def skeletonize(mask: np.ndarray) -> np.ndarray:
    """
    Single-pixel-wide medial axis of a binary mask.

    Tries, in order of speed: cv2.ximgproc.thinning (contrib build),
    skimage.morphology.skeletonize, then a vectorised Zhang-Suen fallback.

    NOTE: every thinning algorithm is iterative and peels one pixel per pass,
    so its cost scales with the *thickest* feature in the image, not the
    thinnest. Never call this on a full-resolution artwork — use
    skeletonize_regions(), which crops to the thin parts first.
    """
    if not mask.any():
        return np.zeros_like(mask)

    if HAS_XIMGPROC:
        try:
            return as_mask(cv2.ximgproc.thinning(mask, thinningType=cv2.ximgproc.THINNING_ZHANGSUEN))
        except cv2.error:
            pass

    if HAS_SKIMAGE:
        try:
            from skimage.morphology import skeletonize as _sk
            return as_mask(_sk(mask > 0))
        except Exception:
            pass

    return _zhang_suen(mask)


def _zhang_suen(mask: np.ndarray, max_iter: int = 400) -> np.ndarray:
    """
    Dependency-free Zhang-Suen thinning, fully vectorised over the image.

    Slower than the native implementations but correct, and it only ever runs
    when neither opencv-contrib nor scikit-image is installed.
    """
    img = (mask > 0).astype(np.uint8)
    for _ in range(max_iter):
        changed = False
        for step in (0, 1):
            P = np.pad(img, 1, mode="constant")
            p2 = P[:-2, 1:-1]; p3 = P[:-2, 2:];   p4 = P[1:-1, 2:]
            p5 = P[2:, 2:];    p6 = P[2:, 1:-1];  p7 = P[2:, :-2]
            p8 = P[1:-1, :-2]; p9 = P[:-2, :-2]

            neighbours = p2 + p3 + p4 + p5 + p6 + p7 + p8 + p9
            ring = [p2, p3, p4, p5, p6, p7, p8, p9, p2]
            transitions = np.zeros_like(neighbours)
            for i in range(8):
                transitions += ((ring[i] == 0) & (ring[i + 1] == 1)).astype(np.uint8)

            if step == 0:
                cond = ((p2 * p4 * p6) == 0) & ((p4 * p6 * p8) == 0)
            else:
                cond = ((p2 * p4 * p8) == 0) & ((p2 * p6 * p8) == 0)

            kill = (img == 1) & (neighbours >= 2) & (neighbours <= 6) & (transitions == 1) & cond
            if kill.any():
                img[kill] = 0
                changed = True
        if not changed:
            break
    return as_mask(img)


def skeletonize_regions(mask: np.ndarray, pad: int = 2) -> np.ndarray:
    """
    Medial axis of a mask, computed component-by-component on cropped tiles.

    Thinning cost is proportional to the local half-thickness times the tile
    area. By only ever running it on already-thin components inside their own
    bounding boxes, the whole operation stays in the millisecond range even on
    a 5000 x 5000 px sheet, where a full-image thinning takes over a minute.
    """
    out = np.zeros_like(mask)
    if not mask.any():
        return out
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    h, w = mask.shape[:2]
    for i in range(1, n):
        x = max(0, stats[i, cv2.CC_STAT_LEFT] - pad)
        y = max(0, stats[i, cv2.CC_STAT_TOP] - pad)
        x2 = min(w, x + stats[i, cv2.CC_STAT_WIDTH] + 2 * pad)
        y2 = min(h, y + stats[i, cv2.CC_STAT_HEIGHT] + 2 * pad)
        tile = as_mask(labels[y:y2, x:x2] == i)
        out[y:y2, x:x2] = cv2.bitwise_or(out[y:y2, x:x2], skeletonize(tile))
    return out


def boundary_points(mask: np.ndarray, max_points: int = 4000) -> np.ndarray:
    """Uniformly subsampled boundary pixel coordinates as an (N, 2) int array (x, y)."""
    edge = cv2.morphologyEx(mask, cv2.MORPH_GRADIENT, np.ones((3, 3), np.uint8))
    pts = np.argwhere(edge > 0)          # (row, col)
    if pts.size == 0:
        pts = np.argwhere(mask > 0)
    if pts.size == 0:
        return np.empty((0, 2), dtype=np.int32)
    if len(pts) > max_points:
        idx = np.linspace(0, len(pts) - 1, max_points).astype(np.int64)
        pts = pts[idx]
    return pts[:, ::-1].astype(np.int32)  # -> (x, y)


def nearest_pair(a: np.ndarray, b: np.ndarray,
                 chunk: int = 512) -> Tuple[Optional[Tuple[int, int]], Optional[Tuple[int, int]], float]:
    """
    Brute-force closest point pair between two coordinate sets, chunked so memory
    stays bounded. Returns ((ax, ay), (bx, by), distance_px).
    """
    if len(a) == 0 or len(b) == 0:
        return None, None, float("inf")

    af = a.astype(np.float32)
    bf = b.astype(np.float32)
    best = (None, None, float("inf"))
    for start in range(0, len(af), chunk):
        block = af[start:start + chunk]
        d2 = ((block[:, None, :] - bf[None, :, :]) ** 2).sum(axis=2)
        i, j = np.unravel_index(int(np.argmin(d2)), d2.shape)
        dist = float(math.sqrt(float(d2[i, j])))
        if dist < best[2]:
            best = (tuple(int(v) for v in block[i]), tuple(int(v) for v in bf[j]), dist)
    return best


# ---------------------------------------------------------------------------
# Input loading
# ---------------------------------------------------------------------------

_UNIT_TO_MM = {
    "mm": 1.0, "cm": 10.0, "m": 1000.0,
    "in": 25.4, "pt": 25.4 / 72.0, "pc": 25.4 / 6.0,
    "px": 25.4 / 96.0, "": 25.4 / 96.0,
}
_LEN_RE = re.compile(r"^\s*([-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)\s*([a-zA-Z%]*)\s*$")


def parse_svg_length_mm(value: Optional[str]) -> Optional[float]:
    """Parse an SVG length attribute into millimetres. Returns None if unusable."""
    if not value:
        return None
    m = _LEN_RE.match(value)
    if not m:
        return None
    number, unit = float(m.group(1)), m.group(2).lower()
    if unit == "%":
        return None
    factor = _UNIT_TO_MM.get(unit)
    return number * factor if factor else None


def read_svg_intrinsic_size(path: Path) -> Optional[Tuple[float, float]]:
    """Physical (width_mm, height_mm) declared by the SVG, if it declares real units."""
    try:
        root = ET.parse(str(path)).getroot()
    except Exception:
        return None
    w = parse_svg_length_mm(root.get("width"))
    h = parse_svg_length_mm(root.get("height"))
    if w and h:
        return w, h
    vb = root.get("viewBox")
    if vb:
        try:
            parts = [float(p) for p in re.split(r"[ ,]+", vb.strip()) if p]
            if len(parts) == 4 and parts[2] > 0 and parts[3] > 0:
                # viewBox units are user units -> assume 96 dpi CSS pixels
                return parts[2] * _UNIT_TO_MM["px"], parts[3] * _UNIT_TO_MM["px"]
        except ValueError:
            pass
    return None


def find_svg_tool(name: str) -> Optional[str]:
    """
    Locate an SVG rasteriser executable.

    PATH first, then the places each platform's installer actually puts it.
    Inkscape's Windows installer does not add itself to PATH, so shutil.which()
    alone reports "not installed" on a machine where it plainly is -- which is
    the single most common reason this tool refuses SVG input on Windows.
    """
    found = shutil.which(name)
    if found:
        return found

    candidates: List[Path] = []
    if name == "inkscape":
        if os.name == "nt":
            roots = [os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                     os.environ.get("LOCALAPPDATA", "")]
            for root in roots:
                if not root:
                    continue
                candidates += [Path(root) / "Inkscape" / "bin" / "inkscape.exe",
                               Path(root) / "Inkscape" / "inkscape.exe",
                               Path(root) / "Programs" / "Inkscape" / "bin" / "inkscape.exe"]
        elif sys.platform == "darwin":
            candidates.append(Path("/Applications/Inkscape.app/Contents/MacOS/inkscape"))

    for c in candidates:
        if c.exists():
            return str(c)
    return None


def _svg_tool_help() -> str:
    """Installation advice for the platform actually running, not for Debian."""
    if os.name == "nt":
        return (
            "No SVG rasteriser available. On Windows the easiest fix is Inkscape:\n"
            "  1. Install it from https://inkscape.org/release/\n"
            "  2. Re-run -- this tool now finds it in Program Files even if it is\n"
            "     not on PATH, so no PATH editing is needed.\n"
            "Alternatively: pip install cairosvg (needs the GTK runtime, more work),\n"
            "or export your design as PNG instead of SVG."
        )
    if sys.platform == "darwin":
        return (
            "No SVG rasteriser available. Install one of:\n"
            "  pip install cairosvg   (plus: brew install cairo pango gdk-pixbuf libffi)\n"
            "  brew install inkscape"
        )
    return (
        "No SVG rasteriser available. Install one of:\n"
        "  pip install cairosvg   (plus native cairo: apt-get install libcairo2)\n"
        "  apt-get install librsvg2-bin      # provides rsvg-convert\n"
        "  apt-get install inkscape"
    )


def rasterize_svg(path: Path, out_w_px: int, out_h_px: int) -> np.ndarray:
    """
    Render an SVG to a grayscale numpy array at the requested pixel size.

    Order of attempts: cairosvg (library) -> rsvg-convert -> inkscape.
    """
    if HAS_CAIROSVG:
        import cairosvg
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            cairosvg.svg2png(
                url=str(path), write_to=tmp_path,
                output_width=out_w_px, output_height=out_h_px,
                background_color="white",
            )
            return load_raster(Path(tmp_path))
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    for name, argv in (
        ("rsvg-convert", ["{exe}", "-w", str(out_w_px), "-h", str(out_h_px),
                          "-b", "white", "-o", "{out}", str(path)]),
        ("inkscape", ["{exe}", str(path), "--export-type=png",
                      f"--export-width={out_w_px}", f"--export-height={out_h_px}",
                      "--export-background=white", "--export-filename={out}"]),
    ):
        exe = find_svg_tool(name)
        if exe:
            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
                tmp_path = tmp.name
            try:
                cmd = [a.replace("{exe}", exe).replace("{out}", tmp_path) for a in argv]
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=300)
                return load_raster(Path(tmp_path))
            except (subprocess.SubprocessError, OSError):
                continue
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    raise RuntimeError(_svg_tool_help())


def load_raster(path: Path) -> np.ndarray:
    """Load any raster format as an 8-bit grayscale array, alpha flattened onto white."""
    with Image.open(path) as im:
        im.load()
        if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
            rgba = im.convert("RGBA")
            canvas = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
            canvas.alpha_composite(rgba)
            im = canvas.convert("L")
        else:
            im = im.convert("L")
        return np.asarray(im, dtype=np.uint8).copy()


def load_source(path: Path, cfg: Config) -> Tuple[np.ndarray, float, float]:
    """
    Load a design and resolve its physical size.

    Returns (grayscale_image, width_mm, height_mm).
    """
    ext = path.suffix.lower()

    if ext in VECTOR_EXT:
        intrinsic = read_svg_intrinsic_size(path)
        aspect = (intrinsic[1] / intrinsic[0]) if intrinsic and intrinsic[0] else 1.0
        w_mm, h_mm = resolve_physical_size(cfg, aspect, intrinsic)

        # Rasterise directly at the working resolution (no resample needed later).
        w_px = int(round(w_mm * cfg.work_res))
        h_px = int(round(h_mm * cfg.work_res))
        scale = min(1.0, cfg.max_dim_px / max(w_px, h_px, 1))
        w_px, h_px = max(1, int(w_px * scale)), max(1, int(h_px * scale))

        dpi = (w_px / (w_mm / 25.4)) if w_mm else 300.0
        if dpi > cfg.svg_dpi_cap:
            shrink = cfg.svg_dpi_cap / dpi
            w_px, h_px = max(1, int(w_px * shrink)), max(1, int(h_px * shrink))

        gray = rasterize_svg(path, w_px, h_px)
        return gray, w_mm, h_mm

    if ext in RASTER_EXT:
        gray = load_raster(path)
        h_px, w_px = gray.shape[:2]
        aspect = h_px / w_px
        w_mm, h_mm = resolve_physical_size(cfg, aspect, None)
        return gray, w_mm, h_mm

    raise ValueError(f"Unsupported input extension: {path.suffix}")


def resolve_physical_size(cfg: Config, aspect: float,
                          intrinsic: Optional[Tuple[float, float]]) -> Tuple[float, float]:
    """
    Decide the finished part size in mm.

    Priority: explicit --width-mm > explicit --height-mm > SVG intrinsic size.
    """
    if cfg.width_mm:
        return cfg.width_mm, cfg.width_mm * aspect
    if cfg.height_mm:
        return cfg.height_mm / aspect if aspect else cfg.height_mm, cfg.height_mm
    if intrinsic:
        return intrinsic
    raise ValueError(
        "Cannot determine physical size: pass --width-mm or --height-mm "
        "(--native-size only works for SVG inputs that declare their own width/height "
        "or viewBox).")


# ---------------------------------------------------------------------------
# Stage 1 — binarisation & denoise
# ---------------------------------------------------------------------------

def binarize(gray: np.ndarray, cfg: Config) -> np.ndarray:
    """
    Produce a strict 0/255 mask where 255 == metal that remains.

    Handles both conventions (black art on white, or white art on black) by
    inspecting the border ring, which is virtually always background.
    """
    inverted = False
    if cfg.invert == "yes":
        inverted = True
    elif cfg.invert == "auto":
        h, w = gray.shape[:2]
        ring = np.concatenate([
            gray[0, :].ravel(), gray[h - 1, :].ravel(),
            gray[:, 0].ravel(), gray[:, w - 1].ravel(),
        ])
        # Dark border -> the art is the bright thing.
        inverted = float(np.median(ring)) < 128.0

    if cfg.threshold is not None:
        mode = cv2.THRESH_BINARY if inverted else cv2.THRESH_BINARY_INV
        _, bw = cv2.threshold(gray, int(cfg.threshold), FG, mode)
    else:
        mode = (cv2.THRESH_BINARY if inverted else cv2.THRESH_BINARY_INV) | cv2.THRESH_OTSU
        _, bw = cv2.threshold(gray, 0, FG, mode)

    return as_mask(bw)


def denoise(mask: np.ndarray, cfg: Config) -> Tuple[np.ndarray, Dict]:
    """Remove speckle islands, pinhole holes, and single-pixel morphological fuzz."""
    stats: Dict[str, float] = {}

    # A morphological opening is the obvious way to despeckle and the wrong one
    # here: opening with radius r deletes everything thinner than 2r, so a
    # 0.35 mm clean-up pass silently erases 0.4 mm line art -- precisely the
    # input the thickening stage exists to rescue. A median filter removes
    # salt-and-pepper noise while leaving any run two pixels wide or more
    # intact, and the real speckle rejection is done on component area, which
    # cannot destroy a thin-but-legitimate stroke at all.
    if cfg.despeckle_mm > 0:
        k = cfg.px_int(cfg.despeckle_mm, minimum=1) * 2 + 1
        # Never let the filter reach the minimum feature size.
        k = min(k, max(3, int(cfg.px(cfg.min_thickness_mm) / 2) | 1))
        if k >= 3:
            mask = as_mask(cv2.medianBlur(mask, min(k, 15)) > 127)
            stats["median_kernel_px"] = int(min(k, 15))

    min_area = cfg.mm2_to_px2(cfg.min_feature_area_mm2)
    mask, removed = drop_small_components(mask, min_area)
    stats["speckles_removed"] = removed

    min_hole = cfg.mm2_to_px2(cfg.min_hole_area_mm2)
    mask, filled = fill_small_holes(mask, min_hole)
    stats["pinholes_filled"] = filled

    return mask, stats


def apply_label_lut(labels: np.ndarray, keep: np.ndarray) -> np.ndarray:
    """
    Rebuild a mask from a per-label boolean decision in a single pass.

    Filtering by looping `labels == i` costs one full-image comparison per
    component, which on a 5000 x 5000 sheet carrying a few thousand AI speckles
    is minutes of pure overhead. A lookup table indexed by label does it once.
    """
    lut = np.where(keep, np.uint8(FG), np.uint8(BG))
    lut[0] = BG  # label 0 is always background
    return lut[labels]


def group_max(labels: np.ndarray, values: np.ndarray, selection: np.ndarray,
              n_labels: int) -> np.ndarray:
    """Per-label maximum of `values`, evaluated only over the selected pixels."""
    out = np.zeros(n_labels, dtype=np.float32)
    idx = np.flatnonzero(selection.ravel())
    if idx.size == 0:
        return out
    lab = labels.ravel()[idx]
    val = values.ravel()[idx].astype(np.float32)
    order = np.argsort(lab, kind="stable")
    lab, val = lab[order], val[order]
    starts = np.flatnonzero(np.r_[True, lab[1:] != lab[:-1]])
    out[lab[starts]] = np.maximum.reduceat(val, starts)
    return out


def drop_small_components(mask: np.ndarray, min_area_px: float) -> Tuple[np.ndarray, int]:
    """Zero out foreground components below min_area_px. Returns (mask, count_removed)."""
    if min_area_px <= 0 or not mask.any():
        return mask, 0
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    keep = stats[:, cv2.CC_STAT_AREA] >= min_area_px
    removed = int((~keep[1:]).sum())
    if removed == 0:
        return mask, 0
    return apply_label_lut(labels, keep), removed


def fill_small_holes(mask: np.ndarray, min_area_px: float) -> Tuple[np.ndarray, int]:
    """Fill interior holes below min_area_px. Returns (mask, count_filled)."""
    if min_area_px <= 0 or not mask.any():
        return mask, 0
    solid = fill_all_holes(mask)
    holes = cv2.bitwise_and(solid, cv2.bitwise_not(mask))
    if not holes.any():
        return mask, 0
    n, labels, stats, _ = cv2.connectedComponentsWithStats(holes, connectivity=8)
    small = stats[:, cv2.CC_STAT_AREA] < min_area_px
    small[0] = False
    filled = int(small.sum())
    if filled == 0:
        return mask, 0
    return cv2.bitwise_or(mask, apply_label_lut(labels, small)), filled


def resample_to_work_res(gray: np.ndarray, cfg: Config, w_mm: float) -> np.ndarray:
    """
    Rescale the *grayscale* source so that exactly cfg.work_res pixels map to
    one millimetre. Working at a fixed px/mm is what lets every later threshold
    be a physical number.

    This must run BEFORE binarize(), never after. A binary mask carries no
    sub-pixel edge information, so upsampling one can only ever produce a
    staircase: every diagonal becomes a flight of steps one source-pixel
    tall, and those steps survive the whole rest of the pipeline as burrs on
    the cut edge. The grayscale source still has its anti-aliased edge ramps,
    and interpolating *those* before thresholding reconstructs a smooth
    boundary at the working resolution -- the difference is stark on the AI
    exports this tool is fed, which are routinely 3x coarser than the raster
    a 550 mm part needs.
    """
    h_px, w_px = gray.shape[:2]
    target_w = int(round(w_mm * cfg.work_res))
    target_w = max(32, min(target_w, cfg.max_dim_px))
    if target_w == w_px:
        return gray
    target_h = max(32, int(round(h_px * target_w / w_px)))
    interp = cv2.INTER_AREA if target_w < w_px else cv2.INTER_CUBIC
    return cv2.resize(gray, (target_w, target_h), interpolation=interp)


def dejag(mask: np.ndarray, cfg: Config) -> np.ndarray:
    """
    Blur-and-re-threshold. Curvature below the blur radius is averaged away,
    which removes stair-stepping on diagonals without displacing real edges
    (a symmetric blur followed by a 50% threshold is area-preserving).
    """
    sigma_px = cfg.px(cfg.smooth_mm)
    if sigma_px < 0.4:
        return mask
    k = int(sigma_px * 3) | 1
    k = max(3, min(k, 199))
    blurred = cv2.GaussianBlur(mask, (k, k), sigma_px)
    return as_mask(blurred > 127)


# ---------------------------------------------------------------------------
# Stage 2 — floating element detection & bridging
# ---------------------------------------------------------------------------

@dataclass
class Bridge:
    """A tab added to anchor an otherwise-floating island."""
    from_xy: Tuple[int, int]
    to_xy: Tuple[int, int]
    length_mm: float
    width_mm: float
    island_area_mm2: float


def _extend_segment(p0: Tuple[int, int], p1: Tuple[int, int], overlap_px: int,
                    shape: Tuple[int, int]) -> Tuple[Tuple[int, int], Tuple[int, int]]:
    """Push both ends of a segment `overlap_px` further along its own direction."""
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return p0, p1
    ux, uy = dx / length, dy / length
    ext = max(2, int(overlap_px))
    h, w = shape[:2]
    clamp = lambda x, y: (int(min(max(x, 0), w - 1)), int(min(max(y, 0), h - 1)))
    return (clamp(p0[0] - ux * ext, p0[1] - uy * ext),
            clamp(p1[0] + ux * ext, p1[1] + uy * ext))


def extract_component(labels: np.ndarray, stats: np.ndarray, label: int,
                      shape: Tuple[int, int]) -> np.ndarray:
    """Full-size mask of one labelled component, compared only inside its bbox."""
    out = np.zeros(shape, np.uint8)
    x, y = stats[label, cv2.CC_STAT_LEFT], stats[label, cv2.CC_STAT_TOP]
    w, h = stats[label, cv2.CC_STAT_WIDTH], stats[label, cv2.CC_STAT_HEIGHT]
    out[y:y + h, x:x + w] = as_mask(labels[y:y + h, x:x + w] == label)
    return out


def handle_floating(mask: np.ndarray, cfg: Config) -> Tuple[np.ndarray, Dict, List[Bridge], np.ndarray]:
    """
    Find every foreground component that is not the main body.

    In a single-sheet cut, anything disconnected falls through the slats.
    Policies:
        warn   - leave geometry alone, record it in the report (default)
        remove - delete the floaters
        bridge - weld each floater to the nearest anchored metal with a tab
        keep   - silently accept (multi-piece designs, magnets, standoffs)
    """
    info: Dict = {"islands": [], "island_count": 0, "policy": cfg.floating}
    bridges: List[Bridge] = []
    bridge_layer = np.zeros_like(mask)

    if not mask.any():
        return mask, info, bridges, bridge_layer

    n, labels, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if n <= 2:  # background + a single body
        return mask, info, bridges, bridge_layer

    areas = [(stats[i, cv2.CC_STAT_AREA], i) for i in range(1, n)]
    areas.sort(reverse=True)
    main_label = areas[0][1]
    px2_to_mm2 = cfg.mm_per_px ** 2

    island_labels = [lbl for _, lbl in areas[1:]]
    info["island_count"] = len(island_labels)
    for lbl in island_labels:
        info["islands"].append({
            "area_mm2": round(float(stats[lbl, cv2.CC_STAT_AREA]) * px2_to_mm2, 3),
            "centroid_mm": [round(float(cents[lbl][0]) * cfg.mm_per_px, 2),
                            round(float(cents[lbl][1]) * cfg.mm_per_px, 2)],
        })

    if cfg.floating in ("warn", "keep"):
        return mask, info, bridges, bridge_layer

    out = mask.copy()

    if cfg.floating == "remove":
        keep = np.zeros(n, dtype=bool)
        keep[main_label] = True
        info["removed"] = len(island_labels)
        return apply_label_lut(labels, keep), info, bridges, bridge_layer

    # --- bridge ------------------------------------------------------------
    bridge_w_mm = cfg.bridge_width_mm or cfg.min_thickness_mm
    thickness_px = max(1, int(round(cfg.px(bridge_w_mm))))
    anchor = as_mask(labels == main_label)

    for lbl in island_labels[: cfg.max_bridges]:
        island = extract_component(labels, stats, lbl, mask.shape)
        a_pts = boundary_points(island)
        b_pts = boundary_points(anchor)
        p_from, p_to, dist_px = nearest_pair(a_pts, b_pts)
        if p_from is None or p_to is None or not math.isfinite(dist_px):
            continue

        # Both endpoints sit *on* a boundary, so a plain segment would meet the
        # metal in a knife edge one pixel deep. That is topologically connected
        # but geometrically fragile: the Douglas-Peucker pass will happily shave
        # the junction off and hand the shop a part that falls apart on the
        # table. Push each end into the solid it is anchoring to.
        a, b = _extend_segment(p_from, p_to, thickness_px, out.shape)
        cv2.line(out, a, b, FG, thickness=thickness_px, lineType=cv2.LINE_8)
        cv2.line(bridge_layer, a, b, FG, thickness=thickness_px, lineType=cv2.LINE_8)

        bridges.append(Bridge(
            from_xy=p_from, to_xy=p_to,
            length_mm=round(dist_px * cfg.mm_per_px, 2),
            width_mm=round(bridge_w_mm, 2),
            island_area_mm2=round(float(stats[lbl, cv2.CC_STAT_AREA]) * px2_to_mm2, 2),
        ))
        anchor = cv2.bitwise_or(anchor, cv2.bitwise_or(island, bridge_layer))

    if len(island_labels) > cfg.max_bridges:
        info["bridge_cap_hit"] = True

    info["bridges_added"] = len(bridges)
    return out, info, bridges, bridge_layer


# ---------------------------------------------------------------------------
# Stage 3 — minimum thickness enforcement
# ---------------------------------------------------------------------------

def sharp_tip_tolerance(tip_angle_deg: float) -> float:
    """
    How far (in units of r_min) residue may extend from the safe core before it
    counts as a genuine thin feature rather than an acceptable corner.

    Opening a shape with a disk of radius r rounds every convex corner to that
    radius. For a corner of included angle theta, the deepest point of the
    left-over sliver sits (1/sin(theta/2) - 1) * r from the rounded arc. So a
    single tolerance number is exactly equivalent to declaring the sharpest
    tip angle the torch is allowed to attempt:

        90 deg -> 0.41 r      60 deg -> 1.00 r      45 deg -> 1.61 r

    Anything reaching further than the tolerance is a hairline, a needle tip or
    a starved bridge, not a corner.
    """
    theta = math.radians(max(1.0, min(179.0, tip_angle_deg)) / 2.0)
    return max(0.05, 1.0 / math.sin(theta) - 1.0)


def analyze_thin_features(mask: np.ndarray, r_min_px: float,
                          tip_tol: float) -> Tuple[np.ndarray, List[float]]:
    """
    Locate every piece of material narrower than 2 * r_min_px.

    Method: morphological opening with a disk of radius r_min keeps exactly the
    material a minimum-width disk can roll through ("the safe core"). The
    residue is everything the disk cannot reach. That residue contains two very
    different things -- harmless slivers at convex corners, and genuinely fatal
    hairlines/needle tips -- and they are separated by how far the residue
    reaches away from the core (see sharp_tip_tolerance).

    Returns (thin_material_mask, measured_widths_px).
    """
    empty = np.zeros_like(mask)
    if r_min_px < 0.5 or not mask.any():
        return empty, []

    core = cv2.morphologyEx(mask, cv2.MORPH_OPEN, disk(int(math.ceil(r_min_px))))
    residue = cv2.bitwise_and(mask, cv2.bitwise_not(core))
    if not residue.any():
        return empty, []

    width_map = cv2.distanceTransform(mask, cv2.DIST_L2, 5)
    core_present = bool(core.any())
    if core_present:
        reach_map = cv2.distanceTransform(cv2.bitwise_not(core), cv2.DIST_L2, 5)

    n, labels, _, _ = cv2.connectedComponentsWithStats(residue, connectivity=8)
    sel = residue > 0
    limit = r_min_px * tip_tol

    if core_present:
        reach = group_max(labels, reach_map, sel, n)
        is_thin = reach > limit
    else:
        # No safe core anywhere: the entire design is sub-minimum.
        is_thin = np.ones(n, dtype=bool)
    is_thin[0] = False

    if not is_thin.any():
        return empty, []

    thin = apply_label_lut(labels, is_thin)
    half_widths = group_max(labels, width_map, sel, n)
    widths = [2.0 * float(w) for w in half_widths[is_thin]]
    return thin, widths


def enforce_min_thickness(mask: np.ndarray, cfg: Config) -> Tuple[np.ndarray, Dict, np.ndarray]:
    """
    Grow every sub-minimum feature up to the minimum thickness.

    Thin material is reduced to its centre line, and that centre line is dilated
    by the minimum radius. Because the medial axis is by definition equidistant
    from both walls, the regrown stroke lands symmetrically on the original
    path: hairlines fatten in place, needle tips round off to a radius the torch
    can survive, and everything already thick enough is bit-for-bit untouched.
    """
    r_min_px = cfg.px(cfg.min_thickness_mm) / 2.0
    tip_tol = sharp_tip_tolerance(cfg.sharp_tip_deg)
    info: Dict = {
        "min_thickness_mm": cfg.min_thickness_mm,
        "sharp_tip_deg": cfg.sharp_tip_deg,
        "passes": 0,
        "thin_regions_initial": 0,
        "thin_regions_remaining": 0,
        "added_area_mm2": 0.0,
        "added_area_pct": 0.0,
        "min_feature_mm_before": None,
        "min_feature_mm_after": None,
    }
    added_empty = np.zeros_like(mask)
    if r_min_px < 0.5 or not mask.any():
        return mask, info, added_empty

    original = mask.copy()
    original_area = float((original > 0).sum())
    kernel = disk(int(math.ceil(r_min_px)))
    out = mask

    for p in range(max(1, cfg.thicken_passes)):
        thin, widths = analyze_thin_features(out, r_min_px, tip_tol)
        if p == 0:
            info["thin_regions_initial"] = _count_components(thin)
            if widths:
                info["min_feature_mm_before"] = round(min(widths) * cfg.mm_per_px, 3)
        info["passes"] = p + 1
        if not thin.any():
            break
        grown = cv2.dilate(skeletonize_regions(thin), kernel)
        merged = cv2.bitwise_or(out, grown)
        if np.array_equal(merged, out):
            break
        out = merged

    residual, residual_widths = analyze_thin_features(out, r_min_px * 0.98, tip_tol)
    info["thin_regions_remaining"] = _count_components(residual)
    if residual_widths:
        info["min_feature_mm_after"] = round(min(residual_widths) * cfg.mm_per_px, 3)

    added = cv2.bitwise_and(out, cv2.bitwise_not(original))
    added_px = float((added > 0).sum())
    info["added_area_mm2"] = round(added_px * cfg.mm_per_px ** 2, 2)
    info["added_area_pct"] = round(100.0 * added_px / original_area, 2) if original_area else 0.0
    return out, info, added


def _count_components(mask: np.ndarray) -> int:
    if not mask.any():
        return 0
    n, _ = cv2.connectedComponents(mask, connectivity=8)
    return max(0, n - 1)


# ---------------------------------------------------------------------------
# Stage 4 — narrow negative space (kerf survivability)
# ---------------------------------------------------------------------------

def analyze_narrow_gaps(mask: np.ndarray, cfg: Config) -> Tuple[np.ndarray, Dict, np.ndarray]:
    """
    Find slots of *removed* material too narrow for the beam to produce.

    This is the exact dual of the minimum-thickness check: run the same
    opening-residue analysis on the negative space. Below roughly
    kerf * gap_factor the two kerf walls overlap, the slot closes up, and two
    features that were meant to be separate fuse into one.

    Using the same reach test here matters -- a naive closing flags every
    concave corner in the design, which buries the real defects in noise. The
    mask is padded first so artwork running off the sheet edge is not mistaken
    for a narrow channel.
    """
    info: Dict = {
        "kerf_mm": cfg.kerf_mm,
        "gap_threshold_mm": round(cfg.kerf_mm * cfg.gap_factor, 3),
        "narrow_gap_count": 0,
        "narrow_gap_area_mm2": 0.0,
        "min_gap_mm": None,
        "filled": False,
    }
    threshold_mm = cfg.kerf_mm * cfg.gap_factor
    r_px = cfg.px(threshold_mm) / 2.0
    if r_px < 0.5 or not mask.any():
        return mask, info, np.zeros_like(mask)

    pad = int(math.ceil(r_px)) + 2
    padded = cv2.copyMakeBorder(mask, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=BG)
    background = cv2.bitwise_not(padded)

    tip_tol = sharp_tip_tolerance(cfg.sharp_tip_deg)
    thin_bg, widths = analyze_thin_features(background, r_px, tip_tol)
    gaps = thin_bg[pad:pad + mask.shape[0], pad:pad + mask.shape[1]]

    # Discard sub-pixel speckle picked up by the round structuring element.
    gaps, _ = drop_small_components(gaps, max(4.0, cfg.mm2_to_px2(0.25)))

    info["narrow_gap_count"] = _count_components(gaps)
    info["narrow_gap_area_mm2"] = round(float((gaps > 0).sum()) * cfg.mm_per_px ** 2, 2)
    if widths and info["narrow_gap_count"]:
        info["min_gap_mm"] = round(min(widths) * cfg.mm_per_px, 3)

    if cfg.fill_narrow_gaps and gaps.any():
        mask = cv2.bitwise_or(mask, gaps)
        info["filled"] = True

    return mask, info, gaps


def apply_kerf_offset(mask: np.ndarray, cfg: Config) -> Tuple[np.ndarray, Dict]:
    """
    Optional global kerf compensation.

    Most CAM packages (SheetCam, Fusion, LightBurn) apply the offset themselves
    from the tool definition. Baking it into the geometry as well doubles it —
    hence the 'none' default. Use 'outward' only when your post has no tool
    offset and you cut on the line.
    """
    info = {"kerf_compensate": cfg.kerf_compensate, "offset_mm": 0.0}
    if cfg.kerf_compensate == "none" or cfg.kerf_mm <= 0:
        return mask, info
    r = cfg.px(cfg.kerf_mm / 2.0)
    if r < 0.5:
        return mask, info
    k = disk(int(round(r)))
    info["offset_mm"] = round(cfg.kerf_mm / 2.0, 3)
    if cfg.kerf_compensate == "outward":
        return cv2.dilate(mask, k), info
    return cv2.erode(mask, k), info


# ---------------------------------------------------------------------------
# Stage 5 — vectorisation: contours -> Douglas-Peucker -> corner-aware Bezier
# ---------------------------------------------------------------------------

Point = Tuple[float, float]


@dataclass
class SubPath:
    """One closed loop. Segments are ('L', p) or ('C', c1, c2, p)."""
    start: Point
    segments: List[tuple]
    is_hole: bool
    node_count: int
    raw_node_count: int


def trace_contours(mask: np.ndarray) -> List[Tuple[np.ndarray, bool]]:
    """
    Extract outer boundaries and holes.

    RETR_CCOMP gives a two-level hierarchy: top level = outer shells,
    second level = the holes inside them. CHAIN_APPROX_NONE keeps every pixel
    so the Douglas-Peucker step has full information to work from.
    """
    cnts, hier = cv2.findContours(mask, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_NONE)
    if hier is None or len(cnts) == 0:
        return []
    hier = hier[0]
    out: List[Tuple[np.ndarray, bool]] = []
    for i, c in enumerate(cnts):
        if len(c) < 3:
            continue
        is_hole = hier[i][3] != -1
        out.append((c.reshape(-1, 2).astype(np.float64), is_hole))
    return out


def simplify_contour(points: np.ndarray, epsilon_px: float) -> np.ndarray:
    """
    Douglas-Peucker node reduction.

    This is the step that stops the machine stuttering: a pixel-traced contour
    has one node per pixel, and a controller trying to decelerate into each one
    produces visible facet marks and dwell burn. Epsilon is a real distance,
    so the maximum deviation from the traced outline is bounded in millimetres.
    """
    c = points.reshape(-1, 1, 2).astype(np.float32)
    approx = cv2.approxPolyDP(c, float(max(epsilon_px, 1e-6)), True)
    simplified = approx.reshape(-1, 2).astype(np.float64)
    if len(simplified) < 3:  # degenerate — fall back to a coarse decimation
        step = max(1, len(points) // 8)
        simplified = points[::step]
    return dedupe_closed(simplified)


def dedupe_closed(pts: np.ndarray, tol: float = 1e-6) -> np.ndarray:
    """Drop consecutive duplicate vertices, including the wrap-around pair."""
    if len(pts) < 2:
        return pts
    keep = [pts[0]]
    for p in pts[1:]:
        if np.hypot(*(p - keep[-1])) > tol:
            keep.append(p)
    arr = np.asarray(keep)
    while len(arr) > 3 and np.hypot(*(arr[0] - arr[-1])) <= tol:
        arr = arr[:-1]
    return arr


def turn_angles_deg(pts: np.ndarray) -> np.ndarray:
    """Interior turn angle at each vertex of a closed polygon, in degrees."""
    prev_v = pts - np.roll(pts, 1, axis=0)
    next_v = np.roll(pts, -1, axis=0) - pts
    n1 = np.linalg.norm(prev_v, axis=1)
    n2 = np.linalg.norm(next_v, axis=1)
    safe = (n1 > 1e-9) & (n2 > 1e-9)
    cos = np.ones(len(pts))
    cos[safe] = np.clip(
        (prev_v[safe] * next_v[safe]).sum(axis=1) / (n1[safe] * n2[safe]), -1.0, 1.0)
    return np.degrees(np.arccos(cos))


def polygon_to_bezier(pts: np.ndarray, corner_deg: float, tension: float) -> List[tuple]:
    """
    Corner-preserving Catmull-Rom -> cubic Bezier conversion.

    A vertex whose turn angle exceeds `corner_deg` is treated as a deliberate
    artistic corner: its tangent is zeroed so the adjacent segments meet sharply.
    Everywhere else a smooth tangent is fitted, which gives the cutting head a
    continuous path to accelerate along instead of a chain of micro-facets.
    """
    n = len(pts)
    if n < 3:
        return [("L", tuple(p)) for p in pts[1:]]
    if tension <= 0.0:
        return [("L", tuple(pts[(i + 1) % n])) for i in range(n)]

    angles = turn_angles_deg(pts)
    is_corner = angles > float(corner_deg)

    tangents = (np.roll(pts, -1, axis=0) - np.roll(pts, 1, axis=0)) * 0.5 * float(tension)
    tangents[is_corner] = 0.0

    segments: List[tuple] = []
    for i in range(n):
        j = (i + 1) % n
        p0, p1 = pts[i], pts[j]
        t0, t1 = tangents[i], tangents[j]
        if not t0.any() and not t1.any():
            segments.append(("L", (float(p1[0]), float(p1[1]))))
            continue

        # Clamp each handle to a third of this segment's own length. A raw
        # Catmull-Rom tangent is derived from the *neighbouring* vertices, so
        # where node spacing changes abruptly -- exactly what happens at a
        # bridge junction, where a long straight edge meets a short corner run
        # -- the handle overshoots past the far endpoint and the curve loops
        # back on itself. Under an even-odd fill that loop cancels, punching a
        # hole straight through the join. Clamping keeps every curve inside the
        # hull of its own endpoints.
        seg_len = float(np.linalg.norm(p1 - p0))
        max_handle = seg_len / 3.0
        h0, h1 = t0 / 3.0, t1 / 3.0
        n0, n1 = float(np.linalg.norm(h0)), float(np.linalg.norm(h1))
        if n0 > max_handle > 0:
            h0 = h0 * (max_handle / n0)
        if n1 > max_handle > 0:
            h1 = h1 * (max_handle / n1)
        c1 = p0 + h0
        c2 = p1 - h1
        segments.append((
            "C",
            (float(c1[0]), float(c1[1])),
            (float(c2[0]), float(c2[1])),
            (float(p1[0]), float(p1[1])),
        ))
    return segments


def rasterize_subpaths(subpaths: List[SubPath], shape: Tuple[int, int],
                       flatten_tol_px: float) -> np.ndarray:
    """
    Re-render vector output back to a mask, honouring the even-odd fill rule.

    Even-odd fill is exactly a parity test, so XOR-ing each subpath's filled
    interior reproduces what a CAM importer will see -- including islands that
    sit inside holes, which a naive outer-then-hole fill would erase.
    """
    acc = np.zeros(shape, np.uint8)
    layer = np.zeros(shape, np.uint8)
    for sp in subpaths:
        pts = subpath_to_polyline(sp, flatten_tol_px)
        if len(pts) < 3:
            continue
        poly = np.round(np.asarray(pts, dtype=np.float64)).astype(np.int32).reshape(-1, 1, 2)
        layer[:] = 0
        cv2.fillPoly(layer, [poly], 1)
        np.bitwise_xor(acc, layer, out=acc)
    return as_mask(acc)


def _segments_cross(p1, p2, p3, p4) -> bool:
    """Proper (non-degenerate) intersection test for two open segments."""
    def orient(a, b, c):
        v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        return 0 if abs(v) < 1e-9 else (1 if v > 0 else -1)
    d1, d2 = orient(p3, p4, p1), orient(p3, p4, p2)
    d3, d4 = orient(p1, p2, p3), orient(p1, p2, p4)
    return d1 != d2 and d3 != d4 and d1 != 0 and d2 != 0 and d3 != 0 and d4 != 0


def count_self_intersections(polyline: List[Point], cell: float = 24.0,
                             cap: int = 50) -> int:
    """
    Count places where a closed polyline crosses itself.

    A self-intersecting contour is not a cosmetic problem: under an even-odd
    fill the overlapping lobe inverts, and CAM will happily emit a toolpath
    that cuts through the middle of the part. Segments are bucketed into a
    uniform grid so only near neighbours are compared, which keeps this linear
    in practice instead of quadratic.
    """
    n = len(polyline)
    if n < 5:
        return 0
    buckets: Dict[Tuple[int, int], List[int]] = {}
    for i in range(n):
        a, b = polyline[i], polyline[(i + 1) % n]
        x0, x1 = sorted((a[0], b[0]))
        y0, y1 = sorted((a[1], b[1]))
        for cx in range(int(x0 // cell), int(x1 // cell) + 1):
            for cy in range(int(y0 // cell), int(y1 // cell) + 1):
                buckets.setdefault((cx, cy), []).append(i)

    seen = set()
    hits = 0
    for ids in buckets.values():
        for ai in range(len(ids)):
            for bi in range(ai + 1, len(ids)):
                i, j = ids[ai], ids[bi]
                if i > j:
                    i, j = j, i
                if j - i <= 1 or (i == 0 and j == n - 1):
                    continue  # adjacent segments legitimately share an endpoint
                if (i, j) in seen:
                    continue
                seen.add((i, j))
                if _segments_cross(polyline[i], polyline[(i + 1) % n],
                                   polyline[j], polyline[(j + 1) % n]):
                    hits += 1
                    if hits >= cap:
                        return hits
    return hits


def check_topology(mask: np.ndarray, subpaths: List[SubPath],
                   flatten_tol_px: float) -> Tuple[bool, Dict]:
    """
    Confirm the exported geometry is still the shape the pipeline signed off on.

    Node reduction is not topology-preserving in general: a Douglas-Peucker pass
    can cut across a narrow neck and silently sever a bridge, so a part that
    passed every structural check leaves as two pieces. Comparing component
    count and area overlap against the validated mask is cheap insurance, and
    it is the difference between finding this at the desk and finding it on the
    cutting table.
    """
    rendered = rasterize_subpaths(subpaths, mask.shape, flatten_tol_px)
    want = _count_components(mask)
    got = _count_components(rendered)
    inter = float(cv2.bitwise_and(mask, rendered).sum())
    union = float(cv2.bitwise_or(mask, rendered).sum())
    iou = inter / union if union else 1.0

    # cv2.fillPoly floods self-intersections solid, so an area comparison alone
    # cannot see a cancelling loop. Test the polylines directly.
    crossings = 0
    for sp in subpaths:
        crossings += count_self_intersections(subpath_to_polyline(sp, flatten_tol_px))
        if crossings >= 50:
            break

    # A *split* is the dangerous direction: a piece that was one part in the
    # validated mask leaves as two, which is how a severed bridge escapes.
    # A *merge* in the opposite direction is almost always a rasterisation
    # artefact -- fragments a pixel apart fuse in any renderer, including the
    # CAM importer's -- and the narrow-gap stage already owns real fusion
    # risk, so it is recorded but does not fail the check or trigger a retry.
    split = got > want
    ok = (not split) and iou >= 0.98 and crossings == 0
    retryable = split or crossings > 0 or iou < 0.98
    return ok, {
        "components_expected": want,
        "components_exported": got,
        "components_split": bool(split),
        "components_merged": max(0, want - got),
        "iou": round(iou, 4),
        "self_intersections": crossings,
        "retryable": retryable,
    }


def vectorize(mask: np.ndarray, cfg: Config) -> Tuple[List[SubPath], Dict]:
    """
    Full raster -> path conversion, verified.

    Simplification runs at the requested tolerance, the result is checked
    against the mask, and on a topology change the tolerance is halved and the
    pass retried. Fewer nodes is always negotiable; a part that arrives in two
    pieces is not.
    """
    eps_px = cfg.px(cfg.simplify_mm)
    flatten_px = cfg.px(cfg.bezier_flatten_mm)
    best = None

    for attempt in range(4):
        subpaths, stats = _vectorize_once(mask, cfg, eps_px)
        ok, topo = check_topology(mask, subpaths, flatten_px) if subpaths else (
            False, {"retryable": False})
        stats["topology"] = topo
        stats["topology_ok"] = ok
        stats["simplify_epsilon_mm"] = round(eps_px * cfg.mm_per_px, 4)
        stats["simplify_retries"] = attempt

        score = (ok, topo.get("iou", 0.0) - 0.01 * topo.get("self_intersections", 0))
        if best is None or score > best[0]:
            best = (score, subpaths, stats)

        # Only halve the tolerance for faults a finer fit can actually repair.
        # Retrying blindly quadruples the node count for no benefit, which is
        # the opposite of the point of this stage.
        if ok or not topo.get("retryable") or eps_px <= 0.25:
            break
        eps_px *= 0.5

    return best[1], best[2]


def _vectorize_once(mask: np.ndarray, cfg: Config,
                    eps_px: float) -> Tuple[List[SubPath], Dict]:
    """One simplification pass at a fixed tolerance."""
    subpaths: List[SubPath] = []
    raw_total = 0
    kept_total = 0

    for contour, is_hole in trace_contours(mask):
        raw_total += len(contour)
        simple = simplify_contour(contour, eps_px)
        if len(simple) < 3:
            continue
        kept_total += len(simple)
        segments = polygon_to_bezier(simple, cfg.corner_deg, cfg.bezier_tension)
        subpaths.append(SubPath(
            start=(float(simple[0][0]), float(simple[0][1])),
            segments=segments,
            is_hole=is_hole,
            node_count=len(simple),
            raw_node_count=len(contour),
        ))

    stats = {
        "subpaths": len(subpaths),
        "holes": sum(1 for s in subpaths if s.is_hole),
        "nodes_raw": raw_total,
        "nodes_final": kept_total,
        "node_reduction_pct": round(100.0 * (1 - kept_total / raw_total), 2) if raw_total else 0.0,
    }
    return subpaths, stats


# ---------------------------------------------------------------------------
# Stage 6 — writers
# ---------------------------------------------------------------------------

def flatten_cubic(p0: Point, c1: Point, c2: Point, p1: Point, tol_px: float) -> List[Point]:
    """Adaptive-ish flattening of a cubic Bezier into a polyline (for DXF)."""
    chord = math.dist(p0, p1)
    ctrl = math.dist(p0, c1) + math.dist(c1, c2) + math.dist(c2, p1)
    error_est = max(ctrl - chord, 0.0)
    n = int(math.ceil(math.sqrt(error_est / max(tol_px, 1e-6)) * 4)) if error_est > 0 else 1
    n = max(2, min(n, 64))
    ts = np.linspace(0.0, 1.0, n + 1)[1:]
    out = []
    for t in ts:
        mt = 1.0 - t
        x = (mt ** 3 * p0[0] + 3 * mt ** 2 * t * c1[0] + 3 * mt * t ** 2 * c2[0] + t ** 3 * p1[0])
        y = (mt ** 3 * p0[1] + 3 * mt ** 2 * t * c1[1] + 3 * mt * t ** 2 * c2[1] + t ** 3 * p1[1])
        out.append((x, y))
    return out


def subpath_to_polyline(sp: SubPath, tol: float) -> List[Point]:
    """Flatten one subpath into a closed polyline."""
    pts: List[Point] = [sp.start]
    cur = sp.start
    for seg in sp.segments:
        if seg[0] == "L":
            pts.append(seg[1]); cur = seg[1]
        else:
            _, c1, c2, p = seg
            pts.extend(flatten_cubic(cur, c1, c2, p, tol))
            cur = p
    return pts


def write_svg(path: Path, subpaths: List[SubPath], w_mm: float, h_mm: float,
              mm_per_px: float, source_name: str) -> None:
    """
    Emit an SVG in real millimetre units.

    Outer shells and holes go into one path with fill-rule="evenodd", which is
    how every CAM importer expects nested cut geometry to arrive.
    """
    def fmt(v: float) -> str:
        return f"{v:.4f}".rstrip("0").rstrip(".") or "0"

    def to_mm(p: Point) -> Tuple[float, float]:
        return p[0] * mm_per_px, p[1] * mm_per_px

    commands: List[str] = []
    for sp in subpaths:
        sx, sy = to_mm(sp.start)
        commands.append(f"M {fmt(sx)} {fmt(sy)}")
        for seg in sp.segments:
            if seg[0] == "L":
                x, y = to_mm(seg[1])
                commands.append(f"L {fmt(x)} {fmt(y)}")
            else:
                _, c1, c2, p = seg
                c1x, c1y = to_mm(c1); c2x, c2y = to_mm(c2); px_, py_ = to_mm(p)
                commands.append(
                    f"C {fmt(c1x)} {fmt(c1y)} {fmt(c2x)} {fmt(c2y)} {fmt(px_)} {fmt(py_)}")
        commands.append("Z")

    d = " ".join(commands)
    svg = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1"\n'
        f'     width="{fmt(w_mm)}mm" height="{fmt(h_mm)}mm"\n'
        f'     viewBox="0 0 {fmt(w_mm)} {fmt(h_mm)}">\n'
        f'  <title>{_xml_escape(source_name)} — CNC optimised</title>\n'
        f'  <desc>Generated by cnc_prep.py. Units: mm. fill-rule=evenodd.</desc>\n'
        f'  <g id="cut">\n'
        f'    <path fill="#000000" fill-rule="evenodd" stroke="none" d="{d}"/>\n'
        f'  </g>\n'
        f'</svg>\n'
    )
    path.write_text(svg, encoding="utf-8")


def _xml_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
             .replace(">", "&gt;").replace('"', "&quot;"))


def write_dxf(path: Path, subpaths: List[SubPath], w_mm: float, h_mm: float,
              mm_per_px: float, flatten_tol_px: float) -> None:
    """
    Minimal, maximally-compatible DXF R12 (AC1009) with closed POLYLINE entities.

    R12 is deliberate: every plasma/laser CAM package on earth reads it without
    argument, and it avoids SPLINE entities that older controllers choke on.
    Y is flipped because DXF is Y-up while raster/SVG are Y-down.
    """
    loops: List[List[Point]] = []
    for sp in subpaths:
        pts = subpath_to_polyline(sp, flatten_tol_px)
        loops.append([(x * mm_per_px, h_mm - y * mm_per_px) for x, y in pts])

    if loops:
        xs = [p[0] for lp in loops for p in lp]
        ys = [p[1] for lp in loops for p in lp]
        xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    else:
        xmin, xmax, ymin, ymax = 0.0, w_mm, 0.0, h_mm

    out: List[str] = []
    def g(code: int, value) -> None:
        out.append(f"{code}\n{value}\n")

    # HEADER
    g(0, "SECTION"); g(2, "HEADER")
    g(9, "$ACADVER"); g(1, "AC1009")
    g(9, "$INSUNITS"); g(70, 4)          # 4 == millimetres
    g(9, "$EXTMIN"); g(10, f"{xmin:.6f}"); g(20, f"{ymin:.6f}"); g(30, "0.0")
    g(9, "$EXTMAX"); g(10, f"{xmax:.6f}"); g(20, f"{ymax:.6f}"); g(30, "0.0")
    g(0, "ENDSEC")

    # TABLES (linetype + layer, so strict readers are happy)
    g(0, "SECTION"); g(2, "TABLES")
    g(0, "TABLE"); g(2, "LTYPE"); g(70, 1)
    g(0, "LTYPE"); g(2, "CONTINUOUS"); g(70, 0); g(3, "Solid line")
    g(72, 65); g(73, 0); g(40, "0.0")
    g(0, "ENDTAB")
    g(0, "TABLE"); g(2, "LAYER"); g(70, 1)
    g(0, "LAYER"); g(2, "CUT"); g(70, 0); g(62, 7); g(6, "CONTINUOUS")
    g(0, "ENDTAB")
    g(0, "ENDSEC")

    # ENTITIES
    g(0, "SECTION"); g(2, "ENTITIES")
    for loop in loops:
        if len(loop) < 3:
            continue
        g(0, "POLYLINE"); g(8, "CUT"); g(66, 1); g(70, 1)
        g(10, "0.0"); g(20, "0.0"); g(30, "0.0")
        for x, y in loop:
            g(0, "VERTEX"); g(8, "CUT")
            g(10, f"{x:.6f}"); g(20, f"{y:.6f}"); g(30, "0.0")
        g(0, "SEQEND"); g(8, "CUT")
    g(0, "ENDSEC")
    g(0, "EOF")

    path.write_text("".join(out), encoding="ascii", errors="replace")


def write_png(path: Path, subpaths: List[SubPath], w_mm: float, h_mm: float,
              mm_per_px: float, flatten_tol_px: float, dpi: float,
              canvas_px: Optional[Tuple[int, int]]) -> Tuple[int, int]:
    """
    Rasterise the final vector geometry to a pure black-on-white PNG.

    Rendered fresh from the verified subpaths (not the working-resolution mask)
    so the PNG matches the SVG/DXF pixel-for-pixel at whatever DPI a print /
    engraving vendor asks for. If canvas_px is given, the artwork is scaled to
    fit inside it (never upscaled beyond its native DPI) and centred on a white
    canvas of exactly that pixel size -- vendors that demand a fixed upload
    resolution (e.g. 4500x5100) need the canvas exact even when the artwork's
    own aspect ratio does not match it.

    Returns the (width, height) actually written, for reporting.
    """
    px_per_mm = dpi / 25.4
    design_w_px = w_mm * px_per_mm
    design_h_px = h_mm * px_per_mm

    if canvas_px:
        canvas_w, canvas_h = canvas_px
        fit = min(canvas_w / design_w_px, canvas_h / design_h_px, 1.0)
    else:
        canvas_w = max(1, int(round(design_w_px)))
        canvas_h = max(1, int(round(design_h_px)))
        fit = 1.0

    scale = mm_per_px * px_per_mm * fit
    off_x = (canvas_w - design_w_px * fit) / 2.0
    off_y = (canvas_h - design_h_px * fit) / 2.0

    acc = np.zeros((canvas_h, canvas_w), np.uint8)
    layer = np.zeros_like(acc)
    for sp in subpaths:
        pts = subpath_to_polyline(sp, flatten_tol_px)
        if len(pts) < 3:
            continue
        poly = np.array(
            [(p[0] * scale + off_x, p[1] * scale + off_y) for p in pts],
            dtype=np.float64,
        )
        poly = np.round(poly).astype(np.int32).reshape(-1, 1, 2)
        layer[:] = 0
        cv2.fillPoly(layer, [poly], 1)
        np.bitwise_xor(acc, layer, out=acc)

    art = as_mask(acc)
    img = np.full((canvas_h, canvas_w), 255, np.uint8)   # white background
    img[art > 0] = 0                                      # black shape
    im = Image.fromarray(img, mode="L").convert("1")       # 1-bit: keeps the file tiny
    im.save(str(path), dpi=(dpi, dpi), optimize=True)
    return canvas_w, canvas_h


def write_preview(path: Path, base: np.ndarray, final: np.ndarray, added: np.ndarray,
                  gaps: np.ndarray, bridges: np.ndarray, islands_mask: np.ndarray,
                  vector_deviation: Optional[np.ndarray] = None,
                  max_dim: int = 2000) -> None:
    """
    QC overlay so a human can eyeball what the pipeline changed.

        grey    = original metal              green   = material added (thickening)
        blue    = bridges welded in           orange  = sub-kerf slots
        magenta = floating islands            red     = vector output deviates from mask

    The red layer only appears when check_topology() flagged an IoU below its
    threshold: it is the symmetric difference between the validated raster mask
    and what rasterize_subpaths() produces from the *exported* geometry, so it
    points at the exact spot the Douglas-Peucker / Bezier pass distorted --
    otherwise that warning names no location at all.
    """
    h, w = base.shape[:2]
    canvas = np.full((h, w, 3), 255, np.uint8)
    canvas[final > 0] = (70, 70, 70)
    canvas[(base > 0) & (final == 0)] = (200, 200, 255)   # removed material
    if added is not None and added.any():
        canvas[added > 0] = (0, 190, 0)
    if gaps is not None and gaps.any():
        canvas[gaps > 0] = (0, 140, 255)
    if bridges is not None and bridges.any():
        canvas[bridges > 0] = (255, 120, 0)
    if islands_mask is not None and islands_mask.any():
        cnts, _ = cv2.findContours(islands_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(canvas, cnts, -1, (255, 0, 255), 3)
    if vector_deviation is not None and vector_deviation.any():
        canvas[vector_deviation > 0] = (0, 0, 255)
        cnts, _ = cv2.findContours(vector_deviation, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        cv2.drawContours(canvas, cnts, -1, (0, 0, 200), 2)

    scale = min(1.0, max_dim / max(h, w))
    if scale < 1.0:
        canvas = cv2.resize(canvas, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(path), canvas)


# ---------------------------------------------------------------------------
# Minimum safe size search
# ---------------------------------------------------------------------------

MM_PER_INCH = 25.4


def _check_size_ok(src: Path, base_cfg: Config, width_mm: float,
                   min_iou: float = 0.90) -> Tuple[bool, int, int, float]:
    """
    Run the *entire* pipeline (through vectorisation + topology check) at a
    trial width, without writing anything. Returns (ok, thin_regions, narrow_gaps, iou).

    Small trial sizes need the full pipeline, not just thickness/gap
    enforcement: at a low raster resolution the fixed-mm Douglas-Peucker
    tolerance becomes large relative to the shrunk raster and distorts the
    outline even when every stroke is already thick enough -- that failure
    mode only shows up after vectorising.

    "ok" is deliberately *not* vec_stats["topology_ok"]. That flag demands
    IoU >= 0.98, which conflates two unrelated failures: a severed bridge or
    self-crossing outline (genuinely dangerous -- the part can fall apart on
    the table) versus a hatching-dense design's edges picking up a fraction
    of a pixel of rounding noise at every one of a few hundred thin parallel
    strokes (a cosmetic sub-pixel fuzz with zero cutting risk). Using the
    0.98 bar here made --suggest-size wildly over-recommend size on
    engraving-style line art. What actually endangers the part is a split
    or a self-intersection; IoU is only checked against a much looser floor
    to catch a genuinely mangled outline, not to chase perfect crispness.
    """
    trial = dataclass_replace(base_cfg, width_mm=width_mm, height_mm=None)
    gray, w_mm, h_mm = load_source(src, trial)
    gray = resample_to_work_res(gray, trial, w_mm)
    trial.mm_per_px = w_mm / gray.shape[1]

    mask = binarize(gray, trial)
    if not mask.any() or float((mask > 0).mean()) > 0.999:
        return False, -1, -1, 0.0

    mask, _ = denoise(mask, trial)
    mask = dejag(mask, trial)

    mask, _, _, _ = handle_floating(mask, trial)
    mask, thick_info, _ = enforce_min_thickness(mask, trial)
    mask, gap_info, _ = analyze_narrow_gaps(mask, trial)
    mask, _ = apply_kerf_offset(mask, trial)

    subpaths, vec_stats = vectorize(mask, trial)
    topo = vec_stats.get("topology", {}) if subpaths else {}
    iou = topo.get("iou", 0.0)
    structurally_sound = bool(subpaths) and not topo.get("components_split", True) \
        and topo.get("self_intersections", 1) == 0 and iou >= min_iou

    gaps_ok = gap_info["narrow_gap_count"] == 0 or trial.fill_narrow_gaps
    ok = thick_info["thin_regions_remaining"] == 0 and gaps_ok and structurally_sound
    return ok, thick_info["thin_regions_remaining"], gap_info["narrow_gap_count"], iou


def _check_size_ok_worker(args: Tuple[str, Config, float]) -> Tuple[float, Tuple[bool, int, int, float]]:
    """Top-level so it can be pickled by ProcessPoolExecutor. See _worker() for the same pattern."""
    src_str, base_cfg, width = args
    cv2.setNumThreads(1)
    return width, _check_size_ok(Path(src_str), base_cfg, width)


def estimate_min_safe_size(src: Path, base_cfg: Config, jobs: int = 4) -> Dict:
    """
    Find the smallest finished width that is *structurally* safe to cut: every
    stroke clears --min-thickness-mm, every gap clears the kerf threshold, and
    the exported outline has no severed part and no self-crossing edge. It
    does NOT chase a pixel-perfect vector fit (see _check_size_ok) -- a design
    made of hundreds of thin parallel hatching strokes will always show a
    little sub-pixel rounding noise, and that noise is cosmetic, not a cutting
    risk. The reported size can still carry that cosmetic fuzz; "iou" in each
    checked[] entry is left in the result for exactly that judgement call.

    Search strategy: a geometric progression of candidate widths from a small
    floor up to a generous ceiling is evaluated *in parallel* (each candidate
    is fully independent of the others, so there is nothing to gain from
    doing them one at a time), then the narrow bracket between the largest
    failure and the smallest pass is refined sequentially to a few
    millimetres. On a hatching-dense design where a single evaluation can
    take minutes, running the coarse pass in parallel is the difference
    between a few minutes and tens of minutes.
    """
    ref_width = base_cfg.width_mm or (base_cfg.height_mm or 400.0)
    lo = max(20.0, ref_width * 0.15)
    hi = ref_width * 4.0
    tol_mm = 3.0

    info: Dict = {
        "checked": [],
        "min_safe_width_mm": None,
        "min_safe_height_mm": None,
        "min_safe_width_in": None,
        "min_safe_height_in": None,
        "aspect_h_over_w": None,
    }

    try:
        gray0, w0_mm, h0_mm = load_source(src, dataclass_replace(base_cfg, height_mm=None,
                                                                  width_mm=ref_width))
    except Exception as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"
        return info
    aspect = h0_mm / w0_mm if w0_mm else 1.0
    info["aspect_h_over_w"] = round(aspect, 4)

    candidates: List[float] = []
    w = lo
    while w <= hi:
        candidates.append(w)
        w *= 1.35

    results: Dict[float, Tuple[bool, int, int, float]] = {}
    workers = max(1, min(jobs, len(candidates), os.cpu_count() or 4))
    if workers > 1 and len(candidates) > 1:
        payload = [(str(src), base_cfg, cw) for cw in candidates]
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for cw, res in pool.map(_check_size_ok_worker, payload):
                results[cw] = res
    else:
        for cw in candidates:
            results[cw] = _check_size_ok(src, base_cfg, cw)

    for cw in candidates:
        ok, thin, gaps, iou = results[cw]
        info["checked"].append({"width_mm": round(cw, 1), "ok": ok,
                                "thin_regions": thin, "narrow_gaps": gaps, "iou": round(iou, 4)})

    last_fail_width = lo
    first_pass_width = None
    for cw in candidates:
        ok = results[cw][0]
        if ok:
            first_pass_width = cw
            break
        last_fail_width = cw

    if first_pass_width is None:
        info["error"] = (
            f"{hi:.0f}mm genişliğe kadar denendi, hâlâ ince/dar bölge var — "
            f"tasarımın kendisinde neredeyse sıfır genişlikte bir kusur olabilir"
        )
        return info

    # Binary-search refine between the last known failure and the first pass.
    # This range is narrow (one geometric step wide) so it stays sequential --
    # only a handful of evaluations are needed and there is little left to
    # parallelise.
    low, high = last_fail_width, first_pass_width
    while high - low > tol_mm:
        mid = (low + high) / 2.0
        ok, thin, gaps, iou = _check_size_ok(src, base_cfg, mid)
        info["checked"].append({"width_mm": round(mid, 1), "ok": ok,
                                "thin_regions": thin, "narrow_gaps": gaps, "iou": round(iou, 4)})
        if ok:
            high = mid
        else:
            low = mid

    safe_w = math.ceil(high)
    safe_h = safe_w * aspect
    info["min_safe_width_mm"] = round(safe_w, 1)
    info["min_safe_height_mm"] = round(safe_h, 1)
    info["min_safe_width_in"] = round(safe_w / MM_PER_INCH, 2)
    info["min_safe_height_in"] = round(safe_h / MM_PER_INCH, 2)
    return info


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def process_file(src: Path, out_dir: Path, cfg: Config) -> Dict:
    """Run the whole pipeline for one design. Never raises; failures land in the report."""
    t0 = time.perf_counter()
    report: Dict = {
        "source": str(src),
        "status": "ok",
        "warnings": [],
        "errors": [],
        "outputs": [],
    }

    try:
        if cfg.suggest_size:
            report["size_suggestion"] = estimate_min_safe_size(src, cfg, jobs=cfg.suggest_jobs)

        gray, w_mm, h_mm = load_source(src, cfg)

        if int(gray.max()) - int(gray.min()) < 8:
            raise ValueError("image is a single flat tone — there is no artwork to cut")

        gray = resample_to_work_res(gray, cfg, w_mm)   # grayscale first -- see the docstring
        cfg.mm_per_px = w_mm / gray.shape[1]

        mask = binarize(gray, cfg)
        if not mask.any():
            raise ValueError("binarisation produced an empty mask — try --invert yes "
                             "or a fixed --threshold")
        if float((mask > 0).mean()) > 0.999:
            raise ValueError("binarisation selected the entire sheet — try --invert no "
                             "or a fixed --threshold")

        mask, denoise_stats = denoise(mask, cfg)
        mask = dejag(mask, cfg)

        base = mask.copy()

        # islands (recorded before any mutation, for the preview overlay)
        islands_mask = compute_islands_mask(mask)

        mask, float_info, bridges, bridge_layer = handle_floating(mask, cfg)
        mask, thick_info, added = enforce_min_thickness(mask, cfg)
        mask, gap_info, gaps = analyze_narrow_gaps(mask, cfg)

        if gap_info["filled"]:
            mask, thick_info2, added2 = enforce_min_thickness(mask, cfg)
            added = cv2.bitwise_or(added, added2)
            thick_info["thin_regions_remaining"] = thick_info2["thin_regions_remaining"]
            thick_info["min_feature_mm_after"] = thick_info2["min_feature_mm_after"]

        mask, kerf_info = apply_kerf_offset(mask, cfg)

        subpaths, vec_stats = vectorize(mask, cfg)
        if not subpaths:
            raise ValueError("vectorisation produced no geometry")

        vec_deviation = None
        if not vec_stats.get("topology_ok", True):
            rendered = rasterize_subpaths(subpaths, mask.shape, cfg.px(cfg.bezier_flatten_mm))
            vec_deviation = cv2.bitwise_xor(mask, rendered)

        report.update({
            "size_mm": [round(w_mm, 2), round(h_mm, 2)],
            "work_resolution_px_per_mm": round(1.0 / cfg.mm_per_px, 3),
            "raster_px": [int(mask.shape[1]), int(mask.shape[0])],
            "denoise": denoise_stats,
            "floating": float_info,
            "thickness": thick_info,
            "gaps": gap_info,
            "kerf": kerf_info,
            "vector": vec_stats,
            "bridges": [asdict(b) for b in bridges],
        })

        # --- warnings the operator actually needs to see --------------------
        if float_info["island_count"] and cfg.floating == "warn":
            report["warnings"].append(
                f"{float_info['island_count']} floating element(s) will drop out of the sheet — "
                f"re-run with --floating bridge or --floating remove")
        if thick_info["thin_regions_remaining"]:
            report["warnings"].append(
                f"{thick_info['thin_regions_remaining']} region(s) still below "
                f"{cfg.min_thickness_mm} mm after thickening — inspect the preview")
        if thick_info["added_area_pct"] > 12.0:
            report["warnings"].append(
                f"thickening added {thick_info['added_area_pct']}% area — the design may be "
                f"too fine for {cfg.min_thickness_mm} mm minimum; consider scaling up")
        if gap_info["narrow_gap_count"] and not gap_info["filled"]:
            report["warnings"].append(
                f"{gap_info['narrow_gap_count']} slot(s) narrower than "
                f"{gap_info['gap_threshold_mm']} mm will close up at this kerf")
        if not vec_stats.get("topology_ok", True):
            t = vec_stats.get("topology", {})
            detail = []
            if t.get("components_split"):
                detail.append(f"a part split into {t.get('components_exported')} pieces")
            if t.get("self_intersections"):
                detail.append(f"{t['self_intersections']} self-intersection(s)")
            if t.get("iou", 1.0) < 0.98:
                detail.append(f"outline deviates from the mask (IoU {t.get('iou')})")
            report["warnings"].append(
                "exported geometry failed verification: " + "; ".join(detail) +
                " — lower --simplify-mm or --bezier-tension")
        if float_info.get("bridge_cap_hit"):
            report["warnings"].append(
                f"more islands than --max-bridges ({cfg.max_bridges}); some remain unbridged")

        # --- write ----------------------------------------------------------
        if not cfg.dry_run:
            out_dir.mkdir(parents=True, exist_ok=True)
            stem = src.stem
            if cfg.write_svg:
                p = out_dir / f"{stem}.svg"
                write_svg(p, subpaths, w_mm, h_mm, cfg.mm_per_px, src.name)
                report["outputs"].append(str(p))
            if cfg.write_dxf:
                p = out_dir / f"{stem}.dxf"
                write_dxf(p, subpaths, w_mm, h_mm, cfg.mm_per_px,
                          cfg.px(cfg.bezier_flatten_mm))
                report["outputs"].append(str(p))
            if cfg.write_png:
                p = out_dir / f"{stem}.png"
                png_w, png_h = write_png(p, subpaths, w_mm, h_mm, cfg.mm_per_px,
                                         cfg.px(cfg.bezier_flatten_mm), cfg.png_dpi,
                                         cfg.canvas_px)
                report["outputs"].append(str(p))
                report["png_px"] = [png_w, png_h]
            if cfg.preview:
                p = out_dir / f"{stem}_preview.png"
                write_preview(p, base, mask, added, gaps, bridge_layer, islands_mask,
                              vec_deviation)
                report["outputs"].append(str(p))

        if report["warnings"]:
            report["status"] = "ok_with_warnings"

    except Exception as exc:  # noqa: BLE001 — batch tools must survive one bad file
        report["status"] = "failed"
        report["errors"].append(f"{type(exc).__name__}: {exc}")
        report["traceback"] = traceback.format_exc(limit=6)

    report["elapsed_s"] = round(time.perf_counter() - t0, 2)
    return report


def compute_islands_mask(mask: np.ndarray) -> np.ndarray:
    """Mask of every foreground component other than the largest."""
    if not mask.any():
        return np.zeros_like(mask)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if n <= 2:
        return np.zeros_like(mask)
    main = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    keep = np.ones(n, dtype=bool)
    keep[main] = False
    return apply_label_lut(labels, keep)


def _worker(args) -> Dict:
    """Top-level so it can be pickled by ProcessPoolExecutor."""
    src, out_dir, cfg = args
    # OpenCV defaults to one thread per core *per process*; with N workers that
    # oversubscribes the CPU badly and each file gets slower than it would have
    # been serially. One thread per worker, N workers, is the right split.
    cv2.setNumThreads(1)
    return process_file(Path(src), Path(out_dir), cfg)


def gather_inputs(root: Path, recursive: bool) -> List[Path]:
    if root.is_file():
        return [root] if root.suffix.lower() in SUPPORTED_EXT else []
    it = root.rglob("*") if recursive else root.glob("*")
    return sorted(p for p in it if p.is_file() and p.suffix.lower() in SUPPORTED_EXT)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_canvas_px(value: str) -> Tuple[int, int]:
    """Parse a 'WIDTHxHEIGHT' CLI argument, e.g. '4500x5100'."""
    m = re.match(r"^\s*(\d+)\s*[xX]\s*(\d+)\s*$", value)
    if not m:
        raise argparse.ArgumentTypeError(
            f"invalid canvas size {value!r}; expected WIDTHxHEIGHT, e.g. 4500x5100")
    w, h = int(m.group(1)), int(m.group(2))
    if w <= 0 or h <= 0:
        raise argparse.ArgumentTypeError(f"canvas size must be positive: {value!r}")
    return w, h


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cnc_prep",
        description="Optimise black-and-white artwork (PNG/SVG) for CNC plasma / laser cutting.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        epilog="Every threshold is in millimetres of finished part. "
               "Set the finished size with --width-mm first; everything else follows from it.",
    )
    io_g = p.add_argument_group("input / output")
    io_g.add_argument("-i", "--input", required=True, type=Path,
                      help="input folder (or a single file)")
    io_g.add_argument("-o", "--output", required=True, type=Path,
                      help="output folder for CNC-ready files")
    io_g.add_argument("-r", "--recursive", action="store_true", help="recurse into subfolders")
    io_g.add_argument("--no-svg", action="store_true", help="skip SVG output")
    io_g.add_argument("--no-dxf", action="store_true", help="skip DXF output")
    io_g.add_argument("--no-png", action="store_true",
                      help="skip the cut-ready black-on-white PNG output")
    io_g.add_argument("--canvas-px", type=parse_canvas_px, default=None,
                      metavar="WxH",
                      help="pad/centre the exported PNG onto an exact pixel canvas "
                           "(e.g. 4500x5100), as required by some print/cut vendors")
    io_g.add_argument("--png-dpi", type=float, default=300.0,
                      help="resolution (DPI) baked into the exported PNG")
    io_g.add_argument("--preview", action="store_true",
                      help="write a QC overlay PNG per design")
    io_g.add_argument("--report", type=Path, default=None,
                      help="path for the JSON report (default: <output>/cnc_prep_report.json)")
    io_g.add_argument("--dry-run", action="store_true",
                      help="analyse and report only; write nothing")
    io_g.add_argument("-j", "--jobs", type=int, default=1,
                      help="parallel worker processes")
    io_g.add_argument("-q", "--quiet", action="store_true", help="suppress per-file logging")
    io_g.add_argument("--strict", action="store_true",
                      help="exit non-zero if any design produces a warning")
    io_g.add_argument("--suggest-size", action="store_true",
                      help="search for and report the minimum finished size (mm & inch) "
                           "at which no forced thickening/gap-filling is needed")
    io_g.add_argument("--suggest-jobs", type=int, default=4,
                      help="parallel worker processes for the --suggest-size search")

    sc = p.add_argument_group("scale")
    sc.add_argument("--width-mm", type=float, default=None,
                    help="finished width in mm (drives every other threshold). "
                         "Defaults to 600mm unless --native-size or --height-mm is given.")
    sc.add_argument("--height-mm", type=float, default=None,
                    help="finished height in mm (alternative to --width-mm)")
    sc.add_argument("--native-size", action="store_true",
                    help="use each SVG's own declared width/height instead of a fixed "
                         "size (SVG inputs only; PNG/raster inputs have no intrinsic "
                         "physical size and will error)")
    sc.add_argument("--work-res", type=float, default=8.0,
                    help="internal working resolution in px/mm")
    sc.add_argument("--max-dim-px", type=int, default=12000,
                    help="upper bound on internal raster dimension")

    bn = p.add_argument_group("binarisation & denoise")
    bn.add_argument("--invert", choices=["auto", "yes", "no"], default="auto",
                    help="'yes' if the artwork is white-on-black")
    bn.add_argument("--threshold", type=int, default=None,
                    help="fixed 0-255 threshold instead of Otsu")
    bn.add_argument("--min-feature-area-mm2", type=float, default=4.0,
                    help="delete metal islands smaller than this")
    bn.add_argument("--min-hole-area-mm2", type=float, default=2.0,
                    help="fill holes smaller than this")
    bn.add_argument("--despeckle-mm", type=float, default=0.35,
                    help="median-filter radius for salt-and-pepper noise; "
                         "auto-capped below --min-thickness-mm so line art survives")
    bn.add_argument("--smooth-mm", type=float, default=0.25,
                    help="de-jag blur sigma; 0 disables")

    st = p.add_argument_group("structural rules")
    st.add_argument("--min-thickness-mm", type=float, default=2.0,
                    help="thinnest metal allowed to survive the cut")
    st.add_argument("--sharp-tip-deg", type=float, default=60.0,
                    help="sharpest tip angle allowed; below this, tips get rounded off")
    st.add_argument("--thicken-passes", type=int, default=3,
                    help="thickening convergence iterations")
    st.add_argument("--kerf-mm", type=float, default=1.2,
                    help="beam/torch kerf (plasma ~1.2, fiber laser ~0.15)")
    st.add_argument("--gap-factor", type=float, default=1.5,
                    help="slots below kerf*factor are flagged")
    st.add_argument("--fill-narrow-gaps", action="store_true",
                    help="weld shut slots that cannot survive the kerf")
    st.add_argument("--kerf-compensate", choices=["none", "outward", "inward"], default="none",
                    help="bake a kerf/2 offset into the geometry (usually leave to CAM)")

    fl = p.add_argument_group("floating elements")
    fl.add_argument("--floating", choices=["warn", "bridge", "remove", "keep"], default="warn",
                    help="what to do with disconnected islands")
    fl.add_argument("--bridge-width-mm", type=float, default=None,
                    help="tab width when bridging (default: --min-thickness-mm)")
    fl.add_argument("--max-bridges", type=int, default=24,
                    help="safety cap on tabs added per design")

    vc = p.add_argument_group("vectorisation")
    vc.add_argument("--simplify-mm", type=float, default=0.15,
                    help="Douglas-Peucker tolerance; larger = fewer nodes")
    vc.add_argument("--corner-deg", type=float, default=48.0,
                    help="turn angle above which a node stays a hard corner")
    vc.add_argument("--bezier-tension", type=float, default=0.85,
                    help="0 = polyline output, 1 = full Catmull-Rom smoothing")
    vc.add_argument("--flatten-mm", type=float, default=0.05,
                    help="curve flattening tolerance for DXF output")
    return p


def config_from_args(a: argparse.Namespace) -> Config:
    if a.native_size:
        width_mm, height_mm = None, None
    elif a.width_mm is None and a.height_mm is None:
        width_mm, height_mm = 600.0, None   # library default when nothing was requested
    else:
        width_mm, height_mm = a.width_mm, a.height_mm

    return Config(
        width_mm=width_mm,
        height_mm=height_mm,
        work_res=a.work_res,
        max_dim_px=a.max_dim_px,
        invert=a.invert,
        threshold=a.threshold,
        min_feature_area_mm2=a.min_feature_area_mm2,
        min_hole_area_mm2=a.min_hole_area_mm2,
        despeckle_mm=a.despeckle_mm,
        smooth_mm=a.smooth_mm,
        min_thickness_mm=a.min_thickness_mm,
        sharp_tip_deg=a.sharp_tip_deg,
        thicken_passes=a.thicken_passes,
        kerf_mm=a.kerf_mm,
        gap_factor=a.gap_factor,
        fill_narrow_gaps=a.fill_narrow_gaps,
        kerf_compensate=a.kerf_compensate,
        floating=a.floating,
        bridge_width_mm=a.bridge_width_mm,
        max_bridges=a.max_bridges,
        simplify_mm=a.simplify_mm,
        corner_deg=a.corner_deg,
        bezier_tension=a.bezier_tension,
        bezier_flatten_mm=a.flatten_mm,
        write_svg=not a.no_svg,
        write_dxf=not a.no_dxf,
        write_png=not a.no_png,
        png_dpi=a.png_dpi,
        canvas_px=a.canvas_px,
        preview=a.preview,
        dry_run=a.dry_run,
        strict=a.strict,
        suggest_size=a.suggest_size,
        suggest_jobs=a.suggest_jobs,
    )


def _fmt_feature(v) -> str:
    """None means 'nothing below the minimum was found', which is the good case."""
    return f"{v}mm" if v is not None else "clear"


def log_size_suggestion(name: str, sug: Dict) -> None:
    if sug.get("error"):
        print(f"       size: {sug['error']}")
        return
    w_mm, h_mm = sug["min_safe_width_mm"], sug["min_safe_height_mm"]
    w_in, h_in = sug["min_safe_width_in"], sug["min_safe_height_in"]
    print(f"       size: min safe {w_mm}x{h_mm}mm  ({w_in}\"x{h_in}\")")


def log_result(rep: Dict, quiet: bool) -> None:
    # A --suggest-size answer is the whole point of the run even under -q:
    # -q exists to silence per-file processing noise in a batch, not to hide
    # the one thing the caller explicitly asked to compute.
    if rep.get("size_suggestion"):
        log_size_suggestion(Path(rep["source"]).name, rep["size_suggestion"])
    if quiet:
        return
    name = Path(rep["source"]).name
    icon = {"ok": "OK  ", "ok_with_warnings": "WARN", "failed": "FAIL"}[rep["status"]]
    if rep["status"] == "failed":
        print(f"[{icon}] {name}: {rep['errors'][0]}")
        return
    v = rep.get("vector", {})
    t = rep.get("thickness", {})
    print(f"[{icon}] {name}  "
          f"{rep['size_mm'][0]}x{rep['size_mm'][1]}mm  "
          f"paths={v.get('subpaths', 0)} "
          f"nodes {v.get('nodes_raw', 0)}->{v.get('nodes_final', 0)} "
          f"(-{v.get('node_reduction_pct', 0)}%)  "
          f"min feature {_fmt_feature(t.get('min_feature_mm_before'))}"
          f"->{_fmt_feature(t.get('min_feature_mm_after'))}  "
          f"{rep['elapsed_s']}s")
    for w in rep["warnings"]:
        print(f"       ! {w}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = config_from_args(args)

    if not args.input.exists():
        print(f"error: input path does not exist: {args.input}", file=sys.stderr)
        return 2

    files = gather_inputs(args.input, args.recursive)
    if not files:
        print(f"error: no supported files found in {args.input} "
              f"({', '.join(sorted(SUPPORTED_EXT))})", file=sys.stderr)
        return 2

    if any(f.suffix.lower() in VECTOR_EXT for f in files) and not HAS_CAIROSVG:
        if not (find_svg_tool("rsvg-convert") or find_svg_tool("inkscape")):
            print("warning: SVG inputs found but no rasteriser available — "
                  "those files will fail.\n" + _svg_tool_help(), file=sys.stderr)
    if not (HAS_XIMGPROC or HAS_SKIMAGE) and not args.quiet:
        print("note: neither cv2.ximgproc nor scikit-image found — using the slow "
              "pure-python skeletoniser. `pip install opencv-contrib-python` for a "
              "large speedup.", file=sys.stderr)

    if not args.quiet:
        mode = "DRY RUN (no files written)" if cfg.dry_run else f"writing to {args.output}"
        print(f"cnc_prep: {len(files)} design(s), {mode}")
        print(f"          min thickness {cfg.min_thickness_mm}mm | kerf {cfg.kerf_mm}mm | "
              f"floating={cfg.floating} | simplify {cfg.simplify_mm}mm")

    results: List[Dict] = []
    jobs = max(1, args.jobs)
    if jobs == 1 or len(files) == 1:
        for f in files:
            rep = process_file(f, args.output, cfg)
            log_result(rep, args.quiet)
            results.append(rep)
    else:
        payload = [(str(f), str(args.output), cfg) for f in files]
        with ProcessPoolExecutor(max_workers=jobs) as pool:
            futures = {pool.submit(_worker, p): p[0] for p in payload}
            for fut in as_completed(futures):
                rep = fut.result()
                log_result(rep, args.quiet)
                results.append(rep)
        results.sort(key=lambda r: r["source"])

    ok = sum(1 for r in results if r["status"] == "ok")
    warned = sum(1 for r in results if r["status"] == "ok_with_warnings")
    failed = sum(1 for r in results if r["status"] == "failed")

    summary = {
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "config": {k: v for k, v in asdict(cfg).items() if k != "mm_per_px"},
        "totals": {"processed": len(results), "clean": ok,
                   "with_warnings": warned, "failed": failed},
        "designs": results,
    }

    report_path = args.report or (args.output / "cnc_prep_report.json")
    if not cfg.dry_run or args.report:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    if not args.quiet:
        print(f"\ndone: {ok} clean, {warned} with warnings, {failed} failed")
        if not cfg.dry_run or args.report:
            print(f"report: {report_path}")

    if failed:
        return 1
    if warned and cfg.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())