@echo off
rem Сборка dist\minikeys.exe (один файл, без окна консоли). Подробности: README, раздел "Сборка minikeys.exe".
chcp 65001 >nul
cd /d "%~dp0"

rem interception.dll нужна внутри exe. Если её нет в lib\x64, ищем рядом с проектом и в Загрузках.
if not exist lib\x64\interception.dll (
    echo Ищу interception.dll рядом с проектом и в папке Загрузки...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "$roots = @('%~dp0..', (Join-Path $env:USERPROFILE 'Downloads')); $f = Get-ChildItem -Path $roots -Recurse -Filter interception.dll -ErrorAction SilentlyContinue | Where-Object { $_.FullName -match '\\x64\\' } | Select-Object -First 1; if ($f) { New-Item -ItemType Directory -Force -Path 'lib\x64' | Out-Null; Copy-Item -LiteralPath $f.FullName -Destination 'lib\x64\interception.dll'; Write-Host ('Found: ' + $f.FullName) }"
)
if not exist lib\x64\interception.dll (
    echo Не нашёл interception.dll. Скопируйте файл library\x64\interception.dll из архива Interception
    echo в папку %~dp0lib\x64\ и запустите build.bat ещё раз.
    pause
    exit /b 1
)

echo [1/2] Устанавливаю PyInstaller и PySide6...
python -m pip install --upgrade pyinstaller PySide6
if errorlevel 1 (
    echo Не удалось установить пакеты. Проверьте интернет и что Python есть в PATH.
    pause
    exit /b 1
)

echo [2/2] Собираю minikeys.exe (1-3 минуты)...
python -m PyInstaller --noconfirm --clean minikeys.spec
if errorlevel 1 (
    echo Сборка не удалась, смотрите сообщения выше.
    pause
    exit /b 1
)

echo.
echo Готово: %~dp0dist\minikeys.exe
pause
