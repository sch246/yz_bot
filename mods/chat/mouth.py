"""嘴：脑子在 QQ 里写好的话，换成她自己的口气再发出去。

脑子（主运行）照旧读笔记、干活、决定说不说、说什么，交给 ``send`` 的那段话在这里只当
草稿。先用一次关思考的小调用把草稿抽成一句意思（不带原句措辞），再用一次独立的短调用
措辞：只拿「数理柚子」人设卡、这个窗口最近 40 条消息、那句意思，不拿笔记、工具和规矩。

WHY: 2026-11-07 维护者盲评 30 个群聊场景（实验在仓库外）：她当时实际说的 30 次只被选
1 次；同样是 deepseek-flash，换成「人设卡 + 几十条消息 + 一行意思」的小上下文后被选 9 次，
gemini-3.8-flash 19 次。口气坏在 5.8 万 token 的大杂烩上下文和一路叠上去的规矩，不在模型。
意思先抽象一次，是因为那次实验喂给嘴的就是抽象过的意思；直接给草稿会不会把口气拖回去，
还没测过。删掉抽象这一步之前先做那轮对照。

WHY: 所有 QQ 窗口默认开着（维护者 2026-11-08：「效果是确定性地好的，全开即可」），
``#mouth off`` 按窗口关，``#mouth off all`` 全关。任何一步出错、超时或判不了，就原样发草稿——
嘴坏了不能让她说不出话。长段、带链接或代码块、命令、草稿中间夹着图片表情的，也原样发：
那些是干活的输出或要精确的东西，口气不是它们的问题，改写只会丢信息。

每次改写记一行到 data/chat/mouth.jsonl（窗口、最近消息、草稿、意思、发出的话），给维护者回头盲评。
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from mods import identity, llm, storage

from . import screen, windows

DEFAULT_MODEL = "google/gemini-3.8-flash"
INTENT_MODEL = "deepseek/deepseek-flash"
PERSONA = "数理柚子"
CONTEXT = 40
TIMEOUT = 25.0
MAX_DRAFT = 160
_LOG = Path("data") / "chat" / "mouth.jsonl"
_log_lock = threading.Lock()

_LEAD = re.compile(r"^((?:\s*\[CQ:(?:reply|at),[^\]]*\])+)\s*", re.DOTALL)
_SKIP = re.compile(r"https?://|```|\[CQ:")
_HEAD = re.compile(r"^(?:【[^】]*】)?(?P<name>.*)\((?P<uid>\d+)\) (?P<time>\d\d:\d\d:\d\d)(?: \| (?P<id>-?\d+))?\s*$")


def config() -> dict:
    """``{"all": 总开关, "off": [关掉的窗口标签...], "model": 选择}``，存在 agent/config 的 mouth 键下。"""
    data = storage.get("agent", "config")
    value = data.get("mouth")
    if not isinstance(value, dict):
        value = {}
    off = [str(item) for item in value.get("off", []) if isinstance(item, str)]
    return {"all": value.get("all", True) is not False, "off": off, "model": str(value.get("model") or DEFAULT_MODEL)}


def save_config(**changes) -> None:
    value = {**config(), **changes}
    value["off"] = sorted(set(value["off"]))
    storage.get("agent", "config")["mouth"] = value
    storage.save()


def enabled(window: tuple) -> bool:
    settings = config()
    return window[0] in windows.QQ and settings["all"] and windows.label(window) not in settings["off"]


def _persona() -> str:
    entry = storage.get("llm_system", "prompts").get(PERSONA)
    if isinstance(entry, list) and entry and isinstance(entry[0], dict):
        return str(entry[0].get("content") or "").strip()
    return ""


def _place(window: tuple) -> str:
    name = windows.known_name(window)
    if window[0] == "group":
        return f"你在 QQ 群「{name or '群聊'}」里，大家叫你柚子。"
    return f"你在 QQ 上和「{name or '对方'}」私聊，对方叫你柚子。"


def _render(text: str, names: dict[str, str], ids: dict[str, str]) -> str:
    def reply(match):
        who = ids.get(match.group(1))
        return f"[回复 {who}] " if who else "[回复] "

    text = re.sub(r"\[CQ:reply,id=(-?\d+)[^\]]*\]", reply, text)
    text = re.sub(r"\[CQ:at,qq=(\d+)[^\]]*\]", lambda m: "@" + names.get(m.group(1), "某人") + " ", text)
    text = re.sub(r"\[CQ:image,[^\]]*summary=(?:\[|&#91;)?动画表情[^\]]*\]", "[动画表情]", text)
    text = re.sub(r"\[CQ:image[^\]]*\]", "[图片]", text)
    text = re.sub(r"\[CQ:(?:face|mface)[^\]]*\]", "[表情]", text)
    text = re.sub(r"\[CQ:record[^\]]*\]", "[语音]", text)
    text = re.sub(r"\[CQ:(?:json|markdown|forward|file|video)[^\]]*\]", "[卡片]", text)
    text = re.sub(r"\[CQ:[^\]]*\]", "", text)
    return text.replace("&#91;", "[").replace("&#93;", "]").replace("&amp;", "&").strip()


def recent(window: tuple, count: int = CONTEXT) -> list[dict]:
    """最近 *count* 条消息：``[{who, time, text, is_her}]``，名字不带 QQ 号。"""
    try:
        me = str(identity.bot_id())
    except RuntimeError:
        me = ""
    rows = []
    for item in screen.records(window, count):
        first, _, body = item["text"].partition("\n")
        head = _HEAD.match(first)
        if head is None:
            continue
        body = "\n".join(line[4:] if line.startswith("    ") else line for line in body.splitlines())
        rows.append({"uid": head["uid"], "name": head["name"].strip() or "（空白昵称）", "id": head["id"],
                     "time": head["time"], "body": body})
    names = {row["uid"]: ("柚子" if row["uid"] == me else row["name"]) for row in rows}
    ids = {row["id"]: names[row["uid"]] for row in rows if row["id"]}
    out = []
    for row in rows:
        text = _render(row["body"], names, ids)
        if text:
            out.append({"who": names[row["uid"]], "time": row["time"], "text": text[:300], "is_her": row["uid"] == me})
    return out


def _transcript(context: list[dict]) -> str:
    return "\n".join(f"[{m['time']}] {'柚子（你）' if m['is_her'] else m['who']}: {m['text']}" for m in context)


def _ask(selection: str, messages: list[dict], **extra) -> str:
    client = llm.get_client()
    provider, api_model, _ = llm.resolve_model(client.config, selection)
    openai = client._client_for(provider)
    if openai is None:
        raise ValueError(f"Provider {provider} not configured")
    response = openai.with_options(timeout=TIMEOUT).chat.completions.create(model=api_model, messages=messages, **extra)
    llm._record_usage(selection, response)
    return str(response.choices[0].message.content or "").strip()


def _intent(transcript: str, draft: str) -> str:
    return _ask(INTENT_MODEL, [
        {"role": "system", "content": "你帮忙概括一条群聊回复的意图。"},
        {"role": "user", "content": f"聊天记录：\n{transcript}\n\n柚子打算接着发：\n{draft}\n\n"
         "用一句话概括柚子这次想表达什么、对谁说、什么态度。必须传达的事实（数字、名字、结论、对方问的答案）"
         "照留，但不要引用或改写原句的措辞，不超过 60 字。只写这一句。"}],
        temperature=0.3, reasoning_effort="none")


def _speak(window: tuple, transcript: str, intent: str, model: str) -> str:
    system = _persona() + "\n\n" + _place(window)
    where = "群里" if window[0] == "group" else "私聊里"
    prompt = (f"这是{where}最近的聊天记录（标着「柚子（你）」的是你自己之前发的）：\n\n{transcript}\n\n"
              f"现在轮到你说话。你这次想表达的意思大致是：{intent}\n\n"
              "直接写出你要发的消息。想分几条发就用空行隔开。只写消息本身，不要写别的。")
    extra = {"temperature": 1.3, "reasoning_effort": "none"} if model.startswith("deepseek/") else {}
    return _ask(model, [{"role": "system", "content": system}, {"role": "user", "content": prompt}], **extra)


def _clean(text: str) -> str:
    text = re.sub(r"^\s*柚子(?:（你）|\(你\))?\s*[:：]\s*", "", text.strip())
    text = re.sub(r"^\s*\[\d\d:\d\d:\d\d\]\s*", "", text)
    if len(text) >= 2 and text[0] + text[-1] in ('""', "“”", "「」"):
        text = text[1:-1].strip()
    return text


def _record(entry: dict) -> None:
    try:
        with _log_lock:
            _LOG.parent.mkdir(parents=True, exist_ok=True)
            with _LOG.open("a", encoding="utf-8") as file:
                file.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass


def phrase(window: tuple, draft: str) -> str | None:
    """草稿换成她的口气；返回 None 表示原样发草稿。"""
    lead = _LEAD.match(draft)
    prefix = lead.group(1).strip() if lead else ""
    body = draft[lead.end():] if lead else draft
    stripped = body.strip()
    if (not stripped or len(stripped) > MAX_DRAFT or _SKIP.search(stripped)
            or stripped[0] in ".#/。！!"):
        return None
    model = config()["model"]
    entry = {"time": time.time(), "window": windows.label(window), "model": model, "draft": draft}
    started = time.monotonic()
    try:
        context = recent(window)
        entry["context"] = context
        transcript = _transcript(context)
        entry["intent"] = intent = _intent(transcript, stripped)
        said = _clean(_speak(window, transcript, intent, model))
    except Exception as error:
        entry["error"] = f"{type(error).__name__}: {str(error)[:200]}"
        _record(entry)
        return None
    entry["ms"] = int((time.monotonic() - started) * 1000)
    if not said or "[CQ:" in said or len(said) > max(3 * len(stripped), 120):
        entry["rejected"] = said
        _record(entry)
        return None
    entry["said"] = said
    _record(entry)
    return f"{prefix}{said}" if prefix else said
