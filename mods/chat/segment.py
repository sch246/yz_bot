"""Numbered working views over immutable journal rows.

Persisted view: {ctx: str, started: float, items: list[str], stats: dict}.
Only stats that cannot be derived belong here (tokens, cost, pressure counters,
reason, ended). No messages/marks/scopes/told/last_shown mirrors are stored.

append/begin_wake/begin_act/tool_started/record_results default to persisting
main view.json. A fork passes persist=False, keeps its references in memory,
and finally archive(view, "fork"); it cannot replace the main current view.
All frozen views use views/<ctx>.json; kind is metadata, never part of identity.
Settle runs, reflection and old central handoffs enter the years.md index.
Only a wake owned by this view's ctx is inherited by newly appended entries.

Source metadata on journal rows:
* wake: number (allocated from all ctx wakes, even dropped ones), at: str,
  ctx_started: float (bounds allocation to this context's shard days),
  boxes: {window: text}, read_snapshot: opaque screen.read_snapshot() dict.
* screen: window: str, shown: list[record identity], read_patch: opaque dict,
  follow_ids: list[identity], wake_id: str.
* note: scope: str, version: str|float, name: str, wake_id: str.
  Imported combined note messages instead use note_sources:
  {scope: {version, name}}; each source maps to the same unchanged row.
* system: tool_state: dict when it announces tools; wake_id: str.
* assistant: wake_id: str, step: int (allocated from all tool-call acts in that wake).
* fold: covers: list[item id], summary: str, level: int.
* drop: covers: list[item id], wake_id: str; carries no rendered message.

``derive`` returns {notes:{scope:{id,version,name}}, screens:[screen rows],
shown:{window:[record id]}, windows:[label], tool_state:dict|None,
wakes:[{id,number,at,time,items:[visible refs]}],
steps:[{id:"n.m",wake:int,sub:int,act_id,items:[refs],tools:[name],complete:bool}],
folds:[fold rows]}. Folded rows retain screens/shown but hide notes/tool state;
re-showing those is the caller's ordinary source comparison, not another mirror.

Each run owns one view (stateless core, 2026-11-01): the caller passes
persist=False and run() freezes it with archive(view, kind) at the end.
view.json/load/save remain only for migrated installs and old tooling.

WHY: 2026-11-01 维护者推翻 D20（不交接就一直是同一段上下文）：每次运行只为一个目的，
视图随运行冻结，不再折叠、清除或交接。迁移来的旧视图里仍有 fold/drop 行，渲染、derive
和 read_context 照旧能读，所以这里保留读取它们的代码，只删掉了创建它们的接口。
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
import uuid

import mods.chat as _chat_root
from . import journal

ROOT = Path("data/chat")
_YEARS = Path("data/notes/years.md")
_lock = threading.RLock()


def fresh(*, ctx=None, started=None) -> dict:
    stamp = time.time() if started is None else started
    return {"ctx": ctx or time.strftime("%Y%m%d-%H%M%S", time.localtime(stamp)) + "-" + uuid.uuid4().hex[:8],
            "started": stamp, "items": [], "stats": {}}


def _validate(view):
    if not isinstance(view, dict) or set(view) != {"ctx", "started", "items", "stats"}:
        raise ValueError("view must contain ctx, started, items, stats only")
    if not isinstance(view["ctx"], str) or not re.fullmatch(r"[0-9A-Za-z_-]+", view["ctx"]):
        raise ValueError("invalid view ctx")
    if not isinstance(view["items"], list) or not isinstance(view["stats"], dict):
        raise ValueError("invalid view items/stats")
    if len(set(view["items"])) != len(view["items"]):
        raise ValueError("duplicate view references")
    _entries(view, deep=True)


def _atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("x", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temp.unlink(missing_ok=True)


def save(view):
    with _lock:
        _validate(view)
        current, backup = ROOT / "view.json", ROOT / "view.bak.json"
        if current.is_file():
            # WHY: keep a known-valid prior view before replacing references, so a
            # torn/manual invalid current file cannot erase the only usable view.
            previous = json.loads(current.read_text(encoding="utf-8"))
            _validate(previous)
            _atomic(backup, previous)
        _atomic(current, view)


def load():
    with _lock:
        current, backup = ROOT / "view.json", ROOT / "view.bak.json"
        if not current.exists():
            return fresh()
        try:
            view = json.loads(current.read_text(encoding="utf-8"))
            _validate(view)
            return view
        except (OSError, ValueError, KeyError, TypeError):
            # Do not silently manufacture an empty context over damaged data.
            if not backup.is_file():
                raise ValueError("current view is damaged and has no valid backup")
            restored = json.loads(backup.read_text(encoding="utf-8"))
            _validate(restored)
            broken = current.with_name("view.broken-" + uuid.uuid4().hex + ".json")
            os.replace(current, broken)
            _atomic(current, restored)
            return restored


def _entries(view, *, deep=False):
    def visit(ids, trail):
        found = []
        for item_id in ids:
            if item_id in trail:
                raise ValueError("cyclic fold references")
            row = journal.get(item_id)
            if deep and row["kind"] == "fold":
                found.extend(visit(row["covers"], trail | {item_id}))
            else:
                found.append(row)
        return found
    return visit(view["items"], set())


def extend(view, ids, *, persist=True):
    with _lock:
        before = list(view["items"])
        ids = list(ids)
        for item_id in ids:
            journal.get(item_id)
        for item_id in ids:
            if item_id not in view["items"]:
                view["items"].append(item_id)
        try:
            if persist:
                save(view)
        except BaseException:
            view["items"] = before
            raise


def _current_wake(view):
    wakes = [row for row in _entries(view, deep=True) if row["kind"] == "wake" and row["ctx"] == view["ctx"]]
    return max(wakes, key=lambda row: row["number"]) if wakes else None


def append(view, kind, *, messages=(), persist=True, **metadata):
    with _lock:
        if kind != "wake" and "wake_id" not in metadata:
            wake = _current_wake(view)
            if wake:
                metadata["wake_id"] = wake["id"]
        item_id = journal.append(kind, ctx=view["ctx"], messages=messages, **metadata)
        extend(view, [item_id], persist=persist)
        return item_id


def begin_wake(view, *, messages=(), boxes=None, read_snapshot=None, at=None, persist=True, **metadata):
    metadata.setdefault("ctx_started", view["started"])
    return append(view, "wake", messages=messages, boxes=boxes or {}, read_snapshot=read_snapshot or {},
                  at=at or time.strftime("%Y-%m-%d %H:%M:%S"), persist=persist, **metadata)


def begin_act(view, assistant, *, persist=True, **metadata):
    with _lock:
        wake = _current_wake(view)
        if wake:
            metadata.setdefault("wake_id", wake["id"])
        item_id = journal.begin_act(view["ctx"], assistant, **metadata)
        extend(view, [item_id], persist=persist)
        return item_id


def tool_started(view, act_id, call_id, *, persist=True):
    item_id = journal.tool_started(act_id, call_id)
    extend(view, [item_id], persist=persist)
    return item_id


def record_results(view, act_id, results, *, complete=False, persist=True):
    # Each result becomes durable and visible before asking for the next one.
    for result in results:
        journal.record_results(act_id, [result])
        extend(view, [row["id"] for row in journal.act_entries(act_id)], persist=persist)
    if complete:
        journal.record_results(act_id, [], complete=True)
        extend(view, [row["id"] for row in journal.act_entries(act_id)], persist=persist)


def source_ids(view, *, expand_folds=False):
    """Referenced rows, optionally descending through nested fold covers."""
    return [row["id"] for row in _entries(view, deep=expand_folds)]


def raw_messages(view, *, expand_folds=False):
    """Exact original JSON messages for these refs, without repair or truncation.

    expand_folds retrieves the sources hidden by nested folds for full-text
    recall; the default preserves the actual view for migration comparison.
    """
    return [message for row in _entries(view, deep=expand_folds) for message in row["messages"]]


def render(view, *, reasoning="keep", mark_borrowed=False):
    """Provider messages for this view; ``mark_borrowed`` flags messages from other contexts.

    WHY: 2026-10-31 维护者定图片以唤醒为界变旧：这次的图一直可见，以前的只留地址和已有描述。
    无状态中心里"以前的"就是借来的行——构造带进来的旧材料、fork 继承的引用；这次运行自己
    写下的行（画面、附图结果）才是新的。llm 看到 _old_image 就不再下载或描述它的图。
    """
    if reasoning not in ("keep", "drop"):
        raise ValueError("reasoning must be keep or drop")
    rows = _entries(view)
    latest_wake = _current_wake(view)
    latest_wake_id = latest_wake["id"] if latest_wake else None
    by_act = {}
    for row in rows:
        if row.get("act_id"):
            by_act.setdefault(row["act_id"], []).append(row)
    result, waiting, current = [], [], []

    def finish():
        result.extend(journal.missing_result(call_id, current) for call_id in waiting)
        waiting.clear()

    for row in rows:
        for message in row["messages"]:
            if message.get("role") != "tool":
                finish()
            if message.get("role") == "assistant":
                current = by_act.get(row["id"], [])
                waiting = [call["id"] for call in message.get("tool_calls") or []]
                own_current_chain = row["ctx"] == view["ctx"] and (
                    latest_wake_id is None or row.get("wake_id") == latest_wake_id)
                # WHY: the provider's native thinking is part of the active
                # tool chain. Drop only older material, including borrowed refs
                # in a fork; a fork's own wake-less acts are still its live chain.
                if reasoning == "drop" and not own_current_chain:
                    message.pop("reasoning_content", None)
                    message.pop("reasoning", None)
                # WHY: keep the compress call and receipt to avoid repeating an
                # already successful fold (maintainer 2026-10-28); shorten only
                # the model projection, never its original recorded arguments.
                for call in message.get("tool_calls") or []:
                    function = call.get("function", {})
                    if function.get("name") != "compress":
                        continue
                    try:
                        args = json.loads(function.get("arguments", "{}"))
                        text = args.get("summary", "")
                        if len(text) > 60:
                            args["summary"] = text[:60] + f"……（共 {len(text)} 字，完整见收起行）"
                            function["arguments"] = json.dumps(args, ensure_ascii=False)
                    except (TypeError, ValueError, AttributeError):
                        pass
            elif message.get("role") == "tool":
                call_id = message.get("tool_call_id")
                if call_id not in waiting:
                    # A tool-only ref remains readable/raw, but is not a valid
                    # provider history without its assistant call.
                    continue
                waiting.remove(call_id)
            if mark_borrowed and row["ctx"] != view["ctx"]:
                message["_old_image"] = True
            result.append(message)
    finish()
    return result


def derive(view):
    visible = _entries(view)
    all_rows = _entries(view, deep=True)
    info = {"notes": {}, "screens": [], "shown": {}, "windows": [], "tool_state": None,
            "wakes": [], "steps": [], "folds": []}
    for row in all_rows:
        if row["kind"] == "screen":
            info["screens"].append(row)
            label = row["window"]
            ids = info["shown"].setdefault(label, [])
            ids.extend(one for one in row.get("shown", []) if one not in ids)
            if label not in info["windows"]:
                info["windows"].append(label)
    for row in visible:
        if "note_sources" in row:
            sources = row["note_sources"]
        elif row["kind"] == "note":
            sources = {row["scope"]: {"version": row.get("version"), "name": row.get("name", row["scope"])}}
        else:
            sources = {}
        for scope, source in sources.items():
            info["notes"][scope] = {"id": row["id"], "version": source.get("version"), "name": source.get("name", scope)}
        if "tool_state" in row:
            info["tool_state"] = row["tool_state"]
        if row["kind"] == "fold":
            info["folds"].append(row)
    # Addresses are facts on original rows, not indices in the shortened view.
    wakes = {row["id"]: row for row in all_rows if row["kind"] == "wake"}
    for row in all_rows:
        if row.get("wake_id") and row["wake_id"] not in wakes:
            wake = journal.get(row["wake_id"])
            wakes[wake["id"]] = wake
    for wake in sorted(wakes.values(), key=lambda row: row["number"]):
        members = [row["id"] for row in visible if row["id"] == wake["id"] or row.get("wake_id") == wake["id"]]
        # A whole-wake fold substitutes one visible reference for its members.
        for row in visible:
            if row["kind"] == "fold" and any(one["id"] == wake["id"] or one.get("wake_id") == wake["id"]
                                                for one in _entries({"items": [row["id"]]}, deep=True)):
                if row["id"] not in members:
                    members.append(row["id"])
        info["wakes"].append({"id": wake["id"], "number": wake["number"], "at": wake.get("at", ""),
                              "time": wake["time"], "items": [one for one in view["items"] if one in members]})
    for act in (row for row in all_rows if row["kind"] == "assistant" and row.get("wake_id")):
        wake = wakes[act["wake_id"]]
        calls = act["messages"][0].get("tool_calls") or []
        if not calls:
            continue
        members = [row for row in all_rows if row.get("act_id") == act["id"]]
        answered = {row.get("call_id") for row in members if row["kind"] == "tool"}
        complete = any(row["kind"] == "act_end" for row in members) or all(call["id"] in answered for call in calls)
        ids = {act["id"], *(row["id"] for row in members)}
        refs = [row["id"] for row in visible if row["id"] in ids or
                (row["kind"] == "fold" and any(one["id"] in ids for one in _entries({"items": [row["id"]]}, deep=True)))]
        info["steps"].append({"id": f"{wake['number']}.{act['step']}", "wake": wake["number"], "sub": act["step"],
                              "act_id": act["id"], "items": refs,
                              "tools": [call.get("function", {}).get("name", "?") for call in calls], "complete": complete})
    return info


def resolve_refs(view, refs):
    """Resolve newline refs: #wake, n.m, journal id, ctx name, note scope/name,
    ``policy:<name>`` and ``log:<window>[@<before>][*<count>]``.

    Invisible folded addresses resolve to the visible containing fold. Exact
    journal ids always mean the original row; a note not present uses its file.
    Unknown refs fail with the failing line rather than silently disappearing.

    WHY: 2026-11-01 维护者的视角是“只有原子，线把原子串起来”：所有种类的原子（日志条目、
    视图、笔记、策略、聊天记录）用同一套写法放上线，黑板、assemble、fork 共用这一处。
    """
    if isinstance(refs, (str, int)):
        refs = str(refs).splitlines()
    info = derive(view)
    result = []
    for value in refs:
        ref = str(value).strip()
        if not ref:
            continue
        found = None
        if re.fullmatch(r"#?\d+", ref):
            found = next((wake["items"] for wake in info["wakes"] if wake["number"] == int(ref.lstrip("#"))), None)
        elif re.fullmatch(r"\d+\.\d+", ref):
            found = next((step["items"] for step in info["steps"] if step["id"] == ref), None)
        elif journal._ID.fullmatch(ref):
            journal.get(ref)
            found = [ref]
        elif ref == view["ctx"] or ref == "current":
            found = list(view["items"])
        elif ref.startswith("policy:"):
            found = _policy_ref(view, ref[len("policy:"):].strip())
        elif ref.startswith("log:"):
            found = _log_ref(view, ref[len("log:"):].strip())
        else:
            archived_view = read_archived(ref)
            if archived_view is not None:
                found = archived_view["items"]
            else:
                note = next((item for scope, item in info["notes"].items() if ref in (scope, item["name"])), None)
                if note:
                    found = [note["id"]]
                else:
                    found = _note_refs(view, ref)
        if found is None:
            raise ValueError(f"unresolved reference: {ref}")
        result.extend(one for one in found if one not in result)
    return result


def _note_refs(view, scope):
    from . import notes

    try:
        text = notes.render(scope).strip()
    except ValueError:
        return None
    return _text_ref(view, scope, text) if text else None


def _policy_ref(view, name):
    from . import notes

    try:
        text = notes.read_policy(name).strip()
    except ValueError:
        return None
    return _text_ref(view, "policy:" + name, f"## 策略 {name}\n{text}") if text else None


def _log_ref(view, value):
    """A window's chatlog records before a moment, recorded as one row of this run."""
    from . import roles

    match = re.fullmatch(r"([^@*\s]+)(?:@([^*]+?))?(?:\*(\d+))?", value)
    if match is None:
        return None
    window, moment, count = match[1], (match[2] or "").strip(), int(match[3] or 30)
    try:
        text = roles.read_log(window, count, moment)
    except (ValueError, KeyError):
        return None
    return [journal.append("log", ctx=view["ctx"], window=window, before=moment, count=count,
                           messages=[{"role": "user", "content": f"【聊天记录 {window}"
                                      + (f"，{moment} 之前" if moment else "") + f"，{count} 条】\n{text}"}])]


