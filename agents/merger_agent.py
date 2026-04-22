import subprocess
import time
import os

from github import Github
from github.GithubException import GithubException
from agents.base_agent import REPO_DIR

CI_POLL_INTERVAL_SECONDS = 30
CI_TIMEOUT_SECONDS = 600  # 10 minutes


def get_repo():
    g = Github(os.getenv("GITHUB_TOKEN"))
    return g.get_repo(os.getenv("GITHUB_REPO"))


class MergerAgent:
    def __init__(self):
        pass

    def wait_for_ci(self, pr_number: int) -> str:
        """
        Poll GitHub until all CI checks on the PR are complete.
        Returns "success", "failure", or "timeout".
        """
        repo = get_repo()
        elapsed = 0

        while elapsed < CI_TIMEOUT_SECONDS:
            try:
                pr = repo.get_pull(pr_number)
                commit = repo.get_commit(pr.head.sha)
                check_runs = list(commit.get_check_runs())

                if not check_runs:
                    print("[Merger] No CI checks found — proceeding to merge.")
                    return "success"

                statuses = [run.status for run in check_runs]
                conclusions = [run.conclusion for run in check_runs]

                if any(s != "completed" for s in statuses):
                    print(f"[Merger] CI in progress... ({elapsed}s elapsed)")
                    time.sleep(CI_POLL_INTERVAL_SECONDS)
                    elapsed += CI_POLL_INTERVAL_SECONDS
                    continue

                if all(c == "success" for c in conclusions):
                    return "success"
                else:
                    failed = [c for c in conclusions if c != "success"]
                    print(f"[Merger] CI failed with conclusions: {failed}")
                    return "failure"

            except GithubException as e:
                print(f"[Merger] Warning: error checking CI status: {e}")
                time.sleep(CI_POLL_INTERVAL_SECONDS)
                elapsed += CI_POLL_INTERVAL_SECONDS

        print(f"[Merger] CI timed out after {CI_TIMEOUT_SECONDS}s")
        return "timeout"

    def get_ci_failure_details(self, pr_number: int) -> str:
        """
        Fetch failure details from failed CI check runs.
        Returns a formatted string describing what failed, suitable for
        passing to the coder agent as feedback.
        """
        repo = get_repo()
        try:
            pr = repo.get_pull(pr_number)
            commit = repo.get_commit(pr.head.sha)
            check_runs = list(commit.get_check_runs())

            failed_runs = [r for r in check_runs if r.conclusion not in ("success", "skipped", None)]
            if not failed_runs:
                return "CI failed but no failed check runs found."

            lines = ["CI checks failed. Fix the following issues:\n"]
            for run in failed_runs:
                lines.append(f"Check: {run.name} — {run.conclusion}")
                if run.output and run.output.summary:
                    lines.append(f"Summary: {run.output.summary}")
                if run.output and run.output.text:
                    # Truncate long output
                    text = run.output.text[:3000]
                    lines.append(f"Details:\n{text}")
                lines.append("")

            return "\n".join(lines)

        except GithubException as e:
            return f"Could not fetch CI failure details: {e}"

    def merge_pr(self, pr_number: int) -> dict:
        """
        Merge the PR using the GitHub CLI.
        Returns {"success": True} or {"success": False, "error": "..."}
        """
        try:
            result = subprocess.run(
                ["gh", "pr", "merge", str(pr_number), "--squash", "--delete-branch"],
                cwd=REPO_DIR,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
            )

            if result.returncode == 0:
                print(f"[Merger] PR #{pr_number} merged successfully.")
                return {"success": True}
            else:
                error = result.stderr.strip() or result.stdout.strip()
                print(f"[Merger] Merge failed: {error}")
                return {"success": False, "error": error}

        except subprocess.TimeoutExpired:
            return {"success": False, "error": "gh pr merge timed out after 60s"}
        except FileNotFoundError:
            return {"success": False, "error": "gh CLI not found — is it installed and on PATH?"}

    async def run(self, context: dict) -> dict:
        """Legacy interface — prefer calling wait_for_ci + merge_pr directly."""
        pr_number = context.get("pr_number")
        ci_result = self.wait_for_ci(pr_number)
        if ci_result != "success":
            return {"agent": "merger", "pr_number": pr_number, "success": False,
                    "error": f"CI did not pass (status: {ci_result})"}
        result = self.merge_pr(pr_number)
        return {"agent": "merger", "pr_number": pr_number, **result}
