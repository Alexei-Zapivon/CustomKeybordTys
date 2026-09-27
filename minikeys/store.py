"""profile.json: профили (устройство + бинды + расположение кнопок) и настройки программы.

Формат (версия 2):
{
  "version": 2,
  "active": "Default",                         # профиль, который работает сейчас
  "app": {"start_minimized": false},           # настройки самой программы
  "profiles": {
    "Default": {
      "device":   {"match": "VID_1189&PID_8840"},          # + "device_number": 4 — только это устройство
      "settings": {"unmapped": "block", "log_keys": false},
      "binds":    {"a": "ctrl+shift+m", "1": {"hotkey": "volume_down", "repeat": true}},
      "layout": {
        "keys":     {"a": {"x": 0, "y": 0, "label": "K1"}},
        "encoders": [{"id": "enc1", "name": "Крутилка 1", "left": "1", "right": "2",
                      "press": "3", "x": 0, "y": 200}]
      }
    },
    "Gaming": {...}
  }
}
Файл версии 1 (один профиль прямо в корне) при загрузке становится профилем "Default".
Консольная версия читает активный профиль: `python -m minikeys run -c profile.json`.

Функции ниже без слова root работают с ОДНИМ профилем (dict из "profiles").
"""

from __future__ import annotations

import copy
import json
import os
import re
import tomllib
from pathlib import Path
from typing import Any

from . import paths
from .config import ConfigError, Profile, parse_profile

DEFAULT_PROFILE = "Default"

KEY_SIZE = 84      # размер кнопки на поле
GRID_STEP = 96     # шаг автоматической раскладки
ENCODER_SIZE = 160
COLUMNS = 6


def new_document() -> dict:
    return {
        "device": {"match": ""},
        "settings": {"unmapped": "block", "log_keys": False},
        "binds": {},
        "layout": {"keys": {}, "encoders": []},
    }


def normalize(doc: dict) -> dict:
    """Дополняет недостающие секции, чтобы GUI мог на них полагаться."""
    base = new_document()
    for section in ("device", "settings"):
        if isinstance(doc.get(section), dict):
            base[section].update(doc[section])
    if isinstance(doc.get("binds"), dict):
        base["binds"] = dict(doc["binds"])
    layout = doc.get("layout") if isinstance(doc.get("layout"), dict) else {}
    if isinstance(layout.get("keys"), dict):
        base["layout"]["keys"] = {k: dict(v) for k, v in layout["keys"].items() if isinstance(v, dict)}
    if isinstance(layout.get("encoders"), list):
        base["layout"]["encoders"] = [dict(e) for e in layout["encoders"] if isinstance(e, dict)]
    return base


def default_path() -> Path:
    return paths.data_dir() / "profile.json"


def legacy_toml() -> Path:
    return paths.app_dir() / "config.toml"


def _import_toml(legacy: Path | None) -> dict:
    """Профиль из старого config.toml (первый запуск GUI) или пустой."""
    doc = new_document()
    if legacy is None or not legacy.exists():
        return doc
    try:
        with legacy.open("rb") as f:
            old = tomllib.load(f)
        parse_profile(old)  # переносим только корректный конфиг
    except (OSError, tomllib.TOMLDecodeError, ConfigError):
        return doc
    doc = normalize(old)
    for key in doc["binds"]:
        place_key(doc, key)
    return doc


# --- корневой документ: несколько профилей + настройки программы ------------------
def new_root(profile: dict | None = None) -> dict:
    return {"version": 2, "active": DEFAULT_PROFILE, "app": {"start_minimized": False},
            "profiles": {DEFAULT_PROFILE: profile or new_document()}}


