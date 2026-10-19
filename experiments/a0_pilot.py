"""A0 capacity-reserved pilot. Default doctor uses fictional input and closed HTTP."""
from __future__ import annotations

import argparse
from contextlib import contextmanager, redirect_stdout, ExitStack
from datetime import date, datetime, timedelta
import hashlib
import io
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import a0_calibration as a0
import a0_sdk_calibration as sdk
from a0_capacity import CapacityPolicy, LIMITS, PRICES, MODEL, WIRE_MODEL, ENDPOINT, declaration, validate_contract
from a0_ledger import Ledger, canonical, digest, durable
import httpx
import openai
from mods import llm

DATES = ["2023-03-14", "2023-03-15", "2023-03-16"]
PROJECTION = {"reasoning_mode": "keep", "keep_reasoning": True, "preserve_native": True,
              "persist_reasoning": False, "model": MODEL, "protocol": "deepseek-chat-completions"}
FACT = "离线模拟：只可见已经开放的历史；say仅记录候选意图和分支模拟回声，不影响历史、不发送真实消息。"


class PilotStop(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def error_code(error):
    if isinstance(error, PilotStop):
        return error.code
    if isinstance(error, RuntimeError) and str(error).startswith("模型流未完整结束: length"):
        return "length"
    if isinstance(error, GeneratorExit):
        return "generator-closed"
    return "internal-error"


@contextmanager
def shanghai():
    prior = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Shanghai"
    time.tzset()
    try:
        yield
    finally:
        if prior is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = prior
        time.tzset()


def code_state():
    root = a0.replay.REPOSITORY
    files = a0.code_hashes()
    for name in ("a0_pilot.py", "a0_capacity.py"):
        files["experiments/" + name] = a0.replay._hash(Path(__file__).parent / name)
    def git(*args):
        return subprocess.run(["git", *args], cwd=root, capture_output=True, check=True).stdout
    return {"commit": git("rev-parse", "HEAD").decode().strip(),
            "tracked_diff_sha256": hashlib.sha256(git("diff", "--binary", "HEAD", "--", "mods", "experiments")).hexdigest(),
            "files": files}


def safe_path(value):
    path = Path(value).expanduser().absolute()
    # Check every component before resolving, including directory symlinks.
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError("symlink input/output refused")
    return a0.external_root(path)


def read_json(path):
    return json.loads(safe_path(path).read_bytes())


def load_identity(path):
    value = read_json(path)
    if set(value) != {"schema", "kind", "target", "bot", "bot_names"} or value["schema"] != 1 or value["kind"] != "group":
        raise ValueError("only explicit group identity schema 1 is supported")
    if (type(value["target"]) is not int or value["target"] <= 0 or set(value["bot"]) != {"id", "name"}
            or type(value["bot"]["id"]) is not int or value["bot"]["id"] <= 0
            or not isinstance(value["bot"]["name"], str) or not value["bot"]["name"]
            or not isinstance(value["bot_names"], dict) or not value["bot_names"]
            or any(not isinstance(name, str) or not name or type(number) is not int or number <= 0
                   for name, number in value["bot_names"].items())
            or value["bot_names"].get(value["bot"]["name"]) != value["bot"]["id"]):
        raise ValueError("missing or ambiguous Bot identity mapping")
    return value


def load_prepared(path, identity):
    from mods import chatlog
    root = safe_path(path)
    expected_paths = {"manifest.json", *(f"archive/{day[:7]}/{day[8:]}.log" for day in DATES)}
    actual = set()
    for item in root.rglob("*"):
        if item.is_symlink():
            raise ValueError("prepared symlink refused")
        if item.is_file():
            actual.add(str(item.relative_to(root)))
    if actual != expected_paths:
        raise ValueError("prepared must contain only manifest and the three selected ordinary days")
    manifest = read_json(root / "manifest.json")
    required = {"schema", "model", "prompt_mode", "archive_kind", "input_sha256", "dates", "events", "status"}
    if (set(manifest) != required or manifest["schema"] != 1 or manifest["model"] != MODEL
            or manifest["archive_kind"] != "group" or manifest["status"] != "prepared-only"
            or manifest["prompt_mode"] != a0.replay.PROMPT_MODE
            or [entry.get("date") for entry in manifest["dates"]] != DATES):
        raise ValueError("unsupported prepared schema/model/format/date range")
    records, hashes, overall = [], {}, hashlib.sha256()
    stats = {"records": 0, "v0": 0, "v1": 0, "missing": {}, "guessed": {}, "excluded": 0}
    with shanghai():
        for entry in manifest["dates"]:
            if set(entry) != {"date", "sha256", "events"}:
                raise ValueError("unsupported prepared day declaration")
            day = date.fromisoformat(entry["date"])
            relative = f"archive/{day:%Y-%m}/{day:%d}.log"
            raw = (root / relative).read_bytes()
            sha = hashlib.sha256(raw).hexdigest()
            if sha != entry["sha256"]:
                raise ValueError("prepared day hash mismatch")
            hashes[relative] = sha
            overall.update(day.isoformat().encode() + b"\0" + bytes.fromhex(sha))
            text = raw.decode("utf-8", errors="strict")
            if "\r" in text or (text and not text.endswith("\n")):
                raise ValueError("minimal ordinary format requires LF terminated records")
            parsed = chatlog.parse_log(text, kind="group", target=identity["target"], day=(day.year, day.month, day.day),
                                       bot_id=identity["bot"]["id"], bot_names=identity["bot_names"], names_complete=True,
                                       origin_path=f"group/{identity['target']}/{day:%Y-%m}/{day:%d}.log")
            lines = raw.splitlines(keepends=True)
            offsets = [0]
            for line in lines:
                offsets.append(offsets[-1] + len(line))
            if len(parsed) != entry["events"] or (raw and (not parsed or parsed[0]["_log_origin"].rpartition(":")[2] != "1")):
                raise ValueError("ordinary records/count/preamble mismatch")
            for index, record in enumerate(parsed):
                for category in ("missing", "guessed"):
                    for field in record.get("_" + category, []):
                        stats[category][field] = stats[category].get(field, 0) + 1
                if (record.get("_kind") or record.get("_missing") or record.get("_guessed")
                        or type(record.get("message_id")) is not int or type(record.get("user_id")) is not int):
                    raise ValueError("unsupported notice/unparsed/absent-ID/ambiguous ordinary record; no records skipped")
                first = int(record["_log_origin"].rpartition(":")[2]) - 1
                last = int(parsed[index + 1]["_log_origin"].rpartition(":")[2]) - 1 if index + 1 < len(parsed) else len(lines)
                part = raw[offsets[first]:offsets[last]]
                if not part or part.startswith(b"    "):
                    raise ValueError("ordinary record boundary mismatch")
                stats[record["_version"]] += 1
                stats["records"] += 1
                records.append({"day": day.isoformat(), "position": index, "record": record,
                                "source": {"file": relative, "line": first + 1, "byte_start": offsets[first],
                                           "byte_end": offsets[last], "sha256": hashlib.sha256(part).hexdigest(), "raw_utf8": part.decode()}})
    if overall.hexdigest() != manifest["input_sha256"] or len(records) != manifest["events"]:
        raise ValueError("prepared aggregate mismatch")
    ids = [item["record"]["message_id"] for item in records]
    if any(number <= 0 for number in ids) or len(set(ids)) != len(ids):
        raise ValueError("duplicate/conflicting message IDs or negative candidate namespace collision")
    records.sort(key=lambda item: (item["day"], item["record"]["time"], item["position"]))
    return {"root": root, "manifest": manifest, "records": records, "hashes": hashes,
            "manifest_sha256": a0.replay._hash(root / "manifest.json"), "statistics": stats}


def verify_prepared(package):
    if a0.replay._hash(safe_path(package["root"] / "manifest.json")) != package["manifest_sha256"]:
        raise PilotStop("prepared-manifest-drift")
    for name, sha in package["hashes"].items():
        path = package["root"] / name
        safe_path(path)
        if a0.replay._hash(path) != sha:
            raise PilotStop("prepared-material-drift")


def schedule():
    with shanghai():
        return [{"day": day, "clock": int(datetime.combine(date.fromisoformat(day) + timedelta(days=1), datetime.min.time()).timestamp())}
                for day in DATES]


def claim_pilot(record, pilot, output, contract=None):
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{7,63}", pilot):
        raise ValueError("pilot ID must be an explicit unique 8–64 character label")
    record = safe_path(record)
    with record.open("x", encoding="utf-8") as stream:
        os.chmod(record, 0o600)
        stream.write(canonical({"pilot": pilot, "root": str(output), "contract_sha256": digest(contract) if contract else None,
                                "status": "claimed-no-auto-resume"}).decode())
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(record.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class PilotClient(sdk.ClosedSDKClient):
    """Reuse SDK observation/parser buffering, with a separate authorized constructor."""
    def __init__(self, root, ledger, contract, *, scenario="normal", credential=None):
        self.root, self.ledger, self.contract, self.scenario = root, ledger, contract, scenario
        self.live = contract["mode"] == "live"
        self.contract_hash = digest(contract)
        if (self.live and (not credential or scenario != "normal")) or (not self.live and credential is not None):
            raise ValueError("invalid pilot credential/mode combination")
        self.environment_reads, self.cancellations = [], []
        self.attempts = self.settlements = 0
        self.active, self.streams, self.responses, self.exchanges = None, [], [], []
        self.response_identity = None
        self.response_observations = []
        self.request_stop = None
        self.config = {"default_model": MODEL, "providers": {"deepseek": {"models": {
            WIRE_MODEL: {"function_calling": True, "vision": False, "tool_images": False}}}}}
        self.strict_tools = True
        self.request_policy = lambda selection, request: {"max_tokens": 4096}
        from types import SimpleNamespace
        self.clients = {"deepseek": SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=self._create)))}
        with sdk.no_sdk_environment(self.environment_reads):
            transport = None if self.live else httpx.MockTransport(self._mock)
            self.http = httpx.Client(transport=transport, trust_env=False, follow_redirects=False,
                                     event_hooks={"request": [self._reserve_http]})
            self.sdk = openai.OpenAI(api_key=credential if self.live else "sk-fictional-pilot",
                                    admin_api_key="unused", organization="", project="", webhook_secret="unused",
                                    base_url=ENDPOINT if self.live else sdk.BASE_URL, http_client=self.http,
                                    max_retries=0, timeout=contract["timeout_seconds"])

    def _create(self, **params):
        self.request_stop = None
        params.update(extra_body={"thinking": {"type": "enabled"}}, reasoning_effort="high",
                      extra_headers={"X-Synthetic-Request-ID": f"{self.ledger.state['pilot']}/{len(self.ledger.state['requests']) + 1:04d}"})
        with sdk.no_sdk_environment(self.environment_reads):
            try:
                response = self.sdk.chat.completions.create(**params)
            except PilotStop:
                raise
            except Exception:
                # Provider error bodies may echo sensitive values. The parser's
                # console prints exception text, so expose only a fixed label.
                raise PilotStop(self.request_stop or "transport-error") from None
        observed = PilotStream(self, response)
        self.responses.append(observed)
        return observed

    def _reserve_http(self, request):
        try:
            self._reserve_http_checked(request)
        except PilotStop as error:
            # The SDK wraps HTTP event-hook errors as APIConnectionError.
            self.request_stop = error.code
            raise
        except (OSError, ValueError, KeyError):
            self.request_stop = "reservation-persistence-unknown" if self.ledger.poisoned else "local-request-integrity-error"
            raise

    def _reserve_http_checked(self, request):
        try:
            self.ledger.guard()
        except RuntimeError:
            raise PilotStop(self.ledger.state["closed"] or "in-flight-or-persistence-unknown") from None
        if self.ledger.limits != LIMITS or self.ledger.prices != PRICES:
            raise PilotStop("ledger-policy-drift")
        try:
            disk = read_json(self.root / "ledger.json")
        except (OSError, ValueError):
            self.poison("ledger-unreadable")
        if disk != {"state": self.ledger.state, "sha256": digest(self.ledger.state)}:
            self.poison("ledger-corruption")
        manifest_path = self.root / "manifest.json"
        if self.live or manifest_path.exists():
            frozen = read_json(manifest_path)
            if digest(frozen) != self.ledger.state["manifest"] or frozen["contract"] != self.contract:
                raise PilotStop("manifest-drift")
            if any(a0.replay._hash(safe_path(self.root / "skills" / name)) != sha for name, sha in frozen["skills"].items()):
                raise PilotStop("skill-drift")
        if digest(self.contract) != self.contract_hash:
            raise PilotStop("contract-drift")
        if self.active is None or self.active["row"] is not None:
            raise PilotStop("duplicate-http-request")
        body = json.loads(request.content)
        base = ENDPOINT if self.live else sdk.BASE_URL
        if str(request.url) != base + "/chat/completions" or request.method != "POST" or body.get("model") != WIRE_MODEL:
            raise PilotStop("endpoint-model-drift")
        if any(body.get(key) != value for key, value in self.contract["request"].items()):
            raise PilotStop("request-parameter-drift")
        if set(body) != {"model", "messages", "tools", *self.contract["request"]}:
            raise PilotStop("unexpected-capability-or-fee")
        if any("strict" in tool.get("function", {}) for tool in body["tools"]):
            raise PilotStop("beta-strict-unsupported")
        if code_state() != self.contract["code"]:
            raise PilotStop("code-drift")
        envelope = {"body": body, "serialized_body": request.content.decode(), "method": request.method,
                    "path": request.url.path, "provider_model": MODEL, "request_id": request.headers["X-Synthetic-Request-ID"],
                    "body_sha256": hashlib.sha256(request.content).hexdigest()}
        if self.scenario == "reserve-fsync":
            with patch("a0_ledger.os.fsync", side_effect=OSError("fictional reservation fsync failure")):
                row = self.ledger.reserve(envelope, 4096)
        else:
            try:
                row = self.ledger.reserve(envelope, 4096)
            except RuntimeError as error:
                code = {"cumulative CNY limit": "budget-exhausted", "actual attempt limit": "attempt-limit",
                        "cumulative allowance limit": "allowance-limit", "output allowance limit": "output-limit"}.get(str(error), "reservation-refused")
                raise PilotStop(code) from None
        self.active["row"] = row
        if row["id"] != envelope["request_id"]:
            raise PilotStop("request-id-drift")
        durable(self.root / f"request-{len(self.ledger.state['requests']):04d}.json", envelope)
        self.exchanges.append(envelope)
        if self.live:
            self.active["attempted"] = True
            self.attempts += 1

    def poison(self, code):
        # Never overwrite a damaged authority with the in-memory projection.
        self.ledger.poisoned = True
        try:
            durable(self.root / "persistence-unknown.json", {"status": "integrity-unknown", "reason": code})
        except OSError:
            pass
        raise PilotStop(code)

    def _mock(self, request):
        assert self.active["row"] is not None and not self.active["attempted"]
        disk = json.loads((self.root / "ledger.json").read_text())["state"]
        assert disk["requests"][-1]["status"] == "reserved"
        assert request.content.decode() == self.exchanges[-1]["serialized_body"]
        assert self.active["row"]["reserved"] == 2129920
        self.active["attempted"] = True
        self.attempts += 1
        if self.scenario == "reserve-fsync":
            raise AssertionError("reserve failure must never reach mock")
        if self.scenario == "secret-error":
            raise RuntimeError(self.sdk.api_key)
        if self.scenario in {"connection", "429", "500"}:
            if self.scenario == "connection":
                raise httpx.ConnectError("fictional transport failure", request=request)
            return httpx.Response(int(self.scenario), json={"error": {"message": "fictional", "type": "synthetic"}})
        stream = PilotFixtureSSE(self, self.attempts)
        self.streams.append(stream)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    def observe(self, usage):
        super().observe(usage)
        if usage is not None:
            durable(self.root / f"raw-usage-{len(self.ledger.state['requests']):04d}.json", self.active["raw_usage"])

    def _settle(self, error):
        active = self.active
        if active["row"] is None:
            return
        valid, invalid = [], []
        for raw in active["raw_usage"]:
            try:
                valid.append(sdk.normalize_usage(raw))
            except (ValueError, TypeError, KeyError):
                invalid.append("invalid-usage")
        conflict = len({tuple(value) for value in valid}) > 1
        usage = valid[0] if valid and not invalid and not conflict else None
        outcome = "invalid-usage" if invalid else "conflicting-usage" if conflict else error_code(error) if error else "stop"
        if error and self.scenario in {"close-before", "close-after"}:
            outcome = "generator-closed"
        durable(self.root / f"observation-{len(self.ledger.state['requests']):04d}.json",
                {"raw_usage": active["raw_usage"], "outcome": outcome, "response_identity": self.response_identity,
                 "response_closed": all(stream.closed for stream in self.streams)})
        self.settlements += 1
        if self.scenario == "settle-fsync":
            with patch("a0_ledger.os.fsync", side_effect=OSError("fictional settlement fsync failure")):
                self.ledger.settle(active["row"], usage, outcome)
        else:
            self.ledger.settle(active["row"], usage, outcome)

    def generate_response(self, *args, **kwargs):
        try:
            return (yield from super().generate_response(*args, **kwargs))
        except PilotStop as error:
            # The shared buffering wrapper uses exception class names for local
            # failures without a reservation. Preserve this path's safe code.
            if not self.ledger.poisoned and self.ledger.state["closed"] in {"", "PilotStop"}:
                self.ledger.stop(error.code)
            raise


