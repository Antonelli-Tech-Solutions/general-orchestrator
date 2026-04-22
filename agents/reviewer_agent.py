import json
import os
from github import Github
from agents.base_agent import run_claude, RateLimitError, PromptTooLongError, TransientError  # noqa: F401

REVIEWER_PROMPT = """You are a code reviewer for Spades Online, a digital card game implementation.

Review the provided code changes and categorize all findings by severity.

IMPORTANT: If this is a retry attempt (previous review history is provided below),
focus only on the SINGLE most important remaining blocking issue. Do not re-report
issues that have already been addressed. Reporting one clear issue at a time helps
the coder converge rather than getting overwhelmed.

Review the provided code changes and categorize all findings by severity:

CRITICAL: Must fix before merge
- Security issues (e.g. card data exposed to wrong client, auth bypass)
- Game rule violations (e.g. illegal moves not rejected, bid logic wrong)
- Data loss or corruption risks

HIGH: Must fix before merge
- Bugs that would cause incorrect behavior or crashes
- Memory leaks or resource mismanagement
- Missing error handling on critical paths

MEDIUM: Should fix, but won't block merge
- Performance issues or inefficient patterns
- Unhandled edge cases that are unlikely but possible
- Missing input validation on non-critical paths

LOW: Nice to fix eventually
- Style and naming convention issues
- Minor code clarity improvements
- Redundant code

For each finding also assess implementation effort:
- "low": a one or two line change, trivial to implement, no design decisions needed
  (e.g. add a missing null check, rename a variable, add a missing await, add a comment)
- "high": requires design thought, touches multiple files, or has unclear best approach
  (e.g. refactor a module, add a new abstraction, change a data model)

Set fix_inline to true when BOTH of these are true:
- severity is "MEDIUM" or "LOW"
- effort is "low"

These will be fixed immediately in the same PR rather than creating backlog issues.

IMPORTANT: Do not generate MEDIUM or LOW findings for test files (any file under
test/ or ending in .test.js, .spec.js). Test style, structure, and minor quality
issues in test files are not worth tracking as backlog items. Only flag CRITICAL
or HIGH findings in test files (e.g. a test that asserts the wrong expected value,
or a test that could never fail).

Respond ONLY with a valid JSON object in this exact format — no preamble, no markdown:

{
  "findings": [
    {
      "severity": "CRITICAL" | "HIGH" | "MEDIUM" | "LOW",
      "effort": "low" | "high",
      "fix_inline": true | false,
      "title": "Short title for a GitHub issue",
      "body": "Detailed description of the problem and suggested fix",
      "file": "path/to/file.js or null",
      "line": 42 or null
    }
  ],
  "summary": "One or two sentence overall assessment"
}

If there are no findings, return an empty findings array.
"""


def get_repo():
    g = Github(os.getenv("GITHUB_TOKEN"))
    return g.get_repo(os.getenv("GITHUB_REPO"))


