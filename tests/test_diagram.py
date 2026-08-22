from pathlib import Path

import pytest
from PIL import Image

from document_enhancer.diagram import (
    process_graph_to_mermaid,
    render_process_graph_png,
    validate_process_graph_sources,
)
from document_enhancer.models import ProcessEdge, ProcessGraph, ProcessNode, ProcessNodeKind


def _graph() -> ProcessGraph:
    return ProcessGraph(
        title="Desktop review flow",
        nodes=[
            ProcessNode(
                id="NODE-001",
                label="Start",
                kind=ProcessNodeKind.START,
                source_block_ids=["SRC-001"],
            ),
            ProcessNode(
                id="NODE-002",
                label="Open the review queue",
                kind=ProcessNodeKind.ACTION,
                source_block_ids=["SRC-001"],
            ),
            ProcessNode(
                id="NODE-003",
                label="Ready to publish?",
                kind=ProcessNodeKind.DECISION,
                source_block_ids=["SRC-002"],
            ),
            ProcessNode(
                id="NODE-004",
                label="End",
                kind=ProcessNodeKind.END,
                source_block_ids=["SRC-002"],
            ),
        ],
        edges=[
            ProcessEdge(
                from_node_id="NODE-001",
                to_node_id="NODE-002",
                source_block_ids=["SRC-001"],
            ),
            ProcessEdge(
                from_node_id="NODE-002",
                to_node_id="NODE-003",
                source_block_ids=["SRC-001"],
            ),
            ProcessEdge(
                from_node_id="NODE-003",
                to_node_id="NODE-004",
                label="yes",
                source_block_ids=["SRC-002"],
            ),
        ],
    )


def test_process_graph_to_mermaid_has_shapes_edges_and_safe_labels() -> None:
    graph = _graph().model_copy(
        update={
            "nodes": [
                *_graph().nodes[:3],
                _graph().nodes[3].model_copy(update={"label": 'Publish ]\nEND[ "now"'}),
            ]
        }
    )

    mermaid = process_graph_to_mermaid(graph)

    assert mermaid.startswith("flowchart TD\n")
    assert 'NODE_001(["Start"])' in mermaid
    assert 'NODE_002["Open the review queue"]' in mermaid
    assert 'NODE_003{"Ready to publish?"}' in mermaid
    assert 'NODE_004(["Publish END now"])' in mermaid
    assert "NODE_001 --> NODE_002" in mermaid
    assert "NODE_003 -->|yes| NODE_004" in mermaid
    assert "\nEND[" not in mermaid
    assert '"now"' not in mermaid


def test_validate_process_graph_sources_rejects_unknown_node_or_edge_citations() -> None:
    graph = _graph().model_copy(
        update={
            "edges": [
                *_graph().edges,
                ProcessEdge(
                    from_node_id="NODE-002",
                    to_node_id="NODE-004",
                    label="fallback",
                    source_block_ids=["SRC-999"],
                ),
            ]
        }
    )

    with pytest.raises(ValueError, match=r"unknown source IDs.*SRC-999"):
        validate_process_graph_sources(graph, {"SRC-001", "SRC-002"})

    validate_process_graph_sources(_graph(), {"SRC-001", "SRC-002"})


def test_render_process_graph_png_creates_nested_nonempty_png(tmp_path: Path) -> None:
    output = tmp_path / "nested" / "flow" / "process.png"

    assert render_process_graph_png(_graph(), output) == output
    assert output.is_file()
    assert output.stat().st_size > 0

    with Image.open(output) as image:
        assert image.format == "PNG"
        assert image.width >= 900
        assert image.height >= 250
        assert len(set(image.get_flattened_data())) > 1
