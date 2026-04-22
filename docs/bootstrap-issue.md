# Bootstrap: create issues for every task in the refactor task list

@claude Please create one GitHub Issue for each task in [`docs/orchestrator-refactor-tasks.md`](./docs/orchestrator-refactor-tasks.md).

## What to do

1. Read `docs/orchestrator-refactor-tasks.md` and identify every `### Task N.M:` block. There should be around 26 of them.
2. Make sure these labels exist (create with `gh label create` if they don't):
   - `refactor` (any color)
   - `phase-1`, `phase-2`, `phase-3`, `phase-4`, `phase-5`, `phase-6`, `phase-7` (any colors)
3. For each task block, create an issue:
   - **Title:** `Task N.M: <the task's heading text after the colon>`
     - Example: the heading `### Task 1.1: Scaffold defaults/ directory structure` becomes the title `Task 1.1: Scaffold defaults/ directory structure`.
   - **Body:** the full markdown of that task block (Goal, Context if present, Steps, Acceptance), then a horizontal rule, then:

     ```
     ---
     See [docs/orchestrator-refactor-prd.md](./docs/orchestrator-refactor-prd.md) for full context, and `CLAUDE.md` at the repo root for workflow conventions.
     ```

   - **Labels:** `refactor` and the appropriate `phase-<N>` label.
4. After **every** issue is created, iterate through them in task-list order and **add a handoff line** to the bottom of each issue body (using `gh issue edit --body`) pointing at the next issue's number:

   ```
   When this issue is completed, assign Issue #<next-issue-number> to dantonel
   ```

   - Task 7.2 is the last task and gets **no handoff line**.
   - Phase 5 tasks 5.1 → 5.2 → 5.3 → 5.4 → 5.5 still chain sequentially in the handoff, but add a note to the body of 5.2 through 5.5: `Note: Phase 5 tasks can run in parallel once Task 5.1 is merged.`

5. Once all issues exist with handoff lines in place, post a single summary comment on **this** bootstrap issue containing:
   - The total number of issues created.
   - A numbered list of the created issue numbers in task-list order (e.g., `Task 1.1 → #12, Task 1.2 → #13, …`).
   - The command the human should run to kick off the refactor:

     ```
     gh issue edit <Task 1.1 issue number> --add-assignee dantonel
     ```

## Important

- **Do not start implementing any of the created tasks.** This issue is bootstrap only. Implementation begins when a human assigns Issue "Task 1.1" to `dantonel`, which triggers the `@claude` action on that specific issue.
- **Close this bootstrap issue** with `gh issue close` after the summary comment is posted.
- If you hit an error (e.g., a label creation fails, an issue body is too long), comment here and stop rather than guessing.
