# IronForgeCraft — 1 Haftalık Plan (27 Eylül – 3 Ekim 2026)

Hedef: Mevcut 36 listing'i yeni standarda getirmek, yeni listing sürecini 5 listing'le kalibre etmek ve ikinci haftadan itibaren haftada 25–30 listing hızına geçmeye hazır olmak.

Bu klasördeki dosyalar:
- `01_listeleme_promptu.md` — yeni listing'ler için ana prompt (kural kitabı)
- `02_mevcut_listing_guncelleme_promptu.md` — mevcut 36 listing'i tarayıcıdaki Claude ile güncellemek için
- `03_30_tasarim_promptu.md` — Firefly'da üretilecek 30 tasarım promptu

---

## Gün 1 — Pazar 27 Eylül: Envanter ve hazırlık

- [ ] Claude kotası yenilenince tarayıcıdaki Claude'a `02_mevcut_listing_guncelleme_promptu.md` dosyasını yapıştır. En alttaki yere `01_listeleme_promptu.md` dosyasının tamamını koy.
- [ ] Yalnızca **Aşama 1'i** (envanter) çalıştır. Hiçbir şey değişmeyecek.
- [ ] Çıkan envanter tablosunu ve soruları bir not dosyasına kopyala (kota biterse yeni sohbette devam etmek için).
- [ ] Soruların cevaplarını hazırla:
  - Hangi ürünler line art, yani delikli (Pre-drilled) gidecek?
  - Her tasarımın minimum ölçüsü (Cut-Ready'den).
  - Giriş bedeni fiyatı: **hepsinde $99 liste fiyatı** (aramada $64,35 görünür).
- [ ] Etsy'de otomatik yenilemeyi kontrol et (Shop Manager → listing ayarları → Renewal options). Eleme kuralını uygulayabilmek için yenilemeyi elle yapacağız.

## Gün 2 — Pazartesi 28 Eylül: Mevcut listing'ler, 1. yarı

- [ ] Tarayıcıdaki Claude ile **Aşama 2 ve 3**. En çok görüntülenenlerden başla, 5'erli gruplar hâlinde.
- [ ] Bugünün hedefi yaklaşık 18 listing: metin, fiyat ($99 giriş bedeni), attribute'lar, montaj varyasyonunun kaldırılması, SKU düzeltmesi.
- [ ] Her grubun sonunda Claude'un verdiği **değişiklik kaydını** sakla.
- [ ] Montaj varyasyonu kaldırılan listing'lerde fiyatların sıfırlanmadığını kontrol et.

## Gün 3 — Salı 29 Eylül: Mevcut listing'ler, 2. yarı

- [ ] Kalan yaklaşık 18 listing için Aşama 2 ve 3.
- [ ] Claude'un bitişte verdiği listeyi al: senin elle yapman gerekenler, hâlâ $65'in üstünde görünen listing'ler.
- [ ] **Bundan sonra 7–10 gün başlık ve tag'lere dokunma.**

## Gün 4 — Çarşamba 30 Eylül: En iyi 10 listing'in görselleri

Sıfırdan üretme. Sadece bozuk olanı düzelt, eksik olanı tamamla.

- [ ] **Vinyl:** arcade görselini değiştir (ekranda Donkey Kong var, fikri mülkiyet riski). Ekran boş veya soyut desenli olsun. Diğer görsellere dokunma.
- [ ] **Privacy Screen:** yatay bank görselini kaldır (tasarım farklı, yatay beden yok). Vidaları görünmeyecek şekilde en iyi 2 görseli (ahşap çit, gece patio) yeniden üret. Balkon kelepçeleri ve kapı direkleri olan görselleri değiştir.
- [ ] **Ayı:** Beige (ahşap gibi duran) görseli kaldır. Kahverengi giriş holü görselini ayı banktan dar olacak şekilde yeniden üret. Yeni ana görsel: açık kütük duvar, ayı kadrajın %70–75'i.
- [ ] **Hepsine** ölçü karşılaştırma görseli (H) ekle ve ilk 4–5 sıraya koy.
- [ ] **10'dan az görseli olanlar:** Cactus, Matisse, Agave, Geyik, Lotus — 10'a tamamla.
- [ ] Diğer listing'ler: Raven, Kurt, Çelenk, Art Deco (gölge) — aşağıdaki kalite kontrolüne göre bak, sadece bozuk olanı değiştir.

## Gün 5 — Perşembe 1 Ekim: 5 yeni tasarım

- [ ] `03_30_tasarim_promptu.md` dosyasından şu 5'ini üret: **1** (huş privacy panel), **5** (dağ gölü yatay panel), **15** (moose), **19** (hayat ağacı), **22** (kiraz çiçeği dalı).
  - Kişiye özel tabelaları (11–14) şimdilik bekletiyoruz: üreticinin sipariş başına dosya kabul ettiği doğrulanmadan yayına alınmaz.
- [ ] Her tasarımı Cut-Ready'de EazyDemand şablonuyla çalıştır, **Min Güvenli Ölçüyü Hesapla**.
- [ ] Kayıtta **"floating"** kelimesini ara. Varsa düzelt veya yeniden üret.
- [ ] Minimum ölçüdeki PNG'yi orijinalle yan yana aç. Önemli detay kaybolmuşsa bir üst bedeni giriş bedeni yap.
- [ ] Minimum ölçü hedeften çok büyükse promptun sonuna `Simplify: fewer and larger shapes, remove the smallest details.` ekleyip yeniden üret.

## Gün 6 — Cuma 2 Ekim: 5 yeni listing

- [ ] Her tasarım için `01_listeleme_promptu.md` ile metin ve görsel promptlarını al.
- [ ] Önce şu 5 görseli kaliteli üret: **A** (ana), **B** ve **C** (iki farklı alıcı tipine hitap eden yaşam alanı), **F** (yakın detay), **H** (ölçü karşılaştırması). Renk mock'ları sonra.
- [ ] Her görseli aşağıdaki kalite kontrolünden geçir.
- [ ] Gerçek kartela fotoğrafını ekle ve yayınla.

## Gün 7 — Cumartesi 3 Ekim: Kontrol (benimle)

Bana şunları getir:
- [ ] Yeni Etsy listing CSV'si (Shop Manager → Settings → Options → Download Data)
- [ ] eRank Listings dosyası ve yeni Tag Report
- [ ] 5 yeni listing'in görselleri (kalibrasyon için)
- [ ] Etsy Stats'tan son 7 günün ekran görüntüsü

Birlikte bakacaklarımız: fiyat değişikliğinin etkisi, yeni listing'lerin kalitesi, ikinci haftadan itibaren 25–30 listing'e geçiş.

---

## Görsel kalite kontrolü (her listing, yaklaşık 5 dakika)

- [ ] **Tasarım bozulmamış:** orijinalle yan yana koy — eksik/fazla parça, kapanmış boşluk, farklı desen yok.
- [ ] **Gölge var:** ürün duvara aynı ışık yönünde gölge düşürüyor, etiket gibi yapışık durmuyor.
- [ ] **Ölçek doğru:** ürün hiçbir görselde 150 cm'den büyük görünmüyor (mobilyaya göre bak).
- [ ] **Kadraj payı:** her kenarda boşluk var; burun, pati, uç kesilmiyor.
- [ ] **Malzeme doğru:** açık renkler ahşap gibi, Silver paslanmaz çelik gibi durmuyor.
- [ ] **Donanım görünmüyor:** vida, kelepçe, direk, braket yok.
- [ ] **Fikri mülkiyet temiz:** logo, oyun ekranı, karakter, tanınır eser yok.
- [ ] **Alıcı çeşitliliği:** B ve C farklı oda, farklı duvar ve farklı alıcı tipi.
- [ ] **Telefonda anlaşılır:** küçük resimde ürün ilk bakışta okunuyor.

## Sabit kurallar

- **Fiyat:** giriş bedeni liste fiyatı $99 (aramada $64,35). Büyük bedenlerde maliyet × 4.
- **Üretilemeyen beden yok:** minimum ölçünün altında beden listelenmez.
- **Reklam:** stratejiyi değiştirme, bütçeyi artırma — 30 gün dokunma.
- **Başlık/tag:** güncellemeden sonra 7–10 gün dokunma.
- **Eleme:** listing 4 aylık olunca satış veya 3–5 favori varsa yenile, yoksa düşmesine izin ver.
- **Kazananı çoğalt:** satmaya başlayan tasarımın 2–3 varyantını ekle.
- **Hacim:** 2. haftadan itibaren haftada 25–30 listing, 3–4 ana kulvarda (dış mekân/paneller, kabin/rustik, kişiye özel tabela, modern/soyut).
