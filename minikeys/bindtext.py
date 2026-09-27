"""Человекочитаемое представление биндов: подписи на кнопках, текст макросов.

Бинд хранится в том же виде, что и в config.toml/profile.json:
  "ctrl+c"                                — сочетание (короткая форма)
  {"hotkey": "volume_up", "repeat": true} — сочетание с опциями
  {"run": "...", "args": "..."} / {"cmd": "..."} / {"text": "..."}
  {"remap": "ctrl"} / {"macro": [шаги...]}
"""

from __future__ import annotations

import ntpath
import re
from typing import Any
from urllib.parse import urlparse

from . import keys

# Мультимедиа: (подпись в списке, клавиша, повтор при удержании)
MEDIA = [
    ("Play / Pause", "media_play_pause", False),
    ("Следующий трек", "media_next", False),
    ("Предыдущий трек", "media_prev", False),
    ("Стоп", "media_stop", False),
    ("Громкость +", "volume_up", True),
    ("Громкость −", "volume_down", True),
    ("Без звука (Mute)", "volume_mute", False),
]
MEDIA_KEYS = {key: title for title, key, _ in MEDIA}
# Клавиши F13-F24: на обычной клавиатуре их нет, поэтому они идеальны для горячих
# клавиш в Discord, OBS, играх: не мешают набору текста и ни с чем не конфликтуют.
FREE_KEYS = [f"f{i}" for i in range(13, 25)]


def free_key_of(spec: Any) -> str | None:
    """Если бинд — одиночная клавиша F13-F24 (нажатие или удержание), вернуть её."""
    if spec is None:
        return None
    if isinstance(spec, str):
        spec = {"hotkey": spec}
    for kind in ("hotkey", "remap"):
        if kind in spec and not spec.get("repeat"):
            try:
                parsed = keys.parse_chord(spec[kind])
            except keys.KeyNameError:
                return None
            return parsed[0] if len(parsed) == 1 and parsed[0] in FREE_KEYS else None
    return None


# короткие подписи для кнопок на поле
MEDIA_SHORT = {
    "media_play_pause": "Play / Pause", "media_next": "След. трек", "media_prev": "Пред. трек",
    "media_stop": "Стоп", "volume_up": "Громк. +", "volume_down": "Громк. −", "volume_mute": "Mute",
}

_PRETTY = {
    "lctrl": "Ctrl", "rctrl": "RCtrl", "lshift": "Shift", "rshift": "RShift",
    "lalt": "Alt", "ralt": "AltGr", "lwin": "Win", "rwin": "RWin",
    "esc": "Esc", "enter": "Enter", "space": "Space", "tab": "Tab", "backspace": "Backspace",
    "delete": "Del", "insert": "Ins", "home": "Home", "end": "End",
    "page_up": "PgUp", "page_down": "PgDn", "up": "↑", "down": "↓", "left": "←", "right": "→",
    "print_screen": "PrtSc", "caps_lock": "CapsLock", "num_lock": "NumLock",
    "scroll_lock": "ScrLk", "apps": "Menu", "pause": "Pause",
    "minus": "-", "equal": "=", "lbracket": "[", "rbracket": "]", "semicolon": ";",
    "quote": "'", "backtick": "`", "backslash": "\\", "comma": ",", "period": ".", "slash": "/",
    "numpad_add": "Num +", "numpad_sub": "Num −", "numpad_mul": "Num *", "numpad_div": "Num /",
    "numpad_dot": "Num .", "numpad_enter": "Num Enter",
}


def pretty_key(name: str) -> str:
    if name in _PRETTY:
        return _PRETTY[name]
    if name in MEDIA_KEYS:
        return MEDIA_KEYS[name]
    if name.startswith("numpad") and name[6:].isdigit():
        return "Num " + name[6:]
    if len(name) == 1 or re.fullmatch(r"f\d{1,2}", name):
        return name.upper()
    return name.replace("_", " ").capitalize()


def pretty_chord(chord: str) -> str:
    try:
        return "+".join(pretty_key(k) for k in keys.parse_chord(chord))
    except keys.KeyNameError:
        return chord


def _short_target(target: str) -> str:
    if re.match(r"^[a-z][a-z0-9+.-]*://", target, re.I):
        return urlparse(target).netloc.removeprefix("www.") or target
    base = ntpath.basename(target.rstrip("\\/")) or target
    stem, ext = ntpath.splitext(base)
    return stem if ext.lower() in (".exe", ".lnk", ".bat", ".cmd") else base


