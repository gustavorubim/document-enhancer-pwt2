"""Lean LangGraph orchestration for one bounded document-authoring pass."""

from __future__ import annotations

import operator
import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph import END, START, StateGraph

from document_enhancer.ingest import ingest_source
from document_enhancer.models import (
    AnalysisMapping,
    DraftSection,
    ParsedTemplate,
    QualityReview,
    RecoveredStructure,
    SourceDocument,
    StructureAssessment,
    StructureMode,
)
from document_enhancer.template import parse_template


class AuthoringState(TypedDict, total=False):
    source_path: Path
    template_path: Path
    provider: Any
    structure_mode: StructureMode
    source: SourceDocument
    template: ParsedTemplate
    structure_assessment: StructureAssessment
    recovered_structure: RecoveredStructure
    persisted_recovered_structure: RecoveredStructure
    mapping: AnalysisMapping
    sections: list[DraftSection]
    quality_review: QualityReview
    recovery_used: bool
    trace: Annotated[list[str], operator.add]


_GENERIC_HEADING = re.compile(r"^(?:Source content \d+|Preamble|Untitled|Document)$", re.I)


def assess_structure(source: SourceDocument) -> StructureAssessment:
    """Score whether deterministic parsing produced a useful document outline."""

    block_count = len(source.blocks)
    generic = sum(
        bool(_GENERIC_HEADING.fullmatch(block.heading.strip())) for block in source.blocks
    )
    meaningful = sum(
        not _GENERIC_HEADING.fullmatch(block.heading.strip())
        and block.heading.strip().casefold() != source.title.strip().casefold()
        for block in source.blocks
    )
    longest = max(len(block.content) for block in source.blocks)
    score = 0.25 + 0.55 * (meaningful / block_count)
    if block_count >= 3:
        score += 0.10
    if longest <= 2500:
        score += 0.10
    elif longest > 6000:
        score -= 0.20
    if block_count == 1:
        score -= 0.20
    score = max(0.0, min(1.0, round(score, 3)))
    reasons = []
    if generic:
        reasons.append(f"{generic} block headings are generic parser labels")
    if meaningful == 0:
        reasons.append("no meaningful section headings were detected")
    if block_count == 1:
        reasons.append("the complete document collapsed into one source block")
    if longest > 6000:
        reasons.append("at least one source block is too large for reliable section mapping")
    return StructureAssessment(
        score=score,
        needs_recovery=score < 0.60,
        reasons=reasons,
        block_count=block_count,
        meaningful_heading_count=meaningful,
        generic_heading_count=generic,
        longest_block_characters=longest,
    )


def apply_recovered_structure(
    source: SourceDocument,
    assessment: StructureAssessment,
    recovered: RecoveredStructure,
) -> SourceDocument:
    """Promote headings only when every existing block ID is covered once and in order."""

    expected_ids = [block.id for block in source.blocks]
    recovered_ids = [
        source_id for section in recovered.sections for source_id in section.source_block_ids
    ]
    if recovered_ids != expected_ids:
        raise ValueError(
            "recovered structure must cover every source block exactly once in original order; "
            f"expected {expected_ids}, received {recovered_ids}"
        )
    heading_by_id = {
        source_id: section.heading
        for section in recovered.sections
        for source_id in section.source_block_ids
    }
    blocks = [
        block.model_copy(update={"heading": heading_by_id[block.id]}) for block in source.blocks
    ]
    return source.model_copy(
        update={
            "blocks": blocks,
            "structure_assessment": assessment,
            "structure_recovered": True,
        }
    )


def _load_inputs(state: AuthoringState) -> AuthoringState:
    source = ingest_source(state["source_path"])
    template = parse_template(state["template_path"])
    assessment = assess_structure(source)
    source = source.model_copy(update={"structure_assessment": assessment})
    return {
        "source": source,
        "template": template,
        "structure_assessment": assessment,
        "recovery_used": False,
        "trace": ["load_inputs"],
    }


