"""The central agent loop: one context, appended to on every wake, until a handoff.

WHY: 2026-10-22 维护者定：只要不交接，就一直是同一个上下文。交接之后第一次被叫醒时
从头组装一次——开头是 self、注意事项和工具状态，接着是笔记、一份只列名字的目录、
这次唤醒的画面；之后每次唤醒只往后追加新消息、第一次出现的人或群的笔记、谁叫醒的她，
前面一个字都不动。这一段存在 ``segment`` 的普通文件里，重启后接着用。

交接的驱动力是"这段上下文要被移除了，确认交接做好"：在同一段末尾追加一句交接指令，
她把各类信息确认到该在的地方（recent、人和群的笔记、global、todo、当天日记），
这段才归档。笔记都是改写成应有的样子，所以交接可以安全地跑很多次。

WHY: 交接由她自己在同一段里做，不另起一个只看到摘要的"图书管理员"会话：总结的人得
看到和她一样多，只是目的不同；同一段上下文还能整段命中前缀缓存。
"""

from __future__ import annotations

import json
import threading
import time

from mods import context, identity, image, llm, oplog, py, storage

import mods.chat as _chat_root
from . import notes as _notes, reader as _reader, roles as _roles, screen as _screen, segment as _segment, view as _view, windows as _windows
from . import subconscious as _subconscious

_SEEN_KEY = "seen"
# WHY: 什么时候交接是可以改的策略（2026-10-22 维护者："唤醒条件本身也配置化"）。
# 条件是 `data/notes/policy/tidy.py` 里的 Python，用 `.py` 那份共享环境求值，末行的值为真
# （或是一段说明原因的文字）就交接；文件不存在时用下面这份默认，和它写成文件后一模一样。
DEFAULT_TIDY = """# 什么时候交接。每次子请求结束（stage == "round"）和每次唤醒往这一段追加之前（stage == "assemble"）
# 各求值一次：前面的行照常执行，最后一行是一个表达式，它的值为真就交接，是字符串时就是交接的原因。
# 可用的名字：stage、used（这一段上下文的 token 数，以接口上次报的为准，之后追加的按字数估）、grown（这次唤醒长了多少）、limit（#limit 设的整理阈值）、
# since_handoff（这一段开始了多少秒）、kind（唤醒／闹钟／反思）、hour。
"上下文太长" if used > limit else None
"""
# A window shows at least this many lines before its first unseen one.
_LEAD_IN = 3
_FRESH_MAX = 200
# dismiss 能清回的记号：每次唤醒追加之前，这一段里会被这次唤醒改动的那几项；最多留最近这么多个。
_MARKED = ("last_shown", "windows", "scopes", "told", "tokens", "notice")
_MARKS = 40
_KNOWN_PEOPLE = 6
_TYPING_EVERY = 5
# 循环：一轮结束后隔几秒叫醒她。
_LOOP_GAP = 2.0
# 开着的循环（只在内存里，重启就没了）：在做什么、转到第几轮、下一轮是不是已经排上了、排上的是第几次
# 排的（她 wait 一下，先排上的那一轮就作废）；她 wait 了还有 `due`（下一轮的时刻）和 `waited`（等了几秒）。
_loop: dict | None = None
# 一次唤醒里最多交接几次：防她每一段都调 tidy 时原地打转，不是常态限制。
_HANDOFFS_PER_WAKE = 3
_FORCE_AFTER_FAILURES = 2
_FORCE_OVER_THRESHOLD = 2


def _text_tokens(messages: list[dict]) -> int:
    def strip(value):
        if isinstance(value, list):
            return [strip(part) for part in value]
        if isinstance(value, dict):
            if value.get("type") == "image_url":
                return "[image]"
            return {key: strip(part) for key, part in value.items()}
        return value

    return _chat_root.count_tokens(json.dumps(strip(messages), ensure_ascii=False))


def _context_size(reported, body: list[dict]) -> int:
    """How long *body* is: the size the API last reported for its first messages, plus an estimate of the rest.

    *reported* is ``[tokens, count]``: the API counted ``tokens`` when ``body[:count]``
    was the whole request and its answer.

    WHY: 维护者 2026-10-23：以接口返回的为准。字数估算把图片当成几个字，DeepSeek 每张图
    却最多算 1024 token（官方图像理解文档），图多的群攒上几百张也不会交接，直到撞上单个
    请求 600 张图或 48 MiB 的上限；接口报的 prompt+completion 连系统提示、工具定义和图片都
    算在内。报数之后才追加的（工具结果、新画面）还没人报过，先按字数估。
    """
    if isinstance(reported, list) and len(reported) == 2 and 0 <= reported[1] <= len(body):
        return reported[0] + _text_tokens(body[reported[1]:])
    return _text_tokens(body)


def _seen() -> dict:
    data = storage.get("", "agent")
    value = data.get(_SEEN_KEY)
    if not isinstance(value, dict):
        value = data[_SEEN_KEY] = {}
    return value


def _woken(notice: dict | None, turn) -> tuple[list[tuple], list[str], list[int]]:
    windows: list[tuple] = []
    lines: list[str] = []
    authors: list[int] = []
    if notice is not None:
        for item in notice.get("activations", []):
            if isinstance(item.get("user_id"), int):
                authors.append(item["user_id"])
            window = tuple(item["window"])
            windows.append(window)
            stamp = _screen.clock(item["time"]) if isinstance(item.get("time"), (int, float)) else "?"
            author = f" 作者={item['user_id']}" if item.get("user_id") is not None else ""
            lines.append(f"{_screen.label(window)} {item.get('kind')}{author} 时间={stamp}")
        for window in notice.get("windows", []):
            windows.append(tuple(window))
    windows.extend(window for window in turn.associated_windows if _windows.known(window))
    return list(dict.fromkeys(windows)), lines, authors


def _note_block(scopes: list[str], title: str) -> dict | None:
    parts = [text for scope in scopes if (text := _notes.render(scope))]
    if not parts:
        return None
    return {"role": "system", "content": title + "\n\n" + "\n\n".join(parts)}


def _head(woken: list[tuple], segment: dict) -> list[dict]:
    """What a new segment starts with: her standing notes and a directory of the rest.

    WHY: 做法（global）、交接下来的 recent 和最近一篇日记在每一段开头；叫醒她的窗口和
    叫她的人的笔记跟着画面一起出现（见 `_people`）。待办每个子请求都挂在末尾。别的都由
    她自己召回，目录只列名字。
    """
    days = [key for key in _notes.diaries() if len(key) == 10]
    scopes = ["global", "recent", *(f"diary:{days[-1]}" for _ in days[-1:])]
    segment["scopes"] = list(scopes)
    messages = []
    block = _note_block(scopes, "以下是你的笔记（普通文件，你随时能改，可能不完整）：")
    if block:
        messages.append(block)
    messages.append({"role": "system", "content": "还能召回的东西（用 search 找，read_log、read_actions、read_note 读）：\n"
                     + _roles.directory(woken)})
    return messages


