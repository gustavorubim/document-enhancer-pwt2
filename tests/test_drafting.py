import json
import re
from pathlib import Path

import pytest

from document_enhancer.ingest import ingest_source
from document_enhancer.models import GapKind
from document_enhancer.pipeline import (
    DeterministicProvider,
    PipelineContractError,
    assemble_draft_markdown,
    draft_sections,
    validate_draft_sections,
)
from document_enhancer.template import parse_template

ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATH = ROOT / "tests" / "fixtures" / "messy_desktop_procedure.docx"
EXPECTED_PATH = ROOT / "tests" / "fixtures" / "expected_facts.json"
TEMPLATE_PATH = ROOT / "templates" / "desktop_procedure.md"


def _run_fake():
    source = ingest_source(SOURCE_PATH)
    template = parse_template(TEMPLATE_PATH)
    provider = DeterministicProvider()
    mapping = provider.analyze(source, template)
    sections = draft_sections(provider, source, template, mapping)
    return source, template, provider, mapping, sections


def test_draft_retains_all_nine_steps_and_expected_operational_facts() -> None:
    _source, template, _provider, _mapping, sections = _run_fake()
    expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
    markdown = assemble_draft_markdown(template, sections)

    for step in expected["steps"]:
        for fact in step["must_contain"]:
            assert fact in markdown
    assert len(re.findall(r"(?m)^\d+\. ", markdown)) >= 9
    assert "variance_amount = invoice_amount - po_amount" in markdown
    assert "MISSING_SUPPLIER" in markdown
    assert "reviewer initials and the review timestamp" in markdown


def test_draft_preserves_template_fixed_text_and_exposes_deduplicated_gaps() -> None:
    _source, template, _provider, mapping, sections = _run_fake()
    markdown = assemble_draft_markdown(template, sections)

    assert all(section.fixed_markdown in markdown for section in template.sections)
    assert "<!-- REQUIREMENTS" not in markdown
    assert "[MISSING:" in markdown
    assert "[CONFLICT:" in markdown
    assert len([gap for gap in mapping.gaps if gap.kind is GapKind.CONFLICT]) == 1
    assert len(mapping.questions) == 2
    assert len({question.text.casefold() for question in mapping.questions}) == 2


def test_unsupported_business_fact_is_rejected_before_rendering() -> None:
    source, template, _provider, mapping, sections = _run_fake()
    first = sections[0].model_copy(
        update={
            "content_markdown": sections[0].content_markdown
            + "\n\nSubmit the package in Salesforce within 72 hours."
        }
    )

    with pytest.raises(PipelineContractError, match="unsupported business facts"):
        validate_draft_sections([first, *sections[1:]], source, template, mapping)


def test_deterministic_quality_review_finds_no_lost_steps_or_unsupported_claims() -> None:
    source, template, provider, mapping, sections = _run_fake()
    review = provider.review(source, template, mapping, sections)

    assert review.omitted_source_details == []
    assert review.unsupported_claims == []
    assert review.inadequate_or_overly_summarized_sections == []
    assert review.unresolved_template_requirements == []
    assert review.duplicate_content_or_questions == []
    assert review.readability_problems == []


def test_expected_facts_contract_is_fully_accounted_for() -> None:
    source, template, provider, mapping, sections = _run_fake()
    expected = json.loads(EXPECTED_PATH.read_text(encoding="utf-8"))
    markdown = assemble_draft_markdown(template, sections)

    assert len(source.blocks) == expected["fixture"]["expected_source_block_count"]
    assert [block.heading for block in source.blocks] == expected["fixture"][
        "expected_source_block_headings"
    ]
    missing = [value for value in expected["required_strings"] if value not in markdown]
    assert missing == []
    assert len(re.findall(r"(?m)^\d+\. ", markdown)) == expected["counts"][
        "ordered_step_count"
    ]
    assert len([gap for gap in mapping.gaps if gap.kind is GapKind.MISSING]) == expected["counts"][
        "missing_fact_count"
    ]
    assert len([gap for gap in mapping.gaps if gap.kind is GapKind.CONFLICT]) == expected["counts"][
        "conflict_count"
    ]
    assert len(mapping.questions) == expected["counts"]["expected_question_count"]
    assert len({question.text.casefold() for question in mapping.questions}) == len(
        mapping.questions
    )
    review = provider.review(source, template, mapping, sections)
    assert review.omitted_source_details == []
