# Setup: drop-in refactor workflow

This directory contains everything needed to run the orchestrator refactor as a series of `@claude`-executed GitHub Issues.

## One-time setup

1. **Copy files into the orchestrator repo root**, preserving paths:

   ```
   CLAUDE.md
   requirements.txt
   requirements-dev.txt
   docs/orchestrator-refactor-prd.md
   docs/orchestrator-refactor-tasks.md
   docs/bootstrap-issue.md
   .github/workflows/claude.yml
   .github/workflows/ci.yml
   .github/workflows/claude-code-review.yml
   .github/workflows/ci-failure-notifier.yml
   .github/workflows/pr-merged-notifier.yml
   ```

2. **Verify repo secrets are set** (the workflows assume these already exist from prior use on Spades):
   - `APP_ID` — GitHub App ID for `claude-bot-dom`.
   - `APP_PRIVATE_KEY` — private key for that App.
   - `CLAUDE_CODE_OAUTH_TOKEN` — Max-subscription OAuth token for the Claude Code action.

3. **Verify `requirements.txt` captures all runtime deps.** I inferred these from current imports in `agents/`, `orchestrator/`, and `main.py`. If the repo actually uses a different set (e.g., pinned versions), update the file before the first `@claude` run. The only new dev dep the refactor adds is `pytest` (for Task 1.7's loader unit tests).

4. **Commit and push to `main`.** The workflows don't run until they exist on the default branch.

## Kicking off the refactor

1. Create one GitHub Issue using the content of `docs/bootstrap-issue.md` as the body. Title it `Bootstrap: create issues for refactor tasks`.
2. Assign the bootstrap issue to `dantonel`. This triggers the `@claude` workflow (because of the `@claude` mention in the body plus the `assignee_trigger: dantonel` config in `claude.yml`).
3. `@claude` will create ~26 individual task issues with handoff lines chaining them together.
4. Once `@claude` posts the summary comment on the bootstrap issue, assign **Task 1.1** to `dantonel` to start real implementation.
5. From then on: each merged PR triggers `pr-merged-notifier.yml`, which reads the "When this issue is completed…" line and pings `@claude` on the next issue.

## What each workflow does

- **`claude.yml`** — the workhorse. Fires when an issue is assigned to `dantonel` or when someone types `@claude` in a comment. Runs the Claude Code action with the allowed-tools list from `CLAUDE.md`.
- **`ci.yml`** — lint + `pytest` on every PR to `main`. Replaces the Spades Node.js/Redis version; adjust if the orchestrator later adds services (e.g., a Postgres-backed checkpointer).
- **`claude-code-review.yml`** — automated code review on every PR that touches Python/config/doc files. Harmless if you disable it; kept because it catches issues early.
- **`ci-failure-notifier.yml`** — pings `@claude` on the PR when CI fails so the same branch gets fixed without manual intervention. Modified from the Spades version: does **not** auto-merge on success, because this refactor requires human review gate before merging (see CLAUDE.md).
- **`pr-merged-notifier.yml`** — the sequential-issue glue. When a `claude/*` PR lands on `main` and its body contains `Closes #N`, this pings `@claude` on issue N's handoff line, which chains to the next task.

## Manual overrides

- **Skip a task:** close the issue, then manually post a comment on the *next* issue telling `@claude` to proceed.
- **Pause the chain:** just don't merge a PR. The chain advances only on merge.
- **Reorder:** edit the handoff line in an issue body with `gh issue edit <n> --body-file -` before the predecessor merges.
- **Kill-switch for `@claude`:** disable the `claude.yml` workflow from the Actions tab. The other workflows won't trigger it on their own.

## Notes on scope

- The task list's end-to-end validation tasks (2.3, 5.6, 6.5, 7.2) are marked for human execution in `CLAUDE.md`. `@claude` will implement the code changes that precede them and mark the PR as "requires human validation." Don't let it run the orchestrator against a live target repo from within a CI job — that's what the `GITHUB_TOKEN: dummy-token-for-ci` line in `ci.yml` protects against.
- The cutover task (7.1) is explicitly out-of-scope for `@claude` per `CLAUDE.md`. It involves dropping `checkpoints.db` and restarting processes, which need a human.
