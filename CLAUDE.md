# Tedarikçi Denetim

**Stack:** Python 3.11+ · FastAPI · SQLite (stdlib `sqlite3`, ORM yok) · vanilla JS SPA (build adımı yok) · pytest · ruff

**Yapı**
- `app/main.py` — API uçları (tedarikçi, denetim, bulgu, istihbarat, pano)
- `app/db.py` — şema + `MIGRATIONS` (yeni sütun = buraya ekle; tablo silme/yeniden adlandırma yok)
- `app/auth.py` — kullanıcı, oturum, RBAC middleware, satır izolasyonu (`scope`)
- `app/templates_engine.py` — koşullu soru + puanlama/kill-switch (saf); `app/template_store.py` — şablon DB erişimi
- `app/capa.py`, `app/evidence.py`, `app/events.py`, `app/summary.py`, `app/notify.py` — servisler
- `app/lifecycle.py` — kabul/doğal risk, çıkarma, sözleşmeler
- `app/risk.py` — olgunluk ağırlıkları, finansal/ESG puanları, yaptırım taraması; `app/importer.py` — CSV/XLSX içe aktarma
- `app/reports/` — bağımsız rapor servisi (PDF/CSV, CLI: `python -m app.reports <id>`)
- `app/intel/scanner.py` — pasif dış istihbarat
- `static/` — SPA (`app.js`), tedarikçi yanıt sayfası (`yanit.html`)
- `tests/` — pytest; `data/` — çalışma verisi (git dışı)

**Komutlar**
- Çalıştır: `uvicorn app.main:app --reload` · örnek veri: `python -m app.seed`
- Test: `python -m pytest -q`
- Lint: `ruff check app tests`

**Kurallar**
- Yanıtlar kısa; uzun açıklama yazma, sadece yapılanı ve sonucu söyle.
- Gereksiz dosya oluşturma (özet/plan/not .md dosyası yok); mevcut dosyayı düzenlemeyi tercih et.
- Her yeni uç için yetki (`PERMS`) ve tedarikçi izolasyonu zorunlu; test ekle.
- İş kuralları saf fonksiyon olarak yazılır ve birim testi olur.
- Kullanıcıya görünen metinler Türkçe.
