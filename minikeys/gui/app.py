"""Главное окно minikeys: профили слева, поле с кнопками, панель инструментов, трей.

Архитектура:
  GUI-поток (Qt)            ←сигналы─  ServiceBridge  ←вызовы─  поток RemapService (драйвер)
       │ set_profile / set_mode / list_devices (очередь команд) ──────────────►
Интерфейс редактирует документ profile.json (несколько профилей) и отдаёт перехватчику
готовый Profile активного профиля; перехват идёт в своём потоке и не зависит от окна.

Изменения версии 1.1:
  * заголовок окна просто «minikeys», в текстах интерфейса нет длинных тире;
  * боковая панель «Профили» (gui/profiles.py), переключение на лету, профили в трее;
  * «Настройки» → «Запускать вместе с Windows» (autostart.py) и «Запускать свернутой»;
  * выбор «только это устройство» для нескольких одинаковых клавиатур (dialogs.DeviceDialog);
  * пути через paths.py, чтобы всё работало и в собранном minikeys.exe.
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QActionGroup, QCloseEvent, QDesktopServices
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QInputDialog, QLabel, QMainWindow,
                               QMenu, QMessageBox, QSizePolicy, QSystemTrayIcon, QToolBar,
                               QToolButton, QVBoxLayout, QWidget)

from .. import __version__, autostart, paths, store
from ..config import ConfigError
from ..service import Mode, RemapService
from . import theme
from .bridge import ServiceBridge
from .canvas import Board
from .dialogs import DeviceDialog, EncoderDialog, EncoderWizard, KeyDialog
from .profiles import ProfilePanel

log = logging.getLogger("minikeys")


def device_text(device: dict) -> str:
    """'VID_1189&PID_8840' или 'VID_1189&PID_8840 №4' (если выбрано одно устройство)."""
    text = device.get("match") or ""
    if device.get("device_number") is not None:
        text = f"{text} №{device['device_number']}".strip()
    return text


class MainWindow(QMainWindow):
    def __init__(self, service: RemapService, bridge: ServiceBridge, root: dict,
                 profile_path: Path) -> None:
        super().__init__()
        self.service = service
        self.bridge = bridge
        self.root = root                         # весь profile.json: профили + настройки
        self.doc = store.active_profile(root)    # активный профиль (ссылка внутрь root)
        self.profile_path = profile_path
        self.service_ok = True
        self._learning = False
        self._wizard: EncoderWizard | None = None
        self._quitting = False
        self._tray_hint_shown = False
        self._targets: dict[int, list[str]] = service.targets  # то, что сервис нашёл до нас

        self.setWindowTitle("minikeys")
        self.setWindowIcon(theme.app_icon())
        self.resize(1180, 660)

        self._build_toolbar()
        self.banner = QLabel()
        self.banner.setObjectName("banner")
        self.banner.setWordWrap(True)
        self.banner.hide()
        self.board = Board()
        self.profiles = ProfilePanel()

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.addWidget(self.banner)
        right.addWidget(self.board, 1)
        central = QWidget()
        lay = QHBoxLayout(central)
        lay.setContentsMargins(0, 0, 10, 10)
        lay.setSpacing(10)
        lay.addWidget(self.profiles)
        lay.addLayout(right, 1)
        self.setCentralWidget(central)

        self.device_label = QLabel()
        self.statusBar().addWidget(self.device_label, 1)
        self.statusBar().addPermanentWidget(QLabel(f"v{__version__}"))

        self._build_tray()

        self.board.key_clicked.connect(self.edit_key)
        self.board.encoder_clicked.connect(self.edit_encoder)
        self.board.layout_changed.connect(self.save)
        self.board.key_menu.connect(self._key_menu)
        self.board.encoder_menu.connect(self._encoder_menu)
        self.profiles.activated.connect(self.switch_profile)
        self.profiles.create_requested.connect(self.new_profile)
        self.profiles.duplicate_requested.connect(self.duplicate_profile)
        self.profiles.rename_requested.connect(self.rename_profile)
        self.profiles.delete_requested.connect(self.delete_profile)
        bridge.targets_changed.connect(self._on_targets)
        bridge.key_event.connect(self._on_key_event)
        bridge.learned.connect(self._on_learned)
        bridge.stopped.connect(self._on_service_stopped)

        self.board.set_document(self.doc)
        self._refresh_profiles()
        self._update_device_label()

    def set_service_error(self, error: str) -> None:
        self.service_ok = False
        self._set_status(theme.ERROR, f"Перехват не работает: {error}")

    # --- интерфейс ---------------------------------------------------------------
    def _build_toolbar(self) -> None:
        tb = QToolBar()
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.addToolBar(tb)

        self.act_device = QAction("⌨  Устройство…", self)
        self.act_device.triggered.connect(self.choose_device)
        self.act_learn = QAction("＋ Кнопка", self)
        self.act_learn.setCheckable(True)
        self.act_learn.setToolTip("Нажимайте кнопки на мини-клавиатуре, они появятся на поле")
        self.act_learn.toggled.connect(self.set_learning)
        self.act_encoder = QAction("＋ Крутилка", self)
        self.act_encoder.triggered.connect(self.add_encoder)
        self.act_arrange = QAction("Упорядочить", self)
        self.act_arrange.triggered.connect(self.arrange)
        self.act_enabled = QAction("● Перехват включён", self)
        self.act_enabled.setCheckable(True)
        self.act_enabled.setChecked(True)
        self.act_enabled.toggled.connect(self.set_enabled)

        # --- меню «Настройки» ---
        self.act_block = QAction("Глушить неназначенные кнопки", self)
        self.act_block.setCheckable(True)
        self.act_block.setChecked(self.doc["settings"].get("unmapped", "block") == "block")
        self.act_block.toggled.connect(self._set_unmapped)

        # НОВОЕ: автозагрузка (реестр или Планировщик, см. autostart.py)
        self.act_autostart = QAction("Запускать вместе с Windows", self)
        self.act_autostart.setCheckable(True)
        try:
            self.act_autostart.setChecked(autostart.status() is not None)
        except Exception as exc:  # нет доступа к реестру/schtasks: просто показываем «выключено»
            log.warning("не удалось проверить автозапуск: %s", exc)
        self.act_autostart.setEnabled(sys.platform == "win32")
        self.act_autostart.toggled.connect(self._set_autostart)

        # НОВОЕ: запуск сразу в трей
        self.act_start_min = QAction("Запускать свернутой", self)
        self.act_start_min.setCheckable(True)
        self.act_start_min.setChecked(bool(self.root["app"].get("start_minimized")))
        self.act_start_min.setToolTip("При запуске окно не показывается, программа сразу уходит в трей")
        self.act_start_min.toggled.connect(self._set_start_minimized)

        settings = QMenu(self)
        settings.addAction(self.act_block)
        settings.addSeparator()
        settings.addAction(self.act_autostart)
        settings.addAction(self.act_start_min)
        settings.addSeparator()
        settings.addAction("Открыть папку с профилями", self._open_profile_folder)
        settings_btn = QToolButton()
        settings_btn.setText("⚙ Настройки")
        settings_btn.setMenu(settings)
        settings_btn.setPopupMode(QToolButton.InstantPopup)

        for act in (self.act_device, self.act_learn, self.act_encoder, self.act_arrange):
            tb.addAction(act)
        tb.addWidget(settings_btn)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        spacer.setStyleSheet("background: transparent;")
        tb.addWidget(spacer)
        tb.addAction(self.act_enabled)

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(theme.app_icon(), self)
        self.tray.setToolTip("minikeys")
        menu = QMenu()
        menu.addAction("Открыть", self.show_window)
        self.tray_profiles = menu.addMenu("Профиль")   # НОВОЕ: переключение профиля из трея
        menu.addAction(self.act_enabled)
        menu.addSeparator()
        menu.addAction("Выход", self.quit)
        self.tray.setContextMenu(menu)
        self._tray_menu = menu
        self.tray.activated.connect(
            lambda reason: self.show_window() if reason in (QSystemTrayIcon.Trigger,
                                                            QSystemTrayIcon.DoubleClick) else None)
        self.tray.show()

    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._quitting or not QSystemTrayIcon.isSystemTrayAvailable():
            self.quit()
            event.accept()
            return
        event.ignore()
        self.hide()  # крестик сворачивает в трей, перехват продолжает работать
        if not self._tray_hint_shown:
            self._tray_hint_shown = True
            self.tray.showMessage("minikeys работает в фоне",
                                  "Бинды активны. Открыть окно или выйти можно через значок в трее.",
                                  theme.app_icon(), 4000)

    def quit(self) -> None:
        self._quitting = True
        self.tray.hide()
        self.service.stop()
        QApplication.quit()

    def _set_status(self, color: str, text: str) -> None:
        self.device_label.setText(f"<span style='color:{color}'>●</span>&nbsp; {text}")

    def _update_device_label(self) -> None:
        if not self.service_ok:
            return
        prefix = f"Профиль «{self.root['active']}»: "
        device = device_text(self.doc["device"])
        if not store.device_configured(self.doc):
            self._set_status(theme.WARN, prefix + "мини-клавиатура не выбрана, нажмите «Устройство»")
        elif not self.act_enabled.isChecked():
            self._set_status(theme.SUBTLE, prefix + f"{device}, перехват выключен, все кнопки работают как обычно")
        elif self._targets:
            numbers = ", ".join(f"№{d}" for d in sorted(self._targets))
            self._set_status(theme.OK, prefix + f"{device} подключена ({numbers}), бинды работают")
        else:
            self._set_status(theme.WARN, prefix + f"{device} не найдена, подключите мини-клавиатуру")

    # --- сохранение ----------------------------------------------------------------
    def save(self) -> None:
        """Сохранить profile.json и сразу применить активный профиль в перехватчике."""
        try:
            store.save_root(self.root, self.profile_path)
        except OSError as exc:
            QMessageBox.critical(self, "Не удалось сохранить", str(exc))
        try:
            self.service.set_profile(store.build_profile(self.doc))
        except ConfigError as exc:
            QMessageBox.warning(self, "Ошибка в профиле", str(exc))
        self.board.refresh_texts()

    def _set_unmapped(self, block: bool) -> None:
        self.doc["settings"]["unmapped"] = "block" if block else "pass"
        self.save()

    def _set_start_minimized(self, on: bool) -> None:
        self.root["app"]["start_minimized"] = on
        self.save()

    def _set_autostart(self, on: bool) -> None:
        try:
            if on:
                method = autostart.enable()
                if method == autostart.REGISTRY:
                    QMessageBox.information(
                        self, "Автозапуск включён",
                        "minikeys будет запускаться при входе в Windows.\n\n"
                        "Программа сейчас работает без прав администратора, поэтому и при "
                        "автозапуске стартует без них: в окнах, запущенных от администратора "
                        "(Диспетчер задач, некоторые игры), бинды срабатывать не будут.\n\n"
                        "Если это нужно: запустите minikeys от имени администратора, снимите и "
                        "снова поставьте эту галочку. Тогда автозапуск пойдёт через Планировщик "
                        "заданий с нужными правами.")
            else:
                autostart.disable()
        except Exception as exc:
            QMessageBox.warning(self, "Автозапуск", str(exc))
            self.act_autostart.blockSignals(True)
            self.act_autostart.setChecked(not on)
            self.act_autostart.blockSignals(False)

    def _open_profile_folder(self) -> None:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.profile_path.parent)))

    # --- профили (НОВОЕ) ---------------------------------------------------------
    def _refresh_profiles(self) -> None:
        names = list(self.root["profiles"])
        self.profiles.set_profiles(names, self.root["active"])
        self.tray_profiles.clear()
        group = QActionGroup(self.tray_profiles)
        for name in names:
            act = self.tray_profiles.addAction(name)
            act.setCheckable(True)
            act.setChecked(name == self.root["active"])
            act.triggered.connect(lambda _checked=False, n=name: self.switch_profile(n))
            group.addAction(act)
        self.tray.setToolTip(f"minikeys: {self.root['active']}")

    def switch_profile(self, name: str) -> None:
        """Переключение на лету: перехватчик сразу получает бинды нового профиля."""
        if name == self.root["active"] or name not in self.root["profiles"]:
            return
        if self._learning:
            self.act_learn.setChecked(False)
        self.doc = store.set_active(self.root, name)
        self.board.set_document(self.doc)
        self.act_block.blockSignals(True)
        self.act_block.setChecked(self.doc["settings"].get("unmapped", "block") == "block")
        self.act_block.blockSignals(False)
        self.save()
        self._refresh_profiles()
        self._update_device_label()
        self.statusBar().showMessage(f"Профиль «{name}»", 2500)
        if not self.isVisible():
            self.tray.showMessage("minikeys", f"Профиль «{name}»", theme.app_icon(), 1500)

    def _ask_name(self, title: str, text: str, default: str) -> str | None:
        name, ok = QInputDialog.getText(self, title, text, text=default)
        return name.strip() if ok and name.strip() else None

    def new_profile(self) -> None:
        name = self._ask_name("Новый профиль",
                              "Название профиля.\nУстройство и расположение кнопок возьмутся из текущего, "
                              "бинды будут пустыми.", store.unique_name(self.root, "Профиль"))
        if name:
            self._create(name, copy_binds=False)

    def duplicate_profile(self, source: str) -> None:
        name = self._ask_name("Копия профиля", "Название копии:",
                              store.unique_name(self.root, f"{source} копия"))
        if name:
            self._create(name, copy_binds=True, template=self.root["profiles"][source])

    def _create(self, name: str, copy_binds: bool, template: dict | None = None) -> None:
        try:
            name = store.create_profile(self.root, name, template or self.doc, copy_binds)
        except ValueError as exc:
            QMessageBox.warning(self, "Профиль", str(exc))
            return
        self.switch_profile(name)

    def rename_profile(self, old: str) -> None:
        name = self._ask_name("Переименовать профиль", "Новое название:", old)
        if not name:
            return
        try:
            store.rename_profile(self.root, old, name)
        except ValueError as exc:
            QMessageBox.warning(self, "Профиль", str(exc))
            return
        store.save_root(self.root, self.profile_path)
        self._refresh_profiles()
        self._update_device_label()

    def delete_profile(self, name: str) -> None:
        if len(self.root["profiles"]) <= 1:
            return
        if QMessageBox.question(self, "Удалить профиль",
                                f"Удалить профиль «{name}» со всеми биндами?") != QMessageBox.Yes:
            return
        was_active = name == self.root["active"]
        store.delete_profile(self.root, name)
        if was_active:
            self.doc = store.active_profile(self.root)
            self.board.set_document(self.doc)
        self.save()
        self._refresh_profiles()
        self._update_device_label()

    # --- устройство --------------------------------------------------------------
    def choose_device(self) -> bool:
        if not self._require_service():
            return False
        dialog = DeviceDialog(self.service, self.bridge, self.doc["device"], self)
        if dialog.exec() != DeviceDialog.Accepted:
            return False
        self.doc["device"] = dialog.result_device
        self.save()
        self._update_device_label()
        return True

    def _on_targets(self, targets: dict) -> None:
        self._targets = targets
        self._update_device_label()

    def _on_service_stopped(self, error: str) -> None:
        if self._quitting:
            return
        self.service_ok = False
        self._set_status(theme.ERROR, f"Перехватчик остановился: {error or 'неизвестная причина'}. "
                                      "Перезапустите программу.")

    def _require_service(self) -> bool:
        if self.service_ok:
            return True
        QMessageBox.warning(self, "Перехват не работает",
                            "Драйвер Interception недоступен, поэтому нельзя слушать мини-клавиатуру.\n"
                            "Проверьте установку драйвера (см. README) и перезапустите программу.\n\n"
                            "Раскладку и бинды при этом можно редактировать.")
        return False

    def set_enabled(self, on: bool) -> None:
        if self._learning and not on:
            self.act_learn.setChecked(False)
        self.service.set_mode(Mode.RUN if on else Mode.PAUSED)
        self.act_enabled.setText("● Перехват включён" if on else "○ Перехват выключен")
        self.tray.setIcon(theme.app_icon(on))
        self._update_device_label()

    # --- обучение: добавление кнопок --------------------------------------------
    def _ensure_device(self) -> bool:
        if not self._require_service():
            return False
        if store.device_configured(self.doc):
            return True
        QMessageBox.information(self, "Сначала выберите устройство",
                                "Укажите, какая клавиатура является мини-клавиатурой. "
                                "Остальные клавиатуры программа трогать не будет.")
        return self.choose_device()

    def set_learning(self, on: bool) -> None:
        if on and not self._ensure_device():
            self.act_learn.setChecked(False)
            return
        self._learning = on
        if on:
            self.act_enabled.setChecked(True)
            self.service.set_mode(Mode.LEARN)
            self.banner.setText("Режим добавления: нажимайте кнопки на мини-клавиатуре, каждая новая "
                                "появится на поле. Бинды сейчас не срабатывают. "
                                "Когда закончите, снова нажмите «＋ Кнопка».")
            self.banner.show()
        else:
            self.banner.hide()
            if self._wizard is None:
                self.service.set_mode(Mode.RUN if self.act_enabled.isChecked() else Mode.PAUSED)

    def _on_learned(self, key: str) -> None:
        if self._wizard is not None:
            self._wizard.feed(key)
            return
        if not self._learning:
            return
        if key in store.all_layout_keys(self.doc):
            self._flash(key)
            self.statusBar().showMessage(f"Кнопка «{key}» уже есть на поле", 2500)
            return
        store.place_key(self.doc, key)
        self.board.set_document(self.doc)
        self.save()
        self._flash(key)
        self.statusBar().showMessage(f"Добавлена кнопка «{key}»", 2500)

    def add_encoder(self) -> None:
        if not self._ensure_device():
            return
        self.act_learn.setChecked(False)
        self._wizard = EncoderWizard(self)
        self.service.set_mode(Mode.LEARN)
        try:
            accepted = self._wizard.exec() == EncoderWizard.Accepted
            keys_found = self._wizard.result_keys
        finally:
            self._wizard = None
            self.service.set_mode(Mode.RUN if self.act_enabled.isChecked() else Mode.PAUSED)
        if not accepted:
            return
        enc = store.add_encoder(self.doc, keys_found["left"], keys_found["right"], keys_found.get("press"))
        self.board.set_document(self.doc)
        self.save()
        self.edit_encoder(enc["id"], "left")

    # --- редактирование ----------------------------------------------------------
    def edit_key(self, key: str) -> None:
        layout = self.doc["layout"]["keys"].setdefault(key, {"x": 0, "y": 0})
        dialog = KeyDialog(key, store.get_bind(self.doc, key), layout.get("label", ""), self)
        if dialog.exec() != KeyDialog.Accepted:
            return
        caption = dialog.caption.text().strip()
        if caption:
            layout["label"] = caption
        else:
            layout.pop("label", None)
        store.set_bind(self.doc, key, dialog.result_spec)
        self.save()

    def edit_encoder(self, enc_id: str, part: str) -> None:
        enc = next((e for e in self.doc["layout"]["encoders"] if e["id"] == enc_id), None)
        if enc is None:
            return
        dialog = EncoderDialog(enc, self.doc["binds"], part, self)
        if dialog.exec() != EncoderDialog.Accepted:
            return
        enc["name"] = dialog.name.text().strip() or enc.get("name", "")
        for p, spec in dialog.result_specs.items():
            store.set_bind(self.doc, enc[p], spec)
        self.save()

    def _key_menu(self, key: str, pos: QPoint) -> None:
        menu = QMenu(self)
        menu.addAction("Настроить…", lambda: self.edit_key(key))
        menu.addAction("Переименовать…", lambda: self._rename_key(key))
        menu.addSeparator()
        menu.addAction("Удалить кнопку", lambda: self._remove(lambda: store.remove_key(self.doc, key)))
        menu.exec(pos)

    def _encoder_menu(self, enc_id: str, pos: QPoint) -> None:
        menu = QMenu(self)
        for part, title in (("left", "Поворот влево…"), ("right", "Поворот вправо…"), ("press", "Нажатие…")):
            menu.addAction(title, lambda p=part: self.edit_encoder(enc_id, p))
        menu.addSeparator()
        menu.addAction("Удалить крутилку", lambda: self._remove(lambda: store.remove_encoder(self.doc, enc_id)))
        menu.exec(pos)

    def _rename_key(self, key: str) -> None:
        layout = self.doc["layout"]["keys"].setdefault(key, {"x": 0, "y": 0})
        text, ok = QInputDialog.getText(self, "Подпись кнопки", "Подпись на поле:", text=layout.get("label", ""))
        if ok:
            if text.strip():
                layout["label"] = text.strip()
            else:
                layout.pop("label", None)
            self.save()

    def _remove(self, action) -> None:
        if QMessageBox.question(self, "Удалить", "Удалить элемент и его бинды?") != QMessageBox.Yes:
            return
        action()
        self.board.set_document(self.doc)
        self.save()

    def arrange(self) -> None:
        store.auto_arrange(self.doc)
        self.board.set_document(self.doc)
        self.save()

    # --- подсветка ---------------------------------------------------------------
    def _on_key_event(self, key: str, is_down: bool) -> None:
        self.board.light_key(key, is_down)

    def _flash(self, key: str) -> None:
        if self.board.light_key(key, True):
            QTimer.singleShot(300, lambda: self.board.light_key(key, False))


# --- запуск ------------------------------------------------------------------------
def _setup_logging(folder: Path) -> None:
    handlers: list[logging.Handler] = []
    try:
        handlers.append(logging.handlers.RotatingFileHandler(
            folder / "minikeys.log", maxBytes=1_000_000, backupCount=1, encoding="utf-8"))
    except OSError:
        pass
    if sys.stderr is not None:  # в minikeys.exe (windowed) консоли нет
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S", handlers=handlers or [logging.NullHandler()])


def main(argv: list[str] | None = None, service_factory=None) -> int:
    parser = argparse.ArgumentParser(prog="minikeys")
    parser.add_argument("--minimized", action="store_true", help="запуститься свернутой в трей")
    parser.add_argument("--autostart", action="store_true",
                        help="запуск из автозагрузки Windows (ставится автоматически)")
    parser.add_argument("--config", help="путь к profile.json (по умолчанию рядом с программой)")
    parser.add_argument("--dll", help="путь к interception.dll")
    args, _unknown = parser.parse_known_args(argv)

    profile_path = Path(args.config) if args.config else store.default_path()
    _setup_logging(profile_path.parent)

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("minikeys")
    app.setStyle("Fusion")
    app.setStyleSheet(theme.STYLESHEET)
    app.setWindowIcon(theme.app_icon())
    app.setQuitOnLastWindowClosed(False)  # закрытие окна = сворачивание в трей

    mutex = None
    if sys.platform == "win32":
        from ..service import acquire_single_instance
        mutex = acquire_single_instance()
        if mutex is None:
            if not args.autostart:
                QMessageBox.information(None, "minikeys", "minikeys уже запущена, ищите значок в трее.")
            return 0

    try:
        root = store.load_root(profile_path)
    except ConfigError as exc:
        QMessageBox.critical(None, "minikeys", f"Профиль повреждён:\n{exc}")
        return 1
    if not profile_path.exists():
        store.save_root(root, profile_path)  # первый запуск: фиксируем перенос из config.toml
    try:
        autostart.refresh()  # программу перенесли в другую папку: поправить путь автозапуска
    except Exception as exc:
        log.warning("не удалось обновить автозапуск: %s", exc)

    bridge = ServiceBridge()
    service = (service_factory or RemapService)(bridge, args.dll)
    # окно подписывается на сигналы ДО запуска сервиса, чтобы первые события не потерялись
    window = MainWindow(service, bridge, root, profile_path)
    try:
        service.set_profile(store.build_profile(store.active_profile(root)))
        service.start()
    except ConfigError as exc:
        window.set_service_error(f"ошибка в профиле: {exc}")
    except Exception as exc:
        log.error("перехватчик не запущен: %s", exc)
        window.set_service_error(str(exc))

    minimized = args.minimized or root["app"].get("start_minimized", False)
    if not minimized or not QSystemTrayIcon.isSystemTrayAvailable():
        window.show()
    log.info("minikeys %s запущена (%s), профиль: %s, файл: %s", __version__,
             "exe" if paths.FROZEN else "исходники", root["active"], profile_path)
    code = app.exec()
    service.stop()
    del mutex
    return code
