"""Unit tests for RefactorAgent.parse_response."""
import pytest
from agents.refactor_agent import RefactorAgent
from agents.base_agent import OutputContractError


class TestRefactorAgentParseResponse:
    def setup_method(self):
        self.agent = RefactorAgent()

    def test_parse_response_extracts_commit_message(self):
        text = "Some output\nCOMMIT: refactor: extract auth module\nTrailing text"
        result = self.agent.parse_response(text)
        assert result == {"commit_message": "refactor: extract auth module"}

    def test_parse_response_returns_first_commit_line(self):
        text = "COMMIT: refactor: first\nCOMMIT: refactor: second"
        result = self.agent.parse_response(text)
        assert result == {"commit_message": "refactor: first"}

    def test_parse_response_strips_whitespace_from_message(self):
        text = "COMMIT:   refactor: rename foo to bar   "
        result = self.agent.parse_response(text)
        assert result == {"commit_message": "refactor: rename foo to bar"}

    def test_parse_response_raises_on_missing_commit_line(self):
        text = "I read the code and made some changes."
        with pytest.raises(OutputContractError) as exc_info:
            self.agent.parse_response(text)
        err = exc_info.value
        assert err.agent == "refactor"
        assert err.task == "refactor"
        assert "COMMIT:" in err.expected

    def test_parse_response_raises_on_empty_commit_message(self):
        text = "COMMIT:   \nSome trailing text."
        with pytest.raises(OutputContractError):
            self.agent.parse_response(text)

    def test_parse_response_raises_on_empty_response(self):
        with pytest.raises(OutputContractError) as exc_info:
            self.agent.parse_response("")
        assert exc_info.value.got_preview == ""

    def test_parse_response_preview_truncated_to_200_chars(self):
        long_text = "x" * 300
        with pytest.raises(OutputContractError) as exc_info:
            self.agent.parse_response(long_text)
        assert len(exc_info.value.got_preview) == 200
