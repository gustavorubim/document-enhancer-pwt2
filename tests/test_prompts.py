from pathlib import Path

import pytest

from document_enhancer.prompts import PROMPT_NAMES, PromptStore


def test_every_operation_loads_shared_and_operation_prompt() -> None:
    store = PromptStore.default()
    shared = store.load("shared")
    for name in PROMPT_NAMES[1:]:
        operation = store.operation(name)
        assert shared in operation
        assert store.load(name) in operation


def test_unknown_prompt_is_rejected() -> None:
    with pytest.raises(KeyError, match="unknown prompt"):
        PromptStore.default().load("invented")


def test_substantive_prompt_rule_is_not_embedded_in_python() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src"
    python_text = "\n".join(path.read_text(encoding="utf-8") for path in source_root.rglob("*.py"))
    assert "Never invent owners, dates, deadlines" not in python_text
    assert "Do not compress several source steps" not in python_text
