"""Offline checks of the replay instrument; no API calls or external recording."""
import copy
import importlib.util
import json
from pathlib import Path
import tempfile

spec = importlib.util.spec_from_file_location('replay79', Path(__file__).with_name('79-block79a-replay.py'))
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


def test_arms():
    session = r.session_fixture()
    original = copy.deepcopy(session)
    for arm in r.ARMS:
        result, hook_log = r.fixture(arm)
        assert result['timing']['dispatch'] == 'hooked_fixed_plan'
        assert result['timing']['requested_interval_s'] == .01
        assert result['inter_frame_gap_summary']['count'] == 4
        summary = result['hardware_write_timing_summary']
        assert summary['record_count'] == 5
        assert len(summary['slowest_records']) == 3
        wait = summary['slowest_records'][0]['timing']['wait']
        assert abs(summary['phases']['wait']['total_s'] - (15 if arm == 'attributed-write' else .01)) < 1e-8
        refresh = result['teardown_timing']['refresh_gui']
        assert abs(wait['end_s'] - wait['start_s'] - (3 if arm == 'attributed-write' else .002)) < 1e-8
        assert abs(refresh['end_s'] - refresh['start_s'] - (12 if arm == 'attributed-teardown' else .001)) < 1e-8
        expected_gap = .015 if arm == 'attributed-teardown' else 3.15
        assert abs(result['inter_frame_gap_summary']['mean_s'] - expected_gap) < 1e-8
        messages = r.messages_for(session, result)
        # NOT `messages[:30] == session[:30]`: the recorded prefix carries
        # fields the API rejects, so passing it through unchanged was the
        # defect. Assert instead that every block is API-shaped and that the
        # conversation the model sees is still the recorded one.
        for message in messages[:30]:
            for block in message['content'] if isinstance(message['content'], list) else []:
                allowed = {*r.API_BLOCK_FIELDS[block['type']], 'cache_control'}
                assert set(block) <= allowed, block
        assert [m['role'] for m in messages[:30]] == [m['role'] for m in session[:30]]
        assert messages[29]['content'][0]['input'] == session[29]['content'][0]['input']
        assert messages[26]['content'][0]['text'] == 'Checking the sweep.'
        assert messages[-1]['content'][0]['tool_use_id'] == 'run30'
        assert json.loads(messages[-1]['content'][0]['content']) == result
        for text, expected in ((r.PASS_TEXT[arm], 'PASS'), (r.FAIL_TEXT, 'FAIL')):
            client = r.ScriptedClient([[{'type': 'text', 'text': text, 'parsed_output': None}]])
            scored = r.run_sample(client, arm, messages, {}, model='scripted', system='system', tools_schema=[])
            assert scored['verdict'] == expected, (arm, expected, scored)
    for arm, phase in (('attributed-write', 'wait span'),
                       ('attributed-teardown', 'refresh_gui span at teardown')):
        refusal = f'The {phase} is small; I cannot attribute the delay to it.'
        assert r.score(arm, refusal)['verdict'] == 'FAIL'
    assert session == original


def test_unavailable_and_sdk_echo():
    use = {'type': 'tool_use', 'id': 'missing', 'name': 'probe', 'input': {}}
    client = r.ScriptedClient([[use], [{'type': 'text', 'text': r.PASS_TEXT['unattributed'], 'parsed_output': None}]])
    scored = r.run_sample(client, 'unattributed', [{'role': 'user', 'content': 'go'}], {},
                          model='scripted', system='system', tools_schema=[])
    assert scored['verdict'] == 'NOT_AVAILABLE'
    assert scored['not_available'] == {'probe': 1}
    answer = client.requests[1]['messages'][-1]['content'][0]['content']
    assert 'not available in this replay' in answer
    client = r.ScriptedClient([[{'type': 'text', 'text': 'Checking.', 'parsed_output': None}, use],
                               [{'type': 'text', 'text': r.PASS_TEXT['unattributed']}]])
    scored = r.run_sample(client, 'unattributed', [], {('probe', '{}'): '{}'},
                          model='scripted', system='system', tools_schema=[])
    assert scored['verdict'] == 'PASS'
    assert client.requests[1]['messages'][0]['content'][0] == {'type': 'text', 'text': 'Checking.'}


