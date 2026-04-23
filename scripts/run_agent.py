#!/usr/bin/env python3
"""
Run a single agent task from the command line.

Usage:
    python scripts/run_agent.py <agent-name> <task-name> [--input FILE] [--context KEY=VALUE ...]

Examples:
    python scripts/run_agent.py product-planner draft-prd --input some-brief.txt
    python scripts/run_agent.py product-planner propose-issues --input prd.md
    python scripts/run_agent.py product-planner draft-prd --context brief="A note-taking app for developers"

The --input file contents are injected as the primary context variable for the task:
  draft-prd       -> {{brief}}
  propose-issues  -> {{prd}}
  (other tasks)   -> {{input}}
"""
import argparse
import asyncio
import importlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# Maps agent name -> fully-qualified class path
_AGENT_CLASSES: dict[str, str] = {
    "product-planner": "agents.product_planner_agent.ProductPlannerAgent",
    "triager": "agents.triager_agent.TriagerAgent",
}

# Maps task name -> context variable name for --input
_TASK_INPUT_VARS: dict[str, str] = {
    "draft-prd": "brief",
    "propose-issues": "prd",
}


def _load_agent_class(agent_name: str):
    if agent_name not in _AGENT_CLASSES:
        known = ", ".join(sorted(_AGENT_CLASSES))
        print(f"Unknown agent: {agent_name!r}. Known agents: {known}", file=sys.stderr)
        sys.exit(1)
    module_path, class_name = _AGENT_CLASSES[agent_name].rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run one agent task end-to-end against the Claude CLI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("agent", help="Agent name (e.g. product-planner)")
    parser.add_argument("task", help="Task name (e.g. draft-prd, propose-issues)")
    parser.add_argument(
        "--input", "-i",
        metavar="FILE",
        help="Input file; contents injected as the task's primary context variable",
    )
    parser.add_argument(
        "--context", "-c",
        nargs="*",
        metavar="KEY=VALUE",
        help="Additional context variables (may be repeated)",
    )
    args = parser.parse_args()

    context: dict = {"task": args.task}

    if args.input:
        file_path = Path(args.input)
        if not file_path.exists():
            print(f"Input file not found: {args.input}", file=sys.stderr)
            sys.exit(1)
        content = file_path.read_text(encoding="utf-8")
        var = _TASK_INPUT_VARS.get(args.task, "input")
        context[var] = content

    for pair in args.context or []:
        if "=" not in pair:
            print(f"Warning: skipping --context {pair!r} (expected KEY=VALUE)", file=sys.stderr)
            continue
        k, v = pair.split("=", 1)
        context[k] = v

    AgentClass = _load_agent_class(args.agent)
    agent = AgentClass()
    result = asyncio.run(agent.run(context))
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
