"""Persistent lines and per-line memory: Fork, Compact/Sediment, Send, hand_back, Inspect, Probe.

Every line lives at ``data/chat/lines/<safe_name>.json`` as a plain dictionary:
``{name, aliases, past, present, future, source, borrowed_until, claimed_windows, follow_ups, stats}``.
``past`` and ``present`` form the persisted prefix (cached across wakes via immutable journal
atom IDs); ``future``, ``follow_ups`` and the BM25/ACT-R concern directory are rendered only
into the ephemeral request tail ``Hint`` (never persisted into ``present``).

WHY: 2026-11-12 统一规范（docs/working/proposals/lines-and-memory.md）：分叉 = 线程 = 窗口 = 线，
所有线默认永久落盘、休眠零开销，不再设短时/长期类型或每轮末尾强制收尾；单线内部按正交的
过去/现在/未来保持 O(1) 有界，切换回 #construct off 时由 compact 同步维护 handoff:<窗口>.md。
"""

from __future__ import annotations

from collections import Counter
import json
import math
import os
from pathlib import Path
import re
import threading
import time

from mods import cq, msgs
from . import journal, notes as _notes, segment, windows as _windows

ROOT = Path("data/chat/lines")
MAIN = "line:main"
MAX_PRESENT_FOLDS = 6
MAX_PAST_ITEMS = 24
MAX_FOLLOW_UPS = 20
MAX_USES = 40
MAX_SENT_IDS = 200
# WHY: 2026-11-13 柚子审查指出 claimed_windows 若不带活跃时间、仅靠显式 close=True 释放，
# 现实中不常调 close 就会让旧专注线永久锁死窗口，cold 分诊再也接不到新话题。
# 因此认领附带 claimed_at 时间戳，超过保鲜期无互动仅释放窗口认领退回 cold 分诊，保留本线 future 不丢。
CLAIM_TTL_SECS = 2 * 3600
# WHY: 2026-11-19：主线活跃看板只浮出有未过期认领或近期（48h 内）动过的线；久未触达的线不删 future，
# 自然沉入水下、仍留在 BM25/[CQ:reply] 召回池里，避免旧势差长期赖在顶层视野。
BOARD_IDLE_TTL_SECS = 48 * 3600
# WHY: 2026-11-19：累计未收起的工作工具过程（tool 返回 + 参数 + 旧思考）超过此字符水位时，
# 在唤醒收尾趁热缓存询问模型是否做局部过程收束（兜住“上轮重活跳过、下轮轻活收尾”的场景）。
PROCESS_FOLD_CHARS = 15000
# 表达与调度类轻工具本身就是第一人称对话/路由经历的一部分，scope="process" 局部收束时原样保留。
_KEEP_IN_PROCESS_TOOLS = frozenset({
    "send", "send_file", "type_word",
    "line__send", "line__fork", "line__future", "line__hand_back", "line__compact",
    "wait", "cancel_wait", "follow", "unfollow", "mute", "unmute",
})

_lock = threading.RLock()
_UNSAFE_FILE = re.compile(r'[^0-9A-Za-z._\-\u4e00-\u9fff]+')
_CATEGORY = re.compile(r"^[a-z][a-z0-9_]*:.+")


def normalize_name(name: str) -> str:
    """Canonicalize a line name; bare names default to ``line:<name>``."""
    raw = str(name or "").strip()
    if not raw or raw in ("main", MAIN):
        return MAIN
    if "/" in raw or "\\" in raw or ".." in raw:
        raise ValueError(f"线名不能包含路径分隔符或 ..：{raw}")
    if raw == "me" or re.fullmatch(r"[gu][1-9][0-9]*", raw) or _CATEGORY.fullmatch(raw):
        return raw
    return f"line:{raw}"


def _safe_stem(name: str) -> str:
    canonical = normalize_name(name)
    encoded = canonical.replace("%", "%25").replace(":", "__")
    cleaned = _UNSAFE_FILE.sub(lambda m: "".join(f"_{ord(ch):02x}" for ch in m.group(0)), encoded).strip("._")
    if not cleaned:
        raise ValueError(f"无效线名：{name!r}")
    return cleaned


def _path(name: str) -> Path:
    return ROOT / f"{_safe_stem(name)}.json"


def _outbound_path() -> Path:
    return ROOT / "_outbound.json"


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _format_iso_ts(ts: float | None = None) -> str:
    moment = time.time() if ts is None else float(ts)
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(moment))


def _parse_claim_ts(raw, fallback: float = 0.0) -> float:
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw or "").strip()
    if not s:
        return float(fallback or 0.0)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return float(time.mktime(time.strptime(s[:19], fmt)))
        except ValueError:
            continue
    try:
        return float(s)
    except ValueError:
        return float(fallback or 0.0)


def _fresh_line(name: str, *, source: str = "", future: str = "", aliases=()) -> dict:
    canonical = normalize_name(name)
    now = time.time()
    now_iso = _format_iso_ts(now)
    clean_aliases = list(dict.fromkeys(str(a).strip() for a in (aliases or ()) if str(a).strip()))
    return {
        "name": canonical,
        "aliases": clean_aliases,
        "past": [],
        "present": [],
        "future": str(future or "").strip(),
        "source": normalize_name(source) if str(source or "").strip() else "",
        "borrowed_until": 0,
        "claimed_windows": [],
        "claimed_at": {},
        "follow_ups": [],
        "stats": {
            "created": now,
            "updated": now,
            "updated_at": now_iso,
            "acts": 0,
            "used": 0,
            "folds": 0,
            "uses": [round(now)],
            "past_atom": "",
            "underwater_past": [],
            "sent_ids": [],
        },
    }


def _normalize_loaded(data: dict, fallback_name: str = MAIN) -> dict:
    if not isinstance(data, dict):
        raise ValueError("line file must be a JSON object")
    # Support legacy view-shaped drafts if encountered cleanly
    if "items" in data and "present" not in data:
        base = _fresh_line(fallback_name)
        base["present"] = [str(x) for x in data.get("items", []) if isinstance(x, str)]
        base["stats"].update(data.get("stats") or {})
        return base
    canonical = normalize_name(data.get("name") or fallback_name)
    base = _fresh_line(canonical)
    base["aliases"] = list(dict.fromkeys(str(x).strip() for x in (data.get("aliases") or []) if str(x).strip()))
    base["past"] = [dict(item) for item in (data.get("past") or []) if isinstance(item, dict)]
    base["present"] = [str(item) for item in (data.get("present") or []) if isinstance(item, str) and item]
    base["future"] = str(data.get("future") or "").strip()
    src = str(data.get("source") or "").strip()
    base["source"] = normalize_name(src) if src else ""
    base["borrowed_until"] = max(0, min(int(data.get("borrowed_until") or 0), len(base["present"])))
    base["claimed_windows"] = list(dict.fromkeys(
        str(w).strip() for w in (data.get("claimed_windows") or []) if str(w).strip()
    ))
    raw_claimed_at = data.get("claimed_at")
    claimed_at: dict[str, str] = {}
    if isinstance(raw_claimed_at, dict):
        for k, v in raw_claimed_at.items():
            wk = str(k).strip()
            if not wk or v is None:
                continue
            if isinstance(v, (int, float)):
                claimed_at[wk] = _format_iso_ts(float(v))
            elif str(v).strip():
                claimed_at[wk] = str(v).strip()
    base["claimed_at"] = {w: claimed_at[w] for w in base["claimed_windows"] if w in claimed_at}
    base["follow_ups"] = [dict(f) for f in (data.get("follow_ups") or []) if isinstance(f, dict)][-MAX_FOLLOW_UPS:]
    if isinstance(data.get("stats"), dict):
        base["stats"].update(data["stats"])
        if "updated_at" not in data["stats"] and isinstance(base["stats"].get("updated"), (int, float)):
            base["stats"]["updated_at"] = _format_iso_ts(float(base["stats"]["updated"]))
    return base


