@echo off
rem Запуск minikeys в окне консоли. Для работы в окнах "от администратора"
rem запускайте этот файл тоже от администратора (ПКМ -> Запуск от имени администратора).
chcp 65001 >nul
cd /d "%~dp0"
python -m minikeys %*
if errorlevel 1 pause
