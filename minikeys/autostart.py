"""Автозагрузка вместе с Windows (галочка «Запускать вместе с Windows»).

Два способа, выбирается автоматически:
1. Программа запущена обычным пользователем: значение "minikeys" в реестре
   HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run. Права администратора не нужны,
   но и программа стартует без них: бинды не срабатывают в окнах, запущенных от
   администратора (Диспетчер задач, установщики, часть игр).
2. Программа запущена от администратора: задание Планировщика «minikeys» с
   наивысшими правами. Из ключа Run Windows НЕ запускает программы, которым нужны
   права администратора, поэтому для работы в «админских» окнах нужен именно Планировщик.

В обоих случаях запускается paths.launch_command(): сам minikeys.exe после сборки или
pythonw.exe MiniKeys.pyw при запуске из исходников, с флагом --autostart.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

from . import paths

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "minikeys"
TASK_NAME = "minikeys"
AUTOSTART_FLAG = "--autostart"

REGISTRY = "registry"
TASK = "task"


class AutostartError(RuntimeError):
    pass


def command() -> list[str]:
    return paths.launch_command() + [AUTOSTART_FLAG]


def command_line() -> str:
    return subprocess.list2cmdline(command())


def is_admin() -> bool:
    from .elevation import is_admin as _is_admin
    return _is_admin()


# --- реестр (HKCU\...\Run) ---------------------------------------------------------
def _registry_get() -> str | None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, VALUE_NAME)[0]
    except OSError:
        return None


def _registry_set(value: str) -> None:
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, value)


def _registry_delete() -> None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, VALUE_NAME)
    except FileNotFoundError:
        pass


# --- Планировщик заданий ---------------------------------------------------------
def _schtasks(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["schtasks", *args], capture_output=True, text=True,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _task_exists() -> bool:
    return _schtasks("/Query", "/TN", TASK_NAME).returncode == 0


def task_xml(cmd: list[str], user: str) -> str:
    """Описание задания: при входе пользователя, наивысшие права, без ограничения по времени.

    Через XML, потому что `schtasks /Create /SC ONLOGON` по умолчанию убивает задание
    через 72 часа и не запускает его от батареи.
    """
    exe, args = cmd[0], subprocess.list2cmdline(cmd[1:])
    workdir = str(Path(exe).parent) if paths.FROZEN else str(paths.SOURCE_ROOT)
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>minikeys: бинды мини-клавиатуры</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><UserId>{escape(user)}</UserId></LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{escape(user)}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>4</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{escape(exe)}</Command>
      <Arguments>{escape(args)}</Arguments>
      <WorkingDirectory>{escape(workdir)}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _task_create() -> None:
    user = f"{os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '')}".lstrip("\\")
    fd, name = tempfile.mkstemp(suffix=".xml")
    os.close(fd)
    try:
        Path(name).write_text(task_xml(command(), user), encoding="utf-16")
        result = _schtasks("/Create", "/TN", TASK_NAME, "/XML", name, "/F")
    finally:
        os.unlink(name)
    if result.returncode != 0:
        raise AutostartError(f"Планировщик заданий отказал: {(result.stderr or result.stdout).strip()}")


def _task_delete() -> None:
    result = _schtasks("/Delete", "/TN", TASK_NAME, "/F")
    if result.returncode != 0 and _task_exists():
        raise AutostartError("Автозапуск был включён от имени администратора. Чтобы выключить его, "
                             "запустите minikeys от имени администратора и снимите галочку ещё раз.")


# --- общий интерфейс -------------------------------------------------------------
def status() -> str | None:
    """TASK, REGISTRY или None (автозапуск выключен)."""
    if sys.platform != "win32":
        return None
    if _task_exists():
        return TASK
    if _registry_get():
        return REGISTRY
    return None


def enable() -> str:
    """Включить автозапуск. Возвращает способ: TASK (от администратора) или REGISTRY."""
    if sys.platform != "win32":
        raise AutostartError("автозапуск доступен только в Windows")
    if is_admin():
        _task_create()
        _registry_delete()  # чтобы не было двух способов сразу
        return TASK
    _registry_set(command_line())
    return REGISTRY


def disable() -> None:
    if sys.platform != "win32":
        return
    _registry_delete()
    if _task_exists():
        _task_delete()


def refresh() -> None:
    """Если программу перенесли в другую папку, поправить путь в реестре."""
    if sys.platform != "win32":
        return
    current = _registry_get()
    if current and current != command_line():
        _registry_set(command_line())
