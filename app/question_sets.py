"""Yerleşik (standart) soru setleri.

Her set: key, name, standard, description, sections[{id, title, weight, questions[(id, metin)]}]
Sorular standartların ilgili maddelerine göre hazırlanmış özet denetim sorularıdır; standardın
resmi metni değildir. Maddeler parantez içinde referans olarak verilmiştir.
Uygulama açılışında bu setler veritabanına yüklenir/güncellenir (key'e göre).
Kendi setlerinizi arayüzdeki "Soru Setleri" sayfasından oluşturun.
"""
from .checklist import CHECKLIST


def _s(id, title, weight, *questions):
    """Soru: "metin" veya ("metin", {critical, weight, condition, code}). Kod varsayılanı: <bölüm>.<sıra>"""
    out = []
    for i, q in enumerate(questions, 1):
        text, opts = (q, {}) if isinstance(q, str) else q
        out.append({"code": opts.get("code", f"{id}.{i}"), "text": text, "weight": opts.get("weight", 1),
                    "critical": opts.get("critical", False), "condition": opts.get("condition")})
    return {"id": id, "title": title, "weight": weight, "questions": out}


KRITIK = {"critical": True}


def eger(code, *answers):
    """Alt soru: <code> sorusunun cevabı verilenlerden biriyse göster."""
    return {"q": code, "op": "in", "value": list(answers)}


GENEL = {
    "key": "genel",
    "name": "Genel Tedarikçi Denetimi",
    "standard": "Genel",
    "description": "Kalite, İSG, çevre, bilgi güvenliği, KVKK, süreklilik ve etik konularını kapsayan genel değerlendirme.",
    "sections": [
        {"id": sec["id"], "title": sec["title"], "weight": sec["weight"],
         "questions": [{"code": q, "text": t, "critical": q == "etik.2"} for q, t in sec["questions"]]
         + ([{"code": "kalite.1a", "text": "Sertifika yoksa: belgelenmiş kalite el kitabı, prosedürler ve kayıtlar mevcut mu?",
              "condition": eger("kalite.1", "kısmen", "uygun değil")}] if sec["id"] == "kalite" else [])
         + ([{"code": "bg.1a", "text": "Sertifika/politika yoksa: son 12 ayda bağımsız güvenlik değerlendirmesi (SOC 2, sızma testi) yapılmış mı?",
              "condition": eger("bg.1", "kısmen", "uygun değil")}] if sec["id"] == "bg" else [])}
        for sec in CHECKLIST
    ],
}

