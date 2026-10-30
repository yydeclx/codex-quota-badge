"""Isolated fault injection; executable lookup never reaches installed clients."""
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

import quota_badge as quota
import sidebar_supervisor as supervisor


REPORT = {'reports': [{'provider': 'openai', 'quota': {
    'weeklyPercent': 5, 'aggregation': {'currentAccount': {
        'plan': 'prolite', 'quota': {'weeklyPercent': 5, 'weeklyResetAt': 2000000000}}}}}]}
RPC_SERVER = '''
import json, sys, time
for line in sys.stdin:
    request = json.loads(line)
    if request.get('id') == 1:
        # Malformed lines and notifications must not fill the response queue.
        sys.stdout.write('invalid\\n[]\\nnull\\n' + '{"method":"notice"}\\n' * 5000)
        print(json.dumps({'id': 1, 'result': {}}), flush=True)
    if request.get('id') == 2:
        result = {'rateLimits': {'primary': {'usedPercent': 20,
                  'windowDurationMins': 10080, 'resetsAt': 2000000000}}}
        print(json.dumps({'id': 2, 'result': result}), flush=True)
        time.sleep(20)
'''


class OwnedProcessTests(unittest.TestCase):
    def test_cleanup_escalates_and_bounds_every_wait(self):
        child = Mock()
        child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired('test', 3), 0]
        with patch.object(supervisor, 'ProcessJob') as job:
            with supervisor.owned_process(['test'], popen=lambda *a, **k: child):
                pass
        self.assertEqual(child.wait.call_count, 2)
        self.assertTrue(all(call.kwargs == {'timeout': supervisor.PROCESS_STOP_SECONDS}
                            for call in child.wait.call_args_list))
        child.kill.assert_called_once()
        job.return_value.close.assert_called_once()

    def test_termination_race_still_waits_for_exit(self):
        child = Mock()
        child.poll.return_value = None
        child.terminate.side_effect = PermissionError('already exiting')
        child.wait.return_value = 0
        with patch.object(supervisor, 'ProcessJob'):
            with supervisor.owned_process(['test'], popen=lambda *a, **k: child):
                pass
        child.wait.assert_called_once_with(timeout=supervisor.PROCESS_STOP_SECONDS)

    def test_kill_failure_does_not_introduce_unbounded_wait(self):
        child = Mock()
        child.poll.return_value = None
        child.terminate.side_effect = OSError('cannot terminate')
        child.kill.side_effect = OSError('cannot kill')
        child.wait.side_effect = subprocess.TimeoutExpired('test', 3)
        with patch.object(supervisor, 'ProcessJob'):
            with self.assertRaises(supervisor.ProcessCleanupError):
                with supervisor.owned_process(['test'], popen=lambda *a, **k: child):
                    pass
        self.assertEqual(child.wait.call_count, 2)
        self.assertTrue(all(call.kwargs == {'timeout': supervisor.PROCESS_STOP_SECONDS}
                            for call in child.wait.call_args_list))

    def test_job_close_failure_still_reaps_direct_child_with_timeout(self):
        child = Mock()
        child.poll.return_value = None
        child.wait.return_value = 0
        with patch.object(supervisor, 'ProcessJob') as job:
            job.return_value.close.side_effect = OSError('close failed')
            with self.assertRaises(supervisor.ProcessCleanupError):
                with supervisor.owned_process(['test'], popen=lambda *a, **k: child):
                    pass
        child.wait.assert_called_once_with(timeout=supervisor.PROCESS_STOP_SECONDS)

    def test_assignment_failure_reaps_child_and_closes_all_pipes(self):
        child = Mock()
        child.poll.return_value = None
        child.wait.return_value = 0
        with patch.object(supervisor, 'ProcessJob') as job:
            job.return_value.assign.side_effect = OSError('assignment failed')
            with self.assertRaises(OSError):
                with supervisor.owned_process(['test'], popen=lambda *a, **k: child):
                    self.fail('must not enter')
        child.terminate.assert_called_once()
        for stream in (child.stdin, child.stdout, child.stderr):
            stream.close.assert_called_once()

    def test_supervisor_preserves_independent_update_descendants(self):
        child = Mock()
        child.poll.return_value = 0
        child.wait.return_value = 0
        with patch.object(supervisor, 'ProcessJob') as job, \
             patch.object(supervisor.SupervisorMutex, 'acquire', return_value=True), \
             patch.object(supervisor.SupervisorMutex, 'close'):
            self.assertEqual(supervisor.run_supervisor(popen=lambda *a, **k: child), 0)
        job.assert_not_called()

    def test_cleanup_failure_stops_supervisor_without_retry(self):
        child = Mock()
        child.wait.side_effect = [OSError('wait failed'),
                                 subprocess.TimeoutExpired('test', 3),
                                 subprocess.TimeoutExpired('test', 3)]
        child.poll.return_value = None
        launch = Mock(return_value=child)
        with patch.object(supervisor, 'ProcessJob'), \
             patch.object(supervisor.SupervisorMutex, 'acquire', return_value=True), \
             patch.object(supervisor.SupervisorMutex, 'close'), \
             patch.object(supervisor, '_write_error') as log:
            self.assertEqual(supervisor.run_supervisor(popen=launch, sleep=lambda _: None), 1)
        launch.assert_called_once()
        self.assertIn('Unable to stop', log.call_args.args[0])

    def test_wait_error_reaps_child_before_restart(self):
        first, second = Mock(), Mock()
        first.wait.side_effect = [OSError('wait failed'), 0]
        first.poll.return_value = None
        second.wait.return_value = 0
        second.poll.return_value = 0
        def launch(*args, **kwargs):
            if first.terminate.called:
                first.wait.assert_called_with(timeout=supervisor.PROCESS_STOP_SECONDS)
                return second
            return first
        with patch.object(supervisor, 'ProcessJob'), \
             patch.object(supervisor.SupervisorMutex, 'acquire', return_value=True), \
             patch.object(supervisor.SupervisorMutex, 'close'), \
             patch.object(supervisor, '_write_error'):
            self.assertEqual(supervisor.run_supervisor(popen=launch, sleep=lambda _: None), 0)
        first.terminate.assert_called_once()

    def test_repeated_launch_errors_are_bounded(self):
        launch = Mock(side_effect=OSError('unavailable'))
        with patch.object(supervisor, 'ProcessJob'), \
             patch.object(supervisor.SupervisorMutex, 'acquire', return_value=True), \
             patch.object(supervisor.SupervisorMutex, 'close'), \
             patch.object(supervisor, '_write_error'):
            self.assertEqual(supervisor.run_supervisor(popen=launch, sleep=lambda _: None), 1)
        self.assertEqual(launch.call_count, 2)

    def test_private_log_is_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'error.log'
            with patch.object(supervisor, 'LOG', path):
                supervisor._write_error('x' * (supervisor.ERROR_LOG_LIMIT * 3))
                self.assertEqual(path.stat().st_size, supervisor.ERROR_LOG_LIMIT)
                supervisor._write_error('错误' * supervisor.ERROR_LOG_LIMIT)
                self.assertLessEqual(path.stat().st_size, supervisor.ERROR_LOG_LIMIT)
                self.assertTrue(path.read_text(encoding='utf-8').endswith('错误'))
                supervisor._write_error('new failure')
            self.assertEqual(path.read_text(), 'new failure')

    @unittest.skipUnless(os.name == 'nt', 'Windows job semantics')
    def test_private_job_reaps_grandchild_after_parent_exit(self):
        from ctypes import wintypes as W
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
        kernel.OpenProcess.restype = W.HANDLE
        kernel.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
        kernel.WaitForSingleObject.restype = W.DWORD
        kernel.TerminateProcess.argtypes = [W.HANDLE, W.UINT]
        kernel.CloseHandle.argtypes = [W.HANDLE]
        code = ("import subprocess,sys; sys.stdin.readline(); "
                "p=subprocess.Popen([sys.executable,'-B','-c','import time; time.sleep(20)']); "
                "print(p.pid,flush=True)")
        handle = None
        try:
            with supervisor.owned_process([sys.executable, '-B', '-c', code],
                                          stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                          creationflags=subprocess.CREATE_NO_WINDOW) as child:
                try:
                    child.stdin.write(b'go\n')
                    child.stdin.flush()
                    pid = int(child.stdout.readline())
                    handle = kernel.OpenProcess(0x100000 | 0x1, False, pid)
                    self.assertTrue(handle)
                    self.assertEqual(child.wait(timeout=5), 0)
                finally:
                    child.stdin.close()
            self.assertEqual(kernel.WaitForSingleObject(handle, 5000), 0)
        finally:
            if handle:
                if kernel.WaitForSingleObject(handle, 0) != 0:
                    kernel.TerminateProcess(handle, 1)  # Only this test's child.
                kernel.CloseHandle(handle)
            if 'child' in locals():
                child.stdout.close()

    @unittest.skipUnless(os.name == 'nt', 'Windows process ownership boundary')
    def test_direct_child_cleanup_preserves_independent_ui_and_gateway_children(self):
        from ctypes import wintypes as W
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
        kernel.OpenProcess.restype = W.HANDLE
        kernel.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
        kernel.WaitForSingleObject.restype = W.DWORD
        kernel.TerminateProcess.argtypes = [W.HANDLE, W.UINT]
        kernel.CloseHandle.argtypes = [W.HANDLE]
        # Three disposable sleeping Python processes represent UI/shared services.
        code = ("import json,subprocess,sys,time; "
                "children={name:subprocess.Popen([sys.executable,'-B','-c','import time; time.sleep(20)']) "
                "for name in ('manager','details','gateway')}; "
                "print(json.dumps({name:p.pid for name,p in children.items()}),flush=True); time.sleep(20)")
        handles = {}
        try:
            with supervisor.owned_process([sys.executable, '-B', '-c', code], track_tree=False,
                                          stdout=subprocess.PIPE,
                                          creationflags=subprocess.CREATE_NO_WINDOW) as child:
                pids = json.loads(child.stdout.readline())
                for name, pid in pids.items():
                    handles[name] = kernel.OpenProcess(0x100000 | 0x1, False, pid)
                    self.assertTrue(handles[name])
            self.assertIsNotNone(child.poll())
            for name, handle in handles.items():
                self.assertEqual(kernel.WaitForSingleObject(handle, 0), 258,
                                 f'independent {name} process was terminated')
        finally:
            for handle in handles.values():
                if handle:
                    kernel.TerminateProcess(handle, 0)  # Only our disposable test children.
                    kernel.WaitForSingleObject(handle, 3000)
                    kernel.CloseHandle(handle)
            if 'child' in locals():
                child.stdout.close()


