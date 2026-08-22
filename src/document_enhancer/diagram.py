"""Deterministic Mermaid and PNG renderers for source-grounded process graphs.

The process graph models deliberately contain only a small amount of structure:
nodes have a kind and label, while edges have an optional label.  This module
keeps the renderers equally small and dependency-light.  Mermaid output is
plain text with conservative escaping, and the raster renderer uses Pillow
directly rather than requiring Graphviz or a browser runtime.
"""

from __future__ import annotations

import heapq
import math
import re
import unicodedata
from collections.abc import Iterable
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from document_enhancer.models import ProcessEdge, ProcessGraph, ProcessNode, ProcessNodeKind

__all__ = [
    "process_graph_to_mermaid",
    "render_process_graph_png",
    "validate_process_graph_sources",
]


# Mermaid labels are placed inside explicit shape delimiters.  Keep the
# accepted character set intentionally conservative so a label cannot close a
# shape, inject a new statement, or create an edge.  Unicode word characters
# remain available for ordinary non-ASCII procedure text.
_UNSAFE_MERMAID_TEXT = re.compile(r"[^\w\s.,!?'+\-:$%()/=]", re.UNICODE)
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f-\x9f]+")
_WHITESPACE = re.compile(r"\s+")

_NODE_WIDTH = 260
_NODE_HEIGHT = 140
_HORIZONTAL_GAP = 72
_VERTICAL_GAP = 132
_MARGIN_X = 64
_GRAPH_TOP = 88
_BOTTOM_MARGIN = 56
_MAX_COLUMNS = 4

_BACKGROUND = "#F8FAFC"
_EDGE_COLOR = "#475569"
_TEXT_COLOR = "#0F172A"
_EDGE_LABEL_FILL = "#FFFFFF"

_KIND_COLORS: dict[ProcessNodeKind, tuple[str, str]] = {
    ProcessNodeKind.START: ("#DCFCE7", "#15803D"),
    ProcessNodeKind.ACTION: ("#DBEAFE", "#1D4ED8"),
    ProcessNodeKind.DECISION: ("#FEF3C7", "#B45309"),
    ProcessNodeKind.END: ("#FEE2E2", "#B91C1C"),
}


def validate_process_graph_sources(
    graph: ProcessGraph,
    valid_source_ids: Iterable[str],
) -> None:
    """Validate every node and edge source citation against ``valid_source_ids``.

    ``ProcessGraph`` validates graph topology and node references, but source
    block IDs belong to the surrounding source document.  Keeping this check
    separate lets callers apply the same graph contract to a particular source
    snapshot without putting document-specific state into the graph model.

    A single error lists all unknown IDs in sorted order so failures are stable
    and actionable.  The function returns ``None`` on success.
    """

    known_source_ids = {str(source_id) for source_id in valid_source_ids}
    unknown_source_ids: set[str] = set()

    for node in graph.nodes:
        unknown_source_ids.update(
            source_id for source_id in node.source_block_ids if source_id not in known_source_ids
        )
    for edge in graph.edges:
        unknown_source_ids.update(
            source_id for source_id in edge.source_block_ids if source_id not in known_source_ids
        )

    if unknown_source_ids:
        raise ValueError(
            f"process graph references unknown source IDs: {sorted(unknown_source_ids)}"
        )


def process_graph_to_mermaid(graph: ProcessGraph) -> str:
    """Return a deterministic, sanitized Mermaid ``flowchart TD`` definition.

    Mermaid node IDs are derived from the model-constrained IDs and use
    underscores instead of hyphens.  Labels are quoted within their shape and
    stripped of Mermaid delimiters/control characters.  Nodes and edges are
    sorted by stable model fields, so equivalent graph instances render to the
    same text even if their input lists were assembled in a different order.
    """

    lines = [
        "flowchart TD",
        f"    %% {_sanitize_mermaid_text(graph.title, fallback='Process graph')}",
    ]

    nodes = sorted(graph.nodes, key=_node_sort_key)
    for node in nodes:
        node_id = _mermaid_node_id(node.id)
        label = _sanitize_mermaid_text(node.label, fallback=node.id)
        if node.kind in (ProcessNodeKind.START, ProcessNodeKind.END):
            # Stadium shapes are the conventional process start/end shape.
            lines.append(f'    {node_id}(["{label}"])')
        elif node.kind is ProcessNodeKind.ACTION:
            lines.append(f'    {node_id}["{label}"]')
        elif node.kind is ProcessNodeKind.DECISION:
            lines.append(f'    {node_id}{{"{label}"}}')
        else:  # pragma: no cover - ProcessNodeKind is exhaustive at runtime.
            raise ValueError(f"unsupported process node kind: {node.kind!r}")

    for edge in sorted(graph.edges, key=_edge_sort_key):
        from_id = _mermaid_node_id(edge.from_node_id)
        to_id = _mermaid_node_id(edge.to_node_id)
        label = _sanitize_mermaid_text(edge.label, fallback="")
        if label:
            lines.append(f"    {from_id} -->|{label}| {to_id}")
        else:
            lines.append(f"    {from_id} --> {to_id}")

    return "\n".join(lines) + "\n"


