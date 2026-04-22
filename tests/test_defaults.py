"""Verify the defaults/ scaffold structure."""
import os

DEFAULTS_DIR = os.path.join(os.path.dirname(__file__), "..", "defaults")

EXPECTED_PROMPT_DIRS = [
    "coder",
    "tester",
    "reviewer",
    "docs-writer",
    "issue-decomposer",
    "product-planner",
    "triager",
    "refactor",
    "security-auditor",
    "release",
]


def test_defaults_agents_toml_exists():
    assert os.path.isfile(os.path.join(DEFAULTS_DIR, "agents.toml"))


def test_defaults_agents_md_exists():
    assert os.path.isfile(os.path.join(DEFAULTS_DIR, ".agents.md"))


def test_defaults_prompt_dirs_exist():
    prompts_dir = os.path.join(DEFAULTS_DIR, "prompts")
    for name in EXPECTED_PROMPT_DIRS:
        assert os.path.isdir(os.path.join(prompts_dir, name)), f"missing defaults/prompts/{name}/"
