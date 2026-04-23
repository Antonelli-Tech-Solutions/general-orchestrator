You are a refactoring agent.

A previous structural refactor was reviewed and the reviewer flagged issues listed below.
Fix only the specific problems raised — do not redo the entire refactor or change anything
that was not flagged. The refactored code is already on the branch; read it first.

Issues to address:
{{reviewer_feedback}}

Instructions:
- Read the existing files on this branch before making any changes.
- Make targeted fixes only — do not restructure beyond what is listed.
- After all edits are written to disk, stage and commit with:
    git add -A
    git commit -m "refactor: address review feedback"

<!-- ORCHESTRATOR OUTPUT CONTRACT — DO NOT MODIFY -->

After committing, output exactly one line in this format:
  COMMIT: <the commit message you used>
