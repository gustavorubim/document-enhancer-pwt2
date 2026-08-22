import json
import re
from pathlib import Path

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from markdown_it import MarkdownIt
from typer.testing import CliRunner

from document_enhancer.cli import app

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "tests" / "fixtures" / "messy_desktop_procedure.docx"
TEMPLATE = ROOT / "templates" / "desktop_procedure.md"
REQUIRED_OUTPUTS = {
    "draft.md",
    "draft.docx",
    "analysis.md",
    "analysis.docx",
    "mapping.json",
}


def _normalize(value: str) -> str:
    return " ".join(value.split())


def _markdown_atoms(path: Path) -> list[str]:
    tokens = MarkdownIt("commonmark").enable("table").parse(path.read_text(encoding="utf-8"))
    atoms = []
    for token in tokens:
        if token.type != "inline":
            continue
        content = "".join(
            " " if child.type in {"softbreak", "hardbreak"} else child.content
            for child in (token.children or [])
            if child.type in {"text", "code_inline", "softbreak", "hardbreak"}
        )
        if normalized := _normalize(content):
            atoms.append(normalized)
    return atoms


def _docx_atoms(path: Path) -> list[str]:
    document = Document(path)
    atoms = []
    for item in document.iter_inner_content():
        if isinstance(item, Paragraph):
            if normalized := _normalize(item.text):
                atoms.append(normalized)
        elif isinstance(item, Table):
            atoms.extend(
                normalized
                for row in item.rows
                for cell in row.cells
                if (normalized := _normalize(cell.text))
            )
    return atoms


def test_cli_creates_five_equivalent_detailed_artifacts(tmp_path: Path) -> None:
    output_dir = tmp_path / "example"
    result = CliRunner().invoke(
        app,
        [
            "run",
            "--source",
            str(SOURCE),
            "--template",
            str(TEMPLATE),
            "--output-dir",
            str(output_dir),
            "--provider",
            "fake",
        ],
    )

    assert result.exit_code == 0, result.output
    assert {path.name for path in output_dir.iterdir()} == REQUIRED_OUTPUTS
    assert _markdown_atoms(output_dir / "draft.md") == _docx_atoms(output_dir / "draft.docx")
    assert _markdown_atoms(output_dir / "analysis.md") == _docx_atoms(output_dir / "analysis.docx")

    draft = (output_dir / "draft.md").read_text(encoding="utf-8")
    analysis = (output_dir / "analysis.md").read_text(encoding="utf-8")
    mapping = json.loads((output_dir / "mapping.json").read_text(encoding="utf-8"))
    assert len(re.findall(r"(?m)^\d+\. ", draft)) >= 9
    assert len(draft.split()) >= 700
    assert "[MISSING:" in draft and "[CONFLICT:" in draft
    assert "<!-- REQUIREMENTS" not in draft
    assert [
        heading
        for heading in (
            "Executive assessment",
            "Template coverage",
            "Source-section disposition",
            "Desktop-procedure component analysis",
            "Questions and recommendations",
        )
        if f"## {heading}" in analysis
    ] == [
        "Executive assessment",
        "Template coverage",
        "Source-section disposition",
        "Desktop-procedure component analysis",
        "Questions and recommendations",
    ]
    assert len(mapping["target_sections"]) == 10
    assert len(mapping["source_section_dispositions"]) == 9
    assert len(mapping["questions"]) == 2


def test_auto_provider_uses_deterministic_path_without_api_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    result = CliRunner().invoke(
        app,
        [
            "run",
            "--source",
            str(SOURCE),
            "--template",
            str(TEMPLATE),
            "--output-dir",
            str(tmp_path / "auto"),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "Provider: fake" in result.output
