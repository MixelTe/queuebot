import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class RuntimeTests(unittest.TestCase):
    def test_nested_lock_serializes_different_worker_processes(self):
        script = '''
import os
import sys
import bafser_config
from utils.runtime import update_lock

bafser_config.update_lock_path = os.environ["QUEUEBOT_TEST_LOCK"]
if sys.argv[1] == "first":
    with update_lock, update_lock:
        print("locked", flush=True)
        sys.stdin.readline()
else:
    print("waiting", flush=True)
    with update_lock:
        print("acquired", flush=True)
'''
        with tempfile.TemporaryDirectory() as temporary:
            environment = os.environ.copy()
            environment["QUEUEBOT_TEST_LOCK"] = str(Path(temporary) / "updates.lock")
            options = dict(cwd=Path(__file__).resolve().parents[1], env=environment,
                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           text=True, encoding="utf-8")
            first = subprocess.Popen([sys.executable, "-c", script, "first"], **options)
            second = None
            try:
                self.assertEqual(first.stdout.readline().strip(), "locked")
                second = subprocess.Popen([sys.executable, "-c", script, "second"], **options)
                self.assertEqual(second.stdout.readline().strip(), "waiting")
                with self.assertRaises(subprocess.TimeoutExpired):
                    second.communicate(timeout=1)
                _, first_error = first.communicate(input="\n", timeout=10)
                output, second_error = second.communicate(timeout=10)
                self.assertEqual(first.returncode, 0, first_error)
                self.assertEqual(second.returncode, 0, second_error)
                self.assertIn("acquired", output)
            finally:
                for process in (first, second):
                    if process is not None:
                        if process.poll() is None:
                            process.kill()
                        process.communicate(timeout=10)


if __name__ == "__main__":
    unittest.main()
