"""Таблицы клавиш.

Вход (что прилетает с мини-клавиатуры) описывается скан-кодами Set 1 —
именно их отдаёт драйвер Interception. Выход (что мы отправляем в систему)
описывается виртуальными кодами Windows (VK) — они не зависят от раскладки,
поэтому Ctrl+Shift+M работает и при русской раскладке.

Имена клавиш общие для входа и выхода: "a", "f13", "numpad1", "page_up" и т.д.
Регистр, пробелы, "_" и "-" в именах игнорируются: "Page Up" == "page_up" == "pageup".
"""

from __future__ import annotations

# --- Флаги состояния клавиши в Interception ---------------------------------
KEY_UP = 0x01
KEY_E0 = 0x02
KEY_E1 = 0x04

# --- Скан-коды (вход) --------------------------------------------------------
# имя -> (скан-код, префикс), где префикс: 0, KEY_E0 или KEY_E1
SCANCODES: dict[str, tuple[int, int]] = {}


def _sc(name: str, code: int, prefix: int = 0) -> None:
    SCANCODES[name] = (code, prefix)


_sc("esc", 0x01)
for _i, _ch in enumerate("1234567890"):
    _sc(_ch, 0x02 + _i)
_sc("minus", 0x0C)
_sc("equal", 0x0D)
_sc("backspace", 0x0E)
_sc("tab", 0x0F)
for _i, _ch in enumerate("qwertyuiop"):
    _sc(_ch, 0x10 + _i)
_sc("lbracket", 0x1A)
_sc("rbracket", 0x1B)
_sc("enter", 0x1C)
_sc("lctrl", 0x1D)
for _i, _ch in enumerate("asdfghjkl"):
    _sc(_ch, 0x1E + _i)
_sc("semicolon", 0x27)
_sc("quote", 0x28)
_sc("backtick", 0x29)
_sc("lshift", 0x2A)
_sc("backslash", 0x2B)
for _i, _ch in enumerate("zxcvbnm"):
    _sc(_ch, 0x2C + _i)
_sc("comma", 0x33)
_sc("period", 0x34)
_sc("slash", 0x35)
_sc("rshift", 0x36)
_sc("numpad_mul", 0x37)
_sc("lalt", 0x38)
_sc("space", 0x39)
_sc("caps_lock", 0x3A)
for _i in range(10):
    _sc(f"f{_i + 1}", 0x3B + _i)
_sc("num_lock", 0x45)
_sc("scroll_lock", 0x46)
_sc("numpad7", 0x47)
_sc("numpad8", 0x48)
_sc("numpad9", 0x49)
_sc("numpad_sub", 0x4A)
_sc("numpad4", 0x4B)
_sc("numpad5", 0x4C)
_sc("numpad6", 0x4D)
_sc("numpad_add", 0x4E)
_sc("numpad1", 0x4F)
_sc("numpad2", 0x50)
_sc("numpad3", 0x51)
_sc("numpad0", 0x52)
_sc("numpad_dot", 0x53)
_sc("oem_102", 0x56)  # доп. клавиша "\" у ISO-клавиатур
_sc("f11", 0x57)
_sc("f12", 0x58)
for _i in range(11):  # F13..F23 = 0x64..0x6E
    _sc(f"f{_i + 13}", 0x64 + _i)
_sc("f24", 0x76)

