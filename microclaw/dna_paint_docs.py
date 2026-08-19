"""The DNA-PAINT protocol, read from the packaged markdown.

The reference is a .md file rather than a string constant like SMLM_REFERENCE
because it is a wet-lab protocol the user edits and reads directly — tables of
buffer recipes and a step-by-step bench procedure. Keeping it as markdown means
there is one copy: the file the user maintains IS the file the tool returns, so
the two cannot drift the way a hand-copied string would.

Read through importlib.resources, not __file__, so it resolves from a wheel or a
zipimport as well as the source tree (same reason as assets.py).
"""
from __future__ import annotations

from importlib import resources

PROTOCOL_FILE = "dna_paint_protocol.md"


def load_reference() -> str:
    """The protocol text. Read at call time — this is not a hot path."""
    return (
        resources.files("microclaw").joinpath(PROTOCOL_FILE)
        .read_text(encoding="utf-8")
    )
