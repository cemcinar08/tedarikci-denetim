@echo off
chcp 65001 >nul
cd /d "%~dp0"
rem ---- otomatik guncelleme (data ve .venv klasorlerine dokunmaz)
if "%1"=="--guncellendi" goto basla
if defined TD_NO_UPDATE goto basla
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ErrorActionPreference='Stop'; try { $r='cemcinar08/tedarikci-denetim';" ^
  "$sha=(Invoke-RestMethod -TimeoutSec 10 https://api.github.com/repos/$r/commits/main).sha;" ^
  "$cur=if(Test-Path .surum){(Get-Content .surum -Raw).Trim()}else{''};" ^
  "if($sha -and $sha -ne $cur){ Write-Host 'Yeni surum bulundu, guncelleniyor...'; $t=Join-Path $env:TEMP ('td'+[guid]::NewGuid());" ^
  "New-Item -ItemType Directory $t | Out-Null; Invoke-WebRequest -TimeoutSec 120 \"https://github.com/$r/archive/$sha.zip\" -OutFile \"$t\s.zip\";" ^
  "Expand-Archive \"$t\s.zip\" $t; $src=(Get-ChildItem $t -Directory)[0].FullName;" ^
  "robocopy $src . /E /XD data .venv .git /XF .surum /NFL /NDL /NJH /NJS | Out-Null; Set-Content .surum $sha; Remove-Item $t -Recurse -Force; exit 10 } } catch { Write-Host 'Guncelleme denetlenemedi, mevcut surumle devam.' }"
if %errorlevel%==10 ( call "%~f0" --guncellendi & exit /b )
:basla
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
