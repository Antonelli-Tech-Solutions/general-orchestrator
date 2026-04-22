"""Unit tests for orchestrator/loader.py (Task 1.7).

Covers: registry loading with/without target override; prompt composition order;
output contract non-overridability; variable interpolation; malformed TOML errors;
missing default task file errors.
"""
import pytest
from pathlib import Path

from orchestrator.loader import load_registry, compose_prompt, interpolate, OUTPUT_CONTRACT_MARKER


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


# ---------------------------------------------------------------------------
# Helpers for compose_prompt tests
# ---------------------------------------------------------------------------

def _make_default_task(root: Path, agent: str, task: str, content: str) -> None:
    task_dir = root / "defaults" / "prompts" / agent
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / f"{task}.md").write_text(content, encoding="utf-8")


def _make_default_agents_md(root: Path, content: str) -> None:
    (root / "defaults" / ".agents.md").write_text(content, encoding="utf-8")


def _registry(identity: str = "") -> dict:
    return {"agents": {"tester": {"identity": identity}}, "conventions": {}}


# ---------------------------------------------------------------------------
# compose_prompt tests (Task 1.5)
# ---------------------------------------------------------------------------

class TestComposePromptCompositionOrder:
    """Verify all five parts appear in the correct order (PRD §4.3)."""

    def test_all_parts_present_in_correct_order(self, tmp_path):
        target = tmp_path / "target"
        target.mkdir()
        _make_defaults(tmp_path, "[conventions]\n")
        _make_default_agents_md(
            tmp_path,
            "## @shared\nShared instructions.\n\n## @tester\nTester-specific instructions.\n",
        )
        default_task = (
            "Do the task here.\n\n"
            f"{OUTPUT_CONTRACT_MARKER}\n\n"
            "Output: RESULT: <value>"
        )
        _make_default_task(tmp_path, "tester", "write-tests", default_task)

        result = compose_prompt(
            "tester",
            "write-tests",
            {"issue_number": "42"},
            _registry("You are a tester agent."),
            tmp_path,
            target,
        )

        assert "You are a tester agent." in result
        assert "Shared instructions." in result
        assert "Tester-specific instructions." in result
        assert "Do the task here." in result
        assert OUTPUT_CONTRACT_MARKER in result
        assert "Output: RESULT: <value>" in result
        assert "Runtime Context" in result

        pos_identity = result.index("You are a tester agent.")
        pos_shared = result.index("Shared instructions.")
        pos_tester = result.index("Tester-specific instructions.")
        pos_task = result.index("Do the task here.")
        pos_marker = result.index(OUTPUT_CONTRACT_MARKER)
        pos_contract = result.index("Output: RESULT: <value>")
        pos_context = result.index("Runtime Context")

        assert pos_identity < pos_shared < pos_tester < pos_task
        assert pos_task < pos_marker < pos_contract < pos_context

    def test_empty_context_omits_runtime_context_section(self, tmp_path):
        target = tmp_path / "target"
        target.mkdir()
        _make_defaults(tmp_path, "[conventions]\n")
        _make_default_task(tmp_path, "tester", "write-tests", "Body.")

        result = compose_prompt("tester", "write-tests", {}, _registry(), tmp_path, target)

        assert "Runtime Context" not in result

    def test_missing_identity_omits_identity_section(self, tmp_path):
        target = tmp_path / "target"
        target.mkdir()
        _make_defaults(tmp_path, "[conventions]\n")
        _make_default_task(tmp_path, "tester", "write-tests", "Body.")

        result = compose_prompt("tester", "write-tests", {}, _registry(""), tmp_path, target)

        assert result.startswith("Body.")


class TestComposePromptOutputContractIsUnoverridable:
    """Verify the output contract always comes from orchestrator defaults (PRD H2)."""

    def test_target_task_without_marker_still_gets_default_contract(self, tmp_path):
        target = tmp_path / "target"
        target.mkdir()
        _make_defaults(tmp_path, "[conventions]\n")
        _make_default_task(
            tmp_path,
            "tester",
            "write-tests",
            f"Default body.\n\n{OUTPUT_CONTRACT_MARKER}\n\nOutput: DEFAULT_CONTRACT",
        )
        target_prompts = target / "prompts" / "tester"
        target_prompts.mkdir(parents=True)
        (target_prompts / "write-tests.md").write_text(
            "Target body, no marker.", encoding="utf-8"
        )

        result = compose_prompt("tester", "write-tests", {}, _registry(), tmp_path, target)

        assert "Target body, no marker." in result
        assert "Default body." not in result
        assert "Output: DEFAULT_CONTRACT" in result

    def test_target_task_with_marker_uses_default_contract_not_its_own(self, tmp_path):
        target = tmp_path / "target"
        target.mkdir()
        _make_defaults(tmp_path, "[conventions]\n")
        _make_default_task(
            tmp_path,
            "tester",
            "write-tests",
            f"Default body.\n\n{OUTPUT_CONTRACT_MARKER}\n\nOutput: DEFAULT_CONTRACT",
        )
        target_prompts = target / "prompts" / "tester"
        target_prompts.mkdir(parents=True)
        (target_prompts / "write-tests.md").write_text(
            f"Target body.\n\n{OUTPUT_CONTRACT_MARKER}\n\nOutput: TARGET_CONTRACT",
            encoding="utf-8",
        )

        result = compose_prompt("tester", "write-tests", {}, _registry(), tmp_path, target)

        assert "Target body." in result
        assert "Output: DEFAULT_CONTRACT" in result
        assert "Output: TARGET_CONTRACT" not in result