class PilotStream(sdk.ObservedStream):
    def __next__(self):
        try:
            value = super().__next__()
        except (StopIteration, GeneratorExit):
            raise
        except Exception:
            code = "cancelled" if self.owner.cancellations else "stream-disconnect"
            raise PilotStop(code) from None
        identity = {"model": value.model, "system_fingerprint": getattr(value, "system_fingerprint", None)}
        if not self.owner.response_observations or identity != self.owner.response_observations[-1]:
            self.owner.response_observations.append(identity)
            durable(self.owner.root / "response-observations.json", self.owner.response_observations)
        if self.owner.response_identity is None:
            self.owner.response_identity = identity
            durable(self.owner.root / "response-identity.json", identity)
        if identity != self.owner.response_identity or value.model != self.owner.contract["response_model"]:
            raise PilotStop("response-identity-drift")
        return value


class PilotFixtureSSE(sdk.SyntheticSSE):
    def __iter__(self):
        for block in super().__iter__():
            if self.owner.scenario in {"model-drift", "fingerprint-drift"} and b'"usage"' in block:
                value = json.loads(block.removeprefix(b"data: ").strip())
                value["model" if self.owner.scenario == "model-drift" else "system_fingerprint"] = "fictional-drift"
                block = b"data: " + canonical(value) + b"\n\n"
            if self.owner.scenario in {"full", "joint-overreport"} and b'"usage"' in block:
                value = json.loads(block.removeprefix(b"data: ").strip())
                prompt = 1048576 if self.owner.scenario == "joint-overreport" else 1044480
                value["usage"] = {"prompt_tokens": prompt, "completion_tokens": 4096, "total_tokens": prompt + 4096}
                block = b"data: " + canonical(value) + b"\n\n"
            yield block


