"""Block 5 gate B4b: prove run_mda is refused live. Takes no exposure.

run_mda is fully dose-planned by Block 4 yet has no `acquisition-tool:` row and
sits behind an `excluded` mmstudio-mda path. This probe proves the exclusion
still holds at dispatch after Block 5 added the dose policy -- i.e. that the new
policy did not quietly become the thing that admits MDA.

Where possible it obtains a REAL preview token from get_mda_settings first, so
the refusal cannot be explained away as a stale-token rejection.
"""

import argparse
import json
import sys

from microclaw import tools
from microclaw.authorization import validate_live_rig
from microclaw.config import load_safety_config_or_exit
from microclaw.controller import MicroscopeController
from microclaw.safety import SafetyGuard


sys.stdout.reconfigure(line_buffering=True)
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--config", required=True)
parser.add_argument("--port", required=True, type=int)
args = parser.parse_args()

parsed = load_safety_config_or_exit(args.config)
guard = SafetyGuard(parsed.constraints)
ctrl = MicroscopeController(port=args.port, guard=guard)
if not ctrl.is_connected():
    raise SystemExit(f"FAIL: controller did not connect on port {args.port}")

report = validate_live_rig(ctrl, parsed, guard=guard)

mda_rows = [e.classification for e in report.entries if e.path == "mmstudio-mda"]
tool_row = any(e.path == "acquisition-tool:run_mda" for e in report.entries)
print("MAP", json.dumps({
    "verdict": report.verdict,
    "complete": report.complete,
    "mmstudio_mda": mda_rows,
    "acquisition_tool_run_mda_row": tool_row,
}, sort_keys=True))

# A real token if MMStudio is reachable; otherwise a placeholder. Either way the
# authorization gate runs before the token is examined.
token, token_kind = "no-token-available", "placeholder"
try:
    settings = tools.execute_tool("get_mda_settings", {}, ctrl, guard)
    payload = json.loads(settings) if isinstance(settings, str) else settings
    if isinstance(payload, dict) and payload.get("preview_token"):
        token, token_kind = payload["preview_token"], "real"
    else:
        token_kind = f"placeholder (get_mda_settings returned {str(payload)[:120]})"
except Exception as exc:  # MMStudio absent or unreachable
    token_kind = f"placeholder (get_mda_settings raised {type(exc).__name__}: {exc})"
print("TOKEN", json.dumps({"kind": token_kind}, sort_keys=True))

before = tools._acquisition_ledger(ctrl)
before_frames = before.frames

result = tools.execute_tool("run_mda", {"preview_token": token}, ctrl, guard)
print("RESULT", result)

after = tools._acquisition_ledger(ctrl)
print("LEDGER", json.dumps({
    "frames_before": before_frames,
    "frames_after": after.frames,
    "illuminated_ms_after": after.illuminated_ms,
}, sort_keys=True))

text = result if isinstance(result, str) else json.dumps(result)
refused = "mmstudio-mda" in text and "excluded" in text
unchanged = after.frames == before_frames and after.illuminated_ms == 0.0
print("VERDICT", "PASS" if (refused and unchanged and not tool_row) else "FAIL")
if not refused:
    print("  - run_mda was NOT refused for the excluded mmstudio-mda path")
if not unchanged:
    print("  - the ledger moved; something was reserved or acquired")
if tool_row:
    print("  - run_mda gained an acquisition-tool row; the dose policy admitted it")