class QuotaProcessTests(unittest.TestCase):
    def setUp(self):
        self.children = []
        self.commands = []
        self.provider_code = 'import json; print(' + repr(json.dumps(REPORT)) + ')'
        self.rpc_code = RPC_SERVER
        self.readers_before = set(threading.enumerate())
        for name, value in [('find_opencodex', Path('isolated-ocx.exe')),
                            ('find_codex', Path('isolated-codex.exe'))]:
            patcher = patch.object(quota, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        @contextmanager
        def test_process(command, **kwargs):
            self.commands.append(command)
            code = self.rpc_code if 'app-server' in command else self.provider_code
            with supervisor.owned_process([sys.executable, '-B', '-c', code], **kwargs) as proc:
                self.children.append(proc)
                yield proc
        patcher = patch.object(quota, 'owned_process', side_effect=test_process)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        for proc in self.children:
            try:
                self.assertIsNotNone(proc.poll(), 'owned test process leaked')
                for stream in (proc.stdin, proc.stdout):
                    if stream is not None:
                        self.assertTrue(stream.closed)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=3)
        self.assertFalse(set(threading.enumerate()) - self.readers_before,
                         'quota reader thread leaked')

    def test_provider_success_preserves_source(self):
        result = quota.fetch_quota()
        self.assertEqual(result['remaining'], 95)
        self.assertEqual(result['quotaSource'], 'opencodex provider quota')
        self.assertEqual(len(self.commands), 1)

    def test_rpc_fallback_discards_flood_and_malformed_lines(self):
        self.provider_code = 'print("invalid")'
        self.assertEqual(quota.fetch_quota()['remaining'], 80)
        self.assertEqual(len(self.commands), 2)

    def test_provider_timeout_reaps_before_fallback(self):
        self.provider_code = 'import time; time.sleep(20)'
        with patch.object(quota, 'QUOTA_COMMAND_TIMEOUT', .15):
            self.assertEqual(quota.fetch_quota()['remaining'], 80)
        self.assertEqual(len(self.commands), 2)

    def test_oversized_provider_output_falls_back(self):
        self.provider_code = 'import sys,time; sys.stdout.write("x"*4097); sys.stdout.flush(); time.sleep(20)'
        with patch.object(quota, 'MAX_QUOTA_OUTPUT', 4096):
            self.assertEqual(quota.fetch_quota()['remaining'], 80)

    def test_invalid_utf8_provider_output_falls_back(self):
        self.provider_code = 'import sys; sys.stdout.buffer.write(b"\\xff\\xfe")'
        self.assertEqual(quota.fetch_quota()['remaining'], 80)

    def test_provider_nonzero_exit_falls_back(self):
        self.provider_code = 'import sys; sys.exit(7)'
        self.assertEqual(quota.fetch_quota()['remaining'], 80)

    def test_provider_wrong_nested_types_fall_back(self):
        self.provider_code = 'print(' + repr(json.dumps({'reports': [{'provider': 'openai', 'quota': {
            'weeklyPercent': 5, 'aggregation': {'weekly': ['bad']}}}]})) + ')'
        self.assertEqual(quota.fetch_quota()['remaining'], 80)

    def test_rpc_duplicate_responses_do_not_overflow_queue(self):
        self.provider_code = 'print("invalid")'
        self.rpc_code = RPC_SERVER.replace("print(json.dumps({'id': 1, 'result': {}}), flush=True)",
            "sys.stdout.write((json.dumps({'id': 1, 'result': {}})+'\\n')*5000); sys.stdout.flush()")
        self.assertEqual(quota.fetch_quota()['remaining'], 80)

    def test_deeply_nested_provider_json_falls_back(self):
        self.provider_code = 'print("["*2000 + "]"*2000)'
        self.assertEqual(quota.fetch_quota()['remaining'], 80)

    def test_rpc_timeout_is_bounded_and_reaped(self):
        self.provider_code = 'print("invalid")'
        self.rpc_code = 'import time; time.sleep(20)'
        with patch.object(quota, 'QUOTA_RPC_TIMEOUT', .15):
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                quota.fetch_quota()

    def test_rpc_eof_is_reported_immediately(self):
        self.provider_code = 'print("invalid")'
        self.rpc_code = 'pass'
        with self.assertRaises((RuntimeError, BrokenPipeError)):
            quota.fetch_quota()

    def test_rpc_oversized_line_is_rejected(self):
        self.provider_code = 'print("invalid")'
        self.rpc_code = 'import sys,time; sys.stdout.write("x"*4097); sys.stdout.flush(); time.sleep(20)'
        with patch.object(quota, 'MAX_QUOTA_OUTPUT', 4096):
            with self.assertRaisesRegex(RuntimeError, 'unavailable'):
                quota.fetch_quota()

    def test_reader_start_failure_closes_process_and_pipes(self):
        with patch.object(quota.threading.Thread, 'start', side_effect=RuntimeError('cannot start')):
            with self.assertRaisesRegex(RuntimeError, 'cannot start'):
                quota.fetch_quota()

    def test_uncertain_cleanup_never_starts_fallback(self):
        with patch.object(quota, '_provider_quota_output', side_effect=supervisor.ProcessCleanupError('uncertain')):
            with self.assertRaises(supervisor.ProcessCleanupError):
                quota.fetch_quota()
        self.assertEqual(self.commands, [])


if __name__ == '__main__':
    unittest.main()
