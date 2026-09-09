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


def check_review_contracts(schema):
    by_name = {tool['name']: tool for tool in schema}
    for name in ('run_zstack', 'run_timelapse', 'run_multiposition_acquisition',
                 'run_tile_acquisition', 'run_adaptive_survey'):
        hook = by_name[name]['input_schema']['properties']['hook_strategy']['description']
        assert 'snr_observer' in hook and 'read_hook_log' in hook
        assert 'run_adaptive_survey' in hook or name == 'run_adaptive_survey'
    zstack = by_name['run_zstack']
    assert 'max_frames' not in zstack['input_schema']['properties']['hook_strategy']['description']
    assert 'Acquire the requested Z planes' in zstack['description']
    assert 'fixed-length movie' in by_name['run_timelapse']['description']
    params = by_name['run_timelapse']['input_schema']['properties']
    assert 'silently produces blank frames' in params['laser_slot']['description']
    multi = by_name['run_multiposition_acquisition']['input_schema']['properties']
    assert 'read_hook_log takes one of them at a time' in multi['hook_strategy']['description']
    import json
    for name in ('run_zstack', 'run_timelapse', 'run_multiposition_acquisition',
                 'run_tile_acquisition', 'run_adaptive_survey'):
        text = json.dumps(by_name[name])
        assert 'on this rig' not in text
        assert text.count('ships no') <= 1


def test_review_contracts_with_parameter_mutations():
    import copy
    check_review_contracts(TOOLS)
    for name, parameter, phrase in (
        *((name, 'hook_strategy', 'snr_observer') for name in (
            'run_zstack', 'run_timelapse', 'run_multiposition_acquisition',
            'run_tile_acquisition', 'run_adaptive_survey')),
        ('run_zstack', 'hook_strategy', 'run_adaptive_survey'),
        ('run_timelapse', 'laser_slot', 'silently produces blank frames'),
        ('run_multiposition_acquisition', 'hook_strategy', 'read_hook_log takes one of them at a time'),
    ):
        mutated = copy.deepcopy(TOOLS)
        param = next(t for t in mutated if t['name'] == name)['input_schema']['properties'][parameter]
        param['description'] = param['description'].replace(phrase, '')
        with pytest.raises(AssertionError):
            check_review_contracts(mutated)
