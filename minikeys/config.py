"""Загрузка и проверка config.toml."""

from __future__ import annotations

import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import keys
from .actions import (Action, Binding, Click, Delay, Hotkey, KeyDown, KeyUp, Launch, Macro, Shell,
                      Text)

MOUSE_BUTTONS = ("left", "right", "middle")
ACTION_KINDS = ("hotkey", "text", "run", "cmd", "remap", "macro", "click")
MACRO_STEP_KINDS = ("hotkey", "text", "run", "cmd", "click", "delay", "down", "up")


class ConfigError(ValueError):
    pass


@dataclass
class DeviceMatch:
    """Как найти мини-клавиатуру среди устройств Interception."""
    match: list[str] = field(default_factory=list)   # все подстроки должны входить в Hardware ID
    exclude: list[str] = field(default_factory=list)  # ни одна не должна входить
    device_number: int | None = None                  # жёсткий номер слота Interception (1..10)

    def matches(self, device: int, hardware_ids: list[str]) -> bool:
        if self.device_number is not None:
            return device == self.device_number
        haystack = "\n".join(hardware_ids).upper()
        return (bool(self.match)
                and all(m.upper() in haystack for m in self.match)
                and not any(x.upper() in haystack for x in self.exclude))

    def describe(self) -> str:
        if self.device_number is not None:
            return f"устройство №{self.device_number}"
        text = " и ".join(repr(m) for m in self.match)
        if self.exclude:
            text += ", кроме " + ", ".join(repr(x) for x in self.exclude)
        return text


@dataclass
class Profile:
    device: DeviceMatch
    binds: dict[str, Binding]
    unmapped: str = "block"   # "block" — глушить неназначенные клавиши, "pass" — пропускать
    log_keys: bool = False
    path: Path | None = None


def load_profile(path: str | Path) -> Profile:
    path = Path(path)
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"файл конфигурации не найден: {path}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: синтаксическая ошибка TOML: {exc}") from None
    try:
        profile = parse_profile(data)
    except ConfigError as exc:
        raise ConfigError(f"{path}: {exc}") from None
    profile.path = path
    return profile


def _str_list(value: Any, where: str) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise ConfigError(f"{where}: ожидается строка или список строк")
    return [v.strip() for v in value]


def _check_keys(table: dict, allowed: set[str], where: str) -> None:
    extra = set(table) - allowed
    if extra:
        raise ConfigError(f"{where}: неизвестные параметры {sorted(extra)}; допустимы {sorted(allowed)}")


def parse_device(data: Any) -> DeviceMatch:
    if not isinstance(data, dict):
        raise ConfigError("нет секции [device] — укажите, как найти мини-клавиатуру")
    _check_keys(data, {"match", "exclude", "device_number"}, "[device]")
    dev = DeviceMatch()
    if "match" in data:
        dev.match = _str_list(data["match"], "[device].match")
    if "exclude" in data:
        dev.exclude = _str_list(data["exclude"], "[device].exclude")
    if "device_number" in data:
        n = data["device_number"]
        if not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 10:
            raise ConfigError("[device].device_number: число от 1 до 10")
        dev.device_number = n
    if not dev.match and dev.device_number is None:
        raise ConfigError("[device]: задайте match (часть Hardware ID) или device_number")
    return dev


def _chord(value: Any, where: str) -> list[str]:
    if not isinstance(value, str):
        raise ConfigError(f"{where}: ожидается строка вида \"ctrl+shift+m\"")
    try:
        return keys.parse_chord(value)
    except keys.KeyNameError as exc:
        raise ConfigError(f"{where}: {exc}") from None


def _single_kind(spec: dict, kinds: tuple[str, ...], where: str) -> str:
    found = [k for k in kinds if k in spec]
    if len(found) != 1:
        raise ConfigError(f"{where}: нужно ровно одно из {list(kinds)}, найдено {found or 'ничего'}")
    return found[0]


def _opt_str(spec: dict, name: str, where: str) -> str | None:
    value = spec.get(name)
    if value is not None and not isinstance(value, str):
        raise ConfigError(f"{where}.{name}: ожидается строка")
    return value


