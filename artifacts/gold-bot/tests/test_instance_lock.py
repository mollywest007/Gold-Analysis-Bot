import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.instance_lock import acquire_instance_lock


class InstanceLockTests(unittest.TestCase):
    def test_only_one_process_can_hold_the_same_entrypoint_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            identity = str(Path(directory) / "main.py")
            first = acquire_instance_lock(identity, lock_dir=directory)
            self.assertIsNotNone(first)
            try:
                self.assertIsNone(
                    acquire_instance_lock(identity, lock_dir=directory)
                )
            finally:
                first.close()

            replacement = acquire_instance_lock(identity, lock_dir=directory)
            self.assertIsNotNone(replacement)
            replacement.close()


if __name__ == "__main__":
    unittest.main()