def _people(windows: list[tuple], authors: list[int], segment: dict) -> tuple[list[dict], list[str]]:
    """Notes for windows and people appearing in this segment for the first time, and their scopes."""
    scopes = [_notes.scope_of(window) for window in windows if window[0] in _windows.QQ]
    scopes.extend(f"person:{author}" for author in authors[:_KNOWN_PEOPLE])
    new = [scope for scope in dict.fromkeys(scopes) if scope not in segment["scopes"]]
    block = _note_block(new, "这一段里第一次出现的人或群，你的笔记：")
    return ([block] if block else []), new


def _offline_calls(window: tuple, shown: list[dict]) -> list[dict]:
    """Unread recovered calls to her that the screen's slice leaves out, oldest first.

    WHY: 补回的消息按时间插在开机后实时到的消息前面。这一段已经给过这个窗口时，画面只
    接着"上次给到哪一条"往后给，看到时间也已经过了它们，叫她的那条就不在画面里，第一次
    输出却会把它标成已读（2026-10-23 审查）。所以按消息号把还没读的离线呼叫找出来补上。
    """
    from mods import _backfill, chatlog

    calls = [entry["event"] for entry in oplog.unread(window)
             if entry.get("activation_kind") == _backfill.OFFLINE_CALL]
    if not calls:
        return []
    wanted = {chatlog.recall_key(event.get("message_id")) for event in calls}
    texts = {item["text"] for item in shown}
    found = []
    for item in _screen.records(window, 1_000_000, since=min(int(event["time"]) for event in calls)):
        head = chatlog._split_head(item["text"].partition("\n")[0])
        if (item["text"] not in texts and head is not None and head["message_id"] is not None
                and chatlog.recall_key(head["message_id"]) in wanted):
            found.append(item)
    return found


def _with_calls(window: tuple, calls: list[dict], text: str) -> str:
    if not calls:
        return text
    return ("（下面是离线期间叫你的消息，补回时插在了更早的位置）\n" + _screen.render(window, calls)
            + ("\n" + text if text else ""))


def _screen_text(window: tuple, count: int, last: str | None) -> tuple[str, tuple, str | None]:
    """One window's chatlog lines for this wake, the (time, arrival) to mark seen, and its last record.

    *last* is the last record of this window already shown earlier in this segment;
    then only the records after it are new.
    """
    mark = _seen().get(_screen.label(window))
    # WHY: 标已读只标到读画面这一刻为止的未读（2026-10-22 审查发现：生成期间同窗口
    # 再来的 at 会在第一次输出时一起被标掉，收尾不再接续）。chatlog 写入与登记到达
    # 在同一把窗口锁里、先写后登记，所以先取边界再读文件，边界内的都在画面能读到的文件里。
    through = oplog.latest_pending_arrival(window)
    # WHY: 维护者 2026-10-24：follow 期间（包括说完话自动听）到的消息不截，之前的照旧最多 _FRESH_MAX
    # 条。她选了要跟上，dismiss 掉又回来的那批要原样给全，不靠 read_log 往前翻；一个群一天的消息放得下。
    # 只按"现在盖着 follow"不行：follow 一个很久没看的群，第一屏会把几周的积压全倒出来。
    layer = _chat_root._awaker.layer(window)
    since = layer.get("since") if layer is not None and layer["state"] == "follow" else None
    # 记录时间只到秒：起点取整，同一秒里到的也算 follow 期间。
    during = (sum(1 for item in _screen.records(window, 1_000_000, since=since)
                  if item["time"] is not None and item["time"] >= int(since)) if since is not None else 0)
    cap = _FRESH_MAX + during
    items = _screen.records(window, max(count, 1) + cap)
    texts = [item["text"] for item in items]
    if last is not None and last in texts:
        # WHY: 这一段里已经给过这个窗口：按"上次给到哪一条"接着给，不按看到时间——看到
        # 时间只精确到秒，同一秒里后到的那条按时间会被当成看过。
        after = items[len(texts) - texts[::-1].index(last):]
        shown = after[-cap:]
        calls = _offline_calls(window, shown)
        if not shown:
            return _with_calls(window, calls, ""), (None, through), last
        text = _screen.render(window, shown, mark)
        if len(after) > cap:
            text = (f"（{_screen.label(window)} 又有 {len(after)} 条新消息，这里只展示最新的 {len(shown)} 条；"
                    f"更早的用 read_log(\"{_screen.label(window)}\", before=\"{_screen.clock(shown[0]['time'])}\") 往前翻）\n" + text)
        return _with_calls(window, calls, text), (_screen.latest_time(shown), through), shown[-1]["text"]
    fresh = [item for item in items if mark is not None and item["time"] is not None and item["time"] > mark]
    # WHY: 新消息（_FRESH_MAX 条以内）全部展示，画面条数只决定还附多少旧消息；至少带上新消息之前的
    # 几条接上话头。看到时间只精确到秒，同一秒里后到的那条也靠这几条不丢。
    older = [item for item in items if item not in fresh][-max(count - len(fresh), _LEAD_IN):]
    shown = older + fresh if mark is not None else items[-max(count, _LEAD_IN):]
    skipped = 0
    if len(fresh) > cap:
        # WHY: 维护者 2026-10-22 定：新消息太多（比如离线一晚）只展示最新的 _FRESH_MAX 条，
        # 但要明说跳过了多少、从哪里接着翻，看到位置照样推进；不静默丢，也不撑爆上下文。
        total = sum(1 for item in _screen.records(window, 1_000_000, since=mark)
                    if item["time"] is not None and item["time"] > mark)
        shown = fresh[-cap:]
        skipped = total - len(shown)
    if not shown:
        return "", (None, through), last
    text = _screen.render(window, shown, mark)
    if skipped:
        text = (f"（{_screen.label(window)} 上次之后有 {skipped + len(shown)} 条新消息，这里只展示最新的 "
                f"{len(shown)} 条；更早的 {skipped} 条要看就用 read_log(\"{_screen.label(window)}\", "
                f"before=\"{_screen.clock(shown[0]['time'])}\") 往前翻）\n" + text)
    return _with_calls(window, _offline_calls(window, shown), text), (_screen.latest_time(shown), through), shown[-1]["text"]


def _unread_hint(on_screen: set[tuple]) -> str:
    rows = []
    for detail in oplog.pending_details():
        window = tuple(detail["window"])
        if not _windows.known(window) or window in on_screen:
            continue
        rows.append(f"{_screen.label(window)} {detail['unread']} 条"
                    + (f"（@你 {detail['mentions']}）" if detail["mentions"] else "")
                    + "".join(f"（{badge}）" for badge in _chat_root._awaker.badges(window)))
    return ("其它窗口还有没看过的消息：" + "；".join(rows[:10])
            + "。要看就用 read_log 读原文。") if rows else ""


