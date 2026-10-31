"""Global agent context, settings, and QQ-window hints."""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from pathlib import Path
import re
import threading
import time
import traceback
from typing import Callable

from mods import context, cq, history, identity, image, llm, log, message, msgs, op, py, storage, text, thread
from mods.capture import capture
from mods.llm import pricing


LOAD_AFTER = ("chatlog", "history", "identity", "image", "llm", "storage")

IMAGE_MODES = ("off", "lazy", "eager")
IMAGE_MODE_ALIASES = {"0": "off", "1": "lazy", "2": "eager"}

AUDIO_MODES = ("off", "lazy", "eager")
AUDIO_MODE_ALIASES = {"0": "off", "1": "lazy", "2": "eager"}

# keep/drop 只改变模型投影，原思考始终留在经历日志。
REASONING_MODES = ("keep", "drop")
REASONING_ALIASES = {"on": "keep", "off": "drop", "1": "keep", "0": "drop"}

REASONING_EFFORTS = ("default", "none", "low", "medium", "high", "max")
REASONING_EFFORT_ALIASES = {"off": "none", "0": "none", "auto": "default", "unset": "default", "reset": "default"}

settings: list = []
prompts: dict = {}
chat_groups: list = []
description_cache: dict = {}
llm_config: dict = {}
# WHY: 两个值的默认写死在这里，不再读 llm_system/config.json；运行期由全局 agent
# storage 覆盖。max_events 是一个窗口画面默认展示的条数（参照 QQ-agent 的 80，存储键
# 沿用旧名）；tidy_token 是整理阈值，tidy.py 里的 `limit`。2026-10-22 以前它是
# max_token × pressure_percent 两个数，维护者觉得一个阈值拆成两个数没有意义，合成一个。
# 2026-10-23 维护者定默认 50 万，约是模型上下文的一半：同一段上下文一直往后接，阈值
# 要给交接本身（整段再加一句交接指令）留出余量，也让"超长卡死"在正常使用里遇不到。
DEFAULT_MAX_EVENTS = 80
DEFAULT_TIDY_TOKEN = 500000
AGENT_WINDOW = ("agent", 0)
NOTICE_TOKENS = 500
_cost_lock = threading.Lock()
# Eager capture is image work reported on the image stream, not chat traffic.
_image_stream = log.stream("image")
# 自言自语走 msg 流：和收发消息的回显共用同一把行租约，见 run。
_self_talk = log.stream("msg")
# hint 求值失败只记日志，所以它有自己的流，不混进聊天流量。
_hint_stream = log.stream("hint")


def normalize_image_mode(value) -> str:
    if value is True:
        return "lazy"
    if value is False or value is None:
        return "off"
    normalized = IMAGE_MODE_ALIASES.get(str(value).lower(), str(value).lower())
    return normalized if normalized in IMAGE_MODES else "off"


def normalize_audio_mode(value) -> str:
    if value is True:
        return "lazy"
    if value is False or value is None:
        return "off"
    normalized = AUDIO_MODE_ALIASES.get(str(value).lower(), str(value).lower())
    return normalized if normalized in AUDIO_MODES else "off"


def agent_setting(name: str, data: dict | None = None):
    key, normalize = AGENT_SETTINGS[name]
    return normalize((storage.get("", "agent") if data is None else data).get(key))


def limit() -> tuple[int, int]:
    """Screen size and tidy threshold."""
    data = storage.get("", "agent")
    if "max_token" in data or "pressure_percent" in data:
        # 旧的两个数是按每次重组上下文定的预算，套到同一段上下文上会太小：直接删掉，用默认。
        data.pop("max_token", None)
        data.pop("pressure_percent", None)
        storage.save()
    return agent_setting("max_events", data), agent_setting("tidy_token", data)


def get_image_mode(data: dict | None = None) -> str:
    return agent_setting("image", data)


def get_audio_mode(data: dict | None = None) -> str:
    return agent_setting("audio", data)


# WHY: 归一化负责把存坏的值拉回默认，读取端永远拿得到合法值。
def normalize_reasoning_mode(value) -> str:
    normalized = REASONING_ALIASES.get(str(value).lower(), str(value).lower())
    return normalized if normalized in REASONING_MODES else "keep"


def get_reasoning_mode(data: dict | None = None) -> str:
    return agent_setting("reasoning", data)


def normalize_reasoning_effort(value) -> str:
    if value is None or value == "":
        return "default"
    normalized = REASONING_EFFORT_ALIASES.get(str(value).lower(), str(value).lower())
    return normalized if normalized in REASONING_EFFORTS else "default"


