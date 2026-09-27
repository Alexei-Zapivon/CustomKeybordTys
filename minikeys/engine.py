"""Логика обработки нажатий мини-клавиатуры (без привязки к драйверу)."""

from __future__ import annotations

import logging
import queue
import threading
from typing import Callable

from .actions import Action, Binding, Output
from .config import Profile
from .keys import IGNORED_INPUT, KEY_E1, KEY_UP, name_from_stroke

log = logging.getLogger("minikeys")


class Worker:
    """Выполняет действия по очереди в отдельном потоке.

    Цикл чтения драйвера никогда не ждёт действий (макрос с паузами не
    задерживает приём следующих нажатий), а порядок действий сохраняется.
    """

    def __init__(self, out: Output):
        self._out = out
        self._q: queue.Queue[Action | None] = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name="minikeys-actions", daemon=True)
        self._thread.start()

    def submit(self, action: Action) -> None:
        self._q.put(action)

    def _loop(self) -> None:
        while (action := self._q.get()) is not None:
            try:
                action.run(self._out)
            except Exception as exc:  # одно сломанное действие не должно убивать программу
                log.error("ошибка при выполнении «%s»: %s", action.describe(), exc)

    def stop(self, timeout: float = 2.0) -> None:
        self._q.put(None)
        self._thread.join(timeout)


class Engine:
    def __init__(self, profile: Profile, submit: Callable[[Action], None]):
        self.profile = profile
        self._submit = submit
        # (устройство, клавиша) -> бинд, сработавший на нажатие. Устройство в ключе нужно,
        # когда подключены две одинаковые мини-клавиатуры: «a» на каждой живёт своей жизнью.
        self._held: dict[tuple[int, str], Binding] = {}

    def set_profile(self, profile: Profile) -> None:
        # уже зажатые клавиши отпустятся по старым биндам (они хранятся в _held)
        self.profile = profile

    def handle(self, key: str, is_down: bool, device: int = 0) -> bool:
        """Обработать нажатие. True — нажатие поглощено, False — пропустить в систему."""
        if (device, key) in self._held or key in self.profile.binds:
            self._dispatch((device, key), is_down)
            return True
        # неназначенная клавиша
        if is_down and self.profile.log_keys and key not in IGNORED_INPUT:
            log.info("клавиша %s не назначена (%s)", key,
                     "заглушена" if self.profile.unmapped == "block" else "пропущена")
        return self.profile.unmapped == "block"

    def _dispatch(self, held_key: tuple[int, str], is_down: bool) -> None:
        if not is_down:
            binding = self._held.pop(held_key, None)  # None: клавиша была зажата ещё до запуска
            if binding is not None and binding.on_release is not None:
                self._submit(binding.on_release)
            return

        is_repeat = held_key in self._held
        binding = self._held.get(held_key) or self.profile.binds[held_key[1]]
        self._held[held_key] = binding
        if is_repeat and not (binding.is_hold or binding.repeat):
            return
        if not is_repeat and self.profile.log_keys:
            log.info("%s → %s", held_key[1], binding.describe())
        self._submit(binding.on_press)

    def release_all(self) -> None:
        """Отпустить всё, что держат remap-бинды (при выходе/потере устройства)."""
        for held_key in list(self._held):
            self._dispatch(held_key, is_down=False)


class Router:
    """Решает судьбу каждого события от драйвера: отдать системе или поглотить."""

    def __init__(self, engine: Engine):
        self.engine = engine
        self.targets: frozenset[int] = frozenset()  # номера устройств мини-клавиатуры
        self._e1_tail: bool | None = None  # Pause шлёт 2 события (E1 1D + 45)

    def route(self, device: int, code: int, state: int) -> bool:
        """True — переслать событие в систему без изменений."""
        if device not in self.targets:
            return True  # другая клавиатура — не трогаем
        if self._e1_tail is not None and not state & KEY_E1 and code == 0x45:
            forward, self._e1_tail = not self._e1_tail, None  # хвост Pause — как и его начало
            return forward
        swallow = self.engine.handle(name_from_stroke(code, state), not state & KEY_UP, device)
        if state & KEY_E1:
            self._e1_tail = swallow
        return not swallow