def _text_ref(view, scope, text):
    version = hashlib.md5(text.encode("utf-8")).hexdigest()
    # Reuse an identical already-recorded source version instead of copying a
    # note each time a fork names it. Referencing alone does not change the view.
    # WHY: 2026-11-01 every construct resolves several notes; scanning the whole
    # journal each time grows without bound. Reuse is only an optimisation, so a
    # week of history is enough; an older identical version just gets a new row.
    for row in reversed(journal.events(kind="note", after=time.time() - 7 * 86400)):
        if row.get("scope") == scope and row.get("version") == version:
            return [row["id"]]
    return [journal.append("note", ctx=view["ctx"], scope=scope, name=scope, version=version,
                           messages=[{"role": "system", "content": text}])]


def archive(view, kind=""):
    """Freeze views/<ctx>.json, idempotently finishing an interrupted archive.

    Existing content must match exactly, reusing only its automatically chosen
    ended time when the caller has none. A different view never overwrites it.
    """
    if kind and not re.fullmatch(r"[0-9A-Za-z_-]+", kind):
        raise ValueError("invalid archive kind")
    with _lock:
        frozen = copy.deepcopy(view)
        if kind:
            frozen["stats"]["kind"] = kind
        target = ROOT / "views" / (view["ctx"] + ".json")
        if target.exists():
            existing = json.loads(target.read_text(encoding="utf-8"))
            _validate(existing)
            frozen["stats"].setdefault("ended", existing["stats"]["ended"])
            if existing != frozen:
                raise ValueError("different archive already exists for this ctx")
            frozen = existing
        else:
            frozen["stats"].setdefault("ended", time.time())
            _validate(frozen)
            _atomic(target, frozen)
        if frozen["stats"].get("kind", "") in ("", "reflect", "settle"):
            _index_year(frozen)
        if not kind:
            current = ROOT / "view.json"
            if current.is_file() and json.loads(current.read_text(encoding="utf-8"))["ctx"] == view["ctx"]:
                current.unlink()
                (ROOT / "view.bak.json").unlink(missing_ok=True)
        return view["ctx"]


