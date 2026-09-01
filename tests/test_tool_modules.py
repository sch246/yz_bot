from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def _load_tools_module():
    for name in tuple(sys.modules):
        if name == "mods" or name.startswith("mods.llm") or name.startswith("mods.tools"):
            sys.modules.pop(name, None)

    mods = ModuleType("mods")
    mods.__path__ = [str(ROOT / "mods")]
    mods.marker = 7
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


class FakeChat:
    def __init__(self) -> None:
        self.functions = {}


class ToolModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tools, cls.mods = _load_tools_module()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def write(self, name: str, content: str) -> Path:
        path = self.root / name
        path.write_text(content, encoding="utf-8")
        return path

    def registry(self):
        return self.tools.ToolRegistry(self.root)

    def test_python_imports_explicit_exports_and_namespacing(self) -> None:
        self.write("_helper.py", "VALUE = 5\n")
        self.write(
            "alpha.py",
            '''"""计算组合值
只在激活后出现的完整说明。
"""
import math
from mods import marker
from ._helper import VALUE

def calculate(value: int) -> int:
    """组合输入值。"""
    return math.floor(value) + marker + VALUE

def hidden(value: int) -> int:
    """不导出的函数。"""
    return value

__all__ = ["calculate"]
''',
        )
        registry = self.registry()

        module = registry.modules["alpha"]

        self.assertEqual(module.description, "计算组合值")
        self.assertEqual(module.content, "只在激活后出现的完整说明。\n")
        self.assertEqual(set(module.tools), {"alpha__calculate"})
        self.assertEqual(module.tools["alpha__calculate"].call(3), 15)
        self.assertEqual(
            module.tools["alpha__calculate"].description["function"]["name"],
            "alpha__calculate",
        )

    def test_python_requires_all_but_all_may_be_empty(self) -> None:
        self.write("empty.py", '"""仅提供说明"""\n__all__ = []\n')
        self.write(
            "implicit.py",
            '"""缺少导出声明"""\n\n'
            'def implicit(value: str) -> str:\n'
            '    """返回输入。"""\n'
            '    return value\n',
        )
        registry = self.registry()

        self.assertEqual(dict(registry.modules["empty"].tools), {})
        self.assertNotIn("implicit", registry.modules)
        self.assertIn("Traceback (most recent call last)", registry.failures["implicit"])
        self.assertIn("must define __all__ explicitly", registry.failures["implicit"])

    def test_markdown_uses_first_line_and_preserves_all_remaining_content(self) -> None:
        self.write("guide.md", "简短描述\n第一段\n\n第二段\n")

        module = self.registry().modules["guide"]

        self.assertEqual(module.description, "简短描述")
        self.assertEqual(module.content, "第一段\n\n第二段\n")
        self.assertEqual(dict(module.tools), {})

    def test_first_use_loads_last_good_and_isolates_failures_and_conflicts(self) -> None:
        self.write("good.md", "可用描述\n正文")
        self.write("broken.py", '"""损坏模块"""\nraise RuntimeError("boom")\n__all__ = []\n')
        self.write("duo.py", '"""Python 版本"""\n__all__ = []\n')
        self.write("duo.md", "Markdown 版本")
        registry = self.registry()

        self.assertEqual(set(registry.modules), {"good"})
        self.assertEqual(set(registry.failures), {"broken", "duo"})
        self.assertIn("RuntimeError: boom", registry.failures["broken"])
        self.assertIn("conflicting sources", registry.failures["duo"])

    def test_scan_reports_changes_without_applying_them(self) -> None:
        path = self.write("alpha.md", "旧描述\n旧正文")
        registry = self.registry()
        old = registry.modules["alpha"]
        path.write_text("新描述\n新正文", encoding="utf-8")
        self.write("added.md", "新增描述")

        changes = registry.scan()

        self.assertEqual(changes, {"added": ["added"], "modified": ["alpha"], "deleted": []})
        self.assertIs(registry.get("alpha"), old)
        self.assertEqual(registry.get("alpha").description, "旧描述")

    def test_failed_reload_keeps_previous_last_good(self) -> None:
        path = self.write("alpha.md", "旧描述\n旧正文")
        registry = self.registry()
        previous = registry.modules["alpha"]
        path.write_text("", encoding="utf-8")

        result = registry.reload(["alpha"])["alpha"]

        self.assertFalse(result.ok)
        self.assertIs(registry.get("alpha"), previous)
        self.assertIn("Traceback (most recent call last)", result.error)
        self.assertIn("non-empty first-line description", result.error)

    def test_multi_module_reload_commits_success_and_failure_independently(self) -> None:
        good = self.write("good.md", "旧好描述\n旧好正文")
        bad = self.write("bad.md", "旧坏描述\n旧坏正文")
        registry = self.registry()
        bad_previous = registry.modules["bad"]
        good.write_text("新好描述\n新好正文", encoding="utf-8")
        bad.write_text("", encoding="utf-8")

        results = registry.reload(["bad", "good"])

        self.assertFalse(results["bad"].ok)
        self.assertTrue(results["good"].ok)
        self.assertIs(registry.get("bad"), bad_previous)
        self.assertEqual(registry.get("good").description, "新好描述")

    def test_load_uses_only_last_good_and_context_is_rebuilt_without_duplicates(self) -> None:
        path = self.write("guide.md", "指南描述\n只应出现一次的正文")
        registry = self.registry()
        context = self.tools.create_context_message(registry=registry)
        session = FakeChat()
        binding = self.tools.bind_session(session, context, registry=registry)
        path.write_text("磁盘新描述\n不应被 load 读到", encoding="utf-8")

        first = binding.load(["guide"])["guide"]
        second = binding.load(["guide"])["guide"]

        self.assertTrue(first.ok)
        self.assertEqual(second.action, "replaced")
        self.assertIn("指南描述", context["content"])
        self.assertNotIn("磁盘新描述", context["content"])
        self.assertNotIn("不应被 load 读到", context["content"])
        self.assertEqual(context["content"].count("只应出现一次的正文"), 1)

    def test_module_activation_conflict_is_atomic(self) -> None:
        self.write(
            "alpha.py",
            '''"""两个函数"""
def first(value: str) -> str:
    """第一个函数。"""
    return value
def second(value: str) -> str:
    """第二个函数。"""
    return value
__all__ = ["first", "second"]
''',
        )
        registry = self.registry()
        context = self.tools.create_context_message(registry=registry)
        session = FakeChat()
        binding = self.tools.bind_session(session, context, registry=registry)
        sentinel = object()
        session.functions["alpha__second"] = sentinel

        result = binding.load(["alpha"])["alpha"]

        self.assertFalse(result.ok)
        self.assertNotIn("alpha__first", session.functions)
        self.assertIs(session.functions["alpha__second"], sentinel)
        self.assertNotIn("alpha", binding.active)

    def test_explicit_reload_of_deleted_active_module_removes_both_projections(self) -> None:
        path = self.write(
            "alpha.py",
            '''"""可删除模块
删除前正文
"""
def run(value: str) -> str:
    """返回输入。"""
    return value
__all__ = ["run"]
''',
        )
        registry = self.registry()
        context = self.tools.create_context_message(registry=registry)
        session = FakeChat()
        binding = self.tools.bind_session(
            session, context, ["alpha"], registry=registry
        )
        path.unlink()

        result = binding.reload(["alpha"])["alpha"]

        self.assertTrue(result.ok)
        self.assertEqual(result.action, "deleted")
        self.assertNotIn("alpha", registry.modules)
        self.assertNotIn("alpha", binding.active)
        self.assertNotIn("alpha__run", session.functions)
        self.assertNotIn("可删除模块", context["content"])
        self.assertNotIn("删除前正文", context["content"])

    def test_base_closures_are_registered_and_exec_code_uses_py_loc(self) -> None:
        registry = self.registry()
        context = self.tools.create_context_message(registry=registry)
        session = FakeChat()
        self.tools.bind_session(session, context, registry=registry)
        fake_context = SimpleNamespace(current=lambda: {"user_id": 1})
        fake_op = SimpleNamespace(require_op=lambda _event: True)
        fake_py = SimpleNamespace(loc={"value": 4})
        original_values = {
            name: getattr(self.mods, name, None)
            for name in ("context", "op", "py")
        }
        self.mods.context, self.mods.op, self.mods.py = fake_context, fake_op, fake_py
        try:
            result = session.functions["exec_code"].call(
                "value + extra", "extra = 3\nprint('hello')"
            )
            fake_op.require_op = lambda _event: False
            denied = session.functions["exec_code"].call("1")
        finally:
            for name, value in original_values.items():
                if value is None:
                    self.mods.__dict__.pop(name, None)
                else:
                    setattr(self.mods, name, value)

        self.assertEqual(
            set(session.functions),
            {"exec_code", "list_tools", "reload_tools", "load_tools"},
        )
        self.assertEqual(result, "[print输出]\nhello\n[结果] 7")
        self.assertEqual(denied, "权限不足")
        self.assertNotIn("print", fake_py.loc)

    def test_active_reload_replaces_content_and_tools_for_next_snapshot(self) -> None:
        path = self.write(
            "alpha.py",
            '''"""旧描述
旧正文
"""
def run() -> int:
    """返回版本。"""
    return 1
__all__ = ["run"]
''',
        )
        registry = self.registry()
        context = self.tools.create_context_message(registry=registry)
        session = FakeChat()
        binding = self.tools.bind_session(
            session, context, ["alpha"], registry=registry
        )
        old_tool = session.functions["alpha__run"]
        path.write_text(
            '''"""新描述
新正文
"""
def run() -> int:
    """返回版本。"""
    return 2
__all__ = ["run"]
''',
            encoding="utf-8",
        )

        text = session.functions["reload_tools"].call(["alpha"])

        self.assertIn("已重载", text)
        self.assertIsNot(session.functions["alpha__run"], old_tool)
        self.assertEqual(session.functions["alpha__run"].call(), 2)
        self.assertIn("新描述", context["content"])
        self.assertIn("新正文", context["content"])
        self.assertNotIn("旧描述", context["content"])
        self.assertNotIn("旧正文", context["content"])

    def test_active_reload_conflict_keeps_registry_and_session_on_old_group(self) -> None:
        path = self.write(
            "alpha.py",
            '''"""旧描述"""
def first() -> int:
    """返回旧版本。"""
    return 1
__all__ = ["first"]
''',
        )
        registry = self.registry()
        context = self.tools.create_context_message(registry=registry)
        session = FakeChat()
        binding = self.tools.bind_session(
            session, context, ["alpha"], registry=registry
        )
        previous_module = registry.get("alpha")
        previous_tool = session.functions["alpha__first"]
        sentinel = object()
        session.functions["alpha__second"] = sentinel
        path.write_text(
            '''"""新描述"""
def first() -> int:
    """返回新版本。"""
    return 2
def second() -> int:
    """返回第二个值。"""
    return 3
__all__ = ["first", "second"]
''',
            encoding="utf-8",
        )

        result = binding.reload(["alpha"])["alpha"]

        self.assertFalse(result.ok)
        self.assertIs(registry.get("alpha"), previous_module)
        self.assertIs(session.functions["alpha__first"], previous_tool)
        self.assertIs(session.functions["alpha__second"], sentinel)
        self.assertEqual(session.functions["alpha__first"].call(), 1)
        self.assertIn("旧描述", context["content"])
        self.assertNotIn("新描述", context["content"])

    def test_load_tools_closure_does_not_apply_changed_source(self) -> None:
        path = self.write(
            "alpha.py",
            '''"""内存版本"""
def run() -> int:
    """返回版本。"""
    return 1
__all__ = ["run"]
''',
        )
        registry = self.registry()
        context = self.tools.create_context_message(registry=registry)
        session = FakeChat()
        self.tools.bind_session(session, context, registry=registry)
        path.write_text('raise RuntimeError("must not execute")\n', encoding="utf-8")

        text = session.functions["load_tools"].call(["alpha"])

        self.assertIn("已激活", text)
        self.assertEqual(session.functions["alpha__run"].call(), 1)

    def test_version_control_wrappers_only_reexport_existing_functions(self) -> None:
        chat = ModuleType("mods.chat")
        weather = ModuleType("mods.weather")

        def make_function(name):
            def function(value: str = "") -> str:
                """测试替身函数。"""
                return f"{name}:{value}"

            function.__name__ = name
            return function

        chat_names = {
            "get_time", "poke", "recognize_image", "create_image",
            "create_image_from_references", "later_add", "later_del",
            "get_user_data", "set_user_data", "assign_tasks",
            "search_mc_mod", "check_mod",
        }
        weather_names = {
            "search_city", "get_realtime_weather", "get_daily_forecast",
            "get_hourly_forecast",
        }
        for name in chat_names:
            setattr(chat, name, make_function(name))
        for name in weather_names:
            setattr(weather, name, make_function(name))

        with mock.patch.dict(
            sys.modules,
            {"mods.chat": chat, "mods.weather": weather},
        ):
            registry = self.tools.ToolRegistry(ROOT / "mods" / "tools")
            modules = registry.modules

        self.assertEqual(
            set(modules),
            {"agents", "common", "image", "later", "minecraft", "user_data", "weather"},
        )
        self.assertTrue(all(module.description for module in modules.values()))
        self.assertIs(modules["common"].tools["common__get_time"].call, chat.get_time)
        self.assertIs(
            modules["weather"].tools["weather__search_city"].call,
            weather.search_city,
        )


if __name__ == "__main__":
    unittest.main()
