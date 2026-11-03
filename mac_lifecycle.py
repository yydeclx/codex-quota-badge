"""Per-installation macOS sidebar ownership and safe updater handoff."""
import contextlib
import fcntl
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


@contextlib.contextmanager
def instance_guard(directory):
    root = Path(directory).resolve()
    with (root / '.mac-sidebar.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Sidebar already running for this installation') from None
        pid_path = root / '.mac-sidebar.pid'
        pid_path.write_text(str(os.getpid()), encoding='ascii')
        try:
            yield
        finally:
            pid_path.unlink(missing_ok=True)
            fcntl.flock(lock, fcntl.LOCK_UN)


def _running(pid):
    try:
        os.kill(pid, 0)
        state = subprocess.check_output(['ps', '-p', str(pid), '-o', 'stat='], text=True).strip()
        return bool(state) and not state.startswith('Z')
    except (ProcessLookupError, subprocess.CalledProcessError):
        return False


def _legacy_pids(root):
    """Prove cwd before signaling pre-v1.5.0 instances lacking PID files."""
    listing = subprocess.check_output(['ps', '-axo', 'pid=,args='], text=True)
    result = []
    for line in listing.splitlines():
        fields = line.strip().split(None, 1)
        if len(fields) != 2 or not fields[0].isdigit():
            continue
        pid, command = int(fields[0]), fields[1]
        if pid == os.getpid() or not (command.endswith(' mac_sidebar.py') or command == 'python3 mac_sidebar.py'):
            continue
        try:
            cwd = subprocess.check_output(['lsof', '-a', '-p', str(pid), '-d', 'cwd', '-Fn'],
                                          text=True, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.CalledProcessError):
            raise RuntimeError('Cannot establish ownership of a legacy sidebar process') from None
        dirs = [line[1:] for line in cwd.splitlines() if line.startswith('n')]
        if not dirs:
            raise RuntimeError('Cannot establish cwd of a legacy sidebar process')
        if Path(dirs[-1]).resolve() == root:
            result.append(pid)
    return result


def stop_for_update(directory, timeout=8.0):
    root = Path(directory).resolve()
    if not root.is_dir():
        raise RuntimeError('Target installation does not exist')
    pid_path = root / '.mac-sidebar.pid'
    pids = set(_legacy_pids(root))
    if pid_path.exists():
        try:
            pid = int(pid_path.read_text(encoding='ascii').strip())
        except ValueError:
            raise RuntimeError('Invalid sidebar PID file') from None
        if pid > 1 and _running(pid):
            if str(root / 'mac_sidebar.py') not in _command(pid).split():
                raise RuntimeError('Sidebar PID points to an unrelated process')
            pids.add(pid)
    for pid in pids:
        os.kill(pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not any(_running(pid) for pid in pids):
            break
        time.sleep(.1)
    if any(_running(pid) for pid in pids):
        raise RuntimeError('Existing sidebar did not exit; update aborted')
    with (root / '.mac-sidebar.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Sidebar lock still held; update aborted') from None
    return bool(pids)


def _command(pid):
    return subprocess.check_output(['ps', '-p', str(pid), '-o', 'args='], text=True).strip()


if __name__ == '__main__':
    if len(sys.argv) != 3 or sys.argv[1] != 'stop':
        raise SystemExit('usage: mac_lifecycle.py stop INSTALL_DIR')
    try:
        print('was_running=' + str(int(stop_for_update(sys.argv[2]))))
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc)) from exc
