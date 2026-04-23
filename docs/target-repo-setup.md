# Target Repo Setup Guide

This guide explains how to configure a new repository to work with the general orchestrator. After reading it, you should be able to create the necessary config files, point the orchestrator at your repo, and have agents running against your codebase.

---

## Contents

1. [The two-repo model](#1-the-two-repo-model)
2. [Target repo file layout](#2-target-repo-file-layout)
3. [The `agents.toml` schema](#3-the-agentstoml-schema)
4. [Convention variables reference](#4-convention-variables-reference)
5. [How to override an agent task](#5-how-to-override-an-agent-task)
6. [How `.agents.md` works](#6-how-agentsmd-works)
7. [The output-contract rule](#7-the-output-contract-rule)
8. [Variable interpolation syntax](#8-variable-interpolation-syntax)
9. [Pointing the orchestrator at your repo](#9-pointing-the-orchestrator-at-your-repo)
10. [Minimum viable config](#10-minimum-viable-config)
11. [Worked example: the Spades target repo](#11-worked-example-the-spades-target-repo)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. The two-repo model

The orchestrator is designed as a reusable engine that operates against arbitrary codebases. It lives in its own repository (this one) and is kept entirely separate from the repos it works on. The repo the orchestrator processes is called the **target repo**.

```
orchestrator-repo/          ← the engine (this repo)
    main.py
    agents/
    orchestrator/
    defaults/               ← orchestrator-shipped defaults
        agents.toml
        prompts/
        .agents.md

target-repo/                ← the codebase being worked on
    agents.toml             ← (optional) overrides for agent config
    prompts/                ← (optional) task prompt overrides
    .agents.md              ← (optional) per-agent shared rules
    <all your source code>
```

The key principle: **any config file the target repo provides replaces the orchestrator default for that specific piece**. Everything else falls back to the orchestrator's shipped defaults. A target repo with zero config files works fine — it just gets purely generic agent behavior.

### Structural invariant

No file that a target repo is expected to provide (`agents.toml`, `prompts/`, `.agents.md`) exists at the root of the orchestrator repo. The orchestrator ships these under `defaults/`. This ensures the orchestrator can even target itself without filesystem collisions — self-targeting looks for root-level files, which the orchestrator repo doesn't have.

---

## 2. Target repo file layout

All target-repo config files live at the **root** of the target repo:

```
your-repo/
├── agents.toml                     # conventions + optional per-agent overrides
├── .agents.md                      # shared and per-agent instruction text
└── prompts/
    └── <agent-name>/
        └── <task-name>.md          # task prompt override (body only)
```

All three are optional. Omit any file you don't need — the orchestrator falls back gracefully.

### agents.toml

Declares how your repo works: language, test command, install command, and so on. Can also override individual agent definitions (model, tools, identity) if the defaults don't fit.

### .agents.md

Markdown file that injects contextual information about your codebase into every agent prompt. Structured with section headers so you can scope rules to specific agents or share them globally.

### prompts/<agent>/<task>.md

A full replacement for one task's prompt body. Use this when the default prompt for a task doesn't make sense for your repo and you want to provide a different framing entirely.

---

## 3. The `agents.toml` schema

`agents.toml` has two top-level sections: `[conventions]` and `[agents.*]`.

### `[conventions]` block

This is the primary section most target repos will use. It declares your repo's language, commands, and file conventions. The orchestrator's default prompts reference these as `{{variable}}` placeholders.

```toml
[conventions]
primary_language   = "python"
test_command       = "pytest"
install_command    = "pip install -r requirements.txt"
lint_command       = "ruff check"
test_file_pattern  = "tests/**/*.py"
test_file_extension = "py"
test_runner        = "pytest"
module_system_hint = "Python imports — use standard import statements"
codebase_layout_hint = "src/ for source code, tests/ for tests"
```

**Merge rule:** if a target repo supplies `[conventions]`, it **replaces** the orchestrator's `[conventions]` block entirely. There is no field-level merging — if you declare `[conventions]`, you own the whole block and must supply every variable that any prompt you use references. See [section 4](#4-convention-variables-reference) for the full list.

### `[agents.*]` blocks

Each `[agents.<name>]` block optionally overrides a specific agent's configuration. This is rarely needed — most repos only need `[conventions]`.

```toml
[agents.coder]
model    = "claude-opus-4-7"
tools    = ["Read", "Edit", "Bash"]
identity = """
You are a coding agent working on a Python/FastAPI service.
Always use type annotations. Prefer explicit imports over star imports.
"""
tasks    = ["implement", "fix-review", "fix-ci", "fix-inline"]
```

**Merge rule:** if a target repo supplies `[agents.coder]`, it replaces the orchestrator's `[agents.coder]` entry wholesale. Other agents not mentioned in the target's file remain from the defaults.

#### Agent block fields

| Field | Required | Description |
|---|---|---|
| `model` | No | Claude model ID to use (e.g. `"claude-opus-4-7"`, `"claude-sonnet-4-6"`). Falls back to orchestrator default if omitted. |
| `tools` | No | List of Claude Code tools allowed for this agent (e.g. `["Read", "Edit", "Bash"]`). |
| `identity` | No | A short paragraph describing the agent's role and style. Prepended to every prompt. |
| `tasks` | No | List of task names this agent supports. Each maps to a `prompts/<agent>/<task>.md` file. |
| `prompt_dir` | No | Override the directory where prompt files are looked up. Defaults to `prompts/<agent>`. |

### Built-in agent names

The following agents are defined in the orchestrator's `defaults/agents.toml`:

| Agent name | Tasks | Role |
|---|---|---|
| `coder` | `implement`, `fix-review`, `fix-ci`, `fix-inline` | Writes and fixes code |
| `tester` | `write-tests` | Writes test files |
| `reviewer` | `review` | Reviews code changes |
| `docs-writer` | `document` | Updates documentation |
| `issue-decomposer` | `decompose` | Breaks large issues into sub-issues |
| `product-planner` | `draft-prd`, `propose-issues` | Turns briefs into PRDs and issues |
| `refactor` | `refactor`, `fix-review` | Structural code changes |
| `triager` | `triage` | Classifies CI failures and bugs |
| `security-auditor` | `audit` | Scans diffs for vulnerabilities |
| `release` | `draft-changelog`, `bump-version` | Summarizes releases and bumps versions |

---

## 4. Convention variables reference

Convention variables are set in `[conventions]` in your `agents.toml` and referenced in prompts as `{{variable_name}}`. If your `[conventions]` block is present, you must supply every variable that any prompt you exercise actually uses — an undefined variable raises a `ValueError` at prompt-composition time rather than silently inserting `{{foo}}`.

The canonical variable reference — including defaults and which prompt files each variable appears in — is in the [README under "Template variables"](../README.md#template-variables). The table below focuses on what each variable requires from a target-repo operator's perspective.

### Required vs. optional

Variables marked **must set** have no useful default and must be provided for the relevant agents to work correctly.

| Variable | Must set? |
|---|---|
| `primary_language` | Recommended |
| `codebase_layout_hint` | Recommended |
| `test_file_pattern` | Yes (if using tester/reviewer) |
| `test_file_extension` | Yes (if using tester) |
| `test_runner` | Yes (if using tester) |
| `test_command` | Yes (if using coder) |
| `install_command` | Yes (if using coder) |
| `module_system_hint` | Yes (if using coder) |

### Variable descriptions

**`primary_language`** — Human-readable name for the language (e.g. `"Python"`, `"TypeScript"`, `"Go"`). Used by the issue-decomposer to estimate issue scope.

**`codebase_layout_hint`** — Short description of where source and test files live (e.g. `"src/ for source, tests/ for tests"`). Helps the issue-decomposer give accurate scope estimates.

**`test_file_pattern`** — Glob pattern for locating test files (e.g. `"tests/**/*.py"`, `"**/*.test.ts"`). Used by the tester to place new test files and by the reviewer to know where tests live.

**`test_file_extension`** — File extension for new test files, without the leading dot (e.g. `"py"`, `"test.ts"`, `"spec.rb"`). Used by the tester to generate the correct file path in its output.

**`test_runner`** — Name or short description of the testing framework (e.g. `"pytest"`, `"jest"`, `"node:test (built-in)"`). Used in the tester prompt to tell it which framework to use and how to import.

**`test_command`** — Shell command to run the test suite (e.g. `"pytest"`, `"npm test"`, `"go test ./..."`). Used by the coder to verify its implementation and in CI fix prompts.

**`install_command`** — Dependency install command (e.g. `"pip install -r requirements.txt"`, `"npm install"`). Used in coder prompts to tell it *not* to run this command — dependencies are assumed pre-installed in the sandbox.

**`module_system_hint`** — Short description of how imports work in your codebase (e.g. `"Python imports — use standard import statements"`, `"ES Modules — use import/export, include .js extension on local paths"`). This is injected verbatim into the coder prompt as an instruction.

---

## 5. How to override an agent task

Sometimes the default prompt for a task doesn't fit your repo well. For example, the default `coder/implement.md` might use language that doesn't match your project's idioms, or you want to add domain-specific instructions the coder must follow.

To override a task prompt:

1. Create the file `prompts/<agent-name>/<task-name>.md` at the root of your target repo.
2. Write your replacement prompt body. Do **not** include the output-contract block (see [section 7](#7-the-output-contract-rule)).
3. Commit and push. The orchestrator will use your file instead of the default when it next runs.

### Example

Default prompt at `defaults/prompts/coder/implement.md` (abbreviated):

```markdown
You are a coding agent.

Implement the code required to resolve this GitHub issue and make the tests pass.

Issue #{{issue_number}}:
{{issue_description}}
...

<!-- ORCHESTRATOR OUTPUT CONTRACT — DO NOT MODIFY -->

After committing, output exactly two lines in this format:
  COMMIT: <the commit message you used>
  SUMMARY: <2-3 sentences describing what files were changed and why>
```

Your override at `prompts/coder/implement.md` in your target repo:

```markdown
You are a coding agent working on a FastAPI service.

Implement the code to resolve issue #{{issue_number}}.

{{issue_description}}

Test file to pass: {{test_file_path}}
{{test_code}}

Instructions:
- Follow the existing patterns in `app/` strictly.
- All new endpoints must use the `APIRouter` pattern already established.
- Use `app/dependencies.py` for shared dependency injection.
- Type-annotate all function signatures.
- NEVER run {{install_command}}.
- After implementing, run {{test_command}} and commit with a `feat:` prefix.
```

Notice:
- The output-contract block is absent — it will be appended automatically from the orchestrator default.
- `{{variable_name}}` placeholders still work — both convention variables and runtime context variables are available.
- The override is **wholesale**: your file fully replaces the default's pre-contract body.

### Partial override pattern

If you only want to add instructions on top of the default, the simplest approach is to copy the default's pre-contract body from `defaults/prompts/<agent>/<task>.md` and append your additions:

```markdown
<!-- copied default body here, then additions below -->

Additional instructions for this repo:
- Always run `black .` before committing.
- Never modify files in `migrations/` — those are managed by the release process.
```

---

## 6. How `.agents.md` works

`.agents.md` is a Markdown file at your target repo's root that injects contextual text into agent prompts. It lets you give every agent (or specific agents) background knowledge about your codebase without duplicating it across every prompt file.

### Section format

The file uses `## @<name>` headers to scope content:

```markdown
## @shared

Content here is injected into every agent's prompt.

## @coder

Content here is injected only into coder prompts.

## @reviewer

Content here is injected only into reviewer prompts.
```

Any section header not matching the current agent name is ignored. The `@shared` section is always included.

### Composition order

When the orchestrator builds a prompt, `.agents.md` content is injected in this order:

1. Orchestrator's `defaults/.agents.md` — the `@shared` and `@<agent>` sections (if the file has any).
2. Your target repo's `.agents.md` — the `@shared` and `@<agent>` sections.

Both files are read. If the orchestrator default has shared context and your target repo also has shared context, both appear in the prompt (orchestrator's first, yours second). You don't override the orchestrator `.agents.md` — you append to it.

### Example `.agents.md`

```markdown
## @shared

This is a Python/Django REST API. Key facts:
- Models live in `app/models/` — one file per domain entity.
- Serializers live in `app/serializers/`.
- Views live in `app/views/` and use class-based views throughout.
- Never modify `app/migrations/` — always use `makemigrations`.
- The test database is SQLite in-memory; don't write tests that assume PostgreSQL-specific features.

## @coder

- Add type annotations to all new functions.
- Follow the existing `app/views/` pattern when adding new endpoints.
- Never use raw SQL — always use the ORM.

## @reviewer

Watch for these project-specific issues:
- Missing `@login_required` on views that touch user data.
- N+1 queries in serializers — use `select_related` / `prefetch_related`.
- Mutations inside `GET` handlers.

## @tester

- Test files go in `tests/` mirroring the app structure (e.g. `tests/views/test_users.py`).
- Use `pytest-django` fixtures (`db`, `client`, `rf`) — no manual Django test setup.
- Factory classes live in `tests/factories.py` — use them instead of `Model.objects.create()`.
```

### Supported agent names in section headers

You can use the exact agent names from the agent registry:

- `@shared`
- `@coder`
- `@tester`
- `@reviewer`
- `@docs-writer`
- `@issue-decomposer`
- `@product-planner`
- `@refactor`
- `@triager`
- `@security-auditor`
- `@release`

Any other section header (e.g. `## @myagent`) is silently ignored unless a custom agent with that name has been registered.

---

## 7. The output-contract rule

Every default task file contains an output-contract block — a structured format declaration that tells the agent exactly what to output at the end of its response. The orchestrator splits on this marker and uses the post-marker content to parse the agent's structured output.

The marker is:

```
<!-- ORCHESTRATOR OUTPUT CONTRACT — DO NOT MODIFY -->
```

**This marker uses an em-dash (—), not a hyphen (-).** The split is exact.

### What target overrides may change

When you override a task prompt, you control only the **pre-marker body** — the instructions you give the agent. You cannot change what the agent is asked to output. The orchestrator always appends the output-contract block from the default task file.

```
Your override file        Orchestrator default file
┌───────────────┐         ┌───────────────────────────┐
│ task body     │    +    │ <!-- OUTPUT CONTRACT... --> │
│ (your text)   │         │ output format instructions  │
└───────────────┘         └───────────────────────────┘
```

If you include the marker in your override file, the loader strips everything from your file at and after the marker before composing the prompt — only your pre-marker body is used.

### Why this is non-negotiable

The post-contract section defines what each agent outputs (`COMMIT: ...`, `TEST_FILE: ...`, JSON). The orchestrator's `parse_response` methods on each agent class parse these outputs. If the output format changes, parsing breaks. The lock on the contract ensures a target repo can never accidentally break the orchestrator's ability to read agent responses.

---

## 8. Variable interpolation syntax

Prompts use `{{variable_name}}` (double braces) for substitution. A simple regex-based substitution runs over the composed prompt before it is sent to the agent.

### Syntax rules

- Double braces: `{{variable_name}}` — not single `{variable}` (single braces appear in JSON examples and code snippets in prompts).
- Variable names are word characters only (`[a-zA-Z0-9_]`).
- Undefined variables raise `ValueError` immediately rather than silently emitting `{{foo}}` in the prompt. This is intentional — a missing variable means your config is incomplete.

### Available variables

All variables from your `[conventions]` block are available in every prompt. Runtime context variables are also available and are documented in the README under "Template variables".

### Example

In `prompts/coder/implement.md`:

```markdown
Implement the solution for issue #{{issue_number}}.

The primary language is {{primary_language}}.
Run tests with: {{test_command}}
NEVER run: {{install_command}}
```

At runtime, `{{issue_number}}` is populated by the orchestrator, and `{{primary_language}}`, `{{test_command}}`, and `{{install_command}}` come from your `[conventions]` block.

### Order of variable resolution

Variables are resolved from a single merged dict: convention variables first, then runtime context. Runtime context wins if both define the same key (this is unusual in practice — convention and runtime variables occupy different namespaces by convention).

### Security note on runtime context

Runtime context variables — including `{{issue_body}}` and `{{issue_description}}` — are populated **after** interpolation, not before. This means an issue body containing `{{some_variable}}` is appended as literal text; it is never scanned for placeholders. This prevents a class of prompt-injection attacks where a user-supplied value could cause unexpected variable substitution.

---

## 9. Pointing the orchestrator at your repo

The orchestrator discovers the target repo through the `TARGET_REPO_PATH` environment variable.

### Setting TARGET_REPO_PATH

```bash
export TARGET_REPO_PATH=/path/to/your/repo
python main.py <issue_number>
```

Or inline:

```bash
TARGET_REPO_PATH=/path/to/your/repo python main.py 42
```

The path must be an absolute path to the root of the target repo. The orchestrator looks for `agents.toml`, `prompts/`, and `.agents.md` directly at this path.

### Default value

If `TARGET_REPO_PATH` is not set, the orchestrator defaults to `working-repo/` relative to the orchestrator's own root. This is the path used when running in CI against the primary target repo.

### Practical setup for a new repo

1. Clone the orchestrator repo.
2. Clone your target repo somewhere accessible.
3. Set `TARGET_REPO_PATH` to the absolute path of your target repo root.
4. Set the other required environment variables (`GITHUB_TOKEN`, `ANTHROPIC_API_KEY`, etc. — see `SETUP.md`).
5. Run `python main.py <issue_number>`.

For long-lived setups (e.g. a GitHub Actions workflow or a systemd service), put `TARGET_REPO_PATH` in a `.env` file or the environment of the process that runs the orchestrator.

### One orchestrator, multiple target repos

You can run separate orchestrator processes pointing at different repos simultaneously — each needs its own `TARGET_REPO_PATH`. They share the same orchestrator codebase but are entirely isolated at the config level: each target repo has its own `agents.toml`, `prompts/`, and `.agents.md`.

---

## 10. Minimum viable config

This is the smallest `agents.toml` that makes the core workflow (issue-decomposer → tester → coder → reviewer) functional for a Python repo. Copy it to the root of your target repo and fill in the values:

```toml
# agents.toml — minimum viable config for a Python target repo

[conventions]
primary_language     = "Python"
codebase_layout_hint = "src/ for source code, tests/ for tests"
test_file_pattern    = "tests/**/*.py"
test_file_extension  = "py"
test_runner          = "pytest"
test_command         = "pytest"
install_command      = "pip install -r requirements.txt"
module_system_hint   = "Python imports — use standard import statements"
```

For a TypeScript/Node.js repo:

```toml
[conventions]
primary_language     = "TypeScript"
codebase_layout_hint = "src/ for source code, src/__tests__/ for tests"
test_file_pattern    = "src/__tests__/**/*.test.ts"
test_file_extension  = "test.ts"
test_runner          = "jest"
test_command         = "npm test"
install_command      = "npm install"
module_system_hint   = "TypeScript ES Modules — use named imports, include file extensions on local imports"
```

For a Go repo:

```toml
[conventions]
primary_language     = "Go"
codebase_layout_hint = "package directories at the repo root, _test.go files alongside source"
test_file_pattern    = "**/*_test.go"
test_file_extension  = "_test.go"
test_runner          = "go test"
test_command         = "go test ./..."
install_command      = "go mod download"
module_system_hint   = "Go modules — import by full module path, not relative"
```

After adding `agents.toml`, you can add `.agents.md` to give agents background on your codebase. The `## @shared` section is the single most valuable addition — it prevents agents from making assumptions about your project structure:

```markdown
## @shared

<Your project name> is a <brief description>.

- Source files live in `<source_dir>/`.
- Tests live in `<test_dir>/`.
- Never modify `<protected_dir>/` — <reason>.
- The project uses <framework> — follow existing patterns in `<example_dir>/`.
```

---

## 11. Worked example: the Spades target repo

The Spades Online target repo config lives at `examples/spades-target/` in this repository. These files are intended to be copied to the root of the actual Spades repo.

### `examples/spades-target/agents.toml`

```toml
# Spades Online — target-repo config for the general orchestrator.

[conventions]
primary_language     = "javascript"
codebase_layout_hint = "server/game/ for game logic, server/ for API and socket handlers, test/ for tests"
test_file_pattern    = "test/**/*.test.js"
test_file_extension  = "js"
test_runner          = "node:test (built-in — use: import { test, describe } from 'node:test'; import assert from 'node:assert/strict';)"
test_command         = "node --test"
install_command      = "npm install"
module_system_hint   = "ES Modules — use import/export syntax. Never use require(). All local import paths must include the .js extension (e.g. './game.js' not './game')."
```

#### Why these values?

- **`primary_language = "javascript"`** — Spades is a Node.js backend.
- **`codebase_layout_hint`** — tells the issue-decomposer where to look before estimating scope.
- **`test_runner`** — the full description is intentional: it tells the tester *exactly* what imports to use, avoiding the common mistake of importing jest or mocha when node:test is built-in.
- **`module_system_hint`** — the explicit `.js` extension requirement is an ES Modules gotcha. Embedding it here means every coder prompt reminds the coder, rather than hoping it remembers.
- **`install_command = "npm install"`** — prompts tell the coder *not* to run this. Dependencies are pre-installed.

### `examples/spades-target/.agents.md`

```markdown
## @shared

Spades Online is a card game backend built with Node.js and ES Modules.

- Game logic lives in `server/game/` — this is the core domain code.
- The server layer (routes, socket handlers, etc.) lives in `server/`.
- Never modify `.gitignore`.
- Never run `npm install` — dependencies are pre-installed in the repo environment.
- Use ES Modules (`import`/`export`) throughout. All local import paths require the
  `.js` extension (e.g. `'./game.js'`, not `'./game'`).
- Tests use the Node.js built-in `node:test` runner. Import with
  `import { test, describe } from 'node:test'` and
  `import assert from 'node:assert/strict'`.

## @reviewer

In addition to the standard review criteria, watch for these Spades-specific issues:

- **Missing `.js` extension on local imports** — Node.js ES Modules require explicit
  extensions; bare imports will throw at runtime.
- **`require()` calls** — this project is ES Modules only. Flag any CommonJS
  `require()` or `module.exports`.
- **Game-state mutation across sessions** — game state is per-room; mutations must
  not bleed across concurrent games.
- **Synchronous I/O or blocking operations** in async game handlers — these will
  stall the event loop and affect all connected players.
- **Unhandled promise rejections** in socket event handlers — always `await` or
  `.catch()`.

## @tester

- Test files go in `test/` following the `test/**/*.test.js` pattern.
- Use `node:test` and `node:assert/strict` — do not install or import jest, mocha,
  or other frameworks.
- Each test file must be independently runnable with `node --test <file>`.
- When testing game logic in `server/game/`, import the module directly and test
  pure functions in isolation where possible.
```

#### What this achieves

- **`@shared`** — every agent knows the project layout and the ES Modules requirement. Cuts down on coder mistakes and reviewer false-positives significantly.
- **`@reviewer`** — the reviewer knows what Spades-specific bugs to watch for. Without this, the generic reviewer would catch standard issues but miss the ES Modules pitfalls that are common in Node.js projects.
- **`@tester`** — stops the tester from reaching for jest or mocha (both are popular but neither is installed). The explicit `independently runnable` instruction prevents test files that only work as a suite.

### Running against Spades

```bash
export TARGET_REPO_PATH=/path/to/spades-repo
# Copy examples/spades-target/agents.toml → /path/to/spades-repo/agents.toml
# Copy examples/spades-target/.agents.md  → /path/to/spades-repo/.agents.md
python main.py <issue_number>
```

---

## 12. Troubleshooting

### `ValueError: Undefined variable {{foo}}`

A prompt references `{{foo}}` but your `[conventions]` block doesn't define it. Either:
- Add `foo = "..."` to your `[conventions]` block.
- Or you are using an agent task that requires a variable you haven't declared — check the [convention variables reference](#4-convention-variables-reference).

### `FileNotFoundError: Default task file missing`

The orchestrator couldn't find a default task file at `defaults/prompts/<agent>/<task>.md`. This means:
- The agent name or task name is misspelled.
- You are trying to use a task that doesn't exist in the orchestrator's default set.

To add a new task to an existing agent, create `defaults/prompts/<agent>/<task>.md` in the orchestrator repo and add the task name to the agent's `tasks` list in `defaults/agents.toml`.

### `ValueError: Malformed TOML`

Your `agents.toml` has a syntax error. Common causes:
- Unquoted strings that contain special characters (colons, brackets).
- Multi-line strings that aren't wrapped in triple quotes (`"""`).

Run `python -c "import tomllib; tomllib.load(open('agents.toml', 'rb'))"` in your target repo root to check for TOML errors before running the orchestrator.

### Agent is ignoring my `.agents.md` additions

- Check that your section header matches exactly: `## @coder`, not `## @Coder` or `## coder`.
- Check the agent name in the section header against the agent names in [section 3](#3-the-agentstoml-schema) — names are lowercase and hyphenated (e.g. `docs-writer`, `issue-decomposer`).
- Make sure the file is at the root of the target repo (the directory pointed to by `TARGET_REPO_PATH`).

### My task override isn't being used

- Verify the file is at `prompts/<agent>/<task>.md` (plural `prompts`, not `prompt`).
- Verify the agent name and task name match exactly (case-sensitive, hyphens not underscores).
- The override path must be relative to your target repo root (`TARGET_REPO_PATH/prompts/<agent>/<task>.md`).

### Output contract errors at runtime

If an agent's `parse_response` raises `OutputContractError`, the agent returned output that doesn't match the expected format. This usually means:
- Your task override removed or changed the output-contract instructions. Check that your override file doesn't include the `<!-- ORCHESTRATOR OUTPUT CONTRACT — DO NOT MODIFY -->` marker followed by different format instructions.
- The model returned an unexpected format. The orchestrator treats this as a transient failure and retries automatically.
