# image-processor — Cut-Ready

`mycode.py` boru hattını terminale hiç dokunmadan çalıştıran form tabanlı masaüstü uygulaması (`cnc_prep_gui.py`).
macOS ve Windows'ta aynı şekilde çalışır.

## İçindekiler

- [Ne İşe Yarar](#ne-i̇şe-yarar)
- [Kurulum](#kurulum)
  - [macOS](#macos)
  - [Windows](#windows)
- [Uygulamayı Açma](#uygulamayı-açma)
- [Formu Tanı](#formu-tanı)
- [En Hızlı Yol (EazyDemand)](#en-hızlı-yol-eazydemand)
- [Komut Satırı (CLI)](#komut-satırı-cli)
- [Listing Kiti](#listing-kiti)
- [Yerel AI ile toplu üretim (mflux)](#yerel-ai-ile-toplu-üretim-mflux)
- [LoRA veri seti](#lora-veri-seti-kendi-tarzımızı-öğretmek)
- [Sorun Giderme](#sorun-giderme)

## Ne İşe Yarar

Bu form, terminaldeki uzun `mycode.py` komutunu senin yerine yazan bir ön yüz. Bir tasarım dosyası
seçersin, kalınlık/kerf/boyut ayarlarını kutulara girersin (ya da hazır **EazyDemand** şablonunu tek
tıkla yüklersin), **Çalıştır**'a basarsın — alttaki kayıt penceresinde ilerlemeyi canlı izlersin.
Arka planda hâlâ aynı `mycode.py` çalışır; form sadece komut satırını senin için yazar.

`mycode.py`'nin kendisi, ham siyah-beyaz AI çizimlerini (PNG/SVG) CNC plazma/lazer kesime hazır
`.dxf` + `.png` çiftine dönüştüren bir görüntü işleme boru hattı: gürültü temizleme, Douglas-Peucker
düğüm azaltma, minimum kalınlık zorlaması, uçan parça tespiti/köprüleme ve topoloji doğrulaması içerir.

## Kurulum

Bir kere yapılır. Aşağıdaki örneklerde proje klasörünün adı `image`.

### macOS

1. Homebrew yoksa kur (zaten varsa atla):

   ```bash
   /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
   ```

2. SVG okuma ve arayüz için native kütüphaneler:

   ```bash
   brew install cairo pango gdk-pixbuf libffi python-tk@3.14
   ```

3. Proje klasöründe sanal ortam kur:

   ```bash
   cd ~/Projects/image
   python3 -m venv .venv
   ```

4. Python paketlerini kur:

   ```bash
   .venv/bin/pip install "opencv-contrib-python>=4.8" numpy Pillow cairosvg
   ```

### Windows

1. [python.org](https://www.python.org/downloads/)'dan Python 3.11+ kur — kurulum ekranında
   **"Add python.exe to PATH"** kutusunu işaretle. Tkinter zaten dahildir, ekstra kurulum gerekmez.

2. SVG okumak için [Inkscape](https://inkscape.org/release/)'i kur. **PATH'e eklemene gerek yok** —
   script Inkscape'i `Program Files` altında kendisi bulur. cairosvg'nin Windows'ta gerektirdiği
   GTK kurulumundan çok daha kolay. (Sadece PNG ile çalışacaksan bu adımı atlayabilirsin.)

3. PowerShell'de proje klasöründe sanal ortam kur:

   ```powershell
   cd C:\Users\SEN\Projects\image
   python -m venv .venv
   ```

4. Python paketlerini kur:

   ```powershell
   .venv\Scripts\pip install opencv-contrib-python numpy Pillow
   ```

> **cairosvg mi Inkscape mi?** Script ikisini de destekler, hangisi kuruluysa onu otomatik kullanır.
> macOS'ta cairosvg kolay (Homebrew tek satır); Windows'ta Inkscape kurmak, cairosvg'nin istediği GTK
> çalışma zamanını uğraşmadan atlatır.

## Uygulamayı Açma

**macOS** — proje klasöründeki `run_gui.command` dosyasına çift tıkla. İlk açılışta macOS
"bilinmeyen geliştirici" uyarısı verirse: dosyaya sağ tık → **Aç** → açılan uyarıda yine **Aç**.
Terminalden açmak istersen:

```bash
cd ~/Projects/image
./run_gui.command
```

**Windows** — proje klasöründeki `run_gui.bat` dosyasına çift tıkla. Windows Defender "yayımcısı
bilinmiyor" uyarısı verirse **Yine de çalıştır**'ı seç. PowerShell'den açmak istersen:

```powershell
cd C:\Users\SEN\Projects\image
run_gui.bat
```

## Formu Tanı

Pencere açıldığında yukarıdan aşağıya sırayla gördüklerin:

1. **Girdi / Çıktı** — İşlenecek dosyayı ya da klasörü, ve sonuçların yazılacağı klasörü buradan
   seçersin.
2. **Boyut** — Bitmiş parçanın mm cinsinden ölçüsü. Emin değilsen **"Min Güvenli Ölçüyü Hesapla"**'ya
   bas — script tasarımını tarayıp iki ölçü yazar:
   - **tasarım korunarak min** — onarımların (kalınlaştırma, dar boşluk kapatma, uç kırpma) metal
     alanının en fazla %1'ini değiştirdiği ve hiçbir kapalı boşluğun (göz, yarık, delik) kapanmadığı
     en küçük ölçü. **Satışa koyacağın en küçük beden budur.**
   - **sadece kırılmadan** — parçanın kesimde kırılmadığı en küçük ölçü. Bunun altında değil ama
     bununla "tasarım korunarak" arasında da tasarım değişir (ör. ince yarıklar kaynar, yapraklar
     birleşir); bilgi için gösterilir.

   Bir ölçüde çalıştırdığında onarımlar tasarımı bundan fazla değiştirirse sonuç "temiz" yerine
   **uyarı** olur ("design altered at this size"). Eşik `--max-design-change-pct` ile değişir.
3. **Yapısal Kurallar** — Ne kadar ince çizgiye izin verildiği, torç/lazer kerf payı, sivri uçların
   ne zaman yuvarlanacağı, ve **İnce yer onarımı**: `taper` ince bir çizgiyi kalınlaştırırken o
   çizginin kendi kalınlık profilini tek bir katsayıyla ölçekler, böylece konik uçlar konik kalır;
   `uniform` ise sabit çaplı disk basar ve her sivri ucu topağa çevirir (eski davranış). Oymabaskı
   tarzı, tarama çizgili çizimlerde `taper` şart — varsayılan odur. Yanındaki **Sivri uç politikası**
   ise kesilemeyecek kadar incelen *serbest* uçlara ne olacağını söyler: `trim` çizgiyi erken bitirir
   ve kamanın düz kenarlarını korur, `round` kalınlaştırıp yuvarlar. Taşıyıcı boyunlar her iki
   durumda da kalınlaştırılır — onlar kesilirse parça ikiye ayrılır.

   > Bir ucun kesilemeyen boyu `s = (kerf/2) / tan(uç açısı/2)` kadardır ve **parça boyutundan
   > bağımsızdır**: 7°'lik bir uç 1.8mm kerfte son 14.7mm'sini kaybeder, 0.15mm kerfte sadece
   > 1.2mm'sini. İnce tarama detayı kaybolduğunda çare parçayı büyütmek değil, kerfi küçültmektir.
4. **Çıktı Formatı** — Hangi dosyaların üretileceği (SVG/DXF/PNG) ve PNG için piksel/DPI ayarları.
5. **EazyDemand Ayarlarını Yükle** — 3-4 numaralı tüm alanları EazyDemand'ın gerektirdiği değerlerle
   tek tıkla doldurur.
6. **Çalıştırma Kontrolleri** — *Kuru Kontrol* hiçbir dosya yazmadan analiz eder; *Çalıştır* gerçekten
   üretir; *Durdur* işlemi keser; *Çıktı Klasörünü Aç* sonucu Finder/Explorer'da gösterir.
7. **Kayıt** — Terminalde göreceğin her satır (uyarılar dahil) burada canlı akar.

## En Hızlı Yol (EazyDemand)

EazyDemand'a yükleyeceğin bir dosyan varsa dört tıkla biter:

1. **Dosya...** butonuyla tasarımını seç, **Klasör...** ile çıktı yerini seç.
2. **EazyDemand Ayarlarını Yükle**'ye bas.
3. **Çalıştır**'a bas, Kayıt penceresinde bitmesini bekle.
4. **Çıktı Klasörünü Aç**'a bas — PNG ve DXF orada.

EazyDemand gereksinimleri: PNG + DXF format, dosya < 3MB, en ince çizgi/delik ≥ 1.5mm, görsel
4500×5100px @ 300 DPI, beyaz arka plan — hepsi bu şablonla otomatik karşılanır. Şablon min
kalınlığı 1.8mm'e çeker: EazyDemand'ın 1.5mm sınırına tam oturmak yerine 0.3mm pay bırakır, böylece
kesimde inceldiği için reddedilen çizgi kalmaz.

## Komut Satırı (CLI)

Formu atlayıp doğrudan terminalden de çalıştırabilirsin:

```bash
.venv/bin/python3 mycode.py -i ./raw_designs -o ./cnc_ready --width-mm 550
```

Tüm bayraklar için:

```bash
.venv/bin/python3 mycode.py --help
```

Bir tasarımın en küçük boyutlarını (tasarım korunarak ve sadece kırılmadan, mm ve inch) öğrenmek için:

```bash
.venv/bin/python3 mycode.py -i ./tasarim.svg -o /tmp/x --suggest-size --dry-run
```

## Listing Kiti

`listing_kit.py`, Firefly'dan seçtiğin tek bir siyah-beyaz tasarımdan Etsy'ye yüklemeye hazır bir
klasör çıkarır. Senin yazman gereken tek şey title, tag ve giriş paragrafı; onların promptu da
klasörde hazır.

**Arayüzden:** Girdi'de tasarım dosyasını, Çıktı klasörü'nde ana klasörünü seç. Formun altındaki
**Etsy Listing Kiti** bölümünde tasarım adını yaz, ürün tipini ve temayı seç, **Listing Kiti
Oluştur**'a bas. Bitince **Kit Klasörünü Aç** seni doğrudan `<çıktı klasörü>/kits/<tasarım-adı>/`
klasörüne götürür. Kesim dosyaları formdaki min kalınlık, kerf ve sadeleştirme değerleriyle üretilir.

**Terminalden:**

```bash
.venv/bin/python3 listing_kit.py -i ./tasarim.png -o ./kits --name "Ocean Wave Bundle"
```

Çıktı (`kits/ocean-wave-bundle/`):

| Dosya | İçerik |
|---|---|
| `listing.md` | Bedenler, Etsy Size değerleri (≤ 20 karakter), fiyat tablosu (maliyet / liste / görünen / kâr), SKU, varyasyonlar, açıklamanın sabit blokları, gümrük satırı, title–tag–giriş promptu |
| `cut/<beden>/` | Her beden için kesim dosyaları (SVG + DXF + 4500×5100 PNG), EazyDemand ayarlarıyla |
| `size_guide.jpg`, `size_collage.jpg` | 175 cm insanla ölçek görselleri |
| `kit.json` | Hepsi makinenin okuyacağı formatta |

Bedenler ve fiyatlar listing promptundaki (v6) kurallarla hesaplanır. En küçük beden, Cut-Ready'nin
**tasarım korunarak** ölçüsüdür; bu arama birkaç dakika sürer. Ölçüyü zaten biliyorsan `--min-cm 45`
ile atla. SKU'lar `kits/sku_ledger.csv` defterinden sırayla verilir; aynı gün iki listinge aynı SKU
düşmez.

Sık kullanılan bayraklar:

| Bayrak | Ne yapar |
|---|---|
| `--type privacy` | Privacy panel: en büyük beden 240 cm |
| `--theme deco` | Alternatif renkleri seçer: `modern`, `deco`, `botanical`, `coastal`, `privacy`, `wildlife`, `gothic`, `western`, `kitchen`, `mancave`, `family`, `kids` |
| `--min-cm 45` | Bilinen en küçük uzun kenar (cm); ölçü aramasını atlar |
| `--no-cut` | Kesim dosyalarını üretmez (hızlı deneme) |
| `--simplify-mm 0.5` | İnce paralel çizgili tasarımlarda düğüm sayısını düşürür |
| `--mockups` | 2D stüdyo yerleşim görselleri ve renk paneli de üretir (varsayılan kapalı; oda görselleri ChatGPT'de çok daha iyi çıkıyor) |
| `--rooms ./rooms` | Boş oda fotoğraflarına yerleştirir (`--mockups`'ı açar; aşağıya bak) |

Oda mockup'ları ve renk paneli ChatGPT'de, kesim dosyasının PNG'si yüklenerek üretilir; kit ölçek görsellerini (boyut rehberi, kolaj) üretir, çünkü görüntü modelleri ölçeği tutturamaz.

**Boş oda fotoğrafları (isteğe bağlı):** `rooms/` klasörüne duvarı boş oda fotoğraflarını koy ve yanına bir
`rooms.json` yaz. `box`, eserin sığacağı dikdörtgen (piksel: x, y, genişlik, yükseklik); `light`,
ışığın geldiği taraf.

```json
[
  {"file": "salon1.jpg", "box": [620, 300, 1400, 700], "light": "left"},
  {"file": "yatak1.jpg", "box": [500, 250, 1200, 500], "light": "right"}
]
```

## Yerel AI ile toplu üretim (mflux)

`batch_generate.py`, prompt paketlerimizden birini (ör. `53_50_yeni_nisler_standart.md`) Mac'te
yerel FLUX.1 schnell modeliyle toplu olarak üretir ve her görseli Cut-Ready'nin kendi siyah-beyaz
ayrımıyla kontrol eder:

- `keep/`: tek parça çıkanlar, bunlara bakarsın
- `reject/`: kopuk parçası olanlar veya boş görseller
- `report.csv`: her görselin parça sayısı

Apple Silicon (M1 ve sonrası) gerekir; 16 GB RAM'de 4-bit modda çalışır. mflux bu projenin bağımlılığı
değildir, kendi ortamına kurulur:

```bash
python3 -m venv ~/mflux-env
~/mflux-env/bin/pip install -U mflux
```

İlk çalıştırmada model (~30 GB) Hugging Face'ten iner. Diskte yer aç, interneti açık bırak. İndirme
bittikten sonra modeli 4-bit olarak kaydet; her çalıştırmada yeniden dönüştürmesin, hızlı açılsın:

```bash
~/mflux-env/bin/mflux-save --model schnell --quantize 4 --path ~/flux-schnell-4bit
```

Sonra paketten birkaç prompt dene:

```bash
.venv/bin/python3 batch_generate.py -p 53_50_yeni_nisler_standart.md -o gen_out \
    --only 1,9,29 --variants 3 \
    --mflux ~/mflux-env/bin/mflux-generate --model-path ~/flux-schnell-4bit
```

`--only` vermezsen paketin tamamını üretir (50 prompt × 3 varyant). Gece çalışmaya bırakılabilir.
`--dry-run`, hiçbir şey üretmeden çalışacak komutları yazdırır.

**Gece üretimi (`gece.command`):** Prompt paketlerini (`.md`) proje klasöründeki `prompts/` klasörüne
koy, akşam `gece.command`'a çift tıkla. Script Mac'in uyumasını engeller, paketlerden daha önce
üretilmemiş rastgele prompt'lar seçer ve her birinden 2 varyant olmak üzere ~50 görsel üretir. Sabah:

- `gece/<tarih>/sheet.jpg`: tek parça çıkanların numaralı özeti. En kalın çizgiliden inceye sıralı;
  kalın olanlar küçük bedende de kesilir. "ince" etiketli olanların min ölçüsü büyük çıkar.
- `gece/<tarih>/keep/`: o tasarımların saf siyah-beyaz PNG'leri. Doğrudan listing kitine verilebilir.
- `gece/history.csv`: hangi prompt hangi gece üretildi. Bütün kütüphane bitmeden aynı prompt tekrar seçilmez.

Görsel sayısı için `COUNT=80 ./gece.command`. mflux ya da model başka bir yerdeyse `MFLUX=...` ve
`MODEL=...` ile ver.

FLUX.1 schnell promptun en fazla ~256 token'ını okur. Standart formattaki uzun Technical bloğu bu
yüzden yerelde aynı kuralları taşıyan kısa bir sürümle değiştirilir; tasarım tarifi ve Style satırı
olduğu gibi kalır.

## LoRA veri seti (kendi tarzımızı öğretmek)

`lora_dataset.py`, daha önce Cut-Ready'den geçirdiğin tasarımları (ör. `~/etsy` altındaki listing
klasörleri) tarar ve LoRA eğitimi için veri seti hazırlar. Klasör düzeni önemli değil: Cut-Ready'nin
yanına bıraktığı `cnc_prep_report.json` ve kendi SVG damgası üzerinden bulur.

```bash
.venv/bin/python3 lora_dataset.py -i ~/etsy -o lora_data --size 768
```

- Her tasarımdan tek kopya alır; farklı bedenler ve yeniden adlandırılmış kopyalar ayıklanır.
- `lora_data/0001.png`: kare, saf siyah-beyaz, ortalanmış. Yanındaki `0001.txt`, `ifcsteel style, ...`
  ile başlayan açıklama.
- `dataset_sheet.jpg`: hepsi numaralı. Zayıf olanların `.png`'sini sil.
- `captions.csv`: açıklamaları burada düzelt, sonra `--apply-captions` ile `.txt` dosyalarına yaz:

```bash
.venv/bin/python3 lora_dataset.py -o lora_data --apply-captions
```

## Sorun Giderme

Karşılaşman muhtemel şeyler, sırayla ne yapacağın:

| Platform | Belirti | Çözüm |
|---|---|---|
| macOS | Pencere tamamen siyah açılıyor (`No module named '_tkinter'`) | `brew install python-tk@3.14` kur, uygulamayı kapatıp tekrar aç. |
| macOS | Çift tıklayınca "bilinmeyen geliştirici" uyarısı | `run_gui.command` dosyasına sağ tık → **Aç** → açılan uyarıda yine **Aç**. Bir kere yaptıktan sonra sıradan çift tık yeterli olur. |
| Windows | PowerShell "python tanınmıyor" diyor | Python'u python.org'dan "Add python.exe to PATH" işaretli olarak yeniden kur. |
| Her ikisi | "No SVG rasteriser available" | SVG girdisi var ama ne cairosvg ne Inkscape bulunamadı. Windows'ta [Inkscape](https://inkscape.org/release/) kur (PATH'e eklemene gerek yok, script Program Files'ta bulur); macOS'ta `brew install cairo pango gdk-pixbuf libffi` veya `brew install inkscape`. Alternatif: tasarımı SVG yerine PNG olarak ver. |
| Her ikisi | "Zaten bir işlem çalışıyor" mesajı | Önceki Çalıştır/Kuru Kontrol henüz bitmedi. Kayıt penceresinde `[bitti]` satırını bekle, ya da **Durdur**'a bas. |
| Her ikisi | Öneri kutusu "bulunamadı" yazıyor | Girdi dosyası seçilmemiş olabilir, ya da dosya çok yoğun detaylıysa hesaplama birkaç dakika sürebilir — Kayıt penceresinde ilerlemeyi izleyebilirsin. |

Hâlâ takılıyorsan: Kayıt penceresindeki tam metni kopyala paylaş — script hiçbir zaman sessizce
başarısız olmaz, hatanın nedenini her zaman orada yazar.