def _index_year(view):
    try:
        _YEARS.parent.mkdir(parents=True, exist_ok=True)
        marker = f"[{view['ctx']}]"
        if _YEARS.is_file() and any(line.rstrip().endswith(marker)
                                  for line in _YEARS.read_text(encoding="utf-8").splitlines()):
            return
        with _YEARS.open("a", encoding="utf-8") as handle:
            handle.write(_year_line(view) + "\n")
    except OSError as error:
        # 年鉴只是索引，写不进去不能妨碍归档本身，但记录告警。
        logging.getLogger(__name__).warning("追加年鉴索引失败：%s", error)


def fill_years() -> str:
    """Append yearbook lines missing for archived views; never removes or rewrites a line.

    WHY: 2026-10-31 维护者加了 `#years rebuild`，从归档整份重建年鉴并核对每行能定位到原文。
    新核心的年鉴行本来就带 `[ctx]`；迁移来的 views/ 里旧段的 kind 不全，整份重建会把不该进
    年鉴的段写进去、把手写的行冲掉，所以只补缺：明确标了可入年鉴 kind 的视图没有行就补一行。
    """
    with _lock:
        text = _YEARS.read_text(encoding="utf-8") if _YEARS.is_file() else ""
        lines = [line for line in text.splitlines() if line.strip()]
        marked = {line.rstrip().rsplit("[", 1)[-1].rstrip("]") for line in lines if line.rstrip().endswith("]")}
        missing = []
        for name, view in each_archived():
            if "kind" in view["stats"] and view["stats"]["kind"] in ("", "reflect", "settle") and name not in marked:
                view["stats"].setdefault("ended", view["started"])
                missing.append((view["started"], _year_line(view)))
        unlocated = sum(not line.rstrip().endswith("]") for line in lines)
        if missing:
            _YEARS.parent.mkdir(parents=True, exist_ok=True)
            with _YEARS.open("a", encoding="utf-8") as handle:
                handle.write("".join(line + "\n" for _started, line in sorted(missing)))
        return (f"年鉴原有 {len(lines)} 行（其中 {unlocated} 行没有 [ctx] 定位，是切换前写的），"
                f"补了 {len(missing)} 行")


