# CLAUDE.md

Project-specific instructions for `@claude` invocations on this repository.

This is the **orchestrator repo** — a LangGraph-based system that coordinates coding agents against target repositories. It is itself undergoing a refactor (see `docs/orchestrator-refactor-prd.md` and `docs/orchestrator-refactor-tasks.md`).

---

## Global rules

- **Base branch:** `main`. Open PRs against `main` unless an issue says otherwise.
- **Branch naming:** `claude/<short-slug>` (matches the PR-merged notifier workflow filter).
- **Commit style:** short imperative subject lines, e.g. `add OutputContractError exception`.
- **Never edit `checkpoints.db`.** It's a runtime artifact and will be dropped at cutover per PRD §5.
- **Never touch `spades-orchestrator.zip` or `files.zip`.** Legacy artifacts unrelated to this refactor.
- **Do not start agents or run the orchestrator end-to-end from inside a CI or `@claude` job.** Live end-to-end validation tasks (2.3, 5.6, 6.5, 7.2) must be run by a human — `@claude` should implement the code changes and mark the task ready for human validation in the PR description.

## How tasks are defined

Each GitHub Issue in this refactor corresponds to one task from `docs/orchestrator-refactor-tasks.md`. The issue body should include:

1. The full task block (Goal, Steps, Acceptance).
2. A pointer to the PRD: `See docs/orchestrator-refactor-prd.md for full context.`
3. A handoff line at the end if the next task should auto-start: `When this issue is completed, assign Issue #<N+1> to dantonel`. The `pr-merged-notifier` workflow reads this and pings the next issue after merge.

## Default workflow when assigned to an issue

1. **Read the PRD** (`docs/orchestrator-refactor-prd.md`) and the **task list** (`docs/orchestrator-refactor-tasks.md`) for surrounding context, especially adjacent tasks.
2. **Read the specific task's Goal, Steps, and Acceptance** from the issue body.
3. **Implement the changes** on a new branch named `claude/<issue-number>-<short-slug>`.
4. **Verify the Acceptance criteria locally** — run the grep commands, run `pytest tests/test_loader.py` if relevant, etc. If an acceptance check requires running the orchestrator against a live repo, note it in the PR body as "requires human validation" rather than attempting it.
5. **Commit and push.** Commit messages should be short and imperative.
6. **Open a PR** against `main`. The PR body must include:
   - `Closes #<issue-number>` (triggers the PR-merged notifier).
   - A short summary of what was done.
   - An "Acceptance criteria" section that restates each criterion with a ✅ (verified by me) or ⏳ (requires human validation).
7. **Wait for CI.** The `ci-failure-notifier` and `claude-code-review` workflows will ping you if anything comes back. Fix on the same branch, push, and repeat until checks pass.
8. **Do not merge yourself.** Merging is gated on the `pr-merged-notifier` flow being triggered by a human merge, which keeps the sequential-issue handoff working cleanly.

## Bootstrap task: creating issues from the task list

If assigned an issue whose body says something like "Create GitHub issues for every task in `docs/orchestrator-refactor-tasks.md`":

1. Read the task list file.
2. For each `### Task N.M:` block, create one GitHub issue using `gh issue create`:
   - **Title:** `Task N.M: <the task's heading text>` (strip the `### Task N.M:` prefix).
   - **Body:** the full markdown for that task (Goal, Context if present, Steps, Acceptance), followed by:
     - `---`
     - `See [docs/orchestrator-refactor-prd.md](./docs/orchestrator-refactor-prd.md) for full context.`
     - `When this issue is completed, assign Issue #<next-issue-number> to dantonel` — but only if the next task exists. **Do this by editing each issue after all are created**, once you know the issue numbers GitHub assigned. Task 7.2 (the last task) should NOT have a handoff line.
   - **Labels:** `refactor`, `phase-<N>` (create labels first with `gh label create` if they don't exist).
3. After all issues are created, post a single comment on the bootstrap issue listing the created issue numbers in order, so the human can kick off Task 1.1 with a single `@claude` assignment.
4. Do not start implementation on any of the created issues — they are for later, one at a time.

**One exception to the "do this in order" rule:** Phase 5 tasks (5.1–5.5) can run in parallel once 5.1 lands, because each new agent is independent. If the task list's closing notes call this out, respect it. Still add handoff lines to chain 5.1 → 5.2, but note in the issue body for 5.2–5.5 that they can start once 5.1 is merged rather than strictly in sequence.

## Orchestrator-specific conventions

- **Python 3.11+** (required for `tomllib`). If `tomllib` is unavailable at runtime, fall back to the `tomli` package — do not use `toml` (an older, less-maintained library).
- **Async patterns** — agents use `async def run(self, context: dict) -> dict`. Keep this signature when adding new agents.
- **Subprocess calls to `claude`** — only `agents/base_agent.py::run_claude` invokes `claude -p`. Don't spawn the CLI from elsewhere.
- **Prompt files** are plain markdown in `defaults/prompts/<agent>/<task>.md`. Use `{{variable}}` for substitutions (never single braces — they conflict with JSON examples in prompts).
- **Output contract marker** — always exactly `<!-- ORCHESTRATOR OUTPUT CONTRACT — DO NOT MODIFY -->` (em-dash, not hyphen). The loader splits on this exact string.
- **Orchestrator defaults namespace rule** — nothing under `defaults/` should be named the same as a file a target repo is expected to provide. Target repos own root-level `agents.toml`, `prompts/`, `.agents.md`; the orchestrator owns `defaults/agents.toml`, `defaults/prompts/`, `defaults/.agents.md`. Do not violate this, even temporarily.

## Allowed tools

The `@claude` action is configured with these tool permissions (see `.github/workflows/claude.yml`):

- `gh issue` (create, close, comment, list, edit)
- `gh pr` (create, merge)
- `gh label`
- `git status`, `git log`
- `pip install` (for dev deps when running tests locally in the sandbox)

If you need a tool that isn't on that list, **do not attempt to work around it** — comment on the issue explaining what's needed and stop.

## Things that are out of scope for `@claude`

- Dropping the checkpoint database (`checkpoints.db`) — human-only, at cutover.
- Any write to a target repo (the Spades repo or the Monday repo). Target-repo config tasks (6.1) should produce the files **within this repo** under something like `examples/spades-target/`, and the human copies them to the real Spades repo.
- Restarting the running orchestrator process.
- Live-running the orchestrator against a real GitHub issue.

## When something is ambiguous

Comment on the issue describing the ambiguity and stop. Do not guess. The refactor tolerates a slower pace much better than it tolerates a subtle regression.
