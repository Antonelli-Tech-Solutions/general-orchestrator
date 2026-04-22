"""Unit tests for orchestrator/loader.py — registry loading (Task 1.4)."""
import pytest
from pathlib import Path

from orchestrator.loader import load_registry


def _make_defaults(root: Path, agents_toml_content: str = "") -> None:
    """Create a minimal defaults/ structure under root."""
    defaults = root / "defaults"
    defaults.mkdir()
    (defaults / "agents.toml").write_text(agents_toml_content, encoding="utf-8")


def _make_prompt_dir(root: Path, relative: str) -> None:
    (root / "defaults" / relative).mkdir(parents=True, exist_ok=True)


class TestLoadRegistryDefaultsOnly:
    def test_returns_empty_agents_and_conventions_when_toml_is_minimal(self, tmp_path):
        _make_defaults(tmp_path, "[conventions]\n")
        result = load_registry(tmp_path, tmp_path / "target")
        assert result["agents"] == {}
        assert result["conventions"] == {}

    def test_returns_parsed_conventions_from_defaults(self, tmp_path):
        _make_defaults(
            tmp_path,
            '[conventions]\nprimary_language = "python"\ntest_command = "pytest"\n',
        )
        result = load_registry(tmp_path, tmp_path / "target")
        assert result["conventions"]["primary_language"] == "python"
        assert result["conventions"]["test_command"] == "pytest"

    def test_returns_parsed_agents_from_defaults(self, tmp_path):
        _make_defaults(
            tmp_path,
            '[agents.tester]\nmodel = "claude-sonnet-4-6"\ntasks = ["write-tests"]\n',
        )
        _make_prompt_dir(tmp_path, "prompts/tester")
        result = load_registry(tmp_path, tmp_path / "target")
        assert "tester" in result["agents"]
        assert result["agents"]["tester"]["model"] == "claude-sonnet-4-6"


class TestLoadRegistryTargetOverride:
    def test_target_agent_replaces_default_agent(self, tmp_path):
        _make_defaults(
            tmp_path,
            '[agents.coder]\nmodel = "claude-sonnet-4-6"\ntasks = ["implement"]\n',
        )
        target = tmp_path / "target"
        target.mkdir()
        (target / "agents.toml").write_text(
            '[agents.coder]\nmodel = "claude-opus-4-7"\ntasks = ["implement", "fix-review"]\n',
            encoding="utf-8",
        )
        result = load_registry(tmp_path, target)
        assert result["agents"]["coder"]["model"] == "claude-opus-4-7"
        assert "fix-review" in result["agents"]["coder"]["tasks"]

    def test_target_override_preserves_unoverridden_agents(self, tmp_path):
        _make_defaults(
            tmp_path,
            '[agents.coder]\nmodel = "claude-sonnet-4-6"\n\n'
            '[agents.tester]\nmodel = "claude-haiku-4-5-20251001"\n',
        )
        target = tmp_path / "target"
        target.mkdir()
        (target / "agents.toml").write_text(
            '[agents.coder]\nmodel = "claude-opus-4-7"\n',
            encoding="utf-8",
        )
        result = load_registry(tmp_path, target)
        # coder overridden
        assert result["agents"]["coder"]["model"] == "claude-opus-4-7"
        # tester kept from defaults
        assert result["agents"]["tester"]["model"] == "claude-haiku-4-5-20251001"

    def test_target_conventions_replace_defaults_conventions(self, tmp_path):
        _make_defaults(
            tmp_path,
            '[conventions]\ntest_command = "node --test"\n',
        )
        target = tmp_path / "target"
        target.mkdir()
        (target / "agents.toml").write_text(
            '[conventions]\ntest_command = "pytest"\nprimary_language = "python"\n',
            encoding="utf-8",
        )
        result = load_registry(tmp_path, target)
        assert result["conventions"]["test_command"] == "pytest"
        assert result["conventions"]["primary_language"] == "python"

    def test_no_target_file_uses_defaults_conventions(self, tmp_path):
        _make_defaults(
            tmp_path,
            '[conventions]\ntest_command = "pytest"\n',
        )
        result = load_registry(tmp_path, tmp_path / "no-target")
        assert result["conventions"]["test_command"] == "pytest"


class TestLoadRegistryMalformedToml:
    def test_malformed_default_toml_raises_value_error_naming_file(self, tmp_path):
        _make_defaults(tmp_path, "this is not valid toml !!!\n[[[")
        with pytest.raises(ValueError, match="agents.toml"):
            load_registry(tmp_path, tmp_path / "target")

    def test_malformed_target_toml_raises_value_error_naming_file(self, tmp_path):
        _make_defaults(tmp_path, "[conventions]\n")
        target = tmp_path / "target"
        target.mkdir()
        (target / "agents.toml").write_text("bad toml [[[\n", encoding="utf-8")
        with pytest.raises(ValueError, match="agents.toml"):
            load_registry(tmp_path, target)


class TestLoadRegistryMissingPromptDir:
    def test_missing_prompt_dir_raises_file_not_found_error(self, tmp_path):
        _make_defaults(
            tmp_path,
            '[agents.coder]\nmodel = "claude-opus-4-7"\nprompt_dir = "prompts/coder"\n',
        )
        # Do NOT create defaults/prompts/coder
        with pytest.raises(FileNotFoundError, match="coder"):
            load_registry(tmp_path, tmp_path / "target")

    def test_existing_prompt_dir_does_not_raise(self, tmp_path):
        _make_defaults(
            tmp_path,
            '[agents.coder]\nmodel = "claude-opus-4-7"\nprompt_dir = "prompts/coder"\n',
        )
        _make_prompt_dir(tmp_path, "prompts/coder")
        result = load_registry(tmp_path, tmp_path / "target")
        assert "coder" in result["agents"]