def _year_line(view):
    stats = view["stats"]
    span = time.strftime("%m-%d %H:%M", time.localtime(view["started"])) + "–" + time.strftime("%m-%d %H:%M", time.localtime(stats["ended"]))
    reason = str(stats.get("reason") or "").strip().split("\n")[0][:80]
    return f"- {span}（{len(raw_messages(view))} 条）{reason or '窗口 ' + '、'.join(derive(view)['windows'])} [{view['ctx']}]"


def each_archived(since=0, newest_first=False):
    views = []
    for path in (ROOT / "views").glob("*.json"):
        # WHY: 2026-11-01 every act freezes its own view, so this directory grows by
        # hundreds a day. A file is written once, at or after its view ended, so a
        # file older than ``since`` cannot hold a view that ended after it.
        if since and path.stat().st_mtime < since:
            continue
        view = json.loads(path.read_text(encoding="utf-8"))
        _validate(view)
        if view["stats"].get("ended", 0) > since:
            views.append((path.stem, view))
    yield from sorted(views, key=lambda pair: pair[1]["stats"].get("ended", 0), reverse=newest_first)


def archived(since=0, last=None):
    found = []
    for name, view in each_archived(since, newest_first=last is not None):
        found.append({"name": name, "started": view["started"], "ended": view["stats"].get("ended", 0),
                      "messages": len(raw_messages(view)), "windows": derive(view)["windows"], "reason": view["stats"].get("reason", "")})
        if last is not None and len(found) >= last:
            break
    return found[::-1] if last is not None else found


