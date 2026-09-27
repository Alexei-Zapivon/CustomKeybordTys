# Автозапуск minikeys при входе в Windows (через Планировщик заданий, без окна консоли).
# Запускать в PowerShell ОТ АДМИНИСТРАТОРА:
#
#   powershell -ExecutionPolicy Bypass -File tools\autostart.ps1            # GUI, свёрнутый в трей
#   powershell -ExecutionPolicy Bypass -File tools\autostart.ps1 -Console   # консольная версия (config.toml)
#   powershell -ExecutionPolicy Bypass -File tools\autostart.ps1 -Remove    # выключить
#
# Задание выполняется с наивысшими правами — иначе SendInput не сможет
# нажимать клавиши в окнах, запущенных от администратора.
param([switch]$Remove, [switch]$Console)

$TaskName = 'minikeys'

if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Автозапуск '$TaskName' удалён."
    exit 0
}

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$PythonW = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
if (-not $PythonW) {
    Write-Error 'pythonw.exe не найден в PATH. Установите Python 3.11+ с галочкой "Add python.exe to PATH".'
    exit 1
}

$user = "$env:USERDOMAIN\$env:USERNAME"
if ($Console) {
    $arguments = "-m minikeys run --log-file `"$Root\minikeys.log`""
} else {
    $arguments = "-m minikeys.gui --minimized"   # GUI сам пишет лог в minikeys.log
}
$action = New-ScheduledTaskAction -Execute $PythonW -Argument $arguments -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "Готово: minikeys будет запускаться при входе. Лог: $Root\minikeys.log"
Write-Host "Запустить сейчас: Start-ScheduledTask -TaskName $TaskName"
