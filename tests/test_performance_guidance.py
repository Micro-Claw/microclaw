"""Structure guards for the surfaces changed by 79b; mutation controls below."""
import re
from pathlib import Path
import pytest
from microclaw import agent, tools
from microclaw.tools_schema import TOOLS
from microclaw.skills import _parse_skill

ROOT = Path(__file__).resolve().parents[1]


def guidance():
    import json
    return json.dumps(TOOLS) + '\n' + '\n'.join(p.read_text(encoding='utf-8') for p in (ROOT/'microclaw/skills').glob('*/SKILL.md'))


def check_routes(text):
    # Route tokens share these suffixes; all six must be present and originate
    # in the actual result function, rather than a parallel allowed-name list.
    used = set(re.findall(r'\b\w+_(?:plan|handoff|clock|delay|axis)\b', text))
    used -= {'hook_action_plan', 'action_plan', 'position_axis', 'time_axis'}
    returned = set()
    for delay in (None, 0, 2):
        for hook, adaptive in ((None, False), (object(), False), (object(), True)):
            result = tools._single_run_timing(n_frames=3, interval_s=delay, hook=hook, adaptive=adaptive)
            returned.update((result['strategy'], result['dispatch']))
    assert returned <= used
    assert used <= returned, used - returned


def check_thresholds(text):
    assert not re.search(r'(?:>=?|<=?|at least|minimum|below|above)\s*\d+(?:\.\d+)?\s*(?:ms|milliseconds)\b',text,re.I)


def check_moved(prompt):
    assert 'interval_s=0 means' not in prompt
    assert '`snr_observer` is observation-only:' not in prompt


def test_guidance_routes_and_mutation():
    text = guidance()
    check_routes(text)
    with pytest.raises(AssertionError): check_routes(text+' invented_handoff')


def test_no_sequencing_threshold_and_mutation():
    import json
    check_thresholds(json.dumps(TOOLS))
    with pytest.raises(AssertionError): check_thresholds('Per-frame sequencing requires at least 1 ms')


def test_parameter_rules_not_prompt_and_mutation():
    check_moved(agent.SYSTEM_PROMPT)
    params = next(t for t in TOOLS if t['name']=='run_timelapse')['input_schema']['properties']
    assert 'no_requested_delay' in params['interval_s']['description']
    assert 'snr_observer' in params['hook_strategy']['description']
    for duplicate in ('interval_s=0 means','`snr_observer` is observation-only:'):
        with pytest.raises(AssertionError): check_moved(agent.SYSTEM_PROMPT+duplicate)


def test_skill_frontmatter_and_mutation(tmp_path):
    for name in ('smlm','htsmlm','hook-authoring'):
        path = ROOT/'microclaw/skills'/name/'SKILL.md'
        assert _parse_skill(path).name == name
        broken = tmp_path/'SKILL.md'
        broken.write_text(path.read_text(encoding='utf-8').replace('---\n','',1), encoding='utf-8')
        with pytest.raises(RuntimeError,match='frontmatter'): _parse_skill(broken)
    text=(ROOT/'microclaw/skills/htsmlm/SKILL.md').read_text(encoding='utf-8')
    assert all(word in text for word in ('2026-09-08','30b6bfd','one-second','1,000','three-frame','later version'))
