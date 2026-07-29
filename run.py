"""Run the Bot, or request an explicit check/smoke mode."""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time


ROOT = Path(__file__).resolve().parent
MAIN = ROOT / "main.py"
VENV_PYTHON = ROOT / ".venv" / "bin" / "python"
DEFAULT_API_PORT = 5700
DEFAULT_LISTEN_PORT = 5701


def _runtime_python() -> str:
    if not VENV_PYTHON.is_file():
        raise SystemExit(f"environment is missing; run `uv sync` in {ROOT}")
    return str(VENV_PYTHON)


def check() -> int:
    paths = sorted((ROOT / "mods").rglob("*.py"))
    for path in paths:
        compile(path.read_text(), str(path), "exec")

    files = {
        path.stem
        for path in (ROOT / "mods").glob("*.py")
        if path.name != "__init__.py" and not path.name.startswith("_")
    }
    packages = {
        path.name
        for path in (ROOT / "mods").iterdir()
        if path.is_dir()
        and not path.name.startswith("_")
        and (path / "__init__.py").is_file()
    }
    collisions = sorted(files & packages)
    if collisions:
        raise RuntimeError("file/package collisions: " + ", ".join(collisions))
    print(
        f"check passed: {len(paths)} Python files, "
        f"{len(files | packages)} public modules"
    )
    return 0


class _FakeOneBot(BaseHTTPRequestHandler):
    message_id = 1

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length) or b"{}")
        action = self.path.strip("/")
        if action == "get_login_info":
            data = {"user_id": 10000, "nickname": "TrialBot"}
        elif action == "send_msg":
            type(self).message_id += 1
            data = {"message_id": type(self).message_id}
        elif action == "get_msg":
            data = {
                "time": 0,
                "message_id": request.get("message_id", 1),
                "message": "trial",
                "sender": {"user_id": 10000, "nickname": "TrialBot"},
            }
        else:
            data = {}
        body = json.dumps({"retcode": 0, "data": data}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args) -> None:
        return


def _run_smoke(runtime_root: Path, api_port: int) -> int:
    environment = os.environ.copy()
    environment["YUZU_RUNTIME_ROOT"] = str(runtime_root)
    completed = subprocess.run(
        [
            _runtime_python(),
            str(MAIN),
            "--smoke",
            "-q",
            str(api_port),
            "-p",
            "0",
        ],
        cwd=ROOT,
        env=environment,
        check=False,
    )
    return completed.returncode


def smoke(use_migrated_state: bool = False) -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOneBot)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        if use_migrated_state:
            return _run_smoke(ROOT, server.server_port)
        with tempfile.TemporaryDirectory(prefix="yuzu-smoke-") as directory:
            runtime_root = Path(directory)
            data_root = runtime_root / "data"
            data_root.mkdir()
            (runtime_root / "config.json").write_text(
                json.dumps({"ops": [10000], "nicknames": ["TrialBot"]}),
                encoding="utf-8",
            )
            (data_root / "pyload.py").write_text(
                "mc_path = " + repr(str(runtime_root / "minecraft")) + "\n"
                "mc_worldname = 'smoke-world'\n"
                "mc_packformat = 1\n",
                encoding="utf-8",
            )
            return _run_smoke(runtime_root, server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def _child_args(arguments) -> list[str]:
    result = ["-q", str(arguments.qq), "-p", str(arguments.port)]
    if arguments.log_only:
        result.append("-l")
    if arguments.debug:
        result.append("-d")
    if arguments.auto_reboot:
        result.append("-a")
    return result


def serve(arguments) -> int:
    child_args = _child_args(arguments)
    while True:
        process = subprocess.Popen(
            [_runtime_python(), str(MAIN), *child_args],
            cwd=ROOT,
        )
        forwarded = False

        def forward(sig, _frame) -> None:
            nonlocal forwarded
            if forwarded or process.poll() is not None:
                return
            forwarded = True
            process.send_signal(sig)

        original = signal.signal(signal.SIGINT, forward)
        try:
            returncode = process.wait()
        finally:
            signal.signal(signal.SIGINT, original)
        print("Bot exited with", returncode)
        if returncode == 233 or (arguments.auto_reboot and returncode != 0):
            time.sleep(1)
            continue
        return returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="compile without importing")
    mode.add_argument(
        "--smoke",
        action="store_true",
        help="boot the required core with temporary state; report optional failures",
    )
    mode.add_argument(
        "--smoke-migrated",
        action="store_true",
        help="boot the required core against current runtime state; may load/save that state",
    )
    parser.add_argument("-q", "--qq", type=int, default=DEFAULT_API_PORT)
    parser.add_argument("-p", "--port", type=int, default=DEFAULT_LISTEN_PORT)
    parser.add_argument("-l", "--log-only", action="store_true")
    parser.add_argument("-d", "--debug", action="store_true")
    parser.add_argument("-a", "--auto-reboot", action="store_true")
    arguments = parser.parse_args()
    if arguments.check:
        return check()
    if arguments.smoke:
        return smoke()
    if arguments.smoke_migrated:
        return smoke(use_migrated_state=True)
    return serve(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
