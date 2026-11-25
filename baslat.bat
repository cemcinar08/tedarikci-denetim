@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY=
for %%V in (3.13 3.12 3.11 3.10) do (
  if not defined PY ( py -%%V -c "import sys" >nul 2>nul && set "PY=py -%%V" )
)
if not defined PY ( python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul && set "PY=python" )
if not defined PY (
  echo HATA: Python 3.10 veya daha yeni surum bulunamadi.
  echo https://www.python.org/downloads/ adresinden kurun ^(kurulumda "Add Python to PATH" kutusunu isaretleyin^).
  start https://www.python.org/downloads/
  pause & exit /b 1
)
if exist .venv ( .venv\Scripts\python -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" >nul 2>nul || rmdir /s /q .venv )
if not exist .venv (
  echo Ilk kurulum yapiliyor, bu bir-iki dakika surebilir...
  %PY% -m venv .venv || (pause & exit /b 1)
  set FIRST=1
)
.venv\Scripts\python -m pip install -q --disable-pip-version-check -r requirements.txt || (echo Paketler yuklenemedi. & pause & exit /b 1)
if defined FIRST if not exist data\denetim.db .venv\Scripts\python -m app.seed
echo.
echo Uygulama baslatiliyor: http://localhost:8000   (kapatmak icin bu pencereyi kapatin)
start "" http://localhost:8000
.venv\Scripts\python -m uvicorn app.main:app --port 8000
echo.
echo Uygulama durdu. Yukaridaki hata mesajini kopyalayip gonderin.
pause
