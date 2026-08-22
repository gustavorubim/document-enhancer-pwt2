"""Load version-controlled model instructions from the repository prompt directory."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROMPT_NAMES = ("shared", "analyze", "draft_section", "review", "diagram", "finalize")


@dataclass(frozen=True)
class PromptStore:
    root: Path

    @classmethod
    def default(cls) -> PromptStore:
        return cls(Path(__file__).resolve().parents[2] / "prompts")

    def load(self, name: str) -> str:
        if name not in PROMPT_NAMES:
            raise KeyError(f"unknown prompt: {name}")
        path = self.root / f"{name}.md"
        try:
            content = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError as exc:
            raise RuntimeError(f"required prompt file is missing: {path}") from exc
        if not content:
            raise RuntimeError(f"required prompt file is empty: {path}")
        return content

    def operation(self, name: str) -> str:
        if name == "shared":
            return self.load("shared")
        return f"{self.load('shared')}\n\n{self.load(name)}"
