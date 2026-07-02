import sys
import json
import datetime
import cProfile
import pstats
import io
from pathlib import Path
from pstats import SortKey

from microclaw.agent import run_agent
from microclaw.controller import MicroscopeController
from microclaw.config import load_safety_config
from microclaw.safety import SafetyGuard


def write_history(fn, history, save=True):
    if not save:
        return
    # History is a list of message dicts whose content blocks may be Anthropic
    # SDK objects. Serialise via the public model_dump() rather than the private
    # anthropic._utils._json.openapi_dumps (which can vanish across SDK releases).
    def default(o):
        return o.model_dump() if hasattr(o, "model_dump") else str(o)

    Path(fn).write_text(json.dumps(history, default=default, indent=2))

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Microclaw: AI agent for Micro-Manager")
    parser.add_argument("--safety-config", default="safety_config.yaml")
    parser.add_argument("--port", type=int, default=4827)
    parser.add_argument(
        "--model",
        default=None,
        help="Anthropic model id (default: $MICROCLAW_MODEL or the built-in default).",
    )
    parser.add_argument("--profile", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--save-history", action=argparse.BooleanOptionalAction, default=True)
    args = parser.parse_args()

    constraints = load_safety_config(args.safety_config)
    guard = SafetyGuard(constraints)

    print("Connecting to Micro-Manager...")
    ctrl = MicroscopeController(port=args.port, guard=guard)
    if not ctrl.is_connected():
        sys.exit(
            "Could not connect to Micro-Manager. "
            "Is the ZMQ server enabled in Tools → Options?"
        )
    print("Connected. Type your instructions (type 'exit' or press Ctrl-C to quit).\n")

    # we will overwrite this
    history_fn_name = f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}_microclaw_history.json"

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
        
            reply, history = run_agent(user_input, ctrl, guard, history, model=args.model)
            print(f"\nMicroclaw: {reply}\n")

            # print profiling
            pr.disable()
            s = io.StringIO()
            sortby = SortKey.TIME
            ps = pstats.Stats(pr, stream=s).sort_stats(sortby)
            ps.print_stats(20)
            print(s.getvalue())
        else:
            reply, history = run_agent(user_input, ctrl, guard, history, model=args.model)
            print(f"\nMicroclaw: {reply}\n")

        # Now write once per loop, in case it crashes
        write_history(history_fn_name, history, args.save_history)

    # final history
    write_history(history_fn_name, history, args.save_history)


if __name__ == "__main__":
    main()
