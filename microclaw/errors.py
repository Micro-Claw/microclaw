"""Error types for microclaw.

SafetyViolation lives in safety.py to avoid circular imports.
This module documents the full error taxonomy used across the package.

Error sources:
- SafetyViolation (safety.py): tool call would violate a user-defined constraint
- Exception from pycromanager/MM (Java): device not found, stage at limit, ZMQ timeout
- ValueError / TypeError: agent called a tool with invalid arguments
- ConnectionError: MM not running or ZMQ server disabled
"""