def _late_arrivals(newest: dict[tuple, tuple], last_shown: dict[str, str],
                   count: int) -> tuple[list[dict], dict[tuple, tuple]]:
    """Messages that reached the windows on screen since she read them, to append to this segment.

    Advances *newest* and *last_shown* now; the marks it returns are applied
    (seen time, read) only once she has produced output after seeing them.

    WHY: 维护者 2026-10-23：私聊每条都叫醒她以后，人分几条发的话会在她干活时陆续到，
    不用延迟、也不打断请求，下一次子请求之前写进这一段，由她自己决定接不接（参照 Cortico
    在轮次边界投递）。以前挂在请求末尾的提示里，每次重算、不进这一段，收尾还要为它们再叫
    一次。

    WHY: 已读要等她看过以后有了一次输出（哪怕只是思考）才标，维护者说这样最稳：请求失败
    时它们仍是未读，叫她的那条照样会再叫醒她。`last_shown` 却在追加时就推进，它和这一段
    一起存盘，这一段里有了它们，下次的画面就不会再给一遍。

    WHY: 只是她自己发出去的回显不单独插一条：她刚 send 的话不用再看一遍，它们留着未读，
    下次和别的新消息一起出现。
    """
    added: list[dict] = []
    marks: dict[tuple, tuple] = {}
    bot = identity.bot_id()
    for window, (_stamp, through) in list(newest.items()):
        late = [entry for entry in oplog.unread(window)
                if through is None or not oplog.arrival_before_or_at(entry["arrival"], through)]
        if not any(entry["event"].get("user_id") != bot for entry in late):
            continue
        label = _screen.label(window)
        text, mark, last = _screen_text(window, count, last_shown.get(label))
        newest[window] = marks[window] = mark
        if not text:
            continue
        last_shown[label] = last
        calls = [f"{entry.get('activation_kind')}"
                 + (f" 作者={entry['event']['user_id']}" if entry["event"].get("user_id") is not None else "")
                 for entry in late if entry.get("activated")]
        facts = ["你干活时刚到的", _screen.clock(), *([f"叫你的：{'、'.join(calls)}"] if calls else []),
                 *_chat_root._awaker.badges(window)]
        added.append(_view.outside(label, facts, text))
    return added, marks


def _box_note(boxes: dict) -> str:
    """Unsent input boxes, told once after a handoff.

    WHY: 维护者 2026-10-23：type_word 的返回已经在上下文里，每次请求末尾再列一遍是重复。
    只有交接后要说：输入框跟着唤醒走还在，打字那次的返回却随上一段归档了。
    """
    return ("交接前你在输入框里打了还没发的字：\n"
            + "\n".join(f"{label}：{box['text']}" for label, box in boxes.items())
            + "\n要发就 send，不发就不管它。")


def _mark_seen(newest: dict[tuple, tuple]) -> None:
    seen = _seen()
    for window, (stamp, through) in newest.items():
        if stamp is not None:
            seen[_screen.label(window)] = max(stamp, seen.get(_screen.label(window), 0))
        _reader.mark_window_read(window, through=through)
    storage.save()


def _did_something(messages: list[dict]) -> bool:
    return any(message.get("role") == "assistant" and not _view.is_incoming(message) for message in messages)


def _tidy_reason(turn, segment: dict, stage: str, used: int, grown: int = 0) -> str:
    """Evaluate the handoff condition; a non-empty string means hand off now, for that reason."""
    _max_events, threshold = _chat_root.limit()
    namespace = dict(py.loc)
    namespace.update(stage=stage, used=used, grown=grown, limit=threshold,
                     since_handoff=time.time() - segment["started"], kind=turn.log["kind"],
                     hour=time.localtime().tm_hour)
    code = _notes.read_policy("tidy").strip() or DEFAULT_TIDY
    try:
        result = py.eval_last(code, namespace)
    except Exception:
        # 她或维护者写坏了条件：这次按默认判断，终端里留下 traceback。
        _roles._stream.exception("交接条件求值失败，按默认判断")
        result = py.eval_last(DEFAULT_TIDY, namespace) if code != DEFAULT_TIDY else None
    if not result:
        return ""
    return result if isinstance(result, str) else "交接条件成立"


def diary_day(moment: float) -> str:
    """The day a handoff writes about: until 6 a.m. it is still the day before."""
    return time.strftime("%Y-%m-%d", time.localtime(moment - 6 * 3600))


def _handoff(turn, reason: str, model: str | None = None) -> bool:
    """Confirm the handoff at the end of the current segment, then archive it.

    WHY: 交接没跑完（出错、被打断、没有输出）就不归档：这一段原样留着，下次再交接一遍。
    笔记都是改写成应有的样子、内容没变就不写，所以重跑不会重复。"跑完"指她最后一次回复
    不再调用工具：2026-10-23 审查发现，只看"有过输出"时，中途出错、被打断、被阈值停下
    都会被当成交接完成而归档，没来得及记下的东西就跟着这段一起离开了眼前。
    """
    segment = _segment.load()
    if not _did_something(segment["messages"]):
        return False
    if turn.handoffs >= _HANDOFFS_PER_WAKE:
        _roles._stream.warning(f"这次唤醒已经交接了 {turn.handoffs} 次，不再交接（原因：{reason}）")
        turn.handoff_failed = True
        return False
    # 强制归档也算一次：续做时每段都长过两倍阈值的话，不能借它绕过上限原地打转。
    turn.handoffs += 1
    _max_events, threshold = _chat_root.limit()
    forced = _forced_reason(segment, threshold)
    if forced:
        name = _archive(turn, segment, f"未交接，强制归档（{forced}；本来要交接的原因：{reason}）")
        _roles._stream.warning(f"这一段{forced}，没有交接就归档为 {name}")
        # WHY: 这句提醒不能只挂在这次唤醒上：接下来的请求要是失败、没有输出，新的一段不会保存，
        # 她就再也不知道该去补哪一段（2026-10-23 审查）。所以写进新的一段里，第一次输出时才消掉。
        fresh = _segment.fresh()
        fresh["notice"] = (f"上一段上下文没能交接（{forced}），已经原样归档为 {name}。recent 和其它笔记可能没记到最后；"
                           f"需要时加载 memory_system 用 read_context(\"{name}\") 读那一段，把漏下的补进笔记。")
        _segment.save(fresh)
        turn.handoff_note = ""
        return True
    messages = [*segment["messages"],
                {"role": "system", "content": _roles.handoff_prompt(reason, diary_day(time.time()))}]
    grew = []

    def stop_when(used: int, initial: int) -> bool:
        if used - initial > threshold:
            grew.append(used - initial)
            return True
        return False

    session, failure = None, ""
    try:
        session = _run_session(model, turn, segment, messages, lambda: None, stop_when,
                               quiet=True, persist=False, fail_fast=True, known=len(segment["messages"]))
    except Exception as error:
        _roles._stream.exception("交接出错")
        failure = f"出错：{type(error).__name__}: {error}"
    turn.release = ""
    if not failure:
        added = session.messages[_system_count(session) + len(messages):]
        last = session.messages[-1] if session.messages else {}
        if turn.cancelled:
            failure = "被打断"
        elif grew:
            failure = f"交接时又长了 {grew[0]} token，超过整理阈值，被停下"
        elif not any(message.get("role") == "assistant" and (str(message.get("content") or "").strip()
                                                             or message.get("tool_calls")) for message in added):
            failure = "没有输出"
        elif last.get("role") != "assistant" or last.get("tool_calls"):
            failure = "没有正常结束"
    if failure:
        turn.handoff_failed = True
        if failure != "被打断":
            # 被打断是有人 ^C，不算交接本身的毛病；其余的记在这一段里，连着失败就强制归档。
            segment["handoff_failures"] = segment.get("handoff_failures", 0) + 1
            _segment.save(segment)
        _roles._stream.warning(f"交接没完成（{failure}），这一段（{_screen.clock(segment['started'])} 开始）原样留着；"
                               f"已经连着失败 {segment.get('handoff_failures', 0)} 次")
        return False
    segment["messages"] = _body(session)
    name = _archive(turn, segment, reason)
    _roles._stream.info(f"交接完成（{reason}），归档为 {name}")
    turn.handoff_note = f"你刚把上一段上下文交接掉了（原因：{reason}）。接着做刚才的事，做到哪儿见 recent。"
    return True


