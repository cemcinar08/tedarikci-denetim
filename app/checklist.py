"""Varsayılan tedarikçi denetim kontrol listesi ve puanlama.

Sorular buradan düzenlenebilir. `weight` bölümün toplam puandaki ağırlığıdır.
Cevap puanları: uygun = 1, kısmen = 0.5, uygun değil = 0, na = hesaba katılmaz.
"""

CHECKLIST = [
    {
        "id": "kalite", "title": "Kalite Yönetimi", "weight": 25,
        "questions": [
            ("kalite.1", "Geçerli bir ISO 9001 (veya sektörel eşdeğeri, ör. IATF 16949) sertifikası var mı?"),
            ("kalite.2", "Girdi, proses ve final kontrolleri tanımlı ve kayıt altında mı?"),
            ("kalite.3", "Ölçüm ekipmanları kalibre ve kalibrasyon kayıtları güncel mi?"),
            ("kalite.4", "Uygun olmayan ürün yönetimi ve izlenebilirlik (lot/seri) sağlanıyor mu?"),
            ("kalite.5", "Müşteri şikâyetleri için kök neden analizi ve DÖF süreci işletiliyor mu?"),
        ],
    },
    {
        "id": "isg", "title": "İş Sağlığı ve Güvenliği", "weight": 15,
        "questions": [
            ("isg.1", "Güncel risk değerlendirmesi ve acil durum planı mevcut mu?"),
            ("isg.2", "Çalışanların İSG eğitimleri ve sağlık muayeneleri güncel mi?"),
            ("isg.3", "KKD kullanımı sahada uygun şekilde sağlanıyor mu?"),
            ("isg.4", "İş kazaları ve ramak kala olaylar kayıt altına alınıp analiz ediliyor mu?"),
        ],
    },
    {
        "id": "cevre", "title": "Çevre ve Sürdürülebilirlik", "weight": 10,
        "questions": [
            ("cevre.1", "Gerekli çevre izinleri / ISO 14001 mevcut mu?"),
            ("cevre.2", "Atık yönetimi (ayrıştırma, lisanslı bertaraf) yapılıyor mu?"),
            ("cevre.3", "Enerji / emisyon verileri izleniyor mu?"),
        ],
    },
    {
        "id": "bg", "title": "Bilgi Güvenliği", "weight": 20,
        "questions": [
            ("bg.1", "ISO 27001 sertifikası veya belgelenmiş bir bilgi güvenliği politikası var mı?"),
            ("bg.2", "Kullanıcı erişimleri rol bazlı yönetiliyor ve periyodik gözden geçiriliyor mu?"),
            ("bg.3", "Kritik sistemlerde çok faktörlü kimlik doğrulama (MFA) kullanılıyor mu?"),
            ("bg.4", "Düzenli yedekleme yapılıyor ve geri dönüş testleri gerçekleştiriliyor mu?"),
            ("bg.5", "Güvenlik olaylarına müdahale prosedürü ve bildirim süresi tanımlı mı?"),
            ("bg.6", "Yama ve zafiyet yönetimi süreci işletiliyor mu?"),
        ],
    },
    {
        "id": "kvkk", "title": "KVKK / Veri Koruma", "weight": 10,
        "questions": [
            ("kvkk.1", "Kişisel veri işleme envanteri ve aydınlatma metinleri hazır mı?"),
            ("kvkk.2", "Veri işleyen sözleşmesi / gizlilik taahhüdü imzalanmış mı?"),
            ("kvkk.3", "Yurt dışına veri aktarımı varsa yasal dayanağı mevcut mu?"),
        ],
    },
    {
        "id": "sureklilik", "title": "Tedarik Sürekliliği", "weight": 10,
        "questions": [
            ("sureklilik.1", "İş sürekliliği planı (BCP) mevcut ve test edilmiş mi?"),
            ("sureklilik.2", "Kritik hammadde / alt tedarikçiler için alternatif kaynak var mı?"),
            ("sureklilik.3", "Kapasite ve teslimat performansı (OTD) izleniyor mu?"),
        ],
    },
    {
        "id": "etik", "title": "Etik ve Uyum", "weight": 10,
        "questions": [
            ("etik.1", "Rüşvet ve yolsuzlukla mücadele politikası var mı?"),
            ("etik.2", "Çocuk işçi / zorla çalıştırma yasağına uyum doğrulanıyor mu?"),
            ("etik.3", "Vergi, SGK ve ticari sicil yükümlülükleri güncel mi?"),
        ],
    },
]

ANSWER_POINTS = {"uygun": 1.0, "kısmen": 0.5, "uygun değil": 0.0}


def all_question_ids() -> set[str]:
    return {qid for sec in CHECKLIST for qid, _ in sec["questions"]}


def question_text(qid: str) -> str:
    for sec in CHECKLIST:
        for q, text in sec["questions"]:
            if q == qid:
                return text
    return qid


def compute_score(answers: dict[str, str]) -> dict:
    """Cevaplardan bölüm puanlarını ve ağırlıklı toplam puanı (0-100) hesaplar."""
    sections = []
    total_w = 0.0
    total = 0.0
    for sec in CHECKLIST:
        pts = [ANSWER_POINTS[answers[q]] for q, _ in sec["questions"] if answers.get(q) in ANSWER_POINTS]
        if pts:
            pct = 100 * sum(pts) / len(pts)
            total += pct * sec["weight"]
            total_w += sec["weight"]
        else:
            pct = None
        sections.append({"id": sec["id"], "title": sec["title"], "score": pct, "answered": len(pts)})
    overall = round(total / total_w, 1) if total_w else None
    return {"score": overall, "rating": rating(overall), "sections": sections}


def rating(score):
    if score is None:
        return None
    if score >= 85:
        return "Onaylı"
    if score >= 70:
        return "Şartlı Onaylı"
    if score >= 50:
        return "Gelişim Gerekli"
    return "Onaysız"