PCI_DSS = {
    "key": "pci-dss-4",
    "name": "PCI DSS v4.0",
    "standard": "PCI DSS",
    "description": "Kart sahibi verisini işleyen, saklayan veya ileten tedarikçiler (ödeme altyapısı, çağrı merkezi, barındırma vb.) için 12 gereksinim.",
    "sections": [
        _s("kapsam", "Kapsam ve Uyum Kanıtı", 10,
           "Güncel bir PCI DSS Uyum Beyanı (AOC) veya QSA raporu (ROC) sunuldu mu?",
           ("AOC/ROC yoksa: imzalı ve güncel bir öz değerlendirme anketi (SAQ) sunuldu mu?",
            {"code": "kapsam.1a", "condition": eger("kapsam.1", "kısmen", "uygun değil")}),
           "Kart verisi ortamının (CDE) kapsamı ve veri akış diyagramı belgelenmiş mi?",
           "Sorumluluk matrisi (tedarikçi / müşteri hangi gereksinimden sorumlu) yazılı olarak tanımlı mı? (12.8.5, 12.9)"),
        _s("r1", "Gereksinim 1 — Ağ Güvenliği Kontrolleri", 8,
           "CDE, güvenlik duvarı / ağ güvenlik kontrolleriyle diğer ağlardan ayrılmış mı? (1.2, 1.3)",
           "Kural setleri en az 6 ayda bir gözden geçiriliyor mu? (1.2.7)",
           "İnternetten CDE'ye doğrudan erişim engellenmiş mi? (1.4)"),
        _s("r2", "Gereksinim 2 — Güvenli Yapılandırma", 6,
           "Varsayılan parolalar ve gereksiz servisler/hesaplar kaldırılıyor mu? (2.2.2, 2.2.4)",
           "Sistemler için sıkılaştırma (hardening) standartları uygulanıyor mu? (2.2.1)"),
        _s("r3", "Gereksinim 3 — Saklanan Hesap Verisinin Korunması", 10,
           ("Hassas kimlik doğrulama verisi (CVV, PIN, tam iz verisi) yetkilendirme sonrası saklanmıyor mu? (3.3)", KRITIK),
           "PAN saklanıyorsa okunamaz hale getiriliyor mu (şifreleme, tokenizasyon, hash)? (3.5)",
           "PAN ekranlarda maskeleniyor mu? (3.4)",
           "Şifreleme anahtarları için anahtar yönetimi prosedürleri var mı? (3.6, 3.7)",
           "Veri saklama süreleri tanımlı ve süresi dolan veri siliniyor mu? (3.2)"),
        _s("r4", "Gereksinim 4 — İletimde Şifreleme", 6,
           "Açık ağlarda kart verisi güçlü kriptografi (TLS 1.2+) ile iletiliyor mu? (4.2.1)",
           "PAN e-posta, anlık mesajlaşma gibi kanallarla şifresiz gönderilmiyor mu? (4.2.2)"),
        _s("r5", "Gereksinim 5 — Kötü Amaçlı Yazılımlara Karşı Koruma", 5,
           "Tüm uygun sistemlerde güncel anti-malware çözümü çalışıyor mu? (5.2, 5.3)",
           "Oltalama (phishing) saldırılarına karşı teknik önlemler var mı? (5.4.1)"),
        _s("r6", "Gereksinim 6 — Güvenli Sistem ve Yazılım", 8,
           "Kritik güvenlik yamaları 1 ay içinde uygulanıyor mu? (6.3.3)",
           "Yazılım geliştirme güvenli kodlama eğitimine ve kod incelemesine dayanıyor mu? (6.2)",
           "Halka açık web uygulamaları WAF veya düzenli güvenlik testi ile korunuyor mu? (6.4)",
           "Ödeme sayfalarındaki betikler envanterde ve bütünlükleri izleniyor mu? (6.4.3)"),
        _s("r7", "Gereksinim 7 — Bilmesi Gereken Kadar Erişim", 6,
           "Kart verisine erişim rol bazlı ve en az yetki ilkesine göre verilmiş mi? (7.2)",
           "Kullanıcı hesapları ve yetkileri en az 6 ayda bir gözden geçiriliyor mu? (7.2.4)"),
        _s("r8", "Gereksinim 8 — Kimlik Tanımlama ve Doğrulama", 8,
           "Her kullanıcının benzersiz bir kimliği var mı, paylaşılan hesap kullanılmıyor mu? (8.2)",
           ("CDE'ye tüm erişimlerde çok faktörlü kimlik doğrulama (MFA) kullanılıyor mu? (8.4)", {"critical": True, "weight": 2}),
           "Parola politikası (en az 12 karakter, kilitleme) uygulanıyor mu? (8.3)",
           "İşten ayrılanların erişimleri derhal kaldırılıyor mu? (8.2.5)"),
        _s("r9", "Gereksinim 9 — Fiziksel Erişim", 5,
           "Veri merkezi / sistem odasına fiziksel erişim kontrol ediliyor ve kaydediliyor mu? (9.2, 9.3)",
           "Kart verisi içeren medya güvenli saklanıyor ve imha ediliyor mu? (9.4)",
           "POS / kart okuyucu cihazlar kurcalamaya karşı düzenli kontrol ediliyor mu? (9.5)"),
        _s("r10", "Gereksinim 10 — Kayıt ve İzleme", 8,
           "CDE'deki tüm erişimler ve yönetici işlemleri loglanıyor mu? (10.2)",
           "Loglar günlük olarak (tercihen otomatik) inceleniyor mu? (10.4)",
           "Loglar en az 12 ay saklanıyor, son 3 ayı hemen erişilebilir mi? (10.5)",
           "Zaman senkronizasyonu (NTP) yapılandırılmış mı? (10.6)"),
        _s("r11", "Gereksinim 11 — Düzenli Güvenlik Testleri", 8,
           "Çeyreklik iç ve ASV onaylı dış zafiyet taramaları yapılıyor mu? (11.3)",
           "Yıllık sızma testi (ve segmentasyon testi) yapılıyor mu? (11.4)",
           "Kablosuz erişim noktaları için çeyreklik kontrol yapılıyor mu? (11.2)",
           "Kritik dosyalar için değişiklik tespiti (FIM) var mı? (11.5.2)"),
        _s("r12", "Gereksinim 12 — Politika ve Programlar", 6,
           "Bilgi güvenliği politikası yılda bir gözden geçiriliyor mu? (12.1)",
           "Yıllık risk analizi yapılıyor mu? (12.3)",
           "Çalışanlara yıllık güvenlik farkındalık eğitimi veriliyor mu? (12.6)",
           "Olay müdahale planı mevcut ve yılda bir test ediliyor mu? (12.10)",
           "Kendi alt hizmet sağlayıcılarının PCI DSS uyumunu izliyor mu? (12.8)"),
    ],
}