def render_process_graph_png(graph: ProcessGraph, output_path: Path | str) -> Path:
    """Render ``graph`` as a readable top-to-bottom PNG and return its path.

    The layout is a deterministic longest-path layering for DAGs.  If a caller
    supplies a cyclic graph despite the process-graph contract's DAG intent,
    the remaining cycle nodes are placed in stable fallback rows rather than
    making image generation fail unpredictably.
    """

    target_path = Path(output_path).expanduser()
    target_path.parent.mkdir(parents=True, exist_ok=True)

    nodes = sorted(graph.nodes, key=_node_sort_key)
    edges = sorted(graph.edges, key=_edge_sort_key)
    visual_rows, _layers = _visual_rows(graph, nodes, edges)

    title_font = _load_font(24, bold=True)
    node_font = _load_font(16)
    edge_font = _load_font(13)

    largest_row = max((len(row) for row in visual_rows), default=1)
    width = max(
        900,
        2 * _MARGIN_X + largest_row * _NODE_WIDTH + max(0, largest_row - 1) * _HORIZONTAL_GAP,
    )
    height = (
        _GRAPH_TOP
        + len(visual_rows) * _NODE_HEIGHT
        + max(0, len(visual_rows) - 1) * _VERTICAL_GAP
        + _BOTTOM_MARGIN
    )

    image = Image.new("RGB", (width, height), _BACKGROUND)
    draw = ImageDraw.Draw(image)
    title = _display_text(graph.title, fallback="Process graph")
    _draw_centered_text(draw, (width / 2, 34), title, title_font, fill=_TEXT_COLOR)

    boxes: dict[str, tuple[float, float, float, float]] = {}
    for row_index, row in enumerate(visual_rows):
        row_width = len(row) * _NODE_WIDTH + max(0, len(row) - 1) * _HORIZONTAL_GAP
        left = (width - row_width) / 2
        top = _GRAPH_TOP + row_index * (_NODE_HEIGHT + _VERTICAL_GAP)
        for column_index, node_id in enumerate(row):
            x0 = left + column_index * (_NODE_WIDTH + _HORIZONTAL_GAP)
            box = (x0, top, x0 + _NODE_WIDTH, top + _NODE_HEIGHT)
            boxes[node_id] = box

    # Draw connectors before nodes so boundaries remain crisp and arrows never
    # obscure node labels.  Edge labels are drawn afterwards for legibility.
    labeled_edges: list[tuple[ProcessEdge, tuple[float, float], int]] = []
    for edge_index, edge in enumerate(edges):
        source_box = boxes[edge.from_node_id]
        target_box = boxes[edge.to_node_id]
        start = _box_anchor(source_box, target_box, _node_kind(graph, edge.from_node_id))
        end = _box_anchor(target_box, source_box, _node_kind(graph, edge.to_node_id))
        draw.line((start, end), fill=_EDGE_COLOR, width=3)
        _draw_arrowhead(draw, start, end, fill=_EDGE_COLOR)
        if edge.label.strip():
            midpoint = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
            labeled_edges.append((edge, midpoint, edge_index))

    for node in nodes:
        _draw_node(draw, boxes[node.id], node, node_font)

    for edge, midpoint, edge_index in labeled_edges:
        # A small deterministic perpendicular offset keeps labels for parallel
        # edges from sitting exactly on top of one another.
        source_box = boxes[edge.from_node_id]
        target_box = boxes[edge.to_node_id]
        dx = target_box[2] + target_box[0] - source_box[2] - source_box[0]
        dy = target_box[3] + target_box[1] - source_box[3] - source_box[1]
        length = math.hypot(dx, dy) or 1.0
        offset = ((edge_index % 3) - 1) * 13.0
        label_point = (
            midpoint[0] - (dy / length) * offset,
            midpoint[1] + (dx / length) * offset,
        )
        _draw_edge_label(draw, label_point, edge.label, edge_font, width)

    image.save(target_path, format="PNG")
    return target_path


