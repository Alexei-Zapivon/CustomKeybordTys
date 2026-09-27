import ctypes
import struct
import tomllib
import unittest
from pathlib import Path

from minikeys import keys
from minikeys.actions import Delay, Hotkey, KeyDown, KeyUp, Launch, Macro, Text
from minikeys.config import ConfigError, DeviceMatch, load_profile, parse_profile
from minikeys.engine import Engine, Router

ROOT = Path(__file__).resolve().parent.parent


def profile(binds, unmapped="block"):
    return parse_profile({"device": {"match": "VID_1189&PID_8890"},
                          "settings": {"unmapped": unmapped}, "binds": binds})


class KeysTest(unittest.TestCase):
    def test_input_names(self):
        self.assertEqual(keys.input_key("A"), "a")
        self.assertEqual(keys.input_key("Page Up"), "page_up")
        self.assertEqual(keys.input_key("pgup"), "page_up")
        self.assertEqual(keys.input_key("-"), "minus")
        self.assertEqual(keys.input_key("sc_1e"), "a")         # известный скан-код -> имя
        self.assertEqual(keys.input_key("sc_e0_1c"), "numpad_enter")
        self.assertEqual(keys.input_key("sc_7f"), "sc_7f")     # неизвестный остаётся сырым
        with self.assertRaises(keys.KeyNameError):
            keys.input_key("nosuchkey")
        with self.assertRaises(keys.KeyNameError):
            keys.input_key("volume_up_really")

    def test_stroke_names(self):
        self.assertEqual(keys.name_from_stroke(0x1E, 0), "a")
        self.assertEqual(keys.name_from_stroke(0x1E, keys.KEY_UP), "a")
        self.assertEqual(keys.name_from_stroke(0x1C, keys.KEY_E0), "numpad_enter")
        self.assertEqual(keys.name_from_stroke(0x1C, 0), "enter")
        self.assertEqual(keys.name_from_stroke(0x1D, keys.KEY_E1), "pause")
        self.assertEqual(keys.name_from_stroke(0x7F, keys.KEY_E0), "sc_e0_7f")
        self.assertEqual(keys.name_from_stroke(0x64, 0), "f13")
        self.assertEqual(keys.name_from_stroke(0x76, 0), "f24")

    def test_roundtrip_all_scancodes(self):
        for name, (code, prefix) in keys.SCANCODES.items():
            self.assertEqual(keys.name_from_stroke(code, prefix), name)
            self.assertEqual(keys.input_key(name), name)

    def test_chord(self):
        self.assertEqual(keys.parse_chord("Ctrl+Shift+M"), ["lctrl", "lshift", "m"])
        self.assertEqual(keys.parse_chord("win + d"), ["lwin", "d"])
        self.assertEqual(keys.parse_chord("volume up"), ["volume_up"])
        for bad in ("", "ctrl+", "ctrl++m", "ctrl+ctrl", "ctrl+nosuch"):
            with self.assertRaises(keys.KeyNameError, msg=bad):
                keys.parse_chord(bad)

    def test_vk_values(self):
        self.assertEqual(keys.VK["m"], 0x4D)
        self.assertEqual(keys.VK["f13"], 0x7C)
        self.assertEqual(keys.VK["f24"], 0x87)
        self.assertEqual(keys.VK["numpad0"], 0x60)


class StructLayoutTest(unittest.TestCase):
    """Размеры структур должны совпадать с Windows x64."""

    @unittest.skipUnless(struct.calcsize("P") == 8, "64-битный Python")
    def test_input_size(self):
        from minikeys.winput import INPUT
        self.assertEqual(ctypes.sizeof(INPUT), 40)

    def test_stroke_size(self):
        from minikeys.interception import KeyStroke, Stroke
        self.assertEqual(ctypes.sizeof(KeyStroke), 8)
        self.assertEqual(ctypes.sizeof(Stroke), 20)


