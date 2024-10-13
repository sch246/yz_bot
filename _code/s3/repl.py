import subprocess
import select
import time
import fcntl
import os
from typing import Callable, Optional

class Repl:
    def __init__(self, command: list):
        self.process = None
        self.command = command

    def start(self):
        self.process = subprocess.Popen(
            self.command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1
        )
        self.process.stdin.flush()
        self.process.stdout.flush()

        for pipe in [self.process.stdout, self.process.stderr]:
            fcntl.fcntl(pipe, fcntl.F_SETFL, os.O_NONBLOCK)

    def stop(self):
        if self.process:
            self.process.terminate()
            self.process = None

    def is_running(self):
        return self.process is not None and self.process.poll() is None

    def run_code(self, code: str, callback: Callable[[str], None], endsign: str, waittime: float = 0.2, timeout: float = 0):
        if not self.is_running():
            self.start()


        self.process.stdin.write(code + '\n')
        self.process.stdin.flush()

        time.sleep(waittime)

        start_time = time.time()
        while timeout == 0 or time.time() - start_time < timeout:
            readable, _, _ = select.select([self.process.stdout, self.process.stderr], [], [], 0.1)

            if self.process.stdout in readable:
                try:
                    output = os.read(self.process.stdout.fileno(), 1024).decode()
                    if output:
                        callback(output.strip())
                        if output.rstrip().endswith(endsign):
                            return
                except BlockingIOError:
                    pass

            if self.process.stderr in readable:
                error = self.process.stderr.readline()
                if error:
                    callback(f"错误: {error.strip()}")

            if not self.is_running():
                remaining_output, remaining_error = self.process.communicate()
                if remaining_output:
                    callback(remaining_output.strip())
                if remaining_error:
                    callback(f"错误: {remaining_error.strip()}")
                callback(f'程序已退出，返回值 {self.process.returncode}')
                return

        callback("超时")

def ensure(command: list) -> tuple[bool, Optional[str]]:
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
        return True, None
    except subprocess.CalledProcessError as e:
        return False, f"环境错误:\n{e.stderr}"
    except FileNotFoundError:
        return False, f"{command[0]} 未安装或不在系统PATH中"
