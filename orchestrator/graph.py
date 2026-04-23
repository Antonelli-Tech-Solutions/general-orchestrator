# orchestrator/graph.py
from typing import Literal

from github import Github
from github.GithubException import GithubException
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt, Command

from agents.issue_decomposer_agent import IssueDecomposerAgent
from agents.test_agent import TestAgent
from agents.coder_agent import CoderAgent, find_existing_pr
from agents.reviewer_agent import ReviewerAgent
from agents.documentation_agent import DocumentationAgent
from agents.merger_agent import MergerAgent
from orchestrator.state import IssueState

import os

CHECKPOINT_DB = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "checkpoints.db"
)

MAX_FIX_ATTEMPTS = 5
MAX_CI_ATTEMPTS = 5


# ---------------------------------------------------------------------------
# Shared agent instances (created once, reused across all graph invocations)
# ---------------------------------------------------------------------------

issue_decomposer_agent = IssueDecomposerAgent()
test_agent = TestAgent()
coder_agent = CoderAgent()
reviewer_agent = ReviewerAgent()
doc_agent = DocumentationAgent()
merger_agent = MergerAgent()


def get_repo():
    g = Github(os.getenv("GITHUB_TOKEN"))
    return g.get_repo(os.getenv("GITHUB_REPO"))


def _comment(issue_number: int, message: str):
    """Post a comment on the issue. Never raises."""
    try:
        repo = get_repo()
        issue = repo.get_issue(issue_number)
        issue.create_comment(message)
    except Exception as e:
        print(f"[Graph] Warning: could not post comment on #{issue_number}: {e}")


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------

async def node_check_resume(state: IssueState) -> dict:
    """
    Check if a PR already exists for this issue.
    If so, we skip test + coder and resume from review.
    Sets pr_number, branch, and code_changes if resuming.
    """
    repo = get_repo()
    issue_number = state["issue_number"]

    # Freshness check — issue may have been closed between selector picking it
    # up and the graph starting (race condition with a concurrent PR merge)
    issue = repo.get_issue(issue_number)
    if issue.state == "closed":
        print(f"[Graph] Issue #{issue_number} is already closed — skipping.")
        return {"status": "completed"}

    # Assign the issue so the selector skips it on the next cycle
    try:
        g = Github(os.getenv("GITHUB_TOKEN"))
        bot_user = g.get_user().login
        issue.add_to_assignees(bot_user)
    except Exception as e:
        print(f"[Graph] Warning: could not assign issue #{issue_number}: {e}")

    _comment(issue_number, "🤖 Orchestrator picking up this issue.")

    existing_pr = find_existing_pr(repo, issue_number)
    if existing_pr:
        branch = existing_pr.head.ref
        print(f"[Graph] PR #{existing_pr.number} already exists — resuming from review.")
        # Get diff for reviewer
        import subprocess
        from agents.base_agent import REPO_DIR
        default = coder_agent._get_default_branch()
        result = subprocess.run(
            ["git", "diff", f"origin/{default}..origin/{branch}"],
            cwd=REPO_DIR, capture_output=True, text=True, encoding="utf-8",
        )
        return {
            "pr_number": existing_pr.number,
            "branch": branch,
            "code_changes": result.stdout.strip(),
            "fix_attempts": 0,
            "ci_attempts": 0,
            "reviewer_feedback": None,
        "review_history": [],
        }

    return {
        "pr_number": None,
        "branch": None,
        "code_changes": "",
        "fix_attempts": 0,
        "ci_attempts": 0,
        "reviewer_feedback": None,
    }


async def node_close_already_done(state: IssueState) -> dict:
    """Close an issue the planner determined is already fully implemented."""
    issue_number = state["issue_number"]
    assessment = state.get("complexity_assessment", {})
    reasoning = assessment.get("reasoning", "")

    print(f"[Graph] Issue #{issue_number} already implemented — closing.")
    _comment(issue_number,
        f"✅ **No changes needed.**\n\n"
        f"{reasoning}\n\n"
        f"This issue has been closed as already implemented."
    )

    try:
        repo = get_repo()
        issue = repo.get_issue(issue_number)
        issue.edit(state="closed", state_reason="completed")
    except Exception as e:
        print(f"[Graph] Warning: could not close issue #{issue_number}: {e}")

    return {"status": "completed"}


