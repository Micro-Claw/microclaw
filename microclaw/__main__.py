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

from microclaw import credentials
from microclaw.authorization import RigAuthorizationError, validate_live_rig
from microclaw.assets import load_page
from microclaw.controller import MicroscopeController
from microclaw.config import (
    load_safety_config, load_safety_config_or_exit, validate_safety_config,
)
from microclaw.conversation import (
    AuditLog,
    ConversationStore,
    atomic_write_text,
    json_default,
    load_history,
    prune_transcripts,
)
from microclaw.paths import default_safety_config
from microclaw.safety import SafetyGuard

# Compatibility seam for tests/embedders. Restricted commands leave this as
# None; only the interactive REPL imports and installs the agent callable.
run_agent = None


def write_history(fn, history, save=True):
    """Atomically write a legacy array-format history.

    Kept for compatibility with callers outside the live session path. New
    sessions use :class:`AuditLog` JSONL and append each message immediately.
    """
    if not save:
        return
    atomic_write_text(
        fn, json.dumps(history, default=json_default, indent=2)
    )


def view_history(path, open_browser=True):
    """Render a saved history JSON with the bundled HTML viewer."""
    src = Path(path)
    if not src.exists():
        sys.exit(f"History file not found: {src}")
    try:
        # utf-8 explicit: histories contain µm, →, etc., and the platform
        # default is cp1252 on Windows (design/14 knowledge-base bug).
        loaded = load_history(src)
        history = loaded.messages
    except (json.JSONDecodeError, ValueError) as e:
        sys.exit(f"Not valid history ({src}): {e}")
    for notice in loaded.warnings:
        print(f"Warning: {notice}", file=sys.stderr)

    # Inlines transcript.css/transcript.js — the emitted file lives in a temp
    # directory and can't resolve them as siblings.
    template = load_page("history_viewer.html")
    # Embed as JSON text inside a <script type="application/json"> block. Escape
    # '<' so a '</script>' hiding in any string can't terminate that block early;
    # JSON.parse turns the < escapes back into '<' browser-side.
    data = json.dumps(history).replace("<", "\\u003c")
    html = template.replace("__MICROCLAW_HISTORY_DATA__", data)

    out = Path(tempfile.gettempdir()) / f"{src.stem}_view.html"
    atomic_write_text(out, html)
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
        print("Left as it is — `--force` overwrites it with a fresh copy of the example.")
        if not args.no_edit:
            # Re-running `init` is how you get back to the limits file; opening it
            # is the point. Say so, or the editor appearing looks like an
            # overwrite just happened.
            print("Opening it for editing.")
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


def install_shortcut(args):
    """Put a Microclaw launcher on the desktop (Windows), or explain why not."""
    from microclaw import shortcut

    if not shortcut.supported():
        # Exit 0, not a traceback: v4's installer runs this unconditionally, and a
        # developer on macOS has a terminal.
        print(
            "Desktop shortcuts are Windows only. "
            "On this platform, run `microclaw serve` from a terminal."
        )
        return

    try:
        if args.remove:
            gone = shortcut.remove(args.dest)
            for p in gone:
                print(f"Removed {p}")
            if not gone:
                print("Nothing to remove.")
            return

        p = shortcut.install(args.dest, dry_run=args.dry_run)
    except shortcut.ShortcutError as e:
        sys.exit(f"Could not install the shortcut: {e}")

    if args.dry_run:
        print("Would write:")
        print(f"  shortcut : {p['lnk']}")
        print(f"  wrapper  : {p['cmd']}")
        print(f"  icon     : {p['icon']}")
        print(f"  launching: {p['target']} {' '.join(p['args'])}")
        print(f"  from     : {p['workdir']}")
        return

    print(f"Installed {p['lnk']}")
    print("Double-click it to start Microclaw. Its console window is the server:")
    print("closing that window stops it.")


