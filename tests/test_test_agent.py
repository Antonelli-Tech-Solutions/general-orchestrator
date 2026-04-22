"""Unit tests for TestAgent.parse_response."""
import pytest
from agents.test_agent import TestAgent
from agents.base_agent import OutputContractError


class TestParseResponse:
    def test_returns_test_file_path_on_valid_output(self):
        agent = TestAgent()
        result = agent.parse_response(
            "Some output\nTEST_FILE: test/unit/foo.test.js\nMore stuff"
        )
        assert result["test_file_path"] == "test/unit/foo.test.js"

    def test_returns_first_test_file_path_when_multiple_lines_present(self):
        agent = TestAgent()
        result = agent.parse_response(
            "Preamble\nTEST_FILE: test/unit/first.test.js\nTEST_FILE: test/unit/second.test.js"
        )
        assert result["test_file_path"] == "test/unit/first.test.js"

    def test_strips_leading_and_trailing_whitespace_from_path(self):
        agent = TestAgent()
        result = agent.parse_response("TEST_FILE:   test/unit/bar.test.js  ")
        assert result["test_file_path"] == "test/unit/bar.test.js"

    def test_raises_output_contract_error_on_missing_marker(self):
        agent = TestAgent()
        with pytest.raises(OutputContractError) as exc_info:
            agent.parse_response("No marker here at all.")
        err = exc_info.value
        assert err.agent == "tester"
        assert err.task == "write-tests"
        assert "TEST_FILE:" in err.expected

    def test_raises_output_contract_error_on_empty_path(self):
        agent = TestAgent()
        with pytest.raises(OutputContractError):
            agent.parse_response("TEST_FILE:   \nOther content")

    def test_raises_output_contract_error_on_completely_empty_response(self):
        agent = TestAgent()
        with pytest.raises(OutputContractError) as exc_info:
            agent.parse_response("")
        assert exc_info.value.got_preview == ""

    def test_got_preview_truncated_to_200_chars(self):
        agent = TestAgent()
        long_text = "x" * 300
        with pytest.raises(OutputContractError) as exc_info:
            agent.parse_response(long_text)
        assert len(exc_info.value.got_preview) == 200
