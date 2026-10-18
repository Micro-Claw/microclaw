"""88b demo gate: packages observing typed ND-TIFF MDA, scored from captured files.

Imports 88a's bridge/runner/history machinery and 87a's offline fixture installation,
store restoration and metrics scoring. No acquisition or package runtime is copied.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import statistics
import sys
import tempfile
import time
import traceback
from unittest.mock import patch

HERE = Path(__file__).resolve().parent

def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


a88 = module('gate88b_base88a', '88-block88a-demo-gate.py')
b87 = module('gate88b_base87a', '87-block87a-demo-gate.py')
e4 = b87.e4
read_json, write_json, now = a88.read_json, a88.write_json, a88.now
load, require, NotExercised = a88.load, a88.require, a88.NotExercised
# Imported gates have distinct NotExercised classes; preserve their meaning.
NOT_EXERCISED = (NotExercised, b87.NotExercised, b87.f6.NotExercised)
POINTER = '88b-gate-evidence.txt'
b87.POINTER = b87.b84.POINTER = POINTER
SIZES = (6, 1000, 12000, 2000, 200)  # repetitions, burst, session, Stop plan, Stop threshold
SELFTEST_SIZES = (6, 360, 900, 900, 200)
PACKAGES = b87.PACKAGES
R01 = ('I am about to run run_mda for 12000 frames at interval 0 with '
       'fixture-lab/photon-observer:observe_dataset attached. Before anything runs: '
       'if something looks wrong partway through, can I stop it, and how? Do not start it yet.')
WORKING_QUESTION = ("Under the frames line, does photon-observer's block show its metrics, "
                    "each with a value and an 'up to frame' number that increases while frames are acquired?")
LIMBS = {'B1': 'jobs bound to real suffixed datastore paths',
         'B2': 'completed job lifecycle, complete input and saved-frame coverage',
         'B3': 'two Stop runs: cancelled early, writer proved, observer finished',
         'B4': 'live photon metrics before acquisition completion, keeping pace',
         'B5': 'thermal live and final metrics use its own declarations',
         'B6': 'five scored repetitions: median photon/none duration <= 1.10',
         'B7': 'TIFF and feedback refusals: no consent, engine start or dataset',
         'B8': 'dispatch timing only on attached runs',
         'S1': 'literal session MDA cancelled by Stop, writer proved, job bound',
         'S2': 'operator YES on changing working line, screenshot captured'}


def features():
    from microclaw.tools_schema import ANALYSIS_TOOLS, TOOLS
    if ('run_mda' not in ANALYSIS_TOOLS or 'analysis' not in
            next(t for t in TOOLS if t['name'] == 'run_mda')['input_schema']['properties']):
        raise NotExercised('Installed slot lacks 88b MDA analysis; run step 0 (install.bat).')
    b87.features()


def plan(digests, directory, sizes=SIZES):
    reps, burst, session, stop_frames, stop_at = sizes
    rows = []
    def row(condition, rep, frames, *, abort=None, refusal=None):
        ident = f'r{rep}-{condition}' if rep is not None else condition
        settings = dict(n_frames=frames, interval_s=0, exposure_ms=10, name=ident,
                        save_dir=str(directory), save_mode='ND_TIFF')
        args = dict(settings=settings)
        if condition != 'none':
            key = 'thermal' if condition == 'thermal' else 'metrics'
            args['analysis'] = dict(adapter=PACKAGES[key] + ':observe_dataset',
                                    release_digest=digests.get(key, ''), parameters={})
        if refusal == 'tiff':
            settings['save_mode'] = 'MULTIPAGE_TIFF'
        if refusal == 'feedback':
            args['analysis_feedback'] = {}
        return dict(id=ident, kind=condition, condition=condition, rep=rep, repetition=rep,
                    tool='run_mda', input=args, frames=frames, abort=abort,
                    abort_at=stop_at if abort else None, refusal=refusal)
    for rep in range(reps):
        arms = ('none', 'photon') if rep % 2 == 0 else ('photon', 'none')
        rows.extend(row(arm, rep, burst) for arm in arms)
    rows.append(row('thermal', None, burst))
    rows.extend(row('stop', rep, stop_frames, abort='stop') for rep in range(2))
    rows.extend(row('refuse-' + why, None, burst, refusal=why) for why in ('tiff', 'feedback'))
    return rows


def flat(row):
    return dict(row, condition='metrics' if row['condition'] == 'photon' else row['condition'],
                input={**row['input']['settings'], **{k: v for k, v in row['input'].items() if k != 'settings'}})


# 87a prepare's installer/disk reservation calls its plan. Only translate the plan;
# its store backup, test trust policy, signing and offline install stay unchanged.
b87.plan = lambda digests, directory, sizes=SIZES: [
    dict(flat(r), condition=r['condition'], repetition=r['rep'] if r['condition'] in ('none', 'photon') else None)
    for r in plan(digests, directory, sizes)]


def session_input(prep):
    return dict(settings=dict(n_frames=prep['sizes'][2], interval_s=0, exposure_ms=10,
        save_mode='ND_TIFF', name=prep['session_name'], save_dir=prep['session_dir']),
        analysis=dict(adapter=PACKAGES['metrics'] + ':observe_dataset',
                      release_digest=prep['digests']['metrics'], parameters={}))


def portable(path):
    return PurePosixPath(str(path).replace('\\', '/'))


class Gate(b87.Gate, a88.Gate):
    @property
    def saved_store(self):
        return self.store_root.with_name(self.store_root.name + '.88b-saved')

    # Reuse the 87a owner marker and its guard, with this gate's distinct backup
    # and pointer. No second implementation of store mutation/restore.
    capture_history = a88.Gate.capture_history
    close_microclaw = a88.Gate.close_microclaw

    def prepare(self, *, port=4827, sizes=SIZES):
        # The admission bypass exists only on this isolated selftest instance.
        if not (self.fake and getattr(self, '_pre_block_selftest', False)):
            features()  # refuse an old slot before touching the operator's store
        if (self.out / 'prepare.json').exists():
            raise NotExercised('Prepare evidence exists; cleanup then use a new evidence folder.')
        super().prepare(port=port, sizes=sizes)
        prep = self.need('prepare')
        directory = Path(prep['save_dir'].replace('block87a-', 'block88b-'))
        prep.update(save_dir=str(directory), session_dir=str(directory / 'session'),
                    session_name='gate88b-session', runs=plan(prep['digests'], directory, sizes))
        self.save('prepare', prep)
        self.say('RECORDED: prepare')

    def capture_job(self, result, folder):
        from microclaw import tools
        for index, job in enumerate(result.get('analysis', {}).get('jobs', [])):
            started = time.perf_counter()
            bound = e4.JOB_WAIT_S
            try:
                self.wait_terminal(job, folder, index)
            except Exception as exc:
                write_json(folder / f'job-{index}-wait.json', dict(bound_s=bound, expired=True,
                    elapsed_s=time.perf_counter() - started, error=str(exc)))
                raise
            write_json(folder / f'job-{index}-wait.json', dict(bound_s=bound, expired=False,
                elapsed_s=time.perf_counter() - started))
            # Copy exactly the durable record after terminal, never the result snapshot.
            record = load(folder / f'job-{index}.json')
            if job.get('job_record_path'):
                shutil.copy2(job['job_record_path'], folder / f'job-record-{index}.json')
            require(record['state'] not in self.store.ANALYSIS_NONTERMINAL, 'Job still nonterminal')
            status = json.loads(tools.execute_tool('analysis_job_status', {'job_id': job['job_id']}, None, None))
            write_json(folder / f'status-{index}.json', status)
            path = Path(record['dataset']) / 'analysis'
            write_json(folder / 'analysis-listing.json', dict(dataset=record['dataset'],
                captured_at=now().isoformat(), entries=[dict(name=p.name, is_dir=p.is_dir())
                                                       for p in sorted(path.iterdir())]))
            self.say(f"  job={job['job_id']} bound_dataset={record['dataset']} terminal={record['state']}")

    def run_one(self, row, folder, ctrl, guard, writer):
        # 88a approves acquisition consent; 87a approves the selected package.
        original = a88.recording_confirm
        analysis = row['input'].get('analysis')
        subject = analysis['adapter'].split(':')[0] + '@' + analysis['release_digest'] if analysis else ''
        def confirm(prompts, on_write):
            acquisition = original(prompts, on_write)
            package = e4.recording_confirm(prompts, subject, on_write)
            return lambda summary, kind='action', **kw: (package if kind == 'analysis' else acquisition)(summary, kind=kind, **kw)
        directory = Path(row['input']['settings']['save_dir'])
        before = sorted(p.name for p in directory.iterdir()) if directory.exists() else []
        from microclaw import tools
        tools.SESSION_GRANTS.clear()
        with patch.object(a88, 'recording_confirm', confirm):
            result = a88.Gate.run_one(self, row, folder, ctrl, guard, writer)
        after = sorted(p.name for p in directory.iterdir()) if directory.exists() else []
        captured = load(folder / 'events.json')
        write_json(folder / 'browser-events.json', captured['events'])
        write_json(folder / 'folders.json', dict(before=before, after=after, new=sorted(set(after) - set(before))))
        state = load(folder / 'state.json')
        state['engine_start_observed'] = (state['pre']['running'] or state['post']['running'] or
            any(e['type'] in ('acquisition_construction', 'acquisition_event_submission', 'acquisition_first_frame_accounted')
                for e in captured['events']))
        state['engine_observation_source'] = 'pre/post engine reads and the sole MDA start path lifecycle diagnostics'
        write_json(folder / 'state.json', state)
        self.capture_job(result, folder)
        return result

    def measure(self):
        from microclaw import completed_dataset
        from microclaw.conversation import AcquisitionDiagnosticWriter, AuditLog
        self.close_microclaw('Measure requires MicroClaw closed.')
        prep = self.need('prepare')
        if (self.out / 'measure.json').exists():
            raise NotExercised('Measure evidence exists; refusing to overwrite.')
        _, guard, ctrl = self.connect(prep['port'])
        disk = e4.disk_check(ctrl.core, prep['disk']['dataset_root'], [flat(r) for r in prep['runs']])
        if disk['bytes_per_frame'] != prep['disk']['bytes_per_frame']:
            raise NotExercised('Camera dimensions changed after prepare.')
        self.save('measure', dict(started_at=now().isoformat(), disk=disk))
        writer = tools_writer(self.out / 'measure_microclaw_acquisitions.jsonl')
        completed = 0
        try:
            for row in prep['runs']:
                result = self.run_one(row, self.out / 'runs' / row['id'], ctrl, guard, writer)
                completed += 1
                if result.get('acquisition') in ('unterminated', 'refused'):
                    raise NotExercised('MDA did not terminate; later runs are unsafe to start.')
        finally:
            writer.close()
            completed_dataset.close_analysis_supervisor()
            self.save('measure-end', dict(completed=completed, planned=len(prep['runs']), ended_at=now().isoformat()))
        self.say('RECORDED: measure')

    def capture_r01(self, started):
        self.capture_history(started)
        messages = e4.e3._jsonl(self.out / 'session-history', '*_microclaw_history.jsonl', started)
        def texts(message):
            content = message.get('content')
            return [content] if isinstance(content, str) else [b['text'] for b in content or []
                if isinstance(b, dict) and b.get('type') == 'text']
        starts = [i for i, m in enumerate(messages) if m.get('role') == 'user' and R01 in texts(m)]
        if len(starts) != 1:
            self.save('r01', dict(question=R01, replies=[],
                unavailable='No unique literal R01 question in captured history.', source='captured history; unscored'))
            return
        replies, originals, tool_uses = [], [], []
        for message in messages[starts[0] + 1:]:
            if message.get('role') == 'user' and texts(message):
                break
            if message.get('role') == 'assistant':
                content = message.get('content')
                if isinstance(content, list):
                    tool_uses.extend(b for b in content if isinstance(b, dict) and b.get('type') == 'tool_use')
                replies.extend(texts(message))
                originals.append(message)
        self.save('r01', dict(question=R01, replies=replies, history_messages=originals, tool_uses=tool_uses,
                             source='verbatim assistant text from captured history; unscored',
                             unavailable=None if replies else 'No assistant text reply captured.'))

    def session(self):
        if self.fake:
            return self.synthetic_session()
        self.close_microclaw('Begin session with MicroClaw closed.')
        if (self.out / 'session.json').exists():
            raise NotExercised('Session evidence exists; refusing to overwrite.')
        prep, started = self.need('prepare'), now()
        args = session_input(prep)
        data = dict(started_at=started.isoformat(), input=args, answers={}, synthetic=False)
        self.save('session', data)
        self.say('STEP 1: Launch MicroClaw from its desktop icon. Put Firefox and PowerShell side by side.')
        data['answers']['launch'] = self.ask('Type DONE when both are visible.', 'launch', {'DONE'})
        self.say('STEP 2 (R01, recorded only): Paste exactly this question. Do not start anything:\n' + R01)
        data['answers']['r01'] = self.ask('Wait for the reply, then type DONE.', 'r01', {'DONE'})
        self.capture_r01(started)
        self.say('STEP 3: Paste this literal line and approve. Type DONE while frames move and metrics are visible:\n'
                 + a88.chat('run_mda', args))
        data['answers']['working'] = self.ask('Type DONE while the working line is visible.', 'working', {'DONE'})
        data['working_screenshot'] = b87.b84.screenshot(self.out / 'working.png')
        self.save('session', data)
        data['working_answer'] = self.ask(WORKING_QUESTION, 'working-line', {'YES', 'NO'})
        self.save('session', data)
        data['answers']['stop'] = self.ask("Click MicroClaw's 'Stop after the current step' button now, then type DONE.", 'stop', {'DONE'})
        data['stop_screenshot'] = b87.b84.screenshot(self.out / 'stop.png')
        self.save('session', data)
        data['answers']['reply'] = self.ask('Wait for the reply, then type DONE.', 'reply', {'DONE'})
        self.capture_history(started)
        self.say('STEP 5: Quit MicroClaw by closing its console and launcher windows.')
        data['answers']['quit'] = self.ask('Type DONE after quitting.', 'quit', {'DONE'})
        self.capture_history(started)
        self.save('session', data)
        self.close_microclaw('Session evidence collection needs MicroClaw closed.')
        _, call = a88.session_call(self.out, 'run_mda', args)
        folder = self.out / 'session-final'
        folder.mkdir(exist_ok=True)
        self.capture_job(call['result'], folder)
        self.say('RECORDED: session')

    def synthetic_session(self):
        from microclaw import completed_dataset
        from microclaw.conversation import AuditLog
        prep, started = self.need('prepare'), now()
        _, guard, ctrl = self.connect(prep['port'])
        args = session_input(prep)
        folder = self.out / 'session-run'
        writer = tools_writer(self.out / 'session_microclaw_acquisitions.jsonl')
        row = dict(id='session', tool='run_mda', input=args, abort='stop', abort_at=prep['sizes'][4], frames=prep['sizes'][2])
        try:
            result = self.run_one(row, folder, ctrl, guard, writer)
        finally:
            writer.close()
            completed_dataset.close_analysis_supervisor()
        history = self.root / ('synthetic_' + started.strftime('%H%M%S%f') + '_microclaw_history.jsonl')
        audit = AuditLog(history, enabled=True, secrets=())
        audit.append(dict(role='user', content=R01))
        audit.append(dict(role='assistant', content=[dict(type='text', text='Synthetic R01 reply: Stop aborts the MDA mid-run; Ctrl-C also aborts it.')]))
        audit.append(dict(role='user', content=a88.chat('run_mda', args)))
        audit.append(dict(role='assistant', content=[dict(type='tool_use', id='session', name='run_mda', input=args)]))
        audit.append(dict(role='user', content=[dict(type='tool_result', tool_use_id='session', content=json.dumps(result))]))
        self.capture_r01(started)
        self.capture_history(started)
        self.save('session', dict(started_at=started.isoformat(), input=args, synthetic=True,
            note='Synthetic UI, answers, history envelopes and screenshots; real tools, workers and dataset.',
            answers={key: 'DONE' for key in ('launch', 'r01', 'working', 'stop', 'reply', 'quit')},
            working_answer='YES', working_screenshot=b87.b84.screenshot(self.out / 'working.png', synthetic=True),
            stop_screenshot=b87.b84.screenshot(self.out / 'stop.png', synthetic=True)))
        shutil.copytree(folder, self.out / 'session-final')
        self.say('RECORDED: session (SYNTHETIC UI and answers)')


def tools_writer(path):
    from microclaw.conversation import AcquisitionDiagnosticWriter, AuditLog
    return AcquisitionDiagnosticWriter(AuditLog(path, enabled=True, secrets=()))


@contextmanager
def metrics_view(out, row, diagnostics):
    """87a's read-only scorer adapted to MDA's nested settings/duration.

    Its timelapse teardown boundary becomes MDA's acquisition_finished boundary,
    preserving the product timestamp. No metrics or reception times are invented.
    The temporary view is derived solely from captured files; raw evidence stays intact.
    """
    with tempfile.TemporaryDirectory(prefix='gate88b-metrics-') as temp:
        view = Path(temp)
        source, target = out / 'runs' / row['id'], view / 'runs' / row['id']
        target.mkdir(parents=True)
        for name in ('browser-events.json', 'job-0.json', 'status-0.json'):
            write_json(target / name, load(source / name))
        result = load(source / 'result.json')
        write_json(target / 'result.json', dict(result, duration_s=result['duration_breakdown']['duration_s']))
        events = []
        for event in diagnostics:
            if event.get('tool_call_id') != row['id'] or event['type'] == 'acquisition_teardown_completion':
                continue
            if event['type'] == 'acquisition_finished':
                event = dict(event, type='acquisition_teardown_completion', frames_accounted=result['frames_accounted'])
            events.append(event)
        yield view, flat(row), events


def bound(out, row, folder=None, result=None):
    folder = folder or out / 'runs' / row['id']
    result = result or load(folder / 'result.json')
    jobs = result.get('analysis', {}).get('jobs', [])
    require(len(jobs) == 1, f"{row['id']}: expected exactly one job")
    job, listing = load(folder / 'job-0.json'), load(folder / 'analysis-listing.json')
    settings = row['input']['settings']
    dataset = result.get('dataset_path')
    require(dataset == job.get('dataset') == result.get('datastore', {}).get('save_path') == listing.get('dataset'),
            f"{row['id']}: job/result/datastore/listing paths differ")
    require(portable(dataset) != portable(settings['save_dir']) / settings['name'] and
            re.fullmatch(re.escape(settings['name']) + r'_\d+', portable(dataset).name),
            f"{row['id']}: datastore path is not MM-suffixed")
    require(portable(job['output_dir']) == portable(dataset) / 'analysis' / job['job_id'], 'Wrong reserved output directory')
    require(any(e['name'] == job['job_id'] and e['is_dir'] for e in listing['entries']), 'Missing analysis job directory')
    require(job['job_id'] == jobs[0]['job_id'], 'Final record belongs to another job')
    return job


def lifecycle(job, outcome, frames):
    notifications = job.get('notifications', [])
    require([n.get('type') for n in notifications] == ['acquisition', 'writer'],
            'Expected exactly acquisition then writer: ' + str(notifications))
    acquisition, writer = notifications
    require(all(n.get('state') == 'delivered' for n in notifications), 'Lifecycle notification not delivered')
    require(acquisition['message']['outcome'] == outcome and acquisition['message']['writer'] == 'unknown', 'Wrong acquisition outcome/writer')
    require(writer['message']['state'] == 'finished', 'Writer not finished')
    require(job.get('state') == 'succeeded', 'Observer did not succeed: ' + str(job.get('failure')))
    require(job.get('result', {}).get('input_complete') is True, 'Observer input incomplete')
    require(job.get('result', {}).get('output', {}).get('frames_read') == frames, 'Observer did not read every saved frame')


def cadence(out, rows, expected):
    medians, gaps = {}, {}
    for arm in ('none', 'photon'):
        selected = [r for r in rows if r['condition'] == arm and r['rep'] >= 1]
        require(len(selected) == expected, f'Expected {expected} scored {arm} repetitions')
        values, spread = [], []
        for row in selected:
            result, ds, _, _, _ = a88.run_files(out, row)
            require(result.get('outcome') == 'completed' and ds['frames'] == row['frames'], 'Cadence run incomplete')
            duration = e4.number(result['duration_breakdown']['duration_s'], 'product duration_s')
            require(duration > 0, 'Invalid product duration')
            values.append(duration)
            # Reuse 87a's source for saved-frame gap scoring through its inherited reader.
            spacing = ds.get('spacing', {})
            with tempfile.TemporaryDirectory(prefix='gate88b-gaps-') as temp:
                write_json(Path(temp) / 'dataset.json', spacing)
                spread.extend(e4._gaps(Path(temp), flat(row)))
        medians[arm] = statistics.median(values)
        gaps[arm] = dict(p50_s=statistics.median(spread), p95_s=e4.p95(spread))
    return dict(medians_s=medians, photon_none_ratio=medians['photon'] / medians['none'], gaps=gaps,
                source='product duration_breakdown.duration_s; repetition 0 excluded; metadata gaps unscored')


def verify(gate, *, cleanup=True, echo=True):
    # 88a's independent check pattern: every limb is scored even if another fails.
    out, results, details = gate.out, {}, {}
    def check(limb, fn):
        try:
            details[limb] = fn()
            results[limb] = 'PASS'
        except NOT_EXERCISED as exc:
            results[limb], details[limb] = 'NOT EXERCISED', str(exc)
        except Exception as exc:
            results[limb], details[limb] = 'FAIL', str(exc)
        if echo:
            gate.say(f'LIMB {limb} {results[limb]}: {LIMBS[limb]}' +
                     (f' -- {details[limb]}' if results[limb] != 'PASS' else ''))
    try:
        prep = gate.need('prepare')
        rows = prep['runs']
        diagnostics = e4._diagnostics(out)
        attached = [r for r in rows if 'analysis' in r['input'] and not r['refusal']]
        completed = [r for r in attached if not r['abort']]
        photon = [r for r in rows if r['condition'] == 'photon' and r['rep'] >= 1]
    except Exception as exc:
        for key in LIMBS:
            results[key], details[key] = 'NOT EXERCISED', str(exc)
        prep, rows, attached, completed, photon, diagnostics = {}, [], [], [], [], []
    else:
        def b1():
            for row in attached:
                bound(out, row)
            session, call = a88.session_call(out, 'run_mda', session_input(prep))
            bound(out, dict(id='session', input=call['input']), out / 'session-final', call['result'])
        check('B1', b1)
        def b2():
            for row in completed:
                result, ds, _, _, _ = a88.run_files(out, row)
                require(result.get('outcome') == 'completed' and ds['frames'] == ds['index_entries'] == row['frames'], 'Completed MDA missing frames')
                lifecycle(load(out / 'runs' / row['id'] / 'job-0.json'), 'completed', ds['frames'])
        check('B2', b2)
        def b3():
            stops = [r for r in rows if r['abort']]
            require(len(stops) == 2, 'Need both Stop runs')
            for row in stops:
                a88.check_stopped(out, row)
                lifecycle(load(out / 'runs' / row['id'] / 'job-0.json'), 'cancelled', load(out / 'runs' / row['id'] / 'dataset.json')['frames'])
        check('B3', b3)
        def b4():
            observations = []
            for row in photon:
                folder = out / 'runs' / row['id']
                captured = load(folder / 'events.json')
                events = captured['events']
                ends = [i for i, e in enumerate(events) if e['type'] == 'acquisition_finished']
                require(len(ends) == 1, 'No unique acquisition-finished diagnostic')
                live = [e for e in events[:ends[0]] if e['type'] == 'acquisition_progress' and
                        e.get('analysis_metrics', {}).get('values', {}).get('mean_intensity') is not None]
                with metrics_view(out, row, diagnostics) as (view, adapted, stamped):
                    # Exposure of the load comes from the package's durable history,
                    # independently of the forwarding path this limb is testing.
                    package = b87.load_evidence(view, adapted, stamped)
                    if not package.get('exercised') or package['lines_before_teardown_lower_bound'] == 0:
                        raise NotExercised('No package lines proved before acquisition end: ' + str(package))
                    end = datetime.fromisoformat(package['teardown_utc'])
                    history = load(folder / 'job-0.json')['metrics']['history']
                    before = [bucket for bucket in history
                              if datetime.fromisoformat(bucket['received_last_at']) < end
                              and 'mean_intensity' in bucket.get('frames', {})]
                    if not before:
                        raise NotExercised('No package-side mean_intensity range proved before acquisition end')
                    package_last = max(bucket['frames']['mean_intensity'][1] for bucket in before)
                    package_lag = package['accounted'] - (package_last + 1)
                    if package_lag > 200:
                        raise NotExercised(f'Package was {package_lag} frames behind before acquisition end')
                    require(live, 'Package reported during acquisition, but no live metrics were forwarded before acquisition_finished')
                    pace = b87.keeps_pace(view, adapted, stamped)
                    require(len(live) >= 3, f"{row['id']}: fewer than three live metrics events")
                    last_ordinal = {}
                    for event in live:
                        for metric, value in event['analysis_metrics']['values'].items():
                            ordinal = value['frames'][1]
                            require(ordinal >= last_ordinal.get(metric, -1), 'Live metric range decreased: ' + metric)
                            last_ordinal[metric] = ordinal
                    observations.append(dict(run=row['id'], live=b87.live(view, adapted, stamped, 'mean_intensity'),
                                             pace=pace, package_side_frame_lag=package_lag))
            require(len(observations) == prep['sizes'][0] - 1, 'Missing scored photon run')
            return observations
        check('B4', b4)
        def b5():
            row = next(r for r in rows if r['condition'] == 'thermal')
            folder = out / 'runs' / row['id']
            job = load(folder / 'job-0.json')
            declarations = load(out / 'installed.json')
            names = set(next(op for op in declarations['thermal']['manifest']['operations'] if op['name'] == 'observe_dataset')['metrics'])
            photon_names = set(next(op for op in declarations['metrics']['manifest']['operations'] if op['name'] == 'observe_dataset')['metrics'])
            require(set(job['metrics']['declared']) == names and not names & photon_names, 'Thermal declaration changed')
            events = load(folder / 'events.json')['events']
            end = next(i for i, e in enumerate(events) if e['type'] == 'acquisition_finished')
            live = [e['analysis_metrics']['values'] for e in events[:end] if e.get('analysis_metrics', {}).get('values')]
            require(live and any(names <= set(v) for v in live) and not any(set(v) & photon_names for v in live), 'Thermal live names are missing or contaminated by photon')
            with metrics_view(out, row, diagnostics) as (view, adapted, stamped):
                b87.live(view, adapted, stamped, 'mean_frame_bytes')
                b87.keeps_pace(view, adapted, stamped)
                metrics = b87.final_metrics(view, adapted, names)
                require(set(metrics['latest']) == names, 'Thermal final names differ')
        check('B5', b5)
        measurement = {}
        def b6():
            require(prep['sizes'][0] == 6, 'Six repetitions (one warm-up) required')
            measurement.update(cadence(out, rows, 5))
            require(measurement['photon_none_ratio'] <= 1.10, 'Median photon/none > 1.10: ' + str(measurement))
            return measurement
        check('B6', b6)
        def b7():
            refusals = [r for r in rows if r['refusal']]
            require(len(refusals) == 2, 'Need both refusals')
            for row in refusals:
                folder = out / 'runs' / row['id']
                result, _, state, events, _ = a88.run_files(out, row)
                reason = 'ND_TIFF' if row['refusal'] == 'tiff' else 'analysis_feedback'
                require(reason in result.get('error', ''), 'Wrong refusal reason')
                require(load(folder / 'confirmations.json') == [], 'Refusal prompted for confirmation')
                require(state.get('engine_start_observed') is False and not state['pre']['running'] and not state['post']['running'], 'Refusal started engine')
                require(not events['events'] and not result.get('dataset_path'), 'Refusal emitted acquisition traffic')
                require(load(folder / 'folders.json')['new'] == [], 'Refusal created a dataset folder')
        check('B7', b7)
        def b8():
            dispatch = {}
            for row in rows:
                if row['refusal']:
                    continue
                result = load(out / 'runs' / row['id'] / 'result.json')
                phases = result.get('duration_breakdown', {}).get('phases', {})
                require(('analysis_dispatch' in phases) == ('analysis' in row['input']), 'Wrong dispatch phase presence: ' + row['id'])
                if 'analysis' in row['input']:
                    dispatch[row['id']] = e4.number(phases['analysis_dispatch']['total_s'], 'dispatch seconds')
            _, call = a88.session_call(out, 'run_mda', session_input(prep))
            dispatch['session'] = e4.number(call['result']['duration_breakdown']['phases']['analysis_dispatch']['total_s'], 'session dispatch')
            return dispatch
        check('B8', b8)
        def s1():
            session, call = a88.session_call(out, 'run_mda', session_input(prep))
            result = call['result']
            require(result.get('outcome') == 'cancelled' and result.get('writer_completion', {}).get('state') == 'proved', 'Session not Stop-cancelled with proved writer')
            require(0 < result.get('frames_accounted', 0) < call['input']['settings']['n_frames'], 'Session did not stop early')
            jobs = result.get('analysis', {}).get('jobs', [])
            require(len(jobs) == 1 and jobs[0].get('dataset') == result.get('dataset_path'), 'Session observer not bound')
            require(session.get('answers', {}).get('stop', '').strip().upper() == 'DONE', 'Stop step not recorded')
        check('S1', s1)
        def s2():
            session = load(out / 'session.json')
            require(session.get('working_answer', '').strip().upper() == 'YES', 'Operator answered NO or did not answer')
            require(session.get('working_screenshot', {}).get('ok') is True and (out / 'working.png').is_file(), 'Working screenshot missing/failed')
        check('S2', s2)
    # Measurements are independent of scores. Missing measurements are explicit,
    # never silently replaced by zero or a fabricated timestamp.
    measurements = dict(cadence=measurement if rows and measurement else details.get('B6'),
                        dispatch_s=details.get('B8'), runs={})
    for row in (r for r in rows if not r['refusal']):
        try:
            folder = out / 'runs' / row['id']
            events = load(folder / 'events.json')
            first = next(t for e, t in zip(events['events'], events['receipt_s']) if e.get('dataset_path'))
            measurements['runs'][row['id']] = dict(save_path_first_seen_receipt_s=first)
            if 'analysis' not in row['input']:
                continue
            writer = next(e for e in diagnostics if e.get('tool_call_id') == row['id'] and e['type'] == 'writer_finished')
            terminal = load(folder / 'job-0-clock.json')
            with metrics_view(out, row, diagnostics) as (view, adapted, stamped):
                info = b87.load_evidence(view, adapted, stamped)
            measurements['runs'][row['id']] = dict(save_path_first_seen_receipt_s=first,
                metrics_lines_before_acquisition_end=info.get('lines_before_teardown_lower_bound'),
                last_live_frame_lag=info.get('last_live_frame_lag'),
                writer_to_job_terminal_s=(datetime.fromisoformat(terminal['terminal_record_utc']) - datetime.fromisoformat(writer['timestamp'])).total_seconds())
        except Exception as exc:
            measurements['runs'][row['id']] = dict(unavailable=str(exc))
    try:
        r01 = load(out / 'r01.json')
        if echo:
            gate.say('R01 REPORTED (unscored, verbatim captured assistant text):')
            if not r01.get('replies'):
                gate.say('R01 REPORTED unavailable: ' + str(r01.get('unavailable')))
            if r01.get('tool_uses'):
                gate.say('R01 REPORTED unexpected tool calls: ' + json.dumps(r01['tool_uses']))
            for reply in r01['replies']:
                print(reply, flush=True)
    except Exception as exc:
        r01 = dict(unavailable=str(exc))
        if echo:
            gate.say('R01 REPORTED: unavailable: ' + str(exc))
    cleanup_note = 'not requested'
    if cleanup:
        try:
            gate.close_microclaw('Verify restores the operator store.')
            own = load(out / 'ownership.json')
            if own.get('host') == socket.gethostname() and own.get('store') == str(gate.store_root.resolve()) and own.get('evidence') == str(out.resolve()):
                cleanup_note = gate.cleanup()
            else:
                cleanup_note = 'Transported evidence; local store untouched.'
        except Exception as exc:
            cleanup_note = 'Cleanup could not finish: ' + str(exc)
    bad = sum(s != 'PASS' for s in results.values()) + int(str(cleanup_note).startswith('Cleanup could not finish:'))
    gate.last_results = results
    gate.save('verify', dict(summary=results, details=details, measurements=measurements, r01=r01,
                            cleanup=cleanup_note, nonpass=bad, synthetic=prep.get('selftest', False)))
    if echo:
        gate.say('MEASUREMENT durations, ratio and gaps (only ratio bounded in B6): ' + json.dumps(measurements['cadence']))
        gate.say('MEASUREMENT analysis_dispatch seconds: ' + json.dumps(measurements['dispatch_s']))
        for ident, data in measurements['runs'].items():
            gate.say('MEASUREMENT ' + ident + ': ' + json.dumps(data))
        gate.say(f'RESULT: {bad} FAIL/NOT EXERCISED; CLEANUP: {cleanup_note}')
    return bad


def selftest(output, *, pre_block=False):
    from microclaw import paths, controller, tools, completed_dataset
    if not pre_block:
        features()
    require(SIZES == (6, 1000, 12000, 2000, 200), 'Production frame/repetition/Stop plan changed')
    canonical = plan({'metrics': 'a' * 64, 'thermal': 'b' * 64}, Path('datasets'))
    require(len(canonical) == 17, 'Production plan must have 17 runs')
    for rep in range(6):
        arms = [r['condition'] for r in canonical if r['condition'] in ('none', 'photon') and r['rep'] == rep]
        require(arms == (['none', 'photon'] if rep % 2 == 0 else ['photon', 'none']), 'Arm order does not rotate')
    output = Path(output).resolve()
    if output.exists() and any(output.iterdir()):
        raise NotExercised('Selftest output must be empty.')
    with tempfile.TemporaryDirectory(prefix='block88b-') as temporary:
        base = Path(temporary)
        env = {key: str(base / key) for key in ('XDG_DATA_HOME', 'LOCALAPPDATA', 'XDG_CONFIG_HOME', 'APPDATA')}
        env.update(UV_OFFLINE='1', UV_CACHE_DIR=str(base / 'uv-cache'))
        with patch.dict(os.environ, env):
            config = paths.default_safety_config()
            config.parent.mkdir(parents=True)
            config.write_text(e4.SAFETY_YAML, encoding='utf-8')
            root = paths.user_data_dir()
            root.mkdir(parents=True)
            mm = base / 'fake-mm'
            (mm / 'plugins').mkdir(parents=True)
            engine = a88.FakeEngine(a88._mda_fakes(), size=512)
            class Core(e4.BridgeCore):
                _creation_port = 4827
                exposure = 10.
                def get_exposure(self): return type(self).exposure
                def set_exposure(self, *args): type(self).exposure = float(args[-1])
                def get_image_width(self): return 512
                def get_image_height(self): return 512
                def get_bytes_per_pixel(self): return 2
            class Studio(e4.BridgeStudio):
                def acquisitions(self): return engine
            gate = Gate(root, base / 'evidence', fake=e4.OfflineInstall())
            gate._pre_block_selftest = pre_block
            marker = gate.store_root / 'operator-marker.txt'
            marker.parent.mkdir(parents=True)
            marker.write_bytes(b'88b operator store must survive\r\n')
            gate.say('SELFTEST REAL: 512 x 512 NDTiff, execute_tool, offline signed fixtures, real workers and lifecycle records.')
            gate.say('SELFTEST SYNTHETIC: bridge, imaging cadence, UI, answers, history envelopes and screenshots.')
            import pycromanager
            java_class = lambda classpath, port=None, **kw: engine.f.static_class(port, classpath)
            try:
                with patch.object(controller, 'Core', Core), patch.object(controller, 'Studio', Studio), \
                        patch.object(pycromanager, 'JavaClass', java_class), \
                        patch.object(controller.MicroscopeController, 'get_mm_app_dir', return_value=str(mm)):
                    gate.prepare(sizes=SELFTEST_SIZES)
                    gate.measure()
                    gate.session()
                bad = verify(gate)
                shutil.copytree(gate.out, output, dirs_exist_ok=True)
                require(marker.read_bytes() == b'88b operator store must survive\r\n' and not gate.saved_store.exists(), 'Operator store not restored')
                if pre_block:
                    gate.save('selftest', dict(pre_block=True, actual_scores=gate.last_results,
                        nonpass=bad, frame_size=512, store_restored=True,
                        phases=['prepare', 'measure', 'synthetic session', 'verify']))
                    gate.say('SELFTEST --pre-block: full verify reached; ' + json.dumps(gate.last_results))
                    shutil.copytree(gate.out, output, dirs_exist_ok=True)
                    return int(bool(bad))
                # Cadence is measured on the fake, never declared demo performance.
                require(all(v == 'PASS' for k, v in gate.last_results.items() if k != 'B6'),
                        'Unmutated structural limbs failed: ' + str(gate.last_results))
                gate.say('SELFTEST actual fake-machine scores: ' + json.dumps(gate.last_results))
                control = base / 'control'
                shutil.copytree(gate.out, control)
                prep = load(control / 'prepare.json')
                # Deterministic scorer control ONLY: raw evidence above is untouched.
                for row in prep['runs']:
                    if row['condition'] in ('none', 'photon'):
                        path = control / 'runs' / row['id'] / 'result.json'
                        data = load(path)
                        data['duration_breakdown']['duration_s'] = 1.0
                        write_json(path, data)
                arm = Gate(root, control, fake=e4.OfflineInstall())
                require(verify(arm, cleanup=False, echo=False) == 0, 'Scorer control did not pass: ' + str(arm.last_results))
                backup = base / 'scorer-control'
                shutil.copytree(control, backup)
                first = next(r for r in prep['runs'] if r['condition'] == 'photon' and r['rep'] == 1)
                stop = next(r for r in prep['runs'] if r['abort'])
                thermal = next(r for r in prep['runs'] if r['condition'] == 'thermal')
                none = next(r for r in prep['runs'] if r['condition'] == 'none')
                refused = next(r for r in prep['runs'] if r['refusal'])
                def edit(row, filename, change):
                    path = control / 'runs' / row['id'] / filename
                    value = load(path); change(value); write_json(path, value)
                def after_only():
                    path = control / 'runs' / first['id'] / 'events.json'
                    value = load(path)
                    metrics = [(e, t) for e, t in zip(value['events'], value['receipt_s']) if e.get('analysis_metrics')]
                    ordinary = [(e, t) for e, t in zip(value['events'], value['receipt_s']) if not e.get('analysis_metrics')]
                    value.update(events=[e for e, t in ordinary + metrics], receipt_s=[t for e, t in ordinary + metrics])
                    write_json(path, value)
                def no_package_lines_before_end():
                    events = e4._diagnostics(control)
                    end = next(datetime.fromisoformat(e['timestamp']) for e in events
                               if e.get('tool_call_id') == first['id'] and e['type'] == 'acquisition_finished')
                    edit(first, 'job-0.json', lambda d: d['metrics'].update(history=[
                        bucket for bucket in d['metrics']['history']
                        if datetime.fromisoformat(bucket['received_last_at']) >= end]))
                def cadence_bad():
                    for row in prep['runs']:
                        if row['condition'] == 'photon' and row['rep'] >= 1:
                            edit(row, 'result.json', lambda d: d['duration_breakdown'].update(duration_s=1.11))
                mutations = [
                    ('B1', 'guessed path', lambda: edit(first, 'job-0.json', lambda d: d.update(dataset=str(Path(first['input']['settings']['save_dir']) / first['input']['settings']['name'])))),
                    ('B2', 'writer first', lambda: edit(first, 'job-0.json', lambda d: d['notifications'].reverse())),
                    ('B2', 'duplicate acquisition', lambda: edit(first, 'job-0.json', lambda d: d['notifications'].insert(1, deepcopy(d['notifications'][0])))),
                    ('B3', 'Stop job completed', lambda: edit(stop, 'job-0.json', lambda d: d['notifications'][0]['message'].update(outcome='completed'))),
                    ('B4', 'metrics only after acquisition', after_only),
                    ('B4', 'no package lines before acquisition end', no_package_lines_before_end),
                    ('B5', 'photon names on thermal', lambda: edit(thermal, 'events.json', lambda d: [e['analysis_metrics'].update(values={'mean_intensity': dict(value=1, frames=[0, 1])}) for e in d['events'] if e.get('analysis_metrics')])),
                    ('B6', 'median 1.11', cadence_bad),
                    ('B7', 'refusal prompt', lambda: edit(refused, 'confirmations.json', lambda d: d.append(dict(kind='analysis')))),
                    ('B8', 'unattached dispatch', lambda: edit(none, 'result.json', lambda d: d['duration_breakdown']['phases'].update(analysis_dispatch=dict(total_s=0.1)))),
                    ('S2', 'operator NO', lambda: write_json(control / 'session.json', dict(load(control / 'session.json'), working_answer='NO'))),
                ]
                mutation_results = []
                for limb, label, mutate in mutations:
                    shutil.rmtree(control); shutil.copytree(backup, control)
                    mutate()
                    verify(arm, cleanup=False, echo=False)
                    changed = {k for k, v in arm.last_results.items() if v != 'PASS'}
                    require(changed == {limb}, f'{label}: changed {changed}, expected only {limb}')
                    expected = 'NOT EXERCISED' if label == 'no package lines before acquisition end' else 'FAIL'
                    require(arm.last_results[limb] == expected, f'{label}: expected {expected}, got {arm.last_results[limb]}')
                    mutation_results.append(dict(limb=limb, mutation=label, status=arm.last_results[limb], other_limbs='PASS'))
                    gate.say(f'SELFTEST mutation {label}: {limb} {arm.last_results[limb]}; all other limbs PASS')
                gate.save('selftest', dict(actual_scores=gate.last_results, mutations=mutation_results, frame_size=512,
                    phases=['prepare', 'measure', 'synthetic session', 'verify'], store_restored=True))
                gate.say(f'SELFTEST PASSED: prepare -> measure -> synthetic session -> verify; {len(mutation_results)} isolated mutations; store restored.')
                shutil.copytree(gate.out, output, dirs_exist_ok=True)
                return 0
            finally:
                completed_dataset.close_analysis_supervisor()
                if not output.exists():
                    shutil.copytree(gate.out, output, dirs_exist_ok=True)
                gate.cleanup()


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('phase', choices=('prepare', 'measure', 'session', 'verify', 'cleanup', 'selftest'))
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--port', type=int, default=4827)
    parser.add_argument('--pre-block', action='store_true', help='selftest only: bypass 88b admission and score the full fake run')
    args = parser.parse_args()
    if args.pre_block and args.phase != 'selftest':
        parser.error('--pre-block is allowed only with phase selftest')
    gate = None
    try:
        if args.phase == 'selftest':
            return selftest(args.out, pre_block=args.pre_block)
        from microclaw import paths
        gate = Gate(paths.user_data_dir(), args.out)
        if args.phase == 'verify':
            gate.close_microclaw('Verify requires MicroClaw closed.')
            return int(bool(verify(gate)))
        if args.phase == 'prepare':
            gate.prepare(port=args.port)
        else:
            value = getattr(gate, args.phase)()
            if args.phase == 'cleanup':
                gate.save('cleanup', value)
        return 0
    except NOT_EXERCISED as exc:
        text, code = 'NOT EXERCISED: ' + str(exc), 2
    except Exception as exc:
        text, code = 'FAILED: ' + str(exc) + '\n' + traceback.format_exc(), 1
    if gate:
        gate.say(text)
        gate.save(args.phase + '-error', dict(error=text))
    else:
        print(text, flush=True)
    return code


if __name__ == '__main__':
    sys.exit(main())
