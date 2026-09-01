"""Regression tests for `microclaw view-history` HTML generation.

The subcommand substitutes the run's JSON into the bundled viewer template with a
blind `str.replace`. An earlier bug used a token (`__HISTORY_DATA__`) that also
appeared inside a JavaScript guard, so the replace clobbered the guard and the
transcript never auto-rendered. These tests lock in the invariants that prevent
that class of bug: the token appears exactly once in the template, and the
generated HTML embeds parseable JSON with the loader guard left intact.
"""
import json
import re
from importlib import resources

import pytest

from microclaw.assets import CSS_TAG, JS_TAG, RECOVERY_JS_TAG, load_page
from microclaw.__main__ import view_history

TOKEN = "__MICROCLAW_HISTORY_DATA__"

# A history exercising the awkward cases: a user string containing a literal
# "</script>" and a "<" (must not break out of the data <script> block), an
# assistant text + tool_use, and the matching tool_result.
SAMPLE_HISTORY = [
    {"role": "user", "content": "watch out for </script> and x < y in here"},
    {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "Checking channels."},
            {"type": "tool_use", "id": "toolu_1", "name": "get_available_channels", "input": {}},
        ],
    },
    {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "toolu_1", "content": '{"channels": []}'}
        ],
    },
]


def _data_blob(html):
    """Return the raw text embedded in the data <script> block."""
    m = re.search(
        r'<script id="history-data" type="application/json">(.*?)</script>',
        html,
        re.S,
    )
    assert m, "data script block not found in generated HTML"
    return m.group(1)


def test_template_has_exactly_one_substitution_token():
    """The token must live only in the data script, never in JS/guard code.

    If a future edit reintroduces the token elsewhere (e.g. a `raw !== TOKEN`
    guard), the blind replace would corrupt it again — fail loudly here instead.
    """
    template = (
        resources.files("microclaw")
        .joinpath("history_viewer.html")
        .read_text(encoding="utf-8")
    )
    assert template.count(TOKEN) == 1


def test_view_history_embeds_parseable_json(tmp_path):
    src = tmp_path / "20990101_000000_microclaw_history.json"
    src.write_text(json.dumps(SAMPLE_HISTORY), encoding="utf-8")

    out = view_history(src, open_browser=False)
    html = out.read_text(encoding="utf-8")

    # Token fully substituted, nothing left behind.
    assert TOKEN not in html

    # The embedded blob round-trips to the original history.
    blob = _data_blob(html)
    assert json.loads(blob) == SAMPLE_HISTORY

    # The user's literal "</script>" must be escaped so it can't terminate the
    # data block early; JSON.parse restores it browser-side.
    assert "</script" not in blob.lower()

    # The auto-render guard survives substitution intact.
    assert "if (raw) {" in html
    assert "render(JSON.parse(raw))" in html


def test_view_history_recovers_truncated_jsonl_with_visible_warning(tmp_path, capsys):
    src = tmp_path / "session_microclaw_history.jsonl"
    src.write_text(
        "\n".join(json.dumps(message) for message in SAMPLE_HISTORY) +
        '\n{"role":"assistant"',
        encoding="utf-8",
    )
    html = view_history(src, open_browser=False).read_text(encoding="utf-8")
    assert json.loads(_data_blob(html)) == SAMPLE_HISTORY
    assert "incomplete final JSONL record" in capsys.readouterr().err


def test_transcript_js_has_no_end_script_tag():
    """assets.py inlines transcript.js into a script block. A literal end-script
    tag anywhere in it — even in a comment or a string — would close that block
    early and dump the rest of the renderer into the page as text."""
    js = resources.files("microclaw").joinpath("transcript.js").read_text(encoding="utf-8")
    assert "</script" not in js.lower()


@pytest.mark.parametrize("page", ["history_viewer.html", "serve.html"])
def test_pages_reference_the_shared_assets_exactly_once(page):
    """load_page does a blind single replace of each tag; a duplicate would
    leave one behind, and a rename would silently skip the inline."""
    html = resources.files("microclaw").joinpath(page).read_text(encoding="utf-8")
    assert html.count(CSS_TAG) == 1
    assert html.count(JS_TAG) == 1


@pytest.mark.parametrize("page", ["history_viewer.html", "serve.html"])
def test_load_page_inlines_both_assets(page):
    html = load_page(page)
    assert CSS_TAG not in html and JS_TAG not in html
    assert "global.Transcript = {" in html   # transcript.js
    assert "--tool-line:" in html            # transcript.css


def test_serve_inlines_recovery_without_requiring_it_in_history_viewer():
    served = load_page("serve.html")
    history = load_page("history_viewer.html")
    assert RECOVERY_JS_TAG not in served
    assert "global.Recovery = { create, sseEvents };" in served
    assert RECOVERY_JS_TAG not in history
    assert "global.Recovery = { create };" not in history


def test_view_history_output_is_self_contained(tmp_path):
    """It is written to a temp directory, where transcript.css/js are not siblings."""
    src = tmp_path / "h.json"
    src.write_text(json.dumps(SAMPLE_HISTORY), encoding="utf-8")
    html = view_history(src, open_browser=False).read_text(encoding="utf-8")
    assert 'href="transcript.css"' not in html
    assert 'src="transcript.js"' not in html
