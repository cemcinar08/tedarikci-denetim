#!/bin/bash
# Mac / Linux başlatıcı:  Terminal'e "bash " yazıp bu dosyayı sürükleyin, Enter.
cd "$(dirname "$0")"

# ---------------------------------------------------------------- otomatik güncelleme
# GitHub'daki en son sürümü indirir. data/ (verileriniz) ve .venv/ asla değiştirilmez.
REPO="cemcinar08/tedarikci-denetim"
if [ "$1" != "--guncellendi" ] && [ -z "$TD_NO_UPDATE" ]; then
  LATEST=$(curl -fsS --max-time 10 "https://api.github.com/repos/$REPO/commits/main" 2>/dev/null | grep -m1 '"sha"' | sed -E 's/.*"sha": *"([0-9a-f]+)".*/\1/')
  CURRENT=$(cat .surum 2>/dev/null)
  if [ -n "$LATEST" ] && [ "$LATEST" != "$CURRENT" ]; then
    echo "Yeni sürüm bulundu, güncelleniyor..."
    TMP=$(mktemp -d)
    if curl -fsSL --max-time 120 "https://github.com/$REPO/archive/$LATEST.zip" -o "$TMP/s.zip" && unzip -q "$TMP/s.zip" -d "$TMP"; then
      SRC=$(find "$TMP" -mindepth 1 -maxdepth 1 -type d | head -1)
      if command -v rsync >/dev/null 2>&1; then
        rsync -a --delete --exclude data/ --exclude .venv/ --exclude .surum --exclude .git/ "$SRC"/ ./
      else
        rm -rf "$SRC/data" "$SRC/.venv"; cp -R "$SRC"/. ./
      fi && echo "$LATEST" > .surum && echo "Güncellendi."
    else
      echo "Güncelleme indirilemedi, mevcut sürümle devam ediliyor."
    fi
    rm -rf "$TMP"
    exec bash "$0" --guncellendi
  fi
fi

# Python 3.10 veya üstünü bul (macOS'in kendi python3'ü genelde 3.9'dur ve yetmez)
PY=""
for c in python3.13 python3.12 python3.11 python3.10 \
         /Library/Frameworks/Python.framework/Versions/3.13/bin/python3 \
         /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
         /Library/Frameworks/Python.framework/Versions/3.11/bin/python3 \
         /opt/homebrew/bin/python3 /usr/local/bin/python3 python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$(command -v "$c")"; break
  fi
done
if [ -z "$PY" ]; then
  echo ""
  echo "HATA: Python 3.10 veya daha yeni bir sürüm bulunamadı."
  echo "Bilgisayarınızdaki sürüm: $(python3 --version 2>&1)"
  echo "Çözüm: https://www.python.org/downloads/ adresinden güncel Python'u (macOS installer) kurun,"
  echo "       sonra bu Terminal penceresini kapatıp başlatmayı tekrar deneyin."
  open https://www.python.org/downloads/ 2>/dev/null
  read -p "Kapatmak için Enter'a basın"; exit 1
fi
echo "Kullanılan Python: $("$PY" --version)"

# Eski (3.10 altı) Python ile kurulmuş sanal ortam varsa yeniden kur
if [ -d .venv ] && ! .venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
  echo "Eski Python ile kurulmuş ortam bulundu, yeniden kuruluyor..."
  rm -rf .venv
fi
if [ ! -d .venv ]; then
  echo "İlk kurulum yapılıyor, bu bir-iki dakika sürebilir..."
  "$PY" -m venv .venv || { read -p "Sanal ortam kurulamadı. Enter"; exit 1; }
  FIRST=1
fi
.venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt || { echo "Paketler yüklenemedi (internet bağlantısını kontrol edin)."; read -p "Enter"; exit 1; }
[ -n "$FIRST" ] && [ ! -f data/denetim.db ] && .venv/bin/python -m app.seed

if lsof -ti tcp:8000 >/dev/null 2>&1; then
  echo "8000 portunda çalışan eski bir uygulama kapatılıyor..."
  kill $(lsof -ti tcp:8000) 2>/dev/null; sleep 1
fi
echo ""
echo "Uygulama başlatılıyor: http://localhost:8000   (kapatmak için bu pencereyi kapatın)"
( sleep 3; open http://localhost:8000 2>/dev/null || xdg-open http://localhost:8000 2>/dev/null ) &
.venv/bin/python -m uvicorn app.main:app --port 8000
echo ""
read -p "Uygulama durdu. Yukarıdaki hata mesajını kopyalayıp gönderin. Kapatmak için Enter."
