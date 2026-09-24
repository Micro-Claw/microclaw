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


def test_every_node_invocation_decodes_as_utf8():
    """`text=True` alone decodes with the locale encoding, which on Windows is
    cp1252 — so an em dash, a middle dot or an ellipsis in the rendered HTML
    comes back as a replacement character and six tests fail on a string the
    product got right. Node writes UTF-8 to a pipe on every platform.

    Checked by parsing both files rather than grepping them, because the next
    node call added here will be written from the one above it.
    """
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parent
    for name in ("test_transcript_js.py", "test_recovery_js.py"):
        source = (root / name).read_text(encoding="utf-8")
        calls = [
            node for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "run"
        ]
        assert calls, f"{name} invokes no subprocess"
        for call in calls:
            keywords = {k.arg: k.value for k in call.keywords}
            assert "encoding" in keywords, f"{name}:{call.lineno} decodes with the locale"
            assert keywords["encoding"].value == "utf-8", f"{name}:{call.lineno}"


def render_result(content):
    """Load transcript.js in node and call Transcript.renderResult(content)."""
    path = resources.files("microclaw").joinpath("transcript.js")
    script = (
        "global.window = {};\n"
        f"require({json.dumps(str(path))});\n"
        f"process.stdout.write(window.Transcript.renderResult({json.dumps(content)}));\n"
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, encoding="utf-8", check=True
    )
    return out.stdout


def update_view(state):
    path = resources.files("microclaw").joinpath("transcript.js")
    script = (
        "global.window = {};\n"
        f"require({json.dumps(str(path))});\n"
        f"process.stdout.write(JSON.stringify(window.Transcript.updateBannerView({json.dumps(state)})));\n"
    )
    return json.loads(subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout)


def test_update_banner_offer_progress_and_restart_views_escape_via_text_contract():
    candidate = {"sha": "abcdef123456", "subject": '<img onerror="boom">', "url": "https://github.com/x/y"}
    offer = update_view({"candidate": candidate, "staging": False, "pending_staged": False})
    assert offer["buttons"] == ["update", "later", "view"]
    assert offer["text"] == 'A newer Microclaw commit is available: abcdef1 — <img onerror="boom">'
    assert update_view({"candidate": candidate, "staging": True})["buttons"] == ["progress"]
    restart = update_view({"candidate": candidate, "pending_staged": True, "automatic_restart": True})
    assert restart["buttons"] == ["restart-now", "restart-later"]


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
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", check=True)
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
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, encoding="utf-8", check=True)
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


def md(text):
    """Load transcript.js in node and call Transcript.md(text)."""
    path = resources.files("microclaw").joinpath("transcript.js")
    script = (
        "global.window = {};\n"
        f"require({json.dumps(str(path))});\n"
        f"process.stdout.write(window.Transcript.md({json.dumps(text)}));\n"
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, encoding="utf-8", check=True
    )
    return out.stdout


def test_a_fenced_block_renders_as_a_block_with_no_stray_backticks():
    """M5 gate, 2026-08-13. The model quotes tool results back inside ``` fences
    constantly, and every one of them arrived as a run-on line with backticks
    left on the page: the inline-code rule matched from the third backtick of
    the opening fence to the first of the closing one."""
    html = md('Result:\n\n```\n{"error": "refused"}\n```\n\nThat was deliberate.')
    assert html == (
        "<p>Result:</p>"
        '<pre class="code">{&quot;error&quot;: &quot;refused&quot;}</pre>'
        "<p>That was deliberate.</p>"
    ).replace("&quot;", '"')
    assert "`" not in html


def test_a_language_tag_is_not_rendered_as_content():
    assert md('```json\n{"a": 1}\n```') == '<pre class="code">{"a": 1}</pre>'


def test_a_fence_still_streaming_renders_as_a_block():
    """`serve` renders every text delta, so a half-written fence is on screen
    for as long as the model takes to close it."""
    assert md("here:\n\n```\n{partial") == '<p>here:</p><pre class="code">{partial</pre>'


def test_inline_code_and_bold_still_work():
    assert md("use `move_stage_xy` and **stop**") == (
        "<p>use <code>move_stage_xy</code> and <strong>stop</strong></p>"
    )


def test_prose_that_looks_like_the_placeholder_is_left_alone():
    """The held-block placeholder has to be something a transcript cannot
    contain: a printable marker would eventually appear in ordinary prose and
    be swapped for an unrelated code block."""
    assert md("about 3 minutes, roughly 0 frames lost") == (
        "<p>about 3 minutes, roughly 0 frames lost</p>"
    )


