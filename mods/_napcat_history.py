"""Page backwards through NapCat history without owning storage or a connection."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class HistoryGap(RuntimeError):
    """A remote page cannot establish that the next older page is reachable."""

    def __init__(self, reason: str, cursor: str | None, requests: int, saved: int):
        self.reason = reason
        self.cursor = cursor
        self.requests = requests
        self.saved = saved
        super().__init__(f"NapCat history gap: {reason}; cursor={cursor!r}; requests={requests}; saved={saved}")


def _number(value: Any, field: str, *, signed: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"{field} is not a decimal integer")
    digits = str(value)
    magnitude = digits[1:] if signed and digits.startswith("-") else digits
    if not magnitude.isascii() or not magnitude.isdecimal():
        raise ValueError(f"{field} is not a decimal integer")
    return int(digits)


def crawl_history(
    kind: str,
    target: int,
    *,
    call_api: Callable[..., dict],
    save_page: Callable[[list[dict]], None],
    anchor_message_id: int | str | None = None,
    anchor_time: int | None = None,
    start_seq: int | str | None = None,
    count: int = 100,
    max_requests: int | None = None,
    earliest_time: int | None = None,
) -> dict[str, Any]:
    """Persist each older page before requesting the next one.

    ``kind`` is ``group`` or ``private``; ``target`` is the group or private
    peer id. ``call_api`` has the signature of ``mods.connect.call_api`` but
    must be supplied explicitly. Pages arrive newest first; ``save_page``
    receives each page's fresh rows in oldest-to-newest sequence order and
    must durably commit them before returning.

    A reliable local chatlog ``message_id`` can be passed as the anchor. The
    anchor and all older rows on its page are excluded from persistence. The
    result distinguishes reaching it from an empty remote page before it was
    found; ``oldest_seq`` is the reached anchor or last committed cursor.
    A short nonempty page is never treated as the end. A request budget or
    local-anchor time bound reports a resumable gap after committed progress.
    On an API,
    format, or cursor failure, ``HistoryGap`` reports progress without
    advancing past the uncommitted page. A persistence exception propagates.
    """
    if kind not in ("group", "private"):
        raise ValueError("kind must be 'group' or 'private'")
    if isinstance(target, bool) or not isinstance(target, int) or target <= 0:
        raise ValueError("target must be a positive integer")
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise ValueError("count must be a positive integer")
    anchor = None if anchor_message_id is None else _number(anchor_message_id, "anchor_message_id", signed=True)

    action = "get_group_msg_history" if kind == "group" else "get_friend_msg_history"
    target_field = "group_id" if kind == "group" else "user_id"
    cursor = None if start_seq is None else _number(start_seq, "start_seq")
    requests = 0
    saved = 0

    def gap(reason: str) -> HistoryGap:
        return HistoryGap(reason, None if cursor is None else str(cursor), requests, saved)

    while True:
        if max_requests is not None and requests >= max_requests:
            raise gap("request budget reached before local anchor")
        params: dict[str, Any] = {target_field: target, "count": count + (cursor is not None),
                                  "disable_get_url": True, "parse_mult_msg": False}
        if cursor is not None:
            # WHY: NapCat 4.18.28 pages by message_seq, not message_id, and may
            # include the requested row. reverse_order is needed to go older.
            params.update(message_seq=str(cursor), reverse_order=True)
        requests += 1
        try:
            response = call_api(action, **params)
        except Exception as error:
            raise gap(f"API call failed ({type(error).__name__})") from error
        if not isinstance(response, dict):
            raise gap("API response is not an object")
        if response.get("retcode") != 0:
            raise gap(f"API retcode={response.get('retcode')!r}")
        data = response.get("data")
        rows = data.get("messages") if isinstance(data, dict) else None
        if not isinstance(rows, list):
            raise gap("API response has no messages list")
        if not rows:
            return {"stop_reason": "exhausted", "anchor_found": False,
                    "requests": requests, "saved": saved,
                    "oldest_seq": None if cursor is None else str(cursor)}

        numbered = []
        seen_seqs = set()
        try:
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError("message is not an object")
                sequence = _number(row.get("message_seq"), "message_seq")
                message_id = _number(row.get("message_id"), "message_id", signed=True)
                if sequence in seen_seqs:
                    raise ValueError("page repeats a message_seq")
                if cursor is not None and sequence > cursor:
                    raise ValueError("page returned a newer message_seq than its cursor")
                seen_seqs.add(sequence)
                numbered.append((sequence, message_id, row))
        except ValueError as error:
            raise gap(str(error)) from error

        numbered.sort(key=lambda item: item[0])
        if cursor is not None:
            numbered = [item for item in numbered if item[0] != cursor]
        if not numbered:
            raise gap("page contains no message older than its cursor")

        matches = [index for index, item in enumerate(numbered)
                   if item[1] == anchor and (anchor_time is None or item[2].get("time") == anchor_time)]
        if len(matches) > 1:
            raise gap("anchor message_id occurs more than once on a page")
        if matches:
            anchor_seq = numbered[matches[0]][0]
            numbered = numbered[matches[0] + 1:]
        passed_time = False
        if earliest_time is not None and not matches:
            if any(type(item[2].get("time")) is not int for item in numbered):
                raise gap("remote message has no reliable time")
            passed_time = any(item[2]["time"] < earliest_time for item in numbered)
            if passed_time:
                numbered = [item for item in numbered if item[2]["time"] >= earliest_time]
        page = [item[2] for item in numbered]
        if page:
            save_page(page)
            saved += len(page)
        if passed_time:
            if numbered:
                cursor = numbered[0][0]
            raise gap("remote history passed local anchor time")
        if matches:
            return {"stop_reason": "anchor", "anchor_found": True,
                    "requests": requests, "saved": saved,
                    "oldest_seq": str(anchor_seq)}
        cursor = numbered[0][0]
