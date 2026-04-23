# General Orchestrator

A LangGraph-based orchestrator that coordinates coding agents (tester, coder, reviewer, docs-writer, issue-decomposer) against a target GitHub repository.

## How it works

1. You point the orchestrator at a GitHub issue number.
2. It fetches the issue, composes prompts for each agent by interpolating variables from `agents.toml` and runtime context, and runs agents in sequence via a LangGraph state machine.
3. Each agent calls `claude -p` (Claude Code CLI) with its composed prompt and writes results back to the context dict for downstream agents.

```
issue → issue-decomposer → tester → coder → reviewer → docs-writer
```

The coder/reviewer loop can repeat until the reviewer passes or a max-attempt limit is hit.

## Quick start

```bash
pip install -r requirements.txt
python main.py <issue_number>
# Resume a paused issue after approval:
python main.py <issue_number> approve
```

See `SETUP.md` for one-time secrets and workflow setup.

## Configuration

### `defaults/agents.toml`

The orchestrator ships with default agent definitions and conventions. Target repos can override any agent or the entire `[conventions]` block by providing a root-level `agents.toml`.

Merge rule: per-agent entries and the `[conventions]` block are replaced wholesale when the target repo supplies them. Other agents remain from defaults.

### Target-repo layout expected

```
agents.toml          # optional — overrides defaults/agents.toml sections
prompts/<agent>/<task>.md  # optional — overrides default task prompt body
.agents.md           # optional — per-agent shared context injected into every prompt
```

### Prompt composition order

For each `(agent, task)` pair the final prompt is assembled as:

1. Agent `identity` string (from registry)
2. `defaults/.agents.md` `@shared` + `@<agent>` sections
3. Target repo's `.agents.md` `@shared` + `@<agent>` sections (if present)
4. Task body — target repo's `prompts/<agent>/<task>.md` wins wholesale; else the default's pre-contract body
5. Output contract — always from the orchestrator default, split on `<!-- ORCHESTRATOR OUTPUT CONTRACT — DO NOT MODIFY -->`
6. Runtime context — appended **after** variable interpolation (prevents user-supplied values from being scanned for `{{...}}` patterns)

Variable interpolation happens over steps 1–5 using `{{variable_name}}` syntax.

---

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `TARGET_REPO_PATH` | `working-repo/` (relative to orchestrator root) | Absolute path to the target repository. Set to point the orchestrator at a different repo. Full setup docs in `docs/target-repo-setup.md` (Task 6.4). |

---

## Template variables

Template variables appear in prompt files as `{{variable_name}}`. They are resolved by `orchestrator/loader.py::interpolate()` at prompt-composition time. Every variable used in a prompt **must** be resolvable; an undefined variable raises a `ValueError` naming the prompt file.

Variables fall into two categories:

### Convention variables

Set in `[conventions]` of `agents.toml`. The target repo's `[conventions]` block replaces the defaults' entirely if present.

| Variable | Default value | Description |
|---|---|---|
| `{{primary_language}}` | `"the project's primary programming language"` | The language used by the target repo (e.g. `"Python"`, `"TypeScript"`). Used by `issue-decomposer/decompose.md` when estimating scope. |
| `{{codebase_layout_hint}}` | `"the project's main source directories and test directory"` | A short description of where source and test files live (e.g. `"src/ for source, tests/ for tests"`). Used by `issue-decomposer/decompose.md`. |
| `{{test_file_pattern}}` | `"test_*.py"` | Glob pattern for locating test files (e.g. `"test_*.py"`, `"*.test.ts"`). Used by `reviewer/review.md` and `tester/write-tests.md`. |
| `{{test_file_extension}}` | _(must be set by target repo)_ | File extension for new test files (e.g. `"py"`, `"test.ts"`). Used by `tester/write-tests.md`. |
| `{{test_runner}}` | _(must be set by target repo)_ | Testing framework name (e.g. `"pytest"`, `"jest"`). Used by `tester/write-tests.md`. |
| `{{test_command}}` | _(must be set by target repo)_ | Shell command to run the test suite (e.g. `"pytest"`, `"npm test"`). Used by `coder/implement.md` and `coder/fix-ci.md`. |
| `{{install_command}}` | _(must be set by target repo)_ | Dependency install command (e.g. `"pip install -r requirements.txt"`). Used in `coder/implement.md`, `coder/fix-review.md`, and `coder/fix-ci.md` to tell the coder **not** to run it. |
| `{{module_system_hint}}` | _(must be set by target repo)_ | Short description of the import/module system (e.g. `"Python imports"`, `"ES Modules with named exports"`). Used by `coder/implement.md`. |

### Runtime context variables

Populated by the orchestrator or individual agents at runtime. They come from the issue being processed or from earlier agents in the pipeline.

