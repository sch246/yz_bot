"""Entry point for the module-based Bot architecture."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import sys


CODE_ROOT = Path(__file__).resolve().parent
_sigint_received = False


def _handle_sigint(_signal, _frame) -> None:
    """Let the first SIGINT enter graceful Exit; ignore repeats while saving."""
    global _sigint_received
    if _sigint_received:
        return
    _sigint_received = True
    raise KeyboardInterrupt


def _prepare_runtime_root() -> Path:
    runtime_root = Path(os.environ.get("YUZU_RUNTIME_ROOT", CODE_ROOT)).resolve()
    runtime_root.mkdir(parents=True, exist_ok=True)
    os.chdir(runtime_root)
    try:
        from dotenv import load_dotenv
    except ImportError:
        pass
    else:
        # Load the deployment-specific OneBot endpoint from this code tree.
        load_dotenv(CODE_ROOT / ".env")
    return runtime_root


def main() -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--smoke", action="store_true")
    arguments, _ = parser.parse_known_args()

    signal.signal(signal.SIGINT, _handle_sigint)
    runtime_root = _prepare_runtime_root()
    loaded = None
    try:
        print("启动中", flush=True)
        import mods

        loaded = mods
        if arguments.smoke:
            print(
                "smoke required core loaded; optional failures do not fail this check: "
                f"{len(mods.available)}/{len(mods.ctx)} modules "
                f"in {runtime_root}"
            )
            if mods.import_failures:
                print("optional import failures:", sorted(mods.import_failures))
            if mods.load_failures:
                print("optional load failures:", sorted(mods.load_failures))
            return 0
        if mods.import_failures:
            print("可选模块 Import 失败：" + ", ".join(sorted(mods.import_failures)))
        if mods.load_failures:
            print("可选模块 Load 失败：" + ", ".join(sorted(mods.load_failures)))
        print("连接完成")
        print("加载完成")
        print(f"{mods.identity.bot_name()}({mods.identity.bot_id()})启动了！")
        if "-a" in sys.argv or "--auto-reboot" in sys.argv:
            print("自动重启已开启")
        if "-d" in sys.argv or "--debug" in sys.argv:
            print("debug模式")
        mods.bot.run()
        return 0
    except KeyboardInterrupt:
        print("bye.")
        return 0
    finally:
        if loaded is not None:
            loaded.exit()


if __name__ == "__main__":
    raise SystemExit(main())
