"""Мост между потоком перехватчика и интерфейсом.

RemapService вызывает методы Listener в СВОЁМ потоке. Здесь каждый вызов
превращается в Qt-сигнал; сигнал, испущенный из чужого потока, Qt доставляет
в GUI-поток через очередь событий (queued connection). Поэтому интерфейс
не блокируется драйвером, а драйвер — интерфейсом.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from ..service import Listener


class ServiceBridge(QObject, Listener):
    targets_changed = Signal(object)   # dict[int, list[str]]
    key_event = Signal(str, bool)      # клавиша, нажата?
    learned = Signal(str)
    probed = Signal(int, object, str)  # устройство, hardware ids, клавиша
    stopped = Signal(str)              # текст ошибки или ""
    app_command = Signal(str)          # НОВОЕ 1.3: команда окну, например "toggle_window"

    def on_targets(self, targets: dict[int, list[str]]) -> None:
        self.targets_changed.emit(targets)

    def on_key(self, key: str, is_down: bool) -> None:
        self.key_event.emit(key, is_down)

    def on_learned(self, key: str) -> None:
        self.learned.emit(key)

    def on_probe(self, device: int, hardware_ids: list[str], key: str) -> None:
        self.probed.emit(device, hardware_ids, key)

    def on_app_command(self, name: str) -> None:
        self.app_command.emit(name)  # вызывается из потока действий, слот сработает в GUI-потоке

    def on_stopped(self, error: str | None) -> None:
        self.stopped.emit(error or "")
