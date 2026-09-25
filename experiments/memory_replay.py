"""Strictly offline archive preparation and a synthetic replay boundary probe."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile


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
    for entry in manifest["dates"]:
        day = date.fromisoformat(entry["date"])
        path = output / "archive" / day.strftime("%Y-%m") / f"{day:%d}.log"
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise ValueError("frozen archive hash mismatch")
        records = chatlog.parse_log(
            raw.decode("utf-8", errors="strict"), kind=kind, target=target,
            day=(day.year, day.month, day.day),
        )
        if len(records) != entry["events"] or any("time" not in record for record in records):
            raise ValueError("frozen archive event count or time mismatch")
        ordered.extend((day, record["time"], position, record)
                       for position, record in enumerate(records))
    ordered.sort(key=lambda item: (item[0], item[1], item[2]))
    return [record for _, _, _, record in ordered]


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
    run = commands.add_parser("run", help="reserved; paid production-like replay is not wired")
    run.add_argument("--output", required=True)
    run.add_argument("--confirm-paid", action="store_true")
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
            raise ValueError("run is disabled: production-safe model, prompt, tool and send injection points are missing")
    except (OSError, ValueError, UnicodeError, AssertionError) as error:
        print(f"memory replay: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
