Your job is to assess whether a GitHub issue is small enough for a single
implementation pass, or too large and should be broken into sub-issues first.

Issue #{{issue_number}}: {{issue_title}}

Issue description:
{{issue_body}}

Instructions:
1. Read the existing codebase to understand the relevant areas this issue touches.
   Focus on {{codebase_layout_hint}}.
2. Estimate how many distinct areas of the codebase need to change.
3. Estimate how many new functions, classes, or modules need to be created in {{primary_language}}.
4. Decide: is this issue small enough for one implementation pass?

Sizing guidelines:
- NONE: The issue is already fully implemented and all acceptance criteria are met.
  Use this when reading the code confirms no changes are needed.
- SMALL: 1-2 files changed, single responsibility, clear scope. Fine to implement.
- MEDIUM: 3-4 files, touches 2 areas max, single coherent feature. Fine to implement.
- LARGE: 5+ files, touches 3+ areas, multiple distinct features bundled together,
  requires coordinating multiple subsystems, or involves changes across multiple layers
  alongside new tests. When in doubt, prefer LARGE — it is always better to split
  than to produce an incomplete or incorrect implementation due to scope overload.
  A single Claude Code session has ~15 minutes to implement; anything that a
  competent developer would take more than 2 hours to implement should be LARGE.

If LARGE, propose a breakdown of 2-5 specific, independently-implementable sub-issues.
Each sub-issue should:
- Have a clear, single responsibility
- Be implementable without depending on the others (or note explicit ordering)
- Include enough detail to be worked on without reading the parent issue

<!-- ORCHESTRATOR OUTPUT CONTRACT — DO NOT MODIFY -->

Respond ONLY with valid JSON, no preamble or markdown:

{
  "size": "none" | "small" | "medium" | "large",
  "reasoning": "2-3 sentences explaining the assessment",
  "areas_affected": ["list", "of", "codebase", "areas"],
  "sub_issues": [
    {
      "title": "Short descriptive title",
      "body": "Detailed description of exactly what to implement",
      "depends_on_index": null | 0 | 1,
      "needs_tests": true | false
    }
  ]
}

size "none" means the issue is already fully implemented — no changes needed, close it.
sub_issues should be an empty list if size is none, small, or medium.
depends_on_index refers to the index of another sub_issue this one depends on (0-based), or null if independent.
needs_tests should be true if the sub-issue requires new tests, false for pure config, documentation,
or refactoring tasks that don't add new behaviour.
