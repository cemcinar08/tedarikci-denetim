#!/bin/bash
# Mac / Linux: çift tıklayın (Mac) veya terminalde ./baslat.command
cd "$(dirname "$0")"
PY=$(command -v python3 || command -v python)
if [ -z "$PY" ]; then
  echo "Python bulunamadı. https://www.python.org/downloads/ adresinden kurun, sonra tekrar çalıştırın."
  read -p "Kapatmak için Enter'a basın"; exit 1
fi
if [ ! -d .venv ]; then
  echo "İlk kurulum yapılıyor, bu bir-iki dakika sürebilir..."
  "$PY" -m venv .venv && .venv/bin/python -m pip install -q -r requirements.txt || { read -p "Hata oluştu. Enter"; exit 1; }
  .venv/bin/python -m app.seed
fi
echo "Uygulama başlatılıyor: http://localhost:8000  (kapatmak için bu pencereyi kapatın)"
( sleep 2; open http://localhost:8000 2>/dev/null || xdg-open http://localhost:8000 2>/dev/null ) &
.venv/bin/python -m uvicorn app.main:app --port 8000
