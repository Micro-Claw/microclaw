"""Offline instrument checks, including controls that fire; no session file."""
import copy
import importlib.util
import inspect
import json
import tempfile
from contextlib import ExitStack, redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('route79b', Path(__file__).with_name('79-block79b-route-replay.py'))
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


def test_first_acquisition_boundary():
    """Also runnable with pre-79b 79a: fail on behavior, not a new keyword."""
    use = dict(type='tool_use',id='acq',**r.SCENARIOS['native']['passing'])
    client = r.attribution.ScriptedClient([[use]])
    kwargs = dict(model='scripted',system='system',tools_schema=[],max_turns=1)
    if 'acquisition_tools' in inspect.signature(r.run_sample).parameters:
        kwargs['acquisition_tools'] = {'run_timelapse'}
    result = r.run_sample(client,'unattributed',[],{},**kwargs)
    assert result['verdict'] == 'ACQUISITION', result
    assert result['acquisition'] == use
    assert result['not_available'] == {}


def test_call_criteria_mutations():
    for name, declaration in r.SCENARIOS.items():
        assert r.score_call(name,declaration['passing'])['verdict']=='PASS'
        assert r.score_call(name,declaration['wrong'])['verdict']=='FAIL', name
        for key in declaration['passing']['input']:
            mutant=copy.deepcopy(declaration['passing'])
            mutant['input'][key]='wrong'
            assert r.score_call(name,mutant)['verdict']=='FAIL', (name,key)
            if key != 'acquisition_order':
                mutant=copy.deepcopy(declaration['passing'])
                del mutant['input'][key]
                assert r.score_call(name,mutant)['verdict']=='FAIL', (name,key,'missing')
        assert r.score_call(name,declaration['passing'],r.attribution.FAIL_TEXT)['verdict']=='FAIL'
        assert r.score_call(name,declaration['passing'],"I won't call it irreducible.")['verdict']=='PASS'
    assert r.SCENARIOS['per-field']['kind']=='regression'


def test_fixture_discovery_and_boundary_order():
    with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
        fixtures=r.Fixtures(Path(directory),stack)
        for name,args in [('list_devices',{}),('list_hooks',{}),('list_mm_plugins',{}),('get_system_state',{}),('get_device_property',{'device':'Trigger','property':'Duration'}),('list_device_properties',{'device':'Trigger'}),('describe_hook',{'name':'mean_stop'}),('describe_hook',{'name':'blink_stop'}),('load_skill',{'name':'htsmlm'})]:
            payload=json.loads(fixtures.get((name,json.dumps(args))))
            assert 'error' not in payload,(name,payload)
            if name=='describe_hook': assert not payload['resolve_refusal']['would_refuse'],payload
        client=r.attribution.ScriptedClient([
            [dict(type='tool_use',id='discover',name='list_devices',input={})],
            [dict(type='tool_use',id='again',name='list_hooks',input={}),dict(type='tool_use',id='acq',**r.SCENARIOS['native']['passing']),dict(type='tool_use',id='later',name='run_mda',input={})]])
        result=r.run_route(client,'native',fixtures,'system',[],'scripted')
        assert result['verdict']=='PASS',result
        assert result['calls']==['list_devices','list_hooks','run_timelapse']
        assert len(client.requests)==2
        assert json.loads(client.requests[1]['messages'][-1]['content'][0]['content'])=={'devices':['Camera','Trigger']}
        missing=r.attribution.ScriptedClient([[dict(type='tool_use',id='x',name='unavailable',input={})],[dict(type='tool_use',id='a',**r.SCENARIOS['native']['passing'])]])
        assert r.run_route(missing,'native',fixtures,'system',[],'scripted')['verdict']=='NOT_AVAILABLE'
        prose=r.attribution.ScriptedClient([[dict(type='text',text='I will run_timelapse')]])
        assert r.run_route(prose,'native',fixtures,'system',[],'scripted')['verdict']=='NO_DECISION'


def test_fixture_hooks_execute():
    import numpy as np
    from microclaw.hook_decisions import ContinueAcquisition, StopAcquisition
    for source,name in [(r.MEAN_SOURCE,'MeanStop'),(r.BLINK_SOURCE,'BlinkStop')]:
        namespace={}
        exec(source,namespace)
        hook=namespace[name]()
        if name=='MeanStop':
            assert isinstance(hook.analyze_frame(np.full((2,2),20),{}).actions[0],ContinueAcquisition)
            assert isinstance(hook.analyze_frame(np.zeros((2,2)),{}).actions[0],StopAcquisition)
        else:
            actions=[hook.analyze_frame(np.zeros((2,2)),{}).actions[0] for _ in range(1003)]
            assert all(isinstance(a,ContinueAcquisition) for a in actions[:-1])
            assert isinstance(actions[-1],StopAcquisition)