_sc("numpad_enter", 0x1C, KEY_E0)
_sc("rctrl", 0x1D, KEY_E0)
_sc("numpad_div", 0x35, KEY_E0)
_sc("print_screen", 0x37, KEY_E0)
_sc("ralt", 0x38, KEY_E0)
_sc("home", 0x47, KEY_E0)
_sc("up", 0x48, KEY_E0)
_sc("page_up", 0x49, KEY_E0)
_sc("left", 0x4B, KEY_E0)
_sc("right", 0x4D, KEY_E0)
_sc("end", 0x4F, KEY_E0)
_sc("down", 0x50, KEY_E0)
_sc("page_down", 0x51, KEY_E0)
_sc("insert", 0x52, KEY_E0)
_sc("delete", 0x53, KEY_E0)
_sc("lwin", 0x5B, KEY_E0)
_sc("rwin", 0x5C, KEY_E0)
_sc("apps", 0x5D, KEY_E0)
_sc("pause", 0x1D, KEY_E1)
# Мультимедиа (видны только если устройство шлёт их как обычную клавиатуру)
_sc("media_prev", 0x10, KEY_E0)
_sc("media_next", 0x19, KEY_E0)
_sc("volume_mute", 0x20, KEY_E0)
_sc("launch_app2", 0x21, KEY_E0)
_sc("media_play_pause", 0x22, KEY_E0)
_sc("media_stop", 0x24, KEY_E0)
_sc("volume_down", 0x2E, KEY_E0)
_sc("volume_up", 0x30, KEY_E0)
_sc("browser_home", 0x32, KEY_E0)
# "Фальшивые" Shift, которые Windows вставляет вокруг некоторых E0-клавиш.
# Их никогда не биндят, они просто глушатся/пропускаются как неназначенные.
_sc("fake_lshift", 0x2A, KEY_E0)
_sc("fake_rshift", 0x36, KEY_E0)

IGNORED_INPUT = frozenset({"fake_lshift", "fake_rshift"})

SC_TO_NAME: dict[tuple[int, int], str] = {v: k for k, v in SCANCODES.items()}

# --- Виртуальные коды (выход) ------------------------------------------------
VK: dict[str, int] = {}
for _ch in "abcdefghijklmnopqrstuvwxyz":
    VK[_ch] = ord(_ch.upper())
for _ch in "0123456789":
    VK[_ch] = ord(_ch)
for _i in range(24):
    VK[f"f{_i + 1}"] = 0x70 + _i
for _i in range(10):
    VK[f"numpad{_i}"] = 0x60 + _i
VK.update({
    "numpad_mul": 0x6A, "numpad_add": 0x6B, "numpad_sub": 0x6D,
    "numpad_dot": 0x6E, "numpad_div": 0x6F, "numpad_enter": 0x0D,
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "pause": 0x13,
    "caps_lock": 0x14, "esc": 0x1B, "space": 0x20,
    "page_up": 0x21, "page_down": 0x22, "end": 0x23, "home": 0x24,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "print_screen": 0x2C, "insert": 0x2D, "delete": 0x2E,
    "lwin": 0x5B, "rwin": 0x5C, "apps": 0x5D,
    "num_lock": 0x90, "scroll_lock": 0x91,
    "lshift": 0xA0, "rshift": 0xA1, "lctrl": 0xA2, "rctrl": 0xA3,
    "lalt": 0xA4, "ralt": 0xA5,
    "browser_back": 0xA6, "browser_forward": 0xA7, "browser_refresh": 0xA8,
    "browser_home": 0xAC,
    "volume_mute": 0xAD, "volume_down": 0xAE, "volume_up": 0xAF,
    "media_next": 0xB0, "media_prev": 0xB1, "media_stop": 0xB2,
    "media_play_pause": 0xB3, "launch_mail": 0xB4,
    "launch_app1": 0xB6, "launch_app2": 0xB7,
    "semicolon": 0xBA, "equal": 0xBB, "comma": 0xBC, "minus": 0xBD,
    "period": 0xBE, "slash": 0xBF, "backtick": 0xC0,
    "lbracket": 0xDB, "backslash": 0xDC, "rbracket": 0xDD, "quote": 0xDE,
    "oem_102": 0xE2,
})

# Клавиши, которым в SendInput нужен флаг EXTENDEDKEY
EXTENDED_VK = frozenset({
    0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2C, 0x2D, 0x2E,
    0x5B, 0x5C, 0x5D, 0x6F, 0x90, 0xA3, 0xA5,
    *range(0xA6, 0xB8),
})

MODIFIERS = frozenset({"lshift", "rshift", "lctrl", "rctrl", "lalt", "ralt", "lwin", "rwin"})