def load(name: str = MAIN) -> dict | None:
    """Load one persistent line by name, or ``None`` if it has not been created."""
    canonical = normalize_name(name)
    with _lock:
        path = _path(canonical)
        if path.is_file():
            return _normalize_loaded(json.loads(path.read_text(encoding="utf-8")), canonical)
        if ROOT.is_dir():
            for candidate in sorted(ROOT.glob("*.json")):
                if candidate.name.startswith("_") or candidate.name.endswith(".archived.json"):
                    continue
                try:
                    raw = json.loads(candidate.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if isinstance(raw, dict) and raw.get("name") and normalize_name(raw["name"]) == canonical:
                    return _normalize_loaded(raw, canonical)
        return None


def save(line: dict, *, touch_updated: bool = True) -> dict:
    """Validate and atomically persist *line*."""
    with _lock:
        normalized = _normalize_loaded(line, line.get("name") or MAIN)
        if touch_updated:
            now = time.time()
            normalized["stats"]["updated"] = now
            normalized["stats"]["updated_at"] = _format_iso_ts(now)
        _atomic_json(_path(normalized["name"]), normalized)
        return normalized


def ensure(name: str = MAIN, *, source: str = "", future: str = "", aliases=()) -> dict:
    """Load *name* or create and persist an empty line."""
    with _lock:
        existing = load(name)
        if existing is not None:
            changed = False
            for alias in aliases or ():
                val = str(alias).strip()
                if val and val not in existing["aliases"]:
                    existing["aliases"].append(val)
                    changed = True
            if changed:
                existing = save(existing)
            return existing
        created = _fresh_line(name, source=source, future=future, aliases=aliases)
        return save(created)


def list_lines() -> list[dict]:
    """All persistent lines currently stored under ``data/chat/lines/``."""
    with _lock:
        if not ROOT.is_dir():
            return []
        found = []
        for path in sorted(ROOT.glob("*.json")):
            if path.name.startswith("_") or path.name.endswith(".tmp"):
                continue
            # Archived rolled snapshots use ``.archived.json``
            if path.name.endswith(".archived.json"):
                continue
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(raw, dict) and ("present" in raw or "name" in raw):
                    found.append(_normalize_loaded(raw, path.stem))
            except (OSError, ValueError):
                continue
        return found


def _touch_use(line: dict, now: float | None = None) -> None:
    ts = time.time() if now is None else float(now)
    uses = list(line["stats"].get("uses") or [])
    if not uses or ts - uses[-1] >= 60:
        uses.append(round(ts))
        del uses[:-MAX_USES]
    line["stats"]["uses"] = uses
    line["stats"]["updated"] = ts
    line["stats"]["updated_at"] = _format_iso_ts(ts)


def _claim_window(line: dict, win_label: str, *, now: float | None = None) -> None:
    label = str(win_label or "").strip()
    if not label or label == "me" or line.get("name") == MAIN:
        return
    if label not in line["claimed_windows"]:
        line["claimed_windows"].append(label)
    line.setdefault("claimed_at", {})[label] = _format_iso_ts(now)
    stats = line.setdefault("stats", {})
    stats["last_windows"] = list(dict.fromkeys([*(stats.get("last_windows") or []), label]))[-8:]
    if stats.get("handed_back_windows"):
        stats["handed_back_windows"] = [w for w in stats["handed_back_windows"] if w != label]


def _release_window(line: dict, win_label: str | None = None) -> list[str]:
    if win_label:
        label = str(win_label).strip()
        released = [w for w in line.get("claimed_windows") or [] if w == label]
        line["claimed_windows"] = [w for w in line.get("claimed_windows") or [] if w != label]
        (line.get("claimed_at") or {}).pop(label, None)
        return released
    released = list(line.get("claimed_windows") or [])
    line["claimed_windows"] = []
    line["claimed_at"] = {}
    return released


def _evict_window_claims(win_label: str, *, keep: str = "", now: float | None = None) -> list[str]:
    """Release *win_label* from other lines before *keep* claims it, so a window has one owner.

    WHY: 2026-11-15 现场（line.py 审计）：忘了 hand_back 就 fork 新话题，两根线同时认领同一窗口，
    ``claimed_line_for_window`` 看到不唯一直接退 cold，之后的消息全掉回主线靠人工转。
    这里把「后认领接管」做在派发路径上：被顶掉的线只放窗口认领，``future`` 与 past 原样保留，
    并在它自己的 present 里留一条 system atom 说明窗口去向。
    """
    label = str(win_label or "").strip()
    if not label or label == "me":
        return []
    keep_key = normalize_name(keep) if str(keep or "").strip() else ""
    evicted: list[str] = []
    for item in list_lines():
        name = item["name"]
        if name in (MAIN, keep_key):
            continue
        if label not in (item.get("claimed_windows") or []):
            continue
        target = load(name)
        if target is None or label not in (target.get("claimed_windows") or []):
            continue
        _release_window(target, label)
        stats = target.setdefault("stats", {})
        stats["last_windows"] = [w for w in (stats.get("last_windows") or []) if w != label]
        stats["handed_back_windows"] = list(dict.fromkeys([*(stats.get("handed_back_windows") or []), label]))[-8:]
        note = (
            f"【窗口接管｜{label}】\n"
            f"该窗口已派给 {keep_key or '新线'}：本线只释放窗口认领，future 与 past 不动。"
        )
        atom_id = journal.append(
            "system",
            ctx=f"evict-{_safe_stem(name)}",
            line=name,
            window=label,
            messages=[{"role": "system", "content": note}],
        )
        target["present"].append(atom_id)
        _touch_use(target, now=now)
        save(target, touch_updated=False)
        evicted.append(name)
    return evicted


def prune_expired_claims(
    line: dict,
    *,
    now: float | None = None,
    ttl: float = CLAIM_TTL_SECS,
    persist: bool = True,
) -> list[str]:
    """Drop window claims idle for longer than *ttl* seconds while keeping ``line['future']`` intact."""
    claimed = list(line.get("claimed_windows") or [])
    if not claimed:
        return []
    ts = time.time() if now is None else float(now)
    stats = line.get("stats") or {}
    fallback_ts = _parse_claim_ts(stats.get("updated_at"), fallback=float(stats.get("updated") or 0.0))
    claimed_at = dict(line.get("claimed_at") or {})
    expired: list[str] = []
    kept: list[str] = []
    for w in claimed:
        last_active = _parse_claim_ts(claimed_at.get(w), fallback=fallback_ts)
        if ttl >= 0 and last_active > 0 and (ts - last_active) > ttl:
            expired.append(w)
            claimed_at.pop(w, None)
        else:
            kept.append(w)
    if expired:
        line["claimed_windows"] = kept
        line["claimed_at"] = {w: claimed_at[w] for w in kept if w in claimed_at}
        if persist:
            save(line, touch_updated=False)
    return expired


def _format_claim_badges(line: dict, *, now: float | None = None, ttl: float = CLAIM_TTL_SECS) -> list[str]:
    ts = time.time() if now is None else float(now)
    stats = line.get("stats") or {}
    fallback_ts = _parse_claim_ts(stats.get("updated_at"), fallback=float(stats.get("updated") or 0.0))
    claimed_at = line.get("claimed_at") or {}
    badges: list[str] = []
    for w in line.get("claimed_windows") or []:
        raw = claimed_at.get(w)
        last_ts = _parse_claim_ts(raw, fallback=fallback_ts)
        if last_ts > 0:
            rem_min = max(0, int((ttl - (ts - last_ts)) // 60))
            clock_hm = time.strftime("%H:%M", time.localtime(last_ts))
            badges.append(f"{w}（活跃 {clock_hm}，余 {rem_min}m）")
        else:
            badges.append(w)
    return badges


def render_past_text(line: dict) -> str:
    """Format ``line['past']`` into a compact predictive index block with atom provenance."""
    entries = line.get("past") or []
    if not entries:
        return ""
    parts = [f"【本线（{line['name']}）过去沉积的认知与索引（需要核对细节时按来源原子或 read_file 展开）】"]
    for item in entries:
        key = str(item.get("key") or "认知").strip()
        text = str(item.get("text") or "").strip()
        atoms = [str(a) for a in (item.get("atoms") or []) if a]
        water = int(item.get("water") or 0)
        quote = str(item.get("peak_quote") or "").strip()
        head = f"- [{key}] {text}" if text else f"- [{key}]"
        if water > 0:
            head += f"（情绪水位={water}/3）"
        if atoms:
            head += f" [来源: {', '.join(atoms[:5])}]"
        parts.append(head)
        if quote:
            parts.append(f"  峰值原话：「{quote}」")
    return "\n".join(parts)


def ensure_past_atom(line: dict) -> str:
    """Return a stable journal atom ID representing ``line['past']`` (minted only when past changes)."""
    if not line.get("past"):
        line["stats"]["past_atom"] = ""
        return ""
    atom_id = str(line["stats"].get("past_atom") or "")
    if atom_id:
        try:
            row = journal.get(atom_id)
            if row.get("kind") == "system":
                return atom_id
        except KeyError:
            pass
    text = render_past_text(line)
    atom_id = journal.append(
        "system",
        ctx=f"past-{_safe_stem(line['name'])}",
        line=line["name"],
        messages=[{"role": "system", "content": text}],
    )
    line["stats"]["past_atom"] = atom_id
    return atom_id


_ensure_past_atom = ensure_past_atom


def prefix_items(line: dict | None) -> list[str]:
    """The immutable journal atom ID sequence ``[past_atom?, *present]`` forming a line's prefix."""
    if not line:
        return []
    with _lock:
        prev_atom = (line.get("stats") or {}).get("past_atom")
        past_atom = ensure_past_atom(line)
        if past_atom and past_atom != prev_atom:
            save(line)
        result = [past_atom] if past_atom else []
        for item in line.get("present") or []:
            if item not in result:
                result.append(item)
        return result


def active_modules(line: dict | None) -> list[str]:
    """Tool modules the line last announced as active in its prefix."""
    if not line:
        return []
    items = [item for item in (line.get("present") or []) if isinstance(item, str)]
    if not items:
        return []
    fake_view = {"ctx": "line-derive", "started": 0.0, "items": items, "stats": {}}
    state = segment.derive(fake_view)["tool_state"] or {}
    return [name for name in (state.get("active") or {}) if name not in ("meta", "line")]


def enqueue_line_wake(line_name: str, *, kind: str = "line", reason: str = "", window: tuple | None = None,
                      record_ids=(), event: dict | None = None) -> bool:
    """Schedule a wake targeting *line_name* through ``attention``."""
    from . import attention

    canonical = normalize_name(line_name)
    target_window = tuple(window) if window is not None else _windows.ME
    payload = dict(event or {})
    payload["_target_line"] = canonical
    eff_reason = str(reason or "").strip() or f"{canonical} 有新消息"
    if canonical not in eff_reason:
        eff_reason = f"{eff_reason} -> {canonical}"
    return attention.enqueue(
        target_window,
        kind=kind,
        reason=eff_reason,
        record_ids=record_ids,
        event=payload,
    )


def _load_outbound() -> dict[str, dict]:
    path = _outbound_path()
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        return {}


def record_outbound(line_name: str, window: tuple | str, message_ids) -> None:
    """Record outgoing message IDs emitted by *line_name* so ``[CQ:reply,id=...]`` routes back in 1 hop."""
    canonical = normalize_name(line_name)
    win_label = window if isinstance(window, str) else _windows.label(tuple(window))
    ids = [str(mid).strip() for mid in (message_ids or ()) if str(mid).strip()]
    if not ids:
        return
    with _lock:
        table = _load_outbound()
        now = time.time()
        for mid in ids:
            table[f"{win_label}:{mid}"] = {"line": canonical, "window": win_label, "time": now}
            table[f"*:{mid}"] = {"line": canonical, "window": win_label, "time": now}
        if len(table) > MAX_SENT_IDS * 4:
            ordered = sorted(table.items(), key=lambda kv: float((kv[1] or {}).get("time") or 0.0))
            table = dict(ordered[-MAX_SENT_IDS * 2:])
        _atomic_json(_outbound_path(), table)
        line = load(canonical)
        if line is not None:
            sent = list(line["stats"].get("sent_ids") or [])
            for mid in ids:
                tag = f"{win_label}:{mid}"
                if tag not in sent:
                    sent.append(tag)
            line["stats"]["sent_ids"] = sent[-MAX_SENT_IDS:]
            if canonical != MAIN and line.get("future") and win_label in (line.get("claimed_windows") or []):
                _claim_window(line, win_label, now=now)
            save(line)


def owner_of_message(window: tuple | str, message_id: str | int) -> str | None:
    """Return the line name that emitted *message_id* in *window*, or ``None``."""
    mid = str(message_id or "").strip()
    if not mid:
        return None
    win_label = window if isinstance(window, str) else _windows.label(tuple(window))
    with _lock:
        table = _load_outbound()
        hit = table.get(f"{win_label}:{mid}") or table.get(f"*:{mid}")
        if isinstance(hit, dict) and hit.get("line"):
            candidate = normalize_name(hit["line"])
            if load(candidate) is not None:
                return candidate
    return None


def extract_reply_id(event: dict | None) -> str:
    """Extract the target ``message_id`` from a OneBot event's ``[CQ:reply,id=...]`` or reply segment, if any."""
    if not isinstance(event, dict):
        return ""
    msg_field = event.get("message")
    if isinstance(msg_field, str):
        raw_cq = msgs.reply_cq(event)
        if raw_cq:
            try:
                parsed = cq.load(raw_cq)
                rid = str((parsed.get("data") or {}).get("id") or "").strip()
                if rid:
                    return rid
            except ValueError:
                pass
    elif isinstance(msg_field, list):
        for seg in msg_field:
            if isinstance(seg, dict) and seg.get("type") == "reply":
                rid = str((seg.get("data") or {}).get("id") or "").strip()
                if rid:
                    return rid
    for text_field in (msg_field if isinstance(msg_field, str) else "", str(event.get("raw_message") or "")):
        if text_field:
            found = re.search(r"\[CQ:reply,[^\]]*\bid=([^,\]]+)", text_field)
            if found:
                return found.group(1).strip()
    return ""


def record_follow_up(window: tuple | str, target_line: str, summary: str = "", *, target_master: str = MAIN) -> None:
    """Append a lightweight non-waking ``follow_up`` trace to ``line:main`` when 1-hop hot routing fires."""
    win_label = window if isinstance(window, str) else _windows.label(tuple(window))
    canonical_target = normalize_name(target_line)
    master_name = normalize_name(target_master)
    if canonical_target == master_name:
        return
    with _lock:
        master = ensure(master_name)
        entry = {
            "time": time.time(),
            "window": win_label,
            "line": canonical_target,
            "summary": str(summary or "").strip()[:160],
        }
        master["follow_ups"] = [*master.get("follow_ups", []), entry][-MAX_FOLLOW_UPS:]
        save(master)


def list_active_lines(*, now: float | None = None, ttl: float = CLAIM_TTL_SECS) -> list[dict]:
    """All non-``main`` lines with an active ``future``, after pruning stale window claims past *ttl*."""
    active: list[dict] = []
    for item in list_lines():
        if item["name"] == MAIN:
            continue
        prune_expired_claims(item, now=now, ttl=ttl, persist=True)
        if item.get("future"):
            active.append(item)
    return active


def claimed_line_for_window(
    window: tuple | str,
    *,
    now: float | None = None,
    ttl: float = CLAIM_TTL_SECS,
) -> str | None:
    """If exactly one active focused line holds an unexpired claim on *window*, return its name."""
    win_label = window if isinstance(window, str) else _windows.label(tuple(window))
    matches: list[tuple[float, str]] = []
    for item in list_active_lines(now=now, ttl=ttl):
        if win_label in (item.get("claimed_windows") or []):
            stats = item.get("stats") or {}
            fallback = _parse_claim_ts(stats.get("updated_at"), fallback=float(stats.get("updated") or 0.0))
            ts = _parse_claim_ts((item.get("claimed_at") or {}).get(win_label), fallback=fallback)
            matches.append((ts, item["name"]))
    if not matches:
        return None
    matches.sort(key=lambda pair: pair[0], reverse=True)
    return matches[0][1]


def route_arrival(
    window: tuple,
    event: dict | None = None,
    *,
    preview: str = "",
    now: float | None = None,
    ttl: float = CLAIM_TTL_SECS,
) -> tuple[str, str]:
    """Decide ``(target_line, route_kind)`` for an inbound event under ``#construct line``.

    Returns ``(line_name, 'explicit' | 'reply' | 'claimed' | 'cold')``:
    - ``explicit``: event carries ``_target_line`` (e.g. ``line.send``, ``fork``, background task completion).
    - ``reply``: ``[CQ:reply,id=...]`` matches a message sent by a known focused line -> 1-hop hot path + follow_up on ``line:main`` (and re-activates that window claim if ``future`` is non-empty).
    - ``claimed``: single active focused line (``future != ''``) holds an unexpired claim on *window* -> 1-hop hot path + follow_up on ``line:main``.
    - ``cold``: unclaimed or TTL-expired arrival -> routes to ``line:main`` for non-working triage.
    """
    if isinstance(event, dict) and event.get("_target_line"):
        return normalize_name(event["_target_line"]), "explicit"
    win_label = _windows.label(tuple(window))
    reply_id = extract_reply_id(event)
    if reply_id:
        owner = owner_of_message(window, reply_id)
        if owner and owner != MAIN:
            with _lock:
                owner_line = load(owner)
                if owner_line is not None and owner_line.get("future") and win_label != "me":
                    _claim_window(owner_line, win_label, now=now)
                    save(owner_line, touch_updated=False)
            record_follow_up(window, owner, preview or f"回复消息 #{reply_id}")
            return owner, "reply"
    claimed = claimed_line_for_window(window, now=now, ttl=ttl)
    if claimed:
        record_follow_up(window, claimed, preview or "延续已认领对话")
        return claimed, "claimed"
    return MAIN, "cold"


resolve_target_line = route_arrival


def _sync_handoff(
    line: dict,
    windows_to_sync=(),
    *,
    note_text: str = "",
    tools=(),
    closed: bool = False,
    preserve_existing: bool = False,
) -> None:
    """Keep ``data/notes/handoff:<window>.md`` synced so ``#construct off`` can take over in 1s."""
    from . import construct as _construct

    raw_windows = list(windows_to_sync or ()) or list(line.get("claimed_windows") or ())
    parsed = []
    for item in raw_windows:
        if isinstance(item, tuple):
            parsed.append(item)
        elif isinstance(item, str) and item.strip():
            try:
                parsed.append(_windows.parse(item.strip()))
            except ValueError:
                continue
    if not parsed:
        return

    line_focus = str(line.get("future") or "").strip()
    clean_note = str(note_text or "").strip()
    summary_parts = []
    if clean_note:
        summary_parts.append(clean_note)
    if line.get("past"):
        for p in line["past"][-4:]:
            atoms = [str(a) for a in (p.get("atoms") or []) if a]
            src_tag = f" [来源: {', '.join(atoms[:5])}]" if atoms else ""
            summary_parts.append(f"- {p.get('key', '认知')}: {p.get('text', '')}{src_tag}")
    default_body = "\n".join(summary_parts).strip()

    line_refs = []
    for entry in line.get("past") or []:
        for token in re.findall(r"@([A-Za-z0-9_:\-\u4e00-\u9fff@L]+)", str(entry.get("text") or "")):
            if token not in line_refs:
                line_refs.append(token)
    mod_list = list(dict.fromkeys([*active_modules(line), *(tools or ())]))
    last_ctx = str(line.get("stats", {}).get("last_ctx") or "")

    for win in parsed:
        existing = _construct.read_handoff(win) if (preserve_existing or (not line_focus and not closed)) else None
        existing_focus = str((existing or {}).get("focus") or "").strip()
        existing_valid_focus = existing_focus if (existing_focus and not is_closed_handoff_focus(existing_focus)) else ""

        if closed:
            focus = f"（线 {line['name']} 已办结当前目标）"
        elif preserve_existing and existing_valid_focus:
            # WHY: 04:00 _reflect updates handoff:me.md right before calling line.roll();
            # keep the freshly written reflection focus instead of overwriting it with line:main's empty/older future.
            focus = existing_valid_focus
        elif line_focus:
            focus = line_focus
        elif existing_valid_focus:
            focus = existing_valid_focus
        else:
            focus = f"（线 {line['name']} 常驻待命）"

        existing_note = str((existing or {}).get("note") or "").strip()
        if preserve_existing and existing_note:
            if clean_note and clean_note not in existing_note:
                body = f"{existing_note}\n{clean_note}"
            else:
                body = existing_note
        else:
            body = default_body
        if len(body) > _construct.HANDOFF_NOTE_MAX:
            body = body[:_construct.HANDOFF_NOTE_MAX] + "……（交接太长，后面截掉了）"

        if preserve_existing and existing:
            refs = list(dict.fromkeys([*(existing.get("refs") or []), *line_refs]))
            win_tools = list(dict.fromkeys([*(existing.get("tools") or []), *mod_list]))
            win_ctx = str(existing.get("ctx") or "").strip() or last_ctx
        else:
            refs = line_refs
            win_tools = mod_list
            win_ctx = last_ctx

        lines_out = [
            focus,
            "",
            body,
            "",
            *(f"@{r}" for r in refs),
            *([f"工具：{' '.join(win_tools)}"] if win_tools else []),
            *([f"运行：{win_ctx}"] if win_ctx else []),
        ]
        rendered = "\n".join(lines_out).replace("\n\n\n", "\n\n").strip() + "\n"
        path = _notes._path(_construct.handoff_scope(win))
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(rendered, encoding="utf-8")
        tmp.replace(path)


def fork(parent_name: str, name: str, target: str, *, window: str = "", aliases=(),
         reuse: bool = True, wake: bool = True) -> str:
    """Create or reuse a focused line sharing *parent_name*'s current prefix references (`§3.1 (1)`)."""
    parent_key = normalize_name(parent_name) if str(parent_name or "").strip() else MAIN
    child_key = normalize_name(name)
    goal = str(target or "").strip()
    if not goal:
        raise ValueError("fork 的 target（初始目标）不能为空")
    if child_key == parent_key:
        raise ValueError(f"不能从 {parent_key} 分叉到同名线自身")
    win_tuple = None
    win_label = ""
    if str(window or "").strip():
        win_tuple = _windows.parse(str(window).strip())
        win_label = _windows.label(win_tuple)

    with _lock:
        existing = load(child_key)
        if reuse and existing is not None:
            for alias in aliases or ():
                val = str(alias).strip()
                if val and val not in existing["aliases"]:
                    existing["aliases"].append(val)
            existing["future"] = goal
            if not existing.get("source"):
                existing["source"] = parent_key
            if win_label:
                _evict_window_claims(win_label, keep=child_key)
                _claim_window(existing, win_label)
            msg_text = (f"【来自 {parent_key} 的续接分派】\n目标：{goal}"
                        + (f"\n关联窗口：{win_label}" if win_label else ""))
            atom_id = journal.append(
                "input",
                ctx=f"dispatch-{_safe_stem(child_key)}",
                line=child_key,
                sender=parent_key,
                window=win_label,
                messages=[{"role": "user", "content": msg_text}],
            )
            existing["present"].append(atom_id)
            _touch_use(existing)
            save(existing)
            _sync_handoff(existing, [win_tuple] if win_tuple else ())
        else:
            from . import construct as _construct

            prior_handoff = _construct.read_handoff(win_tuple) if win_tuple and win_tuple != _windows.ME else None
            prior_block = ""
            if prior_handoff and (prior_handoff.get("focus") or prior_handoff.get("note")):
                prior_note = str(prior_handoff.get("note") or "").strip()
                prior_block = (
                    f"\n上次窗口交接：{prior_handoff.get('focus', '')}"
                    + (f"\n交接详情：{prior_note}" if prior_note else "")
                )
            parent = load(parent_key)
            shared = prefix_items(parent) if parent is not None else []
            created = _fresh_line(child_key, source=parent_key, future=goal, aliases=aliases)
            marker_text = (
                f"【分叉标记｜本线：{child_key}｜源线：{parent_key}】\n"
                f"初始目标：{goal}"
                + (f"\n负责窗口：{win_label}" if win_label else "")
                + prior_block
                + f"\n说明：你共享了源线 {parent_key} 此刻的前缀引用；中途可用 line__future 更新目标、用 line__compact 折叠阶段进展（默认保留 future），彻底办结时显式传 close=True 归零释放窗口并按需用 line__send 回传 {parent_key}。"
            )
            marker_id = journal.append(
                "system",
                ctx=f"fork-{_safe_stem(child_key)}",
                line=child_key,
                fork_marker={"name": child_key, "target": goal, "source": parent_key, "window": win_label},
                messages=[{"role": "system", "content": marker_text}],
            )
            created["present"] = [*shared, marker_id]
            created["borrowed_until"] = len(shared)
            if win_label:
                _evict_window_claims(win_label, keep=child_key)
                _claim_window(created, win_label)
            _touch_use(created)
            save(created)
            _sync_handoff(created, [win_tuple] if win_tuple else ())

    if wake:
        enqueue_line_wake(child_key, kind="fork", reason=f"分叉 {child_key}：{goal[:60]}", window=win_tuple)
    return child_key


def send(sender: str, recipient: str, content: str, *, window: str = "", wake: bool = True) -> str:
    """Append a message atom to *recipient*'s own ``present`` and schedule/interject *recipient* (`§3.1 (3)`)."""
    from_key = normalize_name(sender) if str(sender or "").strip() else MAIN
    to_key = normalize_name(recipient)
    body = str(content or "").strip()
    if not body:
        raise ValueError("send 的消息内容不能为空")
    win_tuple = None
    win_label = ""
    if str(window or "").strip():
        win_tuple = _windows.parse(str(window).strip())
        win_label = _windows.label(win_tuple)

    with _lock:
        target_line = ensure(to_key)
        seeded_from_send = False
        if win_label and win_label != "me" and to_key != MAIN:
            if not target_line.get("future"):
                first_line = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")[:80]
                target_line["future"] = f"跟进 {from_key} 转交：{first_line}" if first_line else f"跟进窗口 {win_label} 对话"
                seeded_from_send = True
            _evict_window_claims(win_label, keep=to_key)
            _claim_window(target_line, win_label)
        header = f"【来自 {from_key} 的跨线消息" + (f"｜关联窗口 {win_label}" if win_label else "") + "】"
        atom_id = journal.append(
            "input",
            ctx=f"msg-{_safe_stem(to_key)}",
            line=to_key,
            sender=from_key,
            window=win_label,
            messages=[{"role": "user", "content": f"{header}\n{body}"}],
        )
        target_line["present"].append(atom_id)
        _touch_use(target_line)
        save(target_line)
        if seeded_from_send and win_tuple is not None:
            _sync_handoff(target_line, [win_tuple])

    if wake:
        enqueue_line_wake(to_key, kind="line_send", reason=f"{from_key} 发来消息", window=win_tuple)
    return atom_id


def hand_back(line_name: str, reason: str, *, window: str = "", wake: bool = True) -> str:
    """Release *line_name*'s claim on *window* and return the arrival to ``line:main`` for re-dispatch (`§3.1 (3)` & `§6.2`)."""
    source_key = normalize_name(line_name)
    why = str(reason or "").strip()
    if not why:
        raise ValueError("hand_back 的 reason 不能为空")
    win_tuple = None
    win_label = ""
    if str(window or "").strip():
        win_tuple = _windows.parse(str(window).strip())
        win_label = _windows.label(win_tuple)

    with _lock:
        current = ensure(source_key)
        stats = current.setdefault("stats", {})
        if win_label:
            _release_window(current, win_label)
            stats["last_windows"] = [w for w in (stats.get("last_windows") or []) if w != win_label]
            stats["handed_back_windows"] = list(dict.fromkeys([*(stats.get("handed_back_windows") or []), win_label]))[-8:]
        else:
            released = _release_window(current)
            if released:
                rel_set = set(released)
                stats["last_windows"] = [w for w in (stats.get("last_windows") or []) if w not in rel_set]
                stats["handed_back_windows"] = list(dict.fromkeys([*(stats.get("handed_back_windows") or []), *released]))[-8:]
            if released and not win_label:
                win_label = released[0]
                try:
                    win_tuple = _windows.parse(win_label)
                except ValueError:
                    win_tuple = None
        save(current)

        master = ensure(MAIN)
        notice = (
            f"【专注线 {source_key} 超界交还（hand_back）】"
            + (f"\n窗口：{win_label}" if win_label else "")
            + f"\n原因：{why}\n请按全局关注目录重新分派。"
        )
        atom_id = journal.append(
            "input",
            ctx="handback-main",
            line=MAIN,
            sender=source_key,
            window=win_label,
            hand_back={"from": source_key, "window": win_label, "reason": why},
            messages=[{"role": "user", "content": notice}],
        )
        master["present"].append(atom_id)
        _touch_use(master)
        save(master)

    if wake:
        enqueue_line_wake(MAIN, kind="hand_back", reason=f"{source_key} 交还：{why[:60]}", window=win_tuple)
    return atom_id


def flush_before_compact(name: str, view: dict, *, current_act: str | None = None,
                         opening: set | None = None, stop_before_idx: int | None = None) -> set[str]:
    """Flush completed turn atoms prior to *current_act* into ``present`` before an in-turn ``compact``."""
    canonical = normalize_name(name)
    opening_ids = set(opening or ())
    raw_items = list(view.get("items") or [])
    if stop_before_idx is not None:
        raw_items = raw_items[:max(0, int(stop_before_idx))]
    with _lock:
        line = ensure(canonical)
        existing = set(prefix_items(line))
        flushed: list[str] = []
        for item in raw_items:
            if not isinstance(item, str) or item in existing:
                continue
            if current_act and item == current_act:
                break
            row = journal.get(item)
            if current_act and row.get("act_id") == current_act:
                continue
            if row.get("kind") == "goal":
                continue
            if item in opening_ids and line["present"]:
                continue
            flushed.append(item)
            existing.add(item)
        if flushed:
            line["present"].extend(flushed)
            save(line)
        return set(line["present"])


def _row_payload_chars(row: dict) -> int:
    total = 0
    for msg in row.get("messages") or ():
        if not isinstance(msg, dict):
            continue
        content = msg.get("content")
        if isinstance(content, str):
            total += len(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    total += len(part["text"])
        if msg.get("reasoning_content"):
            total += len(str(msg["reasoning_content"]))
        if msg.get("tool_calls"):
            total += len(json.dumps(msg["tool_calls"], ensure_ascii=False))
    return total


def _collect_unfolded_process(items: list[str], *, borrowed_until: int = 0, past_atom: str = "") -> dict:
    """Identify un-folded work-tool acts after the last ``fold`` atom in *items*.

    Only ``assistant`` acts that invoked at least one tool outside ``_KEEP_IN_PROCESS_TOOLS``
    (together with their paired ``call_start``/``tool``/``act_end`` rows) count as process
    scaffolding; ``screen``, ``input``, pure-text replies, and expression/routing calls stay intact.
    """
    seq = [x for x in (items or ()) if isinstance(x, str) and x and x != past_atom]
    base = max(0, min(int(borrowed_until or 0), len(seq)))
    last_fold_idx = base - 1
    for idx in range(base, len(seq)):
        try:
            row = journal.get(seq[idx])
        except KeyError:
            continue
        if row.get("kind") == "fold":
            last_fold_idx = idx
    anchor_idx = last_fold_idx + 1
    tail = seq[anchor_idx:]

    process_act_ids: set[str] = set()
    rows_by_id: dict[str, dict] = {}
    tool_calls_count = 0
    ctxs: list[str] = []
    sent_snippets: list[str] = []
    for aid in tail:
        try:
            row = journal.get(aid)
        except KeyError:
            continue
        rows_by_id[aid] = row
        if row.get("kind") != "assistant":
            continue
        msgs_list = row.get("messages") or []
        msg = msgs_list[0] if msgs_list and isinstance(msgs_list[0], dict) else {}
        calls = msg.get("tool_calls") or []
        if not calls:
            continue
        names = [str((c.get("function") or {}).get("name") or "") for c in calls if isinstance(c, dict)]
        if any(n and n not in _KEEP_IN_PROCESS_TOOLS for n in names):
            process_act_ids.add(aid)
            tool_calls_count += len(calls)
            cval = str(row.get("ctx") or "").strip()
            if cval and cval not in ctxs:
                ctxs.append(cval)
            for c in calls:
                if not isinstance(c, dict):
                    continue
                fn = (c.get("function") or {}).get("name")
                if fn == "send":
                    try:
                        args = json.loads((c.get("function") or {}).get("arguments") or "{}")
                        txt = str(args.get("text") or args.get("message") or "").strip()
                        if txt:
                            sent_snippets.append(txt[:80])
                    except ValueError:
                        pass

    process_ids: list[str] = []
    process_rows: list[dict] = []
    total_chars = 0
    for aid in tail:
        row = rows_by_id.get(aid)
        if row is None:
            continue
        if aid in process_act_ids or row.get("act_id") in process_act_ids:
            process_ids.append(aid)
            process_rows.append(row)
            total_chars += _row_payload_chars(row)

    return {
        "anchor_idx": anchor_idx,
        "process_ids": process_ids,
        "process_set": set(process_ids),
        "process_rows": process_rows,
        "acts": len(process_act_ids),
        "tool_calls": tool_calls_count,
        "chars": total_chars,
        "ctxs": ctxs,
        "sent_snippets": sent_snippets,
    }


def pending_process(name: str, items=None) -> dict:
    """Measure un-folded work-tool scaffolding for *name* (optionally including live *view['items']*)."""
    canonical = normalize_name(name)
    line = load(canonical)
    borrowed = int((line or {}).get("borrowed_until") or 0)
    past_atom = str(((line or {}).get("stats") or {}).get("past_atom") or "")
    source_items = list(items) if items is not None else list((line or {}).get("present") or [])
    return _collect_unfolded_process(source_items, borrowed_until=borrowed, past_atom=past_atom)


def append_turn(name: str, view: dict, *, opening: set | None = None, skip_ids: set | None = None,
                used: int | None = None, windows=(), closed_in_run: bool = False) -> list[str]:
    """Append persistent turn atoms from *view* onto *name*'s ``present`` without forcing a closing round."""
    canonical = normalize_name(name)
    opening_ids = set(opening or ())
    skipped = set(skip_ids or ())
    with _lock:
        line = ensure(canonical)
        existing = set(prefix_items(line)) | skipped
        added = []
        for item in view.get("items") or []:
            if not isinstance(item, str) or item in existing:
                continue
            row = journal.get(item)
            kind = row.get("kind")
            if kind == "goal":
                continue
            if item in opening_ids and line["present"]:
                continue
            added.append(item)
            existing.add(item)
        if added:
            line["present"].extend(added)
        line["stats"]["acts"] = int(line["stats"].get("acts") or 0) + 1
        line["stats"]["last_ctx"] = view.get("ctx", "")
        if used is not None:
            line["stats"]["used"] = int(used)
        seeded_fallback = False
        external_labels: list[str] = []
        external_tuples: list[tuple] = []
        if canonical != MAIN and not closed_in_run:
            for win in windows or ():
                if isinstance(win, str):
                    label = win.strip()
                    try:
                        win_t = _windows.parse(label) if label else None
                    except ValueError:
                        win_t = None
                else:
                    win_t = tuple(win)
                    label = _windows.label(win_t)
                if label and label != "me":
                    if label not in external_labels:
                        external_labels.append(label)
                    if win_t is not None and win_t not in external_tuples:
                        external_tuples.append(win_t)
            if external_labels:
                if not line.get("future"):
                    from . import construct as _construct

                    prior_focus = ""
                    for win_t in external_tuples:
                        rec = _construct.read_handoff(win_t)
                        cand = str((rec or {}).get("focus") or "").strip()
                        if cand and not is_placeholder_future(cand) and not is_closed_handoff_focus(cand):
                            prior_focus = cand
                            break
                    line["future"] = prior_focus or f"跟进窗口 {'、'.join(external_labels)} 未完对话"
                    seeded_fallback = True
                for label in external_labels:
                    _claim_window(line, label)
        _touch_use(line)
        save(line)
        if seeded_fallback and external_labels:
            _sync_handoff(line, external_labels)
        return added


def _normalize_past_updates(updates, default_atoms: list[str], water: int, peak_quote: str) -> list[dict]:
    normalized = []
    now = time.time()
    clamped_water = max(0, min(3, int(water or 0)))
    quote = str(peak_quote or "").strip()
    if isinstance(updates, str) and updates.strip():
        updates = [line.strip() for line in updates.splitlines() if line.strip()]
    elif isinstance(updates, dict):
        updates = [{"key": k, "text": v} for k, v in updates.items()]
    for entry in updates or ():
        if isinstance(entry, str):
            raw = entry.strip().lstrip("-* ")
            if not raw:
                continue
            if "：" in raw or ":" in raw:
                sep = "：" if "：" in raw and (":" not in raw or raw.index("：") < raw.index(":")) else ":"
                key, _, text = raw.partition(sep)
                key, text = key.strip() or "认知", text.strip()
            else:
                key, text = raw[:24], raw
            normalized.append({
                "key": key,
                "text": text,
                "atoms": list(default_atoms[:5]),
                "water": clamped_water,
                "peak_quote": quote,
                "updated": now,
            })
        elif isinstance(entry, dict):
            key = str(entry.get("key") or "认知").strip()
            text = str(entry.get("text") or "").strip()
            if not text and not entry.get("peak_quote"):
                continue
            atoms = [str(a) for a in (entry.get("atoms") or default_atoms[:5]) if a]
            w = max(0, min(3, int(entry.get("water", clamped_water) or 0)))
            pq = str(entry.get("peak_quote") or quote).strip()
            normalized.append({
                "key": key,
                "text": text,
                "atoms": atoms,
                "water": w,
                "peak_quote": pq,
                "updated": now,
            })
    return normalized


def merge_past_bounded(line: dict, incoming: list[dict]) -> None:
    """In-place merge into ``line['past']`` by ``key`` and enforce ``MAX_PAST_ITEMS`` bound (`§5.2`)."""
    by_key: dict[str, dict] = {}
    order: list[str] = []
    for item in line.get("past") or []:
        k = str(item.get("key") or "认知")
        if k not in by_key:
            order.append(k)
        by_key[k] = dict(item)
    for item in incoming:
        k = str(item.get("key") or "认知")
        if k in by_key:
            prev_atoms = list(by_key[k].get("atoms") or [])
            merged_atoms = list(dict.fromkeys([*item.get("atoms", []), *prev_atoms]))[:8]
            by_key[k].update(item)
            by_key[k]["atoms"] = merged_atoms
        else:
            order.append(k)
            by_key[k] = dict(item)
    merged = [by_key[k] for k in order]
    if len(merged) > MAX_PAST_ITEMS:
        now = time.time()
        scored = sorted(
            merged,
            key=lambda x: (int(x.get("water") or 0) * 0.5) - math.log(max(now - float(x.get("updated") or now), 60.0)) * 0.1,
            reverse=True,
        )
        kept = scored[:MAX_PAST_ITEMS]
        kept_keys = {id(x) for x in kept}
        evicted = [x for x in merged if id(x) not in kept_keys]
        underwater = list(line["stats"].get("underwater_past") or [])
        underwater.extend(evicted)
        line["stats"]["underwater_past"] = underwater[-100:]
        merged = [x for x in merged if id(x) in kept_keys]
    line["past"] = merged
    line["stats"]["past_atom"] = ""


_merge_past_bounded = merge_past_bounded


# WHY: 2026-11-13 柚子读完 construct line 源码后反馈「不放心那条 future，没 compact 会顺手写成 none，没办完事会丢」。
# 模型在工具调用时极易把不打算改的字符串参数顺手填成 ""、"none"、"null"、"无" 或干脆省略。
# 因此把「清空 future、解除窗口认领」收紧为仅在显式传 close=True 时发生；close=False 时一切空串和占位词都保留原 future。
_PLACEHOLDER_FUTURES = frozenset({
    "", "none", "null", "nil", "无", "keep", "unchanged", "保持", "不变", "(none)", "（无）", "暂无",
})


def is_placeholder_future(value: str | None) -> bool:
    if value is None:
        return True
    return str(value).strip().casefold() in _PLACEHOLDER_FUTURES


def is_closed_handoff_focus(value: str | None) -> bool:
    s = str(value or "").strip()
    if not s:
        return True
    return (s.startswith("（线 ") and "已办结" in s) or s.startswith("（上次没有写交接")


def resolve_future_arg(
    current_future: str,
    future: str | None = None,
    *,
    close: bool = False,
) -> tuple[str, bool, bool]:
    """Return ``(next_future, closed, ignored_placeholder)``.

    Only ``close=True`` clears ``future`` to ``""``; when ``close=False``, ``None``,
    empty strings, and placeholder tokens like ``"none"`` preserve *current_future*.
    """
    cur = str(current_future or "").strip()
    if close:
        return "", True, False
    if future is None:
        return cur, False, False
    cleaned = str(future).strip()
    if cleaned.casefold() in _PLACEHOLDER_FUTURES:
        return cur, False, bool(cur)
    return cleaned, False, False


def set_future(
    name: str,
    future: str | None = None,
    *,
    close: bool = False,
    window: str = "",
    note: str = "",
    notify_source: bool = False,
    sync_handoff: bool = True,
) -> dict:
    """Update or close *name*'s ``future`` without folding ``present``."""
    canonical = normalize_name(name)
    win_label = ""
    if str(window or "").strip():
        win_tuple = _windows.parse(str(window).strip())
        win_label = _windows.label(win_tuple)

    source_return: tuple[str, str] | None = None
    with _lock:
        line = ensure(canonical)
        prev_future = str(line.get("future") or "")
        prev_claimed = list(line.get("claimed_windows") or [])
        next_future, closed, ignored_placeholder = resolve_future_arg(prev_future, future, close=close)
        line["future"] = next_future
        if closed:
            _release_window(line)
            if notify_source and line.get("source") and prev_future:
                source_return = (
                    line["source"],
                    f"子线 {canonical} 已办结「{prev_future}」" + (f"：{str(note).strip()}" if str(note or "").strip() else ""),
                )
        elif canonical != MAIN and next_future:
            if win_label and win_label != "me":
                _claim_window(line, win_label)
            else:
                for w in list(line.get("claimed_windows") or ()):
                    _claim_window(line, w)
        _touch_use(line)
        save(line)

        if sync_handoff:
            sync_targets = (
                [win_label] if win_label
                else (list(line.get("claimed_windows") or []) or prev_claimed or (["me"] if canonical == MAIN else []))
            )
            if sync_targets:
                _sync_handoff(line, sync_targets, note_text=str(note or "").strip(), closed=closed)

    if source_return is not None:
        send(canonical, source_return[0], source_return[1], wake=True)

    return {
        "name": canonical,
        "future": line["future"],
        "closed": closed,
        "ignored_placeholder": ignored_placeholder,
        "claimed_windows": list(line["claimed_windows"]),
        "present_len": len(line["present"]),
    }


def compact(
    name: str,
    summary: str = "",
    *,
    scope: str = "all",
    done=(),
    future: str | None = None,
    close: bool = False,
    past_updates=(),
    water: int = 0,
    peak_quote: str = "",
    entity_sends=None,
    window: str = "",
    ctx: str = "",
    notify_source: bool = False,
) -> dict:
    """Fold exploration in ``present``, optionally ``Sediment`` into ``past``, update ``future``, and sync handoffs.

    ``scope="process"`` folds only un-folded work-tool scaffolding since the last ``fold`` atom,
    keeping earlier history, chat screens, inputs, and spoken ``send`` calls untouched while
    preserving ``read_context`` recall pointers. ``scope="all"`` performs the full-line fold/sediment.
    """
    canonical = normalize_name(name)
    norm_scope = str(scope or "all").strip().lower()
    if norm_scope not in ("all", "process"):
        norm_scope = "all"
    clean_summary = str(summary or "").strip()
    done_list = [str(d).strip() for d in ([done] if isinstance(done, str) else (done or ())) if str(d).strip()]
    has_past_updates = bool(str(past_updates).strip() if isinstance(past_updates, str) else past_updates)
    if not clean_summary and not done_list and not has_past_updates:
        if close or (future is not None and not is_placeholder_future(future)):
            res = set_future(
                canonical,
                future,
                close=close,
                window=window,
                notify_source=notify_source,
            )
            cur = load(canonical) or {}
            return {
                "name": canonical,
                "scope": norm_scope,
                "fold_atom": "",
                "sedimented": False,
                "borrowed_until": int(cur.get("borrowed_until") or 0),
                "future": res["future"],
                "closed": res["closed"],
                "ignored_placeholder": res["ignored_placeholder"],
                "claimed_windows": res["claimed_windows"],
                "entity_receipts": [],
            }
        raise ValueError("compact 不能为空（公理 A3：折叠时须写清 summary；若仅更新目标请传 future 或用 line__future）")

    win_label = ""
    if str(window or "").strip():
        win_label = _windows.label(_windows.parse(str(window).strip()))

    dispatched_entities: list[tuple[str, str]] = []
    source_return: tuple[str, str] | None = None

    with _lock:
        line = ensure(canonical)
        prev_future = str(line.get("future") or "")
        prev_claimed = list(line.get("claimed_windows") or [])
        borrowed = int(line.get("borrowed_until") or 0)
        next_future, closed, ignored_placeholder = resolve_future_arg(prev_future, future, close=close)

        if norm_scope == "process" and not closed:
            from . import construct as _construct

            proc = _collect_unfolded_process(line["present"], borrowed_until=borrowed)
            anchor_idx = proc["anchor_idx"]
            process_ids = proc["process_ids"]
            process_set = proc["process_set"]
            proc_ctxs = proc["ctxs"]
            proc_done = done_list or [
                d for d in _construct.done_lines(proc["process_rows"]) if "line__compact" not in d
            ]
            fold_lines = [f"【过程留痕｜{canonical}｜可召回】"]
            if clean_summary:
                fold_lines.append(clean_summary)
            if proc_done:
                shown_done = "；".join(proc_done[:10])
                more = f" 等 {len(proc_done)} 步" if len(proc_done) > 10 else ""
                fold_lines.append(
                    f"涉及动作：{shown_done}{more}（收起 {proc['acts']} 轮工具过程，约 {proc['chars'] / 1000:.1f}k 字）"
                )
            if proc["sent_snippets"]:
                fold_lines.append("期间发出：" + " / ".join(proc["sent_snippets"][:3]))
            recall_ctxs = proc_ctxs or ([ctx] if ctx else [])
            if recall_ctxs:
                recall_cmds = " / ".join(f'read_context("{c}")' for c in recall_ctxs[-4:])
                fold_lines.append(f"召回原文：{recall_cmds}")
            fold_text = "\n".join(fold_lines)
            fold_ctx = ctx or (recall_ctxs[-1] if recall_ctxs else str(line["stats"].get("last_ctx") or f"compact-{_safe_stem(canonical)}"))
            fold_atom = journal.append(
                "fold",
                ctx=fold_ctx,
                line=canonical,
                scope="process",
                covers=process_ids,
                ctxs=recall_ctxs,
                summary=fold_text,
                level=1,
                messages=[{"role": "system", "content": fold_text}],
            )
            provenance = process_ids[-6:] if process_ids else [fold_atom]
            normalized_updates = _normalize_past_updates(past_updates, provenance, water, peak_quote)
            should_sediment = bool(normalized_updates)
            if normalized_updates:
                merge_past_bounded(line, normalized_updates)

            kept_prefix = list(line["present"][:anchor_idx])
            tail = list(line["present"][anchor_idx:])
            last_proc_pos = max((i for i, aid in enumerate(tail) if aid in process_set), default=-1)
            new_tail: list[str] = []
            for idx, aid in enumerate(tail):
                if aid not in process_set:
                    new_tail.append(aid)
                if idx == last_proc_pos:
                    new_tail.append(fold_atom)
            if last_proc_pos < 0:
                new_tail.append(fold_atom)
            line["present"] = [*kept_prefix, *dict.fromkeys(new_tail)]
        else:
            own_slice = list(line["present"][borrowed:])
            kept_head = list(line["present"][:borrowed])

            latest_tool_state = None
            kept_screens: list[str] = []
            covered: list[str] = []
            existing_folds: list[str] = []
            for item_id in own_slice:
                try:
                    row = journal.get(item_id)
                except KeyError:
                    continue
                if "tool_state" in row or row.get("kind") == "tool_state":
                    latest_tool_state = item_id
                elif row.get("kind") == "fold":
                    existing_folds.append(item_id)
                elif row.get("kind") == "screen":
                    kept_screens.append(item_id)
                    covered.append(item_id)
                else:
                    covered.append(item_id)

            fold_lines = [f"【折叠印象｜{canonical}】"]
            if done_list:
                fold_lines.append("刚做过的：" + "；".join(done_list))
            if clean_summary:
                fold_lines.append("结论与拐点：" + clean_summary)
            if closed:
                fold_lines.append("接下来：（当前事项已办结，势差归零）")
            elif next_future:
                fold_lines.append("接下来：" + next_future)
            else:
                fold_lines.append("接下来：（未设显式 future，保持现状）")
            fold_text = "\n".join(fold_lines)
            fold_ctx = ctx or str(line["stats"].get("last_ctx") or f"compact-{_safe_stem(canonical)}")
            fold_atom = journal.append(
                "fold",
                ctx=fold_ctx,
                line=canonical,
                covers=covered,
                summary=fold_text,
                level=1,
                messages=[{"role": "system", "content": fold_text}],
            )

            provenance = covered[-6:] if covered else [fold_atom]
            normalized_updates = _normalize_past_updates(past_updates, provenance, water, peak_quote)
            all_folds = [*existing_folds, fold_atom]
            should_sediment = bool(normalized_updates) or len(all_folds) > MAX_PRESENT_FOLDS

            if should_sediment:
                if not normalized_updates and clean_summary:
                    normalized_updates = _normalize_past_updates(
                        [{"key": f"沉淀-{time.strftime('%m%d-%H%M')}", "text": clean_summary}],
                        [fold_atom, *provenance],
                        water,
                        peak_quote,
                    )
                if normalized_updates:
                    merge_past_bounded(line, normalized_updates)
                # Default 4: First Sediment drops borrowed parent prefix so child decouples cleanly
                kept_head = []
                line["borrowed_until"] = 0
                new_own = ([latest_tool_state] if latest_tool_state else []) + kept_screens[-2:] + [fold_atom]
            else:
                new_own = ([latest_tool_state] if latest_tool_state else []) + existing_folds + kept_screens[-4:] + [fold_atom]

            line["present"] = [*kept_head, *dict.fromkeys(new_own)]

        def _is_fold(atom_id: str) -> bool:
            try:
                return journal.get(atom_id).get("kind") == "fold"
            except KeyError:
                return False

        line["stats"]["folds"] = sum(1 for i in line["present"][line["borrowed_until"]:] if _is_fold(i))
        line["future"] = next_future
        if closed:
            # §2.3 仅在显式 close=True 时势差归零：清空 claimed_windows 消除幽灵订阅
            _release_window(line)
            if notify_source and line.get("source") and prev_future:
                source_return = (line["source"], f"子线 {canonical} 已办结「{prev_future}」：{clean_summary or '已完成'}")
        elif canonical != MAIN and next_future:
            if win_label and win_label != "me":
                _claim_window(line, win_label)
            else:
                for w in list(line.get("claimed_windows") or ()):
                    _claim_window(line, w)

        if line["past"]:
            ensure_past_atom(line)
        _touch_use(line)
        save(line)

        sync_targets = (
            [win_label]
            if win_label
            else list(line.get("claimed_windows") or prev_claimed or (["me"] if canonical == MAIN else []))
        )
        if sync_targets:
            _sync_handoff(line, sync_targets, note_text=clean_summary, closed=closed)

        if isinstance(entity_sends, dict):
            for target_entity, fact in entity_sends.items():
                if str(target_entity).strip() and str(fact).strip():
                    dispatched_entities.append((normalize_name(str(target_entity)), str(fact).strip()))
        elif isinstance(entity_sends, (list, tuple)):
            for pair in entity_sends:
                if isinstance(pair, (list, tuple)) and len(pair) == 2:
                    dispatched_entities.append((normalize_name(str(pair[0])), str(pair[1]).strip()))
                elif isinstance(pair, dict) and pair.get("line") and pair.get("content"):
                    dispatched_entities.append((normalize_name(str(pair["line"])), str(pair["content"]).strip()))

    sent_receipts = []
    for target_entity, fact in dispatched_entities:
        sent_receipts.append(send(canonical, target_entity, f"【跨线沉积事实】{fact}", wake=False))
    if source_return is not None:
        send(canonical, source_return[0], source_return[1], wake=True)

    return {
        "name": canonical,
        "scope": norm_scope,
        "fold_atom": fold_atom,
        "sedimented": should_sediment,
        "borrowed_until": line["borrowed_until"],
        "future": line["future"],
        "closed": closed,
        "ignored_placeholder": ignored_placeholder,
        "claimed_windows": list(line["claimed_windows"]),
        "entity_receipts": sent_receipts,
    }


def _extract_roll_excerpt(atom_ids: list[str]) -> str:
    snippets: list[str] = []
    for aid in atom_ids:
        try:
            row = journal.get(aid)
        except KeyError:
            continue
        kind = row.get("kind")
        if kind == "fold":
            txt = str(row.get("summary") or "").strip()
            first = next((ln.strip() for ln in txt.splitlines() if ln.strip() and not ln.startswith("【折叠印象")), "")
            if first:
                snippets.append(first[:60])
        elif kind in ("assistant", "input", "screen"):
            msgs_list = row.get("messages") or []
            if not msgs_list or not isinstance(msgs_list[0], dict):
                continue
            msg = msgs_list[0]
            content = msg.get("content")
            if isinstance(content, list):
                content = " ".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
            txt = str(content or "").strip()
            if txt:
                first = next((ln.strip() for ln in txt.splitlines() if ln.strip() and not ln.startswith(("——", "# "))), "")
                if first:
                    snippets.append(first[:60])
            elif msg.get("tool_calls"):
                names = [str((c.get("function") or {}).get("name") or "") for c in msg["tool_calls"] if isinstance(c, dict)]
                names = [n for n in names if n]
                if names:
                    snippets.append("调用 " + ",".join(names[:3]))
    deduped = list(dict.fromkeys(s for s in snippets if s))
    return "；".join(deduped[-3:])[:160]


def _resolve_roll_sync_targets(
    canonical: str,
    line: dict,
    dropped_ids: list[str],
    *,
    now: float | None = None,
) -> list[str]:
    """Determine ``handoff:<win>.md`` targets on ``roll``, recovering TTL-expired windows unless taken over."""
    if canonical == MAIN:
        return ["me"]

    from . import construct as _construct

    stats = line.get("stats") or {}
    handed_back = set(stats.get("handed_back_windows") or ())
    active_claims = list(line.get("claimed_windows") or [])
    candidates: list[str] = active_claims if active_claims else list(stats.get("last_windows") or [])
    if not active_claims:
        for aid in dropped_ids:
            try:
                row = journal.get(aid)
            except KeyError:
                continue
            for w in (row.get("window"), (row.get("fork_marker") or {}).get("window")):
                lbl = str(w or "").strip()
                if lbl and lbl != "me":
                    candidates.append(lbl)

    line_future = str(line.get("future") or "").strip()
    last_ctx = str(stats.get("last_ctx") or "").strip()
    resolved: list[str] = []
    for w in dict.fromkeys(candidates):
        if not w or w == "me" or w in handed_back:
            continue
        try:
            win_tuple = _windows.parse(w)
        except ValueError:
            continue
        if claimed_line_for_window(w, now=now) not in (None, canonical):
            continue
        rec = _construct.read_handoff(win_tuple)
        if (
            rec is None
            or str(rec.get("focus") or "").strip() == line_future
            or (not line_future and is_closed_handoff_focus(rec.get("focus")))
            or (last_ctx and str(rec.get("ctx") or "").strip() == last_ctx)
        ):
            resolved.append(w)
    return resolved


def _roll_one(canonical: str, reason: str = "") -> str:
    with _lock:
        line = load(canonical)
        if line is None:
            return ""
        now_ts = time.time()
        pruned_claims = prune_expired_claims(line, now=now_ts, persist=False)
        present = list(line.get("present") or [])
        borrowed = int(line.get("borrowed_until") or 0)
        tool_rows: list[str] = []
        for item_id in present:
            try:
                r = journal.get(item_id)
                if "tool_state" in r or r.get("kind") == "tool_state":
                    tool_rows = [item_id]
            except KeyError:
                continue
        tool_set = set(tool_rows)
        own_slice = [aid for aid in present[borrowed:] if aid not in tool_set]
        dropped_ids = own_slice or [aid for aid in present if aid not in tool_set]
        if not dropped_ids:
            # WHY: 2026-11-19：roll 清空 present 时会保留最新 1 条 tool_state，
            # 休眠老线的 present 长度始终为 1（truthy）。若不先判断是否存在待归档的非 tool_state 原子，
            # 每晚 04:00 都会重复写出无变化的 .archived.json 并刷新 updated_at。
            if pruned_claims or present != tool_rows or borrowed or line["stats"].get("folds"):
                line["borrowed_until"] = 0
                line["present"] = tool_rows
                line["stats"]["folds"] = 0
                save(line, touch_updated=False)
            return ""
        stamp = int(now_ts)
        archived = dict(line, stats={**line.get("stats", {}), "rolled_at": now_ts, "reason": reason})
        _atomic_json(ROOT / f"{_safe_stem(canonical)}-{stamp}.archived.json", archived)
        # WHY: 2026-11-13 柚子指出夜里 roll 清 present 前若没调过 compact，不能让昨天的过程只躺在
        # .archived.json 里不指路；必须先把被清原子的 [first_id ~ last_id] 与摘录收成折叠原子并合入 past，
        # 再在保留现成交接的前提下同步更新 handoff:<window>.md（含超过 2h 认领保鲜期但未被他线接管的窗口）。
        sync_targets = _resolve_roll_sync_targets(canonical, line, dropped_ids, now=now_ts)
        first_id, last_id = dropped_ids[0], dropped_ids[-1]
        span_label = f"{first_id} ~ {last_id}" if first_id != last_id else first_id
        excerpt = _extract_roll_excerpt(dropped_ids)
        win_hint = sync_targets[0] if sync_targets else ""
        roll_summary = (
            f"【归档索引｜{canonical}｜{reason or '换页归档'}】"
            f"范围 [{span_label}]（共 {len(dropped_ids)} 条原子）"
            + (f"：{excerpt}" if excerpt else "")
        )
        roll_atom = journal.append(
            "fold",
            ctx=str(line["stats"].get("last_ctx") or f"roll-{_safe_stem(canonical)}"),
            line=canonical,
            window=win_hint,
            covers=dropped_ids,
            refs=dropped_ids,
            summary=roll_summary,
            level=1,
            messages=[{"role": "system", "content": roll_summary}],
        )
        atom_ptrs = list(dict.fromkeys([roll_atom, first_id, *dropped_ids[-2:], last_id]))
        stamp_label = time.strftime("%m-%d %H:%M", time.localtime(now_ts))
        arch_text = f"{stamp_label} {reason or '归档'} [{span_label}]（{len(dropped_ids)} 条）" + (f"：{excerpt}" if excerpt else "")
        # WHY: 2026-11-19：用单一滚动键「上次换页」原地覆写，不再每晚往 past 堆新的时间戳 key
        # 占满 24 槽位、把经过她自己观测沉淀的语义认知挤进 underwater_past；完整历史仍留在 journal fold。
        merge_past_bounded(line, [{
            "key": "上次换页",
            "text": arch_text,
            "atoms": atom_ptrs,
            "water": 0,
            "peak_quote": "",
            "updated": now_ts,
        }])
        line["stats"]["last_roll_atom"] = roll_atom
        roll_note = f"- 上次换页（{stamp_label}）: {arch_text} [来源: {', '.join(atom_ptrs[:5])}]"
        line["borrowed_until"] = 0
        line["present"] = tool_rows
        line["stats"]["folds"] = 0
        if line["past"]:
            ensure_past_atom(line)
        save(line)
        if sync_targets:
            _sync_handoff(line, sync_targets, note_text=roll_note, closed=False, preserve_existing=True)
        return canonical


def roll_all(reason: str = "") -> list[str]:
    """Roll every persistent line on disk that holds unrolled non-tool_state ``present`` atoms."""
    rolled: list[str] = []
    for item in list_lines():
        if item.get("present") or item.get("claimed_windows"):
            res = _roll_one(item["name"], reason=reason)
            if res:
                rolled.append(res)
    return rolled


def roll(key: str | None = None, reason: str = "") -> str:
    """Archive rolled line snapshot(s), index dropped ``present`` atoms into ``past``, and sync handoffs."""
    if key is None:
        rolled = roll_all(reason=reason)
        return MAIN if MAIN in rolled else (rolled[0] if rolled else "")
    return _roll_one(normalize_name(key), reason=reason)


def inspect(name: str, *, now: float | None = None) -> str:
    """Read-only section view of *name* (`§3.1 (4)`): 0 LLM calls, zero mutation on *name*."""
    canonical = normalize_name(name)
    line = load(canonical)
    if line is None:
        return f"未找到线：{canonical}"
    prune_expired_claims(line, now=now, persist=False)
    claim_badges = _format_claim_badges(line, now=now)
    parts = [
        f"线名：{line['name']}",
        f"别名：{'、'.join(line['aliases']) if line['aliases'] else '（无）'}",
        f"源线：{line['source'] or '（根线）'}｜借用前缀数：{line['borrowed_until']}",
        f"认领窗口：{'、'.join(claim_badges) if claim_badges else '（无）'}",
        f"当前目标（Future）：{line['future'] or '（空，势差=0）'}",
        f"统计：{line['stats'].get('acts', 0)} 次行动，present {len(line['present'])} 条原子，past {len(line['past'])} 条索引",
    ]
    if line["past"]:
        parts.append("\n" + render_past_text(line))
    folds = []
    for item_id in reversed(line["present"]):
        try:
            row = journal.get(item_id)
        except KeyError:
            continue
        if row.get("kind") == "fold":
            msgs_list = row.get("messages") or []
            first_msg = msgs_list[0] if msgs_list and isinstance(msgs_list[0], dict) else {}
            folds.append(str(row.get("summary") or first_msg.get("content") or "").strip())
            if len(folds) >= 3:
                break
    if folds:
        parts.append("\n【最近折叠印象】\n" + "\n---\n".join(reversed(folds)))
    if line.get("follow_ups"):
        recent_fu = [
            f"- {time.strftime('%H:%M:%S', time.localtime(f.get('time') or 0))} {f.get('window')} -> {f.get('line')}: {f.get('summary')}"
            for f in line["follow_ups"][-5:]
        ]
        parts.append("\n【近期热续接留痕】\n" + "\n".join(recent_fu))
    return "\n".join(parts)


def probe(name: str, question: str, *, model: str | None = None, ask_fn=None, now: float | None = None) -> str:
    """One-shot read-only query against *name*'s shared prefix (`§3.1 (4)`); never mutates *name*."""
    import mods.chat as chat
    from mods import llm

    canonical = normalize_name(name)
    prompt = str(question or "").strip()
    if not prompt:
        raise ValueError("probe 的 question 不能为空")
    line = load(canonical)
    if line is None:
        return f"未找到线：{canonical}"
    prune_expired_claims(line, now=now, persist=False)
    claim_badges = _format_claim_badges(line, now=now)
    claim_str = f"｜认领窗口：{'、'.join(claim_badges)}" if claim_badges else ""
    items = prefix_items(line)
    temp_view = {"ctx": f"probe-{_safe_stem(canonical)}", "started": 0.0, "items": items, "stats": {}}
    rendered = segment.render(temp_view, reasoning=chat.get_reasoning_mode(), mark_borrowed=True)
    try:
        base_prompt = chat._view._base_prompt()
    except RuntimeError:
        base_prompt = []
    head = [*chat.get_prompt(), *base_prompt,
            {"role": "system", "content": f"这是一次对线 {canonical} 的只读前缀探针（Probe）；只根据已有上下文简要回答问题，不修改任何状态。"}]
    query_msg = {
        "role": "user",
        "content": f"【只读探针追问｜当前目标：{line['future'] or '无'}{claim_str}】\n{prompt}",
    }
    full_messages = [*head, *rendered, query_msg]
    if ask_fn is not None:
        return str(ask_fn(full_messages)).strip()
    session = llm.Chat(
        model=model or chat.get_model(),
        messages=full_messages,
        chat_client=llm.get_client(),
        description_cache=chat.description_cache,
        do_process_image=False,
    )
    session.preserve_native = True
    session.keep_reasoning = False
    pieces: list[str] = []
    session.chat(recall_func=lambda chunk: pieces.append(str(chunk.content))
                 if chunk.role == "assistant" and chunk.content else None, stream=False)
    return "".join(pieces).strip()


def _line_head_text(line: dict) -> str:
    past_txt = " ".join(f"{p.get('key', '')} {p.get('text', '')} {p.get('peak_quote', '')}" for p in (line.get("past") or []))
    return "\n".join([
        line["name"],
        line["name"].split(":", 1)[-1],
        " ".join(line.get("aliases") or []),
        str(line.get("future") or ""),
        past_txt,
    ]).strip()


def _activation_score(line: dict, now: float) -> float:
    clock = now - now % 3600
    uses = [float(t) for t in (line.get("stats", {}).get("uses") or []) if float(t) < clock]
    changed = min(float(line.get("stats", {}).get("updated") or now), clock - 60.0)
    uses.append(changed)
    base = math.log(sum(max(clock - t, 60.0) ** -0.5 for t in uses))
    max_water = max((int(p.get("water") or 0) for p in (line.get("past") or [])), default=0)
    active_bonus = 1.0 if line.get("future") else 0.0
    return base + active_bonus + 0.25 * max_water


def recall_lines(query: str, *, exclude=(), most: int = 8, now: float | None = None) -> list[dict]:
    """Global Concern Directory (`§3.2`): exact name/alias match + Chinese bigram BM25 + ACT-R activation."""
    ts = time.time() if now is None else float(now)
    skip = {normalize_name(x) for x in (exclude or ()) if str(x or "").strip()}
    candidates = [item for item in list_lines() if item["name"] not in skip]
    if not candidates:
        return []

    folded_query = str(query or "").casefold()
    query_terms = set(_notes._bigrams(str(query or "")[-1200:]))

    docs = []
    postings: dict[str, list[tuple[int, int]]] = {}
    for idx, item in enumerate(candidates):
        counts = Counter(_notes._bigrams(_line_head_text(item)))
        length = max(1, sum(counts.values()))
        docs.append((item, length))
        for term, hits in counts.items():
            postings.setdefault(term, []).append((idx, hits))
    avg_len = sum(length for _item, length in docs) / max(1, len(docs))

    bm25: dict[int, float] = {}
    for term in query_terms:
        plist = postings.get(term)
        if not plist:
            continue
        idf = math.log(1.0 + (len(docs) - len(plist) + 0.5) / (len(plist) + 0.5))
        for idx, hits in plist:
            length = docs[idx][1]
            bm25[idx] = bm25.get(idx, 0.0) + idf * hits * 2.5 / (hits + 1.5 * (0.25 + 0.75 * length / avg_len))

    scored: list[tuple[float, dict, str]] = []
    for idx, (item, _length) in enumerate(docs):
        short_name = item["name"].split(":", 1)[-1]
        exact_hit = ""
        for word in [item["name"], short_name, *(item.get("aliases") or [])]:
            if word and len(word) >= 2 and not word.isdigit() and _notes._appears(word, folded_query):
                exact_hit = word
                break
        lex_score = bm25.get(idx, 0.0)
        act_score = _activation_score(item, ts)
        if not exact_hit and lex_score <= 0.0 and not (not folded_query.strip() and item.get("future")):
            continue
        total = (10.0 if exact_hit else 0.0) + lex_score + 0.35 * act_score
        scored.append((total, item, exact_hit))

    scored.sort(key=lambda entry: entry[0], reverse=True)
    results = []
    for score, item, exact_hit in scored[:most]:
        past_brief = "；".join(f"{p.get('key')}: {p.get('text')[:40]}" for p in (item.get("past") or [])[:2])
        results.append({
            "name": item["name"],
            "aliases": list(item.get("aliases") or []),
            "future": item.get("future", ""),
            "past_brief": past_brief,
            "claimed_windows": list(item.get("claimed_windows") or []),
            "matched": exact_hit,
            "score": round(score, 3),
        })
    return results


def tail_hint(line_name: str, query_text: str = "", *, consume_follow_ups: bool = False, now: float | None = None) -> str:
    """Ephemeral request-tail ``Hint`` (`§4.1`): ``future`` + ``follow_ups`` + BM25 concern directory (never persisted)."""
    canonical = normalize_name(line_name)
    line = load(canonical)
    parts: list[str] = []
    if line is not None:
        prune_expired_claims(line, now=now, persist=True)
        goal = line.get("future") or "（当前无未闭环势差；开启或更新目标可用 line__future，折叠进展用 line__compact，仅显式传 close=True 才会关闭目标）"
        own_claims = _format_claim_badges(line, now=now)
        claim_line = f"\n当前热认领窗口：{'、'.join(own_claims)}" if own_claims else ""
        parts.append(f"【当前所在之线：{canonical}】\n未来/当前目标：{goal}{claim_line}")
        if canonical == MAIN and line.get("follow_ups"):
            fu_lines = [
                f"- {time.strftime('%H:%M:%S', time.localtime(f.get('time') or 0))} 窗口 {f.get('window')} 已单跳续接至 {f.get('line')}：{f.get('summary')}"
                for f in line["follow_ups"][-8:]
            ]
            parts.append("【主线免唤醒留痕（近期热连接动态）】\n" + "\n".join(fu_lines))
            if consume_follow_ups:
                with _lock:
                    fresh = load(canonical)
                    if fresh is not None:
                        fresh["follow_ups"] = []
                        save(fresh, touch_updated=False)
    if canonical == MAIN:
        active = list_active_lines(now=now)
        if active:
            ts = time.time() if now is None else float(now)
            surfaced = [
                item for item in active
                if item.get("claimed_windows")
                or (float((item.get("stats") or {}).get("updated") or 0.0) > 0
                    and (ts - float((item.get("stats") or {}).get("updated") or 0.0)) <= BOARD_IDLE_TTL_SECS)
            ]
            surfaced.sort(
                key=lambda item: (bool(item.get("claimed_windows")), float((item.get("stats") or {}).get("updated") or 0.0)),
                reverse=True,
            )
            if surfaced:
                board = []
                for item in surfaced[:10]:
                    badges = _format_claim_badges(item, now=now)
                    board.append(
                        f"- {item['name']}"
                        + (f"（认领 {'、'.join(badges)}）" if badges else "")
                        + f"：{item['future'][:80]}"
                    )
                parts.append("【当前活跃专注线态势】\n" + "\n".join(board))

    recalled = recall_lines(query_text, exclude=[canonical], most=6, now=now)
    if recalled:
        rec_lines = []
        for r in recalled:
            detail = f"目标={r['future']}" if r["future"] else "（休眠）"
            if r["past_brief"]:
                detail += f"｜过去={r['past_brief']}"
            rec_lines.append(f"- {r['name']}（{detail}）")
        triage_rule = (
            "先查后分：优先 line__send 续接既有他线，新事项再 line__fork；仅一句话寒暄才直接开口回复（勿调 line__send 投给自己）"
            if canonical == MAIN
            else "先查后分：优先 line__send 续接既有线，新事项再 line__fork"
        )
        parts.append(f"【全局关注目录关联召回（{triage_rule}）】\n" + "\n".join(rec_lines))
    elif canonical == MAIN:
        parts.append("【分诊准则】优先 line__send 续接既有他线，新事项再 line__fork；仅一句话寒暄才直接开口回复（勿调 line__send 投给自己）。")
    return "\n\n".join(parts)
