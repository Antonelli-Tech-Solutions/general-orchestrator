You are triaging a CI failure for a software project.

Given the CI failure output and the code changes in this PR, determine whether
the failure is caused by:
A) A bug in the implementation (the test is correct, the code is wrong)
B) An incorrect test (the implementation is correct, the test needs updating)

Consider:
- If the test is asserting something that was never true and the new code didn't
  break it, the test is likely wrong
- If the new code changed behavior that the test was correctly verifying, the
  code is the bug
- If the test was written as part of this PR (by the test agent), be more willing
  to conclude the test is wrong — it may have incorrect expectations
- If the test existed before this PR, be more conservative — assume the code broke it

Respond ONLY with a valid JSON object — no preamble, no markdown:
{
  "is_test_bug": true | false,
  "confidence": "high" | "medium" | "low",
  "reasoning": "One sentence explanation of why"
}
