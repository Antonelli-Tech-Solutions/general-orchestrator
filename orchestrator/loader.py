from __future__ import annotations

import re
from pathlib import Path

try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore[no-redef]
    except ImportError as exc:
        raise ImportError("Python 3.11+ required, or install 'tomli'") from exc

OUTPUT_CONTRACT_MARKER = "<!-- ORCHESTRATOR OUTPUT CONTRACT — DO NOT MODIFY -->"


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


def _extract_agents_md_sections(content: str, agent: str) -> str:
    """Extract @shared and @<agent> sections from .agents.md file content."""
    pattern = re.compile(r"^## @(\S+)", re.MULTILINE)
    matches = list(pattern.finditer(content))
    sections: dict[str, str] = {}
    for i, match in enumerate(matches):
        name = match.group(1)
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        sections[name] = content[start:end].strip()
    parts = []
    if "shared" in sections:
        parts.append(sections["shared"])
    if agent in sections:
        parts.append(sections[agent])
    return "\n\n".join(parts)


def compose_prompt(
    agent: str,
    task: str,
    context: dict,
    registry: dict,
    orchestrator_root: Path,
    target_root: Path,
) -> str:
    """Compose the final prompt for a given (agent, task) per PRD §4.3.

    Composition order:
    1. Agent identity (from registry; target already wins via load_registry merge)
    2. .agents.md cascade: orchestrator default then target, @shared + @<agent>
    3. Task body: target file wins wholesale if present; else default's pre-marker body
    4. Output contract: always from orchestrator default (split on OUTPUT_CONTRACT_MARKER)
    5. Runtime context (labeled key: value lines)

    Raises:
        FileNotFoundError: if the orchestrator default task file is missing (required
            for the output contract).
    """
    parts: list[str] = []

    # 1. Agent identity
    identity = registry.get("agents", {}).get(agent, {}).get("identity", "").strip()
    if identity:
        parts.append(identity)

    # 2. .agents.md cascade: orchestrator defaults first, then target
    for agents_md_path in [
        orchestrator_root / "defaults" / ".agents.md",
        target_root / ".agents.md",
    ]:
        if agents_md_path.exists():
            extracted = _extract_agents_md_sections(
                agents_md_path.read_text(encoding="utf-8"), agent
            )
            if extracted:
                parts.append(extracted)

    # 3 & 4. Task body + output contract
    default_task_file = orchestrator_root / "defaults" / "prompts" / agent / f"{task}.md"
    if not default_task_file.exists():
        raise FileNotFoundError(
            f"Default task file missing for agent '{agent}', task '{task}': {default_task_file}"
        )

    default_content = default_task_file.read_text(encoding="utf-8")
    if OUTPUT_CONTRACT_MARKER in default_content:
        default_pre, default_post = default_content.split(OUTPUT_CONTRACT_MARKER, 1)
    else:
        default_pre = default_content
        default_post = ""

    target_task_file = target_root / "prompts" / agent / f"{task}.md"
    if target_task_file.exists():
        target_content = target_task_file.read_text(encoding="utf-8")
        # Strip any output-contract marker from target; the default's contract always wins.
        if OUTPUT_CONTRACT_MARKER in target_content:
            task_body = target_content.split(OUTPUT_CONTRACT_MARKER, 1)[0].strip()
        else:
            task_body = target_content.strip()
    else:
        task_body = default_pre.strip()

    if task_body:
        parts.append(task_body)

    # Output contract is always appended from the orchestrator default (PRD H2).
    if default_post:
        parts.append(OUTPUT_CONTRACT_MARKER)
        parts.append(default_post.strip())

    # 5. Runtime context
    if context:
        context_lines = "\n".join(f"{k}: {v}" for k, v in context.items())
        parts.append(f"## Runtime Context\n\n{context_lines}")

    return "\n\n".join(parts)
