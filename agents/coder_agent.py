import os
import subprocess

from agents.base_agent import run_claude, REPO_DIR, TransientError
from github import Github
from github.GithubException import GithubException


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


PROMPT_TEMPLATE = """\
You are a coding agent for the Spades Online card game backend.

Implement the code required to resolve this GitHub issue and make the tests pass.

Issue #{issue_number}:
{issue_description}

Tests to pass ({test_file_path}):
{test_code}
{feedback_section}
Instructions:
- Read the existing codebase to understand patterns and conventions before writing anything.
- Implement only what is needed to make the tests pass.
- Follow ES Modules (import/export, not require).
- Use async/await for async operations.
- Keep game logic in server/game/.
- Add appropriate error handling and logging.
- Write all files to disk using the Edit tool.
- NEVER modify or delete .gitignore.
- NEVER run npm install — node_modules is not committed to this repo.
- Once all files are written, stage and commit with:
    git add -A
    git commit -m "fix: <short description of what you implemented>"
- After committing, output exactly two lines in this format:
  COMMIT: <the commit message you used>
  SUMMARY: <2-3 sentences describing what files were changed and why>
"""

FEEDBACK_SECTION = """\

Previous attempt was rejected by the reviewer. You MUST address every point below:
{reviewer_feedback}
"""

RETRY_PROMPT_TEMPLATE = """\
You are a coding agent for the Spades Online card game backend.

A previous implementation for issue #{issue_number} was rejected by the reviewer.
Your job is to fix ONLY the specific issues listed below — do not rewrite or
restructure code that was not flagged. The existing implementation is already on
the branch; read it first, then make the minimum changes needed.

Issue #{issue_number}:
{issue_description}

Issues to fix:
{reviewer_feedback}

Instructions:
- Read the existing files on this branch before making any changes.
- Make targeted fixes only — do not refactor beyond what is listed.
- Follow ES Modules (import/export, not require).
- NEVER modify or delete .gitignore.
- NEVER run npm install.
- Write all changes to disk using the Edit tool.
- Once all fixes are applied, stage and commit with:
    git add -A
    git commit -m "fix: address review feedback for issue #{issue_number}"
- After committing, output exactly two lines in this format:
  COMMIT: <the commit message you used>
  SUMMARY: <2-3 sentences describing what you changed and why>
"""

CI_FIX_PROMPT_TEMPLATE = """\
You are a coding agent for the Spades Online card game backend.

CI checks failed on the PR for issue #{issue_number}. Your job is to fix the
failing tests — do not rewrite working code. Read the existing implementation
on this branch first, then make the minimum changes needed to make CI pass.

Issue #{issue_number}:
{issue_description}

CI failure details and diagnosis:
{ci_feedback}

Instructions:
- Read the existing files on this branch before making any changes.
- Make targeted fixes only — do not refactor beyond what is needed to fix CI.
- If the diagnosis says the TEST is wrong, fix the test assertions.
- If the diagnosis says the IMPLEMENTATION is wrong, fix the implementation.
- NEVER modify or delete .gitignore.
- NEVER run npm install.
- Write all changes to disk using the Edit tool.
- Once all fixes are applied, stage and commit with:
    git add -A
    git commit -m "fix: resolve CI failure for issue #{issue_number}"
- After committing, output exactly two lines in this format:
  COMMIT: <the commit message you used>
  SUMMARY: <2-3 sentences describing what you changed and why>
"""


INLINE_FIX_PROMPT_TEMPLATE = """\
You are a coding agent for the Spades Online card game backend.

The code for issue #{issue_number} has been implemented and reviewed. The reviewer
found the following low-effort improvements that should be fixed immediately in this
PR rather than deferred to the backlog. Each fix should be a small, targeted change.

Inline fixes to apply:
{inline_findings}

Instructions:
- Read the relevant files before making any changes.
- Apply each fix as a minimal, targeted change — do not refactor beyond what is listed.
- Write all changes to disk using the Edit tool.
- Once all fixes are applied, stage and commit with:
    git add -A
    git commit -m "fix: apply inline review fixes for issue #{issue_number}"
- After committing, output exactly two lines in this format:
  COMMIT: <the commit message you used>
  SUMMARY: <2-3 sentences describing what files were changed and why>
"""