async def node_assess_complexity(state: IssueState) -> dict:
    """
    Run the issue decomposer and save the assessment to state.
    Kept separate from node_await_approval so the assessment is checkpointed
    before the interrupt fires — meaning on resume the decomposer is never re-run.
    """
    issue_number = state["issue_number"]
    print(f"[Graph] Checking complexity of issue #{issue_number}...")

    assessment = await issue_decomposer_agent.run({
        "issue_number": issue_number,
        "issue_title": state.get("issue_title", ""),
        "issue_body": state.get("issue_body", ""),
    })
    return {"complexity_assessment": assessment}


async def node_await_approval(state: IssueState) -> dict:
    """
    If the assessment found a large issue, interrupt and wait for human approval.
    Because this is a separate node from node_assess_complexity, the assessment
    is already in the checkpoint when this node runs — so on resume LangGraph
    starts here (not at node_assess_complexity) and the decomposer is never re-run.
    """
    issue_number = state["issue_number"]
    assessment = state.get("complexity_assessment", {})

    print(f"[Graph] node_await_approval: size={assessment.get('size')}, "
          f"human_decision={state.get('human_decision')}")

    size = assessment.get("size", "medium")

    if size == "none":
        # Already implemented — close the issue
        return Command(
            update={"human_decision": "none"},
            goto="node_close_already_done",
        )

    if size != "large":
        # Small/medium — proceed immediately, no interrupt needed
        return Command(
            update={"human_decision": "proceed"},
            goto="node_needs_tests_check",
        )

    sub_issues = assessment.get("sub_issues", [])

    # Only post the comment and display breakdown on the first pass, not on resume
    if not state.get("human_decision"):
        # Format breakdown for display
        breakdown_lines = []
        for i, s in enumerate(sub_issues):
            dep_idx = s.get("depends_on_index")
            dep = sub_issues[dep_idx]["title"] if dep_idx is not None and dep_idx < len(sub_issues) else None
            dep_str = f" (after: {dep})" if dep else ""
            breakdown_lines.append(f"  {i+1}. {s['title']}{dep_str}")
            breakdown_lines.append(f"     {s['body'][:200]}")

        breakdown_text = "\n".join(breakdown_lines)

        print(f"\n{'='*60}")
        print(f"[Graph] Issue #{issue_number} assessed as LARGE.")
        print(f"Reasoning: {assessment.get('reasoning', '')}")
        print(f"Areas: {', '.join(assessment.get('areas_affected', []))}")
        print(f"Suggested breakdown:\n{breakdown_text}")
        print(f"{'='*60}")

        _comment(issue_number,
            f"🔬 **Issue too large for a single implementation pass.**\n\n"
            f"**Reasoning:** {assessment.get('reasoning', '')}\n\n"
            f"**Areas affected:** {', '.join(assessment.get('areas_affected', []))}\n\n"
            f"**Suggested breakdown:**\n" +
            "\n".join(
                f"{i+1}. **{s['title']}**\n   {s['body'][:300]}"
                for i, s in enumerate(sub_issues)
            ) +
            "\n\n⏸️ *Waiting for approval to proceed or decompose.*"
        )

    decision = interrupt({
        "issue_number": issue_number,
        "message": (
            f"Issue #{issue_number} is LARGE. Options:\n"
            f"  approve  — create sub-issues and close parent (recommended)\n"
            f"  proceed  — implement anyway without decomposing\n"
        ),
        "assessment": assessment,
    })

    print(f"[Graph] node_await_approval: interrupt returned decision={decision!r}")

    # Use Command to route explicitly — conditional edges read pre-node state,
    # so they can't see the decision returned by interrupt().
    next_node = "node_escalate" if decision == "approve" else "node_needs_tests_check"
    return Command(
        update={"human_decision": decision, "complexity_assessment": assessment},
        goto=next_node,
    )


async def node_run_tests(state: IssueState) -> dict:
    """Test agent — runs by default, skipped if issue has 'skip-tests' label."""
    issue_number = state["issue_number"]
    print(f"[Graph] Running test agent for #{issue_number}...")

    result = await test_agent.run({
        "issue_number": issue_number,
        "issue_body": state.get("issue_body", ""),
    })

    test_file = result.get("test_file_path", "unknown")
    explanation = result.get("explanation", "").strip()
    _comment(issue_number,
        f"🧪 **Tests written:** `{test_file}`\n{explanation}"
    )

    return {"test_result": result}


