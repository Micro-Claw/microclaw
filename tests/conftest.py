import os

import pytest
from unittest.mock import MagicMock
from microclaw.controller import MicroscopeController
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
    return ctrl


@pytest.fixture(autouse=True)
def _clear_emu_session_cache():
    """tools._EMU_SESSION_CACHE is module-global and would otherwise carry a
    parsed config (or a cached 'not an EMU rig') between tests."""
    from microclaw import tools
    tools._EMU_SESSION_CACHE.clear()
    yield
    tools._EMU_SESSION_CACHE.clear()


# ── Safety fixtures ─────────────────────────────────────────────────────────

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
    return SafetyGuard(SafetyConstraints())


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
