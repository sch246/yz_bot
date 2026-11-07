"""Plain Markdown notes she keeps.

``self`` is who she is (it replaced the old persona prompt), ``global`` how
she does things (procedural memory), ``recent`` the blackboard: what is
going on right now plus ``@ref`` lines for the next construct run (2026-11-01),
``todo`` what is still open, ``awaker``
when she wakes herself, one note per person and per group what she knows and
what happened with them, and ``diary:<day|month|year>`` the diary each settle
rewrites for its day.

WHY: 2026-10-22 维护者："能用普通文件的不搞复杂系统"。这些都是她随时能读写的普通
Markdown 文件，维护者也能用任何编辑器改。

WHY: files, not storage JSON, so the maintainer can read and fix them with any
editor while the Bot runs.  Every overwrite or deletion first copies the old
text under ``.history/``: nobody checks an edit before it lands, so a wrong one
must stay recoverable.  A private chat has no note of its own; it is the
person's note (decided 2026-10-19).
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import threading
import time

from mods import storage


ROOT = Path("data") / "notes"
_lock = threading.RLock()
# WHY: 2026-10-26 维护者指出：以前 _SCOPE 只许写死的那几种名字，她没法随手建专题笔记，只好把什么都往
# global 里塞。现在放开任意名字或「分类:名字」（如 消消乐、topic:线代），同时守住 person:/group: 必须用
# 号码、diary: 必须用日期、years 与 policy 不走普通笔记。
# WHY: 2026-11-01：log: 是引用聊天记录区间的写法（segment.resolve_refs），不能同时是笔记名。
_NAME = r"[0-9A-Za-z_\u4e00-\u9fff-]+"
_SCOPE = re.compile(
    rf"^(?:(?!years$|policy$){_NAME}|person:\d+|group:\d+|diary:\d{{4}}(?:-\d{{2}}(?:-\d{{2}})?)?"
    rf"|(?!person:|group:|diary:|policy:|years:|log:){_NAME}:{_NAME})$"
)


def _path(scope: str) -> Path:
    if not _SCOPE.fullmatch(scope):
        raise ValueError(
            "scope 可以是 global、recent、todo、awaker、person:<QQ号>、group:<群号>、diary:<日期>，"
            "或你自己起的专题笔记名（如 消消乐、topic:线代；person/group 冒号后写号码）"
        )
    if ":" not in scope:
        return ROOT / f"{scope}.md"
    kind, number = scope.split(":")
    return ROOT / kind / f"{number}.md"


def is_note_path(path: str | Path) -> bool:
    try:
        Path(path).resolve().relative_to(ROOT.resolve())
        return True
    except ValueError:
        return False


def resolve_path(path: str) -> tuple[str, tuple[int, int] | None]:
    """Resolve a plain path or a note/policy scope (with optional ``@Lfirst-last``) to a file path."""
    raw = path.strip()
    line_slice = None
    match = re.fullmatch(r"(.+)@L([1-9]\d*)-L?([1-9]\d*)", raw)
    if match:
        raw, first, last = match[1].strip(), int(match[2]), int(match[3])
        line_slice = (first - 1, last)
    if raw.startswith("policy:"):
        return _policy_path(raw.split(":", 1)[1].strip()).as_posix(), line_slice
    if raw in ("years", "years.md"):
        return (ROOT / "years.md").as_posix(), line_slice
    if raw.replace("\\", "/") == "data/notes/policy/settle.md":
        return _policy_path("settle").as_posix(), line_slice
    if "/" not in raw and "\\" not in raw and not os.path.exists(raw):
        stem = raw[:-3] if raw.endswith(".md") else raw
        if _SCOPE.fullmatch(stem):
            return _path(stem).as_posix(), line_slice
    return raw, line_slice


def scope_of(window: tuple) -> str:
    return f"group:{window[1]}" if window[0] == "group" else f"person:{window[1]}"


def custom_name(scope: str) -> str | None:
    """WHY: `.setname` 自定义称呼不再进入画面（画面用 chatlog 的 QQ 名），改作这个人笔记的一部分。"""
    if not scope.startswith("person:"):
        return None
    value = storage.get("users", scope.split(":")[1]).get("name")
    return str(value) if value else None


def read(scope: str) -> str:
    path = _path(scope)
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def mtime(scope: str) -> float:
    """Last modification time of one note file, or 0.0 when it does not exist."""
    path = _path(scope)
    try:
        return path.stat().st_mtime if path.is_file() else 0.0
    except OSError:
        return 0.0


def years(last: int = 8) -> list[str]:
    """The newest lines of the year index: one line per archived segment, oldest first."""
    try:
        lines = [line.rstrip() for line in (ROOT / "years.md").read_text(encoding="utf-8").splitlines()
                 if line.startswith("- ")]
    except OSError:
        return []
    return lines[-last:]


def render(scope: str) -> str:
    """One note as the model sees it, or empty when there is nothing to show.

    WHY: 2026-10-26 维护者：每一段上下文开头都写明它来自哪个文件，她一眼就能看到改哪里。
    """
    match = re.fullmatch(r"(.+)@L([1-9]\d*)-L?([1-9]\d*)", scope)
    if match:
        name, first, last = match[1], int(match[2]), int(match[3])
        lines = read(name).splitlines()
        if not 1 <= first <= last <= len(lines):
            raise ValueError(f"{name} 共 {len(lines)} 行；选区须满足 1 <= 起行 <= 末行 <= 总行数")
        # WHY: 整篇人物笔记夹着多年不同话题。选区与 read_file 共用同一语法，
        # 行号按源文件计算，来源保留选区，不能让一小段冒充整份笔记。
        body = "\n".join(f"{number}: {lines[number - 1]}" for number in range(first, last + 1))
        return f"## 笔记 {scope}（{_path(name).as_posix()}，第 {first}–{last} 行／共 {len(lines)} 行；其余未载入）\n{body}"
    raw = read(scope)
    body = raw.strip()
    name = custom_name(scope)
    if name:
        body = f"维护者设定的称呼：{name}\n{body}".strip()
    lines_count = len(raw.splitlines())
    return f"## 笔记 {scope}（{_path(scope).as_posix()}，开场全文，共 {lines_count} 行、{len(body)} 字）\n{body}" if body else ""


def note_hash(scope: str) -> str:
    """A compact fingerprint of one note's rendered body to detect mid-segment edits."""
    import hashlib

    text = render(scope).strip()
    return hashlib.md5(text.encode("utf-8")).hexdigest() if text else ""


