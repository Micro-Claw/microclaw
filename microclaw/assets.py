"""Inline the shared transcript CSS/JS into a page.

history_viewer.html and serve.html both `<link>`/`<script src>` transcript.css
and transcript.js, so either opens correctly straight from the source tree. But
`view-history` writes its HTML to a temp directory, and `serve` hands the page
to a browser over HTTP — neither can resolve those siblings. Both call
`load_page`, which folds the two files in so the page is self-contained.
"""
from __future__ import annotations

from importlib import resources

CSS_TAG = '<link rel="stylesheet" href="transcript.css">'
JS_TAG = '<script src="transcript.js"></script>'

ICON = "favicon.ico"


def _read(name: str) -> str:
    return resources.files("microclaw").joinpath(name).read_text(encoding="utf-8")


def icon_bytes(name: str = ICON) -> bytes:
    """The packaged icon, as bytes.

    Binary, so it can't be inlined by `load_page` the way the CSS and JS are;
    `serve` hands it to the browser on its own route. Read through
    importlib.resources rather than __file__ so it resolves from a wheel, a
    zipimport, or the source tree alike — the desktop shortcut (design/17)
    needs it from an installed package where no source tree exists.
    """
    return resources.files("microclaw").joinpath(name).read_bytes()


def materialize_icon(dest, name: str = ICON):
    """Copy the packaged icon to a stable path on disk, and return it.

    A .lnk stores an absolute path to its icon and reads it years later, so it
    cannot point into the package: `resources.files()` may name a zip member with
    no filesystem path at all, and even a real path under site-packages vanishes
    on the next `pip uninstall`. The installer copies the icon somewhere it owns.
    """
    from pathlib import Path

    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(icon_bytes(name))
    return dest


def load_page(name: str) -> str:
    """Read a bundled HTML page with transcript.css/transcript.js inlined."""
    html = _read(name)
    for tag, asset, open_, close in (
        (CSS_TAG, "transcript.css", "<style>", "</style>"),
        (JS_TAG, "transcript.js", "<script>", "</script>"),
    ):
        if tag not in html:
            raise RuntimeError(f"{name} no longer contains {tag!r}; assets.py is stale.")
        html = html.replace(tag, f"{open_}\n{_read(asset)}\n{close}", 1)
    return html