def read_archived(name):
    if not re.fullmatch(r"[0-9A-Za-z_-]+", name or ""):
        return None
    path = ROOT / "views" / (name + ".json")
    if path.is_file():
        view = json.loads(path.read_text(encoding="utf-8"))
        _validate(view)
        return view
    if journal._ID.fullmatch(name):
        row = journal.get(name)
        return {"ctx": row["ctx"], "started": row["time"],
                "items": row["covers"] if row["kind"] == "fold" else [name], "stats": {}}
    return None


def readable(view, *, compact=True, expand_folds=False):
    messages = []
    for row in _entries(view, deep=expand_folds):
        if row["kind"] == "goal":
            messages.append({"role": "system", "content":
                             f"【{row.get('run_kind', 'run')} 目的 · {row['id']}】{row.get('goal', '')}"})
        messages.extend(row["messages"])
    return readable_messages(messages, compact=compact)


def readable_messages(messages: list[dict], *, compact=True) -> str:
    """A segment as plain text, the way a person reads it back."""
    lines = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(part.get("text", "[图片]") if isinstance(part, dict) else str(part) for part in content)
        content = str(content or "")
        if role == "tool" and str(message.get("tool_call_id") or "").startswith("incoming_"):
            lines.append("【incoming】" + content)
        elif role == "assistant" and message.get("tool_calls") and all(
                call.get("function", {}).get("name") == "incoming" for call in message["tool_calls"]):
            # 系统替她打开窗口的那一半，不是她的调用。
            continue
        elif role == "assistant":
            if str(message.get("reasoning_content") or "").strip():
                lines.append("【她想】" + message["reasoning_content"])
            if content.strip():
                lines.append("【她】" + content)
            for call in message.get("tool_calls") or []:
                function = call.get("function", {})
                lines.append(f"【她调用】{function.get('name')}({function.get('arguments')})")
        elif role == "tool":
            lines.append("【结果】" + content)
        elif compact and content.startswith("<system-reminder>") and len(content) > 1500:
            # 开头那份完整工具状态很长，又和排查无关；中途的状态变化很短，照样显示。
            lines.append(f"【工具状态，{len(content)} 字，略】")
        elif compact and role == "system" and content.startswith(("以下是你的笔记", "还能召回的东西")) and len(content) > 400:
            # WHY: 2026-10-26 审查发现：每段开头的常驻笔记（global/recent/diary）和目录加起来常有上万字，
            # 不折叠的话 read_context 从 start=0 读整整第一页（8000 字）全在看静态笔记，根本看不到这一段
            # 发生了什么，而且用 match 搜词也会先撞上开头的 global。想看当时那一版笔记可调 note_history。
            lines.append(f"【开场笔记与目录，{len(content)} 字，略（查当时版本用 note_history）】")
        else:
            lines.append(f"【{role}】" + content)
    return "\n".join(lines)


