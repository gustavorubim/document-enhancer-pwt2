# Derive the process graph

Derive one compact process graph from the source-supported desktop procedure. Return only the
typed `ProcessGraph` data requested by the application; the application owns Mermaid syntax and
image rendering.

Represent the trigger as a start node, the complete ordered actions as action nodes, each genuine
decision or branch as a decision node, and the source-supported completion state as an end node.
Preserve important branch labels, conditions, failure paths, recovery loops, and escalation when
they are explicit in the source. Do not invent a branch, role, threshold, system, or outcome.

Use short readable labels and cite the supporting source block IDs on every node and edge. When a
decision is unresolved or conflicting, label that limitation rather than silently choosing a value.
Keep the graph at no more than 24 nodes and 40 edges.
