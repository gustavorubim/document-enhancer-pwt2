"""Parse the deliberately small Markdown template contract."""

from __future__ import annotations

import re
from pathlib import Path

from markdown_it import MarkdownIt

from document_enhancer.models import ParsedTemplate, TemplateRequirement, TemplateSection


class TemplateParseError(ValueError):
    """Raised when a Markdown template violates the public template contract."""


_REQUIREMENTS = re.compile(
    r"<!--\s*REQUIREMENTS(?P<body>.*?)-->",
    flags=re.DOTALL | re.IGNORECASE,
)


def parse_template(path: Path) -> ParsedTemplate:
    """Parse ordered headings, requirement comments, and fixed explanatory Markdown."""

    template_path = path.expanduser().resolve()
    if not template_path.is_file():
        raise TemplateParseError(f"template file does not exist: {template_path}")
    if template_path.suffix.casefold() not in {".md", ".markdown"}:
        raise TemplateParseError("template must be a Markdown file with a .md or .markdown suffix")

    raw = template_path.read_text(encoding="utf-8")
    tokens = MarkdownIt("commonmark").parse(raw)
    headings: list[tuple[int, str, int]] = []
    for index, token in enumerate(tokens):
        if token.type == "heading_open" and token.map is not None:
            headings.append((token.map[0], tokens[index + 1].content.strip(), int(token.tag[1])))

    if not headings:
        raise TemplateParseError("template has no sections; add at least one Markdown heading")

    normalized = [heading.casefold() for _, heading, _ in headings]
    duplicates = sorted({heading for heading in normalized if normalized.count(heading) > 1})
    if duplicates:
        raise TemplateParseError(
            "template has duplicate section headings: " + ", ".join(duplicates)
        )

    lines = raw.splitlines()
    sections: list[TemplateSection] = []
    for index, (line_number, heading, level) in enumerate(headings, start=1):
        if not heading:
            raise TemplateParseError(f"template section {index} has an empty heading")
        end = headings[index][0] if index < len(headings) else len(lines)
        section_body = "\n".join(lines[line_number + 1 : end])
        requirement_matches = list(_REQUIREMENTS.finditer(section_body))
        if not requirement_matches:
            if re.search(r"<!--\s*REQUIREMENTS", section_body, flags=re.IGNORECASE):
                raise TemplateParseError(
                    f"section {heading!r} has a malformed or unclosed REQUIREMENTS block"
                )
            raise TemplateParseError(f"section {heading!r} is missing a REQUIREMENTS block")
        if len(requirement_matches) > 1:
            raise TemplateParseError(
                f"section {heading!r} has multiple REQUIREMENTS blocks; expected exactly one"
            )

        match = requirement_matches[0]
        if section_body[: match.start()].strip():
            raise TemplateParseError(
                f"section {heading!r} must place its REQUIREMENTS block immediately beneath "
                "the heading"
            )
        requirements = _parse_requirements(heading, match.group("body"), index)
        fixed_markdown = (section_body[: match.start()] + section_body[match.end() :]).strip()
        sections.append(
            TemplateSection(
                id=f"SEC-{index:03d}",
                heading=heading,
                level=level,
                requirements=requirements,
                fixed_markdown=fixed_markdown,
            )
        )

    return ParsedTemplate(source_path=template_path, sections=sections)


def _parse_requirements(
    heading: str, comment_body: str, section_index: int
) -> list[TemplateRequirement]:
    requirements: list[TemplateRequirement] = []
    for line_number, raw_line in enumerate(comment_body.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        match = re.fullmatch(r"-\s+(.+)", line)
        if not match or not match.group(1).strip():
            raise TemplateParseError(
                f"section {heading!r} has a malformed requirement on comment line "
                f"{line_number}; use '- requirement text'"
            )
        requirements.append(
            TemplateRequirement(
                id=f"REQ-{section_index:03d}-{len(requirements) + 1:02d}",
                text=match.group(1).strip(),
            )
        )
    if not requirements:
        raise TemplateParseError(f"section {heading!r} has an empty REQUIREMENTS block")
    return requirements
