import sys
import unittest
import xml.etree.ElementTree as ET

from minikeys import autostart, elevation, paths

NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


class AutostartTest(unittest.TestCase):
    def test_command_from_sources_uses_pyw(self):
        cmd = autostart.command()
        self.assertEqual(cmd[-1], "--autostart")
        if not paths.FROZEN:
            self.assertTrue(cmd[1].endswith("MiniKeys.pyw"))

    def test_task_xml(self):
        cmd = [r"C:\Program Files\minikeys\minikeys.exe", "--autostart"]
        xml = autostart.task_xml(cmd, "PC\\Пользователь & Co")
        root = ET.fromstring(xml.split("?>", 1)[1])      # декларация UTF-16 мешает fromstring
        self.assertEqual(root.find("t:Principals/t:Principal/t:RunLevel", NS).text, "HighestAvailable")
        self.assertEqual(root.find("t:Settings/t:ExecutionTimeLimit", NS).text, "PT0S")  # без 72 ч
        self.assertEqual(root.find("t:Settings/t:DisallowStartIfOnBatteries", NS).text, "false")
        self.assertEqual(root.find("t:Actions/t:Exec/t:Command", NS).text, cmd[0])
        self.assertEqual(root.find("t:Actions/t:Exec/t:Arguments", NS).text, "--autostart")
        self.assertEqual(root.find("t:Triggers/t:LogonTrigger/t:UserId", NS).text, "PC\\Пользователь & Co")

    def test_elevation_command(self):
        exe, params = elevation.elevation_command(["--minimized", "--elevated"])
        self.assertEqual(exe, paths.launch_command()[0])
        self.assertTrue(params.endswith("--minimized --elevated"))
        self.assertEqual(params.count("--elevated"), 1)       # без повторов
        if not paths.FROZEN:
            self.assertIn("MiniKeys.pyw", params)

    @unittest.skipIf(sys.platform == "win32", "проверка для не-Windows")
    def test_noop_outside_windows(self):
        self.assertIsNone(autostart.status())
        self.assertFalse(elevation.is_admin())
        self.assertFalse(elevation.relaunch_as_admin([]))
        with self.assertRaises(autostart.AutostartError):
            autostart.enable()


if __name__ == "__main__":
    unittest.main()
