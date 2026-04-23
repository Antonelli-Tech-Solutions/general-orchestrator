"""Unit tests for TriagerAgent.parse_response."""
import json
import pytest
from agents.triager_agent import TriagerAgent
from agents.base_agent import OutputContractError


_VALID_PAYLOAD = {
    "classification": "test failure",
    "suggested_agent": "tester",
    "reasoning": "Two assertions are failing in test_auth.py.",
}


class TestParseResponse:
    def test_returns_fields_on_plain_json_object(self):
        agent = TriagerAgent()
        result = agent.parse_response(json.dumps(_VALID_PAYLOAD))
        assert result["classification"] == "test failure"
        assert result["suggested_agent"] == "tester"
        assert result["reasoning"] == "Two assertions are failing in test_auth.py."

    def test_returns_fields_on_markdown_fenced_json(self):
        agent = TriagerAgent()
        text = f"```json\n{json.dumps(_VALID_PAYLOAD)}\n```"
        result = agent.parse_response(text)
        assert result["classification"] == "test failure"
        assert result["suggested_agent"] == "tester"

    def test_returns_fields_when_json_embedded_in_prose(self):
        agent = TriagerAgent()
        text = f"Here is my analysis:\n{json.dumps(_VALID_PAYLOAD)}\nEnd."
        result = agent.parse_response(text)
        assert result["suggested_agent"] == "tester"

    def test_only_returns_required_keys(self):
        agent = TriagerAgent()
        payload = {**_VALID_PAYLOAD, "extra": "ignored"}
        result = agent.parse_response(json.dumps(payload))
        assert set(result.keys()) == {"classification", "suggested_agent", "reasoning"}

    def test_raises_output_contract_error_on_plain_text(self):
        agent = TriagerAgent()
        with pytest.raises(OutputContractError) as exc_info:
            agent.parse_response("This is a log with no JSON.")
        err = exc_info.value
        assert err.agent == "triager"
        assert err.task == "triage"

    def test_raises_output_contract_error_on_missing_key(self):
        agent = TriagerAgent()
        payload = {"classification": "build failure", "suggested_agent": "coder"}
        with pytest.raises(OutputContractError):
            agent.parse_response(json.dumps(payload))

    def test_raises_output_contract_error_on_json_array(self):
        agent = TriagerAgent()
        with pytest.raises(OutputContractError):
            agent.parse_response("[1, 2, 3]")

    def test_raises_output_contract_error_on_empty_string(self):
        agent = TriagerAgent()
        with pytest.raises(OutputContractError) as exc_info:
            agent.parse_response("")
        assert exc_info.value.got_preview == ""

    def test_got_preview_truncated_to_200_chars(self):
        agent = TriagerAgent()
        long_text = "x" * 300
        with pytest.raises(OutputContractError) as exc_info:
            agent.parse_response(long_text)
        assert len(exc_info.value.got_preview) == 200
