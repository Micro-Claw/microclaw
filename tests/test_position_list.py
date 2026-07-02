"""Position-list GUI parity (issue 5): numAxes read path + write-through.

MicroscopeController.__init__ opens ZMQ connections, so these build the object
without __init__ and inject fakes. Fake Java objects stand in for the classes
pycro-manager would construct over the bridge.
"""
from unittest.mock import MagicMock

import pytest

from microclaw.controller import MicroscopeController


# ── Fakes standing in for MM's Java PositionList / MultiStagePosition ─────────

class FakeMSP:
    def __init__(self):
        self.label = None
        self.stages = []

    def set_label(self, label):
        self.label = label

    def add(self, sp):
        self.stages.append(sp)

    def get_label(self):
        return self.label


class FakeStagePositionClass:
    def create2_d(self, dev, x, y):
        return ("2d", dev, x, y)

    def create1_d(self, dev, z):
        return ("1d", dev, z)


class FakePositionList:
    def __init__(self):
        self._pos = []

    def get_number_of_positions(self):
        return len(self._pos)

    def get_position(self, i):
        return self._pos[i]

    def remove_position(self, i):
        self._pos.pop(i)

    def add_position(self, msp):
        self._pos.append(msp)


class FakePM:
    def __init__(self, plist):
        self._plist = plist
        self.set_calls = 0

    def get_position_list(self):
        return self._plist

    def set_position_list(self, plist):
        self.set_calls += 1


def make_controller():
    ctrl = MicroscopeController.__new__(MicroscopeController)
    ctrl._port = 4827
    ctrl._core = MagicMock()
    ctrl._core.get_xy_stage_device.return_value = "DXYStage"
    ctrl._core.get_focus_device.return_value = "DStage"
    plist = FakePositionList()
    pm = FakePM(plist)
    ctrl._studio = MagicMock()
    ctrl._studio.positions.return_value = pm
    ctrl._plugins = None
    ctrl._positions = []
    return ctrl, pm, plist


@pytest.fixture
def patch_java(monkeypatch):
    monkeypatch.setattr("pycromanager.JavaObject", lambda *a, **k: FakeMSP())
    monkeypatch.setattr("pycromanager.JavaClass", lambda *a, **k: FakeStagePositionClass())


# ── Read path: the numAxes field bug found by Spike A ────────────────────────

def _fake_sp(num_axes, x=0.0, y=0.0):
    sp = MagicMock()
    sp.numAxes = num_axes           # bridge exposes the raw Java field name only
    sp.num_axes = MagicMock()       # would NOT resolve over the real bridge
    sp.x = x
    sp.y = y
    return sp


def _fake_msp(label, stage_positions):
    msp = MagicMock()
    msp.get_label.return_value = label
    msp.size.return_value = len(stage_positions)
    msp.get.side_effect = lambda j: stage_positions[j]
    return msp


class TestReadNumAxes:
    def test_reads_xy_via_numAxes(self):
        ctrl, _, _ = make_controller()
        plist = MagicMock()
        plist.get_number_of_positions.return_value = 1
        plist.get_position.return_value = _fake_msp("P1", [_fake_sp(2, x=10.0, y=20.0)])
        ctrl._studio.positions.return_value = MagicMock()
        ctrl._studio.positions.return_value.get_position_list.return_value = plist

        out = ctrl._read_mm_position_list()
        assert out == [{"name": "P1", "x_um": 10.0, "y_um": 20.0}]

    def test_reads_z_only_via_numAxes(self):
        ctrl, _, _ = make_controller()
        plist = MagicMock()
        plist.get_number_of_positions.return_value = 1
        plist.get_position.return_value = _fake_msp("Zonly", [_fake_sp(1, x=42.0)])
        ctrl._studio.positions.return_value = MagicMock()
        ctrl._studio.positions.return_value.get_position_list.return_value = plist

        out = ctrl._read_mm_position_list()
        assert out == [{"name": "Zonly", "z_um": 42.0}]


# ── Write-through: add_position mirrors into MM's PositionList ────────────────

class TestWriteThrough:
    def test_add_position_writes_through_once(self, patch_java):
        ctrl, pm, plist = make_controller()
        ctrl.add_position("P1", 1.0, 2.0, 3.0)
        assert pm.set_calls == 1
        assert plist.get_number_of_positions() == 1
        msp = plist.get_position(0)
        assert msp.label == "P1"
        assert ("2d", "DXYStage", 1.0, 2.0) in msp.stages
        assert ("1d", "DStage", 3.0) in msp.stages

    def test_z_only_entry_uses_create1_d(self, patch_java):
        ctrl, pm, plist = make_controller()
        ctrl.add_position("P1", 1.0, 2.0)          # no z
        msp = plist.get_position(0)
        assert ("2d", "DXYStage", 1.0, 2.0) in msp.stages
        assert all(s[0] != "1d" for s in msp.stages)

    def test_remarking_label_does_not_duplicate(self, patch_java):
        ctrl, pm, plist = make_controller()
        ctrl.add_position("P1", 1.0, 2.0, 3.0)
        ctrl.add_position("P1", 5.0, 6.0, 7.0)     # re-mark same label
        assert plist.get_number_of_positions() == 1
        assert plist.get_position(0).stages[0] == ("2d", "DXYStage", 5.0, 6.0)
        # internal store also de-duped
        assert [p["name"] for p in ctrl.get_positions()] == ["P1"]


# ── Docs parity: shipped wording matches actual behaviour ────────────────────

def test_schema_and_prompt_wording_accurate():
    from microclaw import tools_schema, agent
    mark = next(t for t in tools_schema.TOOLS if t["name"] == "mark_position")
    assert "Position List Manager" in mark["description"]
    save = next(t for t in tools_schema.TOOLS if t["name"] == "save_position_list")
    assert "JSON" in save["description"] and ".pos" in save["description"]
    assert "Position List Manager" in agent.SYSTEM_PROMPT
