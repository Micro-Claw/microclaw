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
    template = resources.files("microclaw").joinpath("history_viewer.html").read_text()
    assert template.count(TOKEN) == 1


def test_view_history_embeds_parseable_json(tmp_path):
    src = tmp_path / "20990101_000000_microclaw_history.json"
    src.write_text(json.dumps(SAMPLE_HISTORY))

    out = view_history(src, open_browser=False)
    html = out.read_text()

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
