"""Запуск графического интерфейса minikeys двойным щелчком (без окна консоли)."""

import importlib.util
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _fatal(text: str) -> None:
    # под pythonw.exe консоли нет — показываем обычное окно Windows
    import ctypes
    ctypes.windll.user32.MessageBoxW(None, text, "minikeys", 0x10)
    sys.exit(1)


if sys.version_info < (3, 11):
    _fatal("Нужен Python 3.11 или новее: https://www.python.org/downloads/")
if importlib.util.find_spec("PySide6") is None:
    _fatal("Не установлена библиотека интерфейса PySide6.\n\n"
           "Откройте PowerShell и выполните:\n    python -m pip install PySide6\n\n"
           "Затем запустите MiniKeys.pyw ещё раз.")

from minikeys.gui.app import main  # noqa: E402

sys.exit(main())
