from pathlib import Path
import importlib.util
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def _load_chat_skills_module():
    """Load the pure helper without importing the production mods package."""
    module_name = "chat_skills_under_test"
    spec = importlib.util.spec_from_file_location(
        module_name,
        ROOT / "mods" / "chat_skills.py",
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


chat_skills = _load_chat_skills_module()


class ChatSkillsTests(unittest.TestCase):
    def test_reads_top_level_markdown_files_in_filename_order(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            (root / "z-last.md").write_text("last", encoding="utf-8")
            (root / "a-first.md").write_text("first", encoding="utf-8")
            (root / "notes.txt").write_text("not a skill", encoding="utf-8")

            messages = chat_skills.load_skill_messages(root)

        self.assertEqual([message["role"] for message in messages], ["system", "system"])
        self.assertIn("BEGIN CHAT SKILL: a-first.md", messages[0]["content"])
        self.assertIn("first", messages[0]["content"])
        self.assertIn("END CHAT SKILL: a-first.md", messages[0]["content"])
        self.assertIn("BEGIN CHAT SKILL: z-last.md", messages[1]["content"])
        self.assertIn("last", messages[1]["content"])
        self.assertNotIn("not a skill", "\n".join(message["content"] for message in messages))

    def test_ignores_markdown_files_in_nested_directories(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            nested = root / "references"
            nested.mkdir()
            (root / "top.md").write_text("top level", encoding="utf-8")
            (nested / "nested.md").write_text("nested reference", encoding="utf-8")

            messages = chat_skills.load_skill_messages(root)

        self.assertEqual(len(messages), 1)
        self.assertIn("top level", messages[0]["content"])
        self.assertNotIn("nested reference", messages[0]["content"])

    def test_reads_files_again_on_every_call(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "changing.md"
            path.write_text("version one", encoding="utf-8")
            first = chat_skills.load_skill_messages(temporary_directory)

            path.write_text("version two", encoding="utf-8")
            second = chat_skills.load_skill_messages(temporary_directory)

        self.assertIn("version one", first[0]["content"])
        self.assertIn("version two", second[0]["content"])
        self.assertNotIn("version one", second[0]["content"])

    def test_missing_directory_returns_empty_list(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            missing = Path(temporary_directory) / "missing"

            self.assertEqual(chat_skills.load_skill_messages(missing), [])

    def test_one_failed_file_is_logged_and_skipped(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            good = root / "good.md"
            broken = root / "broken.md"
            good.write_text("usable", encoding="utf-8")
            broken.write_text("unreadable", encoding="utf-8")
            original_read_text = Path.read_text

            def read_text(path, *args, **kwargs):
                if path == broken:
                    raise OSError("simulated read failure")
                return original_read_text(path, *args, **kwargs)

            with self.assertLogs(chat_skills.__name__, level="ERROR") as captured:
                with patch.object(Path, "read_text", read_text):
                    messages = chat_skills.load_skill_messages(root)

        self.assertEqual(len(messages), 1)
        self.assertIn("good.md", messages[0]["content"])
        log_output = "\n".join(captured.output)
        self.assertIn("broken.md", log_output)
        self.assertIn("Traceback", log_output)


if __name__ == "__main__":
    unittest.main()
