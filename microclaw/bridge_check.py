"""Bounded, real-protocol readiness check for the Micro-Manager ZMQ bridge."""

from __future__ import annotations

import subprocess
import sys


def probe_bridge(port: int, timeout: float) -> tuple[bool, str]:
    """Probe in a disposable process because pyjavaz has unbounded request waits."""
    command = [sys.executable, "-m", "microclaw.bridge_check", "--worker", str(port)]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"no Micro-Manager ZMQ bridge answered on port {port} within {timeout:g} seconds"
    if result.returncode == 0:
        return True, result.stdout.strip()
    detail = result.stderr.strip() or result.stdout.strip() or "the bridge did not answer"
    return False, f"no working Micro-Manager ZMQ bridge was found on port {port}: {detail}"


def _worker(port: int) -> int:
    from pycromanager import Core

    core = None
    try:
        core = Core(port=port, timeout=1000)
        version = core.get_version_info()
        print(f"Micro-Manager ZMQ bridge is ready on port {port} ({version}).")
        return 0
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        if core is not None:
            core._close()


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        raise SystemExit(_worker(int(sys.argv[2])))
    raise SystemExit(2)
