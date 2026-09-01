from pathlib import Path
from types import ModuleType, SimpleNamespace
import importlib
import json
import sys
import unittest

import httpx


ROOT = Path(__file__).resolve().parents[1]


def _load_llm_module():
    """Load mods.llm without booting the production mods package."""
    for name in tuple(sys.modules):
        if name == "mods.llm" or name.startswith("mods.llm."):
            sys.modules.pop(name, None)
    mods = ModuleType("mods")
    mods.__path__ = [str(ROOT / "mods")]
    image = ModuleType("mods.image")
    image.AUTO_IMAGE_SPLIT_PROMPT = "[自动图片切割]"
    image.image_uri_to_data_uri = lambda _uri: "data:image/png;base64,AAAA"
    image.split_long_image_data_uri = lambda uri: ([uri], False)
    mods.image = image
    sys.modules["mods"] = mods
    sys.modules["mods.image"] = image
    return importlib.import_module("mods.llm")


llm = _load_llm_module()


class _SilentStreamPrinter:
    def __init__(self, _model):
        pass

    def chunk(self, *_args):
        pass

    def finish(self):
        pass

    def tool_call(self, *_args):
        pass


llm.console.StreamPrinter = _SilentStreamPrinter
llm.console.notice = lambda *_args, **_kwargs: None
llm.console.print_request = lambda *_args, **_kwargs: None
llm.console.write = lambda *_args, **_kwargs: None
llm.console.error = lambda *_args, **_kwargs: None


class _Delta:
    def __init__(self, *, role=None, reasoning_content=None, content=None, tool_calls=None):
        self.role = role
        self.tool_calls = tool_calls
        self._data = {
            "reasoning_content": reasoning_content,
            "content": content,
        }

    def to_dict(self, exclude_unset=False):
        return self._data


def _stream_chunk(**delta):
    return SimpleNamespace(
        usage=None,
        choices=[SimpleNamespace(delta=_Delta(**delta))],
    )


def _tool_delta(call_id, name, index=0):
    return SimpleNamespace(
        index=index,
        id=call_id,
        function=SimpleNamespace(name=name, arguments="{}"),
    )


class _StrictStreamingCompletions:
    def __init__(self):
        self.requests = []

    def create(self, **params):
        self.requests.append(params)
        request_number = len(self.requests)
        if request_number == 1:
            return iter([
                _stream_chunk(role="assistant", reasoning_content="first "),
                _stream_chunk(reasoning_content="reasoning"),
                _stream_chunk(content="先查看"),
                _stream_chunk(content="目录。"),
                _stream_chunk(tool_calls=[
                    _tool_delta("call_1", "inspect_dir", 0),
                    _tool_delta("call_2", "inspect_dir", 1),
                ]),
            ])

        tool_messages = [
            message
            for message in params["messages"]
            if message["role"] == "assistant"
        ]
        expected = [
            ("先查看目录。", "first reasoning", 2),
            ("", "second reasoning", 1),
        ][: request_number - 1]
        actual = [
            (
                message.get("content"),
                message.get("reasoning_content"),
                len(message.get("tool_calls") or []),
            )
            for message in tool_messages
        ]
        if actual != expected:
            raise RuntimeError(
                "400: The `reasoning_content` in the thinking mode must be "
                f"passed back to the API (expected {expected!r}, got {actual!r})."
            )

        if request_number == 2:
            return iter([
                _stream_chunk(reasoning_content="second reasoning"),
                _stream_chunk(tool_calls=[_tool_delta("call_3", "inspect_dir")]),
            ])
        return iter([_stream_chunk(content="完成。")])