class TestComposePromptMissingDefaultTaskFile:
    def test_raises_file_not_found_naming_agent_and_task(self, tmp_path):
        target = tmp_path / "target"
        target.mkdir()
        _make_defaults(tmp_path, "[conventions]\n")
        _make_prompt_dir(tmp_path, "prompts/tester")
        # Deliberately do NOT create the task file.

        with pytest.raises(FileNotFoundError, match="write-tests"):
            compose_prompt("tester", "write-tests", {}, _registry(), tmp_path, target)


class TestComposePromptAgentsMdCascade:
    """Verify the .agents.md cascade: default first, then target, @shared + @<agent>."""

    def test_extracts_shared_and_agent_sections_from_default_agents_md(self, tmp_path):
        target = tmp_path / "target"
        target.mkdir()
        _make_defaults(tmp_path, "[conventions]\n")
        _make_default_agents_md(
            tmp_path,
            "## @shared\nDefault shared.\n\n## @tester\nDefault tester rules.\n",
        )
        _make_default_task(tmp_path, "tester", "write-tests", "Body.")

        result = compose_prompt("tester", "write-tests", {}, _registry(), tmp_path, target)

        assert "Default shared." in result
        assert "Default tester rules." in result

    def test_target_agents_md_appended_after_default_and_before_task_body(self, tmp_path):
        target = tmp_path / "target"
        target.mkdir()
        _make_defaults(tmp_path, "[conventions]\n")
        _make_default_agents_md(tmp_path, "## @shared\nDefault shared.\n")
        (target / ".agents.md").write_text(
            "## @shared\nTarget shared.\n\n## @tester\nTarget tester rules.\n",
            encoding="utf-8",
        )
        _make_default_task(tmp_path, "tester", "write-tests", "Task body.")

        result = compose_prompt("tester", "write-tests", {}, _registry(), tmp_path, target)

        assert "Default shared." in result
        assert "Target shared." in result
        assert "Target tester rules." in result
        # Default cascade comes before target cascade
        assert result.index("Default shared.") < result.index("Target shared.")
        # Both cascade sections come before task body
        assert result.index("Target tester rules.") < result.index("Task body.")

    def test_unrelated_agent_sections_are_not_included(self, tmp_path):
        target = tmp_path / "target"
        target.mkdir()
        _make_defaults(tmp_path, "[conventions]\n")
        _make_default_agents_md(
            tmp_path,
            "## @shared\nShared rules.\n\n## @coder\nCoder-only rules.\n",
        )
        _make_default_task(tmp_path, "tester", "write-tests", "Body.")

        result = compose_prompt("tester", "write-tests", {}, _registry(), tmp_path, target)

        assert "Shared rules." in result
        assert "Coder-only rules." not in result


# ---------------------------------------------------------------------------
# interpolate tests (Task 1.6)
# ---------------------------------------------------------------------------

class TestInterpolate:
    def test_substitutes_double_brace_variable(self):
        assert interpolate("run {{test_command}}", {"test_command": "pytest"}) == "run pytest"

    def test_raises_value_error_naming_missing_variable(self):
        with pytest.raises(ValueError, match="missing"):
            interpolate("hi {{missing}}", {})

    def test_single_braces_pass_through_unchanged(self):
        text = '{"key": "value"}'
        assert interpolate(text, {}) == text

    def test_multiple_variables_all_substituted(self):
        result = interpolate("{{a}} and {{b}}", {"a": "foo", "b": "bar"})
        assert result == "foo and bar"

    def test_prompt_filename_included_in_error_message(self):
        with pytest.raises(ValueError, match="myfile.md"):
            interpolate("{{missing}}", {}, prompt_filename="myfile.md")

    def test_no_placeholders_returns_text_unchanged(self):
        text = "nothing to replace here"
        assert interpolate(text, {}) == text


class TestComposePromptInterpolation:
    def test_conventions_substituted_into_prompt(self, tmp_path):
        target = tmp_path / "target"
        target.mkdir()
        _make_defaults(tmp_path, "[conventions]\n")
        registry = {
            "agents": {"tester": {"identity": ""}},
            "conventions": {"test_command": "pytest"},
        }
        _make_default_task(tmp_path, "tester", "write-tests", "Run {{test_command}} to test.")

        result = compose_prompt("tester", "write-tests", {}, registry, tmp_path, target)

        assert "Run pytest to test." in result

    def test_explicit_context_overrides_conventions(self, tmp_path):
        target = tmp_path / "target"
        target.mkdir()
        _make_defaults(tmp_path, "[conventions]\n")
        registry = {
            "agents": {"tester": {"identity": ""}},
            "conventions": {"test_command": "jest"},
        }
        _make_default_task(tmp_path, "tester", "write-tests", "Run {{test_command}} to test.")

        result = compose_prompt(
            "tester", "write-tests", {"test_command": "pytest"}, registry, tmp_path, target
        )

        assert "Run pytest to test." in result

    def test_context_value_with_double_brace_pattern_does_not_crash(self, tmp_path):
        """Context values containing {{word}} must not be passed through interpolate."""
        target = tmp_path / "target"
        target.mkdir()
        _make_defaults(tmp_path, "[conventions]\n")
        _make_default_task(tmp_path, "tester", "write-tests", "Body.")

        result = compose_prompt(
            "tester",
            "write-tests",
            {"issue_title": "Implement {{variable}} interpolation"},
            _registry(),
            tmp_path,
            target,
        )

        assert "{{variable}}" in result
        assert "Runtime Context" in result
