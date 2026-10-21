#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
listing_kit.py — one design in, one ready-to-upload Etsy listing kit out.

Takes a finished black-on-white design (the file you picked from Firefly) and
produces everything a listing needs except the creative copy:

    <out>/<slug>/
        listing.md            sizes, Etsy Size values, price table, SKU, the fixed
                              description blocks, variations, and a ready prompt for
                              the title / tags / opening paragraph
        kit.json              the same data, machine readable
        cut/<size>/           cut-ready SVG + DXF + PNG per size (via mycode.py)
        mockups/              studio mockups in the chosen colours + colour swatch panel
        size_guide.jpg        every size side by side next to a 175 cm person
        size_collage.jpg      2x2 collage, same scale in every panel

USAGE
-----
    python listing_kit.py -i design.png -o kits --name "Ocean Wave Bundle"
    python listing_kit.py -i design.png -o kits --name "Heron Panel" --type privacy --theme privacy
    python listing_kit.py -i design.png -o kits --name "X" --min-cm 45 --no-cut    # fast: skip size search and cutting

The size rules and the price formula are the ones in the listing prompt (v6),
so a kit and a hand-made listing quote the same numbers. The minimum size is
the "design preserved" size from mycode.py's --suggest-size search, never a
guess, unless --min-cm overrides it.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import re
import sys
from dataclasses import asdict, dataclass, replace as dataclass_replace
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

import mycode as cnc


# ---------------------------------------------------------------------------
# Shop constants (from the listing prompt v6)
# ---------------------------------------------------------------------------

MAX_LONG_CM = {"wall": 150, "privacy": 240}
SIZE_NAMES_6 = ["X-Small", "Small", "Medium", "Large", "X-Large", "XX-Large"]
SQUARE_RATIO = 0.97
SIZE_VALUE_MAX = 20     # Etsy variation value limit

COLORS = [
    "Matte Black", "Texture Black", "Shiny Black", "Anthracite Gray", "Gray", "Silver",
    "Silver Hammer", "Chrome", "Gold", "Copper", "Copper Hammer", "Chocolate Brown",
    "Terracotta Brown", "Beige", "White", "Wine Red", "Cherry Red", "Orange",
    "Daffodil Yellow", "Racing Yellow", "Blue Lilac", "Purple", "Pink", "Light Pink",
    "Raw", "Navy Blue", "Sky Blue", "Turquoise", "Moss Green", "May Green",
]

# Theme -> (Alt1 premium, Alt2 theme colour, Alt3 light/neutral, Alt4 bold), per v6 section 4.
THEMES: Dict[str, Tuple[str, str, str, str]] = {
    "wildlife": ("Copper Hammer", "Chocolate Brown", "Anthracite Gray", "Moss Green"),
    "botanical": ("Copper", "Moss Green", "White", "Terracotta Brown"),
    "coastal": ("Gold", "Turquoise", "White", "Navy Blue"),
    "privacy": ("Anthracite Gray", "Moss Green", "Copper Hammer", "Terracotta Brown"),
    "deco": ("Gold", "Navy Blue", "Copper", "Wine Red"),
    "modern": ("Gold", "White", "Anthracite Gray", "Silver"),
    "gothic": ("Gold", "Wine Red", "Silver", "Purple"),
    "kids": ("Gold", "Sky Blue", "White", "Light Pink"),
    "mancave": ("Silver Hammer", "Cherry Red", "Racing Yellow", "Anthracite Gray"),
    "kitchen": ("Copper", "Chocolate Brown", "Terracotta Brown", "Gold"),
    "family": ("Gold", "White", "Anthracite Gray", "Copper Hammer"),
    "western": ("Copper Hammer", "Terracotta Brown", "Turquoise", "Chocolate Brown"),
}

# Approximate on-screen colour of each finish, for mockups only.
COLOR_RGB: Dict[str, Tuple[int, int, int]] = {
    "Matte Black": (24, 24, 26), "Texture Black": (30, 30, 32), "Shiny Black": (18, 18, 20),
    "Anthracite Gray": (62, 66, 70), "Gray": (128, 130, 132), "Silver": (188, 190, 194),
    "Silver Hammer": (170, 172, 176), "Chrome": (205, 208, 212), "Gold": (196, 158, 88),
    "Copper": (178, 110, 70), "Copper Hammer": (150, 92, 60), "Chocolate Brown": (78, 52, 40),
    "Terracotta Brown": (160, 84, 58), "Beige": (214, 198, 170), "White": (238, 236, 230),
    "Wine Red": (110, 30, 40), "Cherry Red": (170, 28, 36), "Orange": (220, 110, 40),
    "Daffodil Yellow": (232, 190, 50), "Racing Yellow": (240, 200, 0), "Blue Lilac": (130, 130, 190),
    "Purple": (90, 50, 120), "Pink": (220, 120, 150), "Light Pink": (236, 190, 200),
    "Raw": (92, 98, 104), "Navy Blue": (30, 42, 70), "Sky Blue": (120, 170, 210),
    "Turquoise": (40, 150, 150), "Moss Green": (17, 66, 50), "May Green": (74, 119, 41),
}
METALLIC = {"Gold", "Copper", "Copper Hammer", "Silver", "Silver Hammer", "Chrome"}

# Wall behind each colour, from the v6 contrast rules: dark finishes on light walls,
# metallics and light finishes on dark walls.
WALLS = {
    "light": [(236, 229, 218), (229, 222, 210), (240, 236, 229)],
    "dark": [(28, 52, 44), (31, 38, 58), (46, 46, 48), (74, 62, 56)],
}

PRODUCT_DETAILS = """PRODUCT DETAILS
• Material: 2 mm (0.08") cold-rolled steel, precision laser-cut
• Finish: matte electrostatic powder coat — shown in Matte Black, also available in {alts} and 30 colors in total
• Indoor and covered outdoor use (patios, porches, lanais)
• Available in several sizes — choose yours from the Size menu
• Made to order"""

MOUNTING = """MOUNTING OPTIONS
Sawtooth Hanger: Equipped with a discreet rear sawtooth hanger and spacer supports, allowing the artwork to sit approximately 1.5 cm (0.6") away from the wall for a refined floating appearance and added visual depth.
Screw Hole: Designed with precisely positioned mounting holes for a clean, secure, and stable screw-mounted installation.
No Holes: Supplied without mounting holes or hanger hardware, giving you the freedom to choose the installation method that best suits your wall and space.
Please note: Screws, nails, wall anchors, dowels, and other mounting hardware are not included."""

