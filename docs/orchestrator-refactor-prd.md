# PRD: Orchestrator Refactor — Generalization + Agent/Task Separation

**Status:** Draft
**Owner:** (you)
**Target completion:** By EOW following Monday, to support bring-up of second target repo
**Branch strategy:** Long-lived feature branch, cut over on completion

---

## 1. Background

The current orchestrator is a LangGraph-based system that processes GitHub issues on the Spades Online codebase. It coordinates a set of agents — coder, tester, reviewer, documentation, planner, merger — each implemented as a Python class with hard-coded prompts, models, and allowed tools. The orchestrator is tightly coupled to Spades: prompts reference "Spades Online card game backend" by name, assume ES Modules and `node:test`, and encode Node.js-specific conventions throughout.

Starting the week of this PRD's completion, the same orchestrator needs to run against a second repository whose language, domain, and conventions are unknown. Beyond that second repo, the orchestrator is intended as a generic tool that can be pointed at arbitrary codebases.

This refactor generalizes the orchestrator and restructures its prompt-handling architecture to support this.

## 2. Goals

1. Separate the orchestrator (generic engine) from target-repo configuration (domain-specific content).
2. Restructure prompts around a clean agent-vs-task distinction, resolving the "four coder templates in one file" pattern that currently exists.
3. Add five new agents beyond the current six, with at least basic executability.
4. Make the orchestrator language-agnostic so the Monday repo (language unknown) can be supported with target-repo config only.
5. Preserve existing orchestrator capabilities: LangGraph workflow, GitHub integration, rate-limit handling, LangSmith tracing.

## 3. Non-goals

### Orchestration and workflow
- LangGraph graph structure stays as-is (`graph.py`, `state.py`, issue workflow).
- Issue selection logic (`issue_selector.py`) unchanged.
- New agents are *not* wired into the LangGraph workflow as part of this refactor; they must be executable in isolation but workflow integration is a separate project.

### Infrastructure
- `run_claude` execution model unchanged (still `subprocess.run` of `claude -p`).
- Rate limit, retry, and error-handling machinery unchanged. `OutputContractError` is added as a new exception type, handled identically to `TransientError`.
- GitHub integration (branch naming, PR creation, issue labeling) unchanged.
- LangGraph checkpoint schema unchanged. DB is dropped on cutover rather than migrated.

### Config system scope
- No folder-level `.agents.md` cascade. Architected-for, deferred to v2.
- No section-level override inheritance for tasks. Overrides are wholesale-only.
- No schema-driven output parsing. Each agent owns its own `parse_response` method.
- No `validate` or `explain` CLI commands.
- No config hot-reload.
- No config validation beyond what's needed to produce clear errors at startup.

### Observability
- Existing LangSmith tracing is preserved but not extended.
- Subprocess-level prompt/response visibility is explicitly deferred to future work.
- No new logging, metrics, or tracing infrastructure.
- No agent run history or replay.

### Prompt quality
- Existing prompts are extracted to files as-faithfully-as-possible. Quality improvements are follow-up work. Exception: Spades-specific language is relocated from orchestrator defaults to the Spades target repo (required for generalization).
- New-agent prompts are sufficient to run end-to-end, not production-polished.

## 4. Architecture

### 4.1 Two-repo model

The orchestrator repo (this codebase) contains the engine and default agent configuration. Target repos (Spades, the Monday repo, and future repos) contain overrides and domain-specific config. The orchestrator is cloned once per target repo — or configured to point at an arbitrary target repo path via environment variable.

**Orchestrator repo structure:**

```
orchestrator-repo/
├── main.py
├── agents/                     # agent Python classes (existing)
├── orchestrator/               # LangGraph wiring (existing)
├── defaults/
│   ├── agents.toml             # default agent registry
│   └── prompts/
│       ├── <agent>/
│       │   └── <task>.md       # one file per (agent, task)
│       └── ...
└── docs/
    └── target-repo-setup.md    # author-facing guide
```

**Target repo structure (all files optional):**

```
target-repo/
├── agents.toml                 # overrides for agent config
├── prompts/
│   └── <agent>/
│       └── <task>.md           # overrides for specific tasks
└── .agents.md                  # root-level shared and per-agent rules
```

