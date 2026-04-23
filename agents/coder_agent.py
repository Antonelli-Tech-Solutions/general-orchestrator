import os
import subprocess
from pathlib import Path

from github import Github
from agents.base_agent import (
    BaseAgent, OutputContractError, run_claude,
    RateLimitError, PromptTooLongError, TransientError, REPO_DIR  # noqa: F401
)
from orchestrator.loader import compose_prompt, interpolate, load_registry


_ORCHESTRATOR_ROOT = Path(__file__).parent.parent
_TARGET_ROOT = Path(REPO_DIR)


def get_repo():
    g = Github(os.getenv("GITHUB_TOKEN"))
    return g.get_repo(os.getenv("GITHUB_REPO"))


def branch_name(issue_number: int) -> str:
    return f"fix/issue-{issue_number}"


def repo_url() -> str:
    return f"https://{os.getenv('GITHUB_TOKEN')}@github.com/{os.getenv('GITHUB_REPO')}.git"


def find_existing_pr(repo, issue_number: int):
    """Return an open PR for this issue's branch, or None."""
    branch = branch_name(issue_number)
    pulls = repo.get_pulls(state="open", head=f"{repo.owner.login}:{branch}")
    for pr in pulls:
        return pr
    return None


def branch_exists_on_remote(issue_number: int) -> bool:
    branch = branch_name(issue_number)
    result = subprocess.run(
        ["git", "ls-remote", "--heads", "origin", branch],
        cwd=REPO_DIR,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


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


class CoderAgent(BaseAgent):
    def __init__(self):
        self._ensure_local_repo()

    def parse_response(self, text: str) -> dict:
        for line in text.splitlines():
            if line.startswith("COMMIT:"):
                commit_message = line[len("COMMIT:"):].strip()
                if commit_message:
                    return {"commit_message": commit_message}
        raise OutputContractError(
            agent="coder",
            task="implement",
            expected="COMMIT: <message>",
            got_preview=text[:200],
        )

    def implement(
        self,
        issue_number: int,
        issue_description: str,
        test_file_path: str,
        test_code: str,
        reviewer_feedback: str | None = None,
        review_history: list | None = None,
        task: str = "implement",
    ) -> dict:
        repo = get_repo()
        branch = branch_name(issue_number)

        # Always start from a clean state
        self._reset_to_main()

        # Switch to issue branch (create or reuse)
        remote_exists = branch_exists_on_remote(issue_number)
        default = self._get_default_branch()
        if remote_exists:
            try:
                run_git(["checkout", branch])
            except RuntimeError:
                run_git(["checkout", "-b", branch, f"origin/{branch}"])
            run_git(["pull", "origin", branch])
            print(f"[Coder] Checked out existing branch {branch}.")
        else:
            run_git(["checkout", "-b", branch])
            print(f"[Coder] Created new branch {branch} from {default}.")

        # Build prompt using compose_prompt
        registry = load_registry(_ORCHESTRATOR_ROOT, _TARGET_ROOT)

        prompt_context: dict = {
            "issue_number": issue_number,
            "issue_description": issue_description,
        }

        if task == "fix-ci":
            prompt_context["ci_errors"] = reviewer_feedback or ""
        elif task == "fix-review":
            prompt_context["reviewer_feedback"] = reviewer_feedback or ""
        else:  # implement
            prompt_context["test_file_path"] = test_file_path
            prompt_context["test_code"] = test_code
            prompt_context["feedback_section"] = ""

        prompt = compose_prompt("coder", task, prompt_context, registry, _ORCHESTRATOR_ROOT, _TARGET_ROOT)

        print(f"[Coder] Implementing issue #{issue_number} (task: {task})...")

        # Claude Code runs in REPO_DIR with full Read/Edit/Bash access.
        # It reads the codebase, writes files, and commits — all autonomously.
        response = run_claude(prompt, allowed_tools="Read,Edit,Bash", model="claude-opus-4-7")

        # Parse the COMMIT and SUMMARY lines
        commit_message = None
        summary = None
        for line in response.splitlines():
            if line.startswith("COMMIT:"):
                commit_message = line[len("COMMIT:"):].strip()
            elif line.startswith("SUMMARY:"):
                summary = line[len("SUMMARY:"):].strip()

        if commit_message:
            print(f"[Coder] Committed: {commit_message}")
        elif not response.strip():
            # Empty response — Claude likely timed out
            raise TransientError(
                f"[Coder] Claude returned an empty response for issue #{issue_number} "
                f"— likely a timeout. Will retry."
            )
        else:
            print("[Coder] Warning: could not parse COMMIT line — Claude may not have committed.")

        # Check whether Claude actually committed anything before pushing.
        # Compare against origin/branch (not origin/default) so retries that build
        # on top of a previous attempt's commits are detected correctly.
        # Fall back to comparing against default branch if origin/branch doesn't exist yet.
        try:
            commits_ahead = run_git(["rev-list", f"origin/{branch}..HEAD", "--count"])
        except RuntimeError:
            commits_ahead = run_git(["rev-list", f"origin/{default}..HEAD", "--count"])

        if commits_ahead == "0":
            # Check if Claude explicitly indicated no changes were needed
            # rather than silently failing to commit
            response_lower = response.lower()
            no_changes_phrases = [
                "no commit needed",
                "no changes needed",
                "no issues to fix",
                "nothing to fix",
                "no changes are needed",
                "no fixes needed",
                "code is correct",
                "withdrawn",
                "already committed",
                "already complete",
                "already implemented",
                "already exists",
                "working tree is clean",
                "implementation is complete",
                "implementation is already",
            ]
            if any(phrase in response_lower for phrase in no_changes_phrases):
                print(f"[Coder] Claude indicated no changes needed for issue #{issue_number} — treating as clean.")
                # Return without a commit — orchestrator will proceed to merge
                existing_pr = find_existing_pr(repo, issue_number)
                pr_number = existing_pr.number if existing_pr else None
                return {
                    "agent": "coder",
                    "issue_number": issue_number,
                    "pr_number": pr_number,
                    "branch": branch,
                    "code_changes": run_git(["diff", f"origin/{default}..HEAD"]),
                    "commit_message": "(no changes needed)",
                    "summary": response[:300],
                }
            raise RuntimeError(
                f"[Coder] Claude did not commit any changes for issue #{issue_number}. "
                f"The branch has no new commits. "
                f"Claude's response was:\n{response[:500]}"
            )

        # Proactively rebase onto latest default before pushing so PRs are always clean.
        # This also catches conflicts early rather than leaving them in GitHub's UI.
        run_git(["fetch", "origin"])
        needs_rebase = run_git(["rev-list", f"HEAD..origin/{default}", "--count"]) != "0"
        if needs_rebase:
            rebase_ok = self._rebase_onto_default(branch, default, issue_number)
            if not rebase_ok:
                raise RuntimeError(
                    f"[Coder] Rebase of {branch} onto {default} failed for "
                    f"issue #{issue_number} — merge conflicts could not be resolved."
                )

        # Push — force-with-lease handles retry divergence
        try:
            run_git(["push", "--force-with-lease", "origin", branch])
            print(f"[Coder] Pushed to origin/{branch}.")
        except RuntimeError as push_err:
            err_str = str(push_err)
            if "non-fast-forward" in err_str or "rejected" in err_str:
                # Fetch latest and try rebasing
                run_git(["fetch", "origin"])
                rebase_ok = self._rebase_onto_default(branch, default, issue_number)
                if rebase_ok:
                    run_git(["push", "--force-with-lease", "origin", branch])
                    print(f"[Coder] Pushed to origin/{branch} after rebase.")
                else:
                    raise RuntimeError(
                        f"[Coder] Could not push or rebase branch {branch} for "
                        f"issue #{issue_number} — merge conflicts could not be resolved."
                    )
            else:
                raise

        # Open PR if one doesn't exist yet
        existing_pr = find_existing_pr(repo, issue_number)
        if existing_pr:
            pr_number = existing_pr.number
            print(f"[Coder] Updated existing PR #{pr_number}.")
        else:
            pr = repo.create_pull(
                title=f"Fix #{issue_number}",
                body=(
                    f"Closes #{issue_number}\n\n"
                    f"Automatically generated by the orchestrator."
                ),
                head=branch,
                base=default,
            )
            pr_number = pr.number
            print(f"[Coder] Opened PR #{pr_number}.")

        # Get the diff for the reviewer
        code_changes = run_git(["diff", f"origin/{default}..HEAD"])

        return {
            "agent": "coder",
            "issue_number": issue_number,
            "pr_number": pr_number,
            "branch": branch,
            "code_changes": code_changes,
            "commit_message": commit_message or "",
            "summary": summary or "",
        }

    def implement_inline_fixes(
        self,
        issue_number: int,
        inline_findings: list[dict],
    ) -> dict:
        """
        Apply low-effort inline review fixes to the current branch.
        Called after a successful review pass when there are fix_inline findings.
        """
        findings_text = ""
        for i, f in enumerate(inline_findings, 1):
            location = ""
            if f.get("file"):
                location = f" ({f['file']}"
                if f.get("line"):
                    location += f" line {f['line']}"
                location += ")"
            findings_text += f"{i}. [{f['severity']}] {f['title']}{location}\n   {f['body']}\n\n"

        registry = load_registry(_ORCHESTRATOR_ROOT, _TARGET_ROOT)
        prompt_context = {
            "issue_number": issue_number,
            "inline_findings": findings_text,
        }
        prompt = compose_prompt(
            "coder", "fix-inline", prompt_context, registry, _ORCHESTRATOR_ROOT, _TARGET_ROOT
        )

        print(f"[Coder] Applying {len(inline_findings)} inline fix(es) for issue #{issue_number}...")
        response = run_claude(prompt, allowed_tools="Read,Edit,Bash", model="claude-sonnet-4-6")

        commit_message = None
        for line in response.splitlines():
            if line.startswith("COMMIT:"):
                commit_message = line[len("COMMIT:"):].strip()
                break

        if commit_message:
            print(f"[Coder] Inline fixes committed: {commit_message}")
        else:
            print("[Coder] Warning: could not parse COMMIT line for inline fixes.")

        branch = run_git(["branch", "--show-current"])
        run_git(["push", "origin", branch])
        print(f"[Coder] Pushed inline fixes to origin/{branch}.")

        return {"code_changes": run_git(["diff", f"origin/{self._get_default_branch()}..HEAD"])}

    async def run(self, context: dict) -> dict:
        reviewer_feedback = context.get("reviewer_feedback")
        mode = context.get("mode")

        # Inline fix path — explicit mode or inline_findings present
        if mode == "fix-inline" or context.get("inline_findings"):
            return self.implement_inline_fixes(
                issue_number=context["issue_number"],
                inline_findings=context.get("inline_findings", []),
            )

        # Determine task from explicit mode or existing context signals
        if mode:
            task = mode
        elif reviewer_feedback and reviewer_feedback.startswith("The CI failure"):
            task = "fix-ci"
        elif reviewer_feedback:
            task = "fix-review"
        else:
            task = "implement"

        return self.implement(
            issue_number=context["issue_number"],
            issue_description=context.get("issue_body", ""),
            test_file_path=context.get("test_file_path", ""),
            test_code=context.get("test_code", ""),
            reviewer_feedback=reviewer_feedback,
            review_history=context.get("review_history") or [],
            task=task,
        )

    def _ensure_local_repo(self):
        if not os.path.exists(os.path.join(REPO_DIR, ".git")):
            print(f"[Coder] Cloning repo to {REPO_DIR}...")
            os.makedirs(REPO_DIR, exist_ok=True)
            result = subprocess.run(
                ["git", "clone", repo_url(), REPO_DIR],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise RuntimeError(f"Clone failed:\n{result.stderr}")
            run_git(["config", "user.email", os.getenv("GIT_AUTHOR_EMAIL", "orchestrator@localhost")])
            run_git(["config", "user.name", os.getenv("GIT_AUTHOR_NAME", "Orchestrator")])
            # Set origin/HEAD so symbolic-ref can resolve the default branch
            run_git(["remote", "set-head", "origin", "--auto"])
            print("[Coder] Repo cloned.")
        else:
            print(f"[Coder] Using existing repo at {REPO_DIR}.")

    def _get_default_branch(self) -> str:
        """Get the repo's default branch from the remote."""
        result = subprocess.run(
            ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
            cwd=REPO_DIR, capture_output=True, text=True,
        )
        if result.returncode == 0:
            # e.g. "refs/remotes/origin/dev" -> "dev"
            return result.stdout.strip().split("/")[-1]
        return "main"  # safe fallback

    def _rebase_onto_default(self, branch: str, default: str, issue_number: int) -> bool:
        """
        Rebase the current branch onto origin/default.
        If there are conflicts, invoke Claude to resolve them.
        Returns True if rebase succeeded, False if it failed unrecoverably.
        """
        print(f"[Coder] Rebasing {branch} onto origin/{default}...")
        try:
            run_git(["rebase", f"origin/{default}"])
            print("[Coder] Rebase succeeded cleanly.")
            return True
        except RuntimeError as e:
            if "CONFLICT" not in str(e) and "conflict" not in str(e).lower():
                # Not a conflict — something else went wrong
                run_git(["rebase", "--abort"])
                return False

        # There are conflicts — ask Claude to resolve them
        print("[Coder] Merge conflicts detected — asking Claude to resolve...")

        conflict_template = (_ORCHESTRATOR_ROOT / "defaults" / "prompts" / "coder" / "resolve-conflicts.md").read_text(encoding="utf-8")
        conflict_prompt = interpolate(conflict_template, {
            "branch": branch,
            "default": default,
            "issue_number": issue_number,
        })
        response = run_claude(
            conflict_prompt,
            allowed_tools="Read,Edit,Bash",
            model="claude-opus-4-6",
        )

        if "REBASE: success" in response:
            print("[Coder] Claude resolved merge conflicts successfully.")
            return True
        else:
            print("[Coder] Claude could not resolve conflicts — aborting rebase.")
            try:
                run_git(["rebase", "--abort"])
            except RuntimeError:
                pass
            return False

    def _reset_to_main(self):
        default = self._get_default_branch()
        print(f"[Coder] Resetting to clean {default}...")
        # Discard any modifications to tracked files first (e.g. from a rebase conflict),
        # then clean untracked/gitignored files, then switch branches.
        try:
            run_git(["reset", "--hard", "HEAD"])
        except RuntimeError:
            pass  # HEAD may not exist on a fresh clone
        run_git(["clean", "-fdx"])
        run_git(["checkout", default])
        run_git(["fetch", "origin", "--prune"])
        run_git(["reset", "--hard", f"origin/{default}"])
        # Delete all stale local fix branches so checkout -b never fails
        result = subprocess.run(
            ["git", "branch", "--list", "fix/issue-*"],
            cwd=REPO_DIR, capture_output=True, text=True, encoding="utf-8",
        )
        for local_branch in result.stdout.splitlines():
            local_branch = local_branch.strip().lstrip("* ")
            if local_branch:
                subprocess.run(
                    ["git", "branch", "-D", local_branch],
                    cwd=REPO_DIR, capture_output=True,
                )
        print("[Coder] Clean.")