def _forced_reason(segment: dict, threshold: int) -> str:
    """Why this segment must be archived without a handoff, or empty.

    WHY: 维护者 2026-10-23：整理阈值设在模型上下文的一半左右，正常不会走到这里；真走到了
    （交接连着失败，或者这一段已经是阈值的两倍、连交接请求本身都发不出去），就别卡死，
    原样归档、告诉她，由她自己去读那一段补笔记。
    """
    failures = segment.get("handoff_failures", 0)
    if failures >= _FORCE_AFTER_FAILURES:
        return f"交接连着失败了 {failures} 次"
    used = _context_size(segment.get("tokens"), segment["messages"])
    if used > threshold * _FORCE_OVER_THRESHOLD:
        return f"已经长到 {used} token，超过整理阈值的 {_FORCE_OVER_THRESHOLD} 倍"
    return ""


def _archive(turn, segment: dict, reason: str) -> str:
    segment["reason"] = reason
    name = _segment.archive(segment)
    turn.handed_off = reason
    return name


def _system_count(session: llm.Chat) -> int:
    """How many leading messages are the system prompt rebuilt on every wake.

    WHY: 2026-10-23 起这一段里也有 system 消息（系统自己说的话），不能再按"第一条不是
    system 的消息"找边界，否则这一段开头的笔记会被当成系统提示、存的时候丢掉。
    """
    return session.prompt_count


def _begin(turn, window: tuple | None, kind: str) -> None:
    turn._chat_usage_tokens = 0
    turn.requested_reads = []
    turn.associated_windows = {window} if window is not None else set()
    turn.screens = {}
    turn.authors = []
    turn.release = ""
    turn.handed_off = ""
    turn.handoff_note = ""
    turn.handoff_failed = False
    turn.handoffs = 0
    turn.settling = False
    # 输入框（type_word）跟着这次唤醒走：中途交接换了会话也还在，唤醒结束就没了。
    turn.boxes = {}
    turn.log = {"kind": kind, "start": time.time(), "peak": 0}


def _keep_typing(turn, window: tuple | None, stop: threading.Event) -> None:
    """Show "对方正在输入…" in the private chats she is working on until the wake ends.

    WHY: 维护者 2026-10-22 想要循环进行时有"正在输入"。NapCat 的 set_input_status 只支持
    私聊（C2C），QQ 群没有这个状态。没有明确的"取消"调用，所以隔几秒续一次，唤醒结束
    就不再续。失败只记一次，不影响她干活。
    """
    from mods import connect

    failed = False
    while not stop.is_set():
        try:
            targets = {window} if window is not None and window[0] == "private" else set()
            targets.update(_reader.parse_target(label) for label in tuple(turn.screens) if label.startswith("u"))
            # 潜意识整理时她已经忙完了，对方看到"正在输入"会以为她还有话。
            for target in (() if turn.settling else targets):
                reply = connect.call_api("set_input_status", user_id=str(target[1]), event_type=1)
                if reply.get("retcode") not in (0, None) and not failed:
                    failed = True
                    _roles._stream.warning(f"设置正在输入失败：{reply.get('wording') or reply.get('retcode')}")
        except Exception:
            if not failed:
                failed = True
                _roles._stream.exception("设置正在输入失败")
        stop.wait(_TYPING_EVERY)


def _drive_agent(model: str | None, window: tuple | None) -> bool:
    """Hold the agent's turn until nothing is left to do; False when someone else holds it."""
    turn, owner = context.begin_turn(_chat_root.AGENT_WINDOW)
    if not owner:
        return False
    _begin(turn, window, "闹钟" if window == _windows.ME else "唤醒")
    typing = threading.Event()
    threading.Thread(target=_keep_typing, args=(turn, window, typing), name="chat-typing", daemon=True).start()
    # WHY: 一次对话 = 这次持有的全过程（多轮 + 插话续写，直到 finally），图片检查台账就
    # 活在这段里：同一张图不重复下载/解析，对话结束即清掉，下次再聊重新检查一遍。
    image_ledger = image.begin_conversation()
    origin = context.current()
    try:
        while True:
            context.set_current(None)
            context.set_agent_mode(True)
            try:
                if not _run_agent(model, turn):
                    return True
                if turn.release and not turn.cancelled:
                    # 她叫了 tidy，或者这一段涨过阈值：先交接，再用新的一段接着干。
                    if not turn.handoff_failed:
                        _handoff(turn, turn.release, model)
                    turn.release = ""
                    continue
            finally:
                context.set_agent_mode(False)
                context.set_current(origin)
            if turn.cancelled:
                return True
            turn.associated_windows.intersection_update(
                {window for window, _count, active in oplog.pending_summary() if active})
            # 先排下一轮循环：它隔几秒才到，潜意识照样先整理一遍（维护者 2026-10-24：长时间运行
            # 恰恰不能停了整理），整理期间到的那一轮由下面 finish_turn 接上。
            _next_loop()
            if _subconscious.on() and not oplog.has_unnotified():
                # 她这阵忙完了才轮到潜意识，不挡她的路。它占着同一个 turn，不和她抢这一段；
                # 期间来的召唤照常登记，下面 finish_turn 看到了就接着处理。
                turn.settling = True
                _subconscious.settle(model, turn)
                turn.settling = False
            if not context.finish_turn(_chat_root.AGENT_WINDOW, turn, oplog.has_unnotified):
                return True
    finally:
        if turn.cancelled:
            # 被叫停的那一轮，循环也一起停：维护者 ^C 就是要她停下。
            stop_loop("这一轮被叫停了")
        typing.set()
        image.end_conversation(image_ledger)
        context.end_turn(_chat_root.AGENT_WINDOW, turn)
        # WHY: 循环停下的唯一信号就在这里，见 _run_hint。`#hint` 只有 QQ 窗口有。
        if origin is not None and window is not None and window[0] in _windows.QQ:
            _chat_root._run_hint(window, turn)


def start_loop(note: str) -> dict:
    """Wake her again after every wake until she stops it; returns the loop.

    WHY: 维护者 2026-10-24：她玩游戏时常常不记得接着做，一轮结束会话就停了。循环开着时每轮结束
    隔 `_LOOP_GAP` 秒，往她自己的 me 窗口推一条"循环第几轮：在做什么"叫醒她，直到她想起来关掉。
    不设时长（维护者：不知道什么时候需要停下来）；没人干预就一直转到夜里 4 点整理（`reflect`
    开头关掉它）。维护者叫停她那一轮时也一起停。
    """
    global _loop
    rounds = _loop["round"] if _loop else 0
    _loop = {"note": note, "round": rounds, "pending": False, "token": 0}
    return dict(_loop)


