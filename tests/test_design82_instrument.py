"""Exercise the design instruments through production builders and replay."""
import importlib.util
import json
from pathlib import Path

import pytest

from microclaw import agent, tools_schema
from microclaw.conversation import AuditLog, ConversationStore


def load_script(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'),
        Path(__file__).parents[1] / 'design' / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture_messages():
    messages = []
    for turn in range(12):
        name = 'snap_and_analyze' if turn == 0 else 'get_system_state'
        messages.extend([
            {'role': 'user', 'content': f'Turn {turn} ' + 'payload ' * 600},
            {'role': 'assistant', 'content': [
                {'type': 'tool_use', 'id': f't{turn}', 'name': name, 'input': {}}]},
            {'role': 'user', 'content': [
                {'type': 'tool_result', 'tool_use_id': f't{turn}', 'content': 'done'}]},
            {'role': 'assistant', 'content': [{'type': 'text', 'text':
                'Earlier I used snap_and_analyze. I also used get_system_state. '
                'Consider move_stage_xy.'}]},
        ])
    return messages


@pytest.fixture
def history(tmp_path):
    path = tmp_path / 'fixture_microclaw_history.jsonl'
    path.write_text(''.join(json.dumps(m) + '\n' for m in fixture_messages()), encoding='utf-8')
    return path


def test_d8_default_follows_real_breakpoints(monkeypatch, history, capsys):
    instrument = load_script('82-session-cost-reconstruction')
    blocks = agent._system_blocks()
    blocks += agent._with_cache_breakpoint([{'role': 'user', 'content': 'probe'}])[-1]['content']
    blocks += tools_schema.TOOLS_CACHED
    ttls = {b['cache_control'].get('ttl', '5m') for b in blocks if 'cache_control' in b}
    assert len(ttls) == 1
    expected = ttls.pop()
    monkeypatch.setattr('sys.argv', ['instrument', str(history.parent)])
    assert instrument.main() == 0
    assert f'TTL {expected}' in capsys.readouterr().out


@pytest.mark.parametrize('source', ['system', 'messages', 'tools'])
def test_d8_mixed_real_breakpoints_refuse(monkeypatch, history, source):
    instrument = load_script('82-session-cost-reconstruction')
    if source == 'tools':
        monkeypatch.delitem(tools_schema.TOOLS_CACHED[-1]['cache_control'], 'ttl')
    else:
        attribute = '_system_blocks' if source == 'system' else '_with_cache_breakpoint'
        original = getattr(agent, attribute)
        def changed(*args, **kwargs):
            value = original(*args, **kwargs)
            block = value[0] if source == 'system' else value[-1]['content'][-1]
            block['cache_control'].pop('ttl')
            return value
        monkeypatch.setattr(agent, attribute, changed)
    monkeypatch.setattr('sys.argv', ['instrument', str(history.parent)])
    with pytest.raises(SystemExit, match='2'):
        instrument.main()


def test_d8_selected_rate_prices_replay_and_main(monkeypatch, history, capsys):
    instrument = load_script('82-session-cost-reconstruction')
    original = instrument.replay
    rows = []
    def capture(*args, **kwargs):
        result = original(*args, **kwargs)
        rows.append(result[0])
        return result
    monkeypatch.setattr(instrument, 'replay', capture)
    outputs = []
    for ttl in ['5m', '1h']:
        monkeypatch.setattr('sys.argv', ['instrument', str(history.parent), '--cache-ttl', ttl])
        assert instrument.main() == 0
        outputs.append(capsys.readouterr().out)
    assert rows[0]['write'] == rows[1]['write'] > 0
    assert rows[1]['cost'] - rows[0]['cost'] == pytest.approx(rows[0]['write'] * 3.75 / 1e6)
    for row, output, rate in zip(rows, outputs, [6.25, 10.0]):
        assert f"cache write ${row['write'] * rate / 1e6:.2f} (" in output
        assert f"{row['cost']:.2f}" in output


def test_d8_window_reaches_store(monkeypatch, history, capsys):
    instrument = load_script('82-session-cost-reconstruction')
    original = instrument.replay
    rows = []
    def capture(*args, **kwargs):
        result = original(*args, **kwargs)
        rows.append(result[0])
        return result
    monkeypatch.setattr(instrument, 'replay', capture)
    for high, low in [(120000, 90000), (5000, 3000)]:
        monkeypatch.setattr('sys.argv', ['instrument', str(history.parent),
            '--high-water', str(high), '--low-water', str(low)])
        assert instrument.main() == 0
        assert f'window {high}/{low}' in capsys.readouterr().out
    assert rows[1]['compactions'] > rows[0]['compactions']


def test_r63_real_checkpoint_partition_and_usage(capsys):
    probe = load_script('82-block82c-r63-probe')
    messages = fixture_messages()
    store = ConversationStore(AuditLog(None, enabled=False),
                              high_water_tokens=5000, low_water_tokens=3000)
    usage = []
    turn = 0
    for index, message in enumerate(messages):
        if message['role'] == 'user' and isinstance(message['content'], str):
            turn += 1
        if message['role'] == 'assistant':
            store.model_messages(messages[:index])
            usage.append(dict(compaction_count=store.compaction_count,
                              turn_id=f'turn-{turn}', timestamp=f'fixture-call-{len(usage)}'))
    result = probe.score(messages, usage, high_water=5000, low_water=3000)
    assert result['events']
    assert not result['disagreements']
    categories = {(m['tool'], m['category']) for m in result['mentions']}
    assert ('snap_and_analyze', 'checkpoint-only') in categories
    assert ('get_system_state', 'live-window') in categories
    assert ('move_stage_xy', 'never-called-in-this-session') in categories
    assert any(e['last_mention'] is not None for e in result['events'])
    probe.report(result)
    assert 'Earlier I used snap_and_analyze.' in capsys.readouterr().out
    offline = probe.score(messages, high_water=5000, low_water=3000)
    probe.report(offline)
    assert 'usage-pinned timestamps and turn IDs unavailable' in capsys.readouterr().out
    assert offline['mentions'] == [{**m, 'turn_id': None} for m in result['mentions']]
    usage[0]['compaction_count'] = 1
    mismatch = probe.score(messages, usage, high_water=5000, low_water=3000)
    assert mismatch['disagreements'][0] == 1


def test_r63_reports_real_action_cap(capsys):
    probe = load_script('82-block82c-r63-probe')
    messages = fixture_messages()
    messages[1]['content'] = [dict(type='tool_use', id=f'old-{n}',
        name='snap_and_analyze', input={}) for n in range(205)]
    messages[2]['content'] = [dict(type='tool_result', tool_use_id=f'old-{n}',
        content='done') for n in range(205)]
    result = probe.score(messages, high_water=5000, low_water=3000)
    capped = [e for e in result['events'] if e['actions'] > e['kept']]
    assert capped and all(e['kept'] == 200 for e in capped)
    probe.report(result)
    assert 'checkpoint-only coverage limited' in capsys.readouterr().out


def test_d8_default_changes_with_uniform_real_tree(monkeypatch, history, capsys):
    instrument = load_script('82-session-cost-reconstruction')
    original_system = agent._system_blocks
    original_messages = agent._with_cache_breakpoint
    def system(*args, **kwargs):
        blocks = original_system(*args, **kwargs)
        for block in blocks:
            if 'cache_control' in block:
                block['cache_control'].pop('ttl', None)
        return blocks
    def messages(*args, **kwargs):
        value = original_messages(*args, **kwargs)
        value[-1]['content'][-1]['cache_control'].pop('ttl')
        return value
    monkeypatch.setattr(agent, '_system_blocks', system)
    monkeypatch.setattr(agent, '_with_cache_breakpoint', messages)
    monkeypatch.delitem(tools_schema.TOOLS_CACHED[-1]['cache_control'], 'ttl')
    monkeypatch.setattr('sys.argv', ['instrument', str(history.parent)])
    assert instrument.main() == 0
    assert 'TTL 5m; cache write $6.25/MTok' in capsys.readouterr().out
