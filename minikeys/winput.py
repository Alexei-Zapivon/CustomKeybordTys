"""Вывод действий в Windows: нажатия клавиш, текст, клики, запуск программ, команды.

Версия 1.2: как нажимаются клавиши (Discord, игры)
----------------------------------------------------
Раньше сочетание уходило ОДНИМ вызовом SendInput: все «нажать» и «отпустить»
подряд, клавиша удерживалась 0 мс. Многие программы (запись горячей клавиши в
Discord, игры с DirectInput/Raw Input) такое нажатие не успевают заметить или
смотрят только на скан-код, который раньше мог быть нулевым.

Теперь:
1. Каждая клавиша отправляется отдельно: Key Down → пауза удержания (press_ms,
   по умолчанию 30 мс) → Key Up. Между клавишами сочетания короткая пауза
   (KEY_GAP_MS), чтобы Ctrl/Shift точно были зажаты до основной клавиши.
2. Клавиши отправляются аппаратными скан-кодами (KEYEVENTF_SCANCODE), как от
   настоящей клавиатуры; Windows сама превращает их в виртуальные коды.
   Скан-код берётся из текущей раскладки (MapVirtualKey) для букв/цифр/знаков и
   из фиксированной таблицы keys.SCANCODES для F1-F24, стрелок, модификаторов и т.п.
   Исключения (VK_ONLY) отправляются виртуальным кодом, как раньше: мультимедиа и
   браузерные клавиши (у них нет обычного скан-кода) и цифры цифрового блока
   (их скан-код зависит от NumLock).
3. Режим "driver": клавиши уходят через драйвер Interception (driver_send), то есть
   для Windows это нажатия реальной клавиатуры. Их видят программы, которые
   игнорируют синтетический ввод, и окна, запущенные от администратора (UIPI не
   мешает). Если отправить через драйвер не получилось, используется SendInput.
"""

from __future__ import annotations

import ctypes
import os
import struct
import subprocess
import time
from ctypes import Structure, Union, c_int, c_int32, c_size_t, c_uint, c_uint16, c_uint32
from typing import Callable, Sequence

from .keys import EXTENDED_VK, KEY_E0, SCANCODES, VK

INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008
MAPVK_VK_TO_VSC_EX = 4
MOUSE_FLAGS = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}

# Метка в dwExtraInfo, по которой наши синтетические нажатия можно узнать в логах/хуках
EXTRA_INFO = 0x4D4B4559  # "MKEY"

DEFAULT_PRESS_MS = 30   # сколько держать клавишу нажатой
KEY_GAP_MS = 10         # пауза между клавишами внутри сочетания

OUTPUT_SENDINPUT = "sendinput"
OUTPUT_DRIVER = "driver"

# Отправляются виртуальным кодом, а не скан-кодом
VK_ONLY = frozenset({
    "volume_mute", "volume_down", "volume_up", "media_next", "media_prev", "media_stop",
    "media_play_pause", "browser_back", "browser_forward", "browser_refresh", "browser_home",
    "launch_mail", "launch_app1", "launch_app2",
    *(f"numpad{i}" for i in range(10)), "numpad_dot",
    "pause", "print_screen",
})
# Скан-код этих клавиш зависит от раскладки: берём его у Windows (MapVirtualKey)
_LAYOUT_DEPENDENT = frozenset({
    *"abcdefghijklmnopqrstuvwxyz0123456789",
    "minus", "equal", "lbracket", "rbracket", "semicolon", "quote", "backtick",
    "backslash", "comma", "period", "slash", "oem_102",
})

