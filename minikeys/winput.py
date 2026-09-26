"""Вывод действий в Windows: SendInput, запуск программ, команды."""

from __future__ import annotations

import ctypes
import os
import struct
import subprocess
from ctypes import Structure, Union, c_int, c_int32, c_size_t, c_uint, c_uint16, c_uint32
from typing import Sequence

from .keys import EXTENDED_VK, VK

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
MAPVK_VK_TO_VSC_EX = 4
MOUSE_FLAGS = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}

# Метка в dwExtraInfo, по которой наши синтетические нажатия можно узнать в логах/хуках
EXTRA_INFO = 0x4D4B4559  # "MKEY"


class KEYBDINPUT(Structure):
    _fields_ = [("wVk", c_uint16), ("wScan", c_uint16), ("dwFlags", c_uint32),
                ("time", c_uint32), ("dwExtraInfo", c_size_t)]


class MOUSEINPUT(Structure):
    _fields_ = [("dx", c_int32), ("dy", c_int32), ("mouseData", c_uint32),
                ("dwFlags", c_uint32), ("time", c_uint32), ("dwExtraInfo", c_size_t)]


class HARDWAREINPUT(Structure):
    _fields_ = [("uMsg", c_uint32), ("wParamL", c_uint16), ("wParamH", c_uint16)]


class _InputUnion(Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", c_uint32), ("u", _InputUnion)]


class WinOutput:
    def __init__(self) -> None:
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._user32.SendInput.argtypes = [c_uint, ctypes.POINTER(INPUT), c_int]
        self._user32.SendInput.restype = c_uint
        self._user32.MapVirtualKeyW.argtypes = [c_uint, c_uint]
        self._user32.MapVirtualKeyW.restype = c_uint

    # --- низкий уровень ---
    def _send(self, inputs: list[INPUT]) -> None:
        if not inputs:
            return
        arr = (INPUT * len(inputs))(*inputs)
        sent = self._user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
        if sent != len(inputs):
            err = ctypes.get_last_error()
            raise OSError(err, "SendInput заблокирован. Если активное окно запущено от "
                               "администратора — запустите minikeys тоже от администратора.")

    def _key(self, key: str, up: bool) -> INPUT:
        vk = VK[key]
        sc = self._user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC_EX)
        flags = KEYEVENTF_KEYUP if up else 0
        if vk in EXTENDED_VK or (sc >> 8) in (0xE0, 0xE1):
            flags |= KEYEVENTF_EXTENDEDKEY
        inp = INPUT(type=INPUT_KEYBOARD)
        inp.ki = KEYBDINPUT(vk, sc & 0xFF, flags, 0, EXTRA_INFO)
        return inp

    @staticmethod
    def _unicode(unit: int, up: bool) -> INPUT:
        inp = INPUT(type=INPUT_KEYBOARD)
        flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
        inp.ki = KEYBDINPUT(0, unit, flags, 0, EXTRA_INFO)
        return inp

    # --- интерфейс Output ---
    def key_down(self, key: str) -> None:
        self._send([self._key(key, up=False)])

    def key_up(self, key: str) -> None:
        self._send([self._key(key, up=True)])

    def chord(self, keys: Sequence[str]) -> None:
        # одним вызовом SendInput — чтобы чужие нажатия не вклинились в середину
        downs = [self._key(k, up=False) for k in keys]
        ups = [self._key(k, up=True) for k in reversed(keys)]
        self._send(downs + ups)

    def type_text(self, text: str) -> None:
        inputs: list[INPUT] = []
        for ch in text.replace("\r\n", "\n"):
            if ch == "\n":
                inputs += [self._key("enter", False), self._key("enter", True)]
            elif ch == "\t":
                inputs += [self._key("tab", False), self._key("tab", True)]
            else:
                data = ch.encode("utf-16-le")  # символы вне BMP (эмодзи) — суррогатная пара
                for unit in struct.unpack(f"<{len(data) // 2}H", data):
                    inputs += [self._unicode(unit, False), self._unicode(unit, True)]
        self._send(inputs)

    def click(self, button: str) -> None:
        down, up = MOUSE_FLAGS[button]
        inputs = []
        for flag in (down, up):
            inp = INPUT(type=INPUT_MOUSE)
            inp.mi = MOUSEINPUT(0, 0, 0, flag, 0, EXTRA_INFO)
            inputs.append(inp)
        self._send(inputs)

    def launch(self, target: str, args: str, cwd: str | None) -> None:
        target = os.path.expandvars(target)
        cwd = os.path.expandvars(cwd) if cwd else None
        os.startfile(target, "open", args, cwd)  # type: ignore[attr-defined]

    def shell(self, command: str, cwd: str | None) -> None:
        subprocess.Popen(os.path.expandvars(command), shell=True,
                         cwd=os.path.expandvars(cwd) if cwd else None,
                         creationflags=subprocess.CREATE_NO_WINDOW,  # type: ignore[attr-defined]
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL)
