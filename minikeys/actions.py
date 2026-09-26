"""Действия, которые можно назначить на клавишу мини-клавиатуры.

Каждое действие работает через объект вывода (Output), поэтому логику можно
тестировать без Windows, подставив фейковый вывод.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol, Sequence


class Output(Protocol):
    def key_down(self, key: str) -> None: ...
    def key_up(self, key: str) -> None: ...
    def chord(self, keys: Sequence[str]) -> None: ...
    def type_text(self, text: str) -> None: ...
    def click(self, button: str) -> None: ...
    def launch(self, target: str, args: str, cwd: str | None) -> None: ...
    def shell(self, command: str, cwd: str | None) -> None: ...


class Action:
    def run(self, out: Output) -> None:
        raise NotImplementedError

    def describe(self) -> str:
        raise NotImplementedError


@dataclass
class Hotkey(Action):
    """Нажать и отпустить сочетание: ctrl+shift+m."""
    keys: list[str]

    def run(self, out: Output) -> None:
        out.chord(self.keys)

    def describe(self) -> str:
        return "сочетание " + "+".join(self.keys)


@dataclass
class KeyDown(Action):
    keys: list[str]

    def run(self, out: Output) -> None:
        for k in self.keys:
            out.key_down(k)

    def describe(self) -> str:
        return "зажать " + "+".join(self.keys)


@dataclass
class KeyUp(Action):
    keys: list[str]

    def run(self, out: Output) -> None:
        for k in reversed(self.keys):
            out.key_up(k)

    def describe(self) -> str:
        return "отпустить " + "+".join(self.keys)


@dataclass
class Text(Action):
    """Напечатать текст (Unicode, не зависит от раскладки)."""
    text: str

    def run(self, out: Output) -> None:
        out.type_text(self.text)

    def describe(self) -> str:
        preview = self.text if len(self.text) <= 30 else self.text[:27] + "..."
        return f"текст {preview!r}"


@dataclass
class Launch(Action):
    """Открыть программу, файл, папку или URL (как двойной клик в Проводнике)."""
    target: str
    args: str = ""
    cwd: str | None = None

    def run(self, out: Output) -> None:
        out.launch(self.target, self.args, self.cwd)

    def describe(self) -> str:
        return f"запуск {self.target} {self.args}".rstrip()


@dataclass
class Shell(Action):
    """Выполнить команду через cmd.exe (без окна консоли)."""
    command: str
    cwd: str | None = None

    def run(self, out: Output) -> None:
        out.shell(self.command, self.cwd)

    def describe(self) -> str:
        return f"команда {self.command!r}"


@dataclass
class Click(Action):
    button: str

    def run(self, out: Output) -> None:
        out.click(self.button)

    def describe(self) -> str:
        return f"клик {self.button}"


@dataclass
class Delay(Action):
    ms: int

    def run(self, out: Output) -> None:
        time.sleep(self.ms / 1000)

    def describe(self) -> str:
        return f"пауза {self.ms} мс"


@dataclass
class Macro(Action):
    steps: list[Action]

    def run(self, out: Output) -> None:
        for step in self.steps:
            step.run(out)

    def describe(self) -> str:
        return "макрос: " + " → ".join(s.describe() for s in self.steps)


@dataclass
class Binding:
    """Что делать при нажатии/отпускании клавиши мини-клавиатуры.

    Обычный бинд выполняет `on_press` один раз на нажатие (автоповтор
    игнорируется, если repeat=False). Бинд-удержание (remap) зажимает
    клавиши, пока зажата кнопка мини-клавиатуры: on_press при нажатии и
    автоповторе, on_release — при отпускании.
    """
    on_press: Action
    on_release: Action | None = None
    repeat: bool = False
    source: str = field(default="", compare=False)

    @property
    def is_hold(self) -> bool:
        return self.on_release is not None

    def describe(self) -> str:
        if isinstance(self.on_press, KeyDown):
            return "удерживать " + "+".join(self.on_press.keys)
        text = self.on_press.describe()
        return text + (" (с автоповтором)" if self.repeat else "")
