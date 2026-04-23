"""Unit tests for CoderAgent.parse_response."""
import pytest
from agents.coder_agent import CoderAgent
from agents.base_agent import OutputContractError


class TestCoderAgentParseResponse:
    def setup_method(self):
        # Bypass __init__ to avoid repo clone in unit tests
        self.agent = CoderAgent.__new__(CoderAgent)

    def test_parse_response_extracts_commit_message(self):
        text = "Some output\nCOMMIT: fix: implement user authentication\nSUMMARY: Added auth module."
        result = self.agent.parse_response(text)
        assert result == {"commit_message": "fix: implement user authentication"}

    def test_parse_response_returns_first_commit_line(self):
        text = "COMMIT: fix: first commit\nCOMMIT: fix: second commit"
        result = self.agent.parse_response(text)
        assert result == {"commit_message": "fix: first commit"}

    def test_parse_response_raises_on_missing_commit_line(self):
        text = "I read the code and made some changes.\nSUMMARY: Changed foo.js to add auth."
        with pytest.raises(OutputContractError) as exc_info:
            self.agent.parse_response(text)
        err = exc_info.value
        assert err.agent == "coder"
        assert err.task == "implement"
        assert "COMMIT:" in err.expected

    def test_parse_response_raises_on_empty_commit_message(self):
        text = "COMMIT: \nSUMMARY: Something happened."
        with pytest.raises(OutputContractError):
            self.agent.parse_response(text)

    def test_parse_response_raises_on_empty_response(self):
        with pytest.raises(OutputContractError):
            self.agent.parse_response("")
