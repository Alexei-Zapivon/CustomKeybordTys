"""Диалоги: настройка действия кнопки, крутилки, мастер крутилки, выбор устройства."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
                               QListWidgetItem, QMessageBox, QPlainTextEdit, QPushButton,
                               QStackedWidget, QTabWidget, QVBoxLayout, QWidget)

from .. import bindtext, keys, store
from ..config import ConfigError, parse_binding
from ..service import Mode

# --- запись сочетания с клавиатуры -----------------------------------------------
_VK_TO_NAME: dict[int, str] = {}
for _name, _vk in keys.VK.items():
    _VK_TO_NAME.setdefault(_vk, _name)
_VK_TO_NAME[0x0D] = "enter"
_MODIFIER_VKS = {0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}

_QT_TO_NAME: dict[int, str] = {
    **{int(Qt.Key_A) + i: chr(ord("a") + i) for i in range(26)},
    **{int(Qt.Key_0) + i: str(i) for i in range(10)},
    **{int(Qt.Key_F1) + i: f"f{i + 1}" for i in range(24)},
    int(Qt.Key_Return): "enter", int(Qt.Key_Enter): "enter", int(Qt.Key_Escape): "esc",
    int(Qt.Key_Space): "space", int(Qt.Key_Tab): "tab", int(Qt.Key_Backspace): "backspace",
    int(Qt.Key_Delete): "delete", int(Qt.Key_Insert): "insert", int(Qt.Key_Home): "home",
    int(Qt.Key_End): "end", int(Qt.Key_PageUp): "page_up", int(Qt.Key_PageDown): "page_down",
    int(Qt.Key_Left): "left", int(Qt.Key_Right): "right", int(Qt.Key_Up): "up",
    int(Qt.Key_Down): "down", int(Qt.Key_Print): "print_screen", int(Qt.Key_Pause): "pause",
    int(Qt.Key_Menu): "apps", int(Qt.Key_VolumeUp): "volume_up",
    int(Qt.Key_VolumeDown): "volume_down", int(Qt.Key_VolumeMute): "volume_mute",
    int(Qt.Key_MediaTogglePlayPause): "media_play_pause", int(Qt.Key_MediaNext): "media_next",
    int(Qt.Key_MediaPrevious): "media_prev",
}
_QT_MODIFIER_KEYS = {int(Qt.Key_Control), int(Qt.Key_Shift), int(Qt.Key_Alt), int(Qt.Key_Meta),
                     int(Qt.Key_AltGr), int(Qt.Key_Super_L), int(Qt.Key_Super_R)}


def chord_from_event(event: QKeyEvent) -> str | None:
    """Сочетание из события клавиатуры Qt; None — нажат только модификатор."""
    vk = event.nativeVirtualKey()
    if vk in _MODIFIER_VKS or int(event.key()) in _QT_MODIFIER_KEYS:
        return None
    name = _VK_TO_NAME.get(vk) or _QT_TO_NAME.get(int(event.key()))
    if name is None:
        return None
    mods = event.modifiers()
    parts = [m for m, flag in (("ctrl", Qt.ControlModifier), ("shift", Qt.ShiftModifier),
                               ("alt", Qt.AltModifier), ("win", Qt.MetaModifier)) if mods & flag]
    return "+".join(parts + [name])


class HotkeyEdit(QWidget):
    """Поле сочетания: можно ввести текстом или нажать «Записать» и нажать сочетание."""
    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("например: ctrl+shift+m")
        self.edit.installEventFilter(self)
        self.edit.textChanged.connect(self.changed)
        self.record = QPushButton("⏺ Записать")
        self.record.setCheckable(True)
        self.record.setToolTip("Нажмите, затем нажмите сочетание на ОСНОВНОЙ клавиатуре.\n"
                               "Win+… и Alt+Tab система перехватывает сама, их введите текстом.")
        self.record.toggled.connect(self._set_recording)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.record)

    def _set_recording(self, on: bool) -> None:
        self.edit.setProperty("recording", on)
        self.edit.style().unpolish(self.edit)
        self.edit.style().polish(self.edit)
        if on:
            self.edit.setFocus()
            self.edit.setPlaceholderText("нажмите сочетание…")
        else:
            self.edit.setPlaceholderText("например: ctrl+shift+m")

    def eventFilter(self, obj, event) -> bool:
        if obj is self.edit and self.record.isChecked() and event.type() in (QEvent.KeyPress, QEvent.ShortcutOverride):
            if event.type() == QEvent.KeyPress:
                chord = chord_from_event(event)
                if chord:
                    self.edit.setText(chord)
                    self.record.setChecked(False)
            event.accept()
            return True  # в режиме записи клавиши (и Esc/Enter/Tab) не уходят дальше
        return super().eventFilter(obj, event)

    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, text: str) -> None:
        self.edit.setText(text)


# --- редактор действия -----------------------------------------------------------
ACTION_TYPES = [
    ("none", "Нет действия"),
    ("hotkey", "Сочетание клавиш"),
    ("media", "Мультимедиа (звук, треки)"),
    ("run", "Запуск программы / файла / сайта"),
    ("text", "Ввод текста"),
    ("macro", "Макрос (последовательность шагов)"),
    ("cmd", "Команда (cmd.exe)"),
    ("remap", "Удержание (работает как другая клавиша)"),
]


def classify(spec: Any) -> str:
    if spec is None:
        return "none"
    if isinstance(spec, str):
        spec = {"hotkey": spec}
    if "hotkey" in spec:
        try:
            parsed = keys.parse_chord(spec["hotkey"])
        except keys.KeyNameError:
            return "hotkey"
        return "media" if len(parsed) == 1 and parsed[0] in bindtext.MEDIA_KEYS else "hotkey"
    for kind in ("run", "text", "macro", "cmd", "remap"):
        if kind in spec:
            return kind
    return "none"


def _hint(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("hint")
    label.setWordWrap(True)
    return label


class ActionEditor(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.kind = QComboBox()
        for kind, title in ACTION_TYPES:
            self.kind.addItem(title, kind)
        self.pages = QStackedWidget()

        # none
        self.pages.addWidget(_hint("Кнопка ничего не делает (нажатие глушится или пропускается, "
                                   "см. «Настройки» → «Глушить неназначенные кнопки»)."))
        # hotkey
        page = QWidget()
        lay = QVBoxLayout(page)
        self.hotkey = HotkeyEdit()
        self.hotkey_repeat = QCheckBox("Повторять, пока кнопка зажата")
        lay.addWidget(self.hotkey)
        lay.addWidget(self.hotkey_repeat)
        lay.addWidget(_hint("Модификаторы: ctrl, shift, alt, win. Клавиши: a-z, 0-9, f1-f24, enter, esc, "
                            "space, tab, delete, home, end, page_up, up, down, left, right…\n"
                            "Сочетание не зависит от раскладки: ctrl+c работает и на русской."))
        lay.addStretch()
        self.pages.addWidget(page)
        # media
        page = QWidget()
        lay = QVBoxLayout(page)
        self.media = QComboBox()
        for title, key, _ in bindtext.MEDIA:
            self.media.addItem(title, key)
        lay.addWidget(self.media)
        lay.addWidget(_hint("Громкость повторяется, пока кнопка зажата."))
        lay.addStretch()
        self.pages.addWidget(page)
        # run
        page = QWidget()
        form = QFormLayout(page)
        row = QHBoxLayout()
        self.run_target = QLineEdit()
        self.run_target.setPlaceholderText(r"notepad.exe, C:\путь\к\программе.exe или https://…")
        browse = QPushButton("Обзор…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.run_target, 1)
        row.addWidget(browse)
        form.addRow("Что открыть:", row)
        self.run_args = QLineEdit()
        self.run_args.setPlaceholderText("необязательно")
        form.addRow("Аргументы:", self.run_args)
        form.addRow(_hint("Можно указать программу, документ, папку или адрес сайта. "
                          "Переменные окружения раскрываются: %USERPROFILE%\\Downloads"))
        self.pages.addWidget(page)
        # text
        page = QWidget()
        lay = QVBoxLayout(page)
        self.text = QPlainTextEdit()
        self.text.setPlaceholderText("Текст, который будет напечатан. Перевод строки = Enter.")
        lay.addWidget(self.text)
        self.pages.addWidget(page)
        # macro
        page = QWidget()
        lay = QVBoxLayout(page)
        self.macro = QPlainTextEdit()
        self.macro.setPlaceholderText("ctrl+a\nctrl+c\nпауза 100\nзапуск notepad.exe\nпауза 800\nctrl+v")
        lay.addWidget(self.macro, 1)
        help_label = _hint(bindtext.MACRO_HELP)
        help_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(help_label)
        self.pages.addWidget(page)
        # cmd
        page = QWidget()
        lay = QVBoxLayout(page)
        self.cmd = QLineEdit()
        self.cmd.setPlaceholderText('например: powershell -Command "..."')
        lay.addWidget(self.cmd)
        lay.addWidget(_hint("Выполняется через cmd.exe без окна консоли."))
        lay.addStretch()
        self.pages.addWidget(page)
        # remap
        page = QWidget()
        lay = QVBoxLayout(page)
        self.remap = HotkeyEdit()
        lay.addWidget(self.remap)
        lay.addWidget(_hint("Пока кнопка мини-клавиатуры зажата, зажаты и эти клавиши. "
                            "Например, «ctrl» сделает кнопку вторым Ctrl, а «f13» клавишей F13."))
        lay.addStretch()
        self.pages.addWidget(page)

        self.kind.currentIndexChanged.connect(self.pages.setCurrentIndex)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        form = QFormLayout()
        form.addRow("Действие:", self.kind)
        lay.addLayout(form)
        lay.addWidget(self.pages, 1)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Выберите программу или файл", "",
                                              "Программы (*.exe *.lnk *.bat *.cmd);;Все файлы (*)")
        if path:
            self.run_target.setText(path.replace("/", "\\"))

    def set_spec(self, spec: Any) -> None:
        kind = classify(spec)
        self.kind.setCurrentIndex([k for k, _ in ACTION_TYPES].index(kind))
        if spec is None:
            return
        if isinstance(spec, str):
            spec = {"hotkey": spec}
        if kind == "hotkey":
            self.hotkey.setText(spec["hotkey"])
            self.hotkey_repeat.setChecked(bool(spec.get("repeat")))
        elif kind == "media":
            key = keys.parse_chord(spec["hotkey"])[0]
            self.media.setCurrentIndex(max(0, self.media.findData(key)))
        elif kind == "run":
            self.run_target.setText(spec["run"])
            args = spec.get("args", "")
            self.run_args.setText(args if isinstance(args, str) else " ".join(args))
        elif kind == "text":
            self.text.setPlainText(spec["text"])
        elif kind == "macro":
            self.macro.setPlainText(bindtext.macro_to_text(spec["macro"]))
        elif kind == "cmd":
            self.cmd.setText(spec["cmd"])
        elif kind == "remap":
            self.remap.setText(spec["remap"])

    def spec(self) -> Any:
        """Бинд в формате профиля. ValueError с понятным текстом, если что-то не так."""
        kind = self.kind.currentData()
        if kind == "none":
            return None
        if kind == "hotkey":
            chord = self.hotkey.text()
            spec: Any = {"hotkey": chord, "repeat": True} if self.hotkey_repeat.isChecked() else chord
        elif kind == "media":
            key = self.media.currentData()
            repeat = next(r for _, k, r in bindtext.MEDIA if k == key)
            spec = {"hotkey": key, "repeat": True} if repeat else key
        elif kind == "run":
            spec = {"run": self.run_target.text().strip()}
            if self.run_args.text().strip():
                spec["args"] = self.run_args.text().strip()
        elif kind == "text":
            spec = {"text": self.text.toPlainText()}
        elif kind == "macro":
            spec = {"macro": bindtext.text_to_macro(self.macro.toPlainText())}
        elif kind == "cmd":
            spec = {"cmd": self.cmd.text().strip()}
        else:
            spec = {"remap": self.remap.text()}
        try:
            parse_binding(spec, "действие")
        except ConfigError as exc:
            raise ValueError(str(exc).removeprefix("действие: ").removeprefix("действие.")) from None
        return spec


# --- диалоги ---------------------------------------------------------------------
def _buttons(dialog: QDialog, ok_text: str = "Сохранить") -> QDialogButtonBox:
    box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
    box.button(QDialogButtonBox.Ok).setText(ok_text)
    box.button(QDialogButtonBox.Ok).setObjectName("primary")
    box.button(QDialogButtonBox.Cancel).setText("Отмена")
    box.accepted.connect(dialog.accept)
    box.rejected.connect(dialog.reject)
    return box


class KeyDialog(QDialog):
    def __init__(self, key: str, spec: Any, caption: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"Кнопка «{bindtext.pretty_key(key)}»")
        self.setMinimumWidth(520)
        self.caption = QLineEdit(caption)
        self.caption.setPlaceholderText(bindtext.pretty_key(key))
        self.editor = ActionEditor()
        self.editor.set_spec(spec)
        lay = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("Подпись на поле:", self.caption)
        lay.addLayout(form)
        lay.addWidget(self.editor, 1)
        lay.addWidget(_buttons(self))
        self.result_spec: Any = spec

    def accept(self) -> None:
        try:
            self.result_spec = self.editor.spec()
        except ValueError as exc:
            QMessageBox.warning(self, "Проверьте действие", str(exc))
            return
        super().accept()


PART_TITLES = {"left": "⟲ Поворот влево", "right": "⟳ Поворот вправо", "press": "● Нажатие"}


class EncoderDialog(QDialog):
    def __init__(self, enc: dict, binds: dict, part: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(enc.get("name") or "Крутилка")
        self.setMinimumWidth(540)
        self.enc = enc
        self.name = QLineEdit(enc.get("name", ""))
        self.tabs = QTabWidget()
        self.editors: dict[str, ActionEditor] = {}
        for p in ("left", "right", "press"):
            key = enc.get(p)
            if not key:
                page = _hint("У этой крутилки нет нажатия. Добавьте крутилку заново через мастер, "
                             "если она всё-таки нажимается.")
                self.tabs.addTab(page, PART_TITLES[p])
                self.tabs.setTabEnabled(self.tabs.count() - 1, False)
                continue
            editor = ActionEditor()
            editor.set_spec(binds.get(key))
            page = QWidget()
            lay = QVBoxLayout(page)
            lay.addWidget(_hint(f"Клавиша, которую присылает устройство: {key}"))
            lay.addWidget(editor, 1)
            self.tabs.addTab(page, PART_TITLES[p])
            self.editors[p] = editor
        order = ["left", "right", "press"]
        if part in self.editors:
            self.tabs.setCurrentIndex(order.index(part))
        lay = QVBoxLayout(self)
        form = QFormLayout()
        form.addRow("Название:", self.name)
        lay.addLayout(form)
        lay.addWidget(self.tabs, 1)
        lay.addWidget(_buttons(self))
        self.result_specs: dict[str, Any] = {}

    def accept(self) -> None:
        specs = {}
        for part, editor in self.editors.items():
            try:
                specs[part] = editor.spec()
            except ValueError as exc:
                self.tabs.setCurrentIndex(["left", "right", "press"].index(part))
                QMessageBox.warning(self, "Проверьте действие", f"{PART_TITLES[part]}: {exc}")
                return
        self.result_specs = specs
        super().accept()


class EncoderWizard(QDialog):
    """Пошаговое распознавание крутилки: влево → вправо → нажатие."""
    STEPS = [
        ("left", "Поверните крутилку ВЛЕВО на один щелчок"),
        ("right", "Теперь поверните её ВПРАВО на один щелчок"),
        ("press", "Нажмите на крутилку (или «Пропустить», если она не нажимается)"),
    ]

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Новая крутилка")
        self.setMinimumWidth(460)
        self.result_keys: dict[str, str | None] = {}
        self._step = 0
        self.title = QLabel()
        self.title.setObjectName("banner")
        self.title.setWordWrap(True)
        self.info = _hint("")
        self.skip = QPushButton("Пропустить")
        self.skip.clicked.connect(lambda: self.feed(None))
        cancel = QPushButton("Отмена")
        cancel.clicked.connect(self.reject)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(self.skip)
        row.addWidget(cancel)
        lay = QVBoxLayout(self)
        lay.addWidget(self.title)
        lay.addWidget(self.info)
        lay.addLayout(row)
        self._show_step()

    def _show_step(self) -> None:
        part, text = self.STEPS[self._step]
        self.title.setText(f"Шаг {self._step + 1} из 3. {text}")
        self.skip.setVisible(part == "press")

    def feed(self, key: str | None) -> None:
        """Пришло нажатие с мини-клавиатуры (None — «Пропустить»)."""
        part, _ = self.STEPS[self._step]
        if key is not None and key in self.result_keys.values():
            self.info.setText(f"Клавиша «{key}» уже использована на прошлом шаге. "
                              "Поверните/нажмите крутилку ещё раз.")
            return
        self.result_keys[part] = key
        done = ", ".join(f"{PART_TITLES[p]}: {k or 'нет'}" for p, k in self.result_keys.items())
        self.info.setText("Распознано: " + done)
        self._step += 1
        if self._step >= len(self.STEPS):
            self.accept()
        else:
            self._show_step()


class DeviceDialog(QDialog):
    """Выбор мини-клавиатуры из списка подключённых устройств или нажатием.

    Результат (result_device):
      {"match": "VID_1189&PID_8840"}                      все клавиатуры этой модели (по умолчанию)
      {"match": "VID_1189&PID_8840", "device_number": 4}  только одна из одинаковых клавиатур
    """

    def __init__(self, service, bridge, current_device: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Выбор мини-клавиатуры")
        self.setMinimumSize(640, 460)
        self.service = service
        self.current = (current_device.get("match") or "").upper()
        self.current_number = current_device.get("device_number")
        self.result_device = dict(current_device)
        self._probing = False

        self.list = QListWidget()
        self.list.currentItemChanged.connect(lambda *_: self._update_same_model_hint())
        self.status = _hint("")
        self.detect = QPushButton("🔍 Определить нажатием")
        self.detect.setCheckable(True)
        self.detect.toggled.connect(self._toggle_probe)
        refresh = QPushButton("Обновить список")
        refresh.clicked.connect(self.reload)
        self.manual = QLineEdit()
        self.manual.setPlaceholderText("или вручную: VID_1189&PID_8840")
        # НОВОЕ: несколько одинаковых клавиатур
        self.only_this = QCheckBox("Только это устройство (если подключено несколько одинаковых)")
        self.only_this.setChecked(self.current_number is not None)
        self.same_hint = _hint("")

        row = QHBoxLayout()
        row.addWidget(self.detect)
        row.addWidget(refresh)
        row.addStretch()
        lay = QVBoxLayout(self)
        lay.addWidget(_hint("Выберите мини-клавиатуру. Не уверены, какая? Нажмите «Определить нажатием» "
                            "и нажмите кнопку на мини-клавиатуре: строка выделится сама."))
        lay.addLayout(row)
        lay.addWidget(self.list, 1)
        lay.addWidget(self.status)
        lay.addWidget(self.only_this)
        lay.addWidget(self.same_hint)
        lay.addWidget(self.manual)
        lay.addWidget(_buttons(self, "Выбрать"))
        bridge.probed.connect(self._on_probe)
        self.reload()

    def _is_current(self, dev: int, ids: list[str]) -> bool:
        if not self.current or self.current not in "\n".join(ids).upper():
            return False
        return self.current_number is None or self.current_number == dev

    def reload(self) -> None:
        self.list.clear()
        try:
            devices = self.service.list_devices()
        except Exception as exc:
            self.status.setText(f"Не удалось получить список устройств: {exc}\n"
                                "Можно ввести VID/PID вручную.")
            return
        for dev, ids in sorted(devices.items()):
            match = store.match_from_hardware_id(ids[0])
            extra = ids[0].split(match, 1)[-1].strip("&") if match in ids[0].upper() else ids[0]
            text = f"№{dev}    {match}    {extra}"
            current = self._is_current(dev, ids)
            if current:
                text += "    ← выбрана сейчас"
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, (dev, match))
            item.setToolTip("\n".join(ids))
            self.list.addItem(item)
            if current and self.list.currentRow() < 0:
                self.list.setCurrentItem(item)
        self.status.setText(f"Найдено клавиатур: {self.list.count()}")
        self._update_same_model_hint()

    def _same_model(self, match: str) -> list[int]:
        return [self.list.item(i).data(Qt.UserRole)[0] for i in range(self.list.count())
                if self.list.item(i).data(Qt.UserRole)[1] == match]

    def _update_same_model_hint(self) -> None:
        item = self.list.currentItem()
        same = self._same_model(item.data(Qt.UserRole)[1]) if item else []
        if len(same) > 1:
            numbers = ", ".join(f"№{d}" for d in same)
            self.same_hint.setText(
                f"Подключено несколько устройств этой модели ({numbers}). Без галочки бинды работают "
                "на всех сразу. С галочкой профиль работает только на выбранном устройстве. "
                "Номер привязан к порядку подключения: после переподключения клавиатуры выберите её заново.")
        else:
            self.same_hint.setText("")

    def _toggle_probe(self, on: bool) -> None:
        self._probing = on
        self.service.set_mode(Mode.PROBE if on else Mode.RUN)
        self.detect.setText("Нажмите кнопку на мини-клавиатуре…" if on else "🔍 Определить нажатием")

    def _on_probe(self, device: int, hardware_ids: list, key: str) -> None:
        if not self._probing:
            return
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.UserRole)[0] == device:
                self.list.setCurrentRow(i)
                break
        else:
            self.reload()
        self.status.setText(f"Нажатие пришло с устройства №{device} (клавиша «{key}»). "
                            "Если это мини-клавиатура, нажмите «Выбрать».")
        self.detect.setChecked(False)

    def accept(self) -> None:
        manual = self.manual.text().strip()
        item = self.list.currentItem()
        if manual:
            self.result_device = {"match": manual.upper()}
        elif item is not None:
            dev, match = item.data(Qt.UserRole)
            self.result_device = {"match": match}
            if self.only_this.isChecked():
                self.result_device["device_number"] = dev
        else:
            QMessageBox.information(self, "Выбор устройства", "Выберите строку в списке или введите VID/PID.")
            return
        super().accept()

    def done(self, result: int) -> None:
        if self._probing:
            self.detect.setChecked(False)  # вернуть перехватчик в рабочий режим
        super().done(result)
