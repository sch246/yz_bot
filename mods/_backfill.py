"""Recover one named NapCat source into the archive and its bounded page queue."""

from __future__ import annotations

import logging
from pathlib import Path
import sqlite3
import tempfile

from mods import _napcat_history, _source_pages, chatlog, context, oplog


logger = logging.getLogger(__name__)
PAGE_SIZE = 100


def _prior_boot_members(source: dict, root: Path) -> tuple[tempfile.TemporaryDirectory | None,
                                                            sqlite3.Connection | None]:
    """Index earlier boot FIFOs from their committed pages, not a second authority."""
    if source["source_type"] != "napcat_boot":
        return None, None
    earlier = []
    for candidate in oplog.sources():
        if candidate["key"] == source["key"]:
            break
        if candidate["source_type"] == "napcat_boot" and candidate["window"] == source["window"]:
            if candidate["state"] == "fetching":
                raise RuntimeError("较早的同窗口补回信源尚未收束")
            earlier.append(candidate)
    if not earlier:
        return None, None
    temporary = tempfile.TemporaryDirectory(prefix="yz-boot-members-")
    database = None
    try:
        database = sqlite3.connect(Path(temporary.name) / "members.sqlite3")
        database.execute("PRAGMA cache_size=-2048")
        database.execute("CREATE TABLE members (origin TEXT PRIMARY KEY)")
        with database:
            for candidate in earlier:
                for page_number in range(candidate["pages"]):
                    database.executemany(
                        "INSERT OR IGNORE INTO members VALUES (?)",
                        ((member["origin"],) for member in
                         _source_pages.read_page(root, candidate["key"], page_number)))
        return temporary, database
    except BaseException:
        if database is not None:
            database.close()
        temporary.cleanup()
        raise


def recover_source(source: dict, call_api, *, manual: bool = False) -> dict:
    """Resume a frozen source; fetched history never enters the realtime router."""
    key = source["key"]
    kind, target = source["window"]
    root = oplog.source_page_root()
    state = oplog.resolve_source(key)
    if state is None or state["state"] != "fetching":
        raise ValueError("source is not awaiting recovery")

    temporary, prior_members = _prior_boot_members(state, root)
    try:
        if state["pages"]:
            _source_pages.delete_pending(root, key, state["pages"] - 1)
        while True:
            try:
                cursor, orphan = _source_pages.read_page_info(root, key, state["pages"])
            except (FileNotFoundError, _source_pages.IncompletePageError):
                break
            with context.window_lock((kind, target)):
                state = oplog.publish_source_page(key, state["pages"], cursor, len(orphan),
                                                  sum(member.get("mentioned", False) for member in orphan))
            _source_pages.delete_pending(root, key, state["pages"] - 1)

        def save_page(rows: list[dict]) -> None:
            nonlocal state
            from mods import chat, msgs

            _source_pages.write_pending(root, key, state["pages"], rows)
            with context.window_lock((kind, target)):
                origins = chatlog.append_backfill_page(kind, target, rows)
                if len(origins) != len(rows):
                    raise RuntimeError("回填档案未逐条返回稳定位置")
                members = []
                for row, origin in zip(rows, origins):
                    pending = oplog.pending_message((kind, target), row["message_id"], row["time"],
                                                    row["message_seq"])
                    boundary = state["pending_boundary"]
                    if pending is not None and boundary is not None and oplog.arrival_before_or_at(pending, boundary):
                        continue
                    if prior_members is not None and prior_members.execute(
                            "SELECT 1 FROM members WHERE origin=?", (origin,)).fetchone():
                        continue
                    member = {"origin": origin, "message_id": row["message_id"],
                              "message_seq": int(row["message_seq"]), "time": int(row["time"]),
                              "mentioned": chat.reader._addressed(row, msgs.body(row))}
                    members.append(member)
                cursor = str(rows[0]["message_seq"])
                _source_pages.write_page(root, key, state["pages"], members, cursor=cursor)
                state = oplog.publish_source_page(key, state["pages"], cursor, len(members),
                                                  sum(member["mentioned"] for member in members))
                _source_pages.delete_pending(root, key, state["pages"] - 1)

        pending = _source_pages.read_pending(root, key, state["pages"])
        if pending is not None:
            save_page(pending)

        # WHY: Boot with no trustworthy anchor reads just the recent page;
        # finding older history is an explicit fetch. A reliable frozen anchor
        # must instead be followed until it is found, the upstream ends, or the
        # paging chain actually fails; an arbitrary page cap would manufacture
        # a gap in a long but otherwise continuous offline interval.
        request_budget = (1 if state["fetch_anchor"] is None
                          and state["source_type"] == "napcat_boot" and not manual else None)
        result = _napcat_history.crawl_history(
            kind, int(target), call_api=call_api, save_page=save_page,
            anchor_message_id=state["fetch_anchor"], anchor_time=state.get("anchor_time"),
            start_seq=state["cursor"], count=PAGE_SIZE, max_requests=request_budget)
        gap = ("远端历史到尽头但没有遇到本地锚点" if state["fetch_anchor"] is not None
               and not result["anchor_found"] else None)
        with context.window_lock((kind, target)):
            return oplog.finish_source(key, gap=gap, stop_cursor=result["oldest_seq"])
    except _napcat_history.HistoryGap as error:
        with context.window_lock((kind, target)):
            return oplog.finish_source(key, gap=f"{error.reason}; cursor={error.cursor}",
                                       stop_cursor=error.cursor)
    except Exception:
        logger.exception("NapCat source recovery stopped before a durable page commit")
        raise
    finally:
        if prior_members is not None:
            prior_members.close()
        if temporary is not None:
            temporary.cleanup()
