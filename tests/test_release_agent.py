"""Unit tests for ReleaseAgent.parse_response."""
import pytest
from agents.release_agent import ReleaseAgent
from agents.base_agent import OutputContractError


class TestParseResponseBumpVersion:
    def setup_method(self):
        self.agent = ReleaseAgent()
        self.agent._current_task = "bump-version"

    def test_extracts_version_from_valid_output(self):
        result = self.agent.parse_response("Analysis complete.\nVERSION: 1.2.3\nDone.")
        assert result["version"] == "1.2.3"

    def test_extracts_first_version_when_multiple_lines_present(self):
        result = self.agent.parse_response("VERSION: 1.2.3\nVERSION: 2.0.0")
        assert result["version"] == "1.2.3"

    def test_strips_whitespace_from_version(self):
        result = self.agent.parse_response("VERSION:   2.0.0  ")
        assert result["version"] == "2.0.0"

    def test_raises_on_missing_version_marker(self):
        with pytest.raises(OutputContractError) as exc_info:
            self.agent.parse_response("No version here.")
        err = exc_info.value
        assert err.agent == "release"
        assert err.task == "bump-version"
        assert "VERSION:" in err.expected

    def test_raises_on_empty_version_value(self):
        with pytest.raises(OutputContractError):
            self.agent.parse_response("VERSION:   \nOther content")

    def test_raises_on_empty_response(self):
        with pytest.raises(OutputContractError) as exc_info:
            self.agent.parse_response("")
        assert exc_info.value.got_preview == ""

    def test_got_preview_truncated_to_200_chars(self):
        long_text = "x" * 300
        with pytest.raises(OutputContractError) as exc_info:
            self.agent.parse_response(long_text)
        assert len(exc_info.value.got_preview) == 200


class TestParseResponseDraftChangelog:
    def setup_method(self):
        self.agent = ReleaseAgent()
        self.agent._current_task = "draft-changelog"

    def test_returns_response_with_plain_text(self):
        text = "## Changes\n- Fixed bug #42\n- Added feature X"
        result = self.agent.parse_response(text)
        assert result["response"] == text

    def test_returns_response_for_empty_text(self):
        result = self.agent.parse_response("")
        assert result["response"] == ""
