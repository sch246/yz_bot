from __future__ import annotations

import ast
from datetime import datetime, timezone
import re

from mods import context, cq, history, llm, storage, text
from mods.llm import pricing

import mods.chat as _chat_root


_SUBCOMMAND_HELP = (
    ("help [name]", "显示子命令目录或某条子命令的完整说明。\n格式：#help | #help <名称>"),
    ("model", "查看当前模型"),
    ("model <selection>", "查看指定模型信息"),
    ("models", "列出当前供应商的模型（优先在线列表）"),
    ("use_model [selection]", "设置或重置当前模型"),
    ("prompt", "查看当前提示词"),
    ("add_prompt <list>", "将显式给定的消息列表追加到全局提示词（管理员）"),
    ("setting [name]", "列出或查看设定"),
    ("use_setting [name]", "应用或重置设定"),
    ("set_setting <name> [list]", "保存当前或给定设定"),
    ("del_setting <name>", "删除设定"),
    ("image [off|lazy|eager]", "查看或设置图片读取档位"),
    ("reasoning [keep|drop]", "查看或设置中心已读输出是否原生带回思考内容"),
    ("limit [<画面条数> <token> [整理百分比]|reset]", """查看或设置中心 agent 的窗口画面条数、单次唤醒上下文上限与整理阈值（管理员）。

格式：#limit | #limit <画面条数> <token> [整理百分比] | #limit reset
每次唤醒都重新组装上下文：画面条数是一个窗口默认展示多少条聊天记录；一次唤醒里上下文长到 token×整理百分比、或上次整理以来的行动列表长到它的三分之一时，先让图书管理员把她做过的事收进笔记，再重新组装继续。默认值分别为 80、40000、75%。
#limit                  显示三个值，并标出来自全局覆盖还是默认
#limit <画面条数> <token> [整理百分比] 写入全局设置；省略百分比则保留原设置
#limit reset            清掉全局设置，回落到默认"""),
    ("reflect [now]", """查看上次反思时间，或现在就跑一次夜间整理与反思（管理员）。

夜间整理：图书管理员把这一天写成日记（data/notes/diary/），整理笔记；月末、年末各汇总一次。反思：她读上次反思以来的记忆系统日志（data/journal/），评估自己之前的策略改动，必要时改给图书管理员的附加说明或自己的做法笔记；不对外说话。什么时候自动跑由 awaker 规则决定，默认 daily 04:00 reflect。
#reflect          显示上次反思时间
#reflect now      现在就跑一次（中心 agent 正忙时不执行）"""),
    ("awaker [set <规则>]", """查看或改写她的闹钟规则（改写要管理员）。

规则就是笔记 awaker（data/notes/awaker.md），她自己也能改，每行一条：
every 10m <理由>             每隔一段时间叫醒她一次（最短 1m）
daily 23:30 <理由>           每天这个时刻
at 2026-10-23 08:00 <理由>   某个时刻一次
理由写 reflect 表示夜间整理与反思；其它理由原样交给她。文件为空时只有 daily 04:00 reflect。
#awaker              显示规则、上次响的时间和不认识的行
#awaker set <规则>   整份替换；set 之后第一个换行起是规则全文"""),
    ("hint [get|set|default]", """查看、编写或开关本窗口的结束提示（管理员）。

格式：#hint | #hint get | #hint set <代码> | #hint set | #hint default [get|set <代码>]
本窗口的配置在 chat storage 的 hint 键，全局默认在 storage 的 "" 命名空间；生效的是两者按 {**默认, **窗口} 合并之后 code 非空、on 为真的那份。
聊天循环停下时求值一次，非 None 的结果作为一条消息发出（自带 # 前缀，不进模型上下文）。
求值环境是共享动态环境，另外注入 window（触发窗口）与 usage（中心 reader 最近一次子请求实际收到的已编号事件文本 token 估算；不含系统提示与工具 schema）。
#hint              切换本窗口的开关（只写本窗口）
#hint get          显示合并后生效的代码与开关，并标出代码来自哪里
#hint set <代码>   写入本窗口的代码并打开开关；set 之后第一个换行起即为源码
#hint set          清掉本窗口配置，回落到全局默认
#hint default      切换全局默认的开关
#hint default get  显示全局默认的代码与开关
#hint default set <代码>  写入全局默认的代码并打开开关"""),
)
_SUBCOMMAND_NAMES = {pattern.partition(" ")[0] for pattern, _description in _SUBCOMMAND_HELP}


