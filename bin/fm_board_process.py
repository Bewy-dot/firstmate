"""Short-lived board command guards, shared by the viewer and snapshot readers.

Each guard owns a new command process group and kills that group on completion,
timeout, signal, or owner death (including SIGKILL). A private pipe detects owner
exit without relying on PID reuse; shell callers use their stable parent identity.
Guards poll every 50ms, reap their command, and never retain the owner's pipe in
command descendants. They are command-scoped subprocesses, not a resident service.
The shell entry --run SECONDS COMMAND... is used only by board snapshot timeouts
so nested timeout utilities cannot move unguarded children into separate groups.
"""
import math
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time


def limit(name, maximum):
    """Test/operator overrides may shorten a hard bound, never disable it."""
    try:
        value = float(os.environ.get(name, maximum))
        if math.isfinite(value) and value > 0:
            return min(value, maximum)
    except ValueError:
        pass
    return maximum


class Command:
    def __init__(self, argv, timeout, **kwargs):
        inherited = tuple(kwargs.pop('pass_fds', ()))
        reader, self.writer = os.pipe()
        try:
            self.process = subprocess.Popen(
                [sys.executable, str(Path(__file__).resolve()), '--guard',
                 str(reader), str(timeout), ','.join(map(str, inherited)), *map(str, argv)],
                pass_fds=(reader, *inherited), start_new_session=True, **kwargs)
        except BaseException:
            os.close(self.writer)
            self.writer = None
            raise
        finally:
            os.close(reader)

    def close_pipe(self):
        if self.writer is not None:
            os.close(self.writer)
            self.writer = None

    def poll(self):
        result = self.process.poll()
        if result is not None:
            self.close_pipe()
        return result

    def close(self):
        self.close_pipe()
        self.process.wait()


def run(argv, timeout, **kwargs):
    command = Command(argv, timeout, **kwargs)
    try:
        out, err = command.process.communicate()
        if command.process.returncode == 124:
            raise subprocess.TimeoutExpired(argv, timeout, output=out, stderr=err)
        result = subprocess.CompletedProcess(argv, command.process.returncode, out, err)
        result.check_returncode()
        return result
    finally:
        command.close()


def guard(argv, timeout, reader=None, inherited=()):
    parent = os.getppid()
    if parent == 1:
        return 143
    stopped = [False]
    for sig in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stopped.__setitem__(0, True))
    deadline = time.monotonic() + timeout
    child = None
    try:
        child = subprocess.Popen(argv, start_new_session=True, pass_fds=inherited)
        while True:
            if stopped[0] or os.getppid() != parent:
                return 143
            if reader is not None and select.select([reader], [], [], 0)[0]:
                if not os.read(reader, 1):
                    return 143
            if time.monotonic() >= deadline:
                return 124
            result = child.poll()
            if result is not None:
                return result if result >= 0 else 128 - result
            time.sleep(.05)
    finally:
        if child is not None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
        if reader is not None:
            os.close(reader)


if __name__ == '__main__':
    if len(sys.argv) >= 6 and sys.argv[1] == '--guard':
        inherited = tuple(int(fd) for fd in sys.argv[4].split(',') if fd)
        sys.exit(guard(sys.argv[5:], float(sys.argv[3]), int(sys.argv[2]), inherited))
    if len(sys.argv) >= 4 and sys.argv[1] == '--run':
        # Survive the containing snapshot group being killed long enough to
        # clean up our own nested group, then exit on the parent identity check.
        os.setsid()
        sys.exit(guard(sys.argv[3:], float(sys.argv[2])))
    sys.exit('fm_board_process: private command guard entry only')
