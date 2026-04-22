# Orchestrator Refactor — GitHub Issue Task List

Each task below is sized for a single `@claude` run (should complete well under the 15-minute subprocess timeout). Tasks are grouped by PRD phase and ordered so each builds on the previous. Merge main into the long-lived feature branch at the end of each phase.

Copy each `### Task N.M` block into a new GitHub Issue.

---

## Phase 1 — Foundation

### Task 1.1: Scaffold `defaults/` directory structure

**Goal:** Create the empty scaffolding for orchestrator-shipped defaults. No logic yet.

**Steps:**
- Create `defaults/agents.toml` with a minimal placeholder (`[conventions]` section with a comment explaining target repos override this).
- Create empty directories: `defaults/prompts/coder/`, `defaults/prompts/tester/`, `defaults/prompts/reviewer/`, `defaults/prompts/docs-writer/`, `defaults/prompts/issue-decomposer/`, `defaults/prompts/product-planner/`, `defaults/prompts/triager/`, `defaults/prompts/refactor/`, `defaults/prompts/security-auditor/`, `defaults/prompts/release/`. Drop a `.gitkeep` in each.
- Create an empty `defaults/.agents.md` with a `## @shared` header and a comment placeholder.

**Acceptance:**
- `ls defaults/` shows the tree above.
- No root-level `agents.toml`, `prompts/`, or `.agents.md` exists in the orchestrator repo (PRD S5).

---

### Task 1.2: Add `OutputContractError` and `BaseAgent` class

**Goal:** Introduce the new exception type and a `BaseAgent` class so Phase 2+ migrations can use both immediately.

**Context:** Today `agents/base_agent.py` holds shared infrastructure (`run_claude`, `RateLimitError`, `PromptTooLongError`, `TransientError`, `REPO_DIR`) but no `BaseAgent` *class* — every agent class is bare (`class CoderAgent:`, not `class CoderAgent(BaseAgent):`). PRD §4.6 implies a base class exists ("The default implementation in the base class returns `{"response": text}`"), so introduce it here before any migration needs it.

**Steps:**
- In `agents/base_agent.py`, add `OutputContractError(Exception)` with fields `agent`, `task`, `expected`, `got_preview` (per PRD §4.6). `__init__` should format a clear error message combining these fields. Export it alongside existing exceptions.
- Add a `BaseAgent` class in the same file with:
  - A default `parse_response(self, text: str) -> dict` returning `{"response": text}`.
  - No `__init__` requirements — subclasses should be constructable with no args, matching current usage in `orchestrator/graph.py`.
  - No `run` method stub (keep it abstract-by-convention; subclasses already implement `async def run`).
- Do **not** change existing agent classes to inherit from `BaseAgent` in this task — that happens agent-by-agent during their migration tasks (2.2, 3.1, 3.2, 3.3, 4.5). This keeps Phase 1 non-breaking.

**Acceptance:**
- `from agents.base_agent import OutputContractError, BaseAgent` works.
- Instantiating `OutputContractError(agent="tester", task="write-tests", expected="TEST_FILE: line", got_preview="...")` produces a human-readable `str()`.
- `BaseAgent().parse_response("hello")` returns `{"response": "hello"}`.
- No behavioral change to existing agents — they still don't inherit from `BaseAgent` and continue to work unchanged.

---

### Task 1.3: Wire `OutputContractError` into orchestrator retry loop

**Goal:** Treat `OutputContractError` identically to `TransientError` in the main retry loop.

**Steps:**
- In `orchestrator/orchestrator.py`, import `OutputContractError`.
- Add `except OutputContractError as e:` clause immediately above (or combined with) the existing `TransientError` handler. PRD says "handled identically to `TransientError`" — either a combined `except (TransientError, OutputContractError)` or a duplicate block that delegates is fine.
- Log clearly which exception type fired so the operator can distinguish a parse failure from a subprocess timeout.

