# Progress

- **Current checkpoint:** Enhancement checkpoint 5 complete — implementation and verification finished
- **Completed implementation:** a compact LangGraph `StateGraph` with conditional weak-layout recovery, DOCX PNG/JPEG screenshot extraction and reinsertion, and sequential failure-isolated batch transformation with per-document outputs and an incremental `batch_manifest.json`
- **Normal verification:** Ruff formatting/lint passed; 63 tests passed and the opt-in stress test was deselected as intended
- **Campaign verification:** 20/20 documents processed with zero failures; isolated and persistent runs completed in under 60 seconds (latest persistent run: 27.74 seconds); 7 weak documents used structure recovery; all 6 expected screenshot documents retained exact source bytes in the extracted asset and generated DOCX
- **Campaign contract:** 20 deterministic DOCX fixtures with declared page counts ranging from 5 to 30, structured/unstructured/mixed layouts, and coverage for screenshots, tables, conflicts, and missing-owner gaps
- **Visual QA:** rendered all pages of representative 5-, 18-, and 30-page sources plus a four-page generated draft containing its retained screenshot and Mermaid diagram; no clipping, overflow, or missing figure was observed
- **Next checkpoint:** optional live Gemini quality evaluation on representative real documents when credentials are available
- **Blocker:** none

## Live verification

Neither `GOOGLE_API_KEY` nor `GEMINI_API_KEY` was available. Live Gemini verification was not run;
the deterministic evaluation provider path was completed and verified instead.

## Verification commands

```bash
uv run ruff format --check .
uv run ruff check .
uv run pytest
uv run pytest -m stress tests/test_stress_campaign.py
uv run python scripts/stress_campaign.py --work-dir runs/stress-campaign
```

The default test command excludes the `stress` marker. The complete normal suite and explicit
stress campaign passed in this checkout on 2026-08-23. The campaign page-size contract counts
explicit OOXML page breaks; visual rendering separately covered the 5-, 18-, and 30-page cases.

## Not in MVP

Approval workflows, databases, RAG, GraphRAG, embeddings, ontology, sealing, PDF/OCR handling,
HTML reports, web interfaces, deployment, and autonomous agent loops. Batch processing remains
local and sequential. The two-stage workflow remains an explicit, digest-bound answer-file handoff.