class _StrictNonStreamingCompletions:
    def __init__(self):
        self.requests = []

    def create(self, **params):
        self.requests.append(params)
        request_number = len(self.requests)
        if request_number == 1:
            message = SimpleNamespace(
                role="assistant",
                content="先查看目录。",
                reasoning_content="non-stream reasoning",
                tool_calls=[SimpleNamespace(
                    id="call_1",
                    function=SimpleNamespace(name="inspect_dir", arguments="{}"),
                )],
            )
            return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)

        assistants = [
            message
            for message in params["messages"]
            if message["role"] == "assistant"
        ]
        expected = [("先查看目录。", "non-stream reasoning", 1)]
        actual = [
            (
                message.get("content"),
                message.get("reasoning_content"),
                len(message.get("tool_calls") or []),
            )
            for message in assistants
        ]
        if actual != expected:
            raise RuntimeError(
                "400: The `reasoning_content` in the thinking mode must be "
                f"passed back to the API (expected {expected!r}, got {actual!r})."
            )
        message = SimpleNamespace(
            role="assistant",
            content="完成。",
            reasoning_content="final reasoning",
            tool_calls=[],
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)


class _StrictVisionCompletions:
    def __init__(self):
        self.requests = []

    def create(self, **params):
        self.requests.append(params)
        for message in params["messages"]:
            if message["role"] != "user" or not isinstance(message.get("content"), list):
                continue
            if any(part.get("type") == "image_url" for part in message["content"] if isinstance(part, dict)):
                break
        else:
            raise RuntimeError("user image was not sent to the vision model")

        for message in params["messages"]:
            if message["role"] == "assistant" and isinstance(message.get("content"), list):
                if any(part.get("type") == "image_url" for part in message["content"] if isinstance(part, dict)):
                    raise RuntimeError("400: Image in assistant message is not supported")
        return iter([_stream_chunk(role="assistant", content="完成。")])


class _DynamicToolCompletions:
    def __init__(self):
        self.requests = []

    def create(self, **params):
        self.requests.append(params)
        names = [tool["function"]["name"] for tool in params.get("tools", [])]
        if len(self.requests) == 1:
            if names != ["load_dynamic_tool"]:
                raise AssertionError(f"unexpected initial tools: {names!r}")
            return iter([
                _stream_chunk(tool_calls=[
                    _tool_delta("call_load", "load_dynamic_tool"),
                ]),
            ])
        if names != ["load_dynamic_tool", "dynamic_tool"]:
            raise AssertionError(f"reloaded tool missing from next request: {names!r}")
        return iter([_stream_chunk(content="已看到新工具。")])


def _inspect_dir():
    """Inspect the current directory."""
    return "README.md\nmain.py\nrun.py"


def _client(completions):
    client = object.__new__(llm.LLMClient)
    client.config = {
        "default_model": "deepseek/model",
        "vision_model": "vision/model",
        "providers": {
            "deepseek": {
                "models": {"model": {"function_calling": True}},
            },
            "vision": {
                "models": {"model": {"vision": True}},
            },
        },
    }
    client.clients = {
        "deepseek": SimpleNamespace(
            chat=SimpleNamespace(completions=completions),
        ),
    }
    return client


