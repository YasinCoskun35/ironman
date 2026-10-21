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
