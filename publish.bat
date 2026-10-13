@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "UPDATE_DIR=\\192.168.1.55\Genel Evrak\.encev_sistem\updates"
set "VERSION="
for /f "tokens=3" %%A in ('findstr /R /C:"APP_VERSION =" "enc_takip\version.py"') do set "VERSION=%%~A"
set "VERSION=%VERSION:"=%"
set "VERSION=%VERSION: =%"

if not defined VERSION (
    echo HATA: APP_VERSION okunamadi.
    exit /b 1
)

REM Kritik geçiş: yeni rollback destekli updater tüm bilgisayarlara kurulmadan
REM otomatik dağıtım yayımlanmasın. Bu dosyayı ancak manuel kurulum tamamlanınca oluşturun.
if not exist "UPDATER_V2_DEPLOYED.flag" (
    echo YAYIN ENGELLENDI.
    echo Once installer_out\ENCTakip_Kurulum.exe paketini tum kullanici bilgisayarlarina kurun.
    echo Guncellenmis updater'in her bilgisayara kuruldugunu dogrulayinca
    echo proje kokunde UPDATER_V2_DEPLOYED.flag dosyasini olusturup tekrar deneyin.
    exit /b 2
)

if not exist "dist\ENCTakip.exe" (echo HATA: dist\ENCTakip.exe yok. Once build.bat calistirin.& exit /b 1)
if not exist "dist\ENCTakipUpdater.exe" (echo HATA: dist\ENCTakipUpdater.exe yok. Once build.bat calistirin.& exit /b 1)
if not exist "installer_out\ENCTakip_Kurulum.exe" (echo HATA: Kurulum paketi yok. Once build.bat calistirin.& exit /b 1)

for %%F in ("dist\ENCTakip.exe" "dist\ENCTakipUpdater.exe" "installer_out\ENCTakip_Kurulum.exe") do if %%~zF LSS 1024 (echo HATA: Dosya bos/gecersiz: %%~fF& exit /b 1)

if not exist "%UPDATE_DIR%" mkdir "%UPDATE_DIR%"
if not exist "%UPDATE_DIR%" (echo HATA: Ortak updates klasorune erisilemiyor.& exit /b 1)

REM Mevcut manifest bu surum veya daha yeniyse ayni dosya adini asla ezme.
if exist "%UPDATE_DIR%\latest.json" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; try { $m=Get-Content -Raw -LiteralPath '%UPDATE_DIR%\latest.json' | ConvertFrom-Json; $old=[version]([string]$m.version) } catch { Write-Output ('HATA: Mevcut latest.json dogrulanamadi: '+$_.Exception.Message); exit 12 }; $new=[version]'%VERSION%'; if ($new -le $old) { Write-Output ('HATA: Yeni surum mevcut yayin surumunden buyuk olmali. Mevcut='+$old+' Yeni='+$new); exit 11 }"
    if errorlevel 1 (echo YAYIN DURDURULDU. Yukaridaki surum kontrolu hatasini inceleyin.& exit /b 3)
)

echo ENCTakip v%VERSION% yayin dosyalari kopyalaniyor...
copy /y "dist\ENCTakip.exe" "%UPDATE_DIR%\ENCTakip_%VERSION%.exe.tmp" >nul
if errorlevel 1 goto ERROR
fc /b "dist\ENCTakip.exe" "%UPDATE_DIR%\ENCTakip_%VERSION%.exe.tmp" >nul
if errorlevel 1 goto ERROR
move /y "%UPDATE_DIR%\ENCTakip_%VERSION%.exe.tmp" "%UPDATE_DIR%\ENCTakip_%VERSION%.exe" >nul
if errorlevel 1 goto ERROR

copy /y "dist\ENCTakipUpdater.exe" "%UPDATE_DIR%\ENCTakipUpdater_%VERSION%.exe.tmp" >nul
if errorlevel 1 goto ERROR
fc /b "dist\ENCTakipUpdater.exe" "%UPDATE_DIR%\ENCTakipUpdater_%VERSION%.exe.tmp" >nul
if errorlevel 1 goto ERROR
move /y "%UPDATE_DIR%\ENCTakipUpdater_%VERSION%.exe.tmp" "%UPDATE_DIR%\ENCTakipUpdater_%VERSION%.exe" >nul
if errorlevel 1 goto ERROR

copy /y "installer_out\ENCTakip_Kurulum.exe" "%UPDATE_DIR%\ENCTakip_Kurulum_%VERSION%.exe.tmp" >nul
if errorlevel 1 goto ERROR
fc /b "installer_out\ENCTakip_Kurulum.exe" "%UPDATE_DIR%\ENCTakip_Kurulum_%VERSION%.exe.tmp" >nul
if errorlevel 1 goto ERROR
move /y "%UPDATE_DIR%\ENCTakip_Kurulum_%VERSION%.exe.tmp" "%UPDATE_DIR%\ENCTakip_Kurulum_%VERSION%.exe" >nul
if errorlevel 1 goto ERROR

REM latest.json is the commit pointer: write temp file then rename it only after
REM the three versioned artifacts are already copied and verified.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; $d='%UPDATE_DIR%'; $v='%VERSION%'; $main=Join-Path $d ('ENCTakip_'+$v+'.exe'); $up=Join-Path $d ('ENCTakipUpdater_'+$v+'.exe'); $setup=Join-Path $d ('ENCTakip_Kurulum_'+$v+'.exe'); $m=[ordered]@{version=$v; path=$main; sha256=(Get-FileHash -LiteralPath $main -Algorithm SHA256).Hash.ToLowerInvariant(); updater_path=$up; updater_sha256=(Get-FileHash -LiteralPath $up -Algorithm SHA256).Hash.ToLowerInvariant(); installer_path=$setup; installer_sha256=(Get-FileHash -LiteralPath $setup -Algorithm SHA256).Hash.ToLowerInvariant()}; $json=$m | ConvertTo-Json; $tmp=Join-Path $d ('latest.json.'+[guid]::NewGuid().ToString('N')+'.tmp'); [IO.File]::WriteAllText($tmp,$json,(New-Object System.Text.UTF8Encoding($false))); Move-Item -LiteralPath $tmp -Destination (Join-Path $d 'latest.json') -Force"
if errorlevel 1 goto ERROR

echo.
echo ENCTakip v%VERSION% yayinlandi.
echo Manifest, yalnizca tum yayin dosyalari dogrulandiktan sonra degistirildi.
echo Not: updater helper guncellemeleri eski istemcilere otomatik uygulanmaz;
echo bu nedenle yukaridaki ilk manuel dagitim kontrolu zorunludur.
echo.
exit /b 0

:ERROR
echo.
echo HATA: Yayin tamamlanamadi. latest.json'in degismedigini ve dosya izinlerini kontrol edin.
exit /b 1
