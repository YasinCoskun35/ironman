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
   bas — script tasarımını tarayıp en küçük güvenli boyutu mm/inch olarak yazar.
3. **Yapısal Kurallar** — Ne kadar ince çizgiye izin verildiği, torç/lazer kerf payı, sivri uçların
   ne zaman yuvarlanacağı.
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
4500×5100px @ 300 DPI, beyaz arka plan — hepsi bu şablonla otomatik karşılanır.

## Komut Satırı (CLI)

Formu atlayıp doğrudan terminalden de çalıştırabilirsin:

```bash
.venv/bin/python3 mycode.py -i ./raw_designs -o ./cnc_ready --width-mm 550
```

Tüm bayraklar için:

```bash
.venv/bin/python3 mycode.py --help
```

Bir tasarımın en küçük güvenli boyutunu (mm ve inch) öğrenmek için:

```bash
.venv/bin/python3 mycode.py -i ./tasarim.svg -o /tmp/x --suggest-size --dry-run
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