ISO27001 = {
    "key": "iso-27001-2022",
    "name": "ISO/IEC 27001:2022",
    "standard": "ISO 27001",
    "description": "Bilgi güvenliği yönetim sistemi (BGYS) maddeleri ve Ek-A kontrolleri (organizasyonel, insan, fiziksel, teknolojik).",
    "sections": [
        _s("bgys", "BGYS Gereklilikleri (Madde 4–10)", 20,
           "Geçerli ISO 27001 sertifikası var mı, kapsamı sunulan hizmetleri içeriyor mu?",
           ("Sertifika yoksa: son 12 ayda bağımsız bir güvenlik değerlendirmesi (SOC 2 raporu, sızma testi) sunuldu mu?",
            {"code": "bgys.1a", "condition": eger("bgys.1", "kısmen", "uygun değil")}),
           "Uygulanabilirlik Bildirgesi (SoA) güncel mi?",
           "Bilgi güvenliği risk değerlendirmesi ve risk işleme planı mevcut mu? (6.1)",
           "Bilgi güvenliği hedefleri belirlenmiş ve izleniyor mu? (6.2)",
           "İç denetim ve yönetimin gözden geçirmesi planlı aralıklarla yapılıyor mu? (9.2, 9.3)",
           "Uygunsuzluklar için düzeltici faaliyetler yürütülüyor mu? (10.2)"),
        _s("a5", "Ek-A 5 — Organizasyonel Kontroller", 25,
           "Bilgi güvenliği politikaları onaylanmış ve duyurulmuş mu? (A.5.1)",
           "Varlık envanteri ve sahipleri tanımlı mı? (A.5.9)",
           "Erişim kontrol politikası ve yetki gözden geçirmeleri var mı? (A.5.15, A.5.18)",
           "Tedarikçi ilişkilerinde güvenlik gereksinimleri sözleşmelere yansıtılmış mı? (A.5.19–A.5.22)",
           "Bulut hizmetleri kullanımı için güvenlik gereklilikleri tanımlı mı? (A.5.23)",
           "Bilgi güvenliği olay yönetimi süreci işletiliyor mu? (A.5.24–A.5.28)",
           "İş sürekliliği sırasında bilgi güvenliği ve BİT hazırlığı planlanmış mı? (A.5.29, A.5.30)",
           "Yasal gereklilikler ve kişisel veri koruma yükümlülükleri belirlenmiş mi? (A.5.31, A.5.34)",
           "Tehdit istihbaratı toplanıyor ve değerlendiriliyor mu? (A.5.7)"),
        _s("a6", "Ek-A 6 — İnsan Kaynakları Kontrolleri", 10,
           "İşe alımda geçmiş taraması yapılıyor mu? (A.6.1)",
           "Gizlilik sözleşmeleri imzalatılıyor mu? (A.6.6)",
           "Düzenli bilgi güvenliği farkındalık eğitimleri veriliyor mu? (A.6.3)",
           "Uzaktan çalışma için güvenlik kuralları tanımlı mı? (A.6.7)"),
        _s("a7", "Ek-A 7 — Fiziksel Kontroller", 10,
           "Fiziksel güvenlik çevresi ve giriş kontrolleri uygulanıyor mu? (A.7.1, A.7.2)",
           "Tesisler fiziksel olarak izleniyor mu (kamera, alarm)? (A.7.4)",
           "Temiz masa / temiz ekran kuralı uygulanıyor mu? (A.7.7)",
           "Ekipman ve depolama ortamları güvenli şekilde imha ediliyor mu? (A.7.10, A.7.14)"),
        _s("a8", "Ek-A 8 — Teknolojik Kontroller", 35,
           "Uç nokta cihazları yönetiliyor ve şifreleniyor mu? (A.8.1)",
           "Ayrıcalıklı erişimler sınırlı ve izleniyor mu? (A.8.2)",
           "Güvenli kimlik doğrulama (MFA) uygulanıyor mu? (A.8.5)",
           "Kötü amaçlı yazılıma karşı koruma mevcut mu? (A.8.7)",
           "Teknik zafiyet yönetimi ve yama süreci işletiliyor mu? (A.8.8)",
           "Yapılandırma yönetimi uygulanıyor mu? (A.8.9)",
           ("Yedekleme yapılıyor ve geri yükleme test ediliyor mu? (A.8.13)", {"weight": 2}),
           "Loglama ve izleme yapılıyor mu? (A.8.15, A.8.16)",
           "Ağ güvenliği ve ağ ayrıştırması sağlanmış mı? (A.8.20, A.8.22)",
           "Kriptografi kullanımı ve anahtar yönetimi tanımlı mı? (A.8.24)",
           "Güvenli yazılım geliştirme yaşam döngüsü uygulanıyor mu? (A.8.25–A.8.29)",
           "Veri sızıntısı önleme (DLP) ve veri maskeleme önlemleri var mı? (A.8.11, A.8.12)"),
    ],
}

