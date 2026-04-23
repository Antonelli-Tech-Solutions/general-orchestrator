"""Unit tests for SecurityAuditorAgent.parse_response."""
import json

import pytest
from agents.security_auditor_agent import SecurityAuditorAgent
from agents.base_agent import OutputContractError


_VALID_FINDING = {
    "severity": "CRITICAL",
    "category": "leaked-secret",
    "effort": "low",
    "fix_inline": False,
    "title": "Hardcoded API key in config.py",
    "body": "Line 12 contains a hardcoded API key. Move it to an environment variable.",
    "file": "config.py",
    "line": 12,
}

_VALID_PAYLOAD = {
    "findings": [_VALID_FINDING],
    "summary": "One critical finding: hardcoded secret.",
}

_VALID_JSON = json.dumps(_VALID_PAYLOAD)


class TestParseResponse:
    def setup_method(self):
        self.agent = SecurityAuditorAgent()

    def test_returns_findings_and_summary_on_plain_json(self):
        result = self.agent.parse_response(_VALID_JSON)
        assert result["summary"] == "One critical finding: hardcoded secret."
        assert len(result["findings"]) == 1
        assert result["findings"][0]["category"] == "leaked-secret"

    def test_returns_empty_findings_when_no_issues(self):
        payload = {"findings": [], "summary": "No security issues found."}
        result = self.agent.parse_response(json.dumps(payload))
        assert result["findings"] == []
        assert result["summary"] == "No security issues found."

    def test_parses_json_wrapped_in_markdown_fences(self):
        raw = f"```json\n{_VALID_JSON}\n```"
        result = self.agent.parse_response(raw)
        assert result["summary"] == "One critical finding: hardcoded secret."

    def test_parses_json_preceded_by_prose(self):
        raw = f"Here is my security audit:\n\n{_VALID_JSON}"
        result = self.agent.parse_response(raw)
        assert len(result["findings"]) == 1

    def test_raises_output_contract_error_on_plain_text(self):
        with pytest.raises(OutputContractError) as exc_info:
            self.agent.parse_response("No issues found in this diff.")
        err = exc_info.value
        assert err.agent == "security-auditor"
        assert err.task == "audit"

    def test_raises_output_contract_error_on_missing_findings_key(self):
        payload = {"summary": "All good."}
        with pytest.raises(OutputContractError):
            self.agent.parse_response(json.dumps(payload))

    def test_raises_output_contract_error_on_missing_summary_key(self):
        payload = {"findings": []}
        with pytest.raises(OutputContractError):
            self.agent.parse_response(json.dumps(payload))

    def test_raises_output_contract_error_on_empty_string(self):
        with pytest.raises(OutputContractError) as exc_info:
            self.agent.parse_response("")
        assert exc_info.value.got_preview == ""

    def test_got_preview_truncated_to_200_chars(self):
        long_text = "x" * 300
        with pytest.raises(OutputContractError) as exc_info:
            self.agent.parse_response(long_text)
        assert len(exc_info.value.got_preview) == 200
