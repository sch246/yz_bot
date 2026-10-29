from __future__ import annotations

from datetime import datetime, timezone
import re

from mods import context, cq, history, llm, op, storage, text
from mods.llm import pricing

import mods.chat as _chat_root


_SUBCOMMAND_HELP = (
    ("help [name]", "显示子命令目录或某条子命令的完整说明。\n格式：#help | #help <名称>"),
    ("status", "查看当前所有核心开关与运行状态（模型、思考、防抖、旁观助手、容量等）。"),
    ("model [<selection>|note <provider/model> [备注]]", """查看当前或指定模型信息与备注（修改备注需管理员）。
#model                        查看当前中心 agent 的模型
#model deepseek/deepseek-flash 查看该模型的单价、视觉与工具支持及备注
#model note bytecat/gpt-6-sol 查看该模型的备注
#model note bytecat/gpt-6-sol 极速1.4s 设置该模型的备注"""),
    ("models [供应商]", """列出当前或指定供应商的模型列表与价格、能力及备注。
#models            列出当前主模型所属供应商的模型
#models bytecat    列出 bytecat 的所有模型与备注
#models gemini     列出 gemini 的所有模型与备注"""),
    ("use_model [selection]", "设置或重置当前模型"),
    ("prompt [set <全文>]", """查看或改写她对自己的认识（改写要管理员）。

就是笔记 self（data/notes/self.md），放在系统提示最前面，取代以前的提示词和命名设定。维护者可以直接编辑文件；她只能在夜间反思里改，要写原因和预期。
#prompt              显示全文
#prompt set <全文>   整份替换；set 之后第一个换行起是全文，留空表示清空"""),
    ("image [off|lazy|eager]", "查看或设置图片读取档位"),
    ("audio [off|lazy|eager]", "查看或设置语音条读取档位（off: 仅提示语音; lazy: 附本地音频路径; eager: 自动转录语音内容）"),
    ("reasoning [keep|drop]", "查看或设置以前几次唤醒的思考是否随这一段上下文带回"),
    ("effort [default|off|none|low|medium|high|max]", "查看或设置主模型思考等级（管理员），默认 default（按模型自身默认）。"),
    ("limit [<画面条数> <整理阈值>|reset]", """查看或设置中心 agent 的窗口画面条数与整理阈值（管理员）。

格式：#limit | #limit <画面条数> <整理阈值> | #limit reset
画面条数是一个窗口第一次出现在这一段上下文里时展示多少条聊天记录；整理阈值（token）是这一段上下文长到多少就交接，即 data/notes/policy/tidy.py 里的 limit。默认值分别为 80、500000。
#limit                      显示两个值，并标出来自全局覆盖还是默认
#limit <画面条数> <整理阈值>  写入全局设置
#limit reset                清掉全局设置，回落到默认"""),
    ("reflect [now|keep]", """查看上次反思时间，或现在就跑一次反思（管理员）。

默认会先把手上这一段上下文交接掉（归档到 data/context/archive/）；如果加 keep（如 #reflect keep 或 #reflect now keep），则保留当前活跃上下文不交接/不重置，直接进入反思（适合白天调整 self 人设又不希望丢失当前聊天上下文）。
#reflect          显示上次反思时间
#reflect now      现在就跑一次完整夜间交接与反思（会交接重置上下文）
#reflect keep     立即进入反思，保留当前活跃上下文不交接/不重置"""),
    ("loop [on|off]", """查看或开关循环总开关（开关要管理员），默认开。

循环必须由她自己调用 loop("在做什么") 显式开启一串，外部唤醒不再自动挂起空循环。总开关开着时，开启后每次唤醒结束后隔两秒再叫醒她，一轮接一轮；她用 wait(秒数) 隔一会儿再来，用 loop(stop=True) 结束这一串。她一个动作都没做的那几轮，间隔翻倍、最长半小时。夜里整理时停，维护者叫停她那一轮也停。关着时关闭循环功能并停止正在转的这串。
#loop        显示开关和现在这一串转到第几轮
#loop on     打开
#loop off    关掉（正在转的这一串也停）"""),
    ("subconscious [on|off]", """查看或开关潜意识提醒（开关要管理员），默认开。

不再自动整理：这一段比上次提醒时又长 5 万 token，就在下一轮请求末尾插一条 system 提醒（这一段多长、能收哪几段、各多大）。收不收、收哪段、摘要怎么写都由你自己调 compress；提醒不写进这一段，收掉以后自然不在。
（compress 和 tidy 都是工具，直接调用，不要用 # 号发）
compress(first, last=0, summary="")：按序号折叠唤醒（如 first=26, last=27；只收一轮 last 留空或填 0；收至最新之前 last="latest"）或具体子步骤（如 first="28.1", last="28.1"），换成 summary，原文另存成归档段（名字带 -folded）。入参 summary 自动截短保留调用存照，返回折叠层级（L1/L2）、节省 token 与最新占用。最近一次唤醒和这一段第一次唤醒不收。
#subconscious       显示开关、这一段多长、离下次提醒还差多少、上次提醒
#subconscious on    打开
#subconscious off   关掉"""),
    ("decompress [<序号|名称|latest>]", """查看或解压还原当前上下文中被 compress 折叠收起的某段历史（管理员）。

留空列出当前上下文中所有可解压的收起块；指定序号或名称还原原始消息。
#decompress           列出当前可解压的收起块
#decompress latest    解压最近一次收起的块
#decompress <名称>    解压指定的收起块（如 20261029-180253-folded）"""),
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
同一目标只留一层；me 不能盖，master 的话 mute 压不住。
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
    ("debounce [on|off|<等待秒数> [上限秒数]|reset]", """查看或设置消息防抖延迟（管理员），默认开启（等待 3s，上限 12s）。

参考 MaiBot 机制：当收到唤醒消息后不立刻触发模型，而是等待数秒；期间若同窗口该说话人继续发消息，则重置计时器并合并上下文，最多延迟到设定的上限。避免用户分段打字时抢话或频繁触发。戳一戳、定时闹钟立即触发不延迟。
#debounce             查看当前防抖配置与状态
#debounce on          开启防抖（恢复默认 3s / 12s）
#debounce off         关闭防抖（收到消息立即秒回）
#debounce 3 12        设置等待 3 秒，最多延长至 12 秒
#debounce reset       重置为默认值（3s / 12s）"""),
    ("observer [on|off|model [selection]|effort [level]|context [recent|full]|prompt|set <全文>|reset]", """查看或设置旁观助手（Observer，修改需管理员）。

在中心会话被唤醒时执行单轮旁观，检测未登记黑话与存疑指代并检索、点出矛盾、自我限制和被跳过的情绪，暴露做事的非预期情况。无盲区时静默不打扰。
#observer                    查看当前状态、模型、思考等级、上下文模式与提示词概况
#observer on|off             开启或关闭旁观助手
#observer model [selection]  查看或设置旁观助手模型（留空查看）
#observer effort [level]     查看或设置旁观助手思考等级（default/off/none/low/medium/high/max）
#observer context [recent|full] 查看或设置上下文模式（recent 仅上一轮省 token，full 全段历史）
#observer prompt             查看当前提示词与 Checklist 全文
#observer set <全文>         整份替换 data/notes/observer.md；留空清空并回落默认
#observer reset              恢复默认配置与模型"""),
)
_SUBCOMMAND_NAMES = {pattern.partition(" ")[0] for pattern, _description in _SUBCOMMAND_HELP}


