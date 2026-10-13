@echo off
setlocal EnableExtensions
cd /d "%~dp0"

set "APP_VERSION="
set "STAGE="
set "BACKUP_DIR="
set "COMMIT_STARTED=0"
set "HAD_DIST=0"
set "HAD_INSTALLER_OUT=0"
set "HAD_ASSETS=0"
set "ASSET_BACKUP_READY=0"

for /f "tokens=3" %%A in ('findstr /R /C:"APP_VERSION =" "enc_takip\version.py"') do set "APP_VERSION=%%~A"
set "APP_VERSION=%APP_VERSION:"=%"
set "APP_VERSION=%APP_VERSION: =%"

if not defined APP_VERSION (
    echo HATA: APP_VERSION okunamadi.
    goto ERROR
)

if not exist "main.py" (echo HATA: main.py bulunamadi.& goto ERROR)
if not exist "enc_takip\updater.py" (echo HATA: enc_takip\updater.py bulunamadi. Once guvenli yama uygulanmali.& goto ERROR)
if not exist "requirements.txt" (echo HATA: requirements.txt bulunamadi.& goto ERROR)
if not exist "prepare_assets.py" (echo HATA: prepare_assets.py bulunamadi.& goto ERROR)
if not exist "installer\ENCTakip.iss" (echo HATA: installer\ENCTakip.iss bulunamadi.& goto ERROR)

for /f %%T in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "STAMP=%%T"
set "STAGE=%TEMP%\ENCTakipBuild_%RANDOM%_%RANDOM%"
set "BACKUP_DIR=%CD%\.build_backups\%STAMP%_%RANDOM%"
mkdir "%BACKUP_DIR%" 2>nul
if errorlevel 1 goto ERROR
if exist "assets" set "HAD_ASSETS=1"
if "%HAD_ASSETS%"=="1" (
    xcopy "assets\*" "%BACKUP_DIR%\assets\" /E /I /H /Y >nul
    if errorlevel 2 (echo HATA: Asset yedegi alinamadi.& goto ERROR)
    set "ASSET_BACKUP_READY=1"
)

mkdir "%STAGE%\dist" 2>nul
mkdir "%STAGE%\installer_out" 2>nul
if errorlevel 1 goto ERROR

echo ========================================
echo ENCTakip guvenli derleme
echo Surum: %APP_VERSION%
echo ========================================
echo Gecici derleme klasoru: %STAGE%
echo Mevcut dist / kurulum dosyalari derleme basinda silinmeyecek.
echo.

echo [1/6] Python paketleri...
py -3 -m pip install -r requirements.txt
if errorlevel 1 goto ERROR

echo.
echo [2/6] Asset hazirligi...
py -3 prepare_assets.py
if errorlevel 1 goto ERROR
if not exist "assets\ENCTakip.ico" (echo HATA: assets\ENCTakip.ico bulunamadi.& goto ERROR)
if not exist "assets\encev_logo.png" (echo HATA: assets\encev_logo.png bulunamadi.& goto ERROR)

echo.
echo [3/6] Ana EXE (gecici klasore)...
py -3 -m PyInstaller --noconfirm --clean --onefile --noconsole --name ENCTakip --distpath "%STAGE%\dist" --workpath "%STAGE%\work_main" --specpath "%STAGE%\spec_main" --icon="assets\ENCTakip.ico" --add-data "assets\encev_logo.png;assets" --hidden-import=enc_takip.gui.app main.py
if errorlevel 1 goto ERROR
if not exist "%STAGE%\dist\ENCTakip.exe" (echo HATA: Ana EXE olusmadi.& goto ERROR)
for %%F in ("%STAGE%\dist\ENCTakip.exe") do if %%~zF LSS 1024 (echo HATA: Ana EXE dosyasi bos/gecersiz.& goto ERROR)

echo.
echo [4/6] Updater EXE (gecici klasore)...
py -3 -m PyInstaller --noconfirm --clean --onefile --noconsole --name ENCTakipUpdater --distpath "%STAGE%\dist" --workpath "%STAGE%\work_updater" --specpath "%STAGE%\spec_updater" enc_takip\updater.py
if errorlevel 1 goto ERROR
if not exist "%STAGE%\dist\ENCTakipUpdater.exe" (echo HATA: Updater EXE olusmadi.& goto ERROR)
for %%F in ("%STAGE%\dist\ENCTakipUpdater.exe") do if %%~zF LSS 1024 (echo HATA: Updater EXE dosyasi bos/gecersiz.& goto ERROR)