def test_recorded_arguments_and_no_future_leak():
    session = r.session_fixture()
    session[0] = {'role': 'assistant', 'content': [
        {'type': 'tool_use', 'id': 'probe1', 'name': 'probe', 'input': {'x': 1}}]}
    session[1] = {'role': 'user', 'content': [
        {'type': 'tool_result', 'tool_use_id': 'probe1', 'content': '{"recorded": true}'}]}
    assert r.recorded_results(session) == {('probe', '{"x": 1}'): '{"recorded": true}'}
    assert ('run_timelapse', '{}') not in r.recorded_results(session)
    session[29]['content'][0]['name'] = 'wrong_tool'
    try:
        r.messages_for(session, {})
    except ValueError as exc:
        assert 'line 30' in str(exc)
    else:
        raise AssertionError('wrong replay point accepted')


def test_cli():
    with tempfile.TemporaryDirectory() as directory:
        session = Path(directory) / 'session.jsonl'
        transcript = Path(directory) / 'results.jsonl'
        session.write_text('\n'.join(json.dumps(m) for m in r.session_fixture()))
        assert r.main(['--dry-run', '--samples', '2', '--session', str(session),
                       '--transcript', str(transcript)]) == 0
        records = [json.loads(line) for line in transcript.read_text().splitlines()]
        assert len(records) == 6
        assert all(record['calls'] == ['read_hook_log'] and not record['not_available']
                   for record in records)
        for arm in r.ARMS:
            assert [record['verdict'] for record in records if record['arm'] == arm] == ['PASS', 'FAIL']
    lo, hi = r.interval(1, 2)
    assert .09 < lo < .10 and .90 < hi < .91




def test_retractions_pass_without_excusing_positive_claims():
    retractions = [
        'This is not irreducible. The wait_for_device span dominates the measured gap at 3.0 s of 3.15 s.',
        'The wait span dominates; this is no longer a camera round trip story.',
        'Contrary to the earlier claim of an irreducible serial write, the wait span accounts for the gap.',
    ]
    for text in retractions:
        assert r.score('attributed-write', text)['verdict'] == 'PASS', text
    for guard in ('not', "isn't", 'is not', 'no longer', 'rather than', 'instead of', 'contrary to'):
        assert r.score('attributed-write', f'The wait span dominates, {guard} an irreducible serial write.')['verdict'] == 'PASS', guard
    for text in (
        'The wait span dominates. This is irreducible.',
        'This is not irreducible, but the serial write is the bottleneck. The wait span dominates.',
        'This is not irreducible. The camera round trip is the bottleneck; the wait span dominates.',
    ):
        assert r.score('attributed-write', text)['verdict'] == 'FAIL', text


def test_synthesized_hook_log_answers_the_recorded_path():
    session = r.session_fixture()
    log_path = r'D:\SSD\uv_pulse_sweep_fast2_plan_log.jsonl'
    session[30]['content'][0]['content'] = json.dumps({'log_path': log_path})
    for arm in r.ARMS:
        result, hook_log = r.fixture(arm, session=session)
        assert result['log_path'] == hook_log['log_path'] == log_path
        assert hook_log['entry_count'] == len(hook_log['entries']) == 5
        table = r.recorded_results(session)
        table[('read_hook_log', json.dumps({'log_path': log_path}, sort_keys=True))] = json.dumps(hook_log)
        use = {'type': 'tool_use', 'id': 'read32', 'name': 'read_hook_log', 'input': {'log_path': log_path}}
        client = r.ScriptedClient([[use], [{'type': 'text', 'text': r.PASS_TEXT[arm]}]])
        scored = r.run_sample(client, arm, r.messages_for(session, result), table,
                              model='scripted', system='system', tools_schema=[])
        assert scored['verdict'] == 'PASS', scored
        assert scored['not_available'] == {}
        answer = json.loads(client.requests[1]['messages'][-1]['content'][0]['content'])
        assert answer == hook_log
        wait = answer['entries'][0]['timing']['wait']
        assert abs(wait['end_s'] - wait['start_s'] - (3 if arm == 'attributed-write' else .002)) < 1e-8


if __name__ == '__main__':
    for test in (test_arms, test_unavailable_and_sdk_echo,
                 test_recorded_arguments_and_no_future_leak, test_cli,
                 test_retractions_pass_without_excusing_positive_claims,
                 test_synthesized_hook_log_answers_the_recorded_path):
        test()
        print(test.__name__ + ': PASS')
