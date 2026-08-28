"""Offline scorer for design/59 block 59b, against bridge-shaped fake rigs.

Run this file from inside the tree being scored.  It deliberately inserts cwd,
not this file's checkout, so the same program can discriminate the 59b branch
from main without an editable install deciding which package is imported.

Collections use the StrVector/DoubleVector shape from
design/59-block59a-gate-selftest.py: size()/get(i), with iteration forbidden.
A MagicMock or Python list would repeat the fixture defect that cost 59a a rig
trip.  The log is rewritten beside this program on every run.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path
from types import SimpleNamespace

TREE = Path.cwd()
sys.path.insert(0, str(TREE))
LOG = Path(__file__).with_suffix(".log")


class StrVector:
    def __init__(self, items): self._items = [str(item) for item in items]
    def size(self): return len(self._items)
    def get(self, index): return self._items[index]
    def __iter__(self): raise TypeError("'mmcorej_StrVector' object is not iterable")
    def __len__(self): raise TypeError("object of type 'mmcorej_StrVector' has no len()")


class DoubleVector(StrVector):
    def __init__(self, items): self._items = [float(item) for item in items]
    def __iter__(self): raise TypeError("'mmcorej_DoubleVector' object is not iterable")


class Setting:
    def __init__(self, device, prop, value): self._values = device, prop, value
    def get_device_label(self): return self._values[0]
    def get_property_name(self): return self._values[1]
    def get_property_value(self): return self._values[2]


class Configuration:
    def __init__(self, rules): self._rules = [Setting(*rule) for rule in rules]
    def size(self): return len(self._rules)
    def get_setting(self, index): return self._rules[index]


DEMO_STATES = {
    "Dichroic": ("400DCLP", ["400DCLP", "89402bs", "Q505LP", "Q585LP",
                                *[f"State-{n}" for n in range(4, 10)]]),
    "Emission": ("89402m", ["89402m", "Chroma-D460", "Chroma-HQ535",
                              "Chroma-HQ620", "Chroma-HQ700",
                              *[f"State-{n}" for n in range(5, 10)]]),
    "Excitation": ("Chroma-D360", ["Chroma-D360", "Chroma-HQ480",
                                     "Chroma-HQ570", "Chroma-HQ620", "Empty",
                                     *[f"State-{n}" for n in range(4, 9)]]),
    "LED": ("385nm", ["385nm", "470nm", "550nm", "635nm", "Closed",
                       *[f"State-{n}" for n in range(5, 10)]]),
    "Objective": ("Objective-2", ["Nikon 10X S Fluor", "Nikon 20X Plan Fluor ELWD",
                                    "Nikon 40X Plan Fluor ELWD", "Objective-2",
                                    "Objective-4", "Objective-5"]),
    "Path": ("State-0", ["State-0", "State-1", "State-2"]),
}
DEMO_ADAPTERS = {
    "Path": ("DLightPath", "Demo light path"),
    # The five real adapter strings were not captured.  These plausible,
    # deliberately non-routing values are not used as exact facts by any limb.
    "Dichroic": ("DemoDichroic", "Demo dichroic wheel"),
    "Emission": ("DemoEmission", "Demo emission wheel"),
    "Excitation": ("DemoExcitation", "Demo excitation wheel"),
    "LED": ("DemoLED", "Demo LED selector"),
    "Objective": ("DemoObjective", "Demo objective turret"),
}
DEMO_CONFIGS = {
    "Res10x": [("Objective", "Label", "Nikon 10X S Fluor")],
    "Res20x": [("Objective", "Label", "Nikon 20X Plan Fluor ELWD")],
    "Res40x": [("Objective", "Label", "Nikon 40X Plan Fluor ELWD")],
}
M5_STATES = {
    "Thorlabs ELL6": ("Position 0", ["Position 0", "Position 1"]),
    "Thorlabs Filter Wheel": ("Filter-1", [f"Filter-{n}" for n in range(1, 7)]),
    "Thorlabs Filter Wheel-1": ("Filter-1", [f"Filter-{n}" for n in range(1, 7)]),
    "iChrome-MLE-TCP": ("State-0", ["State-0", "State-1", "State-2"]),
}
M5_ADAPTERS = {
    "Thorlabs ELL6": ("Thorlabs ELL6", "Thorlabs ELL6"),
    "Thorlabs Filter Wheel": ("Thorlabs Filter Wheel", "Thorlabs filter wheel"),
    "Thorlabs Filter Wheel-1": ("Thorlabs Filter Wheel", "Thorlabs filter wheel"),
    "iChrome-MLE-TCP": ("iChrome-MLE-TCP", "iChrome-MLE"),
}


class FakeCore:
    """Scalar calls are counted; every Core collection is bridge-shaped."""
    def __init__(self, states, adapters, *, shutter="", autofocus="", camera="Camera",
                 camera_adapter="DemoCamera", configs=None):
        self.states = {k: (v[0], list(v[1])) for k, v in states.items()}
        self.adapters = dict(adapters)
        self.shutter, self.autofocus = shutter, autofocus
        self.camera, self.camera_adapter = camera, camera_adapter
        self.configs = configs or {}
        self.calls = 0
        self.adapter_reads = []
        self.raise_shutter = False

    def hit(self): self.calls += 1
    def get_loaded_devices(self): self.hit(); return StrVector(self.states)
    def get_device_type(self, device): self.hit(); return 4
    def get_shutter_device(self):
        self.hit()
        if self.raise_shutter: raise RuntimeError("shutter identity unreadable")
        return self.shutter
    def get_shutter_open(self): self.hit(); return False
    def get_auto_shutter(self): self.hit(); return False
    def get_allowed_property_values(self, device, prop):
        self.hit(); return StrVector(self.states[device][1])
    def get_property(self, device, prop): self.hit(); return self.states[device][0]
    def get_device_name(self, device):
        self.hit(); self.adapter_reads.append(("name", device))
        if device == self.camera: return self.camera_adapter
        return self.adapters[device][0]
    def get_device_description(self, device):
        self.hit(); self.adapter_reads.append(("description", device))
        return self.adapters[device][1]
    def get_available_pixel_size_configs(self): self.hit(); return StrVector(self.configs)
    def get_pixel_size_config_data(self, config): self.hit(); return Configuration(self.configs[config])
    def get_pixel_size_um_by_id(self, config): self.hit(); return 1.0
    def get_pixel_size_affine_by_id(self, config):
        self.hit(); return DoubleVector([0.5, 0, 0, 0.5, 0, 0])
    def get_current_pixel_size_config(self): self.hit(); return ""
    def get_pixel_size_um(self): self.hit(); return 0.0
    def get_auto_focus_device(self): self.hit(); return self.autofocus
    def is_continuous_focus_enabled(self): self.hit(); return False
    def get_device_property_names(self, device): self.hit(); return StrVector([])
    def is_property_read_only(self, device, prop): self.hit(); return True
    def get_x_position(self): self.hit(); return 0.0
    def get_y_position(self): self.hit(); return 0.0
    def get_position(self, device=None): self.hit(); return 0.0
    def get_exposure(self): self.hit(); return 10.0
    def get_camera_device(self): self.hit(); return self.camera
    def define_state_label(self, *args): raise AssertionError("state labels must not be written")
    def defineStateLabel(self, *args): raise AssertionError("state labels must not be written")


class Studio:
    def live(self): return self
    def is_live_mode_on(self): return False


class Score:
    def __init__(self): self.rows = []
    def limb(self, number, name, fn, *, missing=()):
        try:
            detail = fn()
            self.rows.append((number, "PASS", name, str(detail or "ok")))
        except (ImportError, AttributeError, FileNotFoundError) as exc:
            if missing and any(token in str(exc) for token in missing):
                self.rows.append((number, "NOT PRESENT", name, str(exc)))
            else:
                self.rows.append((number, "FAIL", name, f"{type(exc).__name__}: {exc}"))
        except Exception as exc:
            self.rows.append((number, "FAIL", name, f"{type(exc).__name__}: {exc}"))
    def check(self, condition, detail):
        if not condition: raise AssertionError(detail)
        return detail


def imports():
    from microclaw import agent, authorization, knowledge_manager, tools, tools_schema
    from microclaw.safety import SafetyConstraints, SafetyGuard, StageConstraints
    return agent, authorization, knowledge_manager, tools, tools_schema, SafetyGuard(
        SafetyConstraints(stage=StageConstraints(x_min=-1, x_max=1, y_min=-1,
                                                 y_max=1, z_min=-1, z_max=1)))


def controller(core, authorization):
    ctrl = SimpleNamespace(core=core, studio=Studio(), get_mm_app_dir=None)
    ctrl._state_device_inventory = authorization._build_state_device_inventory(
        core, authorization._strings(core.get_loaded_devices()))
    return ctrl


def payload(core, authorization, tools, guard):
    ctrl = controller(core, authorization)
    return tools.get_system_state(ctrl, guard), ctrl


def with_knowledge(knowledge_manager, data, fn):
    old = knowledge_manager.load_knowledge
    knowledge_manager.load_knowledge = lambda: data
    try: return fn()
    finally: knowledge_manager.load_knowledge = old


def run() -> tuple[Score, dict]:
    score = Score()
    try:
        agent, authorization, km, tools, schemas, guard = imports()
    except Exception as exc:
        print(f"IMPORT FAILURE: {type(exc).__name__}: {exc}")
        raise

    def reference():
        from microclaw.optics_docs import OPTICS_REFERENCE
        section = OPTICS_REFERENCE.split("## Usual path ordering", 1)[1].split("## What software", 1)[0]
        terms = ["how light paths usually work", "lamphouse", "inverted", "epi-illumination",
                 "TIRF", "spinning-disk", "multi-camera"]
        return score.check(all(t.lower() in section.lower() for t in terms), "ordering/caveat separated")
    score.limb(1, "reference ordering and adjacent stand caveat", reference,
               missing=("optics_docs",))

    def no_nikon():
        from microclaw.optics_docs import OPTICS_REFERENCE
        forbidden = [r"\bPFS\b", r"TIPFSStatus", r"PFSOffset",
                     r"\bTI(?:PFS|LightPath|NosePiece)\w*",
                     r"-?\d{3,4}\s*(?:to|–|-)\s*\+?\d{3,4}", r"\b(?:oil|water)\s+immersion\b"]
        hits = [p for p in forbidden if re.search(p, OPTICS_REFERENCE, re.I)]
        return score.check(not hits, f"forbidden reference matches: {hits}")
    score.limb(2, "reference contains no Nikon identifiers or rig numbers", no_nikon,
               missing=("optics_docs",))

    def tool_surface():
        fn = tools.TOOL_REGISTRY["get_optical_path_documentation"]
        schema_names = {item["name"] for item in schemas.TOOLS}
        return score.check((hasattr(fn, "_microclaw_emitter") or
                            getattr(fn, "_microclaw_emits_nothing", False)) and
                           "get_optical_path_documentation" in schema_names,
                           "registry, emits_nothing decorator, and schema entry present")
    score.limb(3, "documentation tool registry, decorator, schema parity", tool_surface,
               missing=("get_optical_path_documentation",))

    def hint_route():
        core = FakeCore(DEMO_STATES, DEMO_ADAPTERS, shutter="White Light Shutter",
                        autofocus="Autofocus", configs=DEMO_CONFIGS)
        hint = payload(core, authorization, tools, guard)[0]["optical_path"]["hint"]
        sentence = next(s for s in hint.split(". ") if "get_optical_path_documentation" in s)
        return score.check("not getting the signal" in sentence and
                           "when imaging is working there is nothing here to look up" in hint,
                           hint)
    score.limb(4, "hint routes reference only inside missing-signal condition", hint_route,
               missing=("get_optical_path_documentation",))

    score.limb(5, "prompt omits tool and names blank-frame optical_path once", lambda:
               score.check("get_optical_path_documentation" not in agent.SYSTEM_PROMPT and
                           agent.SYSTEM_PROMPT.count("optical_path and any declared_illumination_properties") == 1,
                           "tool absent; blank-frame optical_path instruction occurs once"))

    positives = ["Left80", "Eye100", "Camera-Port", "Eyepiece", "Trinocular",
                 "Sideport", "Leftport", "Frontport", "Bottomport", "Camport", "Phototube"]
    score.limb(6, "tokenizer matches routing tokens", lambda:
               score.check(all(tools._PORT_LABEL_WORDS.search(x) for x in positives),
                           str([x for x in positives if not tools._PORT_LABEL_WORDS.search(x)])))
    negatives = ["Brightfield", "Photoactivation", "Portrait", "Outside", "Photobleach"]
    def tokenizer_negative():
        hits = {x: tools._PORT_LABEL_WORDS.search(x).group(0)
                for x in negatives if tools._PORT_LABEL_WORDS.search(x)}
        return score.check(not hits, f"false positives and hit tokens: {hits}")
    score.limb(7, "tokenizer rejects embedded substrings", tokenizer_negative)

    def real_label_evidence():
        legacy = re.compile(
            r"(?:eye|ocular|binocular|camera|port|side|left|right|front|bottom|photo|tube)",
            re.I,
        )
        hits = {}
        for rig, states in (("demo", DEMO_STATES), ("M5", M5_STATES)):
            labels = [label for _, allowed in states.values() for label in allowed]
            hits[rig] = {
                "current": [label for label in labels if tools._PORT_LABEL_WORDS.search(label)],
                "main-substring": [label for label in labels if legacy.search(label)],
            }
        return score.check(not any(matches for rig in hits.values() for matches in rig.values()),
                           f"real-label matches under both matchers: {hits}")
    score.limb(8, "real demo and M5 labels do not discriminate tokenizer", real_label_evidence)

    demo = FakeCore(DEMO_STATES, DEMO_ADAPTERS, shutter="White Light Shutter",
                    autofocus="Autofocus", configs=DEMO_CONFIGS)
    demo_payload, demo_ctrl = payload(demo, authorization, tools, guard)
    m5 = FakeCore(M5_STATES, M5_ADAPTERS, camera="Camera",
                  camera_adapter="HamamatsuHam_DCAM")
    m5_payload, m5_ctrl = payload(m5, authorization, tools, guard)

    def roles():
        both_states = {"Both": ("Left80", ["Left80"])}
        both_adapters = {"Both": ("LightPathAdapter", "Light path selector")}
        labels_states = {"Labels": ("Eye100", ["Eye100"])}
        labels_adapters = {"Labels": ("GenericSwitch", "Generic selector")}
        both = payload(FakeCore(both_states, both_adapters), authorization, tools, guard)[0]
        labels = payload(FakeCore(labels_states, labels_adapters), authorization, tools, guard)[0]
        path = next(x for x in demo_payload["optical_path"]["discrete_positions"] if x["device"] == "Path")
        both_role = both["optical_path"]["discrete_positions"][0]["role"]
        label_role = labels["optical_path"]["discrete_positions"][0]["role"]
        return score.check(len(both_role) == 2 and len(path["role"]) == 1 and
                           "adapter" in path["role"][0] and len(label_role) == 1 and
                           "labels" in label_role[0], f"roles: {both_role}, {path['role']}, {label_role}")
    score.limb(9, "role sources: both, adapter-only, labels-only", roles)

    def device_label_negative():
        states = {"LightPath": ("A", ["A", "B"])}
        adapters = {"LightPath": ("FilterWheel", "Filter wheel")}
        entry = payload(FakeCore(states, adapters), authorization, tools, guard)[0]["optical_path"]["discrete_positions"][0]
        return score.check(entry["role"] == [], str(entry))
    score.limb(10, "device label LightPath is never a role heuristic", device_label_negative)

    def unnamed_and_map():
        path = next(x for x in demo_payload["optical_path"]["discrete_positions"] if x["device"] == "Path")
        port_core = FakeCore({"Port": ("Left80", ["Left80"])},
                             {"Port": ("Generic", "Generic selector")})
        port = payload(port_core, authorization, tools, guard)[0]["optical_path"]["discrete_positions"][0]
        condition = {"camera_adapter": "DemoCamera", "device": "Path",
                     "adapter": "DLightPath", "allowed": ["State-0", "State-1", "State-2"]}
        saved = {"kind": "optical_path_position_map", "device": "Path",
                 "positions": {"State-0": "camera"}, "observed_on": condition}
        mapped = with_knowledge(km, {"devices": {"path": saved}},
                                lambda: tools.get_system_state(demo_ctrl, guard))
        mapped_path = next(x for x in mapped["optical_path"]["discrete_positions"] if x["device"] == "Path")
        return score.check("positions_unnamed" in path and "positions_unnamed" not in port and
                           "position_map" in mapped_path and "positions_unnamed" not in mapped_path,
                           f"path={path}, port={port}, mapped={mapped_path}")
    score.limb(11, "positions_unnamed lifecycle", unnamed_and_map,
               missing=("position_map",))

    score.limb(12, "marking never filters any StateDevice", lambda:
               score.check({x["device"] for x in demo_payload["optical_path"]["discrete_positions"]}
                           == set(DEMO_STATES) - {"White Light Shutter"} and
                           all(x["allowed"] == DEMO_STATES[x["device"]][1]
                               for x in demo_payload["optical_path"]["discrete_positions"]),
                           "all demo StateDevices and exact allowed labels retained"))

    def shutters():
        demo_ok = demo_payload["optical_path"].get("shutter_exclusion", {}).get("device") == "White Light Shutter"
        m5_ok = (m5_payload["optical_path"].get("shutter_exclusion") is None and
                 m5_ctrl._state_device_inventory.get("shutter_exclusion")
                 == "no shutter device configured")
        raising = FakeCore(M5_STATES, M5_ADAPTERS); raising.raise_shutter = True
        raised = payload(raising, authorization, tools, guard)[0]["optical_path"]
        return score.check(demo_ok and m5_ok and len(raised["discrete_positions"]) == len(M5_STATES)
                           and raised["shutter_exclusion"] == "unknown",
                           f"demo={demo_payload['optical_path']}, m5={m5_payload['optical_path']}, raised={raised}")
    score.limb(13, "configured, empty, and unreadable shutter branches", shutters)

    def objective():
        obj = demo_payload["objective"]
        lives = [d["live"] for c in obj["available_configs"] for d in c["dependencies"]]
        return score.check(obj["pixel_size_config"] is None and obj["pixel_size_um"] == 0.0 and
                           lives == ["Objective-2"] * 3 and "measured objective" not in str(demo_payload).lower()
                           and "default" not in str(demo_payload).lower(), str(obj))
    score.limb(14, "unmatched objective is unknown and dependencies stay live", objective)

    def cost_for(states, adapters, **kwargs):
        core = FakeCore(states, adapters, **kwargs); ctrl = controller(core, authorization)
        startup_adapter_reads = list(core.adapter_reads)
        expected = {(kind, device) for device in states
                    for kind in ("name", "description")}
        if not expected <= set(startup_adapter_reads):
            missing = sorted(expected - set(startup_adapter_reads))
            raise AssertionError(f"inventory did not retain adapter metadata: missing {missing}")
        core.calls = 0; core.adapter_reads.clear()
        tools.get_system_state(ctrl, guard); first = core.calls; first_adapters = list(core.adapter_reads)
        tools.get_system_state(ctrl, guard); second = core.calls - first
        recurring_state_adapter = [x for x in core.adapter_reads[len(first_adapters):]
                                   if x[1] in states]
        if not second < first or recurring_state_adapter:
            raise AssertionError(f"first={first}, second={second}, recurring={recurring_state_adapter}")
        return first, second
    costs = {}
    def call_structure():
        costs["demo"] = cost_for(DEMO_STATES, DEMO_ADAPTERS, shutter="White Light Shutter",
                                 autofocus="Autofocus", configs=DEMO_CONFIGS)
        costs["M5"] = cost_for(M5_STATES, M5_ADAPTERS, camera_adapter="HamamatsuHam_DCAM")
        return f"bridge calls first/second: demo={costs['demo']}, M5={costs['M5']}"
    score.limb(15, "second orientation call is cheaper; adapters retained", call_structure)

    condition = {"camera_adapter": "DemoCamera", "device": "Path", "adapter": "DLightPath",
                 "allowed": ["State-0", "State-1", "State-2"]}
    saved = {"kind": "optical_path_position_map", "device": "Path",
             "positions": {"State-0": "camera", "State-1": "eyes"}, "observed_on": condition}
    def mapped(changes=None):
        ctrl = controller(FakeCore(DEMO_STATES, DEMO_ADAPTERS, shutter="White Light Shutter",
                                   configs=DEMO_CONFIGS), authorization)
        if changes: changes(ctrl)
        return with_knowledge(km, {"devices": {"path": saved}},
                              lambda: tools.get_system_state(ctrl, guard))
    def path_entry(p): return next(x for x in p["optical_path"]["discrete_positions"] if x["device"] == "Path")
    score.limb(16, "complete identity reports map beside device", lambda:
               score.check(path_entry(mapped()).get("position_map") == saved["positions"],
                           "complete identity map reported beside Path"),
               missing=("position_map",))

    def identity_variants():
        camera = mapped(lambda c: setattr(c.core, "camera_adapter", "OtherCamera"))
        adapter = mapped(lambda c: c._state_device_inventory["devices"][-1].update(adapter="OtherPath"))
        allowed = mapped(lambda c: c._state_device_inventory["devices"][-1]["allowed"].append("State-3"))
        description = mapped(lambda c: c._state_device_inventory["devices"][-1].update(adapter_description="Reworded"))
        return score.check(all("position_map" not in path_entry(x) for x in (camera, adapter, allowed))
                           and "position_map" in path_entry(description),
                           "camera/adapter/allowed retire; description change survives")
    score.limb(17, "map identity retirement and description survival", identity_variants,
               missing=("position_map",))

    def discriminator():
        ordinary = {k: v for k, v in saved.items() if k != "kind"}
        p = with_knowledge(km, {"devices": {"ordinary": ordinary}},
                           lambda: tools.get_system_state(demo_ctrl, guard))
        return score.check("position_map" not in path_entry(p), str(path_entry(p)))
    score.limb(18, "kind discriminator excludes otherwise matching ordinary entry", discriminator)

    def load_error():
        old = km.load_knowledge; km.load_knowledge = lambda: (_ for _ in ()).throw(ValueError("bad yaml"))
        try: p = tools.get_system_state(demo_ctrl, guard)
        finally: km.load_knowledge = old
        return score.check("bad yaml" in p["optical_path"].get("position_map_error", ""), str(p["optical_path"]))
    score.limb(19, "knowledge-load failure is visible", load_error,
               missing=("position_map_error",))

    def save_contract():
        old_confirm, old_path = tools.CONFIRM_FN, km.KNOWLEDGE_PATH
        tmp = Path(tempfile.mkdtemp(prefix="score59b-")) / "knowledge.yaml"
        km.KNOWLEDGE_PATH = tmp
        ctrl = controller(FakeCore(DEMO_STATES, DEMO_ADAPTERS), authorization)
        base = {"kind": "optical_path_position_map", "device": "Path",
                "positions": {"State-0": "camera"}}
        try:
            tools.CONFIRM_FN = lambda *a, **k: True
            ok = tools.save_knowledge(ctrl, guard, "devices", "path", dict(base))
            if ok.get("value", {}).get("observed_on") != condition:
                raise AssertionError(f"resolved condition wrong: {ok}")
            def must_not_confirm(*a, **k): raise AssertionError("CONFIRM_FN reached on refusal")
            tools.CONFIRM_FN = must_not_confirm
            cases = []
            session_c_shape = {
                "description": "Motorized light-path selector", "device": "Path",
                "property": "Label", "observed_on": "DemoCamera",
                "position_map": {"State-0": "eyepiece", "State-1": "left camera",
                                 "State-2": "right camera"},
            }
            missing_kind = tools.save_knowledge(
                ctrl, guard, "devices", "session-c", session_c_shape)
            if "optical_path_position_map" not in missing_kind.get("error", ""):
                raise AssertionError(f"missing-kind refusal lacks correct shape: {missing_kind}")
            cases.append(missing_kind)
            wrong = {**base, "observed_on": {**condition, "adapter": "Wrong"}}
            cases.append(tools.save_knowledge(ctrl, guard, "devices", "wrong", wrong))
            ctrl._state_device_inventory["devices"][-1]["adapter"] = "unknown"
            cases.append(tools.save_knowledge(ctrl, guard, "devices", "unknown", dict(base)))
            ctrl._state_device_inventory["devices"][-1]["adapter"] = "DLightPath"
            cases.append(tools.save_knowledge(ctrl, guard, "devices", "missing", {**base, "device": "NoDevice"}))
            saved_inventory = ctrl._state_device_inventory; ctrl._state_device_inventory = None
            cases.append(tools.save_knowledge(ctrl, guard, "devices", "unbuilt", dict(base)))
            ctrl._state_device_inventory = saved_inventory
            cases.append(tools.save_knowledge(ctrl, guard, "devices", "bad-label",
                                              {**base, "positions": {"State-9": "camera"}}))
            tools.CONFIRM_FN = lambda *a, **k: True
            ordinary = tools.save_knowledge(ctrl, guard, "devices", "path-note", {
                "description": "Selector detent is stiff", "device": "Path",
                "observed_on": "DemoCamera", "caveat": {"service": "inspect annually"},
            })
            if "status" not in ordinary:
                raise AssertionError(f"ordinary StateDevice note over-refused: {ordinary}")
            return score.check(all("error" in case for case in cases), str(cases))
        finally:
            tools.CONFIRM_FN, km.KNOWLEDGE_PATH = old_confirm, old_path
    score.limb(20, "save resolves identity and refuses invalid shapes before confirmation", save_contract,
               missing=("optical_path_position_map",))

    def prompt_format():
        structured = {"map": saved}
        only = km.format_for_prompt({"devices": structured})
        mixed = km.format_for_prompt({"devices": {**structured,
                                     "legacy": {"note": "keep", "observed_on": "DemoCamera"}}})
        return score.check(only is None and "optical_path_position_map" not in mixed and
                           "verify before relying" in mixed, f"only={only!r}, mixed={mixed!r}")
    score.limb(21, "prompt omits maps, preserves legacy, avoids bare header", prompt_format)

    def no_writes():
        paths = [TREE / "microclaw/authorization.py", TREE / "microclaw/tools.py",
                 TREE / "microclaw/optics_docs.py"]
        for path in paths:
            text = path.read_text(encoding="utf-8")
            if "define_state_label" in text or "defineStateLabel" in text:
                raise AssertionError(f"state-label writer in {path}")
        tools.get_system_state(controller(FakeCore(DEMO_STATES, DEMO_ADAPTERS), authorization), guard)
        return "scoped sources clean; bridge write traps not reached"
    score.limb(22, "no state-label writes in scoped sources or orientation", no_writes,
               missing=("optics_docs.py",))
    return score, costs


M5_CAPABILITY = {
    1: (False, "source reference, not a rig behavior"),
    2: (False, "source reference, not a rig behavior"),
    3: (False, "tool/schema structure, not a rig behavior"),
    4: (True, "orientation returns the hint on M5"),
    5: (False, "system-prompt structure is settled offline"),
    6: (False, "M5 has no positive port-token label"),
    7: (False, "M5 has none of the negative-control labels"),
    8: (True, "its real labels establish the no-match evidence"),
    9: (False, "M5 has neither adapter nor label routing signal"),
    10: (False, "requires a deliberately named negative-control device"),
    11: (False, "M5 has no adapter-identified light path"),
    12: (True, "all four real StateDevices must remain listed"),
    13: (True, "M5 exercises the empty Core-shutter branch"),
    14: (False, "M5 has no pixel-size configuration group"),
    15: (True, "M5 can measure first/second bridge calls; wall time remains rig-only"),
    16: (True, "identity mechanism can target a retained M5 StateDevice"),
    17: (True, "identity-field variations are mechanically exercisable"),
    18: (True, "kind discriminator is rig-independent once identity is constructed"),
    19: (False, "knowledge-file failure is injected offline, not a rig capability"),
    20: (True, "save resolution can use M5's retained inventory and camera"),
    21: (False, "prompt formatting has no live-rig input"),
    22: (True, "orientation can run with bridge write traps on M5's fingerprint"),
}

NEEDS_RIG = [
    "Wall-time cost: a fake can count calls but cannot measure pyjavaz and adapter latency.",
    "Real bridge behavior: confirm the retained adapter metadata and bridge-shaped collections through pyjavaz.",
    "Driven-session behavior: verify the agent reads optical_path before exposure, asks routing only when unnamed, uses a stored map without asking again, handles a blank frame physically, proposes the hardware lock before an image sweep, and calls the reference only when signal is missing.",
]


def main() -> int:
    lines = [f"BLOCK 59b OFFLINE SCORE — tree: {TREE}"]
    try:
        score, costs = run()
    except Exception:
        lines.extend(["FATAL SETUP FAILURE", traceback.format_exc()])
        text = "\n".join(lines); print(text); LOG.write_text(text + "\n", encoding="utf-8")
        return 1
    lines.append("\nLIMBS")
    for number, status, name, detail in score.rows:
        lines.append(f"{number:02d} {status:<11} {name} — {detail}")
    lines.append("\nM5 CAPABILITY")
    for number in range(1, 23):
        capable, reason = M5_CAPABILITY[number]
        verdict = "EXERCISABLE" if capable else "NOT EXERCISABLE-ON-THIS-RIG"
        lines.append(f"{number:02d} {verdict:<28} {reason}")
    counts = {status: sum(row[1] == status for row in score.rows)
              for status in ("PASS", "FAIL", "NOT PRESENT")}
    discriminated = counts["FAIL"] + counts["NOT PRESENT"]
    main_comparison = None
    if os.environ.get("MICROCLAW_59B_MAIN_CHILD") != "1":
        try:
            listing = subprocess.run(
                ["git", "worktree", "list", "--porcelain"], cwd=TREE,
                text=True, capture_output=True, check=True,
            ).stdout.splitlines()
            candidates = []
            current = None
            for line in listing:
                if line.startswith("worktree "):
                    current = Path(line.removeprefix("worktree "))
                elif line == "branch refs/heads/main" and current is not None:
                    candidates.append(current)
            main_tree = candidates[0] if candidates else None
            if main_tree is not None and main_tree.resolve() != TREE.resolve():
                env = dict(os.environ, MICROCLAW_59B_MAIN_CHILD="1")
                compared = subprocess.run(
                    [sys.executable, str(Path(__file__).resolve())], cwd=main_tree,
                    text=True, capture_output=True, env=env,
                )
                statuses = re.findall(
                    r"^(?:0[1-9]|1[0-9]|2[0-2]) (PASS|FAIL|NOT PRESENT)\s",
                    compared.stdout, re.M,
                )
                if len(statuses) == 22:
                    main_comparison = sum(s in ("FAIL", "NOT PRESENT") for s in statuses)
        except Exception:
            main_comparison = None
    lines.extend(["\nDISCRIMINATION SUMMARY",
                  f"PASS={counts['PASS']} FAIL={counts['FAIL']} NOT PRESENT={counts['NOT PRESENT']}",
                  (f"This tree has {counts['PASS']} passing limbs; {main_comparison} of those "
                   "FAIL or are NOT PRESENT on the checked-out main tree."
                   if main_comparison is not None else
                   f"Main comparison unavailable in this checkout; this run has "
                   f"{discriminated} FAIL-or-NOT-PRESENT limbs.")])
    lines.append("\nNEEDS A RIG")
    lines.extend(f"- {item}" for item in NEEDS_RIG)
    text = "\n".join(lines)
    print(text)
    LOG.write_text(text + "\n", encoding="utf-8")
    return 0 if counts["FAIL"] == 0 and counts["NOT PRESENT"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