def requires_op(value: str) -> bool:
    name, _, tail = value.strip().partition(" ")
    return (name == "hint" or name in {"use_model", "add_prompt", "use_setting", "set_setting",
                                       "del_setting"}
            or name in {"image", "reasoning", "limit", "reflect", "awaker"} and bool(tail.strip()))


def _subcommand_help(name: str = "") -> str:
    if not name:
        # WHY: 每行自带 `#`，用户可以直接照抄；`call()` 又会给整条消息补一个 `#`，所以
        # 把生成的第一个字符空出来，免得渲染成 `##help`。
        lines = [
            f"#{pattern} — {description.splitlines()[0]}"
            for pattern, description in _SUBCOMMAND_HELP
        ]
        lines[0] = lines[0][1:]
        return "\n".join(lines)
    matched = [
        description
        for pattern, description in _SUBCOMMAND_HELP
        if pattern.partition(" ")[0] == name
    ]
    if not matched:
        return "该命令不存在！"
    return "\n".join(matched)


_MODEL_TABLE_HEADER = "模型 输入(未命中/命中) 输出 (单位: 元/(1m token)，当前价) 视觉识别 函数调用"


def _format_model(selection: str, attributes: dict, when: datetime | None = None) -> str:
    if any(key in attributes for key in pricing.PRICE_KEYS):
        provider = llm.provider_config(_chat_root.llm_config, selection)
        prices = pricing.format_prices(pricing.unit_prices(provider, attributes, when))
    else:
        # 本地没有这条模型的元数据，不替对端猜价格（见 UNKNOWN_MODEL_CAPABILITIES）。
        prices = " / ".join("-" for _ in pricing.PRICE_KEYS)
    return f"{selection}\n    {prices} {'👀' if attributes.get('vision') else ''} {'⚙️' if attributes.get('function_calling') else ''}"


def _models_report(data: dict) -> str:
    """当前 provider 的模型列表：先问对端，取不到就用本地配置。

    WHY: 清单的权威在对端，本地 models 只是价格与能力元数据。对端列出而本地没有元数据的
    行只显示名字（价格为 ``-``、无能力标记），不替对端猜能力。
    """
    provider = llm.resolve_model(_chat_root.llm_config, _chat_root.get_model(data))[0]
    local = _chat_root.llm_config.get("providers", {}).get(provider, {}).get("models", {})
    online = llm.get_client().list_models(provider)
    if online is None:
        names = list(local)
        note = f"（未取到 {provider} 的在线模型列表，以上为本地配置）"
    else:
        names = list(online) + [name for name in local if name not in online]
        note = f"（{provider} 的在线模型列表；本地没有元数据的行只显示名字）"
    priced_at = datetime.now(timezone.utc)
    rows = [_MODEL_TABLE_HEADER]
    for name in names:
        selection = f"{provider}/{name}"
        attributes = local.get(name) or {}
        rows.append(_format_model(selection, attributes, priced_at))
    rows.append(note)
    return "\n".join(rows)


def _first_argument(value: str) -> tuple[str, str]:
    if not value.strip():
        return "", ""
    return text.read_params(" " + value.strip(), read_str=True)


def _list_argument(value: str) -> list:
    parsed = ast.literal_eval(value)
    if not isinstance(parsed, list):
        raise ValueError("参数必须是 list")
    return parsed


def _after_tokens(line: str, count: int) -> str:
    """*line* 里前 *count* 个以空白分隔的 token 之后的内容。"""
    position = 0
    for _ in range(count):
        match = re.search(r"\S+", line[position:])
        if match is None:
            return ""
        position += match.end()
    return line[position:].lstrip(" \t")


