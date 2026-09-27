"""Интерфейс без экрана (QT_QPA_PLATFORM=offscreen) на имитации драйвера."""

import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QAction, QKeyEvent
    from PySide6.QtWidgets import QApplication
except ImportError:  # PySide6 не установлен — GUI-тесты пропускаются
    QApplication = None

from minikeys import keys, store
from minikeys.service import Mode, RemapService

from .test_service import MAIN, MINI, FakeInterception, FakeOutput

SHOTS = os.environ.get("MINIKEYS_SHOTS")  # папка для скриншотов (по желанию)


@unittest.skipIf(QApplication is None, "PySide6 не установлен")
class GuiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from minikeys.gui import theme
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")
        cls.app.setStyleSheet(theme.STYLESHEET)

    def setUp(self):
        from minikeys.gui.app import MainWindow
        from minikeys.gui.bridge import ServiceBridge
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "profile.json"
        self.ic = FakeInterception()
        self.bridge = ServiceBridge()
        self.service = RemapService(self.bridge, interception_factory=lambda dll: self.ic,
                                    output_factory=FakeOutput)
        self.root = store.load_root(self.path, Path(__file__).parent.parent / "config.toml")
        self.doc = store.active_profile(self.root)
        self.service.set_profile(store.build_profile(self.doc))
        self.service.start()
        self.win = MainWindow(self.service, self.bridge, self.root, self.path)
        self.win.resize(1000, 640)
        self.win.show()
        self.pump()

    def tearDown(self):
        self.service.stop()
        self.win._quitting = True
        self.win.close()
        self.tmp.cleanup()

    def pump(self, seconds=0.35):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.01)

    def shot(self, widget, name):
        if SHOTS:
            widget.grab().save(str(Path(SHOTS) / name))

    def test_migrated_layout_and_device_status(self):
        self.assertEqual(len(self.win.board._keys), 18)   # бинды из config.toml
        self.assertIn("подключена", self.win.device_label.text())
        self.assertEqual(self.ic.captured, {MINI})
        self.shot(self.win, "main.png")

    def test_learn_adds_button_and_saves(self):
        store.remove_key(self.doc, "a")
        self.win.board.set_document(self.doc)
        self.win.act_learn.setChecked(True)
        self.pump()
        self.assertEqual(self.service._mode, Mode.LEARN)
        self.ic.pending.append((MINI, 0x1E, 0))            # физическая «a»
        self.pump()
        self.assertIn("a", self.doc["layout"]["keys"])
        saved = store.active_profile(store.load_root(self.path, import_legacy=False))
        self.assertIn("a", saved["layout"]["keys"])
        self.assertEqual(self.ic.sent, [])                  # не ушла в систему
        self.shot(self.win, "learn.png")
        self.win.act_learn.setChecked(False)
        self.pump()
        self.assertEqual(self.service._mode, Mode.RUN)

    def test_physical_press_lights_button(self):
        self.ic.pending.append((MINI, 0x1E, 0))
        self.pump()
        self.assertTrue(self.win.board._keys["a"]._lit)

    def test_key_dialog_and_recording(self):
        from minikeys.gui.dialogs import KeyDialog
        dlg = KeyDialog("a", "ctrl+shift+m", "", self.win)
        self.assertEqual(dlg.editor.kind.currentData(), "hotkey")
        dlg.editor.hotkey.record.setChecked(True)
        ev = QKeyEvent(QEvent.KeyPress, Qt.Key_K, Qt.ControlModifier | Qt.AltModifier, "k")
        self.app.sendEvent(dlg.editor.hotkey.edit, ev)
        self.assertEqual(dlg.editor.hotkey.text(), "ctrl+alt+k")
        self.assertFalse(dlg.editor.hotkey.record.isChecked())
        self.shot(dlg, "key_dialog.png")
        dlg.accept()
        self.assertEqual(dlg.result_spec, "ctrl+alt+k")

    def test_action_types_roundtrip(self):
        from minikeys.gui.dialogs import ActionEditor
        ed = ActionEditor()
        for spec in ("ctrl+c", {"hotkey": "ctrl+z", "repeat": True}, "media_play_pause",
                     {"hotkey": "volume_up", "repeat": True}, {"run": "notepad.exe", "args": "x.txt"},
                     {"text": "Привет\nмир"}, {"macro": ["ctrl+c", {"delay": 100}, "ctrl+v"]},
                     {"cmd": "echo 1"}, {"remap": "ctrl"}, None):
            ed.set_spec(spec)
            self.assertEqual(ed.spec(), spec)
        ed.set_spec({"macro": ["ctrl+c"]})
        ed.macro.setPlainText("ctrl+nosuch")
        with self.assertRaises(ValueError):
            ed.spec()

    def test_discord_free_key_preset(self):
        from minikeys.gui.dialogs import ActionEditor, KeyDialog
        ed = ActionEditor(used_free_keys=frozenset({"f13", "f14"}))
        ed.kind.setCurrentIndex(ed.kind.findData("freekey"))
        self.assertEqual(ed.spec(), "f15")                   # первая свободная
        self.assertIn("F15", ed.free_help.text())
        ed.free_hold.setChecked(True)
        self.assertEqual(ed.spec(), {"remap": "f15"})        # режим рации
        for spec in ("f13", {"remap": "f20"}):
            ed.set_spec(spec)
            self.assertEqual(ed.kind.currentData(), "freekey")
            self.assertEqual(ed.spec(), spec)
        # окно кнопки: F13 уже занята другой кнопкой профиля
        store.set_bind(self.doc, "b", "f13")
        dlg = KeyDialog("a", None, "", self.win, used_free_keys=self.win._used_free_keys({"a"}))
        dlg.editor.kind.setCurrentIndex(dlg.editor.kind.findData("freekey"))
        self.assertEqual(dlg.editor.free_key.currentData(), "f14")
        self.shot(dlg, "discord_preset.png")

    def test_toggle_window_from_mini_keyboard(self):
        from minikeys.gui.dialogs import ActionEditor
        ed = ActionEditor()
        ed.kind.setCurrentIndex(ed.kind.findData("app"))
        self.assertEqual(ed.spec(), {"app": "toggle_window"})
        ed.set_spec({"app": "toggle_window"})
        self.assertEqual(ed.kind.currentData(), "app")

        # бинд на физической кнопке «a»; окно спрятано в трей
        store.set_bind(self.doc, "a", {"app": "toggle_window"})
        self.win.save()
        self.win.board.refresh_texts()
        self.assertEqual(self.win.board._keys["a"].caption, "⧉ Окно minikeys")
        self.win.hide()
        self.pump()
        self.ic.pending.append((MINI, 0x1E, 0))            # нажатие → поток сервиса → поток действий
        self.pump(0.6)                                      # → Qt-сигнал → GUI-поток
        self.assertTrue(self.win.isVisible())
        self.assertEqual(self.ic.sent, [])
        # окно открыто и активно: повторное нажатие прячет его в трей
        self.win.isActiveWindow = lambda: True
        self.ic.pending += [(MINI, 0x1E, keys.KEY_UP), (MINI, 0x1E, 0)]
        self.pump(0.6)
        self.assertFalse(self.win.isVisible())

    def test_output_settings_menu(self):
        self.win._set_app("press_ms", 50)
        self.win._set_app("output", "driver")
        self.pump()
        self.assertEqual((self.service._profile.press_ms, self.service._profile.output), (50, "driver"))
        saved = store.load_root(self.path, import_legacy=False)["app"]
        self.assertEqual((saved["press_ms"], saved["output"]), (50, "driver"))

    def test_encoder_wizard_and_dialog(self):
        from minikeys.gui.dialogs import EncoderDialog, EncoderWizard
        wiz = EncoderWizard(self.win)
        wiz.feed("1")
        wiz.feed("1")          # тот же — игнор
        wiz.feed("2")
        wiz.feed("3")
        self.assertEqual(wiz.result_keys, {"left": "1", "right": "2", "press": "3"})
        enc = store.add_encoder(self.doc, "1", "2", "3")
        self.win.board.set_document(self.doc)
        dlg = EncoderDialog(enc, self.doc["binds"], "right", self.win)
        self.assertEqual(dlg.tabs.currentIndex(), 1)
        self.shot(dlg, "encoder_dialog.png")
        self.shot(self.win, "with_encoder.png")
        self.assertTrue(self.win.board.light_key("2", True))
        self.assertEqual(self.win.board._encoders[enc["id"]]._lit, "right")

    def test_device_dialog_probe(self):
        from minikeys.gui.dialogs import DeviceDialog
        dlg = DeviceDialog(self.service, self.bridge, {"match": "VID_1189&PID_8840"}, self.win)
        self.assertEqual(dlg.list.count(), 2)
        self.assertIn("выбрана сейчас", dlg.list.currentItem().text())
        dlg.detect.setChecked(True)
        self.pump()
        self.assertEqual(self.ic.captured, {MAIN, MINI})
        self.ic.pending.append((MAIN, 0x2E, 0))
        self.pump()
        self.assertEqual(dlg.list.currentItem().data(Qt.UserRole)[0], MAIN)
        self.assertEqual(self.ic.sent, [(MAIN, 0x2E, 0)])   # основная клавиатура не заглушена
        self.shot(dlg, "device_dialog.png")
        dlg.reject()
        self.pump()
        self.assertEqual(self.ic.captured, {MINI})

    def test_title_and_no_long_dashes(self):
        from PySide6.QtWidgets import QAbstractButton, QLabel
        self.assertEqual(self.win.windowTitle(), "minikeys")
        texts = [w.text() for w in self.win.findChildren(QLabel) + self.win.findChildren(QAbstractButton)]
        texts += [a.text() for a in self.win.findChildren(QAction)]
        texts += [a.text() for a in self.win.tray.contextMenu().actions()]
        texts.append(self.win.banner.text())
        self.win.act_learn.setChecked(True)
        self.pump()
        texts.append(self.win.banner.text())
        self.win.act_learn.setChecked(False)
        bad = [t for t in texts if "—" in t or "–" in t]
        self.assertEqual(bad, [])

    def test_profiles_switch_on_the_fly(self):
        self.assertEqual(self.win.profiles.list.count(), 1)
        self.win._create("Gaming", copy_binds=False)          # как кнопка «＋ Новый профиль»
        self.pump()
        self.assertEqual(self.root["active"], "Gaming")
        self.assertEqual(self.win.profiles.current_name(), "Gaming")
        self.assertEqual(self.service._profile.binds, {})     # бинды нового профиля пустые
        self.assertEqual(len(self.win.board._keys), 18)       # раскладка скопирована
        self.shot(self.win, "profiles.png")
        # физическая «a» в пустом профиле ничего не делает, но и не печатается
        self.ic.pending.append((MINI, 0x1E, 0))
        self.pump()
        self.assertEqual(self.ic.sent, [])
        # обратно на Default: бинды вернулись
        self.win.profiles.list.setCurrentRow(0)
        self.pump()
        self.assertEqual(self.root["active"], "Default")
        self.assertIn("a", self.service._profile.binds)
        tray_names = [a.text() for a in self.win.tray_profiles.actions()]
        self.assertEqual(tray_names, ["Default", "Gaming"])
        # сохранено в файл в формате v2
        saved = store.load_root(self.path, import_legacy=False)
        self.assertEqual(list(saved["profiles"]), ["Default", "Gaming"])
        self.assertEqual(saved["active"], "Default")

    def test_delete_active_profile(self):
        from PySide6.QtWidgets import QMessageBox
        self.win._create("Gaming", copy_binds=True)
        orig = QMessageBox.question
        QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
        try:
            self.win.delete_profile("Gaming")
        finally:
            QMessageBox.question = orig
        self.assertEqual(list(self.root["profiles"]), ["Default"])
        self.assertEqual(self.root["active"], "Default")
        self.assertIs(self.win.doc, self.root["profiles"]["Default"])

    def test_start_minimized_setting(self):
        self.win.act_start_min.setChecked(True)
        self.assertTrue(store.load_root(self.path, import_legacy=False)["app"]["start_minimized"])

    def test_device_dialog_two_identical(self):
        from minikeys.gui.dialogs import DeviceDialog
        self.ic.devices_now[5] = self.ic.devices_now[MINI]
        dlg = DeviceDialog(self.service, self.bridge, {"match": "VID_1189&PID_8840"}, self.win)
        self.assertEqual(dlg.list.count(), 3)
        self.assertIn("несколько устройств этой модели", dlg.same_hint.text())
        self.shot(dlg, "device_dialog_two.png")
        dlg.only_this.setChecked(True)
        dlg.accept()
        self.assertEqual(dlg.result_device, {"match": "VID_1189&PID_8840", "device_number": MINI})


@unittest.skipIf(QApplication is None, "PySide6 не установлен")
class MainEntryTest(unittest.TestCase):
    def test_main_starts_and_quits(self):
        from PySide6.QtCore import QTimer
        from minikeys.gui import app as gui_app
        QApplication.instance() or QApplication([])
        ic = FakeInterception()
        created = []

        def factory(bridge, dll):
            svc = RemapService(bridge, dll, interception_factory=lambda d: ic, output_factory=FakeOutput)
            created.append(svc)
            return svc

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profile.json"
            QTimer.singleShot(700, QApplication.quit)
            code = gui_app.main(["--config", str(path), "--no-elevate"], service_factory=factory)
            self.assertEqual(code, 0)
            self.assertTrue(path.exists())            # профиль создан при первом запуске
            self.assertIn("profiles", store.load_root(path, import_legacy=False))
        self.assertFalse(created[0].running)          # перехватчик остановлен при выходе
        self.assertTrue(ic.closed)


if __name__ == "__main__":
    unittest.main()
