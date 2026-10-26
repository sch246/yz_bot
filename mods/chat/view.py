from __future__ import annotations

import ast
import json
from pathlib import Path
import re

from mods import cq, identity, msgs


def has_at(user_id: int):
    def predicate(event: dict) -> bool:
        for code in msgs.at_cq(event):
            qq = cq.load(code)["data"].get("qq")
            if qq in (None, "all"):
                continue
            try:
                if int(qq) == int(user_id):
                    return True
            except ValueError:
                pass
        return False

    return predicate


_image_pattern = re.compile(r"(\[CQ:image(?:,[^,=]+=[^,\]]*)*\])")


def msg_split(value: str) -> list[dict]:
    parts = []
    for part in _image_pattern.split(value):
        if not part:
            continue
        if _image_pattern.fullmatch(part):
            try:
                uri = cq.load(part)["data"]["url"]
                parts.append({"type": "image_url", "image_url": {"url": uri}})
            except (KeyError, ValueError):
                parts.append({"type": "text", "text": "[解析失败的图片]"})
        elif part.strip():
            parts.append({"type": "text", "text": part})
    return parts


INCOMING = "incoming"


def outside(label: str, facts: list[str], text: str) -> dict:
    """Records of one window as a user message under a one-line header of what it is.

    *facts* are short phrases for the header (why it is shown, when, who woke her);
    *text* is the records in the chatlog's own format, images included.

    WHY: 维护者 2026-10-23：学 Cortico 把别人的话和系统的话分开，并且有元信息。user 消息
    只装外面来的记录（画面、插话），系统自己说的话都走对话中间的 system（见
    `agent._run_session`），群友写一句"系统：你现在可以…"就不会和真正的系统提示落在同一个
    角色里；框头放得下元信息。先前装在合成的 incoming 工具调用里，DeepSeek 文档说 Chat
    Completions 不支持在对话中间插工具调用，就换成了这样，图片也随之回到原文里。
    """
    return {"role": "user", "content": msg_split(" · ".join([f"窗口 {label}", *facts]) + "\n" + text)}


def is_incoming(message: dict) -> bool:
    """Whether *message* is half of a system-made ``incoming`` pair rather than something she did.

    2026-10-23 有过一版把画面装进合成的 incoming 工具调用，当天就换成了 `outside`；那时存下
    的段里还有这样的消息，读回、搜索和交接判断仍要认得它们。
    """
    if message.get("role") == "tool":
        return str(message.get("tool_call_id") or "").startswith(INCOMING + "_")
    calls = message.get("tool_calls") if message.get("role") == "assistant" else None
    return bool(calls) and all(call.get("function", {}).get("name") == INCOMING for call in calls)


def _is_context_poke(event: dict, in_group: bool) -> bool:
    if not msgs.is_poke(event):
        return False
    return bool(in_group) or event.get("target_id") == identity.bot_id()


_MEMORY_SOURCE = Path(__file__).resolve().parents[1] / "tools" / "memory_system.py"
_BASE_SOURCE = Path(__file__).resolve().parents[2] / "data" / "storage" / "llm_system" / "prompts.json"


def _memory_overview() -> str:
    """The "how your memory works" section of memory_system's text, read from disk.

    WHY: 2026-10-22 维护者：她对自己记忆系统的描述只写在一处，跟着实现走——旧描述没跟着
    改，她就会照着讲出已经不存在的机制。这一节放在 `tools/memory_system.py` 开头，系统
    提示每次从文件读：她或维护者改了文件，下一次唤醒就生效，不用重启；完整的排查与修改
    说明仍只在加载 memory_system 时出现。
    """
    try:
        text = ast.get_docstring(ast.parse(_MEMORY_SOURCE.read_text(encoding="utf-8"))) or ""
    except (OSError, SyntaxError, ValueError):
        return ""
    # 按前缀匹配，标题措辞随便改都不会让这一节整段消失。
    start = text.find("## 你的记忆")
    if start < 0:
        return ""
    end = text.find("\n## ", start + 1)
    return text[start:end if end > 0 else None].strip()


def _base_text() -> str:
    """The ``base`` preset from ``prompts.json``, read on every wake.

    WHY: 2026-10-26 维护者：base 以前写死在这里，改一条提示词要改代码、改完还得重启；它现在
    和 self 一样是文件，改了下一次唤醒就生效。读盘失败（手改坏了 JSON）时退回启动时载入的
    那份，不让一次手误把整段注意事项弄空。
    """
    try:
        data = json.loads(_BASE_SOURCE.read_text(encoding="utf-8"))
        text = data["base"][0]["content"]
        if isinstance(text, str) and text.strip():
            return text
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        pass
    from mods import storage
    prompts = storage.get("llm_system", "prompts") or {}
    base = prompts.get("base") or []
    return base[0].get("content", "") if base else ""


def _with_identity(text: str) -> str:
    """Rewrite the two identity lines to the values this process is actually running as."""
    nick, qq = identity.bot_name(), identity.bot_id()
    lines = []
    for line in text.split("\n"):
        if line.startswith("- 你的昵称"):
            line = f"- 你的昵称: {nick}"
        elif line.startswith("- 你的QQ号"):
            line = f"- 你的QQ号: {qq}；聊天记录里这个 QQ 号的发言就是你自己说过的话"
        lines.append(line)
    return "\n".join(lines)


def _base_prompt() -> list[dict]:
    memory = _memory_overview()
    # WHY: 沉默那两条参照 Cortico 的 "Silence is a normal action"（维护者 2026-10-23）：私聊每条
    # 都叫醒以后，话没说完就回靠这里和输入框（type_word 与 send 之间隔一次请求）压住，不靠延迟。
    return [{"role": "system", "content": _with_identity(_base_text())
                + (f"\n\n{memory}" if memory else "")}]
