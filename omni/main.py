"""Command line: `python -m omni "your goal"` or just `python -m omni` for interactive mode."""
import argparse
import sys
from pathlib import Path

from . import config

config.load_env()

from . import dashboard  # noqa: E402
from .agent import Agent  # noqa: E402  (import after .env is loaded)
from .state import MissionState  # noqa: E402
from .providers import ProviderError, make_chain  # noqa: E402
from .ui import bold, cyan, red  # noqa: E402

BANNER = r"""
   ____  __  __ _   _ ___
  / __ \|  \/  | \ | |_ _|   general-purpose autonomous agent
 | |  | | |\/| |  \| || |    plan -> act -> verify -> reflect
 | |__| | |  | | |\  || |
  \____/|_|  |_|_| \_|___|   type 'exit' to quit
"""


def main():
    ap = argparse.ArgumentParser(prog="omni", description="Run an autonomous agent on a goal.")
    ap.add_argument("goal", nargs="*", help="What you want done. Leave empty for interactive mode.")
    ap.add_argument("-p", "--provider", help="anthropic | openai | grok | kimi | openrouter | ollama")
    ap.add_argument("-m", "--model", help="Model name (default depends on provider)")
    ap.add_argument("-s", "--max-steps", type=int, default=int(config.get("OMNI_MAX_STEPS", "40")))
    ap.add_argument("-w", "--workspace", default=config.get("OMNI_WORKSPACE", str(config.ROOT / "workspace")))
    ap.add_argument("-y", "--yes", action="store_true", help="Auto-approve shell/python/file actions (careful!)")
    ap.add_argument("-f", "--file", help="Read the goal from a text file")
    ap.add_argument("--no-dashboard", action="store_true", help="Don't start the live Mission Control page")
    ap.add_argument("--companion", nargs="?", const=10, type=float,
                    default=float(config.get("OMNI_COMPANION_MINUTES", "0") or 0),
                    help="Companion mode: Omni starts the conversation and checks in after N idle minutes (default 10)")
    ap.add_argument("--port", type=int, default=int(config.get("OMNI_DASHBOARD_PORT", "8765")))
    args = ap.parse_args()

    try:
        provider = make_chain(args.provider, args.model)
    except ProviderError as e:
        print(red(f"Setup problem: {e}"))
        sys.exit(1)

    state = MissionState()
    agent = Agent(provider, Path(args.workspace).resolve(), auto_approve=args.yes, max_steps=args.max_steps,
                  state=state)
    print(cyan(BANNER))
    backups = [p.model for p in getattr(provider, "providers", [])[1:]]
    print(f"  model: {provider.model}   workspace: {agent.ws}")
    if backups:
        print(f"  backups if it fails: {', '.join(backups)}")
    if agent.judge_provider is not provider:
        print(f"  blind judge: {agent.judge_provider.model} (a different model from the agent)")
    from .chess_game import ChessGame
    game = ChessGame(lambda: agent.reflect_provider, state)  # play chess with Omni in the dashboard
    if not args.no_dashboard:
        try:
            print(f"  mission control: {dashboard.start(state, args.port, workspace=agent.ws, game=game)}"
                  + ("   (chess board inside: play while I work)" if game.available() else ""))
        except OSError as e:
            print(red(f"  (dashboard not started: port {args.port} busy? {e})"))
    print()

    def go(goal):
        try:
            answer = agent.run(goal)
        except ProviderError as e:
            print(red(f"\nModel API error: {e}"))
            return
        except KeyboardInterrupt:
            print(red("\nStopped by you."))
            return
        print(bold("\n==================== FINAL ANSWER ====================\n"))
        print(answer)
        print(bold("\n======================================================\n"))

    if args.file:
        go(Path(args.file).read_text(encoding="utf-8"))
    elif args.goal:
        go(" ".join(args.goal))
    else:
        buddy = None
        if args.companion:
            from .companion import Companion
            buddy = Companion(provider, agent.ws, state, args.companion,
                              lambda t, prompt=True: print("\n" + bold(cyan("Omni: ")) + t + "\n" + (cyan("goal> ") if prompt else ""),
                                                           end="", flush=True))
            buddy.greet()
            buddy.start()
        while True:
            try:
                goal = input(cyan("goal> ")).strip()
            except (EOFError, KeyboardInterrupt):
                break
            if buddy:
                buddy.user_spoke()
            if goal.lower() in ("exit", "quit"):
                break
            if not goal:
                continue  # empty line: don't start an empty mission
            state.reset()  # fresh dashboard for each new mission
            game._publish()  # ...but keep the chess game going
            if buddy:
                buddy.busy = True
            go(goal)
            if buddy:
                buddy.busy = False
                buddy.user_spoke()


if __name__ == "__main__":
    main()
