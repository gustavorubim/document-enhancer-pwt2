import json
import tomllib
from pathlib import Path

import pytest

from document_enhancer.models import RecoveredSection, RecoveredStructure, StructureMode
from document_enhancer.pipeline import (
    DeterministicProvider,
    PipelineContractError,
    run_enhancement,
    run_stage2,
)
from document_enhancer.workflow import build_authoring_graph, invoke_authoring_graph

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "desktop_procedure.md"
STRUCTURED_SOURCE = ROOT / "tests" / "fixtures" / "messy_desktop_procedure.docx"


class CountingProvider(DeterministicProvider):
    def __init__(self) -> None:
        self.recovery_calls = 0

    def recover_structure(self, source, assessment):
        self.recovery_calls += 1
        return super().recover_structure(source, assessment)


class InvalidRecoveryProvider(DeterministicProvider):
    def recover_structure(self, source, assessment):
        del assessment
        return RecoveredStructure(
            sections=[
                RecoveredSection(
                    heading="Invented structure",
                    source_block_ids=[source.blocks[0].id, source.blocks[0].id],
                )
            ]
        )


def _unstructured_source(tmp_path: Path) -> Path:
    path = tmp_path / "unstructured.md"
    path.write_text(
        """The weekly queue review prevents invalid requests from proceeding.

The Operations Analyst needs Atlas access and the approved_accounts.csv file.

Every Tuesday, open Atlas after the request export arrives.

1. Open the queue and select the oldest request.

2. Compare the account identifier with approved_accounts.csv.

If the identifier is absent, keep the request on Hold and notify the supervisor.

Save the confirmation identifier in review_log.csv as evidence.
""",
        encoding="utf-8",
    )
    return path


def test_langgraph_bypasses_recovery_for_well_structured_source() -> None:
    provider = CountingProvider()

    state = invoke_authoring_graph(
        source_path=STRUCTURED_SOURCE,
        template_path=TEMPLATE,
        provider=provider,
    )

    assert provider.recovery_calls == 0
    assert state["recovery_used"] is False
    assert state["trace"] == ["load_inputs", "analyze", "draft", "review"]
    assert state["structure_assessment"].needs_recovery is False


def test_langgraph_recovers_weak_structure_once_without_changing_source_text(
    tmp_path: Path,
) -> None:
    provider = CountingProvider()
    source_path = _unstructured_source(tmp_path)

    state = invoke_authoring_graph(
        source_path=source_path,
        template_path=TEMPLATE,
        provider=provider,
    )

    assert provider.recovery_calls == 1
    assert state["recovery_used"] is True
    assert state["trace"] == [
        "load_inputs",
        "recover_structure",
        "analyze",
        "draft",
        "review",
    ]
    assert state["source"].structure_recovered is True
    assert [block.id for block in state["source"].blocks] == [
        f"SRC-{index:03d}" for index in range(1, len(state["source"].blocks) + 1)
    ]
    assert state["source"].full_text == source_path.read_text(encoding="utf-8").strip()
    assert all(not block.heading.startswith("Source content") for block in state["source"].blocks)


def test_structure_mode_never_bypasses_recovery_for_weak_source(tmp_path: Path) -> None:
    provider = CountingProvider()
    state = invoke_authoring_graph(
        source_path=_unstructured_source(tmp_path),
        template_path=TEMPLATE,
        provider=provider,
        structure_mode=StructureMode.NEVER,
    )

    assert provider.recovery_calls == 0
    assert state["recovery_used"] is False


def test_invalid_recovery_cannot_drop_duplicate_or_invent_block_ids(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="cover every source block exactly once"):
        invoke_authoring_graph(
            source_path=_unstructured_source(tmp_path),
            template_path=TEMPLATE,
            provider=InvalidRecoveryProvider(),
            structure_mode=StructureMode.ALWAYS,
        )


def test_compiled_graph_has_fixed_bounded_topology() -> None:
    graph = build_authoring_graph().get_graph()

    assert set(graph.nodes) == {
        "__start__",
        "load_inputs",
        "recover_structure",
        "analyze",
        "draft",
        "review",
        "__end__",
    }


def test_project_uses_langgraph_without_direct_langchain_dependency() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert any(item.startswith("langgraph") for item in project["dependencies"])
    assert not any(
        item.split("<", maxsplit=1)[0].split(">", maxsplit=1)[0] == "langchain"
        for item in project["dependencies"]
    )


def test_stage2_reuses_exact_stage1_recovered_structure(tmp_path: Path) -> None:
    provider = CountingProvider()
    source_path = _unstructured_source(tmp_path)
    stage1 = run_enhancement(
        source_path=source_path,
        template_path=TEMPLATE,
        output_dir=tmp_path / "stage1",
        provider=provider,
        include_process_flow=True,
    )
    payload = json.loads(stage1.questions_json.read_text(encoding="utf-8"))
    mapping = json.loads(stage1.mapping_json.read_text(encoding="utf-8"))
    assert payload["structure_recovered"] is True
    assert payload["recovered_structure"] is not None
    assert mapping["source_structure"]["recovered"] is True
    assert "recover_structure" in mapping["source_structure"]["workflow_trace"]
    for question in payload["questions"]:
        question["answer"] = "Use the document owner's confirmed operating decision."
    stage1.questions_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    run_stage2(
        source_path=source_path,
        template_path=TEMPLATE,
        answers_path=stage1.questions_json,
        output_dir=tmp_path / "stage2",
        provider=provider,
    )

    assert provider.recovery_calls == 1


def test_stage2_rejects_changed_persisted_structure(tmp_path: Path) -> None:
    provider = CountingProvider()
    source_path = _unstructured_source(tmp_path)
    stage1 = run_enhancement(
        source_path=source_path,
        template_path=TEMPLATE,
        output_dir=tmp_path / "stage1",
        provider=provider,
        include_process_flow=True,
    )
    payload = json.loads(stage1.questions_json.read_text(encoding="utf-8"))
    payload["recovered_structure"]["sections"][0]["heading"] = "Changed heading"
    for question in payload["questions"]:
        question["answer"] = "Use the document owner's confirmed operating decision."
    stage1.questions_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    with pytest.raises(PipelineContractError, match="protected structure contract"):
        run_stage2(
            source_path=source_path,
            template_path=TEMPLATE,
            answers_path=stage1.questions_json,
            output_dir=tmp_path / "stage2",
            provider=provider,
        )
