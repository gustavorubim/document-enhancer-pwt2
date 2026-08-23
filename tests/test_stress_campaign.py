from pathlib import Path

import pytest

from document_enhancer.ingest import ingest_source
from scripts.stress_campaign import (
    campaign_specs,
    declared_page_count,
    generate_campaign,
    run_campaign,
)

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "desktop_procedure.md"


def test_campaign_generator_creates_twenty_documents_across_page_range(
    tmp_path: Path,
) -> None:
    specs = generate_campaign(tmp_path / "input")
    documents = sorted((tmp_path / "input").glob("*.docx"))

    assert len(specs) == 20
    assert len(documents) == 20
    assert [spec.pages for spec in specs] == [declared_page_count(path) for path in documents]
    assert min(spec.pages for spec in specs) == 5
    assert max(spec.pages for spec in specs) == 30
    assert {spec.profile for spec in specs} == {"structured", "unstructured", "mixed"}
    screenshot_sources = [
        path for path, spec in zip(documents, specs, strict=True) if spec.screenshot
    ]
    assert len(screenshot_sources) == sum(spec.screenshot for spec in campaign_specs())
    assert all(len(ingest_source(path).assets) == 1 for path in screenshot_sources)


def test_campaign_rejects_unexpected_stale_source(tmp_path: Path) -> None:
    input_dir = tmp_path / "campaign" / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "unexpected.md").write_text("# Stale source\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match=r"unexpected source files: unexpected\.md"):
        run_campaign(work_dir=tmp_path / "campaign", template_path=TEMPLATE)


@pytest.mark.stress
def test_full_twenty_document_stress_campaign(tmp_path: Path) -> None:
    report = run_campaign(work_dir=tmp_path / "campaign", template_path=TEMPLATE)
    manifest = report["manifest"]

    assert report["document_count"] == 20
    assert report["minimum_pages"] == 5
    assert report["maximum_pages"] == 30
    assert manifest["failed_count"] == 0
    assert manifest["completed_count"] + manifest["questions_count"] == 20
    assert manifest["recovered_count"] >= report["expected_recovery_minimum"]
    assert manifest["screenshot_count"] == report["expected_screenshot_documents"]
    assert report["verified_screenshot_documents"] == report["expected_screenshot_documents"]
    assert manifest["total_duration_seconds"] < 180
    for document in manifest["documents"]:
        output_dir = Path(document["output_dir"])
        assert (output_dir / "draft.docx").is_file()
        assert (output_dir / "analysis.docx").is_file()
        assert (output_dir / "mapping.json").is_file()
