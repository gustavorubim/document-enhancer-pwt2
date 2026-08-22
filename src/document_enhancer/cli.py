"""One-command interface for the linear document-enhancement pipeline."""

from pathlib import Path
from typing import Annotated

import typer

from document_enhancer.pipeline import (
    DeterministicProvider,
    GeminiProvider,
    PipelineContractError,
    run_enhancement,
    run_stage2,
)

app = typer.Typer(no_args_is_help=True, help="Enhance a source desktop procedure.")


def _select_provider(provider: str, model: str):
    selected = provider.strip().casefold()
    if selected not in {"auto", "gemini", "fake"}:
        raise typer.BadParameter(
            "provider must be one of: auto, gemini, fake", param_hint="provider"
        )
    if selected == "auto":
        selected = "gemini" if GeminiProvider.credentials_available() else "fake"
    model_provider = (
        GeminiProvider(model=model) if selected == "gemini" else DeterministicProvider()
    )
    return selected, model_provider


@app.callback()
def main() -> None:
    """Create source-grounded desktop procedures from DOCX or Markdown."""


@app.command("run")
def run_command(
    source: Annotated[Path, typer.Option("--source", help="Source .docx or Markdown document.")],
    template: Annotated[Path, typer.Option("--template", help="Required Markdown template.")],
    output_dir: Annotated[Path, typer.Option("--output-dir", help="Directory for five outputs.")],
    provider: Annotated[
        str, typer.Option("--provider", help="auto, gemini, or fake (deterministic evaluation).")
    ] = "auto",
    model: Annotated[str, typer.Option("--model", help="Gemini model name.")] = (
        "gemini-2.5-flash"
    ),
) -> None:
    """Create draft, analysis, and mapping artifacts immediately."""

    try:
        selected, model_provider = _select_provider(provider, model)
        artifacts = run_enhancement(
            source_path=source,
            template_path=template,
            output_dir=output_dir,
            provider=model_provider,
        )
    except (OSError, ValueError, PipelineContractError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    typer.echo(f"Provider: {selected}")
    for path in (
        artifacts.draft_markdown,
        artifacts.draft_docx,
        artifacts.analysis_markdown,
        artifacts.analysis_docx,
        artifacts.mapping_json,
    ):
        typer.echo(path)


@app.command("stage1")
def stage1_command(
    source: Annotated[Path, typer.Option("--source", help="Source .docx or Markdown document.")],
    template: Annotated[Path, typer.Option("--template", help="Required Markdown template.")],
    output_dir: Annotated[Path, typer.Option("--output-dir", help="Stage 1 output directory.")],
    provider: Annotated[
        str, typer.Option("--provider", help="auto, gemini, or fake (deterministic evaluation).")
    ] = "auto",
    model: Annotated[str, typer.Option("--model", help="Gemini model name.")] = (
        "gemini-2.5-flash"
    ),
) -> None:
    """Create the draft, analysis, process flow, and editable questions file."""

    try:
        selected, model_provider = _select_provider(provider, model)
        artifacts = run_enhancement(
            source_path=source,
            template_path=template,
            output_dir=output_dir,
            provider=model_provider,
            include_process_flow=True,
        )
    except (OSError, ValueError, PipelineContractError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(f"Provider: {selected}")
    for path in (
        artifacts.draft_markdown,
        artifacts.draft_docx,
        artifacts.analysis_markdown,
        artifacts.analysis_docx,
        artifacts.mapping_json,
        artifacts.process_flow_mermaid,
        artifacts.process_flow_image,
        artifacts.questions_json,
    ):
        typer.echo(path)


@app.command("stage2")
def stage2_command(
    source: Annotated[Path, typer.Option("--source", help="Original source document.")],
    template: Annotated[Path, typer.Option("--template", help="Original Markdown template.")],
    answers: Annotated[Path, typer.Option("--answers", help="Completed Stage 1 questions.json.")],
    output_dir: Annotated[
        Path, typer.Option("--output-dir", help="Separate final output directory.")
    ],
    provider: Annotated[
        str, typer.Option("--provider", help="auto, gemini, or fake (deterministic evaluation).")
    ] = "auto",
    model: Annotated[str, typer.Option("--model", help="Gemini model name.")] = (
        "gemini-2.5-flash"
    ),
) -> None:
    """Use complete owner answers to create a revised final procedure."""

    try:
        selected, model_provider = _select_provider(provider, model)
        artifacts = run_stage2(
            source_path=source,
            template_path=template,
            answers_path=answers,
            output_dir=output_dir,
            provider=model_provider,
        )
    except (OSError, ValueError, PipelineContractError) as exc:
        typer.echo(f"Error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(f"Provider: {selected}")
    for path in (
        artifacts.final_markdown,
        artifacts.final_docx,
        artifacts.resolution_json,
        artifacts.process_flow_mermaid,
        artifacts.process_flow_image,
    ):
        typer.echo(path)


if __name__ == "__main__":
    app()