class ConfigTest(unittest.TestCase):
    def test_example_config_is_valid(self):
        p = load_profile(ROOT / "config.toml")
        self.assertEqual(p.binds["a"].on_press, Hotkey(["lctrl", "lshift", "m"]))
        self.assertIsInstance(p.binds["e"].on_press, Launch)
        self.assertTrue(p.binds["2"].repeat)

    def test_forms(self):
        p = profile({
            "a": "ctrl+shift+m",
            "b": {"run": "notepad.exe", "args": ["a b", "c"]},
            "c": {"text": "Привет"},
            "d": {"remap": "shift"},
            "e": {"macro": ["ctrl+c", {"delay": 50}, {"down": "alt"}, {"up": "alt"}]},
            "f": {"hotkey": "volume_up", "repeat": True},
        })
        self.assertEqual(p.binds["b"].on_press, Launch("notepad.exe", '"a b" c'))
        self.assertEqual(p.binds["c"].on_press, Text("Привет"))
        self.assertEqual((p.binds["d"].on_press, p.binds["d"].on_release),
                         (KeyDown(["lshift"]), KeyUp(["lshift"])))
        self.assertEqual(p.binds["e"].on_press, Macro([Hotkey(["lctrl", "c"]), Delay(50),
                                                      KeyDown(["lalt"]), KeyUp(["lalt"])]))
        self.assertTrue(p.binds["f"].repeat)

    def test_errors(self):
        bad = [
            {"a": "ctrl+nosuch"},
            {"nosuchkey": "ctrl+c"},
            {"a": {"run": "x", "text": "y"}},            # два действия
            {"a": {"hotkey": "ctrl+c", "typo": 1}},      # неизвестный параметр
            {"a": {"macro": []}},
            {"a": {"macro": [{"delay": -1}]}},
            {"a": {"click": "side"}},
            {"a": "ctrl+c", "A": "ctrl+v"},              # одна клавиша дважды
            {"fake_lshift": "ctrl+c"},
        ]
        for binds in bad:
            with self.assertRaises(ConfigError, msg=binds):
                profile(binds)
        with self.assertRaises(ConfigError):
            parse_profile({"binds": {}})                 # нет [device]
        with self.assertRaises(ConfigError):
            parse_profile({"device": {}, "binds": {}})

    def test_output_settings(self):
        p = parse_profile({"device": {"match": "X"}, "settings": {"press_ms": 50, "output": "driver"}})
        self.assertEqual((p.press_ms, p.output), (50, "driver"))
        p = parse_profile({"device": {"match": "X"}})
        self.assertEqual((p.press_ms, p.output), (30, "sendinput"))
        for bad in ({"press_ms": -1}, {"press_ms": 501}, {"press_ms": True}, {"output": "magic"}):
            with self.assertRaises(ConfigError, msg=bad):
                parse_profile({"device": {"match": "X"}, "settings": bad})

    def test_device_match(self):
        dev = DeviceMatch(match=["vid_1189&pid_8890"], exclude=["MI_02"])
        self.assertTrue(dev.matches(3, ["HID\\VID_1189&PID_8890&REV_0100&MI_00"]))
        self.assertFalse(dev.matches(3, ["HID\\VID_1189&PID_8890&REV_0100&MI_02"]))
        self.assertFalse(dev.matches(3, ["HID\\VID_046D&PID_C52B&MI_00"]))
        self.assertTrue(DeviceMatch(device_number=4).matches(4, []))
        self.assertFalse(DeviceMatch(device_number=4).matches(5, ["anything"]))

    def test_toml_literal_path(self):
        data = tomllib.loads("[device]\nmatch='X'\n[binds]\nq = { run = 'C:\\Tools\\app.exe' }\n")
        self.assertEqual(parse_profile(data).binds["q"].on_press.target, "C:\\Tools\\app.exe")


class EngineTest(unittest.TestCase):
    def setUp(self):
        self.done = []
        self.engine = Engine(profile({
            "a": "ctrl+shift+m",
            "b": {"remap": "ctrl"},
            "c": {"hotkey": "volume_up", "repeat": True},
        }), self.done.append)

    def press(self, key, down=True):
        return self.engine.handle(key, down)

    def test_bound_key_fires_once_and_is_swallowed(self):
        self.assertTrue(self.press("a"))
        self.assertTrue(self.press("a"))           # автоповтор
        self.assertTrue(self.press("a", False))
        self.assertEqual(self.done, [Hotkey(["lctrl", "lshift", "m"])])

    def test_repeat(self):
        self.press("c"); self.press("c"); self.press("c", False)
        self.assertEqual(len(self.done), 2)

    def test_remap_hold(self):
        self.press("b"); self.press("b"); self.press("b", False)
        self.assertEqual(self.done, [KeyDown(["lctrl"]), KeyDown(["lctrl"]), KeyUp(["lctrl"])])

    def test_unmapped_block_and_pass(self):
        self.assertTrue(self.press("z"))
        self.engine.set_profile(profile({}, unmapped="pass"))
        self.assertFalse(self.press("z"))
        self.assertEqual(self.done, [])

    def test_release_uses_binding_from_press_after_reload(self):
        self.press("b")
        self.engine.set_profile(profile({}, unmapped="pass"))
        self.assertTrue(self.press("b", False))    # отпускание всё равно наше
        self.assertEqual(self.done[-1], KeyUp(["lctrl"]))

    def test_release_all(self):
        self.press("b")
        self.engine.release_all()
        self.assertEqual(self.done[-1], KeyUp(["lctrl"]))
        self.engine.release_all()
        self.assertEqual(len(self.done), 2)

    def test_key_up_without_press_is_swallowed_quietly(self):
        self.assertTrue(self.press("a", False))
        self.assertEqual(self.done, [])


class RouterTest(unittest.TestCase):
    MINI, MAIN = 3, 1

    def setUp(self):
        self.done = []
        engine = Engine(profile({"a": "ctrl+shift+m", "pause": "ctrl+c"}), self.done.append)
        self.router = Router(engine)
        self.router.targets = frozenset({self.MINI})

    def test_main_keyboard_is_never_touched(self):
        for code in (0x1E, 0x2A, 0x1C):  # a, lshift, enter
            for state in (0, keys.KEY_UP, keys.KEY_E0):
                self.assertTrue(self.router.route(self.MAIN, code, state))
        self.assertEqual(self.done, [])

    def test_mini_keyboard_is_remapped(self):
        self.assertFalse(self.router.route(self.MINI, 0x1E, 0))            # a down
        self.assertFalse(self.router.route(self.MINI, 0x1E, keys.KEY_UP))  # a up
        self.assertFalse(self.router.route(self.MINI, 0x30, 0))            # b: не назначена -> глушим
        self.assertEqual(self.done, [Hotkey(["lctrl", "lshift", "m"])])

    def test_pause_sequence_is_consumed_whole(self):
        self.assertFalse(self.router.route(self.MINI, 0x1D, keys.KEY_E1))
        self.assertFalse(self.router.route(self.MINI, 0x45, 0))   # хвост Pause, не NumLock
        self.assertFalse(self.router.route(self.MINI, 0x1D, keys.KEY_E1 | keys.KEY_UP))
        self.assertFalse(self.router.route(self.MINI, 0x45, keys.KEY_UP))
        self.assertEqual(self.done, [Hotkey(["lctrl", "c"])])


if __name__ == "__main__":
    unittest.main()
