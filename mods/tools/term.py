"""你自己的长期命令行窗口：看终端屏幕、按 Ctrl-C 这类键、关掉不用的终端。

命令行窗口是 `term:名字`（如 `term:main`），用法和别的窗口一样：`type_word("term:main", "make")`
再 `send("term:main")`，整段原样敲进去并回车，第一次发就开一个新的（在 Bot 的运行目录）。
它是一个跨重启活着的 tmux 会话，需要 Bot 自身有 op。

输出停顿 1 秒、或攒满 10 秒就成一条消息进窗口，记录头上的名字是那一刻前台的程序：
写着 bash 就是回到了提示符，写着 make、python3 就是它还在前台。一条太长时只留开头和结尾。

什么时候叫醒你：
- 你发完以后，前台回到 shell，或者回到你敲字时的那个程序（比如在 python 的 `>>>` 里敲字，它答完）；
- shell 退出了、会话没了；
- 起服务器、进 REPL、开 ssh 这种前台换成别的程序的，它不会自己叫你：发的时候用 `send` 的 `wait`（如 `wait="30s"`）等第一批输出；
- 没人问时的输出（比如服务器日志）只记成未读；想每批都叫醒，`wake_me("follow term:main 30m", 理由)`。

这里的函数：
- `look`：看渲染后的屏幕（vim、top 这类整屏程序也能看），或往前翻最近若干行；
- `keys`：按键，用 tmux 的键名，比如 `C-c` 打断、`C-d`、`Up`、`Tab`、`Escape`、`q`，按完下一批输出会叫醒你；
- `close` 关掉一个终端，`terminals` 看开着哪些。

维护者想亲眼看，在机器上运行 `tmux -S data/term/tmux.sock attach -t main`。
"""

from __future__ import annotations


BOT_OP_ONLY = True


def look(name: str = "main", lines: int = 0) -> str:
    """看一个命令行窗口此刻的屏幕，或者最近若干行的滚动历史，返回去掉颜色的文字。

    @param
    name: 窗口名字，term:main 就写 main
    lines: 0 只看当前屏幕；写 200 就往前带上最近 200 行历史（最多留一万行）
    """
    from mods import term

    return term.look(name, lines)


def keys(name: str, names: str) -> str:
    """在命令行窗口里按键（不是打字，打字用 type_word/send），返回按了什么。

    @param
    name: 窗口名字，term:main 就写 main
    names: tmux 键名，空格分隔，如 "C-c"、"Up Enter"、"q"；要打的字用 type_word/send
    """
    from mods import term

    return term.keys(name, names)


def close(name: str) -> str:
    """关掉一个命令行窗口（结束里面的 shell 和还在跑的程序），返回结果。

    @param
    name: 窗口名字，term:main 就写 main
    """
    from mods import term

    return term.close(name)


def terminals() -> str:
    """列出开着的命令行窗口和各自此刻的前台程序。"""
    from mods import term

    return term.listing()


__all__ = ["look", "keys", "close", "terminals"]
