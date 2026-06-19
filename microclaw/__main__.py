import sys
import datetime
import cProfile, pstats, io
from pstats import SortKey

from microclaw.agent import run_agent
from microclaw.controller import MicroscopeController
from microclaw.config import load_safety_config
from microclaw.safety import SafetyGuard

from anthropic._utils._json import openapi_dumps


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Microclaw: AI agent for Micro-Manager")
    parser.add_argument("--safety-config", default="safety_config.yaml")
    parser.add_argument("--port", type=int, default=4827)
    parser.add_argument("--profile", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--save-history", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    constraints = load_safety_config(args.safety_config)
    guard = SafetyGuard(constraints)

    print("Connecting to Micro-Manager...")
    ctrl = MicroscopeController(port=args.port)
    if not ctrl.is_connected():
        sys.exit(
            "Could not connect to Micro-Manager. "
            "Is the ZMQ server enabled in Tools → Options?"
        )
    print("Connected. Type your instructions (type 'exit' or press Ctrl-C to quit).\n")

    history = []
    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting.")
            break
        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit"}:
            print("Exiting.")
            break

        if args.profile:
            # enable profiling
            pr = cProfile.Profile()
            pr.enable()
        
            reply, history = run_agent(user_input, ctrl, guard, history)
            print(f"\nMicroclaw: {reply}\n")
            
            # print profiling
            pr.disable()
            s = io.StringIO()
            sortby = SortKey.TIME
            ps = pstats.Stats(pr, stream=s).sort_stats(sortby)
            ps.print_stats(20)
            print(s.getvalue())
        else:
            reply, history = run_agent(user_input, ctrl, guard, history)
            print(f"\nMicroclaw: {reply}\n")

    if args.save_history:
        # save chat history
        with open(f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_microclaw_history.json", "wb") as f:
            f.write(openapi_dumps(history))


if __name__ == "__main__":
    main()
