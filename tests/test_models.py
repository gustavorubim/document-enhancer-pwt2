from pathlib import Path

import pytest
from pydantic import ValidationError

from document_enhancer.models import (
    AnalysisMapping,
    AnalysisQuestion,
    CoverageStatus,
    DispositionTreatment,
    Gap,
    GapKind,
    MacroAssessment,
    ParsedTemplate,
    ProcedureComponent,
    ProcedureComponentAssessment,
    RequirementAssessment,
    SourceBlock,
    SourceDocument,
    SourceFormat,
    SourceSectionDisposition,
    TargetSectionAssessment,
    TemplateRequirement,
    TemplateSection,
)


def test_source_document_requires_contiguous_blocks() -> None:
    with pytest.raises(ValidationError, match="contiguous"):
        SourceDocument(
            title="Procedure",
            source_path=Path("source.md"),
            source_format=SourceFormat.MARKDOWN,
            blocks=[SourceBlock(id="SRC-001", heading="Purpose", content="Text", order=2)],
            full_text="Text",
        )


def test_template_rejects_duplicate_headings_case_insensitively() -> None:
    requirement = TemplateRequirement(id="REQ-001-01", text="State the purpose.")
    with pytest.raises(ValidationError, match="headings must be unique"):
        ParsedTemplate(
            source_path=Path("template.md"),
            sections=[
                TemplateSection(
                    id="SEC-001", heading="Purpose", level=2, requirements=[requirement]
                ),
                TemplateSection(
                    id="SEC-002",
                    heading=" purpose ",
                    level=2,
                    requirements=[
                        TemplateRequirement(id="REQ-002-01", text="Repeat the purpose.")
                    ],
                ),
            ],
        )


def test_analysis_mapping_rejects_duplicate_questions() -> None:
    requirement = RequirementAssessment(
        requirement_id="REQ-001-01",
        requirement="Identify the owner.",
        status=CoverageStatus.MISSING,
        assessment="No owner is identified.",
        supporting_source_block_ids=[],
        source_block_ids=["SRC-001"],
        gaps=["GAP-001"],
        recommended_improvements=["Identify the accountable role."],
    )
    payload = {
        "macro_assessment": MacroAssessment(
            usable_as_desktop_procedure=False,
            assessment="The source needs remediation.",
            strengths=[],
            deficiencies=["Owner is missing."],
            overall_remediation="Resolve the missing owner.",
            source_block_ids=["SRC-001"],
        ),
        "target_sections": [
            TargetSectionAssessment(
                target_section_id="SEC-001",
                heading="Roles",
                status=CoverageStatus.MISSING,
                requirements=[requirement],
                source_block_ids=["SRC-001"],
                gaps=["GAP-001"],
                recommended_improvements=["Identify the accountable role."],
            )
        ],
        "source_section_dispositions": [
            SourceSectionDisposition(
                source_block_id="SRC-001",
                source_heading="Notes",
                target_section_ids=["SEC-001"],
                treatment=DispositionTreatment.DIRECT,
                rationale="The notes are relevant to roles.",
                source_block_ids=["SRC-001"],
            )
        ],
        "procedure_component_assessment": [
            ProcedureComponentAssessment(
                component=ProcedureComponent.ROLES,
                status=CoverageStatus.MISSING,
                assessment="The role is absent.",
                supporting_source_block_ids=[],
                source_block_ids=["SRC-001"],
                gaps=["GAP-001"],
                recommendations=["Identify the role."],
            )
        ],
        "gaps": [
            Gap(
                id="GAP-001",
                kind=GapKind.MISSING,
                description="The owner is missing.",
                question="Who owns the procedure?",
                source_block_ids=["SRC-001"],
            )
        ],
        "questions": [
            AnalysisQuestion(
                id="QUE-001",
                text="Who owns the procedure?",
                gap_ids=["GAP-001"],
                source_block_ids=["SRC-001"],
            ),
            AnalysisQuestion(
                id="QUE-002",
                text=" who OWNS the procedure? ",
                gap_ids=["GAP-001"],
                source_block_ids=["SRC-001"],
            ),
        ],
    }
    with pytest.raises(ValidationError, match="question text must be deduplicated"):
        AnalysisMapping.model_validate(payload)
