from __future__ import annotations
from microclaw.safety import SafetyConstraints


def load_safety_config(path: str) -> SafetyConstraints:
    return SafetyConstraints.from_yaml(path)


def launch_headless(mm_app_path: str, config_file: str, port: int = 4827) -> None:
    """Launch Micro-Manager in headless mode with the given config file.

    After this returns, Core() and Studio() connect exactly as in GUI mode.
    No changes to tool functions or agent loop are required.
    """
    from pycromanager import start_headless
    start_headless(
        mm_app_path=mm_app_path,
        config_file=config_file,
        port=port,
    )
