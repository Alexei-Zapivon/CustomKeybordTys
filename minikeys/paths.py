"""Где лежат файлы программы в двух режимах: из исходников и собранный minikeys.exe.

Из исходников: всё в папке проекта (как раньше).
minikeys.exe (PyInstaller): программа распаковывается во временную папку
(sys._MEIPASS), поэтому данные хранятся рядом с exe, если туда можно писать
("портативный" режим, удобно держать exe и profile.json в одной папке), иначе в
%APPDATA%\\minikeys (например, если exe положили в Program Files).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))

# папка с исходниками проекта (для запуска без сборки)
SOURCE_ROOT = Path(__file__).resolve().parent.parent


def app_dir() -> Path:
    """Папка, где лежит программа: minikeys.exe или корень проекта."""
    return Path(sys.executable).resolve().parent if FROZEN else SOURCE_ROOT


def bundle_dir() -> Path:
    """Папка с файлами, упакованными внутрь exe (interception.dll)."""
    return Path(getattr(sys, "_MEIPASS", SOURCE_ROOT))


def _writable(folder: Path) -> bool:
    probe = folder / ".minikeys-write-test"
    try:
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        return False


def data_dir() -> Path:
    """Папка для profile.json и minikeys.log."""
    base = app_dir()
    if not FROZEN or (base / "profile.json").exists() or _writable(base):
        return base
    folder = Path(os.environ.get("APPDATA", Path.home())) / "minikeys"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def launch_command() -> list[str]:
    """Команда, которой Windows должна запускать программу (для автозагрузки)."""
    if FROZEN:
        return [str(Path(sys.executable).resolve())]
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")  # без окна консоли
    return [str(pythonw if pythonw.exists() else exe), str(SOURCE_ROOT / "MiniKeys.pyw")]
