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
              "no text, no shadow, no gray, no mockup, generous white margin.")


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
        cmd += ["--model", "schnell", "--path", model_path]
    else:
        cmd += ["--model", "schnell", "--quantize", str(quantize)]
    return cmd


def check_cuttable(png: Path) -> tuple[int, str]:
    """
    (pieces, verdict) using mycode.py's own binarisation and speckle filter at a
    600 mm reference width, so "one piece" here means one piece to Cut-Ready.
    """
    import cv2
    import mycode as cnc
    cfg = cnc.Config(width_mm=600.0)
    gray, w_mm, _ = cnc.load_source(png, cfg)
    gray = cnc.resample_to_work_res(gray, cfg, w_mm)
    cfg.mm_per_px = w_mm / gray.shape[1]
    mask = cnc.binarize(gray, cfg)
    if not mask.any() or float((mask > 0).mean()) > 0.98:
        return 0, "boş"
    mask, _ = cnc.denoise(mask, cfg)
    n, _ = cv2.connectedComponents(mask, connectivity=8)
    pieces = n - 1
    return pieces, ("tek parça" if pieces == 1 else f"{pieces} parça")


def run(pack: Path, out: Path, only: Optional[set], variants: int, seed0: int, steps: int,
        quantize: int, exe: str, model_path: Optional[str], dry_run: bool, check: bool) -> int:
    prompts = [p for p in parse_pack(pack.read_text(encoding="utf-8")) if not only or p.number in only]
    if not prompts:
        print("error: no prompts found (expected '### N. Name' sections with a ``` block)", file=sys.stderr)
        return 2
    if not dry_run and not shutil.which(exe) and not Path(exe).exists():
        print(f"error: {exe} not found — install mflux and pass --mflux /path/to/mflux-generate", file=sys.stderr)
        return 2
    raw, keep, reject = out / "raw", out / "keep", out / "reject"
    for d in (raw, keep, reject):
        d.mkdir(parents=True, exist_ok=True)

    rows = []
    for p in prompts:
        for v in range(variants):
            seed = seed0 + v
            img = raw / f"{p.number:02d}_{slugify(p.name)}_s{seed}.png"
            cmd = mflux_cmd(exe, p, seed, img, steps, quantize, model_path)
            if dry_run:
                print(" ".join(shlex.quote(c) for c in cmd))
                continue
            if not img.exists():
                print(f"[gen] {p.number}. {p.name} (seed {seed}, {p.ratio})", flush=True)
                r = subprocess.run(cmd)
                if r.returncode != 0 or not img.exists():
                    rows.append([p.number, p.name, seed, "", "üretilemedi"])
                    continue
            pieces, verdict = check_cuttable(img) if check else (-1, "kontrol edilmedi")
            dest = (keep if pieces == 1 or not check else reject) / img.name
            shutil.copy2(img, dest)
            rows.append([p.number, p.name, seed, pieces, verdict])
            print(f"       -> {verdict}", flush=True)

    if not dry_run:
        with (out / "report.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["no", "name", "seed", "pieces", "verdict"])
            w.writerows(rows)
        kept = sum(1 for r in rows if r[4] == "tek parça")
        print(f"\nbitti: {len(rows)} görsel, {kept} tek parça -> {keep}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="batch_generate",
                                 description="Generate a prompt pack locally with mflux and sort the cuttable results.")
    ap.add_argument("-p", "--pack", required=True, type=Path, help="prompt pack markdown file")
    ap.add_argument("-o", "--output", required=True, type=Path, help="output folder")
    ap.add_argument("--only", default="", help="comma-separated prompt numbers, e.g. 1,3,9")
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
               a.model_path, a.dry_run, not a.no_check)


if __name__ == "__main__":
    sys.exit(main())