def _hint_request(raw: str) -> tuple[str, str]:
    """把一次 `#hint` 调用切成 `(动词, 源码)`；动词认不出时给 `"?"`。

    WHY: 源码要整段原样取，所以不能先 strip 再切——那会吃掉作者写的缩进。切分只有一条规则：
    认动词只看第一行（`default` 后面再看一个词），认完把动词那几个 token 去掉，剩下的整段
    就是源码、首尾各 strip 一次。行内换行照旧保留，于是「同一行写 `set x`」「只换行再写」
    「两处都写」三种写法都不丢内容。（`_after_tokens` 按空白取词、不跨行，所以它天然按整段工作。）
    """
    body = raw.lstrip()[len("hint"):].lstrip()
    words = body.split("\n", 1)[0].split()
    if not words:
        return "", ""
    if words[0] == "default":
        if len(words) > 1 and words[1] in ("get", "set"):
            verb, taken = f"default {words[1]}", 2
        else:
            verb, taken = "default", 1
    elif words[0] in ("get", "set"):
        verb, taken = words[0], 1
    else:
        return "?", ""
    return verb, _after_tokens(body, taken).strip()


def _hint_origin(chat_hint: dict | None) -> str:
    """合并后生效的那个 `code` 是从窗口来的，还是从默认来的。"""
    return "本窗口" if isinstance(chat_hint, dict) and "code" in chat_hint else "默认"


def _hint_report(config: dict, origin: str) -> str:
    """`#hint get` 要看的两样：开关，加上那段代码和它的来处。"""
    state = "on" if config.get("on", False) else "off"
    code = config.get("code")
    if not isinstance(code, str) or not code:
        return f"hint: {state}\ncode（{origin}）: （空）"
    return f"hint: {state}\ncode（{origin}）:\n{code}"


def _hint_subcommand(raw: str) -> str:
    """处理一次 `#hint`；op 判权在 `cond`，不在这里。

    WHY: 命令面只管文本——写、看、开关，和 `#prompt` 一系；"聊天循环停下时自动求值"是
    `_run_hint` 那一半，不混进命令语义里。因此没有 del：源码是劳动成果，不用了就
    `#hint set` 回落默认、或把开关切到关。
    WHY: 改完立刻 `storage.save()`，不等后台扫描——hint 是用户手写的配置，紧接着一次重启
    就该还在（cave、link 也是这么落盘的）。
    """
    event = context.current() or {}
    window = history.window(event)
    if window is None:
        return "当前没有聊天窗口"
    data = _chat_root._window_storage(window)
    default = storage.get("", "hint")
    chat_hint = data.get("hint")
    verb, source = _hint_request(raw)
    if verb in ("get", "default", "default get") and source.strip():
        return f"hint {verb} 参数过多"
    if verb == "set":
        if source.strip():
            data["hint"] = {"code": cq.unescape(source), "on": True}
            storage.save()
            return "提示已开启"
        data.pop("hint", None)
        storage.save()
        return "已设为默认"
    if verb == "default set":
        if not source.strip():
            return "hint default set 需要代码"
        default["code"] = cq.unescape(source)
        default["on"] = True
        storage.save()
        return "默认已开启"
    if verb == "get":
        return _hint_report(_chat_root._hint_effective(default, chat_hint), _hint_origin(chat_hint))
    if verb == "default get":
        return _hint_report(default, "默认")
    if verb == "default":
        default["on"] = not bool(default.get("on", False))
        storage.save()
        return "默认已开启" if default["on"] else "默认已关闭"
    if verb == "":
        if not isinstance(chat_hint, dict):
            chat_hint = {}
            data["hint"] = chat_hint
        chat_hint["on"] = not bool(chat_hint.get("on", False))
        storage.save()
        return "提示已开启" if chat_hint["on"] else "提示已关闭"
    return "hint 参数错误，可用 #help hint 查看"


