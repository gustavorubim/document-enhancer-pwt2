from pathlib import Path

import pytest

from document_enhancer.template import TemplateParseError, parse_template


def _write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "template.md"
    path.write_text(content, encoding="utf-8")
    return path


def test_template_preserves_order_requirements_levels_and_fixed_text(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        """# Procedure

<!-- REQUIREMENTS
- Name the procedure.
-->

Keep this explanation exactly.

## Actions

<!-- REQUIREMENTS
- Preserve every action.
- Retain the expected result.
-->

Actions are performed in order.
""",
    )

    result = parse_template(path)

    assert [section.heading for section in result.sections] == ["Procedure", "Actions"]
    assert [section.level for section in result.sections] == [1, 2]
    assert [requirement.id for requirement in result.sections[1].requirements] == [
        "REQ-002-01",
        "REQ-002-02",
    ]
    assert result.sections[0].fixed_markdown == "Keep this explanation exactly."
    assert "REQUIREMENTS" not in result.sections[0].fixed_markdown


def test_repository_template_is_valid_and_realistic() -> None:
    path = Path(__file__).resolve().parents[1] / "templates" / "desktop_procedure.md"
    result = parse_template(path)

    assert len(result.sections) == 10
    assert result.sections[0].heading == "Desktop Procedure"
    assert result.sections[-1].heading == "Exceptions, recovery, and escalation"
    assert all(section.requirements for section in result.sections)
    assert all(section.fixed_markdown for section in result.sections)


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("plain text only", "no sections"),
        (
            "# Purpose\n<!-- REQUIREMENTS\n- State it.\n-->\n# purpose\n"
            "<!-- REQUIREMENTS\n- Repeat it.\n-->",
            "duplicate section headings",
        ),
        ("# Purpose\nNo instructions", "missing a REQUIREMENTS block"),
        ("# Purpose\n<!-- REQUIREMENTS\n-->", "empty REQUIREMENTS block"),
        ("# Purpose\n<!-- REQUIREMENTS\nState it.\n-->", "malformed requirement"),
        (
            "# Purpose\nExplanation first.\n<!-- REQUIREMENTS\n- State it.\n-->",
            "immediately beneath",
        ),
        ("# Purpose\n<!-- REQUIREMENTS\n- State it.", "malformed or unclosed"),
    ],
)
def test_malformed_templates_have_clear_errors(tmp_path: Path, content: str, message: str) -> None:
    with pytest.raises(TemplateParseError, match=message):
        parse_template(_write(tmp_path, content))
