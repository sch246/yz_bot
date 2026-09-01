from __future__ import annotations

import importlib.util
import inspect
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
DISABLED = ROOT / "mods" / "tools" / "disable"


def _load_tools_module():
    for name in tuple(sys.modules):
        if name == "mods" or name.startswith("mods.llm") or name.startswith("mods.tools"):
            sys.modules.pop(name, None)

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

    package_dir = ROOT / "mods" / "tools"
    spec = importlib.util.spec_from_file_location(
        "mods.tools",
        package_dir / "__init__.py",
        submodule_search_locations=[str(package_dir)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules["mods.tools"] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module, mods


class DisabledToolModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tools, cls.mods = _load_tools_module()
        cls.registry = cls.tools.ToolRegistry(DISABLED)

    def test_top_level_registry_does_not_recurse_into_disable(self) -> None:
        registry = self.tools.ToolRegistry(ROOT / "mods" / "tools")

        sources = registry._source_paths()

        self.assertNotIn("disable", sources)
        self.assertTrue(all(path.parent == ROOT / "mods" / "tools" for paths in sources.values() for path in paths))
        self.assertFalse(set(sources) & set(self.registry.modules))

    def test_every_disabled_python_module_has_valid_namespaced_schemas(self) -> None:
        expected = {
            "README": set(),
            "baidu": {"baidu_encyclopedia"},
            "calendar_fortune": {"lunar_date", "xiaoliu"},
            "context_data": {"read_data"},
            "group_info": {"group_size", "group_members"},
            "image_url": {"url2cq"},
            "later_manage": {"later_list", "later_set"},
            "send_message": {"sendmsg"},
        }

        self.assertEqual(self.registry.failures, {})
        self.assertEqual(set(self.registry.modules), set(expected))
        for module_name, exports in expected.items():
            module = self.registry.modules[module_name]
            self.assertTrue(module.description)
            self.assertTrue(module.content.strip())
            self.assertEqual(
                set(module.tools),
                {f"{module_name}__{name}" for name in exports},
            )
            for tool in module.tools.values():
                schema = tool.description["function"]
                self.assertTrue(schema["description"])
                self.assertEqual(
                    set(schema["parameters"]["properties"]),
                    set(inspect.signature(tool.call).parameters),
                )

    def test_unrecoverable_names_are_documented_without_stub_modules(self) -> None:
        readme = (DISABLED / "README.md").read_text(encoding="utf-8")

        for name in ("rag_search", "get_location", "muti_reply", "read_image"):
            self.assertIn(name, readme)
            self.assertFalse((DISABLED / f"{name}.py").exists())

    def test_read_group_calendar_and_url_wrappers_reuse_current_authorities(self) -> None:
        storage = SimpleNamespace(get=lambda namespace, key: {"answer": 42})
        identity = SimpleNamespace(
            memberlist=lambda: [
                {"user_id": 7, "title": "captain", "sex": "unknown"},
                {"user_id": 8, "title": "", "sex": "female"},
            ],
            getname=lambda user_id: {7: "A", 8: "B"}[user_id],
        )
        lunar = SimpleNamespace(lunar_time=lambda: "lunar-date", 小六壬=lambda: "大安")
        cq = SimpleNamespace(url2cq=lambda url: f"cq:{url}")
        with mock.patch.object(self.mods, "storage", storage, create=True), mock.patch.object(
            self.mods, "identity", identity, create=True
        ), mock.patch.object(self.mods, "lunar", lunar, create=True), mock.patch.object(
            self.mods, "cq", cq, create=True
        ):
            self.assertEqual(
                self.registry.modules["context_data"].tools["context_data__read_data"].call("answer"),
                42,
            )
            self.assertEqual(
                self.registry.modules["context_data"].tools["context_data__read_data"].call("missing"),
                "没有找到内容",
            )
            self.assertEqual(
                self.registry.modules["group_info"].tools["group_info__group_size"].call(),
                "2",
            )
            self.assertEqual(
                self.registry.modules["group_info"].tools["group_info__group_members"].call(),
                'A(7) 名片:"captain" sex:unknown\nB(8) 名片:"" sex:female',
            )
            self.assertEqual(
                self.registry.modules["calendar_fortune"].tools["calendar_fortune__lunar_date"].call(),
                "lunar-date",
            )
            self.assertEqual(
                self.registry.modules["calendar_fortune"].tools["calendar_fortune__xiaoliu"].call(),
                "大安",
            )
            self.assertEqual(
                self.registry.modules["image_url"].tools["image_url__url2cq"].call("https://image"),
                "cq:https://image",
            )

    def test_send_and_later_wrappers_preserve_effect_and_command_shapes(self) -> None:
        sends = []
        runs = []
        message = SimpleNamespace(
            sendmsg=lambda text, **target: sends.append((text, target))
        )
        identity = SimpleNamespace(bot_id=lambda: 99)
        later = SimpleNamespace(
            run=lambda command, exec_id=None: runs.append((command, exec_id)) or f"ran:{command}"
        )
        with mock.patch.object(self.mods, "message", message, create=True), mock.patch.object(
            self.mods, "identity", identity, create=True
        ), mock.patch.object(self.mods, "later", later, create=True):
            send = self.registry.modules["send_message"].tools["send_message__sendmsg"].call
            list_tasks = self.registry.modules["later_manage"].tools["later_manage__later_list"].call
            set_task = self.registry.modules["later_manage"].tools["later_manage__later_set"].call

            self.assertEqual(send("hello", group_id=5), "已发送")
            with self.assertRaisesRegex(ValueError, "不能同时提供"):
                send("invalid", user_id=1, group_id=2)
            self.assertEqual(list_tasks(), "ran:")
            self.assertEqual(set_task(3, "10m", "'ready'"), "ran: set 3 10m 'ready'")

        self.assertEqual(sends, [("hello", {"user_id": None, "group_id": 5})])
        self.assertEqual(runs, [("", 99), (" set 3 10m 'ready'", 99)])

    @mock.patch("requests.get")
    def test_baidu_wrapper_keeps_historical_response_contract(self, get) -> None:
        get.return_value.json.side_effect = [
            {"code": 200, "data": {"text": "entry"}},
            {"code": 404, "data": {}},
        ]
        call = self.registry.modules["baidu"].tools["baidu__baidu_encyclopedia"].call

        self.assertEqual(call("测试 对象"), "entry")
        self.assertEqual(call("missing"), "查询失败")

        self.assertEqual(
            get.call_args_list,
            [
                mock.call(
                    "https://api.wer.plus/api/dub?t=%E6%B5%8B%E8%AF%95%20%E5%AF%B9%E8%B1%A1",
                    timeout=15,
                ),
                mock.call("https://api.wer.plus/api/dub?t=missing", timeout=15),
            ],
        )


if __name__ == "__main__":
    unittest.main()
