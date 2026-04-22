# orchestrator/state.py
from typing import TypedDict


class IssueState(TypedDict, total=False):
    # Issue metadata
    issue_number: int
    issue_body: str
    issue_title: str
    milestone_title: str | None

    # Agent outputs
    test_result: dict
    code_result: dict
    review_result: dict
    doc_result: dict

    # Inter-agent communication
    reviewer_feedback: str | None   # blocking feedback → coder on review retry
    review_history: list            # all previous review summaries for context
    ci_feedback: str | None         # CI failure details → coder on CI retry
    code_changes: str               # diff passed to reviewer and doc agent

    # PR / branch tracking
    pr_number: int | None
    branch: str | None

    # Retry counters
    fix_attempts: int               # coder→reviewer cycles
    ci_attempts: int                # CI failure fix cycles

    # Planning
    complexity_assessment: dict     # planner's size assessment and sub-issue suggestions
    human_decision: str | None      # "approve" | "proceed" | "none" — set by node_await_approval

    # Terminal status
    status: str                     # "running" | "completed" | "escalated" | "completed_no_merge"
    escalation_reason: str | None