def semantic(record):
    return {key: record.get(key) for key in ("post_type", "message_type", "time", "message_id", "message",
                                            "user_id", "group_id", "target_id", "sender")}


def open_day(scope, package, identity, opening):
    from mods import chatlog, oplog, identity as bot_identity
    scope.clock.now = opening["clock"]
    window = (identity["kind"], identity["target"])
    arrivals = []
    for item in package["records"]:
        if item["day"] != opening["day"]:
            continue
        record = item["record"]
        sender = record["sender"]
        scope.names[record["user_id"]] = sender["nickname"]
        scope.titles[(window[1], record["user_id"])] = sender.get("title", "")
        event = {key: value for key, value in record.items() if not key.startswith("_")}
        event["raw_message"] = event["message"]
        # Current Bot display names must not overwrite the historical sender.
        with patch.object(bot_identity, "get_group_user_info", return_value=(sender.get("title", ""), sender["nickname"])):
            chatlog.write(event)
        origin = chatlog.consume_origin(event)
        scope.opened.append({"id": event["message_id"], "stamp": event["time"], "window": window,
                             "name": sender["nickname"], "body": event["message"], "origin": origin})
        a0.verify_written(scope, window, event, origin)
        visible = chatlog.read_origin(*window, origin)
        if semantic(visible) != semantic(record):
            scope.fail("normalized visible record differs from complete parsed source")
        path, _, line = origin.rpartition(":")
        data = (Path("archive") / path).read_bytes().splitlines(keepends=True)
        start = int(line) - 1
        end = next((index for index in range(start + 1, len(data)) if not data[index].startswith(b"    ")), len(data))
        shown = b"".join(data[start:end])
        scope.mapping.append({"source": item["source"], "source_record": record, "visible_origin": origin,
                              "visible_sha256": hashlib.sha256(shown).hexdigest(), "visible_raw_utf8": shown.decode(),
                              "visible_record": visible, "projection": "stored v0 display text normalized to v1; original bytes retained"})
        event["_log_origin"] = origin
        arrivals.append(oplog.arrive(window, event, origin=origin))
    if not arrivals:
        scope.fail("empty selected day unsupported; no implicit wake substitution")
    if oplog.has_unnotified():
        scope.fail("material opening unexpectedly activated reader")
    # Arrival of old material and the external daily wake are separate facts.
    oplog.activate(arrivals[-1], "scheduled-day-end")
    scope.wakes.append({**opening, "opened": len(arrivals)})