def test_markup_inside_a_fence_is_escaped():
    assert md("```\n<img src=x onerror=alert(1)>\n```") == (
        '<pre class="code">&lt;img src=x onerror=alert(1)&gt;</pre>'
    )


def rendered_turns(history):
    """Call Transcript.render under the same DOM stand-in as tool_card_body and
    return (counts, concatenated innerHTML)."""
    path = resources.files("microclaw").joinpath("transcript.js")
    script = (
        "global.window = {};\n"
        "const mk = () => ({className: '', innerHTML: '', children: [],"
        " appendChild(c) { this.children.push(c); }, querySelectorAll: () => []});\n"
        "global.document = {createElement: mk, querySelectorAll: () => []};\n"
        f"require({json.dumps(str(path))});\n"
        "const tx = mk();\n"
        f"const counts = window.Transcript.render({json.dumps(history)}, tx, {{}});\n"
        "process.stdout.write(JSON.stringify"
        "({counts, html: tx.children.map(c => c.innerHTML).join('')}));\n"
    )
    out = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, encoding="utf-8", check=True
    )
    return json.loads(out.stdout)


def test_an_assistant_turn_that_is_a_plain_string_renders():
    """microclaw seeds setup mode's opening message as a plain string, the same
    shape a user turn may take. Rendering only arrays dropped it silently: on a
    clean-profile install the page showed banners and no message at all, so the
    operator had to type something to discover what setup wanted of them (block
    48e acceptance run, 2026-08-14)."""
    result = rendered_turns([{"role": "assistant", "content": "Security bounds are not set."}])
    assert result["counts"]["asstTurns"] == 1
    assert "Security bounds are not set." in result["html"]


def test_an_empty_assistant_string_is_not_a_turn():
    result = rendered_turns([{"role": "assistant", "content": "   "}])
    assert result["counts"]["asstTurns"] == 0


def test_a_fetch_warning_reaches_the_banner_text():
    """discover_clone sets `warning` when the fetch failed but a stale local ref
    resolved -- the offline case.  Unrendered, the banner would present a
    possibly-superseded commit as if it were the newest."""
    view = update_view({"candidate": {
        "sha": "abcdef123456", "subject": "Some change",
        "warning": "Open GitHub Desktop, Fetch origin, then Check again.",
    }, "staging": False, "pending_staged": False})
    assert view["text"] == (
        "A newer Microclaw commit is available: abcdef1 — Some change "
        "Open GitHub Desktop, Fetch origin, then Check again."
    )


def extensions_view(state):
    path = resources.files("microclaw").joinpath("transcript.js")
    script = (
        "global.window = {};\n"
        f"require({json.dumps(str(path))});\n"
        f"process.stdout.write(JSON.stringify(window.Transcript.extensionsView({json.dumps(state)})));\n"
    )
    return json.loads(subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout)


@pytest.mark.parametrize("patch,status,text,button", [
    ({}, "not-installed", "Not installed", "Install"),
    ({"ready": True, "recorded": True}, "ready", "Ready", None),
    ({"recorded": True}, "failed-or-missing", "Recorded but missing from this environment.", "Reinstall"),
    ({"error": "Resolution conflict"}, "failed-or-missing", "Resolution conflict", "Reinstall"),
])
def test_extensions_four_states(patch, status, text, button):
    item = {"name": "ilastik", "description": "<untrusted>", "ready": False, "recorded": False, **patch}
    row = extensions_view({"extensions": [item]})[0]
    assert (row["status"], row["text"], row["button"]) == (status, text, button)
    assert row["description"] == "<untrusted>"  # text contract: serve uses textContent.


@pytest.mark.parametrize("phase", [
    "checking environment", "running package installer", "Resolution complete",
    "Downloading numpy", "Downloading h5py", "Downloaded h5py", "Downloaded numpy",
    "Package preparation complete", "Package installation complete", "verifying",
])
def test_extensions_view_renders_observed_phase(phase):
    state = {"extensions": [{"name": "ilastik", "description": "HDF5", "ready": False}],
             "job": {"name": "ilastik", "running": True, "phase": phase}}
    view = extensions_view(state)[0]
    assert view["status"] == "installing"
    assert view["text"] == phase


