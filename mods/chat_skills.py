"""Load small, local Markdown instructions for an LLM chat request."""

from __future__ import annotations

import logging
from pathlib import Path


DEFAULT_SKILLS_DIR = Path("data/skills")

_logger = logging.getLogger(__name__)


def load_skill_messages(
    directory: str | Path = DEFAULT_SKILLS_DIR,
) -> list[dict[str, str]]:
    """Read top-level Markdown skills as appendable system messages.

    Files are read afresh on every call.  A broken file is reported and
    skipped so that it cannot suppress other skills or the surrounding chat.
    """
    root = Path(directory)
    if not root.is_dir():
        return []

    messages: list[dict[str, str]] = []
    paths = sorted(
        (path for path in root.glob("*.md") if path.is_file()),
        key=lambda path: path.name,
    )
    for path in paths:
        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            _logger.exception("failed to read chat skill %s", path)
            continue

        messages.append({
            "role": "system",
            "content": (
                f"--- BEGIN CHAT SKILL: {path.name} ---\n"
                f"{content}\n"
                f"--- END CHAT SKILL: {path.name} ---"
            ),
        })
    return messages