class ReviewerAgent:
    # Max diff chars to send to the reviewer — roughly 100k tokens worth.
    # If the diff exceeds this, we truncate and note it so Claude knows.
    MAX_DIFF_CHARS = 80_000

    def review(self, code_changes: str, review_history: list | None = None) -> dict:
        print("[Reviewer] Analyzing code changes...")

        # Truncate diff if it's too large for the context window
        if len(code_changes) > self.MAX_DIFF_CHARS:
            truncated = code_changes[:self.MAX_DIFF_CHARS]
            note = (
                f"\n\n[DIFF TRUNCATED: showing first {self.MAX_DIFF_CHARS} of "
                f"{len(code_changes)} chars — review the visible changes only]"
            )
            code_changes = truncated + note
            print(f"[Reviewer] Warning: diff truncated to {self.MAX_DIFF_CHARS} chars.")

        history_section = ""
        if review_history:
            history_section = "\n\nPrevious review attempts (do not re-report already-fixed issues):\n"
            for i, h in enumerate(review_history, 1):
                history_section += f"\nAttempt {i}: {h}\n"

        prompt = (
            f"{REVIEWER_PROMPT}\n\n"
            f"Code changes to review:\n\n{code_changes}"
            f"{history_section}"
        )

        try:
            raw = run_claude(prompt, allowed_tools="Read,Bash", model="claude-sonnet-4-6")
        except PromptTooLongError:
            # Even after truncation the prompt is too long — truncate more aggressively
            hard_limit = self.MAX_DIFF_CHARS // 4
            truncated = code_changes[:hard_limit]
            note = (
                f"\n\n[DIFF HEAVILY TRUNCATED: showing first {hard_limit} chars only]"
            )
            prompt = f"{REVIEWER_PROMPT}\n\nCode changes to review:\n\n{truncated}{note}"
            print(f"[Reviewer] Warning: hard truncating diff to {hard_limit} chars.")
            raw = run_claude(prompt, allowed_tools="Read,Bash", model="claude-sonnet-4-6")

        result = self._extract_json(raw)

        findings = result.get("findings", [])
        summary = result.get("summary", "")

        critical_count = sum(1 for f in findings if f["severity"] == "CRITICAL")
        high_count = sum(1 for f in findings if f["severity"] == "HIGH")
        medium_count = sum(1 for f in findings if f["severity"] == "MEDIUM")
        low_count = sum(1 for f in findings if f["severity"] == "LOW")

        print(
            f"[Reviewer] Found: {critical_count} critical, {high_count} high, "
            f"{medium_count} medium, {low_count} low"
        )
        print(f"[Reviewer] Summary: {summary}")

        return result

    def _extract_json(self, raw: str) -> dict:
        """
        Extract JSON from the model response even if it includes prose or markdown fences.
        Tries increasingly aggressive strategies before giving up.
        """
        # Strategy 1: direct parse
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        # Strategy 2: strip markdown fences
        clean = raw.replace("```json", "").replace("```", "").strip()
        try:
            return json.loads(clean)
        except json.JSONDecodeError:
            pass

        # Strategy 3: scan for the first parseable JSON object in the string.
        # Uses raw_decode to avoid false positives from { in code snippets
        # (e.g. JS template literals like `${variable}`) that would cause
        # find/rfind substring extraction to grab the wrong range.
        decoder = json.JSONDecoder()
        for text in (raw, clean):
            for i, ch in enumerate(text):
                if ch == '{':
                    try:
                        obj, _ = decoder.raw_decode(text, i)
                        return obj
                    except json.JSONDecodeError:
                        continue

        print("[Reviewer] Warning: could not parse JSON response, treating as no findings")
        return {"findings": [], "summary": raw[:200]}

    def has_blocking_issues(self, review_result: dict) -> bool:
        return any(
            f["severity"] in ("CRITICAL", "HIGH")
            for f in review_result.get("findings", [])
        )

    def get_blocking_findings(self, review_result: dict) -> list[dict]:
        return [
            f for f in review_result.get("findings", [])
            if f["severity"] in ("CRITICAL", "HIGH")
        ]

    def get_inline_findings(self, review_result: dict) -> list[dict]:
        """
        Returns MEDIUM/LOW findings with low effort — these get fixed immediately
        in the same PR rather than becoming backlog issues.
        """
        return [
            f for f in review_result.get("findings", [])
            if f["severity"] in ("MEDIUM", "LOW") and f.get("fix_inline") is True
        ]

    def get_backlog_findings(self, review_result: dict) -> list[dict]:
        """
        Returns MEDIUM/LOW findings that are NOT inline fixes — these become
        backlog issues. High-effort items that aren't worth fixing right now.
        """
        return [
            f for f in review_result.get("findings", [])
            if f["severity"] in ("MEDIUM", "LOW") and f.get("fix_inline") is not True
        ]

    def format_feedback_for_coder(self, review_result: dict) -> str:
        blocking = self.get_blocking_findings(review_result)
        if not blocking:
            return ""

        lines = ["The following issues must be fixed before this code can be merged:\n"]
        for i, finding in enumerate(blocking, 1):
            location = ""
            if finding.get("file"):
                location = f" ({finding['file']}"
                if finding.get("line"):
                    location += f" line {finding['line']}"
                location += ")"
            lines.append(f"{i}. [{finding['severity']}] {finding['title']}{location}")
            lines.append(f"   {finding['body']}\n")

        return "\n".join(lines)

    def triage_ci_failure(self, ci_failure: str, code_changes: str) -> dict:
        """
        Determine whether a CI failure is caused by a bug in the implementation
        or an incorrect test. Returns {"is_test_bug": bool, "confidence": str, "reasoning": str}.
        """
        triage_prompt = """You are triaging a CI failure for the Spades Online card game.

Given the CI failure output and the code changes in this PR, determine whether
the failure is caused by:
A) A bug in the implementation (the test is correct, the code is wrong)
B) An incorrect test (the implementation is correct, the test needs updating)

Consider:
- If the test is asserting something that was never true and the new code didn't
  break it, the test is likely wrong
- If the new code changed behavior that the test was correctly verifying, the
  code is the bug
- If the test was written as part of this PR (by the test agent), be more willing
  to conclude the test is wrong — it may have incorrect expectations
- If the test existed before this PR, be more conservative — assume the code broke it

Respond ONLY with a valid JSON object — no preamble, no markdown:
{
  "is_test_bug": true | false,
  "confidence": "high" | "medium" | "low",
  "reasoning": "One sentence explanation of why"
}
"""
        prompt = (
            f"{triage_prompt}\n\n"
            f"CI failure output:\n{ci_failure}\n\n"
            f"Code changes in this PR:\n{code_changes[:5000]}"
        )

        print("[Reviewer] Triaging CI failure...")
        raw = run_claude(prompt, allowed_tools="Read,Bash", model="claude-sonnet-4-6")
        result = self._extract_json(raw)

        is_test_bug = result.get("is_test_bug", False)
        confidence = result.get("confidence", "low")
        reasoning = result.get("reasoning", "")

        print(f"[Reviewer] CI triage: is_test_bug={is_test_bug} confidence={confidence}")
        print(f"[Reviewer] Reasoning: {reasoning}")

        return result

    def create_backlog_issues(self, review_result: dict, milestone_title: str | None):
        backlog = self.get_backlog_findings(review_result)
        if not backlog:
            return

        repo = get_repo()

        milestone = None
        if milestone_title:
            try:
                for m in repo.get_milestones(state="open"):
                    if m.title == milestone_title:
                        milestone = m
                        break
            except Exception as e:
                print(f"[Reviewer] Warning: could not resolve milestone '{milestone_title}': {e}")

        for finding in backlog:
            priority_label = f"priority:{finding['severity'].lower()}"

            try:
                repo.get_label(priority_label)
            except Exception:
                color_map = {"medium": "e4e669", "low": "c5def5"}
                color = color_map.get(finding["severity"].lower(), "ededed")
                repo.create_label(priority_label, color)

            body = finding["body"]
            if finding.get("file"):
                body += f"\n\n**Location:** `{finding['file']}`"
                if finding.get("line"):
                    body += f" line {finding['line']}"

            create_kwargs = dict(
                title=finding["title"],
                body=body,
                labels=[priority_label],
            )
            if milestone:
                create_kwargs["milestone"] = milestone

            issue = repo.create_issue(**create_kwargs)
            print(f"[Reviewer] Created backlog issue #{issue.number}: {finding['title']}")
