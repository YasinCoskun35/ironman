#!/bin/bash
# Gece üretimi: prompts/ klasöründeki paketlerden ~50 görsel üretir, kesilebilir olanları ayırır.
# Çift tıkla ya da Terminal'de ./gece.command çalıştır. Mac uyumasın diye caffeinate ile sarılı.
# Sabah: gece/<tarih>/sheet.jpg  (tek parça çıkanların özeti, en kalından inceye)
cd "$(dirname "$0")" || exit 1

COUNT="${COUNT:-50}"
MFLUX="${MFLUX:-$HOME/mflux-env/bin/mflux-generate}"
MODEL="${MODEL:-$HOME/flux-schnell-4bit}"
# Eğitilmiş stil LoRA'sı varsa (mflux-train'in checkpoint zip'i ya da .safetensors) onunla üretir.
LORA="${LORA:-lora/ifcsteel.zip}"; [ -f "$LORA" ] || LORA="lora/ifcsteel.safetensors"
MFLUX_Z="${MFLUX_Z:-$HOME/mflux-env/bin/mflux-generate-z-image-turbo}"
PY=".venv/bin/python3"; [ -x "$PY" ] || PY="python3"

shopt -s nullglob
PACKS=(prompts/*.md)
if [ ${#PACKS[@]} -eq 0 ]; then
  echo "prompts/ klasöründe prompt paketi (.md) yok. Paketleri oraya kopyala."; exit 1
fi
if [ -f "$LORA" ]; then
  echo "LoRA ile üretim (Z-Image Turbo): $LORA"
  ENGINE_ARGS=(--mflux "$MFLUX_Z" --lora "$LORA")
else
  ENGINE_ARGS=(--mflux "$MFLUX" --model-path "$MODEL"); [ -d "$MODEL" ] || ENGINE_ARGS=(--mflux "$MFLUX")
fi

caffeinate -i "$PY" batch_generate.py -p "${PACKS[@]}" -o gece --count "$COUNT" --variants 2 --nightly \
  "${ENGINE_ARGS[@]}"
