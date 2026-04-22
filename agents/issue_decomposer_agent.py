import json
import re
from pathlib import Path

from agents.base_agent import (
    BaseAgent, OutputContractError, TransientError, run_claude,
    RateLimitError, PromptTooLongError, REPO_DIR  # noqa: F401
)
from orchestrator.loader import compose_prompt, load_registry


_ORCHESTRATOR_ROOT = Path(__file__).parent.parent
_TARGET_ROOT = Path(REPO_DIR)


class IssueDecomposerAgent(BaseAgent):

    def parse_response(self, text: str) -> dict:
        # Try direct parse first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        # Strip markdown fences
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            cleaned = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            pass
        # Find first { ... } block
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        raise OutputContractError(
            agent="issue-decomposer",
            task="decompose",
            expected="valid JSON with keys: size, reasoning, areas_affected, sub_issues",
            got_preview=text[:200],
        )

    async def run(self, context: dict) -> dict:
        issue_number = context.get("issue_number")
        print(f"[IssueDecomposer] Assessing complexity of issue #{issue_number}...")

        registry = load_registry(_ORCHESTRATOR_ROOT, _TARGET_ROOT)
        prompt = compose_prompt(
            "issue-decomposer", "decompose", context, registry,
            _ORCHESTRATOR_ROOT, _TARGET_ROOT,
        )

        raw = run_claude(prompt, allowed_tools="Read,Bash", model="claude-sonnet-4-6")

        if not raw.strip():
            raise TransientError(
                f"[IssueDecomposer] Empty response assessing issue #{issue_number}"
            )

        result = self.parse_response(raw)

        size = result.get("size", "")
        reasoning = result.get("reasoning", "")
        areas = result.get("areas_affected", [])
        sub_issues = result.get("sub_issues", [])

        print(f"[IssueDecomposer] Size: {size} — {reasoning}")
        if areas:
            print(f"[IssueDecomposer] Areas affected: {', '.join(areas)}")
        if sub_issues:
            print(f"[IssueDecomposer] Suggested {len(sub_issues)} sub-issues:")
            for i, s in enumerate(sub_issues):
                dep = (
                    f" (depends on #{s['depends_on_index']})"
                    if s.get("depends_on_index") is not None
                    else ""
                )
                print(f"  {i+1}. {s['title']}{dep}")

        return result
