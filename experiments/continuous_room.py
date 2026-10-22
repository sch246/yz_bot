"""Small synthetic, continuing-room run for the isolated center and actions."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import time

_ACTIVE_ROOM = None

WINDOW = ("group", 11)
TARGET = "g11"
BOT_ID = 991100
BOT_NAME = "合成柚子"


def _write_tools(directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "meta.py").write_text('''"""Only synthetic room actions; no host or QQ access."""
from experiments.continuous_room import _active_room

__all__ = ["exec_code", "list_tools", "reload_tools", "load_tools", "say",
           "observe_room", "start_walk", "stop_walk", "rest"]

def _room():
    return _active_room()

def exec_code(code: str) -> str:
    """Unavailable in this synthetic room."""
    return "This tool is unavailable in the synthetic room."

def list_tools() -> str:
    """List synthetic tools."""
    return "Only synthetic room tools are available."

def reload_tools(names: str) -> str:
    """Unavailable in this synthetic room."""
    return "This tool is unavailable in the synthetic room."

def load_tools(names: str) -> str:
    """Unavailable in this synthetic room."""
    return "This tool is unavailable in the synthetic room."

def say(target: str, text: str, final_call: bool = True) -> str:
    """Write a synthetic message to the room outbox. target must be g11."""
    return _room().say(target, text, final_call)

def observe_room() -> str:
    """Observe the fictional workroom and current walking state."""
    return _room().observe_room()

def start_walk(destination: int, route: str = "main", queue: str = "append") -> str:
    """Move the empty box toward a numbered room location."""
    return _room().start_walk(destination, route, queue)

def stop_walk() -> str:
    """Stop the current synthetic walk."""
    return _room().stop_walk()

def rest() -> str:
    """Wait briefly in the synthetic workroom."""
    return _room().rest()
''', encoding="utf-8")


class _Room:
    def __init__(self, center, actions, outbox: Path, clock: float):
        self.center, self.actions, self.outbox, self.clock = center, actions, outbox, clock
        self.assembly = center._assembly
        self.actions_state = None
        self.closed_route = False

    def post_event(self, source, event_type, payload, *, wake=False):
        return self.center.post_event(source, event_type, payload, window=WINDOW,
                                      wake=wake, assembly=self.assembly)

    def say(self, target: str, text: str, final_call: bool) -> str:
        if target != TARGET:
            raise ValueError("synthetic room only accepts target g11")
        row = {"target": TARGET, "text": str(text), "final_call": bool(final_call),
               "at": time.time()}
        with (self.outbox / "messages.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
        from mods.tools import current_binding
        if final_call:
            current_binding().session.turn_done = lambda: True
        return "synthetic message recorded"

    def observe_room(self) -> str:
        return json.dumps({"room": "fictional workroom", "box": "empty", "position": self.position,
                           "destination": self.destination, "route_closed": self.closed_route,
                           "walk": self.actions.status()}, ensure_ascii=False)

    @property
    def position(self):
        return getattr(self, "_position", 0)

    @position.setter
    def position(self, value):
        self._position = value

    @property
    def destination(self):
        return getattr(self, "_destination", None)

    @destination.setter
    def destination(self, value):
        self._destination = value

    def start_walk(self, destination: int, route: str = "main", queue: str = "append") -> str:
        if type(destination) is not int or not 0 <= destination <= 10:
            raise ValueError("destination must be an integer from 0 to 10")
        self.destination = destination
        direction = 1 if destination >= self.position else -1
        steps = [{"position": point, "destination": destination, "route": route}
                 for point in range(self.position + direction, destination + direction, direction)]
        self.actions_state = self.actions.submit(steps, mode=queue)
        return json.dumps(self.actions_state, ensure_ascii=False)

    def step(self, step, cancelled, report):
        if cancelled():
            return {"status": "cancelled", "position": self.position}
        route = step.get("route", "main")
        if route == "main" and self.closed_route:
            return {"status": "blocked", "position": self.position, "route": route,
                    "reason": "road closed"}
        destination = step["destination"]
        self.position = step["position"]
        report({"position": self.position, "destination": destination,
                "route": route, "route_closed": self.closed_route})
        self.progress.append({"elapsed": round(time.monotonic() - self.clock, 3),
                              "position": self.position, "route": route})
        time.sleep(.18)
        return {"status": "progress", "position": self.position}

    def stop_walk(self) -> str:
        return json.dumps(self.actions.stop("room tool requested stop"), ensure_ascii=False)

    def rest(self) -> str:
        time.sleep(.2)
        return self.observe_room()

    def obstruction(self):
        self.closed_route = True
        self.actions.stop("synthetic road closure")


def _active_room():
    if _ACTIVE_ROOM is None:
        raise RuntimeError("synthetic room is not active")
    return _ACTIVE_ROOM


def run(root, client, model="official/deepseek-flash", *, recent_count=2,
        role_model=None, max_wakes=6, time_limit=60) -> dict:
    """Run one bounded synthetic room; caller must supply a client and fresh root."""
    root = Path(root)
    if not root.is_absolute():
        raise ValueError("root must be an absolute path outside the repository")
    root = root.resolve()
    repo = Path(__file__).resolve().parents[1]
    if root == repo or repo in root.parents:
        raise ValueError("root must be outside the repository")
    if root.exists() and any(root.iterdir()):
        raise ValueError("root must be fresh and empty")
    root.mkdir(parents=True, exist_ok=True)
    for name in ("archive", "outbox"):
        (root / name).mkdir()
    _write_tools(root / "tools")

    from mods import chat, chatlog, context, identity, llm, storage
    from mods.tools import ToolRegistry
    from mods.chat.center import Center, isolated
    from mods.chat.actions import Actions

    old = {
        "client": llm.client, "root_path": storage.root_path, "rootfile": chatlog.rootfile,
        "settings": chat.settings, "prompts": chat.prompts, "chat_groups": chat.chat_groups,
        "llm_config": chat.llm_config, "description_cache": chat.description_cache,
        "identity": (identity.qq, identity.name, identity.nicknames,
                     identity.getname, identity.get_user_name),
        "storage": (storage.storage, storage.load_errors, storage._states,
                    storage._pending_file_events),
        "chatlog": (chatlog._boot_anchors,), "context": (context._latest,),
    }
    started = time.monotonic()
    global _ACTIVE_ROOM
    room = None
    actions = None
    summary = None
    try:
        storage.storage, storage.load_errors, storage._states, storage._pending_file_events = {}, [], {}, {}
        storage.root_path = str(root / "data" / "storage")
        chatlog.rootfile = str(root / "archive")
        chatlog._boot_anchors = {}
        context._latest = {}
        chat.settings, chat.prompts, chat.chat_groups = [], {}, []
        chat.llm_config, chat.description_cache = getattr(client, "config", {}), {}
        identity.qq, identity.name, identity.nicknames = BOT_ID, BOT_NAME, [BOT_NAME]
        identity.get_user_name = lambda user_id: BOT_NAME if int(user_id) == BOT_ID else "合成参与者"
        identity.getname = lambda user_id=None, group_id=None: "合成参与者"
        storage.get("", "agent")["model"] = model
        storage.get("", "settings", list)
        registry = ToolRegistry(root / "tools")
        center = Center(root=root / "center", archive_root=root / "archive",
                        source_namespace="synthetic-room", source_format="v1", bot_id=BOT_ID,
                        bot_names={BOT_NAME: BOT_ID}, names_complete=True, timezone="UTC",
                        client=client, send_sink=lambda _text, _window: 1, request_limit=100,
                        registry=registry, selector_model=role_model or model,
                        librarian_model=role_model or model, recent_count=recent_count)
        with isolated(center):
            actions = Actions(center=center, step=lambda step, cancelled, report: room.step(
                step, cancelled, report), window=WINDOW, tick_seconds=.05)
            room = _Room(center, actions, root / "outbox", started)
            _ACTIVE_ROOM = room
            room.position = 0
            room.progress = []
            room.wake_samples = []
            _seed(center, room)
            _drive(center, room, actions, model, max_wakes, time_limit, started)
            actions.close()
            actions = None
            center.join()
            summary = _summary(center, room, root, started)
    finally:
        if actions is not None:
            actions.close()
        # Caller promises a fresh process; restore bindings for interactive reuse too.
        llm.client, storage.root_path, chatlog.rootfile = old["client"], old["root_path"], old["rootfile"]
        chat.settings, chat.prompts, chat.chat_groups = old["settings"], old["prompts"], old["chat_groups"]
        chat.llm_config, chat.description_cache = old["llm_config"], old["description_cache"]
        identity.qq, identity.name, identity.nicknames, identity.getname, identity.get_user_name = old["identity"]
        storage.storage, storage.load_errors, storage._states, storage._pending_file_events = old["storage"]
        chatlog._boot_anchors, = old["chatlog"]
        context._latest, = old["context"]
        _ACTIVE_ROOM = None
    return summary


def _seed(center, room):
    from mods import chatlog
    events = ["旧便条：备用钥匙放在蓝色工具柜的第二层。", "填充事件：窗边有一盆绿植。",
              "填充事件：地面刚擦过。", "请把空盒从工作间这端搬到另一端，路上留意封路。"]
    arrival = None
    for index, body in enumerate(events, 1):
        event = {"post_type": "message", "message_type": "group", "group_id": 11,
                 "user_id": 991101, "message_id": -index, "time": int(time.time()) + index,
                 "message": body, "raw_message": body,
                 "sender": {"user_id": 991101, "nickname": "合成参与者"}}
        written = center.record_event(event, lambda event=event: chatlog.write(event))
        if index == len(events):
            arrival = next(row["id"] for row in reversed(center.events) if row["kind"] == "arrival")
    if arrival:
        center.activate(arrival, "message")


def _drive(center, room, actions, model, max_wakes, time_limit, started):
    from mods import chat
    wakes = 0
    obstruction_sent = False
    active_wake = None
    active_thread = None
    room.wake_errors = []
    room.stop_reason = "complete"
    while True:
        elapsed = time.monotonic() - started
        if active_thread is None:
            pending = center.pending()
            if pending:
                if elapsed >= time_limit:
                    room.stop_reason = "time_limit_with_pending_wake"
                    break
                if wakes >= max_wakes:
                    room.stop_reason = "max_wakes_with_pending_wake"
                    break
                wakes += 1
                before = room.position
                active_wake = {"number": wakes, "started": elapsed,
                               "position_start": before, "position_end": before, "samples": []}
                room.wake_samples.append(active_wake)
                import threading
                def wake():
                    try:
                        center.wake(WINDOW)
                    except BaseException as error:
                        room.wake_errors.append(type(error).__name__)
                active_thread = threading.Thread(target=wake, daemon=True)
                active_thread.start()
                continue
            status = actions.status()
            if _terminal(status):
                if not hasattr(room, "followup_sent"):
                    if elapsed >= time_limit or wakes >= max_wakes:
                        room.stop_reason = "followup_bound_reached"
                        break
                    if not center.join(timeout=max(0.0, time_limit - elapsed)):
                        room.stop_reason = "librarian_join_timeout"
                        break
                    room.followup_sent = True
                    room.followup_arrival = _ingress(center, chat, "备用钥匙放在哪里？空盒现在到哪了？")
                    continue
                room.stop_reason = "wake_error" if room.wake_errors else "complete"
                break
            if elapsed >= time_limit:
                room.stop_reason = "time_limit"
                break
        status = actions.status()
        progressed = _progress_seen(center)
        if not obstruction_sent and progressed:
            obstruction_sent = True
            room.obstruction()
            room.post_event("environment", "road_closed", {
                "route": "main", "fact": "主路封闭，请重新选择路线。"},
                wake=True)
        if active_thread is not None and active_thread.is_alive():
            sample = {"elapsed": round(time.monotonic() - started, 3), "position": room.position}
            if not active_wake["samples"] or active_wake["samples"][-1]["position"] != sample["position"]:
                active_wake["samples"].append(sample)
            active_wake["position_end"] = room.position
            time.sleep(.05)
            continue
        if active_thread is not None:
            active_thread.join()
            active_thread = None
            active_wake["position_end"] = room.position
            active_wake["elapsed"] = round(time.monotonic() - started - active_wake["started"], 3)
            if room.wake_errors:
                room.stop_reason = "wake_error"
        elif actions.status().get("worker_alive"):
            time.sleep(.05)


def _ingress(center, chat, body):
    from mods import chatlog
    event = {"post_type": "message", "message_type": "group", "group_id": 11,
             "user_id": 991101, "message_id": -int(time.time() * 1000), "time": int(time.time()),
             "message": body, "raw_message": body,
             "sender": {"user_id": 991101, "nickname": "合成参与者"}}
    center.record_event(event, lambda: chatlog.write(event))
    arrival = next(row["id"] for row in reversed(center.events) if row["kind"] == "arrival")
    center.activate(arrival, "message")
    return arrival


def _progress_seen(center):
    return any(e.get("source") == "actions" and e.get("type") == "progress" for e in center.events)


def _terminal(status):
    if isinstance(status, dict):
        tasks = status.get("tasks", [])
        return (bool(tasks) and status.get("current") is None and not status.get("queued")
                and all(task.get("status") in {"completed", "failed", "cancelled", "unknown"}
                        for task in tasks))
    return False


def _summary(center, room, root, started):
    requests = [e for e in center.events if e.get("kind") == "request"]
    roles = Counter(str(e.get("role", "main")) for e in requests)
    notes = [e for e in center.events if e.get("kind") == "note"]
    librarian_requests = {e["id"] for e in requests if e.get("role") == "librarian"}
    librarian_note_ids = {entry["id"] for entry in notes
                          if entry.get("note", {}).get("request") in librarian_requests}
    outbox = root / "outbox" / "messages.jsonl"
    output = outbox.read_text(encoding="utf-8").splitlines() if outbox.exists() else []
    return {"roles_requests": dict(roles),
            "notes": [{"version": n["version"], "sources": n["note"]["sources"]} for n in notes],
            "outbox": [json.loads(row) for row in output],
            "actions": room.actions.status(), "position": room.position,
            "wake_position_samples": room.wake_samples, "action_progress": room.progress,
            "wake_errors": room.wake_errors, "stop_reason": room.stop_reason,
            "route_closed": room.closed_route,
            "followup_saw_note": any(e.get("kind") == "request" and e.get("note")
                                      and e.get("role", "main") == "main"
                                      and e["note"] in librarian_note_ids
                                      and int(str(e.get("cutoff", "C0"))[1:] or 0)
                                      >= int(str(getattr(room, "followup_arrival", "C0"))[1:] or 0)
                                      for e in requests),
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "center_journal": str(center.path),
            "limitations": "Synthetic workroom, fixed g11 sink, bounded wakes and time; no live transport."}
