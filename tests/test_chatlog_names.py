from pathlib import Path
from types import ModuleType
import importlib.util
import sys
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]


def _load_chatlog_module():
    """Load chatlog with inert dependencies instead of booting the Bot."""
    mods = ModuleType("mods")
    mods.INFRA = object()
    dependencies = {
        name: ModuleType(f"mods.{name}")
        for name in ("cq", "history", "identity")
    }
    for name, module in dependencies.items():
        setattr(mods, name, module)

    module_name = "chatlog_under_test"
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "mods" / "chatlog.py")
    module = importlib.util.module_from_spec(spec)
    injected = {
        "mods": mods,
        **{f"mods.{name}": dependency for name, dependency in dependencies.items()},
        module_name: module,
    }
    with patch.dict(sys.modules, injected):
        assert spec.loader is not None
        spec.loader.exec_module(module)
    return module, dependencies["identity"]


class PokeNameTests(unittest.TestCase):
    def test_poke_uses_the_same_display_name_resolver_as_chat(self):
        chatlog, identity = _load_chatlog_module()
        identity.getname = Mock(side_effect={101: "自定义发送者", 202: "自定义目标"}.__getitem__)

        self.assertEqual(
            chatlog.format_poke({"user_id": 101, "target_id": 202}),
            "自定义发送者戳了戳自定义目标",
        )
        self.assertEqual(
            chatlog.format_poke({"user_id": 101, "target_id": 202, "group_id": 303}),
            "自定义发送者(101)戳了戳自定义目标(202)",
        )
        self.assertEqual(identity.getname.call_args_list, [
            unittest.mock.call(101),
            unittest.mock.call(202),
            unittest.mock.call(101),
            unittest.mock.call(202),
        ])


if __name__ == "__main__":
    unittest.main()
