"""
Tests for batch_generate.py: prompt-pack parsing, the local prompt rewrite and
the one-piece check. mflux itself only runs on Apple Silicon and is not called.
"""

import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import batch_generate as bg

PACK = """# pack

### 1. Bambu ve Ay
Hedef aramalar: zen · Oran: 1:2 (Firefly'da 9:16) · Hedef min: ~60 cm
```
Laser-cut steel wall art design, 1:2 format: three bamboo stalks.
Style: calm Japanese-inspired steel art.
Technical: pure solid black on pure white, no gray. One single connected piece; long rules.
```

### 2. Sörfçü
Oran: 16:9 (yatay ürün)
```
A surfer on a wave. Flat solid black on pure white.
```

### 3. Başlıksız blok yok
just text, no fence
"""


def test_parse_pack_reads_number_name_ratio():
    ps = bg.parse_pack(PACK)
    assert [(p.number, p.name, p.ratio) for p in ps] == [(1, "Bambu ve Ay", "9:16"), (2, "Sörfçü", "16:9")]


def test_localize_swaps_technical_block_only():
    p1, p2 = bg.parse_pack(PACK)
    local = bg.localize(p1.text)
    assert "long rules" not in local and bg.SHORT_TECH in local
    assert local.startswith("Laser-cut steel wall art design")
    assert bg.localize(p2.text) == p2.text           # short packs pass through


def test_mflux_cmd_uses_ratio_size_and_quantize(tmp_path):
    p = bg.parse_pack(PACK)[0]
    cmd = bg.mflux_cmd("mflux-generate", p, 7, tmp_path / "a.png", 4, 4, None)
    assert cmd[cmd.index("--width") + 1] == "768" and cmd[cmd.index("--height") + 1] == "1344"
    assert cmd[cmd.index("--quantize") + 1] == "4" and cmd[cmd.index("--seed") + 1] == "7"
    saved = bg.mflux_cmd("mflux-generate", p, 7, tmp_path / "a.png", 4, 4, "/m")
    assert "--quantize" not in saved and saved[saved.index("--path") + 1] == "/m"


def test_nearest_ratio():
    assert bg.nearest_ratio("3:1") == "16:9"
    assert bg.nearest_ratio("1:3") == "9:16"


def test_check_cuttable_counts_pieces(tmp_path):
    one = np.full((600, 900), 255, np.uint8)
    cv2.rectangle(one, (100, 100), (800, 500), 0, 40)
    cv2.imwrite(str(tmp_path / "one.png"), one)
    two = one.copy()
    cv2.circle(two, (450, 300), 60, 0, -1)          # floating disc inside the frame
    cv2.imwrite(str(tmp_path / "two.png"), two)
    assert bg.check_cuttable(tmp_path / "one.png") == (1, "tek parça")
    assert bg.check_cuttable(tmp_path / "two.png")[0] == 2


def test_slugify_turkish():
    assert bg.slugify("Dağlı Aile Tabelası") == "dagli-aile-tabelasi"


def _fake_mflux(tmp_path):
    """A stand-in for mflux-generate: writes a framed square (one piece) or, for odd seeds, adds a floating dot."""
    exe = tmp_path / "fake-mflux"
    exe.write_text(
        f"#!{sys.executable}\n"
        "import sys, cv2, numpy as np\n"
        "a = sys.argv[1:]; out = a[a.index('--output') + 1]; seed = int(a[a.index('--seed') + 1])\n"
        "w = int(a[a.index('--width') + 1]); h = int(a[a.index('--height') + 1])\n"
        "img = np.full((h, w), 255, np.uint8); cv2.rectangle(img, (60, 60), (w - 60, h - 60), 0, 30)\n"
        "if seed % 2: cv2.circle(img, (w // 2, h // 2), 40, 0, -1)\n"
        "cv2.imwrite(out, img)\n", encoding="utf-8")
    exe.chmod(0o755)
    return str(exe)


def test_nightly_run_sorts_records_history_and_skips_done(tmp_path):
    pack = tmp_path / "pack.md"
    pack.write_text(PACK, encoding="utf-8")
    exe = _fake_mflux(tmp_path)
    out = tmp_path / "gece"
    assert bg.run([pack], out, None, 2, 1, 4, 4, exe, None, False, True, count=2, nightly=False) == 0
    report = (out / "report.csv").read_text(encoding="utf-8")
    assert report.count("\n") == 3                     # header + 1 prompt x 2 variants
    assert len(list((out / "keep").iterdir())) + len(list((out / "reject").iterdir())) == 2
    first = (out / "history.csv").read_text(encoding="utf-8")
    # a second night must pick the prompt not run yet
    assert bg.run([pack], out, None, 2, 1, 4, 4, exe, None, False, True, count=2, nightly=False) == 0
    second = (out / "history.csv").read_text(encoding="utf-8")
    names = {line.split(",")[2] for line in second.strip().splitlines()[1:]}
    assert names == {"Bambu ve Ay", "Sörfçü"}
    assert len(first.strip().splitlines()) == 2


def test_kept_files_are_pure_black_and_white(tmp_path):
    pack = tmp_path / "pack.md"
    pack.write_text(PACK, encoding="utf-8")
    out = tmp_path / "o"
    bg.run([pack], out, {2}, 1, 2, 4, 4, _fake_mflux(tmp_path), None, False, True)   # even seed: one piece
    kept = list((out / "keep").iterdir())
    assert len(kept) == 1 and (out / "sheet.jpg").exists()
    vals = set(np.unique(cv2.imread(str(kept[0]), cv2.IMREAD_GRAYSCALE)).tolist())
    assert vals <= {0, 255}
