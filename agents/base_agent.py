import subprocess
import json
import os
import tempfile

REPO_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "working-repo")

# Rate limit phrases as reported by the claude CLI
RATE_LIMIT_PHRASES = [
    "rate limit",
    "usage limit",
    "credit balance is too low",
    "too many requests",
    "quota exceeded",
    "529",                             # anthropic overloaded HTTP status
    "claude ai usage limit reached",   # one CLI variant
    "please try again after",          # accompanies some variants
    "you've hit your limit",           # actual CLI message as of 2026
    "you have hit your limit",         # alternate phrasing
    "resets",                          # present in "resets 2am (America/Toronto)"
]


class RateLimitError(Exception):
    """
    Raised when the Claude CLI reports a rate or usage limit.
    Optionally carries wait_seconds parsed from the CLI message so the
    orchestrator can sleep exactly as long as needed rather than a fixed interval.
    """
    def __init__(self, message: str, wait_seconds: int | None = None):
        super().__init__(message)
        self.wait_seconds = wait_seconds


class PromptTooLongError(Exception):
    """Raised when the prompt exceeds Claude's context window."""
    pass


class TransientError(Exception):
    """
    Raised for failures that should be retried quickly — not rate limits.
    Examples: Claude timed out on a complex task, subprocess error unrelated to limits.
    """
    pass


def run_claude(prompt: str, allowed_tools: str = "Read,Edit,Bash", cwd: str = None, model: str = None) -> str:
    """
    Run `claude -p` non-interactively and return the text response.
    Strips ANTHROPIC_API_KEY from the environment so Claude Code uses the
    Max subscription via OAuth rather than falling back to API credits.

    Raises RateLimitError if a rate/usage limit is hit so callers can
    distinguish it from other failures and wait before retrying.
    """
    env = {k: v for k, v in os.environ.items() if k != "ANTHROPIC_API_KEY"}

    cmd = [
        "claude",
        "--allowedTools", allowed_tools,
        "--output-format", "json",
        "--dangerously-skip-permissions",  # required for non-interactive automation
    ]

    # Write prompt to a temp file and pipe via stdin to avoid Windows
    # command line length limit (~32,767 chars) which breaks long diffs
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False, encoding="utf-8"
    ) as tmp:
        tmp.write(prompt)
        tmp_path = tmp.name

    try:
        with open(tmp_path, "r", encoding="utf-8") as stdin_file:
            result = subprocess.run(
                cmd,
                stdin=stdin_file,
                cwd=cwd or REPO_DIR,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=900,  # 15 minutes — coder reads the full codebase before writing
                env=env,
            )
    except subprocess.TimeoutExpired:
        raise TransientError(
            "claude CLI timed out after 15 minutes"
        )
    finally:
        os.unlink(tmp_path)

    # Check for errors in the JSON response — only when is_error is true
    # to avoid false positives where phrases appear in normal output
    try:
        data = json.loads(result.stdout)
        if data.get("is_error"):
            result_text = (data.get("result", "") + result.stderr).lower()
            if "prompt is too long" in result_text:
                raise PromptTooLongError(
                    f"Prompt exceeded Claude context window:\n{result.stdout[:300]}"
                )
            # Anthropic 500 = transient server error, retry quickly
            if '"type":"api_error"' in result.stdout or "internal server error" in result_text:
                raise TransientError(
                    f"Anthropic API returned a server error (500) — will retry.\n"
                    f"{result.stdout[:200]}"
                )

            if any(phrase in result_text for phrase in RATE_LIMIT_PHRASES):
                # Try to parse wait time from the CLI message.
                # Known formats:
                #   "please try again after [unix timestamp]"
                #   "you've hit your limit · resets 2am (America/Toronto)"
                wait_seconds = None
                import re, time as _time
                from datetime import datetime
                import zoneinfo

                # Format 1: unix timestamp
                match = re.search(r"please try again after (\d+)", result_text)
                if match:
                    reset_at = int(match.group(1))
                    wait_seconds = max(0, reset_at - int(_time.time())) + 60

                # Format 2: "resets 2am (America/Toronto)"
                if wait_seconds is None:
                    match = re.search(r"resets (\d+)(am|pm)\s*\(([^)]+)\)", result_text)
                    if match:
                        try:
                            hour = int(match.group(1))
                            ampm = match.group(2)
                            tz_name = match.group(3)
                            if ampm == "pm" and hour != 12:
                                hour += 12
                            elif ampm == "am" and hour == 12:
                                hour = 0
                            tz = zoneinfo.ZoneInfo(tz_name)
                            now = datetime.now(tz)
                            reset = now.replace(hour=hour, minute=0, second=0, microsecond=0)
                            if reset <= now:
                                # Already past that time today — must mean tomorrow
                                from datetime import timedelta
                                reset += timedelta(days=1)
                            wait_seconds = int((reset - now).total_seconds()) + 60
                        except Exception:
                            pass  # couldn't parse — fall back to default interval

                raise RateLimitError(
                    f"Claude rate/usage limit hit:\n{result.stdout[:300]}",
                    wait_seconds=wait_seconds,
                )
    except (json.JSONDecodeError, AttributeError):
        pass

    if result.returncode != 0:
        raise RuntimeError(
            f"claude CLI failed (exit {result.returncode}):\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )

    try:
        data = json.loads(result.stdout)
        return data.get("result", result.stdout)
    except json.JSONDecodeError:
        return result.stdout.strip()