**Acceptance:**
- Raising `OutputContractError` from an agent triggers the same retry/backoff/escalation path as `TransientError`.
- Logs clearly mark the error type.

---

### Task 1.4: Implement `orchestrator/loader.py` — registry loading

**Goal:** Load `agents.toml` from orchestrator defaults and target repo, with target overrides winning.

**Steps:**
- Create `orchestrator/loader.py`.
- Function `load_registry(orchestrator_root: Path, target_root: Path) -> dict` that:
  - Parses `{orchestrator_root}/defaults/agents.toml` with `tomllib` (Python 3.11+).
  - If `{target_root}/agents.toml` exists, parses it and merges wholesale per top-level key (`[agents.coder]` in target fully replaces the default, per PRD §4.3).
  - Returns a dict with `agents` (dict of agent configs) and `conventions` (dict of target convention vars).
- Raise a clear error if TOML is malformed, naming the file and line if possible.
- Raise a clear error if a referenced `prompt_dir` is missing when the agent is loaded.
- No prompt composition yet — that's Task 1.5.

**Acceptance:**
- Given a minimal `defaults/agents.toml` and no target overrides, `load_registry` returns the parsed defaults.
- Given a target `agents.toml` that overrides `[agents.coder]`, the returned `agents["coder"]` reflects the target version.
- Malformed TOML produces an error naming the file.

---

### Task 1.5: Implement `orchestrator/loader.py` — prompt composition

**Goal:** Compose the final prompt for a given `(agent, task)` per PRD §4.3.

**Steps:**
- Add `compose_prompt(agent: str, task: str, context: dict, registry: dict, orchestrator_root: Path, target_root: Path) -> str`.
- Compose in the order from PRD §4.3:
  1. Agent identity (from registry; target wins).
  2. Cascade from orchestrator-default `.agents.md` then target `.agents.md`, pulling `## @shared` and `## @<agent>` sections.
  3. Task body from `prompts/<agent>/<task>.md` (target wins wholesale if present; else default).
  4. Output contract: *always* from the orchestrator default task file. Split on `<!-- ORCHESTRATOR OUTPUT CONTRACT -->` marker; append the post-marker block unconditionally.
  5. Runtime context section (stringified dict or labeled lines — keep simple).
- Raise a clear error if the default task file is missing (needed for the output contract).

**Acceptance:**
- Unit-testable: given fake dirs with known files, `compose_prompt("tester", "write-tests", {...}, ...)` returns a string containing identity + cascade + task body + output contract, in that order.
- If the target task file omits the output-contract marker or tries to include one, the orchestrator's post-marker block is still appended (PRD H2).

---

### Task 1.6: Implement `orchestrator/loader.py` — variable interpolation

**Goal:** Substitute `{{variable}}` placeholders with values from context + conventions.

**Steps:**
- Add `interpolate(text: str, variables: dict) -> str` using a simple regex (`re.sub(r"\{\{(\w+)\}\}", ...)`) per PRD §4.5.
- Undefined variable → raise `ValueError(f"Undefined variable {{{{{name}}}}} in prompt")` with the prompt filename if available (pass it as optional arg).
- Conventions from `registry["conventions"]` should be merged into the variable namespace automatically. Explicit per-call context overrides conventions.
- Call `interpolate` at the end of `compose_prompt`.

**Acceptance:**
- `interpolate("run {{test_command}}", {"test_command": "pytest"})` → `"run pytest"`.
- `interpolate("hi {{missing}}", {})` raises `ValueError` naming `missing`.
- Literal single braces (as in JSON examples) pass through unchanged.

---

### Task 1.7: Loader unit tests

**Goal:** Freeze loader behavior before migrations start depending on it.