def stop_loop(reason: str) -> dict | None:
    """Stop the loop; returns what it was, or None when none was running."""
    global _loop
    stopped, _loop = _loop, None
    if stopped:
        _roles._stream.info("循环关了（转了 %d 轮）：%s", stopped["round"], reason)
    return stopped


def wait_loop(seconds: float) -> dict | None:
    """Called by the `wait` tool: the next round comes *seconds* from now instead of right after this wake.

    WHY: 维护者 2026-10-24：循环开着时 wait 不阻塞，就是"多久之后叫醒我"。要等别人回、等东西加载、
    等体力恢复时她用它，不必为了等一会儿把循环关掉、再记着重开。已经排上的那一轮作废（换个排号），
    这次唤醒结束时按 `due` 重排；期间来了消息照样叫醒她，那次唤醒结束也还是按 `due` 排。
    """
    loop = _loop
    if loop is None:
        return None
    loop["due"], loop["waited"] = time.time() + seconds, seconds
    loop["token"] += 1
    loop["pending"] = False
    return dict(loop)


def _next_loop() -> None:
    """At the end of a wake: queue the next loop round."""
    loop = _loop
    if loop is None or loop["pending"]:
        return
    loop["pending"] = True
    loop["token"] += 1
    delay = max(_LOOP_GAP, loop.get("due", 0) - time.time())
    threading.Timer(delay, _loop_round, args=(loop, loop["token"])).start()


def _loop_round(loop: dict, token: int) -> None:
    if _loop is not loop or loop["token"] != token:
        return  # 这期间她关掉、重开了，或者 wait 换了时间
    loop["pending"] = False
    loop.pop("due", None)
    waited = loop.pop("waited", None)
    loop["round"] += 1
    after = f"（等了 {_span(waited)}）" if waited else ""
    try:
        _chat_root.push(_windows.ME, "循环", f"循环第 {loop['round']} 轮{after}：{loop['note']}",
                        wake=f"循环第 {loop['round']} 轮{after}：接着做——{loop['note']}；"
                             "要等一会儿就 wait(秒数)，到时候再叫你；这件事彻底不做了才 loop(stop=True)")
    except Exception:
        _roles._stream.exception("循环叫醒她失败，循环停了")
        stop_loop("叫醒失败")


def _span(seconds: float) -> str:
    if seconds >= 3600:
        return f"{seconds / 3600:g} 小时"
    if seconds >= 60:
        return f"{seconds / 60:g} 分钟"
    return f"{seconds:g} 秒"


def request_tidy(note: str) -> bool:
    """Called by the `tidy` tool: hand off this segment once this round ends."""
    turn = context.get_turn(_chat_root.AGENT_WINDOW)
    if turn is None or not hasattr(turn, "release"):
        return False
    turn.release = note or "她自己要求交接"
    return True


def _run_agent(model: str | None, turn) -> bool:
    max_events, _threshold = _chat_root.limit()
    notice = oplog.deliver_notifications(_chat_root.AGENT_WINDOW)
    woken, activation_lines, authors = _woken(notice, turn)
    turn.authors = list(dict.fromkeys([*turn.authors, *authors]))
    continuing = turn.handed_off
    if notice is None and not woken and not continuing:
        return True
    segment = _segment.load()
    if _did_something(segment["messages"]) and not turn.handoff_failed:
        reason = _tidy_reason(turn, segment, "assemble", _context_size(segment.get("tokens"), segment["messages"]))
        if reason:
            if _handoff(turn, reason, model):
                continuing = turn.handed_off
            # 没交接成功时文件里多了失败次数，也要重读，否则这次唤醒的保存会把它盖掉。
            segment = _segment.load()
    if _chat_root.get_reasoning_mode() == "drop":
        # 以前几次唤醒的思考不再带回；这次唤醒之内照旧原样保留（DeepSeek 带工具时要求）。
        for message in segment["messages"]:
            if not _view.is_incoming(message):
                message.pop("reasoning_content", None)
    material: list[dict] = []
    if not segment["messages"]:
        material.extend(_head(woken, segment))
    # 交接后续做时没有新唤醒，沿用刚才看着的窗口。
    focus = woken or _windows.parse_all(turn.screens)
    # 记成"这一段出现过"要等第一次输出，和画面位置一样：dismiss 退回去时笔记也跟着回来。
    people, met = _people(focus, turn.authors, segment)
    material.extend(people)
    screens: dict[str, str] = {}
    newest: dict[tuple, tuple] = {}
    shown_last: dict[str, str] = {}
    for window in focus:
        label = _screen.label(window)
        text, mark, last = _screen_text(window, max_events, segment.setdefault("last_shown", {}).get(label))
        newest[window] = mark
        if text:
            screens[label] = text
            shown_last[label] = last
            material.append(_view.outside(label, ["画面", _screen.clock(), *_chat_root._awaker.badges(window)], text))
    now = _screen.clock()
    if notice is not None:
        material.append({"role": "system", "content": f"这次唤醒你的是（{now}）：\n" + "\n".join(activation_lines)})
    if segment.get("notice"):
        material.append({"role": "system", "content": segment["notice"]})
    if continuing:
        if turn.handoff_note:
            material.append({"role": "system", "content": turn.handoff_note})
        if turn.boxes:
            material.append({"role": "system", "content": _box_note(turn.boxes)})
        turn.handed_off = ""
    if not material:
        return True
    mark = ({"at": now, "time": time.time(), "index": len(segment["messages"]),
             **{key: json.loads(json.dumps(segment.get(key), ensure_ascii=False)) for key in _MARKED},
             "boxes": {label: box["text"] for label, box in turn.boxes.items() if box.get("text")},
             "seen": dict(_seen())}
            if notice is not None else None)

    def first_output() -> None:
        started.append(True)
        if notice is not None:
            oplog.acknowledge_notification(notice["id"])
        if mark is not None:
            segment["marks"] = [*segment.get("marks", [])[-(_MARKS - 1):], mark]
        _mark_seen(newest)
        turn.screens.update(screens)
        segment["windows"] = list(dict.fromkeys([*segment["windows"], *screens]))
        segment["scopes"].extend(met)
        segment["last_shown"].update(shown_last)
        segment.pop("notice", None)

    def stop_when(used: int, initial: int) -> bool:
        if turn.release:
            return True
        reason = _tidy_reason(turn, segment, "round", used, used - initial)
        if reason:
            turn.release = reason
            return True
        return False

    started: list[bool] = []
    unconfirmed: dict[tuple, tuple] = {}

    def late() -> list[dict]:
        # 第一次输出之前画面还没标已读，刚到的留给下一次请求一起给。
        if not started:
            return []
        added, marks = _late_arrivals(newest, segment["last_shown"], max_events)
        unconfirmed.update(marks)
        return added

    def seen_late() -> None:
        if unconfirmed:
            _mark_seen(dict(unconfirmed))
            unconfirmed.clear()

    session = _run_session(model, turn, segment, [*segment["messages"], *material], first_output, stop_when,
                           providers=[late], hints=[lambda: _unread_hint(set(newest))],
                           known=len(segment["messages"]), each_output=seen_late)
    if getattr(session, "dismiss", None) is not None:
        # 工具里查过一次；同一次输出里排在它后面的 send、type_word 可能又改了情况，这里再查。
        problem = _dismiss_problem(session, session.dismiss)
        if problem:
            _roles._stream.warning(f"dismiss 没有生效：{problem}")
        else:
            _drop_since(segment, session.dismiss)
    return session.output_recorded


