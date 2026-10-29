"""Execute the real Windows updater only against disposable, inert installations.

No installed plugin, UI, gateway, or real manage.py is invoked. The package uses
the repository's real window_mode.py; only fixture start/manage entry points and
explicit fault injections are replaced. Run with Python on Windows + PowerShell 7.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parent
PWSH = Path(r"C:\Program Files\PowerShell\7\pwsh.exe")
UPDATER = (ROOT / "update.ps1").read_text(encoding="utf-8-sig")


def manifest_array(name):
    match = re.search(r"\$" + name + r"\s*=\s*@\((.*?)\)", UPDATER, re.S)
    if match is None:
        raise AssertionError(f"Missing updater manifest: {name}")
    return re.findall(r"'([^']+)'", match.group(1))


PROGRAM_FILES = manifest_array("programFiles")
PROGRAM_DIRS = manifest_array("programDirectories")
MANAGE = '''import json, sys
from pathlib import Path
root = Path(__file__).resolve().parent
with (root/'manage-events.txt').open('a') as stream:
    stream.write(sys.argv[1]+'\\n')
if sys.argv[1] == 'status':
    state = json.loads((root/'status.json').read_text())
    print(json.dumps({'running': (root/'fixture-running').exists(), **state}))
elif sys.argv[1] == 'stop':
    (root/'stop.request').touch()
else:
    raise RuntimeError('Unexpected fixture manage action')
'''


def start_sentinel(name, exit_code=0):
    return f'''$ErrorActionPreference = 'Stop'
$modePath = Join-Path $PSScriptRoot 'window-mode.json'
$mode = 'child'
if (Test-Path -LiteralPath $modePath) {{
    $mode = (Get-Content -LiteralPath $modePath -Raw | ConvertFrom-Json).mode
}}
$exited = Test-Path -LiteralPath (Join-Path $PSScriptRoot 'supervisor-exited')
if ((Test-Path -LiteralPath (Join-Path $PSScriptRoot 'expect-supervisor-wait')) -and -not $exited) {{
    throw 'Updater restarted before the old supervisor released its mutex'
}}
@{{mode=$mode; modeFileExists=(Test-Path -LiteralPath $modePath); supervisorExited=$exited}} |
    ConvertTo-Json -Compress | Set-Content -LiteralPath (Join-Path $PSScriptRoot '{name}') -Encoding utf8
exit {exit_code}
'''


@unittest.skipUnless(os.name == "nt" and PWSH.is_file(), "Requires Windows and PowerShell 7")
class WindowsUpdateTests(unittest.TestCase):
    maxDiff = None

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="quota-updater-test-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        # Spaces and Unicode exercise native argument boundaries too.
        self.source = self.base / "package 新版"
        self.target = self.base / "install 旧版"
        self.source.mkdir()
        self.target.mkdir()
        for relative in PROGRAM_FILES:
            source = self.source / relative
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, source)
            # Newly introduced helper/compat must also disappear on rollback.
            if relative in {"window_mode.py", "sidebar_compat.py"}:
                continue
            target = self.target / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes((f"# old program: {relative}\n").encode())
        for relative in PROGRAM_DIRS:
            shutil.copytree(ROOT / relative, self.source / relative,
                            ignore=shutil.ignore_patterns("__pycache__"))
            for path in (self.source / relative).rglob("*"):
                if path.is_file():
                    target = self.target / path.relative_to(self.source)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(b"old package metadata\n")
        self.assertIn("window_mode.py", PROGRAM_FILES)
        self.assertIn("sidebar_compat.py", PROGRAM_FILES)
        self.assertNotIn("window-mode.json", PROGRAM_FILES)
        for directory in (self.target, self.source):
            (directory / "manage.py").write_text(MANAGE, encoding="utf-8")
        (self.source / "start.ps1").write_text(start_sentinel("new-start.json"), encoding="utf-8")
        (self.target / "start.ps1").write_text(start_sentinel("old-start.json"), encoding="utf-8")
        self.legacy(False)
        # Even an accidentally supplied package copy must never be deployed.
        (self.source / "window-mode.json").write_text('{"mode":"owned-popup"}', encoding="utf-8")
        self.preserved = {}
        names = ["sidebar-config.json", "update-config.json", "quota-history.json",
                 "quota-alert-state.json", "connection-history.json", "reset-signals.json",
                 "sidebar-status.json", "update-status.json", "supervisor-error.log",
                 "accounts/account.json", "history/archive.bin", "local-only.py",
                 "skills/local-only/local-data.bin", ".codex-plugin/local-state.bin"]
        for index, name in enumerate(names):
            path = self.target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            content = f"keep exactly {index}\r\n".encode() + b"\x00\xff\xef\xbb\xbf"
            path.write_bytes(content)
            self.preserved[name] = content
        content = b'{"pid":0,"hwnd":0,"fixture":"stale status"}\r\n'
        (self.target / "status.json").write_bytes(content)
        self.preserved["status.json"] = content

    def legacy(self, popup):
        entry = "sidebar_compat.py" if popup else "sidebar_plugin.py"
        (self.target / "sidebar_supervisor.py").write_text(
            "from pathlib import Path\nROOT=Path(__file__).resolve().parent\n"
            f"CHILD = ROOT / {entry!r}\n"
            "raise RuntimeError('Legacy code must be parsed, never imported')\n", encoding="utf-8")
        if popup:
            (self.target / "sidebar_compat.py").write_text("# legacy compat with hoverfix\n", encoding="utf-8")

    def mode(self, value):
        content = b"\xef\xbb\xbf" + ('{ "mode": "' + value + '" }\r\n').encode()
        (self.target / "window-mode.json").write_bytes(content)
        self.preserved["window-mode.json"] = content

    def snapshot_programs(self):
        paths = list(PROGRAM_FILES)
        for directory in PROGRAM_DIRS:
            paths.extend(str(p.relative_to(self.source)) for p in (self.source / directory).rglob("*") if p.is_file())
        return {name: (self.target / name).read_bytes() if (self.target / name).is_file() else None
                for name in paths}

    def assert_programs_restored(self, before):
        for name, content in before.items():
            path = self.target / name
            if content is None:
                self.assertFalse(path.exists(), name)
            else:
                self.assertEqual(content, path.read_bytes(), name)

    def run_update(self, success=True):
        result = subprocess.run([str(PWSH), "-NoProfile", "-NonInteractive", "-File",
                                 str(self.source / "update.ps1"), "-TargetPath", str(self.target)],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                encoding="utf-8", errors="replace", timeout=65)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout)
        for name, content in self.preserved.items():
            self.assertEqual(content, (self.target / name).read_bytes(), name)
        # Backup scope must never include accounts/config/history/mode/local extras.
        backups = self.target / ".update-backups"
        if backups.is_dir():
            for backup in backups.iterdir():
                for name in self.preserved:
                    self.assertFalse((backup / name).exists(), f"User state was backed up: {name}")
        return result

    def start_result(self, old=False):
        return json.loads((self.target / ("old-start.json" if old else "new-start.json")).read_text(encoding="utf-8-sig"))

    def test_legacy_popup_migrates_before_code_replacement_and_keeps_hoverfix(self):
        self.legacy(True)
        # Prove migration invokes the staged helper rather than any old helper.
        (self.target / "window_mode.py").write_text("raise RuntimeError('old helper invoked')\n", encoding="utf-8")
        self.run_update()
        self.assertEqual(json.loads((self.target / "window-mode.json").read_text()), {"mode": "owned-popup"})
        self.assertEqual(self.start_result()["mode"], "owned-popup")
        for name in PROGRAM_FILES:
            self.assertEqual((self.source / name).read_bytes(), (self.target / name).read_bytes(), name)
        for name in ("sidebar_compat.py", "window_mode.py", "hover_panel.py", "interaction_runtime.py"):
            self.assertTrue((self.target / name).is_file())

    def test_explicit_child_overrides_legacy_popup_without_rewriting_bytes(self):
        self.legacy(True)
        self.mode("child")
        self.run_update()
        self.assertEqual(self.start_result()["mode"], "child")

    def test_explicit_popup_is_preserved(self):
        self.mode("owned-popup")
        self.run_update()
        self.assertEqual(self.start_result()["mode"], "owned-popup")

    def test_default_child_does_not_create_persistent_mode(self):
        # Merely shipping a compat entry does not opt in.
        (self.target / "sidebar_compat.py").write_text("# unselected compat\n", encoding="utf-8")
        self.run_update()
        self.assertFalse((self.target / "window-mode.json").exists())
        self.assertEqual(self.start_result()["mode"], "child")

    def test_missing_legacy_supervisor_keeps_default(self):
        (self.target / "sidebar_supervisor.py").unlink()
        self.run_update()
        self.assertFalse((self.target / "window-mode.json").exists())

    def test_dynamic_legacy_assignment_does_not_opt_in(self):
        (self.target / "sidebar_supervisor.py").write_text(
            "# sidebar_compat.py\nCHILD = choose_entry()\n", encoding="utf-8")
        self.run_update()
        self.assertFalse((self.target / "window-mode.json").exists())

    def test_start_failure_restores_legacy_code_and_removes_created_mode(self):
        self.legacy(True)
        before = self.snapshot_programs()
        (self.source / "start.ps1").write_text(start_sentinel("new-start.json", 23), encoding="utf-8")
        result = self.run_update(False)
        self.assertIn("侧栏启动脚本返回失败", result.stdout)
        self.assert_programs_restored(before)
        self.assertFalse((self.target / "window-mode.json").exists())
        self.assertFalse((self.target / "old-start.json").exists())  # previously stopped

    def test_start_failure_preserves_existing_child(self):
        self.legacy(True)
        self.mode("child")
        before = self.snapshot_programs()
        (self.source / "start.ps1").write_text(start_sentinel("new-start.json", 23), encoding="utf-8")
        self.run_update(False)
        self.assert_programs_restored(before)

    def test_start_failure_preserves_existing_popup(self):
        self.mode("owned-popup")
        before = self.snapshot_programs()
        (self.source / "start.ps1").write_text(start_sentinel("new-start.json", 23), encoding="utf-8")
        self.run_update(False)
        self.assert_programs_restored(before)

    def test_start_failure_keeps_default_mode_absent(self):
        before = self.snapshot_programs()
        (self.source / "start.ps1").write_text(start_sentinel("new-start.json", 23), encoding="utf-8")
        self.run_update(False)
        self.assert_programs_restored(before)
        self.assertFalse((self.target / "window-mode.json").exists())

    def inject_helper_tail(self, code):
        with (self.source / "window_mode.py").open("a", encoding="utf-8") as stream:
            stream.write("\n" + code + "\n")

    def test_helper_failure_after_creating_mode_cleans_up_before_returning(self):
        self.legacy(True)
        before = self.snapshot_programs()
        self.inject_helper_tail("raise RuntimeError('injected post-create failure')")
        self.run_update(False)
        self.assert_programs_restored(before)
        self.assertFalse((self.target / "window-mode.json").exists())
        self.assertFalse((self.target / "new-start.json").exists())

    def test_bad_helper_output_after_create_cleans_up(self):
        self.legacy(True)
        before = self.snapshot_programs()
        self.inject_helper_tail("print('invalid trailing output')")
        self.run_update(False)
        self.assert_programs_restored(before)
        self.assertFalse((self.target / "window-mode.json").exists())

    def test_helper_failure_never_removes_existing_file(self):
        self.legacy(True)
        self.mode("child")
        before = self.snapshot_programs()
        self.inject_helper_tail("raise RuntimeError('injected helper failure')")
        self.run_update(False)
        self.assert_programs_restored(before)

    def test_invalid_existing_mode_aborts_without_program_changes(self):
        self.mode("invalid")
        before = self.snapshot_programs()
        self.run_update(False)
        self.assert_programs_restored(before)
        self.assertFalse((self.target / "new-start.json").exists())

    def test_post_deploy_compile_failure_restores_code_and_mode(self):
        self.legacy(True)
        before = self.snapshot_programs()
        # Modify only the disposable stage AFTER its preflight compilation.
        self.inject_helper_tail("(Path(__file__).parent/'sidebar_plugin.py').write_text('def invalid(:', encoding='utf-8')")
        result = self.run_update(False)
        self.assertIn("更新后的程序未通过 py_compile", result.stdout)
        self.assert_programs_restored(before)
        self.assertFalse((self.target / "window-mode.json").exists())
        self.assertFalse((self.target / "new-start.json").exists())

    def test_mid_deploy_copy_failure_restores_already_replaced_files(self):
        self.legacy(True)
        before = self.snapshot_programs()
        # The updater already captured stageFiles and all backups before migration.
        self.inject_helper_tail("(Path(__file__).parent/'sidebar_styles.py').unlink()")
        self.run_update(False)
        self.assert_programs_restored(before)
        self.assertFalse((self.target / "window-mode.json").exists())
        self.assertFalse((self.target / "new-start.json").exists())

    def test_backup_failure_never_migrates_or_replaces_code(self):
        self.legacy(True)
        before = self.snapshot_programs()
        (self.target / ".update-backups").write_bytes(b"fixture obstruction")
        self.run_update(False)
        self.assert_programs_restored(before)
        self.assertFalse((self.target / "window-mode.json").exists())

    def test_preflight_compile_failure_never_modifies_target(self):
        self.legacy(True)
        before = self.snapshot_programs()
        (self.source / "sidebar_compat.py").write_text("def invalid(:", encoding="utf-8")
        self.run_update(False)
        self.assert_programs_restored(before)
        self.assertFalse((self.target / "window-mode.json").exists())
        self.assertFalse((self.target / "manage-events.txt").exists())

    def running_fixture(self):
        self.legacy(True)
        (self.target / "fixture-running").touch()
        (self.target / "expect-supervisor-wait").touch()
        # A real, harmless parent/child pair exercises the mutex-release race.
        child_code = ("from pathlib import Path; import time; "
                      f"flag=Path({str(self.target / 'stop.request')!r}); "
                      "exec('while not flag.exists(): time.sleep(0.02)')")
        supervisor = self.target / "sidebar_supervisor.py"
        # Remove the parse-only trap before actually running this fixture.
        source = supervisor.read_text(encoding="utf-8").split("raise RuntimeError")[0]
        supervisor.write_text(source + f'''
import json, subprocess, sys, time
child = subprocess.Popen([sys.executable, '-c', {child_code!r}])
(ROOT/'status.json').write_text(json.dumps({{'pid':child.pid,'hwnd':0}}))
try:
    child.wait(timeout=55)
    time.sleep(1.5)
    (ROOT/'supervisor-exited').touch()
finally:
    if child.poll() is None:
        child.terminate()
        child.wait(timeout=5)
''', encoding="utf-8")
        proc = subprocess.Popen([sys.executable, str(supervisor)], stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW)

        def cleanup():
            (self.target / "stop.request").touch()
            try:
                proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.terminate()
                proc.wait(timeout=5)

        self.addCleanup(cleanup)
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            try:
                state = json.loads((self.target / "status.json").read_text())
                if state["pid"]:
                    break
            except (OSError, ValueError):
                pass
            self.assertIsNone(proc.poll(), "Fixture supervisor exited unexpectedly")
            time.sleep(0.02)
        else:
            self.fail("Fixture supervisor did not become ready")
        self.preserved["status.json"] = (self.target / "status.json").read_bytes()

    def test_running_update_waits_for_old_supervisor_before_restart(self):
        self.running_fixture()
        self.run_update()
        self.assertTrue(self.start_result()["supervisorExited"])
        self.assertEqual(self.start_result()["mode"], "owned-popup")
        self.assertEqual((self.target / "manage-events.txt").read_text().splitlines(), ["status", "stop"])

    def test_running_rollback_removes_migration_before_old_restart(self):
        self.running_fixture()
        before = self.snapshot_programs()
        (self.source / "start.ps1").write_text(start_sentinel("new-start.json", 23), encoding="utf-8")
        self.run_update(False)
        self.assert_programs_restored(before)
        self.assertFalse((self.target / "window-mode.json").exists())
        old = self.start_result(old=True)
        self.assertFalse(old["modeFileExists"])
        self.assertTrue(old["supervisorExited"])


class _RecordedResult(unittest.TestResult):
    """Keep worker results private; replay them on the runner thread in order."""

    def __init__(self):
        super().__init__()
        self.events = []

    def addSuccess(self, test):
        self.events.append(("addSuccess", ()))

    def addError(self, test, err):
        self.events.append(("addError", (err,)))

    def addFailure(self, test, err):
        self.events.append(("addFailure", (err,)))

    def addSkip(self, test, reason):
        self.events.append(("addSkip", (reason,)))


class _SandboxSuite(unittest.TestSuite):
    """Bound wall time without mocking updater execution or sharing fixtures."""

    def run(self, result, debug=False):
        if debug:
            return super().run(result, debug)

        def execute(test):
            recorded = _RecordedResult()
            test.run(recorded)
            return recorded.events

        with ThreadPoolExecutor(max_workers=4) as pool:
            pending = {pool.submit(execute, test): test for test in self}
            for future in as_completed(pending):
                test = pending[future]
                result.startTest(test)
                try:
                    for method, args in future.result():
                        getattr(result, method)(test, *args)
                except Exception:
                    result.addError(test, sys.exc_info())
                finally:
                    result.stopTest(test)
        return result


def load_tests(loader, tests, pattern):
    # unittest's normal module/discovery entry points both use this bounded suite.
    # No class/module fixtures or mutable state are shared between these cases.
    return _SandboxSuite(loader.loadTestsFromTestCase(WindowsUpdateTests))


if __name__ == "__main__":
    unittest.main()