**Steps:**
- Create `tests/test_loader.py` (add `pytest` to dev deps if not present).
- Cover:
  - Registry loading with and without target override.
  - Prompt composition order (identity, cascade, task body, output contract).
  - Output contract is never overrideable (target's post-marker content is discarded).
  - Variable interpolation: success, missing var error, conventions merged.
  - Malformed TOML and missing default-task-file produce clear errors.
- Use `tmp_path` fixtures rather than real repo files.

**Acceptance:**
- `pytest tests/test_loader.py` passes.
- Each of the six cases above has a named test.

---

## Phase 2 — Migrate tester

### Task 2.1: Extract tester prompt to `defaults/prompts/tester/write-tests.md`

**Goal:** Move `PROMPT_TEMPLATE` out of `agents/test_agent.py` into a file, faithfully (PRD §3: "as-faithfully-as-possible").

**Steps:**
- Create `defaults/prompts/tester/write-tests.md` with the exact content of `PROMPT_TEMPLATE`, but:
  - Replace `{issue_number}` → `{{issue_number}}` (double-brace).
  - Replace `{issue_description}` → `{{issue_description}}`.
  - Replace the Spades-specific opener, `test/unit/` references, `node:test` note, and `.test.js` suffix with placeholders — this language moves to the Spades target repo in Phase 6. For now, use generic language like "Write comprehensive tests for this GitHub issue, following TDD principles." and reference `{{test_file_pattern}}`, `{{test_runner}}`, `{{test_file_extension}}` (these become conventions).
  - Keep the `TEST_FILE: <path>` output instruction.
- Add the output-contract marker: everything from "After writing" onward (the `TEST_FILE:` line) goes *below* `<!-- ORCHESTRATOR OUTPUT CONTRACT — DO NOT MODIFY -->`.
- Register `tester` in `defaults/agents.toml` with `tasks = ["write-tests"]`, `model = "claude-sonnet-4-6"`, `tools = ["Read", "Edit", "Bash"]`, and a short identity string.

**Acceptance:**
- File exists at the path above.
- `grep "Spades\|node:test\|\.test\.js" defaults/prompts/tester/write-tests.md` returns nothing (S2).
- Output-contract marker present.

---

### Task 2.2: Refactor `TestAgent.run` to use `compose_prompt` + `parse_response`

**Goal:** Switch tester to the new architecture. First agent to inherit from `BaseAgent`.

**Steps:**
- In `agents/test_agent.py`, import `BaseAgent` and `OutputContractError` from `agents.base_agent`.
- Make `TestAgent` inherit from `BaseAgent`: `class TestAgent(BaseAgent):`.
- Remove `PROMPT_TEMPLATE` from `agents/test_agent.py`.
- `TestAgent.run` calls `compose_prompt("tester", "write-tests", context, ...)` instead of inline formatting.
- Override `parse_response(self, text: str) -> dict` to extract `TEST_FILE:` line. On missing/malformed line, raise `OutputContractError(agent="tester", task="write-tests", expected="TEST_FILE: <path>", got_preview=text[:200])` — no more silent fallback to `test/unit/issue-{n}.test.js`.

**Acceptance:**
- `grep "PROMPT_TEMPLATE" agents/test_agent.py` returns nothing.
- `TestAgent` inherits from `BaseAgent`.
- Malformed tester output raises `OutputContractError` and the orchestrator retries (confirmed by running against a Spades issue and artificially breaking the response once).

---

### Task 2.3: End-to-end Spades validation of tester migration

**Goal:** Prove Phase 2 hasn't broken anything.

**Steps:**
- Run the orchestrator against one real Spades issue that hits the tester path.
- Confirm: test file is written, `TEST_FILE:` parsed, coder receives test code, PR is created.
- Fix any bugs surfaced.
- Merge `main` into the feature branch (PRD §5.3).

**Acceptance:**
- One Spades issue runs from decompose → test → implement → review → merge with no regressions vs. pre-refactor.

---

## Phase 3 — Migrate documentation, issue-decomposer, reviewer

### Task 3.1: Migrate `documentation_agent` → `docs-writer`

**Goal:** Same pattern as tester.

**Steps:**
- Extract `PROMPT_TEMPLATE` from `agents/documentation_agent.py` to `defaults/prompts/docs-writer/document.md`.
- Remove Spades-specific doc file references (`docs/api.md`, `docs/websocket.md`) — replace with generic language or pull from conventions (`{{doc_paths}}` as a list, or make the prompt target-agnostic and let the target repo override the whole task file).
- Convert `{code_changes}` → `{{code_changes}}`.
- Put the `NO_CHANGES` / `UPDATED:` output section below the output-contract marker.
- Register `docs-writer` in `agents.toml` with `tasks = ["document"]`. (The `fix-review` task is listed in PRD §4.7 but not implemented today; skip it for this task — it's part of v1.1.)
- Add `parse_response` extracting `UPDATED:` lines and `NO_CHANGES` sentinel. Raise `OutputContractError` if neither is present.
- Remove the literal `PROMPT_TEMPLATE` from `documentation_agent.py`; `run` uses `compose_prompt`.
- Make `DocumentationAgent` inherit from `BaseAgent`.

**Acceptance:**
- `grep "PROMPT_TEMPLATE\|api\.md\|websocket\.md" agents/documentation_agent.py` returns nothing.
- End-to-end Spades run still updates docs correctly.

---

### Task 3.2: Rename `planner` → `issue-decomposer`, migrate prompt

**Goal:** Rename per PRD §4.7 and migrate to new architecture.

**Steps:**
- Rename `agents/planner_agent.py` → `agents/issue_decomposer_agent.py`; class `PlannerAgent` → `IssueDecomposerAgent`.
- Update all imports (`orchestrator/graph.py` and anywhere else).
- Extract `PLANNER_PROMPT` to `defaults/prompts/issue-decomposer/decompose.md`.
- De-Spades-ify: remove `server/routes/`, `server/game/`, `server/middleware/` path references — replace with generic guidance referencing `{{primary_language}}` and a `{{codebase_layout_hint}}` variable that targets can supply.
- Keep the JSON output contract below the marker.
- `parse_response` wraps the existing `_extract_json` logic; raise `OutputContractError` if all three fallback parses fail (instead of silently returning `medium`). The current silent fallback is explicitly called out in PRD §4.6 ("No silent fallback to defaults").
- Register in `agents.toml` as `issue-decomposer` with `tasks = ["decompose"]`.
- Make `IssueDecomposerAgent` inherit from `BaseAgent`.

**Acceptance:**
- `grep "PlannerAgent\|planner_agent" .` shows only historical references (e.g., checkpoint DB — which is dropped at cutover).
- `grep "server/routes\|server/game" defaults/` returns nothing.
- Decomposer still runs end-to-end on Spades.

---

### Task 3.3: Migrate reviewer

**Goal:** Same pattern; validates `parse_response` handles structured JSON per PRD §5.2.

**Steps:**
- Extract `REVIEWER_PROMPT` from `agents/reviewer_agent.py` to `defaults/prompts/reviewer/review.md`.
- De-Spades-ify: remove Spades-specific security examples ("card data exposed to wrong client", "illegal moves not rejected", "bid logic wrong"). Replace CRITICAL/HIGH/MEDIUM/LOW rubric with language-agnostic examples; Spades-specific examples move to the target repo's override.
- Remove the `.test.js`/`.spec.js` specific test-file exclusion — use `{{test_file_pattern}}` or generalize.
- Put the JSON output contract below the marker.
- `parse_response` extracts the JSON object. Raise `OutputContractError` on parse failure.
- Make `ReviewerAgent` inherit from `BaseAgent`.
- Register in `agents.toml`.

**Acceptance:**
- `grep "Spades\|card\|bid\|\.test\.js" defaults/prompts/reviewer/` returns nothing.
- Reviewer still produces findings on a Spades PR.
- Merge `main` into the feature branch at phase end.

---

## Phase 4 — Migrate coder

### Task 4.1: Extract coder `implement` prompt

**Goal:** Move `PROMPT_TEMPLATE` (the implement flow) to its own file.

**Steps:**
- Extract `PROMPT_TEMPLATE` (lines ~55–88 of `coder_agent.py`) to `defaults/prompts/coder/implement.md`.
- Convert all `{var}` → `{{var}}` (`issue_number`, `issue_description`, `test_file_path`, `test_code`, `feedback_section`).
- De-Spades-ify: replace "Spades Online card game backend", "ES Modules (import/export, not require)", "Keep game logic in server/game/", "NEVER run npm install", "node_modules" — these move to the Spades target repo override. Use `{{test_command}}`, `{{install_command}}`, `{{module_system_hint}}`, and a generic "Keep code organized per existing conventions" line.
- Output contract below the marker: the `COMMIT: <msg>` line.
- Do *not* edit the agent class yet (Task 4.5 does the wiring).

**Acceptance:**
- File exists.
- `grep "Spades\|ES Modules\|npm\|node_modules\|server/game" defaults/prompts/coder/implement.md` returns nothing.

---

### Task 4.2: Extract coder `fix-review` prompt

**Goal:** Move `RETRY_PROMPT_TEMPLATE` (reviewer feedback flow).

**Steps:**
- Extract `RETRY_PROMPT_TEMPLATE` (line ~90) to `defaults/prompts/coder/fix-review.md`.
- Convert placeholders; de-Spades-ify same as 4.1.
- Output contract (COMMIT line) below the marker.

**Acceptance:**
- File exists, no Spades-isms.

---

### Task 4.3: Extract coder `fix-ci` prompt

**Goal:** Move `CI_FIX_PROMPT_TEMPLATE`.

**Steps:**
- Extract `CI_FIX_PROMPT_TEMPLATE` (line ~119) to `defaults/prompts/coder/fix-ci.md`.
- Convert placeholders including `{{ci_errors}}` and `{{test_command}}`.
- Output contract below the marker.

**Acceptance:**
- File exists, no Spades-isms.

---

### Task 4.4: Extract coder `fix-inline` prompt

**Goal:** Move `INLINE_FIX_PROMPT_TEMPLATE`.

**Steps:**
- Extract `INLINE_FIX_PROMPT_TEMPLATE` (line ~149) to `defaults/prompts/coder/fix-inline.md`.
- Convert placeholders.
- Output contract below the marker.

**Acceptance:**
- File exists, no Spades-isms.

---

### Task 4.5: Refactor `CoderAgent` to task dispatch + `parse_response`

**Goal:** Wire the four extracted prompts into the agent class.

**Steps:**
- Remove all four `*_PROMPT_TEMPLATE` constants from `agents/coder_agent.py`.
- Register `coder` in `agents.toml` with `tasks = ["implement", "fix-review", "fix-ci", "fix-inline"]`, `model = "claude-opus-4-7"`, `tools = ["Read", "Edit", "Bash"]`, and the identity from PRD §4.2.
- `CoderAgent.run(context)` inspects `context["mode"]` (or an equivalent existing signal) and selects the task name accordingly: default → `implement`, with review feedback → `fix-review`, with CI failure → `fix-ci`, with inline review comment → `fix-inline`. Map from whatever fields already gate the current `if/elif` around lines 215/226/233/455.
- `parse_response` extracts the `COMMIT:` line. Raise `OutputContractError` on missing/malformed.
- Make `CoderAgent` inherit from `BaseAgent`.

**Acceptance:**
- `grep "PROMPT_TEMPLATE\|RETRY_PROMPT_TEMPLATE\|CI_FIX_PROMPT_TEMPLATE\|INLINE_FIX_PROMPT_TEMPLATE" agents/coder_agent.py` returns nothing.
- End-to-end Spades run succeeds through implement + at least one fix-review cycle.
- Merge `main` into the feature branch at phase end.

---

## Phase 5 — New agents

Each new agent gets its own small task. Per PRD §3 and F3, prompts need only be sufficient to run end-to-end once; polish is follow-up work.

### Task 5.1: Add `product-planner` agent

**Goal:** New agent with tasks `draft-prd`, `propose-issues` (PRD §4.7).

**Steps:**
- Create `agents/product_planner_agent.py` with class `ProductPlannerAgent`. Follow the tester pattern: `run(context)` calls `compose_prompt` and `parse_response`.
- Decide whether this agent needs a custom class at all, or can be handled by a generic `ConfigDrivenAgent(BaseAgent)` that reads `agents.toml`. If the generic class works for a no-structured-output agent, use it (PRD Appendix notes new agents "may share a base that reads config-driven agents"). Recommend implementing the generic base now so subsequent 5.x tasks reuse it. `ConfigDrivenAgent` should inherit from `BaseAgent` so it gets the default `parse_response` for free.
- Create `defaults/prompts/product-planner/draft-prd.md` — rough prompt asking Claude to produce a PRD from a brief. Output contract: the PRD itself, no parsing needed (base `parse_response` returns `{"response": text}`).
- Create `defaults/prompts/product-planner/propose-issues.md` — rough prompt asking for a list of proposed GitHub issues from a PRD. Output contract: a JSON array; `parse_response` extracts it.
- Register in `agents.toml`.
- Write a tiny script `scripts/run_agent.py` (or extend `main.py` with a CLI flag) that invokes one agent + task with a prompt from stdin or a file, so F3 ("runs at least one task successfully end-to-end") is testable for agents that aren't wired into the graph.

**Acceptance:**
- `python scripts/run_agent.py product-planner draft-prd --input some-brief.txt` produces a PRD.
- `product-planner propose-issues` produces a parseable JSON array.

---

### Task 5.2: Add `triager` agent

**Goal:** Reads failing CI / bug reports and routes (PRD §4.7).

**Steps:**
- Create `defaults/prompts/triager/triage.md`. Rough prompt: given a failing CI log or bug report, classify and suggest next agent (`coder`, `tester`, or `refactor`) with reasoning. JSON output.
- Reuse `ConfigDrivenAgent` from 5.1.
- Register in `agents.toml`.
- `parse_response` extracts JSON `{classification, suggested_agent, reasoning}`.
- Not wired into the graph (PRD §3 explicitly says new agents are isolated).

**Acceptance:**
- `python scripts/run_agent.py triager triage --input some-ci-log.txt` produces parseable JSON.

---

### Task 5.3: Add `refactor` agent

**Goal:** Structural changes agent with tasks `refactor`, `fix-review`.

**Steps:**
- Create `defaults/prompts/refactor/refactor.md` — rough prompt asking Claude to perform a requested structural change (rename, extract function, split module) without changing behavior. Output contract: `COMMIT:` line (same pattern as coder).
- Create `defaults/prompts/refactor/fix-review.md` — rough prompt for addressing review feedback on a prior refactor.
- Reuse `ConfigDrivenAgent`. Override `parse_response` to extract `COMMIT:`, raising `OutputContractError` on failure.
- Register in `agents.toml`.

**Acceptance:**
- `python scripts/run_agent.py refactor refactor --input some-refactor-request.txt` produces a commit on a scratch branch.

---

### Task 5.4: Add `security-auditor` agent

**Goal:** Scans diffs for secrets and unsafe patterns (PRD §4.7).

**Steps:**
- Create `defaults/prompts/security-auditor/audit.md` — rough prompt: given a diff, report findings as JSON (same schema as reviewer findings, but scoped to security: leaked secrets, SQL injection, command injection, etc.).
- Reuse `ConfigDrivenAgent`.
- `parse_response` extracts JSON.
- Register in `agents.toml`.

**Acceptance:**
- `python scripts/run_agent.py security-auditor audit --input some-diff.txt` returns parseable findings JSON.

---

### Task 5.5: Add `release` agent

**Goal:** Post-merge summarization with tasks `draft-changelog`, `bump-version`.

**Steps:**
- Create `defaults/prompts/release/draft-changelog.md` — rough prompt: given a list of merged PRs since the last tag, draft a changelog entry. Plain-text output.
- Create `defaults/prompts/release/bump-version.md` — rough prompt: given current version and the changelog, suggest next semver bump. Output contract: a single line `VERSION: x.y.z`; `parse_response` extracts it.
- Reuse `ConfigDrivenAgent`.
- Register in `agents.toml`.

**Acceptance:**
- Both tasks execute end-to-end with toy input.

---

### Task 5.6: End-to-end smoke tests for all new agents

**Goal:** Satisfy PRD F3 (each new agent runs at least one task end-to-end).

**Steps:**
- For each of `product-planner`, `triager`, `refactor`, `security-auditor`, `release`: run at least one task against a small input file. Record the invocation and outcome in a new `docs/new-agent-smoke-tests.md`.
- Fix any loader/parser bugs surfaced.
- Merge `main` into the feature branch at phase end.

**Acceptance:**
- `docs/new-agent-smoke-tests.md` shows 5 green runs.

---

## Phase 6 — Spades target-repo extraction

### Task 6.1: Create Spades target-repo config files

**Goal:** Move all Spades-specific language out of `defaults/` into a Spades target config.

**Steps:**
- In the **Spades repo** (not this one), create `agents.toml` with:
  - A `[conventions]` block: `primary_language = "javascript"`, `test_command = "node --test"`, `install_command` set appropriately, `test_file_pattern = "test/**/*.test.js"`, etc.
  - Any per-agent overrides needed (likely none required for v1 if the defaults are well-generalized).
- Create `prompts/` directory in Spades with any task overrides needed. Most should not be needed if Phase 2–4 de-Spadesing was thorough.
- Create `.agents.md` in Spades with a `## @shared` section containing Spades domain context ("card game backend, ES Modules, game logic in server/game/, never modify .gitignore, never run npm install") and per-agent sections as needed (e.g., Spades-specific reviewer findings examples under `## @reviewer`).

**Acceptance:**
- `ls spades/` (or wherever Spades target is) shows `agents.toml`, `prompts/`, `.agents.md`.
- Orchestrator can be pointed at Spades via env var (or whatever mechanism PRD §4.1 lands on).

---

### Task 6.2: Orchestrator-side target-repo path configuration

**Goal:** Support pointing the orchestrator at an arbitrary target repo via env var, per PRD §4.1.

**Steps:**
- Add `TARGET_REPO_PATH` environment variable, read in `main.py` or wherever the loader is instantiated.
- Default to `REPO_DIR` (the existing `working-repo/`) for backward compatibility if unset.
- Pass this path into `load_registry` and `compose_prompt` as `target_root`.
- Document in `docs/target-repo-setup.md` (created in 6.4).

**Acceptance:**
- `TARGET_REPO_PATH=/path/to/spades python main.py` loads Spades config.
- Unset: behavior matches pre-refactor.

---

### Task 6.3: Final Spades-ism grep pass + cleanup

**Goal:** Enforce PRD S2.

**Steps:**
- Run `grep -rn 'Spades\|card game\|ES Modules\|npm\|node:test\|node_modules\|\.test\.js' defaults/ orchestrator/ agents/` — expect zero matches.
- Run `grep -rn 'PROMPT_TEMPLATE\|"""You are' orchestrator/ agents/` — expect zero matches (S1).
- Fix anything surfaced by moving to the Spades target repo or rephrasing generically.

**Acceptance:**
- Both greps return nothing.
- S1 + S2 + S5 from PRD §6 all hold.

---

### Task 6.4: Write `docs/target-repo-setup.md`

**Goal:** Satisfy PRD O2 — someone else can configure a new target repo.

**Steps:**
- Create `docs/target-repo-setup.md` covering:
  - Two-repo model (PRD §4.1).
  - File layout of a target repo.
  - `agents.toml` schema with every supported convention variable (`primary_language`, `test_command`, `install_command`, `lint_command`, `test_file_pattern`, any others introduced).
  - How to override an agent task (create `prompts/<agent>/<task>.md`).
  - How `.agents.md` sections work (`## @shared`, `## @<agent>`).
  - Output-contract rule (target overrides the pre-marker body only).
  - Variable interpolation syntax.
  - How to point the orchestrator at the new target (`TARGET_REPO_PATH`).
  - Minimum viable config for a new repo.
- Reference Spades target as a worked example.

**Acceptance:**
- Doc exists and is long enough to be useful (target: 300–600 lines with examples).

---

### Task 6.5: End-to-end Spades parity validation (PRD F1)

**Goal:** Prove the refactored orchestrator processes Spades issues with behavior equivalent to pre-refactor.

**Steps:**
- Point the orchestrator at the Spades target repo.
- Run one full issue from decompose → test → implement → review → fix-on-review → merge.
- Run one issue that hits the `fix-ci` path.
- Run one issue that hits the `fix-inline` path.
- Confirm no regressions.
- Fix anything surfaced.
- Merge `main` into the feature branch one more time.

**Acceptance:**
- All three flows succeed.
- Behavior matches pre-refactor.

---

### Task 6.6: Verify PRD acceptance criteria

**Goal:** Final checklist pass.

**Steps:**
- Run every grep in PRD §6 (S1, S2, S3, S5).
- For S3: add a trivial `summarize` task to some agent using only a new `.md` file + `agents.toml` entry; run it once; then delete. Confirm no Python change was needed.
- For H1: ship a deliberately broken task override in a scratch branch, confirm `OutputContractError` fires and retries.
- For H2: attempt to override the post-marker block in a target task file; confirm orchestrator's version still ships.
- For O1: introduce a typo in `agents.toml`, a missing prompt file, malformed TOML, and an undefined variable; confirm each produces a clear startup error.
- Record results in `docs/acceptance-criteria-checklist.md`.

**Acceptance:**
- All of F1, F2 (bring-up happens post-cutover but the infra is ready), F3, S1–S5, H1–H2, O1–O2 checked off.

---

## Cutover

### Task 7.1: Cutover checklist

**Goal:** Flip to the refactored orchestrator.

**Steps:**
- Drain in-flight Spades issues (PRD §8 risk mitigation).
- Drop `checkpoints.db`.
- Merge feature branch to main.
- Restart orchestrator against Spades.
- Monitor the first 2–3 issues for regressions.

**Acceptance:**
- No in-flight issues lost.
- Post-merge orchestrator processes new Spades issues cleanly.

---

### Task 7.2: Monday repo bring-up (PRD F2)

**Goal:** Pointed at the Monday repo, runs implement → test → review using only target-repo config.

**Steps:**
- In the Monday repo: create `agents.toml` with `[conventions]` for whatever language/toolchain it uses. Create `.agents.md` with domain context. Copy Spades target's structure as a template.
- Run the orchestrator against the Monday repo.
- If any generalization gap surfaces that requires orchestrator code changes, evaluate against the 2-hour escape hatch (PRD E1) — ≤2 hours is v1.1 scope, more is a v1 failure requiring rework.

**Acceptance:**
- At least one Monday issue runs implement → test → review.
- Zero orchestrator code changes required (or changes documented as v1.1 per E1).

---

## Notes for running `@claude` on these tasks

- **One task per issue, one issue per `@claude` invocation.** Each is scoped to complete inside the 15-minute `run_claude` timeout.
- **Attach PRD + the current file as context** in the issue body or as a linked document, so `@claude` has the full picture.
- **Merge main into the feature branch at the end of each phase** (PRD §5.3).
- **Don't skip the end-to-end validation tasks** (2.3, 5.6, 6.5) — they're the only way to catch integration issues that unit tests won't.
- **Phase 5 tasks (5.1–5.5) can be parallelized** if you want to run multiple `@claude` invocations concurrently, since each new agent is independent. Keep 5.6 serial at the end.
