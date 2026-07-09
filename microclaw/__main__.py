import sys
import json
import datetime
import cProfile
import os
import pstats
import io
import subprocess
import tempfile
import webbrowser
from importlib import resources
from pathlib import Path
from pstats import SortKey

from microclaw.agent import run_agent
from microclaw.assets import load_page
from microclaw.controller import MicroscopeController
from microclaw.config import load_safety_config_or_exit
from microclaw.paths import default_safety_config
from microclaw.safety import SafetyGuard


def json_default(o):
    """Encode the Anthropic SDK content blocks that history holds.

    Via the public model_dump() rather than the private
    anthropic._utils._json.openapi_dumps (which can vanish across SDK releases).
    """
    return o.model_dump() if hasattr(o, "model_dump") else str(o)


def write_history(fn, history, save=True):
    if not save:
        return
    Path(fn).write_text(
        json.dumps(history, default=json_default, indent=2), encoding="utf-8"
    )


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

    # Inlines transcript.css/transcript.js — the emitted file lives in a temp
    # directory and can't resolve them as siblings.
    template = load_page("history_viewer.html")
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


def _open_in_editor(path):
    """Show `path` to the user in whatever edits text on this machine.

    os.startfile raises if the extension has no registered handler, and nothing
    in a base Windows install claims .yaml (design/17 spike Q6 found VS Code
    only because that box has it). An unhandled OSError here would abort `init`
    at exactly the moment the user needs the file in front of them.
    """
    try:
        if sys.platform == "win32":
            os.startfile(path)  # noqa: S606 — the path is ours, not user input
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-t", str(path)])
        else:
            subprocess.Popen([os.environ.get("EDITOR") or "xdg-open", str(path)])
    except (OSError, AttributeError):
        if sys.platform == "win32":
            subprocess.Popen(["notepad.exe", str(path)])
        else:
            print(f"Open this file in an editor: {path}")


def init(args):
    """Create the per-user safety config, and put it in front of the user.

    Deliberately copies the example *unedited*, `reviewed: false` and all: the
    limits it ships are fictional, and the only way past the gate in
    `load_safety_config` is for a human to read the file and change that line.
    """
    dest = Path(args.path) if args.path else default_safety_config()
    if dest.exists() and not args.force:
        print(f"Already present: {dest}")
        print("Pass --force to overwrite it with a fresh copy of the example.")
        if not args.no_edit:
            _open_in_editor(dest)
        return dest

    example = resources.files("microclaw").joinpath("safety_config.example.yaml")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")

    print(f"Wrote {dest}\n")
    print("These limits are the example's. They match no real microscope, and")
    print("Microclaw will refuse to start until you have edited them for this")
    print("instrument and set `reviewed: true` at the top of the file.")
    if not args.no_edit:
        _open_in_editor(dest)
    return dest


def run_session(args):
    """Interactive agent loop against a live Micro-Manager instance."""
    # No --safety-config means the per-user default that `microclaw init` writes,
    # which is what a desktop shortcut loads. Either way the file must carry
    # `reviewed: true`, so a session still cannot start under the example's
    # fictional limits (design/14 §6, design/17 v2).
    constraints = load_safety_config_or_exit(args.safety_config)
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
            "Path to THIS RIG's safety-limits YAML. Defaults to the per-user file "
            f"`microclaw init` writes ({default_safety_config()}). Either way it "
            "must carry `reviewed: true`."
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

    # Subcommands take no session flags of their own: the agent-session options
    # above live on the top-level parser, so they must precede the subcommand
    # (`microclaw --safety-config x.yaml serve`). Repeating them on the
    # subparser would let its defaults silently clobber what was passed there.
    sub = parser.add_subparsers(dest="command")

    it = sub.add_parser(
        "init",
        help="Create this machine's safety-limits file and open it for editing.",
        description=(
            "Copies the example safety config to a per-user location and opens it. "
            "Its limits are fictional: edit them for this microscope and set "
            "`reviewed: true`, or Microclaw will refuse to start."
        ),
    )
    it.add_argument("--path", default=None, help="Write somewhere other than the default.")
    it.add_argument("--force", action="store_true", help="Overwrite an existing file.")
    it.add_argument("--no-edit", action="store_true", help="Don't open an editor.")

    sv = sub.add_parser(
        "serve",
        help="Serve the interactive web GUI on localhost.",
        description=(
            "Drive Microclaw from a browser. Binds 127.0.0.1 only — this "
            "endpoint moves real hardware."
        ),
    )
    sv.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1).")
    sv.add_argument("--web-port", type=int, default=8000, help="HTTP port (default: 8000).")
    sv.add_argument(
        "--no-browser",
        action="store_true",
        help="Print the URL instead of opening a browser window.",
    )
    sv.add_argument(
        "--allow-remote",
        action="store_true",
        help=(
            "Permit binding beyond localhost. Exposes microscope control on the "
            "network — only on a trusted, isolated LAN."
        ),
    )

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

    if args.command == "init":
        init(args)
        return

    if args.command == "view-history":
        view_history(args.path, open_browser=not args.no_browser)
        return

    if args.command == "serve":
        # Imported here so `view-history` and the REPL don't need fastapi.
        try:
            from microclaw.webserve import serve
        except ImportError:
            sys.exit(
                "`microclaw serve` needs fastapi and uvicorn: pip install 'microclaw[serve]'"
            )
        serve(args)
        return

    run_session(args)


if __name__ == "__main__":
    main()