def run_session(args):
    """Interactive agent loop against a live Micro-Manager instance."""
    # No --safety-config means the per-user default that `microclaw init` writes,
    # which is what a desktop shortcut loads. Either way the file must carry
    # `reviewed: true`, so a session still cannot start under the example's
    # fictional limits (design/14 §6, design/17 v2).
    parsed_safety = load_safety_config_or_exit(args.safety_config)
    guard = SafetyGuard(parsed_safety.constraints)

    key, source = credentials.load_api_key()
    if key is None:
        sys.exit(
            "No Anthropic API key found. Looked in the ANTHROPIC_API_KEY "
            "environment variable, the system keyring, and the Microclaw credential file."
        )
    # Keep the agent import lazy for restricted commands, but use the same key
    # injection path as the browser server before any hardware contact.
    from microclaw.agent import set_api_key
    set_api_key(key)
    print(f"Anthropic API key: {credentials.mask(key)} (from {source})")

    print("Connecting to Micro-Manager...")
    ctrl = MicroscopeController(port=args.port, guard=guard)
    if not ctrl.is_connected():
        sys.exit(
            "Could not connect to Micro-Manager. "
            "Is the ZMQ server enabled in Tools → Options?"
        )
    try:
        validate_live_rig(ctrl, parsed_safety, guard=guard)
    except RigAuthorizationError as exc:
        sys.exit(str(exc))

    print("Connected. Type your instructions (type 'exit' or press Ctrl-C to quit).\n")

    history_fn_name = f"{datetime.datetime.now().strftime('%Y%m%d_%H%M%S_%f')}_microclaw_history.jsonl"
    removed = prune_transcripts(".", getattr(args, "history_retention_days", None))
    for path in removed:
        print(f"[microclaw] Pruned transcript: {path}")

    # run_agent returns a fresh list each turn; _repl mutates this one in place
    # (history[:] = ...) so the finally block always sees the latest turns.
    history = []
    store = ConversationStore(AuditLog(history_fn_name, enabled=args.save_history))
    # AuditLog writes as messages are produced. The finally ensures EVERY exit
    # path — 'exit', Ctrl-C, or a crash inside run_agent — shutters illumination
    # (design/14 §3: a session once ended with a 638 nm laser left at 25%).
    try:
        _repl(args, ctrl, guard, history, store)
    finally:
        shuttered = guard.shutter_all(ctrl.core)
        if shuttered:
            print(f"[microclaw] Illumination off: {', '.join(shuttered)}")


def _repl(args, ctrl, guard, history, store):
    global run_agent
    if run_agent is None:
        # Kept lazy so restricted commands such as first-launch setup never
        # import or expose the agent implementation.
        from microclaw.agent import run_agent as agent_runner
        run_agent = agent_runner
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

            reply, new_history = run_agent(
                user_input, ctrl, guard, history, model=args.model,
                context_provider=store.model_messages, on_message=store.append,
            )
            print(f"\nMicroclaw: {reply}\n")

            # print profiling
            pr.disable()
            s = io.StringIO()
            sortby = SortKey.TIME
            ps = pstats.Stats(pr, stream=s).sort_stats(sortby)
            ps.print_stats(20)
            print(s.getvalue())
        else:
            reply, new_history = run_agent(
                user_input, ctrl, guard, history, model=args.model,
                context_provider=store.model_messages, on_message=store.append,
            )
            print(f"\nMicroclaw: {reply}\n")

        # In-place so main()'s finally block sees the same list.
        history[:] = new_history

        # AuditLog already appended and flushed each produced message.


