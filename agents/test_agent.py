import os
import subprocess
from agents.base_agent import run_claude, RateLimitError, PromptTooLongError, TransientError, REPO_DIR  # noqa: F401

PROMPT_TEMPLATE = """\
You are a test-writing agent for the Spades Online card game backend.

Write comprehensive tests for this GitHub issue, following TDD principles.

Issue #{issue_number}:
{issue_description}

Instructions:
- Read the existing test directories first to understand the patterns and conventions.
- Decide the correct test file path based on what is being tested:
  - Game logic → test/unit/game/
  - API routes → test/integration/
  - WebSocket events → test/integration/
- Use Node's built-in test runner (node:test and node:assert).
- Test both happy paths and edge/error cases.
- Each test must be independent.
- Write the test file to disk at the correct path using the Edit tool.
- After writing, output exactly one line in this format:
  TEST_FILE: <relative/path/to/test/file.test.js>
"""


def run_git(args: list[str]):
    result = subprocess.run(
        ["git"] + args,
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        # Include stderr in error but don't fail on non-empty stderr alone —
        # git writes hints and warnings to stderr even on success
        raise RuntimeError(f"git {' '.join(args)} failed (exit {result.returncode}):\n{result.stderr}")
    return result.stdout.strip()


class TestAgent:
    async def run(self, context: dict) -> dict:
        issue_number = context["issue_number"]
        issue_description = context.get("issue_body", "")

        print(f"[Test] Writing tests for issue #{issue_number}...")

        prompt = PROMPT_TEMPLATE.format(
            issue_number=issue_number,
            issue_description=issue_description,
        )

        response = run_claude(prompt, allowed_tools="Read,Edit,Bash", model="claude-sonnet-4-6")

        # Parse the TEST_FILE line Claude outputs
        test_file_path = None
        for line in response.splitlines():
            if line.startswith("TEST_FILE:"):
                test_file_path = line[len("TEST_FILE:"):].strip()
                break

        if not test_file_path:
            test_file_path = f"test/unit/issue-{issue_number}.test.js"
            print(f"[Test] Warning: could not parse TEST_FILE, using {test_file_path}")
        else:
            print(f"[Test] Tests written to {test_file_path}")

        # Read the file Claude wrote
        abs_path = os.path.join(REPO_DIR, test_file_path)
        test_code = ""
        if os.path.exists(abs_path):
            with open(abs_path, encoding="utf-8", errors="replace") as f:
                test_code = f.read()
        else:
            print(f"[Test] Warning: expected test file not found at {abs_path}")

        # Tests are returned to the coder agent which writes them to the issue
        # branch before implementing. No commit to dev needed.

        return {
            "agent": "test",
            "issue_number": issue_number,
            "test_file_path": test_file_path,
            "test_code": test_code,
            "response": f"Test file: {test_file_path}\n\n{test_code}",
        }