def text_tokens(messages: list[dict]) -> int:
    def strip(value):
        if isinstance(value, list):
            return [strip(part) for part in value]
        if isinstance(value, dict):
            if value.get("type") == "image_url":
                return "[image]"
            return {key: strip(part) for key, part in value.items()}
        return value

    return _chat_root.count_tokens(json.dumps(strip(messages), ensure_ascii=False))


def context_size(reported, body: list[dict]) -> int:
    """How long *body* is: the size the API last reported for its first messages, plus an estimate of the rest.

    *reported* is ``[tokens, count]``: the API counted ``tokens`` when ``body[:count]``
    was the whole request and its answer.

    WHY: 维护者 2026-10-23：以接口返回的为准。字数估算把图片当成几个字，DeepSeek 每张图
    却最多算 1024 token（官方图像理解文档），图多的群攒上几百张也不会交接，直到撞上单个
    请求 600 张图或 48 MiB 的上限；接口报的 prompt+completion 连系统提示、工具定义和图片都
    算在内。报数之后才追加的（工具结果、新画面）还没人报过，先按字数估。
    """
    if isinstance(reported, list) and len(reported) == 2 and 0 <= reported[1] <= len(body):
        return reported[0] + text_tokens(body[reported[1]:])
    return text_tokens(body)


def amount(tokens: int) -> str:
    return f"{tokens / 10000:.1f} 万" if tokens >= 10000 else str(tokens)
