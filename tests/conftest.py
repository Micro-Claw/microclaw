import os

import pytest
from unittest.mock import MagicMock
from microclaw.controller import MicroscopeController, PositionProjection
from microclaw.safety import SafetyConstraints, SafetyGuard, StageConstraints, CameraConstraints


# ── Mock controller (no MM required) ───────────────────────────────────────

@pytest.fixture
def mock_core():
    core = MagicMock()
    core.get_x_position.return_value = 0.0
    core.get_y_position.return_value = 0.0
    core.get_position.return_value = 50.0
    core.get_exposure.return_value = 100.0
    core.get_xy_stage_device.return_value = "DXYStage"
    core.get_focus_device.return_value = "DStage"
    core.get_available_configs.return_value = ["DAPI", "FITC", "Cy5"]
    core.get_loaded_devices.return_value = ["DCam", "DXYStage", "DStage"]
    return core


@pytest.fixture
def mock_studio(mock_core):
    studio = MagicMock()
    studio.live().is_live_mode_on.return_value = False
    return studio


@pytest.fixture
def mock_ctrl(mock_core, mock_studio):
    ctrl = MagicMock(spec=MicroscopeController)
    ctrl.core = mock_core
    ctrl.studio = mock_studio
    # A unit-test controller has no live JVM. MagicMock would otherwise
    # auto-create this method and let host discovery inspect a real MM install.
    ctrl.get_mm_app_dir = None
    ctrl.inspect_current_position_list.return_value = PositionProjection([], [], [])
    # A bare MagicMock answers int() with 1, so an unconfigured controller would
    # report a 1x1 sensor — and the flat-curve guard now scales with the frame's
    # pixel count, so that silently becomes a 153.6 threshold that refuses
    # everything. Default to an ordinary full frame; tests about small frames
    # set their own.
    ctrl.core.get_image_width.return_value = 1024
    ctrl.core.get_image_height.return_value = 1024
    return ctrl


@pytest.fixture(autouse=True)
def _isolate_microclaw_home(tmp_path, monkeypatch):
    """No test may read or write the developer's ~/.microclaw.

    These module-level constants bind at import, so setting $HOME does not move
    them; they must be redirected by name. Without this a unit test reads
    whatever the *host* has, and the suite passes on a laptop while failing on
    the microscope — or worse, passes on both while asserting the lab's data.
    Both have happened (design/19, design/20): a hook the agent saved on the rig
    turned up inside `list_saved_hooks()` during unit tests.

    A test that wants a saved hook, a knowledge base or an EMU map monkeypatches
    over this; function-scoped patches applied in the test body win.
    """
    from microclaw import hook_manager, knowledge_manager

    hooks = tmp_path / "microclaw_home" / "hooks"
    hooks.mkdir(parents=True)
    monkeypatch.setattr(hook_manager, "HOOKS_DIR", hooks)
    monkeypatch.setattr(hook_manager, "MANIFEST", hooks / "manifest.json")
    monkeypatch.setattr(
        knowledge_manager, "KNOWLEDGE_PATH", tmp_path / "microclaw_home" / "knowledge.yaml"
    )


@pytest.fixture(autouse=True)
def _clear_emu_session_cache():
    """tools._EMU_SESSION_CACHE is module-global and would otherwise carry a
    parsed config (or a cached 'not an EMU rig') between tests.

    Primed to None rather than merely cleared: an empty cache sends
    _cached_emu_properties out to find_mm_app_dir, which asks the (mock) bridge,
    then falls through to ~/.microclaw/emu.json and to guessing at
    C:/Program Files/Micro-Manager-2.0. On a lab machine every one of those can
    hit. Default the suite to "not an EMU rig"; tests that want a map patch
    tools._cached_emu_properties directly.
    """
    from microclaw import tools
    tools._EMU_SESSION_CACHE.clear()
    tools._EMU_SESSION_CACHE["properties"] = None
    yield
    tools._EMU_SESSION_CACHE.clear()


# ── Safety fixtures ─────────────────────────────────────────────────────────

class PermissiveTestGuard(SafetyGuard):
    """No-op stage checks for tests whose subject is not motion safety."""

    def check_xy(self, x, y):
        return None

    def check_z(self, z):
        return None


@pytest.fixture
def default_guard():
    constraints = SafetyConstraints(
        stage=StageConstraints(x_min=-1000, x_max=1000, y_min=-1000, y_max=1000,
                               z_min=0, z_max=200),
        camera=CameraConstraints(max_exposure_ms=2000),
        allowed_channels=["DAPI", "FITC", "Cy5"],
    )
    return SafetyGuard(constraints)


@pytest.fixture
def unconstrained_guard():
    return PermissiveTestGuard(SafetyConstraints())


# ── Live MM connection (requires Micro-Manager running with demo config) ─────
# Open Micro-Manager manually with MMConfig_demo.cfg loaded, then set
# MM_RUNNING=1 before running integration tests.  Optionally set MM_PORT
# if MM is bridged on a non-default port (default: 4827).
#
#   Windows CMD:        set MM_RUNNING=1
#   Windows PowerShell: $env:MM_RUNNING = "1"
#   macOS/Linux:        export MM_RUNNING=1
#
# Then run: pytest -m integration

@pytest.fixture(scope="session")
def headless_mm():
    """Connect to a running Micro-Manager instance. Requires MM_RUNNING=1 env var."""
    if not os.environ.get("MM_RUNNING"):
        pytest.skip(
            "MM_RUNNING is not set. Open Micro-Manager with the demo config, "
            "then set MM_RUNNING=1 and re-run."
        )
    port = int(os.environ.get("MM_PORT", "4827"))
    yield MicroscopeController(port=port)