Any file omitted from the target repo falls back to the orchestrator default.

**Structural invariant:** no file that a target repo is expected to provide may exist at the same path in the orchestrator repo. All orchestrator defaults live under `defaults/`; target-repo files live at the root of the target. This ensures the orchestrator can target itself without filesystem collisions — self-targeting looks for root-level `agents.toml`, `prompts/`, and `.agents.md`, which the orchestrator repo does not use for its own defaults. The `defaults/` directory is the sole location for orchestrator-shipped config; any future config file added in either repo must respect this separation.

### 4.2 Agent-vs-task separation

An **agent** is an identity: a role, a model, a tool set, and an inherent set of rules. A **task** is a situation the agent is operating in.

Previously, the coder had four prompt templates (`PROMPT_TEMPLATE`, `RETRY_PROMPT_TEMPLATE`, `CI_FIX_PROMPT_TEMPLATE`, `INLINE_FIX_PROMPT_TEMPLATE`). These were four tasks filed under one agent, conflated into one file.

In the new model, agents are defined in `agents.toml` and tasks are individual markdown files:

```toml
[agents.coder]
model = "claude-opus-4-7"
tools = ["Read", "Edit", "Bash"]
identity = """
You are a coding agent. Always read the codebase to understand patterns
before writing. Prefer clarity over cleverness.
"""
tasks = ["implement", "fix-review", "fix-ci", "fix-inline"]
prompt_dir = "prompts/coder"
```

Each listed task has a corresponding file at `prompts/coder/<task>.md`.

### 4.3 Prompt composition

When the orchestrator invokes an agent on a task, it composes the final prompt in this order:

1. **Agent identity** — from `agents.toml`, target override wins if present.
2. **Root `.agents.md` cascade** — the target repo's root `.agents.md`, pulling the `@shared` section (applies to all agents) and the `@<agent>` section. Orchestrator default `.agents.md` is read first if present.
3. **Task body** — from `prompts/<agent>/<task>.md`, target override wins wholesale if present.
4. **Output contract** — *always* taken from the orchestrator default task file. Structurally unoverridable.
5. **Runtime context** — issue body, test code, CI errors, conventions variables, etc.

The output contract is a marked region in each default task file:

```markdown
<!-- task: fix-ci -->

CI failed with these errors:
{{ci_errors}}

Fix only what's broken. To verify your fix, run: {{test_command}}

<!-- ORCHESTRATOR OUTPUT CONTRACT — DO NOT MODIFY -->

After fixing, output exactly one line in this format:
  COMMIT: <your commit message>
```

The loader splits on the `<!-- ORCHESTRATOR OUTPUT CONTRACT -->` marker. The target's override, if present, contributes only the pre-marker body. The orchestrator's post-marker block is always appended.

### 4.4 Target repo conventions

To support language-agnostic operation, target repos declare their conventions in `agents.toml`:

```toml
[conventions]
primary_language = "python"
test_command = "pytest"
lint_command = "ruff check"
install_command = "pip install -e ."
test_file_pattern = "tests/**/*.py"
```

These values are available as `{{primary_language}}`, `{{test_command}}`, etc. in prompts that need them. Orchestrator default prompts reference these variables instead of hard-coding language-specific commands.

### 4.5 Variable interpolation

Prompts use double-brace syntax: `{{variable_name}}`. A simple regex-based interpolator performs substitution before the prompt is sent to Claude. Undefined variables raise a loud error rather than silently leaving `{{foo}}` in the prompt.

Double braces (rather than single) avoid conflicts with literal single braces in code snippets, JSON examples, and regex patterns embedded in prompts.

### 4.6 Output parsing

Every agent implements a `parse_response(text: str) -> dict` method. The default implementation in the base class returns `{"response": text}`. Agents with structured output (coder, tester, planner, reviewer) override it to extract their specific fields.

When parsing fails, the method raises `OutputContractError(agent, task, expected, got_preview)`. The orchestrator catches this and handles it as a retryable failure (same path as `TransientError`), surfacing a clear error in logs. No silent fallback to defaults.

### 4.7 Agent inventory

