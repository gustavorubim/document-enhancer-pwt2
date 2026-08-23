# Prompt files

All substantive model instructions live in this directory and are loaded at runtime:

- `shared.md` supplies grounding and detailed-writing rules to every model operation.
- `structure.md` defines bounded recovery for poorly sectioned documents.
- `analyze.md` defines complete-source analysis and mapping.
- `draft_section.md` defines bounded per-section drafting.
- `review.md` defines the final completeness and grounding review.
- `diagram.md` defines source-grounded process-graph extraction.
- `finalize.md` defines the bounded answer-driven Stage 2 rewrite.

Python code may serialize application data and select a schema, but it must not embed substantive
model instructions.
