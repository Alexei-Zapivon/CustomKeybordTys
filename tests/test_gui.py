"""Интерфейс без экрана (QT_QPA_PLATFORM=offscreen) на имитации драйвера."""

import os
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication
except ImportError:  # PySide6 не установлен — GUI-тесты пропускаются
    QApplication = None

from minikeys import store
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
        self.doc = store.load_document(self.path, Path(__file__).parent.parent / "config.toml")
        self.service.set_profile(store.build_profile(self.doc))
        self.service.start()
        self.win = MainWindow(self.service, self.bridge, self.doc, self.path)
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
        self.assertIn("a", store.load_document(self.path, None)["layout"]["keys"])
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
        dlg = DeviceDialog(self.service, self.bridge, "VID_1189&PID_8840", self.win)
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
            code = gui_app.main(["--profile", str(path)], service_factory=factory)
            self.assertEqual(code, 0)
            self.assertTrue(path.exists())            # профиль создан при первом запуске
        self.assertFalse(created[0].running)          # перехватчик остановлен при выходе
        self.assertTrue(ic.closed)


if __name__ == "__main__":
    unittest.main()
