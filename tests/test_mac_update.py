"""macOS lifecycle regressions using only isolated temporary processes."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

FIX = Path(__file__).resolve().parents[1]


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.src, self.dst = self.root / 'source', self.root / 'target'
        for folder in (self.src, self.dst):
            folder.mkdir()
            for name in ('README.md', 'PORTABLE-INSTALL.md', 'package-manifest.json'):
                (folder / name).write_text('fixture')
            for name in ('.codex-plugin', 'skills'):
                (folder / name).mkdir()
            for name in ('update-mac.sh', 'start-mac.sh', 'mac_lifecycle.py'):
                shutil.copy2(FIX / name, folder / name)
            (folder / 'mac_sidebar.py').write_text(
                'from pathlib import Path\nfrom mac_lifecycle import instance_guard\n'
                'import signal,time\n'
                'with instance_guard(Path(__file__).resolve().parent):\n'
                ' signal.signal(signal.SIGTERM,lambda s,f:exit(0))\n'
                ' while True:time.sleep(.05)\n')
        (self.src / 'mac_sidebar.py').write_text((self.dst / 'mac_sidebar.py').read_text() + '# updated\n')
        (self.dst / 'sidebar-config.json').write_text('personal')
        (self.dst / 'quota-history.json').write_text('history')
        self.children = []
        self.addCleanup(self.cleanup_processes)

    def cleanup_processes(self):
        for child in self.children:
            if child.poll() is None:
                child.terminate()
                try: child.wait(timeout=2)
                except subprocess.TimeoutExpired: child.kill();child.wait()
        marker = self.dst / '.mac-sidebar.pid'
        if marker.exists():
            try:
                pid = int(marker.read_text())
                if pid > 1 and str(self.dst) in subprocess.check_output(
                        ['ps', '-p', str(pid), '-o', 'args='], text=True):
                    os.kill(pid, 15)
            except (ProcessLookupError, ValueError, subprocess.CalledProcessError): pass

    def launch(self):
        child = subprocess.Popen(['sh', str(self.dst / 'start-mac.sh')],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.children.append(child)
        for _ in range(60):
            if (self.dst / '.mac-sidebar.pid').exists() or child.poll() is not None: break
            time.sleep(.05)
        return child

    def update(self, env=None):
        return subprocess.run(['sh', str(self.src / 'update-mac.sh'), str(self.dst)],
                              capture_output=True, text=True, env=env, timeout=20)

    def test_first_launch_and_duplicate(self):
        first = self.launch()
        self.assertIsNone(first.poll())
        second = self.launch()
        second.wait(timeout=3)
        self.assertNotEqual(second.returncode, 0)
        self.assertIsNone(first.poll())

    def test_running_update(self):
        first = self.launch()
        result = self.update()
        self.assertEqual(result.returncode, 0, result.stderr)
        first.wait(timeout=4)
        self.assertIn('# updated', (self.dst / 'mac_sidebar.py').read_text())
        self.assertEqual((self.dst / 'sidebar-config.json').read_text(), 'personal')
        self.assertEqual((self.dst / 'quota-history.json').read_text(), 'history')
        for _ in range(60):
            if (self.dst / '.mac-sidebar.pid').exists(): break
            time.sleep(.05)
        self.assertTrue((self.dst / '.mac-sidebar.pid').exists())
        self.assertNotEqual(int((self.dst / '.mac-sidebar.pid').read_text()), first.pid)

    def test_unrelated_pid_never_signaled(self):
        unrelated = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(15)'])
        self.children.append(unrelated)
        (self.dst / '.mac-sidebar.pid').write_text(str(unrelated.pid))
        self.assertNotEqual(self.update().returncode, 0)
        self.assertIsNone(unrelated.poll())
        self.assertNotIn('# updated', (self.dst / 'mac_sidebar.py').read_text())

    def test_failed_deployment_restores_and_removes_new_files(self):
        original = (self.dst / 'mac_sidebar.py').read_text()
        (self.src / 'added.py').write_text('x=1\n')
        bindir = self.root / 'bin';bindir.mkdir()
        wrapper = bindir / 'cp'
        wrapper.write_text('#!/bin/sh\ncase "$*" in *"/stage/mac_sidebar.py "*) exit 17;; esac\nexec '
                           + shutil.which('cp') + ' "$@"\n')
        wrapper.chmod(0o755)
        env = dict(os.environ, PATH=str(bindir)+os.pathsep+os.environ['PATH'])
        self.assertNotEqual(self.update(env).returncode, 0)
        self.assertEqual((self.dst / 'mac_sidebar.py').read_text(), original)
        self.assertFalse((self.dst / 'added.py').exists())
        self.assertEqual((self.dst / 'sidebar-config.json').read_text(), 'personal')

    def test_non_exiting_process_aborts_without_file_changes(self):
        original = (self.dst / 'mac_sidebar.py').read_text()
        (self.dst / 'mac_sidebar.py').write_text(
            'from pathlib import Path\nfrom mac_lifecycle import instance_guard\n'
            'import signal,time\nwith instance_guard(Path(__file__).resolve().parent):\n'
            ' signal.signal(signal.SIGTERM,signal.SIG_IGN)\n'
            ' while True:time.sleep(.05)\n')
        first = self.launch()
        self.assertIsNone(first.poll())
        result = self.update()
        self.assertNotEqual(result.returncode, 0)
        self.assertIsNone(first.poll())
        self.assertNotIn('# updated', (self.dst / 'mac_sidebar.py').read_text())


if __name__ == '__main__':
    unittest.main()
