"""Small structured contracts shared by ingestion, providers, and rendering."""

from __future__ import annotations

import hashlib
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


class StructureMode(StrEnum):
    AUTO = "auto"
    ALWAYS = "always"
    NEVER = "never"


class BatchStatus(StrEnum):
    COMPLETED = "completed"
    COMPLETED_WITH_QUESTIONS = "completed_with_questions"
    FAILED = "failed"


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


class StructureAssessment(StrictModel):
    score: float = Field(ge=0, le=1)
    needs_recovery: bool
    reasons: list[str]
    block_count: int = Field(ge=1)
    meaningful_heading_count: int = Field(ge=0)
    generic_heading_count: int = Field(ge=0)
    longest_block_characters: int = Field(ge=0)


class RecoveredSection(StrictModel):
    heading: str = Field(min_length=1, max_length=120)
    level: int = Field(default=2, ge=1, le=6)
    source_block_ids: list[str] = Field(min_length=1)


class RecoveredStructure(StrictModel):
    sections: list[RecoveredSection] = Field(min_length=1)


class SourceBlock(StrictModel):
    id: str = Field(pattern=r"^SRC-\d{3}$")
    heading: str = Field(min_length=1)
    content: str = Field(min_length=1)
    order: int = Field(ge=1)


class SourceAsset(StrictModel):
    id: str = Field(pattern=r"^FIG-\d{3}$")
    source_block_id: str = Field(pattern=r"^SRC-\d{3}$")
    order: int = Field(ge=1)
    original_name: str = Field(min_length=1)
    media_type: Literal["image/png", "image/jpeg"]
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    size_bytes: int = Field(gt=0)
    width_pixels: int = Field(gt=0)
    height_pixels: int = Field(gt=0)
    anchor_text: str = ""
    alt_text: str = ""
    payload: bytes = Field(repr=False, exclude=True)


class SourceDocument(StrictModel):
    title: str = Field(min_length=1)
    source_path: Path
    source_format: SourceFormat
    blocks: list[SourceBlock] = Field(min_length=1)
    assets: list[SourceAsset] = Field(default_factory=list)
    full_text: str = Field(min_length=1)
    structure_assessment: StructureAssessment | None = None
    structure_recovered: bool = False

    @model_validator(mode="after")
    def validate_blocks(self) -> SourceDocument:
        ids = [block.id for block in self.blocks]
        orders = [block.order for block in self.blocks]
        if len(ids) != len(set(ids)):
            raise ValueError("source block IDs must be unique")
        if orders != list(range(1, len(self.blocks) + 1)):
            raise ValueError("source block order must be contiguous and start at 1")
        asset_ids = [asset.id for asset in self.assets]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("source asset IDs must be unique")
        if [asset.order for asset in self.assets] != list(range(1, len(self.assets) + 1)):
            raise ValueError("source asset order must be contiguous and start at 1")
        known_source_ids = set(ids)
        for asset in self.assets:
            if asset.source_block_id not in known_source_ids:
                raise ValueError(f"source asset references unknown block: {asset.source_block_id}")
            if hashlib.sha256(asset.payload).hexdigest() != asset.sha256:
                raise ValueError(f"source asset digest does not match payload: {asset.id}")
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
    schema_version: Literal["questions.v2"] = "questions.v2"
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    template_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    structure_mode: StructureMode
    structure_recovered: bool
    structure_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    recovered_structure: RecoveredStructure | None = None
    questions: list[QuestionResponse]

    @model_validator(mode="after")
    def validate_recovered_structure(self) -> Questionnaire:
        if self.structure_recovered != (self.recovered_structure is not None):
            raise ValueError(
                "structure_recovered must agree with the persisted recovered structure"
            )
        return self


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


class BatchDocumentResult(StrictModel):
    source_name: str = Field(min_length=1)
    source_path: Path
    output_dir: Path
    status: BatchStatus
    question_count: int = Field(ge=0)
    screenshot_count: int = Field(ge=0)
    structure_score: float | None = Field(default=None, ge=0, le=1)
    structure_recovered: bool = False
    duration_seconds: float = Field(ge=0)
    error: str | None = None


class BatchManifest(StrictModel):
    schema_version: Literal["batch.v1"] = "batch.v1"
    input_dir: Path
    template_path: Path
    template_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    documents: list[BatchDocumentResult]
    completed_count: int = Field(ge=0)
    questions_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    screenshot_count: int = Field(ge=0)
    recovered_count: int = Field(ge=0)
    total_duration_seconds: float = Field(ge=0)
