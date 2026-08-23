import json
from pathlib import Path

from typer.testing import CliRunner

from document_enhancer.batch import run_batch
from document_enhancer.cli import app
from document_enhancer.models import BatchStatus
from document_enhancer.pipeline import DeterministicProvider

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "desktop_procedure.md"


def _batch_sources(tmp_path: Path) -> Path:
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    (source_dir / "structured.md").write_text(
        """# Queue Review

## Purpose
Review queued requests before approval.

## Procedure steps
1. Open Atlas.
2. Select the oldest request.
3. Save the confirmation identifier.

## Evidence
Record the identifier in review_log.csv.
""",
        encoding="utf-8",
    )
    (source_dir / "unstructured.md").write_text(
        """The analyst reviews queued requests before approval.

Atlas access and approved_accounts.csv are required.

Every Tuesday the analyst opens the oldest request.

1. Compare the account with approved_accounts.csv.

2. Save the confirmation identifier in review_log.csv.
""",
        encoding="utf-8",
    )
    (source_dir / "broken.docx").write_bytes(b"not-a-docx")
    return source_dir


def test_batch_continues_after_failure_and_writes_manifest(tmp_path: Path) -> None:
    source_dir = _batch_sources(tmp_path)
    output_dir = tmp_path / "batch"

    manifest = run_batch(
        input_dir=source_dir,
        template_path=TEMPLATE,
        output_dir=output_dir,
        provider=DeterministicProvider(),
    )

    assert len(manifest.documents) == 3
    assert manifest.failed_count == 1
    assert manifest.completed_count + manifest.questions_count == 2
    assert manifest.recovered_count >= 1
    failed = next(item for item in manifest.documents if item.status is BatchStatus.FAILED)
    assert failed.source_name == "broken.docx"
    assert "could not read DOCX" in (failed.error or "")
    completed = [item for item in manifest.documents if item.status is not BatchStatus.FAILED]
    assert all((item.output_dir / "draft.docx").is_file() for item in completed)
    payload = json.loads((output_dir / "batch_manifest.json").read_text(encoding="utf-8"))
    assert len(payload["documents"]) == 3
    assert payload["failed_count"] == 1


def test_batch_cli_reports_partial_failure_after_processing_all_sources(tmp_path: Path) -> None:
    source_dir = _batch_sources(tmp_path)
    output_dir = tmp_path / "batch"

    result = CliRunner().invoke(
        app,
        [
            "batch",
            "--input-dir",
            str(source_dir),
            "--template",
            str(TEMPLATE),
            "--output-dir",
            str(output_dir),
            "--provider",
            "fake",
        ],
    )

    assert result.exit_code == 3
    assert "failed: 1" in result.output
    assert (output_dir / "batch_manifest.json").is_file()
    assert len(json.loads((output_dir / "batch_manifest.json").read_text())["documents"]) == 3