async def node_run_coder(state: IssueState) -> dict:
    """Coder agent — implements the issue and opens/updates a PR."""
    issue_number = state["issue_number"]
    attempt = state.get("fix_attempts", 0) + 1
    print(f"[Graph] Running coder agent for #{issue_number} (attempt {attempt}/{MAX_FIX_ATTEMPTS})...")

    test_result = state.get("test_result", {})
    code_result = await coder_agent.run({
        "issue_number": issue_number,
        "issue_body": state.get("issue_body", ""),
        "test_file_path": test_result.get("test_file_path", ""),
        "test_code": test_result.get("test_code", ""),
        "reviewer_feedback": state.get("reviewer_feedback"),
        "review_history": state.get("review_history") or [],
    })

    pr_number = code_result.get("pr_number")
    commit_msg = code_result.get("commit_message", "")
    summary = code_result.get("summary", "")

    if attempt == 1:
        _comment(issue_number,
            f"🔨 **Implementation ready:** PR #{pr_number}\n"
            f"Commit: `{commit_msg}`\n\n"
            f"{summary}"
        )
    else:
        _comment(issue_number,
            f"🔨 **Revised implementation** (attempt {attempt}): PR #{pr_number}\n"
            f"Commit: `{commit_msg}`\n\n"
            f"{summary}"
        )

    return {
        "code_result": code_result,
        "code_changes": code_result.get("code_changes", ""),
        "pr_number": pr_number,
        "branch": code_result.get("branch"),
        "fix_attempts": attempt,
        "reviewer_feedback": None,  # clear feedback after each coder run
    }


async def node_run_reviewer(state: IssueState) -> dict:
    """Reviewer agent — analyzes the diff and categorizes findings."""
    issue_number = state["issue_number"]
    attempt = state.get("fix_attempts", 0)
    print(f"[Graph] Running reviewer for #{issue_number} (attempt {attempt}/{MAX_FIX_ATTEMPTS})...")

    review_result = reviewer_agent.review(
        code_changes=state.get("code_changes", ""),
        review_history=state.get("review_history") or [],
    )

    findings = review_result.get("findings", [])
    summary = review_result.get("summary", "")
    critical = sum(1 for f in findings if f["severity"] == "CRITICAL")
    high     = sum(1 for f in findings if f["severity"] == "HIGH")
    medium   = sum(1 for f in findings if f["severity"] == "MEDIUM")
    low      = sum(1 for f in findings if f["severity"] == "LOW")

    if not reviewer_agent.has_blocking_issues(review_result):
        _comment(issue_number,
            f"🔍 **Review passed:** {critical} critical, {high} high, "
            f"{medium} medium, {low} low\n\n{summary}"
        )
    else:
        _comment(issue_number,
            f"🔍 **Review found blocking issues** (attempt {attempt}): "
            f"{critical} critical, {high} high\n\n{summary}\n\n"
            f"Sending feedback to Coder Agent for revision."
        )

    # Format feedback for the coder in case we need to retry
    reviewer_feedback = reviewer_agent.format_feedback_for_coder(review_result) \
        if reviewer_agent.has_blocking_issues(review_result) else None

    # Accumulate review history so future attempts know what was already tried
    history_entry = f"{summary} (blocking: {critical} critical, {high} high)"
    existing_history = state.get("review_history") or []

    return {
        "review_result": review_result,
        "reviewer_feedback": reviewer_feedback,
        "review_history": existing_history + [history_entry],
    }


async def node_apply_inline_fixes(state: IssueState) -> dict:
    """Apply low-effort inline fixes identified by the reviewer."""
    issue_number = state["issue_number"]
    review_result = state.get("review_result", {})
    inline = reviewer_agent.get_inline_findings(review_result)

    if inline:
        print(f"[Graph] Applying {len(inline)} inline fix(es) for #{issue_number}...")
        coder_agent.implement_inline_fixes(
            issue_number=issue_number,
            inline_findings=inline,
        )
        titles = ", ".join(f["title"] for f in inline)
        _comment(issue_number, f"🔧 **{len(inline)} inline fix(es) applied:** {titles}")

    return {}


