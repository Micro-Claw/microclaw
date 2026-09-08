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
        result = r.fixture(arm)
        assert result['timing']['dispatch'] == 'hooked_fixed_plan'
        assert result['timing']['requested_interval_s'] == .01
        assert result['inter_frame_gap_summary']['count'] == 4
        assert len(result['hardware_write_records']) == 5
        wait = result['hardware_write_records'][0]['timing']['wait']
        refresh = result['teardown_timing']['refresh_gui']
        assert abs(wait['end_s'] - wait['start_s'] - (3 if arm == 'attributed-write' else .002)) < 1e-8
        assert abs(refresh['end_s'] - refresh['start_s'] - (12 if arm == 'attributed-teardown' else .001)) < 1e-8
        expected_gap = .015 if arm == 'attributed-teardown' else 3.15
        assert abs(result['inter_frame_gap_summary']['mean_s'] - expected_gap) < 1e-8
        messages = r.messages_for(session, result)
        assert messages[:30] == session[:30]
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
        for arm in r.ARMS:
            assert [record['verdict'] for record in records if record['arm'] == arm] == ['PASS', 'FAIL']
    lo, hi = r.interval(1, 2)
    assert .09 < lo < .10 and .90 < hi < .91


if __name__ == '__main__':
    for test in (test_arms, test_unavailable_and_sdk_echo,
                 test_recorded_arguments_and_no_future_leak, test_cli):
        test()
        print(test.__name__ + ': PASS')