SHIPPING = """SHIPPING
• Free express shipping via FedEx / UPS / DHL, usually 3–5 business days after production
• US duties and import taxes prepaid — no extra fees at delivery
• Packed in a reinforced protective box"""

CLOSING = ("Room images are styled visualizations to show placement; please check the size menu "
           "and measure your wall before ordering.")

CUSTOMS = "Customs information / US estimated tariffs → Tariff (HTS) code 8306.30.0000, Country of origin Türkiye"

# The repo's EazyDemand preset (cnc_prep_gui.py), plus the repair choices used for every listing.
EAZYDEMAND = dict(min_thickness_mm=1.8, kerf_mm=1.8, gap_factor=1.0, fill_narrow_gaps=True,
                  sharp_tip_deg=12.0, thicken_mode="taper", tip_policy="trim", floating="remove",
                  min_hole_area_mm2=15.0, canvas_px=(4500, 5100), png_dpi=300.0)


# ---------------------------------------------------------------------------
# Sizes and prices
# ---------------------------------------------------------------------------

@dataclass
class Size:
    name: str
    long_cm: int
    short_cm: int
    width_cm: int
    height_cm: int
    width_in: int
    height_in: int
    value: str          # Etsy Size variation value, <= 20 chars
    cost: float = 0.0
    list_price: float = 0.0
    shown_price: float = 0.0
    profit: float = 0.0
    notes: str = ""


def round_to(x: float, step: int) -> int:
    return int(step * round(x / step))


def size_ladder(min_long_cm: float, ratio: float, horizontal: bool,
                product: str = "wall") -> List[Size]:
    """
    The v6 size rules: biggest size 150 cm (wall) / 240 cm (privacy) on the long
    side, six sizes (five when the range is under 60 cm), long sides growing by an
    equal ratio, the in-between sizes rounded to a multiple of 5.

    The smallest size is the measured minimum rounded *up*, never down: rounding
    it to the nearest 5 like the others could quote a size the design cannot be
    cut at without being altered.
    """
    top = MAX_LONG_CM[product]
    low = int(math.ceil(min_long_cm))
    if low >= top:
        longs = [low]
    else:
        n = 6 if top - low >= 60 else 5
        step = (top / low) ** (1.0 / (n - 1))
        longs = [low]
        for i in range(1, n - 1):
            v = max(round_to(low * step ** i, 5), longs[-1] + 5)
            longs.append(v)
        longs.append(top)
        # Rounding can push the last in-between size into the top one.
        while len(longs) > 2 and longs[-2] >= top:
            longs.pop(-2)
    names = SIZE_NAMES_6[-len(longs):] if len(longs) <= 6 else SIZE_NAMES_6

    square = ratio >= SQUARE_RATIO
    sizes: List[Size] = []
    used_in = set()
    for name, L in zip(names, longs):
        S = L if square else max(1, int(round(L * ratio)))
        w, h = (L, S) if horizontal else (S, L)
        wi, hi = int(round(w / 2.54)), int(round(h / 2.54))
        while (wi, hi) in used_in:          # two sizes landing on the same inches
            wi, hi = (wi + 1, hi) if horizontal else (wi, hi + 1)
        used_in.add((wi, hi))
        sizes.append(Size(name, L, S, w, h, wi, hi, size_value(wi, hi, w, h)))
    return sizes


def size_value(wi: int, hi: int, wc: int, hc: int) -> str:
    """Etsy Size value like 12" × 7" (30×18cm), squeezed until it fits 20 chars."""
    for fmt in ('{wi}" × {hi}" ({wc}×{hc}cm)', '{wi}"×{hi}" ({wc}×{hc}cm)', '{wi}"×{hi}"({wc}×{hc}cm)'):
        v = fmt.format(wi=wi, hi=hi, wc=wc, hc=hc)
        if len(v) <= SIZE_VALUE_MAX:
            return v
    return v


def roundup(x: float, digits: int = 2) -> float:
    """Spreadsheet ROUNDUP. The epsilon keeps 2.30 from turning into 2.31."""
    f = 10 ** digits
    return math.ceil(x * f - 1e-9) / f


def price(long_cm: float, short_cm: float) -> Tuple[float, float, float, float, str]:
    """
    v6 price formula. Returns (cost, list price, shown price, profit, notes).

    Shown is half of list because the shop runs a permanent 50% sale; profit is
    what is left of the shown price after Etsy's ~20% cut, minus the cost.
    """
    L, S = float(long_cm), float(short_cm)
    G, area = L + 2 * S, L * S
    base = (24.0839 + 0.0055798 * area + 0.2407801 * L
            + 28.3256 * (G > 290) + 42.4887 * (G > 365)
            + 39.6550 * (L > 150) + 62.3191 * (L > 180) + 79.3091 * (L > 210))
    cost = round(base * 1.32, 2)
    list_price = roundup(roundup(cost * 1.5 / 0.8) / 0.45)
    shown = round(list_price * 0.5, 2)
    profit = round(shown * 0.8 - cost, 2)

    notes = []
    if S < 60:
        notes.append("model doğrulanmadı")
    if L > 240 or S > 150:
        notes.append("bu bedeni önerme")
    if 285 <= G <= 293.4 or 360 <= G <= 366.6 or any(a <= L <= b for a, b in ((150, 165), (180, 195), (210, 225))):
        notes.append("eşik: üretici fiyatını doğrula")
    return cost, list_price, shown, profit, "; ".join(notes)


def price_sizes(sizes: List[Size]) -> None:
    for s in sizes:
        s.cost, s.list_price, s.shown_price, s.profit, s.notes = price(s.long_cm, s.short_cm)


# ---------------------------------------------------------------------------
# SKU ledger
# ---------------------------------------------------------------------------

