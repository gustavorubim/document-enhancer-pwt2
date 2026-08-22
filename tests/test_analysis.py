from pathlib import Path

import pytest

from document_enhancer.ingest import ingest_source
from document_enhancer.models import CoverageStatus, GapKind
from document_enhancer.pipeline import (
    DeterministicProvider,
    PipelineContractError,
    validate_mapping,
)
from document_enhancer.template import parse_template


def _inputs(tmp_path: Path):
    source_path = tmp_path / "source.md"
    source_path.write_text(
        """# Queue Review Notes

## Purpose and scope

The Operations Analyst reviews the Atlas exception queue so invalid requests do not proceed.
The review applies to requests received in the North America queue.

## Before starting

The analyst needs Atlas Reviewer access and the current `approved-accounts.csv` file.

## Timing

Start when an exception request is received. Complete the exception review within 2 hours of
receiving the request.

## Working notes

Complete the exception review within 4 hours of receiving the request. The four-hour timing is
also written on the team checklist.

## Actions

1. Open Atlas and select the Exception Review queue.
2. Open the oldest request and compare its account ID with `approved-accounts.csv`.
3. If the account is present, select **Approve**; otherwise select **Hold**.
4. Save the decision and verify that Atlas displays a confirmation identifier.
5. Record the identifier in the daily review log.

## Failure handling

If Atlas does not display a confirmation identifier, retry Save once. If the second attempt fails,
keep the request on Hold and notify the Operations Supervisor. The source does not identify the
shared location where the daily review log must be retained.
""",
        encoding="utf-8",
    )
    template_path = tmp_path / "template.md"
    template_path.write_text(
        """# Queue Review
<!-- REQUIREMENTS
- Identify the procedure and its intended outcome.
-->

## Access and inputs
<!-- REQUIREMENTS
- List required access, systems, files, and inputs.
-->

## Timing
<!-- REQUIREMENTS
- State the trigger and completion deadline.
-->

## Procedure steps
<!-- REQUIREMENTS
- Preserve the complete ordered action sequence.
- Explain each decision condition and validation check.
-->

## Evidence and recovery
<!-- REQUIREMENTS
- Identify evidence and where it is retained.
- Explain failure, recovery, and escalation actions.
-->
""",
        encoding="utf-8",
    )
    return ingest_source(source_path), parse_template(template_path)


def test_analysis_accounts_for_every_section_requirement_and_source_block(tmp_path: Path) -> None:
    source, template = _inputs(tmp_path)
    mapping = DeterministicProvider().analyze(source, template)

    assert [item.target_section_id for item in mapping.target_sections] == [
        section.id for section in template.sections
    ]
    assert {
        requirement.requirement_id
        for section in mapping.target_sections
        for requirement in section.requirements
    } == {requirement.id for section in template.sections for requirement in section.requirements}
    assert {item.source_block_id for item in mapping.source_section_dispositions} == {
        block.id for block in source.blocks
    }
    assert all(item.source_block_ids for item in mapping.target_sections)
    assert len(mapping.procedure_component_assessment) == 16


def test_repeated_timing_conflict_creates_one_gap_and_one_question(tmp_path: Path) -> None:
    source, template = _inputs(tmp_path)
    mapping = DeterministicProvider().analyze(source, template)

    conflict_gaps = [gap for gap in mapping.gaps if gap.kind is GapKind.CONFLICT]
    conflict_questions = [
        question
        for question in mapping.questions
        if any(gap_id in {gap.id for gap in conflict_gaps} for gap_id in question.gap_ids)
    ]
    assert len(conflict_gaps) == 1
    assert len(conflict_questions) == 1
    assert "2 hours" in conflict_gaps[0].description
    assert "4 hours" in conflict_gaps[0].description
    timing = next(item for item in mapping.target_sections if item.heading == "Timing")
    assert timing.status is CoverageStatus.CONFLICTING


def test_mapping_validation_fails_closed_on_missing_source_disposition(tmp_path: Path) -> None:
    source, template = _inputs(tmp_path)
    mapping = DeterministicProvider().analyze(source, template)
    invalid = mapping.model_copy(
        update={"source_section_dispositions": mapping.source_section_dispositions[:-1]}
    )

    with pytest.raises(PipelineContractError, match="disposition every source block"):
        validate_mapping(invalid, source, template)


def test_mapping_validation_fails_closed_on_unknown_source_citation(tmp_path: Path) -> None:
    source, template = _inputs(tmp_path)
    mapping = DeterministicProvider().analyze(source, template)
    invalid_macro = mapping.macro_assessment.model_copy(update={"source_block_ids": ["SRC-999"]})
    invalid = mapping.model_copy(update={"macro_assessment": invalid_macro})

    with pytest.raises(PipelineContractError, match="unknown source block IDs"):
        validate_mapping(invalid, source, template)
