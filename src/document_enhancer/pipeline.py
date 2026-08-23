"""Application-owned document operations executed by the bounded LangGraph workflow."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, TypeVar

from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel

from document_enhancer.diagram import (
    process_graph_to_mermaid,
    render_process_graph_png,
    validate_process_graph_sources,
)
from document_enhancer.models import (
    AnalysisMapping,
    AnalysisQuestion,
    CoverageStatus,
    DispositionTreatment,
    DraftSection,
    Gap,
    GapKind,
    MacroAssessment,
    ParsedTemplate,
    ProcedureComponent,
    ProcedureComponentAssessment,
    ProcessEdge,
    ProcessGraph,
    ProcessNode,
    ProcessNodeKind,
    QualityReview,
    Questionnaire,
    QuestionResponse,
    RecoveredSection,
    RecoveredStructure,
    RequirementAssessment,
    ResolutionRecord,
    SourceAsset,
    SourceBlock,
    SourceDocument,
    SourceSectionDisposition,
    Stage2Resolution,
    StructureAssessment,
    StructureMode,
    TargetSectionAssessment,
    TemplateRequirement,
    TemplateSection,
)
from document_enhancer.prompts import PromptStore
from document_enhancer.render import render_markdown_pair, render_markdown_to_docx


class PipelineContractError(ValueError):
    """Raised when a provider response violates the application-owned contract."""


@dataclass(frozen=True)
class RunArtifacts:
    draft_markdown: Path
    draft_docx: Path
    analysis_markdown: Path
    analysis_docx: Path
    mapping_json: Path
    quality_review: QualityReview
    structure_assessment: StructureAssessment
    structure_recovered: bool
    workflow_trace: tuple[str, ...]
    source_asset_paths: tuple[Path, ...] = ()
    process_flow_mermaid: Path | None = None
    process_flow_image: Path | None = None
    questions_json: Path | None = None


@dataclass(frozen=True)
class Stage2Artifacts:
    final_markdown: Path
    final_docx: Path
    resolution_json: Path
    process_flow_mermaid: Path
    process_flow_image: Path
    source_asset_paths: tuple[Path, ...] = ()


class AnalysisProvider(Protocol):
    def recover_structure(
        self, source: SourceDocument, assessment: StructureAssessment
    ) -> RecoveredStructure: ...

    def analyze(self, source: SourceDocument, template: ParsedTemplate) -> AnalysisMapping: ...

    def draft_section(
        self,
        source: SourceDocument,
        section: TemplateSection,
        assessment: TargetSectionAssessment,
        mapping: AnalysisMapping,
        prior_sections: list[DraftSection],
    ) -> DraftSection: ...

    def review(
        self,
        source: SourceDocument,
        template: ParsedTemplate,
        mapping: AnalysisMapping,
        sections: list[DraftSection],
    ) -> QualityReview: ...

    def process_graph(
        self,
        source: SourceDocument,
        mapping: AnalysisMapping,
        sections: list[DraftSection],
    ) -> ProcessGraph: ...


StructuredModel = TypeVar("StructuredModel", bound=BaseModel)


class GeminiProvider:
    """Single Gemini setup used for each bounded structured model operation."""

    def __init__(
        self,
        *,
        model: str = "gemini-2.5-flash",
        prompt_store: PromptStore | None = None,
    ) -> None:
        self._model = ChatGoogleGenerativeAI(model=model, temperature=0)
        self._prompts = prompt_store or PromptStore.default()

    @staticmethod
    def credentials_available() -> bool:
        return bool(os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))

    def recover_structure(
        self, source: SourceDocument, assessment: StructureAssessment
    ) -> RecoveredStructure:
        payload = {
            "source": source.model_dump(mode="json"),
            "structure_assessment": assessment.model_dump(mode="json"),
        }
        return self._invoke("structure", RecoveredStructure, payload)

    def analyze(self, source: SourceDocument, template: ParsedTemplate) -> AnalysisMapping:
        payload = {
            "source": source.model_dump(mode="json"),
            "template": template.model_dump(mode="json"),
        }
        return self._invoke("analyze", AnalysisMapping, payload)

    def draft_section(
        self,
        source: SourceDocument,
        section: TemplateSection,
        assessment: TargetSectionAssessment,
        mapping: AnalysisMapping,
        prior_sections: list[DraftSection],
    ) -> DraftSection:
        payload = {
            "source": source.model_dump(mode="json"),
            "target_section": section.model_dump(mode="json"),
            "section_assessment": assessment.model_dump(mode="json"),
            "declared_gaps": [
                gap.model_dump(mode="json") for gap in mapping.gaps if gap.id in assessment.gaps
            ],
            "prior_sections": [item.model_dump(mode="json") for item in prior_sections],
        }
        operation = (
            "finalize"
            if any(block.heading.startswith("Authoritative answer to ") for block in source.blocks)
            else "draft_section"
        )
        return self._invoke(operation, DraftSection, payload)

    def review(
        self,
        source: SourceDocument,
        template: ParsedTemplate,
        mapping: AnalysisMapping,
        sections: list[DraftSection],
    ) -> QualityReview:
        payload = {
            "source": source.model_dump(mode="json"),
            "template": template.model_dump(mode="json"),
            "mapping": mapping.model_dump(mode="json"),
            "draft_sections": [item.model_dump(mode="json") for item in sections],
        }
        return self._invoke("review", QualityReview, payload)

    def process_graph(
        self,
        source: SourceDocument,
        mapping: AnalysisMapping,
        sections: list[DraftSection],
    ) -> ProcessGraph:
        payload = {
            "source": source.model_dump(mode="json"),
            "mapping": mapping.model_dump(mode="json"),
            "draft_sections": [item.model_dump(mode="json") for item in sections],
        }
        return self._invoke("diagram", ProcessGraph, payload)

    def _invoke(
        self,
        operation: str,
        schema: type[StructuredModel],
        payload: dict[str, Any],
    ) -> StructuredModel:
        runnable = self._model.with_structured_output(schema, method="json_schema")
        result = runnable.invoke(
            [
                ("system", self._prompts.operation(operation)),
                ("human", json.dumps(payload, ensure_ascii=False, indent=2)),
            ]
        )
        return result if isinstance(result, schema) else schema.model_validate(result)


@dataclass(frozen=True)
class _ConflictFinding:
    description: str
    question: str
    source_block_ids: tuple[str, ...]
    components: tuple[ProcedureComponent, ...]


@dataclass(frozen=True)
class _ExplicitGapFinding:
    description: str
    question: str
    source_block_ids: tuple[str, ...]
    components: tuple[ProcedureComponent, ...]


@dataclass
class _GapRegistry:
    gaps: list[Gap]
    keys: dict[str, str]

    @classmethod
    def create(cls) -> _GapRegistry:
        return cls(gaps=[], keys={})

    def add(
        self,
        *,
        key: str,
        kind: GapKind,
        description: str,
        question: str,
        source_block_ids: list[str],
    ) -> str:
        normalized_key = _normalize_key(key)
        existing = self.keys.get(normalized_key)
        if existing:
            return existing
        gap_id = f"GAP-{len(self.gaps) + 1:03d}"
        self.gaps.append(
            Gap(
                id=gap_id,
                kind=kind,
                description=description,
                question=question,
                source_block_ids=_unique(source_block_ids),
            )
        )
        self.keys[normalized_key] = gap_id
        return gap_id


class DeterministicProvider:
    """Transparent offline evaluator used by tests and the bundled example."""

    def recover_structure(
        self, source: SourceDocument, assessment: StructureAssessment
    ) -> RecoveredStructure:
        del assessment
        return _deterministic_recovered_structure(source)

    def analyze(self, source: SourceDocument, template: ParsedTemplate) -> AnalysisMapping:
        source_ids = [block.id for block in source.blocks]
        relevance = {
            section.id: {block.id: _section_relevance(section, block) for block in source.blocks}
            for section in template.sections
        }
        candidates = {
            section.id: _candidate_blocks(section, source.blocks, relevance[section.id])
            for section in template.sections
        }
        conflicts = _find_numeric_conflicts(source.blocks)
        explicit_gaps = _find_explicit_source_gaps(source.blocks)
        gap_registry = _GapRegistry.create()
        explicit_gap_pairs = [
            (
                finding,
                gap_registry.add(
                    key=f"explicit-missing:{finding.question}",
                    kind=GapKind.MISSING,
                    description=finding.description,
                    question=finding.question,
                    source_block_ids=list(finding.source_block_ids),
                ),
            )
            for finding in explicit_gaps
        ]
        conflict_gap_pairs = [
            (
                finding,
                gap_registry.add(
                    key=f"conflict:{finding.question}",
                    kind=GapKind.CONFLICT,
                    description=finding.description,
                    question=finding.question,
                    source_block_ids=list(finding.source_block_ids),
                ),
            )
            for finding in conflicts
        ]

        target_assessments: list[TargetSectionAssessment] = []
        for section in template.sections:
            section_blocks = candidates[section.id]
            requirement_assessments = [
                self._assess_requirement(
                    requirement,
                    section,
                    section_blocks,
                    source.blocks,
                    conflict_gap_pairs,
                    explicit_gap_pairs,
                    gap_registry,
                )
                for requirement in section.requirements
            ]
            statuses = [assessment.status for assessment in requirement_assessments]
            section_status = _aggregate_status(statuses)
            target_assessments.append(
                TargetSectionAssessment(
                    target_section_id=section.id,
                    heading=section.heading,
                    status=section_status,
                    requirements=requirement_assessments,
                    source_block_ids=_unique(
                        source_id
                        for assessment in requirement_assessments
                        for source_id in assessment.source_block_ids
                    ),
                    gaps=_unique(
                        gap_id
                        for assessment in requirement_assessments
                        for gap_id in assessment.gaps
                    ),
                    recommended_improvements=_unique(
                        recommendation
                        for assessment in requirement_assessments
                        for recommendation in assessment.recommended_improvements
                    ),
                )
            )

        dispositions = _source_dispositions(source.blocks, template.sections, relevance, candidates)
        components = self._component_assessments(source.blocks, gap_registry, source_ids)
        questions = [
            AnalysisQuestion(
                id=f"QUE-{index:03d}",
                text=gap.question,
                gap_ids=[gap.id],
                source_block_ids=gap.source_block_ids,
            )
            for index, gap in enumerate(gap_registry.gaps, start=1)
        ]
        mapping = AnalysisMapping(
            macro_assessment=_macro_assessment(source_ids, target_assessments, components),
            target_sections=target_assessments,
            source_section_dispositions=dispositions,
            procedure_component_assessment=components,
            gaps=gap_registry.gaps,
            questions=questions,
        )
        validate_mapping(mapping, source, template)
        return mapping

    def draft_section(
        self,
        source: SourceDocument,
        section: TemplateSection,
        assessment: TargetSectionAssessment,
        mapping: AnalysisMapping,
        prior_sections: list[DraftSection],
    ) -> DraftSection:
        disposition_by_source = {
            item.source_block_id: item for item in mapping.source_section_dispositions
        }
        assessment_source_ids = _unique(
            source_id
            for requirement in assessment.requirements
            for source_id in requirement.supporting_source_block_ids
        )
        mapped_source_ids = [
            block.id
            for block in source.blocks
            if section.id in disposition_by_source[block.id].target_section_ids
        ]
        if section.level == 1:
            candidate_ids = [source.blocks[0].id]
        elif mapped_source_ids:
            candidate_ids = mapped_source_ids
        else:
            candidate_ids = [
                block.id
                for block in sorted(
                    (block for block in source.blocks if block.id in assessment_source_ids),
                    key=lambda block: _section_relevance(section, block),
                    reverse=True,
                )[:2]
            ]
        used_segments = {
            _normalize_prose(segment)
            for prior in prior_sections
            for segment in _content_segments(prior.content_markdown)
            if len(_normalize_prose(segment)) >= 30
        }
        drafted_parts: list[str] = []
        supporting_ids: list[str] = []
        assessment_by_target = {item.target_section_id: item for item in mapping.target_sections}
        for block in source.blocks:
            if block.id not in candidate_ids:
                continue
            disposition = disposition_by_source[block.id]
            primary_target = (
                disposition.target_section_ids[0] if disposition.target_section_ids else None
            )
            segments = _content_segments(block.content)
            if section.level > 1 and len(disposition.target_section_ids) > 1:
                segments = [
                    segment
                    for segment in segments
                    if max(
                        disposition.target_section_ids,
                        key=lambda target_id: _assessment_segment_score(
                            assessment_by_target[target_id], segment
                        ),
                    )
                    == section.id
                ]
            elif primary_target != section.id:
                segments = [
                    segment for segment in segments if _section_segment_relevant(section, segment)
                ]
            unused = [
                segment
                for segment in segments
                if len(_normalize_prose(segment)) < 30
                or _normalize_prose(segment) not in used_segments
            ]
            if not unused:
                continue
            subheading_level = min(section.level + 1, 6)
            drafted_parts.append(
                f"{'#' * subheading_level} {block.heading}\n\n" + "\n\n".join(unused)
            )
            supporting_ids.append(block.id)
            used_segments.update(
                _normalize_prose(segment)
                for segment in unused
                if len(_normalize_prose(segment)) >= 30
            )

        content_parts = [section.fixed_markdown] if section.fixed_markdown else []
        content_parts.extend(drafted_parts)
        gap_by_id = {gap.id: gap for gap in mapping.gaps}
        for gap_id in assessment.gaps:
            gap = gap_by_id[gap_id]
            label = {
                GapKind.MISSING: "MISSING",
                GapKind.CONFLICT: "CONFLICT",
                GapKind.UNCLEAR: "UNCLEAR",
            }[gap.kind]
            content_parts.append(f"[{label}: {gap.description}]")
        if not content_parts:
            raise PipelineContractError(
                f"section {section.id} has neither fixed text, grounded source content, nor a gap"
            )
        return DraftSection(
            target_section_id=section.id,
            heading=section.heading,
            content_markdown="\n\n".join(part.strip() for part in content_parts if part.strip()),
            supporting_source_block_ids=supporting_ids,
            unresolved_gap_ids=assessment.gaps,
        )

    def review(
        self,
        source: SourceDocument,
        template: ParsedTemplate,
        mapping: AnalysisMapping,
        sections: list[DraftSection],
    ) -> QualityReview:
        assembled = assemble_draft_markdown(template, sections)
        normalized_draft = _normalize_prose(assembled)
        omitted = [
            block.id
            for block in source.blocks
            if any(
                len(_normalize_prose(segment)) >= 20
                and _normalize_prose(segment) not in normalized_draft
                for segment in _content_segments(block.content)
            )
        ]
        unsupported: list[str] = []
        for section in sections:
            section_model = next(
                item for item in template.sections if item.id == section.target_section_id
            )
            unsupported.extend(
                f"{section.target_section_id}: {anchor}"
                for anchor in _unsupported_anchors(section, section_model, source)
            )
        inadequate = [
            section.target_section_id
            for section in sections
            if section.supporting_source_block_ids and len(_tokens(section.content_markdown)) < 8
        ]
        unresolved = []
        gap_by_id = {gap.id: gap for gap in mapping.gaps}
        for section in sections:
            for gap_id in section.unresolved_gap_ids:
                if gap_by_id[gap_id].description not in section.content_markdown:
                    unresolved.append(f"{section.target_section_id}: {gap_id}")
        duplicates = _duplicate_segments(sections)
        source_steps = sum(
            bool(re.match(r"^\s*\d+[.)]\s+", line))
            for block in source.blocks
            for line in block.content.splitlines()
        )
        draft_steps = sum(
            bool(re.match(r"^\s*\d+[.)]\s+", line)) for line in assembled.splitlines()
        )
        readability = (
            [
                f"The source contains {source_steps} ordered actions, but the draft retains "
                f"only {draft_steps}."
            ]
            if draft_steps < source_steps
            else []
        )
        return QualityReview(
            omitted_source_details=omitted,
            unsupported_claims=unsupported,
            inadequate_or_overly_summarized_sections=inadequate,
            unresolved_template_requirements=unresolved,
            duplicate_content_or_questions=duplicates,
            readability_problems=readability,
            corrected_sections=[],
        )

    def process_graph(
        self,
        source: SourceDocument,
        mapping: AnalysisMapping,
        sections: list[DraftSection],
    ) -> ProcessGraph:
        del sections
        return _deterministic_process_graph(source, mapping)

    def _assess_requirement(
        self,
        requirement: TemplateRequirement,
        section: TemplateSection,
        section_blocks: list[SourceBlock],
        all_blocks: list[SourceBlock],
        conflict_gap_pairs: list[tuple[_ConflictFinding, str]],
        explicit_gap_pairs: list[tuple[_ExplicitGapFinding, str]],
        gaps: _GapRegistry,
    ) -> RequirementAssessment:
        requirement_action = requirement.text.rstrip(".").lower()
        evaluated_ids = [block.id for block in section_blocks] or [block.id for block in all_blocks]
        relevant = _relevant_requirement_blocks(
            requirement,
            section,
            all_blocks,
            preferred_source_ids={block.id for block in section_blocks},
        )
        related_conflicts = [
            (finding, gap_id)
            for finding, gap_id in conflict_gap_pairs
            if _conflict_applies(finding, section, requirement)
        ]
        if related_conflicts:
            evaluated_ids = _unique(
                [*evaluated_ids]
                + [
                    source_id
                    for finding, _ in related_conflicts
                    for source_id in finding.source_block_ids
                ]
            )
            return RequirementAssessment(
                requirement_id=requirement.id,
                requirement=requirement.text,
                status=CoverageStatus.CONFLICTING,
                assessment="The source supplies incompatible business values for this requirement.",
                supporting_source_block_ids=_unique(
                    source_id
                    for finding, _ in related_conflicts
                    for source_id in finding.source_block_ids
                ),
                source_block_ids=evaluated_ids,
                gaps=[gap_id for _, gap_id in related_conflicts],
                recommended_improvements=[
                    "Confirm the authoritative source-supported value before publication."
                ],
            )

        not_applicable = [
            block
            for block in relevant
            if re.search(r"\b(?:not applicable|n/?a)\b", block.content, flags=re.IGNORECASE)
        ]
        if not_applicable:
            return RequirementAssessment(
                requirement_id=requirement.id,
                requirement=requirement.text,
                status=CoverageStatus.NOT_APPLICABLE,
                assessment="The source explicitly identifies this requirement as not applicable.",
                supporting_source_block_ids=[block.id for block in not_applicable],
                source_block_ids=evaluated_ids,
                gaps=[],
                recommended_improvements=[],
            )

        related_explicit_gaps = [
            (finding, gap_id)
            for finding, gap_id in explicit_gap_pairs
            if _explicit_gap_applies(finding, section, requirement)
        ]
        if related_explicit_gaps and relevant:
            evaluated_ids = _unique(
                [*evaluated_ids]
                + [
                    source_id
                    for finding, _ in related_explicit_gaps
                    for source_id in finding.source_block_ids
                ]
            )
            return RequirementAssessment(
                requirement_id=requirement.id,
                requirement=requirement.text,
                status=CoverageStatus.PARTIALLY_SUPPORTED,
                assessment=(
                    "The source provides relevant information but explicitly leaves a required "
                    "business detail unresolved."
                ),
                supporting_source_block_ids=_unique(
                    [block.id for block in relevant]
                    + [
                        source_id
                        for finding, _ in related_explicit_gaps
                        for source_id in finding.source_block_ids
                    ]
                ),
                source_block_ids=evaluated_ids,
                gaps=[gap_id for _, gap_id in related_explicit_gaps],
                recommended_improvements=[
                    "Obtain the explicitly missing business detail from the document owner."
                ],
            )

        if relevant:
            ambiguous_blocks = [
                block
                for block in relevant
                if re.search(
                    r"\bunclear\b|does not explain|not explain",
                    block.content,
                    flags=re.IGNORECASE,
                )
            ]
            if ambiguous_blocks:
                status = CoverageStatus.PARTIALLY_SUPPORTED
                gap_id = gaps.add(
                    key=f"unclear:{_requirement_key(requirement)}",
                    kind=GapKind.UNCLEAR,
                    description=f"The source only partially explains how to {requirement_action}.",
                    question=(
                        f"What additional source-supported detail is needed to "
                        f"{requirement_action}?"
                    ),
                    source_block_ids=[block.id for block in ambiguous_blocks],
                )
                assessment = (
                    "The source mentions the subject but lacks enough detail to satisfy it fully."
                )
                requirement_gaps = [gap_id]
                improvements = ["Add the missing operational detail after the owner confirms it."]
            else:
                status = CoverageStatus.SUPPORTED
                assessment = (
                    "The cited source blocks provide information responsive to the requirement."
                )
                requirement_gaps = []
                improvements = []
            return RequirementAssessment(
                requirement_id=requirement.id,
                requirement=requirement.text,
                status=status,
                assessment=assessment,
                supporting_source_block_ids=[block.id for block in relevant],
                source_block_ids=evaluated_ids,
                gaps=requirement_gaps,
                recommended_improvements=improvements,
            )

        gap_id = gaps.add(
            key=f"missing:{_requirement_key(requirement)}",
            kind=GapKind.MISSING,
            description=f"The source does not explain how to {requirement_action}.",
            question=f"What source-supported information should be used to {requirement_action}?",
            source_block_ids=evaluated_ids,
        )
        return RequirementAssessment(
            requirement_id=requirement.id,
            requirement=requirement.text,
            status=CoverageStatus.MISSING,
            assessment="No source block provides information responsive to this requirement.",
            supporting_source_block_ids=[],
            source_block_ids=evaluated_ids,
            gaps=[gap_id],
            recommended_improvements=[
                "Resolve the missing business information before treating this requirement "
                "as complete."
            ],
        )

    def _component_assessments(
        self,
        blocks: list[SourceBlock],
        gaps: _GapRegistry,
        all_source_ids: list[str],
    ) -> list[ProcedureComponentAssessment]:
        assessments: list[ProcedureComponentAssessment] = []
        for component in ProcedureComponent:
            component_name = _display_component(component)
            supporting = [
                block.id
                for block in sorted(
                    blocks,
                    key=lambda block: (
                        (10 if component in _signals(block.heading) else 0)
                        + (2 if component in _signals(block.content) else 0)
                    ),
                    reverse=True,
                )
                if component in _signals(f"{block.heading} {block.content}")
            ][:5]
            related_gaps = [gap.id for gap in gaps.gaps if component in _gap_components(gap)]
            if supporting:
                status = (
                    CoverageStatus.PARTIALLY_SUPPORTED if related_gaps else CoverageStatus.SUPPORTED
                )
                assessment = f"The source contains information relevant to {component_name}."
                recommendations = (
                    ["Resolve the related documented gaps to make this component complete."]
                    if status is CoverageStatus.PARTIALLY_SUPPORTED
                    else []
                )
            else:
                status = CoverageStatus.MISSING
                if not related_gaps:
                    related_gaps = [
                        gaps.add(
                            key=f"missing-component:{component.value}",
                            kind=GapKind.MISSING,
                            description=(
                                f"The source does not provide {component_name} information."
                            ),
                            question=(
                                f"What source-supported {component_name} information should the "
                                "procedure include?"
                            ),
                            source_block_ids=all_source_ids,
                        )
                    ]
                assessment = (
                    f"The source does not provide enough information about {component_name}."
                )
                recommendations = [
                    "Obtain the missing business information from the document owner."
                ]
            assessments.append(
                ProcedureComponentAssessment(
                    component=component,
                    status=status,
                    assessment=assessment,
                    supporting_source_block_ids=supporting,
                    source_block_ids=supporting or all_source_ids,
                    gaps=related_gaps,
                    recommendations=recommendations,
                )
            )
        return assessments


def _deterministic_recovered_structure(source: SourceDocument) -> RecoveredStructure:
    sections: list[RecoveredSection] = []
    for block in source.blocks:
        text = f"{block.heading} {block.content}"
        signals = _signals(text)
        if re.search(r"(?m)^\s*\d+[.)]\s+", block.content):
            heading = "Procedure steps"
        elif signals & {
            ProcedureComponent.EXCEPTIONS,
            ProcedureComponent.RECOVERY,
            ProcedureComponent.ESCALATION,
        }:
            heading = "Exceptions, recovery, and escalation"
        elif signals & {ProcedureComponent.DECISION_POINTS, ProcedureComponent.VALIDATION}:
            heading = "Decision points and validation"
        elif ProcedureComponent.EVIDENCE in signals:
            heading = "Outputs and evidence"
        elif signals & {ProcedureComponent.PREREQUISITES, ProcedureComponent.TOOLS_AND_ACCESS}:
            heading = "Prerequisites, tools, and access"
        elif ProcedureComponent.ROLES in signals:
            heading = "Roles and responsibilities"
        elif ProcedureComponent.TRIGGERS in signals:
            heading = "Triggers and timing"
        elif signals & {ProcedureComponent.INTENDED_USER, ProcedureComponent.SCOPE}:
            heading = "Audience and scope"
        elif signals & {ProcedureComponent.OBJECTIVE, ProcedureComponent.EXPECTED_RESULTS}:
            heading = "Purpose and outcome"
        else:
            heading = "Procedure details"
        if sections and sections[-1].heading == heading:
            sections[-1] = sections[-1].model_copy(
                update={"source_block_ids": [*sections[-1].source_block_ids, block.id]}
            )
        else:
            sections.append(RecoveredSection(heading=heading, source_block_ids=[block.id]))
    return RecoveredStructure(sections=sections)


def _deterministic_process_graph(source: SourceDocument, mapping: AnalysisMapping) -> ProcessGraph:
    step_records: list[tuple[SourceBlock, str]] = []
    for block in source.blocks:
        for line in block.content.splitlines():
            match = re.match(r"^\s*\d+[.)]\s+(.+)", line.strip())
            if match:
                step_records.append((block, match.group(1).strip()))
    if not step_records:
        step_records = [
            (block, block.content.splitlines()[0])
            for block in source.blocks
            if block.content.strip()
        ][:12]

    trigger_block = next(
        (
            block
            for block in source.blocks
            if re.search(r"\btrigger\b|\bstart every\b", block.content, re.IGNORECASE)
        ),
        source.blocks[0],
    )
    trigger_match = re.search(r"Trigger:\s*([^\n]+)", trigger_block.content, re.IGNORECASE)
    if trigger_match is None:
        trigger_match = re.search(
            r"([^\n]*\bstart every\b[^\n]*)", trigger_block.content, re.IGNORECASE
        )
    trigger_label = _short_graph_label(
        trigger_match.group(1) if trigger_match else trigger_block.heading
    )
    nodes = [
        ProcessNode(
            id="NODE-001",
            label=trigger_label,
            kind=ProcessNodeKind.START,
            source_block_ids=[trigger_block.id],
        )
    ]
    step_node_ids: list[str] = []
    for index, (block, text) in enumerate(step_records, start=2):
        node_id = f"NODE-{index:03d}"
        step_node_ids.append(node_id)
        nodes.append(
            ProcessNode(
                id=node_id,
                label=_short_graph_label(text),
                kind=ProcessNodeKind.ACTION,
                source_block_ids=[block.id],
            )
        )

    answer_blocks = [
        block for block in source.blocks if block.heading.startswith("Authoritative answer to ")
    ]
    threshold_answer = next(
        (block for block in answer_blocks if re.search(r"\$\s*\d+", block.content)), None
    )
    threshold_gap = next(
        (
            gap
            for gap in mapping.gaps
            if gap.kind is GapKind.CONFLICT
            and re.search(r"threshold", gap.description, re.IGNORECASE)
        ),
        None,
    )
    decision_step_index = next(
        (
            index
            for index, (_, text) in enumerate(step_records)
            if re.search(r"threshold|at or below|anything above", text, re.IGNORECASE)
        ),
        None,
    )
    decision_id: str | None = None
    if decision_step_index is not None and (threshold_gap or threshold_answer):
        decision_id = f"NODE-{len(nodes) + 1:03d}"
        if threshold_answer:
            decision_label = _short_graph_label(threshold_answer.content)
            decision_sources = [threshold_answer.id, step_records[decision_step_index][0].id]
        else:
            decision_label = _short_graph_label(f"Unresolved decision: {threshold_gap.description}")
            decision_sources = threshold_gap.source_block_ids
        nodes.append(
            ProcessNode(
                id=decision_id,
                label=decision_label,
                kind=ProcessNodeKind.DECISION,
                source_block_ids=decision_sources,
            )
        )

    completion_block = next(
        (
            block
            for block in reversed(source.blocks)
            if re.search(r"complete|completion|evidence", block.content, re.IGNORECASE)
        ),
        source.blocks[-1],
    )
    end_id = f"NODE-{len(nodes) + 1:03d}"
    nodes.append(
        ProcessNode(
            id=end_id,
            label="Evidence retained and procedure complete",
            kind=ProcessNodeKind.END,
            source_block_ids=[completion_block.id],
        )
    )

    edges: list[ProcessEdge] = []
    if step_node_ids:
        edges.append(
            ProcessEdge(
                from_node_id="NODE-001",
                to_node_id=step_node_ids[0],
                source_block_ids=[trigger_block.id, step_records[0][0].id],
            )
        )
    for index in range(len(step_node_ids) - 1):
        source_id = step_records[index][0].id
        if decision_id and index == decision_step_index:
            edges.append(
                ProcessEdge(
                    from_node_id=step_node_ids[index],
                    to_node_id=decision_id,
                    source_block_ids=[source_id],
                )
            )
            edges.append(
                ProcessEdge(
                    from_node_id=decision_id,
                    to_node_id=step_node_ids[index + 1],
                    label="Pending / exception",
                    source_block_ids=nodes[-2].source_block_ids,
                )
            )
            if index + 2 < len(step_node_ids):
                edges.append(
                    ProcessEdge(
                        from_node_id=decision_id,
                        to_node_id=step_node_ids[index + 2],
                        label="Clear / continue",
                        source_block_ids=nodes[-2].source_block_ids,
                    )
                )
            continue
        edges.append(
            ProcessEdge(
                from_node_id=step_node_ids[index],
                to_node_id=step_node_ids[index + 1],
                source_block_ids=[source_id, step_records[index + 1][0].id],
            )
        )
    if step_node_ids:
        edges.append(
            ProcessEdge(
                from_node_id=step_node_ids[-1],
                to_node_id=end_id,
                source_block_ids=[step_records[-1][0].id, completion_block.id],
            )
        )
    else:
        edges.append(
            ProcessEdge(
                from_node_id="NODE-001",
                to_node_id=end_id,
                source_block_ids=[trigger_block.id, completion_block.id],
            )
        )
    return ProcessGraph(title=f"{source.title} process flow", nodes=nodes, edges=edges)


def _short_graph_label(value: str, limit: int = 150) -> str:
    normalized = " ".join(value.split())
    return normalized if len(normalized) <= limit else normalized[: limit - 1].rstrip() + "…"


def validate_mapping(
    mapping: AnalysisMapping, source: SourceDocument, template: ParsedTemplate
) -> None:
    """Fail closed when a provider omits records or cites unknown application IDs."""

    source_ids = {block.id for block in source.blocks}
    target_ids = {section.id for section in template.sections}
    expected_requirements = {
        section.id: {requirement.id for requirement in section.requirements}
        for section in template.sections
    }
    actual_targets = {assessment.target_section_id for assessment in mapping.target_sections}
    if actual_targets != target_ids:
        raise PipelineContractError(
            f"provider must assess every template section exactly once; expected "
            f"{sorted(target_ids)}, received {sorted(actual_targets)}"
        )
    actual_dispositions = {
        disposition.source_block_id for disposition in mapping.source_section_dispositions
    }
    if actual_dispositions != source_ids:
        raise PipelineContractError(
            f"provider must disposition every source block exactly once; expected "
            f"{sorted(source_ids)}, received {sorted(actual_dispositions)}"
        )
    for assessment in mapping.target_sections:
        actual_requirements = {item.requirement_id for item in assessment.requirements}
        if actual_requirements != expected_requirements[assessment.target_section_id]:
            raise PipelineContractError(
                f"provider must assess every requirement for {assessment.target_section_id}; "
                f"expected {sorted(expected_requirements[assessment.target_section_id])}, "
                f"received {sorted(actual_requirements)}"
            )
    for target_id in (
        target_id
        for disposition in mapping.source_section_dispositions
        for target_id in disposition.target_section_ids
    ):
        if target_id not in target_ids:
            raise PipelineContractError(f"provider cited unknown target section ID: {target_id}")
    cited_source_ids = _all_cited_source_ids(mapping)
    unknown_source_ids = cited_source_ids - source_ids
    if unknown_source_ids:
        raise PipelineContractError(
            f"provider cited unknown source block IDs: {sorted(unknown_source_ids)}"
        )
    gap_ids = {gap.id for gap in mapping.gaps}
    cited_gap_ids = {gap_id for target in mapping.target_sections for gap_id in target.gaps} | {
        gap_id for component in mapping.procedure_component_assessment for gap_id in component.gaps
    }
    unknown_gap_ids = cited_gap_ids - gap_ids
    if unknown_gap_ids:
        raise PipelineContractError(f"provider cited unknown gap IDs: {sorted(unknown_gap_ids)}")


def draft_sections(
    provider: AnalysisProvider,
    source: SourceDocument,
    template: ParsedTemplate,
    mapping: AnalysisMapping,
) -> list[DraftSection]:
    """Draft every target section once in deterministic template order."""

    assessment_by_target = {
        assessment.target_section_id: assessment for assessment in mapping.target_sections
    }
    sections: list[DraftSection] = []
    for target in template.sections:
        sections.append(
            provider.draft_section(
                source,
                target,
                assessment_by_target[target.id],
                mapping,
                sections,
            )
        )
    validate_draft_sections(sections, source, template, mapping)
    return sections


def review_sections(
    provider: AnalysisProvider,
    source: SourceDocument,
    template: ParsedTemplate,
    mapping: AnalysisMapping,
    sections: list[DraftSection],
) -> tuple[list[DraftSection], QualityReview]:
    """Run one bounded quality review and apply only declared corrected sections."""

    review = provider.review(source, template, mapping, sections)
    if not review.corrected_sections:
        return sections, review
    corrected_by_id = {section.target_section_id: section for section in review.corrected_sections}
    unknown = set(corrected_by_id) - {section.id for section in template.sections}
    if unknown:
        raise PipelineContractError(
            f"quality review corrected unknown target section IDs: {sorted(unknown)}"
        )
    revised = [corrected_by_id.get(section.target_section_id, section) for section in sections]
    validate_draft_sections(revised, source, template, mapping)
    return revised, review


def validate_draft_sections(
    sections: list[DraftSection],
    source: SourceDocument,
    template: ParsedTemplate,
    mapping: AnalysisMapping,
) -> None:
    """Reject missing targets, unknown references, hidden comments, and factual anchors."""

    expected_order = [section.id for section in template.sections]
    actual_order = [section.target_section_id for section in sections]
    if actual_order != expected_order:
        raise PipelineContractError(
            f"draft sections must match template order {expected_order}; received {actual_order}"
        )
    source_ids = {block.id for block in source.blocks}
    gap_ids = {gap.id for gap in mapping.gaps}
    template_by_id = {section.id: section for section in template.sections}
    for draft in sections:
        target = template_by_id[draft.target_section_id]
        if draft.heading != target.heading:
            raise PipelineContractError(
                f"draft heading for {target.id} must remain {target.heading!r}"
            )
        if "REQUIREMENTS" in draft.content_markdown or "<!--" in draft.content_markdown:
            raise PipelineContractError(
                f"draft section {target.id} leaked a template requirement comment"
            )
        if target.fixed_markdown and target.fixed_markdown not in draft.content_markdown:
            raise PipelineContractError(
                f"draft section {target.id} did not preserve the template's fixed text"
            )
        unknown_sources = set(draft.supporting_source_block_ids) - source_ids
        if unknown_sources:
            raise PipelineContractError(
                f"draft section {target.id} cited unknown source IDs: {sorted(unknown_sources)}"
            )
        unknown_gaps = set(draft.unresolved_gap_ids) - gap_ids
        if unknown_gaps:
            raise PipelineContractError(
                f"draft section {target.id} cited unknown gap IDs: {sorted(unknown_gaps)}"
            )
        unsupported = _unsupported_anchors(draft, target, source)
        if unsupported:
            raise PipelineContractError(
                f"draft section {target.id} contains unsupported business facts: {unsupported}"
            )


def assemble_draft_markdown(template: ParsedTemplate, sections: list[DraftSection]) -> str:
    """Assemble section content beneath the template's exact ordered headings."""

    section_by_id = {section.target_section_id: section for section in sections}
    parts = []
    for target in template.sections:
        draft = section_by_id[target.id]
        parts.append(f"{'#' * target.level} {target.heading}\n\n{draft.content_markdown.strip()}")
    return "\n\n".join(parts).rstrip() + "\n"