def _route_structure(state: AuthoringState) -> Literal["recover_structure", "analyze"]:
    mode = state.get("structure_mode", StructureMode.AUTO)
    if mode is StructureMode.ALWAYS:
        return "recover_structure"
    if mode is StructureMode.NEVER:
        return "analyze"
    return "recover_structure" if state["structure_assessment"].needs_recovery else "analyze"


def _recover_structure(state: AuthoringState) -> AuthoringState:
    recovered = state.get("persisted_recovered_structure")
    if recovered is None:
        recovered = state["provider"].recover_structure(
            state["source"], state["structure_assessment"]
        )
    source = apply_recovered_structure(state["source"], state["structure_assessment"], recovered)
    return {
        "source": source,
        "recovered_structure": recovered,
        "recovery_used": True,
        "trace": ["recover_structure"],
    }


def _analyze(state: AuthoringState) -> AuthoringState:
    from document_enhancer.pipeline import validate_mapping

    mapping = state["provider"].analyze(state["source"], state["template"])
    validate_mapping(mapping, state["source"], state["template"])
    return {"mapping": mapping, "trace": ["analyze"]}


def _draft(state: AuthoringState) -> AuthoringState:
    from document_enhancer.pipeline import draft_sections

    sections = draft_sections(
        state["provider"], state["source"], state["template"], state["mapping"]
    )
    return {"sections": sections, "trace": ["draft"]}


def _review(state: AuthoringState) -> AuthoringState:
    from document_enhancer.pipeline import review_sections

    sections, review = review_sections(
        state["provider"],
        state["source"],
        state["template"],
        state["mapping"],
        state["sections"],
    )
    return {"sections": sections, "quality_review": review, "trace": ["review"]}


@lru_cache(maxsize=1)
def build_authoring_graph():
    builder = StateGraph(AuthoringState)
    builder.add_node("load_inputs", _load_inputs)
    builder.add_node("recover_structure", _recover_structure)
    builder.add_node("analyze", _analyze)
    builder.add_node("draft", _draft)
    builder.add_node("review", _review)
    builder.add_edge(START, "load_inputs")
    builder.add_conditional_edges("load_inputs", _route_structure)
    builder.add_edge("recover_structure", "analyze")
    builder.add_edge("analyze", "draft")
    builder.add_edge("draft", "review")
    builder.add_edge("review", END)
    return builder.compile(name="document-enhancer-authoring")


@lru_cache(maxsize=1)
def build_analysis_graph():
    builder = StateGraph(AuthoringState)
    builder.add_node("load_inputs", _load_inputs)
    builder.add_node("recover_structure", _recover_structure)
    builder.add_node("analyze", _analyze)
    builder.add_edge(START, "load_inputs")
    builder.add_conditional_edges("load_inputs", _route_structure)
    builder.add_edge("recover_structure", "analyze")
    builder.add_edge("analyze", END)
    return builder.compile(name="document-enhancer-analysis")


def invoke_authoring_graph(
    *,
    source_path: Path,
    template_path: Path,
    provider: Any,
    structure_mode: StructureMode = StructureMode.AUTO,
) -> AuthoringState:
    return build_authoring_graph().invoke(
        {
            "source_path": source_path,
            "template_path": template_path,
            "provider": provider,
            "structure_mode": structure_mode,
            "trace": [],
        }
    )


def invoke_analysis_graph(
    *,
    source_path: Path,
    template_path: Path,
    provider: Any,
    structure_mode: StructureMode = StructureMode.AUTO,
    persisted_recovered_structure: RecoveredStructure | None = None,
) -> AuthoringState:
    initial_state: AuthoringState = {
        "source_path": source_path,
        "template_path": template_path,
        "provider": provider,
        "structure_mode": structure_mode,
        "trace": [],
    }
    if persisted_recovered_structure is not None:
        initial_state["persisted_recovered_structure"] = persisted_recovered_structure
    return build_analysis_graph().invoke(initial_state)