def print_authorization_map(args):
    """Connect, validate, print the map as JSON, and expose no mutation surface."""
    parsed_safety = load_safety_config_or_exit(args.safety_config)
    guard = SafetyGuard(parsed_safety.constraints)
    print("Connecting to Micro-Manager...", file=sys.stderr)
    ctrl = MicroscopeController(port=args.port, guard=guard)
    if not ctrl.is_connected():
        sys.exit(
            "Could not connect to Micro-Manager. "
            "Is the ZMQ server enabled in Tools → Options?"
        )
    try:
        # No guard is handed over: this path prints the map and exits without
        # exposing a mutation surface, so nothing needs the runtime allowlist.
        report = validate_live_rig(ctrl, parsed_safety)
    except RigAuthorizationError as exc:
        sys.exit(str(exc))
    print(json.dumps(report.to_dict(), indent=2, sort_keys=True))


def inspect_rig(args):
    """Connect for read-only discovery and write evidence under one directory."""
    from microclaw.rig_inventory import (
        compare_reviewed_config,
        enumerate_rig,
        write_inventory_outputs,
    )

    # Unlike authorization-map, absence of a safety config is intentional.  A
    # supplied reviewed file is parsed only to annotate a comparison after the
    # live facts have been collected.
    parsed_safety = load_safety_config(args.safety_config) if args.safety_config else None
    print("Connecting to Micro-Manager for read-only rig inspection...", file=sys.stderr)
    try:
        ctrl = MicroscopeController(port=args.port)
    except Exception:
        sys.exit(
            "Could not connect to Micro-Manager. "
            "Is the ZMQ server enabled in Tools → Options?"
        )
    if not ctrl.is_connected():
        sys.exit(
            "Could not connect to Micro-Manager. "
            "Is the ZMQ server enabled in Tools → Options?"
        )
    try:
        inventory = enumerate_rig(ctrl.core, mm_config=args.mm_config)
    except OSError as exc:
        sys.exit(f"Could not read Micro-Manager config {args.mm_config}: {exc}")
    if parsed_safety is not None:
        compare_reviewed_config(
            inventory, parsed_safety, str(Path(args.safety_config)), ctrl.core
        )
    inventory_path, review_path = write_inventory_outputs(inventory, args.out)
    print(f"Inventory: {inventory_path}")
    print(f"Review: {review_path}")


def check_config(args):
    """Present the reusable offline validator without connecting to a rig."""
    result = validate_safety_config(args.path or args.safety_config)
    print(f"Safety config: {result.path}")
    if result.parsed is not None:
        print("Schema: valid")
    if result.reviewed is True:
        print("Review: reviewed")
    for item in result.diagnostics:
        label = {
            "schema": "SCHEMA ERROR",
            "review": "REVIEW REQUIRED",
            "guaranteed_mode": "GUARANTEED-MODE REQUIREMENT",
            "degraded_mode": "DEGRADED-MODE WARNING",
            "live_check": "LIVE CHECK REQUIRED",
        }[item.kind]
        print(f"{label}: {item.message}")
    if result.can_start_live_validation:
        print(
            "Offline checks passed. This does not authorize the rig; run normal "
            "live startup next."
        )
        return
    print("Config is not ready for live startup.")
    raise SystemExit(1)


