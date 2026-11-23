# Tedarikçi Denetim

Tedarikçi denetimi, bulgu/DÖF takibi ve **dış istihbarat** (domain'lerin dışarıdan görünen siber güvenlik durumu) uygulaması.

## Özellikler
- **Tedarikçiler**: firma bilgileri, kritiklik, durum, domain listesi
- **Soru setleri**: Denetim planlanırken özel sorular yazılabilir ve/veya bir ya da birden fazla standart set seçilebilir;
  seçilenler tek denetimde birleşir. Hazır setler: Genel Tedarikçi Denetimi, **PCI DSS v4.0**, **ISO/IEC 27001:2022**,
  **ISO 9001:2015**, **ISO 14001:2015**, **ISO 45001:2018**, **KVKK**. "Soru Setleri" sayfasından özel set oluşturulabilir,
  standart setler kopyalanıp uyarlanabilir. Her denetim, oluşturulduğu andaki soruların kopyasını saklar; set sonradan
  değişse de eski denetimler bozulmaz.
- **Puanlama**: bölüm ağırlıklı (Uygun = 1, Kısmen = 0,5, Uygun değil = 0, N/A hesaba katılmaz)
  → *Onaylı / Şartlı Onaylı / Gelişim Gerekli / Onaysız*
- **Bulgular & aksiyonlar**: denetim cevaplarından veya taramalardan otomatik bulgu, önem bazlı termin
  (kritik 7, yüksek 30, orta 60, düşük 90 gün), sorumlu, durum, gecikme takibi, CSV dışa aktarma
- **Dış istihbarat** (yalnızca pasif / herkese açık kaynaklar):
  | Kontrol | Kaynak |
  |---|---|
  | SPF, DMARC, DNSSEC, CAA, MX/NS | DNS (engelliyse DNS-over-HTTPS) |
  | Sertifika geçerliliği/bitişi, TLS 1.0/1.1 desteği | TLS el sıkışması (443) |
  | HTTP→HTTPS, HSTS, CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, sürüm ifşası, security.txt | HTTPS ana sayfa |
  | İnternete açık riskli servisler (RDP, SMB, DB…), bilinen CVE'ler | Shodan InternetDB (ücretsiz, anahtarsız) |
  | Alt alan adları, hassas isimler (dev, test, vpn, admin…) | crt.sh (Sertifika Şeffaflığı) |
  | Domain kayıt/bitiş tarihi, kayıt firması | RDAP |

  Not: A ≥ 90, B ≥ 80, C ≥ 70, D ≥ 60, F < 60. Başarısız kontroller bulguya dönüşür; sonraki taramada düzelmişse bulgu **otomatik kapanır**.
  Ölçülemeyen kontroller (ağ engeli vb.) puanlamaya katılmaz.
- **Pano**: risk dağılımı, önem dağılımı, siber notlar, risk sıralaması, geciken aksiyonlar, planlı denetimler, 12 aydır denetlenmeyenler
- **Rapor**: tedarikçi bazında yazdırılabilir / PDF rapor
- **Genel puan** = denetim × %60 + siber × %40. Risk: kritik açık bulgu, genel puan < 50 veya yüksek kritik tedarikçide < 70 → *yüksek*.

## Hızlı başlangıç (çift tıkla)
- **Windows:** `baslat.bat` dosyasına çift tıklayın.
- **Mac:** `baslat.command` dosyasına sağ tıklayıp *Aç* deyin (ilk seferde güvenlik onayı ister).

İlk açılışta gerekli paketler kurulur ve örnek veri yüklenir; tarayıcı otomatik olarak http://localhost:8000 adresini açar.
Gereken tek şey Python 3.10+ (https://www.python.org/downloads/).

## Kurulum (elle)
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python -m app.seed            # (isteğe bağlı) örnek veri
uvicorn app.main:app --host 0.0.0.0 --port 8000
```
Tarayıcıda: http://localhost:8000 — API dokümantasyonu: http://localhost:8000/docs

Komut satırından tek domain taraması: `python -m app.intel.scanner ornekfirma.com.tr`

Veritabanı: `data/denetim.db` (SQLite). Farklı konum için `TD_DB_PATH` ortam değişkeni.

## Özelleştirme
- Standart soru setleri: `app/question_sets.py` (uygulama açılışında veritabanına yüklenir/güncellenir)
- Özel setler: arayüzde **Soru Setleri** sayfası
- Tarama kontrolleri, ağırlıkları, riskli port listesi: `app/intel/scanner.py`
- Genel puan / risk kuralları: `app/main.py` → `_summary()`

## Testler
```bash
pip install pytest && python -m pytest -q
```

## Yasal not
Tarayıcı yalnızca herkese açık bilgileri ve normal bir web ziyaretinin yaptığı bağlantıları kullanır; port taraması veya
zafiyet istismarı yapmaz. Yine de tedarikçi sözleşmelerinize dış güvenlik değerlendirmesi yapılacağına dair bir madde eklemeniz önerilir.
Shodan/crt.sh verileri gecikmeli olabilir; kritik bulguları tedarikçiyle teyit edin.
