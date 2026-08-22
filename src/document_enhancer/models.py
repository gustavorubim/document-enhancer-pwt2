"""Small structured contracts shared by ingestion, providers, and rendering."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    """Base model that rejects accidental provider fields."""

    model_config = ConfigDict(extra="forbid")


class SourceFormat(StrEnum):
    MARKDOWN = "markdown"
    DOCX = "docx"


class CoverageStatus(StrEnum):
    SUPPORTED = "supported"
    PARTIALLY_SUPPORTED = "partially_supported"
    MISSING = "missing"
    CONFLICTING = "conflicting"
    NOT_APPLICABLE = "not_applicable"


class DispositionTreatment(StrEnum):
    DIRECT = "direct"
    SPLIT = "split"
    COMBINED = "combined"
    UNRESOLVED = "unresolved"
    OMITTED = "omitted"


class GapKind(StrEnum):
    MISSING = "missing"
    CONFLICT = "conflict"
    UNCLEAR = "unclear"


class ProcedureComponent(StrEnum):
    OBJECTIVE = "objective"
    INTENDED_USER = "intended_user"
    SCOPE = "scope"
    PREREQUISITES = "prerequisites"
    TOOLS_AND_ACCESS = "tools_and_access"
    ROLES = "roles"
    TRIGGERS = "triggers"
    STEP_SEQUENCE = "step_sequence"
    DECISION_POINTS = "decision_points"
    VALIDATION = "validation"
    EXPECTED_RESULTS = "expected_results"
    EVIDENCE = "evidence"
    EXCEPTIONS = "exceptions"
    RECOVERY = "recovery"
    ESCALATION = "escalation"
    READABILITY_AND_USABILITY = "readability_and_usability"


class ProcessNodeKind(StrEnum):
    START = "start"
    ACTION = "action"
    DECISION = "decision"
    END = "end"


class SourceBlock(StrictModel):
    id: str = Field(pattern=r"^SRC-\d{3}$")
    heading: str = Field(min_length=1)
    content: str = Field(min_length=1)
    order: int = Field(ge=1)


class SourceDocument(StrictModel):
    title: str = Field(min_length=1)
    source_path: Path
    source_format: SourceFormat
    blocks: list[SourceBlock] = Field(min_length=1)
    full_text: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_blocks(self) -> SourceDocument:
        ids = [block.id for block in self.blocks]
        orders = [block.order for block in self.blocks]
        if len(ids) != len(set(ids)):
            raise ValueError("source block IDs must be unique")
        if orders != list(range(1, len(self.blocks) + 1)):
            raise ValueError("source block order must be contiguous and start at 1")
        return self


class TemplateRequirement(StrictModel):
    id: str = Field(pattern=r"^REQ-\d{3}-\d{2}$")
    text: str = Field(min_length=1)


class TemplateSection(StrictModel):
    id: str = Field(pattern=r"^SEC-\d{3}$")
    heading: str = Field(min_length=1)
    level: int = Field(ge=1, le=6)
    requirements: list[TemplateRequirement] = Field(min_length=1)
    fixed_markdown: str = ""


class ParsedTemplate(StrictModel):
    source_path: Path
    sections: list[TemplateSection] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_sections(self) -> ParsedTemplate:
        ids = [section.id for section in self.sections]
        headings = [section.heading.strip().casefold() for section in self.sections]
        if len(ids) != len(set(ids)):
            raise ValueError("template section IDs must be unique")
        if len(headings) != len(set(headings)):
            raise ValueError("template section headings must be unique")
        return self


class MacroAssessment(StrictModel):
    usable_as_desktop_procedure: bool
    assessment: str = Field(min_length=1)
    strengths: list[str]
    deficiencies: list[str]
    overall_remediation: str = Field(min_length=1)
    source_block_ids: list[str] = Field(min_length=1)


class RequirementAssessment(StrictModel):
    requirement_id: str
    requirement: str = Field(min_length=1)
    status: CoverageStatus
    assessment: str = Field(min_length=1)
    supporting_source_block_ids: list[str]
    source_block_ids: list[str] = Field(min_length=1)
    gaps: list[str]
    recommended_improvements: list[str]


class TargetSectionAssessment(StrictModel):
    target_section_id: str
    heading: str = Field(min_length=1)
    status: CoverageStatus
    requirements: list[RequirementAssessment] = Field(min_length=1)
    source_block_ids: list[str] = Field(min_length=1)
    gaps: list[str]
    recommended_improvements: list[str]


class SourceSectionDisposition(StrictModel):
    source_block_id: str
    source_heading: str = Field(min_length=1)
    target_section_ids: list[str]
    treatment: DispositionTreatment
    intentionally_omitted: bool = False
    rationale: str = Field(min_length=1)
    source_block_ids: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_omission(self) -> SourceSectionDisposition:
        if self.intentionally_omitted != (self.treatment is DispositionTreatment.OMITTED):
            raise ValueError("intentionally_omitted must agree with omitted treatment")
        if self.intentionally_omitted and self.target_section_ids:
            raise ValueError("an intentionally omitted block cannot have a target destination")
        return self


class ProcedureComponentAssessment(StrictModel):
    component: ProcedureComponent
    status: CoverageStatus
    assessment: str = Field(min_length=1)
    supporting_source_block_ids: list[str]
    source_block_ids: list[str] = Field(min_length=1)
    gaps: list[str]
    recommendations: list[str]


class Gap(StrictModel):
    id: str = Field(pattern=r"^GAP-\d{3}$")
    kind: GapKind
    description: str = Field(min_length=1)
    question: str = Field(min_length=1)
    source_block_ids: list[str] = Field(min_length=1)


class AnalysisQuestion(StrictModel):
    id: str = Field(pattern=r"^QUE-\d{3}$")
    text: str = Field(min_length=1)
    gap_ids: list[str] = Field(min_length=1)
    source_block_ids: list[str] = Field(min_length=1)


class AnalysisMapping(StrictModel):
    macro_assessment: MacroAssessment
    target_sections: list[TargetSectionAssessment] = Field(min_length=1)
    source_section_dispositions: list[SourceSectionDisposition] = Field(min_length=1)
    procedure_component_assessment: list[ProcedureComponentAssessment] = Field(min_length=1)
    gaps: list[Gap]
    questions: list[AnalysisQuestion]

    @model_validator(mode="after")
    def validate_unique_records(self) -> AnalysisMapping:
        collections = {
            "target section": [item.target_section_id for item in self.target_sections],
            "source disposition": [
                item.source_block_id for item in self.source_section_dispositions
            ],
            "gap": [item.id for item in self.gaps],
            "question": [item.id for item in self.questions],
        }
        for label, identifiers in collections.items():
            if len(identifiers) != len(set(identifiers)):
                raise ValueError(f"{label} records must be unique")
        normalized_questions = [item.text.strip().casefold() for item in self.questions]
        if len(normalized_questions) != len(set(normalized_questions)):
            raise ValueError("question text must be deduplicated")
        gap_ids = {gap.id for gap in self.gaps}
        if any(gap_id not in gap_ids for question in self.questions for gap_id in question.gap_ids):
            raise ValueError("every question gap ID must reference a declared gap")
        return self


class DraftSection(StrictModel):
    target_section_id: str
    heading: str = Field(min_length=1)
    content_markdown: str = Field(min_length=1)
    supporting_source_block_ids: list[str]
    unresolved_gap_ids: list[str]


class QualityReview(StrictModel):
    omitted_source_details: list[str]
    unsupported_claims: list[str]
    inadequate_or_overly_summarized_sections: list[str]
    unresolved_template_requirements: list[str]
    duplicate_content_or_questions: list[str]
    readability_problems: list[str]
    corrected_sections: list[DraftSection]


class ProcessNode(StrictModel):
    id: str = Field(pattern=r"^NODE-\d{3}$")
    label: str = Field(min_length=1, max_length=180)
    kind: ProcessNodeKind
    source_block_ids: list[str] = Field(min_length=1)


class ProcessEdge(StrictModel):
    from_node_id: str
    to_node_id: str
    label: str = Field(default="", max_length=100)
    source_block_ids: list[str] = Field(min_length=1)


class ProcessGraph(StrictModel):
    title: str = Field(min_length=1)
    nodes: list[ProcessNode] = Field(min_length=2, max_length=24)
    edges: list[ProcessEdge] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def validate_graph(self) -> ProcessGraph:
        node_ids = [node.id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("process node IDs must be unique")
        known = set(node_ids)
        unknown = {
            node_id
            for edge in self.edges
            for node_id in (edge.from_node_id, edge.to_node_id)
            if node_id not in known
        }
        if unknown:
            raise ValueError(f"process edges reference unknown nodes: {sorted(unknown)}")
        return self


class QuestionResponse(StrictModel):
    id: str = Field(pattern=r"^QUE-\d{3}$")
    text: str = Field(min_length=1)
    gap_ids: list[str] = Field(min_length=1)
    answer: str = ""


class Questionnaire(StrictModel):
    schema_version: Literal["questions.v1"] = "questions.v1"
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    template_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    questions: list[QuestionResponse]


class ResolutionRecord(StrictModel):
    question_id: str = Field(pattern=r"^QUE-\d{3}$")
    gap_ids: list[str] = Field(min_length=1)
    answer: str = Field(min_length=1)
    answer_source_block_id: str = Field(pattern=r"^SRC-\d{3}$")


class Stage2Resolution(StrictModel):
    schema_version: Literal["resolution.v1"] = "resolution.v1"
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    template_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    resolutions: list[ResolutionRecord]
