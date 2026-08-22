# Document Enhancer MVP

A lean, source-grounded application that turns one messy desktop procedure into a detailed new
procedure aligned to a required Markdown template. One command creates a draft, an owner-facing
analysis, and a traceable mapping in both Markdown and DOCX where applicable.

## Scope

This MVP handles **desktop procedures only**.

- Source input: one `.docx`, `.md`, or `.markdown` file.
- Template input: one required Markdown template.
- Output: `draft.md`, `draft.docx`, `analysis.md`, `analysis.docx`, and `mapping.json`.
- Model access: Gemini through LangChain and `langchain-google-genai` only.

The draft is source-supported. Template requirements say what a section should contain, but they
are never treated as evidence that a business fact is true. Missing, conflicting, and unclear
information remains visible instead of being filled with generic advice.

## Install

Python 3.12 and `uv` are required.

```bash
uv sync
```

For live Gemini use, set either environment variable (do not commit the value):

```bash
export GOOGLE_API_KEY="..."
# or
export GEMINI_API_KEY="..."
```

`GOOGLE_API_KEY` takes precedence when both are present. The default model is
`gemini-2.5-flash`; override it with `--model`.

## Complete example

```bash
uv run docenhance run \
  --source tests/fixtures/messy_desktop_procedure.docx \
  --template templates/desktop_procedure.md \
  --output-dir runs/example
```

Provider selection is explicit:

- `--provider auto` (default) uses Gemini when a supported API-key environment variable exists;
  otherwise it uses the deterministic evaluation provider.
- `--provider gemini` requires working Gemini credentials.
- `--provider fake` runs the transparent deterministic path used by tests and the bundled fixture.

The fake provider proves contracts, accounting, rendering, and fact retention. It is not a
replacement for live model-quality verification on real documents.

## Optional two-stage workflow

Use Stage 1 when the document owner should answer gaps before a final rewrite. It preserves the
five baseline artifacts and adds a source-derived process flow plus an editable question file.

```bash
uv run docenhance stage1 \
  --source tests/fixtures/messy_desktop_procedure.docx \
  --template templates/desktop_procedure.md \
  --output-dir runs/stage1
```

Additional Stage 1 files:

- `process_flow.png` — a rendered graph derived from the procedure;
- `process_flow.mmd` — deterministic Mermaid source for the same graph; and
- `questions.json` — protected question IDs/text/gap links plus blank `answer` fields.

The Stage 1 draft contains both the rendered process-flow image and the Mermaid code. Fill only
each `answer` value in `questions.json`; do not change the source/template digests, IDs, question
text, or gap links.

Run Stage 2 into a separate directory:

```bash
uv run docenhance stage2 \
  --source tests/fixtures/messy_desktop_procedure.docx \
  --template templates/desktop_procedure.md \
  --answers runs/stage1/questions.json \
  --output-dir runs/stage2
```

Stage 2 fails closed if an answer is blank, a question contract changed, or the source/template no
longer matches the Stage 1 digests. Each answer becomes a new cited `SRC-###` authoritative input.
The final rewrite removes resolved callouts and superseded conflict language while preserving
unrelated source detail. It creates `final.md`, `final.docx`, `resolution.json`, and updated
`process_flow.mmd`/`process_flow.png` without modifying the Stage 1 directory.

## Template format

Every Markdown heading is an ordered target section. Put one non-empty `REQUIREMENTS` comment
immediately beneath every heading. Text outside the comment is fixed explanatory Markdown and is
preserved in the generated draft.

```markdown
# Template title

<!-- REQUIREMENTS
- Identify the procedure and its intended outcome.
-->

This fixed explanatory text remains in the draft.

## Procedure steps

<!-- REQUIREMENTS
- Provide the complete numbered action sequence.
- Preserve warnings and decision conditions.
-->
```

Requirement comments are removed from the draft. Parsing fails clearly when the template has no
headings, duplicate headings, a missing or malformed requirements block, or an empty requirements
block. [`templates/desktop_procedure.md`](templates/desktop_procedure.md) is the bundled realistic
template.

## Detailed-writing standard

The generated procedure is expected to be operational, not an executive summary. It retains
source-supported purpose, audience, scope, prerequisites, access, systems, tools, files, inputs,
roles, triggers, timing, numbered actions and substeps, decisions, validation, outputs, evidence,
warnings, exceptions, recovery, and escalation.

Sequential source actions remain separate numbered steps. The application preserves examples and
qualifications, keeps fixed template prose, and rejects newly introduced numeric, time, file,
system, or identifier anchors that are unsupported by the cited source blocks. Unresolved facts use
visible callouts:

```text
[MISSING: The source does not identify the person responsible for this step.]
[CONFLICT: The source gives two different completion deadlines.]
[UNCLEAR: The source mentions validation but does not explain how it is performed.]
```

## Linear pipeline

There is no agent graph or workflow engine. Application code owns one bounded sequence:

1. Read and normalize the complete DOCX or Markdown source into ordered `SRC-###` blocks.
2. Parse every ordered template section and requirement.
3. Analyze the complete source and create `AnalysisMapping` with section coverage, requirement
   coverage, source dispositions, component assessments, gaps, questions, and source-block IDs.
4. Draft one detailed `DraftSection` per target section.
5. Review the complete assembled draft for omissions, grounding, compression, duplication, and
   readability; apply only typed corrected sections.
6. Validate source, target, gap, and factual-anchor references.
7. Render the same Markdown content to the matching DOCX files and write `mapping.json`.

Gemini operations use one `ChatGoogleGenerativeAI` setup and native Pydantic structured output via
`method="json_schema"`. All substantive model instructions, including process-graph extraction and
the answer-driven final rewrite, live in [`prompts/`](prompts/); Python only selects the operation,
serializes application data, and validates the returned schema.

## Analysis contents

The analysis gives the document owner:

1. an executive assessment;
2. status and evidence for every template section and requirement;
3. a disposition for every meaningful source block;
4. assessment of all required desktop-procedure components; and
5. one deduplicated question for each genuine missing fact, ambiguity, or conflict.

## Verification

```bash
uv run ruff check .
uv run pytest
```

The evaluation fixture is reproducible:

- [`tests/fixtures/build_messy_fixture.py`](tests/fixtures/build_messy_fixture.py) builds the DOCX.
- [`tests/fixtures/expected_facts.json`](tests/fixtures/expected_facts.json) records expected facts,
  nine ordered actions, warnings, recovery paths, one missing-owner issue, and one repeated
  threshold conflict.

Tests verify complete section/requirement/source accounting, expected-fact retention, visible gaps,
conflict-question deduplication, unsupported-fact rejection, prompt placement, the exact five-file
CLI contract, and Markdown/DOCX semantic equivalence. DOCX rendering uses real numbering and fixed
table geometry suitable for a compact operator reference. V2 tests additionally verify Mermaid
sanitization, source citations, PNG/DOCX image embedding, digest-bound questions, immutable Stage 1
artifacts, complete-answer enforcement, resolved-conflict cleanup, and nine-step retention.

## Explicit non-goals

The application does not include Google Drive or Google Docs APIs, LangGraph, Deep Agents,
databases, RAG, GraphRAG, embeddings, FAISS, ontologies, sealing, an approval/resume state machine,
screenshots, PDF input/output, HTML reports, a web UI, deployment, or autonomous agent loops. The
two-stage commands are independent, digest-bound runs connected by an explicit answer file.