| Agent | Tasks | Notes |
|---|---|---|
| `product-planner` | `draft-prd`, `propose-issues` | New. Upstream of issue workflow. |
| `issue-decomposer` | `decompose` | Renamed from `planner`. |
| `coder` | `implement`, `fix-review`, `fix-ci`, `fix-inline` | Existing. |
| `tester` | `write-tests`, `fix-tests` | `fix-tests` is new (tests were wrong, not code). |
| `reviewer` | `review` | Existing. Single task, no separate re-review mode. |
| `docs-writer` | `document`, `fix-review` | Existing + `fix-review` for doc review comments. |
| `triager` | `triage` | New. Reads failing CI / bug reports, routes. |
| `refactor` | `refactor`, `fix-review` | New. Structural changes. |
| `security-auditor` | `audit` | New. Scans diffs for secrets, unsafe patterns. |
| `release` | `draft-changelog`, `bump-version` | New. Post-merge summarization. |
| `merger` | — | No LLM calls. Pure git operations. Excluded from prompt system. |

### 4.8 File format decisions

- **Registry:** TOML (`agents.toml`). Native Python support in 3.11+, comment-friendly, avoids YAML footguns.
- **Prompt files:** Plain Markdown, no frontmatter. Task metadata lives in `agents.toml`; the output-contract marker is a structural HTML comment.
- **`.agents.md`:** Markdown with `@shared` and `@<agent>` section headers (`## @shared`, `## @coder`, etc.).

## 5. Implementation plan

### 5.1 Work decomposition

Roughly six phases, executed on a long-lived feature branch:

**Phase 1: Foundation**
- Create `defaults/agents.toml` and `defaults/prompts/` scaffolding.
- Implement the loader: registry loading, prompt composition, variable interpolation.
- Add `OutputContractError` to `base_agent.py` and wire it into the orchestrator's retry loop.
- Add the `conventions` concept and the target-repo config loading path.

**Phase 2: Migrate tester** (simplest agent, single task)
- Extract `PROMPT_TEMPLATE` to `defaults/prompts/tester/write-tests.md`.
- Refactor `TestAgent.run` to use `compose_prompt`.
- Move `TEST_FILE:` parsing into `parse_response`.
- Verify end-to-end on Spades.

**Phase 3: Migrate documentation, issue-decomposer (renamed), reviewer**
- One at a time, same pattern. Reviewer tests `parse_response` with JSON output.

**Phase 4: Migrate coder**
- Extract all four templates to `defaults/prompts/coder/{implement,fix-review,fix-ci,fix-inline}.md`.
- Refactor `CoderAgent.run` to select task based on current mode.
- Move `COMMIT:` parsing into `parse_response`.

**Phase 5: Add new agents**
- `product-planner`, `issue-decomposer` (already migrated in phase 3, confirm rename), `triager`, `refactor`, `security-auditor`, `release`.
- Each needs an entry in `agents.toml`, prompt files for its tasks, and an agent class (or a generic one that reads config).
- Each runs at least once end-to-end against some input. Prompts can be rough.

**Phase 6: Spades target-repo extraction**
- Create a `spades/` target-repo config with Spades-specific language, conventions, and any overrides.
- Move all Spades-isms from orchestrator defaults into this config.
- Verify full Spades workflow still passes end-to-end.

**Cutover:**
- Drop `checkpoints.db`.
- Merge branch to main.
- Bring up Monday repo with its own target-repo config.

### 5.2 Migration order rationale

Tester first because it's the smallest end-to-end validation of the loader, registry, and `parse_response` pattern. Simpler agents follow. Coder last among the migrations because it's the most complex and benefits from a battle-tested loader. New agents come after all existing migrations so the patterns are locked in.

### 5.3 Branch hygiene

Although concurrent main-branch work is not anticipated, merge main into the feature branch at the completion of each phase. Cheap insurance against env or dependency changes landing unexpectedly.

## 6. Acceptance criteria

