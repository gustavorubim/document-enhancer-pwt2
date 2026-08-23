"""Thin, sequential batch orchestration around the single-document LangGraph kernel."""

from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from document_enhancer.models import (
    BatchDocumentResult,
    BatchManifest,
    BatchStatus,
    StructureMode,
)
from document_enhancer.pipeline import AnalysisProvider, run_enhancement

SUPPORTED_SUFFIXES = frozenset({".docx", ".md", ".markdown"})


def run_batch(
    *,
    input_dir: Path,
    template_path: Path,
    output_dir: Path,
    provider: AnalysisProvider,
    structure_mode: StructureMode = StructureMode.AUTO,
) -> BatchManifest:
    """Transform each supported document independently and always write a summary manifest."""

    source_dir = input_dir.expanduser().resolve()
    template = template_path.expanduser().resolve()
    target_dir = output_dir.expanduser().resolve()
    if not source_dir.is_dir():
        raise ValueError(f"batch input directory does not exist: {source_dir}")
    if not template.is_file():
        raise ValueError(f"batch template does not exist: {template}")
    sources = sorted(
        path
        for path in source_dir.iterdir()
        if path.is_file() and path.suffix.casefold() in SUPPORTED_SUFFIXES
    )
    if not sources:
        raise ValueError(f"batch input directory contains no supported documents: {source_dir}")
    target_dir.mkdir(parents=True, exist_ok=True)
    template_sha256 = hashlib.sha256(template.read_bytes()).hexdigest()
    results: list[BatchDocumentResult] = []
    used_slugs: set[str] = set()
    for source in sources:
        slug = _unique_slug(source.stem, used_slugs)
        document_output = target_dir / slug
        started = time.perf_counter()
        try:
            artifacts = run_enhancement(
                source_path=source,
                template_path=template,
                output_dir=document_output,
                provider=provider,
                include_process_flow=True,
                structure_mode=structure_mode,
            )
            questions = json.loads(artifacts.questions_json.read_text(encoding="utf-8"))[
                "questions"
            ]
            status = BatchStatus.COMPLETED_WITH_QUESTIONS if questions else BatchStatus.COMPLETED
            result = BatchDocumentResult(
                source_name=source.name,
                source_path=source,
                output_dir=document_output,
                status=status,
                question_count=len(questions),
                screenshot_count=len(artifacts.source_asset_paths),
                structure_score=artifacts.structure_assessment.score,
                structure_recovered=artifacts.structure_recovered,
                duration_seconds=round(time.perf_counter() - started, 6),
            )
        except Exception as exc:  # each source is an intentional failure boundary
            result = BatchDocumentResult(
                source_name=source.name,
                source_path=source,
                output_dir=document_output,
                status=BatchStatus.FAILED,
                question_count=0,
                screenshot_count=0,
                duration_seconds=round(time.perf_counter() - started, 6),
                error=f"{type(exc).__name__}: {str(exc)[:1000]}",
            )
        results.append(result)
        _write_manifest(
            target_dir=target_dir,
            input_dir=source_dir,
            template_path=template,
            template_sha256=template_sha256,
            documents=results,
        )
    return _manifest(
        input_dir=source_dir,
        template_path=template,
        template_sha256=template_sha256,
        documents=results,
    )


def _unique_slug(stem: str, used: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", stem.casefold()).strip("-") or "document"
    slug = base
    suffix = 2
    while slug in used:
        slug = f"{base}-{suffix}"
        suffix += 1
    used.add(slug)
    return slug


def _manifest(
    *,
    input_dir: Path,
    template_path: Path,
    template_sha256: str,
    documents: list[BatchDocumentResult],
) -> BatchManifest:
    return BatchManifest(
        input_dir=input_dir,
        template_path=template_path,
        template_sha256=template_sha256,
        documents=documents,
        completed_count=sum(item.status is BatchStatus.COMPLETED for item in documents),
        questions_count=sum(
            item.status is BatchStatus.COMPLETED_WITH_QUESTIONS for item in documents
        ),
        failed_count=sum(item.status is BatchStatus.FAILED for item in documents),
        screenshot_count=sum(item.screenshot_count for item in documents),
        recovered_count=sum(item.structure_recovered for item in documents),
        total_duration_seconds=round(sum(item.duration_seconds for item in documents), 6),
    )


def _write_manifest(
    *,
    target_dir: Path,
    input_dir: Path,
    template_path: Path,
    template_sha256: str,
    documents: list[BatchDocumentResult],
) -> None:
    manifest = _manifest(
        input_dir=input_dir,
        template_path=template_path,
        template_sha256=template_sha256,
        documents=documents,
    )
    (target_dir / "batch_manifest.json").write_text(
        manifest.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
