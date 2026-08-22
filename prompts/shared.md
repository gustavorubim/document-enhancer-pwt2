# Shared source-grounding and writing rules

Use only business information supported by the supplied source blocks. The template describes
what the output should contain, but it is not evidence that a business fact is true.

Never invent owners, dates, deadlines, thresholds, systems, tools, policies, approvals, metrics,
evidence, decisions, or procedural actions. Every factual statement must be traceable to one or
more supplied source block IDs. When information is absent, inconsistent, or too vague to use,
preserve that limitation with one explicit `[MISSING: ...]`, `[CONFLICT: ...]`, or `[UNCLEAR: ...]`
callout and a deduplicated owner question.

Write a complete desktop procedure, not an executive summary. Retain useful operational details:
purpose and intended outcome; audience and scope; prerequisites; access, systems, tools, files,
and inputs; roles; triggers and timing; numbered actions and substeps; decision conditions;
validation; expected results; evidence; warnings; exceptions; failure conditions; recovery; and
escalation. Identify actor, action, input, tool, condition, and expected result whenever the source
provides them. Preserve examples, qualifications, transitions, and the order of multi-step
instructions. Do not compress several source steps into one summary sentence.

Use readable prose, short section introductions, numbered steps for sequences, and bullets,
notes, warnings, or tables only when they improve usability. Reorganize and rewrite for clarity,
but avoid filler, repetition, generic best-practice advice, and vague phrases when the source gives
the actual process.
