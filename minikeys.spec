# -*- mode: python ; coding: utf-8 -*-
# Сборка minikeys.exe (один файл, без окна консоли):
#
#     build.bat
# или вручную:
#     python -m pip install pyinstaller PySide6
#     python -m PyInstaller --noconfirm --clean minikeys.spec
#
# Результат: dist\minikeys.exe

import re
from pathlib import Path

ROOT = Path(SPECPATH)  # noqa: F821  (SPECPATH задаёт PyInstaller)
VERSION = re.search(r'__version__ = "([^"]+)"',
                    (ROOT / "minikeys" / "__init__.py").read_text(encoding="utf-8")).group(1)

# interception.dll кладётся ВНУТРЬ exe; при запуске она распаковывается во временную
# папку (sys._MEIPASS), где её находит minikeys/interception.py:find_dll().
DLL = ROOT / "lib" / "x64" / "interception.dll"
if not DLL.exists():
    raise SystemExit(f"Нет файла {DLL}.\n"
                     "Скопируйте library\\x64\\interception.dll из архива Interception в lib\\x64\\.")

# Свойства файла (ПКМ по exe → Свойства → Подробно)
parts = [int(p) for p in VERSION.split(".")][:4]
parts += [0] * (4 - len(parts))
version_file = ROOT / "build" / "version_info.txt"
version_file.parent.mkdir(exist_ok=True)
version_file.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={tuple(parts)}, prodvers={tuple(parts)}, mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('041904B0', [
      StringStruct('FileDescription', 'minikeys: бинды и макросы для мини-клавиатуры'),
      StringStruct('FileVersion', '{VERSION}'),
      StringStruct('InternalName', 'minikeys'),
      StringStruct('OriginalFilename', 'minikeys.exe'),
      StringStruct('ProductName', 'minikeys'),
      StringStruct('ProductVersion', '{VERSION}')])]),
    VarFileInfo([VarStruct('Translation', [1049, 1200])])
  ]
)
""", encoding="utf-8")

a = Analysis(  # noqa: F821
    [str(ROOT / "MiniKeys.pyw")],
    pathex=[str(ROOT)],
    binaries=[(str(DLL), ".")],
    datas=[],
    hiddenimports=[],
    excludes=["tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    a.binaries,       # всё внутри одного файла (onefile)
    a.datas,
    [],
    name="minikeys",
    icon=str(ROOT / "assets" / "minikeys.ico"),
    version=str(version_file),
    console=False,    # windowed: без чёрного окна консоли, только окно и значок в трее
    uac_admin=False,  # права администратора: через автозапуск из Планировщика (см. README)
    upx=False,        # UPX-сжатие чаще вызывает ложные срабатывания антивирусов
    debug=False,
    strip=False,
    runtime_tmpdir=None,
)