def requires_op(value: str) -> bool:
    name, _, tail = value.strip().partition(" ")
    if name == "observer":
        first, _, remaining = tail.strip().partition(" ")
        if first in ("on", "off", "set", "reset"):
            return True
        if first in ("model", "effort") and bool(remaining.strip()):
            return True
        return False
    return (name == "hint" or name == "use_model"
            or name in {"image", "reasoning", "effort", "limit", "reflect", "awaker", "prompt", "subconscious", "loop", "debounce"} and bool(tail.strip()))


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
    note = str(attributes.get("note") or "").strip()
    note_str = f"\n    备注: {note}" if note else ""
    return f"{selection}\n    {prices} {'👀' if attributes.get('vision') else ''} {'⚙️' if attributes.get('function_calling') else ''}{note_str}"


def _models_report(data: dict, provider_arg: str = "") -> str:
    """指定 provider（或当前 provider）的模型列表：先问对端，取不到就用本地配置。

    WHY: 清单的权威在对端，本地 models 只是价格与能力元数据。对端列出而本地没有元数据的
    行只显示名字（价格为 ``-``、无能力标记），不替对端猜能力。
    """
    provider_arg = provider_arg.strip().rstrip("/")
    if provider_arg:
        providers = _chat_root.llm_config.get("providers", {})
        if provider_arg not in providers:
            available = "、".join(providers.keys())
            return f"未知供应商 {provider_arg}，当前配置的供应商有：{available}"
        provider = provider_arg
    else:
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
    if tail in ("keep", "now keep", "keep now", "now --keep-context"):
        return "开始反思（保留当前上下文不交接/不重置，反思这一段归档在 data/context/archive/）" if _chat_root.reflect_now(handoff=False) else "中心 agent 正忙，稍后再试"
    return "reflect 参数错误，可用 #help reflect 查看"


