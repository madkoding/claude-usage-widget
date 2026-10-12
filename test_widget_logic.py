import sys
import unittest
from pathlib import Path
from unittest import mock

import claude_usage_widget as w


class AlertStep(unittest.TestCase):
    def test_fires_once_when_crossing(self):
        armed, fire = w.alert_step(False, 85, True, 80)
        self.assertEqual((armed, fire), (True, True))
        self.assertEqual(w.alert_step(True, 90, True, 80), (True, False))

    def test_hysteresis_rearms_below_threshold_minus_10(self):
        self.assertEqual(w.alert_step(True, 75, True, 80), (True, False))
        self.assertEqual(w.alert_step(True, 69, True, 80), (False, False))
        self.assertEqual(w.alert_step(False, 69, True, 80), (False, False))

    def test_no_data_rearms_and_never_fires(self):
        self.assertEqual(w.alert_step(True, 0, False, 80), (False, False))
        self.assertEqual(w.alert_step(False, 99, False, 80), (False, False))


class Autostart(unittest.TestCase):
    def test_command_quotes_python_and_script(self):
        cmd = w.autostart_cmd()
        self.assertTrue(cmd.startswith('"'))
        self.assertIn(str(Path(w.__file__).resolve()), cmd)

    def test_noop_outside_windows(self):
        with mock.patch.object(sys, "platform", "linux"):
            self.assertFalse(w.autostart_get())
            w.autostart_set(True)  # no debe lanzar


if __name__ == "__main__":
    unittest.main()
