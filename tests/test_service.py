"""RemapService на имитации драйвера: основная клавиатура №1, мини-клавиатура №4."""

import unittest

from minikeys import keys
from minikeys.config import parse_profile
from minikeys.interception import FILTER_KEY_ALL, Stroke
from minikeys.service import Listener, Mode, RemapService

MAIN, MINI = 1, 4
DEVICES = {
    MAIN: ["HID\\VID_05AC&PID_024F&REV_0100&MI_00"],
    MINI: ["HID\\VID_1189&PID_8840&REV_0100&MI_01&Col01"],
}


class FakeInterception:
    def __init__(self, dll=None):
        self.devices_now = dict(DEVICES)
        self.pending = []      # (device, code, state)
        self.sent = []         # что ушло в систему
        self.captured = frozenset()
        self.closed = False

    def devices(self, which):
        return {d: ids for d, ids in self.devices_now.items() if d in which}

    def hardware_ids(self, dev):
        return self.devices_now.get(dev, [])

    def capture_only(self, devices, filter_mask=FILTER_KEY_ALL):
        self.captured = frozenset(devices)

    def wait(self, timeout_ms):
        # драйвер отдаёт события только перехватываемых устройств
        for i, (dev, *_rest) in enumerate(self.pending):
            if dev in self.captured:
                return dev
        return 0

    def receive(self, dev):
        for i, (d, code, state) in enumerate(self.pending):
            if d == dev:
                del self.pending[i]
                s = Stroke()
                s.key.code, s.key.state = code, state
                return s
        return None

    def send(self, dev, stroke):
        self.sent.append((dev, stroke.key.code, stroke.key.state))

    def close(self):
        self.closed = True


class FakeOutput:
    def __init__(self):
        self.calls = []

    def chord(self, keys_):
        self.calls.append(("chord", list(keys_)))

    def key_down(self, k):
        self.calls.append(("down", k))

    def key_up(self, k):
        self.calls.append(("up", k))


class RecordingListener(Listener):
    def __init__(self):
        self.events = []

    def on_targets(self, targets):
        self.events.append(("targets", sorted(targets)))

    def on_key(self, key, is_down):
        self.events.append(("key", key, is_down))

    def on_learned(self, key):
        self.events.append(("learned", key))

    def on_probe(self, device, ids, key):
        self.events.append(("probe", device, key))


def profile():
    return parse_profile({"device": {"match": "VID_1189&PID_8840"},
                          "binds": {"a": "ctrl+shift+m"}})


class ServiceTest(unittest.TestCase):
    def setUp(self):
        self.ic = FakeInterception()
        self.out = FakeOutput()
        self.listener = RecordingListener()
        self.svc = RemapService(self.listener, interception_factory=lambda dll: self.ic,
                                output_factory=lambda: self.out)
        self.svc._open()

    def tearDown(self):
        self.svc._shutdown()

    def run_ticks(self, n=3):
        for _ in range(n):
            self.svc.tick()

    def press(self, dev, code, up=False):
        self.ic.pending.append((dev, code, keys.KEY_UP if up else 0))

    def actions(self):
        self.svc._worker.stop()   # дождаться выполнения очереди действий
        return self.out.calls

    def test_without_profile_nothing_is_captured(self):
        self.run_ticks()
        self.assertEqual(self.ic.captured, frozenset())

    def test_only_mini_keyboard_is_captured(self):
        self.svc.set_profile(profile())
        self.run_ticks()
        self.assertEqual(self.ic.captured, {MINI})
        self.assertIn(("targets", [MINI]), self.listener.events)

    def test_mini_key_is_remapped_and_swallowed(self):
        self.svc.set_profile(profile())
        self.run_ticks()
        self.press(MINI, 0x1E)
        self.press(MINI, 0x1E, up=True)
        self.run_ticks()
        self.assertEqual(self.ic.sent, [])   # «a» не дошла до системы
        self.assertEqual(self.actions(), [("chord", ["lctrl", "lshift", "m"])])
        self.assertIn(("key", "a", True), self.listener.events)
        self.assertIn(("key", "a", False), self.listener.events)

    def test_learn_mode_reports_and_does_not_execute(self):
        self.svc.set_profile(profile())
        self.svc.set_mode(Mode.LEARN)
        self.run_ticks()
        self.press(MINI, 0x1E)
        self.press(MINI, 0x1E)             # автоповтор — не второе событие
        self.press(MINI, 0x1E, up=True)
        self.run_ticks(4)
        self.assertEqual([e for e in self.listener.events if e[0] == "learned"], [("learned", "a")])
        self.assertEqual(self.ic.sent, [])
        self.assertEqual(self.actions(), [])

    def test_probe_listens_to_all_and_passes_through(self):
        self.svc.set_profile(profile())
        self.svc.set_mode(Mode.PROBE)
        self.run_ticks()
        self.assertEqual(self.ic.captured, {MAIN, MINI})
        self.press(MAIN, 0x2E)
        self.run_ticks()
        self.assertEqual(self.ic.sent, [(MAIN, 0x2E, 0)])
        self.assertIn(("probe", MAIN, "c"), self.listener.events)
        self.svc.set_mode(Mode.RUN)
        self.run_ticks()
        self.assertEqual(self.ic.captured, {MINI})

    def test_paused_captures_nothing(self):
        self.svc.set_profile(profile())
        self.run_ticks()
        self.svc.set_mode(Mode.PAUSED)
        self.run_ticks()
        self.assertEqual(self.ic.captured, frozenset())

    def test_reconnect(self):
        self.svc.set_profile(profile())
        self.run_ticks()
        del self.ic.devices_now[MINI]
        self.svc._next_scan = 0
        self.run_ticks()
        self.assertEqual(self.ic.captured, frozenset())
        self.ic.devices_now[7] = DEVICES[MINI]   # воткнули в другой порт — новый номер
        self.svc._next_scan = 0
        self.run_ticks()
        self.assertEqual(self.ic.captured, {7})

    def test_hold_is_released_when_mode_changes(self):
        self.svc.set_profile(parse_profile({"device": {"match": "VID_1189"},
                                            "binds": {"b": {"remap": "ctrl"}}}))
        self.run_ticks()
        self.press(MINI, 0x30)
        self.run_ticks()
        self.svc.set_mode(Mode.LEARN)
        self.run_ticks()
        self.assertEqual(self.actions(), [("down", "lctrl"), ("up", "lctrl")])


