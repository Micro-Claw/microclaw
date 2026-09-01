"""Execute the browser recovery reconciler's pure functions under node."""
import json
import shutil
import subprocess
from importlib import resources

import pytest

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")


def reconcile(payload, *, confirm_id=None, grant_ids=()):
    path = resources.files("microclaw").joinpath("recovery.js")
    script = (
        "global.window = {};\n"
        f"require({json.dumps(str(path))});\n"
        "const calls = [];\n"
        "const recovery = window.Recovery.create({\n"
        f"  fetch: async (url) => ({{ json: async () => ({json.dumps(payload)}) }}),\n"
        "  now: () => 1234,\n"
        f"  getConfirmId: () => {json.dumps(confirm_id)},\n"
        f"  getGrantIds: () => {json.dumps(list(grant_ids))},\n"
        "  showGrants: (grants) => calls.push(['grants', grants]),\n"
        "  showConfirm: (pending) => calls.push(['confirm', pending]),\n"
        "  hideConfirm: (id) => calls.push(['hide', id]),\n"
        "});\n"
        "recovery.reconcileConfirmation().then((state) => {\n"
        "  process.stdout.write(JSON.stringify({ calls, state, now: recovery.now() }));\n"
        "});\n"
    )
    return json.loads(subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, check=True
    ).stdout)


def test_boot_reconciliation_renders_grants_and_pending_confirmation():
    pending = {"id": "c1", "summary": "Proceed?", "grants": [{"id": "g1"}]}
    result = reconcile(pending)
    assert result == {
        "calls": [["grants", [{"id": "g1"}]], ["confirm", pending]],
        "state": pending,
        "now": 1234,
    }


def test_boot_reconciliation_with_no_pending_confirmation_is_a_no_op_when_empty():
    state = {"grants": []}
    result = reconcile(state)
    assert result["calls"] == []
    assert result["state"] == state


def test_same_pending_id_and_grant_ids_are_reconciliation_no_ops():
    state = {"id": "c1", "grants": [{"id": "g1"}, {"id": "g2"}]}
    assert reconcile(state, confirm_id="c1", grant_ids=("g1", "g2"))["calls"] == []


def test_missing_pending_confirmation_hides_the_rendered_banner():
    result = reconcile({"grants": []}, confirm_id="c1")
    assert result["calls"] == [["hide", "c1"]]