echo.
echo [5/6] Kurulum paketi (gecici klasore)...
set "ISCC=C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" set "ISCC=C:\Program Files\Inno Setup 6\ISCC.exe"
if not exist "%ISCC%" (echo HATA: Inno Setup bulunamadi. Her iki standart kurulum yolu da kontrol edildi.& goto ERROR)
"%ISCC%" /DAPP_VERSION=%APP_VERSION% "/DBuildDistDir=%STAGE%\dist" "/O%STAGE%\installer_out" "%~dp0installer\ENCTakip.iss"
if errorlevel 1 goto ERROR
if not exist "%STAGE%\installer_out\ENCTakip_Kurulum.exe" (echo HATA: Kurulum EXE olusmadi.& goto ERROR)
for %%F in ("%STAGE%\installer_out\ENCTakip_Kurulum.exe") do if %%~zF LSS 1024 (echo HATA: Kurulum EXE dosyasi bos/gecersiz.& goto ERROR)

echo.
echo [6/6] Dogrulanmis dosyalarin guvenli yerlestirilmesi...
if exist "dist" set "HAD_DIST=1"
if exist "installer_out" set "HAD_INSTALLER_OUT=1"
if "%HAD_DIST%"=="1" (
    xcopy "dist\*" "%BACKUP_DIR%\dist\" /E /I /H /Y >nul
    if errorlevel 2 (echo HATA: Mevcut dist yedegi alinamadi.& goto ERROR)
)
if "%HAD_INSTALLER_OUT%"=="1" (
    xcopy "installer_out\*" "%BACKUP_DIR%\installer_out\" /E /I /H /Y >nul
    if errorlevel 2 (echo HATA: Mevcut installer_out yedegi alinamadi.& goto ERROR)
)

set "COMMIT_STARTED=1"
if not exist "dist" mkdir "dist"
if not exist "installer_out" mkdir "installer_out"
copy /y "%STAGE%\dist\ENCTakip.exe" "dist\ENCTakip.exe.new" >nul
if errorlevel 1 goto ERROR
copy /y "%STAGE%\dist\ENCTakipUpdater.exe" "dist\ENCTakipUpdater.exe.new" >nul
if errorlevel 1 goto ERROR
copy /y "%STAGE%\installer_out\ENCTakip_Kurulum.exe" "installer_out\ENCTakip_Kurulum.exe.new" >nul
if errorlevel 1 goto ERROR

move /y "dist\ENCTakip.exe.new" "dist\ENCTakip.exe" >nul
if errorlevel 1 goto ERROR
move /y "dist\ENCTakipUpdater.exe.new" "dist\ENCTakipUpdater.exe" >nul
if errorlevel 1 goto ERROR
move /y "installer_out\ENCTakip_Kurulum.exe.new" "installer_out\ENCTakip_Kurulum.exe" >nul
if errorlevel 1 goto ERROR

if not exist "dist\ENCTakip.exe" goto ERROR
if not exist "dist\ENCTakipUpdater.exe" goto ERROR
if not exist "installer_out\ENCTakip_Kurulum.exe" goto ERROR

echo.
echo ========================================
echo DERLEME VE KURULUM BASARILI
echo Surum: %APP_VERSION%
echo Ana EXE: %CD%\dist\ENCTakip.exe
echo Updater: %CD%\dist\ENCTakipUpdater.exe
echo Kurulum: %CD%\installer_out\ENCTakip_Kurulum.exe
echo Onceki dosyalarin yedegi: %BACKUP_DIR%
echo ========================================
if exist "%STAGE%" rmdir /s /q "%STAGE%"
goto END

:ERROR
echo.
echo ========================================
echo DERLEME BASARISIZ. Yukaridaki ilk hata satirini inceleyin.
echo ========================================
if "%COMMIT_STARTED%"=="1" (
    echo Onceki ciktilar geri yukleniyor...
    if exist "dist" rmdir /s /q "dist"
    if "%HAD_DIST%"=="1" if exist "%BACKUP_DIR%\dist" xcopy "%BACKUP_DIR%\dist\*" "dist\" /E /I /H /Y >nul
    if exist "installer_out" rmdir /s /q "installer_out"
    if "%HAD_INSTALLER_OUT%"=="1" if exist "%BACKUP_DIR%\installer_out" xcopy "%BACKUP_DIR%\installer_out\*" "installer_out\" /E /I /H /Y >nul
)
if "%ASSET_BACKUP_READY%"=="1" (
    echo Assetler onceki hallerine geri yukleniyor...
    if exist "assets" rmdir /s /q "assets"
    if "%HAD_ASSETS%"=="1" if exist "%BACKUP_DIR%\assets" xcopy "%BACKUP_DIR%\assets\*" "assets\" /E /I /H /Y >nul
)
if defined STAGE if exist "%STAGE%" echo Gecici derleme dosyalari hata incelemesi icin birakildi: %STAGE%
if defined BACKUP_DIR if exist "%BACKUP_DIR%" echo Yedek klasoru: %BACKUP_DIR%
echo Mevcut kaynak ve onceki EXE dosyalari bilerek otomatik silinmedi.

:END
echo.
pause
endlocal
