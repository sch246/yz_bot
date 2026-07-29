"""Lazy, reusable subprocess REPL sessions."""

import fcntl
import os
import select
import subprocess
import time
import weakref
from collections.abc import Callable


_instances = weakref.WeakSet()


class Repl:
    def __init__(self, command: list[str]):
        self.command = command
        self.process = None
        _instances.add(self)

    def start(self):
        self.process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.process.stdin.flush()
        self.process.stdout.flush()
        for pipe in (self.process.stdout, self.process.stderr):
            fcntl.fcntl(pipe, fcntl.F_SETFL, os.O_NONBLOCK)

    def stop(self):
        process, self.process = self.process, None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        for pipe in (process.stdin, process.stdout, process.stderr):
            if pipe is not None and not pipe.closed:
                pipe.close()

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def run_code(
        self,
        code: str,
        callback: Callable[[str], None],
        endsigns: list[str],
        waittime: float = 0.2,
        timeout: float = 0,
    ):
        if not self.is_running():
            self.start()

        self.process.stdin.write(code + "\n")
        self.process.stdin.flush()
        time.sleep(waittime)

        started_at = time.monotonic()
        while timeout == 0 or time.monotonic() - started_at < timeout:
            readable, _, _ = select.select(
                [self.process.stdout, self.process.stderr], [], [], 0.1
            )
            if self.process.stdout in readable:
                try:
                    output = os.read(self.process.stdout.fileno(), 1024).decode()
                except BlockingIOError:
                    output = ""
                if output:
                    callback(output.strip())
                    if any(output.rstrip().endswith(sign) for sign in endsigns):
                        return
            if self.process.stderr in readable:
                error = self.process.stderr.readline()
                if error:
                    callback(f"错误: {error.strip()}")
            if not self.is_running():
                output, error = self.process.communicate()
                if output:
                    callback(output.strip())
                if error:
                    callback(f"错误: {error.strip()}")
                callback(f"程序已退出，返回值 {self.process.returncode}")
                return
        callback("超时")


def ensure(command: list[str]):
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as error:
        return False, f"环境错误:\n{error.stderr}"
    except FileNotFoundError:
        return False, f"{command[0]} 未安装或不在系统PATH中"
    return True, None


def on_exit():
    for repl in list(_instances):
        try:
            repl.stop()
        except Exception:
            # One broken child must not leave the module's other children alive.
            pass