# driver_send(скан-код, префикс E0, отпускание?) -> True, если драйвер отправил
DriverSend = Callable[[int, int, bool], bool]


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
    def __init__(self, press_ms: int = DEFAULT_PRESS_MS, method: str = OUTPUT_SENDINPUT,
                 driver_send: DriverSend | None = None) -> None:
        self.press_ms = press_ms
        self.method = method
        self.driver_send = driver_send
        self._user32 = None
        self._sleep = time.sleep  # подменяется в тестах

    def configure(self, *, press_ms: int | None = None, method: str | None = None,
                  driver_send: DriverSend | None = None) -> None:
        """Настройки из профиля (вызывается перехватчиком при смене профиля)."""
        if press_ms is not None:
            self.press_ms = press_ms
        if method is not None:
            self.method = method
        self.driver_send = driver_send

    # --- низкий уровень ---
    def _lib(self):
        if self._user32 is None:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.SendInput.argtypes = [c_uint, ctypes.POINTER(INPUT), c_int]
            user32.SendInput.restype = c_uint
            user32.MapVirtualKeyW.argtypes = [c_uint, c_uint]
            user32.MapVirtualKeyW.restype = c_uint
            self._user32 = user32
        return self._user32

    def _mapvk(self, vk: int) -> int:
        return self._lib().MapVirtualKeyW(vk, MAPVK_VK_TO_VSC_EX)

    def _send(self, inputs: list[INPUT]) -> None:
        if not inputs:
            return
        arr = (INPUT * len(inputs))(*inputs)
        sent = self._lib().SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
        if sent != len(inputs):
            err = ctypes.get_last_error()
            raise OSError(err, "SendInput заблокирован. Если активное окно запущено от "
                               "администратора, запустите minikeys тоже от администратора "
                               "или включите отправку через драйвер.")

    def _pause(self, ms: float) -> None:
        if ms > 0:
            self._sleep(ms / 1000)

    def scancode(self, key: str) -> tuple[int, int] | None:
        """(скан-код, префикс KEY_E0 или 0) для клавиши; None, если слать нужно по VK."""
        if key in VK_ONLY:
            return None
        if key in _LAYOUT_DEPENDENT:
            sc = self._mapvk(VK[key])
            if sc & 0xFF:
                return sc & 0xFF, KEY_E0 if (sc >> 8) == 0xE0 else 0
        if key in SCANCODES:
            return SCANCODES[key]
        sc = self._mapvk(VK[key])
        if sc & 0xFF:
            return sc & 0xFF, KEY_E0 if (sc >> 8) == 0xE0 else 0
        return None

    def _vk_input(self, key: str, up: bool) -> INPUT:
        vk = VK[key]
        sc = self._mapvk(vk)
        flags = KEYEVENTF_KEYUP if up else 0
        if vk in EXTENDED_VK or (sc >> 8) in (0xE0, 0xE1):
            flags |= KEYEVENTF_EXTENDEDKEY
        inp = INPUT(type=INPUT_KEYBOARD)
        inp.ki = KEYBDINPUT(vk, sc & 0xFF, flags, 0, EXTRA_INFO)
        return inp

    def key_input(self, key: str, up: bool) -> INPUT:
        """INPUT для SendInput: по скан-коду, если он известен, иначе по виртуальному коду."""
        sc = self.scancode(key)
        if sc is None:
            return self._vk_input(key, up)
        code, prefix = sc
        flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
        if prefix & KEY_E0:
            flags |= KEYEVENTF_EXTENDEDKEY
        inp = INPUT(type=INPUT_KEYBOARD)
        inp.ki = KEYBDINPUT(0, code, flags, 0, EXTRA_INFO)  # wVk=0: Windows возьмёт VK из скан-кода
        return inp

    def _key_event(self, key: str, up: bool) -> None:
        if self.method == OUTPUT_DRIVER and self.driver_send is not None:
            sc = self.scancode(key)
            if sc is not None and self.driver_send(sc[0], sc[1], up):
                return
        self._send([self.key_input(key, up)])

    @staticmethod
    def _unicode(unit: int, up: bool) -> INPUT:
        inp = INPUT(type=INPUT_KEYBOARD)
        flags = KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0)
        inp.ki = KEYBDINPUT(0, unit, flags, 0, EXTRA_INFO)
        return inp

    # --- интерфейс Output ---
    def key_down(self, key: str) -> None:
        self._key_event(key, up=False)

    def key_up(self, key: str) -> None:
        self._key_event(key, up=True)

    def chord(self, keys: Sequence[str]) -> None:
        """Ctrl+Shift+M: Ctrl↓ Shift↓ M↓, удержание press_ms, M↑ Shift↑ Ctrl↑."""
        pressed: list[str] = []
        try:
            for i, key in enumerate(keys):
                if i:
                    self._pause(KEY_GAP_MS)
                self._key_event(key, up=False)
                pressed.append(key)
            self._pause(self.press_ms)
        finally:
            # отпускаем даже после ошибки, чтобы модификатор не «залип»
            for i, key in enumerate(reversed(pressed)):
                if i:
                    self._pause(KEY_GAP_MS)
                self._key_event(key, up=True)

    def type_text(self, text: str) -> None:
        inputs: list[INPUT] = []
        for ch in text.replace("\r\n", "\n"):
            if ch == "\n":
                inputs += [self.key_input("enter", False), self.key_input("enter", True)]
            elif ch == "\t":
                inputs += [self.key_input("tab", False), self.key_input("tab", True)]
            else:
                data = ch.encode("utf-16-le")  # символы вне BMP (эмодзи): суррогатная пара
                for unit in struct.unpack(f"<{len(data) // 2}H", data):
                    inputs += [self._unicode(unit, False), self._unicode(unit, True)]
        self._send(inputs)

    def click(self, button: str) -> None:
        down, up = MOUSE_FLAGS[button]
        for i, flag in enumerate((down, up)):
            if i:
                self._pause(self.press_ms)
            inp = INPUT(type=INPUT_MOUSE)
            inp.mi = MOUSEINPUT(0, 0, 0, flag, 0, EXTRA_INFO)
            self._send([inp])

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
