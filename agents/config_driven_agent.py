import os
from pathlib import Path

from agents.base_agent import BaseAgent, run_claude, REPO_DIR
from orchestrator.loader import compose_prompt, load_registry

_ORCHESTRATOR_ROOT = Path(__file__).parent.parent
_TARGET_ROOT = Path(os.environ.get("TARGET_REPO_PATH", REPO_DIR))


class ConfigDrivenAgent(BaseAgent):
    """Generic agent that reads identity, model, and tools from agents.toml.

    Subclasses override parse_response for structured output; agents with
    unstructured output (returning plain text) use this class directly.
    """

    def __init__(self, agent_name: str):
        self.agent_name = agent_name

    async def run(self, context: dict) -> dict:
        task = context["task"]
        registry = load_registry(_ORCHESTRATOR_ROOT, _TARGET_ROOT)
        agent_config = registry.get("agents", {}).get(self.agent_name, {})
        model = agent_config.get("model", "claude-sonnet-4-6")
        tools = ",".join(agent_config.get("tools", ["Read", "Bash"]))
        prompt = compose_prompt(
            self.agent_name, task, context, registry, _ORCHESTRATOR_ROOT, _TARGET_ROOT
        )
        response = run_claude(prompt, allowed_tools=tools, model=model, cwd=str(_TARGET_ROOT))
        parsed = self.parse_response(response)
        return {"agent": self.agent_name, "task": task, **parsed}
