"""Derive docs/microclaw-icon.png from microclaw/favicon.ico.

The .ico is the single source of truth (design/17): Windows reads it directly for
the desktop shortcut, and `serve` hands it to the browser. The README cannot use
it — GitHub renders a multi-frame ICO in an <img> inconsistently — so it gets a
PNG derived from the .ico's largest frame.

Run this whenever favicon.ico changes; the PNG is committed. `pytest
tests/test_assets.py` fails if the two have drifted.

    python scripts/derive_icons.py [--check]
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
ICO = ROOT / "microclaw" / "favicon.ico"
PNG = ROOT / "docs" / "microclaw-icon.png"

# What the README wants. Windows separately wants 16/32/48 inside the .ico, but
# it reads those itself; nothing here needs to produce them.
TARGET = (256, 256)


def render() -> bytes:
    with Image.open(ICO) as ico:
        frames = sorted(ico.ico.sizes())
        if TARGET not in frames:
            print(
                f"warning: {ICO.name} has no {TARGET[0]}px frame (has {frames}); "
                "the README image will be an upscale and will look soft.",
                file=sys.stderr,
            )
        img = ico.ico.getimage(TARGET).convert("RGBA")
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if the committed PNG is stale, without rewriting it.",
    )
    args = ap.parse_args()

    want = render()
    if args.check:
        have = PNG.read_bytes() if PNG.exists() else b""
        if have != want:
            print(f"{PNG} is stale; run: python scripts/derive_icons.py", file=sys.stderr)
            return 1
        print(f"{PNG} is up to date.")
        return 0

    PNG.parent.mkdir(parents=True, exist_ok=True)
    PNG.write_bytes(want)
    print(f"wrote {PNG} ({len(want)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