async def node_create_backlog_issues(state: IssueState) -> dict:
    """Create GitHub issues for high-effort MEDIUM/LOW findings."""
    issue_number = state["issue_number"]
    review_result = state.get("review_result", {})
    backlog = reviewer_agent.get_backlog_findings(review_result)

    if backlog:
        print(f"[Graph] Creating {len(backlog)} backlog issue(s) for #{issue_number}...")
        reviewer_agent.create_backlog_issues(review_result, state.get("milestone_title"))
        _comment(issue_number,
            f"📋 **{len(backlog)} backlog issue(s) created** for deferred improvements."
        )

    return {}


async def node_run_doc_agent(state: IssueState) -> dict:
    """Documentation agent — updates docs based on code changes."""
    issue_number = state["issue_number"]
    print(f"[Graph] Running doc agent for #{issue_number}...")

    doc_result = await doc_agent.run({
        "issue_number": issue_number,
        "code_changes": state.get("code_changes", ""),
        "branch": state.get("branch"),
    })

    files_updated = doc_result.get("files_updated", [])
    if files_updated:
        _comment(issue_number,
            f"📝 **Documentation updated:** "
            f"{', '.join(f'`{f}`' for f in files_updated)}"
        )

    return {"doc_result": doc_result}


async def node_wait_for_ci(state: IssueState) -> dict:
    """Wait for CI checks to complete on the PR."""
    pr_number = state.get("pr_number")
    ci_attempt = state.get("ci_attempts", 0) + 1
    print(f"[Graph] Waiting for CI on PR #{pr_number} "
          f"(attempt {ci_attempt}/{MAX_CI_ATTEMPTS})...")

    ci_result = merger_agent.wait_for_ci(pr_number)
    print(f"[Graph] CI result: {ci_result}")

    return {
        "ci_attempts": ci_attempt,
        "ci_feedback": ci_result,   # "success", "failure", or "timeout"
    }


async def node_merge(state: IssueState) -> dict:
    """Merge the PR."""
    pr_number = state.get("pr_number")
    issue_number = state["issue_number"]
    print(f"[Graph] Merging PR #{pr_number}...")

    merge_result = merger_agent.merge_pr(pr_number)

    if merge_result["success"]:
        _comment(issue_number, f"✅ **Merged:** PR #{pr_number}")
        try:
            repo = get_repo()
            issue = repo.get_issue(issue_number)
            issue.edit(state="closed")
        except GithubException:
            pass  # already closed via "Closes #N"
        return {"status": "completed"}
    else:
        return {
            "status": "escalated",
            "escalation_reason": merge_result.get("error", "Merge failed"),
        }


async def node_triage_ci_failure(state: IssueState) -> dict:
    """Triage a CI failure — code bug or test bug?"""
    pr_number = state.get("pr_number")
    issue_number = state["issue_number"]
    print(f"[Graph] Triaging CI failure on PR #{pr_number}...")

    ci_failure = merger_agent.get_ci_failure_details(pr_number)
    triage = reviewer_agent.triage_ci_failure(ci_failure, state.get("code_changes", ""))

    is_test_bug = triage.get("is_test_bug", False)
    confidence = triage.get("confidence", "low")
    reasoning = triage.get("reasoning", "")

    if is_test_bug:
        feedback = (
            f"The CI failure is caused by an INCORRECT TEST "
            f"(confidence: {confidence}).\n"
            f"Reviewer reasoning: {reasoning}\n\n"
            f"Fix the test. Do not change the implementation.\n\n"
            f"CI failure details:\n{ci_failure}"
        )
    else:
        feedback = (
            f"The CI failure is caused by a BUG IN THE IMPLEMENTATION "
            f"(confidence: {confidence}).\n"
            f"Reviewer reasoning: {reasoning}\n\n"
            f"Fix the implementation. Do not change the tests.\n\n"
            f"CI failure details:\n{ci_failure}"
        )

    diagnosis = "incorrect test" if is_test_bug else "implementation bug"
    _comment(issue_number,
        f"⚠️ **CI failed** (attempt {state.get('ci_attempts', 0)}): {reasoning}\n\n"
        f"Diagnosis: {diagnosis} (confidence: {confidence}) — sending fix to Coder Agent."
    )

    return {"reviewer_feedback": feedback}


