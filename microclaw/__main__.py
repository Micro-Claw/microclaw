import sys
import json
import datetime
import cProfile
import pstats
import io
import tempfile
import webbrowser
from importlib import resources
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

    Path(fn).write_text(json.dumps(history, default=default, indent=2), encoding="utf-8")


def view_history(path, open_browser=True):
    """Render a saved history JSON with the bundled HTML viewer."""
    src = Path(path)
    if not src.exists():
        sys.exit(f"History file not found: {src}")
    try:
        # utf-8 explicit: histories contain µm, →, etc., and the platform
        # default is cp1252 on Windows (design/14 knowledge-base bug).
        history = json.loads(src.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        sys.exit(f"Not valid JSON ({src}): {e}")

    template = resources.files("microclaw").joinpath("history_viewer.html").read_text(
        encoding="utf-8"
    )
    # Embed as JSON text inside a <script type="application/json"> block. Escape
    # '<' so a '</script>' hiding in any string can't terminate that block early;
    # JSON.parse turns the < escapes back into '<' browser-side.
    data = json.dumps(history).replace("<", "\\u003c")
    html = template.replace("__MICROCLAW_HISTORY_DATA__", data)

    out = Path(tempfile.gettempdir()) / f"{src.stem}_view.html"
    out.write_text(html, encoding="utf-8")
    print(f"Wrote viewer: {out}")
    if open_browser:
        webbrowser.open(out.as_uri())
    else:
        print(f"Open it in a browser: {out.as_uri()}")
    return out


def run_session(args):
    """Interactive agent loop against a live Micro-Manager instance."""
    # Enforced here rather than as an argparse `required` flag: --safety-config
    # lives on the top-level parser (so `microclaw --port ...` still launches a
    # session), but making it required there would also force it on the
    # `view-history` subcommand, which touches no hardware. A session still
    # refuses to start without an explicit config — the repo ships only
    # safety_config.example.yaml, whose limits match no real rig (design/14 §6).
    if not args.safety_config:
        sys.exit(
            "A session requires --safety-config PATH. Copy "
            "safety_config.example.yaml and edit it for THIS rig; the example's "
            "limits match no real hardware."
        )
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

    # run_agent returns a fresh list each turn; _repl mutates this one in place
    # (history[:] = ...) so the finally block always sees the latest turns.
    history = []
    # try/finally so EVERY exit path — 'exit', Ctrl-C, or a crash inside
    # run_agent — writes the history AND shutters known illumination
    # (design/14 §3: a session once ended with a 638 nm laser left at 25%).
    try:
        _repl(args, ctrl, guard, history, history_fn_name)
    finally:
        write_history(history_fn_name, history, args.save_history)
        shuttered = guard.shutter_all(ctrl.core)
        if shuttered:
            print(f"[microclaw] Illumination off: {', '.join(shuttered)}")


def _repl(args, ctrl, guard, history, history_fn_name):
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

            reply, new_history = run_agent(user_input, ctrl, guard, history, model=args.model)
            print(f"\nMicroclaw: {reply}\n")

            # print profiling
            pr.disable()
            s = io.StringIO()
            sortby = SortKey.TIME
            ps = pstats.Stats(pr, stream=s).sort_stats(sortby)
            ps.print_stats(20)
            print(s.getvalue())
        else:
            reply, new_history = run_agent(user_input, ctrl, guard, history, model=args.model)
            print(f"\nMicroclaw: {reply}\n")

        # In-place so main()'s finally block sees the same list.
        history[:] = new_history

        # Now write once per loop, in case it crashes
        write_history(history_fn_name, history, args.save_history)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Microclaw: AI agent for Micro-Manager")
    # Agent-session options stay on the top-level parser so `microclaw --port ...`
    # (no subcommand) keeps launching a session, as before.
    parser.add_argument(
        "--safety-config",
        default=None,
        help=(
            "Path to THIS RIG's safety-limits YAML (copy safety_config.example.yaml "
            "and edit). Required to launch a session; not needed for view-history."
        ),
    )
    parser.add_argument("--port", type=int, default=4827)
    parser.add_argument(
        "--model",
        default=None,
        help="Anthropic model id (default: $MICROCLAW_MODEL or the built-in default).",
    )
    parser.add_argument("--profile", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--save-history", action=argparse.BooleanOptionalAction, default=True)

    sub = parser.add_subparsers(dest="command")
    vh = sub.add_parser(
        "view-history",
        help="Open a saved *_microclaw_history.json in the browser transcript viewer.",
    )
    vh.add_argument("path", help="Path to a saved history JSON file.")
    vh.add_argument(
        "--no-browser",
        action="store_true",
        help="Write the viewer HTML and print its path instead of opening a browser.",
    )

    args = parser.parse_args()

    if args.command == "view-history":
        view_history(args.path, open_browser=not args.no_browser)
        return

    run_session(args)


if __name__ == "__main__":
    main()