def test_meter_budget_and_truncation():
    class Live:
        def __init__(self,*args): self.cost=0
        def spent(self): return self.cost
        def send(self,**kwargs):
            self.cost+=1
            return {'content':[dict(type='tool_use',id='a',**r.SCENARIOS['native']['passing'])], 'stop_reason':'end_turn'}
    with tempfile.TemporaryDirectory() as directory:
        session=Path(directory)/'session.jsonl'
        transcript=Path(directory)/'out.jsonl'
        session.write_text('\n'.join(json.dumps(m) for m in r.attribution.session_fixture()))
        output=StringIO()
        with patch.object(r.prior,'LiveClient',Live), redirect_stdout(output):
            assert r.main(['--tree',str(Path(__file__).resolve().parents[1]),'--samples','2','--session',str(session),'--transcript',str(transcript),'--budget','1'])==0
        records=[json.loads(line) for line in transcript.read_text().splitlines()]
        assert len(records)==1 and records[0]['spent']==1
        assert json.loads(output.getvalue().splitlines()[0])['spent']==1
    class Truncated(Live):
        def send(self,**kwargs): return {'content':[], 'stop_reason':'max_tokens'}
    result=r.run_sample(r.MeteredClient(Truncated()),'unattributed',[],{},model='scripted',system='',tools_schema=[],acquisition_tools={'run_timelapse'})
    assert result['verdict']=='NO_DECISION'


def test_live_adapter_preserves_truncation():
    from collections import Counter
    from contextlib import nullcontext
    response=SimpleNamespace(content=[],stop_reason='max_tokens',usage=SimpleNamespace(input_tokens=4,output_tokens=2))
    client=r.prior.LiveClient.__new__(r.prior.LiveClient)
    client._max_tokens=10
    client.usage=Counter()
    client._client=SimpleNamespace(messages=SimpleNamespace(stream=lambda **kwargs:nullcontext(SimpleNamespace(get_final_message=lambda:response))))
    result=client.send(model='scripted',system='system',messages=[],tools=[{'name':'probe'}])
    assert result.get('stop_reason')=='max_tokens', result
    assert client.usage['input_tokens']==4 and client.spent()>0


def test_cli_and_tree():
    with tempfile.TemporaryDirectory() as directory:
        transcript=Path(directory)/'dry.jsonl'
        with redirect_stdout(StringIO()):
            assert r.main(['--tree',str(Path(__file__).resolve().parents[1]),'--samples','2','--dry-run','--transcript',str(transcript)])==0
        rows=[json.loads(line) for line in transcript.read_text().splitlines()]
        assert len(rows)==20
        assert all(row['turns'] == (2 if row['scenario'] in r.SCENARIOS else 1) for row in rows)
        for scenario in [*r.SCENARIOS,*r.attribution.ARMS]:
            assert [v['verdict'] for v in rows if v['scenario']==scenario]==['PASS','FAIL']
        import microclaw
        assert Path(microclaw.__file__).resolve().parents[1]==Path(__file__).resolve().parents[1]


def test_sweep_deadline_predicate():
    for delay, expected in ((.002, 'PASS'), (.02, 'PASS'), (1.0, 'PASS'),
                            (0, 'FAIL'), (.0001, 'FAIL'), (-1, 'FAIL'),
                            (float('nan'), 'FAIL'), (True, 'FAIL')):
        acquisition = copy.deepcopy(r.SCENARIOS['sweep']['passing'])
        acquisition['input']['interval_s'] = delay
        assert r.score_call('sweep', acquisition)['verdict'] == expected, delay
    assert '0.05' not in r.SCENARIOS['sweep']['user']
    # Predicate depends on the whole frame count, not a fixed cutoff.
    from microclaw.tools import _refuse_sequenced_time_axis
    _refuse_sequenced_time_axis(5, .001, hardware_actions=True)
    try:
        _refuse_sequenced_time_axis(4008, .001, hardware_actions=True)
    except ValueError:
        pass
    else:
        raise AssertionError('long-run deadline collision was accepted')


def test_sample_turn_counts():
    use = dict(type='tool_use', id='probe', name='probe', input={})
    acquisition = dict(type='tool_use', id='acq', **r.SCENARIOS['native']['passing'])
    for turns, expected in (([[acquisition]], 1), ([[use], [acquisition]], 2),
                            ([[dict(type='text', text='No decision')]], 1)):
        client = r.attribution.ScriptedClient(turns)
        result = r.run_sample(client, 'unattributed', [], {('probe', '{}'): '{}'},
                              model='scripted', system='', tools_schema=[],
                              acquisition_tools={'run_timelapse'})
        assert result.get('turns') == expected, result
    client = r.attribution.ScriptedClient([[use], [use]])
    result = r.run_sample(client, 'unattributed', [], {('probe', '{}'): '{}'},
                          model='scripted', system='', tools_schema=[], max_turns=2)
    assert result['verdict'] == 'NO_DECISION' and result.get('turns') == 2


if __name__=='__main__':
    for name,fn in list(globals().items()):
        if name.startswith('test_'):
            fn()
            print(name+': PASS')