async def node_escalate(state: IssueState) -> dict:
    """Escalate the issue — label it and create a companion escalation issue."""
    print(f"[Graph] node_escalate state keys: {list(state.keys())}")
    issue_number = state["issue_number"]
    repo = get_repo()

    complexity = state.get("complexity_assessment", {})
    created_sub_issues = []
    reason = state.get("escalation_reason", "Unknown reason")

    # Detect decomposition: large issue, no other escalation reason, came via node_await_approval.
    # We check human_decision from state first, but also accept if complexity is large and
    # escalation_reason is unset (meaning we got here from the approval flow, not a code failure).
    human_decision = state.get("human_decision", "")
    is_decomposition = (
        complexity.get("size") == "large"
        and not state.get("escalation_reason")
        and (human_decision == "approve" or not human_decision)
    )

    print(f"[Graph] node_escalate: is_decomposition={is_decomposition}, "
          f"human_decision={human_decision!r}, escalation_reason={state.get('escalation_reason')!r}")

    # If human approved decomposition, create the sub-issues automatically
    if is_decomposition:
        sub_issues = complexity.get("sub_issues", [])
        milestone_title = state.get("milestone_title")

        # Resolve milestone object
        milestone = None
        try:
            if milestone_title:
                for m in repo.get_milestones(state="open"):
                    if m.title == milestone_title:
                        milestone = m
                        break
        except Exception:
            pass

        # Resolve priority label from parent issue
        try:
            parent_issue = repo.get_issue(issue_number)
            priority_labels = [
                lbl.name for lbl in parent_issue.labels
                if lbl.name.startswith("priority:")
            ]
        except Exception:
            priority_labels = ["priority:medium"]

        # Create each sub-issue, tracking numbers for depends_on linking
        created_numbers = []
        for i, sub in enumerate(sub_issues):
            try:
                labels = priority_labels.copy()

                # Add blocked-by label if this sub-issue depends on another
                dep_idx = sub.get("depends_on_index")
                if dep_idx is not None and dep_idx < len(created_numbers):
                    labels.append(f"blocked-by:#{created_numbers[dep_idx]}")

                # Add skip-tests if planner determined no tests needed
                if not sub.get("needs_tests", True):
                    labels.append("skip-tests")

                create_kwargs = dict(
                    title=sub["title"],
                    body=(
                        f"{sub['body']}\n\n"
                        f"---\n"
                        f"*Auto-created from #{issue_number} by the orchestrator "
                        f"after complexity assessment.*"
                    ),
                    labels=labels,
                )
                if milestone:
                    create_kwargs["milestone"] = milestone

                new_issue = repo.create_issue(**create_kwargs)
                created_numbers.append(new_issue.number)
                created_sub_issues.append(new_issue.number)
                print(f"[Graph] Created sub-issue #{new_issue.number}: {sub['title']}")
            except Exception as e:
                print(f"[Graph] Warning: could not create sub-issue {i+1}: {e}")

        sub_issue_refs = ", ".join(f"#{n}" for n in created_numbers)
        reason = (
            f"Issue decomposed into {len(created_numbers)} sub-issues: {sub_issue_refs}\n\n"
            f"{complexity.get('reasoning', '')}"
        )

        # Close the parent issue and label it as decomposed (not escalated)
        if created_numbers:
            try:
                # Create decomposed label if it doesn't exist
                try:
                    repo.get_label("decomposed")
                except Exception:
                    repo.create_label("decomposed", "0075ca",
                                      "Issue broken into sub-issues by the planner")

                parent_issue = repo.get_issue(issue_number)
                parent_issue.add_to_labels("decomposed")
                parent_issue.edit(
                    state="closed",
                    state_reason="not_planned",
                )
            except Exception as e:
                print(f"[Graph] Warning: could not close parent issue: {e}")

            _comment(issue_number,
                f"✅ **Decomposed into {len(created_numbers)} sub-issues:** {sub_issue_refs}\n\n"
                f"This issue has been closed. It will be considered complete when all "
                f"sub-issues are resolved. The orchestrator will pick them up automatically."
            )
    print(f"[Graph] Escalating issue #{issue_number}: {reason}")

    # If this was a decomposition, sub-issues and parent closure are already handled above
    if is_decomposition:
        return {"status": "escalated"}

    try:
        issue = repo.get_issue(issue_number)
        review_result = state.get("review_result")
        milestone_title = state.get("milestone_title")

        # Ensure escalated label exists
        try:
            repo.get_label("escalated")
        except Exception:
            repo.create_label("escalated", "b60205")

        issue.add_to_labels("escalated")

        # Build escalation issue body
        body_lines = [
            f"**Original issue:** #{issue_number} — {state.get('issue_title', '')}",
            "",
            f"**Reason:** {reason}",
            "",
        ]

        if review_result:
            blocking = reviewer_agent.get_blocking_findings(review_result)
            if blocking:
                body_lines.append("**Unresolved blocking findings:**")
                for f in blocking:
                    location = ""
                    if f.get("file"):
                        location = f" (`{f['file']}`"
                        if f.get("line"):
                            location += f" line {f['line']}"
                        location += ")"
                    body_lines.append(f"- [{f['severity']}] {f['title']}{location}")
                    body_lines.append(f"  {f['body']}")
                    body_lines.append("")

        body_lines += [
            "**To resume automated processing:**",
            "1. Fix the issues above manually on the branch.",
            "2. Remove the `escalated` label from the original issue.",
            "3. The orchestrator will pick it up on its next cycle.",
        ]

        # Resolve milestone
        milestone = None
        if milestone_title:
            for m in repo.get_milestones(state="open"):
                if m.title == milestone_title:
                    milestone = m
                    break

        create_kwargs = dict(
            title=f"[ESCALATED] #{issue_number}: {state.get('issue_title', '')}",
            body="\n".join(body_lines),
            labels=["escalated"],
        )
        if milestone:
            create_kwargs["milestone"] = milestone

        escalation_issue = repo.create_issue(**create_kwargs)
        print(f"[Graph] Escalation issue created: #{escalation_issue.number}")

    except Exception as e:
        print(f"[Graph] Warning: escalation failed: {e}")

    return {"status": "escalated"}