def next_sku(ledger: Path, name: str, today: Optional[dt.date] = None) -> str:
    """
    YYYYMMDDNN, one per listing, from a ledger kept next to the kits.

    The ledger exists because the shop has already shipped three listings with
    the same SKU: picking "today + 01" by hand cannot see what was used earlier
    the same day.
    """
    today = today or dt.date.today()
    prefix = today.strftime("%Y%m%d")
    used = []
    if ledger.exists():
        with ledger.open(newline="", encoding="utf-8") as f:
            used = [r["sku"] for r in csv.DictReader(f)]
    taken = [int(s[8:]) for s in used if s.startswith(prefix) and s[8:].isdigit()]
    sku = f"{prefix}{(max(taken) + 1 if taken else 1):02d}"
    new = not ledger.exists()
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["sku", "date", "name"])
        if new:
            w.writeheader()
        w.writerow({"sku": sku, "date": today.isoformat(), "name": name})
    return sku


# ---------------------------------------------------------------------------
# Design measurement and cutting
# ---------------------------------------------------------------------------

def cut_config(width_mm: float, overrides: Dict) -> cnc.Config:
    cfg = cnc.Config(width_mm=width_mm, **{**EAZYDEMAND, **overrides})
    return cfg


def measure_ink(src: Path, cfg: cnc.Config) -> Tuple[float, float, float]:
    """
    (ink width fraction, ink height fraction, ink height/width).

    Sizes are quoted for the metal that gets cut, not for the canvas: AI exports
    carry white margin, and the customer measures the part on the wall.
    """
    gray, w_mm, _ = cnc.load_source(src, cfg)
    probe = dataclass_replace(cfg)
    probe.mm_per_px = w_mm / gray.shape[1]
    mask = cnc.binarize(gray, probe)
    ys, xs = np.nonzero(mask)
    if not xs.size:
        raise ValueError("the design is empty after binarisation")
    fw = float(xs.max() - xs.min() + 1) / gray.shape[1]
    fh = float(ys.max() - ys.min() + 1) / gray.shape[0]
    return fw, fh, (fh * gray.shape[0]) / (fw * gray.shape[1])


def design_mask(src: Path, cfg: cnc.Config, max_px: int = 2400) -> Image.Image:
    """The design cropped to its ink, as an 'L' image: 255 = metal."""
    gray, w_mm, _ = cnc.load_source(src, cfg)
    probe = dataclass_replace(cfg)
    probe.mm_per_px = w_mm / gray.shape[1]
    mask = cnc.binarize(gray, probe)
    im = Image.fromarray(mask)
    im = im.crop(im.getbbox())
    scale = min(1.0, max_px / max(im.size))
    if scale < 1.0:
        im = im.resize((int(im.width * scale), int(im.height * scale)), Image.LANCZOS)
    return im


def min_long_side_cm(src: Path, cfg: cnc.Config, ink_ar: float, jobs: int) -> Tuple[float, Dict]:
    """Smallest long side (cm) at which the design survives unaltered, from mycode's size search."""
    info = cnc.estimate_min_safe_size(src, cfg, jobs=jobs)
    w = info.get("min_preserved_artwork_width_mm") or info.get("min_safe_artwork_width_mm")
    if not w:
        raise RuntimeError("size search found no safe size: " + str(info.get("error", info)))
    h = w * ink_ar
    return max(w, h) / 10.0, info


def cut_sizes(src: Path, out: Path, sizes: List[Size], fw: float, overrides: Dict, sku: str) -> List[Dict]:
    """
    Run mycode.py once per size; canvas width is chosen so the *ink* lands on the quoted width.

    Files are renamed SKU_size_WxHcm: every size of a design otherwise leaves as
    the same "design.dxf", and the one sent to the cutter is picked by folder.
    """
    reports = []
    for s in sizes:
        canvas_mm = s.width_cm * 10.0 / fw
        cfg = cut_config(canvas_mm, overrides)
        tag = f"{s.name}_{s.width_cm}x{s.height_cm}cm"
        d = out / tag
        rep = cnc.process_file(src, d, cfg)
        outputs = []
        for p in map(Path, rep["outputs"]):
            target = p.with_name(f"{sku}_{tag}{p.name[len(src.stem):]}")
            p.replace(target)
            outputs.append(str(target))
        reports.append({"size": s.name, "status": rep["status"], "warnings": rep["warnings"],
                        "errors": rep["errors"], "outputs": outputs})
    return reports


# ---------------------------------------------------------------------------
# Mockups
# ---------------------------------------------------------------------------

def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    for name in (("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"),
                 ("Arial Bold.ttf" if bold else "Arial.ttf"), ("arialbd.ttf" if bold else "arial.ttf")):
        for root in ("/usr/share/fonts/truetype/dejavu", "/Library/Fonts", "/System/Library/Fonts/Supplemental",
                     "C:/Windows/Fonts", ""):
            try:
                return ImageFont.truetype(str(Path(root) / name) if root else name, size)
            except OSError:
                continue
    return ImageFont.load_default()


def finish(alpha: Image.Image, color: str) -> Image.Image:
    """
    The design as a painted part: RGBA in the finish colour.

    Metallics get a soft diagonal sheen so they read as satin powder coat
    rather than flat paint; everything else stays flat, as matte coat looks.
    """
    w, h = alpha.size
    rgb = np.array(COLOR_RGB.get(color, (24, 24, 26)), np.float32)
    if color in METALLIC:
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
        t = ((xx / max(w, 1)) * 0.6 + (yy / max(h, 1)) * 0.4)
        sheen = 0.82 + 0.36 * np.sin(t * math.pi * 1.4 + 0.6) ** 2
        img = np.clip(rgb[None, None, :] * sheen[..., None], 0, 255)
    else:
        img = np.broadcast_to(rgb, (h, w, 3)).astype(np.float32)
    if "Hammer" in color or color == "Texture Black":
        rng = np.random.default_rng(7)
        img = np.clip(img * (1 + rng.normal(0, 0.05, (h, w, 1))), 0, 255)
    out = Image.fromarray(img.astype(np.uint8), "RGB").convert("RGBA")
    out.putalpha(alpha)
    return out


def _shift_blur(a: np.ndarray, dx: float, dy: float, sigma: float) -> np.ndarray:
    """Sub-pixel shift then Gaussian blur, for shadows and the steel's side face."""
    m = np.float32([[1, 0, dx], [0, 1, dy]])
    out = cv2.warpAffine(a, m, (a.shape[1], a.shape[0]), flags=cv2.INTER_LINEAR, borderValue=0)
    if sigma > 0.3:
        out = cv2.GaussianBlur(out, (0, 0), sigma)
    return out