def normalize_root(data: dict) -> dict:
    raw = data.get("profiles")
    if not isinstance(raw, dict) or not raw:
        return new_root(normalize(data))  # файл версии 1: один профиль в корне
    profiles = {str(name): normalize(p if isinstance(p, dict) else {}) for name, p in raw.items()}
    active = data.get("active") if data.get("active") in profiles else next(iter(profiles))
    app = data.get("app") if isinstance(data.get("app"), dict) else {}
    return {"version": 2, "active": active,
            "app": {"start_minimized": bool(app.get("start_minimized", False))},
            "profiles": profiles}


def load_root(path: Path | None = None, legacy: Path | None = None, *,
              import_legacy: bool = True) -> dict:
    """Читает profile.json; при первом запуске переносит бинды из config.toml."""
    path = path or default_path()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"{path}: не удалось прочитать: {exc}") from None
        return normalize_root(data if isinstance(data, dict) else {})
    if import_legacy and legacy is None:
        legacy = legacy_toml()
    return new_root(_import_toml(legacy if import_legacy else None))


def save_root(root: dict, path: Path | None = None) -> None:
    """Атомарная запись: сбой посреди сохранения не испортит файл."""
    path = path or default_path()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(root, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def active_profile(root: dict) -> dict:
    return root["profiles"][root["active"]]


def unique_name(root: dict, base: str) -> str:
    name, n = base, 2
    while name in root["profiles"]:
        name, n = f"{base} {n}", n + 1
    return name


def _check_name(root: dict, name: str) -> str:
    name = name.strip()
    if not name:
        raise ValueError("Имя профиля не может быть пустым")
    if name in root["profiles"]:
        raise ValueError(f"Профиль «{name}» уже есть")
    return name


def create_profile(root: dict, name: str, template: dict | None = None,
                   copy_binds: bool = False) -> str:
    """Новый профиль. От template берутся устройство и раскладка кнопок (и бинды, если copy_binds)."""
    name = _check_name(root, name)
    doc = new_document()
    if template is not None:
        doc["device"] = copy.deepcopy(template["device"])
        doc["settings"] = copy.deepcopy(template["settings"])
        doc["layout"] = copy.deepcopy(template["layout"])
        if copy_binds:
            doc["binds"] = copy.deepcopy(template["binds"])
    root["profiles"][name] = doc
    return name


def rename_profile(root: dict, old: str, new: str) -> str:
    if new.strip() == old:
        return old
    new = _check_name(root, new)
    # сохраняем порядок профилей в списке
    root["profiles"] = {(new if k == old else k): v for k, v in root["profiles"].items()}
    if root["active"] == old:
        root["active"] = new
    return new


def delete_profile(root: dict, name: str) -> None:
    if len(root["profiles"]) <= 1:
        raise ValueError("Нельзя удалить единственный профиль")
    del root["profiles"][name]
    if root["active"] == name:
        root["active"] = next(iter(root["profiles"]))


def set_active(root: dict, name: str) -> dict:
    if name not in root["profiles"]:
        raise KeyError(name)
    root["active"] = name
    return root["profiles"][name]


def device_configured(doc: dict) -> bool:
    dev = doc.get("device", {})
    return bool(dev.get("match")) or dev.get("device_number") is not None


def build_profile(doc: dict) -> Profile | None:
    """Profile для перехватчика или None, если устройство ещё не выбрано."""
    if not device_configured(doc):
        return None
    data = copy.deepcopy(doc)
    data["device"] = {k: v for k, v in data["device"].items() if v not in ("", None, [])}
    return parse_profile(data)


def match_from_hardware_id(hardware_id: str) -> str:
    """'HID\\VID_1189&PID_8840&REV_0100&MI_01' -> 'VID_1189&PID_8840'."""
    for pattern in (r"VID_[0-9A-F]{4}&PID_[0-9A-F]{4}", r"VID&[0-9A-F]{8}_PID&[0-9A-F]{4}"):
        m = re.search(pattern, hardware_id, re.I)
        if m:
            return m.group(0).upper()
    return hardware_id


# --- раскладка ------------------------------------------------------------------
def all_layout_keys(doc: dict) -> set[str]:
    """Все клавиши, у которых есть элемент на поле (кнопка или часть крутилки)."""
    layout = doc["layout"]
    used = set(layout["keys"])
    for enc in layout["encoders"]:
        used.update(k for k in (enc.get("left"), enc.get("right"), enc.get("press")) if k)
    return used


def _occupied(doc: dict) -> list[tuple[float, float, float, float]]:
    rects = [(p.get("x", 0), p.get("y", 0), KEY_SIZE, KEY_SIZE) for p in doc["layout"]["keys"].values()]
    rects += [(e.get("x", 0), e.get("y", 0), ENCODER_SIZE, ENCODER_SIZE + 24)
              for e in doc["layout"]["encoders"]]
    return rects


def free_position(doc: dict, w: float = KEY_SIZE, h: float = KEY_SIZE) -> tuple[int, int]:
    """Первая свободная клетка сетки (слева направо, сверху вниз)."""
    rects = _occupied(doc)
    for i in range(10_000):
        x, y = (i % COLUMNS) * GRID_STEP, (i // COLUMNS) * GRID_STEP
        if all(x + w <= rx or rx + rw <= x or y + h <= ry or ry + rh <= y for rx, ry, rw, rh in rects):
            return x, y
    return 0, 0


def place_key(doc: dict, key: str) -> None:
    if key not in doc["layout"]["keys"]:
        x, y = free_position(doc)
        doc["layout"]["keys"][key] = {"x": x, "y": y}


def add_encoder(doc: dict, left: str, right: str, press: str | None) -> dict:
    layout = doc["layout"]
    for k in (left, right, press):
        if k:
            layout["keys"].pop(k, None)  # клавиша переезжает из кнопок в крутилку
    ids = {e.get("id") for e in layout["encoders"]}
    n = 1
    while f"enc{n}" in ids:
        n += 1
    x, y = free_position(doc, ENCODER_SIZE, ENCODER_SIZE + 24)
    enc = {"id": f"enc{n}", "name": f"Крутилка {n}", "left": left, "right": right,
           "press": press, "x": x, "y": y}
    layout["encoders"].append(enc)
    return enc


def remove_key(doc: dict, key: str) -> None:
    doc["layout"]["keys"].pop(key, None)
    doc["binds"].pop(key, None)


def remove_encoder(doc: dict, enc_id: str) -> None:
    layout = doc["layout"]
    for enc in [e for e in layout["encoders"] if e.get("id") == enc_id]:
        for k in (enc.get("left"), enc.get("right"), enc.get("press")):
            if k:
                doc["binds"].pop(k, None)
        layout["encoders"].remove(enc)


def auto_arrange(doc: dict) -> None:
    """Разложить всё сеткой: кнопки сверху, крутилки строкой ниже."""
    layout = doc["layout"]
    ordered = sorted(layout["keys"].items(), key=lambda kv: (kv[1].get("y", 0), kv[1].get("x", 0)))
    for i, (_, pos) in enumerate(ordered):
        pos["x"], pos["y"] = (i % COLUMNS) * GRID_STEP, (i // COLUMNS) * GRID_STEP
    rows = (len(ordered) + COLUMNS - 1) // COLUMNS
    for i, enc in enumerate(layout["encoders"]):
        enc["x"], enc["y"] = i * (ENCODER_SIZE + 24), rows * GRID_STEP + 16


def find_encoder(doc: dict, key: str) -> tuple[dict, str] | None:
    """(крутилка, часть) для клавиши, если она принадлежит крутилке."""
    for enc in doc["layout"]["encoders"]:
        for part in ("left", "right", "press"):
            if enc.get(part) == key:
                return enc, part
    return None


def get_bind(doc: dict, key: str) -> Any:
    return doc["binds"].get(key)


def set_bind(doc: dict, key: str, spec: Any) -> None:
    if spec is None:
        doc["binds"].pop(key, None)
    else:
        doc["binds"][key] = spec
