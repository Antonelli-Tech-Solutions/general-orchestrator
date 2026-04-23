"""Unit tests for ProductPlannerAgent.parse_response (propose-issues task)."""
import json
import pytest
from agents.product_planner_agent import ProductPlannerAgent
from agents.base_agent import OutputContractError


class TestParseResponse:
    def test_returns_issues_on_plain_json_array(self):
        agent = ProductPlannerAgent()
        payload = [{"title": "Add login", "body": "Implement POST /login"}]
        result = agent.parse_response(json.dumps(payload))
        assert result["issues"] == payload

    def test_returns_issues_on_markdown_fenced_json(self):
        agent = ProductPlannerAgent()
        payload = [{"title": "Add logout", "body": "Implement DELETE /session"}]
        text = f"```json\n{json.dumps(payload)}\n```"
        result = agent.parse_response(text)
        assert result["issues"] == payload

    def test_returns_issues_when_array_embedded_in_prose(self):
        agent = ProductPlannerAgent()
        payload = [{"title": "Issue A", "body": "Body A"}]
        text = f"Here are the issues:\n{json.dumps(payload)}\nEnd."
        result = agent.parse_response(text)
        assert result["issues"] == payload

    def test_returns_empty_list_on_empty_array(self):
        agent = ProductPlannerAgent()
        result = agent.parse_response("[]")
        assert result["issues"] == []

    def test_raises_output_contract_error_on_plain_text(self):
        agent = ProductPlannerAgent()
        with pytest.raises(OutputContractError) as exc_info:
            agent.parse_response("This is just a PRD, not a JSON array.")
        err = exc_info.value
        assert err.agent == "product-planner"
        assert err.task == "propose-issues"

    def test_raises_output_contract_error_on_json_object(self):
        agent = ProductPlannerAgent()
        with pytest.raises(OutputContractError):
            agent.parse_response('{"title": "oops", "body": "not an array"}')

    def test_raises_output_contract_error_on_empty_string(self):
        agent = ProductPlannerAgent()
        with pytest.raises(OutputContractError) as exc_info:
            agent.parse_response("")
        assert exc_info.value.got_preview == ""

    def test_got_preview_truncated_to_200_chars(self):
        agent = ProductPlannerAgent()
        long_text = "x" * 300
        with pytest.raises(OutputContractError) as exc_info:
            agent.parse_response(long_text)
        assert len(exc_info.value.got_preview) == 200
