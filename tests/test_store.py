import json
import tempfile
import unittest
from pathlib import Path

from minikeys import bindtext, store
from minikeys.config import ConfigError, load_profile, parse_binding


class BindTextTest(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(bindtext.label("ctrl+shift+m"), "Ctrl+Shift+M")
        self.assertEqual(bindtext.label({"hotkey": "volume_up", "repeat": True}), "Громк. +")
        self.assertEqual(bindtext.label("media_play_pause"), "Play / Pause")
        self.assertEqual(bindtext.label({"run": r"C:\Program Files\App\app.exe"}), "▶ app")
        self.assertEqual(bindtext.label({"run": "https://www.youtube.com/x"}), "▶ youtube.com")
        self.assertEqual(bindtext.label({"text": "Hi"}), "✎ Hi")
        self.assertEqual(bindtext.label({"macro": ["ctrl+c", {"delay": 5}]}), "⚙ Макрос (2)")
        self.assertEqual(bindtext.label({"remap": "ctrl"}), "⇩ Ctrl")
        self.assertEqual(bindtext.label(None), "")

    def test_macro_roundtrip(self):
        steps = ["ctrl+c", {"delay": 100}, {"text": "Привет\nмир \\ ok"},
                 {"run": "notepad.exe", "args": "a.txt"}, {"cmd": "echo 1"},
                 {"click": "left"}, {"down": "shift"}, {"up": "shift"}]
        text = bindtext.macro_to_text(steps)
        self.assertEqual(bindtext.text_to_macro(text), steps)
        parse_binding({"macro": steps}, "t")  # формат годится для перехватчика

    def test_macro_errors(self):
        for bad in ("", "пауза abc", "ctrl+nosuch", "текст"):
            with self.assertRaises(ValueError, msg=bad):
                bindtext.text_to_macro(bad)


class FreeKeyTest(unittest.TestCase):
    def test_free_key_of(self):
        self.assertEqual(bindtext.free_key_of("f13"), "f13")
        self.assertEqual(bindtext.free_key_of({"remap": "F24"}), "f24")
        self.assertIsNone(bindtext.free_key_of("ctrl+f13"))
        self.assertIsNone(bindtext.free_key_of("f12"))
        self.assertIsNone(bindtext.free_key_of({"hotkey": "f13", "repeat": True}))
        self.assertIsNone(bindtext.free_key_of({"run": "x"}))

    def test_app_settings_reach_profile(self):
        root = store.new_root()
        doc = store.active_profile(root)
        doc["device"]["match"] = "VID_1189&PID_8840"
        root["app"].update(press_ms=100, output="driver")
        p = store.build_profile(doc, root["app"])
        self.assertEqual((p.press_ms, p.output), (100, "driver"))
        self.assertEqual(store.normalize_app({"press_ms": "x", "output": "?"}), store.APP_DEFAULTS)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_migrates_from_toml(self):
        toml = self.dir / "config.toml"
        toml.write_text('[device]\nmatch = "VID_1189&PID_8840"\n[binds]\na = "ctrl+c"\nb = "ctrl+v"\n',
                        encoding="utf-8")
        root = store.load_root(self.dir / "profile.json", toml)
        doc = store.active_profile(root)
        self.assertEqual(root["active"], "Default")
        self.assertEqual(doc["device"]["match"], "VID_1189&PID_8840")
        self.assertEqual(set(doc["layout"]["keys"]), {"a", "b"})
        self.assertNotEqual(doc["layout"]["keys"]["a"], doc["layout"]["keys"]["b"])

    def test_migrates_v1_profile_json(self):
        path = self.dir / "profile.json"
        v1 = store.new_document()
        v1["device"]["match"] = "VID_1189&PID_8840"
        v1["binds"]["a"] = "ctrl+c"
        path.write_text(json.dumps(v1), encoding="utf-8")
        root = store.load_root(path, import_legacy=False)
        self.assertEqual(list(root["profiles"]), ["Default"])
        self.assertEqual(store.active_profile(root)["binds"], {"a": "ctrl+c"})
        self.assertFalse(root["app"]["start_minimized"])

    def test_save_load_and_console_compat(self):
        root = store.load_root(self.dir / "profile.json", import_legacy=False)
        doc = store.active_profile(root)
        doc["device"]["match"] = "VID_1189&PID_8840"
        store.set_bind(doc, "a", "ctrl+shift+m")
        store.place_key(doc, "a")
        enc = store.add_encoder(doc, "1", "2", "3")
        store.set_bind(doc, "1", {"hotkey": "volume_down", "repeat": True})
        store.create_profile(root, "Gaming", doc, copy_binds=False)
        store.set_bind(root["profiles"]["Gaming"], "a", "f13")
        path = self.dir / "profile.json"
        store.save_root(root, path)
        self.assertEqual(store.load_root(path), root)
        # консольная версия читает тот же файл: активный профиль или указанный
        self.assertEqual(set(load_profile(path).binds), {"a", "1"})
        self.assertEqual(set(load_profile(path, "Gaming").binds), {"a"})
        with self.assertRaises(ConfigError):
            load_profile(path, "Нет такого")
        self.assertEqual(store.find_encoder(doc, "2"), (enc, "right"))
        self.assertIn("Крутилка", json.loads(path.read_text(encoding="utf-8"))
                      ["profiles"]["Default"]["layout"]["encoders"][0]["name"])

    def test_profiles_crud(self):
        root = store.new_root()
        base = store.active_profile(root)
        base["device"]["match"] = "VID_1189&PID_8840"
        base["binds"]["a"] = "ctrl+c"
        store.place_key(base, "a")

        name = store.create_profile(root, "Gaming", base, copy_binds=False)
        gaming = root["profiles"][name]
        self.assertEqual(gaming["device"], base["device"])        # устройство и раскладка
        self.assertEqual(gaming["layout"], base["layout"])
        self.assertEqual(gaming["binds"], {})                     # бинды пустые
        gaming["layout"]["keys"]["a"]["x"] = 500                  # копия, а не ссылка
        self.assertNotEqual(base["layout"]["keys"]["a"]["x"], 500)

        copy_name = store.create_profile(root, store.unique_name(root, "Default"), base, copy_binds=True)
        self.assertEqual(copy_name, "Default 2")
        self.assertEqual(root["profiles"][copy_name]["binds"], {"a": "ctrl+c"})

        with self.assertRaises(ValueError):
            store.create_profile(root, "Gaming")                  # имя занято
        with self.assertRaises(ValueError):
            store.create_profile(root, "   ")

        store.set_active(root, "Gaming")
        store.rename_profile(root, "Gaming", "Игры")
        self.assertEqual(root["active"], "Игры")
        self.assertEqual(list(root["profiles"]), ["Default", "Игры", "Default 2"])  # порядок сохранён

        store.delete_profile(root, "Игры")
        self.assertEqual(root["active"], "Default")
        store.delete_profile(root, "Default 2")
        with self.assertRaises(ValueError):
            store.delete_profile(root, "Default")                 # последний не удаляется

    def test_encoder_takes_keys_from_buttons(self):
        doc = store.new_document()
        for k in ("1", "2", "a"):
            store.place_key(doc, k)
        store.add_encoder(doc, "1", "2", None)
        self.assertEqual(set(doc["layout"]["keys"]), {"a"})
        self.assertEqual(store.all_layout_keys(doc), {"a", "1", "2"})

    def test_free_position_does_not_overlap(self):
        doc = store.new_document()
        for i in range(20):
            store.place_key(doc, f"f{i + 1}")
        positions = {(p["x"], p["y"]) for p in doc["layout"]["keys"].values()}
        self.assertEqual(len(positions), 20)

    def test_build_profile_needs_device(self):
        doc = store.new_document()
        self.assertIsNone(store.build_profile(doc))
        doc["device"]["match"] = "VID_1189&PID_8840"
        self.assertIsNotNone(store.build_profile(doc))

    def test_remove(self):
        doc = store.new_document()
        store.place_key(doc, "a")
        store.set_bind(doc, "a", "ctrl+c")
        enc = store.add_encoder(doc, "1", "2", "3")
        store.set_bind(doc, "3", "volume_mute")
        store.remove_key(doc, "a")
        store.remove_encoder(doc, enc["id"])
        self.assertEqual(doc["binds"], {})
        self.assertEqual(store.all_layout_keys(doc), set())

    def test_match_from_hardware_id(self):
        self.assertEqual(store.match_from_hardware_id("HID\\VID_1189&PID_8840&REV_0100&MI_01&Col01"),
                         "VID_1189&PID_8840")
        self.assertEqual(store.match_from_hardware_id(
            "HID\\{00001124-0000-1000-8000-00805f9b34fb}_VID&0002046d_PID&b35b"),
            "VID&0002046D_PID&B35B")


if __name__ == "__main__":
    unittest.main()
