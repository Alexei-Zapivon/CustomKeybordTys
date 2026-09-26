# Показывает все подключённые клавиатуры: имя, Hardware ID (VID/PID), USB-порт.
# Работает без драйвера Interception и без прав администратора.
#
#   powershell -ExecutionPolicy Bypass -File tools\list-keyboards.ps1
#
# Приём: запустите, отключите мини-клавиатуру, запустите ещё раз —
# пропавшая строка и есть она. Нужная часть ID — "VID_xxxx&PID_yyyy".

function Get-Prop($id, $key) {
    (Get-PnpDeviceProperty -InstanceId $id -KeyName $key -ErrorAction SilentlyContinue).Data
}

# Поднимаемся по дереву устройств до USB-узла, у которого есть номер порта/хаба
function Get-UsbLocation($id) {
    for ($i = 0; $i -lt 5 -and $id; $i++) {
        $loc = Get-Prop $id 'DEVPKEY_Device_LocationInfo'
        if ($loc -match 'Port_#') { return $loc }
        $id = Get-Prop $id 'DEVPKEY_Device_Parent'
    }
    return ''
}

Get-PnpDevice -Class Keyboard -PresentOnly | ForEach-Object {
    $hw = Get-Prop $_.InstanceId 'DEVPKEY_Device_HardwareIds'
    $vidpid = if ($hw -and $hw[0] -match '(VID_[0-9A-F]{4}&PID_[0-9A-F]{4})') { $Matches[1] }
              elseif ($hw -and $hw[0] -match '(VID&[0-9A-F]{8}_PID&[0-9A-F]{4})') { $Matches[1] }  # Bluetooth
              else { '' }
    [pscustomobject]@{
        'Имя'         = $_.FriendlyName
        'VID/PID'     = $vidpid
        'Hardware ID' = if ($hw) { $hw[0] } else { '' }
        'Порт USB'    = Get-UsbLocation $_.InstanceId
        'Instance ID' = $_.InstanceId
    }
} | Format-List