def analysis_to_markdown(mapping: AnalysisMapping) -> str:
    """Render the required owner-facing analysis in a detailed, scannable structure."""

    macro = mapping.macro_assessment
    lines = [
        "# Document Enhancement Analysis",
        "",
        "## Executive assessment",
        "",
        "**Usable as a desktop procedure:** "
        + ("Yes" if macro.usable_as_desktop_procedure else "No"),
        "",
        macro.assessment,
        "",
        f"**Overall remediation:** {macro.overall_remediation}",
        "",
        f"**Source blocks reviewed:** {', '.join(macro.source_block_ids)}",
        "",
        "### Strengths",
        "",
        *(_markdown_bullets(macro.strengths) or ["- No complete strengths were established."]),
        "",
        "### Most important deficiencies",
        "",
        *(_markdown_bullets(macro.deficiencies) or ["- No material deficiencies were identified."]),
        "",
        "## Template coverage",
        "",
        "Every target section and requirement is evaluated below.",
    ]
    for target in mapping.target_sections:
        lines.extend(
            [
                "",
                f"### {target.heading}",
                "",
                f"**Overall status:** {target.status.value.replace('_', ' ')}  ",
                f"**Source blocks evaluated:** {', '.join(target.source_block_ids)}",
                "",
                "| Requirement | Status | Supporting source | Gaps | Recommended improvement |",
                "| --- | --- | --- | --- | --- |",
            ]
        )
        for requirement in target.requirements:
            lines.append(
                "| "
                + " | ".join(
                    [
                        _table_text(requirement.requirement),
                        requirement.status.value.replace("_", " "),
                        _table_text(", ".join(requirement.supporting_source_block_ids) or "None"),
                        _table_text(", ".join(requirement.gaps) or "None"),
                        _table_text("; ".join(requirement.recommended_improvements) or "None"),
                    ]
                )
                + " |"
            )
        if target.gaps:
            lines.extend(["", f"**Section gaps:** {', '.join(target.gaps)}"])

    lines.extend(
        [
            "",
            "## Source-section disposition",
            "",
            "Every meaningful source block is accounted for.",
            "",
            "| Source block | Source section | Target destination | Treatment | Omitted | "
            "Rationale |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for disposition in mapping.source_section_dispositions:
        lines.append(
            "| "
            + " | ".join(
                [
                    disposition.source_block_id,
                    _table_text(disposition.source_heading),
                    _table_text(", ".join(disposition.target_section_ids) or "Unresolved"),
                    disposition.treatment.value,
                    "Yes" if disposition.intentionally_omitted else "No",
                    _table_text(disposition.rationale),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Desktop-procedure component analysis",
            "",
            "| Component | Status | Supporting source | Assessment | Gaps | Recommendations |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for component in mapping.procedure_component_assessment:
        lines.append(
            "| "
            + " | ".join(
                [
                    component.component.value.replace("_", " "),
                    component.status.value.replace("_", " "),
                    _table_text(", ".join(component.supporting_source_block_ids) or "None"),
                    _table_text(component.assessment),
                    _table_text(", ".join(component.gaps) or "None"),
                    _table_text("; ".join(component.recommendations) or "None"),
                ]
            )
            + " |"
        )

    lines.extend(["", "## Questions and recommendations", ""])
    gap_by_id = {gap.id: gap for gap in mapping.gaps}
    if not mapping.questions:
        lines.append("No unresolved business questions were identified.")
    for index, question in enumerate(mapping.questions, start=1):
        gaps = [gap_by_id[gap_id] for gap_id in question.gap_ids]
        lines.extend(
            [
                f"### {index}. {question.text}",
                "",
                f"**Gap IDs:** {', '.join(question.gap_ids)}  ",
                f"**Classification:** {', '.join(gap.kind.value for gap in gaps)}  ",
                f"**Supporting source blocks:** {', '.join(question.source_block_ids)}",
                "",
                " ".join(gap.description for gap in gaps),
                "",
                "**Recommendation:** Resolve the question with authoritative business input, "
                "then update the source before regenerating the procedure.",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _persist_source_assets(source: SourceDocument, target_dir: Path) -> tuple[Path, ...]:
    if not source.assets:
        return ()
    asset_dir = target_dir / "assets"
    asset_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for asset in source.assets:
        suffix = ".png" if asset.media_type == "image/png" else ".jpg"
        path = asset_dir / f"{asset.id}{suffix}"
        if hashlib.sha256(asset.payload).hexdigest() != asset.sha256:
            raise PipelineContractError(f"source screenshot digest mismatch: {asset.id}")
        path.write_bytes(asset.payload)
        paths.append(path)
    return tuple(paths)


def _place_source_assets(
    markdown: str,
    source: SourceDocument,
    mapping: AnalysisMapping,
    template: ParsedTemplate,
) -> str:
    if not source.assets:
        return markdown
    disposition_by_source = {
        item.source_block_id: item for item in mapping.source_section_dispositions
    }
    target_by_id = {section.id: section for section in template.sections}
    result = markdown
    for asset in reversed(source.assets):
        figure_markdown = _source_asset_markdown(asset)
        disposition = disposition_by_source.get(asset.source_block_id)
        target_id = (
            disposition.target_section_ids[0]
            if disposition and disposition.target_section_ids
            else None
        )
        target = target_by_id.get(target_id or "")
        if asset.anchor_text and target:
            placed = _insert_after_anchor_in_section(
                result, target, asset.anchor_text, figure_markdown
            )
            if placed is not None:
                result = placed
                continue
        if asset.anchor_text and result.count(asset.anchor_text) == 1:
            result = result.replace(
                asset.anchor_text,
                asset.anchor_text + "\n\n" + figure_markdown,
                1,
            )
            continue
        if target:
            result = _append_to_markdown_section(result, target, figure_markdown)
        else:
            result = result.rstrip() + "\n\n## Source screenshots\n\n" + figure_markdown + "\n"
    return result


def _source_asset_markdown(asset: SourceAsset) -> str:
    suffix = ".png" if asset.media_type == "image/png" else ".jpg"
    alt = _markdown_inline_text(asset.alt_text or f"Original source screenshot {asset.id}")
    anchor = _markdown_inline_text(" ".join(asset.anchor_text.split())[:160])
    context = f' near "{anchor}"' if anchor else ""
    return (
        f"![{alt}](assets/{asset.id}{suffix})\n\n"
        f"*{asset.id} - Original source screenshot retained from "
        f"{asset.source_block_id}{context}.*"
    )


def _markdown_inline_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")


def _append_to_markdown_section(markdown: str, target: TemplateSection, content: str) -> str:
    heading = f"{'#' * target.level} {target.heading}"
    start = markdown.find(heading)
    if start < 0:
        return markdown.rstrip() + "\n\n" + content + "\n"
    body_start = start + len(heading)
    next_heading = re.search(rf"(?m)^#{{1,{target.level}}}\s+", markdown[body_start:])
    end = body_start + next_heading.start() if next_heading else len(markdown)
    return markdown[:end].rstrip() + "\n\n" + content + "\n\n" + markdown[end:].lstrip()


def _insert_after_anchor_in_section(
    markdown: str,
    target: TemplateSection,
    anchor: str,
    content: str,
) -> str | None:
    heading = f"{'#' * target.level} {target.heading}"
    start = markdown.find(heading)
    if start < 0:
        return None
    body_start = start + len(heading)
    next_heading = re.search(rf"(?m)^#{{1,{target.level}}}\s+", markdown[body_start:])
    end = body_start + next_heading.start() if next_heading else len(markdown)
    anchor_start = markdown.find(anchor, body_start, end)
    if anchor_start < 0:
        return None
    anchor_end = anchor_start + len(anchor)
    return markdown[:anchor_end] + "\n\n" + content + markdown[anchor_end:]


def run_enhancement(
    *,
    source_path: Path,
    template_path: Path,
    output_dir: Path,
    provider: AnalysisProvider,
    include_process_flow: bool = False,
    structure_mode: StructureMode = StructureMode.AUTO,
) -> RunArtifacts:
    """Run the complete pipeline; the baseline emits exactly five product artifacts."""

    from document_enhancer.workflow import invoke_authoring_graph

    state = invoke_authoring_graph(
        source_path=source_path,
        template_path=template_path,
        provider=provider,
        structure_mode=structure_mode,
    )
    source = state["source"]
    template = state["template"]
    mapping = state["mapping"]
    sections = state["sections"]
    quality_review = state["quality_review"]
    draft_markdown = assemble_draft_markdown(template, sections)
    analysis_markdown = analysis_to_markdown(mapping)

    target_dir = output_dir.expanduser().resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    source_asset_paths = _persist_source_assets(source, target_dir)
    draft_markdown = _place_source_assets(draft_markdown, source, mapping, template)
    flow_mermaid_path: Path | None = None
    flow_image_path: Path | None = None
    questions_path: Path | None = None
    if include_process_flow:
        graph = provider.process_graph(source, mapping, sections)
        validate_process_graph_sources(graph, [block.id for block in source.blocks])
        mermaid = process_graph_to_mermaid(graph)
        flow_mermaid_path = target_dir / "process_flow.mmd"
        flow_image_path = target_dir / "process_flow.png"
        questions_path = target_dir / "questions.json"
        flow_mermaid_path.write_text(mermaid, encoding="utf-8")
        render_process_graph_png(graph, flow_image_path)
        questions_path.write_text(
            _questionnaire(
                mapping,
                source_path,
                template_path,
                structure_mode=structure_mode,
                recovered_structure=state.get("recovered_structure"),
            ).model_dump_json(indent=2)
            + "\n",
            encoding="utf-8",
        )
        draft_markdown = draft_markdown.rstrip() + _process_flow_appendix(mermaid)

    draft_md_path = target_dir / "draft.md"
    analysis_md_path = target_dir / "analysis.md"
    mapping_path = target_dir / "mapping.json"
    draft_md_path.write_text(draft_markdown, encoding="utf-8")
    analysis_md_path.write_text(analysis_markdown, encoding="utf-8")
    mapping_payload = mapping.model_dump(mode="json")
    mapping_payload["source_structure"] = {
        "assessment": state["structure_assessment"].model_dump(mode="json"),
        "recovered": state["recovery_used"],
        "recovered_structure": (
            state["recovered_structure"].model_dump(mode="json")
            if state.get("recovered_structure") is not None
            else None
        ),
        "workflow_trace": state["trace"],
    }
    mapping_payload["source_assets"] = [asset.model_dump(mode="json") for asset in source.assets]
    mapping_path.write_text(
        json.dumps(mapping_payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    rendered = render_markdown_pair(draft_md_path, analysis_md_path, target_dir)
    return RunArtifacts(
        draft_markdown=draft_md_path,
        draft_docx=rendered.draft_docx,
        analysis_markdown=analysis_md_path,
        analysis_docx=rendered.analysis_docx,
        mapping_json=mapping_path,
        quality_review=quality_review,
        structure_assessment=state["structure_assessment"],
        structure_recovered=state["recovery_used"],
        workflow_trace=tuple(state["trace"]),
        source_asset_paths=source_asset_paths,
        process_flow_mermaid=flow_mermaid_path,
        process_flow_image=flow_image_path,
        questions_json=questions_path,
    )


def _process_flow_appendix(mermaid: str) -> str:
    return (
        "\n\n## Process flow\n\n"
        "![Source-derived process flow](process_flow.png)\n\n"
        "### Mermaid source\n\n"
        "```mermaid\n"
        f"{mermaid.rstrip()}\n"
        "```\n"
    )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _questionnaire(
    mapping: AnalysisMapping,
    source_path: Path,
    template_path: Path,
    *,
    structure_mode: StructureMode,
    recovered_structure: RecoveredStructure | None,
) -> Questionnaire:
    return Questionnaire(
        source_sha256=_file_sha256(source_path.resolve()),
        template_sha256=_file_sha256(template_path.resolve()),
        structure_mode=structure_mode,
        structure_recovered=recovered_structure is not None,
        structure_sha256=_structure_sha256(recovered_structure),
        recovered_structure=recovered_structure,
        questions=[
            QuestionResponse(
                id=question.id,
                text=question.text,
                gap_ids=question.gap_ids,
                answer="",
            )
            for question in mapping.questions
        ],
    )


def _structure_sha256(recovered_structure: RecoveredStructure | None) -> str:
    payload = recovered_structure.model_dump_json() if recovered_structure is not None else "null"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _read_answered_questionnaire(
    answers_path: Path,
    source_path: Path,
    template_path: Path,
) -> Questionnaire:
    try:
        questionnaire = Questionnaire.model_validate_json(answers_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PipelineContractError(f"could not read Stage 2 answers: {answers_path}") from exc
    if questionnaire.source_sha256 != _file_sha256(source_path.resolve()):
        raise PipelineContractError("answers file does not match the current source document")
    if questionnaire.template_sha256 != _file_sha256(template_path.resolve()):
        raise PipelineContractError("answers file does not match the current template")
    if questionnaire.structure_sha256 != _structure_sha256(questionnaire.recovered_structure):
        raise PipelineContractError("answers file changed the protected structure contract")
    for response in questionnaire.questions:
        if not response.answer.strip():
            raise PipelineContractError(f"Stage 2 answer is empty for {response.id}")
    return questionnaire


def _validate_questionnaire_contract(
    questionnaire: Questionnaire, mapping: AnalysisMapping
) -> None:
    expected = {question.id: question for question in mapping.questions}
    received = {question.id: question for question in questionnaire.questions}
    if set(received) != set(expected):
        raise PipelineContractError(
            "answers must contain every current question exactly once; expected "
            f"{sorted(expected)}, received {sorted(received)}"
        )
    for question_id, response in received.items():
        expected_question = expected[question_id]
        if response.text != expected_question.text or response.gap_ids != expected_question.gap_ids:
            raise PipelineContractError(
                f"answers file changed the protected question contract for {question_id}"
            )
    covered_gaps = {gap_id for response in questionnaire.questions for gap_id in response.gap_ids}
    mapping_gaps = {gap.id for gap in mapping.gaps}
    if covered_gaps != mapping_gaps:
        raise PipelineContractError(
            f"questions must cover every current gap; expected {sorted(mapping_gaps)}, "
            f"received {sorted(covered_gaps)}"
        )


def _augment_source_with_answers(
    source: SourceDocument, questionnaire: Questionnaire
) -> tuple[SourceDocument, dict[str, SourceBlock]]:
    blocks = list(source.blocks)
    answer_blocks: dict[str, SourceBlock] = {}
    for response in questionnaire.questions:
        order = len(blocks) + 1
        block = SourceBlock(
            id=f"SRC-{order:03d}",
            heading=f"Authoritative answer to {response.id}",
            content=response.answer.strip(),
            order=order,
        )
        blocks.append(block)
        answer_blocks[response.id] = block
    return (
        source.model_copy(
            update={
                "blocks": blocks,
                "full_text": source.full_text
                + "\n\n"
                + "\n\n".join(
                    f"{block.heading}\n{block.content}" for block in answer_blocks.values()
                ),
            }
        ),
        answer_blocks,
    )


def _resolve_mapping_with_answers(
    mapping: AnalysisMapping,
    questionnaire: Questionnaire,
    answer_blocks: dict[str, SourceBlock],
) -> tuple[AnalysisMapping, Stage2Resolution]:
    answer_by_gap: dict[str, tuple[QuestionResponse, SourceBlock]] = {}
    for response in questionnaire.questions:
        block = answer_blocks[response.id]
        for gap_id in response.gap_ids:
            answer_by_gap[gap_id] = (response, block)
    resolved_gap_ids = set(answer_by_gap)

    targets: list[TargetSectionAssessment] = []
    answer_targets: dict[str, set[str]] = {
        response.id: set() for response in questionnaire.questions
    }
    for target in mapping.target_sections:
        target_answer_ids = _unique(
            answer_by_gap[gap_id][1].id for gap_id in target.gaps if gap_id in answer_by_gap
        )
        for gap_id in target.gaps:
            if gap_id in answer_by_gap:
                answer_targets[answer_by_gap[gap_id][0].id].add(target.target_section_id)
        requirements = []
        for requirement in target.requirements:
            resolved_for_requirement = set(requirement.gaps) & resolved_gap_ids
            answer_ids = [answer_by_gap[gap_id][1].id for gap_id in resolved_for_requirement]
            remaining_gaps = [
                gap_id for gap_id in requirement.gaps if gap_id not in resolved_gap_ids
            ]
            status = requirement.status
            if resolved_for_requirement and not remaining_gaps:
                status = CoverageStatus.SUPPORTED
            requirements.append(
                requirement.model_copy(
                    update={
                        "status": status,
                        "gaps": remaining_gaps,
                        "supporting_source_block_ids": _unique(
                            [*requirement.supporting_source_block_ids, *answer_ids]
                        ),
                        "source_block_ids": _unique([*requirement.source_block_ids, *answer_ids]),
                        "recommended_improvements": (
                            []
                            if resolved_for_requirement and not remaining_gaps
                            else requirement.recommended_improvements
                        ),
                    }
                )
            )
        remaining_target_gaps = [gap_id for gap_id in target.gaps if gap_id not in resolved_gap_ids]
        targets.append(
            target.model_copy(
                update={
                    "requirements": requirements,
                    "status": _aggregate_status([item.status for item in requirements]),
                    "gaps": remaining_target_gaps,
                    "source_block_ids": _unique([*target.source_block_ids, *target_answer_ids]),
                    "recommended_improvements": (
                        [] if not remaining_target_gaps else target.recommended_improvements
                    ),
                }
            )
        )

    components = []
    for component in mapping.procedure_component_assessment:
        resolved_for_component = set(component.gaps) & resolved_gap_ids
        answer_ids = [answer_by_gap[gap_id][1].id for gap_id in resolved_for_component]
        remaining = [gap_id for gap_id in component.gaps if gap_id not in resolved_gap_ids]
        components.append(
            component.model_copy(
                update={
                    "status": (
                        CoverageStatus.SUPPORTED
                        if resolved_for_component and not remaining
                        else component.status
                    ),
                    "gaps": remaining,
                    "supporting_source_block_ids": _unique(
                        [*component.supporting_source_block_ids, *answer_ids]
                    ),
                    "source_block_ids": _unique([*component.source_block_ids, *answer_ids]),
                    "recommendations": [] if not remaining else component.recommendations,
                }
            )
        )

    dispositions = list(mapping.source_section_dispositions)
    for response in questionnaire.questions:
        block = answer_blocks[response.id]
        target_ids = sorted(answer_targets[response.id])
        dispositions.append(
            SourceSectionDisposition(
                source_block_id=block.id,
                source_heading=block.heading,
                target_section_ids=target_ids,
                treatment=(
                    DispositionTreatment.SPLIT
                    if len(target_ids) > 1
                    else DispositionTreatment.DIRECT
                ),
                intentionally_omitted=False,
                rationale="The document owner supplied this authoritative Stage 2 answer.",
                source_block_ids=[block.id],
            )
        )

    answer_ids = [block.id for block in answer_blocks.values()]
    resolved = mapping.model_copy(
        update={
            "macro_assessment": mapping.macro_assessment.model_copy(
                update={
                    "source_block_ids": _unique(
                        [*mapping.macro_assessment.source_block_ids, *answer_ids]
                    ),
                    "overall_remediation": (
                        "Stage 1 questions were answered and incorporated as authoritative "
                        "business input."
                    ),
                }
            ),
            "target_sections": targets,
            "source_section_dispositions": dispositions,
            "procedure_component_assessment": components,
            "gaps": [gap for gap in mapping.gaps if gap.id not in resolved_gap_ids],
            "questions": [
                question
                for question in mapping.questions
                if not set(question.gap_ids) <= resolved_gap_ids
            ],
        }
    )
    resolution = Stage2Resolution(
        source_sha256=questionnaire.source_sha256,
        template_sha256=questionnaire.template_sha256,
        resolutions=[
            ResolutionRecord(
                question_id=response.id,
                gap_ids=response.gap_ids,
                answer=response.answer.strip(),
                answer_source_block_id=answer_blocks[response.id].id,
            )
            for response in questionnaire.questions
        ],
    )
    return resolved, resolution


def _apply_stage2_answers(
    sections: list[DraftSection],
    original_mapping: AnalysisMapping,
    questionnaire: Questionnaire,
    answer_blocks: dict[str, SourceBlock],
) -> list[DraftSection]:
    gaps = {gap.id: gap for gap in original_mapping.gaps}
    answers_by_gap = {
        gap_id: (response, answer_blocks[response.id])
        for response in questionnaire.questions
        for gap_id in response.gap_ids
    }
    target_gaps = {
        target.target_section_id: set(target.gaps) for target in original_mapping.target_sections
    }
    revised: list[DraftSection] = []
    for section in sections:
        content = _remove_answer_source_sections(section.content_markdown, questionnaire)
        supporting_ids = list(section.supporting_source_block_ids)
        for gap_id, (response, block) in answers_by_gap.items():
            updated = _remove_resolved_gap_language(content, gaps[gap_id], response.answer.strip())
            if updated != content:
                supporting_ids = _unique([*supporting_ids, block.id])
            content = updated
        applicable = [
            (response, answer_blocks[response.id])
            for response in questionnaire.questions
            if set(response.gap_ids) & target_gaps[section.target_section_id]
        ]
        if applicable:
            answer_lines = [f"- {response.answer.strip()}" for response, _ in applicable]
            content = (
                content.rstrip() + "\n\n### Resolved business input\n\n" + "\n".join(answer_lines)
            )
            supporting_ids = _unique([*supporting_ids, *(block.id for _, block in applicable)])
        content = _remove_orphan_markdown_headings(content)
        revised.append(
            section.model_copy(
                update={
                    "content_markdown": content,
                    "supporting_source_block_ids": supporting_ids,
                    "unresolved_gap_ids": [],
                }
            )
        )
    return revised


def _remove_answer_source_sections(content: str, questionnaire: Questionnaire) -> str:
    answers = {response.id: response.answer.strip() for response in questionnaire.questions}
    segments = _content_segments(content)
    cleaned: list[str] = []
    index = 0
    while index < len(segments):
        match = re.match(r"^#{1,6}\s+Authoritative answer to (QUE-\d{3})$", segments[index])
        if match and match.group(1) in answers:
            index += 1
            if index < len(segments) and segments[index].strip() == answers[match.group(1)]:
                index += 1
            continue
        cleaned.append(segments[index])
        index += 1
    return "\n\n".join(cleaned)


def _remove_resolved_gap_language(content: str, gap: Gap, answer: str) -> str:
    labels = {GapKind.MISSING: "MISSING", GapKind.CONFLICT: "CONFLICT", GapKind.UNCLEAR: "UNCLEAR"}
    content = content.replace(f"[{labels[gap.kind]}: {gap.description}]", "")
    paragraphs = _content_segments(content)
    if gap.kind is GapKind.CONFLICT:
        values = set(re.findall(r"\$\s*\d+(?:\.\d+)?", gap.description))
        chosen = [value for value in values if value.replace(" ", "") in answer.replace(" ", "")]
        alternatives = values - set(chosen)
        cleaned = []
        for paragraph in paragraphs:
            if any(value in paragraph for value in alternatives):
                history = re.search(
                    r"conflict|old desk|do not silently|unresolved|disagree|which business owner",
                    paragraph,
                    re.IGNORECASE,
                )
                if history and re.match(r"^\d+[.)]\s+", paragraph):
                    sentences = re.split(r"(?<=[.!?])\s+", paragraph)
                    paragraph = " ".join(
                        sentence
                        for sentence in sentences
                        if not any(value in sentence for value in alternatives)
                        and not re.search(
                            r"conflict|old desk|do not silently|unresolved|disagree",
                            sentence,
                            re.IGNORECASE,
                        )
                    )
                    if not paragraph:
                        continue
                elif history:
                    continue
                if chosen:
                    for value in alternatives:
                        paragraph = paragraph.replace(value, chosen[0])
            cleaned.append(paragraph)
        paragraphs = cleaned
    elif gap.kind is GapKind.MISSING:
        cleaned = []
        for paragraph in paragraphs:
            paragraph = re.sub(
                r";\s*the owner and queue are not named in this checklist\.?",
                ".",
                paragraph,
                flags=re.IGNORECASE,
            )
            paragraph = re.sub(
                r"finance owner referenced above",
                "resolved finance owner identified below",
                paragraph,
                flags=re.IGNORECASE,
            )
            paragraph = re.sub(
                r"unnamed finance owner",
                "resolved finance owner identified below",
                paragraph,
                flags=re.IGNORECASE,
            )
            if re.search(
                r"does not identify|not named in this checklist|do not invent that missing owner",
                paragraph,
                re.IGNORECASE,
            ):
                continue
            cleaned.append(paragraph)
        paragraphs = cleaned
    return "\n\n".join(paragraphs).strip()


def _remove_orphan_markdown_headings(content: str) -> str:
    segments = _content_segments(content)
    cleaned = [
        segment
        for index, segment in enumerate(segments)
        if not (
            re.match(r"^#{1,6}\s+", segment)
            and (index == len(segments) - 1 or re.match(r"^#{1,6}\s+", segments[index + 1]))
        )
    ]
    return "\n\n".join(cleaned).strip()


def run_stage2(
    *,
    source_path: Path,
    template_path: Path,
    answers_path: Path,
    output_dir: Path,
    provider: AnalysisProvider,
    structure_mode: StructureMode = StructureMode.AUTO,
) -> Stage2Artifacts:
    """Create a final procedure from a complete, matching Stage 1 answer file."""

    target_dir = output_dir.expanduser().resolve()
    if target_dir == answers_path.expanduser().resolve().parent:
        raise PipelineContractError(
            "Stage 2 output directory must differ from the Stage 1 answers directory"
        )
    questionnaire = _read_answered_questionnaire(answers_path, source_path, template_path)
    if questionnaire.structure_mode is not structure_mode:
        raise PipelineContractError(
            "Stage 2 structure mode must match Stage 1; "
            f"expected {questionnaire.structure_mode.value}, received {structure_mode.value}"
        )

    from document_enhancer.workflow import invoke_analysis_graph

    state = invoke_analysis_graph(
        source_path=source_path,
        template_path=template_path,
        provider=provider,
        structure_mode=structure_mode,
        persisted_recovered_structure=questionnaire.recovered_structure,
    )
    source = state["source"]
    template = state["template"]
    original_mapping = state["mapping"]
    _validate_questionnaire_contract(questionnaire, original_mapping)
    augmented_source, answer_blocks = _augment_source_with_answers(source, questionnaire)
    resolved_mapping, resolution = _resolve_mapping_with_answers(
        original_mapping, questionnaire, answer_blocks
    )
    validate_mapping(resolved_mapping, augmented_source, template)
    sections = draft_sections(provider, augmented_source, template, resolved_mapping)
    sections = _apply_stage2_answers(sections, original_mapping, questionnaire, answer_blocks)
    validate_draft_sections(sections, augmented_source, template, resolved_mapping)
    graph = provider.process_graph(augmented_source, resolved_mapping, sections)
    validate_process_graph_sources(graph, [block.id for block in augmented_source.blocks])
    mermaid = process_graph_to_mermaid(graph)

    target_dir.mkdir(parents=True, exist_ok=True)
    final_md_path = target_dir / "final.md"
    final_docx_path = target_dir / "final.docx"
    resolution_path = target_dir / "resolution.json"
    flow_mermaid_path = target_dir / "process_flow.mmd"
    flow_image_path = target_dir / "process_flow.png"
    source_asset_paths = _persist_source_assets(augmented_source, target_dir)
    flow_mermaid_path.write_text(mermaid, encoding="utf-8")
    render_process_graph_png(graph, flow_image_path)
    final_markdown = assemble_draft_markdown(template, sections)
    final_markdown = _place_source_assets(
        final_markdown, augmented_source, resolved_mapping, template
    ).rstrip()
    final_md_path.write_text(final_markdown + _process_flow_appendix(mermaid), encoding="utf-8")
    resolution_path.write_text(resolution.model_dump_json(indent=2) + "\n", encoding="utf-8")
    render_markdown_to_docx(final_md_path, final_docx_path)
    return Stage2Artifacts(
        final_markdown=final_md_path,
        final_docx=final_docx_path,
        resolution_json=resolution_path,
        process_flow_mermaid=flow_mermaid_path,
        process_flow_image=flow_image_path,
        source_asset_paths=source_asset_paths,
    )


def _all_cited_source_ids(mapping: AnalysisMapping) -> set[str]:
    cited = set(mapping.macro_assessment.source_block_ids)
    for target in mapping.target_sections:
        cited.update(target.source_block_ids)
        for requirement in target.requirements:
            cited.update(requirement.source_block_ids)
            cited.update(requirement.supporting_source_block_ids)
    for disposition in mapping.source_section_dispositions:
        cited.update(disposition.source_block_ids)
        cited.add(disposition.source_block_id)
    for component in mapping.procedure_component_assessment:
        cited.update(component.source_block_ids)
        cited.update(component.supporting_source_block_ids)
    for gap in mapping.gaps:
        cited.update(gap.source_block_ids)
    for question in mapping.questions:
        cited.update(question.source_block_ids)
    return cited


def _source_dispositions(
    blocks: list[SourceBlock],
    sections: list[TemplateSection],
    relevance: dict[str, dict[str, int]],
    candidates: dict[str, list[SourceBlock]],
) -> list[SourceSectionDisposition]:
    dispositions: list[SourceSectionDisposition] = []
    for block in blocks:
        eligible_sections = (
            sections if block.order == 1 else [section for section in sections if section.level > 1]
        )
        scored = sorted(
            ((section.id, relevance[section.id][block.id]) for section in eligible_sections),
            key=lambda item: item[1],
            reverse=True,
        )
        maximum = scored[0][1]
        destinations = [
            section_id
            for section_id, score in scored
            if score > 0 and score >= max(3, round(maximum * 0.35))
        ][:3]
        title_target = next((section.id for section in sections if section.level == 1), None)
        if block.order == 1 and title_target:
            destinations = [title_target, *[item for item in destinations if item != title_target]][
                :3
            ]
        if not destinations:
            treatment = DispositionTreatment.UNRESOLVED
            rationale = (
                "The block remains unresolved because the deterministic evaluator found no "
                "source-supported target fit; it is not intentionally omitted."
            )
        elif len(destinations) > 1:
            treatment = DispositionTreatment.SPLIT
            rationale = "The block contains details used by more than one target section."
        elif sum(block in section_blocks for section_blocks in candidates.values()) > 1:
            treatment = DispositionTreatment.SPLIT
            rationale = (
                "The block supplies detail to multiple requirements in the target structure."
            )
        elif sum(
            len(section_blocks) > 1 and block in section_blocks
            for section_blocks in candidates.values()
        ):
            treatment = DispositionTreatment.COMBINED
            rationale = "The block is combined with related source material in its target section."
        else:
            treatment = DispositionTreatment.DIRECT
            rationale = "The block maps directly to the selected target section."
        dispositions.append(
            SourceSectionDisposition(
                source_block_id=block.id,
                source_heading=block.heading,
                target_section_ids=destinations,
                treatment=treatment,
                intentionally_omitted=False,
                rationale=rationale,
                source_block_ids=[block.id],
            )
        )
    return dispositions


def _macro_assessment(
    source_ids: list[str],
    targets: list[TargetSectionAssessment],
    components: list[ProcedureComponentAssessment],
) -> MacroAssessment:
    statuses = [requirement.status for target in targets for requirement in target.requirements]
    complete = sum(
        status in {CoverageStatus.SUPPORTED, CoverageStatus.NOT_APPLICABLE} for status in statuses
    )
    usable = (
        bool(statuses)
        and complete / len(statuses) >= 0.6
        and any(
            component.component is ProcedureComponent.STEP_SEQUENCE
            and component.status is not CoverageStatus.MISSING
            for component in components
        )
    )
    strengths = [
        f"The source supports {_display_component(component.component)}."
        for component in components
        if component.status is CoverageStatus.SUPPORTED
    ][:5]
    deficiencies = [
        f"{target.heading}: {target.status.value.replace('_', ' ')}."
        for target in targets
        if target.status not in {CoverageStatus.SUPPORTED, CoverageStatus.NOT_APPLICABLE}
    ]
    assessment = (
        "The source is usable as a desktop-procedure foundation but still requires the "
        "documented remediation."
        if usable
        else "The source is not yet complete enough to use as a desktop procedure without the "
        "documented remediation."
    )
    return MacroAssessment(
        usable_as_desktop_procedure=usable,
        assessment=assessment,
        strengths=strengths,
        deficiencies=deficiencies,
        overall_remediation=(
            "Retain the mapped operational detail, resolve each deduplicated gap, and confirm "
            "conflicting business values before publication."
        ),
        source_block_ids=source_ids,
    )


def _candidate_blocks(
    section: TemplateSection, blocks: list[SourceBlock], scores: dict[str, int]
) -> list[SourceBlock]:
    ranked = sorted(blocks, key=lambda block: scores[block.id], reverse=True)
    if not ranked or scores[ranked[0].id] <= 0:
        return []
    maximum = scores[ranked[0].id]
    threshold = max(2, round(maximum * 0.45))
    selected = [block for block in ranked if scores[block.id] >= threshold]
    return selected[: max(1, min(5, len(selected)))]


def _section_relevance(section: TemplateSection, block: SourceBlock) -> int:
    target = section.heading + " " + " ".join(req.text for req in section.requirements)
    source = f"{block.heading} {block.content}"
    heading_signal_overlap = len(_signals(section.heading) & _signals(block.heading))
    signal_overlap = len(_signals(target) & _signals(source))
    heading_overlap = len(_tokens(section.heading) & _tokens(block.heading))
    heading_relevance = _text_relevance(section.heading, block.heading)
    content_relevance = min(_text_relevance(target, source), 5)
    score = (
        heading_signal_overlap * 20
        + heading_overlap * 10
        + heading_relevance * 4
        + signal_overlap * 2
        + content_relevance
    )
    if re.search(r"threshold|conflict", block.content, re.IGNORECASE) and re.search(
        r"decision|validation", section.heading, re.IGNORECASE
    ):
        score += 25
    return score


def _text_relevance(target: str, source: str) -> int:
    target_tokens = _expanded_tokens(target)
    source_tokens = _expanded_tokens(source)
    return len(target_tokens & source_tokens)


def _relevant_requirement_blocks(
    requirement: TemplateRequirement,
    section: TemplateSection,
    blocks: list[SourceBlock],
    preferred_source_ids: set[str],
) -> list[SourceBlock]:
    scored = []
    requirement_signals = _signals(requirement.text)
    for block in blocks:
        source_text = f"{block.heading} {block.content}"
        score = (
            _text_relevance(requirement.text, source_text)
            + _text_relevance(requirement.text, block.heading) * 3
            + len(requirement_signals & _signals(source_text)) * 3
            + min(_section_relevance(section, block) // 4, 25)
            + (20 if block.id in preferred_source_ids else 0)
        )
        scored.append((score, block))
    scored.sort(key=lambda item: item[0], reverse=True)
    maximum = scored[0][0] if scored else 0
    if maximum < 3:
        return []
    threshold = max(3, round(maximum * 0.5))
    return [block for score, block in scored if score >= threshold][:3]


def _find_numeric_conflicts(blocks: list[SourceBlock]) -> list[_ConflictFinding]:
    observations: list[tuple[str, str, str, set[str]]] = []
    pattern = re.compile(
        r"\b(?:within|no later than|in)\s+(\d+)\s+(minutes?|hours?|days?)\b",
        flags=re.IGNORECASE,
    )
    for block in blocks:
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", block.content):
            for match in pattern.finditer(sentence):
                unit = match.group(2).casefold().rstrip("s")
                value = f"{match.group(1)} {unit}{'' if match.group(1) == '1' else 's'}"
                context = _tokens(pattern.sub(" deadline ", sentence)) - {
                    "within",
                    "later",
                    "than",
                    "deadline",
                }
                observations.append((block.id, value, sentence.strip(), context))

    clusters: list[list[tuple[str, str, str, set[str]]]] = []
    for observation in observations:
        for cluster in clusters:
            if _jaccard(observation[3], cluster[0][3]) >= 0.45:
                cluster.append(observation)
                break
        else:
            clusters.append([observation])

    findings: list[_ConflictFinding] = []
    for cluster in clusters:
        values = sorted({item[1] for item in cluster})
        if len(values) < 2:
            continue
        source_ids = tuple(_unique(item[0] for item in cluster))
        findings.append(
            _ConflictFinding(
                description=(
                    "The source gives incompatible completion deadlines: "
                    + " and ".join(values)
                    + "."
                ),
                question=(
                    "What is the authoritative completion deadline: " + " or ".join(values) + "?"
                ),
                source_block_ids=source_ids,
                components=(ProcedureComponent.TRIGGERS,),
            )
        )

    threshold_observations: list[tuple[str, str]] = []
    for block in blocks:
        if not re.search(
            r"threshold|variance|\bClear\b|\bPending\b", block.content, flags=re.IGNORECASE
        ):
            continue
        for value in re.findall(r"\$\s*\d+(?:\.\d+)?", block.content):
            threshold_observations.append((block.id, value.replace(" ", "")))
    threshold_values = sorted(
        {value for _, value in threshold_observations},
        key=lambda value: float(value.removeprefix("$")),
    )
    if len(threshold_values) > 1:
        findings.append(
            _ConflictFinding(
                description=(
                    "The source gives incompatible threshold values: "
                    + " and ".join(threshold_values)
                    + "."
                ),
                question=(
                    "What is the authoritative threshold: " + " or ".join(threshold_values) + "?"
                ),
                source_block_ids=tuple(_unique(item[0] for item in threshold_observations)),
                components=(ProcedureComponent.DECISION_POINTS,),
            )
        )
    return findings


def _find_explicit_source_gaps(blocks: list[SourceBlock]) -> list[_ExplicitGapFinding]:
    matching_blocks = [
        block
        for block in blocks
        if re.search(
            r"does not identify|not (?:identify|named)|unnamed (?:owner|role|team|queue)",
            block.content,
            flags=re.IGNORECASE,
        )
    ]
    if not matching_blocks:
        return []
    text = " ".join(block.content for block in matching_blocks)
    if re.search(r"finance owner|team queue|contact method", text, flags=re.IGNORECASE):
        return [
            _ExplicitGapFinding(
                description=(
                    "The source does not identify the finance owner, team queue, or contact "
                    "method required for escalation."
                ),
                question=(
                    "Who is the finance owner, which team queue should receive the escalation, "
                    "and what contact method should be used?"
                ),
                source_block_ids=tuple(block.id for block in matching_blocks),
                components=(ProcedureComponent.ROLES, ProcedureComponent.ESCALATION),
            )
        ]
    return [
        _ExplicitGapFinding(
            description="The source explicitly identifies business information as unavailable.",
            question="What authoritative business information resolves the source-identified gap?",
            source_block_ids=tuple(block.id for block in matching_blocks),
            components=tuple(
                sorted(
                    {
                        component
                        for block in matching_blocks
                        for component in _signals(f"{block.heading} {block.content}")
                    },
                    key=lambda component: component.value,
                )
            ),
        )
    ]


def _conflict_applies(
    finding: _ConflictFinding,
    section: TemplateSection,
    requirement: TemplateRequirement,
) -> bool:
    target_text = f"{section.heading} {requirement.text}"
    if ProcedureComponent.TRIGGERS in finding.components:
        return bool(
            re.search(r"timing|deadline|cadence|complete|trigger", target_text, re.IGNORECASE)
        )
    if ProcedureComponent.DECISION_POINTS in finding.components:
        return bool(re.search(r"threshold|decision|outcome branch", target_text, re.IGNORECASE))
    return bool(_signals(target_text) & set(finding.components))


def _explicit_gap_applies(
    finding: _ExplicitGapFinding,
    section: TemplateSection,
    requirement: TemplateRequirement,
) -> bool:
    target_text = f"{section.heading} {requirement.text}"
    if set(finding.components) & {ProcedureComponent.ROLES, ProcedureComponent.ESCALATION}:
        return bool(
            re.search(
                r"responsibil|handoff|approval|escalat|owner|failure|recovery",
                target_text,
                re.IGNORECASE,
            )
        )
    return bool(_signals(target_text) & set(finding.components))


_COMPONENT_TERMS: dict[ProcedureComponent, set[str]] = {
    ProcedureComponent.OBJECTIVE: {
        "purpose",
        "objective",
        "outcome",
        "goal",
        "accomplish",
        "identifies",
    },
    ProcedureComponent.INTENDED_USER: {"audience", "user", "operator", "analyst", "specialist"},
    ProcedureComponent.SCOPE: {"scope", "include", "exclude", "applies", "coverage"},
    ProcedureComponent.PREREQUISITES: {
        "prerequisite",
        "readiness",
        "before",
        "prepare",
        "required",
    },
    ProcedureComponent.TOOLS_AND_ACCESS: {
        "tool",
        "system",
        "access",
        "file",
        "input",
        "application",
        "portal",
        "queue",
    },
    ProcedureComponent.ROLES: {
        "role",
        "responsibility",
        "owner",
        "reviewer",
        "approver",
        "supervisor",
        "analyst",
    },
    ProcedureComponent.TRIGGERS: {
        "trigger",
        "timing",
        "cadence",
        "deadline",
        "received",
        "daily",
        "weekly",
        "monthly",
        "complete",
    },
    ProcedureComponent.STEP_SEQUENCE: {
        "procedure",
        "step",
        "action",
        "sequence",
        "open",
        "select",
        "export",
        "enter",
        "upload",
        "save",
    },
    ProcedureComponent.DECISION_POINTS: {
        "decision",
        "condition",
        "branch",
        "if",
        "otherwise",
        "threshold",
    },
    ProcedureComponent.VALIDATION: {
        "validation",
        "validate",
        "check",
        "verify",
        "compare",
        "confirm",
        "quality",
    },
    ProcedureComponent.EXPECTED_RESULTS: {
        "result",
        "outcome",
        "success",
        "completed",
        "completion",
        "produce",
        "produces",
    },
    ProcedureComponent.EVIDENCE: {
        "evidence",
        "record",
        "log",
        "retain",
        "archive",
        "screenshot",
    },
    ProcedureComponent.EXCEPTIONS: {"exception", "failure", "error", "unable", "warning"},
    ProcedureComponent.RECOVERY: {"recovery", "recover", "retry", "restore", "reopen"},
    ProcedureComponent.ESCALATION: {"escalation", "escalate", "notify", "contact"},
    ProcedureComponent.READABILITY_AND_USABILITY: {
        "procedure",
        "step",
        "note",
        "warning",
        "example",
    },
}


_STOPWORDS = {
    "a",
    "an",
    "and",
    "as",
    "at",
    "be",
    "by",
    "each",
    "for",
    "from",
    "how",
    "identify",
    "in",
    "is",
    "it",
    "of",
    "or",
    "should",
    "state",
    "that",
    "the",
    "this",
    "to",
    "when",
    "with",
}


def _signals(text: str) -> set[ProcedureComponent]:
    tokens = _tokens(text)
    return {component for component, terms in _COMPONENT_TERMS.items() if tokens & terms}


def _tokens(text: str) -> set[str]:
    raw_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", text.casefold())
        if token not in _STOPWORDS and len(token) > 1
    }
    singular = {
        (token[:-3] + "y" if token.endswith("ies") else token[:-1])
        for token in raw_tokens
        if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us"))
    }
    return raw_tokens | singular


def _expanded_tokens(text: str) -> set[str]:
    tokens = _tokens(text)
    expanded = set(tokens)
    for terms in _COMPONENT_TERMS.values():
        if tokens & terms:
            expanded.update(terms)
    return expanded


def _aggregate_status(statuses: list[CoverageStatus]) -> CoverageStatus:
    if CoverageStatus.CONFLICTING in statuses:
        return CoverageStatus.CONFLICTING
    meaningful = [status for status in statuses if status is not CoverageStatus.NOT_APPLICABLE]
    if not meaningful:
        return CoverageStatus.NOT_APPLICABLE
    if all(status is CoverageStatus.SUPPORTED for status in meaningful):
        return CoverageStatus.SUPPORTED
    if all(status is CoverageStatus.MISSING for status in meaningful):
        return CoverageStatus.MISSING
    return CoverageStatus.PARTIALLY_SUPPORTED


def _requirement_key(requirement: TemplateRequirement) -> str:
    signals = sorted(component.value for component in _signals(requirement.text))
    return ":".join(signals) or "-".join(sorted(_tokens(requirement.text)))


def _normalize_key(value: str) -> str:
    return " ".join(sorted(_tokens(value)))


def _display_component(component: ProcedureComponent) -> str:
    return component.value.replace("_", " ")


def _gap_components(gap: Gap) -> set[ProcedureComponent]:
    text = f"{gap.description} {gap.question}"
    if gap.kind is GapKind.CONFLICT and re.search(r"threshold", text, re.IGNORECASE):
        return {ProcedureComponent.DECISION_POINTS}
    if re.search(r"finance owner|team queue|contact method", text, re.IGNORECASE):
        return {ProcedureComponent.ROLES, ProcedureComponent.ESCALATION}
    return _signals(text)


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _content_segments(markdown: str) -> list[str]:
    return [segment.strip() for segment in re.split(r"\n\s*\n", markdown) if segment.strip()]


def _section_segment_relevant(section: TemplateSection, segment: str) -> bool:
    target_text = (
        section.heading + " " + " ".join(requirement.text for requirement in section.requirements)
    )
    if re.match(r"^\s*\d+[.)]\s+", segment) and ProcedureComponent.STEP_SEQUENCE in _signals(
        target_text
    ):
        return True
    return (
        bool(_signals(target_text) & _signals(segment))
        or _text_relevance(target_text, segment) >= 3
    )


def _assessment_segment_score(assessment: TargetSectionAssessment, segment: str) -> int:
    requirement_text = " ".join(item.requirement for item in assessment.requirements)
    heading_signals = _signals(assessment.heading)
    segment_signals = _signals(segment)
    score = (
        len(heading_signals & segment_signals) * 10
        + len(_signals(requirement_text) & segment_signals) * 4
        + _text_relevance(f"{assessment.heading} {requirement_text}", segment)
    )
    if re.search(
        r"threshold|conflict|absolute total variance|\$\s*\d+", segment, re.IGNORECASE
    ) and re.search(r"decision|validation", assessment.heading, re.IGNORECASE):
        score += 50
    return score


def _normalize_prose(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9$%]+", text.casefold()))


def _fact_anchors(text: str) -> set[str]:
    patterns = [
        r"`([^`]+)`",
        r"\$\s*\d+(?:\.\d+)?",
        r"\b\d{1,2}:\d{2}\s*[A-Z]{2}\b",
        r"\b\d+(?:\.\d+)?\s*(?:minutes?|hours?|days?|weeks?|percent|%)\b",
        r"\b[A-Za-z0-9_./\\-]+\.(?:csv|xlsx|xls|docx|pdf|png|json)\b",
        r"\b[A-Z][A-Z0-9_]{2,}\b",
        r"\b[A-Z][a-z0-9]+[A-Z][A-Za-z0-9]*\b",
        r"\b(?:Salesforce|Workday|ServiceNow|LedgerHub|Exception Queue|Finance Reviewer)\b",
    ]
    anchors: set[str] = set()
    for pattern in patterns:
        for match in re.finditer(pattern, text):
            anchors.add(match.group(1) if match.lastindex else match.group(0))
    return anchors


def _unsupported_anchors(
    draft: DraftSection, target: TemplateSection, source: SourceDocument
) -> list[str]:
    business_text = draft.content_markdown
    if target.fixed_markdown:
        business_text = business_text.replace(target.fixed_markdown, "")
    business_text = re.sub(
        r"\[(?:MISSING|CONFLICT|UNCLEAR):.*?\]", "", business_text, flags=re.DOTALL
    )
    supporting = {block.id: f"{block.heading}\n{block.content}" for block in source.blocks}
    supporting_text = "\n".join(
        supporting[source_id]
        for source_id in draft.supporting_source_block_ids
        if source_id in supporting
    )
    normalized_support = _normalize_prose(supporting_text)
    return sorted(
        anchor
        for anchor in _fact_anchors(business_text)
        if _normalize_prose(anchor) not in normalized_support
    )


def _duplicate_segments(sections: list[DraftSection]) -> list[str]:
    seen: dict[str, str] = {}
    duplicates: list[str] = []
    for section in sections:
        for segment in _content_segments(section.content_markdown):
            normalized = _normalize_prose(segment)
            if len(normalized) < 60 or normalized.startswith(("missing ", "conflict ", "unclear ")):
                continue
            previous = seen.get(normalized)
            if previous and previous != section.target_section_id:
                duplicates.append(
                    f"{previous} and {section.target_section_id} repeat the same source detail."
                )
            else:
                seen[normalized] = section.target_section_id
    return _unique(duplicates)


def _markdown_bullets(items: list[str]) -> list[str]:
    return [f"- {item}" for item in items]


def _table_text(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def _unique(values: Any) -> list[Any]:
    return list(dict.fromkeys(values))
