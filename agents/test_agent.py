import os
import subprocess
from pathlib import Path

from agents.base_agent import (
    BaseAgent, OutputContractError, run_claude,
    RateLimitError, PromptTooLongError, TransientError, REPO_DIR  # noqa: F401
)
from orchestrator.loader import compose_prompt, load_registry


_ORCHESTRATOR_ROOT = Path(__file__).parent.parent
_TARGET_ROOT = Path(REPO_DIR)


def run_git(args: list[str]):
    result = subprocess.run(
        ["git"] + args,
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed (exit {result.returncode}):\n{result.stderr}")
    return result.stdout.strip()


class TestAgent(BaseAgent):
    def parse_response(self, text: str) -> dict:
        for line in text.splitlines():
            if line.startswith("TEST_FILE:"):
                path = line[len("TEST_FILE:"):].strip()
                if path:
                    return {"test_file_path": path}
        raise OutputContractError(
            agent="tester",
            task="write-tests",
            expected="TEST_FILE: <path>",
            got_preview=text[:200],
        )

    async def run(self, context: dict) -> dict:
        issue_number = context["issue_number"]
        print(f"[Test] Writing tests for issue #{issue_number}...")

        registry = load_registry(_ORCHESTRATOR_ROOT, _TARGET_ROOT)
        enriched = {**context, "issue_description": context.get("issue_body", "")}
        prompt = compose_prompt(
            "tester", "write-tests", enriched, registry, _ORCHESTRATOR_ROOT, _TARGET_ROOT
        )

        response = run_claude(prompt, allowed_tools="Read,Edit,Bash", model="claude-sonnet-4-6")

        parsed = self.parse_response(response)
        test_file_path = parsed["test_file_path"]
        print(f"[Test] Tests written to {test_file_path}")

        abs_path = os.path.join(REPO_DIR, test_file_path)
        test_code = ""
        if os.path.exists(abs_path):
            with open(abs_path, encoding="utf-8", errors="replace") as f:
                test_code = f.read()
        else:
            print(f"[Test] Warning: expected test file not found at {abs_path}")

        return {
            "agent": "test",
            "issue_number": issue_number,
            "test_file_path": test_file_path,
            "test_code": test_code,
            "response": f"Test file: {test_file_path}\n\n{test_code}",
        }
