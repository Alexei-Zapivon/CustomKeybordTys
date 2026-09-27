"""Точка входа: python -m minikeys [run|identify|list|check]."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from . import __version__
from .config import ConfigError, Profile, load_profile
from .keys import IGNORED_INPUT, KEY_E0, KEY_UP, name_from_stroke

log = logging.getLogger("minikeys")

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = ROOT / "config.toml"
RESCAN_SECONDS = 2.0   # как часто проверять изменения конфига
WAIT_MS = 200


def _setup_logging(verbose: bool, log_file: str | None) -> None:
    handlers: list[logging.Handler] = []
    if sys.stderr is not None:  # под pythonw.exe консоли нет
        handlers.append(logging.StreamHandler())
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(message)s", datefmt="%H:%M:%S",
                        handlers=handlers or [logging.NullHandler()])


def _open_interception(dll: str | None):
    from .interception import Interception, InterceptionError
    try:
        return Interception(dll)
    except InterceptionError as exc:
        log.error("Interception: %s", exc)
        sys.exit(2)


# --- list ---------------------------------------------------------------------
def cmd_list(args: argparse.Namespace) -> int:
    from .interception import KEYBOARDS, MICE
    with _open_interception(args.dll) as ic:
        for title, which in (("Клавиатуры", KEYBOARDS), ("Мыши", MICE)):
            print(f"\n{title}:")
            devices = ic.devices(which)
            if not devices:
                print("  (нет)")
            for dev, ids in devices.items():
                print(f"  №{dev:<2} {ids[0]}")
                for extra in ids[1:]:
                    print(f"       {extra}")
    print("\nЧтобы понять, какая из клавиатур мини-клавиатура: python -m minikeys identify")
    return 0


# --- identify -------------------------------------------------------------------
def cmd_identify(args: argparse.Namespace) -> int:
    from .interception import KEYBOARDS
    print("Нажимайте клавиши на мини-клавиатуре (и на основной — для сравнения).")
    print("Все нажатия проходят в систему как обычно. Выход — Ctrl+C.\n")
    with _open_interception(args.dll) as ic:
        ids_cache = ic.devices(KEYBOARDS)
        ic.capture_only(KEYBOARDS)
        held: set[tuple[int, str]] = set()
        try:
            while True:
                dev = ic.wait(WAIT_MS)
                if not dev:
                    continue
                stroke = ic.receive(dev)
                if stroke is None:
                    continue
                ic.send(dev, stroke)  # сразу отдаём нажатие системе
                key = stroke.key
                name = name_from_stroke(key.code, key.state)
                if name in IGNORED_INPUT:
                    continue
                if key.state & KEY_UP:
                    held.discard((dev, name))
                    continue
                if (dev, name) in held:
                    continue  # автоповтор
                held.add((dev, name))
                if dev not in ids_cache:
                    ids_cache[dev] = ic.hardware_ids(dev) or ["?"]
                print(f"устройство №{dev:<2} клавиша {name:<14} "
                      f"(скан-код 0x{key.code:02X}{' E0' if key.state & KEY_E0 else ''})  "
                      f"ID: {ids_cache[dev][0]}")
        except KeyboardInterrupt:
            pass
    print("\nСкопируйте в config.toml, в [device] match, часть ID вида \"VID_xxxx&PID_yyyy\".")
    return 0


# --- check ----------------------------------------------------------------------
def cmd_check(args: argparse.Namespace) -> int:
    try:
        profile = load_profile(args.config)
    except ConfigError as exc:
        print(f"ОШИБКА: {exc}")
        return 1
    print(f"Конфиг {args.config} в порядке.")
    print(f"Устройство: {profile.device.describe()}")
    print(f"Неназначенные клавиши: {'глушить' if profile.unmapped == 'block' else 'пропускать'}")
    print(f"Биндов: {len(profile.binds)}")
    for key, binding in profile.binds.items():
        print(f"  {key:<14} → {binding.describe()}")
    return 0


# --- run ------------------------------------------------------------------------
class _ConfigWatcher:
    def __init__(self, path: Path):
        self.path = path
        self._mtime = self._stat()

    def _stat(self) -> float | None:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return None

    def changed(self) -> bool:
        mtime = self._stat()
        if mtime is not None and mtime != self._mtime:
            self._mtime = mtime
            return True
        return False


def cmd_run(args: argparse.Namespace) -> int:
    from .interception import InterceptionError
    from .service import RemapService, acquire_single_instance

    try:
        profile = load_profile(args.config)
    except ConfigError as exc:
        log.error("%s", exc)
        return 1

    mutex = acquire_single_instance()
    if mutex is None:
        log.error("minikeys уже запущен (консоль или GUI) — второй экземпляр не нужен.")
        return 3

    service = RemapService(dll=args.dll)
    service.set_profile(profile)
    try:
        service.start()
    except InterceptionError as exc:
        log.error("Interception: %s", exc)
        return 2
    watcher = _ConfigWatcher(Path(args.config))
    log.info("minikeys %s: биндов %d, ищу устройство: %s",
             __version__, len(profile.binds), profile.device.describe())
    try:
        while service.running:
            time.sleep(RESCAN_SECONDS)
            if watcher.changed():
                profile = _reload(profile, watcher.path)
                service.set_profile(profile)
    except KeyboardInterrupt:
        log.info("остановлено")
    finally:
        service.stop()
    return 0


def _reload(profile: Profile, path: Path) -> Profile:
    try:
        new = load_profile(path)
    except ConfigError as exc:
        log.error("конфиг не перезагружен, работаю со старым: %s", exc)
        return profile
    log.info("конфиг перезагружен: биндов %d", len(new.binds))
    return new


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m minikeys",
        description="Бинды и макросы только для отдельной (мини-)клавиатуры в Windows.")
    parser.add_argument("command", nargs="?", default="run",
                        choices=["run", "identify", "list", "check"],
                        help="run — работать (по умолчанию); identify — узнать ID клавиатуры "
                             "нажатием; list — список устройств; check — проверить конфиг")
    parser.add_argument("-c", "--config", default=str(DEFAULT_CONFIG), help="путь к config.toml")
    parser.add_argument("--dll", help="путь к interception.dll (по умолчанию ищется в lib\\)")
    parser.add_argument("--log-file", help="дополнительно писать лог в файл")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    _setup_logging(args.verbose, args.log_file)

    if args.command == "check":
        return cmd_check(args)
    if sys.platform != "win32":
        log.error("команда %r работает только в Windows", args.command)
        return 2
    return {"run": cmd_run, "identify": cmd_identify, "list": cmd_list}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