def first_launch_setup(args):
    """Enumerate through Core only, disconnect, interview, and write a draft."""
    from microclaw.first_launch import (
        CONTACT_ACKNOWLEDGEMENT, INTRO, InterviewTranscript, SetupRefusal,
        disconnect_core, interview, load_inventory, new_interview_transcript,
        write_profile,
    )
    from microclaw.rig_inventory import enumerate_rig, write_inventory_outputs

    target = Path(args.out)
    evidence_dir = Path(args.evidence_out or (str(target) + ".inventory"))
    transcript: InterviewTranscript | None = None
    try:
        if target.exists() and not args.force:
            raise SetupRefusal(
                f"SETUP REFUSAL: {target} already exists. Preserve the reviewed work, "
                "choose another --out path, or pass --force deliberately."
            )
        try:
            transcript = new_interview_transcript(evidence_dir)
        except OSError as exc:
            raise SetupRefusal(
                f"SETUP REFUSAL: Could not create interview evidence in "
                f"{evidence_dir}: {exc}"
            ) from exc
        transcript.say(INTRO)
        if args.inventory:
            inventory = load_inventory(args.inventory)
            inventory_path = Path(args.inventory)
            transcript.identify_inventory(inventory_path)
            transcript.say("Using an existing inspect-rig inventory; no hardware connection was opened.")
        else:
            from pycromanager import Core
            if args.mm_config:
                try:
                    Path(args.mm_config).read_bytes()
                except OSError as exc:
                    raise SetupRefusal(
                        f"SETUP REFUSAL: Could not read Micro-Manager config "
                        f"{args.mm_config}: {exc}"
                    ) from exc
            acknowledgement = transcript.ask(
                "To proceed with hardware-contacting enumeration, type exactly "
                f"{CONTACT_ACKNOWLEDGEMENT!r}: "
            ).strip()
            if acknowledgement != CONTACT_ACKNOWLEDGEMENT:
                raise SetupRefusal(
                    "SETUP REFUSAL: Hardware-contact acknowledgement did not match. "
                    "Exited without connecting to Micro-Manager or generating a profile."
                )
            transcript.say(
                "Connecting to the already-running Micro-Manager Core for read-only "
                "enumeration (no Studio, agent, or tool dispatcher)...",
                stream=sys.stderr,
            )
            core = None
            try:
                try:
                    core = Core(port=args.port)
                    # This query verifies the bridge and is also part of enumerate_rig.
                    core.get_version_info()
                except Exception as exc:
                    raise SetupRefusal(
                        "SETUP REFUSAL: Could not connect to the already-running "
                        f"Micro-Manager Core on port {args.port}: {exc}"
                    ) from exc
                try:
                    inventory = enumerate_rig(core, mm_config=args.mm_config)
                except Exception as exc:
                    raise SetupRefusal(
                        "SETUP REFUSAL: Read-only rig enumeration failed before a "
                        f"complete inventory could be produced: {exc}"
                    ) from exc
                try:
                    inventory_path, review_path = write_inventory_outputs(inventory, evidence_dir)
                except OSError as exc:
                    raise SetupRefusal(
                        f"SETUP REFUSAL: Could not write inventory evidence to "
                        f"{evidence_dir}: {exc}"
                    ) from exc
                transcript.identify_inventory(inventory_path)
                transcript.say(f"Inventory: {inventory_path}")
                transcript.say(f"Review: {review_path}")
            finally:
                if core is not None:
                    disconnect_core(core, args.port)
                    transcript.say("Disconnected from Micro-Manager before the interview.")
        config, notes = interview(inventory, ask=transcript.ask, say=transcript.say)
        write_profile(config, notes, target)
        transcript.say(f"Wrote unreviewed safety profile: {target}")
        transcript.say(
            "Disconnected. Manually review every declaration and limit, keep unsupported "
            "items excluded, then set `reviewed: true` and perform a normal restart. "
            "The generated profile has not been hot-loaded."
        )
    except (EOFError, KeyboardInterrupt):
        message = (
            "SETUP REFUSAL: The interview ended before every decision was answered. "
            "No profile was generated or loaded; rerun setup to start a complete interview."
        )
        if transcript is not None:
            transcript.outcome(message)
        sys.exit(message)
    except SetupRefusal as exc:
        if transcript is not None:
            transcript.outcome(str(exc))
        sys.exit(str(exc))
    finally:
        if transcript is not None:
            transcript.close()


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
    parser.add_argument(
        "--history-retention-days", type=int, default=None, metavar="DAYS",
        help=("Opt in to deleting JSONL transcripts older than DAYS at startup "
              "(default: keep everything)."),
    )

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

    sc = sub.add_parser(
        "install-shortcut",
        help="Put a Microclaw launcher on the desktop (Windows).",
        description=(
            "Writes a desktop shortcut that runs `microclaw serve` under this "
            "environment, with the Microclaw icon. It passes no other flags: the "
            "GUI it opens is loopback-only, under the safety limits in the config "
            "`microclaw init` wrote."
        ),
    )
    sc.add_argument("--dest", default=None, help="Write to a directory other than the desktop.")
    sc.add_argument("--dry-run", action="store_true", help="Print what would be written.")
    sc.add_argument("--remove", action="store_true", help="Delete a previously installed shortcut.")

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

    sub.add_parser(
        "authorization-map",
        help="Connect read-only, print the effective authorization map, and exit.",
        description=(
            "Connects only to enumerate and validate the effective authorization "
            "surface. It never constructs an agent, app, or mutation-tool dispatcher."
        ),
    )
    cc = sub.add_parser(
        "check-config",
        help="Validate a safety config without connecting to Micro-Manager.",
        description=(
            "Checks the strict document schema, review state, and offline-detectable "
            "guaranteed-mode requirements. It never connects to the rig."
        ),
    )
    cc.add_argument(
        "path", nargs="?", default=None,
        help="Config path (default: --safety-config or the per-user file).",
    )
    ir = sub.add_parser(
        "inspect-rig",
        help="Enumerate a rig read-only and write inventory evidence.",
        description=(
            "Connects only for read-only MMCore enumeration. It starts no agent, "
            "server, acquisition, plugin, or mutation-tool dispatcher. --mm-config "
            "identifies the already-loaded configuration; this command never applies it."
        ),
    )
    ir.add_argument("--mm-config", default=None, help="Path to the already-loaded MM .cfg (record/hash only).")
    ir.add_argument("--out", required=True, help="Directory for inventory.json and review.md.")
    fl = sub.add_parser(
        "first-launch-setup",
        help="Enumerate and interview for an unreviewed rig safety profile.",
        description=(
            "Restricted setup: Core-only read enumeration, explicit operator decisions, "
            "an unreviewed profile, disconnect, manual review, and normal restart. It "
            "constructs no agent, server, plugin, or mutation-tool dispatcher."
        ),
    )
    fl.add_argument("--out", required=True, help="Generated unreviewed safety YAML path.")
    fl.add_argument("--inventory", default=None, help="Consume an existing inspect-rig inventory.json without connecting.")
    fl.add_argument("--mm-config", default=None, help="Already-loaded MM .cfg path (record/hash only; never applied).")
    fl.add_argument("--evidence-out", default=None, help="Inventory evidence directory (default: <out>.inventory).")
    fl.add_argument("--force", action="store_true", help="Overwrite an existing output profile deliberately.")
    sv.add_argument(
        "--allow-remote",
        action="store_true",
        help=(
            "Permit binding beyond localhost. Requires --behind-tls-proxy and "
            "remote authentication."
        ),
    )
    sv.add_argument(
        "--behind-tls-proxy",
        action="store_true",
        help=(
            "Assert that a trusted TLS-terminating proxy is in front; trust its "
            "X-Forwarded-Proto: https header. The bind port must be reachable "
            "only by that proxy. Requires --allow-remote."
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

    # Before anything that can sys.exit(): a shortcut-spawned console closes the
    # instant the process does, so a refusal ("safety config unreviewed", "could
    # not connect") would flash past unread. No-op unless the .cmd wrapper ran.
    from microclaw.shortcut import pause_on_exit

    pause_on_exit()

    if args.command == "init":
        init(args)
        return

    if args.command == "install-shortcut":
        install_shortcut(args)
        return

    if args.command == "view-history":
        view_history(args.path, open_browser=not args.no_browser)
        return

    if args.command == "authorization-map":
        print_authorization_map(args)
        return

    if args.command == "inspect-rig":
        inspect_rig(args)
        return

    if args.command == "check-config":
        check_config(args)
        return

    if args.command == "first-launch-setup":
        first_launch_setup(args)
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