def label(spec: Any) -> str:
    """Короткая подпись для кнопки на поле."""
    if spec is None:
        return ""
    if isinstance(spec, str):
        spec = {"hotkey": spec}
    if "hotkey" in spec:
        chord = spec["hotkey"]
        try:
            parsed = keys.parse_chord(chord)
        except keys.KeyNameError:
            return chord
        if len(parsed) == 1 and parsed[0] in MEDIA_SHORT:
            return MEDIA_SHORT[parsed[0]]
        return pretty_chord(chord)
    if "run" in spec:
        return "▶ " + _short_target(spec["run"])
    if "cmd" in spec:
        return ">_ " + spec["cmd"].split()[0] if spec["cmd"].split() else ">_"
    if "text" in spec:
        text = spec["text"].replace("\n", " ⏎ ")
        return "✎ " + (text if len(text) <= 14 else text[:13] + "…")
    if "remap" in spec:
        return "⇩ " + pretty_chord(spec["remap"])
    if "macro" in spec:
        return f"⚙ Макрос ({len(spec['macro'])})"
    if "click" in spec:
        return "Клик " + spec["click"]
    return "?"


# --- Макросы в виде текста: одна строка — один шаг ------------------------------
MACRO_HELP = """Одна строка — один шаг:
  ctrl+c                  сочетание клавиш
  пауза 100               пауза в миллисекундах
  текст Привет!\\n         напечатать текст (\\n — Enter)
  запуск notepad.exe      открыть программу / файл / сайт
  команда ipconfig        команда cmd.exe без окна
  клик left               клик мышью: left / right / middle
  зажать shift            нажать и держать
  отпустить shift         отпустить"""

_WORDS = {
    "пауза": "delay", "delay": "delay", "wait": "delay",
    "текст": "text", "text": "text",
    "запуск": "run", "run": "run",
    "команда": "cmd", "cmd": "cmd",
    "клик": "click", "click": "click",
    "зажать": "down", "down": "down",
    "отпустить": "up", "up": "up",
}
_NAMES = {"delay": "пауза", "text": "текст", "run": "запуск", "cmd": "команда",
          "click": "клик", "down": "зажать", "up": "отпустить"}


def macro_to_text(steps: list) -> str:
    lines = []
    for step in steps:
        if isinstance(step, str):
            lines.append(step)
            continue
        kind = next(k for k in ("hotkey", *_NAMES) if k in step)
        value = step[kind]
        if kind == "hotkey":
            lines.append(value)
        elif kind == "text":
            lines.append("текст " + value.replace("\\", "\\\\").replace("\n", "\\n"))
        elif kind == "run" and step.get("args"):
            lines.append(f"запуск {value} | {step['args']}")
        else:
            lines.append(f"{_NAMES[kind]} {value}")
    return "\n".join(lines)


def _unescape(text: str) -> str:
    return re.sub(r"\\(.)", lambda m: "\n" if m.group(1) == "n" else m.group(1), text)


def text_to_macro(text: str) -> list:
    """Разбор текста макроса. ValueError с номером строки при ошибке."""
    steps: list = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        word, _, rest = line.partition(" ")
        kind = _WORDS.get(word.lower())
        rest = rest.strip()
        if kind is None:
            try:
                keys.parse_chord(line)
            except keys.KeyNameError as exc:
                raise ValueError(f"строка {n}: {exc}") from None
            steps.append(line)
            continue
        if not rest:
            raise ValueError(f"строка {n}: после «{word}» нужно значение")
        if kind == "delay":
            if not rest.isdigit():
                raise ValueError(f"строка {n}: пауза задаётся числом миллисекунд")
            steps.append({"delay": int(rest)})
        elif kind == "text":
            steps.append({"text": _unescape(raw.strip()[len(word):].lstrip())})
        elif kind == "run":
            target, _, args = rest.partition(" | ")
            step = {"run": target.strip()}
            if args.strip():
                step["args"] = args.strip()
            steps.append(step)
        elif kind == "click":
            steps.append({"click": rest.lower()})
        else:
            steps.append({kind: rest})
    if not steps:
        raise ValueError("макрос пустой")
    return steps
