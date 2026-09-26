"""Strictly offline archive preparation and a synthetic replay boundary probe."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, timedelta
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
from types import MappingProxyType


MODEL = "deepseek/deepseek-flash"
PROMPT_MODE = "production-default-plus-offline-review-task-v3"
REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))
_OPLOG_BINDINGS = ("_root", "_events", "_by_id", "_by_arrival", "_windows", "_next",
                   "_pending", "_notified", "_arrival_order", "_arrival_members",
                   "_arrival_skips", "_source_arrivals", "_covered", "_coverage_nodes",
                   "_mentioned_by", "_origins", "_sources", "_seen_messages", "_failed")
_CONTEXT_BINDINGS = ("_local", "_latest", "_waiters", "_turns", "_window_locks", "_arrival_links")
_CHATLOG_BINDINGS = ("_boot_anchors", "_line_positions", "_live_origins", "_window_locks",
                     "_recalls", "_live_recalls")
_STORAGE_BINDINGS = ("storage", "load_errors", "_states", "_pending_file_events")


def _days(first: date, last: date):
    if first > last:
        raise ValueError("--from must not be after --to")
    current = first
    while current <= last:
        yield current
        current += timedelta(days=1)


def _output_path(value: str, input_root: Path | None = None) -> Path:
    path = Path(value).expanduser()
    if path.exists() or path.is_symlink():
        raise ValueError("output directory must not already exist")
    resolved = path.resolve()
    if resolved.is_relative_to(REPOSITORY):
        raise ValueError("output must be outside the repository")
    if input_root is not None and resolved.is_relative_to(input_root):
        raise ValueError("output must be outside the input archive")
    return path


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def _write_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".new")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _file_hashes(root: Path) -> dict[str, str]:
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError("run contains a symlink")
        relative = str(path.relative_to(root))
        if path.is_file() and relative not in (".lock", "checkpoint.json"):
            result[relative] = _hash(path)
    return result


@contextmanager
def _locked(root: Path):
    descriptor = os.open(root / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("run is already locked by another process") from error
        yield
    finally:
        os.close(descriptor)


def _verify_checkpoint(root: Path) -> dict:
    checkpoint = json.loads((root / "checkpoint.json").read_text(encoding="utf-8"))
    if checkpoint.get("schema") != 1 or checkpoint.get("files") != _file_hashes(root):
        raise ValueError("checkpoint file hashes differ; refusing resume or fork")
    segment = checkpoint.get("segment")
    rows = checkpoint.get("journal_rows")
    if (type(segment) is not int or segment < 1 or type(rows) is not int or rows < 0
            or not (root / f"segment-{segment:04d}.json").is_file()
            or json.loads((root / f"segment-{segment:04d}.json").read_text(encoding="utf-8"))
               .get("journal_rows") != rows
            or len(_journal_rows(root)) != rows):
        raise ValueError("checkpoint metadata differs from the event journal")
    return checkpoint


def _checkpoint(root: Path, segment: int, journal_rows: int) -> None:
    _write_json(root / "checkpoint.json", {"schema": 1, "segment": segment,
                                            "journal_rows": journal_rows,
                                            "files": _file_hashes(root)})


def _identity_digest(salt: str, kind: str, target: int, bot_id: int, bot_name: str) -> str:
    raw = json.dumps([salt, kind, target, bot_id, bot_name], ensure_ascii=False,
                     separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _journal_rows(root: Path) -> list[dict]:
    rows = []
    for path in sorted((root / "data/event_stream").glob("????????.jsonl")):
        rows.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    return rows


def _assert_settled(rows: list[dict]) -> None:
    returned: dict[str, set[int]] = {}
    for row in rows:
        if row["kind"] == "result":
            returned.setdefault(row["source"], set()).update(
                item["position"] for item in row["returns"])
        elif row["kind"] == "arrival" and "_stream_results" in row["event"]:
            result = row["event"]["_stream_results"]
            returned.setdefault(result["source"], set()).update(
                item["position"] for item in result["returns"])
    for row in rows:
        if row["kind"] != "output":
            continue
        assistant = row.get("assistant")
        calls = (assistant.get("tool_calls") or [] if isinstance(assistant, dict)
                 else row.get("actions") or [])
        if set(range(len(calls))) != returned.get(row["id"], set()):
            raise RuntimeError("model action has no durable result; checkpoint is unsafe")


def _assert_storage_saved(storage_module) -> None:
    for namespace, values in storage_module.storage.items():
        for name, value in values.items():
            path = Path(storage_module._path(namespace, name))
            state = storage_module._states.get((namespace, name))
            if (not path.is_file() or state is None or
                    state.baseline_digest != storage_module._serialize_reporting(value)[1]):
                raise RuntimeError("isolated storage was not fully saved; checkpoint is unsafe")


def _append_jsonl(path: Path, rows: list[dict]) -> None:
    with path.open("a", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _prepare(root: Path, kind: str, target: int, first: date, last: date, output: Path) -> dict:
    from mods import chatlog

    if target <= 0:
        raise ValueError("--target must be a positive integer")
    if not root.is_dir():
        raise ValueError("chatlog root is not a directory")
    days = list(_days(first, last))
    paths = []
    for day in days:
        relative = Path(kind) / str(target) / day.strftime("%Y-%m") / f"{day:%d}.log"
        source = root / relative
        if source.is_symlink() or not source.is_file() or not source.resolve().is_relative_to(root):
            raise ValueError(f"archive day {day.isoformat()} is absent or unsafe")
        paths.append((day, source))

    output.mkdir(mode=0o700, parents=True)
    try:
        archive = output / "archive"
        archive.mkdir(mode=0o700)
        overall = hashlib.sha256()
        entries = []
        for day, source in paths:
            raw = source.read_bytes()
            text = raw.decode("utf-8", errors="strict")
            frozen = archive / day.strftime("%Y-%m")
            frozen.mkdir(mode=0o700, exist_ok=True)
            snapshot = frozen / f"{day:%d}.log"
            descriptor = os.open(snapshot, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
            digest = hashlib.sha256(raw).hexdigest()
            overall.update(day.isoformat().encode("ascii") + b"\0" + bytes.fromhex(digest))
            records = chatlog.parse_log(
                text, kind=kind, target=target, day=(day.year, day.month, day.day)
            )
            if any("time" not in record for record in records):
                raise ValueError(f"archive day {day.isoformat()} has a record without time")
            entries.append({"date": day.isoformat(), "sha256": digest, "events": len(records)})
        manifest = {
            "schema": 1,
            "model": MODEL,
            "prompt_mode": PROMPT_MODE,
            "archive_kind": kind,
            "input_sha256": overall.hexdigest(),
            "dates": entries,
            "events": sum(entry["events"] for entry in entries),
            "status": "prepared-only",
        }
        descriptor = os.open(output / "manifest.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        return manifest
    except BaseException:
        shutil.rmtree(output)
        raise


def _ordered_snapshot(output: Path, kind: str, target: int, manifest: dict) -> list[dict]:
    from mods import chatlog

    ordered = []
    overall = hashlib.sha256()
    for entry in manifest["dates"]:
        day = date.fromisoformat(entry["date"])
        path = output / "archive" / day.strftime("%Y-%m") / f"{day:%d}.log"
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise ValueError("frozen archive hash mismatch")
        overall.update(day.isoformat().encode("ascii") + b"\0" + bytes.fromhex(entry["sha256"]))
        records = chatlog.parse_log(
            raw.decode("utf-8", errors="strict"), kind=kind, target=target,
            day=(day.year, day.month, day.day),
            origin_path=f"{kind}/{target}/{day:%Y-%m}/{day:%d}.log",
        )
        if len(records) != entry["events"] or any("time" not in record for record in records):
            raise ValueError("frozen archive event count or time mismatch")
        ordered.extend((day, record["time"], position, record)
                       for position, record in enumerate(records))
    if overall.hexdigest() != manifest["input_sha256"] or len(ordered) != manifest["events"]:
        raise ValueError("frozen archive aggregate mismatch")
    ordered.sort(key=lambda item: (item[0], item[1], item[2]))
    return [record for _, _, _, record in ordered]


OFFLINE_FACT = (
    "正在离线回看已经发生的历史。本次任务是从这条冻结历史的有序未读集合分段正式阅读，"
    "并在过程中自行整理；如何分段与整理由你自己决定。你不能影响或回复当时的参与者。"
)
STRATEGIES = {
    "baseline": "",
    "progressive-index": (
        "让历史真实进入统一经历流并形成可反查记忆。status 只用于定向；mentions 和 read_messages 都会安排正式阅读。"
        "状态定向后，自主选择有界 take，并在读取新一段、整理、判断继续或暂停之间循环。"
        "cover 得到的结论若开始并列累积，也要继续递归整理；同一来源只在未来检索入口确实不同"
        "（如人物、话题、任务或反例）时进入多个索引。未读数量本身不是继续读取的理由；"
        "若说不清下一段的用途就暂停，并只用 hint 保存真正未完成的任务或会改变下次行为的自我观察。"
    ),
    "bounded-hierarchy": (
        "让历史真实进入统一经历流并形成可反查记忆。首次可用一次 status 定向；"
        "之后每次只 take 20 到 40 条，读完一批先整理，再决定是否读取下一批，不连续囤积多批原文。"
        "cover 结论也是经历：同层结论积累到数个时，把仍值得保留的结论继续收拢成上位节点；"
        "同一来源可在人物、话题、任务或反例等未来入口确实不同时进入多个索引。"
        "用 hint 保留当前阶段、下一步和会改变后续行为的自我观察，避免忘记进度后重复阅读或回复。"
    ),
    "grounded-hierarchy": (
        "让历史真实进入统一经历流并形成可反查记忆。首次可用一次 status 定向；"
        "之后每次只 take 16 到 24 条，读完一批先整理并看到结果，再决定是否读取下一批。"
        "cover 只能填写眼前实际出现过的正式事件号，不要按数字连续性补齐或猜号；需要回看范围时用"
        " recall_events 的 anchor 或 start/end。积累三个左右的同层总结后，覆盖这些总结所在的旧输出事件，把仍值得保留的内容"
        "收拢成上位节点；同一来源可在未来检索入口确实不同时进入多个索引。"
        "用 hint 保留当前阶段、下一步和会改变后续行为的自我观察，避免忘记进度后重复阅读或回复。"
    ),
    "grounded-loop": (
        "让历史真实进入统一经历流并形成可反查记忆。首次可用一次 status 定向；"
        "之后每轮只按这个顺序决定下一步：眼前有尚未整理的新输入，就立刻用实际出现过的正式号做一次"
        "简洁 cover；否则有三个左右仍并列的同层总结，就覆盖这些总结所在的旧输出事件形成上位节点；"
        "否则 take 20 条。不要按编号连续性补齐或猜号，需要回看时才用 recall_events 的范围参数。"
        "不要在思考里重新复述或重建全部历史，只需简短确认当前属于上述哪种情况并行动。"
        "同一来源可在未来检索入口确实不同时进入多个索引；hint 只在阶段或下一步确实改变时更新。"
    ),
    "serial-loop": (
        "让历史真实进入统一经历流并形成可反查记忆。首次可用一次 status 定向；之后每次输出只执行"
        "一个核心动作，不要在同一次输出里同时 cover 和 take。眼前有尚未整理的新输入就用实际出现的"
        "正式号做一次简洁 cover；否则有三个左右仍并列的同层总结，就覆盖这些总结所在的旧输出事件"
        "形成上位节点；否则 take 20 条。不要按编号连续性补齐或猜号，需要回看时才用 recall_events 的范围参数。"
        "不要在思考里重新复述全部历史；hint 只在阶段或下一步确实改变时更新。"
    ),
    "serial-loop-reasoning": (
        "让历史真实进入统一经历流并形成可反查记忆。首次可用一次 status 定向；之后每次输出只执行"
        "一个核心动作，不要在同一次输出里同时 cover 和 take。眼前有尚未整理的新输入就用实际出现的"
        "正式号做一次简洁 cover；否则有三个左右仍并列的同层总结，就覆盖这些总结所在的旧输出事件"
        "形成上位节点；否则 take 20 条。不要按编号连续性补齐或猜号，需要回看时才用 recall_events 的范围参数。"
        "已完成输出的思考会作为该输出的一部分保留，直到输出被覆盖；把会影响后续行动的判断明确写在"
        "思考或结论中，不要每轮重新推导。hint 只在阶段或下一步确实改变时更新。"
    ),
    "serial-loop-hint": (
        "让历史真实进入统一经历流并形成可反查记忆。首次可用一次 status 定向；之后每次输出只执行"
        "一个核心动作，不要在同一次输出里同时 cover 和 take。眼前有尚未整理的新输入就用实际出现的"
        "正式号做一次简洁 cover；否则有三个左右仍并列的同层总结，就覆盖这些总结所在的旧输出事件"
        "形成上位节点；否则 take 20 条。不要按编号连续性补齐或猜号，需要回看时才用 recall_events 的范围参数。"
        "需要跨请求继续、忘掉后会重新推导的未兑现决定，用 edit_hint 留下足以接续的简短待办；必要时"
        "附相关正式号、阻碍或决定下一步的理由。成功后更新或清空，工具失败时按实际结果修正。"
        "不要把可查询的未读数、未读坐标、全部根或长期摘要复制进 hint；长期证据仍由 cover 保存。"
    ),
    "serial-loop-checkpoint": (
        "让历史真实进入统一经历流并形成可反查记忆。首次 status 后先用 edit_hint 写下当前目标和下一项"
        "尚未兑现的动作；之后每次请求先读 hint，除非刚收到的实际结果使它失效，否则直接执行而不重新"
        "推导。每次输出只执行 cover 或 take 中的一个核心动作；edit_hint 是附带的工作记忆动作，可以"
        "与核心动作同批调用。眼前有尚未整理的新输入就用实际出现的正式号做一次简洁 cover；否则有"
        "三个左右仍并列的同层总结，就覆盖这些总结所在的旧输出事件形成上位节点；否则 take 20 条。"
        "只有下一步、阻碍或理由改变时才整体更新 hint，执行完成就删掉对应待办；总长度不超过 200 字。"
        "不要复制可查询的未读数、未读坐标、全部根或长期摘要，不要按编号连续性猜号。"
    ),
    "serial-loop-linked": (
        "让历史真实进入统一经历流并形成可反查记忆。首次可用一次 status 定向；之后每次输出只执行"
        "一个核心动作，不要在同一次输出里同时 cover 和 take。眼前有尚未整理的新输入就用实际出现的"
        "正式号做一次简洁 cover；否则有三个左右仍并列的同层总结，就覆盖这些总结所在的旧输出事件"
        "形成上位节点；否则 take 20 条。不要按编号连续性补齐或猜号，需要回看时才用 recall_events 的范围参数。"
        "每次 cover 的 conclusion 都要把保留的每项事实、印象或话题入口就地标注其直接依据号；叶级"
        "结论引用本批实际输入号，父级结论引用被收拢的旧总结输出号。编号必须紧挨它所支持的语义，"
        "不能只在末尾列一串成员，也不用为未保留的琐碎内容强造条目。没有语义到下一跳的映射就不算"
        "完成整理。不要在思考里重演全部历史；hint 只在阶段或下一步确实改变时更新。"
    ),
}
PERSIST_REASONING_STRATEGIES = frozenset({"serial-loop-reasoning"})
SAFE_TOOLS = frozenset({
    "list_tools", "load_tools", "reload_tools", "recall_events",
    "event_links", "cover_events", "say", "status", "mentions", "read_messages", "take", "pull",
    "edit_hint",
})


def _safe_registry(skill_root: Path):
    from mods import tools

    registry = tools.ToolRegistry(skill_root)
    registry._initialized = True
    source = REPOSITORY / "mods" / "tools" / "meta.py"
    meta = registry._load_python("meta", source, source.read_bytes())
    selected = {name: tool for name, tool in meta.tools.items() if name in SAFE_TOOLS}
    if set(selected) != SAFE_TOOLS:
        raise RuntimeError("offline safe tool set no longer matches production meta")
    for action in ("load_tools", "reload_tools"):
        original = selected[action].call

        def only_skills(names, *, original=original):
            if not isinstance(names, list) or any(
                not isinstance(name, str) or not name.isidentifier()
                or not (skill_root / f"{name}.md").is_file()
                for name in names
            ):
                raise RuntimeError("offline tool modules must be existing isolated Markdown Skills")
            return original(names)

        selected[action].call = only_skills
        selected[action].description["function"]["description"] = (
            "只对隔离运行目录中已有的 Markdown Skill 执行该操作；不能访问 Python 模块。")
    selected["say"].description["function"]["description"] = (
        "记录离线发言意图并产生模拟回声；绝不发送 QQ 消息。")
    registry._modules["meta"] = replace(
        meta,
        description="离线历史读取、记忆覆盖、Skill 加载、待办与模拟发言",
        content=("只能使用本轮列出的离线工具。Skill 仅可从隔离目录读取、加载和重载；"
                 "不能写入 Skill，也不能执行代码、访问网络或发送真实消息。"
                 "say 只记录发言意图并产生模拟回声，不会回复当时参与者。"),
        tools=MappingProxyType(selected),
    )
    for source in sorted(skill_root.glob("*.md")):
        registry._modules[source.stem] = registry._load_candidate(source.stem, [source])
    scan = registry.scan

    def scan_skills():
        changes = scan()
        changes["deleted"] = [name for name in changes["deleted"] if name != "meta"]
        return changes

    registry.scan = scan_skills
    return registry


def _checked_in_skills() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", "mods/tools/*.md"],
        cwd=REPOSITORY, capture_output=True, check=True,
    )
    paths = [REPOSITORY / name.decode("utf-8") for name in result.stdout.split(b"\0") if name]
    return [path for path in paths if path.parent == REPOSITORY / "mods" / "tools"
            and path.is_file() and not path.is_symlink()]


def _read_llm_config(path: Path) -> dict:
    if (not path.is_file() or path.is_symlink() or path.name == "config.json"
            or path.name.startswith(".env") or path.resolve().is_relative_to(REPOSITORY)):
        raise ValueError("--llm-config must be an explicit file outside the repository")
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or set(raw) != {"base_url", "api_key"}:
        raise ValueError("LLM config must contain only literal base_url and api_key")
    if any(not isinstance(raw[key], str) or not raw[key].strip() or raw[key].startswith("${")
           for key in ("base_url", "api_key")):
        raise ValueError("LLM config values must be nonempty literals, not environment references")
    return raw


def _run(prepared: Path, output: Path, kind: str, target: int, bot_id: int, bot_name: str,
         config: dict, max_calls: int, max_prompt_tokens: int,
         max_completion_tokens: int, max_output_tokens_per_call: int,
         *, strategy: str = "baseline", resume: bool = False) -> dict:
    if resume:
        if not output.is_dir() or output.is_symlink() or output.resolve().is_relative_to(REPOSITORY):
            raise ValueError("resume needs an existing run directory outside the repository")
    else:
        output.mkdir(mode=0o700, parents=True)
    with _locked(output):
        return _run_locked(prepared, output, kind, target, bot_id, bot_name, config,
                           max_calls, max_prompt_tokens, max_completion_tokens,
                           max_output_tokens_per_call, strategy=strategy, resume=resume)


def _fork(source: Path, output: Path) -> dict:
    if not source.is_dir() or source.is_symlink() or source.resolve().is_relative_to(REPOSITORY):
        raise ValueError("fork source must be an existing run outside the repository")
    with _locked(source):
        checkpoint = _verify_checkpoint(source)
        output.mkdir(mode=0o700, parents=True)
        try:
            shutil.copytree(source, output, dirs_exist_ok=True,
                            ignore=lambda directory, _names: (
                                {".lock", "checkpoint.json"} if Path(directory) == source else set()))
            if _file_hashes(output) != checkpoint["files"]:
                raise ValueError("fork copy does not match the source checkpoint")
            _write_json(output / "lineage.json", {
                "parent_checkpoint_sha256": _hash(source / "checkpoint.json"),
                "parent_segment": checkpoint["segment"],
            })
            _checkpoint(output, checkpoint["segment"], checkpoint["journal_rows"])
        except BaseException:
            shutil.rmtree(output)
            raise
    return {"status": "forked", "segment": checkpoint["segment"]}


def _run_locked(prepared: Path, output: Path, kind: str, target: int, bot_id: int,
                bot_name: str, config: dict, max_calls: int, max_prompt_tokens: int,
                max_completion_tokens: int, max_output_tokens_per_call: int,
                *, strategy: str, resume: bool) -> dict:
    from mods import chat, chatlog, connect, context, identity, llm, message, oplog, storage

    if (target <= 0 or bot_id <= 0 or not bot_name.strip() or strategy not in STRATEGIES
            or min(max_calls, max_prompt_tokens, max_completion_tokens,
                   max_output_tokens_per_call) <= 0):
        raise ValueError("target, bot id, bot name and all budgets must be valid")
    if (connect._server is not None or message._worker is not None or llm.client is not None
            or storage._worker is not None or storage._observer is not None):
        raise RuntimeError("run requires a fresh process without Bot or storage workers")
    manifest = json.loads((prepared / "manifest.json").read_text(encoding="utf-8"))
    if (manifest.get("schema") != 1 or manifest.get("model") != MODEL
            or manifest.get("prompt_mode") != PROMPT_MODE
            or manifest.get("archive_kind") != kind
            or manifest.get("status") != "prepared-only"
            or not isinstance(manifest.get("dates"), list)):
        raise ValueError("prepared manifest is incompatible with this replay")
    ordered = _ordered_snapshot(prepared, kind, target, manifest)
    if not ordered:
        raise ValueError("prepared archive contains no events")
    prepared_hash = _hash(prepared / "manifest.json")
    strategy_hash = hashlib.sha256(STRATEGIES[strategy].encode("utf-8")).hexdigest()
    persist_reasoning = strategy in PERSIST_REASONING_STRATEGIES
    if resume:
        checkpoint = _verify_checkpoint(output)
        run_manifest = json.loads((output / "run_manifest.json").read_text(encoding="utf-8"))
        if (run_manifest.get("schema") != 1 or run_manifest.get("prepared_sha256") != prepared_hash
                or run_manifest.get("input_sha256") != manifest["input_sha256"]
                or run_manifest.get("model") != MODEL or run_manifest.get("prompt_mode") != PROMPT_MODE
                or run_manifest.get("strategy", "baseline") != strategy
                or (run_manifest.get("strategy_sha256") is not None
                    and run_manifest["strategy_sha256"] != strategy_hash)
                or (run_manifest.get("strategy_sha256") is None and strategy != "baseline")
                or bool(run_manifest.get("persist_reasoning", False)) != persist_reasoning
                or run_manifest.get("identity_sha256") != _identity_digest(
                    run_manifest.get("salt", ""), kind, target, bot_id, bot_name)):
            raise ValueError("run manifest differs from requested prepared input or identity")
        segment = checkpoint["segment"] + 1
        prior_rows = checkpoint["journal_rows"]
    else:
        segment, prior_rows = 1, 0
        salt = os.urandom(16).hex()
        run_manifest = {"schema": 1, "prepared_sha256": prepared_hash,
                        "input_sha256": manifest["input_sha256"], "model": MODEL,
                        "prompt_mode": PROMPT_MODE, "strategy": strategy,
                        "strategy_sha256": strategy_hash,
                        "persist_reasoning": persist_reasoning, "salt": salt,
                        "identity_sha256": _identity_digest(salt, kind, target, bot_id, bot_name)}
        _write_json(output / "run_manifest.json", run_manifest)
    previous_cwd = Path.cwd()
    old_client, old_root, old_chatlog = llm.client, storage.root_path, chatlog.rootfile
    old_chat_state = (chat.settings, chat.prompts, chat.chat_groups,
                      chat.llm_config, chat.description_cache)
    old_oplog_state = {name: getattr(oplog, name) for name in _OPLOG_BINDINGS}
    old_context_state = {name: getattr(context, name) for name in _CONTEXT_BINDINGS}
    old_chatlog_state = {name: getattr(chatlog, name) for name in _CHATLOG_BINDINGS}
    old_storage_state = {name: getattr(storage, name) for name in _STORAGE_BINDINGS}
    outbound_names = ("send", "sendmsg", "_send_now")
    original_outbound = {name: getattr(message, name) for name in outbound_names}
    original_call_api = connect.call_api
    old_name, old_user_name, old_qq = identity.getname, identity.get_user_name, identity.qq
    old_bot_name, old_nicknames = identity.name, identity.nicknames
    transcript: list[dict] = []
    usage: list[dict] = []
    runtime_started = False
    stop_reason = "complete"
    result = None
    try:
        for name, value in old_oplog_state.items():
            setattr(oplog, name, None if name == "_root" else type(value)())
        for name, value in old_context_state.items():
            setattr(context, name, None if name == "_latest" else type(value)())
        for name, value in old_chatlog_state.items():
            setattr(chatlog, name, None if name == "_boot_anchors" else type(value)())
        for name, value in old_storage_state.items():
            setattr(storage, name, type(value)())
        os.chdir(output)
        storage.root_path = "data/storage"
        chatlog.rootfile = "archive"
        runtime_started = True
        if not resume:
            archive = Path("archive") / kind / str(target)
            for entry in manifest["dates"]:
                day = date.fromisoformat(entry["date"])
                source = prepared / "archive" / day.strftime("%Y-%m") / f"{day:%d}.log"
                destination = archive / day.strftime("%Y-%m") / f"{day:%d}.log"
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
                if hashlib.sha256(destination.read_bytes()).hexdigest() != entry["sha256"]:
                    raise ValueError("isolated archive copy hash mismatch")
        skills = Path("skills")
        if not resume:
            skills.mkdir(mode=0o700)
            for source in _checked_in_skills():
                shutil.copyfile(source, skills / source.name)
        else:
            storage.load()
            if storage.load_errors:
                raise ValueError("isolated storage has unreadable files")
            if len(_journal_rows(Path.cwd())) != prior_rows:
                raise ValueError("checkpoint journal row count differs")
        registry = _safe_registry(skills)
        names = {int(event["user_id"]): str((event.get("sender") or {}).get("card")
                                           or (event.get("sender") or {}).get("nickname")
                                           or event.get("user_id"))
                 for event in ordered if isinstance(event.get("user_id"), int)}
        identity.qq = bot_id
        identity.name, identity.nicknames = bot_name, [bot_name]
        identity.get_user_name = lambda user_id: (bot_name if int(user_id) == bot_id
                                                   else names.get(int(user_id), ""))
        identity.getname = lambda user_id=None, group_id=None: names.get(int(user_id), "未知") if user_id is not None else "未知"

        class LiteralClient(llm.LLMClient):
            @staticmethod
            def _resolve_config_value(value):
                return value

        client = LiteralClient({"default_model": MODEL, "providers": {"deepseek": {
            "base_url": config["base_url"], "api_key": config["api_key"],
            "models": {"deepseek-flash": {"function_calling": True, "vision": False}},
        }}})
        client.strict_tools = True
        llm.client = client
        chat.settings = []
        chat.prompts = {}
        chat.chat_groups = []
        chat.llm_config = client.config
        chat.description_cache = {}

        def finalize_usage() -> None:
            if usage and usage[-1]["source"] != "api" and "reserved_completion_tokens" in usage[-1]:
                usage[-1]["observed_completion_bytes"] = usage[-1]["completion_tokens"]
                usage[-1]["completion_tokens"] = usage[-1]["reserved_completion_tokens"]
                usage[-1]["source"] = "reserved-max-no-api-usage"

        def request_policy(selection: str, request: dict) -> dict:
            finalize_usage()
            if selection != MODEL:
                raise RuntimeError("offline replay model changed")
            if len(usage) >= max_calls:
                raise RuntimeError("max-calls budget reached")
            prompt_bound = len(json.dumps(request, ensure_ascii=False).encode("utf-8")) + 1024
            if sum(row["prompt_tokens"] for row in usage) + prompt_bound > max_prompt_tokens:
                raise RuntimeError("max-prompt-tokens budget reached")
            remaining = max_completion_tokens - sum(row["completion_tokens"] for row in usage)
            if remaining <= 0:
                raise RuntimeError("max-completion-tokens budget reached")
            allowance = min(remaining, max_output_tokens_per_call)
            usage.append({"model": selection, "prompt_tokens": prompt_bound,
                          "completion_tokens": 0, "reserved_completion_tokens": allowance,
                          "source": "utf8-upper-bound"})
            return {"max_tokens": allowance}

        def on_chunk(chunk) -> None:
            if chunk.total_tokens:
                if (sum(row["prompt_tokens"] for row in usage[:-1]) + chunk.prompt_tokens > max_prompt_tokens
                        or sum(row["completion_tokens"] for row in usage[:-1])
                        + chunk.completion_tokens > max_completion_tokens):
                    raise RuntimeError("provider usage exceeded offline replay budget")
                usage[-1].update(prompt_tokens=chunk.prompt_tokens,
                                 completion_tokens=chunk.completion_tokens,
                                 cached_tokens=chunk.cached_tokens, source="api")
            elif chunk.role in ("assistant", "tool"):
                usage[-1]["completion_tokens"] += len(chunk.content.encode("utf-8"))

        client.request_policy = request_policy
        window = (kind, target)
        echo_number = max((-row["event"]["message_id"] for row in _journal_rows(Path.cwd())
                           if row.get("kind") == "arrival"
                           and row["event"].get("post_type") == "message_sent"
                           and isinstance(row["event"].get("message_id"), int)
                           and row["event"]["message_id"] < 0), default=0) if resume else 0

        def dry_say(body: str, destination: tuple) -> int:
            nonlocal echo_number
            if destination != window:
                raise RuntimeError("offline say target is outside the prepared window")
            echo_number += 1
            message_id = -echo_number
            event = {"post_type": "message_sent", "message_type": kind,
                     "message_id": message_id, "message": body, "raw_message": body,
                     "user_id": bot_id, "time": ordered[-1]["time"] + echo_number,
                     "sender": {"user_id": bot_id, "nickname": "离线模拟 Bot"}}
            event["group_id" if kind == "group" else "target_id"] = target
            arrival = oplog.arrive(window, event)
            oplog.activate(arrival)
            transcript.append({"kind": "dry_say", "target": destination, "content": body,
                               "message_id": message_id, "sent": False,
                               "occurred_at": time.time()})
            return message_id

        def forbidden(*_args, **_kwargs):
            raise RuntimeError("offline replay outbound operation blocked")

        for name in outbound_names:
            setattr(message, name, forbidden)
        connect.call_api = forbidden
        extra = STRATEGIES[strategy]
        replay_prompt = OFFLINE_FACT + (("\n" + extra) if extra else "")
        send_context = registry.get("meta").tools["say"].call.__globals__["_offline_send_sink"]
        if not resume:
            last_arrival = None
            for event in ordered:
                last_arrival = oplog.arrive(window, event, origin=event["_log_origin"])
            oplog.activate(last_arrival)
        try:
            # WHY: requested_reads lives only for this drive. If a budget stops
            # before the next provider request, no input fact exists and those
            # members remain unread for a later replay to choose again.
            chat.run_offline(MODEL, window, fact=replay_prompt, registry=registry,
                             on_chunk=on_chunk, persist_reasoning=persist_reasoning,
                             send_context=send_context, send_sink=dry_say)
        except Exception as error:
            stop_reason = str(error)
            if not (type(error).__name__ in {
                "APIConnectionError", "APITimeoutError", "RateLimitError", "APIStatusError"
            } or stop_reason in {
                "max-calls budget reached", "max-prompt-tokens budget reached",
                "max-completion-tokens budget reached"
            } or
                    stop_reason.startswith("模型流未完整结束") or
                    stop_reason.startswith("模型响应未完整结束") or
                    stop_reason.startswith("模型流缺少结束标记") or
                    stop_reason.startswith("模型在结束标记后继续生成")):
                raise
        finalize_usage()
        result = {"status": "complete" if stop_reason == "complete" else "stopped",
                  "stop_reason": stop_reason, "events": len(ordered),
                  "model_calls": len(usage), "dry_says": echo_number, "segment": segment}
        return result
    finally:
        try:
            if runtime_started and result is not None:
                finalize_usage()
                storage.save()
                _assert_storage_saved(storage)
                rows = _journal_rows(Path.cwd())
                if len(rows) < prior_rows:
                    raise RuntimeError("event journal shrank during replay")
                _assert_settled(rows)
                intentions = {row["message_id"]: row for row in transcript}
                transcript = []
                for row in rows[prior_rows:]:
                    if row.get("kind") == "arrival":
                        message_id = row.get("event", {}).get("message_id")
                        if message_id in intentions:
                            transcript.append(intentions.pop(message_id))
                    transcript.append(row)
                transcript.extend(intentions.values())
                _append_jsonl(Path("transcript.jsonl"), transcript)
                _append_jsonl(Path("usage.jsonl"), [{"segment": segment, **row} for row in usage])
                _write_json(Path(f"segment-{segment:04d}.json"), {
                    "segment": segment, "status": result["status"],
                    "stop_reason": stop_reason, "model_calls": len(usage),
                    "prompt_tokens": sum(row["prompt_tokens"] for row in usage),
                    "completion_tokens": sum(row["completion_tokens"] for row in usage),
                    "journal_rows": len(rows),
                })
                _checkpoint(Path.cwd(), segment, len(rows))
        finally:
            llm.client, storage.root_path, chatlog.rootfile = old_client, old_root, old_chatlog
            chat.settings, chat.prompts, chat.chat_groups, chat.llm_config, chat.description_cache = old_chat_state
            for name, value in old_oplog_state.items():
                setattr(oplog, name, value)
            for name, value in old_context_state.items():
                setattr(context, name, value)
            for name, value in old_chatlog_state.items():
                setattr(chatlog, name, value)
            for name, value in old_storage_state.items():
                setattr(storage, name, value)
            for name, original in original_outbound.items():
                setattr(message, name, original)
            connect.call_api = original_call_api
            identity.getname, identity.get_user_name, identity.qq = old_name, old_user_name, old_qq
            identity.name, identity.nicknames = old_bot_name, old_nicknames
            os.chdir(previous_cwd)


def _doctor(output: Path) -> dict:
    from mods import connect, message, oplog, storage

    root = output / "runtime"
    root.mkdir(mode=0o700)
    old_storage_root = storage.root_path
    old_oplog_state = {name: getattr(oplog, name) for name in _OPLOG_BINDINGS}
    original_send = message.send
    original_sendmsg = message.sendmsg
    original_send_now = message._send_now
    original_call_api = connect.call_api
    blocked_attempts = 0

    def block_outbound(*_args, **_kwargs):
        nonlocal blocked_attempts
        blocked_attempts += 1
        raise AssertionError("doctor outbound guard blocked a call")

    message.send = block_outbound
    message.sendmsg = block_outbound
    message._send_now = block_outbound
    connect.call_api = block_outbound
    storage.root_path = str(root / "data" / "storage")
    try:
        for name, value in old_oplog_state.items():
            setattr(oplog, name, None if name == "_root" else type(value)())
        if connect._server is not None or message._worker is not None:
            raise AssertionError("doctor has a live listener or send worker")
        try:
            message.sendmsg("synthetic probe", group_id=1)
        except AssertionError as error:
            if str(error) != "doctor outbound guard blocked a call":
                raise
        else:
            raise AssertionError("doctor outbound guard did not block")
        # These are invented records, never copied from an account or real archive.
        source = output / "fixture"
        for day, lines in (
            ("2000-01-01", [("09:00:00", 1), ("08:00:00", 2)]),
            ("2000-01-02", [("07:00:00", 3)]),
        ):
            day_value = date.fromisoformat(day)
            path = source / "group" / "1" / day_value.strftime("%Y-%m")
            path.mkdir(parents=True, exist_ok=True)
            (path / f"{day_value:%d}.log").write_text(
                "".join(f"【】Synthetic(2) {stamp} | {number}\n    event-{number}\n"
                        for stamp, number in lines), encoding="utf-8"
            )
        prepared = _prepare(source.resolve(), "group", 1, date(2000, 1, 1),
                            date(2000, 1, 2), output / "prepared")
        ordered = _ordered_snapshot(output / "prepared", "group", 1, prepared)
        if [record["message_id"] for record in ordered] != [2, 1, 3]:
            raise AssertionError("day/time/file-order replay is unstable")

        window = ("group", 1)
        for event in ordered:
            oplog.arrive(window, event, origin=event["_log_origin"])
        if [item["event"]["message_id"] for item in oplog.unread(window)] != [2, 1, 3]:
            raise AssertionError("production oplog FIFO differs")

        def project(entries):
            return [oplog.input(oplog.AGENT_WINDOW, entry["event"],
                                {"role": "user", "content": entry["event"]["message"]},
                                entry["arrival"], source_window=window)["id"]
                    for entry in entries]

        first_page = project(oplog.unread(window)[:2])
        second_page = project(oplog.unread(window)[:2])
        if len(first_page) != 2 or len(second_page) != 1 or oplog.unread(window):
            raise AssertionError("production FIFO did not drain in bounded pages")
        recalled, missing = oplog.recall_events(oplog.AGENT_WINDOW, first_page + second_page)
        if missing or [entry["event"]["message_id"] for entry in recalled] != [2, 1, 3]:
            raise AssertionError("production event journal recall differs")
        output_id = oplog.output(
            oplog.AGENT_WINDOW, {"content": "synthetic conclusion"},
            [{"function": {"name": "cover_events", "arguments": "{}"}}],
        )
        covered = oplog.cover(oplog.AGENT_WINDOW, f"{output_id}#1", first_page,
                              set(first_page + second_page))
        if covered != set(first_page) or not covered <= oplog.covered(oplog.AGENT_WINDOW):
            raise AssertionError("production cover journal differs")
        recalled_after_cover, missing = oplog.recall_events(oplog.AGENT_WINDOW, first_page)
        if missing or [entry["id"] for entry in recalled_after_cover] != first_page:
            raise AssertionError("covered inputs are no longer recallable")

        transcript = [
            {"kind": "input", "id": entry["id"], "content": entry["projection"]["content"]}
            for entry in recalled
        ]
        transcript.append({"kind": "output", "id": output_id,
                           "content": "synthetic conclusion", "simulated": True})
        transcript.append({"kind": "cover", "node": f"{output_id}#1",
                           "members": sorted(covered), "simulated": True})
        transcript.append({"kind": "dry_run_say", "target": "synthetic-window",
                           "content": "synthetic reply", "sent": False})
        _write_jsonl(output / "transcript.jsonl", transcript)
        _write_jsonl(output / "usage.jsonl", [{"model": MODEL, "paid_calls": 0,
                                               "prompt_tokens": 0, "completion_tokens": 0}])
        if blocked_attempts != 1:
            raise AssertionError("unexpected outbound call during doctor")
        if not (root / "data" / "event_stream").is_dir():
            raise AssertionError("event journal was not isolated in doctor runtime")
        return {"status": "ok", "events": len(ordered), "model_calls": 0,
                "send_calls": 0, "blocked_send_probes": blocked_attempts,
                "runtime_isolated": True}
    finally:
        storage.root_path = old_storage_root
        for name, value in old_oplog_state.items():
            setattr(oplog, name, value)
        message.send = original_send
        message.sendmsg = original_sendmsg
        message._send_now = original_send_now
        connect.call_api = original_call_api


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command")
    prepare = commands.add_parser("prepare", help="freeze an explicitly selected archive")
    prepare.add_argument("--chatlog-root", required=True)
    prepare.add_argument("--kind", required=True, choices=("group", "private"))
    prepare.add_argument("--target", required=True, type=int)
    prepare.add_argument("--from", dest="first", required=True, type=date.fromisoformat)
    prepare.add_argument("--to", dest="last", required=True, type=date.fromisoformat)
    prepare.add_argument("--output", required=True)
    doctor = commands.add_parser("doctor", help="run a synthetic no-model/no-send probe")
    doctor.add_argument("--output")
    for command in ("run", "resume"):
        run = commands.add_parser(command, help="run one isolated central-reader segment")
        run.add_argument("--prepared", required=True)
        run.add_argument("--output", required=True)
        run.add_argument("--kind", required=True, choices=("group", "private"))
        run.add_argument("--target", required=True, type=int)
        run.add_argument("--bot-id", required=True, type=int)
        run.add_argument("--bot-name", required=True)
        run.add_argument("--llm-config", required=True)
        run.add_argument("--strategy", choices=tuple(STRATEGIES), default="baseline")
        run.add_argument("--confirm-paid", action="store_true")
        run.add_argument("--max-calls", type=int, required=True)
        run.add_argument("--max-prompt-tokens", type=int, required=True)
        run.add_argument("--max-completion-tokens", type=int, required=True)
        run.add_argument("--max-output-tokens-per-call", type=int, required=True)
    fork = commands.add_parser("fork", help="clone a stopped run into a new isolated directory")
    fork.add_argument("--source", required=True)
    fork.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command in (None, "doctor"):
            if args.command == "doctor" and args.output:
                output = _output_path(args.output)
            else:
                temporary_root = Path(tempfile.gettempdir()).resolve()
                if temporary_root.is_relative_to(REPOSITORY):
                    raise ValueError("system temporary directory must be outside the repository")
                output = Path(tempfile.mkdtemp(prefix="bot-memory-replay-doctor-"))
            if not output.exists():
                output.mkdir(mode=0o700, parents=True)
            result = _doctor(output)
            print(json.dumps({**result, "output": str(output)}, ensure_ascii=False))
        elif args.command == "prepare":
            root = Path(args.chatlog_root).expanduser().resolve()
            output = _output_path(args.output, root)
            result = _prepare(root, args.kind, args.target, args.first, args.last, output)
            print(json.dumps({"status": result["status"], "events": result["events"],
                              "output": str(output)}, ensure_ascii=False))
        elif args.command == "fork":
            source = Path(args.source).expanduser().resolve()
            output = _output_path(args.output, source)
            result = _fork(source, output)
            print(json.dumps({**result, "output": str(output)}, ensure_ascii=False))
        else:
            if not args.confirm_paid:
                raise ValueError("run and resume require --confirm-paid")
            prepared = Path(args.prepared).expanduser().resolve()
            output = (_output_path(args.output, prepared) if args.command == "run"
                      else Path(args.output).expanduser().resolve())
            config = _read_llm_config(Path(args.llm_config).expanduser())
            result = _run(prepared, output, args.kind, args.target, args.bot_id, args.bot_name,
                          config, args.max_calls, args.max_prompt_tokens,
                          args.max_completion_tokens, args.max_output_tokens_per_call,
                          strategy=args.strategy, resume=args.command == "resume")
            print(json.dumps({**result, "output": str(output)}, ensure_ascii=False))
    except (OSError, ValueError, UnicodeError, AssertionError, RuntimeError, KeyError) as error:
        print(f"memory replay: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
