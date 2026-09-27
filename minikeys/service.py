"""Фоновый перехватчик: цикл драйвера Interception в отдельном потоке.

Используется и консольной версией, и GUI. Все обращения к драйверу идут только
из потока сервиса; снаружи сервисом управляют командами (set_profile, set_mode,
list_devices), которые кладутся в очередь и выполняются между событиями.
О происходящем сервис сообщает слушателю (Listener) — GUI превращает эти
вызовы в Qt-сигналы, поэтому интерфейс никогда не ждёт драйвер.
"""

from __future__ import annotations

import ctypes
import logging
import queue
import threading
import time
from concurrent.futures import Future
from enum import Enum
from typing import Callable

from .actions import Output
from .config import DeviceMatch, Profile
from .engine import Engine, Router, Worker
from .interception import KEYBOARDS, Interception, Stroke, is_keyboard
from .keys import IGNORED_INPUT, KEY_E0, KEY_UP, name_from_stroke

log = logging.getLogger("minikeys")

RESCAN_SECONDS = 2.0   # как часто искать переподключённую клавиатуру
WAIT_MS = 100          # как часто цикл проверяет команды, если нажатий нет

EMPTY_PROFILE = Profile(device=DeviceMatch(), binds={})


def acquire_single_instance() -> object | None:
    """Именованный мьютекс Windows: консоль и GUI не должны перехватывать клавиатуру вдвоём.

    Возвращает дескриптор (держите ссылку, пока программа работает) или None,
    если minikeys уже запущен.
    """
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, "Local\\minikeys-remapper")
    # 183 ERROR_ALREADY_EXISTS; 5 ERROR_ACCESS_DENIED: мьютекс создан экземпляром,
    # запущенным от администратора, а мы запущены без прав. В обоих случаях копия уже работает.
    if not handle or ctypes.get_last_error() in (183, 5):
        return None
    return handle


class Mode(str, Enum):
    RUN = "run"        # бинды работают
    LEARN = "learn"    # нажатия мини-клавиатуры глушатся и сообщаются (добавление кнопок в GUI)
    PROBE = "probe"    # слушаем ВСЕ клавиатуры и всё пропускаем (выбор устройства нажатием)
    PAUSED = "paused"  # ничего не перехватываем


class Listener:
    """Уведомления сервиса. Вызываются из потока сервиса — не трогайте в них GUI напрямую."""

    def on_targets(self, targets: dict[int, list[str]]) -> None:
        """Изменился набор перехватываемых устройств (пусто — мини-клавиатура не найдена)."""

    def on_key(self, key: str, is_down: bool) -> None:
        """Нажата/отпущена кнопка мини-клавиатуры в рабочем режиме (без автоповтора)."""

    def on_learned(self, key: str) -> None:
        """В режиме LEARN нажата кнопка мини-клавиатуры."""

    def on_probe(self, device: int, hardware_ids: list[str], key: str) -> None:
        """В режиме PROBE нажата клавиша на любой клавиатуре."""

    def on_app_command(self, name: str) -> None:
        """Сработал бинд-команда программе (например, "toggle_window"). Из потока действий."""

    def on_stopped(self, error: str | None) -> None:
        """Цикл завершился (error — текст ошибки, если упал)."""