def check_view(scope, package, opening):
    from mods import chatlog, oplog
    scope.safe_tree()
    assert scope.clock.now == opening["clock"]
    window = tuple(scope.opened[0]["window"])
    visible_ids = {item["id"] for item in scope.opened} | {item["event"]["message_id"] for item in scope.echoes}
    assert set(chatlog.known_windows()) == {window}
    for record in chatlog.read_range(*window):
        assert record["message_id"] in visible_ids and record["time"] <= scope.clock.now
        for kwargs in ({"origin": record["_log_origin"]}, {"message_id": record["message_id"]}):
            assert all(item["message_id"] in visible_ids for item in chatlog.read_around(*window, before=100, after=100, **kwargs))
    for mapping in scope.mapping:
        assert semantic(chatlog.read_origin(*window, mapping["visible_origin"])) == semantic(mapping["source_record"])
        source = mapping["source"]
        raw = safe_path(package["root"] / source["file"]).read_bytes()[source["byte_start"]:source["byte_end"]]
        assert raw == source["raw_utf8"].encode() and hashlib.sha256(raw).hexdigest() == source["sha256"]
    for item in scope.echoes:
        assert chatlog.read_origin(*window, item["origin"])["message"] == item["event"]["message"]
    assert all(entry["event"]["time"] <= scope.clock.now for entry in oplog.unread(window))
    assert all(entry["id"].split("-")[0] <= scope.clock.strftime("%Y%m%d") for entry in oplog.events(oplog.AGENT_WINDOW))
    for item in package["records"]:
        if item["day"] > opening["day"]:
            assert not chatlog.read_around(*window, message_id=item["record"]["message_id"])
    scope.probes.append({"day": opening["day"], "clock": scope.clock.now, "opened": len(scope.opened),
                         "paths": ["range", "around-id", "around-origin", "origin", "directory", "unread", "raw-source"], "ok": True})


@contextmanager
def pilot_scope(*args, **kwargs):
    from mods import oplog
    with a0.isolated(*args, **kwargs) as scope:
        try:
            yield scope
        finally:
            # Capture this before isolated restores the caller's oplog bindings.
            scope.remaining_unread = sum(count for _window, count, _active in oplog.pending_summary())


