from __future__ import annotations
from microclaw.safety import SafetyConstraints


def load_safety_config(path: str) -> SafetyConstraints:
    return SafetyConstraints.from_yaml(path)
