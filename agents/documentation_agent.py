import os
import subprocess

from agents.base_agent import run_claude, RateLimitError, PromptTooLongError, TransientError, REPO_DIR  # noqa: F401


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


PROMPT_TEMPLATE = """\
You are a documentation agent for the Spades Online card game.

Review the following code changes and update any documentation files that need to reflect them.

Code changes (git diff --stat):
{code_changes}

Documentation files to consider updating:
- README.md — if new features, setup steps, or environment variables changed
- docs/api.md — if HTTP routes were added, removed, or changed
- docs/websocket.md — if WebSocket events were added, removed, or changed

Instructions:
- Read each documentation file first to understand its current state.
- Only edit files that genuinely need updating — don't touch unaffected docs.
- Write the full updated content of any file you change using the Edit tool.
- If no documentation changes are needed, output exactly: NO_CHANGES
- Otherwise, after writing all files, output exactly one line per file changed:
  UPDATED: <relative/path/to/file.md>
"""


class DocumentationAgent:
    async def run(self, context: dict) -> dict:
        issue_number = context.get("issue_number")
        code_changes = context.get("code_changes", "")
        branch = context.get("branch")

        if not code_changes:
            print("[Docs] No code changes provided — skipping.")
            return {"agent": "documentation", "issue_number": issue_number, "files_updated": []}

        print(f"[Docs] Checking documentation for issue #{issue_number}...")

        prompt = PROMPT_TEMPLATE.format(code_changes=code_changes)

        # Claude Code runs in REPO_DIR so it can read and write doc files directly
        response = run_claude(prompt, allowed_tools="Read,Edit,Bash", model="claude-sonnet-4-6")

        if response.strip() == "NO_CHANGES":
            print("[Docs] No documentation changes needed.")
            return {"agent": "documentation", "issue_number": issue_number, "files_updated": []}

        # Parse UPDATED: lines to know which files were changed
        files_updated = []
        for line in response.splitlines():
            if line.startswith("UPDATED:"):
                files_updated.append(line[len("UPDATED:"):].strip())

        if not files_updated:
            print("[Docs] No UPDATED lines found — assuming no changes.")
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
