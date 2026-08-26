import sys
import argparse
import json
import datetime
import cProfile
import os
import pstats
import io
import math
import subprocess
import tempfile
import webbrowser
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
    # No --safety-config means the reviewed per-user default that setup generates,
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
    confirmation_fn_name = history_fn_name.replace("_history.jsonl", "_confirmations.jsonl")
    confirmation_audit = AuditLog(confirmation_fn_name, enabled=args.save_history)
    from microclaw import tools
    tools.CONFIRM_AUDIT_FN = confirmation_audit.append
    # AuditLog writes as messages are produced. Every exit path — 'exit',
    # Ctrl-C, or a crash inside run_agent — reports declared illumination state
    # without changing it (design/38 F9 reverses design/14 §3).
    try:
        _repl(args, ctrl, guard, history, store)
    finally:
        tools.CONFIRM_AUDIT_FN = None
        report_declared_illumination_on_exit(guard, ctrl.core)


def report_declared_illumination_on_exit(guard, core, *, flush=False):
    """Print conspicuous non-off or unreadable declared properties on exit.

    Runs inside a `finally`, so it must never raise: an exception here would
    replace whatever ended the session — including the traceback the operator
    needs. Per-property read failures are already reported individually; this
    guards the enumeration itself.
    """
    if guard is None:
        return
    try:
        readings = guard.declared_illumination_state(core)
    except Exception as exc:                                    # noqa: BLE001
        print(
            "[microclaw] EXIT ILLUMINATION REPORT FAILED: "
            f"{type(exc).__name__}: {exc}. Rig illumination state is unverified.",
            flush=flush,
        )
        return
    for item in readings:
        name = f"{item['device']}.{item['property']}"
        if "error" in item:
            print(
                f"[microclaw] EXIT ILLUMINATION READ FAILED: {name}: {item['error']}",
                flush=flush,
            )
        elif item["value"] != item["off_value"]:
            print(
                f"[microclaw] EXIT ILLUMINATION NOT OFF: {name} = {item['value']!r} "
                f"(off_value={item['off_value']!r})",
                flush=flush,
            )


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
        if user_input.lower() == "grants":
            from microclaw import tools
            grants = tools.SESSION_GRANTS.active()
            if not grants:
                print("No active session grants.")
                continue
            print("Active session grants (terminal revocation is between turns):")
            for index, grant in enumerate(grants, 1):
                print(
                    f"  {index}. {grant['kind']}/{grant['subject']} "
                    f"(id {grant['id']})"
                )
            choice = input("Revoke which grant? [number/id, Enter to keep] ").strip()
            if not choice:
                continue
            grant_id = choice
            if choice.isdigit() and 1 <= int(choice) <= len(grants):
                grant_id = grants[int(choice) - 1]["id"]
            revoked = tools.SESSION_GRANTS.revoke(grant_id)
            if revoked is None:
                print("No active grant matched that number or id.")
                continue
            tools._stdin_decision_record(
                f"SESSION GRANT REVOKED: {revoked['kind']}/{revoked['subject']}",
                revoked["kind"], revoked["subject"],
                f"revoked:{revoked['id']}", grant_id=revoked["id"],
            )
            print(f"Revoked {revoked['kind']}/{revoked['subject']} session grant.")
            continue

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
            "example_limits": "EXAMPLE-LIMIT REVIEW",
            "plugin_motion": "HARDWARE-MOTION PLUGIN WARNING",
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


def check_bridge(args):
    """Exit successfully only after a bounded real bridge handshake and Core query."""
    from microclaw.bridge_check import probe_bridge

    ready, message = probe_bridge(args.port, args.bridge_timeout)
    print(message)
    if not ready:
        raise SystemExit(1)


def _positive_seconds(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("timeout must be a finite number greater than zero")
    return parsed


def main():
    parser = argparse.ArgumentParser(description="Microclaw: AI agent for Micro-Manager")
    # Agent-session options stay on the top-level parser so `microclaw --port ...`
    # (no subcommand) keeps launching a session, as before.
    parser.add_argument(
        "--safety-config",
        default=None,
        help=(
            "Path to THIS RIG's safety-limits YAML. Defaults to the per-user file "
            f"setup generates ({default_safety_config()}). Either way it "
            "must carry `reviewed: true`."
        ),
    )
    parser.add_argument(
        "--no-update-check", action="store_true",
        help="Skip this launch's managed-install update check.",
    )
    parser.add_argument("--port", type=int, default=4827)
    parser.add_argument(
        "--setup-write-security-config", action="store_true",
        help="Permit one confirmed write of setup security bounds (serve on loopback only).",
    )
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

    sc = sub.add_parser(
        "install-shortcut",
        help="Put a Microclaw launcher on the desktop (Windows).",
        description=(
            "Writes a desktop shortcut that runs `microclaw serve` under this "
            "environment, with the Microclaw icon. It passes no other flags: the "
            "GUI it opens is loopback-only, under the reviewed security bounds at "
            "the per-user path."
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
            "Checks the document schema and review state. It never connects to the "
            "rig, so it cannot check bounds against the stages this rig actually has."
        ),
    )
    cc.add_argument(
        "path", nargs="?", default=None,
        help="Config path (default: --safety-config or the per-user file).",
    )
    cb = sub.add_parser(
        "check-bridge",
        help="Check that the local Micro-Manager ZMQ bridge answers a real request.",
    )
    cb.add_argument(
        "--bridge-timeout", type=_positive_seconds, default=5.0, metavar="SECONDS",
        help="Maximum bridge handshake time (default: 5 seconds).",
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

    if args.setup_write_security_config and args.command != "serve":
        parser.error("--setup-write-security-config is valid only with serve")
    if args.setup_write_security_config and args.safety_config is not None:
        parser.error(
            "--setup-write-security-config targets only the per-user default; "
            "do not pass --safety-config"
        )
    if (args.setup_write_security_config and args.command == "serve"
            and args.host not in {"127.0.0.1", "localhost", "::1"}):
        parser.error("--setup-write-security-config is available only on a loopback bind")

    # Before anything that can sys.exit(): a shortcut-spawned console closes the
    # instant the process does, so a refusal ("safety config unreviewed", "could
    # not connect") would flash past unread. No-op unless the .cmd wrapper ran.
    from microclaw.shortcut import pause_on_exit

    pause_on_exit()

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

    if args.command == "check-bridge":
        check_bridge(args)
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