def run_pilot(package, identity, contract, output, pilot, *, scenario="normal", credential=None):
    from mods import chat, context, oplog, storage
    validate_contract(contract, contract["mode"], code_state())
    if output.exists() or output.is_symlink():
        raise ValueError("existing pilot root refused; no resume/fork or budget reset")
    output.mkdir(mode=0o700)
    policy = CapacityPolicy(contract)
    ledger = Ledger(output, pilot, "initializing", limits=LIMITS, prices=PRICES, policy=policy)
    client, scope, reason = None, None, "complete"
    started, wake_reports = time.time(), []
    with a0.replay._locked(output):
        try:
            client = PilotClient(output, ledger, contract, scenario=scenario, credential=credential)
            clock = a0.Clock()
            clock.now = schedule()[0]["clock"]
            skills = a0.replay._checked_in_skills()
            bot = {**identity["bot"], "names": identity["bot_names"]}
            with pilot_scope(output, ledger, clock=clock, bot=bot, skill_files=skills,
                             client=client, model=MODEL, allow_network=contract["mode"] == "live") as scope:
                scope.mapping, scope.wakes = [], []
                chat.llm_config = client.config
                storage.get("", "agent")["reasoning"] = "keep"
                storage.get("", "agent")["image"] = "off"
                frozen = {"schema": "a0-pilot-v1", "mode": contract["mode"], "pilot": pilot,
                          "contract": contract, "projection": PROJECTION, "drive": {"model": MODEL, "persist_reasoning": False},
                          "initial_state": "new empty isolated storage/history/oplog; keep, image off; no production hint",
                          "base_prompt": scope.base, "base_sha256": digest(scope.base), "tools": scope.schema,
                          "schema_sha256": digest(scope.schema), "capability_fact": FACT,
                          "skills": {source.name: a0.replay._hash(source) for source in skills},
                          "skill_sources": {source.name: "mods/tools/" + source.name for source in skills},
                          "identity_sha256": digest(identity), "prepared": package["manifest"], "statistics": package["statistics"],
                          "timezone": "Asia/Shanghai", "schedule": schedule(), "sdk": openai.__version__, "httpx": httpx.__version__,
                          "credential_supply": "explicit-private-file" if contract["mode"] == "live" else "fixed-fictional-key",
                          "real_billing_clock": "system wall time; conservative peak conversion, not provider bill",
                          "resume": "unsupported", "source_fidelity": "ordinary group records with IDs; v0 stored projection retained, never claimed raw OneBot"}
                durable(output / "manifest.json", frozen)
                ledger.state["manifest"] = digest(frozen)
                ledger.save()
                durable(output / "frozen-input.json", {"records": package["records"]})
                for opening in schedule():
                    verify_prepared(package)
                    ledger.wake()
                    open_day(scope, package, identity, opening)
                    check_view(scope, package, opening)
                    wake_started = time.time()
                    chat.run_offline(MODEL, (identity["kind"], identity["target"]), fact=FACT, registry=scope.registry,
                                     on_chunk=lambda value: None, persist_reasoning=False,
                                     send_context=scope.send_context, send_sink=scope.say)
                    if context.current() is not None or oplog.has_unnotified():
                        raise RuntimeError("reader did not finish the current wake")
                    check_view(scope, package, opening)
                    wake_reports.append({"day": opening["day"], "elapsed_seconds": time.time() - wake_started,
                                         "cumulative_attempts": client.attempts, "opened": len(scope.opened)})
                storage.save()
                a0.replay._assert_storage_saved(storage)
                a0.replay._assert_settled(a0.replay._journal_rows(output))
        except BaseException as error:
            reason = ledger.state["closed"] or error_code(error)
            if ledger.poisoned and not isinstance(error, PilotStop):
                reason = "persistence-unknown"
            if (reason == "missing-usage" and ledger.state["requests"]
                    and ledger.state["requests"][-1].get("outcome") not in {None, "stop"}):
                reason = ledger.state["requests"][-1]["outcome"]
            try:
                if not ledger.poisoned and not ledger.state["closed"]:
                    ledger.stop(reason)
            except OSError:
                pass
        finally:
            if client is not None:
                client.close()
            if scope is not None:
                durable(output / "source-mapping.json", scope.mapping)
            rows = ledger.state["requests"]
            # A readable checksum after replace does not prove directory fsync
            # succeeded. Never report a release from persistence-unknown bytes.
            report_rows = None if ledger.poisoned else rows
            journal = a0.replay._journal_rows(output)
            result = {"status": "complete" if reason == "complete" else "stopped", "stop_reason": reason,
                      "mode": contract["mode"], "manifest_sha256": ledger.state["manifest"],
                      "attempts": client.attempts if client else 0, "settlements": client.settlements if client else 0,
                      "durable_settlements": sum(row["status"] == "settled" for row in report_rows) if report_rows is not None else None,
                      "external_wakes": len(scope.wakes) if scope else 0, "completed_wakes": wake_reports,
                      "opened_records": len(scope.opened) if scope else 0,
                      "unopened_records": len(package["records"]) - (len(scope.opened) if scope else 0),
                      "remaining_unread": getattr(scope, "remaining_unread", None),
                      "formal_inputs": sum(row["kind"] == "input" for row in journal),
                      "journal_rows": len(journal),
                      "echoes": len(scope.echoes) if scope else 0, "echo_sent": False,
                      "actions": len(scope.actions) if scope else 0, "elapsed_seconds": time.time() - started,
                      "first_request_elapsed_seconds": (rows[0]["settled_at"] - rows[0]["requested_at"]
                                                         if rows and "settled_at" in rows[0] else None),
                      "charged_or_reserved_micro_cny": sum(row.get("charged", row["reserved"]) for row in report_rows) if report_rows is not None else None,
                      "requests": [{key: row.get(key) for key in ("id", "status", "input_bound", "allowance", "reserved", "usage", "charged", "usage_source", "outcome", "requested_at", "settled_at")} for row in report_rows] if report_rows is not None else None,
                      "unsettled_observed_usage": rows[-1].get("usage") if ledger.poisoned and rows else None,
                      "persistence_unknown": ledger.poisoned, "resume": "unsupported", "provider_bill": "unknown",
                      "ledger_closed": ledger.state["closed"],
                      "cancellations": client.cancellations if client else [],
                      "production_calls": scope.counters if scope else {}, "view_checks": scope.probes if scope else []}
            durable(output / "result.json", result)
    return result


def synthetic_package(root):
    from mods import chatlog
    root.mkdir()
    identity = {"schema": 1, "kind": "group", "target": 1, "bot": {"id": 9, "name": "Fictional Bot"},
                "bot_names": {"Fictional Bot": 9}}
    durable(root / "identity.json", identity)
    prepared = root / "prepared"
    prepared.mkdir()
    entries, overall = [], hashlib.sha256()
    with shanghai():
        for index, day in enumerate(DATES):
            stamp = int(datetime.fromisoformat(day + "T12:00:00").timestamp())
            texts = []
            # Same-second order is deliberately distinguishable; day two has a historical Bot line.
            for position in range(2):
                sender = 9 if index == 1 and position == 1 else 2
                name = "Fictional Bot" if sender == 9 else f"Fable day {index + 1}"
                event = a0.event_for({"id": 101 + index * 2 + position, "stamp": stamp,
                                      "window": ("group", 1), "name": name},
                                     body=f"fictional pilot day {index + 1} record {position + 1}\ncomplete second line &amp;", sender=sender)
                texts.append(chatlog.format_message(event, name, "title"))
            raw = "".join(texts).encode()
            path = prepared / "archive" / day[:7] / (day[8:] + ".log")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            sha = hashlib.sha256(raw).hexdigest()
            entries.append({"date": day, "sha256": sha, "events": 2})
            overall.update(day.encode() + b"\0" + bytes.fromhex(sha))
    durable(prepared / "manifest.json", {"schema": 1, "model": MODEL, "prompt_mode": a0.replay.PROMPT_MODE,
                                         "archive_kind": "group", "input_sha256": overall.hexdigest(), "dates": entries,
                                         "events": 6, "status": "prepared-only"})
    return prepared, root / "identity.json"


def authorize(args):
    # This check precedes path resolution, material/credential reads and clients.
    required = ("prepared", "identity", "contract", "credential_file", "pilot_record", "pilot_id", "output")
    if (not args.authorize_materials or not args.confirm_paid or any(not getattr(args, key, None) for key in required)):
        raise ValueError("real pilot requires explicit material/credential and paid authorization plus every manifest input")


def read_credential(path):
    path = safe_path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid() or info.st_size > 512:
            raise ValueError("credential file must be owner-only regular file, at most 512 bytes")
        with os.fdopen(descriptor, "rb", closefd=False) as stream:
            raw = stream.read(513)
        value = raw.decode("ascii").strip()
        if not value or any(character.isspace() for character in value):
            raise ValueError("credential file must contain one literal ASCII key")
        return value
    finally:
        os.close(descriptor)


def live_run(args):
    authorize(args)
    output, record = safe_path(args.output), safe_path(args.pilot_record)
    if output.exists() or record.exists():
        raise ValueError("pilot root/claim already exists; no implicit rerun")
    if record.is_relative_to(output) or output.is_relative_to(safe_path(args.prepared)):
        raise ValueError("pilot record must outlive root and output must be outside prepared")
    contract = validate_contract(read_json(args.contract), "live", code_state())
    if contract["pilot_binding"] != {"id": args.pilot_id, "root": str(output), "record": str(record)}:
        raise ValueError("pilot/root/claim differs from the frozen authorization contract")
    identity = load_identity(args.identity)
    package = load_prepared(args.prepared, identity)
    verify_prepared(package)
    claim_pilot(record, args.pilot_id, output, contract)
    credential = read_credential(args.credential_file)
    # No constructor is reached until all explicit inputs and authorizations pass.
    return run_pilot(package, identity, contract, output, args.pilot_id, credential=credential)


