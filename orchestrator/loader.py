from __future__ import annotations

from pathlib import Path

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError as exc:
        raise ImportError("Python 3.11+ required, or install 'tomli'") from exc


def _load_toml(path: Path) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Malformed TOML in {path}: {exc}") from exc


def load_registry(orchestrator_root: Path, target_root: Path) -> dict:
    """Load the agent registry, merging target-repo overrides on top of defaults.

    Merge rule: per individual agent key and per top-level section, the target
    wins wholesale.  If the target defines [agents.coder], that entry fully
    replaces the default [agents.coder]; other agents remain from defaults.
    [conventions] in the target replaces the defaults' [conventions] entirely.

    Returns:
        {
            "agents":      {agent_name: config_dict, ...},
            "conventions": {variable_name: value, ...},
        }

    Raises:
        FileNotFoundError: if the orchestrator defaults file is missing, or if
            a loaded agent references a prompt_dir that does not exist under
            defaults/.
        ValueError: if either TOML file is malformed (message names the file).
    """
    default_toml = orchestrator_root / "defaults" / "agents.toml"
    if not default_toml.exists():
        raise FileNotFoundError(f"Orchestrator defaults not found: {default_toml}")

    default_data = _load_toml(default_toml)

    target_toml = target_root / "agents.toml"
    target_data = _load_toml(target_toml) if target_toml.exists() else {}

    # Per-agent merge: target entries win wholesale over defaults.
    default_agents: dict = default_data.get("agents", {})
    target_agents: dict = target_data.get("agents", {})
    agents = {**default_agents, **target_agents}

    # Conventions: target block wins entirely if present.
    conventions: dict = target_data.get("conventions", default_data.get("conventions", {}))

    # Validate that every referenced prompt_dir exists under defaults/.
    for agent_name, agent_config in agents.items():
        prompt_dir = agent_config.get("prompt_dir")
        if prompt_dir:
            full_path = orchestrator_root / "defaults" / prompt_dir
            if not full_path.is_dir():
                raise FileNotFoundError(
                    f"Agent '{agent_name}' references prompt_dir '{prompt_dir}' "
                    f"but {full_path} does not exist"
                )

    return {"agents": agents, "conventions": conventions}