ISO9001 = {
    "key": "iso-9001-2015",
    "name": "ISO 9001:2015",
    "standard": "ISO 9001",
    "description": "Kalite yönetim sistemi — bağlam, liderlik, planlama, destek, operasyon, performans ve iyileştirme.",
    "sections": [
        _s("m4", "Madde 4 — Kuruluşun Bağlamı", 10,
           "Geçerli ISO 9001 sertifikası var mı, kapsamı tedarik edilen ürün/hizmeti içeriyor mu?",
           "İlgili tarafların ihtiyaç ve beklentileri belirlenmiş mi? (4.2)",
           "KYS prosesleri ve etkileşimleri tanımlı mı? (4.4)"),
        _s("m5", "Madde 5 — Liderlik", 10,
           "Kalite politikası yayımlanmış ve çalışanlar tarafından biliniyor mu? (5.2)",
           "Görev, yetki ve sorumluluklar tanımlanmış mı? (5.3)",
           "Müşteri odaklılık üst yönetim tarafından sağlanıyor mu? (5.1.2)"),
        _s("m6", "Madde 6 — Planlama", 10,
           "Riskler ve fırsatlar belirlenip ele alınıyor mu? (6.1)",
           "Ölçülebilir kalite hedefleri belirlenmiş ve izleniyor mu? (6.2)"),
        _s("m7", "Madde 7 — Destek", 15,
           "Ölçüm ekipmanları kalibre ve izlenebilir mi? (7.1.5)",
           "Personel yetkinlikleri belirlenmiş ve eğitim kayıtları tutuluyor mu? (7.2)",
           "Doküman ve kayıt kontrolü uygulanıyor mu? (7.5)",
           "Altyapı ve çalışma ortamı uygun mu? (7.1.3, 7.1.4)"),
        _s("m8", "Madde 8 — Operasyon", 30,
           "Müşteri gereklilikleri sipariş öncesi gözden geçiriliyor mu? (8.2.3)",
           "Tasarım ve geliştirme süreci kontrollü mü? (8.3)",
           "Kendi tedarikçileri değerlendiriliyor ve izleniyor mu? (8.4)",
           "Üretim/hizmet sunumu kontrollü koşullarda yapılıyor mu (talimatlar, kontrol planları)? (8.5.1)",
           "Tanımlama ve izlenebilirlik (lot/seri) sağlanıyor mu? (8.5.2)",
           "Ürün sevkiyat öncesi doğrulanıyor mu? (8.6)",
           "Uygun olmayan çıktılar tanımlanıp ayrıştırılıyor mu? (8.7)"),
        _s("m9", "Madde 9 — Performans Değerlendirme", 15,
           "Müşteri memnuniyeti izleniyor mu? (9.1.2)",
           "İç denetimler planlı yapılıyor mu? (9.2)",
           "Yönetimin gözden geçirmesi yapılıyor mu? (9.3)"),
        _s("m10", "Madde 10 — İyileştirme", 10,
           "Uygunsuzluklar için kök neden analizi ve DÖF yürütülüyor mu? (10.2)",
           "Sürekli iyileştirme faaliyetleri kanıtlanabiliyor mu? (10.3)"),
    ],
}

