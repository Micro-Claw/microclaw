"""The packaged icon, and the README PNG derived from it (design/17 v1).

The icon has three consumers with different needs — the browser tab, the README,
and (design/17 v3) the Windows desktop shortcut — all fed from one committed
`favicon.ico`. These tests pin the properties each consumer depends on.
"""
import io
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from microclaw.assets import ICON, icon_bytes

ROOT = Path(__file__).resolve().parents[1]
PNG = ROOT / "docs" / "microclaw-icon.png"

# Windows renders the shortcut at 16px (taskbar), 32px (desktop) and 48px
# (alt-tab), and picks the nearest frame; a missing one is upscaled and looks
# soft. 256px is what the README PNG derives from.
REQUIRED_FRAMES = {(16, 16), (32, 32), (48, 48), (256, 256)}


def test_icon_bytes_resolves_through_importlib():
    """Not via __file__: the shortcut reads this from an installed wheel."""
    data = icon_bytes()
    assert data[:4] == b"\x00\x00\x01\x00", "not an ICO header"
    assert len(data) > 1000


def test_icon_carries_every_frame_windows_and_the_readme_need():
    with Image.open(io.BytesIO(icon_bytes())) as im:
        frames = set(im.ico.sizes())
    missing = REQUIRED_FRAMES - frames
    assert not missing, f"{ICON} is missing frames {sorted(missing)}"


def test_readme_png_is_not_stale():
    """`docs/microclaw-icon.png` must be what derive_icons.py produces today.

    Otherwise the README drifts from the icon everything else uses, and nobody
    notices until the two look different in a screenshot.
    """
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "derive_icons.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr or r.stdout


def test_readme_references_the_derived_png():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/microclaw-icon.png" in readme