class RemapService:
    def __init__(self, listener: Listener | None = None, dll: str | None = None, *,
                 interception_factory: Callable[[str | None], Interception] = Interception,
                 output_factory: Callable[[], Output] | None = None):
        self._listener = listener or Listener()
        self._dll = dll
        self._ic_factory = interception_factory
        self._output_factory = output_factory
        self._commands: queue.Queue[Callable[[], None]] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._ic: Interception | None = None
        self._worker: Worker | None = None

        self._profile = EMPTY_PROFILE
        self._mode = Mode.RUN
        self._engine: Engine | None = None
        self._router: Router | None = None
        self._targets: dict[int, list[str]] = {}
        self._applied: frozenset[int] = frozenset()
        self._next_scan = 0.0
        self._pressed: set[tuple[int, str]] = set()   # для отсева автоповтора в уведомлениях
        self._keyboards: frozenset[int] = frozenset()  # все подключённые клавиатуры
        self._out = None
        # драйвер трогают два потока: этот (приём) и поток действий (отправка через драйвер)
        self._io_lock = threading.Lock()

    # --- управление (можно вызывать из любого потока) ---------------------------
    def start(self) -> None:
        """Открывает драйвер (InterceptionError — если не вышло) и запускает поток."""
        self._open()
        self._thread = threading.Thread(target=self._run, name="minikeys-service", daemon=True)
        self._thread.start()

    def _open(self) -> None:
        self._ic = self._ic_factory(self._dll)
        if self._output_factory is None:
            from .winput import WinOutput
            self._output_factory = WinOutput
        self._out = self._output_factory()
        if hasattr(self._out, "set_app_handler"):
            self._out.set_app_handler(self._listener.on_app_command)
        self._worker = Worker(self._out)
        self._engine = Engine(self._profile, self._worker.submit)
        self._router = Router(self._engine)

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)

    @property
    def targets(self) -> dict[int, list[str]]:
        """Последний известный набор перехватываемых устройств (снимок, можно из любого потока)."""
        return dict(self._targets)

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def set_profile(self, profile: Profile | None) -> None:
        self._commands.put(lambda: self._apply_profile(profile or EMPTY_PROFILE))

    def set_mode(self, mode: Mode) -> None:
        self._commands.put(lambda: self._apply_mode(Mode(mode)))

    def list_devices(self, timeout: float = 3.0) -> dict[int, list[str]]:
        """{номер: [hardware id...]} всех подключённых клавиатур."""
        return self.call(lambda: self._ic.devices(KEYBOARDS)).result(timeout)

    def call(self, fn: Callable[[], object]) -> Future:
        """Выполнить fn в потоке сервиса (там, где можно трогать драйвер)."""
        fut: Future = Future()

        def run() -> None:
            try:
                fut.set_result(fn())
            except BaseException as exc:
                fut.set_exception(exc)
        if not self.running:
            fut.set_exception(RuntimeError("перехватчик не запущен"))
        else:
            self._commands.put(run)
        return fut

    # --- поток сервиса ----------------------------------------------------------
    def _run(self) -> None:
        error = None
        try:
            while not self._stop.is_set():
                self.tick()
        except Exception as exc:
            log.exception("перехватчик остановлен из-за ошибки")
            error = str(exc)
        finally:
            self._shutdown()
            self._listener.on_stopped(error)

    def _shutdown(self) -> None:
        if self._engine is not None:
            self._engine.release_all()
        if self._worker is not None:
            self._worker.stop()
        if self._ic is not None:
            self._ic.close()  # драйвер сам снимает все фильтры

    def tick(self) -> None:
        """Одна итерация цикла: команды, поиск устройств, одно событие драйвера."""
        while True:
            try:
                self._commands.get_nowait()()
            except queue.Empty:
                break
        now = time.monotonic()
        if now >= self._next_scan:
            self._next_scan = now + RESCAN_SECONDS
            self._rescan()

        ic = self._ic
        dev = ic.wait(WAIT_MS)
        if not dev:
            return
        with self._io_lock:
            stroke = ic.receive(dev)
        if stroke is None:
            return
        if dev not in self._applied or not is_keyboard(dev):
            self._forward(dev, stroke)
            return
        code, state = stroke.key.code, stroke.key.state
        name = name_from_stroke(code, state)
        is_down = not state & KEY_UP
        first_down = is_down and (dev, name) not in self._pressed
        if is_down:
            self._pressed.add((dev, name))
        else:
            self._pressed.discard((dev, name))
        notable = first_down and name not in IGNORED_INPUT

        if self._mode is Mode.PROBE:
            self._forward(dev, stroke)
            if notable:
                self._listener.on_probe(dev, self._targets.get(dev) or ic.hardware_ids(dev), name)
        elif self._mode is Mode.LEARN:
            if notable:  # в режиме обучения ничего не выполняем и в систему не пускаем
                self._listener.on_learned(name)
        else:
            if self._router.route(dev, code, state):
                self._forward(dev, stroke)
            if name not in IGNORED_INPUT and (first_down or not is_down):
                self._listener.on_key(name, is_down)

    def _forward(self, dev: int, stroke) -> None:
        with self._io_lock:
            self._ic.send(dev, stroke)

    # --- отправка нажатий через драйвер (режим output = "driver") --------------------
    def _output_device(self) -> int | None:
        """Через какую клавиатуру слать: лучше через ту, что мы НЕ перехватываем."""
        free = sorted(self._keyboards - self._applied)
        if free:
            return free[0]
        return min(self._targets) if self._targets else None

    def driver_send(self, code: int, prefix: int, up: bool) -> bool:
        """Нажатие «от имени» настоящей клавиатуры. Вызывается из потока действий."""
        dev = self._output_device()
        if dev is None or self._ic is None:
            return False
        stroke = Stroke()
        stroke.key.code = code
        stroke.key.state = (KEY_UP if up else 0) | (prefix & KEY_E0)
        self._forward(dev, stroke)
        return True

    def _configure_output(self, profile: Profile) -> None:
        configure = getattr(self._out, "configure", None)
        if configure is not None:
            configure(press_ms=profile.press_ms, method=profile.output,
                      driver_send=self.driver_send if profile.output == "driver" else None)

    def _rescan(self) -> None:
        devices = self._ic.devices(KEYBOARDS)
        self._keyboards = frozenset(devices)
        targets = {d: ids for d, ids in devices.items() if self._profile.device.matches(d, ids)}
        if targets.keys() != self._targets.keys():
            for dev in sorted(targets.keys() - self._targets.keys()):
                log.info("перехватываю устройство №%d: %s", dev, targets[dev][0])
            for dev in sorted(self._targets.keys() - targets.keys()):
                log.info("устройство №%d отключено", dev)
            if not targets:
                self._engine.release_all()
                if self._profile is not EMPTY_PROFILE:
                    log.warning("мини-клавиатура не найдена, жду подключения")
            self._listener.on_targets(dict(targets))
        self._targets = targets
        self._router.targets = frozenset(targets)
        self._apply_filter(frozenset(devices))

    def _apply_filter(self, all_keyboards: frozenset[int]) -> None:
        if self._mode is Mode.PROBE:
            wanted = all_keyboards
        elif self._mode is Mode.PAUSED:
            wanted = frozenset()
        else:
            wanted = frozenset(self._targets)
        if wanted != self._applied:
            self._ic.capture_only(wanted)
            self._applied = wanted
            self._pressed.clear()

    def _apply_profile(self, profile: Profile) -> None:
        device_changed = profile.device != self._profile.device
        self._profile = profile
        self._engine.set_profile(profile)
        self._configure_output(profile)
        if device_changed:
            self._engine.release_all()
            self._next_scan = 0.0  # сразу перепроверить устройства

    def _apply_mode(self, mode: Mode) -> None:
        if mode is self._mode:
            return
        log.info("режим: %s", mode.value)
        self._engine.release_all()
        self._mode = mode
        self._next_scan = 0.0
