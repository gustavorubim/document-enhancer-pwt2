# Document Enhancer MVP

A lean, source-grounded application that turns one messy desktop procedure into a detailed new
procedure aligned to a required Markdown template. One command creates a draft, an owner-facing
analysis, and a traceable mapping in both Markdown and DOCX where applicable.

## Scope

This MVP handles **desktop procedures only**.

- Source input: one `.docx`, `.md`, or `.markdown` file.
- Template input: one required Markdown template.
- Output: `draft.md`, `draft.docx`, `analysis.md`, `analysis.docx`, and `mapping.json`.
- Model access: one Gemini setup through `langchain-google-genai`.
- Workflow orchestration: one bounded LangGraph `StateGraph`; the direct classic `langchain`
  package is not a project dependency.

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

For a DOCX source, inline PNG/JPEG figures in the document body or table cells are extracted as
ordered `FIG-###` assets, written under the output `assets/` directory, checksum-tracked in
`mapping.json`, and placed back into the draft near their source context. Markdown sources do not
carry embedded DOCX media.

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
each `answer` value in `questions.json`; do not change the source/template digests, structure
contract, IDs, question text, or gap links.

Run Stage 2 into a separate directory:

```bash
uv run docenhance stage2 \
  --source tests/fixtures/messy_desktop_procedure.docx \
  --template templates/desktop_procedure.md \
  --answers runs/stage1/questions.json \
  --output-dir runs/stage2
```

Stage 2 fails closed if an answer is blank, a question or structure contract changed, the selected
structure mode differs from Stage 1, or the source/template no longer matches the Stage 1 digests.
When Stage 1 recovered a weak outline, Stage 2 reuses that exact recovered structure instead of
asking the model to infer it again. Each answer becomes a new cited `SRC-###` authoritative input.
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

## LangGraph workflow

The authoring path is a compact, fixed `StateGraph` with one conditional branch:

```text
START -> load_inputs -> analyze -> draft -> review -> END
                    \-> recover_structure -/
```

`load_inputs` scores the parsed outline. In `auto` mode, only a weak layout is sent to the bounded
structure-recovery operation; a sufficient outline goes directly to analysis. `--structure-mode`
can explicitly select `auto`, `always`, or `never`. Recovery may rename headings, but it must cover
every existing `SRC-###` block exactly once, in source order, without changing source text. Stage 2
uses the corresponding shorter load/recover/analyze graph before applying the answer file. The
assessment, recovered outline when applicable, and executed graph trace are recorded under
`source_structure` in `mapping.json`.

The remaining nodes analyze the complete source, draft one detailed `DraftSection` per target
section, review the assembled draft, and validate source, target, gap, and factual-anchor
references. Gemini operations use one `ChatGoogleGenerativeAI` setup and native Pydantic structured
output via `method="json_schema"`. All substantive model instructions, including structure
recovery, process-graph extraction, and the answer-driven final rewrite, live in
[`prompts/`](prompts/); Python selects the operation, serializes application data, and validates
the returned schema.

## Batch transformation

Transform every supported source in a directory independently. The deterministic path is useful
for a repeatable local campaign:

```bash
uv run docenhance batch \
  --input-dir docs/incoming \
  --template templates/desktop_procedure.md \
  --output-dir runs/batch \
  --provider fake
```

Batch accepts `.docx`, `.md`, and `.markdown`. Each source gets its own output directory containing
the normal artifacts, while `batch_manifest.json` records status, question count, retained
screenshot count, structure score/recovery, duration, and any error. Processing is sequential and
failure-isolated, so one bad source does not stop the remaining documents. Use `--provider auto`
or `--provider gemini` for live model runs.

## Stress campaign

The lean campaign generator creates 20 deterministic DOCX fixtures with declared lengths from 5
to 30 pages, structured/unstructured/mixed layouts, and a rotating mix of screenshots, tables,
conflicts, and missing-owner gaps. Page counts are defined by explicit OOXML page breaks, which
makes the fixture contract deterministic. Run it with:

```bash
uv run python scripts/stress_campaign.py --work-dir runs/stress-campaign
```

It writes the generated inputs and `campaign_spec.json`, per-document batch outputs and
`batch_manifest.json`, plus `stress_report.json`. The runner rejects unexpected stale source files,
requires every unstructured fixture to take the recovery path, and verifies screenshot bytes in
both the extracted asset and generated DOCX. The full campaign test is opt-in because normal tests
exclude the `stress` marker:

```bash
uv run pytest -m stress tests/test_stress_campaign.py
```

## Analysis contents

The analysis gives the document owner:

1. an executive assessment;
2. status and evidence for every template section and requirement;
3. a disposition for every meaningful source block;
4. assessment of all required desktop-procedure components; and
5. one deduplicated question for each genuine missing fact, ambiguity, or conflict.

## Verification

```bash
uv run ruff format --check .
uv run ruff check .
uv run pytest
uv run pytest -m stress tests/test_stress_campaign.py
```

The evaluation fixture is reproducible:

- [`tests/fixtures/build_messy_fixture.py`](tests/fixtures/build_messy_fixture.py) builds the DOCX.
- [`tests/fixtures/expected_facts.json`](tests/fixtures/expected_facts.json) records expected facts,
  nine ordered actions, warnings, recovery paths, one missing-owner issue, and one repeated
  threshold conflict.

Tests verify complete section/requirement/source accounting, expected-fact retention, visible gaps,
conflict-question deduplication, unsupported-fact rejection, prompt placement, the CLI contract,
and Markdown/DOCX semantic equivalence. Additional tests cover LangGraph routing, bounded
structure recovery, source-image extraction and embedding, batch failure isolation, Mermaid
sanitization, source citations, digest-bound questions, immutable Stage 1 artifacts,
complete-answer enforcement, resolved-conflict cleanup, and nine-step retention. DOCX rendering
uses real numbering and fixed table geometry suitable for a compact operator reference.

## Explicit non-goals

The application does not include Google Drive or Google Docs APIs, Deep Agents, databases, RAG,
GraphRAG, embeddings, FAISS, ontologies, sealing, an approval/resume state machine, PDF input/output,
OCR, HTML reports, a web UI, deployment, or autonomous agent loops. Batch processing is currently
local and sequential. The two-stage commands are independent, digest-bound runs connected by an
explicit answer file.