@pytest.mark.parametrize("ready,recorded,running", [
    (False, False, False), (False, True, False), (True, True, False),
    (True, False, False), (False, True, True), (True, True, True),
])
def test_extensions_view_retains_extras_error_in_every_state(ready, recorded, running):
    error = "Update abc: combined extras spec failed."
    item = {"name": "ilastik", "ready": ready, "recorded": recorded, "error": error}
    row = extensions_view({"extensions": [item], "job": {
        "name": "ilastik", "running": running, "phase": "verifying"}})[0]
    assert row["text"].count(error) == 1
    if running:
        assert row["status"] == "installing" and row["button"] is None
        assert row["text"].startswith("verifying")
    elif ready and recorded:
        assert row["status"] == "ready" and row["button"] is None
        assert row["text"] == "Ready — " + error
    else:
        assert row["status"] == "failed-or-missing" and row["button"] == "Reinstall"


@pytest.mark.parametrize("scenario,ready", [
    ("rollback", False), ("ordinary reinstall", True), ("rebuilt environment", False),
])
def test_extension_catalog_reconcile_renders_controlled_slot(tmp_path, monkeypatch, scenario, ready):
    from microclaw import extensions as ext
    monkeypatch.setattr(ext, "user_data_dir", lambda: tmp_path)
    monkeypatch.setattr(ext, "requirements_for", lambda name: ["h5py>=3.10"])
    monkeypatch.setattr(ext, "ready", lambda name: ready)
    ext.record("ilastik", ["h5py>=3.10"])
    catalog = ext.available()
    assert catalog[0]["recorded"] is True
    assert catalog[0]["ready"] is ready
    row = extensions_view({"extensions": catalog})[0]
    assert row["status"] == ("ready" if ready else "failed-or-missing"), scenario
    assert row["button"] == (None if ready else "Reinstall"), scenario


def test_skill_packages_view_keeps_disabled_release_and_test_banner():
    path = resources.files("microclaw").joinpath("transcript.js")
    state = {"trust": {"test_roots_active": True}, "packages": [{
        "package_id": "example", "installs": [{"manifest": {"publisher": "lab", "version": "1.0.0"},
        "artifact_digest": "abcdef1234567890", "state": "ready", "eligible": False,
        "reasons": [{"field": "microclaw", "detail": "incompatible with 2.0.0"}]}]}]}
    script = ("global.window = {};\n" + f"require({json.dumps(str(path))});\n" +
              f"process.stdout.write(JSON.stringify(window.Transcript.skillPackagesView({json.dumps(state)})));\n")
    view = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True,
                                    encoding="utf-8", check=True).stdout)
    assert view["rows"] == ["lab/example 1.0.0 abcdef123456 — ready — disabled: microclaw: incompatible with 2.0.0"]
    assert view["trust"] == "TEST-ONLY trust roots are active: releases signed with publicly known test keys can run on this machine."
    html = resources.files("microclaw").joinpath("serve.html").read_text(encoding="utf-8")
    assert '<details id="skill-packages-panel"' in html
    assert "Transcript.skillPackagesView(state)" in html and "item.textContent = row" in html
    panel = html.split('<details id="skill-packages-panel"')[1].split("</details>")[0]
    assert "<button" not in panel


def test_skill_discovery_toggle_exclusion_and_disclosure():
    path = resources.files("microclaw").joinpath("transcript.js")
    state = {"packages": [{"package_id": "example", "discovery": {"enabled": True},
        "installs": [{"state": "ready", "eligible": True,
        "discovery_exclusions": [{"field": "name", "detail": "ambiguous enabled external skill"}]}]},
        {"package_id": "other", "job": {"running": True}}]}
    script = ("global.window = {};\n" + f"require({json.dumps(str(path))});\n" +
              f"process.stdout.write(JSON.stringify(window.Transcript.skillPackagesView({json.dumps(state)})));\n")
    view = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True,
                                    encoding="utf-8", check=True).stdout)
    assert "discovery excluded: name: ambiguous" in view["rows"][0]
    assert view["discovery"] == [
        dict(package_id="example", enabled=True, label="Disable discovery", disabled=False),
        dict(package_id="other", enabled=False, label="Enable discovery", disabled=True)]
    assert view["disclosure"] == ("Discovery shows publisher text to the agent. "
        "It does not authorize execution. "
        "Executable packages run with your user permissions and are not sandboxed.")
    html = resources.files("microclaw").joinpath("serve.html").read_text(encoding="utf-8")
    assert '$("skill-packages-disclosure").textContent = view.disclosure' in html
    assert 'JSON.stringify({enabled: !toggle.enabled})' in html
    assert 'encodeURIComponent(toggle.package_id) + "/discovery"' in html
    assert 'finally { await refreshSkillPackages(); }' in html