### Functional
- **F1.** Spades parity: refactored orchestrator processes Spades issues with behavior equivalent to today (decompose, test, implement, review, fix-on-review, fix-on-CI, fix-on-inline-comment, document, merge).
- **F2.** Monday repo bring-up: same orchestrator, pointed at the Monday repo, runs at least implement → test → review using only target-repo config. Zero orchestrator code changes required.
- **F3.** New agents functional: product-planner, triager, refactor, security-auditor, release each run at least one task successfully end-to-end.

### Structural
- **S1.** No prompts in Python source: `grep -rn 'PROMPT_TEMPLATE\|"""You are' orchestrator/ agents/` returns nothing.
- **S2.** No Spades-isms in orchestrator defaults: `grep -rn 'Spades\|card game\|ES Modules\|npm\|node:test' defaults/` returns nothing.
- **S3.** Adding a task requires no Python changes: verified by adding a trivial test task (e.g., a `summarize` task for some agent) using only a new `.md` file and an `agents.toml` entry.
- **S4.** Target conventions declared in one place: no hardcoded test/build/lint commands in orchestrator code; prompts reference `{{test_command}}` etc.
- **S5.** Namespace separation holds: no file that a target repo is expected to provide (`agents.toml`, `prompts/`, `.agents.md`) exists at the root of the orchestrator repo. Verified by `ls` at the orchestrator root after all phases complete.

### Safety
- **H1.** `OutputContractError` fires on malformed responses: verified by deliberately shipping a broken target-repo task override and confirming a clear error is raised and retried.
- **H2.** Output-format sections are structurally unoverridable: verified by attempting to override the post-marker block in a target repo task file and confirming the orchestrator's version still ships.

### Operational
- **O1.** Clear error messages at startup for: typos in `agents.toml`, missing prompt files, malformed TOML, undefined variables referenced in prompts.
- **O2.** Target-repo-author docs exist in `docs/target-repo-setup.md` and are sufficient for someone else to configure a new target repo.

### Escape hatch
- **E1.** If Monday repo bring-up reveals a required generalization that needs orchestrator code changes of ≤2 hours, it counts as legitimate v1.1 scope rather than a refactor bug. Larger generalizations are v1 failures.

## 7. Future work (explicitly deferred)

Items considered and deliberately deferred, with rationale recorded so they are not forgotten:

### Config system
- **Folder-level `.agents.md` cascade.** The file format supports it; the loader doesn't walk below the root. Add when per-folder rules become a real need.
- **Section-level task override inheritance.** Wholesale replacement is simpler to reason about and debug. Revisit if duplication pain emerges from target repos copying long default task files.
- **Schema-driven output parsing.** Each agent's `parse_response` works today. Centralize when output shapes proliferate across agents and the duplication is real.
- **`validate` CLI command.** Walks target repo and reports config mistakes (unknown `@agent` names, missing prompt files, orphaned overrides). Highest-leverage future addition.
- **`explain` CLI command.** Given `(agent, task, target)`, prints the fully composed prompt with provenance comments. Invaluable for debugging agent behavior.
- **Config hot-reload.** File-watching for live prompt updates during development.

### Observability
- **Attach composed prompt metadata to LangGraph state.** Fields like `last_composed_prompt`, `last_task`, `last_response_preview` propagate into LangSmith traces automatically. Low-effort, high-value.
- **LangSmith spans from `run_claude`.** Wrap the subprocess call with `langsmith.traceable` or the `RunTree` API to add a child span with full prompt/response content.

### Agents and workflow
- **Wiring new agents into the LangGraph workflow.** Triager in the retry loop, product-planner upstream of issue creation, etc. Workflow redesign is a separate project.
- **Prompt quality pass.** Once extracted, prompts can be iterated on independently. Including the new-agent prompts.
- **Agent run history / replay.** Store composed prompts and responses for later inspection.

### Parsing
- **Centralized parsing with declared schemas.** Move per-agent `parse_response` logic into a declarative schema attached to each task in `agents.toml`.

### Workflow extensions
- **Merge conflict handling.** The current merger runs `gh pr merge --squash` and records failure strings generically. It does not detect conflicts ahead of time, rebase, or route conflicts back to the coder. A future extension would add a `resolve-conflicts` task on the coder (or a pre-merge rebase step in the merger) and graph logic to route to it. This is a pre-existing gap, not introduced by the refactor, but more likely to surface on repos with heavier main-branch activity than Spades.

