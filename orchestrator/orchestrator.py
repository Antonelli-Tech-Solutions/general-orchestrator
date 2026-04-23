# orchestrator/orchestrator.py
import asyncio
import os

from dotenv import load_dotenv
load_dotenv()  # must run before orchestrator imports

from github import Github  # noqa: E402

from orchestrator.graph import build_graph, CHECKPOINT_DB  # noqa: E402
from orchestrator.issue_selector import get_highest_priority_unassigned_issue  # noqa: E402
from agents.base_agent import RateLimitError, TransientError, PromptTooLongError, OutputContractError  # noqa: E402
from langgraph.types import Command  # noqa: E402
from langgraph.errors import GraphInterrupt  # noqa: E402
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver  # noqa: E402

STOP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "STOP")

POLL_INTERVAL_SECONDS    = 60
RATE_LIMIT_RETRY_SECONDS = 30 * 60   # 30 minutes
RATE_LIMIT_MAX_RETRIES   = 48        # give up after 24 hours
TRANSIENT_RETRY_SECONDS  = 60        # 1 minute
TRANSIENT_MAX_RETRIES    = 5


def get_repo():
    g = Github(os.getenv("GITHUB_TOKEN"))
    return g.get_repo(os.getenv("GITHUB_REPO"))


class SpadesOrchestrator:

    async def run_forever(self):
        print("[Orchestrator] Starting continuous processing loop...")
        current_issue = None
        rate_limit_retries = 0
        transient_retries = 0

        async with AsyncSqliteSaver.from_conn_string(CHECKPOINT_DB) as checkpointer:
            graph = build_graph(checkpointer=checkpointer)

            while True:
                try:
                    # Stop signal
                    if os.path.exists(STOP_FILE):
                        os.remove(STOP_FILE)
                        print("[Orchestrator] STOP file detected — shutting down gracefully.")
                        return

                    if current_issue is None:
                        # Resume any issues the human (or a pre-applied
                        # auto-approve-split label) has cleared to proceed.
                        await self._check_auto_approvals()
                        current_issue = get_highest_priority_unassigned_issue()

                    if current_issue:
                        print(f"[Orchestrator] Processing issue "
                              f"#{current_issue.number}: {current_issue.title}")

                        milestone_title = (
                            current_issue.milestone.title
                            if current_issue.milestone else None
                        )
                        initial_state = {
                            "issue_number":    current_issue.number,
                            "issue_title":     current_issue.title,
                            "issue_body":      current_issue.body or current_issue.title,
                            "milestone_title": milestone_title,
                            "status":          "running",
                        }

                        # thread_id scopes the checkpoint to this specific issue.
                        # On restart, LangGraph finds the checkpoint and resumes
                        # from the last completed node rather than starting over.
                        config = {"configurable": {"thread_id": f"issue-{current_issue.number}"}}

                        final_state = await graph.ainvoke(initial_state, config=config)

                        # Check if the graph paused at an interrupt rather than
                        # running to completion.  With a checkpointer, ainvoke
                        # returns normally on interrupt instead of raising
                        # GraphInterrupt, so we inspect the snapshot.
                        snapshot = await graph.aget_state(config)
                        if snapshot.next:
                            # Graph is suspended mid-run — waiting for human input
                            print(f"[Orchestrator] Issue #{current_issue.number} "
                                  f"is waiting for approval.")
                            self._add_needs_approval_label(current_issue.number)
                        else:
                            status = final_state.get("status", "unknown")
                            print(f"[Orchestrator] Issue #{current_issue.number} "
                                  f"finished with status: {status}")

                        current_issue = None
                        rate_limit_retries = 0
                        transient_retries = 0

                    else:
                        print(f"[Orchestrator] No issues to process. "
                              f"Waiting {POLL_INTERVAL_SECONDS}s...")
                        await asyncio.sleep(POLL_INTERVAL_SECONDS)

                except GraphInterrupt:
                    # Fallback in case some LangGraph versions still raise
                    issue_num = current_issue.number if current_issue else None
                    print(f"[Orchestrator] Issue #{issue_num} is waiting for approval.")
                    if issue_num:
                        self._add_needs_approval_label(issue_num)
                    current_issue = None
                    rate_limit_retries = 0
                    transient_retries = 0

                except RateLimitError as e:
                    rate_limit_retries += 1
                    if rate_limit_retries > RATE_LIMIT_MAX_RETRIES:
                        print(
                            f"[Orchestrator] Rate limit persisted for over 24 hours. "
                            f"Giving up on issue "
                            f"#{current_issue.number if current_issue else '?'}."
                        )
                        current_issue = None
                        rate_limit_retries = 0
                        continue

                    wait_seconds = e.wait_seconds or RATE_LIMIT_RETRY_SECONDS
                    wait_minutes = round(wait_seconds / 60)
                    print(
                        f"[Orchestrator] Rate/usage limit hit "
                        f"(retry {rate_limit_retries}/{RATE_LIMIT_MAX_RETRIES}). "
                        f"Waiting {wait_minutes} minutes..."
                    )
                    await asyncio.sleep(wait_seconds)

                except (TransientError, OutputContractError) as e:
                    error_type = type(e).__name__
                    transient_retries += 1
                    if transient_retries > TRANSIENT_MAX_RETRIES:
                        print(
                            f"[Orchestrator] {error_type} persisted after "
                            f"{TRANSIENT_MAX_RETRIES} retries — escalating issue "
                            f"#{current_issue.number if current_issue else '?'}."
                        )
                        if current_issue:
                            try:
                                repo = get_repo()
                                issue = repo.get_issue(current_issue.number)
                                try:
                                    repo.get_label("escalated")
                                except Exception:
                                    repo.create_label("escalated", "b60205")
                                issue.add_to_labels("escalated")
                            except Exception:
                                pass
                        current_issue = None
                        transient_retries = 0
                        continue

                    print(
                        f"[Orchestrator] {error_type} "
                        f"(retry {transient_retries}/{TRANSIENT_MAX_RETRIES}). "
                        f"Retrying in {TRANSIENT_RETRY_SECONDS}s: {e}"
                    )
                    await asyncio.sleep(TRANSIENT_RETRY_SECONDS)
                    # Resume from checkpoint by re-invoking with None input —
                    # LangGraph will pick up from the last completed node
                    if current_issue:
                        config = {"configurable": {"thread_id": f"issue-{current_issue.number}"}}
                        try:
                            final_state = await graph.ainvoke(None, config=config)
                            status = final_state.get("status", "unknown")
                            print(f"[Orchestrator] Issue #{current_issue.number} "
                                  f"finished with status: {status}")
                            current_issue = None
                            transient_retries = 0
                        except (TransientError, OutputContractError):
                            pass  # Will be caught on next loop iteration

                except PromptTooLongError:
                    print(
                        f"[Orchestrator] Prompt too long even after truncation — "
                        f"escalating issue "
                        f"#{current_issue.number if current_issue else '?'}."
                    )
                    if current_issue:
                        try:
                            repo = get_repo()
                            issue = repo.get_issue(current_issue.number)
                            try:
                                repo.get_label("escalated")
                            except Exception:
                                repo.create_label("escalated", "b60205")
                            issue.add_to_labels("escalated")
                        except Exception:
                            pass
                    current_issue = None
                    rate_limit_retries = 0

                except (KeyboardInterrupt, asyncio.CancelledError):
                    print("\n[Orchestrator] Interrupted — shutting down gracefully.")
                    return

    async def _check_auto_approvals(self):
        """
        Scan for issues labelled both 'needs-approval' and 'auto-approve-split'.
        Resume them automatically with 'approve' so the orchestrator creates
        sub-issues without manual intervention.
        """
        try:
            repo = get_repo()
            waiting = repo.get_issues(
                state="open",
                labels=["needs-approval", "auto-approve-split"],
            )
            for issue in waiting:
                print(f"[Orchestrator] Auto-approving split for issue #{issue.number}: "
                      f"{issue.title}")
                await self.resume_issue(issue.number, decision="approve")
        except Exception as e:
            print(f"[Orchestrator] Warning: auto-approval check failed: {e}")

    def _add_needs_approval_label(self, issue_number: int):
        """Create the needs-approval label if needed and add it to the issue."""
        try:
            repo = get_repo()
            try:
                repo.get_label("needs-approval")
            except Exception:
                repo.create_label("needs-approval", "e4e669",
                                  "Waiting for human approval before proceeding")
            issue = repo.get_issue(issue_number)
            issue.add_to_labels("needs-approval")
            print(f"[Orchestrator] Added 'needs-approval' label to #{issue_number}.")
        except Exception as e:
            print(f"[Orchestrator] Warning: could not add needs-approval label: {e}")

    async def resume_issue(self, issue_number: int, decision: str) -> dict:
        """
        Resume an interrupted issue with a human decision.
        decision: "approve" (escalate with breakdown) | "proceed" (implement anyway)
        """
        async with AsyncSqliteSaver.from_conn_string(CHECKPOINT_DB) as checkpointer:
            graph = build_graph(checkpointer=checkpointer)
            config = {"configurable": {"thread_id": f"issue-{issue_number}"}}

            # Remove needs-approval label now that we have a decision
            try:
                repo = get_repo()
                issue = repo.get_issue(issue_number)
                current_labels = [lbl.name for lbl in issue.labels]
                if "needs-approval" in current_labels:
                    issue.remove_from_labels("needs-approval")
            except Exception as e:
                print(f"[Orchestrator] Warning: could not remove needs-approval label: {e}")

            print(f"[Orchestrator] Resuming issue #{issue_number} with decision: {decision}")
            final_state = await graph.ainvoke(Command(resume=decision), config=config)
            return {
                "status": final_state.get("status", "unknown"),
                "issue_number": issue_number,
            }

    async def process_issue(self, issue_number: int, max_attempts: int | None = None) -> dict:
        """Single-issue entry point used by test mode (python main.py 123 [attempts])."""
        async with AsyncSqliteSaver.from_conn_string(CHECKPOINT_DB) as checkpointer:
            graph = build_graph(checkpointer=checkpointer, max_attempts=max_attempts)
            repo = get_repo()
            issue = repo.get_issue(issue_number)
            milestone_title = issue.milestone.title if issue.milestone else None

            initial_state = {
                "issue_number":    issue_number,
                "issue_title":     issue.title,
                "issue_body":      issue.body or issue.title,
                "milestone_title": milestone_title,
                "status":          "running",
            }

            config = {"configurable": {"thread_id": f"issue-{issue_number}"}}
            try:
                final_state = await graph.ainvoke(initial_state, config=config)

                # Check if the graph paused at an interrupt
                snapshot = await graph.aget_state(config)
                if snapshot.next:
                    self._add_needs_approval_label(issue_number)
                    print(f"[Orchestrator] Issue #{issue_number} is waiting for approval.")
                    print(f"  Run: python main.py {issue_number} approve")
                    print(f"  Or:  python main.py {issue_number} proceed")
                    return {"status": "waiting_for_approval", "issue_number": issue_number}

                return {
                    "status": final_state.get("status", "unknown"),
                    "issue_number": issue_number,
                }
            except GraphInterrupt:
                # Fallback in case some LangGraph versions still raise
                self._add_needs_approval_label(issue_number)
                print(f"[Orchestrator] Issue #{issue_number} is waiting for approval.")
                print(f"  Run: python main.py {issue_number} approve")
                print(f"  Or:  python main.py {issue_number} proceed")
                return {"status": "waiting_for_approval", "issue_number": issue_number}