# ---------------------------------------------------------------------------
# Routing functions (conditional edges)
# ---------------------------------------------------------------------------

def route_after_resume_check(state: IssueState) -> Literal[
    "node_assess_complexity", "node_run_reviewer", "__end__"
]:
    """If already closed, end immediately. If a PR exists, skip to review. Otherwise assess complexity."""
    if state.get("status") == "completed":
        return "__end__"
    if state.get("pr_number"):
        return "node_run_reviewer"
    return "node_assess_complexity"


def route_needs_tests(state: IssueState) -> Literal[
    "node_run_tests", "node_run_coder"
]:
    """Run test agent unless issue has 'skip-tests' label."""
    repo = get_repo()
    issue = repo.get_issue(state["issue_number"])
    skip = any(lbl.name == "skip-tests" for lbl in issue.labels)
    if skip:
        print("[Graph] Skipping test agent — 'skip-tests' label present.")
    else:
        print("[Graph] Running test agent (add 'skip-tests' label to bypass).")
    return "node_run_coder" if skip else "node_run_tests"


def route_after_review(state: IssueState, fix_attempts: int = MAX_FIX_ATTEMPTS) -> Literal[
    "node_run_coder", "node_apply_inline_fixes", "node_escalate"
]:
    """
    After review:
    - blocking issues + retries remaining → coder
    - blocking issues + no retries left   → escalate
    - no blocking issues                  → inline fixes
    """
    review_result = state.get("review_result", {})
    if reviewer_agent.has_blocking_issues(review_result):
        if state.get("fix_attempts", 0) >= fix_attempts:
            return "node_escalate"
        return "node_run_coder"
    return "node_apply_inline_fixes"


def route_after_ci(state: IssueState, ci_attempts: int = MAX_CI_ATTEMPTS) -> Literal[
    "node_merge", "node_triage_ci_failure", "node_escalate"
]:
    """
    After waiting for CI:
    - success  → merge
    - failure  + retries remaining → triage
    - failure  + no retries left   → escalate
    - timeout                      → escalate
    """
    ci_feedback = state.get("ci_feedback", "")
    if ci_feedback == "success":
        return "node_merge"
    if ci_feedback == "timeout":
        return "node_escalate"
    # failure
    if state.get("ci_attempts", 0) >= ci_attempts:
        return "node_escalate"
    return "node_triage_ci_failure"