| Variable | Set by | Description |
|---|---|---|
| `{{issue_number}}` | `orchestrator.py` | GitHub issue number (integer). Available to all agents. |
| `{{issue_title}}` | `orchestrator.py` | GitHub issue title string. Used by `issue-decomposer/decompose.md`. |
| `{{issue_body}}` | `orchestrator.py` | Full GitHub issue body text. Falls back to the title if the body is empty. Used by `issue-decomposer/decompose.md`. |
| `{{issue_description}}` | `test_agent.py` | Alias for `issue_body` as passed downstream. Used by `coder/implement.md`, `tester/write-tests.md`, and `coder/fix-ci.md`. |
| `{{test_file_path}}` | `test_agent.py` | Relative path to the test file written by the tester agent (e.g. `"tests/test_foo.py"`). Used by `coder/implement.md`. |
| `{{test_code}}` | `test_agent.py` | Full contents of the test file at `{{test_file_path}}`. Used by `coder/implement.md`. |
| `{{code_changes}}` | `coder_agent.py` / `reviewer_agent.py` | Output of `git diff origin/<default_branch>..HEAD` — the diff the coder produced. Used by `reviewer/review.md` and `docs-writer/document.md`. |
| `{{review_history}}` | `reviewer_agent.py` | Accumulated text from all prior review rounds. Used by `reviewer/review.md` so the reviewer can avoid repeating itself. |
| `{{feedback_section}}` | `coder_agent.py` | Reviewer feedback or retry context injected into the coder's re-attempt prompt. Used by `coder/implement.md`. |
| `{{reviewer_feedback}}` | `coder_agent.py` | Feedback text from the reviewer agent listing specific issues the coder must fix. Passed from the orchestrator context into the fix-review prompt. Used by `coder/fix-review.md`. |
| `{{ci_errors}}` | `coder_agent.py` | CI failure output and diagnosis text. Passed when the orchestrator detects a failing CI run on a PR. Used by `coder/fix-ci.md`. |
| `{{inline_findings}}` | `coder_agent.py` | Formatted list of low-effort inline review fixes the coder must apply immediately in the current PR. Used by `coder/fix-inline.md`. |
| `{{brief}}` | `scripts/run_agent.py` | Product brief text passed as `--input` to `product-planner draft-prd`. Used by `product-planner/draft-prd.md`. |
| `{{prd}}` | `scripts/run_agent.py` | Full PRD text passed as `--input` to `product-planner propose-issues`. Used by `product-planner/propose-issues.md`. |
| `{{input}}` | `scripts/run_agent.py` | Generic input text passed as `--input` for tasks not listed in `_TASK_INPUT_VARS` (e.g. `triager triage`). Used by `triager/triage.md`, `security-auditor/audit.md`. |
| `{{last_tag}}` | `scripts/run_agent.py` | The most recent git tag marking the previous release (e.g. `"v1.2.3"`). Used by `release/draft-changelog.md`. |
| `{{merged_prs}}` | `scripts/run_agent.py` | Formatted list of pull requests merged since `{{last_tag}}`. Used by `release/draft-changelog.md`. |
| `{{current_version}}` | `scripts/run_agent.py` | Current version string (e.g. `"1.2.3"`). Used by `release/bump-version.md`. |
| `{{changelog_entry}}` | `release_agent.py` | Changelog text produced by the `draft-changelog` task, passed into `bump-version`. Used by `release/bump-version.md`. |

### Adding a new variable

1. **Convention variable** — add it to `[conventions]` in `defaults/agents.toml` with a clear default value. Document it in the table above with its purpose and which prompt(s) use it.
2. **Runtime context variable** — populate it in the agent that produces it (add to the `return` dict or the `context` dict passed to `compose_prompt`). Document it in the table above, noting which agent sets it and which downstream agents consume it.
3. **Use it in a prompt** — reference it as `{{variable_name}}` in the relevant `.md` file under `defaults/prompts/`.

> **Rule:** Every variable used in a prompt must be documented in this README. When you add a variable, update this file in the same commit.

---

## Project layout

```
defaults/
  agents.toml          # default agent definitions and conventions
  prompts/             # default prompt files, one subdir per agent
    coder/implement.md
    docs-writer/document.md
    issue-decomposer/decompose.md
    reviewer/review.md
    tester/write-tests.md
    ...
  .agents.md           # orchestrator-level shared agent context

agents/                # Python agent implementations
  base_agent.py        # run_claude() helper — the only place that calls claude -p
  coder_agent.py
  documentation_agent.py
  issue_decomposer_agent.py
  merger_agent.py
  reviewer_agent.py
  test_agent.py

orchestrator/
  orchestrator.py      # LangGraph state machine
  loader.py            # registry loading, prompt composition, variable interpolation

main.py                # CLI entry point
```

## Development

```bash
pip install -r requirements-dev.txt
pytest
```

Tests live under `tests/`. See `CLAUDE.md` for the test-writing policy (short version: write tests for `parse_response` methods and `loader.py` logic; skip tests for prompt text changes).