ISO14001 = {
    "key": "iso-14001-2015",
    "name": "ISO 14001:2015",
    "standard": "ISO 14001",
    "description": "Çevre yönetim sistemi — çevre boyutları, yasal uyum, operasyonel kontrol ve acil durum hazırlığı.",
    "sections": [
        _s("sistem", "Sistem ve Liderlik", 20,
           "Geçerli ISO 14001 sertifikası var mı?",
           "Çevre politikası yayımlanmış mı? (5.2)",
           "Çevre hedefleri belirlenmiş ve izleniyor mu? (6.2)"),
        _s("boyut", "Çevre Boyutları ve Uyum", 30,
           "Önemli çevre boyutları ve etkileri belirlenmiş mi? (6.1.2)",
           "Yasal ve diğer şartlar belirlenip uyum değerlendiriliyor mu? (6.1.3, 9.1.2)",
           "Gerekli çevre izinleri (emisyon, deşarj, atık) güncel mi?"),
        _s("operasyon", "Operasyonel Kontrol", 35,
           "Atıklar ayrıştırılıyor ve lisanslı firmalarla bertaraf ediliyor mu? (8.1)",
           "Kimyasallar uygun depolanıyor ve etiketleniyor mu?",
           "Enerji, su ve emisyon tüketimi izleniyor mu? (9.1)",
           "Acil durumlar (döküntü, yangın) için hazırlık ve tatbikat yapılıyor mu? (8.2)"),
        _s("iyilestirme", "Değerlendirme ve İyileştirme", 15,
           "İç denetim ve yönetimin gözden geçirmesi yapılıyor mu? (9.2, 9.3)",
           "Çevre olayları için düzeltici faaliyet yürütülüyor mu? (10.2)"),
    ],
}

