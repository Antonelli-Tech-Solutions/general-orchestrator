# agents/planner_agent.py
from agents.base_agent import run_claude, TransientError

PLANNER_PROMPT = """\
You are a technical planning agent for the Spades Online card game backend.

Your job is to assess whether a GitHub issue is small enough for a single
implementation pass, or too large and should be broken into sub-issues first.

Issue #{issue_number}: {issue_title}

Issue description:
{issue_body}

Instructions:
1. Read the existing codebase to understand the relevant areas this issue touches.
   Focus on server/routes/, server/game/, server/middleware/, and test/ directories.
2. Estimate how many distinct areas of the codebase need to change.
3. Estimate how many new functions, routes, or subsystems need to be created.
4. Decide: is this issue small enough for one implementation pass?

Sizing guidelines:
- NONE: The issue is already fully implemented and all acceptance criteria are met.
  Use this when reading the code confirms no changes are needed.
- SMALL: 1-2 files changed, single responsibility, clear scope. Fine to implement.
- MEDIUM: 3-4 files, touches 2 areas max, single coherent feature. Fine to implement.
- LARGE: 5+ files, touches 3+ areas, multiple distinct features bundled together,
  requires coordinating multiple subsystems, or involves both server AND client changes
  alongside new tests. When in doubt, prefer LARGE — it is always better to split
  than to produce an incomplete or incorrect implementation due to scope overload.
  A single Claude Code session has ~15 minutes to implement; anything that a
  competent developer would take more than 2 hours to implement should be LARGE.

If LARGE, propose a breakdown of 2-5 specific, independently-implementable sub-issues.
Each sub-issue should:
- Have a clear, single responsibility
- Be implementable without depending on the others (or note explicit ordering)
- Include enough detail to be worked on without reading the parent issue

Respond ONLY with valid JSON, no preamble or markdown:
{{
  "size": "none" | "small" | "medium" | "large",
  "reasoning": "2-3 sentences explaining the assessment",
  "areas_affected": ["list", "of", "codebase", "areas"],
  "sub_issues": [
    {{
      "title": "Short descriptive title",
      "body": "Detailed description of exactly what to implement",
      "depends_on_index": null | 0 | 1,
      "needs_tests": true | false
    }}
  ]
}}

size "none" means the issue is already fully implemented — no changes needed, close it.
sub_issues should be an empty list if size is none, small, or medium.
depends_on_index refers to the index of another sub_issue this one depends on (0-based), or null if independent.
needs_tests should be true if the sub-issue requires new tests, false for pure config, documentation,
or refactoring tasks that don't add new behaviour.
"""


class PlannerAgent:

    def assess(self, issue_number: int, issue_title: str, issue_body: str) -> dict:
        """
        Assess issue complexity. Returns a dict with:
          - size: "small" | "medium" | "large"
          - reasoning: str
          - areas_affected: list[str]
          - sub_issues: list[dict]  — empty if small/medium
        """
        print(f"[Planner] Assessing complexity of issue #{issue_number}...")

        prompt = PLANNER_PROMPT.format(
            issue_number=issue_number,
            issue_title=issue_title,
            issue_body=issue_body,
        )

        raw = run_claude(prompt, allowed_tools="Read,Bash", model="claude-sonnet-4-6")

        if not raw.strip():
            raise TransientError(
                f"[Planner] Empty response assessing issue #{issue_number}"
            )

        result = self._extract_json(raw)

        size = result.get("size", "medium")
        reasoning = result.get("reasoning", "")
        areas = result.get("areas_affected", [])
        sub_issues = result.get("sub_issues", [])

        print(f"[Planner] Size: {size} — {reasoning}")
        if areas:
            print(f"[Planner] Areas affected: {', '.join(areas)}")
        if sub_issues:
            print(f"[Planner] Suggested {len(sub_issues)} sub-issues:")
            for i, s in enumerate(sub_issues):
                dep = f" (depends on #{i})" if s.get("depends_on_index") is not None else ""
                print(f"  {i+1}. {s['title']}{dep}")

        return result

    def _extract_json(self, raw: str) -> dict:
        import json
        # Try direct parse first
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        # Strip markdown fences
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            cleaned = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
        # Find first { ... } block
        import re
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        # Fall back to medium with no breakdown
        print("[Planner] Warning: could not parse JSON response, defaulting to medium")
        return {"size": "medium", "reasoning": "Could not parse planner response", "areas_affected": [], "sub_issues": []}
