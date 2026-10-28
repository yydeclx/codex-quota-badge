"""Small Windows supervisor for the native sidebar companion.

The supervisor owns no account, quota, or Codex state.  It only starts the
existing sidebar process, restarts it once after an abnormal exit, and then
backs off when failures repeat in a short window.
"""
from __future__ import annotations

import ctypes as C
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
CHILD = ROOT / "sidebar_plugin.py"
LOG = ROOT / "supervisor-error.log"
MUTEX_NAME = r"Local\CodexQuotaSidebarSupervisor"
RESTART_WINDOW_SECONDS = 60.0
MAX_ABNORMAL_EXITS_IN_WINDOW = 2
CHILD_WAIT_SECONDS = 1.0
PROCESS_STOP_SECONDS = 3.0
ERROR_LOG_LIMIT = 64 * 1024


class ProcessCleanupError(RuntimeError):
    """A child could not be reaped; starting another would risk duplicates."""


class ProcessJob:
    """Close a private Windows job to stop only children launched by this owner."""

    def __init__(self) -> None:
        self.handle = None
        if os.name != "nt":
            return
        from ctypes import wintypes as W

        class BasicLimits(C.Structure):
            _fields_ = [("process_time", C.c_int64), ("job_time", C.c_int64),
                        ("flags", W.DWORD), ("min_working_set", C.c_size_t),
                        ("max_working_set", C.c_size_t), ("active_processes", W.DWORD),
                        ("affinity", C.c_size_t), ("priority", W.DWORD), ("scheduling", W.DWORD)]

        class ExtendedLimits(C.Structure):
            _fields_ = [("basic", BasicLimits), ("io_counters", C.c_uint64 * 6),
                        ("process_memory", C.c_size_t), ("job_memory", C.c_size_t),
                        ("peak_process_memory", C.c_size_t), ("peak_job_memory", C.c_size_t)]

        self.kernel = C.WinDLL("kernel32", use_last_error=True)
        self.kernel.CreateJobObjectW.argtypes = [C.c_void_p, C.c_wchar_p]
        self.kernel.CreateJobObjectW.restype = W.HANDLE
        self.kernel.SetInformationJobObject.argtypes = [W.HANDLE, C.c_int, C.c_void_p, W.DWORD]
        self.kernel.SetInformationJobObject.restype = W.BOOL
        self.kernel.AssignProcessToJobObject.argtypes = [W.HANDLE, W.HANDLE]
        self.kernel.AssignProcessToJobObject.restype = W.BOOL
        self.kernel.CloseHandle.argtypes = [W.HANDLE]
        self.kernel.CloseHandle.restype = W.BOOL
        self.handle = self.kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise C.WinError(C.get_last_error())
        limits = ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.kernel.SetInformationJobObject(self.handle, 9, C.byref(limits), C.sizeof(limits)):
            error = C.WinError(C.get_last_error())
            self.close()
            raise error

    def assign(self, child) -> None:
        if self.handle and not self.kernel.AssignProcessToJobObject(self.handle, child._handle):
            raise C.WinError(C.get_last_error())

    def close(self) -> None:
        if self.handle:
            if not self.kernel.CloseHandle(self.handle):
                raise C.WinError(C.get_last_error())
            self.handle = None


@contextmanager
def owned_process(command, *, popen=None, track_tree=True, **kwargs):
    """Bound cleanup waits and keep descendants inside this child's private job."""
    job = ProcessJob() if track_tree else None
    child = None
    entered = False
    try:
        child = (popen or subprocess.Popen)(command, **kwargs)
        if job is not None:
            job.assign(child)
        entered = True
        yield child
    finally:
        cleanup_error = None
        try:
            if job is not None:
                job.close()
        except OSError as exc:
            cleanup_error = exc
        if child is not None:
            try:
                if child.poll() is None:
                    try:
                        child.terminate()
                    except OSError:
                        pass  # Exit may race termination; still confirm it via wait.
                try:
                    child.wait(timeout=PROCESS_STOP_SECONDS)
                except subprocess.TimeoutExpired:
                    try:
                        child.kill()
                    except OSError:
                        pass
                    child.wait(timeout=PROCESS_STOP_SECONDS)
            except (OSError, subprocess.SubprocessError) as exc:
                cleanup_error = exc
        if cleanup_error is not None:
            raise ProcessCleanupError("Unable to stop owned process tree") from cleanup_error
        if child is not None and not entered:
            # Assignment failed before the caller could own/close these pipes.
            for stream in (child.stdin, child.stdout, child.stderr):
                if stream is not None:
                    stream.close()


def child_command() -> list[str]:
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return [str(pythonw), str(CHILD)]


def is_abnormal_exit(returncode: int | None) -> bool:
    return returncode is None or returncode != 0


def should_restart(abnormal_exit_times: list[float], now: float) -> bool:
    recent = [stamp for stamp in abnormal_exit_times if now - stamp <= RESTART_WINDOW_SECONDS]
    return len(recent) < MAX_ABNORMAL_EXITS_IN_WINDOW


def _write_error(message: str) -> None:
    try:
        tail = message.encode("utf-8", errors="replace")[-ERROR_LOG_LIMIT:]
        LOG.write_text(tail.decode("utf-8", errors="ignore"), encoding="utf-8")
    except OSError:
        pass


class SupervisorMutex:
    def __init__(self) -> None:
        self.handle = None

    def acquire(self) -> bool:
        if os.name != "nt":
            return True
        kernel32 = C.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [C.c_void_p, C.c_int, C.c_wchar_p]
        kernel32.CreateMutexW.restype = C.c_void_p
        self.handle = kernel32.CreateMutexW(None, 0, MUTEX_NAME)
        if not self.handle:
            raise C.WinError(C.get_last_error())
        return C.get_last_error() != 183

    def close(self) -> None:
        if self.handle and os.name == "nt":
            kernel32 = C.WinDLL("kernel32", use_last_error=True)
            kernel32.CloseHandle.argtypes = [C.c_void_p]
            kernel32.CloseHandle.restype = C.c_int
            kernel32.CloseHandle(self.handle)
            self.handle = None


def run_supervisor(*, sleep=time.sleep, popen=None) -> int:
    mutex = SupervisorMutex()
    try:
        if not mutex.acquire():
            return 0
        abnormal_exit_times: list[float] = []
        while True:
            try:
                # Updaters launched by the sidebar must survive its normal exit.
                with owned_process(child_command(), popen=popen, track_tree=False, cwd=str(ROOT),
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) as child:
                    returncode = child.wait()
            except ProcessCleanupError as exc:
                _write_error(str(exc))
                return 1
            except Exception as exc:
                returncode = 1
                _write_error(f"supervisor child launch/wait failed: {type(exc).__name__}: {exc}")

            if not is_abnormal_exit(returncode):
                return 0

            now = time.monotonic()
            abnormal_exit_times = [stamp for stamp in abnormal_exit_times if now - stamp <= RESTART_WINDOW_SECONDS]
            abnormal_exit_times.append(now)
            if not should_restart(abnormal_exit_times, now):
                _write_error("sidebar stopped after repeated abnormal exits")
                return 1
            sleep(CHILD_WAIT_SECONDS)
    finally:
        mutex.close()


if __name__ == "__main__":
    try:
        raise SystemExit(run_supervisor())
    except Exception:
        import traceback
        _write_error(traceback.format_exc())
        raise