# --- Синонимы ----------------------------------------------------------------
ALIASES: dict[str, str] = {
    "ctrl": "lctrl", "control": "lctrl", "shift": "lshift", "alt": "lalt",
    "win": "lwin", "windows": "lwin", "altgr": "ralt",
    "escape": "esc", "return": "enter", "bs": "backspace",
    "del": "delete", "ins": "insert", "pgup": "page_up", "pgdn": "page_down",
    "prtsc": "print_screen", "printscr": "print_screen",
    "capslock": "caps_lock", "numlock": "num_lock", "scrlk": "scroll_lock",
    "menu": "apps", "context_menu": "apps",
    "-": "minus", "=": "equal", "[": "lbracket", "]": "rbracket",
    ";": "semicolon", "'": "quote", "`": "backtick", "grave": "backtick",
    "\\": "backslash", ",": "comma", ".": "period", "/": "slash",
    "numpad_multiply": "numpad_mul", "numpad_plus": "numpad_add",
    "numpad_minus": "numpad_sub", "numpad_subtract": "numpad_sub",
    "numpad_divide": "numpad_div", "numpad_decimal": "numpad_dot",
    "numpad_period": "numpad_dot",
    "mute": "volume_mute", "vol_up": "volume_up", "vol_down": "volume_down",
    "play_pause": "media_play_pause", "next_track": "media_next",
    "prev_track": "media_prev", "calculator": "launch_app2",
}


def _squash(name: str) -> str:
    name = name.strip().lower()
    if len(name) > 1:
        for ch in " _-":
            name = name.replace(ch, "")
    return name


_LOOKUP: dict[str, str] = {}
for _name in (*SCANCODES, *VK):
    _LOOKUP[_squash(_name)] = _name
for _alias, _target in ALIASES.items():
    _LOOKUP[_squash(_alias)] = _target


class KeyNameError(ValueError):
    pass


def _raw_sc_name(code: int, prefix: int) -> str:
    if prefix & KEY_E1:
        return f"sc_e1_{code:02x}"
    if prefix & KEY_E0:
        return f"sc_e0_{code:02x}"
    return f"sc_{code:02x}"


def _parse_raw_sc(squashed: str) -> tuple[int, int] | None:
    """'sc1e' / 'sce01e' (после _squash) -> (код, префикс)."""
    if not squashed.startswith("sc"):
        return None
    rest, prefix = squashed[2:], 0
    if rest.startswith("e0") and len(rest) > 2:
        rest, prefix = rest[2:], KEY_E0
    elif rest.startswith("e1") and len(rest) > 2:
        rest, prefix = rest[2:], KEY_E1
    try:
        code = int(rest, 16)
    except ValueError:
        return None
    return (code, prefix) if 0 < code < 0x100 else None


def input_key(name: str) -> str:
    """Имя клавиши мини-клавиатуры -> каноническое имя (для ключей в [binds])."""
    squashed = _squash(name)
    canon = _LOOKUP.get(squashed)
    if canon in SCANCODES:
        return canon
    raw = _parse_raw_sc(squashed)
    if raw is not None:
        return SC_TO_NAME.get(raw, _raw_sc_name(*raw))
    raise KeyNameError(f"неизвестная клавиша мини-клавиатуры: {name!r} "
                       f"(запустите `python -m minikeys identify`, чтобы узнать имена)")


def output_key(name: str) -> str:
    """Имя клавиши для отправки -> каноническое имя из таблицы VK."""
    canon = _LOOKUP.get(_squash(name))
    if canon in VK:
        return canon
    raise KeyNameError(f"неизвестная клавиша для отправки: {name!r}")


def parse_chord(spec: str) -> list[str]:
    """'ctrl+shift+m' -> ['lctrl', 'lshift', 'm']. Модификаторы идут первыми."""
    parts = [p for p in spec.split("+")]
    if not spec.strip() or any(not p.strip() for p in parts):
        raise KeyNameError(f"некорректное сочетание: {spec!r}")
    keys = [output_key(p) for p in parts]
    if len(set(keys)) != len(keys):
        raise KeyNameError(f"клавиша повторяется в сочетании: {spec!r}")
    return keys


def name_from_stroke(code: int, state: int) -> str:
    """Скан-код + флаги Interception -> каноническое имя (или sc_XX для неизвестных)."""
    prefix = state & (KEY_E0 | KEY_E1)
    return SC_TO_NAME.get((code, prefix), _raw_sc_name(code, prefix))