def _node_sort_key(node: ProcessNode) -> tuple[str, str, str]:
    return (node.id, node.kind.value, node.label)


def _edge_sort_key(edge: ProcessEdge) -> tuple[str, str, str, tuple[str, ...]]:
    return (edge.from_node_id, edge.to_node_id, edge.label, tuple(edge.source_block_ids))


def _mermaid_node_id(node_id: str) -> str:
    """Convert a model ID into an identifier safe in Mermaid statements."""

    safe_id = re.sub(r"[^A-Za-z0-9_]", "_", node_id)
    if not safe_id or safe_id[0].isdigit():
        safe_id = f"N_{safe_id}"
    if safe_id.casefold() in {"end", "graph", "flowchart", "subgraph"}:
        safe_id = f"N_{safe_id}"
    return safe_id


def _sanitize_mermaid_text(value: str, *, fallback: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value))
    normalized = _CONTROL_CHARACTERS.sub(" ", normalized)
    normalized = _UNSAFE_MERMAID_TEXT.sub(" ", normalized)
    normalized = _WHITESPACE.sub(" ", normalized).strip()
    return normalized or fallback


def _display_text(value: str, *, fallback: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value))
    normalized = _CONTROL_CHARACTERS.sub(" ", normalized)
    normalized = _WHITESPACE.sub(" ", normalized).strip()
    return normalized or fallback


def _visual_rows(
    graph: ProcessGraph,
    nodes: list[ProcessNode],
    edges: list[ProcessEdge],
) -> tuple[list[list[str]], dict[str, int]]:
    """Return deterministic rows and logical layers for a graph."""

    node_ids = [node.id for node in nodes]
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    indegree: dict[str, int] = {node_id: 0 for node_id in node_ids}
    for edge in edges:
        outgoing[edge.from_node_id].append(edge.to_node_id)
        indegree[edge.to_node_id] += 1
    for node_id in outgoing:
        outgoing[node_id].sort()

    layers: dict[str, int] = {node_id: 0 for node_id in node_ids}
    queue = [node_id for node_id in node_ids if indegree[node_id] == 0]
    heapq.heapify(queue)
    visited: set[str] = set()
    topological_order: list[str] = []
    while queue:
        node_id = heapq.heappop(queue)
        visited.add(node_id)
        topological_order.append(node_id)
        for target_id in outgoing[node_id]:
            layers[target_id] = max(layers[target_id], layers[node_id] + 1)
            indegree[target_id] -= 1
            if indegree[target_id] == 0:
                heapq.heappush(queue, target_id)

    # ProcessGraph currently does not reject cycles.  Keep rendering useful for
    # such input by placing unvisited nodes in stable fallback layers.
    fallback_layer = max((layers[node_id] for node_id in visited), default=-1) + 1
    remaining = sorted(set(node_ids) - visited)
    for offset, node_id in enumerate(remaining):
        layers[node_id] = fallback_layer + offset
    compact_order = [*topological_order, *remaining]
    visual_rows: list[list[str]] = []
    for start in range(0, len(compact_order), _MAX_COLUMNS):
        row = compact_order[start : start + _MAX_COLUMNS]
        if len(visual_rows) % 2:
            row = list(reversed(row))
        visual_rows.append(row)
    return visual_rows, layers


def _load_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        (
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/Library/Fonts/Arial Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf",
        )
        if bold
        else (
            "/System/Library/Fonts/Supplemental/Arial.ttf",
            "/Library/Fonts/Arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
        )
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow versions before the ``size`` keyword.
        return ImageFont.load_default()