def get_reasoning_effort(data: dict | None = None) -> str:
    return agent_setting("reasoning_effort", data)


def _bounded_int(minimum: int, fallback: int, maximum: int | None = None):
    """归一化成指定范围内的整数，否则回到默认值。"""

    def normalize(value) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return fallback
        return number if number >= minimum and (maximum is None or number <= maximum) else fallback

    return normalize


# 单值全局 agent 配置：storage 键与归一化。
AGENT_SETTINGS = {
    "image": ("image", normalize_image_mode),
    "audio": ("audio", normalize_audio_mode),
    "reasoning": ("reasoning", normalize_reasoning_mode),
    "reasoning_effort": ("reasoning_effort", normalize_reasoning_effort),
    "max_events": ("max_events", _bounded_int(1, DEFAULT_MAX_EVENTS)),
    "tidy_token": ("tidy_token", _bounded_int(1, DEFAULT_TIDY_TOKEN)),
}

_reflect_stream = log.stream("agent")
_reflect_lock = threading.Lock()
_reflect_pending = False


def reflect_now(handoff: bool = True, wait_seconds: float = 60.0) -> bool:
    """Start the reflection pass right away on its own thread; False when already reflecting or pending.

    WHY: 2026-10-29 修复：当她在轮次中执行 send_command("#reflect keep") 时，AGENT_WINDOW 处于活动状态；
    若直接判 busy 返回 False，她永远无法通过工具发起反思。
    此处改为：如果已有反思在排队或当前正在反思则返回 False；若当前正在进行普通对话轮次，
    先通知停止循环，并在后台线程中等待当前轮次结束释放 AGENT_WINDOW，再无缝接入反思。
    """
    global _reflect_pending
    with _reflect_lock:
        if _reflect_pending:
            return False
        current_turn = running_wake()
        if current_turn is not None and current_turn.get("log", {}).get("kind") == "反思":
            return False
        _reflect_pending = True

    def run() -> None:
        global _reflect_pending
        try:
            _attention.stop_loop("准备反思")
            deadline = time.time() + wait_seconds
            while time.time() < deadline:
                if running_wake() is None:
                    break
                time.sleep(0.5)
            _agent.reflect(handoff=handoff)
        except Exception:
            _reflect_stream.exception("反思失败")
        finally:
            with _reflect_lock:
                _reflect_pending = False

    threading.Thread(target=run, name="chat-reflect", daemon=True).start()
    return True


def get_prompt() -> list:
    """Who she is: the ``self`` note, at the head of the system prompt."""
    text = _notes.read("self").strip() or _migrate_self()
    return [{"role": "system", "content": text}] if text else []


def _migrate_self() -> str:
    """Move the old persona prompt into ``data/notes/self.md`` once.

    WHY: 维护者 2026-10-22 定：旧的提示词／命名设定是按窗口配人设时代留下的，中心化以后
    人设就是她对自己的认识，改成普通文件 self，由她在夜间反思里迭代。只搬当时生效的那份；
    storage 里的 `settings`、`agent.prompt` 和命名设定原样留着不删（`.py` 里仍能用 `prompts` 读到），
    搬过一次就记下，之后维护者删掉 self.md 表示真的不要人设，不会再被搬回来。
    """
    data = storage.get("", "agent")
    if data.get("self_migrated"):
        return ""
    selected = data.get("prompt") or settings
    if isinstance(selected, str):
        selected = prompts.get(selected)
    messages = [message for message in selected if isinstance(message, dict)] if isinstance(selected, list) else []
    parts = [str(message.get("content", "")).strip() for message in messages if message.get("role") == "system"]
    examples = [message for message in messages if message.get("role") != "system"]
    if examples:
        parts.append("## 示例对话\n" + "\n".join(
            ("你" if message.get("role") == "assistant" else "对方") + "：" + str(message.get("content", "")).strip()
            for message in examples))
    text = "\n\n".join(part for part in parts if part)
    if text:
        _notes.write("self", text)
    data["self_migrated"] = True
    storage.save()
    return text


def get_model(data: dict | None = None) -> str:
    data = storage.get("", "agent") if data is None else data
    selection = data.get("model", llm_config.get("default_model", llm.DEFAULT_MODEL))
    try:
        llm.resolve_model(llm_config, selection)
    except ValueError:
        data.pop("model", None)
        selection = llm_config.get("default_model", llm.DEFAULT_MODEL)
    return selection