### Self-operation
At the end of this refactor, pointing the orchestrator at its own repo as a target is structurally supported — the two-repo model, language-agnostic design, and file-based prompts all make it natural. Self-operation is explicitly bounded to the following model:

- **Running orchestrator never modifies itself.** Changes land in the repo via normal PR workflow; the running instance remains on the old code. Updates are adopted via a deliberate manual `git pull` + process restart.
- **A separate orchestrator instance** handles self-modification work — never the production instance running against real repos.
- **Narrow-scope, human-supervised work only** in v1. Realistic targets: adding a new prompt file, tweaking an existing prompt, adding a small task. Not: structural changes to the graph, `run_claude`, or the loader without human review.

Prerequisites before this becomes reliable enough to trust:

- An orchestration-critical-files protection mechanism: `main.py`, `orchestrator/graph.py`, `orchestrator/state.py`, `agents/base_agent.py`, `orchestrator/loader.py` require human review before merge. Enforced via branch protection or a "needs-human-review" label the reviewer adds automatically when those paths are touched in a diff.
- Integration-test strategy for orchestration-critical changes. Unit tests on loader logic, prompt parsing, and `parse_response` work fine pre-merge. Graph-level workflow changes are harder to verify pre-merge and rely on manual testing after pull.

Full autonomous self-improvement (where the orchestrator safely modifies itself while running) is explicitly out of scope indefinitely.

## 8. Risks

| Risk | Likelihood | Mitigation |
|---|---|---|
| Monday repo reveals a generalization that exceeds the 2-hour escape hatch | Medium | Build in slack; if breached, descope a new agent to v1.1. |
| Output-contract parsing breaks silently during migration | Low | `OutputContractError` shipped in Phase 1, every migration validates parsing works. |
| Long branch goes stale despite low main-branch activity | Low | Merge main into branch at end of each phase. |
| New-agent prompts are too rough to be useful even for F3 | Medium | F3 only requires "runs end-to-end," not production quality. |
| Target-repo conventions miss a language-specific assumption that's buried in a prompt | Medium-high | Grep pass for language-specific terms (`npm`, `node`, `js`, `ES Modules`, etc.) before cutover. |
| Checkpoint DB drop loses an in-flight issue worth preserving | Low | Drain in-flight issues before cutover; accept restart cost if any remain. |

## 9. Open questions

None at time of drafting. All ten design questions resolved prior to PRD authoring.

---

**Appendix: File-level change summary**

| File | Change |
|---|---|
| `agents/base_agent.py` | Add `OutputContractError`; keep everything else. |
| `agents/test_agent.py` | Remove `PROMPT_TEMPLATE`; add `parse_response`; switch to `compose_prompt`. |
| `agents/documentation_agent.py` | Same pattern. |
| `agents/planner_agent.py` | Rename agent to `issue-decomposer`; same pattern. |
| `agents/reviewer_agent.py` | Same pattern. |
| `agents/coder_agent.py` | Remove all four templates; `run` selects task based on mode; `parse_response` for `COMMIT:`. |
| `agents/merger_agent.py` | No changes. |
| New: `agents/product_planner_agent.py`, `triager_agent.py`, `refactor_agent.py`, `security_auditor_agent.py`, `release_agent.py` | New agent classes. May share a base that reads config-driven agents if pattern supports it. |
| `orchestrator/orchestrator.py` | Catch `OutputContractError` in retry loop (one new except clause). |
| `orchestrator/graph.py` | No structural changes. |
| `orchestrator/state.py` | No changes (optional: add prompt metadata fields — future work). |
| New: `orchestrator/loader.py` | Registry loading, prompt composition, variable interpolation. |
| New: `defaults/agents.toml` | Agent registry. |
| New: `defaults/prompts/<agent>/<task>.md` | Extracted and new prompt files. |
| New: `defaults/.agents.md` | Orchestrator-level shared rules (minimal; most content lives in target repos). |
| New: `docs/target-repo-setup.md` | Author-facing documentation. |
| New (in Spades target): `agents.toml`, `prompts/`, `.agents.md` | Spades-specific config extracted from orchestrator. |
