@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul && (set PY=py -3) || (set PY=python)
%PY% --version >nul 2>nul || (
  echo Python bulunamadi. https://www.python.org/downloads/ adresinden kurun ^(kurulumda "Add Python to PATH" kutusunu isaretleyin^), sonra bu dosyayi tekrar calistirin.
  start https://www.python.org/downloads/
  pause & exit /b 1
)
if not exist .venv (
  echo Ilk kurulum yapiliyor, bu bir-iki dakika surebilir...
  %PY% -m venv .venv || (pause & exit /b 1)
  .venv\Scripts\python -m pip install -q -r requirements.txt || (pause & exit /b 1)
  .venv\Scripts\python -m app.seed
)
.venv\Scripts\python -m pip install -q -r requirements.txt
echo.
echo Uygulama baslatiliyor: http://localhost:8000   (kapatmak icin bu pencereyi kapatin)
start "" http://localhost:8000
.venv\Scripts\python -m uvicorn app.main:app --port 8000
pause
