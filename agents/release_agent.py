from agents.config_driven_agent import ConfigDrivenAgent
from agents.base_agent import OutputContractError


class ReleaseAgent(ConfigDrivenAgent):
    def __init__(self):
        super().__init__("release")
        self._current_task: str = ""

    async def run(self, context: dict) -> dict:
        self._current_task = context.get("task", "")
        return await super().run(context)

    def parse_response(self, text: str) -> dict:
        if self._current_task == "bump-version":
            for line in text.splitlines():
                if line.startswith("VERSION:"):
                    version = line[len("VERSION:"):].strip()
                    if version:
                        return {"version": version}
            raise OutputContractError(
                agent="release",
                task="bump-version",
                expected="VERSION: x.y.z",
                got_preview=text[:200],
            )
        return {"response": text}
