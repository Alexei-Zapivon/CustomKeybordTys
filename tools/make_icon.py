"""Создаёт assets/minikeys.ico (иконка minikeys.exe) из иконки, которую рисует программа.

    python tools/make_icon.py

ICO = заголовок + каталог + PNG-картинки нескольких размеров; сторонние библиотеки не нужны.
"""

import os
import struct
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QBuffer, QByteArray, QIODevice  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

from minikeys.gui.theme import app_icon  # noqa: E402

SIZES = (16, 24, 32, 48, 64, 128, 256)


def main() -> None:
    _app = QGuiApplication.instance() or QGuiApplication([])  # без приложения Qt нельзя рисовать
    icon = app_icon()
    images = []
    for size in SIZES:
        data = QByteArray()
        buf = QBuffer(data)
        buf.open(QIODevice.WriteOnly)
        icon.pixmap(size, size).toImage().save(buf, "PNG")
        images.append((size, bytes(data)))

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries, blobs = b"", b""
    for size, png in images:
        dim = 0 if size >= 256 else size  # 0 в ICO означает 256
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(png), offset)
        blobs += png
        offset += len(png)
    out = ROOT / "assets" / "minikeys.ico"
    out.parent.mkdir(exist_ok=True)
    out.write_bytes(header + entries + blobs)
    print(f"готово: {out} ({out.stat().st_size} байт)")


if __name__ == "__main__":
    main()