def request_dismiss(session, since: str = "") -> str:
    """Called by the `dismiss` tool: drop everything since a wake header once this round ends; returns why not, or "".

    WHY: 维护者 2026-10-23：follow 一个群她看起来很累。这一段里一遍遍叠着"被叫醒、看一眼、
    没我的事"，她照着这一路往下演，累的是这些痕迹，不是叫醒的次数（先试过攒一批再叫，晚了接不上话，
    痕迹照样留着，撤了）。现在没什么要做的就清掉：从她点名的那行「这次唤醒你的是」起（不写就是
    最近一行），画面、插话、思考、工具调用和结果都不留。赌的是她想的时候会有插话：插话照样叫醒她，
    画面从清掉之前接着给，被清掉的消息和新的连在一起，等于她想的那段时间替她攒了一批。

    WHY: 调过工具也信她：清掉的只是眼前的工作记忆，笔记是文件、行动记录和 llm.log 都还在，
    和交接时由她决定留什么是同一种信任。只拦两种：那以后她发过话（别人多半在接她的话，她得记得
    当时为什么这么说；清了只剩聊天记录里那句话）；输入框里有那以后打的、改过的字（本来就有的不拦，
    维护者说框里的字可能是早先打的）。不跨交接：记号跟着这一段，交接后从新的一段算。

    WHY: 维护者 2026-10-24：follow 期间到的消息画面不截，一直 dismiss 就会越攒越多。兜底是思考：
    一个跟着的窗口里，会被退回去的 follow 期间消息超过 _FRESH_MAX 条，就不让清，她把这批看完留下，
    画面位置跟着往前推；不想跟了就揭掉 follow。
    """
    if getattr(session, "quiet", False) or getattr(session, "segment", None) is None:
        return "只有醒着处理消息的时候能用；交接、反思和子代理里没有可清的"
    marks = session.segment.get("marks") or []
    since = " ".join(str(since).split())
    found = [mark for mark in marks if not since or since in (mark["at"], mark["at"][11:])]
    if not found:
        recent = "、".join(mark["at"] for mark in marks[-5:]) or "没有"
        return f"这一段里找不到「这次唤醒你的是（{since}）」那一行；能清回的最近几次是：{recent}"
    problem = _dismiss_problem(session, found[-1])
    if problem:
        return problem
    session.dismiss = found[-1]
    return ""


def _shown_since(segment: dict, mark: dict) -> list[str]:
    """Labels of the windows shown after *mark*: their screen position moved on since."""
    return [label for label, last in segment.get("last_shown", {}).items() if mark["last_shown"].get(label) != last]


def _piled(window: tuple, mark: dict) -> int:
    """Follow-time lines of *window* that would come back unread if the stretch since *mark* went."""
    layer = _chat_root._awaker.layer(window)
    since = layer.get("since") if layer is not None and layer["state"] == "follow" else None
    if since is None:
        return 0
    seen = mark.get("seen", {}).get(_screen.label(window))
    return sum(1 for item in _screen.records(window, 1_000_000, since=since)
               if item["time"] is not None and item["time"] >= int(since) and (seen is None or item["time"] > seen))


def _dismiss_problem(session, mark: dict) -> str:
    if (getattr(session, "last_sent", 0) or 0) >= mark["time"]:
        return f"从 {mark['at']} 起你发过话，这段不能清：别人多半在接你的话，你得记得当时为什么这么说"
    for window in _windows.parse_all(_shown_since(session.segment, mark)):
        piled = _piled(window, mark)
        if piled > _FRESH_MAX:
            label = _screen.label(window)
            return (f"{label} 从 {mark['at']} 起 follow 期间已经攒了 {piled} 条，超过 {_FRESH_MAX} 条就不能再清："
                    f"这次留着，把它们看完；不想跟了就 wake_me(\"follow {label} off\")")
    typed = [label for label, box in session.boxes.items()
             if box.get("text") and box["text"] != mark["boxes"].get(label)]
    if typed:
        return (f"{'、'.join(typed)} 的输入框里有 {mark['at']} 之后打的或改过的字，清掉以后你会不记得打过；"
                "先 send，或者用 type_word 改回原样或清空，再 dismiss")
    return ""


def _restore(segment: dict, mark: dict, keys) -> None:
    for key in keys:
        if key not in mark:
            continue  # 早先记的记号没有 scopes
        if mark[key] is None:
            segment.pop(key, None)
        else:
            segment[key] = mark[key]
    segment["marks"] = [kept for kept in segment.get("marks", []) if kept["time"] < mark["time"]]


def _drop_since(segment: dict, mark: dict) -> None:
    """Put the segment back as it was before *mark*'s wake; what was shown since comes back on the next screen."""
    shown = _shown_since(segment, mark)
    segment["messages"] = segment["messages"][:mark["index"]]
    _restore(segment, mark, _MARKED)
    _segment.save(segment)
    # 画面上的"看到哪"也退回去：清掉的消息下次排在"以下是上次之后的新消息"下面，当没读过的给。
    # oplog 的已读不退（日志里没有"标回未读"这种行），画面外那个窗口的未读条数因此不算它们。
    seen = _seen()
    for label in shown:
        if mark["seen"].get(label) is None:
            seen.pop(label, None)
        else:
            seen[label] = mark["seen"][label]
    storage.save()
    _chat_root._awaker.dismissed(_windows.parse_all(shown))


def _fold_since(segment: dict, mark: dict, label: str) -> str:
    """Replace the stretch since *mark* with one line naming where it was archived whole; returns that name.

    Unlike a dismiss, what was shown stays read: the screen position does not move back.
    """
    shown = _shown_since(segment, mark)
    name = _segment.archive({"started": mark["time"], "windows": shown, "reason": label,
                             "messages": segment["messages"][mark["index"]:]}, "folded")
    where = f"{'、'.join(shown)} 的画面和" if shown else ""
    segment["messages"] = [*segment["messages"][:mark["index"]], {"role": "system", "content": (
        f"【收起】{label}\n（{mark['at'][5:]} 至 {_screen.clock()[5:]}，{where}你那时的想法、调用，后台整理时收成了上面这一句；"
        f"原样存在归档段 {name}，加载 memory_system 用 read_context(\"{name}\") 读，search 也搜得到）")}]
    # 画面位置不退。第一次给的笔记、中途的工具状态跟着收走了，这几项退回去，下次照样给。
    _restore(segment, mark, ("scopes", "told", "tokens", "notice"))
    _segment.save(segment)
    return name