def place_part(img: Image.Image, alpha: Image.Image, color: str, x: int, y: int,
               px_per_mm: float, light: str = "left") -> Image.Image:
    """
    Composite the cut part onto a wall so it reads as a 2 mm steel plate
    standing 15 mm off the wall, not as a sticker.

    What a photo of the real part shows, and how each is reproduced in 2D:
      * shadow: two layers, a tight dark contact shadow close to the edge and a
        wide soft one further out, both cast away from the light and offset by
        the real 15 mm stand-off at this scale; multiplied into the wall so the
        wall's own texture shows through.
      * thickness: the plate's side face, a darker copy shifted 2 mm away from
        the light, visible only where it peeks out from under the front face.
      * edges: a bevel from the blurred silhouette's gradient; edges facing the
        light catch a highlight, the others fall off. Stronger on metallics.
      * light: a broad falloff across the face from the lit side, the room's
        own brightness under the part, and a slight tint toward the room's
        colour so the finish sits in the same light as the wall.
      * coat: fine grain on every finish, coarse mottling on hammertones.
    """
    W, H = img.size
    a = np.asarray(alpha, np.float32) / 255.0
    h, w = a.shape
    standoff = max(3.0, 15.0 * px_per_mm)
    thick = max(1.0, 2.0 * px_per_mm)
    sdir = 1.0 if light == "left" else -1.0          # shadow falls away from the light
    lx, ly = (-0.75, -0.65) if light == "left" else (0.75, -0.65)   # light comes from upper-left/right

    m = int(standoff * 3) + 6
    x0, y0, x1, y1 = max(0, x - m), max(0, y - m), min(W, x + w + m), min(H, y + h + m)
    A = np.zeros((y1 - y0, x1 - x0), np.float32)
    ax0, ay0 = x - x0, y - y0
    sx0, sy0 = max(0, -ax0), max(0, -ay0)
    A[max(0, ay0):ay0 + h, max(0, ax0):ax0 + w] = a[sy0:sy0 + (y1 - y0) - max(0, ay0), sx0:sx0 + (x1 - x0) - max(0, ax0)]
    base = np.asarray(img, np.float32)
    region = base[y0:y1, x0:x1].copy()
    wall_l = float(region.mean())

    # shadows, multiplied into the wall
    contact = _shift_blur(A, sdir * standoff * 0.25, standoff * 0.35, standoff * 0.22)
    ambient = _shift_blur(A, sdir * standoff * 0.8, standoff * 1.1, standoff * 0.9)
    k = 0.62 if wall_l > 110 else 0.5
    occ = np.clip(0.6 * contact + 0.35 * ambient, 0, 1) * k
    region *= (1.0 - occ)[..., None]

    # finish colour, lit
    rgb = np.array(COLOR_RGB.get(color, (24, 24, 26)), np.float32)
    yy, xx = np.mgrid[0:A.shape[0], 0:A.shape[1]].astype(np.float32)
    u = (xx - ax0) / max(w, 1)
    v = (yy - ay0) / max(h, 1)
    along = (u if light == "left" else 1 - u) * 0.65 + v * 0.35         # 0 at the lit corner
    metal = color in METALLIC
    sheen = 1.06 - (0.2 if metal else 0.06) * along
    if metal:
        sheen *= 0.9 + 0.22 * np.sin(along * math.pi * 1.6 + 0.4) ** 2
    soft = cv2.GaussianBlur(A, (0, 0), max(0.8, 0.7 * px_per_mm))
    gx = cv2.Sobel(soft, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(soft, cv2.CV_32F, 0, 1, ksize=3)
    facing = -(gx * lx + gy * ly)
    facing /= max(float(np.abs(facing).max()), 1e-6)
    bevel = 1.0 + (0.45 if metal else 0.22) * facing
    rng = np.random.default_rng(11)
    grain = 1.0 + rng.normal(0, 0.06 if ("Hammer" in color or color == "Texture Black") else 0.015, A.shape)
    wall_mean = region.reshape(-1, 3).mean(0) + 1.0
    tint = (wall_mean / wall_mean.mean()) ** 0.18
    lum = cv2.GaussianBlur(region.mean(axis=2), (0, 0), max(8.0, w / 12.0))
    gain = np.clip((lum / max(float(lum.mean()), 1.0)) ** 0.5, 0.8, 1.2)
    face = rgb[None, None, :] * (sheen * bevel * grain * gain)[..., None] * tint[None, None, :]
    side = rgb[None, None, :] * 0.42 * gain[..., None] * tint[None, None, :]

    # side face of the 2 mm plate, then the front face with a photographed (slightly soft) edge
    side_a = np.clip(_shift_blur(A, sdir * thick, thick * 0.8, 0.4) - A, 0, 1)[..., None]
    region = region * (1 - side_a) + side * side_a
    face_a = cv2.GaussianBlur(A, (0, 0), 0.5)[..., None]
    region = region * (1 - face_a) + face * face_a

    base[y0:y1, x0:x1] = np.clip(region, 0, 255)
    return Image.fromarray(base.astype(np.uint8))


def wall_for(color: str, k: int) -> Tuple[int, int, int]:
    dark = color in METALLIC or color in {"White", "Beige", "Light Pink"}
    pool = WALLS["dark" if dark else "light"]
    return pool[k % len(pool)]


def studio_scene(art: Image.Image, color: str, k: int, size_cm: Tuple[int, int],
                 canvas: int = 2000) -> Image.Image:
    """
    A square, front-on studio wall with a low bench for scale.

    Deliberately plain: this is a placement and scale shot that matches the cut
    file exactly, not a styled room. The art fills ~60% of the frame and the
    bench is drawn at real centimetres against it (about 1.35x the art's width,
    within what sideboards actually come in), so the scale reads true.
    """
    wall = wall_for(color, k)
    rng = np.random.default_rng(k)
    a = np.empty((canvas, canvas, 3), np.float32)
    a[:] = wall
    yy = np.linspace(1.05, 0.88, canvas)[:, None, None]       # soft top light
    xx = 1 - 0.06 * np.abs(np.linspace(-1, 1, canvas))[None, :, None]
    a = np.clip(a * yy * xx * (1 + rng.normal(0, 0.01, (canvas, canvas, 1))), 0, 255)
    img = Image.fromarray(a.astype(np.uint8))
    d = ImageDraw.Draw(img)

    art_w_cm, art_h_cm = size_cm
    px_per_cm = min(canvas * 0.6 / art_w_cm, canvas * 0.5 / art_h_cm)
    bench_cm = min(220, max(60, art_w_cm * 1.35))
    bw = int(bench_cm * px_per_cm)
    slab, plinth = max(8, int(7 * px_per_cm)), int(min(38, bench_cm * 0.2) * px_per_cm)
    cx, floor = canvas // 2, int(canvas * 0.94)
    top = floor - plinth - slab
    d.rectangle((0, floor, canvas, canvas), fill=tuple(int(c * 0.86) for c in wall))
    # low bench: a thin top slab on a recessed plinth, with a soft shadow on the wall
    sh = Image.new("L", img.size, 0)
    ImageDraw.Draw(sh).rectangle((cx - bw // 2 + 8, top + 10, cx + bw // 2 + 8, floor), fill=110)
    img.paste(Image.new("RGB", img.size, tuple(int(c * 0.7) for c in wall)), (0, 0), sh.filter(ImageFilter.GaussianBlur(18)))
    d = ImageDraw.Draw(img)
    stone = (206, 192, 172) if sum(wall) < 400 else (176, 160, 140)
    inset = int(bw * 0.08)
    d.rectangle((cx - bw // 2 + inset, top + slab, cx + bw // 2 - inset, floor), fill=tuple(int(c * 0.86) for c in stone))
    d.rounded_rectangle((cx - bw // 2, top, cx + bw // 2, top + slab), radius=4, fill=stone)
    d.line((cx - bw // 2, top + 1, cx + bw // 2, top + 1), fill=tuple(min(255, c + 22) for c in stone), width=3)

    # art: real size, its bottom ~25 cm above the bench
    w = int(art_w_cm * px_per_cm)
    h = int(w * art.height / art.width)
    alpha = art.resize((w, h), Image.LANCZOS)
    x, y = cx - w // 2, max(int(canvas * 0.05), top - int(25 * px_per_cm) - h)
    return place_part(img, alpha, color, x, y, px_per_cm / 10.0, "left")


ROOM_EXT = {".jpg", ".jpeg", ".png", ".webp"}
# Where the empty wall sits in a room generated with rooms_prompts (fractions of the image).
DEFAULT_BOX = (0.2, 0.12, 0.6, 0.42)
LIGHT_FINISHES = {"White", "Beige", "Light Pink"}


def load_rooms(rooms_dir: Path) -> List[Dict]:
    """
    Rooms from rooms.json when present, otherwise every image in the folder
    with the default wall box. Dropping AI-made empty rooms into the folder is
    then all it takes; rooms.json is only needed to fine-tune a placement.
    """
    cfg = rooms_dir / "rooms.json"
    if cfg.exists():
        return json.loads(cfg.read_text(encoding="utf-8"))
    return [{"file": p.name} for p in sorted(rooms_dir.iterdir()) if p.suffix.lower() in ROOM_EXT]


def wall_luminance(img: Image.Image, box: Tuple[int, int, int, int]) -> Tuple[float, float, float]:
    """Mean luminance of the wall box, and of its left and right halves (to find the light)."""
    x, y, w, h = box
    g = np.asarray(img.convert("L").crop((x, y, x + w, y + h)), np.float32)
    return float(g.mean()), float(g[:, : w // 2].mean()), float(g[:, w // 2:].mean())


def pick_room_color(colors: Sequence[str], wall_l: float, k: int) -> str:
    """
    v6 contrast rule applied to the actual wall: dark walls get metallic or light
    finishes, light walls get the dark ones. Cycles through the kit's colours so
    the rooms do not all show the same finish.
    """
    if wall_l < 110:
        pool = [c for c in colors if c in METALLIC or c in LIGHT_FINISHES] or ["Gold"]
    else:
        pool = [c for c in colors if c not in METALLIC and c not in LIGHT_FINISHES] or ["Matte Black"]
    return pool[k % len(pool)]


def room_scene(art: Image.Image, colors: Sequence[str], room: Dict, rooms_dir: Path,
               k: int = 0, width_cm: float = 120.0) -> Tuple[Image.Image, str]:
    """
    Composite the cut geometry onto an AI-made empty-room photo.

    The room supplies the photographic quality; the geometry stays the cut file,
    which a fully generated mockup cannot promise. Three things keep the paste
    from looking pasted: the part picks up the room's own light falloff across
    the wall, its edge is softened by a fraction of a pixel like a photographed
    edge, and the shadow falls away from the brighter side of the wall.

    rooms.json entries (all optional except file):
        {"file": "salon1.jpg", "box": [x, y, w, h], "light": "left", "color": "Gold"}
    """
    img = Image.open(rooms_dir / room["file"]).convert("RGB")
    W, H = img.size
    box = room.get("box") or [int(W * DEFAULT_BOX[0]), int(H * DEFAULT_BOX[1]),
                              int(W * DEFAULT_BOX[2]), int(H * DEFAULT_BOX[3])]
    bx, by, bw, bh = box
    wall_l, left_l, right_l = wall_luminance(img, box)
    color = room.get("color") or pick_room_color(colors, wall_l, k)
    light = room.get("light") or ("left" if left_l >= right_l else "right")

    s = min(bw / art.width, bh / art.height)
    w, h = int(art.width * s), int(art.height * s)
    alpha = art.resize((w, h), Image.LANCZOS)
    x, y = bx + (bw - w) // 2, by + (bh - h) // 2
    return place_part(img, alpha, color, x, y, w / (width_cm * 10.0), light), color


def swatch_panel(art: Image.Image, colors: Sequence[str], canvas: Tuple[int, int] = (3000, 2000)) -> Image.Image:
    """
    Image J of the listing: the design in five finishes, names underneath.

    Wide designs are stacked in rows instead of columns; squeezed into a fifth
    of the width a 3:1 piece would shrink to a squiggle.
    """
    W, H = canvas
    img = Image.new("RGB", (W, H), (214, 210, 202))
    d = ImageDraw.Draw(img)
    n = len(colors)
    rows = art.width / art.height > 1.6
    f = _font(56 if not rows else 48, True)
    for i, c in enumerate(colors):
        if rows:
            cell = (0, i * H // n, W, (i + 1) * H // n)
            box_w, box_h = int(W * 0.62), int((cell[3] - cell[1]) * 0.78)
        else:
            cell = (i * W // n, 0, (i + 1) * W // n, H)
            box_w, box_h = int((cell[2] - cell[0]) * 0.8), int(H * 0.62)
        s = min(box_w / art.width, box_h / art.height)
        w, h = int(art.width * s), int(art.height * s)
        a = art.resize((max(1, w), max(1, h)), Image.LANCZOS)
        if rows:
            x, y = cell[0] + int(W * 0.06), cell[1] + ((cell[3] - cell[1]) - h) // 2
            label = (cell[0] + int(W * 0.84), (cell[1] + cell[3]) // 2)
        else:
            x, y = cell[0] + ((cell[2] - cell[0]) - w) // 2, int(H * 0.12) + (box_h - h) // 2
            label = ((cell[0] + cell[2]) // 2, int(H * 0.86))
        sh = Image.new("L", img.size, 0)
        sh.paste(a, (x + 10, y + 14))
        sh = sh.filter(ImageFilter.GaussianBlur(10))
        img.paste(Image.new("RGB", img.size, (150, 146, 140)), (0, 0), sh.point(lambda v: int(v * 0.6)))
        p = finish(a, c)
        img.paste(p, (x, y), p)
        d.text(label, c, font=f, fill=(40, 40, 40), anchor="mm")
        if i:
            line = (0, cell[1], W, cell[1]) if rows else (cell[0], int(H * 0.06), cell[0], int(H * 0.94))
            d.line(line, fill=(240, 238, 232), width=6)
    return img


def _person(sil: ImageDraw.ImageDraw, hx: float, floor_y: float, px: float) -> None:
    """Flat 175 cm person silhouette, feet on floor_y."""
    top, hr = floor_y - 175 * px, 11 * px
    sil.ellipse((hx - hr, top, hx + hr, top + 2 * hr), fill=255)
    sh = top + 2 * hr + 3 * px
    sil.rounded_rectangle((hx - 22 * px, sh, hx + 22 * px, sh + 62 * px), radius=int(10 * px), fill=255)
    for a, b in ((-30, -21), (21, 30)):
        sil.rounded_rectangle((hx + a * px, sh + 2 * px, hx + b * px, sh + 60 * px), radius=int(4 * px), fill=255)
    for a, b in ((-19, -2), (2, 19)):
        sil.rounded_rectangle((hx + a * px, sh + 58 * px, hx + b * px, floor_y), radius=int(5 * px), fill=255)


def size_guide(art: Image.Image, sizes: List[Size]) -> Image.Image:
    """
    Every size side by side on one wall, to scale, next to a 175 cm person.

    The canvas height follows the content: the scale is set by the total width,
    and a fixed 3:2 frame left long horizontal pieces floating in empty wall.
    """
    W = 3000
    wall, floor, ink, grey = (238, 231, 221), (214, 203, 188), (28, 28, 28), (150, 142, 132)
    gap, person_w = 28, 60
    total = sum(s.width_cm for s in sizes) + gap * (len(sizes) - 1) + person_w + 90
    px = (W - 260) / total
    tallest = max(175.0, 145 + max(s.height_cm for s in sizes) / 2, max(s.height_cm for s in sizes) + 20)
    floor_y = int(300 + (tallest + 15) * px)
    H = floor_y + 220
    img = Image.new("RGB", (W, H), wall)
    d = ImageDraw.Draw(img)
    d.rectangle((0, floor_y, W, H), fill=floor)
    left = 130
    sil = Image.new("L", (W, H), 0)
    hx = left + person_w * px / 2
    _person(ImageDraw.Draw(sil), hx, floor_y, px)
    img.paste(Image.new("RGB", (W, H), (178, 170, 160)), (0, 0), sil)
    d.text((hx, floor_y + 40), "5'9\" / 175 cm", font=_font(46), fill=grey, anchor="mt")
    x = left + (person_w + 90) * px
    centre_y = floor_y - 145 * px
    for s in sizes:
        w, h = int(s.width_cm * px), int(s.height_cm * px)
        a = art.resize((max(1, w), max(1, h)), Image.LANCZOS)
        y = int(min(centre_y - h / 2, floor_y - h - 20 * px))
        sh = Image.new("L", (W, H), 0)
        sh.paste(a, (int(x) + 10, y + 14))
        sh = sh.filter(ImageFilter.GaussianBlur(10))
        img.paste(Image.new("RGB", (W, H), (205, 196, 184)), (0, 0), sh)
        img.paste(Image.new("RGB", (w, h), ink), (int(x), y), a)
        d.text((x + w / 2, floor_y + 40), f"{s.width_cm}×{s.height_cm} cm", font=_font(46, True), fill=ink, anchor="mt")
        d.text((x + w / 2, floor_y + 108), f'{s.width_in}" × {s.height_in}"', font=_font(42), fill=grey, anchor="mt")
        x += (s.width_cm + gap) * px
    d.text((W / 2, 80), "SIZE GUIDE", font=_font(78, True), fill=ink, anchor="mt")
    d.text((W / 2, 180), "shown to scale next to a 5'9\" (175 cm) person", font=_font(46), fill=grey, anchor="mt")
    return img


def size_collage(art: Image.Image, sizes: List[Size], centre_cm: float = 145) -> Image.Image:
    """2x2 collage of four sizes, same scale in every panel, design centred at eye level."""
    pick = sizes if len(sizes) <= 4 else [sizes[0], sizes[len(sizes) // 3], sizes[2 * len(sizes) // 3], sizes[-1]]
    S, P = 3000, 1500
    wall, floor, ink, grey, person = (238, 231, 221), (214, 203, 188), (28, 28, 28), (140, 132, 122), (178, 170, 160)
    img = Image.new("RGB", (S, S), wall)
    big = max(pick, key=lambda s: s.long_cm)
    top_cm = centre_cm + big.height_cm / 2
    px = min((P - 120) / (top_cm + 12), (P - 140) / (big.width_cm + 110))
    for k, s in enumerate(pick):
        ox, oy = (k % 2) * P, (k // 2) * P
        d = ImageDraw.Draw(img)
        fy = oy + P - 110
        d.rectangle((ox, fy, ox + P, oy + P), fill=floor)
        sil = Image.new("L", (S, S), 0)
        hx = ox + 90 + 30 * px
        _person(ImageDraw.Draw(sil), hx, fy, px)
        img.paste(Image.new("RGB", (S, S), person), (0, 0), sil)
        w, h = int(s.width_cm * px), int(s.height_cm * px)
        a = art.resize((max(1, w), max(1, h)), Image.LANCZOS)
        cx = ox + 90 + 120 * px + (P - 90 - 120 * px - 60) / 2
        x, y = int(cx - w / 2), int(fy - centre_cm * px - h / 2)
        sh = Image.new("L", (S, S), 0)
        sh.paste(a, (x + 8, y + 12))
        sh = sh.filter(ImageFilter.GaussianBlur(9))
        img.paste(Image.new("RGB", (S, S), (205, 196, 184)), (0, 0), sh)
        img.paste(Image.new("RGB", (w, h), ink), (x, y), a)
        d = ImageDraw.Draw(img)
        d.text((ox + P - 60, oy + 60), f"{s.width_cm}×{s.height_cm} cm", font=_font(80, True), fill=ink, anchor="rt")
        d.text((ox + P - 60, oy + 165), f'{s.width_in}" × {s.height_in}"', font=_font(62), fill=grey, anchor="rt")
        d.text((hx, fy + 30), "5'9\" person", font=_font(40), fill=grey, anchor="mt")
    d = ImageDraw.Draw(img)
    d.line((P, 0, P, S), fill=(255, 255, 255), width=14)
    d.line((0, P, S, P), fill=(255, 255, 255), width=14)
    return img


# ---------------------------------------------------------------------------
# listing.md
# ---------------------------------------------------------------------------

def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return s or "design"


def copy_prompt(name: str, product: str, sizes: List[Size], horizontal: bool, ratio: float) -> str:
    """The part a person (or an LLM) still writes: title, tags, the opening paragraph, attributes."""
    shape = "round/square" if ratio >= SQUARE_RATIO else ("horizontal" if horizontal else "vertical")
    return (
        f"IronForgeCraft listing for the attached design \"{name}\" ({'privacy panel' if product == 'privacy' else 'wall art'}, "
        f"{shape}, sizes {sizes[0].width_cm}×{sizes[0].height_cm} to {sizes[-1].width_cm}×{sizes[-1].height_cm} cm). "
        "Sizes, prices, SKU, mounting, shipping and product details are already done — write ONLY:\n"
        "1. TITLE: buyer-focused, 4–5 comma-separated parts, 110–135 characters (give the count). "
        "Part 1 = strongest search phrase + \"Metal Wall Art\" (\"Metal Privacy Screen\" for panels); parts 2–4 = style + room for the main buyer; "
        "a niche gift phrase only in the last part. No word more than twice; no Matte Black, Laser-Cut, Steel, Solid, emoji, parentheses, \"&\". "
        "\"Large\" only if the biggest size is over 100 cm.\n"
        "2. TAGS: 13 lowercase tags, each ≤ 20 characters, one comma-separated line. None of: metal wall art, large metal wall art, metal wall decor. "
        "3–4 motif tags, 3–4 buyer phrases (room/style), 2–3 gift tags tied to the motif. No outdoor/patio tags for indoor pieces; "
        "privacy tags only for real panels.\n"
        "3. OPENING: two short paragraphs. First sentence: \"[title part 1] is [style] for [room], and a [gift] for [recipient].\" "
        "Then the look and feel, then \"precision laser-cut from 2 mm steel and finished in a durable powder coat\" and 4–6 places it suits.\n"
        "4. ATTRIBUTES: Style (1), Orientation, Room (≤5), Subject (≤3) from Etsy's lists, one line."
    )


def write_listing_md(path: Path, kit: Dict, sizes: List[Size]) -> None:
    alts = kit["colors"][1:4]
    rows = ["| Beden | cm (G×Y) | Size değeri (karakter) | Maliyet | Liste | Görünen | Kâr | Not |",
            "|---|---|---|---|---|---|---|---|"]
    for s in sizes:
        rows.append(f"| {s.name} | {s.width_cm}×{s.height_cm} | `{s.value}` ({len(s.value)}) | ${s.cost:.2f} | "
                    f"${s.list_price:.2f} | ${s.shown_price:.2f} | ${s.profit:.2f} | {s.notes} |")
    cut_lines = []
    for r in kit.get("cut", []):
        flag = "✅" if r["status"] == "ok" else ("⚠️" if r["status"] == "ok_with_warnings" else "❌")
        warn = ("; ".join(r["warnings"] + r["errors"]))[:300]
        cut_lines.append(f"- {flag} {r['size']}: {r['status']}" + (f" — {warn}" if warn else ""))
    md = f"""# {kit['name']} — Listing Kiti

**SKU:** `{kit['sku']}` (listing başına tek SKU; "SKUs vary" KAPALI)
**Ürün:** {"Privacy panel" if kit['product'] == 'privacy' else "Duvar sanatı"} · **Oran (kesim sınırı):** {kit['ink_ratio']:.3f} · **Min uzun kenar:** {kit['min_long_cm']:.1f} cm ({kit['min_source']})
**Renkler:** ana {kit['colors'][0]}; alternatifler {", ".join(kit['colors'][1:])}

## Bedenler ve fiyat
{chr(10).join(rows)}

## Kesim dosyaları
{chr(10).join(cut_lines) if cut_lines else "- (atlandı: --no-cut)"}

## Varyasyonlar
- **Size:** {" · ".join(f"`{s.value}`" for s in sizes)} (fiyat bedene göre değişir)
- **Color:** kartelanın 30 rengi, birebir adlarıyla (renk fiyatı değiştirmez)
- **Mounting Option:** Sawtooth Hanger · Screw Hole · No Holes (fiyatı değiştirmez)

## Açıklama — sabit bloklar (giriş paragrafından sonra, aynen)
```
{PRODUCT_DETAILS.format(alts=", ".join(alts))}

{MOUNTING}

{SHIPPING}

{CLOSING}
```

**Gümrük:** {CUSTOMS}

## Görseller
- **Kitten:** `size_guide.jpg` ve `size_collage.jpg` (ölçek görselleri; AI ölçeği tutturamaz)
- **ChatGPT'den:** oda görselleri, renk paneli (J), yakın plan (F). Kesim dosyasının PNG'sini yükle,
  v6'daki görsel tariflerini kullan; renkler: {", ".join(kit['colors'])}.
- **Kontrol:** her AI görselinde siluet kesim dosyasıyla aynı mı? Bıyık, uç, ince kol gibi
  detaylar kopmuş ya da eklenmiş mi? Değişmişse o görseli yeniden üret.

## Title, tag ve giriş paragrafı için prompt
Bu tasarımın PNG'si ile birlikte ChatGPT'ye ya da Claude'a ver:
```
{kit['copy_prompt']}
```
"""
    path.write_text(md, encoding="utf-8")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def build_kit(src: Path, out_root: Path, name: str, product: str = "wall", theme: str = "modern",
              min_cm: Optional[float] = None, do_cut: bool = True, rooms: Optional[Path] = None,
              jobs: int = 4, sku: Optional[str] = None, overrides: Optional[Dict] = None,
              mockups: bool = False, log=print) -> Dict:
    overrides = overrides or {}
    kit_dir = out_root / slugify(name)
    kit_dir.mkdir(parents=True, exist_ok=True)

    ref = cut_config(600.0, overrides)
    fw, fh, ink_ar = measure_ink(src, ref)
    horizontal = ink_ar <= 1.0
    ratio = min(ink_ar, 1 / ink_ar)
    log(f"[kit] {name}: oran {ratio:.3f} ({'yatay' if horizontal else 'dikey'})")

    size_info: Dict = {}
    if min_cm:
        min_long, source = float(min_cm), "--min-cm"
    else:
        log("[kit] tasarım korunarak min ölçü aranıyor (birkaç dakika sürebilir)...")
        min_long, size_info = min_long_side_cm(src, ref, ink_ar, jobs)
        source = "Cut-Ready, tasarım korunarak" if size_info.get("min_preserved_width_mm") else "Cut-Ready, sadece kırılmadan"
    sizes = size_ladder(min_long, ratio, horizontal, product)
    price_sizes(sizes)
    log(f"[kit] bedenler: {', '.join(f'{s.width_cm}x{s.height_cm}' for s in sizes)}")

    colors = ["Matte Black", *THEMES.get(theme, THEMES["modern"])]
    kit: Dict = {
        "name": name, "slug": slugify(name), "source": str(src), "product": product, "theme": theme,
        "sku": sku or next_sku(out_root / "sku_ledger.csv", name),
        "ink_ratio": ratio, "horizontal": horizontal, "min_long_cm": min_long, "min_source": source,
        "colors": colors, "sizes": [asdict(s) for s in sizes], "size_search": size_info,
        "mockups": mockups,
    }

    if do_cut:
        log("[kit] kesim dosyaları üretiliyor...")
        kit["cut"] = cut_sizes(src, kit_dir / "cut", sizes, fw, overrides, kit["sku"])

    art = design_mask(src, ref)
    hero = sizes[len(sizes) // 2]
    # Room mockups are off by default: AI-generated rooms (ChatGPT, from the cut
    # file) look far better than a 2D composite can. The size guide stays on,
    # because scale is the one thing an image model gets wrong.
    if mockups or rooms:
        (kit_dir / "mockups").mkdir(exist_ok=True)
        for k, c in enumerate(colors):
            studio_scene(art, c, k, (hero.width_cm, hero.height_cm)).save(
                kit_dir / "mockups" / f"A_{k + 1}_{slugify(c)}.jpg", quality=92)
        if rooms and rooms.is_dir():
            for k, room in enumerate(load_rooms(rooms)):
                scene, c = room_scene(art, colors, room, rooms, k, hero.width_cm)
                scene.save(kit_dir / "mockups" / f"R_{k + 1}_{slugify(c)}.jpg", quality=92)
        swatch_panel(art, colors).save(kit_dir / "mockups" / "J_colors.jpg", quality=92)
    size_guide(art, sizes).save(kit_dir / "size_guide.jpg", quality=92)
    size_collage(art, sizes).save(kit_dir / "size_collage.jpg", quality=92)

    kit["copy_prompt"] = copy_prompt(name, product, sizes, horizontal, ratio)
    write_listing_md(kit_dir / "listing.md", kit, sizes)
    (kit_dir / "kit.json").write_text(json.dumps(kit, indent=2, ensure_ascii=False), encoding="utf-8")
    log(f"[kit] hazır: {kit_dir}")
    return kit


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="listing_kit",
                                description="Build an Etsy listing kit (sizes, prices, SKU, cut files, mockups) from one design.")
    p.add_argument("-i", "--input", required=True, type=Path, help="black-on-white design (PNG or SVG)")
    p.add_argument("-o", "--output", required=True, type=Path, help="folder that holds all kits and the SKU ledger")
    p.add_argument("--name", required=True, help="short English design name, e.g. \"Ocean Wave Bundle\"")
    p.add_argument("--type", dest="product", choices=["wall", "privacy"], default="wall")
    p.add_argument("--theme", choices=sorted(THEMES), default="modern", help="picks the four alternative colours (v6 section 4)")
    p.add_argument("--min-cm", type=float, default=None, help="known minimum long side in cm; skips the size search")
    p.add_argument("--no-cut", action="store_true", help="skip per-size cut files")
    p.add_argument("--mockups", action="store_true", help="also render 2D studio mockups and a colour panel (off: use AI rooms)")
    p.add_argument("--rooms", type=Path, default=None, help="folder with empty-room photos (implies --mockups)")
    p.add_argument("--sku", default=None, help="use this SKU instead of the next one from the ledger")
    p.add_argument("-j", "--jobs", type=int, default=4, help="parallel workers for the size search")
    p.add_argument("--min-thickness-mm", type=float, default=None)
    p.add_argument("--kerf-mm", type=float, default=None)
    p.add_argument("--simplify-mm", type=float, default=None, help="0.5 for dense thin-line designs")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    a = build_parser().parse_args(argv)
    if not a.input.exists():
        print(f"error: input not found: {a.input}", file=sys.stderr)
        return 2
    overrides = {k: v for k, v in (("min_thickness_mm", a.min_thickness_mm), ("kerf_mm", a.kerf_mm),
                                   ("simplify_mm", a.simplify_mm)) if v is not None}
    build_kit(a.input, a.output, a.name, a.product, a.theme, a.min_cm, not a.no_cut, a.rooms,
              a.jobs, a.sku, overrides, a.mockups)
    return 0


if __name__ == "__main__":
    sys.exit(main())
