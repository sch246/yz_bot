from pathlib import Path
from types import ModuleType
import importlib
import sys
import tempfile
import threading
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _load_self_tools_module():
    """Load mods.self_tools without booting the production mods package."""
    for name in ("mods.self_tools", "mods.llm.tools", "mods.llm", "mods"):
        sys.modules.pop(name, None)
    mods = ModuleType("mods")
    mods.__path__ = [str(ROOT / "mods")]
    sys.modules["mods"] = mods
    return importlib.import_module("mods.self_tools")


self_tools = _load_self_tools_module()


VALID_V1 = '''\
def greet(name: str) -> str:
    """Greet a person.

    Args:
        name: Person to greet.
    """
    return f"hello {name}"
'''


class SelfToolLoaderTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.environments = []

        def environment_factory():
            environment = {"shared_value": len(self.environments)}
            self.environments.append(environment)
            return environment

        self.loader = self_tools.SelfToolLoader(
            self.root,
            environment_factory=environment_factory,
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def write(self, name, source):
        (self.root / f"{name}.py").write_text(source, encoding="utf-8")

    def test_initial_load_builds_schema_without_calling_export(self):
        self.write("greet", VALID_V1.replace(
            'return f"hello {name}"',
            'raise RuntimeError("called during load")',
        ))

        result = self.loader.load("greet")["greet"]

        self.assertTrue(result.ok)
        function = self.loader.list()["greet"]
        self.assertEqual(function.__name__, "greet")
        tool = self_tools.Tool(function)
        self.assertEqual(tool.description["function"]["parameters"], {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Person to greet."},
            },
            "required": ["name"],
        })

    def test_scan_detects_changes_without_activating_them(self):
        self.write("greet", VALID_V1)
        self.loader.load("greet")
        active = self.loader.list()["greet"]
        self.write("greet", VALID_V1.replace("hello", "hi"))
        self.write("later", VALID_V1.replace("greet", "later"))

        self.assertEqual(self.loader.scan(), {
            "added": ["later"],
            "modified": ["greet"],
            "deleted": [],
        })
        self.assertIs(self.loader.list()["greet"], active)
        self.assertEqual(active("Ada"), "hello Ada")

    def test_successful_update_atomically_replaces_active_function(self):
        self.write("greet", VALID_V1)
        self.loader.load("greet")
        old = self.loader.list()["greet"]
        self.write("greet", VALID_V1.replace("hello", "hi"))

        result = self.loader.load(["greet"])["greet"]

        self.assertTrue(result.ok)
        self.assertEqual(result.action, "loaded")
        self.assertIsNot(self.loader.list()["greet"], old)
        self.assertEqual(self.loader.list()["greet"]("Ada"), "hi Ada")
        self.assertEqual(self.loader.scan()["modified"], [])

    def test_candidate_failures_keep_old_version_and_full_traceback(self):
        invalid_sources = {
            "syntax": "def greet(:\n    pass\n",
            "top_level": 'raise RuntimeError("top level failed")\n',
            "tool_validation": '''\
def greet(name):
    return name
''',
        }
        for label, source in invalid_sources.items():
            with self.subTest(label=label):
                self.write("greet", VALID_V1)
                self.loader.load("greet")
                old = self.loader.list()["greet"]
                self.write("greet", source)

                result = self.loader.load("greet")["greet"]

                self.assertFalse(result.ok)
                self.assertEqual(result.action, "failed")
                self.assertIn("Traceback (most recent call last):", result.error)
                self.assertIs(self.loader.list()["greet"], old)
                self.assertEqual(self.loader.scan()["modified"], ["greet"])

    def test_one_failure_does_not_block_another_file(self):
        self.write("broken", 'raise RuntimeError("broken module")\n')
        self.write("greet", VALID_V1)

        results = self.loader.load(["broken", "greet"])

        self.assertFalse(results["broken"].ok)
        self.assertTrue(results["greet"].ok)
        self.assertEqual(list(self.loader.list()), ["greet"])

    def test_deleted_active_file_is_unloaded_but_missing_inactive_is_failure(self):
        self.write("greet", VALID_V1)
        self.loader.load("greet")
        (self.root / "greet.py").unlink()
        self.assertEqual(self.loader.scan()["deleted"], ["greet"])

        removed = self.loader.load("greet")["greet"]
        missing = self.loader.load("never_existed")["never_existed"]

        self.assertTrue(removed.ok)
        self.assertEqual(removed.action, "unloaded")
        self.assertEqual(self.loader.list(), {})
        self.assertFalse(missing.ok)
        self.assertIn("FileNotFoundError", missing.error)

    def test_reserved_name_is_rejected_without_executing_source(self):
        marker = self.root / "executed"
        self.write("builtin", f'''\
from pathlib import Path
Path({str(marker)!r}).write_text("ran")

def builtin(value: str) -> str:
    """Try to replace a built-in tool."""
    return value
''')

        result = self.loader.load("builtin", reserved_names={"builtin"})["builtin"]

        self.assertFalse(result.ok)
        self.assertIn("tool name is reserved", result.error)
        self.assertFalse(marker.exists())
        self.assertEqual(self.loader.list(), {})

    def test_candidate_globals_are_isolated(self):
        self.write("first", '''\
private_state = "first"
def first(value: str) -> str:
    """Return this file's state."""
    return private_state
''')
        self.write("second", '''\
private_state = "second"
def second(value: str) -> str:
    """Return this file's state."""
    return private_state
''')

        results = self.loader.load(["first", "second"])

        self.assertTrue(all(result.ok for result in results.values()))
        self.assertEqual(self.loader.list()["first"]("ignored"), "first")
        self.assertEqual(self.loader.list()["second"]("ignored"), "second")
        self.assertEqual(len(self.environments), 2)
        self.assertNotEqual(
            self.loader.list()["first"].__globals__["shared_value"],
            self.loader.list()["second"].__globals__["shared_value"],
        )

    def test_container_annotations_use_the_existing_tool_schema(self):
        self.write("collect", '''\
def collect(values: list[str], options: dict[str, str] | None = None) -> str:
    """Collect values with optional settings."""
    return ",".join(values)
''')

        result = self.loader.load("collect")["collect"]

        self.assertTrue(result.ok, result.error)
        schema = self_tools.Tool(result.function).description["function"]["parameters"]
        self.assertEqual(schema["properties"]["values"], {
            "type": "array",
            "items": {"type": "string"},
        })
        self.assertEqual(schema["properties"]["options"], {"type": "object"})
        self.assertEqual(schema["required"], ["values"])

    def test_export_shape_and_each_tool_contract_part_are_validated(self):
        invalid_sources = {
            "wrong_export": VALID_V1.replace("greet", "other"),
            "two_public_functions": VALID_V1 + '''\
def extra(value: str) -> str:
    """An extra function."""
    return value
''',
            "keyword_incompatible": '''\
def greet(name: str, /) -> str:
    """Greet a person."""
    return name
''',
            "unsupported_type": '''\
def greet(name: object) -> str:
    """Greet a person."""
    return str(name)
''',
            "missing_docstring": '''\
def greet(name: str) -> str:
    return name
''',
            "async_function": '''\
async def greet(name: str) -> str:
    """Greet a person asynchronously."""
    return name
''',
        }

        for label, source in invalid_sources.items():
            with self.subTest(label=label):
                self.write("greet", source)
                result = self.loader.load("greet")["greet"]
                self.assertFalse(result.ok)
                self.assertIn("Traceback (most recent call last):", result.error)
                self.assertEqual(self.loader.list(), {})

    def test_list_returns_snapshot_safe_during_concurrent_reads(self):
        self.write("greet", VALID_V1)
        errors = []

        def read_repeatedly():
            try:
                for _ in range(100):
                    snapshot = self.loader.list()
                    if snapshot:
                        snapshot["greet"]("Ada")
            except Exception as error:
                errors.append(error)

        reader = threading.Thread(target=read_repeatedly)
        reader.start()
        self.loader.load("greet")
        self.write("greet", VALID_V1.replace("hello", "hi"))
        self.loader.load("greet")
        reader.join()

        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