def _fold_between(segment: dict, mark: dict, after: dict, label: str) -> str:
    """Replace the wakes from *mark* up to (not including) *after* with one line; what follows stays as it was.

    WHY: 维护者 2026-10-24：潜意识动不了段中间不太好，本以为是像操作数据一样操作上下文。中间收掉一块，
    那之后她那条下一次请求要按原价重读（缓存只认前面一样的部分），偶尔一次只是几分钱，贵的是每轮都改
    中间；所以潜意识看得到每处之后有多少 token，值不值由它按说明判断，代码不设门槛。

    和收到最后不同，后面的唤醒还在：它们的记号下标往前挪，区间里第一次给的笔记从这一段和后面的记号里
    一起退掉（下次再给），工具状态退回区间开头那时的（下次把变化重新说一遍，重复无妨）。区间不含这一段
    第一次唤醒，开头那条交接留下的 notice 不会在里面。
    """
    start, end = mark["index"], after["index"]
    removed = segment["messages"][start:end]
    shown = [window for window, last in after["last_shown"].items() if mark["last_shown"].get(window) != last]
    name = _segment.archive({"started": mark["time"], "windows": shown, "reason": label, "messages": removed}, "folded")
    where = f"{'、'.join(shown)} 的画面和" if shown else ""
    line = {"role": "system", "content": (
        f"【收起】{label}\n（{mark['at'][5:]} 起到 {after['at'][5:]} 之前，{where}你那时的想法、调用，后台整理时收成了"
        f"上面这一句；原样存在归档段 {name}，加载 memory_system 用 read_context(\"{name}\") 读，search 也搜得到）")}
    segment["messages"] = [*segment["messages"][:start], line, *segment["messages"][end:]]
    shift = 1 - len(removed)
    saved = _text_tokens(removed) - _text_tokens([line])
    given = [scope for scope in after.get("scopes") or [] if scope not in (mark.get("scopes") or [])]

    def moved(reported):
        # 接口报的数若是在这一块之后报的，扣掉这一块的估算；报在这一块里面的已经对不上了，改回按字数估。
        if not (isinstance(reported, list) and len(reported) == 2) or reported[1] <= start:
            return reported
        return [reported[0] - saved, reported[1] + shift] if reported[1] >= end else None

    segment["scopes"] = [scope for scope in segment.get("scopes") or [] if scope not in given]
    segment["tokens"] = moved(segment.get("tokens"))
    if "told" in mark:
        segment["told"] = mark["told"]
    marks = []
    for other in segment.get("marks", []):
        if other["time"] < mark["time"]:
            marks.append(other)
        elif other["time"] >= after["time"]:
            other = {**other, "index": other["index"] + shift, "tokens": moved(other.get("tokens"))}
            if "scopes" in other:
                other["scopes"] = [scope for scope in other["scopes"] or [] if scope not in given]
            if "told" in mark:
                other["told"] = mark["told"]
            marks.append(other)
    segment["marks"] = marks
    _segment.save(segment)
    return name


def _body(session: llm.Chat) -> list[dict]:
    """Everything after the system prompt, which is rebuilt on every wake."""
    return session.messages[_system_count(session):]


def _run_session(model: str | None, turn, segment: dict, messages: list[dict], first_output, stop_when,
                 *, providers=(), hints=(), load: tuple[str, ...] = (), quiet: bool = False, persist: bool = True,
                 fail_fast: bool = False, known: int = 0, each_output=None) -> llm.Chat:
    """One model session over the segment plus this wake's material; outputs and results go to the oplog.

    With *persist*, the segment file follows the session after every round, but only
    once she has produced output: a request that fails before that leaves the file
    as it was, so the same material is not appended twice.  The first *known* of
    *messages* are already in the segment, so the request log skips them.
    """
    session = llm.Chat(model=model or _chat_root.get_model(), chat_client=llm.get_client())
    session.requested_reads = turn.requested_reads
    session.associated_windows = turn.associated_windows
    session.boxes = turn.boxes
    session.segment = segment
    session.last_sent = segment.get("last_sent", 0.0)
    session.quiet = quiet
    session.fail_fast = fail_fast
    binding = _chat_root._activate_chat(session, messages)
    session.prompt_count = len(session.messages) - len(messages)
    # WHY: 维护者 2026-10-23：别人说的话放 user，系统自己说的话（唤醒原因、笔记、交接、工具
    # 状态、末尾提示）放对话中间的 system。DeepSeek 官方文档说 Chat Completions 支持在对话
    # 中间插 system、不支持插工具调用，所以先前那对合成的 incoming 工具调用换成了这样；实测
    # （仓库外的脚本）对话中间的 system 在思考模式、带工具、前面的思考被删掉时都被接受，四个
    # 行为任务上不比合成框差。
    session.hint_role = "system"
    if load:
        binding.load(list(load))
    binding.sync_registry()
    told = [segment.get("told")]
    if told[0] is None:
        # 新的一段：完整工具状态放在系统提示之后，这一段里不再变。
        state = binding.state_text(None)
        if state:
            session.messages.insert(session.prompt_count, {"role": "system", "content": state})
        told[0] = binding.state_snapshot()
    # WHY: 请求日志以前每次唤醒都从头打印，同一段越长、醒得越勤，llm.log 里同样的前缀就重复
    # 越多遍（2026-10-23 审查）。这一段已有的部分在 current.json 和归档里都有原样；新的一段
    # 从头打印，系统提示因此每段记一次。
    session.logged = _system_count(session) + known if known else 0

    def tool_changes() -> list[dict]:
        binding.sync_registry()
        current = binding.state_snapshot()
        if current == told[0]:
            return []
        text = binding.state_text(told[0])
        told[0] = current
        return [{"role": "system", "content": text}] if text else []
    # WHY: 原生 assistant/tool 配对原样留在 messages（含思考，DeepSeek 带工具时要求回传），
    # 并随这一段一起存下：下次唤醒看到的就是这一路下来的原样。
    session.preserve_native = True
    session.keep_reasoning = True
    session.output_recorded = False
    session.last_usage = None
    reported = [segment.get("tokens")]
    initial = _context_size(reported[0], _body(session))
    turn._chat_usage_tokens = initial
    turn.log["peak"] = max(turn.log["peak"], initial)

    def save() -> None:
        if persist and session.output_recorded:
            segment["messages"] = _body(session)
            segment["told"] = told[0]
            if session.last_sent:
                segment["last_sent"] = session.last_sent
            _segment.save(segment)

    def record_output(assistant, calls):
        if session.last_usage:
            # 报的是刚发出去的整份请求加这次回答；回答在这之后才追加进 messages。
            reported[0] = [session.last_usage, len(_body(session)) + 1]
            session.last_usage = None
            if persist:
                segment["tokens"] = reported[0]
        source = oplog.output(_chat_root.AGENT_WINDOW, assistant, calls)
        if not session.output_recorded:
            first_output()
        session.output_recorded = True
        if each_output is not None:
            each_output()
        return source

    def record_results(source, results, _native):
        for result in results:
            binding.touch(result.name)
        if results:
            oplog.result(_chat_root.AGENT_WINDOW, source, [
                {"position": position, "name": result.name, "arguments": result.arguments,
                 "content": result.content, "tool_call_id": result.tool_call_id}
                for position, result in enumerate(results)], "")

    def turn_done() -> bool:
        used = _context_size(reported[0], _body(session))
        turn._chat_usage_tokens = used
        turn.log["peak"] = max(turn.log["peak"], used)
        save()
        return stop_when(used, initial)

    session.on_output = record_output
    session.on_results = record_results
    session.turn_done = turn_done
    session.add_context_provider(tool_changes)
    for provider in providers:
        session.add_context_provider(provider)
    for hint in hints:
        session.add_hint(hint)
    session.add_hint(lambda: f"现在是 {_screen.clock()}。")
    session.should_stop = lambda: turn.cancelled
    session.chat(recall_func=_chat_root.get_handler(session), description_cache=_chat_root.description_cache)
    save()
    return session


