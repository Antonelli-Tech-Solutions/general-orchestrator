# orchestrator/issue_selector.py

from github import Github
import os
import re

PRIORITY_ORDER = ["priority:critical", "priority:high", "priority:medium", "priority:low"]

def get_repo():
    g = Github(os.getenv("GITHUB_TOKEN"))
    return g.get_repo(os.getenv("GITHUB_REPO"))

def is_escalated(issue) -> bool:
    return any(label.name == "escalated" for label in issue.labels)

def is_awaiting_approval(issue) -> bool:
    return any(label.name == "needs-approval" for label in issue.labels)

def is_decomposed(issue) -> bool:
    return any(label.name == "decomposed" for label in issue.labels)

def is_blocked(issue, repo) -> bool:
    for label in issue.labels:
        if label.name.startswith("blocked-by:#"):
            try:
                blocking_number = int(label.name.split("#")[1])
                blocking_issue = repo.get_issue(blocking_number)
                if blocking_issue.state == "open":
                    return True
            except Exception:
                pass  # malformed label — treat as unblocked
    return False

def is_prioritized(issue) -> bool:
    label_names = [lbl.name for lbl in issue.labels]
    return any(p in label_names for p in PRIORITY_ORDER)

def priority_rank(issue) -> int:
    label_names = [lbl.name for lbl in issue.labels]
    for i, p in enumerate(PRIORITY_ORDER):
        if p in label_names:
            return i
    return len(PRIORITY_ORDER)

def milestone_rank(issue) -> tuple[int, int, int]:
    if issue.milestone is None:
        return (999, 999, 999)

    match = re.match(r'^v?(\d+)\.(\d+)(?:\.(\d+))?$', issue.milestone.title.strip())
    if not match:
        return (999, 999, 999)

    major = int(match.group(1))
    minor = int(match.group(2))
    patch = int(match.group(3)) if match.group(3) else 0

    return (major, minor, patch)

def get_highest_priority_unassigned_issue():
    repo = get_repo()
    open_issues = repo.get_issues(state="open", assignee="none")

    candidates = []
    for issue in open_issues:
        if issue.pull_request:
            continue  # skip PRs
        if not is_prioritized(issue):
            continue  # skip issues with no priority label — must be triaged first
        if is_escalated(issue):
            continue
        if is_awaiting_approval(issue):
            continue
        if is_decomposed(issue):
            continue
        if is_blocked(issue, repo):
            continue
        candidates.append(issue)

    if not candidates:
        return None

    candidates.sort(key=lambda i: (milestone_rank(i), priority_rank(i), i.number))
    return candidates[0]
