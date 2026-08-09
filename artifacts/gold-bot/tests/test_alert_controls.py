"""Regression tests for explicit automatic-alert controls."""
import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src import alerts


class AlertControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        with open(self.tmp.name, "w") as f:
            json.dump({"subscribers": [], "disabled": []}, f)
        self.path_patch = patch.object(alerts, "DATA_PATH", self.tmp.name)
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        os.unlink(self.tmp.name)

    def test_on_control_registers_chat(self):
        alerts.register_user(123)
        self.assertTrue(alerts.is_registered(123))
        self.assertFalse(alerts.is_alerts_disabled(123))

    def test_off_control_unregisters_and_marks_chat_disabled(self):
        alerts.register_user(123)
        alerts.unregister_user(123)
        self.assertFalse(alerts.is_registered(123))
        self.assertTrue(alerts.is_alerts_disabled(123))

    def test_on_control_after_off_reenables_chat(self):
        alerts.unregister_user(123)
        alerts.register_user(123)
        self.assertTrue(alerts.is_registered(123))
        self.assertFalse(alerts.is_alerts_disabled(123))


if __name__ == "__main__":
    unittest.main()