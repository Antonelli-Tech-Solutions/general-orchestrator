The code for issue #{{issue_number}} has been implemented and reviewed. The reviewer
found the following low-effort improvements that should be fixed immediately in this
PR rather than deferred to the backlog. Each fix should be a small, targeted change.

Inline fixes to apply:
{{inline_findings}}

Instructions:
- Read the relevant files before making any changes.
- Apply each fix as a minimal, targeted change — do not refactor beyond what is listed.
- Write all changes to disk using the Edit tool.
- Once all fixes are applied, stage and commit with:
    git add -A
    git commit -m "fix: apply inline review fixes for issue #{{issue_number}}"

<!-- ORCHESTRATOR OUTPUT CONTRACT — DO NOT MODIFY -->

After committing, output exactly two lines in this format:
  COMMIT: <the commit message you used>
  SUMMARY: <2-3 sentences describing what files were changed and why>