def _limit_report() -> str:
    """`#limit` 查看中心 agent 的全局上限与提醒阈值。"""
    data = storage.get("", "agent")
    lines = []
    for name in ("max_events", "max_token", "pressure_percent"):
        key, normalize = _chat_root.AGENT_SETTINGS[name]
        origin = "全局" if key in data else "默认"
        value = normalize(data.get(key))
        lines.append(f"{name}: {value}（{origin}）")
    return "\n".join(lines)


def _limit_set(tail: str) -> str:
    """`#limit <画面条数> <token> [整理百分比]` 写全局设置；`reset` 回落默认。

    WHY: 画面条数与 token 上限仍一起写，避免只改其中一个造成难解释的半份配置。整理百分比
    可选，不写就保留原设置；老的两参数命令因此不会意外重置它。
    """
    data = storage.get("", "agent")
    if tail.strip() == "reset":
        for name in ("max_events", "max_token", "pressure_percent"):
            data.pop(_chat_root.AGENT_SETTINGS[name][0], None)
        storage.save()
        return "已重置中心 agent 上限，回落到默认"
    parts = tail.split()
    if len(parts) not in (2, 3):
        return "limit 参数错误，可用 #help limit 查看"
    written = []
    for name, raw_value in zip(("max_events", "max_token", "pressure_percent"), parts):
        try:
            number = int(raw_value)
        except ValueError:
            return "limit 参数错误，可用 #help limit 查看"
        if number < 1 or name == "pressure_percent" and number > 100:
            return "limit 参数错误，可用 #help limit 查看"
        written.append((name, number))
    for name, number in written:
        data[_chat_root.AGENT_SETTINGS[name][0]] = number
    storage.save()
    return "\n".join(f"{name}: {number}" for name, number in written)


def _reflect_subcommand(tail: str) -> str:
    if not tail:
        last = storage.get("", "agent").get("reflected_at")
        shown = datetime.fromtimestamp(last).strftime("%Y-%m-%d %H:%M") if isinstance(last, (int, float)) else "还没有"
        return f"上次反思：{shown}；什么时候自动跑见 #awaker"
    if tail == "now":
        return "开始夜间整理与反思，记录写在 data/journal/" if _chat_root.reflect_now() else "中心 agent 正忙，稍后再试"
    return "reflect 参数错误，可用 #help reflect 查看"


def _awaker_subcommand(raw: str) -> str:
    body = raw.lstrip()[len("awaker"):]
    head, _, rest = body.partition("\n")
    if not head.strip() and not rest.strip():
        return _chat_root._awaker.report()
    if head.strip().partition(" ")[0] != "set":
        return "awaker 参数错误，可用 #help awaker 查看"
    text = cq.unescape((head.strip()[3:].strip() + "\n" + rest).strip())
    from . import journal

    previous = _chat_root._notes.write("awaker", text)
    journal.append("维护者改 awaker", journal.diff(previous, text))
    return _chat_root._awaker.report()