# WHY: 维护者 2026-10-23 让参照 Claude Code 的 retro（会话复盘）改：问题不止记忆一类，证据只认
# 原始记录，改动要能核对，提案要写成拿到就能动手的样子。起因是当天她调 attach_image 只看到一行
# 地址（其实是 #image off 的 bug），便认定"这只眼睛不支持看图"——只有"没记下／没想起来／交接漏了"
# 三类时，环境坏了会被当成对自己的认识记下来。各类问题的说明在 memory_system 里，这里只点名。
_REFLECT_PROMPT = """现在是每晚反思的时间（{now}）。没有人叫你，这次不要对外说话。

像人排查你的问题那样排查自己：
1. 从症状出发：上面列出了上次反思以来归档的每一段上下文。有人说你忘了、找东西没找到、答错了、交接漏了；工具报错、白跑一趟、同一件事来回调了很多次——先找到这样的地方。没有线索时，挑一两段读一读。
2. 顺着原始记录查：read_context 读那一段当时的原样（你当时看到了什么、做了什么）；search 一次找遍聊天记录、笔记、日记和归档；read_log 读聊天原文；read_actions 读行动记录；note_history 看当时笔记里写了什么。日记、recent 和交接说明是你事后写的总结，只当线索、不当证据；结论要指到原始记录的具体位置，读不到的就写明读不到。
3. 分清是哪一类问题，每类该改的地方不同（memory_system 里有说明）：没记下、记下了没想起来、交接漏了；不知道该去哪找；同一种失误反复出现；判断错了；笔记或技能说明过时、冲突、太长；花得太多；还是环境本身坏了。环境坏了不是你做不到：别把它记成对自己的认识，写进 proposals。
4. 先评估：changes 里还没有评估结论的改动，对照它之后的记录，问题少了吗？用 assess_change 写下结论；没用的就改回去。
5. 找到原因再改，一次最多一两处：能改已有的那一条就别新加一条，只出现过一次的失误不写成长期规则。用 edit_policy 写明原因和预期：交接的附加说明（handoff）、你的做法（global）、什么时候交接（tidy）、你对自己的认识（self，只有现在能改，大多数晚上不需要动它）。机制本身要改就写进 proposals 交给维护者，格式见 memory_system。
6. 没发现问题就什么都不改，直接结束。"""


def _tail(text: str, lines: int) -> str:
    return "\n".join(text.strip().split("\n")[-lines:]) if text.strip() else ""


def reflect(model: str | None = None) -> bool:
    """The nightly pass: hand off the day's segment, roll up diaries, then look back.  False when busy.

    WHY: 维护者 2026-10-22：她要像人排查她那样排查自己——从症状出发，顺着原始记录
    （归档的上下文、聊天记录、行动记录、笔记的历史版本）查原因，再决定改不改。
    占住 AGENT_WINDOW，期间来的召唤照常登记，结束后由 pick_up_pending 接上。
    """
    # 她开着循环时几乎一直在忙，先关掉，这一轮做完反思就接得上（awaker 下一分钟再来）。
    stop_loop("夜里整理")
    turn, owner = context.begin_turn(_chat_root.AGENT_WINDOW)
    if not owner:
        return False
    _begin(turn, None, "反思")
    data = storage.get("", "agent")
    last = data.get("reflected_at")
    image_ledger = image.begin_conversation()
    try:
        context.set_current(None)
        context.set_agent_mode(True)
        try:
            _handoff(turn, "夜里了", model)
            _roll_up(diary_day(turn.log["start"]))
            segments = _segment.archived(last if isinstance(last, (int, float)) else 0)
            # 收起时另存的片段和潜意识自己的那条不算她的段：片段在她的段里有记号指着。
            segments = [item for item in segments
                        if not any(f"-{kind}" in item["name"] for kind in ("reflect", "folded", "subconscious"))]
            if segments:
                _run_reflection(model, turn, segments)
        finally:
            context.set_agent_mode(False)
        data["reflected_at"] = turn.log["start"]
        storage.save()
    finally:
        image.end_conversation(image_ledger)
        context.end_turn(_chat_root.AGENT_WINDOW, turn)
    pick_up_pending()
    return True


def _roll_up(day: str) -> None:
    """On a month's or a year's last night, rewrite its summary from the diaries it covers."""
    following = time.localtime(time.mktime(time.strptime(day, "%Y-%m-%d")) + 36 * 3600)
    periods = []
    if following.tm_mday == 1:
        periods.append(day[:7])
        if following.tm_mon == 1:
            periods.append(day[:4])
    for period in periods:
        try:
            _roles.summarize(period)
        except Exception:
            _roles._stream.exception(f"汇总 {period} 失败")


def _run_reflection(model: str | None, turn, segments: list[dict]) -> None:
    _max_events, threshold = _chat_root.limit()
    notes = [text for scope in ("global", "recent") if (text := _notes.render(scope))]
    listing = "\n".join(
        f"{item['name']}  {_screen.clock(item['started'])[5:16]} 至 {_screen.clock(item['ended'])[5:16]}  "
        f"{item['messages']} 条消息  窗口 {'、'.join(item['windows']) or '无'}"
        + (f"  {item['reason']}" if item.get("reason") else "") for item in segments)
    changes = _tail(_notes.read_policy("changes"), 40)
    proposals = _tail(_notes.read_policy("proposals"), 20)
    messages = [
        {"role": "system", "content": "以下是你的笔记：\n\n" + ("\n\n".join(notes) or "（还没有）")},
        {"role": "system", "content": "## 上次反思以来归档的上下文（用 read_context 读原样）\n" + listing},
        {"role": "system", "content": "## 策略改动记录（changes，最近的）\n" + (changes or "（还没有）")
         + "\n\n## 给维护者的提案（proposals，最近的）\n" + (proposals or "（还没有）")},
        {"role": "system", "content": _REFLECT_PROMPT.format(now=_screen.clock())},
    ]
    reflection = _segment.fresh()
    # 反思材料本身可能就很大：只限制它之后长出来的部分。
    session = _run_session(model, turn, reflection, messages, lambda: None,
                           lambda used, initial: used - initial > threshold,
                           load=("memory_system",), quiet=True, persist=False)
    # 反思这一段也留下来，排查"她昨晚为什么改了这个"时要看。
    reflection["messages"] = _body(session)
    _segment.archive(reflection, "reflect")


def pick_up_pending() -> None:
    """Start a wake for calls that arrived while nobody was there to take them."""
    if not oplog.has_unnotified():
        return
    window = next(iter(oplog.unacknowledged_windows()), None)
    if window is None:
        window = next((window for window, _count, active in oplog.pending_summary()
                       if active and _windows.known(window)), None)
    if window is not None:
        _drive_agent(None, window)
