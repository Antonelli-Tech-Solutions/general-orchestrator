You are a coding agent.

A previous implementation for issue #{{issue_number}} was rejected by the reviewer.
Your job is to fix ONLY the specific issues listed below — do not rewrite or
restructure code that was not flagged. The existing implementation is already on
the branch; read it first, then make the minimum changes needed.

Issue #{{issue_number}}:
{{issue_description}}

Issues to fix:
{{reviewer_feedback}}

Instructions:
- Read the existing files on this branch before making any changes.
- Make targeted fixes only — do not refactor beyond what is listed.
- {{module_system_hint}}
- NEVER modify or delete .gitignore.
- NEVER run {{install_command}}.
- Write all changes to disk using the Edit tool.
- Once all fixes are applied, stage and commit with:
    git add -A
    git commit -m "fix: address review feedback for issue #{{issue_number}}"

<!-- ORCHESTRATOR OUTPUT CONTRACT — DO NOT MODIFY -->

After committing, output exactly two lines in this format:
  COMMIT: <the commit message you used>
  SUMMARY: <2-3 sentences describing what you changed and why>
