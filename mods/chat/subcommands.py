from __future__ import annotations

import ast
from datetime import datetime, timezone
import re

from mods import cq, llm, storage, text
from mods.llm import pricing

import mods.chat as _chat_root
from . import view as _view


_SUBCOMMAND_HELP = (
    ("help [name]", "显示子命令目录或某条子命令的完整说明。\n格式：#help | #help <名称>"),
    ("model", "查看当前模型"),
    ("model <selection>", "查看指定模型信息"),
    ("models", "列出当前供应商的模型（优先在线列表）"),
    ("use_model [selection]", "设置或重置当前模型"),
    ("agent [model|use_model|limit|reset_start|use_setting|ops]", "查看或设置中心 agent 的全局模型、预算、设定与操作记录（管理员）；reset_start 在下一次激活时重选历史起点"),
    ("prompt", "查看当前提示词"),
    ("add_prompt [count|list]", "追加聊天或给定提示词"),
    ("setting [name]", "列出或查看设定"),
    ("use_setting [name]", "应用或重置设定"),
    ("set_setting <name> [list]", "保存当前或给定设定"),
    ("del_setting <name>", "删除设定"),
    ("image [off|lazy|eager]", "查看或设置图片读取档位"),
    ("reasoning [keep|drop]", "查看或设置中心已读输出是否原生带回思考内容"),
    ("tools [append|ui]", "查看或设置工具状态的呈现方式"),
    ("limit [<事件数> <token> [提醒百分比]|reset]", """查看或设置中心 agent 的全局可见事件数、上下文 token 上限与提醒阈值（管理员）。

格式：#limit | #limit <事件数> <token> [提醒百分比] | #limit reset
两个上限只在首次激活或 #agent reset_start 后决定历史起点；之后不自动裁剪。提醒百分比决定模型末尾何时显示上下文 token 用量（已用/上限），达到 token 上限时要求先压缩。默认值分别为 500、40000、75%。
#limit                  显示全局两个上限和提醒百分比，并标出值来自全局覆盖还是默认
#limit <事件数> <token> [提醒百分比] 写入全局上限；省略百分比则保留原设置
#limit reset            清掉全局上限与提醒百分比，回落到默认
它是 #agent limit 的简写；未读 mail 不受历史限额丢弃。旧窗口覆盖仍只供独立 .chat 兼容。"""),
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
    data = _chat_root.getchatstorage()
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
        key, _default, normalize = _chat_root.WINDOW_SETTINGS[name]
        origin = "全局" if key in data else "默认"
        value = normalize(data.get(key))
        lines.append(f"{name}: {value}（{origin}）")
    return "\n".join(lines)


def _limit_set(tail: str) -> str:
    """`#limit <事件数> <token> [提醒百分比]` 写全局设置；`reset` 回落默认。

    WHY: 两个历史上限仍一起写，避免只改其中一个造成难解释的半份配置。提醒百分比
    可选，不写就保留原设置；老的两参数命令因此不会意外重置它。
    """
    data = storage.get("", "agent")
    if tail.strip() == "reset":
        for name in ("max_events", "max_token", "pressure_percent"):
            data.pop(_chat_root.WINDOW_SETTINGS[name][0], None)
        data.pop("max_msg", None)
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
        data[_chat_root.WINDOW_SETTINGS[name][0]] = number
    storage.save()
    return "\n".join(f"{name}: {number}" for name, number in written)


def _agent_subcommand(tail: str) -> str:
    """Handle the op-gated detailed entry for the main agent's global settings."""
    data = storage.get("", "agent")
    parts = tail.split()
    if not parts:
        events, tokens = (_chat_root.WINDOW_SETTINGS["max_events"][2](data.get("max_events")),
                          _chat_root.WINDOW_SETTINGS["max_token"][2](data.get("max_token")))
        pressure = _chat_root.WINDOW_SETTINGS["pressure_percent"][2](data.get("pressure_percent"))
        return (f"model: {_chat_root.get_model(data)}\nlimit: {events} {tokens} {pressure}%\n"
                f"image: {_chat_root.get_image_mode(data)}\nreasoning: {_chat_root.get_reasoning_mode(data)}\n"
                f"tools: {_chat_root.get_tools_mode(data)}\nprompt: {data.get('prompt', '(默认)')}")
    verb, *arguments = parts
    if verb == "reset_start" and not arguments:
        data.pop("history_start", None)
        storage.save()
        return "已安排在下一次激活时重选中心历史起点"
    if verb == "use_model" and len(arguments) <= 1:
        if arguments:
            try:
                llm.resolve_model(_chat_root.llm_config, arguments[0])
            except ValueError as error:
                return str(error)
            data["model"] = arguments[0]
        else:
            data.pop("model", None)
    elif (verb == "limit" and len(arguments) in (2, 3)
          and all(value.isdecimal() and int(value) > 0 for value in arguments)
          and (len(arguments) == 2 or int(arguments[2]) <= 100)):
        data["max_events"], data["max_token"] = map(int, arguments[:2])
        if len(arguments) == 3:
            data["pressure_percent"] = int(arguments[2])
    elif verb == "use_setting" and len(arguments) <= 1:
        if arguments and arguments[0] not in _chat_root.prompts:
            return "未找到设定"
        if arguments:
            data["prompt"] = arguments[0]
        else:
            data.pop("prompt", None)
    elif verb in ("image", "reasoning", "tools") and len(arguments) == 1:
        modes = {"image": _chat_root.IMAGE_MODES, "reasoning": _chat_root.REASONING_MODES, "tools": _chat_root.TOOLS_MODES}
        aliases = {"image": _chat_root.IMAGE_MODE_ALIASES, "reasoning": _chat_root.REASONING_ALIASES,
                   "tools": _chat_root.TOOLS_MODE_ALIASES}
        raw = arguments[0].lower()
        choice = aliases[verb].get(raw, raw)
        if choice not in modes[verb]:
            return "设置值不受支持"
        data[verb] = choice
    else:
        return "用法：#agent [use_model [selection]|limit <events> <tokens> [提醒百分比]|reset_start|use_setting [name]|image/reasoning/tools <mode>|ops [clear]]"
    storage.save()
    return _agent_subcommand("")


def _subcommand(value: str):
    # WHY: hint 的源码要求原样取（含缩进与换行），所以先留一份没 strip 的原文。
    raw = value
    value = value.strip()
    name, _, tail = value.partition(" ")
    tail = tail.strip()
    data = _chat_root.getchatstorage()
    if name == "agent":
        return _agent_subcommand(tail)
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
        return f"模型设置为 {selection}"
    if name == "use_model" and not tail:
        data.pop("model", None)
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
        return f"reasoning: {mode}"
    if name == "tools" and not tail:
        return f"tools: {_chat_root.get_tools_mode(data)}"
    if name == "tools" and tail:
        mode, remaining = _first_argument(tail)
        if remaining.strip():
            return "tools 参数过多"
        mode = _chat_root.TOOLS_MODE_ALIASES.get(mode.lower(), mode.lower())
        if mode not in _chat_root.TOOLS_MODES:
            return "tools 必须是 append 或 ui"
        data["tools"] = mode
        return f"tools: {mode}"
    if name == "prompt" and not tail:
        selected = data.get("prompt")
        if selected is None:
            return f"{_chat_root.settings}\n(默认)"
        if isinstance(selected, str):
            return f"{_chat_root.prompts.get(selected, [])}\n({selected})"
        return str(selected)
    if name == "add_prompt":
        try:
            if not tail:
                addition = _view._chat_msgs()[-1:]
                result = "上一句聊天已追加到提示词"
            elif re.fullmatch(r"-?\d+", tail):
                count = int(tail)
                messages = _view._chat_msgs()
                addition = messages[-count:] if count else messages
                result = "当前聊天已追加到提示词(注意重复)"
            else:
                addition = _list_argument(tail)
                result = "提示词已追加"
        except (SyntaxError, ValueError) as error:
            return f"add_prompt 参数错误: {error}"
        data["prompt"] = [*_chat_root.get_prompt(), *addition]
        return result
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
            return "已重置提示词"
        setting_name, remaining = _first_argument(tail)
        if remaining.strip() or setting_name not in _chat_root.prompts:
            return "未找到设定，你可能需要先创建设定"
        data["prompt"] = setting_name
        return "设定已应用"
    if name == "del_setting" and tail:
        setting_name, remaining = _first_argument(tail)
        if remaining.strip() or setting_name not in _chat_root.prompts:
            return "未找到设定"
        del _chat_root.prompts[setting_name]
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
        return "设定已保存"
    if name == "limit" and not tail:
        return _limit_report()
    if name == "limit" and tail:
        return _limit_set(tail)
    if name == "hint":
        return _hint_subcommand(raw)
    return "子命令格式错误，可用 #help 查看"
