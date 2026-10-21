#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
batch_generate.py — run a prompt pack through local FLUX (mflux) and keep only
the designs that can be cut.

Reads one of our prompt packs (markdown: "### N. Name" headings, each followed by
a fenced prompt block and an "Oran: ... (Firefly'da 16:9)" line), generates a few
variants per prompt with mflux on Apple Silicon, then opens every image with
mycode.py's own binarisation and sorts it:

    <out>/keep/      one connected piece          -> look at these
    <out>/reject/    floating pieces or empty     -> skip, or fix by hand
    <out>/report.csv prompt, seed, pieces, verdict

USAGE
-----
    python batch_generate.py -p 53_50_yeni_nisler_standart.md -o gen_out
    python batch_generate.py -p pack.md -o gen_out --only 1,3,9 --variants 4
    python batch_generate.py -p pack.md -o gen_out --dry-run        # print the mflux commands only

mflux is not a dependency of this repo: install it in its own environment and
point --mflux at its mflux-generate (see README, "Yerel AI ile toplu üretim").
"""

from __future__ import annotations

import argparse
import csv
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence

# FLUX works in multiples of 16; these are the nearest sizes to Firefly's ratios at ~1 MP.
RATIO_PX = {"16:9": (1344, 768), "1:1": (1024, 1024), "3:4": (896, 1152), "9:16": (768, 1344)}

# FLUX.1 schnell reads at most 256 text tokens; past that the prompt is silently cut.
# The pack's Technical block alone is ~90 tokens and comes last, so on a long design
# description the rules that keep the part in one piece are the first thing lost.
# Local runs swap it for this short version, which keeps the rules that matter.
SHORT_TECH = ("Pure solid black on pure white, flat 2D laser-cut steel cut file, one single connected piece, "
              "nothing floating, shapes merge where they cross, bold lines, no hairlines, no dots, "
              "black and white only, no color, no gray, no gradient, no mist, no ink wash, no text, no shadow, "
              "no mockup, no screws, no mounting holes, generous white margin.")


@dataclass
class Prompt:
    number: int
    name: str
    text: str
    ratio: str           # Firefly ratio: 16:9, 1:1, 3:4, 9:16


def slugify(s: str) -> str:
    s = s.lower()
    for a, b in (("ı", "i"), ("ğ", "g"), ("ü", "u"), ("ş", "s"), ("ö", "o"), ("ç", "c"), ("â", "a")):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-") or "design"


def parse_pack(md: str) -> List[Prompt]:
    """
    Every "### N. Name" section with a fenced block. The ratio comes from
    "(Firefly'da X)" when present, else "Oran: X", else 1:1.
    """
    prompts: List[Prompt] = []
    sections = re.split(r"^### ", md, flags=re.M)[1:]
    for sec in sections:
        head = re.match(r"(\d+)\.\s*(.+)", sec)
        block = re.search(r"```\n(.*?)\n```", sec, flags=re.S)
        if not head or not block:
            continue
        ff = re.search(r"Firefly'da\s*(\d+:\d+)", sec)
        plain = re.search(r"Oran:\s*(\d+:\d+)", sec)
        ratio = ff.group(1) if ff else (plain.group(1) if plain else "1:1")
        if ratio not in RATIO_PX:
            ratio = nearest_ratio(ratio)
        prompts.append(Prompt(int(head.group(1)), head.group(2).strip(), block.group(1).strip(), ratio))
    return prompts


def nearest_ratio(r: str) -> str:
    a, b = (float(x) for x in r.split(":"))
    want = a / b
    return min(RATIO_PX, key=lambda k: abs(RATIO_PX[k][0] / RATIO_PX[k][1] - want))


def localize(text: str) -> str:
    """Swap a standard-format Technical block for the short one; leave other prompts alone."""
    lines = [l for l in text.splitlines() if l.strip()]
    if any(l.startswith("Technical:") for l in lines):
        lines = [l for l in lines if not l.startswith("Technical:")] + [SHORT_TECH]
    return " ".join(lines)


def mflux_cmd(exe: str, p: Prompt, seed: int, out: Path, steps: int, quantize: int,
              model_path: Optional[str]) -> List[str]:
    w, h = RATIO_PX[p.ratio]
    cmd = [exe, "--prompt", localize(p.text), "--steps", str(steps), "--seed", str(seed),
           "--width", str(w), "--height", str(h), "--output", str(out)]
    if model_path:
        # Current mflux takes a saved model as --model <dir> plus its family;
        # the older --path flag is gone.
        cmd += ["--model", model_path, "--base-model", "schnell"]
    else:
        cmd += ["--model", "schnell", "--quantize", str(quantize)]
    return cmd


def _cut_mask(png: Path):
    """The design as Cut-Ready sees it at a 600 mm reference width: binarised, despeckled."""
    import mycode as cnc
    cfg = cnc.Config(width_mm=600.0)
    gray, w_mm, _ = cnc.load_source(png, cfg)
    gray = cnc.resample_to_work_res(gray, cfg, w_mm)
    cfg.mm_per_px = w_mm / gray.shape[1]
    mask = cnc.binarize(gray, cfg)
    if not mask.any() or float((mask > 0).mean()) > 0.98:
        return None
    mask, _ = cnc.denoise(mask, cfg)
    return mask


def check_cuttable(png: Path) -> tuple[int, str]:
    """
    (pieces, verdict) using mycode.py's own binarisation and speckle filter at a
    600 mm reference width, so "one piece" here means one piece to Cut-Ready.
    """
    import cv2
    mask = _cut_mask(png)
    if mask is None:
        return 0, "boş"
    n, _ = cv2.connectedComponents(mask, connectivity=8)
    pieces = n - 1
    return pieces, ("tek parça" if pieces == 1 else f"{pieces} parça")


def thinnest_pct(mask) -> float:
    """
    5th-percentile stroke width as a % of the ink width: the morning sort key.

    Measured on the medial axis of a downscaled copy; a design whose thin end
    sits under ~0.35% needs a large minimum size (0.35% of 45 cm is 1.6 mm).
    """
    import cv2
    import numpy as np
    import mycode as cnc
    ys, xs = np.nonzero(mask)
    ink_w = float(xs.max() - xs.min() + 1)
    scale = min(1.0, 1200.0 / mask.shape[1])
    m = cv2.resize(mask, (int(mask.shape[1] * scale), int(mask.shape[0] * scale)), interpolation=cv2.INTER_AREA)
    m = cnc.as_mask(m > 127)
    dt = cv2.distanceTransform(m, cv2.DIST_L2, 5)
    sk = cnc.skeletonize(m) > 0
    if not sk.any():
        return 0.0
    return round(float(np.percentile(2 * dt[sk], 5)) / (ink_w * scale) * 100, 3)


def save_clean(mask, dest: Path) -> None:
    """Pure black-on-white PNG of the mask, ready for Cut-Ready or the listing kit."""
    import cv2
    import numpy as np
    cv2.imwrite(str(dest), np.where(mask > 0, 0, 255).astype("uint8"))


def contact_sheet(items: List[tuple], dest: Path, cols: int = 4, cell: int = 600) -> None:
    """One image of every kept design with its label, for picking in the morning."""
    from PIL import Image, ImageDraw, ImageFont
    if not items:
        return
    rows = (len(items) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * cell, rows * (cell + 70)), "white")
    d = ImageDraw.Draw(sheet)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 24)
    except OSError:
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 24)
        except OSError:
            font = ImageFont.load_default()
    for k, (path, label) in enumerate(items):
        im = Image.open(path).convert("RGB")
        im.thumbnail((cell - 20, cell - 20))
        x, y = (k % cols) * cell, (k // cols) * (cell + 70)
        sheet.paste(im, (x + (cell - im.width) // 2, y + 10))
        d.text((x + 12, y + cell + 8), f"{k + 1}. {label}", fill=(30, 30, 30), font=font)
        d.rectangle((x, y, x + cell - 1, y + cell + 69), outline=(220, 220, 220))
    sheet.save(dest, quality=88)


# A reject whose floating bits together are under this share of the metal is
# saved as "onarıldı" with only the main body kept: losing a stray leaf or a dot
# is a trim, losing a moon is not, and the morning sheet shows which it was.
REPAIR_MAX_SHARE = 0.03


def drop_islands(mask):
    """(main body only, share of metal removed) — the largest component kept, the rest dropped."""
    import cv2
    import numpy as np
    n, lab, st, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    if n <= 2:
        return mask, 0.0
    areas = st[1:, cv2.CC_STAT_AREA]
    main = 1 + int(np.argmax(areas))
    share = 1.0 - float(areas.max()) / float(areas.sum())
    return np.where(lab == main, 255, 0).astype(np.uint8), share


@dataclass
class Job:
    pack: str
    prompt: Prompt


def pick_jobs(packs: Sequence[Path], only: Optional[set], count: Optional[int], variants: int,
              history: Path, seed: int) -> List[Job]:
    """
    Which prompts to run.

    With --only (one pack): exactly those numbers. With --count: enough prompts
    to make that many images, drawn at random from every pack and skipping the
    ones a previous night already ran (history.csv), so night after night the
    whole library gets covered before anything repeats.
    """
    import random
    jobs = [Job(p.stem, pr) for p in packs for pr in parse_pack(p.read_text(encoding="utf-8"))
            if not only or pr.number in only]
    if count is None:
        return jobs
    done = set()
    if history.exists():
        with history.open(newline="", encoding="utf-8") as f:
            done = {(r["pack"], int(r["no"])) for r in csv.DictReader(f)}
    fresh = [j for j in jobs if (j.pack, j.prompt.number) not in done]
    if not fresh:              # the library is exhausted: start a new round
        fresh = jobs
    random.Random(seed).shuffle(fresh)
    return fresh[: max(1, -(-count // variants))]


def run(packs: Sequence[Path], out: Path, only: Optional[set], variants: int, seed0: int, steps: int,
        quantize: int, exe: str, model_path: Optional[str], dry_run: bool, check: bool,
        count: Optional[int] = None, nightly: bool = False) -> int:
    import datetime as dt
    import random
    history = out / "history.csv"
    if nightly:
        out = out / dt.date.today().isoformat()
    jobs = pick_jobs(packs, only, count, variants, history, seed0 if not nightly else int(dt.date.today().strftime("%Y%m%d")))
    if not jobs:
        print("error: no prompts found (expected '### N. Name' sections with a ``` block)", file=sys.stderr)
        return 2
    if not dry_run and not shutil.which(exe) and not Path(exe).exists():
        print(f"error: {exe} not found — install mflux and pass --mflux /path/to/mflux-generate", file=sys.stderr)
        return 2
    raw, keep, reject = out / "raw", out / "keep", out / "reject"
    for d in (raw, keep, reject):
        d.mkdir(parents=True, exist_ok=True)
    print(f"[gece] {len(jobs)} prompt × {variants} varyant = {len(jobs) * variants} görsel -> {out}", flush=True)

    rng = random.Random()
    rows, kept, rejected = [], [], []
    for j in jobs:
        p = j.prompt
        for v in range(variants):
            seed = seed0 + v if count is None else rng.randint(1, 2 ** 31 - 1)
            img = raw / f"{slugify(j.pack)[:18]}_{p.number:02d}_{slugify(p.name)}_s{seed}.png"
            cmd = mflux_cmd(exe, p, seed, img, steps, quantize, model_path)
            if dry_run:
                print(" ".join(shlex.quote(c) for c in cmd))
                continue
            if not img.exists():
                print(f"[gen] {j.pack} #{p.number} {p.name} (seed {seed}, {p.ratio})", flush=True)
                r = subprocess.run(cmd)
                if r.returncode != 0 or not img.exists():
                    rows.append([j.pack, p.number, p.name, seed, "", "", "üretilemedi"])
                    continue
            if not check:
                shutil.copy2(img, keep / img.name)
                rows.append([j.pack, p.number, p.name, seed, "", "", "kontrol edilmedi"])
                continue
            mask = _cut_mask(img)
            if mask is None:
                pieces, verdict, thin = 0, "boş", ""
            else:
                import cv2
                pieces = cv2.connectedComponents(mask, connectivity=8)[0] - 1
                verdict = "tek parça" if pieces == 1 else f"{pieces} parça"
                thin = thinnest_pct(mask) if pieces == 1 else ""
            repaired = None
            if pieces > 1:
                body, share = drop_islands(mask)
                if share <= REPAIR_MAX_SHARE:
                    repaired, mask = share, body
                    thin = thinnest_pct(mask)
                    verdict = f"onarıldı ({pieces - 1} küçük parça silindi, %{share * 100:.1f})"
            if pieces == 1 or repaired is not None:
                name = img.name if repaired is None else img.stem + "_onarildi.png"
                save_clean(mask, keep / name)
                flag = " ince" if thin != "" and thin < 0.35 else ""
                tag = " [onarıldı]" if repaired is not None else ""
                kept.append((thin if thin != "" else 0.0, keep / name, f"#{p.number} {p.name[:20]}{flag}{tag}"))
            else:
                shutil.copy2(img, reject / img.name)
                rejected.append((reject / img.name, f"#{p.number} {p.name[:20]} {verdict}"))
            rows.append([j.pack, p.number, p.name, seed, pieces, thin, verdict])
            print(f"       -> {verdict}" + (f", en ince %{thin}" if thin != "" else ""), flush=True)

    if dry_run:
        return 0
    with (out / "report.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["pack", "no", "name", "seed", "pieces", "thinnest_pct", "verdict"])
        w.writerows(rows)
    if count is not None:
        new = not history.exists()
        with history.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["pack", "no", "name", "date"])
            for j in jobs:
                w.writerow([j.pack, j.prompt.number, j.prompt.name, dt.date.today().isoformat()])
    # thickest-first: those cut at the smallest (cheapest) sizes are worth looking at first
    kept.sort(key=lambda t: -t[0])
    contact_sheet([(pth, label) for _, pth, label in kept], out / "sheet.jpg")
    contact_sheet(rejected, out / "reject_sheet.jpg")
    fixed = sum(1 for _, _, label in kept if label.endswith("[onarıldı]"))
    print(f"\nbitti: {len(rows)} görsel, {len(kept)} kullanılabilir ({fixed} tanesi onarıldı) -> {keep}"
          + (f"  (özet: {out / 'sheet.jpg'})" if kept else ""))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="batch_generate",
                                 description="Generate prompt packs locally with mflux and sort the cuttable results.")
    ap.add_argument("-p", "--pack", required=True, type=Path, nargs="+", help="prompt pack markdown file(s)")
    ap.add_argument("-o", "--output", required=True, type=Path, help="output folder")
    ap.add_argument("--only", default="", help="comma-separated prompt numbers, e.g. 1,3,9 (single pack)")
    ap.add_argument("--count", type=int, default=None,
                    help="make about this many images from random, not-yet-run prompts across all packs")
    ap.add_argument("--nightly", action="store_true", help="write into <output>/<today>/ (use with --count)")
    ap.add_argument("--variants", type=int, default=3, help="images per prompt (different seeds)")
    ap.add_argument("--seed", type=int, default=1, help="first seed")
    ap.add_argument("--steps", type=int, default=4, help="schnell is built for 2-4 steps")
    ap.add_argument("--quantize", type=int, choices=[4, 8], default=4, help="4 for 16 GB Macs, 8 for 32 GB+")
    ap.add_argument("--mflux", default="mflux-generate", help="path to mflux-generate")
    ap.add_argument("--model-path", default=None, help="a model saved with mflux-save (loads much faster)")
    ap.add_argument("--no-check", action="store_true", help="skip the one-piece check")
    ap.add_argument("--dry-run", action="store_true", help="print the commands, generate nothing")
    a = ap.parse_args(argv)
    only = {int(x) for x in a.only.split(",") if x.strip()} or None
    return run(a.pack, a.output, only, a.variants, a.seed, a.steps, a.quantize, a.mflux,
               a.model_path, a.dry_run, not a.no_check, a.count, a.nightly)


if __name__ == "__main__":
    sys.exit(main())
