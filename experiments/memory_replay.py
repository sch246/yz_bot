"""Strictly offline archive preparation and a synthetic replay boundary probe."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import date, timedelta
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
PROMPT_MODE = "production-default-plus-offline-history-fact"
REPOSITORY = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY))


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


OFFLINE_FACT = "正在离线回看已经发生的历史；你不能影响或回复当时的参与者。"
SAFE_TOOLS = frozenset({
    "list_tools", "load_tools", "reload_tools", "recall_events", "event_span",
    "event_links", "cover_events", "say", "pull_mail", "list_unread", "peek", "edit_hint",
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
         max_completion_tokens: int) -> dict:
    from mods import chat, chatlog, connect, context, identity, llm, message, oplog, storage

    if (target <= 0 or bot_id <= 0 or not bot_name.strip()
            or min(max_calls, max_prompt_tokens, max_completion_tokens) <= 0):
        raise ValueError("target, bot id, bot name and all budgets must be valid")
    if connect._server is not None or message._worker is not None or llm.client is not None:
        raise RuntimeError("run requires a fresh process without a Bot listener, sender or LLM client")
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
    output.mkdir(mode=0o700, parents=True)
    previous_cwd = Path.cwd()
    old_client, old_root, old_chatlog = llm.client, storage.root_path, chatlog.rootfile
    old_chat_state = chat.settings, chat.prompts, chat.llm_config, chat.description_cache
    old_name, old_user_name, old_qq = identity.getname, identity.get_user_name, identity.qq
    old_bot_name, old_nicknames = identity.name, identity.nicknames
    transcript: list[dict] = []
    usage: list[dict] = []
    runtime_started = False
    try:
        os.chdir(output)
        storage.root_path = "data/storage"
        chatlog.rootfile = "archive"
        runtime_started = True
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
        skills.mkdir(mode=0o700)
        for source in _checked_in_skills():
            shutil.copyfile(source, skills / source.name)
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
            usage.append({"model": selection, "prompt_tokens": prompt_bound,
                          "completion_tokens": 0, "reserved_completion_tokens": remaining,
                          "source": "utf8-upper-bound"})
            return {"max_tokens": remaining}

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
        echo_number = 0

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
            box = context.mailbox(window)
            box.add(event)
            box.activate(event)
            transcript.append({"kind": "dry_say", "target": destination, "content": body,
                               "message_id": message_id, "sent": False,
                               "occurred_at": time.time()})
            return message_id

        def forbidden(*_args, **_kwargs):
            raise RuntimeError("offline replay outbound operation blocked")

        outbound_names = ("send", "sendmsg", "_send_now")
        original_outbound = {name: getattr(message, name) for name in outbound_names}
        original_call_api = connect.call_api
        for name in outbound_names:
            setattr(message, name, forbidden)
        connect.call_api = forbidden
        scope_token = chat._offline_scope.set({"model": MODEL, "fact": OFFLINE_FACT,
                                               "registry": registry, "on_chunk": on_chunk})
        send_context = registry.get("meta").tools["say"].call.__globals__["_offline_send_sink"]
        sink_token = send_context.set(dry_say)
        try:
            box = context.mailbox(window)
            for event in ordered:
                chatlog._remember_origin(event, event["_log_origin"])
                box.add(event)
            box.activate(ordered[-1])
            chat._drive_agent(MODEL, window)
        finally:
            send_context.reset(sink_token)
            chat._offline_scope.reset(scope_token)
            for name, original in original_outbound.items():
                setattr(message, name, original)
            connect.call_api = original_call_api
        finalize_usage()
        storage.save()
        return {"status": "complete", "events": len(ordered), "model_calls": len(usage),
                "dry_says": echo_number}
    finally:
        if runtime_started:
            if usage and usage[-1]["source"] != "api" and "reserved_completion_tokens" in usage[-1]:
                usage[-1]["observed_completion_bytes"] = usage[-1]["completion_tokens"]
                usage[-1]["completion_tokens"] = usage[-1]["reserved_completion_tokens"]
                usage[-1]["source"] = "reserved-max-no-api-usage"
            intentions = {row["message_id"]: row for row in transcript}
            transcript = []
            for path in sorted(Path("data/event_stream").glob("????????.jsonl")):
                for line in path.read_text(encoding="utf-8").splitlines():
                    row = json.loads(line)
                    if row.get("kind") == "arrival":
                        message_id = row.get("event", {}).get("message_id")
                        if message_id in intentions:
                            transcript.append(intentions.pop(message_id))
                    transcript.append(row)
            transcript.extend(intentions.values())
        if output.exists():
            _write_jsonl(output / "transcript.jsonl", transcript)
            _write_jsonl(output / "usage.jsonl", usage)
        llm.client, storage.root_path, chatlog.rootfile = old_client, old_root, old_chatlog
        chat.settings, chat.prompts, chat.llm_config, chat.description_cache = old_chat_state
        identity.getname, identity.get_user_name, identity.qq = old_name, old_user_name, old_qq
        identity.name, identity.nicknames = old_bot_name, old_nicknames
        os.chdir(previous_cwd)


def _doctor(output: Path) -> dict:
    from mods import connect, context, message, oplog, storage

    root = output / "runtime"
    root.mkdir(mode=0o700)
    old_storage_root = storage.root_path
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
        box = context.Mailbox(window)
        for event in ordered:
            box.add(event)
        if [item.event["message_id"] for item in box.unread()] != [2, 1, 3]:
            raise AssertionError("production Mailbox FIFO differs")

        def project(entries):
            return [oplog.input(oplog.AGENT_WINDOW, entry.event,
                                {"role": "user", "content": entry.event["message"]},
                                entry.arrival, source_window=window)["id"]
                    for entry in entries]

        first_page = box.pull(2, project)
        second_page = box.pull(2, project)
        if len(first_page) != 2 or len(second_page) != 1 or box.unread():
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
    run = commands.add_parser("run", help="replay a frozen archive with the isolated central reader")
    run.add_argument("--prepared", required=True)
    run.add_argument("--output", required=True)
    run.add_argument("--kind", required=True, choices=("group", "private"))
    run.add_argument("--target", required=True, type=int)
    run.add_argument("--bot-id", required=True, type=int)
    run.add_argument("--bot-name", required=True)
    run.add_argument("--llm-config", required=True)
    run.add_argument("--confirm-paid", action="store_true")
    run.add_argument("--max-calls", type=int, required=True)
    run.add_argument("--max-prompt-tokens", type=int, required=True)
    run.add_argument("--max-completion-tokens", type=int, required=True)
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
        else:
            if not args.confirm_paid:
                raise ValueError("run requires --confirm-paid")
            prepared = Path(args.prepared).expanduser().resolve()
            output = _output_path(args.output, prepared)
            config = _read_llm_config(Path(args.llm_config).expanduser())
            result = _run(prepared, output, args.kind, args.target, args.bot_id, args.bot_name,
                          config, args.max_calls, args.max_prompt_tokens,
                          args.max_completion_tokens)
            print(json.dumps({**result, "output": str(output)}, ensure_ascii=False))
    except (OSError, ValueError, UnicodeError, AssertionError, RuntimeError, KeyError) as error:
        print(f"memory replay: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
