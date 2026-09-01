from __future__ import annotations

import ast
from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "mods" / "tools"
MIGRATED = {
    "assign_tasks",
    "check_mod",
    "create_image",
    "create_image_from_references",
    "get_time",
    "get_user_data",
    "later_add",
    "later_del",
    "poke",
    "recognize_image",
    "search_mc_mod",
    "set_user_data",
}


@contextmanager
def load_tool(name: str, **dependencies):
    mods = ModuleType("mods")
    for dependency, value in dependencies.items():
        setattr(mods, dependency, value)
    module_name = f"_enabled_tool_test_{name}_{id(mods):x}"
    spec = importlib.util.spec_from_file_location(module_name, TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    with mock.patch.dict(sys.modules, {"mods": mods, module_name: module}):
        spec.loader.exec_module(module)
        yield module


@contextmanager
def load_registry():
    selected = {
        name: module
        for name, module in sys.modules.items()
        if name == "mods" or name.startswith("mods.llm") or name.startswith("mods.tools")
    }
    for name in selected:
        sys.modules.pop(name, None)
    try:
        mods = ModuleType("mods")
        mods.__path__ = [str(ROOT / "mods")]
        sys.modules["mods"] = mods

        llm = ModuleType("mods.llm")
        llm.__path__ = [str(ROOT / "mods" / "llm")]
        sys.modules["mods.llm"] = llm
        tool_spec = importlib.util.spec_from_file_location(
            "mods.llm.tools", ROOT / "mods" / "llm" / "tools.py"
        )
        tool_module = importlib.util.module_from_spec(tool_spec)
        sys.modules["mods.llm.tools"] = tool_module
        assert tool_spec.loader is not None
        tool_spec.loader.exec_module(tool_module)

        package_spec = importlib.util.spec_from_file_location(
            "mods.tools",
            TOOLS / "__init__.py",
            submodule_search_locations=[str(TOOLS)],
        )
        package = importlib.util.module_from_spec(package_spec)
        sys.modules["mods.tools"] = package
        assert package_spec.loader is not None
        package_spec.loader.exec_module(package)
        yield package, mods, llm
    finally:
        for name in tuple(sys.modules):
            if name == "mods" or name.startswith("mods.llm") or name.startswith("mods.tools"):
                sys.modules.pop(name, None)
        sys.modules.update(selected)


class EnabledToolModuleTests(unittest.TestCase):
    def test_registry_validates_all_enabled_modules_under_their_own_names(self) -> None:
        def weather_call(value: str) -> str:
            """Return the test city value."""
            return value

        weather = SimpleNamespace(
            search_city=weather_call,
            get_realtime_weather=weather_call,
            get_daily_forecast=weather_call,
            get_hourly_forecast=weather_call,
        )
        with load_registry() as (tools, mods, llm):
            mods.context = SimpleNamespace(current=lambda: {"user_id": 7})
            mods.connect = SimpleNamespace(call_api=lambda *_args, **_kwargs: {})
            mods.identity = SimpleNamespace(bot_id=lambda: 1, getstorage=lambda _id: {})
            mods.image = SimpleNamespace(resolve_image_uri=lambda _uri: None)
            mods.message = SimpleNamespace(sendmsg=lambda _value: None)
            mods.op = SimpleNamespace(require_op=lambda _event: True)
            mods.chat = SimpleNamespace(
                inc_usage_cost=lambda _price: None,
                inc_call_tokens_cost=lambda _model, _tokens: None,
            )
            mods.tools = tools
            mods.llm = llm
            mods.get_available = lambda name: weather if name == "weather" else None
            llm.Chat = object
            llm.get_client = lambda: None

            registry = tools.ToolRegistry(TOOLS)
            modules = registry.modules

        self.assertEqual(
            set(modules),
            {
                "agents", "common", "image", "later", "meta",
                "minecraft", "user_data", "weather",
            },
        )
        self.assertEqual(
            set(modules["image"].tools),
            {
                "image__recognize_image",
                "image__create_image",
                "image__create_image_from_references",
            },
        )
        self.assertEqual(
            modules["weather"].tools["weather__search_city"].call("city"), "city"
        )

    def test_chat_retires_migrated_definitions_and_tool_modules_own_them(self) -> None:
        chat_tree = ast.parse((ROOT / "mods" / "chat.py").read_text(encoding="utf-8"))
        chat_functions = {
            node.name for node in chat_tree.body if isinstance(node, ast.FunctionDef)
        }

        self.assertTrue(MIGRATED.isdisjoint(chat_functions))
        owned = set()
        for path in (
            TOOLS / "agents.py",
            TOOLS / "common.py",
            TOOLS / "image.py",
            TOOLS / "later.py",
            TOOLS / "minecraft.py",
            TOOLS / "user_data.py",
        ):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            owned.update(
                node.name for node in tree.body if isinstance(node, ast.FunctionDef)
            )
        self.assertTrue(MIGRATED <= owned)

    def test_common_poke_preserves_window_boundary_and_api_shape(self) -> None:
        event = {"user_id": 7}
        calls = []
        context = SimpleNamespace(current=lambda: event)
        connect = SimpleNamespace(
            call_api=lambda action, **params: calls.append((action, params))
            or {"retcode": 0}
        )
        with load_tool("common", context=context, connect=connect) as common:
            self.assertRegex(
                common.get_time(),
                r"^现在是\d{4}年\d{2}月\d{2}日\d{2}时\d{2}分\d{2}秒$",
            )
            self.assertEqual(
                common.poke(8), "戳一戳失败：私聊中只能戳当前对话者"
            )
            event.update({"group_id": 9})
            self.assertEqual(common.poke(8), "已戳用户 8")

        self.assertEqual(calls, [("send_poke", {"user_id": 8, "group_id": 9})])

    def test_later_preserves_bot_executor_identity_and_command_text(self) -> None:
        calls = []
        later = SimpleNamespace(
            run=lambda body, **kwargs: calls.append((body, kwargs)) or "done"
        )
        identity = SimpleNamespace(bot_id=lambda: 42)
        with load_tool("later", later=later, identity=identity) as tool:
            self.assertEqual(tool.later_add("1h", "x = 1", "x"), "done")
            self.assertEqual(tool.later_del("1,2"), "done")

        self.assertEqual(
            calls,
            [
                (" add 1h x = 1\nx", {"exec_id": 42}),
                (" del 1,2", {"exec_id": 42}),
            ],
        )

    def test_user_data_preserves_owner_permission_literal_and_delete(self) -> None:
        current = {"user_id": 7}
        data = {7: {}, 8: {"old": 1}}
        context = SimpleNamespace(current=lambda: current)
        identity = SimpleNamespace(getstorage=lambda user_id: data.setdefault(user_id, {}))
        op = SimpleNamespace(require_op=lambda _event: False)
        with load_tool(
            "user_data", context=context, identity=identity, op=op
        ) as tool:
            self.assertEqual(tool.set_user_data(8, "x", "1"), "权限不足")
            self.assertEqual(tool.set_user_data(7, "x", "{'nested': True}"), "done")
            self.assertEqual(data[7]["x"], {"nested": True})
            op.require_op = lambda _event: True
            self.assertEqual(tool.set_user_data(8, "old", "del"), "done")
            self.assertNotIn("old", data[8])
            self.assertEqual(tool.get_user_data(7), str(data[7]))

    def test_image_generation_preserves_validation_timeout_send_and_cost(self) -> None:
        sent = []
        costs = []
        response = SimpleNamespace(
            ok=True,
            status_code=200,
            json=lambda: {"data": [{"b64_json": "one"}, {"b64_json": "two"}]},
        )
        context = SimpleNamespace(current=lambda: {"user_id": 7})
        cq = SimpleNamespace(base64_to_cq=lambda value: f"cq:{value}")
        image_mod = SimpleNamespace(resolve_image_uri=lambda _uri: None)
        llm = SimpleNamespace(get_client=lambda: None)
        message = SimpleNamespace(sendmsg=sent.append)
        op = SimpleNamespace(require_op=lambda _event: False)
        chat = SimpleNamespace(inc_usage_cost=costs.append)
        with load_tool(
            "image",
            context=context,
            cq=cq,
            image=image_mod,
            llm=llm,
            message=message,
            op=op,
            chat=chat,
        ) as tool:
            self.assertEqual(
                tool.create_image("", n=1), "生图失败：prompt 不能为空"
            )
            self.assertEqual(
                tool.recognize_image("file:///tmp/x"),
                "图片识别失败：本地文件需要管理员权限",
            )
            with mock.patch.dict(
                os.environ,
                {"BYTECAT_BASE_URL": "https://image.example/", "BYTECAT_IMAGE_API_KEY": "key"},
            ), mock.patch.object(tool.requests, "post", return_value=response) as post:
                self.assertEqual(tool.create_image(" draw ", n=2), "已生成并发送 2 张图片")

        self.assertEqual(sent, ["cq:one", "cq:two"])
        self.assertEqual(costs, [0.26])
        self.assertEqual(post.call_args.kwargs["timeout"], (10, 300))
        self.assertEqual(post.call_args.kwargs["json"]["prompt"], "draw")

    def test_reference_images_are_deduplicated_and_use_edit_timeout(self) -> None:
        resolved = []
        sent = []
        costs = []
        response = SimpleNamespace(
            ok=True,
            status_code=200,
            json=lambda: {"data": [{"b64_json": "result"}]},
        )
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "reference.png"
            path.write_bytes(b"image")
            image_mod = SimpleNamespace(
                resolve_image_uri=lambda uri: resolved.append(uri)
                or (str(path), "image/png")
            )
            with load_tool(
                "image",
                context=SimpleNamespace(current=lambda: {"user_id": 7}),
                cq=SimpleNamespace(base64_to_cq=lambda value: value),
                image=image_mod,
                llm=SimpleNamespace(get_client=lambda: None),
                message=SimpleNamespace(sendmsg=sent.append),
                op=SimpleNamespace(require_op=lambda _event: False),
                chat=SimpleNamespace(inc_usage_cost=costs.append),
            ) as tool, mock.patch.dict(
                os.environ,
                {"BYTECAT_BASE_URL": "https://image.example", "BYTECAT_IMAGE_API_KEY": "key"},
            ), mock.patch.object(tool.requests, "post", return_value=response) as post:
                result = tool.create_image_from_references(
                    "new", "https://a/x\n https://a/x \nhttps://b/y"
                )

        self.assertEqual(result, "已生成并发送 1 张图片")
        self.assertEqual(resolved, ["https://a/x", "https://b/y"])
        self.assertEqual(post.call_args.kwargs["timeout"], (10, 300))
        self.assertEqual(len(post.call_args.kwargs["files"]), 2)
        self.assertEqual(sent, ["result"])
        self.assertEqual(costs, [0.13])

    def test_minecraft_preserves_endpoints_timeout_and_search_limit(self) -> None:
        calls = []

        class Parsed:
            def __init__(self, text, _parser):
                self.text = text

            def find_all(self, class_):
                matches = re.findall(
                    rf'<div class="{re.escape(class_)}">(.*?)</div>', self.text
                )
                return [SimpleNamespace(get_text=lambda value=value: value) for value in matches]

        bs4 = ModuleType("bs4")
        bs4.BeautifulSoup = Parsed

        def get(url, timeout):
            calls.append((url, timeout))
            if "search.mcmod.cn" in url:
                return SimpleNamespace(
                    text=f'<div class="search-result-list">{"x" * 1100}</div>'
                )
            return SimpleNamespace(text='<div class="text-area">detail</div>')

        with load_tool("minecraft") as tool, mock.patch.object(
            tool.requests, "get", side_effect=get
        ), mock.patch.dict(sys.modules, {"bs4": bs4}):
            search = tool.search_mc_mod("abc")
            detail = tool.check_mod(12)

        self.assertEqual(len(search), 1003)
        self.assertTrue(search.endswith("..."))
        self.assertEqual(detail, "detail")
        self.assertEqual(
            calls,
            [
                ("https://search.mcmod.cn/s?key=abc", 15),
                ("https://www.mcmod.cn/class/12.html", 15),
            ],
        )

    def test_assign_tasks_preserves_order_context_modules_cost_and_worker_cap(self) -> None:
        origin = {"user_id": 7, "group_id": 9}
        current = {"value": origin}
        context_calls = []
        costs = []
        bindings = []
        workers = []

        context = SimpleNamespace(
            current=lambda: current["value"],
            set_current=lambda event: (
                context_calls.append(("set", event)), current.__setitem__("value", event)
            )[-1],
            clear_current=lambda: (
                context_calls.append(("clear", None)), current.__setitem__("value", None)
            )[-1],
        )

        class FakeChat:
            def __init__(self, model, chat_client):
                self.model = model
                self.chat_client = chat_client
                self.messages = []

            def set_messages(self, messages):
                self.messages = messages

            def chat(self, recall_func):
                task = self.messages[1].splitlines()[-1]
                recall_func(
                    SimpleNamespace(
                        role="assistant",
                        content=f"done:{task}",
                        total_tokens=3,
                        prompt_tokens=1,
                        completion_tokens=2,
                    )
                )

        class FakeExecutor:
            def __init__(self, max_workers):
                workers.append(max_workers)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def map(self, function, values):
                return map(function, values)

        tool_modules = SimpleNamespace(
            create_context_message=lambda: {"role": "system", "content": "tools"},
            bind_session=lambda session, tool_context, requested: bindings.append(
                (session, tool_context, requested)
            ),
        )
        llm = SimpleNamespace(Chat=FakeChat, get_client=lambda: "client")
        chat = SimpleNamespace(
            inc_call_tokens_cost=lambda model, tokens: costs.append((model, tokens))
        )
        with load_tool(
            "agents",
            context=context,
            llm=llm,
            tools=tool_modules,
            chat=chat,
        ) as tool, mock.patch.object(tool, "ThreadPoolExecutor", FakeExecutor):
            result = tool.assign_tasks(
                "shared", " first \n\nsecond", " common\ncommon\nweather ", max_workers=99
            )
            empty = tool.assign_tasks("shared", "", "", max_workers=0)

        self.assertEqual(
            result,
            repr([("first", "done:first"), ("second", "done:second")]),
        )
        self.assertEqual(empty, "[]")
        self.assertEqual(workers, [5, 1])
        self.assertEqual([entry[2] for entry in bindings], [["common", "weather"]] * 2)
        self.assertEqual(costs, [("deepseek/deepseek-v4-flash", (1, 2))] * 2)
        self.assertEqual(context_calls, [("set", origin), ("clear", None)] * 2)
        self.assertIsNone(current["value"])


if __name__ == "__main__":
    unittest.main()
