"""Тонкая обёртка ctypes над interception.dll (https://github.com/oblitum/Interception).

Interception — драйвер-фильтр режима ядра, который встаёт между драйвером
клавиатуры и Windows. Он видит, с какого *физического* устройства пришло
нажатие, умеет его задержать/поглотить и отдать дальше. Это ровно то, чего
нельзя сделать обычным хуком (WH_KEYBOARD_LL не знает устройство-источник).

Фильтр ставится только на выбранные устройства: нажатия остальных клавиатур
вообще не попадают в наш процесс.
"""

from __future__ import annotations

import ctypes
import os
import struct
import sys
from ctypes import CFUNCTYPE, POINTER, Structure, Union, c_int, c_int16, c_uint, c_uint16, c_void_p
from pathlib import Path
from typing import Callable, Iterable

MAX_KEYBOARD = 10
MAX_MOUSE = 10
MAX_DEVICE = MAX_KEYBOARD + MAX_MOUSE
KEYBOARDS = range(1, MAX_KEYBOARD + 1)
MICE = range(MAX_KEYBOARD + 1, MAX_DEVICE + 1)

FILTER_KEY_NONE = 0x0000
FILTER_KEY_ALL = 0xFFFF


def is_keyboard(device: int) -> bool:
    return 1 <= device <= MAX_KEYBOARD


def is_mouse(device: int) -> bool:
    return MAX_KEYBOARD < device <= MAX_DEVICE


class KeyStroke(Structure):
    _fields_ = [("code", c_uint16), ("state", c_uint16), ("information", c_uint)]


class MouseStroke(Structure):
    _fields_ = [("state", c_uint16), ("flags", c_uint16), ("rolling", c_int16),
                ("x", c_int), ("y", c_int), ("information", c_uint)]


class Stroke(Union):
    """InterceptionStroke = char[sizeof(InterceptionMouseStroke)]."""
    _fields_ = [("key", KeyStroke), ("mouse", MouseStroke)]


_Predicate = CFUNCTYPE(c_int, c_int)


class InterceptionError(RuntimeError):
    pass


def find_dll(explicit: str | os.PathLike | None = None) -> str:
    """Ищет interception.dll нужной разрядности."""
    from .paths import app_dir, bundle_dir
    arch = "x64" if struct.calcsize("P") == 8 else "x86"
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get("INTERCEPTION_DLL"):
        candidates.append(Path(os.environ["INTERCEPTION_DLL"]))
    # bundle_dir — dll, упакованная внутрь minikeys.exe; app_dir — папка проекта или exe
    for root in dict.fromkeys((bundle_dir(), app_dir())):
        candidates += [
            root / "lib" / arch / "interception.dll",
            root / "lib" / "interception.dll",
            root / "interception.dll",
        ]
    for path in candidates:
        if path.is_file():
            return str(path)
    if explicit:
        raise InterceptionError(f"interception.dll не найдена: {explicit}")
    # последний шанс: пусть Windows ищет в PATH
    return "interception.dll"


class Interception:
    """Контекст Interception. Используйте как `with Interception() as ic: ...`."""

    def __init__(self, dll_path: str | os.PathLike | None = None):
        if sys.platform != "win32":
            raise InterceptionError("Interception работает только в Windows")
        path = find_dll(dll_path)
        try:
            self._lib = ctypes.CDLL(path)
        except OSError as exc:
            raise InterceptionError(
                f"не удалось загрузить {path}: {exc}\n"
                f"Скопируйте library\\{'x64' if struct.calcsize('P') == 8 else 'x86'}\\interception.dll "
                f"из архива Interception в папку lib\\ проекта (разрядность должна совпадать с Python)."
            ) from exc
        self._bind()
        self._ctx = self._lib.interception_create_context()
        if not self._ctx:
            raise InterceptionError(
                "не удалось открыть драйвер Interception. Установите его "
                "(install-interception.exe /install от администратора) и перезагрузите ПК."
            )

    def _bind(self) -> None:
        lib = self._lib
        lib.interception_create_context.argtypes = []
        lib.interception_create_context.restype = c_void_p
        lib.interception_destroy_context.argtypes = [c_void_p]
        lib.interception_destroy_context.restype = None
        lib.interception_set_filter.argtypes = [c_void_p, _Predicate, c_uint16]
        lib.interception_set_filter.restype = None
        lib.interception_wait_with_timeout.argtypes = [c_void_p, ctypes.c_ulong]
        lib.interception_wait_with_timeout.restype = c_int
        lib.interception_receive.argtypes = [c_void_p, c_int, POINTER(Stroke), c_uint]
        lib.interception_receive.restype = c_int
        lib.interception_send.argtypes = [c_void_p, c_int, POINTER(Stroke), c_uint]
        lib.interception_send.restype = c_int
        lib.interception_get_hardware_id.argtypes = [c_void_p, c_int, c_void_p, c_uint]
        lib.interception_get_hardware_id.restype = c_uint

    # --- жизненный цикл ---
    def close(self) -> None:
        if getattr(self, "_ctx", None):
            # при закрытии контекста драйвер снимает все наши фильтры
            self._lib.interception_destroy_context(self._ctx)
            self._ctx = None

    def __enter__(self) -> "Interception":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- устройства ---
    def hardware_ids(self, device: int) -> list[str]:
        """Список Hardware ID устройства (REG_MULTI_SZ), пустой — если слот свободен."""
        buf = ctypes.create_unicode_buffer(1024)
        size = self._lib.interception_get_hardware_id(self._ctx, device, buf, ctypes.sizeof(buf))
        if not size:
            return []
        text = ctypes.wstring_at(buf, min(size, ctypes.sizeof(buf)) // ctypes.sizeof(ctypes.c_wchar))
        return [s for s in text.split("\0") if s]

    def devices(self, which: Iterable[int] = range(1, MAX_DEVICE + 1)) -> dict[int, list[str]]:
        """{номер устройства: [hardware id, ...]} для подключённых устройств."""
        result = {}
        for dev in which:
            ids = self.hardware_ids(dev)
            if ids:
                result[dev] = ids
        return result

    # --- перехват ---
    def set_filter(self, predicate: Callable[[int], bool], filter_mask: int) -> None:
        """Ставит фильтр на устройства, для которых predicate(device) истинно."""
        cb = _Predicate(lambda dev: 1 if predicate(dev) else 0)
        self._lib.interception_set_filter(self._ctx, cb, filter_mask)

    def capture_only(self, devices: Iterable[int], filter_mask: int = FILTER_KEY_ALL) -> None:
        """Перехватывать только перечисленные клавиатуры, остальные — не трогать."""
        devices = frozenset(devices)
        self.set_filter(lambda d: is_keyboard(d) and d not in devices, FILTER_KEY_NONE)
        self.set_filter(lambda d: d in devices, filter_mask)

    def wait(self, timeout_ms: int) -> int:
        """Номер устройства с ожидающим событием или 0 по таймауту."""
        return self._lib.interception_wait_with_timeout(self._ctx, timeout_ms)

    def receive(self, device: int) -> Stroke | None:
        stroke = Stroke()
        n = self._lib.interception_receive(self._ctx, device, ctypes.byref(stroke), 1)
        return stroke if n > 0 else None

    def send(self, device: int, stroke: Stroke) -> None:
        self._lib.interception_send(self._ctx, device, ctypes.byref(stroke), 1)
