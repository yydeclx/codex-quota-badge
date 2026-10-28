import unittest
from unittest.mock import patch

import sidebar_supervisor as supervisor


class SupervisorPolicyTests(unittest.TestCase):
    def setUp(self):
        # Never acquire real jobs/mutexes or overwrite the installation's log.
        job = patch.object(supervisor, "ProcessJob")
        log = patch.object(supervisor, "_write_error")
        job.start()
        log.start()
        self.addCleanup(job.stop)
        self.addCleanup(log.stop)

    def test_normal_exit_does_not_restart(self):
        calls = []

        class Child:
            def wait(self, timeout=None):
                if timeout is None:
                    calls.append("wait")
                return 0

            def poll(self):
                return 0

        with patch.object(supervisor.SupervisorMutex, "acquire", return_value=True), \
             patch.object(supervisor.SupervisorMutex, "close"), \
             patch.object(supervisor, "child_command", return_value=["pythonw.exe", "sidebar_plugin.py"]):
            self.assertEqual(supervisor.run_supervisor(popen=lambda *a, **k: Child()), 0)
        self.assertEqual(calls, ["wait"])

    def test_abnormal_exit_restarts_once_then_stops(self):
        results = iter([1, 2, 0])
        launches = []

        class Child:
            def wait(self, timeout=None):
                if timeout is None:
                    self.code = next(results)
                return self.code

            def poll(self):
                return self.code

        with patch.object(supervisor.SupervisorMutex, "acquire", return_value=True), \
             patch.object(supervisor.SupervisorMutex, "close"), \
             patch.object(supervisor, "child_command", return_value=["pythonw.exe", "sidebar_plugin.py"]):
            result = supervisor.run_supervisor(popen=lambda *a, **k: (launches.append(1) or Child()), sleep=lambda _: None)
        self.assertEqual(result, 1)
        self.assertEqual(len(launches), 2)

    def test_single_instance_exits_without_launching_child(self):
        launches = []
        with patch.object(supervisor.SupervisorMutex, "acquire", return_value=False), \
             patch.object(supervisor.SupervisorMutex, "close"), \
             patch.object(supervisor, "child_command") as command:
            self.assertEqual(supervisor.run_supervisor(popen=lambda *a, **k: launches.append(1)), 0)
        self.assertEqual(launches, [])
        command.assert_not_called()

    def test_policy_window(self):
        now = 100.0
        self.assertTrue(supervisor.should_restart([now], now))
        self.assertFalse(supervisor.should_restart([now, now + 1], now + 1))
        self.assertTrue(supervisor.should_restart([now], now + supervisor.RESTART_WINDOW_SECONDS + 1))


if __name__ == "__main__":
    unittest.main()