class DriverOutputTest(unittest.TestCase):
    def setUp(self):
        self.ic = FakeInterception()
        self.out = FakeOutput()
        self.configured = {}
        self.out.configure = lambda **kw: self.configured.update(kw)
        self.svc = RemapService(RecordingListener(), interception_factory=lambda dll: self.ic,
                                output_factory=lambda: self.out)
        self.svc._open()

    def tearDown(self):
        self.svc._shutdown()

    def test_profile_settings_reach_output(self):
        self.svc.set_profile(parse_profile({"device": {"match": "VID_1189&PID_8840"},
                                            "settings": {"press_ms": 50, "output": "driver"}}))
        self.svc.tick()
        self.assertEqual(self.configured["press_ms"], 50)
        self.assertEqual(self.configured["method"], "driver")
        self.assertEqual(self.configured["driver_send"], self.svc.driver_send)

    def test_driver_send_uses_keyboard_we_do_not_capture(self):
        self.svc.set_profile(profile())
        self.svc.tick()
        self.assertTrue(self.svc.driver_send(0x64, 0, False))
        self.assertTrue(self.svc.driver_send(0x4D, keys.KEY_E0, True))
        # через основную клавиатуру №1, а не через перехваченную №4
        self.assertEqual(self.ic.sent, [(MAIN, 0x64, 0), (MAIN, 0x4D, keys.KEY_E0 | keys.KEY_UP)])

    def test_driver_send_without_free_keyboard(self):
        del self.ic.devices_now[MAIN]
        self.svc.set_profile(profile())
        self.svc.tick()
        self.assertTrue(self.svc.driver_send(0x64, 0, False))
        self.assertEqual(self.ic.sent, [(MINI, 0x64, 0)])


class TwoIdenticalKeyboardsTest(unittest.TestCase):
    """Вторая такая же мини-клавиатура (те же VID/PID) подключена как устройство №5."""
    MINI2 = 5

    def setUp(self):
        self.ic = FakeInterception()
        self.ic.devices_now[self.MINI2] = DEVICES[MINI]      # идентичный Hardware ID
        self.out = FakeOutput()
        self.svc = RemapService(RecordingListener(), interception_factory=lambda dll: self.ic,
                                output_factory=lambda: self.out)
        self.svc._open()

    def tearDown(self):
        self.svc._shutdown()

    def ticks(self, n=4):
        for _ in range(n):
            self.svc.tick()

    def test_same_profile_on_both(self):
        self.svc.set_profile(profile())                     # match = VID/PID
        self.ticks()
        self.assertEqual(self.ic.captured, {MINI, self.MINI2})
        self.ic.pending += [(MINI, 0x1E, 0), (self.MINI2, 0x1E, 0)]
        self.ticks()
        self.svc._worker.stop()
        self.assertEqual(len(self.out.calls), 2)            # «a» сработала на обеих
        self.assertEqual(self.ic.sent, [])

    def test_hold_on_both_is_tracked_per_device(self):
        self.svc.set_profile(parse_profile({"device": {"match": "VID_1189&PID_8840"},
                                            "binds": {"b": {"remap": "ctrl"}}}))
        self.ticks()
        self.ic.pending += [(MINI, 0x30, 0), (self.MINI2, 0x30, 0), (MINI, 0x30, keys.KEY_UP)]
        self.ticks(6)
        self.svc._worker.stop()
        # вторая клавиатура всё ещё держит Ctrl: отпущен только один
        self.assertEqual(self.out.calls, [("down", "lctrl"), ("down", "lctrl"), ("up", "lctrl")])

    def test_only_one_device(self):
        self.svc.set_profile(parse_profile({"device": {"match": "VID_1189&PID_8840", "device_number": 5},
                                            "binds": {"a": "ctrl+c"}}))
        self.ticks()
        self.assertEqual(self.ic.captured, {self.MINI2})    # №4 работает как обычная клавиатура
        # номер 1 занят Apple-клавиатурой другой модели: она не должна попасть под перехват
        self.svc.set_profile(parse_profile({"device": {"match": "VID_1189&PID_8840", "device_number": 1},
                                            "binds": {}}))
        self.ticks()
        self.assertEqual(self.ic.captured, frozenset())


if __name__ == "__main__":
    unittest.main()
