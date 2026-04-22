"""Unit tests for DocumentationAgent.parse_response."""
import pytest
from agents.documentation_agent import DocumentationAgent
from agents.base_agent import OutputContractError


class TestParseResponse:
    def test_returns_empty_list_on_no_changes(self):
        agent = DocumentationAgent()
        result = agent.parse_response("NO_CHANGES")
        assert result["files_updated"] == []

    def test_strips_whitespace_around_no_changes(self):
        agent = DocumentationAgent()
        result = agent.parse_response("  NO_CHANGES  ")
        assert result["files_updated"] == []

    def test_returns_single_updated_file(self):
        agent = DocumentationAgent()
        result = agent.parse_response("Some preamble\nUPDATED: README.md\n")
        assert result["files_updated"] == ["README.md"]

    def test_returns_multiple_updated_files(self):
        agent = DocumentationAgent()
        result = agent.parse_response("UPDATED: README.md\nUPDATED: docs/guide.md\n")
        assert result["files_updated"] == ["README.md", "docs/guide.md"]

    def test_strips_whitespace_from_updated_path(self):
        agent = DocumentationAgent()
        result = agent.parse_response("UPDATED:   docs/api.md   ")
        assert result["files_updated"] == ["docs/api.md"]

    def test_raises_output_contract_error_on_missing_sentinel(self):
        agent = DocumentationAgent()
        with pytest.raises(OutputContractError) as exc_info:
            agent.parse_response("No marker here at all.")
        err = exc_info.value
        assert err.agent == "docs-writer"
        assert err.task == "document"
        assert "NO_CHANGES" in err.expected
        assert "UPDATED:" in err.expected

    def test_raises_output_contract_error_on_empty_response(self):
        agent = DocumentationAgent()
        with pytest.raises(OutputContractError) as exc_info:
            agent.parse_response("")
        assert exc_info.value.got_preview == ""

    def test_raises_output_contract_error_when_updated_path_is_empty(self):
        agent = DocumentationAgent()
        with pytest.raises(OutputContractError):
            agent.parse_response("UPDATED:   \nOther content")

    def test_got_preview_truncated_to_200_chars(self):
        agent = DocumentationAgent()
        long_text = "x" * 300
        with pytest.raises(OutputContractError) as exc_info:
            agent.parse_response(long_text)
        assert len(exc_info.value.got_preview) == 200