class SafeParser(argparse.ArgumentParser):
    def error(self, message):
        self.exit(2, "A0 pilot: invalid arguments; use --help.\n")


def arguments(argv=None):
    parser = SafeParser(description=__doc__)
    commands = parser.add_subparsers(dest="command")
    doctor_parser = commands.add_parser("doctor")
    doctor_parser.add_argument("--output")
    commands.add_parser("contract-template")
    live = commands.add_parser("run")
    for name in ("prepared", "identity", "contract", "credential-file", "pilot-record", "pilot-id", "output"):
        live.add_argument("--" + name)
    live.add_argument("--authorize-materials", action="store_true")
    live.add_argument("--confirm-paid", action="store_true")
    return parser.parse_args(argv)


def doctor(root):
    from mods import connect, context, message, oplog
    reports, clients, runs = [], [], []
    counters = {"socket": 0, "QQ": 0, "external_write": 0}
    active = [True]
    def audit(event, args):
        if not active[0]:
            return
        path = None
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            if args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC):
                path = Path(os.fsdecode(args[0]))
        elif event in {"os.mkdir", "os.remove", "os.rmdir", "os.rename"}:
            path = Path(os.fsdecode(args[0]))
        if path is not None and path != Path(os.devnull) and not path.resolve().is_relative_to(root):
            counters["external_write"] += 1
            raise AssertionError("doctor blocked write outside synthetic root")
    sys.addaudithook(audit)
    def passed(name, **details):
        reports.append({"assertion": name, "status": "pass", **details})
    def rejects(name, action):
        try:
            action()
        except (OSError, RuntimeError, ValueError, KeyError, openai.APIError):
            passed(name)
        else:
            raise AssertionError(name + " unexpectedly accepted")
    def block(kind):
        def refuse(*args, **kwargs):
            counters[kind] += 1
            raise AssertionError("doctor blocked " + kind)
        return refuse
    initial = a0.binding_snapshot()
    prior_tz = os.environ.get("TZ")
    output = io.StringIO()
    try:
        with ExitStack() as stack, redirect_stdout(output):
            for name in ("connect", "connect_ex"):
                stack.enter_context(patch.object(socket.socket, name, block("socket")))
            for name in ("create_connection", "getaddrinfo"):
                stack.enter_context(patch.object(socket, name, block("socket")))
            for name in ("send", "sendmsg", "_send_now"):
                stack.enter_context(patch.object(message, name, block("QQ")))
            stack.enter_context(patch.object(connect, "call_api", block("QQ")))
            guarded = a0.binding_snapshot()
            prepared, identity_path = synthetic_package(root / "input")
            identity = load_identity(identity_path)
            package = load_prepared(prepared, identity)
            contract = declaration("synthetic")
            contract["code"] = code_state()
            validate_contract(contract, "synthetic", code_state())
            assert package["statistics"] == {"records": 6, "v0": 6, "v1": 0, "missing": {}, "guessed": {}, "excluded": 0}
            assert [item["record"]["message_id"] for item in package["records"]] == list(range(101, 107))
            assert schedule()[0]["clock"] == 1678809600
            passed("prepared-LF-group-v0-complete-records-same-second-order-and-Shanghai")
            run_root = root / "success"
            claim_pilot(root / "pilot-record.json", "fictional-pilot-success", run_root)
            result = run_pilot(package, identity, contract, run_root, "fictional-pilot-success")
            runs.append(result)
            assert result["status"] == "complete" and result["attempts"] == result["settlements"] == 9
            assert result["external_wakes"] == 3 and result["opened_records"] == 6 and result["echoes"] == 6
            assert result["remaining_unread"] == 2 and result["unopened_records"] == 0
            assert result["charged_or_reserved_micro_cny"] == 2538 and not result["echo_sent"]
            assert all(value == 0 for value in result["production_calls"].values())
            frozen = read_json(run_root / "manifest.json")
            assert frozen["projection"] == PROJECTION and frozen["drive"]["persist_reasoning"] is False
            assert frozen["contract"]["policy"] == "provider-context-cap" and frozen["statistics"]["v0"] == 6
            assert "synthetic.md" not in frozen["skills"]
            assert frozen["skills"] == {source.name: a0.replay._hash(source) for source in a0.replay._checked_in_skills()}
            bodies = [read_json(run_root / f"request-{number:04d}.json")["body"] for number in range(1, 10)]
            for index, body in enumerate(bodies):
                for day in range(index // 3 + 2, 4):
                    assert f"fictional pilot day {day}" not in canonical(body).decode()
                assert body["stream_options"] == {"include_usage": True} and body["max_tokens"] == 4096
            sdk.check_native_pairs(bodies[2], [1, 2])
            sdk.check_native_pairs(bodies[5], [1, 2, 3, 4, 5])
            sdk.check_native_pairs(bodies[8], list(range(1, 9)))
            assert "fictional pilot day 1" in canonical(bodies[1]).decode()
            assert all(row["reserved"] == 2129920 and row["requested_at"] > 1700000000 for row in result["requests"])
            assert result["view_checks"][-1]["clock"] < 1700000000
            passed("actual-A0-three-daily-wakes-False-keep-native-rebuild-final-SDK-body", attempts=9, settlements=9)
            mapping = read_json(run_root / "source-mapping.json")
            assert len(mapping) == 6
            for item in mapping:
                source = item["source"]
                raw = (prepared / source["file"]).read_bytes()[source["byte_start"]:source["byte_end"]]
                assert raw == source["raw_utf8"].encode()
                assert hashlib.sha256(raw).hexdigest() == source["sha256"]
                assert semantic(item["source_record"]) == semantic(item["visible_record"])
                path, _, line = item["visible_origin"].rpartition(":")
                visible = (run_root / "archive" / path).read_bytes().splitlines(keepends=True)
                value = item["visible_raw_utf8"].encode()
                assert b"".join(visible[int(line)-1:]).startswith(value)
            passed("original-byte-ranges-complete-record-map-and-stable-visible-origin-after-echoes")
            rejects("existing-root-no-reset", lambda: run_pilot(package, identity, contract, run_root, "fictional-pilot-success"))
            rejects("same-pilot-record-cannot-switch-root", lambda: claim_pilot(root / "pilot-record.json", "fictional-pilot-success", root / "other"))
            with a0.replay._locked(run_root):
                rejects("concurrent-pilot-root-lock", lambda: a0.enter_lock(run_root))
            def unit(name, scenario="normal"):
                path = root / name
                path.mkdir()
                policy = CapacityPolicy(contract)
                ledger = Ledger(path, "fictional-pilot-" + name, digest(contract), limits=LIMITS, prices=PRICES, policy=policy)
                client = PilotClient(path, ledger, contract, scenario=scenario)
                clients.append(client)
                return client
            capped = unit("cap")
            for _ in range(12):
                sdk.one_request(capped)
            rejects("thirteenth-before-mock-small-usage-releases-balance", lambda: sdk.one_request(capped))
            assert capped.attempts == capped.settlements == len(capped.ledger.state["requests"]) == 12
            assert capped.ledger.state["closed"] == "attempt-limit"
            assert sum(row["charged"] for row in capped.ledger.state["requests"]) == 3384
            full = unit("full", "full")
            sdk.one_request(full)
            sdk.one_request(full)
            rejects("third-full-capacity-reservation-refused-before-mock", lambda: sdk.one_request(full))
            assert full.attempts == full.settlements == 2
            assert full.ledger.state["closed"] == "budget-exhausted"
            assert sum(row["charged"] for row in full.ledger.state["requests"]) == 4243456
            assert 2 * 2129920 <= 5000000 < 3 * 2129920
            passed("capacity-policy-2129920-reserve-two-near-cap-vs-twelve-small-settlements",
                   two_full_reservations=4259840, three_full_reservations=6389760)
            for mode in ("connection", "429", "500", "secret-error", "model-drift", "fingerprint-drift", "length", "disconnect-before", "disconnect-after", "missing",
                         "invalid", "conflict-alias", "conflict-snapshots", "overreport", "joint-overreport", "settle-fsync", "reserve-fsync",
                         "cancel-before", "cancel-after", "close-before", "close-after"):
                client = unit("failure-" + mode, mode)
                emitted = []
                def consume():
                    emitted.extend(sdk.one_request(client)[0])
                rejects(mode + "-stops-with-no-buffered-tool-release", consume)
                assert not emitted
                row = client.ledger.state["requests"][-1]
                if mode == "reserve-fsync":
                    assert client.attempts == client.settlements == 0 and client.ledger.poisoned
                else:
                    assert client.attempts == client.settlements == 1
                    assert all(stream.closed for stream in client.streams)
                expected_reason = ({"connection": "missing-usage", "429": "missing-usage", "500": "missing-usage",
                                    "secret-error": "missing-usage", "length": "length", "disconnect-after": "stream-disconnect",
                                    "cancel-after": "cancelled", "close-after": "generator-closed",
                                    "conflict-snapshots": "missing-usage", "invalid": "missing-usage",
                                    "conflict-alias": "missing-usage", "overreport": "usage-overreport",
                                    "joint-overreport": "usage-overreport", "model-drift": "response-identity-drift",
                                    "fingerprint-drift": "response-identity-drift"}).get(mode)
                if expected_reason:
                    assert client.ledger.state["closed"] == expected_reason
                expected_outcome = ({"connection": "transport-error", "429": "transport-error", "500": "transport-error",
                                     "secret-error": "transport-error", "invalid": "invalid-usage",
                                     "conflict-alias": "invalid-usage", "conflict-snapshots": "conflicting-usage",
                                     "missing": "stop", "length": "length"}).get(mode)
                if expected_outcome:
                    assert row["outcome"] == expected_outcome
                if mode in {"length", "disconnect-after", "cancel-after", "close-after", "model-drift", "fingerprint-drift"}:
                    assert row["usage"] == [100, 40, 20] and row["charged"] == 282
                elif mode in {"missing", "invalid", "conflict-alias", "conflict-snapshots", "connection", "429", "500", "secret-error", "disconnect-before", "cancel-before", "close-before"}:
                    assert row["usage"] is None and row["charged"] == 2129920
                elif mode == "overreport":
                    assert row["usage"] == [100, 40, 4097] and client.ledger.state["closed"]
                elif mode == "joint-overreport":
                    assert row["usage"] == [1048576, 0, 4096] and row["charged"] == 2129920
                    assert client.ledger.state["closed"] == "usage-overreport"
                attempts, settlements = client.attempts, client.settlements
                rejects(mode + "-no-repair-or-new-segment", lambda: sdk.one_request(client))
                assert (client.attempts, client.settlements) == (attempts, settlements)
            for mode in ("cancel-before", "cancel-after", "overreport", "settle-fsync"):
                failed = root / ("a0-" + mode)
                result = run_pilot(package, identity, contract, failed, "fictional-pilot-" + mode, scenario=mode)
                runs.append(result)
                assert result["status"] == "stopped" and result["attempts"] == result["settlements"] == 1
                assert result["external_wakes"] == 1 and result["unopened_records"] == 4 and result["actions"] == 0
                assert result["remaining_unread"] == 2
                if mode.startswith("cancel-"):
                    assert result["stop_reason"] == "cancelled"
                    assert result["cancellations"] == [{"active_turn": True, "hit": True, "cancelled": True,
                                                        "usage_observed": mode == "cancel-after"}]
                elif mode == "overreport":
                    assert result["stop_reason"] == result["ledger_closed"] == "usage-overreport"
                elif mode == "settle-fsync":
                    assert result["stop_reason"] == "persistence-unknown" and result["durable_settlements"] is None
                    assert result["charged_or_reserved_micro_cny"] is None and result["requests"] is None
                    assert result["unsettled_observed_usage"] == [100, 40, 20]
                assert not any(row["kind"] in {"output", "result"} for row in a0.replay._journal_rows(failed))
                passed("actual-A0-" + mode + "-no-action-or-later-daily-wake")
            # Filesystem and contract failures remain entirely synthetic.
            day_path = prepared / "archive/2023-03/14.log"
            saved = day_path.read_bytes()
            day_path.write_bytes(saved + b"fictional drift\n")
            rejects("prepared-hash-drift", lambda: load_prepared(prepared, identity))
            day_path.write_bytes(saved)
            source_manifest = prepared / "manifest.json"
            manifest_bytes = source_manifest.read_bytes()
            source_manifest.unlink()
            source_manifest.symlink_to(day_path)
            with patch.object(a0.replay, "_hash", side_effect=AssertionError("symlink target must not be read")):
                rejects("runtime-prepared-manifest-symlink-before-hash", lambda: verify_prepared(package))
            source_manifest.unlink()
            source_manifest.write_bytes(manifest_bytes)
            link = prepared / "archive/2023-03/17.log"
            link.symlink_to(day_path)
            rejects("prepared-symlink-or-extra-date", lambda: load_prepared(prepared, identity))
            link.unlink()
            for name, old, new in (("duplicate-id", b" | 102", b" | 101"),
                                   ("candidate-id-collision", b" | 101", b" | -1"),
                                   ("missing-sender", b"Fable day 1(2)", b"Fable day 1")):
                other, other_identity = synthetic_package(root / name)
                target = other / "archive/2023-03/14.log"
                target.write_bytes(target.read_bytes().replace(old, new, 1))
                revised = read_json(other / "manifest.json")
                revised["dates"][0]["sha256"] = a0.replay._hash(target)
                overall = hashlib.sha256()
                for entry in revised["dates"]:
                    overall.update(entry["date"].encode() + b"\0" + bytes.fromhex(entry["sha256"]))
                revised["input_sha256"] = overall.hexdigest()
                durable(other / "manifest.json", revised)
                rejects(name + "-with-valid-file-hashes-refused", lambda: load_prepared(other, load_identity(other_identity)))
            broken_identity = {**identity, "bot_names": {}}
            durable(root / "bad-identity.json", broken_identity)
            rejects("missing-identity-map", lambda: load_identity(root / "bad-identity.json"))
            for field, value in (("limits", {**LIMITS, "context": 180000}), ("prices", {**PRICES, "extra_fee": 1}),
                                 ("code", {}), ("model", "fictional/other")):
                changed = {**contract, field: value}
                rejects(field + "-contract-drift-before-client", lambda: validate_contract(changed, "synthetic", code_state()))
            wrong_live = {**contract, "mode": "live"}
            rejects("documentation-example-cannot-claim-live-applicability", lambda: validate_contract(wrong_live, "live", code_state()))
            rejects("unknown-capacity-mode", lambda: validate_contract({**contract, "mode": "other"}, "other", code_state()))
            corrupt = unit("corrupt-ledger")
            (corrupt.root / "ledger.json").write_text("{}")
            rejects("corrupt-ledger-before-mock", lambda: sdk.one_request(corrupt))
            assert corrupt.attempts == corrupt.settlements == 0
            assert (corrupt.root / "ledger.json").read_text() == "{}" and corrupt.ledger.poisoned
            manifest_client = unit("manifest-drift")
            manifest_value = {"contract": contract, "skills": {}}
            manifest_client.ledger.state["manifest"] = digest(manifest_value)
            manifest_client.ledger.save()
            durable(manifest_client.root / "manifest.json", {**manifest_value, "drift": True})
            rejects("runtime-manifest-drift-before-mock", lambda: sdk.one_request(manifest_client))
            assert manifest_client.attempts == manifest_client.settlements == 0
            assert manifest_client.ledger.state["closed"] == "manifest-drift"
            for argv in (["run"], ["run", "--prepared", "/fictional/never-read"],
                         ["run", "--confirm-paid", "--authorize-materials"]):
                args = arguments(argv)
                with patch(__name__ + ".read_json", side_effect=AssertionError("unauthorized file read")), patch(__name__ + ".PilotClient", side_effect=AssertionError("unauthorized client")):
                    rejects("missing-live-authorization-or-input-refused-before-read", lambda: live_run(args))
            bound_live = declaration("live")
            bound_live["code"] = code_state()
            bound_live["capacity_evidence"].update(status="operator-declared-target-applicability",
                                                  applicability="fictional gate probe; no service validation")
            bound_live["pilot_binding"] = {"id": "fictional-bound", "root": str(root / "bound-root"),
                                           "record": str(root / "bound-record.json")}
            args = arguments(["run", "--authorize-materials", "--confirm-paid", "--prepared", "/fictional/never-read",
                              "--identity", "/fictional/never-read", "--contract", "/fictional/never-read",
                              "--credential-file", "/fictional/never-read", "--pilot-record", str(root / "different-record.json"),
                              "--pilot-id", "fictional-bound", "--output", str(root / "different-root")])
            with patch(__name__ + ".read_json", return_value=bound_live), patch(__name__ + ".load_identity", side_effect=AssertionError("must reject binding before material read")):
                rejects("same-contract-cannot-switch-pilot-root-or-claim-record", lambda: live_run(args))
            secret = root / "fictional-credential"
            secret.write_text("sk-FICTIONAL-DOCTOR-SECRET\n")
            secret.chmod(0o600)
            assert read_credential(secret) == "sk-FICTIONAL-DOCTOR-SECRET"
            secret.chmod(0o644)
            rejects("nonprivate-credential-file", lambda: read_credential(secret))
            secret.chmod(0o600)
            assert "sk-FICTIONAL-DOCTOR-SECRET" not in output.getvalue()
            assert "sk-fictional-pilot" not in output.getvalue()
            for path in root.rglob("*"):
                if path.is_file() and path != secret:
                    assert b"sk-FICTIONAL-DOCTOR-SECRET" not in path.read_bytes()
                    assert b"sk-fictional-pilot" not in path.read_bytes()
            passed("credential-not-in-manifest-request-stop-or-console")
            assert all(left is right for left, right in zip(guarded, a0.binding_snapshot()))
            assert os.environ.get("TZ") == prior_tz
            assert all(value == 0 for value in counters.values()), counters
            passed("socket-QQ-production-write-zero-and-finally-bindings-TZ-restored", **counters)
    finally:
        for client in clients:
            client.close()
        active[0] = False
    assert all(left is right for left, right in zip(initial, a0.binding_snapshot()))
    durable(root / "console.json", {"synthetic_console": output.getvalue()})
    result = {"status": "ok", "schema": "a0-pilot-doctor-v1", "assertions": reports, "assertion_count": len(reports),
              "fake_http_attempts": sum(client.attempts for client in clients) + sum(run["attempts"] for run in runs),
              "settlements": sum(client.settlements for client in clients) + sum(run["settlements"] for run in runs),
              "sentinels": counters, "real_HTTP": 0, "openai": openai.__version__, "httpx": httpx.__version__,
              "limitations": ["only built-in synthetic prepared package and MockTransport exercised",
                              "live target applicability/failure billing/material/credential authorization unverified",
                              "capacity reservation conditional on declared provider contract; provider bill unknown",
                              "group ordinary LF records with explicit sender and message IDs only; no notices/sidecars",
                              "partial resume/fork unsupported; no paid or memory-benefit evidence"]}
    durable(root / "doctor.json", result)
    return result


def main(argv=None):
    args = arguments(argv)
    try:
        if args.command == "contract-template":
            value = declaration("live")
            value["code"] = code_state()
            print(json.dumps(value, ensure_ascii=False, indent=2))
            return 0
        if args.command == "run":
            # Production console can contain private input; it never goes to the public report.
            with redirect_stdout(io.StringIO()):
                result = live_run(args)
        else:
            selected = getattr(args, "output", None)
            if selected:
                root = safe_path(selected)
                if root.exists():
                    raise ValueError("doctor requires a fresh root")
                root.mkdir(mode=0o700)
            else:
                root = Path(tempfile.mkdtemp(prefix="bot-a0-pilot-", dir=a0.external_root(tempfile.gettempdir())))
            with a0.replay._locked(root):
                result = doctor(root)
            result = {**result, "output": str(root)}
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] in {"ok", "complete"} else 1
    except (OSError, RuntimeError, ValueError, KeyError, AssertionError):
        # Never interpolate path arguments, credential text or SDK error bodies.
        print("A0 pilot stopped; no implicit retry. Check the private output if one was created.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