def _decompress_subcommand(tail: str) -> str:
    segment = _chat_root._segment.load()
    if not segment or not segment.get("messages"):
        return "当前没有活跃的上下文"
    body = segment["messages"]
    blocks = _chat_root._segment.get_folded_blocks(body)
    if not blocks:
        return "当前上下文中没有被折叠的收起块（无需解压）"
    tail = tail.strip()
    if not tail:
        usable = "\n".join(f"{i + 1}. {b['name']}（{b['preview'][:60]}）" for i, b in enumerate(blocks))
        return f"当前上下文中共有 {len(blocks)} 处收起块可解压：\n{usable}\n\n使用 #decompress <序号|名称|latest> 执行解压"

    chosen = None
    if tail.lower() in ("latest", "-1", "last", "最新"):
        chosen = blocks[-1]
    elif tail.isdigit() and 1 <= int(tail) <= len(blocks):
        chosen = blocks[int(tail) - 1]
    else:
        for b in blocks:
            if tail == b["name"] or tail in b["name"]:
                chosen = b
                break
    if chosen is None:
        usable = "、".join(b["name"] for b in blocks)
        return f"找不到指定的收起块「{tail}」；当前可解压的有：{usable}"

    data = _chat_root._segment.read_archived(chosen["name"])
    if not data or not data.get("messages"):
        return f"找不到归档段 {chosen['name']} 的原始消息"

    orig_msgs = data["messages"]
    target_idx = chosen["index"]
    body = [*body[:target_idx], *orig_msgs, *body[target_idx + 1:]]
    shift = len(orig_msgs) - 1

    restored_marks = []
    if "marks" in data and isinstance(data["marks"], list):
        for m in data["marks"]:
            restored_marks.append({
                "last_shown": {},
                "windows": [],
                "scopes": [],
                "told": None,
                "tokens": None,
                "notice": None,
                "boxes": {},
                "seen": {},
                **m,
                "index": target_idx + m.get("index", 0),
            })
    else:
        for offset, msg in enumerate(orig_msgs):
            content = str(msg.get("content") or "")
            m_wake = re.search(r"这次唤醒你的是（#(\d+)\s*·\s*([^）]+)）", content)
            if m_wake:
                num = int(m_wake.group(1))
                at_time = m_wake.group(2).strip()
                restored_marks.append({
                    "number": num,
                    "at": at_time,
                    "time": time.time(),
                    "index": target_idx + offset,
                    "last_shown": {},
                    "windows": [],
                    "scopes": [],
                    "told": None,
                    "tokens": None,
                    "notice": None,
                    "boxes": {},
                    "seen": {},
                })

    marks = []
    for mark in segment.get("marks", []):
        idx = mark.get("index", 0)
        if idx <= target_idx:
            marks.append(mark)
        else:
            marks.append({**mark, "index": idx + shift})
    marks.extend(restored_marks)
    marks.sort(key=lambda m: m.get("index", 0))
    segment["marks"] = marks
    segment["messages"] = body
    _chat_root._segment.save(segment)
    return f"已成功解压展开收起块 {chosen['name']}：还原了 {len(orig_msgs)} 条消息到当前上下文中"


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


