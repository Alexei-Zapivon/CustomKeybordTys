"""Права администратора: проверка и перезапуск себя с запросом UAC.

Зачем: Windows (UIPI) не пропускает синтетический ввод (SendInput) из обычной
программы в окна, запущенные от администратора. Если Discord, игра или
Диспетчер задач работают с правами администратора, minikeys тоже должна.

Как:
  * minikeys.exe собран с манифестом requireAdministrator (uac_admin=True в
    minikeys.spec), поэтому Windows сама спрашивает UAC при запуске exe;
  * при запуске из исходников main() вызывает relaunch_as_admin(): новый процесс
    запускается через ShellExecute с глаголом "runas" (окно UAC), а текущий завершается;
  * флаг --elevated защищает от бесконечного цикла, если повышение не удалось;
  * если пользователь нажал «Нет» в окне UAC, программа работает дальше без прав
    и показывает предупреждение в строке состояния.
"""

from __future__ import annotations

import ctypes
import logging
import subprocess
import sys

from . import paths

log = logging.getLogger("minikeys")

ELEVATED_FLAG = "--elevated"
SW_SHOWNORMAL = 1


def is_admin() -> bool:
    """Процесс запущен с правами администратора (повышенный токен)."""
    if sys.platform != "win32":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except OSError:
        return False


def elevation_command(argv: list[str]) -> tuple[str, str]:
    """(программа, параметры) для перезапуска: те же аргументы + --elevated."""
    cmd = paths.launch_command()
    args = [a for a in argv if a != ELEVATED_FLAG] + [ELEVATED_FLAG]
    return cmd[0], subprocess.list2cmdline(cmd[1:] + args)


def relaunch_as_admin(argv: list[str]) -> bool:
    """Запустить себя с запросом UAC. True: новый процесс запущен, текущий должен выйти."""
    if sys.platform != "win32":
        return False
    exe, params = elevation_command(argv)
    shell32 = ctypes.windll.shell32
    shell32.ShellExecuteW.restype = ctypes.c_void_p
    result = shell32.ShellExecuteW(None, "runas", exe, params, str(paths.app_dir()), SW_SHOWNORMAL)
    # ShellExecute возвращает число > 32 при успехе; 5 = пользователь отказал в UAC
    ok = (result or 0) > 32
    if not ok:
        log.warning("права администратора не получены (код %s), работаю без них", result)
    return ok
