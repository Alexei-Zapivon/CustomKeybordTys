@echo off
rem Сборка dist\minikeys.exe (один файл, без окна консоли). Подробности: README, раздел "Сборка minikeys.exe".
chcp 65001 >nul
cd /d "%~dp0"

if not exist lib\x64\interception.dll (
    echo Нет lib\x64\interception.dll. Скопируйте его из архива Interception: library\x64\interception.dll
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