def route_after_merge(state: IssueState) -> Literal["node_escalate", "__end__"]:
    """After merge attempt — escalate if it failed, end if successful."""
    if state.get("status") == "escalated":
        return "node_escalate"
    return "__end__"


def route_after_coder_no_commit(state: IssueState) -> Literal[
    "node_run_reviewer", "node_escalate"
]:
    """After CI triage sends feedback back to coder — go to reviewer next."""
    return "node_run_reviewer"


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------

def build_graph(checkpointer=None, max_attempts: int | None = None) -> StateGraph:
    # Allow callers to override the attempt limits (e.g. python main.py 603 10)
    fix_attempts  = max_attempts or MAX_FIX_ATTEMPTS
    ci_attempts   = max_attempts or MAX_CI_ATTEMPTS

    g = StateGraph(IssueState)

    # Add all nodes
    g.add_node("node_check_resume",          node_check_resume)
    g.add_node("node_close_already_done",   node_close_already_done)
    g.add_node("node_assess_complexity",     node_assess_complexity)
    g.add_node("node_await_approval",        node_await_approval)
    g.add_node("node_needs_tests_check",     lambda s: s)  # pass-through routing node
    g.add_node("node_run_tests",             node_run_tests)
    g.add_node("node_run_coder",             node_run_coder)
    g.add_node("node_run_reviewer",          node_run_reviewer)
    g.add_node("node_apply_inline_fixes",    node_apply_inline_fixes)
    g.add_node("node_create_backlog_issues", node_create_backlog_issues)
    g.add_node("node_run_doc_agent",         node_run_doc_agent)
    g.add_node("node_wait_for_ci",           node_wait_for_ci)
    g.add_node("node_merge",                 node_merge)
    g.add_node("node_triage_ci_failure",     node_triage_ci_failure)
    g.add_node("node_escalate",              node_escalate)

    # Entry point
    g.add_edge(START, "node_check_resume")

    # Resume check → branch
    g.add_conditional_edges(
        "node_check_resume",
        route_after_resume_check,
        {
            "node_assess_complexity": "node_assess_complexity",
            "node_run_reviewer":      "node_run_reviewer",
            "__end__":                END,
        }
    )

    # Assess → await approval (always)
    g.add_edge("node_assess_complexity", "node_await_approval")

    g.add_edge("node_close_already_done", END)

    # node_await_approval uses Command(goto=...) to route itself — no edge needed
    # (LangGraph respects Command.goto over any declared edges)

    # Needs-tests check → branch
    g.add_conditional_edges(
        "node_needs_tests_check",
        route_needs_tests,
        {
            "node_run_tests":  "node_run_tests",
            "node_run_coder":  "node_run_coder",
        }
    )

    # Tests → coder (always)
    g.add_edge("node_run_tests", "node_run_coder")

    # Coder → reviewer (always)
    g.add_edge("node_run_coder", "node_run_reviewer")

    # Reviewer → branch
    g.add_conditional_edges(
        "node_run_reviewer",
        lambda s: route_after_review(s, fix_attempts),
        {
            "node_run_coder":           "node_run_coder",
            "node_apply_inline_fixes":  "node_apply_inline_fixes",
            "node_escalate":            "node_escalate",
        }
    )

    # Inline fixes → backlog issues → doc agent → CI
    g.add_edge("node_apply_inline_fixes",    "node_create_backlog_issues")
    g.add_edge("node_create_backlog_issues", "node_run_doc_agent")
    g.add_edge("node_run_doc_agent",         "node_wait_for_ci")

    # CI → branch
    g.add_conditional_edges(
        "node_wait_for_ci",
        lambda s: route_after_ci(s, ci_attempts),
        {
            "node_merge":              "node_merge",
            "node_triage_ci_failure":  "node_triage_ci_failure",
            "node_escalate":           "node_escalate",
        }
    )

    # Triage → coder (with CI feedback as reviewer_feedback) → reviewer
    g.add_edge("node_triage_ci_failure", "node_run_coder")

    # Merge → end or escalate
    g.add_conditional_edges(
        "node_merge",
        route_after_merge,
        {
            "node_escalate": "node_escalate",
            "__end__":       END,
        }
    )

    # Escalate → end
    g.add_edge("node_escalate", END)

    return g.compile(checkpointer=checkpointer)


# Use build_graph() with an AsyncSqliteSaver checkpointer — see orchestrator.py
