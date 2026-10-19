"""Real SDK / production parser calibration over a closed, synthetic HTTP peer."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import a0_calibration as a0
from a0_ledger import LIMITS, PRICES, Ledger, canonical, cost, digest, durable
import httpx
import openai
from mods import llm

MODEL = "deepseek/deepseek-flash"
WIRE_MODEL = "deepseek-flash"
PILOT = "fictional-a0-sdk-v1"
BASE_URL = "https://a0-sdk.invalid"
SDK_LIMITS = {**LIMITS, "context": 180000}
# Public peak-price numbers, used only with synthetic usage, never a live quote.
SDK_PRICES = {**PRICES, "input": "2", "cached": "0.04", "output": "8"}
SCENARIOS = frozenset({"normal", "connection", "429", "500", "length", "length-json",
                       "disconnect-before", "disconnect-after", "cancel-before", "cancel-after",
                       "close-before", "close-after", "missing", "alias", "conflict-alias",
                       "conflict-snapshots", "invalid", "overreport", "settle-fsync", "plain-third", "combined-context"})
SOURCES = ["https://api-docs.deepseek.com/api/create-chat-completion/",
           "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
           "https://api-docs.deepseek.com/guides/thinking_mode/",
           "https://github.com/openai/openai-python/tree/v2.49.0"]


@contextmanager
def no_sdk_environment(reads):
    class EmptyEnvironment(dict):
        def get(self, key, default=None):
            reads.append(key)
            return super().get(key, default)
    # Replace the mapping reference, never copy/inspect real environment values.
    with patch.object(os, "environ", EmptyEnvironment(TZ="Asia/Shanghai")):
        yield


def usage_for(scenario):
    usage = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120,
             "prompt_cache_hit_tokens": 40, "prompt_cache_miss_tokens": 60,
             "completion_tokens_details": {"reasoning_tokens": 12}}
    if scenario == "alias":
        usage.pop("prompt_cache_hit_tokens")
        usage["prompt_tokens_details"] = {"cached_tokens": 40}
    elif scenario == "conflict-alias":
        usage["prompt_tokens_details"] = {"cached_tokens": 41}
    elif scenario == "invalid":
        usage["completion_tokens"] = -1
    elif scenario == "overreport":
        usage.update(completion_tokens=4097, total_tokens=4197)
    return usage


def chunk(delta, *, finish=None, usage=None):
    result = {"id": "fictional-completion", "object": "chat.completion.chunk", "created": 946700000,
              "model": WIRE_MODEL, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    if usage is not None:
        result["usage"] = usage
    return result


class SyntheticSSE(httpx.SyncByteStream):
    def __init__(self, owner, number):
        self.owner, self.number, self.closed = owner, number, False

    def __iter__(self):
        mode = self.owner.scenario
        first = (self.number - 1) % 3 == 0
        name = "take" if first else "say"
        arguments = ({"source": "g1", "count": 10} if first else
                     {"text": "fictional SDK echo", "target": "g1", "final_call": self.number % 3 == 0})
        encoded = json.dumps(arguments)
        delta = {"role": "assistant", "reasoning_content": f"synthetic reasoning {self.number} ",
                 "content": "partial text\n\nmore text" if mode == "close-before" else "",
                 "tool_calls": [{"index": 0, "id": f"fictional-call-{self.number}", "type": "function",
                                  "function": {"name": name, "arguments": encoded[:5]}}]}
        plain = mode == "plain-third" and self.number == 3
        if plain:
            delta.pop("tool_calls")
            delta["content"] = "synthetic reply without a tool"
        yield b"data: " + canonical(chunk(delta)) + b"\n\n"
        if mode == "disconnect-before":
            raise httpx.ReadError("synthetic disconnect before usage")
        tail = {"reasoning_content": "continued"}
        if not plain:
            tail["tool_calls"] = [{"index": 0, "function": {"arguments": encoded[5:]}}]
        yield b"data: " + canonical(chunk(tail)) + b"\n\n"
        usage = None if mode == "missing" else usage_for(mode)
        if mode == "conflict-snapshots":
            yield b"data: " + canonical({**chunk({}), "choices": [], "usage": usage}) + b"\n\n"
            usage = {**usage, "prompt_tokens": 101, "prompt_cache_miss_tokens": 61, "total_tokens": 121}
        yield b"data: " + canonical(chunk({}, finish="length" if mode == "length" else "stop" if plain else "tool_calls", usage=usage)) + b"\n\n"
        if mode == "disconnect-after":
            raise httpx.ReadError("synthetic disconnect after usage")
        yield b"data: [DONE]\n\n"

    def close(self):
        self.closed = True


def normalize_usage(raw):
    allowed = {"prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens",
               "prompt_cache_miss_tokens", "prompt_tokens_details", "completion_tokens_details"}
    if not isinstance(raw, dict) or set(raw) - allowed:
        raise ValueError("unknown usage/fee fields")
    def integer(value):
        if type(value) is not int or value < 0:
            raise ValueError("invalid usage integer")
        return value
    prompt, output, total = (integer(raw[key]) for key in ("prompt_tokens", "completion_tokens", "total_tokens"))
    if total != prompt + output:
        raise ValueError("conflicting total usage")
    details = raw.get("prompt_tokens_details") or {}
    completion = raw.get("completion_tokens_details") or {}
    if set(details) - {"cached_tokens"} or set(completion) - {"reasoning_tokens"}:
        raise ValueError("unknown usage subcategory")
    aliases = [integer(value) for value in (raw.get("prompt_cache_hit_tokens"), details.get("cached_tokens")) if value is not None]
    if len(set(aliases)) > 1:
        raise ValueError("conflicting cache aliases")
    cached = aliases[0] if aliases else 0
    if cached > prompt or integer(completion.get("reasoning_tokens", 0)) > output:
        raise ValueError("usage subcategory exceeds inclusive total")
    if "prompt_cache_miss_tokens" in raw and integer(raw["prompt_cache_miss_tokens"]) + cached != prompt:
        raise ValueError("conflicting cache miss count")
    return [prompt, cached, output]


class ObservedStream:
    """Observe real SDK chunks; parsing remains LLMClient._stream_response."""
    def __init__(self, owner, stream):
        self.owner, self.stream = owner, stream
        self.iterator = iter(stream)

    def __iter__(self):
        return self

    def __next__(self):
        with no_sdk_environment(self.owner.environment_reads):
            value = next(self.iterator)
        self.owner.observe(getattr(value, "usage", None))
        mode = self.owner.scenario
        if mode == "cancel-before" or (mode == "cancel-after" and self.owner.active["raw_usage"]):
            from mods import context, oplog
            turn = context.get_turn(oplog.AGENT_WINDOW)
            hit = context.cancel_turn(oplog.AGENT_WINDOW)
            self.owner.cancellations.append({"active_turn": turn is not None, "hit": hit,
                                             "cancelled": turn.cancelled if turn else False,
                                             "usage_observed": bool(self.owner.active["raw_usage"])})
            raise RuntimeError("synthetic cancellation after SDK chunk")
        return value

    def close(self):
        self.stream.close()


class ClosedSDKClient(llm.LLMClient):
    """Only built-in scenarios; no key/config/client/transport injection seam."""
    def __init__(self, root: Path, scenario="normal"):
        if scenario not in SCENARIOS:
            raise ValueError("unknown closed SDK fixture")
        self.root, self.scenario = a0.external_root(root), scenario
        if any(self.root.iterdir()):
            raise ValueError("SDK calibration requires a fresh root; resume/fork unsupported")
        self.environment_reads = []
        self.attempts, self.settlements = 0, 0
        self.active, self.streams, self.responses, self.exchanges = None, [], [], []
        self.cancellations, self.context_refusals = [], []
        self.config = {"default_model": MODEL, "providers": {"deepseek": {"models": {
            WIRE_MODEL: {"function_calling": True, "vision": False, "tool_images": False}}}}}
        self.strict_tools = True
        self.request_policy = lambda selection, request: {"max_tokens": SDK_LIMITS["output"]}
        self.frozen = {"schema": "a0-sdk-closed-v1", "scenario": scenario, "model": MODEL,
                       "wire_model": WIRE_MODEL, "base_url": BASE_URL, "contract_target": "https://api.deepseek.com",
                       "contract_as_of": "2026-10-19", "contract_model_snapshot": "V4.1-Flash-1017",
                       "openai": openai.__version__, "httpx": httpx.__version__, "sources": SOURCES,
                       "limits": SDK_LIMITS, "prices": SDK_PRICES,
                       "code": {**a0.code_hashes(), "experiments/a0_sdk_calibration.py": a0.replay._hash(Path(__file__))},
                       "input_bound": "synthetic canonical full HTTP envelope bytes + 1024; not model token proof",
                       "persist_reasoning": True, "partial_restore": "unsupported",
                       "joint_context": 4096 if scenario == "combined-context" else 1000000}
        durable(root / "manifest.json", self.frozen)
        self.ledger = Ledger(root, PILOT, digest(self.frozen), limits=SDK_LIMITS, prices=SDK_PRICES)
        with no_sdk_environment(self.environment_reads):
            self.http = httpx.Client(transport=httpx.MockTransport(self._mock), trust_env=False,
                                     follow_redirects=False, event_hooks={"request": [self._reserve_http]})
            self.sdk = openai.OpenAI(api_key="sk-fictional-sdk-calibration", admin_api_key="fictional-admin",
                                    organization="fictional-org", project="fictional-project",
                                    webhook_secret="fictional-webhook", base_url=BASE_URL,
                                    http_client=self.http, max_retries=0, timeout=1)
        # A narrow facade adds frozen request options and raw observation, then
        # invokes the actual SDK. The production generator and parser are intact.
        self.clients = {"deepseek": SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=self._create)))}

    def _create(self, **params):
        if self.active is None or params["model"] != WIRE_MODEL:
            raise RuntimeError("SDK request outside declared generation")
        params["extra_body"] = {"thinking": {"type": "enabled"}}
        params["reasoning_effort"] = "high"
        params["extra_headers"] = {"X-Synthetic-Request-ID": f"{PILOT}/{len(self.ledger.state['requests']) + 1:04d}"}
        with no_sdk_environment(self.environment_reads):
            response = self.sdk.chat.completions.create(**params)
        if params["stream"]:
            observed = ObservedStream(self, response)
            self.responses.append(observed)
            return observed
        self.observe(response.usage)
        return response

    def _reserve_http(self, request):
        if self.active is None or self.active["row"] is not None:
            raise RuntimeError("second SDK request in one generation")
        raw = request.content
        body = json.loads(raw)
        if (str(request.url) != BASE_URL + "/chat/completions" or request.method != "POST"
                or body["model"] != WIRE_MODEL or body["max_tokens"] != 4096
                or body["tool_choice"] != "auto" or body["thinking"] != {"type": "enabled"}
                or body["reasoning_effort"] != "high"
                or (body["stream"] and body["stream_options"] != {"include_usage": True})):
            raise RuntimeError("final SDK request contract drift")
        envelope = {"url": str(request.url), "method": request.method, "body": body,
                    "serialized_body": raw.decode(), "body_sha256": hashlib.sha256(raw).hexdigest(),
                    "request_id": request.headers["X-Synthetic-Request-ID"], "provider_model": MODEL}
        synthetic_bound = len(canonical(envelope)) + 1024
        if synthetic_bound + 4096 > self.frozen["joint_context"]:
            evidence = {"synthetic_input_bound": synthetic_bound, "input_limit": SDK_LIMITS["context"],
                        "output_allowance": 4096, "joint_context": self.frozen["joint_context"]}
            self.context_refusals.append(evidence)
            durable(self.root / "context-refusal.json", evidence)
            raise RuntimeError("synthetic input+output context limit")
        row = self.ledger.reserve(envelope, 4096)
        self.active["row"] = row
        if row["id"] != envelope["request_id"]:
            raise RuntimeError("pilot/request identity mismatch")
        durable(self.root / f"request-{len(self.ledger.state['requests']):04d}.json", envelope)
        self.exchanges.append(envelope)

    def _mock(self, request):
        active = self.active
        if active is None or active["row"] is None or active["attempted"]:
            raise RuntimeError("mock attempt without unique durable reservation")
        assert json.loads((self.root / "ledger.json").read_text())["state"]["requests"][-1]["status"] == "reserved"
        assert request.content.decode() == self.exchanges[-1]["serialized_body"]
        assert request.headers["X-Synthetic-Request-ID"] == active["row"]["id"]
        active["attempted"] = True
        self.attempts += 1
        if self.scenario == "connection":
            raise httpx.ConnectError("synthetic connection failure", request=request)
        if self.scenario in {"429", "500"}:
            return httpx.Response(int(self.scenario), json={"error": {"message": "synthetic status failure", "type": "synthetic"}})
        if self.scenario == "length-json":
            return httpx.Response(200, json={"id": "fictional-json", "object": "chat.completion", "created": 946700000,
                "model": WIRE_MODEL, "choices": [{"index": 0, "finish_reason": "length", "message": {
                    "role": "assistant", "content": "incomplete", "reasoning_content": "synthetic reasoning", "tool_calls": []}}],
                "usage": usage_for("normal")})
        stream = SyntheticSSE(self, self.attempts)
        self.streams.append(stream)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    def observe(self, usage):
        if usage is not None:
            self.active["raw_usage"].append(usage.model_dump(exclude_none=True))

    def _settle(self, error):
        active = self.active
        if active["row"] is None:
            return
        raw = active["raw_usage"]
        valid, invalid = [], []
        for value in raw:
            try:
                valid.append(normalize_usage(value))
            except (ValueError, TypeError, KeyError) as failure:
                invalid.append(str(failure))
        conflict = len({tuple(value) for value in valid}) > 1
        outcome = ("invalid-usage: " + "; ".join(invalid) if invalid else
                   "conflicting-usage" if conflict else type(error).__name__ if error else "stop")
        usage = max(valid, key=lambda value: cost(SDK_PRICES, *value)) if valid else None
        evidence = {"request_id": active["row"]["id"], "raw_usage": raw, "normalized": valid,
                    "outcome": outcome, "parser_error": str(error) if error else None,
                    "response_closed": all(stream.closed for stream in self.streams)}
        durable(self.root / f"observation-{len(self.ledger.state['requests']):04d}.json", evidence)
        self.settlements += 1
        if self.scenario == "settle-fsync":
            with patch("a0_ledger.os.fsync", side_effect=OSError("synthetic settlement fsync failure")):
                self.ledger.settle(active["row"], usage, outcome)
        else:
            self.ledger.settle(active["row"], usage, outcome)

    def generate_response(self, *args, **kwargs):
        if self.active is not None:
            raise RuntimeError("single-flight SDK generation already active")
        self.active = {"row": None, "raw_usage": [], "attempted": False}
        buffered, inner, error, completed = [], None, None, None
        try:
            inner = super().generate_response(*args, **kwargs)
            while True:
                try:
                    value = next(inner)
                except StopIteration as stop:
                    completed = stop.value
                    break
                buffered.append(value)
                if self.scenario in {"close-before", "close-after"}:
                    inner.close()  # Close the actual production parser generator.
                    raise RuntimeError("synthetic parser generator closed")
        except BaseException as failure:
            error = failure
        finally:
            if inner is not None:
                try:
                    inner.close()
                except BaseException as close_error:
                    error = error or close_error
            for response in self.responses:
                try:
                    response.close()
                except BaseException as close_error:
                    error = error or close_error
            try:
                self._settle(error)
            finally:
                self.active = None
        if error is not None:
            if not self.ledger.poisoned and not self.ledger.state["closed"]:
                self.ledger.stop(type(error).__name__)
            raise error
        for value in buffered:
            yield value
        return completed

    def close(self):
        for response in self.responses:
            response.close()
        self.sdk.close()


def fixture_messages():
    return [{"role": "system", "content": "synthetic full history header"},
            {"role": "user", "content": "fictional calibration request"}]


def one_request(client, *, stream=True):
    tools = [llm.Tool(lambda text: text, "edit_hint")]
    generator = client.generate_response(fixture_messages(), tools=tools, model=MODEL,
                                         tool_choice="auto", stream=stream, do_process_image=False)
    values = []
    while True:
        try:
            values.append(next(generator))
        except StopIteration as result:
            return values, result.value



def check_native_pairs(body, numbers):
    messages = body["messages"]
    for number in numbers:
        call_id = f"fictional-call-{number}"
        assistant = next(message for message in messages if any(
            call.get("id") == call_id for call in message.get("tool_calls", [])))
        assert assistant["reasoning_content"] == f"synthetic reasoning {number} continued"
        assert sum(message.get("tool_call_id") == call_id for message in messages) == 1
        position = messages.index(assistant)
        result = next(index for index, message in enumerate(messages) if message.get("tool_call_id") == call_id)
        assert position < result


def drive_case(client):
    from mods import chat, context, llm, oplog, storage
    before = a0.binding_snapshot()
    with a0.isolated(client.root, client.ledger) as scope:
        with patch.object(llm, "client", client), patch.object(chat, "llm_config", client.config):
            for end in (1, 3):
                client.ledger.wake()
                a0.open_prefix(scope, end)
                chat.run_offline(MODEL, ("group", 1), fact=a0.FACT, registry=scope.registry,
                                 on_chunk=lambda value: None, persist_reasoning=True,
                                 send_context=scope.send_context, send_sink=scope.say)
                assert context.current() is None
                a0.check_past(scope)
            storage.save()
            a0.replay._assert_storage_saved(storage)
            a0.replay._assert_settled(a0.replay._journal_rows(client.root))
            assert all(value == 0 for value in scope.counters.values())
            assert client.attempts == 6 and client.settlements == 6
            check_native_pairs(client.exchanges[2]["body"], [1, 2])
            check_native_pairs(client.exchanges[5]["body"], [1, 2, 4, 5] if client.scenario == "plain-third" else [1, 2, 3, 4, 5])
            # The first output is formally read through the actual take/provider
            # path; its following request must contain that fictional record.
            assert "fictional event 101" in canonical(client.exchanges[1]["body"]).decode()
            assert "take" in scope.actions and "say" in scope.actions
            if client.scenario == "plain-third":
                assistant = next(message for message in client.exchanges[3]["body"]["messages"]
                                 if message.get("role") == "assistant" and message.get("content") == "synthetic reply without a tool")
                assert assistant["reasoning_content"] == "synthetic reasoning 3 continued"
                assert not assistant.get("tool_calls")
    assert all(left is right for left, right in zip(before, a0.binding_snapshot()))


def doctor(root):
    from contextlib import ExitStack, redirect_stdout
    import io
    from mods import connect, context, message, oplog
    reports, clients = [], []
    counters = {"socket": 0, "QQ": 0, "external_write": 0}
    audit_active = [True]
    def audit(event, args):
        if not audit_active[0]:
            return
        path = None
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            flags = args[2]
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC):
                path = Path(os.fsdecode(args[0]))
        elif event in {"os.mkdir", "os.remove", "os.rmdir"}:
            path = Path(os.fsdecode(args[0]))
        if path is not None and path != Path(os.devnull) and not path.resolve().is_relative_to(root):
            counters["external_write"] += 1
            raise AssertionError("SDK doctor blocked write outside synthetic root")
    sys.addaudithook(audit)
    def block_socket(*args, **kwargs):
        counters["socket"] += 1
        raise AssertionError("SDK doctor blocked socket")
    def block_QQ(*args, **kwargs):
        counters["QQ"] += 1
        raise AssertionError("SDK doctor blocked QQ")
    def passed(name, **details):
        reports.append({"assertion": name, "status": "pass", **details})
    def rejects(name, action):
        try:
            action()
        except (openai.APIError, OSError, RuntimeError, ValueError) as error:
            passed(name, observed=type(error).__name__ + ": " + str(error))
        else:
            raise AssertionError(name + " was accepted")
    def new_client(name, scenario="normal"):
        path = root / name
        path.mkdir()
        client = ClosedSDKClient(path, scenario)
        clients.append(client)
        return client
    output = io.StringIO()
    caller, prior, latest = {"synthetic": "SDK caller event"}, context.current(), context.latest()
    had_event = hasattr(context._local, "event")
    initial = a0.binding_snapshot()
    context.set_current(caller)
    try:
        with ExitStack() as stack, redirect_stdout(output):
            for name in ("connect", "connect_ex"):
                stack.enter_context(patch.object(socket.socket, name, block_socket))
            stack.enter_context(patch.object(socket, "create_connection", block_socket))
            stack.enter_context(patch.object(socket, "getaddrinfo", block_socket))
            for name in ("send", "sendmsg", "_send_now"):
                stack.enter_context(patch.object(message, name, block_QQ))
            stack.enter_context(patch.object(connect, "call_api", block_QQ))
            guarded = a0.binding_snapshot()
            normal = new_client("normal")
            values, response = one_request(normal)
            assert normal.attempts == normal.settlements == 1
            assert response.reasoning_content == "synthetic reasoning 1 continued"
            assert len([value for value in values if value.role == "tool"]) == 1
            usage = next(value for value in values if value.total_tokens)
            assert [usage.prompt_tokens, usage.cached_tokens, usage.completion_tokens] == [100, 40, 20]
            row = normal.ledger.state["requests"][0]
            assert row["usage"] == [100, 40, 20] and row["charged"] == 282
            envelope = json.loads((normal.root / "request-0001.json").read_text())
            body = envelope["body"]
            assert json.loads(envelope["serialized_body"]) == body
            assert hashlib.sha256(envelope["serialized_body"].encode()).hexdigest() == envelope["body_sha256"]
            assert body["messages"] == fixture_messages() and body["tools"] and "strict" not in body["tools"][0]["function"]
            assert not any(key in body for key in ("search", "fetch", "images", "response_format"))
            assert body["stream_options"] == {"include_usage": True} and body["max_tokens"] == 4096
            assert row["id"] == envelope["request_id"] and envelope["provider_model"] == MODEL and body["model"] == WIRE_MODEL
            assert all(stream.closed for stream in normal.streams)
            passed("final-serialized-http-body-reserved-before-single-mock-attempt",
                   provider_model=MODEL, wire_model=body["model"], body_sha256=envelope["body_sha256"])
            passed("production-stream-parser-reasoning-tools-last-nonempty-choices-usage", usage=row["usage"], micro_cny=row["charged"])
            assert normal.sdk.max_retries == 0 and normal.http._trust_env is False
            passed("frozen-sdk-and-httpx", openai=openai.__version__, httpx=httpx.__version__, max_retries=0)
            frozen_bytes = (normal.root / "manifest.json").read_bytes()
            rejects("existing-sdk-root-cannot-reset-manifest-or-budget", lambda: ClosedSDKClient(normal.root))
            assert (normal.root / "manifest.json").read_bytes() == frozen_bytes
            alias = new_client("alias", "alias")
            alias_values, _ = one_request(alias)
            assert alias.ledger.state["requests"][0]["usage"] == [100, 40, 20]
            assert next(value for value in alias_values if value.total_tokens).cached_tokens == 40
            passed("cache-alias-and-reasoning-inclusive-output-not-double-charged")
            for mode in ("connection", "429", "500", "length", "length-json", "disconnect-before", "disconnect-after",
                         "cancel-before", "cancel-after", "close-before", "close-after", "missing",
                         "conflict-alias", "conflict-snapshots", "invalid", "overreport", "settle-fsync"):
                client = new_client(mode, mode)
                emitted = []
                def consume():
                    generator = client.generate_response(fixture_messages(), tools=[llm.Tool(lambda text: text, "edit_hint")],
                                                         model=MODEL, tool_choice="auto", stream=mode != "length-json",
                                                         do_process_image=False)
                    for value in generator:
                        emitted.append(value)
                rejects(mode + "-stops-without-buffered-tool-release", consume)
                assert not emitted and client.attempts == client.settlements == 1
                assert len(client.ledger.state["requests"]) == 1
                assert all(stream.closed for stream in client.streams)
                observation = json.loads((client.root / "observation-0001.json").read_text())
                row = client.ledger.state["requests"][0]
                if mode in {"length", "length-json", "disconnect-after", "cancel-after", "close-after"}:
                    assert row["usage"] == [100, 40, 20] and row["charged"] == 282
                    assert observation["raw_usage"]
                elif mode in {"connection", "429", "500", "disconnect-before", "cancel-before", "close-before", "missing", "conflict-alias", "invalid"}:
                    assert row["usage"] is None and row["charged"] == row["reserved"]
                elif mode == "overreport":
                    assert row["usage"] == [100, 40, 4097] and client.ledger.state["closed"]
                elif mode == "conflict-snapshots":
                    assert row["usage"] == [101, 40, 20] and len(observation["raw_usage"]) == 2
                elif mode == "settle-fsync":
                    disk = json.loads((client.root / "ledger.json").read_text())["state"]
                    assert disk["requests"][0]["status"] == "reserved" and client.ledger.poisoned
                attempts, settlements = client.attempts, client.settlements
                rejects(mode + "-no-followup-or-repair-request", lambda: one_request(client))
                assert (client.attempts, client.settlements) == (attempts, settlements)
            for mode in ("normal", "plain-third"):
                client = new_client("a0-" + mode, mode)
                drive_case(client)
                assert context.current() is caller
                assert all(left is right for left, right in zip(guarded, a0.binding_snapshot()))
                passed("actual-A0-three-wire-requests-two-tool-pairs-and-cross-drive-" + mode,
                       attempts=client.attempts, settlements=client.settlements)
            for mode in ("overreport", "settle-fsync", "cancel-before", "cancel-after"):
                client = new_client("a0-failure-" + mode, mode)
                rejects("actual-A0-" + mode + "-no-action-dispatched", lambda: drive_case(client))
                rows = a0.replay._journal_rows(client.root)
                assert not any(row["kind"] in {"output", "result"} for row in rows)
                assert client.attempts == client.settlements == 1 and context.current() is caller
                if mode.startswith("cancel-"):
                    assert client.cancellations == [{"active_turn": True, "hit": True, "cancelled": True,
                                                     "usage_observed": mode == "cancel-after"}]
                    row = client.ledger.state["requests"][0]
                    assert row["usage"] == ([100, 40, 20] if mode == "cancel-after" else None)
                    assert row["charged"] == (282 if mode == "cancel-after" else row["reserved"])
                    assert all(stream.closed for stream in client.streams)
                    passed("actual-A0-" + mode + "-hit-active-turn-no-tools", cancellation=client.cancellations[0])
                assert all(left is right for left, right in zip(guarded, a0.binding_snapshot()))
            capped = new_client("attempt-cap")
            for _ in range(12):
                one_request(capped)
            rejects("thirteenth-rejected-before-mock-transport", lambda: one_request(capped))
            assert capped.attempts == capped.settlements == len(capped.ledger.state["requests"]) == 12
            joint = new_client("joint-context", "combined-context")
            rejects("input-alone-fits-but-input-plus-output-exceeds-frozen-context", lambda: one_request(joint))
            assert joint.attempts == joint.settlements == len(joint.ledger.state["requests"]) == 0
            assert joint.ledger.state["closed"]
            evidence = joint.context_refusals[0]
            assert evidence["synthetic_input_bound"] <= evidence["input_limit"]
            assert evidence["synthetic_input_bound"] + 4096 > evidence["joint_context"]
            passed("context-refusal-precedes-reserve-and-mock-attempt", **evidence)
            closed = new_client("consumer-close")
            generator = closed.generate_response(fixture_messages(), tools=[llm.Tool(lambda text: text, "edit_hint")],
                                                 model=MODEL, tool_choice="auto", do_process_image=False)
            assert next(generator).role == "tool"
            generator.close()
            assert closed.attempts == closed.settlements == 1 and all(stream.closed for stream in closed.streams)
            passed("outer-generator-close-does-not-settle-twice")
            poison = {"OPENAI_API_KEY": "poison-fictional-key", "OPENAI_ADMIN_KEY": "poison-fictional-admin",
                      "OPENAI_ORG_ID": "poison-fictional-org", "OPENAI_PROJECT_ID": "poison-fictional-project",
                      "OPENAI_WEBHOOK_SECRET": "poison-fictional-hook", "OPENAI_BASE_URL": "https://poison.invalid",
                      "OPENAI_CUSTOM_HEADERS": "X-Poison: fictional", "HTTP_PROXY": "http://poison.invalid",
                      "HTTPS_PROXY": "http://poison.invalid", "SSL_CERT_FILE": "/fictional/absent.pem"}
            with patch.object(os, "environ", poison):
                client = new_client("controlled-environment")
                one_request(client)
            assert client.sdk.api_key == "sk-fictional-sdk-calibration" and client.sdk.organization == "fictional-org"
            assert client.sdk.project == "fictional-project" and client.sdk.webhook_secret == "fictional-webhook"
            assert str(client.sdk.base_url) == BASE_URL and client.attempts == 1
            assert "OPENAI_API_KEY" not in client.environment_reads
            assert "OPENAI_ORG_ID" not in client.environment_reads and "OPENAI_PROJECT_ID" not in client.environment_reads
            passed("poisoned-synthetic-environment-cannot-configure-sdk", controlled_reads=sorted(set(client.environment_reads)))
            assert 12 * cost(SDK_PRICES, 180000, 0, 4096, reserve=True) == 4713216
            assert 12 * cost(SDK_PRICES, 262144, 0, 4096, reserve=True) == 6684672
            passed("peak-price-arithmetic-only-not-real-token-upper-bound", candidate_micro_cny=4713216,
                   T01_input_ceiling_micro_cny=6684672)
            assert all(value == 0 for value in counters.values()), counters
            passed("socket-QQ-external-write-sentinels-zero", **counters)
    finally:
        for client in clients:
            client.close()
        context.set_current(prior)
        if not had_event:
            context.clear_current()
        context._latest = latest
        audit_active[0] = False
    assert all(left is right for left, right in zip(initial, a0.binding_snapshot()))
    durable(root / "parser-console.json", {"synthetic_console": output.getvalue()})
    result = {"status": "ok", "schema": "a0-sdk-doctor-v1", "assertion_count": len(reports), "assertions": reports,
              "openai": openai.__version__, "httpx": httpx.__version__, "fake_http_attempts": sum(client.attempts for client in clients),
              "settlements": sum(client.settlements for client in clients), "real_HTTP": 0, "QQ": 0,
              "limitations": ["synthetic MockTransport only", "no tokenizer/input-upper-bound proof", "no paid readiness",
                              "partial resume/fork unsupported", "only the closed adapter disables SDK retries"]}
    durable(root / "doctor.json", result)
    return result

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", default="doctor", choices=("doctor",))
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    try:
        if args.output:
            root = a0.replay._output_path(str(a0.external_root(args.output)))
            root.mkdir(mode=0o700, parents=True)
        else:
            root = Path(tempfile.mkdtemp(prefix="bot-a0-sdk-", dir=a0.external_root(tempfile.gettempdir())))
        with a0.replay._locked(root):
            result = doctor(root)
        print(json.dumps({**result, "output": str(root)}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, RuntimeError, AssertionError, KeyError) as error:
        print(f"A0 SDK calibration: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
