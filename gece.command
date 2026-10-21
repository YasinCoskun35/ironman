#!/bin/bash
# Gece üretimi: prompts/ klasöründeki paketlerden ~50 görsel üretir, kesilebilir olanları ayırır.
# Çift tıkla ya da Terminal'de ./gece.command çalıştır. Mac uyumasın diye caffeinate ile sarılı.
# Sabah: gece/<tarih>/sheet.jpg  (tek parça çıkanların özeti, en kalından inceye)
cd "$(dirname "$0")" || exit 1

COUNT="${COUNT:-50}"
MFLUX="${MFLUX:-$HOME/mflux-env/bin/mflux-generate}"
MODEL="${MODEL:-$HOME/flux-schnell-4bit}"
PY=".venv/bin/python3"; [ -x "$PY" ] || PY="python3"

shopt -s nullglob
PACKS=(prompts/*.md)
if [ ${#PACKS[@]} -eq 0 ]; then
  echo "prompts/ klasöründe prompt paketi (.md) yok. Paketleri oraya kopyala."; exit 1
fi
MODEL_ARGS=(--model-path "$MODEL"); [ -d "$MODEL" ] || MODEL_ARGS=()

caffeinate -i "$PY" batch_generate.py -p "${PACKS[@]}" -o gece --count "$COUNT" --variants 2 --nightly \
  --mflux "$MFLUX" "${MODEL_ARGS[@]}"
