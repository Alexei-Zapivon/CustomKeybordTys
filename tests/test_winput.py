"""WinOutput без Windows: подкласс записывает, что ушло бы в SendInput, и паузы."""

import unittest

from minikeys import winput
from minikeys.winput import (KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE,
                             WinOutput)

# MapVirtualKey(vk, MAPVK_VK_TO_VSC_EX) для раскладки US/RU
FAKE_MAP = {0x4D: 0x32, 0x43: 0x2E, 0x31: 0x02, 0xAF: 0xE030}
DOWN, UP = False, True


class RecordingOutput(WinOutput):
    def __init__(self, fail_on_scan=None, **kw):
        super().__init__(**kw)
        self.events = []
        self.fail_on_scan = fail_on_scan
        self._sleep = lambda seconds: self.events.append(("sleep", round(seconds * 1000)))

    def _mapvk(self, vk):
        return FAKE_MAP.get(vk, 0)

    def _send(self, inputs):
        for inp in inputs:
            if self.fail_on_scan is not None and inp.ki.wScan == self.fail_on_scan:
                raise OSError("SendInput отказал")
            self.events.append(("key", inp.ki.wVk, inp.ki.wScan, inp.ki.dwFlags))


def key(scan, up=False, extended=False):
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0) | (KEYEVENTF_EXTENDEDKEY if extended else 0)
    return ("key", 0, scan, flags)


class ScancodeTest(unittest.TestCase):
    def test_chord_is_split_with_hold(self):
        out = RecordingOutput()
        out.chord(["lctrl", "lshift", "m"])
        self.assertEqual(out.events, [
            key(0x1D), ("sleep", 10), key(0x2A), ("sleep", 10), key(0x32),
            ("sleep", 30),                                   # удержание
            key(0x32, UP), ("sleep", 10), key(0x2A, UP), ("sleep", 10), key(0x1D, UP),
        ])

    def test_f13_f24_by_scancode(self):
        out = RecordingOutput()
        out.chord(["f13"])
        self.assertEqual(out.events, [key(0x64), ("sleep", 30), key(0x64, UP)])
        self.assertEqual(out.scancode("f24"), (0x76, 0))
        self.assertEqual(out.scancode("f20"), (0x6B, 0))

    def test_extended_keys(self):
        out = RecordingOutput()
        out.key_down("right")
        out.key_down("rctrl")
        out.key_down("delete")
        self.assertEqual(out.events, [key(0x4D, extended=True), key(0x1D, extended=True),
                                      key(0x53, extended=True)])

    def test_layout_dependent_keys_use_mapvirtualkey(self):
        out = RecordingOutput()
        self.assertEqual(out.scancode("c"), (0x2E, 0))
        self.assertEqual(out.scancode("1"), (0x02, 0))

    def test_media_and_numpad_digits_use_virtual_key(self):
        out = RecordingOutput()
        out.key_down("volume_up")
        out.key_down("numpad1")
        (_, vk1, _, flags1), (_, vk2, _, flags2) = out.events
        self.assertEqual((vk1, vk2), (0xAF, 0x61))
        self.assertFalse(flags1 & KEYEVENTF_SCANCODE)
        self.assertTrue(flags1 & KEYEVENTF_EXTENDEDKEY)
        self.assertFalse(flags2 & KEYEVENTF_SCANCODE)

    def test_press_duration_is_configurable(self):
        out = RecordingOutput()
        out.configure(press_ms=50)
        out.chord(["f13"])
        self.assertIn(("sleep", 50), out.events)

    def test_modifiers_released_after_error(self):
        out = RecordingOutput(fail_on_scan=0x32)  # «m» не отправилась
        with self.assertRaises(OSError):
            out.chord(["lctrl", "m"])
        self.assertEqual(out.events[-1], key(0x1D, UP))   # Ctrl не залип

    def test_driver_mode(self):
        sent = []

        def driver(code, prefix, up):
            sent.append((code, prefix, up))
            return True
        out = RecordingOutput(method=winput.OUTPUT_DRIVER, driver_send=driver)
        out.chord(["lctrl", "right"])
        self.assertEqual(sent, [(0x1D, 0, False), (0x4D, 2, False), (0x4D, 2, True), (0x1D, 0, True)])
        self.assertEqual([e for e in out.events if e[0] == "key"], [])   # SendInput не нужен
        # мультимедиа через драйвер не шлётся: только SendInput по VK
        out.key_down("volume_up")
        self.assertEqual(out.events[-1][1], 0xAF)

    def test_driver_mode_falls_back(self):
        out = RecordingOutput(method=winput.OUTPUT_DRIVER, driver_send=lambda *a: False)
        out.key_down("f13")
        self.assertEqual(out.events, [key(0x64)])


if __name__ == "__main__":
    unittest.main()
