import json
import re
from pathlib import Path

from agents.base_agent import BaseAgent, OutputContractError, run_claude, REPO_DIR
from agents.config_driven_agent import ConfigDrivenAgent
from orchestrator.loader import compose_prompt, load_registry

_ORCHESTRATOR_ROOT = Path(__file__).parent.parent
_TARGET_ROOT = Path(REPO_DIR)


class ProductPlannerAgent(ConfigDrivenAgent):
    def __init__(self):
        super().__init__("product-planner")

    def parse_response(self, text: str) -> dict:
        """Extract a JSON array of proposed issues (for propose-issues task).

        Tries direct JSON parse, then markdown-fence-stripped parse, then a
        bracket-delimited search.  Raises OutputContractError if none succeed.
        """
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.split("\n")
            inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
            cleaned = "\n".join(inner)
        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, list):
                return {"issues": parsed}
        except json.JSONDecodeError:
            pass
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group())
                if isinstance(parsed, list):
                    return {"issues": parsed}
            except json.JSONDecodeError:
                pass
        raise OutputContractError(
            agent="product-planner",
            task="propose-issues",
            expected="JSON array of proposed issues",
            got_preview=text[:200],
        )

    async def run(self, context: dict) -> dict:
        task = context["task"]
        registry = load_registry(_ORCHESTRATOR_ROOT, _TARGET_ROOT)
        agent_config = registry.get("agents", {}).get(self.agent_name, {})
        model = agent_config.get("model", "claude-sonnet-4-6")
        tools = ",".join(agent_config.get("tools", ["Read", "Bash"]))
        prompt = compose_prompt(
            "product-planner", task, context, registry, _ORCHESTRATOR_ROOT, _TARGET_ROOT
        )
        response = run_claude(prompt, allowed_tools=tools, model=model)

        if task == "draft-prd":
            parsed = BaseAgent.parse_response(self, response)
        else:
            parsed = self.parse_response(response)

        return {"agent": "product-planner", "task": task, **parsed}
