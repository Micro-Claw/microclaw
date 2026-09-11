"""Surface R63 compaction-attribution candidates for human reading, offline.

    python3 design/82-block82c-r63-probe.py <a *_microclaw_history.jsonl>

The matching *_microclaw_usage.jsonl is discovered beside the history; an
explicit --usage path is also accepted. Reads artifacts and writes nothing.

WHAT IT ASSUMES, ALL OF IT LOAD-BEARING

* Each assistant history message corresponds in order to one usage record/API
  call. Calls and human turns are numbered from 1; string user messages start
  turns, just as in the cost reconstruction. Usage turn IDs must follow those
  same boundaries. Missing/misaligned records cannot pin replay partitions.
* The real ConversationStore.model_messages(history[:i]) reproduces the context
  under the tree and window being tested. Usage compaction counts are checked
  on every call; disagreement is reported and partitions remain replay estimates,
  not a claim about the historical context. A usage log from a tree with a
  different estimate_tokens pins WHEN compaction fired, not WHERE it cut.
  Compare current/recorded estimated tokens only while both are uncompacted:
  near 1 supports estimator equivalence and replay partition trust; away from 1
  indicates a different estimator and a replay context the model never saw.
  Missing or nonpositive recorded estimates cannot contribute to this ratio.
  Without usage, partitions and compactions are replay estimates only, and
  usage-pinned timestamps and turn IDs are unavailable.
* A mention is a case-sensitive, whole-identifier occurrence of a tool name
  from the shipped schema or this session's tool_use blocks, in assistant text
  blocks/string content only. Arguments, thinking and tool results are excluded.
  Aliases, paraphrases and unknown invented tool names are outside coverage.
  Sentences are split on punctuation followed by whitespace or on newlines.
* Classification uses calls BEFORE the assistant response, including the whole
  live window, not just its current turn. A tool called only later is labelled
  not-yet-called; a previously called tool missing from the capped checkpoint
  is labelled omitted-from-checkpoint. Neither is called never-called.
* Exposure is measured from each compaction to its last checkpoint-only mention
  before the next compaction (zero means same turn; none means no observation).
  These are candidates, not judgements of temporal phrasing or evidence of cause.
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
import re
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from microclaw.conversation import (AuditLog, ConversationStore,
    DEFAULT_CONTEXT_HIGH_WATER_TOKENS, DEFAULT_CONTEXT_LOW_WATER_TOKENS)
from microclaw.tools_schema import TOOLS


def called_tools(messages):
    return {block['name'] for message in messages
            if isinstance(message.get('content'), list)
            for block in message['content'] if isinstance(block, dict)
            and block.get('type') == 'tool_use'}


def score(messages, usage=None, *, high_water=DEFAULT_CONTEXT_HIGH_WATER_TOKENS,
          low_water=DEFAULT_CONTEXT_LOW_WATER_TOKENS):
    store = ConversationStore(AuditLog(None, enabled=False),
                              high_water_tokens=high_water, low_water_tokens=low_water)
    calls = sum(m.get('role') == 'assistant' for m in messages)
    if usage is not None and len(usage) != calls:
        raise ValueError(f'usage/history alignment unavailable: {len(usage)} usage records, '
                         f'{calls} assistant calls')
    session_tools = called_tools(messages)
    vocabulary = session_tools | {tool['name'] for tool in TOOLS}
    pattern = re.compile(r'(?<!\w)(?:' + '|'.join(map(re.escape, sorted(vocabulary))) + r')(?!\w)')
    events, mentions, disagreements, usage_events = [], [], [], []
    estimate_ratios = []
    usage_previous = 0
    if usage is not None:
        for n, record in enumerate(usage, 1):
            if record["compaction_count"] > usage_previous:
                usage_events.append(dict(call=n, turn_id=record["turn_id"],
                                         timestamp=record["timestamp"]))
            usage_previous = record["compaction_count"]
    turn = call = previous_count = 0
    seen = set()
    turn_ids = {}
    for index, message in enumerate(messages):
        if message.get('role') == 'user' and isinstance(message.get('content'), str):
            turn += 1
        if message.get('role') != 'assistant':
            continue
        call += 1
        context = store.model_messages(messages[:index])
        record = usage[call - 1] if usage is not None else None
        if record is not None:
            recorded_estimate = record.get('estimated_tokens')
            if (store.compaction_count == 0 and record['compaction_count'] == 0
                    and recorded_estimate is not None and recorded_estimate > 0):
                estimate_ratios.append(store.last_estimated_tokens / recorded_estimate)
            if record['compaction_count'] != store.compaction_count:
                disagreements.append(call)
            tid = record['turn_id']
            if (turn in turn_ids and turn_ids[turn] != tid) or (
                    turn not in turn_ids and tid in turn_ids.values()):
                raise ValueError(f'usage/history turn alignment disagreement at call {call}')
            turn_ids[turn] = tid
        if store.compaction_count > previous_count:
            head = json.loads(context[0]['content'][0]['text'].split('\n', 1)[1])
            events.append(dict(call=call, turn=turn,
                               turn_id=record['turn_id'] if record else None,
                               timestamp=record['timestamp'] if record and not disagreements else None,
                               kept=len(head['completed_actions_in_earlier_turns']),
                               actions=head['totals']['actions'], last_mention=None))
        previous_count = store.compaction_count
        if events:
            head = json.loads(context[0]['content'][0]['text'].split('\n', 1)[1])
            checkpoint = {a['tool'] for a in head['completed_actions_in_earlier_turns']}
            live = called_tools(context[1:])
            content = message.get('content', '')
            texts = [content] if isinstance(content, str) else [
                b['text'] for b in content if b.get('type') == 'text']
            for text in texts:
                for sentence in re.split(r'(?<=[.!?])\s+|\n+', text):
                    for match in pattern.finditer(sentence):
                        name = match.group()
                        category = ('live-window' if name in live else
                                    'checkpoint-only' if name in checkpoint else
                                    'never-called-in-this-session' if name not in session_tools else
                                    'omitted-from-checkpoint' if name in seen else 'not-yet-called')
                        distance = turn - events[-1]['turn']
                        mentions.append(dict(tool=name, category=category, sentence=sentence.strip(),
                                             call=call, turn=turn, turns_after=distance,
                                             turn_id=record['turn_id'] if record else None))
                        if category == 'checkpoint-only':
                            events[-1]['last_mention'] = distance
        seen.update(called_tools([message]))
    if disagreements:
        for event in events:
            event['timestamp'] = None
    return dict(calls=calls, turns=turn, events=events, mentions=mentions,
                usage_records=len(usage) if usage is not None else None,
                usage_events=usage_events, disagreements=disagreements,
                estimate_ratios=estimate_ratios)


def report(result):
    print(f"{result['calls']} assistant calls, {result['turns']} turns, "
          f"{len(result['events'])} compactions; usage records: {result['usage_records']}")
    if result['usage_records'] is None:
        print('Replay estimates only: usage-pinned timestamps and turn IDs unavailable.')
    else:
        for event in result['usage_events']:
            print(f"USAGE compaction at call index {event['call'] - 1} (call {event['call']}), turn {event['turn_id']}, "
                  f"timestamp {event['timestamp']}")
        if result['disagreements']:
            print(f"Usage/replay compaction counts disagree on {len(result['disagreements'])} calls; "
                  f"first at call {result['disagreements'][0]}.")
            print('Partitions and candidate distances below are replay estimates; '
                  'the exact historical partition is unavailable on this tree.')
        else:
            print('Usage/replay compaction counts and turn boundaries agree on every call.')
    if result['usage_records'] is not None:
        ratios = result['estimate_ratios']
        if ratios:
            print(f'Estimator ratio current/recorded, both uncompacted: n={len(ratios)}; '
                  f'min {min(ratios):.4f}, median {statistics.median(ratios):.4f}, '
                  f'max {max(ratios):.4f}')
            print('Near 1 supports an equivalent estimator and trustworthy replay partition; '
                  'away from 1 indicates a different estimator and a replay context '
                  'the model never saw.')
        else:
            print('Estimator ratio unavailable: no calls with both contexts uncompacted '
                  'and a positive recorded estimated_tokens value.')
    for event in result['events']:
        print(f"REPLAY compaction at call {event['call']}, turn {event['turn']} "
              f"({event['turn_id'] or 'ID unavailable'}), "
              f"timestamp {event['timestamp'] or 'unavailable'}")
        dropped = event['actions'] - event['kept']
        print(f"  actions carried {event['kept']} of {event['actions']}; dropped {dropped}; "
              f"truncated: {'yes — checkpoint-only coverage limited' if dropped else 'no'}")
        exposure = event['last_mention']
        print('  last checkpoint-only mention: ' +
              ('none observed before next compaction/session end' if exposure is None else
               f'{exposure} turns after this compaction'))
    counts = collections.Counter(m['category'] for m in result['mentions'])
    print('Prose mentions from first compaction: ' + ', '.join(
        f'{key}={counts[key]}' for key in ('live-window', 'checkpoint-only',
        'never-called-in-this-session', 'omitted-from-checkpoint', 'not-yet-called')))
    for mention in result['mentions']:
        if mention['category'] == 'checkpoint-only':
            print(f"  call {mention['call']}, turn {mention['turn']} "
                  f"({mention['turn_id'] or 'ID unavailable'}), +{mention['turns_after']} turns: "
                  f"{mention['tool']}: {mention['sentence']}")
    if not counts['checkpoint-only']:
        print('No checkpoint-only mentions found in this session.')
    print('Candidates for human reading only; temporal attribution and cause are not scored.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('history', type=Path)
    parser.add_argument('--usage', type=Path)
    parser.add_argument('--high-water', type=int, default=DEFAULT_CONTEXT_HIGH_WATER_TOKENS)
    parser.add_argument('--low-water', type=int, default=DEFAULT_CONTEXT_LOW_WATER_TOKENS)
    args = parser.parse_args()
    usage_path = args.usage or args.history.with_name(
        args.history.name.replace('_history.jsonl', '_usage.jsonl'))
    def read(path):
        return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()
                if line.strip()]
    try:
        usage = read(usage_path) if args.usage or usage_path.exists() else None
        result = score(read(args.history), usage, high_water=args.high_water, low_water=args.low_water)
    except (ValueError, KeyError, OSError) as exc:
        parser.error(str(exc))
    print(f'{args.history.name}; replay window {args.high_water}/{args.low_water}')
    report(result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
