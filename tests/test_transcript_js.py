"""Execute the shared renderer's pure functions under node.

`Transcript.renderResult` is the one piece of transcript.js that both builds
HTML from untrusted input (a history JSON can be any file dropped on the viewer)
and touches no DOM — so it can be run directly. Skipped where node is absent.
"""
import json
import shutil
import subprocess
from importlib import resources

import pytest

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

THUMB = {
    "type": "image",
    "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="},
}


def render_result(content):
    """Load transcript.js in node and call Transcript.renderResult(content)."""
    path = resources.files("microclaw").joinpath("transcript.js")
    script = (
        "global.window = {};\n"
        f"require({json.dumps(str(path))});\n"
        f"process.stdout.write(window.Transcript.renderResult({json.dumps(content)}));\n"
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    )
    return out.stdout


def test_a_string_result_renders_as_json():
    assert render_result('{"channels": ["DAPI"]}') == (
        '<pre class="json result">{\n  "channels": [\n    "DAPI"\n  ]\n}</pre>'
    )


def test_thumbnail_renders_as_an_image_not_base64_soup():
    """snap_and_analyze returns [text block, base64 PNG block] and run_agent
    stores that list verbatim as the tool_result content — so every saved history
    already carries thumbnails. Before this, they rendered as a screenful of
    base64 inside a <pre>."""
    html = render_result([{"type": "text", "text": '{"focus_metric": 12.5}'}, THUMB])
    assert '<img class="thumb" alt="snap thumbnail" src="data:image/png;base64,iVBORw0KGgo=">' in html
    assert '"focus_metric": 12.5' in html
    assert "<pre" in html                       # the text block still renders


def test_a_hostile_media_type_cannot_inject_attributes():
    """media_type lands inside src="...", where esc() is not enough: it leaves
    quotes alone. Only known image types are emitted."""
    block = {"type": "image", "source": {"type": "base64",
             "media_type": 'x" onerror="alert(1)', "data": "AA"}}
    html = render_result([block])
    assert "onerror" not in html
    assert 'src="data:image/png;base64,AA"' in html


def test_hostile_base64_payload_cannot_escape_the_src_attribute():
    """Non-base64 characters are dropped. `=` survives (it is base64 padding),
    but with the quotes and spaces gone it stays inert inside src="...")."""
    block = {"type": "image", "source": {"type": "base64",
             "media_type": "image/png", "data": 'AA" onerror="alert(1)'}}
    html = render_result([block])
    assert '"' not in html[html.index("src=") + 5: html.rindex('">')]
    assert html.endswith('src="data:image/png;base64,AAonerror=alert1"></div>')


def test_unknown_block_types_still_render():
    assert "<pre" in render_result([{"type": "thinking", "thinking": "hmm"}])