def _debounce_subcommand(tail: str) -> str:
    from mods.chat import debounce as _debounce

    tail = tail.strip()
    data = storage.get("", "agent")
    cfg = _debounce.get_config()
    if not tail:
        if not cfg["enabled"]:
            return "debounce: 关"
        return f"debounce: 开（等待 {cfg['idle']:.1f}s，上限 {cfg['max']:.1f}s）"
    if tail.lower() in ("off", "0", "false"):
        cfg["enabled"] = False
        data["debounce"] = cfg
        storage.save()
        _debounce.cancel_all()
        return "debounce: 关"
    if tail.lower() in ("on", "1", "true"):
        cfg["enabled"] = True
        data["debounce"] = cfg
        storage.save()
        return f"debounce: 开（等待 {cfg['idle']:.1f}s，上限 {cfg['max']:.1f}s）"
    if tail.lower() == "reset":
        cfg = {"enabled": True, "idle": _debounce.DEFAULT_IDLE, "max": _debounce.DEFAULT_MAX}
        data["debounce"] = cfg
        storage.save()
        return f"debounce: 已恢复默认（等待 {cfg['idle']:.1f}s，上限 {cfg['max']:.1f}s）"
    parts = tail.split()
    try:
        if len(parts) == 1:
            idle = float(parts[0])
            max_delay = max(idle * 4.0, 12.0)
        elif len(parts) == 2:
            idle = float(parts[0])
            max_delay = float(parts[1])
        else:
            return "debounce 参数过多，格式：#debounce [<等待秒数> [上限秒数]|on|off|reset]"
    except ValueError:
        return "秒数必须是数字"
    if idle <= 0 or max_delay <= 0:
        return "秒数必须大于 0"
    if max_delay < idle:
        return "上限秒数不能小于等待秒数"
    cfg = {"enabled": True, "idle": idle, "max": max_delay}
    data["debounce"] = cfg
    storage.save()
    return f"debounce: 已设置为 等待 {idle:.1f}s，上限 {max_delay:.1f}s"


def _observer_report() -> str:
    cfg = _chat_root._observer.get_config()
    state = "开" if cfg.get("on", True) else "关"
    model = cfg.get("model", "deepseek/deepseek-flash")
    effort = cfg.get("effort", "none")
    context_mode = cfg.get("context", "recent")
    note_text = _chat_root._notes.read("observer").strip()
    prompt_status = f"自定义（{len(note_text)} 字）" if note_text else "默认"
    return f"observer: {state}\nmodel: {model}\neffort: {effort}\ncontext: {context_mode}\nprompt: {prompt_status}（data/notes/observer.md）"


def _observer_subcommand(raw: str, tail: str) -> str:
    cfg = _chat_root._observer.get_config()
    if not tail:
        return _observer_report()
    action, _, arg = tail.partition(" ")
    action = action.lower()
    arg = arg.strip()

    if action in ("on", "off"):
        if arg:
            return f"observer {action} 参数过多"
        cfg["on"] = (action == "on")
        _chat_root._observer.save_config(cfg)
        return f"observer: {'开' if cfg['on'] else '关'}"

    if action == "model":
        if not arg:
            return f"observer model: {cfg.get('model', 'deepseek/deepseek-flash')}"
        selection, remaining = _first_argument(arg)
        if remaining.strip():
            return "observer model 参数过多"
        try:
            llm.resolve_model(_chat_root.llm_config, selection)
        except ValueError as error:
            return str(error)
        cfg["model"] = selection
        _chat_root._observer.save_config(cfg)
        return f"observer model: {selection}"

    if action == "effort":
        if not arg:
            return f"observer effort: {cfg.get('effort', 'none')}"
        level, remaining = _first_argument(arg)
        if remaining.strip():
            return "observer effort 参数过多"
        raw_level = level.lower()
        normalized = _chat_root.REASONING_EFFORT_ALIASES.get(raw_level, raw_level)
        if normalized not in _chat_root.REASONING_EFFORTS:
            return "observer effort 必须是 default、off/none、low、medium、high 或 max"
        cfg["effort"] = normalized
        _chat_root._observer.save_config(cfg)
        return f"observer effort: {normalized}"

    if action == "context":
        if not arg:
            return f"observer context: {cfg.get('context', 'recent')}"
        mode, remaining = _first_argument(arg)
        if remaining.strip():
            return "observer context 参数过多"
        mode = mode.lower()
        if mode not in ("recent", "full"):
            return "observer context 必须是 recent（仅上一轮，省 token）或 full（全段历史）"
        cfg["context"] = mode
        _chat_root._observer.save_config(cfg)
        return f"observer context: {mode}"

    if action == "prompt":
        if arg:
            return "observer prompt 参数过多"
        text = _chat_root._notes.read("observer").strip()
        origin = "data/notes/observer.md"
        if text:
            return f"{text}\n({origin})"
        return f"{_chat_root._observer.DEFAULT_OBSERVER_PROMPT}\n(内置兜底，尚未创建 {origin})"

    if action == "set":
        body = raw.lstrip()
        set_idx = body.find("set")
        if set_idx >= 0:
            content = body[set_idx + len("set"):].lstrip(" \t")
            if content.startswith("\n"):
                content = content[1:]
        else:
            content = arg
        text = cq.unescape(content.strip())
        _chat_root._observer.set_prompt(text)
        return "已改写 observer 提示词" if text else "已清空 observer 提示词，回落到默认"

    if action == "reset":
        if arg:
            return "observer reset 参数过多"
        cfg = {"on": True, "model": "deepseek/deepseek-flash", "effort": "none", "context": "recent"}
        _chat_root._observer.save_config(cfg)
        return "observer 已恢复默认配置（开，deepseek/deepseek-flash，effort=none，context=recent）"

    return "observer 参数错误，可用 #help observer 查看"


