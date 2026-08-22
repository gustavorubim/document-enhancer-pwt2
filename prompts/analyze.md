# Analyze and map the complete source

Analyze every source block against every ordered template section and every requirement. Return
one `AnalysisMapping` that follows the supplied schema.

For each template requirement, assign exactly one status: `supported`, `partially_supported`,
`missing`, `conflicting`, or `not_applicable`. Explain the conclusion, cite the source block IDs
examined, distinguish genuinely supporting blocks, identify gaps, and recommend only
source-compatible remediation.

Account for every meaningful source block. State its target destination, whether it maps directly,
was split across targets, was combined with other material, remains unresolved, or was
intentionally omitted, and explain why. Assess every required desktop-procedure component.

Create one stable gap for each distinct missing fact, ambiguity, or conflict. Consolidate repeated
mentions of the same issue. Create one deduplicated owner question per genuine missing business
decision, ambiguity, or conflict. Questions may suggest the type of information needed, but may
not suggest an ungrounded business value.

The macro assessment must say whether the source is usable as a desktop procedure, summarize its
strengths and most important deficiencies, and describe the overall remediation needed.
