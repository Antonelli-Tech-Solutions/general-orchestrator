import pytest
from agents.issue_decomposer_agent import IssueDecomposerAgent
from agents.base_agent import OutputContractError


VALID_JSON = '{"size": "small", "reasoning": "Only one file changes.", "areas_affected": ["api"], "sub_issues": []}'

VALID_JSON_LARGE = """{
  "size": "large",
  "reasoning": "Touches many subsystems.",
  "areas_affected": ["auth", "db", "api"],
  "sub_issues": [
    {
      "title": "Add auth middleware",
      "body": "Implement JWT validation middleware.",
      "depends_on_index": null,
      "needs_tests": true
    }
  ]
}"""


class TestParseResponse:
    def setup_method(self):
        self.agent = IssueDecomposerAgent()

    def test_parse_response_direct_json(self):
        result = self.agent.parse_response(VALID_JSON)
        assert result["size"] == "small"
        assert result["reasoning"] == "Only one file changes."
        assert result["areas_affected"] == ["api"]
        assert result["sub_issues"] == []

    def test_parse_response_markdown_fenced_json(self):
        text = f"```json\n{VALID_JSON}\n```"
        result = self.agent.parse_response(text)
        assert result["size"] == "small"

    def test_parse_response_markdown_fenced_no_lang(self):
        text = f"```\n{VALID_JSON}\n```"
        result = self.agent.parse_response(text)
        assert result["size"] == "small"

    def test_parse_response_json_embedded_in_preamble(self):
        text = f"Here is my assessment:\n{VALID_JSON_LARGE}"
        result = self.agent.parse_response(text)
        assert result["size"] == "large"
        assert len(result["sub_issues"]) == 1

    def test_parse_response_large_with_sub_issues(self):
        result = self.agent.parse_response(VALID_JSON_LARGE)
        assert result["size"] == "large"
        assert result["sub_issues"][0]["title"] == "Add auth middleware"
        assert result["sub_issues"][0]["needs_tests"] is True

    def test_parse_response_raises_on_plain_text(self):
        with pytest.raises(OutputContractError) as exc_info:
            self.agent.parse_response("This issue looks medium sized to me.")
        err = exc_info.value
        assert err.agent == "issue-decomposer"
        assert err.task == "decompose"

    def test_parse_response_raises_on_empty_string(self):
        with pytest.raises(OutputContractError):
            self.agent.parse_response("")

    def test_parse_response_raises_on_whitespace_only(self):
        with pytest.raises(OutputContractError):
            self.agent.parse_response("   \n\t  ")

    def test_parse_response_raises_on_malformed_json(self):
        with pytest.raises(OutputContractError):
            self.agent.parse_response('{"size": "small", "reasoning": }')