def _text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> tuple[int, int]:
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return right - left, bottom - top


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
    *,
    max_lines: int,
) -> str:
    words = _display_text(text, fallback="").split()
    if not words:
        return ""

    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and _text_size(draw, candidate, font)[0] <= max_width:
            current = candidate
        elif not current and _text_size(draw, word, font)[0] <= max_width:
            current = word
        else:
            if current:
                lines.append(current)
            current = word
            while _text_size(draw, current, font)[0] > max_width and len(current) > 1:
                split_at = max(1, len(current) * max_width // _text_size(draw, current, font)[0])
                lines.append(current[:split_at])
                current = current[split_at:]
    if current:
        lines.append(current)

    if len(lines) <= max_lines:
        return "\n".join(lines)
    lines = lines[:max_lines]
    tail = lines[-1]
    suffix = "..."
    while tail and _text_size(draw, tail + suffix, font)[0] > max_width:
        tail = tail[:-1]
    lines[-1] = (tail.rstrip() + suffix) if tail else suffix
    return "\n".join(lines)


def _draw_centered_text(
    draw: ImageDraw.ImageDraw,
    center: tuple[float, float],
    text: str,
    font: ImageFont.ImageFont,
    *,
    fill: str,
) -> None:
    width, height = _text_size(draw, text, font)
    draw.text((center[0] - width / 2, center[1] - height / 2), text, font=font, fill=fill)


def _node_kind(graph: ProcessGraph, node_id: str) -> ProcessNodeKind:
    for node in graph.nodes:
        if node.id == node_id:
            return node.kind
    raise ValueError(f"process graph edge references unknown node: {node_id}")


def _box_anchor(
    box: tuple[float, float, float, float],
    other_box: tuple[float, float, float, float],
    kind: ProcessNodeKind,
) -> tuple[float, float]:
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    other_cx, other_cy = (other_box[0] + other_box[2]) / 2, (other_box[1] + other_box[3]) / 2
    dx, dy = other_cx - cx, other_cy - cy
    if dx == 0 and dy == 0:
        dy = 1
    half_width, half_height = (x1 - x0) / 2, (y1 - y0) / 2
    if kind is ProcessNodeKind.DECISION:
        denominator = abs(dx) / half_width + abs(dy) / half_height
        scale = 1.0 / (denominator or 1.0)
    else:
        scale = min(
            half_width / abs(dx) if dx else float("inf"),
            half_height / abs(dy) if dy else float("inf"),
        )
    return (cx + dx * scale, cy + dy * scale)


def _draw_arrowhead(
    draw: ImageDraw.ImageDraw,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    fill: str,
) -> None:
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy) or 1.0
    ux, uy = dx / length, dy / length
    size = 10.0
    base_x, base_y = end[0] - ux * size, end[1] - uy * size
    left = (base_x - uy * size * 0.55, base_y + ux * size * 0.55)
    right = (base_x + uy * size * 0.55, base_y - ux * size * 0.55)
    draw.polygon((end, left, right), fill=fill)


def _draw_node(
    draw: ImageDraw.ImageDraw,
    box: tuple[float, float, float, float],
    node: ProcessNode,
    font: ImageFont.ImageFont,
) -> None:
    x0, y0, x1, y1 = box
    fill, outline = _KIND_COLORS[node.kind]
    if node.kind in (ProcessNodeKind.START, ProcessNodeKind.END):
        draw.rounded_rectangle(box, radius=int((y1 - y0) / 2), fill=fill, outline=outline, width=3)
        max_width = int(x1 - x0 - 42)
    elif node.kind is ProcessNodeKind.DECISION:
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        points = ((cx, y0), (x1, cy), (cx, y1), (x0, cy))
        draw.polygon(points, fill=fill)
        draw.line((*points, points[0]), fill=outline, width=3, joint="curve")
        max_width = int((x1 - x0) * 0.58)
    else:
        draw.rounded_rectangle(box, radius=10, fill=fill, outline=outline, width=3)
        max_width = int(x1 - x0 - 30)

    wrapped = _wrap_text(draw, node.label, font, max_width, max_lines=5)
    bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=4, align="center")
    text_width, text_height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.multiline_text(
        ((x0 + x1 - text_width) / 2, (y0 + y1 - text_height) / 2),
        wrapped,
        font=font,
        fill=_TEXT_COLOR,
        spacing=4,
        align="center",
    )


def _draw_edge_label(
    draw: ImageDraw.ImageDraw,
    center: tuple[float, float],
    text: str,
    font: ImageFont.ImageFont,
    image_width: int,
) -> None:
    wrapped = _wrap_text(draw, text, font, 150, max_lines=2)
    bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=2, align="center")
    text_width, text_height = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad_x, pad_y = 7, 4
    x0 = max(2, min(image_width - text_width - 2 * pad_x - 2, center[0] - text_width / 2 - pad_x))
    y0 = max(2, center[1] - text_height / 2 - pad_y)
    draw.rounded_rectangle(
        (x0, y0, x0 + text_width + 2 * pad_x, y0 + text_height + 2 * pad_y),
        radius=5,
        fill=_EDGE_LABEL_FILL,
        outline="#CBD5E1",
        width=1,
    )
    draw.multiline_text(
        (x0 + pad_x, y0 + pad_y),
        wrapped,
        font=font,
        fill=_TEXT_COLOR,
        spacing=2,
        align="center",
    )