def _subcommand(value: str):
    # WHY: hint 的源码要求原样取（含缩进与换行），所以先留一份没 strip 的原文。
    raw = value
    value = value.strip()
    name, _, tail = value.partition(" ")
    tail = tail.strip()
    data = storage.get("", "agent")
    if name == "help" and not tail:
        return _subcommand_help()
    if name == "help" and tail:
        argument, remaining = _first_argument(tail)
        if remaining.strip():
            return "help 参数过多"
        return _subcommand_help(argument)
    if name == "model" and not tail:
        return _chat_root.get_model(data)
    if name == "model" and tail:
        selection, remaining = _first_argument(tail)
        if remaining.strip():
            return "model 参数过多"
        try:
            _provider, _api_model, attributes = llm.resolve_model(_chat_root.llm_config, selection)
        except ValueError as error:
            return str(error)
        return "\n".join((_MODEL_TABLE_HEADER, _format_model(selection, attributes)))
    if name == "models" and not tail:
        return _models_report(data)
    if name == "use_model" and tail:
        selection, remaining = _first_argument(tail)
        if remaining.strip():
            return "use_model 参数过多"
        try:
            llm.resolve_model(_chat_root.llm_config, selection)
        except ValueError as error:
            return str(error)
        data["model"] = selection
        storage.save()
        return f"模型设置为 {selection}"
    if name == "use_model" and not tail:
        data.pop("model", None)
        storage.save()
        return "已重置模型"
    if name == "image" and not tail:
        return f"image: {_chat_root.get_image_mode(data)}"
    if name == "image" and tail:
        mode, remaining = _first_argument(tail)
        if remaining.strip():
            return "image 参数过多"
        mode = _chat_root.IMAGE_MODE_ALIASES.get(mode.lower(), mode.lower())
        if mode not in _chat_root.IMAGE_MODES:
            return "图片读取档位必须是 off/0、lazy/1 或 eager/2"
        data["image"] = mode
        storage.save()
        return f"image: {mode}"
    if name == "reasoning" and not tail:
        return f"reasoning: {_chat_root.get_reasoning_mode(data)}"
    if name == "reasoning" and tail:
        mode, remaining = _first_argument(tail)
        if remaining.strip():
            return "reasoning 参数过多"
        mode = _chat_root.REASONING_ALIASES.get(mode.lower(), mode.lower())
        if mode not in _chat_root.REASONING_MODES:
            return "reasoning 必须是 keep/on 或 drop/off"
        data["reasoning"] = mode
        storage.save()
        return f"reasoning: {mode}"
    if name == "prompt" and not tail:
        selected = data.get("prompt")
        if selected is None:
            return f"{_chat_root.settings}\n(默认)"
        if isinstance(selected, str):
            return f"{_chat_root.prompts.get(selected, [])}\n({selected})"
        return str(selected)
    if name == "add_prompt":
        try:
            addition = _list_argument(tail)
        except (SyntaxError, ValueError) as error:
            return f"add_prompt 参数错误: {error}"
        data["prompt"] = [*_chat_root.get_prompt(), *addition]
        storage.save()
        return "提示词已追加"
    if name == "setting":
        if not tail:
            return "\n".join(_chat_root.prompts)
        setting_name, remaining = _first_argument(tail)
        if remaining.strip():
            return "setting 参数过多"
        return str(_chat_root.prompts.get(setting_name, "未找到设定，你可能需要先创建设定"))
    if name == "use_setting":
        if not tail:
            data.pop("prompt", None)
            storage.save()
            return "已重置提示词"
        setting_name, remaining = _first_argument(tail)
        if remaining.strip() or setting_name not in _chat_root.prompts:
            return "未找到设定，你可能需要先创建设定"
        data["prompt"] = setting_name
        storage.save()
        return "设定已应用"
    if name == "del_setting" and tail:
        setting_name, remaining = _first_argument(tail)
        if remaining.strip() or setting_name not in _chat_root.prompts:
            return "未找到设定"
        del _chat_root.prompts[setting_name]
        storage.save()
        return "设定已删除"
    if name == "set_setting" and tail:
        setting_name, remaining = _first_argument(tail)
        if not setting_name:
            return "set_setting 需要设定名"
        if remaining.strip():
            try:
                prompt = _list_argument(remaining.strip())
            except (SyntaxError, ValueError) as error:
                return f"set_setting 参数错误: {error}"
        elif "prompt" in data:
            prompt = data["prompt"]
        else:
            return "当前没有可保存的自定义提示词"
        _chat_root.prompts[setting_name] = prompt
        storage.save()
        return "设定已保存"
    if name == "limit" and not tail:
        return _limit_report()
    if name == "limit" and tail:
        return _limit_set(tail)
    if name == "reflect":
        return _reflect_subcommand(tail.strip())
    if name == "awaker":
        return _awaker_subcommand(raw)
    if name == "hint":
        return _hint_subcommand(raw)
    return "子命令格式错误，可用 #help 查看"
