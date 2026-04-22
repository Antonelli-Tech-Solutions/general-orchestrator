"""Unit tests for ReviewerAgent._extract_json."""
from agents.reviewer_agent import ReviewerAgent


VALID_REVIEW = {
    "findings": [
        {
            "severity": "HIGH",
            "effort": "low",
            "fix_inline": False,
            "title": "Missing null check",
            "body": "foo can be null",
            "file": "src/foo.js",
            "line": 10,
        }
    ],
    "summary": "One high finding.",
}

VALID_JSON = '{"findings": [{"severity": "HIGH", "effort": "low", "fix_inline": false, "title": "Missing null check", "body": "foo can be null", "file": "src/foo.js", "line": 10}], "summary": "One high finding."}'


class TestExtractJson:
    def test_parses_plain_json_response(self):
        agent = ReviewerAgent()
        result = agent._extract_json(VALID_JSON)
        assert result["summary"] == "One high finding."
        assert len(result["findings"]) == 1

    def test_parses_json_wrapped_in_markdown_fences(self):
        agent = ReviewerAgent()
        raw = f"```json\n{VALID_JSON}\n```"
        result = agent._extract_json(raw)
        assert result["summary"] == "One high finding."

    def test_parses_json_preceded_by_prose_with_template_literal_braces(self):
        # Regression test: prose containing JS template literals like `${active}`
        # caused the old find("{") strategy to grab the wrong position, making
        # all JSON extraction fail.
        agent = ReviewerAgent()
        raw = (
            "I need to check one concern — the `badgeHtml` inserted raw.\n\n"
            "Looking at line 65:\n"
            "```js\n"
            "return `<button class=\"info-tab-btn${active}`;\n"
            "```\n\n"
            + VALID_JSON
        )
        result = agent._extract_json(raw)
        assert result["summary"] == "One high finding."
        assert len(result["findings"]) == 1

    def test_returns_empty_findings_when_no_json_present(self):
        agent = ReviewerAgent()
        result = agent._extract_json("No JSON here at all.")
        assert result["findings"] == []
        assert "No JSON" in result["summary"]

    def test_summary_fallback_truncated_to_200_chars(self):
        agent = ReviewerAgent()
        long_text = "x" * 300
        result = agent._extract_json(long_text)
        assert len(result["summary"]) == 200
