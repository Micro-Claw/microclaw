"""Run a design/55 gate probe end to end against fakes, before a rig sees it.

Not a gate artifact and not part of the suite. It is how the coordinator finds
out whether a probe works, and it exists because `design/prompts.md` now carries
"running that probe against fakes on both trees before shipping it is the only
reason to trust it" as doctrine -- and doctrine with no instrument gets skipped
or re-derived. Block 9a lost six runbook steps to not having done this, and 52b
lost three rig trips to export tests that compiled a script and never ran it.

It replaces exactly three things: the config load, the controller, and the
live-rig authorization. Every tool call the probe makes is the real one, against
a real SafetyGuard built from a copy of the demo machine's constraints.

Run it on BOTH trees. The point is discrimination, not a green line:

    # the fixed tree -- expect PROBE PASS, exit 0
    python design/55-gate-probe-selftest.py /tmp/probefake

    # the pre-fix tree (git checkout <before> -- microclaw/), expect PROBE FAIL
    python design/55-gate-probe-selftest.py /tmp/probefake

For block 55a that read: pre-fix every case RAN, every dataset directory was
created and the exposure bracket said CHANGED; post-fix every case refused for
the message it was told to expect and both brackets were unchanged.

**Run it from inside the worktree under test**, or `import microclaw` resolves
through the editable install to whichever checkout `pip install -e .` last
pointed at -- which is how the first run of this file silently scored the wrong
tree:

    cd <worktree> && python3 -c "import sys; sys.path.insert(0, '.'); \
        exec(open('design/55-gate-probe-selftest.py').read())" /tmp/probefake

PROBE_PATH below names which probe to drive; point it at the block's own.
"""
import importlib.util, sys, types
from pathlib import Path
from unittest.mock import MagicMock

sys.argv = ["probe", "--device", "Aux Z", "--min", "0", "--max", "200",
            "--save-root", sys.argv[1]]

from microclaw import tools
from microclaw.safety import (SafetyConstraints, SafetyGuard, StageConstraints,
                              NamedStageLimits, CameraConstraints)

constraints = SafetyConstraints(
    stage=StageConstraints(x_min=-5000, x_max=5000, y_min=-5000, y_max=5000,
                           z_min=0.0, z_max=100.0),
    named_stages=[NamedStageLimits("Aux Z", 0.0, 200.0)],
    camera=CameraConstraints(max_exposure_ms=1000.0),
)

class FakeCore:
    def __init__(self):
        self.position = {"Aux Z": 137.5}
        self.z = 42.0
        self.exposure = 25.0
        self.set_position_calls = []
    def get_position(self, device=None):
        return self.z if device is None else self.position[device]
    def set_position(self, device, value=None):
        self.set_position_calls.append((device, value))
        if value is None:
            self.z = device
        else:
            self.position[device] = value
    def get_exposure(self):
        return self.exposure
    def set_exposure(self, ms):
        self.exposure = float(ms)
    def __getattr__(self, name):
        return MagicMock()

core = FakeCore()
ctrl = MagicMock()
ctrl.core = core
ctrl.is_connected.return_value = True

parsed = types.SimpleNamespace(constraints=constraints)

import microclaw.config, microclaw.authorization, microclaw.controller
PROBE_PATH = "design/55-block55a-probe.py"
spec = importlib.util.spec_from_file_location("gate_probe", PROBE_PATH)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

probe.load_safety_config_or_exit = lambda path: parsed
probe.MicroscopeController = lambda **kw: ctrl
probe.validate_live_rig = lambda *a, **k: None

def fake_acquire(guard, save_dir, name, events, hook, **kw):
    target = Path(save_dir) / name
    target.mkdir(parents=True, exist_ok=True)
    return str(target)
tools._acquire_with_hooks = fake_acquire
tools._authorize_acquisition = lambda *a: MagicMock(has_overrun=False)

rc = probe.main()
print("")
print("PROBE EXIT CODE:", rc)
print("set_position calls made during the probe:", core.set_position_calls)
sys.exit(rc)
