import asyncio
import sys
import warnings

warnings.filterwarnings("ignore", message="Core Pydantic V1 functionality")

from dotenv import load_dotenv  # noqa: E402
from orchestrator.orchestrator import SpadesOrchestrator  # noqa: E402

load_dotenv()


async def main():
    orchestrator = SpadesOrchestrator()
    args = sys.argv[1:]

    if not args:
        # Production mode: python main.py
        print("Running in production mode — starting continuous loop")
        await orchestrator.run_forever()

    elif len(args) >= 1 and args[0].isdigit():
        issue_number = int(args[0])
        max_attempts = None
        decision = None

        for arg in args[1:]:
            if arg.isdigit():
                max_attempts = int(arg)
            elif arg in ("approve", "proceed"):
                decision = arg
            else:
                print(f"Error: unrecognised argument '{arg}'")
                _print_usage()
                sys.exit(1)

        if decision:
            print(f"Resuming issue #{issue_number} with decision: {decision}")
            result = await orchestrator.resume_issue(issue_number, decision=decision)
        elif max_attempts:
            print(f"Running in test mode — processing issue #{issue_number} "
                  f"with up to {max_attempts} attempts")
            result = await orchestrator.process_issue(issue_number, max_attempts=max_attempts)
        else:
            print(f"Running in test mode — processing issue #{issue_number}")
            result = await orchestrator.process_issue(issue_number)

        print(f"\n{'='*60}")
        print(f"Result: {result}")
        print(f"{'='*60}")

    else:
        _print_usage()
        sys.exit(1)


def _print_usage():
    print("Usage:")
    print("  python main.py                       # production: continuous loop")
    print("  python main.py <issue>               # process a single issue")
    print("  python main.py <issue> <attempts>    # process with custom max attempts")
    print("  python main.py <issue> approve       # approve complexity breakdown")
    print("  python main.py <issue> proceed       # skip breakdown, implement anyway")
    print()
    print("Examples:")
    print("  python main.py 601 approve           # approve decomposition of #601")
    print("  python main.py 601 proceed           # force implementation of #601 as-is")
    print("  python main.py 603 10                # process #603 with 10 attempts")


if __name__ == "__main__":
    asyncio.run(main())
