@echo off
cd /d "%~dp0.."

echo === StockAutoTagger アプリ化 ===
echo.

if not exist "program\icon.png" (
    echo program\icon.png が見つかりません
    pause
    exit /b 1
)

if not exist "windows\icon.ico" (
    echo アイコンを変換中...
    python -c "from PIL import Image; Image.open('program/icon.png').save('windows/icon.ico', sizes=[(16,16),(32,32),(48,48),(64,64),(128,128),(256,256)])"
    if errorlevel 1 (
        echo アイコン変換に失敗しました。Pillow がインストールされていない可能性があります。
        echo   pip install -r requirements.txt
        pause
        exit /b 1
    )
)

set "ROOT=%cd%"
set "SHORTCUT=%ROOT%\StockAutoTagger.lnk"
set "WORKDIR=%ROOT%"
set "ICON=%ROOT%\windows\icon.ico"

powershell -NoProfile -Command "$ws = New-Object -ComObject WScript.Shell; $sc = $ws.CreateShortcut('%SHORTCUT%'); $sc.TargetPath = 'pythonw.exe'; $sc.Arguments = 'program\app.py'; $sc.WorkingDirectory = '%WORKDIR%'; $sc.IconLocation = '%ICON%'; $sc.Save(); Write-Host 'ショートカット作成完了'"

if errorlevel 1 (
    echo ショートカット作成に失敗しました
    pause
    exit /b 1
)

echo.
echo プロジェクトフォルダ直下に StockAutoTagger ショートカットを作成しました。
echo ダブルクリックで起動できます。
timeout /t 3 /nobreak
exit
