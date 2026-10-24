from __future__ import annotations

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
    ("prompt [set <全文>]", """查看或改写她对自己的认识（改写要管理员）。

就是笔记 self（data/notes/self.md），放在系统提示最前面，取代以前的提示词和命名设定。维护者可以直接编辑文件；她只能在夜间反思里改，要写原因和预期。
#prompt              显示全文
#prompt set <全文>   整份替换；set 之后第一个换行起是全文，留空表示清空"""),
    ("image [off|lazy|eager]", "查看或设置图片读取档位"),
    ("reasoning [keep|drop]", "查看或设置以前几次唤醒的思考是否随这一段上下文带回"),
    ("limit [<画面条数> <整理阈值>|reset]", """查看或设置中心 agent 的窗口画面条数与整理阈值（管理员）。

格式：#limit | #limit <画面条数> <整理阈值> | #limit reset
画面条数是一个窗口第一次出现在这一段上下文里时展示多少条聊天记录；整理阈值（token）是这一段上下文长到多少就交接，即 data/notes/policy/tidy.py 里的 limit。默认值分别为 80、500000。
#limit                      显示两个值，并标出来自全局覆盖还是默认
#limit <画面条数> <整理阈值>  写入全局设置
#limit reset                清掉全局设置，回落到默认"""),
    ("reflect [now]", """查看上次反思时间，或现在就跑一次夜间交接与反思（管理员）。

先把手上这一段上下文交接掉（笔记、日记确认到位后归档到 data/context/archive/），月末、年末各汇总一次日记；然后她用新的一段反思：像人排查一样从症状出发，读归档的上下文、聊天记录、行动记录和笔记的历史版本找原因，必要时改策略或写提案；不对外说话。什么时候自动跑由 awaker 规则决定，默认 daily 04:00 reflect。
#reflect          显示上次反思时间
#reflect now      现在就跑一次（中心 agent 正忙时不执行）"""),
    ("subconscious [on|off]", """查看或开关她的潜意识（开关要管理员），默认关。

潜意识有自己的一条上下文（data/context/subconscious.json），只往后接。她每忙完一阵（没有别的召唤在等），这阵的经过接到它末尾，它决定她那条的末尾怎么办：
收起 <时刻> <记号>  从这次唤醒起换成一句记号，原样另存成归档段（名字带 -folded），记号里写着怎么读回来
放下 <时刻>         同她自己的 dismiss：从这次唤醒起清掉，消息回到没读
留着                什么都不动
怎么判断写在 data/notes/policy/subconscious.md，没有这个文件时用默认说明；它的理由只留在它自己那条里，她看不到。它那条过了 10 万 token 就自己交接：写下地图，旧的归档（名字带 -subconscious），新的一条从地图开始。
#subconscious       显示开关、它这一条多长、上次的决定
#subconscious on    打开
#subconscious off   关掉"""),
    ("awaker [set <规则>]", """查看或改写她的闹钟规则（改写要管理员）。

规则就是笔记 awaker（data/notes/awaker.md），她自己也能改，每行一条：
every 10m <理由>             每隔一段时间叫醒她一次（最短 1m）
daily 23:30 <理由>           每天这个时刻
at 2026-10-23 08:00 <理由>   某个时刻一次
on g123 <理由>               这个窗口下一条消息来时（g123:QQ号 只等这个人，u号 是私聊）
wait g123:QQ号 until 2026-10-23 10:30 <理由>   等这个人的后续，来了或到点都叫她，只叫一次
follow g123 until 2026-10-23 22:30 <理由>   覆盖层：到点之前这个窗口每条消息都叫她（只认 QQ）
mute bili until 2026-10-24 08:00 <理由>     覆盖层：到点之前都不叫她，消息照样未读
覆盖层的目标是窗口（g123、u456、g123:QQ号、bili:BV…）或一整类（group、private、bili）；到期划掉，
同一目标只留一层；me 不能盖，master 的话 mute 压不住。她在群里说完话，那个群自动算 follow 5 分钟
（不写进笔记，这里能看到），她连着两次 dismiss 就停。
when <Python 表达式> -> <理由>   每分钟求值，第一次为真时叫她
at、on、wait、when 响过就从笔记里划掉。她平时用 wake_me 写这些行。
理由写 reflect 表示夜间交接与反思；其它理由原样交给她。文件为空时只有 daily 04:00 reflect。
#awaker              显示规则、上次响的时间和不认识的行
#awaker set <规则>   整份替换；set 之后第一个换行起是规则全文"""),
    ("hint [get|set|default]", """查看、编写或开关本窗口的结束提示（管理员）。

格式：#hint | #hint get | #hint set <代码> | #hint set | #hint default [get|set <代码>]
配置是普通文件：全局默认在 data/hint/default.py，本窗口在 data/hint/g<群号>.py 或 u<QQ号>.py；首行 # on 或 # off 是开关，其余是代码。生效的是两者按 {**默认, **窗口} 合并之后 code 非空、on 为真的那份。
聊天循环停下时求值一次，非 None 的结果作为一条消息发出（自带 # 前缀，不进模型上下文）。
求值环境是共享动态环境，另外注入 window（触发窗口）、usage（这次唤醒最后一次子请求的上下文 token 数，以接口报的为准）、peak（这次唤醒里最大的一次）与 limit（#limit 的整理阈值）。例：f'上下文 {usage}/{limit} token'
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
    return (name == "hint" or name == "use_model"
            or name in {"image", "reasoning", "limit", "reflect", "awaker", "prompt", "subconscious"} and bool(tail.strip()))


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
    WHY: 改完立刻写文件，不等后台扫描——hint 是用户手写的配置，紧接着一次重启就该还在。
    """
    event = context.current() or {}
    window = history.window(event)
    if window is None:
        return "当前没有聊天窗口"
    default = _chat_root.hint_config(None)
    chat_hint = _chat_root.hint_config(window)
    verb, source = _hint_request(raw)
    if verb in ("get", "default", "default get") and source.strip():
        return f"hint {verb} 参数过多"
    if verb == "set":
        if source.strip():
            _chat_root.save_hint(window, {"code": cq.unescape(source), "on": True})
            return "提示已开启"
        _chat_root.save_hint(window, {})
        return "已设为默认"
    if verb == "default set":
        if not source.strip():
            return "hint default set 需要代码"
        _chat_root.save_hint(None, {"code": cq.unescape(source), "on": True})
        return "默认已开启"
    if verb == "get":
        return _hint_report(_chat_root._hint_effective(default, chat_hint), _hint_origin(chat_hint))
    if verb == "default get":
        return _hint_report(default, "默认")
    if verb == "default":
        default["on"] = not bool(default.get("on", False))
        _chat_root.save_hint(None, default)
        return "默认已开启" if default["on"] else "默认已关闭"
    if verb == "":
        chat_hint["on"] = not bool(chat_hint.get("on", False))
        _chat_root.save_hint(window, chat_hint)
        return "提示已开启" if chat_hint["on"] else "提示已关闭"
    return "hint 参数错误，可用 #help hint 查看"


def _limit_report() -> str:
    """`#limit` 查看画面条数与整理阈值。"""
    _chat_root.limit()
    data = storage.get("", "agent")
    lines = []
    for name, label in (("max_events", "画面条数"), ("tidy_token", "整理阈值")):
        key, normalize = _chat_root.AGENT_SETTINGS[name]
        origin = "全局" if key in data else "默认"
        lines.append(f"{label}: {normalize(data.get(key))}（{origin}）")
    return "\n".join(lines)


def _limit_set(tail: str) -> str:
    """`#limit <画面条数> <整理阈值>` 写全局设置；`reset` 回落默认。

    WHY: 两个值仍一起写，避免只改其中一个造成难解释的半份配置。
    """
    data = storage.get("", "agent")
    if tail.strip() == "reset":
        for name in ("max_events", "tidy_token", "max_token", "pressure_percent"):
            data.pop(name, None)
        storage.save()
        return "已重置画面条数与整理阈值，回落到默认"
    parts = tail.split()
    if len(parts) != 2 or not all(part.isdigit() and int(part) >= 1 for part in parts):
        return "limit 参数错误：#limit <画面条数> <整理阈值>，可用 #help limit 查看"
    data["max_events"], data["tidy_token"] = int(parts[0]), int(parts[1])
    data.pop("max_token", None)
    data.pop("pressure_percent", None)
    storage.save()
    return f"画面条数: {parts[0]}\n整理阈值: {parts[1]}"


def _reflect_subcommand(tail: str) -> str:
    if not tail:
        last = storage.get("", "agent").get("reflected_at")
        shown = datetime.fromtimestamp(last).strftime("%Y-%m-%d %H:%M") if isinstance(last, (int, float)) else "还没有"
        return f"上次反思：{shown}；什么时候自动跑见 #awaker"
    if tail == "now":
        return "开始夜间交接与反思，反思这一段归档在 data/context/archive/" if _chat_root.reflect_now() else "中心 agent 正忙，稍后再试"
    return "reflect 参数错误，可用 #help reflect 查看"


def _awaker_subcommand(raw: str) -> str:
    body = raw.lstrip()[len("awaker"):]
    head, _, rest = body.partition("\n")
    if not head.strip() and not rest.strip():
        return _chat_root._awaker.report()
    if head.strip().partition(" ")[0] != "set":
        return "awaker 参数错误，可用 #help awaker 查看"
    text = cq.unescape((head.strip()[3:].strip() + "\n" + rest).strip())
    _chat_root._notes.write("awaker", text)
    return _chat_root._awaker.report()


def _prompt_subcommand(raw: str) -> str:
    body = raw.lstrip()[len("prompt"):]
    head, _, rest = body.partition("\n")
    if not head.strip() and not rest.strip():
        text = _chat_root._notes.read("self").strip()
        return (text or "（self 还是空的）") + "\n(data/notes/self.md)"
    if head.strip().partition(" ")[0] != "set":
        return "prompt 参数错误，可用 #help prompt 查看"
    text = cq.unescape((head.strip()[3:].strip() + "\n" + rest).strip())
    _chat_root._notes.write("self", text)
    return "已改写 self" if text else "已清空 self"


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
    if name == "prompt":
        return _prompt_subcommand(raw)
    if name == "limit" and not tail:
        return _limit_report()
    if name == "limit" and tail:
        return _limit_set(tail)
    if name == "reflect":
        return _reflect_subcommand(tail.strip())
    if name == "subconscious" and not tail:
        return _chat_root._subconscious.report()
    if name == "subconscious" and tail:
        if tail.lower() not in ("on", "off"):
            return "subconscious 只能是 on 或 off"
        data["subconscious"] = tail.lower() == "on"
        storage.save()
        return f"subconscious: {tail.lower()}"
    if name == "awaker":
        return _awaker_subcommand(raw)
    if name == "hint":
        return _hint_subcommand(raw)
    return "子命令格式错误，可用 #help 查看"