ISO45001 = {
    "key": "iso-45001-2018",
    "name": "ISO 45001:2018",
    "standard": "ISO 45001",
    "description": "İş sağlığı ve güvenliği yönetim sistemi — tehlike tanımlama, çalışan katılımı, operasyonel kontrol, olay yönetimi.",
    "sections": [
        _s("sistem", "Sistem, Liderlik ve Katılım", 20,
           "Geçerli ISO 45001 sertifikası var mı?",
           "İSG politikası yayımlanmış mı? (5.2)",
           "Çalışanların danışma ve katılımı sağlanıyor mu (İSG kurulu vb.)? (5.4)"),
        _s("risk", "Tehlike Tanımlama ve Risk Değerlendirmesi", 30,
           ("Tehlikeler tanımlanmış ve risk değerlendirmesi güncel mi? (6.1.2)", {"critical": True, "weight": 2}),
           "Yasal İSG yükümlülükleri (iş güvenliği uzmanı, işyeri hekimi) karşılanıyor mu? (6.1.3)",
           "Kontrol hiyerarşisine göre önlemler alınmış mı? (8.1.2)"),
        _s("operasyon", "Operasyonel Kontrol", 30,
           "Çalışanların İSG eğitimleri ve sağlık muayeneleri güncel mi? (7.2)",
           "KKD sağlanıyor ve kullanımı denetleniyor mu?",
           "Yüksekte çalışma, kapalı alan, sıcak iş gibi riskli işler için izin sistemi var mı?",
           "Alt yükleniciler İSG açısından yönetiliyor mu? (8.1.4)",
           "Acil durum planı ve tatbikatlar yapılıyor mu? (8.2)"),
        _s("olay", "Olay Yönetimi ve İyileştirme", 20,
           "İş kazaları ve ramak kala olaylar kaydediliyor ve analiz ediliyor mu? (10.2)",
           "Kaza sıklık / ağırlık oranları izleniyor mu? (9.1)",
           "İç denetim ve yönetimin gözden geçirmesi yapılıyor mu? (9.2, 9.3)"),
    ],
}

KVKK = {
    "key": "kvkk",
    "name": "KVKK Veri İşleyen Denetimi",
    "standard": "KVKK",
    "description": "6698 sayılı KVKK kapsamında kişisel veri işleyen tedarikçiler için idari ve teknik tedbirler.",
    "sections": [
        _s("hukuki", "Hukuki Yükümlülükler", 35,
           ("Veri işleyen sözleşmesi imzalanmış ve işleme amacı/kapsamı tanımlı mı?", KRITIK),
           "Kişisel veri işleme envanteri hazırlanmış mı?",
           "VERBİS kaydı (yükümlü ise) yapılmış mı?",
           "Yurt dışına veri aktarımı varsa yasal dayanağı (standart sözleşme vb.) mevcut mu?",
           ("Yasal dayanak yoksa: aktarım durdurulmuş veya Kurul'a bildirim yapılmış mı?",
            {"code": "hukuki.4a", "condition": eger("hukuki.4", "kısmen", "uygun değil")}),
           "Saklama ve imha politikası uygulanıyor mu?",
           "Alt veri işleyenler kullanılıyorsa onay alınmış ve sözleşmeyle bağlanmış mı?"),
        _s("idari", "İdari Tedbirler", 25,
           "Çalışanlara KVKK eğitimi verilmiş ve gizlilik taahhüdü alınmış mı?",
           "Kişisel verilere erişim yetki matrisi tanımlı mı?",
           "Veri ihlali bildirim süreci (72 saat) tanımlı mı?"),
        _s("teknik", "Teknik Tedbirler", 40,
           "Kişisel veriler aktarımda ve saklamada şifreleniyor mu?",
           "Erişimler loglanıyor ve izleniyor mu?",
           "Yedekleme yapılıyor ve yedekler korunuyor mu?",
           "Zafiyet taraması / sızma testi yapılıyor mu?",
           "Özel nitelikli kişisel veriler için ek tedbirler (Kurul kararı) uygulanıyor mu?"),
    ],
}

BUILTIN_SETS = [GENEL, ISO9001, ISO27001, PCI_DSS, ISO14001, ISO45001, KVKK]
