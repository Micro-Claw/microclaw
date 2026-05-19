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


# ── Headless MM (requires real MM installation) ──────────────────────────────
# Set MM_PATH and MM_DEMO_CONFIG environment variables before running:
#   Windows CMD:        set MM_PATH=C:\Program Files\Micro-Manager-2.0
#                       set MM_DEMO_CONFIG=C:\Program Files\Micro-Manager-2.0\MMConfig_demo.cfg
#   Windows PowerShell: $env:MM_PATH = "C:\Program Files\Micro-Manager-2.0"
#                       $env:MM_DEMO_CONFIG = "C:\Program Files\Micro-Manager-2.0\MMConfig_demo.cfg"
#   macOS/Linux:        export MM_PATH=/path/to/MicroManager
#                       export MM_DEMO_CONFIG=/path/to/MMConfig_demo.cfg
# Then run: pytest -m integration

@pytest.fixture(scope="session")
def mm_app_path():
    return os.environ.get("MM_PATH")


@pytest.fixture(scope="session")
def demo_config_path():
    return os.environ.get("MM_DEMO_CONFIG")


@pytest.fixture(scope="session")
def headless_mm(mm_app_path, demo_config_path):
    """Launch MM in headless mode with Demo config. Requires MM_PATH and MM_DEMO_CONFIG env vars."""
    if mm_app_path is None or demo_config_path is None:
        pytest.skip("MM_PATH and MM_DEMO_CONFIG environment variables required for integration tests")
    from pycromanager import start_headless
    start_headless(mm_app_path=mm_app_path, config_file=demo_config_path)
    ctrl = MicroscopeController()
    yield ctrl
