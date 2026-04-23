from agents.config_driven_agent import ConfigDrivenAgent
from agents.base_agent import OutputContractError


class RefactorAgent(ConfigDrivenAgent):
    def __init__(self):
        super().__init__("refactor")

    def parse_response(self, text: str) -> dict:
        for line in text.splitlines():
            if line.startswith("COMMIT:"):
                commit_message = line[len("COMMIT:"):].strip()
                if commit_message:
                    return {"commit_message": commit_message}
        raise OutputContractError(
            agent="refactor",
            task="refactor",
            expected="COMMIT: <message>",
            got_preview=text[:200],
        )