class CoderAgent:
    def __init__(self):
        self._ensure_local_repo()

    def implement(
        self,
        issue_number: int,
        issue_description: str,
        test_file_path: str,
        test_code: str,
        reviewer_feedback: str | None = None,
        review_history: list | None = None,
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

        # Build prompt — three cases:
        # 1. CI failure feedback → targeted CI fix prompt
        # 2. Reviewer feedback  → targeted review fix prompt
        # 3. No feedback        → fresh implementation prompt
        is_ci_feedback = (
            reviewer_feedback is not None
            and reviewer_feedback.startswith("The CI failure")
        )

        if is_ci_feedback:
            prompt = CI_FIX_PROMPT_TEMPLATE.format(
                issue_number=issue_number,
                issue_description=issue_description,
                ci_feedback=reviewer_feedback,
            )
        elif reviewer_feedback:
            history_section = ""
            if review_history:
                history_section = "\nPrevious attempts summary (for context only):\n"
                for i, h in enumerate(review_history, 1):
                    history_section += f"  Attempt {i}: {h}\n"
            prompt = RETRY_PROMPT_TEMPLATE.format(
                issue_number=issue_number,
                issue_description=issue_description,
                reviewer_feedback=reviewer_feedback,
                history_section=history_section,
            )
        else:
            prompt = PROMPT_TEMPLATE.format(
                issue_number=issue_number,
                issue_description=issue_description,
                test_file_path=test_file_path,
                test_code=test_code,
                feedback_section="",
            )

        print(f"[Coder] Implementing issue #{issue_number}...")

        # Claude Code runs in REPO_DIR with full Read/Edit/Bash access.
        # It reads the codebase, writes files, and commits — all autonomously.
        response = run_claude(prompt, allowed_tools="Read,Edit,Bash", model="claude-opus-4-6")

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

        # Restore .gitignore from the default branch if Claude deleted or modified it,
        # and remove any node_modules files that got committed as a result.
        try:
            run_git(["checkout", f"origin/{default}", "--", ".gitignore"])

            # Remove node_modules from the index if Claude committed it
            # (this is non-destructive — files stay on disk, just removed from git tracking)
            try:
                run_git(["rm", "-r", "--cached", "--ignore-unmatch", "node_modules/"])
            except RuntimeError:
                pass

            # Check if anything changed and amend if so
            restore_status = run_git(["status", "--porcelain"])
            if restore_status:
                run_git(["add", ".gitignore"])
                run_git(["commit", "--amend", "--no-edit"])
                print(f"[Coder] Warning: .gitignore was modified by Claude — restored, "
                      f"removed node_modules from tracking, and amended commit.")
        except RuntimeError:
            pass  # .gitignore doesn't exist in default branch either — nothing to restore

        # Clean node_modules from disk so it doesn't linger
        import shutil
        node_modules_path = os.path.join(REPO_DIR, "node_modules")
        if os.path.exists(node_modules_path):
            shutil.rmtree(node_modules_path, ignore_errors=True)

        # Safety check: ensure node_modules wasn't committed and .gitignore wasn't deleted
        staged_files = run_git(["diff", "--name-only", f"origin/{default}..HEAD"])
        if "node_modules/" in staged_files or "\nnode_modules" in staged_files:
            run_git(["reset", "--hard", f"origin/{default}"])
            raise RuntimeError(
                f"[Coder] Aborted: node_modules/ was committed by Claude for issue #{issue_number}. "
                f"This is caused by a missing .gitignore. Add one to the repo and retry."
            )
        if ".gitignore" in staged_files:
            # .gitignore was modified — check it still contains node_modules
            gitignore_path = os.path.join(REPO_DIR, ".gitignore")
            if os.path.exists(gitignore_path):
                with open(gitignore_path) as f:
                    gitignore_content = f.read()
                if "node_modules" not in gitignore_content:
                    run_git(["reset", "--hard", f"origin/{default}"])
                    raise RuntimeError(
                        f"[Coder] Aborted: .gitignore was modified to remove node_modules "
                        f"for issue #{issue_number}. Reset and retrying."
                    )
            else:
                run_git(["reset", "--hard", f"origin/{default}"])
                raise RuntimeError(
                    f"[Coder] Aborted: .gitignore was deleted by Claude for issue #{issue_number}. "
                    f"Reset and retrying."
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
                    f"Automatically generated by the Spades Orchestrator."
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

        prompt = INLINE_FIX_PROMPT_TEMPLATE.format(
            issue_number=issue_number,
            inline_findings=findings_text,
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
        return self.implement(
            issue_number=context["issue_number"],
            issue_description=context.get("issue_body", ""),
            test_file_path=context.get("test_file_path", ""),
            test_code=context.get("test_code", ""),
            reviewer_feedback=context.get("reviewer_feedback"),
            review_history=context.get("review_history") or [],
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
            run_git(["config", "user.email", "orchestrator@spades-online.app"])
            run_git(["config", "user.name", "Spades Orchestrator"])
            # Set origin/HEAD so symbolic-ref can resolve the default branch
            run_git(["remote", "set-head", "origin", "--auto"])
            print(f"[Coder] Repo cloned.")
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
            print(f"[Coder] Rebase succeeded cleanly.")
            return True
        except RuntimeError as e:
            if "CONFLICT" not in str(e) and "conflict" not in str(e).lower():
                # Not a conflict — something else went wrong
                run_git(["rebase", "--abort"])
                return False

        # There are conflicts — ask Claude to resolve them
        print(f"[Coder] Merge conflicts detected — asking Claude to resolve...")

        conflict_prompt = f"""You are a coding agent for the Spades Online card game backend.

While rebasing branch `{branch}` onto `{default}` for issue #{issue_number},
merge conflicts occurred. Your job is to resolve all conflicts correctly.

Instructions:
- Run `git status` to see which files have conflicts.
- For each conflicted file, read it carefully and resolve the conflict markers
  (<<<<<<<, =======, >>>>>>>) by keeping the correct code from both sides.
- The HEAD (ours) side is the incoming change from {default}.
- The branch side is the work done for issue #{issue_number}.
- Preserve the intent of both changes where possible.
- After resolving all conflicts, run:
    git add -A
    git rebase --continue
- If git rebase --continue asks for a commit message, keep the existing one.
- Output exactly one line when done:
  REBASE: success
- If you cannot resolve a conflict safely, output:
  REBASE: failed — <reason>
"""
        response = run_claude(
            conflict_prompt,
            allowed_tools="Read,Edit,Bash",
            model="claude-opus-4-6",
        )

        if "REBASE: success" in response:
            print(f"[Coder] Claude resolved merge conflicts successfully.")
            return True
        else:
            print(f"[Coder] Claude could not resolve conflicts — aborting rebase.")
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