def _simple_action(kind: str, spec: dict, where: str) -> Action:
    value = spec[kind]
    if kind == "hotkey":
        return Hotkey(_chord(value, f"{where}.hotkey"))
    if kind == "text":
        if not isinstance(value, str) or not value:
            raise ConfigError(f"{where}.text: ожидается непустая строка")
        return Text(value)
    if kind == "run":
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"{where}.run: ожидается путь к программе, файлу или URL")
        args = spec.get("args", "")
        if isinstance(args, list) and all(isinstance(a, str) for a in args):
            args = subprocess.list2cmdline(args)
        elif not isinstance(args, str):
            raise ConfigError(f"{where}.args: строка или список строк")
        return Launch(value.strip(), args, _opt_str(spec, "cwd", where))
    if kind == "cmd":
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f"{where}.cmd: ожидается команда")
        return Shell(value, _opt_str(spec, "cwd", where))
    if kind == "click":
        if value not in MOUSE_BUTTONS:
            raise ConfigError(f"{where}.click: одно из {list(MOUSE_BUTTONS)}")
        return Click(value)
    if kind == "delay":
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 60_000:
            raise ConfigError(f"{where}.delay: миллисекунды от 0 до 60000")
        return Delay(value)
    if kind == "down":
        return KeyDown(_chord(value, f"{where}.down"))
    if kind == "up":
        return KeyUp(_chord(value, f"{where}.up"))
    raise AssertionError(kind)


_OPTION_KEYS = {"args", "cwd"}


def _macro_step(step: Any, where: str) -> Action:
    if isinstance(step, str):
        return Hotkey(_chord(step, where))
    if not isinstance(step, dict):
        raise ConfigError(f"{where}: шаг макроса — строка-сочетание или таблица")
    kind = _single_kind(step, MACRO_STEP_KINDS, where)
    _check_keys(step, {kind} | _OPTION_KEYS, where)
    return _simple_action(kind, step, where)


def parse_binding(spec: Any, where: str) -> Binding:
    # Короткая форма: a = "ctrl+shift+m"
    if isinstance(spec, str):
        return Binding(Hotkey(_chord(spec, where)), source=where)
    if not isinstance(spec, dict):
        raise ConfigError(f"{where}: ожидается строка-сочетание или таблица с действием")
    kind = _single_kind(spec, ACTION_KINDS, where)
    _check_keys(spec, {kind, "repeat"} | _OPTION_KEYS, where)
    repeat = spec.get("repeat", False)
    if not isinstance(repeat, bool):
        raise ConfigError(f"{where}.repeat: true или false")

    if kind == "remap":
        chord = _chord(spec["remap"], f"{where}.remap")
        if repeat:
            raise ConfigError(f"{where}: repeat не нужен для remap — автоповтор передаётся сам")
        return Binding(KeyDown(chord), KeyUp(chord), source=where)
    if kind == "macro":
        steps = spec["macro"]
        if not isinstance(steps, list) or not steps:
            raise ConfigError(f"{where}.macro: ожидается непустой список шагов")
        action: Action = Macro([_macro_step(s, f"{where}.macro[{i}]") for i, s in enumerate(steps)])
    else:
        action = _simple_action(kind, spec, where)
    return Binding(action, repeat=repeat, source=where)


def parse_profile(data: dict) -> Profile:
    _check_keys(data, {"device", "settings", "binds"}, "корень файла")
    device = parse_device(data.get("device"))

    settings = data.get("settings", {})
    if not isinstance(settings, dict):
        raise ConfigError("[settings] должна быть таблицей")
    _check_keys(settings, {"unmapped", "log_keys"}, "[settings]")
    unmapped = settings.get("unmapped", "block")
    if unmapped not in ("block", "pass"):
        raise ConfigError('[settings].unmapped: "block" или "pass"')
    log_keys = settings.get("log_keys", False)
    if not isinstance(log_keys, bool):
        raise ConfigError("[settings].log_keys: true или false")

    raw_binds = data.get("binds", {})
    if not isinstance(raw_binds, dict):
        raise ConfigError("[binds] должна быть таблицей")
    binds: dict[str, Binding] = {}
    for key_name, spec in raw_binds.items():
        where = f"[binds] {key_name!r}"
        try:
            canon = keys.input_key(key_name)
        except keys.KeyNameError as exc:
            raise ConfigError(f"{where}: {exc}") from None
        if canon in keys.IGNORED_INPUT:
            raise ConfigError(f"{where}: эту служебную клавишу назначать нельзя")
        if canon in binds:
            raise ConfigError(f"{where}: клавиша {canon!r} уже назначена ({binds[canon].source})")
        binds[canon] = parse_binding(spec, where)
    return Profile(device=device, binds=binds, unmapped=unmapped, log_keys=log_keys)