def count_tokens(value: str) -> int:
    try:
        import tiktoken

        return len(tiktoken.encoding_for_model("gpt-4").encode(value))
    except Exception:
        return max(1, len(value) // 3)


def context_usage(turn=None) -> int:
    """The last actual request size captured by its running wake."""
    wake = running_wake() if turn is None else turn
    return int((wake or {}).get("used", 0))


def usage_name(when: datetime | None = None) -> str:
    """The storage name for one month's usage: ``YYYY-MM``.

    WHY: 键必须带年份。裸月份把每一年的同一个月并进同一个文件，"去年九月"无从
    查起，多年数据还会被加在一起——这是 usage 数字失真的直接来源之一。
    """
    moment = when or datetime.today()
    return f"{moment.year}-{moment.month:02d}"


def _usage_entry() -> list | None:
    """The Bot's ``[calls, cost]`` for the month."""
    user_id = identity.bot_id()
    if user_id is None:
        return None
    usage = storage.get("usage", usage_name())
    return usage.setdefault(str(user_id), [0, 0])


def inc_call_count() -> None:
    entry = _usage_entry()
    if entry is not None:
        entry[0] += 1


def inc_call_cost(model: str, prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0,
                  requested_at: datetime | None = None) -> None:
    """把一次调用的费用记到当前 LLM 主体账目。

    WHY: 单价、缓存命中价和峰谷档位全部来自模型/供应商元数据（见 llm.pricing），这里只
    负责取元数据、算钱、记账三件事。命中缓存的那部分必须单独算——聊天的 prompt 大多是
    重复上下文，一律按未命中价算会把费用高估一个数量级（实测同一段上下文第二次调用，
    845 个 prompt token 里有 640 是命中）。
    """
    _, _, attributes = llm.resolve_model(llm_config, model)
    provider = llm.provider_config(llm_config, model)
    inc_usage_cost(pricing.token_cost(provider, attributes, prompt_tokens, completion_tokens, cached_tokens, requested_at))


def inc_usage_cost(price: float) -> None:
    """Add one externally calculated cost to the current LLM actor's usage."""
    # A storage list is the authority; only this read-modify-write needs a lock.
    with _cost_lock:
        entry = _usage_entry()
        if entry is not None:
            entry[1] += price


def _account_side_call(model: str, prompt_tokens: int, completion_tokens: int, cached_tokens: int) -> None:
    """把图片描述这类旁路调用记进月账；日账由 llm 层在调用处写好。"""
    inc_call_count()
    try:
        inc_call_cost(model, prompt_tokens, completion_tokens, cached_tokens)
    except Exception:  # 模型不在配置里时只算调用次数，记账失败不能打断取图
        pass


llm.on_usage(_account_side_call)


def _window_storage(window: tuple) -> dict:
    """只为把旧 `#hint` 配置搬进文件取本窗口 storage，键就是 `history.window(...)`。

    WHY: 不经过 `context.current()`——hint 在 `chat` 的 `finally` 里跑，那个窗口就是调用方
    手上的实参；由实参决定"哪个窗口"，触发点就不依赖线程局部的当前事件，也不跟捕获、派发
    的细节绑在一起。
    """
    if window == AGENT_WINDOW:
        return storage.get("", "agent")
    kind, key = window
    return storage.get("groups" if kind == "group" else "users", str(key))
_AGENT_HINT_KEY = "agent_hint"


def _agent_hint() -> str:
    """The todo note, shown at the end of every sub-request so edits show up at once.

    WHY: 待办以前存在 storage 的 `agent_hint` 键里，2026-10-22 维护者让它退化成普通文件
    （笔记 `todo`）。第一次读到旧键时搬过去并删掉旧键，之后只认文件。
    WHY: 末尾附上结束标记，避免模型误将后续渲染的动态提示（如其它窗口未读）当成 todo 内容误写进文件。
    """
    data = storage.get("", "agent")
    legacy = data.get(_AGENT_HINT_KEY)
    if isinstance(legacy, str) and legacy.strip() and not _notes.read("todo").strip():
        _notes.write("todo", legacy)
    if _AGENT_HINT_KEY in data:
        del data[_AGENT_HINT_KEY]
        storage.save()
    value = _notes.read("todo").strip()
    return f"待办（笔记 todo，用 write_note 改）：\n{value}\n（以上是笔记 todo 全文）" if value else ""

# 会话主体持久激活的工具模块名，以及各自最后一次被调用的时刻。
_ACTIVE_MODULES_KEY = "active_tools"


def _active_modules() -> dict[str, float]:
    """本会话主体上次装着哪些工具模块、各自最后一次被调用是什么时候。

    WHY: 值是使用时刻，`tools.restore` 靠它决定哪些模块已经空闲太久、
    该在这一轮收掉。旧格式（只存名字的列表）一律当成"就是刚才用过"——那是这份格式之前
    留下的，给它一个完整时限比让它立刻消失更不容易误伤。
    """
    value = storage.get("", "agent").get(_ACTIVE_MODULES_KEY)
    if isinstance(value, dict):
        return {
            name: float(stamp)
            for name, stamp in value.items()
            if isinstance(name, str) and isinstance(stamp, (int, float))
        }
    if isinstance(value, list):
        return {name: time.time() for name in value if isinstance(name, str)}
    return {}


def _persist_modules(stamps: dict[str, float]) -> None:
    """给 工具绑定 的回调：把会话主体的激活集合写回 storage。

    WHY: 主 reader 的激活是**全局主体**状态，不是单轮状态。
    每轮都新建一个 `llm.Chat`，
    激活如果只活在内存里，模型下一轮会照上一轮装载过的名字去调用（操作历史轨道把那几次
    `load_tools` 原样重建进了上下文），而那一轮的快照里没有这个名字——`llm` 解析时
    `mapping[name]` 抛 KeyError，整个调用被丢掉，那一轮连一条 tool 结果都没有就结束了
    （2026-09-17 群里 `browser__open_page` 那次）。所以要写在这里：模型改一次，这一
    份就更新一次，下一轮开局原样装回去。

    WHY: 空字典就删键，不留 `{}`。storage 里没有这个键就是"没激活过"，和空字典是一回事，
    少一个需要解释的状态。

    WHY: 值是使用时刻而不是只有名字，见 `_active_modules`；空闲回收在 `tools` 那层判，
    这里只负责如实来回搬。
    """
    data = storage.get("", "agent")
    if stamps:
        data[_ACTIVE_MODULES_KEY] = {name: float(stamp) for name, stamp in stamps.items()}
    else:
        data.pop(_ACTIVE_MODULES_KEY, None)
    storage.save()


def _hint_effective(default: dict, chat_hint: dict | None) -> dict:
    """生效配置：`{**default, **chat_hint}`——窗口的覆盖默认的，只读 `code`/`on`。

    WHY: 就这一句合并，没有别的间接层。窗口只写 `on` 也是合法配置——那样它仍继承默认的
    `code`，只是把自己单独关掉；`on` 缺省当作 False，所以只写了 `code` 的配置不会生效。
    """
    return {**default, **(chat_hint if isinstance(chat_hint, dict) else {})}


_HINT_ROOT = Path("data") / "hint"


def _hint_path(window: tuple | None) -> Path:
    if window is None:
        return _HINT_ROOT / "default.py"
    return _HINT_ROOT / f"{'g' if window[0] == 'group' else 'u'}{window[1]}.py"


def hint_config(window: tuple | None) -> dict:
    """One `#hint` file as the `{code?, on?}` dict the merge rule expects.

    WHY: 2026-10-22 维护者让 `#hint` 退化成普通文件：`data/hint/default.py` 是全局默认，
    `data/hint/g<群号>.py`／`u<QQ号>.py` 是窗口覆盖。首行 `# on` 或 `# off` 是开关，其余
    是代码；只写开关、不写代码的窗口文件照旧继承默认的代码。以前存在 storage 的配置在
    第一次读到时搬进文件并删掉旧键。
    """
    path = _hint_path(window)
    home = storage.get("", "hint") if window is None else _window_storage(window)
    legacy = home if window is None else home.get("hint")
    if isinstance(legacy, dict) and legacy:
        if not path.is_file():
            save_hint(window, legacy)
        if window is None:
            home.clear()
        else:
            del home["hint"]
        storage.save()
    if not path.is_file():
        return {}
    head, _, rest = path.read_text(encoding="utf-8").partition("\n")
    config: dict = {}
    if head.strip() in ("# on", "# off"):
        config["on"] = head.strip() == "# on"
        code = rest
    else:
        code = head + ("\n" + rest if rest else "")
    if code.strip():
        config["code"] = code.strip("\n")
    return config


def save_hint(window: tuple | None, config: dict) -> None:
    path = _hint_path(window)
    lines = []
    if "on" in config:
        lines.append("# on" if config["on"] else "# off")
    if isinstance(config.get("code"), str) and config["code"].strip():
        lines.append(config["code"].strip("\n"))
    if not lines:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_hint(window: tuple, turn=None) -> None:
    """求值本窗口的结束提示，并把结果发出去；触发点写在 `chat` 的 `finally`。

    WHY: 唯一信号是"循环停下"：`while` 里每个 `return` 和异常都经过 `finally`，而每轮
    `_run_agent` 返回时不经过，所以不会每句都刷；`if not owner:` 的早退和没有来源窗口的
    恢复轮也不发送窗口结束提示，于是"真的结束"只算一次、也只由这一轮的持有者来做。

    WHY: 求值与发送的任何异常都吞掉、只写日志，绝不抛回 `finally`——这段代码是用户自己
    写的、每次聊天都自动跑，让它抛出去就等于一段烂代码能污染聊天主流程的返回路径。
    """
    try:
        merged = _hint_effective(hint_config(None), hint_config(window))
        code = merged.get("code")
        if not merged.get("on", False) or not isinstance(code, str) or not code.strip():
            return
        result = _hint_evaluate(code, window, turn)
        if result is not None:
            # `#` 前缀让结束提示不回流进 LLM 上下文，见 chat.screen.records。
            message.sendmsg("#" + cq.escape(str(result)))
    except Exception:
        _report_hint_failure()


def _hint_evaluate(code: str, window: tuple, turn=None):
    """在 `py.loc` 的一份私用副本里跑一次 *code*，返回末行的值。

    WHY: 名字要照旧认（`sendmsg`/`storage`/…都在），痕迹不能留——副本 + 单次求值就够了：
    `window`/`usage`/`peak`/`limit` 是这一次临时的，代码里的赋值也只落进副本，`py.loc` 一个键都不会多。
    仍走 `py.eval_last`，于是「末行是表达式才发」和 Traceback 指回作者那几行都不变。
    """
    namespace = dict(py.loc)
    namespace["window"] = window
    namespace["usage"] = context_usage(turn)
    namespace["peak"] = (turn or {}).get("log", {}).get("peak", 0)
    namespace["limit"] = limit()[1]
    return py.eval_last(code, namespace)


def _report_hint_failure() -> None:
    """照 link._report_error 的惯例，把 traceback 用 `#` 前缀发出去。"""
    _hint_stream.exception("hint 执行失败")
    try:
        # `#` 前缀让 traceback 不回流进 LLM 上下文，见 chat.screen.records。
        message.sendmsg("#" + "".join(traceback.format_exc().splitlines(True)[3:]).strip())
    except Exception:
        _hint_stream.exception("hint 的错误报告也发不出去")


def chat(model: str | None = None) -> None:
    event = context.current() or {}
    if history.window(event) is not None:
        prior = event.get("_chat_model")
        if model:
            event["_chat_model"] = model
        try:
            wake(event, "wake")
        finally:
            if prior is None:
                event.pop("_chat_model", None)
            else:
                event["_chat_model"] = prior


def _in_chat_scope(event: dict) -> bool:
    """这个窗口开了聊天吗——群要在白名单里，私聊一律算开。"""
    group_id = event.get("group_id")
    return group_id is None or group_id in chat_groups


def _mail_candidate(event: dict) -> bool:
    """Whether history may project this event into an enabled chat window."""
    if not _in_chat_scope(event):
        return False
    if msgs.is_msg(event):
        return True
    if msgs.is_recall(event):
        return True
    return _view._is_context_poke(event, event.get("group_id") is not None)


def record_event(event: dict, write: Callable[[], object]) -> object:
    """Write chat history and register the arrival under one window lock."""
    window = history.window(event)
    if window is None and msgs.is_friend_recall(event) and event.get("user_id") is not None:
        window = ("private", event["user_id"])
    if window is None or not _mail_candidate(event):
        return write()
    with context.window_lock(window):
        result = write()
        if result is None:
            context.remember_arrival(event, None)
            return result
        try:
            # Registration is history, not dispatch: commands and links decide
            # before capture_chat is allowed to claim a summon.
            arrival = _screen.register(window, event, kind="")
        except Exception:
            # WHY: unread state failure disables agent mail, not .py/.reboot;
            # explicit maintenance routes run after chat recording.
            _reflect_stream.exception("登记未读失败，仍继续明确命令路由")
            arrival = None
        context.remember_arrival(event, arrival)
        return result


def _addressed(event: dict, value: str) -> bool:
    r"""这条消息是冲着 Bot 说的吗：at、以她的某个名字开头且后面跟逗号或空白、或者 `柚子，`。

    WHY: 名字的认法和 `chat.capture_addressed_fallback` 一样（`{名字}[,，\s]+`）。以前这里只认
    全角逗号，「柚子,在吗」「柚子 在吗」从 capture_chat 漏过去，落到那个兜底里直接调 `chat()`，
    照样叫醒她，却绕开了覆盖层，mute 压不住（2026-10-23 临时脚本试出来的）。
    """
    names = "|".join(re.escape(name) for name in identity.names or (identity.bot_name(),))
    return (_view.has_at(identity.bot_id())(event)
            or value.startswith("柚子，")
            or re.match(rf"(?:{names})[,，\s]", cq.unescape(value)) is not None)


def activation_signal(event: dict) -> bool:
    """这条事件该不该把柚子叫醒——**只问这一件事**，不回答「它进不进上下文」。

    WHY: 这是「红点」那一位，从 `cond` 里摘出来的。`cond` 一直在同时回答两个问题：
    「这条要不要激活一轮」和「这条是不是一条就地执行的 `#` 子命令」，靠返回值的类型
    （bool 还是 callable）区分。两个问题的答案本来就不该共用一个出口——子命令那一支
    既不激活、也不进上下文，它和聊天循环唯一的关系就是「不要碰它」。
    见 docs/working/proposals/mail-and-activation.md 3.0。

    WHY: 四个判据一个不少，顺序也照搬：at／名字开头优先于 `#`，所以 `@Bot #help`
    是激活而不是子命令；`#poke` 是唯一一个长得像子命令的激活信号；最后那行的戳一戳
    判据留在**函数末尾**而不是提前 `return False`，因为今天 `#` 未知子命令那条路就是
    落到它上面的——提前返回要先证明「一个事件不可能同时 is_msg 和 is_poke」，
    而那条证明现在没人做过。
    """
    if not _in_chat_scope(event):
        return False
    if msgs.is_msg(event):
        value = msgs.body(event)
        if _addressed(event, value):
            return True
        if value.startswith("#"):
            if value == "#poke":
                return True
            if value[1:].strip().partition(" ")[0] in _subcommands._SUBCOMMAND_NAMES:
                # 子命令那一支：就地执行，不激活。谁来执行见 _subcommand_call。
                return False
        if _private_from_peer(event):
            return True
    return msgs.is_poke(event) and event.get("target_id") == identity.bot_id()


def _private_from_peer(event: dict) -> bool:
    """对方在私聊里发来的消息；她自己发出去的那条回显不算。

    WHY: 维护者 2026-10-23：私聊每次都要先叫「柚子，」太累，私聊每条都叫醒她。话没说完
    就回的问题不靠延迟解决（人可能隔几分钟才想到下一句），靠她的沉默纪律和干活时
    插进这一段的新消息，见 view._base_prompt 与 agent._prepare。
    """
    return event.get("group_id") is None and event.get("user_id") != identity.bot_id()


def _subcommand_call(event: dict) -> Callable | None:
    """`#` 子命令那一支：返回就地执行它的那个闭包，不是子命令就返回 None。

    WHY: 和 `activation_signal` 是**互斥**的两支，合起来正好是老 `cond` 的全部返回值。
    判据的先后必须与那边一致，否则 `@Bot #help` 会同时被两边认领。
    """
    if not _in_chat_scope(event) or not msgs.is_msg(event):
        return None
    value = msgs.body(event)
    if _addressed(event, value) or not value.startswith("#") or value == "#poke":
        return None
    subcommand = value[1:].strip().partition(" ")[0]
    if subcommand not in _subcommands._SUBCOMMAND_NAMES:
        # WHY: 她自己用 op.send_command 敲错命令时不能静默——`#` 开头的输出默认不回她的
        # 上下文（见 call），失败就这样消失了。普通群友仍不回，免得刷屏。
        if event.get("user_id") == identity.bot_id() or op.is_op(event):
            return lambda name=subcommand: f"没有 #{name} 这个命令，用 #help 看全部"
        return None
    if _subcommands.requires_op(value[1:]) and not op.require_op(
            event, pattern=r"^#\s*(hint|use_model|prompt|image|reasoning|limit|reflect|awaker)"):
        # WHY: 全局设置不可由普通群友改写；只读子命令保持公开。
        return None
    return lambda value=value: _subcommands._subcommand(value[1:])


def call(data: Callable | bool):
    if callable(data):
        text = str(data())
        if (context.current() or {}).get("user_id") == identity.bot_id():
            # WHY: 这条是她自己发的命令（op.send_command），结果要回到她的上下文里；加 `#`
            # 会被当成命令回显挡掉，命令失败时她永远看不见原因。
            return cq.escape(text)
        # `#` 前缀让子命令的输出不回流进 LLM 上下文，见 chat.screen.records。
        return "#" + cq.escape(text)
    return chat()


@capture(before="chatstart")
def capture_chat(event: dict) -> bool:
    # WHY: 两个问题分两次问，不再靠一个返回值的类型来区分。顺序是承重的：子命令先问，
    # 因为它就地执行、既不激活也不进上下文；剩下的才轮到「红点亮不亮」。
    if msgs.is_msg(event) and msgs.body(event).rstrip() in ("^C", "^c"):
        return True
    handler = _subcommand_call(event)
    if handler is not None:
        # `#` 子命令不调模型也不进上下文，跟插话无关，照旧就地执行。
        result = call(handler)
        if result is not None:
            message.sendmsg(result)
        return True
    window = history.window(event)
    if window is None or not _mail_candidate(event):
        return False
    with context.window_lock(window):
        if context.event_arrival(event) is None:
            return activation_signal(event)
        notified = _attention.on_record(window, event)
    # WHY: a muted addressed message must still be swallowed here, or the
    # addressed fallback below would bypass its cover and wake her anyway.
    return notified or activation_signal(event)


def wake(event: dict | None = None, kind: str = "wake", reason: str = "") -> bool:
    """Queue an explicit wake; event wakes require its current route registration.

    WHY: links and .py may explicitly wake her after their own decision. Eventless
    wakes leave their reason in me, like due rules and each actual loop round.
    """
    if event is None:
        push(_windows.ME, kind, reason or kind, wake=kind)
        return True
    window = history.window(event)
    if window is None or not _mail_candidate(event):
        return False
    with context.window_lock(window):
        arrival = context.event_arrival(event)
        if arrival is None:
            return False
        pending = _screen.unread(window).get(arrival)
        if pending is None or pending.get("kind"):
            return False
        return _attention.on_record(window, dict(event, _wake_kind=kind, _wake_reason=reason))


def push(window: tuple, author: str, text: str, *, wake: str = "", record_id: str = "") -> str:
    """Put one record into a non-QQ window and, when *wake* names a reason, wake her; returns the record id.

    *window* is ``(种类, 目标)`` of a kind registered with `kind`, or ``me``. The record is
    written to chatlog first and then registered as unread, under the window lock,
    the same order QQ messages use; so it shows on her screen, in ``read_log`` and in
    ``search``, stays unread until she has seen it, and survives a restart. *wake* is
    what she reads in "这次唤醒你的是". When she is busy it waits for this wake to end.
    *record_id* is the source's own id for the record (a comment's rpid), so she can name
    it the way she names a QQ message_id; left out, an 8-digit hex id is made up.

    WHY: 维护者 2026-10-23：别的来源也要带着内容叫醒她、能被她回，不另造一套"源"。QQ 消息
    不走这里，它们的内容由 NapCat 事件决定，撤回、补漏都在 chatlog 那条路上。
    """
    window = tuple(window)
    if not _windows.known(window) or window[0] in _windows.QQ or _windows.parse_all([_windows.label(window)]) != [window]:
        raise ValueError(f"push 只收已登记的非 QQ 窗口，目标是非空字符串：{window}")
    body = str(text).strip("\n")
    if not body.strip():
        raise ValueError("push 的正文不能空")
    from uuid import uuid4

    from mods import chatlog

    now = time.time()
    record_id = " ".join(str(record_id).split()) or uuid4().hex[:8]
    with context.window_lock(window):
        chatlog.append_record(window[0], window[1], author, body, now, record_id)
        event = {"post_type": "push", "time": int(now), "author": str(author),
                 "text": body, "id": record_id, "_default_wake": wake}
        _screen.register(window, event)
        # WHY: attention only signals a reader thread. push returns when the
        # record is safe; model failures cannot make a producer redeliver it.
        _attention.on_record(window, event)
    return record_id


@capture(before="name加复读")
def capture_addressed_fallback(event: dict) -> bool:
    """Preserve the old addressed fallback outside chat-enabled groups."""
    if not msgs.is_msg(event):
        return False
    captures = text.stc_get(r"{:identity.names}[,，\s]+{Text}")(
        cq.unescape(msgs.body(event)),
        {"identity": identity},
    )
    if captures is None:
        return False
    group_id = event.get("group_id")
    if group_id is None or group_id in chat_groups:
        chat()
        return True
    value = captures["Text"].rstrip()
    value = value.rstrip("？").rstrip("?").rstrip("吗")
    value = value.replace("你", identity.bot_name()).replace("我", "你") + "！"
    message.sendmsg(value)
    return True


def _message_image_urls(event: dict) -> list[str]:
    return [part["image_url"]["url"] for part in _view.msg_split(event.get("message", "")) if part.get("type") == "image_url"]


@thread.to_thread(None)
def _eager_cache_images(event: dict, model: str) -> None:
    capabilities = llm.get_client().get_model_capabilities(model)
    vision_model = llm.get_client().get_vision_model()
    for uri in _message_image_urls(event):
        try:
            if capabilities.vision or not vision_model:
                image.image_uri_to_data_uri(uri)
            else:
                llm.get_client()._get_image_description(uri, vision_model, description_cache)
        except Exception as error:
            _image_stream.info(f"❌ eager 图片捕获失败：{error}")


def eager_cache_images(event: dict) -> None:
    if msgs.is_msg(event) and "[CQ:image" in event.get("message", ""):
        data = storage.get("", "agent")
        if get_image_mode(data) == "eager":
            model = get_model(data)
            count = len(_message_image_urls(event))
            _image_stream.info(f"🖼️ eager 图片捕获：{count} 张，目标模型 {model}")
            _eager_cache_images(event.copy(), model)


def recognize_images(event: dict) -> None:
    """到达时把柜里已有的表情认出来，结论钉死；只读 CQ 的 file 字段，不下载、不调模型。

    WHY: 三档都做。这一级不碰图片字节，`#image off` 也能认出原样重发的图，不违背那一档
    “不在任何群里到处下载图片”的约定；第二、三级才按 eager/lazy/off 分开。
    """
    message = event.get("message")
    if not isinstance(message, str) or "[CQ:image" not in message:
        return
    window = history.window(event)
    if window is None:
        return
    try:
        _recognize.pin(window, event.get("message_id"), message)
    except Exception:
        _image_stream.exception("再认柜中表情失败")


def process_incoming_voice(event: dict) -> None:
    if msgs.is_msg(event) and "[CQ:record" in event.get("message", ""):
        data = storage.get("", "agent")
        mode = get_audio_mode(data)
        if mode != "off":
            _voice.cache_event_voice(event, eager=(mode == "eager"))


def migration_problem() -> str:
    """Existence/manifest check only; old runtime files are never a read fallback."""
    manifest = Path("data/migrations/chat-core-v1/manifest.json")
    if manifest.exists():
        import json
        try:
            complete = json.loads(manifest.read_text(encoding="utf-8")).get("status") == "complete"
        except (OSError, ValueError, AttributeError):
            complete = False
        if not complete:
            return "chat core 迁移未完成；停机后运行 scripts/once/migrate_chat_core.py --runtime-root ABS --apply --rollback 恢复，再检查并迁移"
    old = any(Path(name).exists() for name in ("data/context", "data/event_stream"))
    if old and not (_segment.ROOT / "view.json").exists():
        return "旧经历数据尚未迁移；停机后对离线 runtime root 运行 scripts/once/migrate_chat_core.py --runtime-root ABS，先 dry-run 再 --apply"
    return ""


def on_load(ctx) -> None:
    global settings, prompts, chat_groups, description_cache, llm_config
    from mods import is_available

    missing = [name for name in ("chatlog", "identity", "image", "llm", "storage")
               if not is_available(name)]
    if missing:
        raise RuntimeError("chat requires available mods: " + ", ".join(missing))

    problem = migration_problem()
    if problem:
        raise RuntimeError(problem)

    settings = storage.get("", "settings", list)
    prompts = storage.get("llm_system", "prompts")
    chat_groups = storage.get("", "chat_groups", list)
    description_cache = storage.get("llm_system", "description_cache")
    llm_config = llm.get_client().config

    def resume_recovery() -> None:
        from mods import _backfill, connect, wait_booted

        if wait_booted(120):
            _backfill.start(connect.call_api)

    threading.Thread(target=resume_recovery, name="napcat-backfill-start", daemon=True).start()

    def resume_attention() -> None:
        from mods import wait_booted

        if wait_booted(120):
            try:
                _attention.start(_agent.notify)
                _attention.rescan()
            except Exception:
                _reflect_stream.exception("恢复注意力失败")

    threading.Thread(target=resume_attention, name="chat-attention-start", daemon=True).start()


def cancel_debounce(window: tuple) -> bool:
    return bool(_attention.cancel("debounce", window=window))


def running_wake() -> dict | None:
    return _agent.running_wake()


def cancel_wake(window: tuple) -> bool:
    return _agent.cancel_wake(window)


def on_exit() -> None:
    _attention.stop()



from . import recognize as _recognize, view as _view, segment as _segment, screen as _screen, agent as _agent, roles as _roles, subcommands as _subcommands, notes as _notes, awaker as _awaker, attention as _attention, observer as _observer, voice as _voice, face as _face
from .view import has_at, msg_split
from .windows import kind, parse_target
from .run import run, current_wake
from . import windows as _windows
