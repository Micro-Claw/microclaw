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


def _read(name: str) -> str:
    return resources.files("microclaw").joinpath(name).read_text(encoding="utf-8")


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
