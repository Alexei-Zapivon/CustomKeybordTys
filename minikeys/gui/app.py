"""Главное окно minikeys: поле с кнопками, панель инструментов, трей.

Архитектура:
  GUI-поток (Qt)            ←сигналы─  ServiceBridge  ←вызовы─  поток RemapService (драйвер)
       │ set_profile / set_mode / list_devices (очередь команд) ──────────────►
Интерфейс только редактирует документ профиля (profile.json) и отдаёт готовый
Profile перехватчику; перехват идёт в своём потоке и не зависит от окна.
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (QApplication, QInputDialog, QLabel, QMainWindow, QMenu,
                               QMessageBox, QSizePolicy, QSystemTrayIcon, QToolBar, QToolButton, QVBoxLayout,
                               QWidget)

from .. import __version__, store
from ..config import ConfigError
from ..service import Mode, RemapService
from . import theme
from .bridge import ServiceBridge
from .canvas import Board
from .dialogs import DeviceDialog, EncoderDialog, EncoderWizard, KeyDialog

log = logging.getLogger("minikeys")


class MainWindow(QMainWindow):
    def __init__(self, service: RemapService, bridge: ServiceBridge, doc: dict,
                 profile_path: Path) -> None:
        super().__init__()
        self.service = service
        self.bridge = bridge
        self.doc = doc
        self.profile_path = profile_path
        self.service_ok = True
        self._learning = False
        self._wizard: EncoderWizard | None = None
        self._quitting = False
        self._tray_hint_shown = False
        self._targets: dict[int, list[str]] = service.targets  # то, что сервис нашёл до нас

        self.setWindowTitle("minikeys — мини-клавиатура")
        self.setWindowIcon(theme.app_icon())
        self.resize(980, 640)

        self._build_toolbar()
        self.banner = QLabel()
        self.banner.setObjectName("banner")
        self.banner.setWordWrap(True)
        self.banner.hide()
        self.board = Board()
        central = QWidget()
        lay = QVBoxLayout(central)
        lay.setContentsMargins(10, 10, 10, 10)
        lay.addWidget(self.banner)
        lay.addWidget(self.board, 1)
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
        bridge.targets_changed.connect(self._on_targets)
        bridge.key_event.connect(self._on_key_event)
        bridge.learned.connect(self._on_learned)
        bridge.stopped.connect(self._on_service_stopped)

        self.board.set_document(self.doc)
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
        self.act_learn.setToolTip("Нажимайте кнопки на мини-клавиатуре — они появятся на поле")
        self.act_learn.toggled.connect(self.set_learning)
        self.act_encoder = QAction("＋ Крутилка", self)
        self.act_encoder.triggered.connect(self.add_encoder)
        self.act_arrange = QAction("Упорядочить", self)
        self.act_arrange.triggered.connect(self.arrange)
        self.act_enabled = QAction("● Перехват включён", self)
        self.act_enabled.setCheckable(True)
        self.act_enabled.setChecked(True)
        self.act_enabled.toggled.connect(self.set_enabled)

        self.act_block = QAction("Глушить неназначенные кнопки", self)
        self.act_block.setCheckable(True)
        self.act_block.setChecked(self.doc["settings"].get("unmapped", "block") == "block")
        self.act_block.toggled.connect(self._set_unmapped)
        settings = QMenu(self)
        settings.addAction(self.act_block)
        settings.addSeparator()
        settings.addAction("Открыть папку с профилем", self._open_profile_folder)
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
                                  "Бинды активны. Открыть окно или выйти — через значок в трее.",
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
        match = self.doc["device"].get("match") or ""
        if not store.device_configured(self.doc):
            self._set_status(theme.WARN, "Мини-клавиатура не выбрана — нажмите «Устройство…»")
        elif not self.act_enabled.isChecked():
            self._set_status(theme.SUBTLE, f"{match}: перехват выключен — все кнопки работают как обычно")
        elif self._targets:
            numbers = ", ".join(f"№{d}" for d in sorted(self._targets))
            self._set_status(theme.OK, f"{match} подключена ({numbers}) — бинды работают")
        else:
            self._set_status(theme.WARN, f"{match} не найдена — подключите мини-клавиатуру")

    # --- профиль -----------------------------------------------------------------
    def save(self) -> None:
        """Сохранить профиль и сразу применить его в перехватчике."""
        try:
            store.save_document(self.doc, self.profile_path)
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

    def _open_profile_folder(self) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.profile_path.parent)))

    # --- устройство --------------------------------------------------------------
    def choose_device(self) -> bool:
        if not self._require_service():
            return False
        dialog = DeviceDialog(self.service, self.bridge, self.doc["device"].get("match") or "", self)
        if dialog.exec() != DeviceDialog.Accepted:
            return False
        self.doc["device"] = {"match": dialog.result_match}
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
                                "Укажите, какая клавиатура — мини-клавиатура. "
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
            self.banner.setText("Режим добавления: нажимайте кнопки на мини-клавиатуре — каждая новая "
                                "появится на поле. Бинды сейчас не срабатывают. "
                                "Закончили — снова нажмите «＋ Кнопка».")
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
def _setup_logging() -> None:
    handlers: list[logging.Handler] = []
    try:
        handlers.append(logging.handlers.RotatingFileHandler(
            store.ROOT / "minikeys.log", maxBytes=1_000_000, backupCount=1, encoding="utf-8"))
    except OSError:
        pass
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        datefmt="%H:%M:%S", handlers=handlers or [logging.NullHandler()])


def main(argv: list[str] | None = None, service_factory=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m minikeys.gui")
    parser.add_argument("--minimized", action="store_true", help="запуститься свёрнутым в трей")
    parser.add_argument("--profile", default=str(store.DEFAULT_PATH), help="путь к profile.json")
    parser.add_argument("--dll", help="путь к interception.dll")
    args = parser.parse_args(argv)
    _setup_logging()

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("minikeys")
    app.setStyle("Fusion")
    app.setStyleSheet(theme.STYLESHEET)
    app.setQuitOnLastWindowClosed(False)  # закрытие окна = сворачивание в трей

    mutex = None
    if sys.platform == "win32":
        from ..service import acquire_single_instance
        mutex = acquire_single_instance()
        if mutex is None:
            QMessageBox.information(None, "minikeys", "minikeys уже запущен — ищите значок в трее.")
            return 0

    profile_path = Path(args.profile)
    try:
        doc = store.load_document(profile_path)
    except ConfigError as exc:
        QMessageBox.critical(None, "minikeys", f"Профиль повреждён:\n{exc}")
        return 1
    if not profile_path.exists():
        store.save_document(doc, profile_path)  # первый запуск: фиксируем перенос из config.toml

    bridge = ServiceBridge()
    service = (service_factory or RemapService)(bridge, args.dll)
    # окно подписывается на сигналы ДО запуска сервиса — первые события не потеряются
    window = MainWindow(service, bridge, doc, profile_path)
    try:
        service.set_profile(store.build_profile(doc))
        service.start()
    except ConfigError as exc:
        window.set_service_error(f"ошибка в профиле: {exc}")
    except Exception as exc:
        log.error("перехватчик не запущен: %s", exc)
        window.set_service_error(str(exc))
    if not args.minimized or not QSystemTrayIcon.isSystemTrayAvailable():
        window.show()
    log.info("minikeys GUI %s запущен, профиль: %s", __version__, profile_path)
    code = app.exec()
    service.stop()
    del mutex
    return code