def _backup(path: Path) -> None:
    if not path.is_file():
        return
    relative = path.relative_to(ROOT)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = ROOT / ".history" / relative.parent / f"{relative.stem}.{stamp}{path.suffix}"
    number = 1
    while target.exists():
        number += 1
        target = target.with_name(f"{relative.stem}.{stamp}-{number}{path.suffix}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(path.read_bytes())


def backup_file(path: str | Path) -> None:
    """Back up an existing note file under ``.history/`` before write_file modifies or deletes it.

    WHY: 维护者 2026-10-23 定：changes 是只往后加的流水账，旧内容都还在文件里；以前每追加
    一行都把整个旧文件复制进 .history，备份总量随条数平方增长（一万条约 15 GB）。
    """
    target = Path(path)
    if not target.is_file() or not is_note_path(target):
        return
    relative = target.resolve().relative_to(ROOT.resolve())
    if (relative.parts and relative.parts[0] == ".history") or relative == Path("policy/changes.md"):
        return
    with _lock:
        _backup(ROOT / relative)


def _replace(path: Path, text: str, *, keep_empty=False) -> str:
    """Back up, then write or delete one file; returns the text it replaced."""
    with _lock:
        previous = path.read_text(encoding="utf-8") if path.is_file() else ""
        if previous.strip() == text.strip() and (not keep_empty or path.is_file()):
            return previous
        _backup(path)
        if not text.strip() and not keep_empty:
            if path.is_file():
                path.unlink()
            return previous
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_text(text.strip() + "\n", encoding="utf-8")
        os.replace(temporary, path)
        return previous


def write(scope: str, text: str) -> str:
    return _replace(_path(scope), text)


# WHY: 策略文件是她在反思里能改的那一层（2026-10-22）：changes 是每次改动的原因、预期和事后评估，
# proposals 是要维护者动手的改动。2026-11-01 起没有交接和整段压力：construct 是每次唤醒前
# 构造上下文的说明，settle 是她给沉淀写的附加说明（原 handoff）；tidy.py 与 opening 不再读取。
POLICIES = ("construct", "settle", "changes", "proposals")


def _policy_path(name: str) -> Path:
    if name not in POLICIES:
        raise ValueError("策略文件只有 " + "、".join(POLICIES))
    path = ROOT / "policy" / f"{name}.md"
    if name == "settle" and not path.exists():
        # 给交接（更早是图书管理员）写的附加说明，现在给沉淀；第一次读到时改名。
        for old in (ROOT / "policy" / "handoff.md", ROOT / "policy" / "librarian.md"):
            if old.is_file():
                os.replace(old, path)
                break
    return path


def read_policy(name: str) -> str:
    path = _policy_path(name)
    return path.read_text(encoding="utf-8") if path.is_file() else ""


def write_policy(name: str, text: str) -> str:
    return _replace(_policy_path(name), text)


def _listed() -> list[tuple[str, Path]]:
    """(scope, path) of every note listing() shows, without reading any of them."""
    if not ROOT.is_dir():
        return []
    # WHY: 2026-11-07 量到 rglob 每次要走完 .history 里上万份备份（每次约 0.3 秒，构造每次
    # 唤醒调好几次）。收录范围本来就只有顶层和一层子目录，直接只走这两层。
    candidates = [path for path in ROOT.glob("*.md")]
    for directory in ROOT.iterdir():
        if directory.is_dir() and directory.name not in (".history", "diary", "policy"):
            candidates.extend(directory.glob("*.md"))
    found = []
    for path in candidates:
        relative = path.relative_to(ROOT)
        scope = path.stem if len(relative.parts) == 1 else f"{relative.parts[0]}:{path.stem}"
        # self 总在系统提示最前面，不用列进"可以召回的东西"。
        if not _SCOPE.fullmatch(scope) or scope == "self":
            continue
        found.append((scope, path))
    return found


def listing(limit: int = 60) -> list[dict]:
    """Existing notes, most recently changed first, with their first line."""
    found = []
    for scope, path in _listed():
        raw = path.read_text(encoding="utf-8")
        text = raw.strip()
        found.append({"scope": scope, "changed": path.stat().st_mtime,
                      "first": text.split("\n", 1)[0][:80], "chars": len(text),
                      "lines": len(raw.splitlines())})
    found.sort(key=lambda item: item["changed"], reverse=True)
    return found[:limit]


_SIMILAR_CHUNK = 1200
_similar_cache: dict[str, tuple] = {}  # scope -> (mtime_ns, size, [(first, last, total, Counter)])


def _bigrams(text: str) -> list[str]:
    text = text.casefold()
    tokens = re.findall(r"[a-z0-9_]+", text)
    for segment in re.sub(r"[a-z0-9_\s\W]+", " ", text).split():
        tokens.extend(segment[i:i + 2] for i in range(max(1, len(segment) - 1)))
    return tokens


def _passages(scope: str) -> list[tuple]:
    """A note cut into paragraphs of up to about _SIMILAR_CHUNK characters, cached by mtime and size."""
    from collections import Counter

    path = _path(scope)
    stat = path.stat()
    cached = _similar_cache.get(scope)
    if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        return cached[2]
    lines = path.read_text(encoding="utf-8").splitlines()
    found, start, buffer, size = [], 0, [], 0
    for number, line in enumerate(lines):
        if not buffer:
            start = number
        buffer.append(line)
        size += len(line) + 1
        if size >= _SIMILAR_CHUNK or (not line.strip() and size >= 200):
            found.append((start + 1, number + 1, len(lines), Counter(_bigrams("\n".join(buffer)))))
            buffer, size = [], 0
    if "\n".join(buffer).strip():
        found.append((start + 1, len(lines), len(lines), Counter(_bigrams("\n".join(buffer)))))
    _similar_cache[scope] = (stat.st_mtime_ns, stat.st_size, found)
    return found


def similar(text: str, most: int = 8, exclude=()) -> list[str]:
    """Notes and diaries whose wording overlaps *text* most, as refs (``scope`` or ``scope@La-b``).

    WHY: 2026-11-07 用生产数据离线对照（/root/yuzu-retrieval，维护者授权）：她醒来后实际打开的笔记，
    画面点名的面包屑只提示到约 15%；拿新消息末尾约 1200 字对笔记段落做中文二元组 BM25，前 10 名
    命中 58%，前 5 名 50%。同一批样本上 Qwen3-Embedding-8B 向量不如它（前 10 名 48–52%），
    混合只多一两个点，所以没有用向量，也没有索引文件，只在内存里按 mtime 缓存分词。
    这是给构造的提示，不替她选。现在是影子模式（construct.SHOW_SIMILAR）：usage 的 recall 日志
    记着面包屑没点到、她自己翻开的笔记在候选里排第几。这个比例高，才值得挂出来给她看；
    若长期很低，就删掉它，别加权重去救。
    """
    import math

    query = set(_bigrams(text[-_SIMILAR_CHUNK:]))
    index = _similar_index()
    if not query or not index["docs"]:
        return []
    docs, average = index["docs"], index["average"]
    scores: dict[int, float] = {}
    for term in query:
        postings = index["postings"].get(term)
        if not postings:
            continue
        idf = math.log(1 + (len(docs) - len(postings) + 0.5) / (len(postings) + 0.5))
        for number, hits in postings:
            length = docs[number][4]
            scores[number] = scores.get(number, 0.0) + idf * hits * 2.5 / (hits + 1.5 * (0.25 + 0.75 * length / average))
    skip = set(exclude)
    refs, seen = [], set()
    for number in sorted(scores, key=scores.get, reverse=True):
        scope, first, last, total, _length = docs[number]
        if scope in seen or scope in skip:
            continue
        seen.add(scope)
        refs.append(scope if (first, last) == (1, total) else f"{scope}@L{first}-{last}")
        if len(refs) >= most:
            break
    return refs


_similar_built: dict = {"signature": None}


def _similar_index() -> dict:
    """Inverted index over every recallable note's passages; rebuilt only when a file changed."""
    scopes = [scope for scope, _path_ in _listed() if scope not in _STANDING]
    scopes += [f"diary:{key}" for key in diaries()]
    stats = []
    for scope in scopes:
        try:
            stat = _path(scope).stat()
        except (OSError, ValueError):
            continue
        stats.append((scope, stat.st_mtime_ns, stat.st_size))
    signature = tuple(stats)
    if _similar_built["signature"] == signature:
        return _similar_built
    docs, postings = [], {}
    for scope, _mtime, _size in stats:
        try:
            passages = _passages(scope)
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        for first, last, total, counts in passages:
            number = len(docs)
            docs.append((scope, first, last, total, sum(counts.values())))
            for term, hits in counts.items():
                postings.setdefault(term, []).append((number, hits))
    live = {scope for scope, _m, _s in stats}
    for scope in [scope for scope in _similar_cache if scope not in live]:
        del _similar_cache[scope]
    _similar_built.update(signature=signature, docs=docs, postings=postings,
                          average=sum(doc[4] for doc in docs) / len(docs) if docs else 1.0)
    return _similar_built


def scope_of(path: str | Path) -> str | None:
    """The scope a file under ``data/notes/`` is read as; None for policy, backups and other files."""
    try:
        relative = Path(path).resolve().relative_to(ROOT.resolve())
    except (OSError, ValueError):
        return None
    if relative.suffix != ".md" or not 1 <= len(relative.parts) <= 2 or relative.parts[0] in (".history", "policy"):
        return None
    scope = relative.stem if len(relative.parts) == 1 else f"{relative.parts[0]}:{relative.stem}"
    return scope if _SCOPE.fullmatch(scope) else None


_USES_KEEP = 40


def used(scope: str, now: float | None = None) -> None:
    """She strung this note into a run: a ref resolved into one, or read_file on it.

    Uses within a minute of the last one count once; a resolved ref is often validated
    and then expanded in the same step.
    """
    scope = scope.split("@L", 1)[0].strip()
    now = time.time() if now is None else now
    with _lock:
        times = storage.get("agent", "note_uses").setdefault(scope, [])
        if times and now - times[-1] < 60:
            return
        times.append(round(now))
        del times[:-_USES_KEEP]


def ranked(rows: list[dict], now: float | None = None) -> list[dict]:
    """*rows* from listing(), most active first: ACT-R base-level activation ln Σ (T - tⱼ)^-0.5.

    The uses are her reads and the refs she strung into runs; the last write counts as one more.
    WHY: 2026-11-07 维护者的比喻是水下的小岛：用得越多、越近，岛越高，不用就慢慢淹没，
    不删。以前目录按修改时间挑，读得再勤、没改过的笔记也会沉下去。T 取到整点：目录挑哪些
    份随时间变，一小时最多变一次，前缀缓存才不被每次读取打断（目录显示仍按名字排）。
    """
    import math

    now = time.time() if now is None else now
    clock = now - now % 3600
    try:
        uses = storage.get("agent", "note_uses")
    except ValueError:
        uses = {}

    def activation(item: dict) -> float:
        times = [t for t in uses.get(item["scope"], []) if t < clock] + [min(item["changed"], clock - 60)]
        return math.log(sum(max(clock - t, 60.0) ** -0.5 for t in times))

    return sorted(rows, key=activation, reverse=True)


_WEB = {"http", "https", "www", "com", "png", "jpg", "jpeg", "gif", "webp", "file", "image", "the", "and"}
_EDGE = set("的了是在我你他她它们这那就也都和与及或把被给到着过吗呢吧啊呀哦嗯么还又再很太个")
_folded: dict = {"signature": None, "texts": {}}


def _folded_texts() -> dict[str, str]:
    """Every listed note's and diary's text, casefolded, rebuilt only when a file changed."""
    found = _listed() + [(f"diary:{key}", ROOT / "diary" / f"{key}.md") for key in diaries()]
    signature = []
    for scope, path in found:
        try:
            stat = path.stat()
        except OSError:
            continue
        signature.append((scope, stat.st_mtime_ns, stat.st_size))
    signature = tuple(signature)
    if _folded["signature"] != signature:
        texts = {}
        for scope, _mtime, _size in signature:
            try:
                texts[scope] = _path(scope).read_text(encoding="utf-8").casefold()
            except (OSError, UnicodeDecodeError, ValueError):
                continue
        _folded.update(signature=signature, texts=texts)
    return _folded["texts"]


def shared_terms(query: str, scope: str, most: int = 3) -> list[str]:
    """Words of *query* that also occur in the note, the ones fewest other notes contain first.

    For a note she opened that the breadcrumbs had not pointed at: words that could go on its
    ``别名：`` line. English counts only as whole words of three letters or more, without digits; a Chinese
    stretch is the longest run (up to 6 characters: aliases are short, longer runs are quoted
    chat) also found in the note, trimmed of particles at both ends.

    WHY: 2026-11-07 在 281 次历史漏网上试过三种筛法：只按稀有度排，泛词（画面、上次、当时）
    混进来；“同一词出现在三份以上笔记的候选里就丢”误伤人名（一句话常提到好几个人）；丢掉覆盖
    超过 15% 的，剩下的多是人物笔记里引用的聊天原话被截成的半句。现在只留除这份以外最多再出现
    在一份笔记或日记里的词，宁可少给；拿不准时她可以按 ctx 回看当时。
    """
    import math

    texts = _folded_texts()
    text = texts.get(scope)
    if text is None:
        try:
            text = read(scope).casefold()
        except (OSError, ValueError):
            return []
    query = query.casefold()
    pieces = {word for word in re.findall(r"(?<![a-z0-9_])[a-z][a-z_]{2,}(?![a-z0-9_])", query)
              if word not in _WEB and re.search(rf"(?<![a-z0-9_]){re.escape(word)}(?![a-z0-9_])", text)}
    for segment in re.findall(r"[\u4e00-\u9fff]+", query):
        start = 0
        while start < len(segment) - 1:
            end = start + 2
            if segment[start:end] not in text:
                start += 1
                continue
            while end < len(segment) and end - start < 6 and segment[start:end + 1] in text:
                end += 1
            piece = segment[start:end].strip("".join(_EDGE))
            if len(piece) >= 2:
                pieces.add(piece)
            start = end
    others = [body for name, body in texts.items() if name != scope]
    spread = {piece: sum(piece in body for body in others) for piece in pieces}

    def rarity(piece: str) -> float:
        return math.log((len(others) + 1) / (spread[piece] + 0.5))

    chosen = []
    for piece in sorted(pieces, key=lambda piece: (-rarity(piece), -len(piece), piece)):
        if spread[piece] > 1 or any(piece in other or other in piece for other in chosen):
            continue
        chosen.append(piece)
        if len(chosen) >= most:
            break
    return chosen


def diaries(prefix: str = "") -> list[str]:
    """Diary keys (``YYYY``, ``YYYY-MM`` or ``YYYY-MM-DD``), oldest first."""
    directory = ROOT / "diary"
    if not directory.is_dir():
        return []
    return sorted(path.stem for path in directory.glob("*.md") if path.stem.startswith(prefix))


# 每段开头就摊开的、或者本来就不是话题的笔记：画面里出现这些字不用再递。
_STANDING = {"global", "recent", "todo", "awaker", "observer"}
_ALIAS = re.compile(r"^\s*(?:[-*]\s*)?别名\s*[:：]\s*(.+?)\s*$", re.MULTILINE)


def aliases(scope: str) -> list[str]:
    """The words on the note's ``别名：`` lines, such as ``别名：小明、明哥`` in someone's note."""
    words = []
    for line in _ALIAS.findall(read(scope)):
        # 空格不算分隔：英文别名常是几个词（linear algebra），拆开以后单个 linear 也会命中。
        words.extend(word.strip() for word in re.split(r"[、,，;；/\[\]【】]+", line) if word.strip())
    return words


def _appears(word: str, folded: str) -> bool:
    # 英文那头不能紧挨着别的字母数字：笔记叫 mc，"mcdonald" 不算提到它。
    pattern = re.escape(word.casefold())
    if word[0].isascii() and word[0].isalnum():
        pattern = r"(?<![0-9a-z])" + pattern
    if word[-1].isascii() and word[-1].isalnum():
        pattern += r"(?![0-9a-z])"
    return re.search(pattern, folded) is not None


def mentioned(text: str) -> list[tuple[str, str]]:
    """Notes whose own name, or a word on their ``别名：`` line, appears in *text*: (scope, the word that matched).

    person:/group: 的名字是号码，只认别名；号码本身出现在画面里时，`agent._people` 已经把那份笔记带上了。
    """
    folded = text.casefold()
    found = []
    for item in listing(limit=10**6):
        scope = item["scope"]
        if scope in _STANDING:
            continue
        name = scope.split(":", 1)[-1]
        for word in ([] if name.isdigit() else [name]) + aliases(scope):
            if len(word) >= 2 and _appears(word, folded):
                found.append((scope, word))
                break
    return found


def where(words: list[str]) -> dict[str, list[str]]:
    """For each word, the notes and then the diaries (newest first) whose text contains it, ignoring case."""
    scopes = [item["scope"] for item in listing(limit=10**6) if item["scope"] != "observer"]
    scopes += [f"diary:{key}" for key in reversed(diaries())]
    found: dict[str, list[str]] = {word: [] for word in words}
    for scope in scopes:
        folded = read(scope).casefold()
        for word in words:
            if word.casefold() in folded:
                found[word].append(scope)
    return found
