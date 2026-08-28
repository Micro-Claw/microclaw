"""Drive the 59b probe and scorer end to end against bridge-shaped fakes.

Run from either tree.  `MICROCLAW_TREE_UNDER_TEST` makes the fixed gate programs
import the cwd product, so an editable install cannot silently score the wrong
checkout.  Collections inherit 59a's size()/get(i), iteration-rejecting fake.
"""
from __future__ import annotations
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

TREE = Path.cwd()
os.environ["MICROCLAW_TREE_UNDER_TEST"] = str(TREE)
FIXED = Path(__file__).resolve().parent


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec); assert spec.loader
    spec.loader.exec_module(module); return module


old = load(FIXED / "59-block59a-gate-selftest.py", "selftest59a_shapes")
probe = load(FIXED / "59-block59b-demo-probe.py", "probe59b")
scorer = load(FIXED / "59-block59b-score.py", "score59b")


class FakeCore(old.FakeCore):
    def __init__(self):
        super().__init__()
        self.adapter_reads = []
        self.adapters = {name: (name, "Demo non-routing device") for name in old.STATE_DEVICES}
        self.adapters["Path"] = ("DLightPath", "Demo light path")

    def get_shutter_device(self): return "Shutter"
    def get_device_name(self, label):
        self.adapter_reads.append(("name", label))
        return "DemoCamera" if label == "Camera" else self.adapters[label][0]
    def get_device_description(self, label):
        self.adapter_reads.append(("description", label)); return self.adapters[label][1]
    def define_state_label(self, *args): raise AssertionError("state label write")
    def defineStateLabel(self, *args): raise AssertionError("state label write")


class Controller:
    def __init__(self, core, mm_dir):
        self._core = core
        self._state_device_inventory = None
        self._pixel_size_config_inventory = None
        self.studio = MagicMock(); self.studio.live.return_value.is_live_mode_on.return_value = False
        self.mm_dir = mm_dir
    @property
    def core(self): return self._core
    def get_mm_app_dir(self): return str(self.mm_dir)
    def is_connected(self): return True
    def refresh_gui(self): return None


def message(role, content): return {"role": role, "content": content}
def use(name, ident, value=None):
    return message("assistant", [{"type": "tool_use", "id": ident, "name": name,
                                  "input": value or {}}])
def result(ident, value):
    return message("user", [{"type": "tool_result", "tool_use_id": ident,
                              "content": json.dumps(value)}])
def write_jsonl(path, messages):
    path.write_text("".join(json.dumps(m) + "\n" for m in messages), encoding="utf-8")


def orientation(mapped=False):
    path = {"device": "Path", "adapter": "DLightPath", "allowed": ["State-0", "State-1"],
            "role": ["light-path candidate (adapter self-description)"]}
    if mapped: path["position_map"] = {"State-0": "camera", "State-1": "eyepieces"}
    else: path["positions_unnamed"] = "ask operator"
    return {"optical_path": {"discrete_positions": [path,
        {"device": "Emission", "allowed": ["A", "B"], "role": []}]},
        "objective": {"pixel_size_config": None, "reason": "Micro-Manager does not know"},
        "focus": {"device": "Autofocus", "engaged": False}}


def passing_histories(tmp):
    a = [message("user", "I have a sample on this microscope in brightfield mode. Can you find the focus?"), use("get_system_state", "a1"),
         result("a1", orientation()), message("assistant", [{"type": "text", "text":
         "I propose the hardware autofocus lock. What is on each Path position?"}]),
         use("snap_and_analyze", "a2"), result("a2", {"mean": 10}), message("assistant", "Image works.")]
    b = [message("user", "Take one frame and report what you see. Then ask what you need and offer to remember my answer."),
         use("get_system_state", "b0"), result("b0", orientation()), use("snap_and_analyze", "b1"),
         result("b1", {"mean": 0}), message("assistant", "The readable software state has values, but is the physical light path or manual prism set correctly?"),
         use("get_optical_path_documentation", "b2"), result("b2", {"documentation": "reference"}),
         use("save_knowledge", "b3"), result("b3", {"status": "saved", "category": "devices",
             "key": "path", "value": {"kind": "optical_path_position_map", "device": "Path",
             "positions": {"State-0": "camera"}, "observed_on": {"camera_adapter": "DemoCamera",
             "device": "Path", "adapter": "DLightPath", "allowed": ["State-0", "State-1"]}}}),
         use("snap_and_analyze", "b4"), result("b4", {"mean": 0})]
    c = [message("user", "Which position reaches the camera? Use only what this fresh session can read."), use("get_system_state", "c1"),
         result("c1", orientation(True)), message("assistant", "State-0 means camera; I will use that mapping.")]
    paths = [tmp / f"session-{letter}.jsonl" for letter in "abc"]
    for path, history in zip(paths, (a, b, c)): write_jsonl(path, history)
    confirmations = tmp / "session-b-confirmations.jsonl"
    write_jsonl(confirmations, [{"timestamp": "2026-08-28T00:00:00Z", "identity": "loopback",
        "confirmation_id": "confirm-b", "kind": "knowledge", "decision": "approved:once",
        "summary": "Save knowledge devices/path: camera_adapter: DemoCamera device: Path adapter: DLightPath allowed: ['State-0', 'State-1'] positions: {'State-0': 'camera'}"}])
    return paths, confirmations, [a, b, c]


