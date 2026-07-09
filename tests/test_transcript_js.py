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


def tool_card_body(block, result, live):
    """Call Transcript.toolCard under a document shim and return the card body.

    toolCard touches the DOM only through createElement/className/innerHTML/
    appendChild, so a four-line stand-in exercises it without a headless browser.
    """
    path = resources.files("microclaw").joinpath("transcript.js")
    shim = (
        "global.window = {};\n"
        "global.document = { createElement: (t) => ({ tag: t, className: '',"
        " innerHTML: '', kids: [], appendChild(c) { this.kids.push(c); } }) };\n"
    )
    # `undefined`, not `null`: toolCard's "no result" branch tests for undefined,
    # which is what `results[block.id]` yields for an unanswered tool_use.
    args = ", ".join(
        "undefined" if a is None else json.dumps(a) for a in (block, result, live)
    )
    script = (
        shim
        + f"require({json.dumps(str(path))});\n"
        + f"const card = window.Transcript.toolCard({args});\n"
        "process.stdout.write(card.kids[0].innerHTML);\n"
    )
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    return out.stdout


BLOCK = {"type": "tool_use", "id": "c1", "name": "move_stage_xy", "input": {"x_um": 10}}


def test_a_tool_with_a_result_renders_it():
    body = tool_card_body(BLOCK, {"content": '{"ok": true}'}, False)
    assert '"ok": true' in body
    assert "tool-pending" not in body


def test_a_running_tool_renders_as_pending_not_as_missing():
    """The same absent result means two things: in a saved history the result
    was never recorded; in a live `serve` turn the tool is still running."""
    body = tool_card_body(BLOCK, None, True)
    assert "tool-pending" in body and "running…" in body
    assert "(no result recorded)" not in body


def test_a_saved_history_still_says_no_result_recorded():
    body = tool_card_body(BLOCK, None, False)
    assert "(no result recorded)" in body
    assert "tool-pending" not in body


# ---- artifacts (design/16 v4d) ----

def in_node(expr, protocol=None):
    """Evaluate `expr` against Transcript, optionally with a location shim."""
    path = resources.files("microclaw").joinpath("transcript.js")
    loc = f"global.location = {{ protocol: {json.dumps(protocol)} }};\n" if protocol else ""
    script = (
        "global.window = {};\n" + loc
        + f"require({json.dumps(str(path))});\n"
        + f"process.stdout.write(String({expr}));\n"
    )
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True)
    return out.stdout


SAVED = json.dumps('{"status": "saved", "artifact": {"kind": "position_list", "path": "/ws/p.json"}}')


def test_an_artifact_is_read_from_the_result_not_regexed_out_of_prose():
    assert in_node(f"window.Transcript.artifactOf({SAVED}).path") == "/ws/p.json"
    # a path mentioned in prose is not an artifact
    prose = json.dumps('{"status": "Position list saved to /ws/p.json."}')
    assert in_node(f"window.Transcript.artifactOf({prose})") == "null"


def test_an_artifact_is_found_inside_a_block_list_result():
    blocks = json.dumps([{"type": "text", "text": json.loads(SAVED)}, THUMB])
    assert in_node(f"window.Transcript.artifactOf({blocks}).kind") == "position_list"


def test_a_served_page_links_the_artifact_to_the_download_endpoint():
    html = in_node(
        'window.Transcript.artifactChip({kind: "tiff", path: "/ws/a b.tif"})',
        protocol="http:",
    )
    assert 'href="/api/artifact?path=%2Fws%2Fa%20b.tif"' in html
    assert "download" in html and ">a b.tif<" in html


def test_view_history_renders_the_chip_inert():
    """A file:// page has no server behind it — an <a href> would 404 silently."""
    html = in_node(
        'window.Transcript.artifactChip({kind: "tiff", path: "/ws/a.tif"})',
        protocol="file:",
    )
    assert "<a" not in html and "artifact inert" in html and "a.tif" in html


def test_a_hostile_artifact_path_cannot_break_out_of_an_attribute():
    """The path reaches both href= and title=. esc() escapes < > &, not quotes,
    so title= needs escAttr — a path ending `" onclick="alert(1)` would otherwise
    hang an event handler on the anchor."""
    html = in_node(
        'window.Transcript.artifactChip({kind: "x", path: "a\\" onclick=\\"alert(1)"})',
        protocol="http:",
    )
    tag = html[: html.index(">") + 1]
    assert '" onclick="' not in tag          # never escapes into an attribute
    assert 'title="a&quot; onclick=&quot;alert(1)"' in tag
