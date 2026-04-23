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
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed:\n{result.stderr}")
    return result.stdout.strip()


class DocumentationAgent(BaseAgent):
    def parse_response(self, text: str) -> dict:
        if text.strip() == "NO_CHANGES":
            return {"files_updated": []}

        files_updated = []
        for line in text.splitlines():
            if line.startswith("UPDATED:"):
                path = line[len("UPDATED:"):].strip()
                if path:
                    files_updated.append(path)

        if files_updated:
            return {"files_updated": files_updated}

        raise OutputContractError(
            agent="docs-writer",
            task="document",
            expected="NO_CHANGES or one or more UPDATED: <path> lines",
            got_preview=text[:200],
        )

    async def run(self, context: dict) -> dict:
        issue_number = context.get("issue_number")
        code_changes = context.get("code_changes", "")
        branch = context.get("branch")

        if not code_changes:
            print("[Docs] No code changes provided — skipping.")
            return {"agent": "documentation", "issue_number": issue_number, "files_updated": []}

        print(f"[Docs] Checking documentation for issue #{issue_number}...")

        registry = load_registry(_ORCHESTRATOR_ROOT, _TARGET_ROOT)
        prompt = compose_prompt(
            "docs-writer", "document", context, registry, _ORCHESTRATOR_ROOT, _TARGET_ROOT
        )

        # Claude Code runs in REPO_DIR so it can read and write doc files directly.
        # Retry up to 3 times on OutputContractError, feeding the error back so
        # Claude understands what format is required.
        MAX_FORMAT_RETRIES = 3
        current_prompt = prompt
        for attempt in range(1, MAX_FORMAT_RETRIES + 1):
            response = run_claude(current_prompt, allowed_tools="Read,Edit,Bash", model="claude-sonnet-4-6")
            try:
                parsed = self.parse_response(response)
                break
            except OutputContractError as e:
                if attempt == MAX_FORMAT_RETRIES:
                    raise
                print(f"[Docs] OutputContractError on attempt {attempt}/{MAX_FORMAT_RETRIES}: {e}")
                current_prompt = (
                    prompt
                    + f"\n\n---\n**Previous attempt failed the output contract.**\n"
                    f"Error: {e}\n"
                    f"Your last response was:\n```\n{response[:500]}\n```\n"
                    f"Please re-read the output contract at the bottom of this prompt "
                    f"and respond with ONLY `NO_CHANGES` or one or more `UPDATED: <path>` lines."
                )
        files_updated = parsed["files_updated"]

        if not files_updated:
            print("[Docs] No documentation changes needed.")
            return {"agent": "documentation", "issue_number": issue_number, "files_updated": []}

        for f in files_updated:
            print(f"[Docs] Updated {f}")

        # Commit and push doc changes to the issue branch so they're in the PR
        try:
            run_git(["add"] + files_updated)
            run_git(["commit", "-m", f"docs: update documentation for issue #{issue_number}"])
            if branch:
                run_git(["push", "origin", branch])
                print(f"[Docs] Committed and pushed doc changes to {branch}.")
            else:
                print("[Docs] Warning: no branch provided — committed but not pushed.")
        except RuntimeError as e:
            print(f"[Docs] Warning: could not commit doc changes: {e}")

        return {
            "agent": "documentation",
            "issue_number": issue_number,
            "files_updated": files_updated,
        }
