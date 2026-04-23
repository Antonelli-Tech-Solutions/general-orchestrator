import json
import re

from agents.base_agent import OutputContractError
from agents.config_driven_agent import ConfigDrivenAgent

_REQUIRED_KEYS = {"findings", "summary"}


class SecurityAuditorAgent(ConfigDrivenAgent):
    def __init__(self):
        super().__init__("security-auditor")

    def parse_response(self, text: str) -> dict:
        """Extract JSON {findings, summary} from Claude output.

        Tries direct JSON parse, then markdown-fence-stripped parse, then a
        brace-scan. Raises OutputContractError if none succeed or required
        keys are missing.
        """
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            cleaned = "\n".join(inner).strip()

        candidates = [cleaned]
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            candidates.append(match.group())

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
                if isinstance(parsed, dict) and _REQUIRED_KEYS.issubset(parsed):
                    return {
                        "findings": parsed["findings"],
                        "summary": parsed["summary"],
                    }
            except json.JSONDecodeError:
                pass

        raise OutputContractError(
            agent="security-auditor",
            task="audit",
            expected="JSON object with keys: findings, summary",
            got_preview=text[:200],
        )