class ReasoningContentRoundTripTests(unittest.TestCase):
    def test_tool_provider_is_frozen_per_request_and_reloaded_next_round(self):
        completions = _DynamicToolCompletions()
        active = {}
        calls = []

        def dynamic_tool() -> str:
            """A dynamically loaded tool that must not run during loading."""
            calls.append("called")
            return "ran"

        def load_dynamic_tool() -> str:
            """Install the dynamic tool for the next model request."""
            active["dynamic_tool"] = dynamic_tool
            return "loaded"

        session = llm.Chat(model="deepseek/model", chat_client=_client(completions))
        session.set_messages([{"role": "user", "content": "加载工具"}])
        session.add_tool(load_dynamic_tool)
        session.set_tool_provider(lambda: active)

        responses = session.chat()

        self.assertEqual(len(completions.requests), 2)
        self.assertEqual(calls, [])
        self.assertEqual(responses[-1].content, "已看到新工具。")

    def test_vision_request_sends_images_only_in_user_messages(self):
        completions = _StrictVisionCompletions()
        client = _client(completions)
        client.config["providers"]["deepseek"]["models"]["model"]["vision"] = True
        messages = [
            {"role": "assistant", "content": [{
                "type": "image_url",
                "image_url": {"url": "https://example.invalid/bot.png"},
            }]},
            {"role": "user", "content": [{
                "type": "image_url",
                "image_url": {"url": "https://example.invalid/user.png"},
            }]},
        ]

        list(client.generate_response(messages, model="deepseek/model", do_process_image=True))

        assistant = completions.requests[0]["messages"][0]
        self.assertEqual(assistant, {
            "role": "assistant",
            "content": [{"type": "text", "text": "[图片(https://example.invalid/bot.png)]"}],
        })

    def test_image_preprocessing_preserves_null_assistant_content(self):
        client = _client(SimpleNamespace())
        message = {
            "role": "assistant",
            "content": None,
            "reasoning_content": "reasoning",
            "tool_calls": [],
        }

        self.assertEqual(client._describe_images([message], {}), [message])

    def test_streaming_tool_rounds_replay_reasoning_content(self):
        completions = _StrictStreamingCompletions()
        session = llm.Chat(
            model="deepseek/model",
            chat_client=_client(completions),
            do_process_image=True,
        )
        session.set_messages([
            {"role": "user", "content": "查看目录，然后多调用几次工具"},
        ])
        session.add_tool(_inspect_dir, "inspect_dir")

        responses = session.chat()

        self.assertEqual(len(completions.requests), 3)
        self.assertEqual(
            [message["role"] for message in session.messages],
            ["user", "assistant", "tool", "tool", "assistant", "tool", "assistant"],
        )
        self.assertEqual(
            [message["reasoning_content"] for message in session.messages if message.get("tool_calls")],
            ["first reasoning", "second reasoning"],
        )
        self.assertEqual(
            [len(message["tool_calls"]) for message in session.messages if message.get("tool_calls")],
            [2, 1],
        )
        self.assertEqual(
            [message["content"] for message in session.messages if message.get("tool_calls")],
            ["先查看目录。", ""],
        )
        self.assertEqual(
            [response.content for response in responses if response.role == "assistant" and response.content],
            ["先查看目录。", "完成。"],
        )
        self.assertEqual(session.messages[-1], {"role": "assistant", "content": "完成。"})
        self.assertEqual(responses[-1].content, "完成。")

    def test_non_streaming_tool_round_replays_reasoning_content(self):
        completions = _StrictNonStreamingCompletions()
        session = llm.Chat(model="deepseek/model", chat_client=_client(completions))
        session.set_messages([{"role": "user", "content": "查看目录"}])
        session.add_tool(_inspect_dir, "inspect_dir")

        responses = session.chat(stream=False)

        self.assertEqual(len(completions.requests), 2)
        self.assertEqual(
            [message["role"] for message in session.messages],
            ["user", "assistant", "tool", "assistant"],
        )
        self.assertEqual(
            next(message for message in session.messages if message.get("tool_calls"))["reasoning_content"],
            "non-stream reasoning",
        )
        self.assertEqual(
            next(message for message in session.messages if message.get("tool_calls"))["content"],
            "先查看目录。",
        )
        self.assertEqual(session.messages[-1], {
            "role": "assistant",
            "content": "完成。",
            "reasoning_content": "final reasoning",
        })
        self.assertEqual(responses[-1].content, "完成。")

    def test_openai_sdk_preserves_reasoning_content_on_the_wire(self):
        requests = []

        def respond(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={
                "id": "response_1",
                "object": "chat.completion",
                "created": 0,
                "model": "model",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "完成。"},
                    "finish_reason": "stop",
                }],
            })

        http_client = httpx.Client(transport=httpx.MockTransport(respond))
        try:
            client = llm.OpenAI(
                api_key="test-key",
                base_url="http://mock.invalid/v1",
                http_client=http_client,
            )
            client.chat.completions.create(
                model="model",
                messages=[{
                    "role": "assistant",
                    "content": "先查看目录。",
                    "reasoning_content": "reasoning",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "inspect_dir", "arguments": "{}"},
                    }],
                }],
            )
        finally:
            http_client.close()

        self.assertEqual(requests[0]["messages"][0], {
            "role": "assistant",
            "content": "先查看目录。",
            "reasoning_content": "reasoning",
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "inspect_dir", "arguments": "{}"},
            }],
        })


if __name__ == "__main__":
    unittest.main()