def _status_report(data: dict) -> str:
    """汇总当前所有核心状态与开关（无 emoji，每行以可编辑命令开头）。"""
    model = _chat_root.get_model(data)
    effort = _chat_root.get_reasoning_effort(data)
    reasoning = _chat_root.get_reasoning_mode(data)
    image = _chat_root.get_image_mode(data)
    audio = _chat_root.get_audio_mode(data)

    max_events, threshold = _chat_root.limit()
    segment = _chat_root._segment.load()
    used = _chat_root._segment.context_size(segment.get("tokens"), segment.get("messages", []))

    from mods.chat import debounce as _debounce
    deb_cfg = _debounce.get_config()
    deb_mode = "on" if deb_cfg.get("enabled") else "off"
    deb_detail = f"等待 {deb_cfg['idle']:.1f}s，上限 {deb_cfg['max']:.1f}s" if deb_cfg.get("enabled") else "已关闭"
    loop_switch = "on" if _chat_root._agent.loop_on() else "off"
    loop_detail = _chat_root._agent.loop_status().replace("loop: on", "").replace("loop: off", "").strip("，")
    if not loop_detail:
        loop_detail = "未运行"
    subconscious_switch = "on" if data.get("subconscious", True) else "off"

    obs_cfg = _chat_root._observer.get_config()
    obs_switch = "on" if obs_cfg.get("on", True) else "off"
    obs_model = obs_cfg.get("model", "deepseek/deepseek-flash")
    obs_effort = obs_cfg.get("effort", "none")
    obs_context = obs_cfg.get("context", "recent")
    obs_custom = "自定义" if _chat_root._notes.read("observer").strip() else "默认"

    last_reflect = data.get("reflected_at")
    reflect_str = datetime.fromtimestamp(last_reflect).strftime("%Y-%m-%d %H:%M") if isinstance(last_reflect, (int, float)) else "还没有"
    awaker_rules = [line for line in _chat_root._notes.read("awaker").splitlines() if line.strip() and not line.strip().startswith("#")]
    awaker_str = f"{len(awaker_rules)} 条规则" if awaker_rules else "默认 04:00 reflect"

    lines = [
        # WHY: 每行自带 `#`，用户可以直接照抄；`call()` 又会给整条消息补一个 `#`，
        # 所以首行以 `status` 开头，`call()` 补完正好是 `#status`，且第一行就是 `    #`
        # 绝不回流进模型上下文（见 screen.records）。
        "status 状态面板（复制每行开头命令即可修改）：",
        f"#use_model {model}（当前模型，改用 #use_model <名称>）",
        f"#effort {effort}（思考等级：default/off/none/low/medium/high/max）",
        f"#reasoning {reasoning}（历史思考保留：keep/drop）",
        f"#image {image}（图像读取：off/lazy/eager）",
        f"#audio {audio}（语音读取：off/lazy/eager）",
        f"#limit {max_events} {threshold}（画面 {max_events} 条，整理阈值 {threshold}；当前段约 {used}/{threshold} token）",
        f"#debounce {deb_mode}（防抖延迟：{deb_detail}；改用 #debounce on/off/<等待秒数> [上限秒数]）",
        f"#loop {loop_switch}（循环总开关；状态：{loop_detail}）",
        f"#subconscious {subconscious_switch}（潜意识增长提醒；开关：on/off）",
        f"#observer {obs_switch}（旁观助手；模型: {obs_model}，effort: {obs_effort}，context: {obs_context}，prompt: {obs_custom}）",
        f"#reflect（上次反思：{reflect_str}；立即执行：#reflect now / #reflect keep）",
        f"#awaker（闹钟：{awaker_str}；改写：#awaker set <规则>）",
    ]
    return "\n".join(lines)