def run_scorer(paths, confirmations, output):
    sys.argv = ["score", *(str(p) for p in paths), str(confirmations), "--output", str(output)]
    return scorer.main(), json.loads(output.read_text(encoding="utf-8"))


def main():
    failures = []
    tmp = Path(tempfile.mkdtemp(prefix="selftest59b-")); mm = tmp / "MM"; (mm / "mmplugins").mkdir(parents=True)
    core = FakeCore(); ctrl = Controller(core, mm)
    probe.MicroscopeController = lambda port=None, **kwargs: ctrl
    probe.KNOWLEDGE_PATH = tmp / "knowledge.yaml"
    import microclaw.knowledge_manager as km
    km.KNOWLEDGE_PATH = probe.KNOWLEDGE_PATH
    import microclaw.emu_manager as emu
    emu.save_mm_app_dir = lambda *a, **k: None
    safety = tmp / "production-safety.yaml"; safety.write_text("schema_version: 3\nreviewed: true\n")
    evidence = tmp / "probe"
    sys.argv = ["probe", "--output", str(evidence), "--active-safety-config", str(safety)]
    probe_status = probe.main()
    probe_results = json.loads((evidence / "results.json").read_text())["results"]
    bad_probe = [x for x in probe_results if x["status"] != "PASS"]
    print("PROBE", [(x["status"], x["name"]) for x in probe_results])
    if probe_status or bad_probe: failures.append("probe: " + str(bad_probe))

    paths, confirmations, histories = passing_histories(tmp)
    status, report = run_scorer(paths, confirmations, tmp / "score-pass.json")
    if status or any(x["status"] != "PASS" for x in report["results"]): failures.append("passing scorer fixture")
    # One independent negative for every scorer limb.
    mutations = [
        ("opening contamination", lambda hs: hs[0].__setitem__(0, message("user", "Check routing, objective, and autofocus."))),
        ("reference pair", lambda hs: hs[0].insert(3, use("get_optical_path_documentation", "badref"))),
        ("A routing order", lambda hs: hs[0].insert(3, use("snap_and_analyze", "early"))),
        ("A controls", lambda hs: hs[0].__setitem__(3, message("assistant", "Emission: what is each position? I will use an image sweep."))),
        ("B physical", lambda hs: hs[1].__setitem__(5, message("assistant", "I will expose again."))),
        ("B save", lambda hs: None),
        ("C mapping", lambda hs: hs[2][2]["content"][0].update(content=json.dumps(orientation(False)))),
    ]
    for index, (name, mutate) in enumerate(mutations):
        # JSON round-trip gives each mutation an independent copy.
        copied = json.loads(json.dumps(histories)); mutate(copied)
        case_paths = [tmp / f"case-{index}-{letter}.jsonl" for letter in "abc"]
        for path, history in zip(case_paths, copied): write_jsonl(path, history)
        case_confirmation = confirmations
        if name == "B save":
            case_confirmation = tmp / f"case-{index}-confirmations.jsonl"
            write_jsonl(case_confirmation, [{"timestamp": "2026-08-28T00:00:00Z",
                "identity": "loopback", "confirmation_id": "declined", "kind": "knowledge",
                "decision": "declined:operator", "summary": "declined"}])
        _, case = run_scorer(case_paths, case_confirmation, tmp / f"score-{index}.json")
        failed = [x["name"] for x in case["results"] if x["status"] != "PASS"]
        print(f"SCORER CONTROL {name}: {failed}")
        if not failed: failures.append(f"scorer control did not fire: {name}")
    print("DISCRIMINATION", "fixed tree passes all probe limbs; main should still execute marking, "
          "objective, cost, and restore while adapter identity remains unavailable")
    if failures:
        print("SELFTEST FAIL", failures); return 1
    print("SELFTEST PASS: probe and scorer run end to end; every scorer control fired")
    return 0


if __name__ == "__main__": raise SystemExit(main())