def _subcommand(value: str):
    # WHY: hint 的源码要求原样取（含缩进与换行），所以先留一份没 strip 的原文。
    raw = value
    value = value.strip()
    name, _, tail = value.partition(" ")
    tail = tail.strip()
    data = storage.get("", "agent")
    if name == "status" and not tail:
        return _status_report(data)
    if name == "status" and tail:
        return "status 不需要参数，直接发 #status 即可"
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
        first, remaining = _first_argument(tail)
        if first == "note":
            target_model, note_text = _first_argument(remaining)
            if not target_model:
                return "格式：#model note <provider/model> [备注内容]（留空查看，写 clear 清空）"
            try:
                provider, api_model, attributes = llm.resolve_model(_chat_root.llm_config, target_model)
            except ValueError as error:
                return str(error)
            local_models = _chat_root.llm_config.setdefault("providers", {}).setdefault(provider, {}).setdefault("models", {})
            model_info = local_models.setdefault(api_model, attributes)
            if not note_text.strip():
                current_note = model_info.get("note", "")
                return f"{provider}/{api_model} 备注：{current_note or '（无）'}"
            if not op.bot_is_op():
                return "修改模型备注需要管理员权限"
            note_content = note_text.strip()
            if note_content.lower() in ("clear", "none", "空", "清空"):
                model_info.pop("note", None)
                storage.save()
                return f"已清空 {provider}/{api_model} 的备注"
            model_info["note"] = note_content
            storage.save()
            return f"已更新 {provider}/{api_model} 备注：{note_content}"
        selection = first
        if remaining.strip():
            return "model 参数过多"
        try:
            _provider, _api_model, attributes = llm.resolve_model(_chat_root.llm_config, selection)
        except ValueError as error:
            return str(error)
        return "\n".join((_MODEL_TABLE_HEADER, _format_model(selection, attributes)))
    if name == "models":
        return _models_report(data, tail)
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
    if name == "audio" and not tail:
        return f"audio: {_chat_root.get_audio_mode(data)}"
    if name == "audio" and tail:
        mode, remaining = _first_argument(tail)
        if remaining.strip():
            return "audio 参数过多"
        mode = _chat_root.AUDIO_MODE_ALIASES.get(mode.lower(), mode.lower())
        if mode not in _chat_root.AUDIO_MODES:
            return "语音读取档位必须是 off/0、lazy/1 或 eager/2"
        data["audio"] = mode
        storage.save()
        return f"audio: {mode}"
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
    if name == "effort" and not tail:
        return f"effort: {_chat_root.get_reasoning_effort(data)}"
    if name == "effort" and tail:
        level, remaining = _first_argument(tail)
        if remaining.strip():
            return "effort 参数过多"
        raw_level = level.lower()
        normalized = _chat_root.REASONING_EFFORT_ALIASES.get(raw_level, raw_level)
        if normalized not in _chat_root.REASONING_EFFORTS:
            return "effort 必须是 default、off/none、low、medium、high 或 max"
        data["reasoning_effort"] = normalized
        storage.save()
        return f"effort: {normalized}"
    if name == "prompt":
        return _prompt_subcommand(raw)
    if name == "limit" and not tail:
        return _limit_report()
    if name == "limit" and tail:
        return _limit_set(tail)
    if name == "reflect":
        return _reflect_subcommand(tail.strip())
    if name == "decompress":
        return _decompress_subcommand(tail.strip())
    if name == "subconscious" and not tail:
        return _chat_root._segment.subconscious_report()
    if name == "subconscious" and tail:
        if tail.lower() not in ("on", "off"):
            return "subconscious 只能是 on 或 off"
        data["subconscious"] = tail.lower() == "on"
        storage.save()
        return f"subconscious: {tail.lower()}"
    if name == "loop" and not tail:
        return _chat_root._agent.loop_status()
    if name == "loop" and tail:
        if tail.lower() not in ("on", "off"):
            return "loop 只能是 on 或 off"
        data["loop"] = tail.lower() == "on"
        storage.save()
        if not data["loop"]:
            _chat_root._agent.stop_loop("#loop off")
        return f"loop: {tail.lower()}"
    if name == "awaker":
        return _awaker_subcommand(raw)
    if name == "hint":
        return _hint_subcommand(raw)
    if name == "debounce":
        return _debounce_subcommand(tail)
    if name == "observer":
        return _observer_subcommand(raw, tail)
    return "子命令格式错误，可用 #help 查看"
