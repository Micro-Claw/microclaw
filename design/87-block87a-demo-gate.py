"""87a demo gate: package metrics, live progress, and measured acquisition load.

Imports 84b's cleanup/screenshots and 83e4's connection, offline installer fakes,
consent, dataset reader and bridge-shaped acquisition; earlier gates are unedited.
Verify scores captured files only; optional final cleanup restores the local store.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import socket
import statistics
import sys
import tempfile
import time
import traceback

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('gate87_base84', HERE / '84-block84b-demo-gate.py')
b84 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(b84)
e4, f6 = b84.e4, b84.f6
read_json, write_json, now = e4.read_json, e4.write_json, e4.now
NotExercised = e4.NotExercised
POINTER = '87a-gate-evidence.txt'
WALL_CLOCK_STEP_S = 0.0156
# Configure this private module instance's cleanup pointer, without editing 84b.
b84.POINTER = POINTER
PACKAGES = {'metrics': 'fixture-lab/photon-observer', 'thermal': 'weather-lab/thermal-observer'}
SIZES = (6, 1000, 1200)  # session: about 60 s at 0.05 s, long enough to watch
LIMBS = {1: 'both publishers installed and compact discovery', 2: 'all planned frames saved',
         3: 'live photon progress and one-second cadence', 4: 'final photon metrics and tool snapshot',
         5: 'gauge-only second publisher', 6: 'broken contiguity isolated from acquisition',
         7: 'sustained flood finishes without a loss latch', 8: 'median metrics/none duration <= 1.10',
         9: 'flood cadence and CPU: reporting only', 'S1': 'session acquisition result metrics',
         'S2': 'session analysis_job_status metrics', 'O': 'operator-judged changing working line'}


def features():
    from microclaw import skill_packages, skill_supervisor
    if '1.2' not in skill_packages.SUPPORTED_PROTOCOLS or not hasattr(skill_supervisor.JobHandle, 'metrics_snapshot'):
        raise NotExercised('Installed product lacks protocol 1.2 / JobHandle.metrics_snapshot; run step 0.')


def plan(digests, directory, sizes=SIZES):
    rows = []
    def row(condition, rep, frames):
        name = f'r{rep}-{condition}' if rep is not None else condition
        args = dict(n_frames=frames, interval_s=0, exposure_ms=10, save_dir=str(directory), name=name)
        if condition != 'none':
            source = 'thermal' if condition == 'thermal' else 'metrics'
            parameters = {'flood': True} if condition == 'flood' else {'broken_contiguity': True} if condition == 'broken' else {}
            args['analysis'] = dict(adapter=PACKAGES[source] + ':observe_dataset',
                                    release_digest=digests.get(source, ''), parameters=parameters)
        return dict(id=name, condition=condition, repetition=rep, type='burst', input=args)
    arms = ('none', 'metrics', 'flood')
    for rep in range(sizes[0]):
        for arm in arms[rep % 3:] + arms[:rep % 3]:
            rows.append(row(arm, rep, sizes[1]))
    rows.extend(row(arm, None, sizes[1]) for arm in ('thermal', 'broken'))
    return rows


def session_input(prep):
    return dict(n_frames=prep['sizes'][2], interval_s=0.05, exposure_ms=10,
                save_dir=prep['save_dir'], name=prep['session_name'],
                analysis=dict(adapter=PACKAGES['metrics'] + ':observe_dataset',
                              release_digest=prep['digests']['metrics'], parameters={}))


def chat(tool, arguments):
    return f'Call {tool} exactly once with exactly these arguments: {json.dumps(arguments)}. Do not change, add or drop any value, or substitute another tool. Call no other tool before it.'


class Gate(b84.Gate):
    @property
    def saved_store(self):
        return self.store_root.with_name(self.store_root.name + '.87a-saved')

    def owned_store(self):
        try:
            own = read_json(self.store_root / '.87a-gate-owner.json')
            return own.get('host') == socket.gethostname() and own.get('store') == str(self.store_root.resolve())
        except (OSError, ValueError, AttributeError):
            return False

    def base_python(self):
        # Stdlib-only fixture releases can use the installed slot's base Python.
        # This avoids uv python install/download and requires no extra configuration.
        return sys._base_executable

    def prepare(self, *, port=4827, sizes=SIZES):
        from microclaw import tools
        features()
        self.close_microclaw('Prepare needs MicroClaw closed; leave Micro-Manager open.')
        if self.saved_store.exists() or self.test_store() or self.owned_store():
            raise NotExercised('Previous gate store/backup exists; run cleanup first.')
        parsed, guard, ctrl = self.connect(port)
        base = Path(guard.resolve_in_workspace('.') if parsed.constraints.workspace_dir else self.root).resolve()
        directory = base / ('block87a-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
        # Include the later session in the up-front disk reservation estimate.
        extra = dict(input=dict(n_frames=sizes[2]))
        disk = e4.disk_check(ctrl.core, base, plan({}, directory, sizes) + [extra])
        self.save('ownership', dict(store=str(self.store_root.resolve()), host=socket.gethostname(),
            evidence=str(self.out.resolve()), saved_store=str(self.saved_store)))
        if self.store_root.exists():
            os.replace(self.store_root, self.saved_store)
            self.say('Operator store set aside: ' + str(self.saved_store))
        write_json(self.store_root / '.87a-gate-owner.json', self.need('ownership'))
        write_json(self.store_root / 'trust/roots.json', read_json(e4.FIXTURES / 'trust/roots-TEST-ONLY.json'))
        policy = self.store.store_trust_policy(read_json(e4.FIXTURES / 'trust/telemetry-policy-TEST-ONLY.json'))
        self.save('test-roots', read_json(self.store_root / 'trust/roots.json'))
        self.save('test-policy', read_json(self.store_root / 'trust/policy.json'))
        os.environ['UV_OFFLINE'] = '1'
        releases, digests = {}, {}
        builder = e4.gate83d.load_builder()
        for label, fixture, key in [('metrics', 'metrics', 'publisher-a'), ('thermal', 'weather', 'publisher-b')]:
            artifact = self.out / 'releases' / (label + '.zip')
            artifact.parent.mkdir(parents=True, exist_ok=True)
            intake = builder.build_release(e4.FIXTURES / fixture, artifact, signing_key=key)
            installed = self.store.install(intake, artifact, policy=policy, now=now(), uv_executable=self.uv(),
                retained_digests=frozenset(), find_links=(e4.FIXTURES / 'wheels').resolve(), base_python=self.base_python())
            releases[label], digests[label] = installed, intake['artifact_digest']
            self.say('Installed ' + PACKAGES[label] + '@' + digests[label] + ' (offline)')
        self.save('installed', releases)
        self.save('discovery', dict(text=self.store.discovery_text(), loaded={label: tools.load_skill(ctrl, guard,
            package + '/workflow') for label, package in PACKAGES.items()}))
        self.save('prepare', dict(digests=digests, sizes=list(sizes), port=port, disk=disk,
            save_dir=str(directory), session_name='87a-session-' + self.out.name,
            selftest=bool(self.fake), runs=plan(digests, directory, sizes), prepared_at=now().isoformat(),
            python=sys.executable, product=str(Path(tools.__file__).resolve().parent)))
        (self.root / POINTER).write_text(str(self.out.resolve()) + '\n', encoding='utf-8')
        for rep in range(sizes[0]):
            self.say(f'PLAN repetition {rep}' + (' (warm-up, unscored)' if rep == 0 else '') + ': ' +
                     ', '.join(r['condition'] for r in self.need('prepare')['runs'] if r['repetition'] == rep))
        self.say('Disk: ' + json.dumps(disk))

    def run_one(self, row, folder, ctrl, guard, writer, session_id):
        from microclaw import tools
        folder.mkdir(parents=True, exist_ok=False)
        write_json(folder / 'input.json', row['input'])
        prompts, events = [], []
        analysis = row['input'].get('analysis')
        subject = analysis['adapter'].split(':')[0] + '@' + analysis['release_digest'] if analysis else ''
        previous = tools.CONFIRM_FN
        tools.SESSION_GRANTS.clear()
        tools.CONFIRM_FN = e4.recording_confirm(prompts, subject,
            lambda p: write_json(folder / 'confirmations.json', p))
        clock = dict(start_utc=now().isoformat(), start_perf=time.perf_counter(),
                     process_cpu_start_s=time.process_time(), clock='time.perf_counter / UTC; CPU=time.process_time')
        self.say('RUN ' + row['id'])
        try:
            raw = tools.execute_tool('run_timelapse', deepcopy(row['input']), ctrl, guard,
                acquisition_diagnostic_writer=writer, acquisition_event_sink=lambda event: events.append(deepcopy(event)),
                acquisition_session_id=session_id, tool_call_id=row['id'])
            result = json.loads(raw)
            clock.update(tool_end_utc=now().isoformat(), tool_end_perf=time.perf_counter(),
                         tool_process_cpu_s=time.process_time() - clock['process_cpu_start_s'])
            write_json(folder / 'result.json', result)
            for i, job in enumerate((result.get('analysis') or {}).get('jobs') or []):
                self.wait_terminal(job, folder, i)
                status = json.loads(tools.execute_tool('analysis_job_status', {'job_id': job['job_id']}, ctrl, guard))
                write_json(folder / f'status-{i}.json', status)
            return result
        finally:
            clock.update(end_utc=now().isoformat(), end_perf=time.perf_counter(),
                         process_cpu_s=time.process_time() - clock['process_cpu_start_s'])
            write_json(folder / 'clock.json', clock)
            # Exact browser-sink payloads, no gate-invented event timestamps.
            write_json(folder / 'browser-events.json', events)
            write_json(folder / 'confirmations.json', prompts)
            tools.CONFIRM_FN = previous

    def measure(self):
        from microclaw import completed_dataset
        from microclaw.conversation import AcquisitionDiagnosticWriter, AuditLog
        prep = self.need('prepare')
        self.close_microclaw('Measure drives acquisitions without MicroClaw running.')
        _, guard, ctrl = self.connect(prep['port'])
        disk = e4.disk_check(ctrl.core, prep['disk']['dataset_root'], prep['runs'])
        if disk['bytes_per_frame'] != prep['disk']['bytes_per_frame']:
            raise NotExercised('Camera frame size changed since prepare.')
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        session_id = stamp + '_microclaw_history'
        writer = AcquisitionDiagnosticWriter(AuditLog(self.out / (stamp + '_microclaw_acquisitions.jsonl'), enabled=True, secrets=()))
        self.save('measure', dict(started_at=now().isoformat(), disk=disk,
            timestamp_source='product acquisition diagnostic timestamp; matched to exact browser payload',
            cpu_scope='gate process through terminal job record, including supervisor reader; tool-only CPU also retained'))
        completed = 0
        try:
            for row in prep['runs']:
                folder = self.out / 'runs' / row['id']
                result = self.run_one(row, folder, ctrl, guard, writer, session_id)
                try:
                    evidence = e4.dataset_evidence(result)
                except Exception as exc:
                    evidence = dict(error=str(exc))
                write_json(folder / 'dataset.json', evidence)
                completed += 1
        finally:
            writer.close()
            completed_dataset.close_analysis_supervisor()
            self.save('measure-end', dict(completed=completed, planned=len(prep['runs']), ended_at=now().isoformat()))

    def capture_session(self, started):
        target = self.out / 'session-history'
        target.mkdir(exist_ok=True)
        for path in self.root.glob('*_microclaw_history.jsonl'):
            if datetime.fromtimestamp(path.stat().st_mtime, started.tzinfo) >= started:
                shutil.copy2(path, target / path.name)

    def session(self):
        if self.fake:
            return self.synthetic_session()
        prep = self.need('prepare')
        self.close_microclaw('Begin session with MicroClaw closed, then launch from the desktop icon.')
        if (self.out / 'session.json').exists():
            raise NotExercised('Session evidence already exists; refusing to overwrite.')
        started = now()
        existing = {p.name for p in self.store.analysis_job_path('0'*32).parent.glob('*.json')}
        args = session_input(prep)
        data = dict(started_at=started.isoformat(), input=args, completed=[], answers={}, synthetic=False)
        self.save('session', data)
        self.say('STEP 1: Launch MicroClaw from its desktop icon; wait for Firefox. Put Firefox and this PowerShell window side by side, both visible. Paste this literal line and approve. Type DONE while the acquisition is running, not after the reply:\n' + chat('run_timelapse', args))
        data['answers']['1'] = self.ask('DONE when the working line is visible; STOP abandons.', '1')
        data['completed'].append(1)
        deadline = time.perf_counter() + 90
        job = None
        while time.perf_counter() < deadline:
            for path in self.store.analysis_job_path('0'*32).parent.glob('*.json'):
                if path.name in existing:
                    continue
                candidate = read_json(path)
                if (candidate.get('package') == PACKAGES['metrics'] and candidate.get('digest') == prep['digests']['metrics']
                        and Path(candidate.get('dataset', '')).name.startswith(prep['session_name'])):
                    job = candidate
            if job and (job.get('metrics', {}).get('received', 0) > 0 or job['state'] not in self.store.ANALYSIS_NONTERMINAL):
                break
            time.sleep(0.05)
        if job is None:
            raise NotExercised('No matching session analysis job appeared; evidence retained.')
        data['job_id'] = job['job_id']
        data['working_capture'] = dict(record=job, live=job['state'] in self.store.ANALYSIS_NONTERMINAL,
            screenshot=b84.screenshot(self.out / 'working.png'))
        self.save('session', data)
        self.say('STEP 2: Watch the working line while frames are acquired. The program has captured working.png.')
        question = 'Did the working line under the chat show flux, slow and total values changing while frames were acquired?'
        while True:
            self.say(question + ' Type YES or NO (STOP abandons).')
            self.discard_pending_input()
            answer = input('> ')
            self.say('ANSWER[working-line]: ' + repr(answer))
            if answer.strip().upper() == 'STOP':
                raise NotExercised('Operator stopped the gate.')
            if answer.strip().upper() in ('YES', 'NO'):
                data['working_answer'] = answer
                break
        data['completed'].append(2)
        self.save('session', data)
        folder = self.out / 'session-final'
        folder.mkdir(exist_ok=True)
        self.wait_terminal(job, folder, 0)
        self.say('STEP 3: Paste this literal line, approve if asked, and wait for the reply:\n' + chat('analysis_job_status', {'job_id': job['job_id']}))
        data['answers']['3'] = self.ask('DONE after the status reply; STOP abandons.', '3')
        data['completed'].append(3)
        self.capture_session(started)
        self.save('session', data)
        self.say('STEP 4: Quit MicroClaw by closing its console and launcher windows. Leave Firefox open.')
        data['answers']['4'] = self.ask('DONE after quitting; STOP abandons.', '4')
        data['completed'].append(4)
        self.capture_session(started)
        self.save('session', data)

    def synthetic_session(self):
        """Synthetic UI/answers; actual tools, store, workers and growing dataset."""
        from microclaw import tools, completed_dataset
        from microclaw.conversation import AcquisitionDiagnosticWriter, AuditLog
        prep, started = self.need('prepare'), now()
        _, guard, ctrl = self.connect(prep['port'])
        args = session_input(prep)
        data = dict(started_at=started.isoformat(), input=args, completed=[1, 2, 3, 4],
            answers={'1': 'DONE', '3': 'DONE', '4': 'DONE'}, working_answer=' YES ', synthetic=True,
            note='UI, answers, history envelope and screenshot synthetic; tool results and workers real')
        self.say('SESSION SYNTHETIC answers DONE / YES / DONE / DONE; real tools and worker.')
        folder = self.out / 'session-run'
        writer = AcquisitionDiagnosticWriter(AuditLog(self.out / 'session_microclaw_acquisitions.jsonl', enabled=True, secrets=()))
        try:
            result = self.run_one(dict(id='session', input=args), folder, ctrl, guard, writer, 'synthetic-session')
        finally:
            writer.close()
            completed_dataset.close_analysis_supervisor()
        job = (result.get('analysis') or {}).get('jobs', [])[0]
        data['job_id'] = job['job_id']
        final = read_json(folder / 'job-0.json')
        write_json(self.out / 'session-final/job-0.json', final)
        data['working_capture'] = dict(live=True, synthetic=True, screenshot=b84.screenshot(self.out / 'working.png', synthetic=True))
        status = read_json(folder / 'status-0.json')
        history = self.out / 'session-history' / 'synthetic_microclaw_history.jsonl'
        history.parent.mkdir(exist_ok=True)
        audit = AuditLog(history, enabled=True, secrets=())
        for ident, tool, arguments, output in [('acq', 'run_timelapse', args, result),
                ('status', 'analysis_job_status', {'job_id': job['job_id']}, status)]:
            audit.append(dict(role='assistant', content=[dict(type='tool_use', id=ident, name=tool, input=arguments)]))
            audit.append(dict(role='user', content=[dict(type='tool_result', tool_use_id=ident, content=json.dumps(output))]))
        self.save('session', data)


load, require = b84.load, b84.require


def scored(row):
    return row['repetition'] is None or row['repetition'] >= 1


def progress(out, row, diagnostics):
    folder = out / 'runs' / row['id']
    captured = load(folder / 'browser-events.json')
    browser = [e for e in captured if e.get('analysis_metrics') is not None]
    stamped = [e for e in diagnostics if e.get('tool_call_id') == row['id'] and e.get('analysis_metrics') is not None]
    if not browser:
        # The sink worked if it carried ordinary progress; then the product,
        # not the capture, failed to publish metrics.
        require(not any(e.get('type') == 'acquisition_progress' for e in captured),
                'Progress reached the browser sink, but none carried analysis_metrics.')
        raise NotExercised('No analysis_metrics progress captured from the browser sink.')
    # This event class has one foreground producer. Match its recorded order as
    # well as payload, so repeated empty startup snapshots remain attributable.
    if len(browser) != len(stamped):
        raise NotExercised('Browser/diagnostic event counts differ; no gate timestamp substituted.')
    for payload, event in zip(browser, stamped):
        plain = {k: v for k, v in event.items() if k not in ('timestamp', 'session_id', 'tool_call_id')}
        if plain != payload or not event.get('timestamp'):
            raise NotExercised('Browser payload has no matching product diagnostic timestamp; no gate timestamp substituted.')
    return stamped


def live(out, row, diagnostics, metric):
    events = progress(out, row, diagnostics)
    last = row['input']['n_frames'] - 1
    require(any(e['analysis_metrics'].get('feed_alive') is True and
        0 <= e['analysis_metrics'].get('values', {}).get(metric, {}).get('frames', [0, last])[1] < last for e in events),
        'No live metric range before the planned final ordinal.')
    stamps = [datetime.fromisoformat(e['timestamp']) for e in events]
    gaps = [(b-a).total_seconds() for a, b in zip(stamps, stamps[1:])]
    # The product gates emission on perf_counter >= 1 s, but stamps the event
    # with datetime.now(), which on Windows under Python 3.12 advances in
    # 15.6 ms steps (design/79). Allow two such steps, never more.
    require(all(gap >= 1 - WALL_CLOCK_STEP_S * 2 for gap in gaps),
            'Progress timestamps less than one second apart: ' + str(gaps))
    return dict(events=len(events), timestamp_source='product diagnostic timestamp', consecutive_s=gaps)


def final_metrics(out, row, metrics_names):
    folder = out / 'runs' / row['id']
    result, job, status = load(folder / 'result.json'), load(folder / 'job-0.json'), load(folder / 'status-0.json')
    require(job.get('state') == 'succeeded', 'Job did not succeed: ' + str(job.get('failure')))
    require(all(status.get('analysis', {}).get(k) == job.get(k) for k in ('job_id', 'state', 'failure', 'metrics')),
            'analysis_job_status core state/metrics differ from final durable record.')
    metrics = status.get('analysis', {}).get('metrics')
    require(isinstance(metrics, dict), 'Status has no metrics block.')
    require(metrics.get('received', 0) > 0 and metrics.get('feed_alive') is False and metrics.get('lost_frames') is False,
            'Invalid received/feed/loss diagnostics.')
    require(len(metrics.get('history', [])) <= 1024, 'History exceeds 1024 entries.')
    last = row['input']['n_frames'] - 1
    for name in metrics_names:
        require(metrics.get('latest', {}).get(name, {}).get('frames', [0, -1])[1] == last, name + ' has not reached final ordinal.')
    jobs = result.get('analysis', {}).get('jobs', [])
    require(len(jobs) == 1 and jobs[0].get('job_id') == job.get('job_id'), 'Tool result is not bound to this job.')
    snapshot = jobs[0].get('metrics', {})
    require(snapshot.get('received', 0) > 0 and all(name in snapshot.get('latest', {}) for name in metrics_names),
            'Tool result lacks last-known metric values at return (does not wait for analysis finalization).')
    return metrics


def measurements(out, rows):
    table = {arm: [] for arm in ('none', 'metrics', 'flood')}
    for row in rows:
        arm = row['condition']
        if arm not in table or row['repetition'] < 1:
            continue
        folder = out / 'runs' / row['id']
        result, clock = load(folder / 'result.json'), load(folder / 'clock.json')
        gaps = e4._gaps(folder, row)
        duration = e4.number(result.get('duration_s'), 'product duration_s')
        if duration <= 0:
            raise NotExercised('Invalid product burst duration.')
        table[arm].append(dict(run=row['id'], duration_s=duration,
            cpu_s=e4.number(clock.get('process_cpu_s'), 'process CPU delta'),
            tool_cpu_s=e4.number(clock.get('tool_process_cpu_s'), 'tool CPU delta'),
            gaps_s=gaps))
    if not all(table.values()):
        raise NotExercised('Missing scored arm repetitions.')
    summaries = {}
    for arm, runs in table.items():
        gaps = [x for r in runs for x in r['gaps_s']]
        cpu = [r['cpu_s'] for r in runs]
        summaries[arm] = dict(duration_median_s=statistics.median(r['duration_s'] for r in runs),
            gap_p50_s=statistics.median(gaps), gap_p95_s=e4.p95(gaps), gap_max_s=max(gaps),
            cpu_seconds=cpu, cpu_min_s=min(cpu), cpu_p50_s=statistics.median(cpu), cpu_p95_s=e4.p95(cpu),
            cpu_max_s=max(cpu), cpu_spread_s=max(cpu)-min(cpu),
            runs=[{k: v for k, v in r.items() if k != 'gaps_s'} for r in runs])
    baseline = summaries['none']['duration_median_s']
    return dict(arms=summaries, metrics_none_ratio=summaries['metrics']['duration_median_s']/baseline,
        flood_none_ratio=summaries['flood']['duration_median_s']/baseline,
        duration_source='product run_timelapse result.duration_s, exactly as 84b; never gate wall time',
        gap_source='saved dataset metadata via 83e4._gaps / product _report_frame_spacing',
        warm_up='repetition 0 excluded', flood_threshold=None)


def verify(gate, *, cleanup=True, echo=True):
    """Pure artifact scoring; the only optional machine action is final store restore."""
    out, results = gate.out, {str(k): [] for k in LIMBS}
    say = gate.say if echo else lambda text: None
    def check(limb, run, fn, *, reported=False):
        try:
            detail = fn()
            item = dict(status='PASS', detail=detail)
        except (NotExercised, f6.NotExercised, FileNotFoundError) as exc:
            item = dict(status='NOT EXERCISED', detail=str(exc))
        except Exception as exc:
            item = dict(status='NOT EXERCISED' if reported else 'FAIL', detail=str(exc))
        results[str(limb)].append(dict(run=run, **item))
    try:
        prep = gate.need('prepare')
        rows = prep['runs']
    except Exception as exc:
        prep, rows = {}, []
        for key in results:
            results[key].append(dict(run='prepare', status='NOT EXERCISED', detail=str(exc)))
    def discovery():
        from microclaw import skill_packages
        installed, discovery = load(out / 'installed.json'), load(out / 'discovery.json')
        roots, document = load(out / 'test-roots.json'), load(out / 'test-policy.json')
        require(roots.get('environment') == document.get('environment') == 'test', 'Not a TEST-ONLY trust policy.')
        policy = skill_packages.verify_trust_policy(document, roots)
        for label, package in PACKAGES.items():
            record = installed[label]
            require(record.get('state') == 'ready' and record['manifest']['publisher'] + '/' + record['manifest']['package_id'] == package,
                    'Package is not installed under the expected publisher.')
            skill_packages.check_release(record['intake'], policy, purpose='admission',
                now=datetime.fromisoformat(prep['prepared_at']), artifact=out / 'releases' / (label + '.zip'))
            declaration = next(o for o in record['manifest']['operations'] if o['name'] == 'observe_dataset')['metrics']
            loaded = discovery['loaded'][label]
            require(loaded['operations'][1]['metrics'] == declaration, 'load_skill lost full declarations.')
            require(package in discovery['text'] and 'observe_dataset' in discovery['text'], 'Package/operation absent from discovery.')
            for name, entry in declaration.items():
                require(f"{name} ({entry['kind']}, {entry['unit']})" in discovery['text'], 'Missing compact declaration: ' + name)
                require(entry['description'] not in discovery['text'], 'Description leaked into the prompt.')
        return 'Both signed publisher identities and full/compact discovery captured.'
    check(1, 'prepare', discovery)
    def complete_plan():
        require(rows == plan(prep['digests'], prep['save_dir'], tuple(prep['sizes'])), 'Recorded plan is incomplete or altered.')
        require(prep.get('selftest') or prep['sizes'] == list(SIZES), 'Real gate sizes differ from the fixed 6 x 1000 plan.')
        return 'Complete rotating plan; repetition 0 excluded from cadence comparisons.'
    check(2, 'plan', complete_plan)
    diagnostics = e4._diagnostics(out)
    for row in rows:
        folder, name = out / 'runs' / row['id'], row['id']
        def frames(row=row, folder=folder):
            result, dataset = load(folder / 'result.json'), load(folder / 'dataset.json')
            require(load(folder / 'input.json') == row['input'], 'Input differs from plan.')
            end = [d for d in diagnostics if d.get('tool_call_id') == row['id'] and d.get('type') == 'acquisition_teardown_completion']
            if not end:
                raise NotExercised('No product teardown/accounting diagnostic.')
            n = row['input']['n_frames']
            if type(dataset.get('frames')) is not int:
                raise NotExercised('Saved-dataset frame count unavailable: ' + str(dataset.get('error')))
            return require('error' not in result and dataset.get('frames') == n and len(end) == 1
                and end[0].get('frames_accounted') == n and result.get('inter_frame_gap_summary', {}).get('count') == n-1,
                f'planned={n}, dataset={dataset.get("frames")}, accounting={end}')
        if scored(row):
            check(2, name, frames)
        elif row['condition'] != 'flood':
            continue
        if row['condition'] == 'metrics':
            check(3, name, lambda r=row: live(out, r, diagnostics, 'flux'))
            def photon(row=row):
                metrics = final_metrics(out, row, ('flux', 'slow'))
                return require(metrics.get('latest', {}).get('total', {}).get('value') == row['input']['n_frames'], 'total differs from planned frames.')
            check(4, name, photon)
        elif row['condition'] == 'thermal':
            def thermal(row=row):
                info = live(out, row, diagnostics, 'temperature')
                metrics = final_metrics(out, row, ('temperature',))
                job = load(out / 'runs' / row['id'] / 'job-0.json')
                require(job['release']['publisher'] == 'weather-lab' and set(metrics['declared']) == {'temperature'}
                        and metrics['declared']['temperature']['kind'] == 'gauge', 'Not the gauge-only second publisher.')
                return info
            check(5, name, thermal)
        elif row['condition'] == 'broken':
            def broken(folder=folder):
                job = load(folder / 'job-0.json')
                require(all(load(folder / 'status-0.json').get('analysis', {}).get(k) == job.get(k)
                    for k in ('job_id', 'state', 'failure', 'metrics')), 'Broken status core differs from record.')
                return require(job.get('state') == 'supervisor_failed' and job.get('failure', {}).get('reason') == 'protocol_violation'
                    and job.get('failure', {}).get('field') == 'frames', 'Broken job was not a frames protocol failure.')
            check(6, name, broken)
        elif row['condition'] == 'flood':
            def flood(folder=folder, row=row):
                require(load(folder / 'dataset.json').get('frames') == row['input']['n_frames'], 'Flood did not save all planned frames.')
                job = load(folder / 'job-0.json')
                metrics = job.get('metrics', {})
                require(job.get('state') == 'succeeded' and metrics.get('lost_frames') is False and metrics.get('feed_alive') is False,
                    'Flood did not complete cleanly without a loss latch.')
                require(metrics.get('received', 0) > 0 and metrics.get('peak_rate', 0) > 0, 'No flood lines observed.')
                return dict(received=metrics['received'], peak_rate=metrics['peak_rate'])
            check(7, name, flood)
    measurement = {}
    def cadence():
        durations = {arm: [] for arm in ('none', 'metrics')}
        for row in rows:
            if row['condition'] not in durations or row['repetition'] < 1:
                continue
            duration = e4.number(load(out / 'runs' / row['id'] / 'result.json').get('duration_s'), 'product duration_s')
            if duration <= 0:
                raise NotExercised('Nonpositive product duration.')
            durations[row['condition']].append(duration)
        if any(len(values) != prep.get('sizes', [0])[0]-1 or not values for values in durations.values()):
            raise NotExercised('Missing scored none/metrics repetitions.')
        medians = {arm: statistics.median(values) for arm, values in durations.items()}
        ratio = medians['metrics'] / medians['none']
        detail = dict(median_seconds=medians, metrics_none_ratio=ratio, limit=1.10,
            source='product run_timelapse result.duration_s, as 84b; repetition 0 excluded')
        measurement['scored_cadence'] = detail
        require(ratio <= 1.10, 'Median metrics/none cadence: ' + json.dumps(detail))
        return detail
    check(8, 'repetitions 1+', cadence)
    def report_load():
        measurement['reported_load'] = measurements(out, rows)
        return measurement['reported_load']
    check(9, 'report only; no flood performance threshold', report_load, reported=True)
    def session_calls():
        session = load(out / 'session.json')
        calls = e4.e3.recorded_calls(out / 'session-history', datetime.fromisoformat(session['started_at']))
        matches = [c for c in calls if c['name'] == 'run_timelapse' and c['input'] == session_input(prep)]
        if not matches:
            raise NotExercised('No exact session run_timelapse call/result in captured history.')
        require(len(matches) == 1, 'Session acquisition was repeated.')
        return session, calls, matches[0]
    def session_result():
        session, _, call = session_calls()
        jobs = call['result'].get('analysis', {}).get('jobs', [])
        return require('error' not in call['result'] and len(jobs) == 1 and jobs[0].get('job_id') == session.get('job_id')
            and jobs[0].get('metrics', {}).get('received', 0) > 0 and 'flux' in jobs[0]['metrics'].get('latest', {}),
            'Session tool result lacks this job\'s metrics.')
    check('S1', 'history JSONL', session_result)
    def session_status():
        session, calls, _ = session_calls()
        found = [c for c in calls if c['name'] == 'analysis_job_status' and c['input'] == {'job_id': session['job_id']}]
        if not found:
            raise NotExercised('No status call/result for the session job.')
        final = load(out / 'session-final/job-0.json')
        require(all(found[-1]['result'].get('analysis', {}).get(k) == final.get(k) for k in ('job_id', 'state', 'failure', 'metrics')),
                'Session status core differs from final job record.')
        return require(final.get('state') == 'succeeded' and final.get('metrics', {}).get('latest', {}).get('flux', {}).get('frames', [0, -1])[1]
            == prep['sizes'][2]-1 and final['metrics'].get('feed_alive') is False, 'Session final metrics incomplete.')
    check('S2', 'history JSONL + job record', session_status)
    def operator():
        session = load(out / 'session.json')
        answer = session.get('working_answer')
        capture = session.get('working_capture', {})
        if not isinstance(answer, str) or answer.strip().upper() not in ('YES', 'NO'):
            raise NotExercised('No verbatim YES/NO answer.')
        if not capture.get('live') or not capture.get('screenshot', {}).get('ok') or not (out / 'working.png').is_file():
            raise NotExercised('No successful working-line capture during a nonterminal job.')
        if capture['screenshot'].get('synthetic') and not session.get('synthetic'):
            raise NotExercised('Desktop screenshot mechanism was synthetic; answer=' + repr(answer))
        require(answer.strip().upper() == 'YES', 'OPERATOR-JUDGED answer=' + repr(answer))
        return dict(judgement='OPERATOR-JUDGED', answer=answer, synthetic=session.get('synthetic', False))
    check('O', 'operator-judged', operator)
    summary = {key: 'FAIL' if any(i['status'] == 'FAIL' for i in items) else
        'NOT EXERCISED' if not items or any(i['status'] != 'PASS' for i in items) else 'PASS' for key, items in results.items()}
    for key, items in results.items():
        for item in items:
            say(f"{item['status']}: {key} {LIMBS[int(key) if key.isdigit() else key]} [{item['run']}]: {item['detail']}")
    cleanup_note = 'not requested'
    if cleanup:
        try:
            own = load(out / 'ownership.json')
            if (own.get('host') == socket.gethostname() and own.get('store') == str(gate.store_root.resolve())
                    and own.get('evidence') == str(out.resolve())):
                cleanup_note = gate.cleanup()
            else:
                cleanup_note = 'Transported evidence: cleanup skipped; local stores untouched.'
        except Exception as exc:
            cleanup_note = 'Cleanup could not finish: ' + str(exc)
    bad = sum(v != 'PASS' for v in summary.values()) + int(str(cleanup_note).startswith('Cleanup could not finish:'))
    gate.last_results = summary
    gate.save('verify', dict(summary=summary, limbs=results, measurement=measurement, cleanup=cleanup_note,
        nonpass=bad, synthetic=prep.get('selftest', False)))
    say('MEASUREMENT: ' + json.dumps(measurement))
    say(f'RESULT: {bad} FAIL/NOT EXERCISED limbs; measurement 9 has no flood threshold. CLEANUP: {cleanup_note}')
    return bad


def selftest(output=None, sizes=(2, 120, 40)):
    from unittest.mock import patch
    from microclaw import paths, controller, tools, completed_dataset
    features()  # Old product is refused before any store mutation.
    if sizes[0] < 2 or sizes[1] < 110 or sizes[2] < 30:
        raise NotExercised('Selftest requires >=2 repetitions, >=110 burst frames and >=30 session frames to observe live progress.')
    output = Path(output).resolve() if output else None
    if output and output.exists() and any(output.iterdir()):
        raise NotExercised('Selftest output must be empty: ' + str(output))
    failures = 0
    with tempfile.TemporaryDirectory(prefix='block87a-') as temporary:
        base = Path(temporary)
        env = {name: str(base / name) for name in ('XDG_DATA_HOME', 'LOCALAPPDATA', 'XDG_CONFIG_HOME', 'APPDATA')}
        env.update(UV_OFFLINE='1', UV_CACHE_DIR=str(base / 'uv-cache'))
        with patch.dict(os.environ, env):
            config = paths.default_safety_config()
            config.parent.mkdir(parents=True)
            config.write_text(e4.SAFETY_YAML, encoding='utf-8')
            mm = base / 'fake-mm'
            (mm / 'plugins').mkdir(parents=True)
            root = paths.user_data_dir()
            root.mkdir(parents=True)
            gate = Gate(root, base / 'evidence', fake=e4.OfflineInstall())
            marker = gate.store_root / 'operator-marker.txt'
            marker.parent.mkdir(parents=True)
            marker.write_bytes(b'operator store must survive exactly\r\n')
            gate.say('SELFTEST product: ' + str(Path(tools.__file__).resolve().parent))
            gate.say('SELFTEST real: offline installs, both signed publishers, execute_tool, growing NDTiff, worker stdout, recorder and diagnostic writer.')
            gate.say('SELFTEST SYNTHETIC: bridge, acquisition imaging, UI/answers/history envelopes/screenshots. Physical cadence is observed, not asserted as demo-machine performance.')
            try:
                canonical = plan({'metrics': 'a'*64, 'thermal': 'b'*64}, base / 'datasets')
                require(len(canonical) == 20 and all(r['input']['n_frames'] == 1000 and r['input']['interval_s'] == 0
                    and r['input']['exposure_ms'] == 10 for r in canonical), 'Real 20-burst plan changed.')
                require(all([r['condition'] for r in canonical if r['repetition'] == rep] ==
                    list(('none', 'metrics', 'flood')[rep % 3:] + ('none', 'metrics', 'flood')[:rep % 3]) for rep in range(6)),
                    'Real arm order does not rotate.')
                # The inherited vectors must reject Python iteration, as the bridge does.
                vector = e4.e3.StrVector(['Camera'])
                try:
                    iter(vector)
                    raise AssertionError('Bridge fake unexpectedly iterable')
                except TypeError:
                    pass
                require(vector.size() == 1 and vector.get(0) == 'Camera', 'Bridge-shaped fake broken.')
                gate.fake.listening = 'synthetic listener'
                try:
                    gate.prepare(sizes=sizes)
                    raise AssertionError('Prepare ignored a running MicroClaw')
                except NotExercised:
                    require(marker.exists() and not gate.saved_store.exists(), 'Prepare touched operator data before refusing.')
                finally:
                    gate.fake.listening = None
                with patch.object(controller, 'Core', e4.BridgeCore), patch.object(controller, 'Studio', e4.BridgeStudio), \
                        patch.object(controller.MicroscopeController, 'get_mm_app_dir', return_value=str(mm)), \
                        patch.object(tools, 'Acquisition', e4.FakeAcquisition):
                    gate.prepare(sizes=sizes)
                    gate.measure()
                    gate.session()
                baseline = base / 'baseline'
                shutil.copytree(gate.out, baseline)
                verify(gate)
                observed = dict(gate.last_results)
                structural = all(v == 'PASS' for key, v in observed.items() if key != '8')
                require(structural, 'Unmutated structural limbs failed: ' + str(observed))
                require(marker.read_bytes() == b'operator store must survive exactly\r\n' and not gate.saved_store.exists(), 'Operator store not restored exactly.')
                gate.say('SELFTEST baseline=' + json.dumps(observed) + '; limb 8 is a fake-machine observation, not a wall-clock selftest assertion.')
                gate.cleanup()
                # Exercise 84b's interrupted restore, idempotence and owner guard.
                os.replace(gate.store_root, gate.saved_store)
                write_json(gate.store_root / '.87a-gate-owner.json', gate.need('ownership'))
                gate.cleanup(); gate.cleanup()
                os.replace(gate.store_root, gate.saved_store)
                gate.cleanup()
                require(marker.read_bytes() == b'operator store must survive exactly\r\n', 'Interrupted restore lost operator bytes.')
                # Explicit cleanup must work even if the pointer file was lost.
                os.replace(gate.store_root, gate.saved_store)
                write_json(gate.store_root / '.87a-gate-owner.json', gate.need('ownership'))
                (root / POINTER).unlink(missing_ok=True)
                Gate(root, base / 'pointerless-cleanup', fake=e4.OfflineInstall()).cleanup()
                require(marker.read_bytes() == b'operator store must survive exactly\r\n', 'Pointerless cleanup lost operator bytes.')
                gate.say('SELFTEST PASS: store restore, interrupted cleanup, pointerless cleanup and idempotence.')
                control = base / 'control'
                shutil.copytree(baseline, control)
                prep = read_json(control / 'prepare.json')
                # Only a COPY used by the scorer receives synthetic timing values.
                for row in prep['runs']:
                    path = control / 'runs' / row['id'] / 'result.json'
                    value = read_json(path); value['duration_s'] = 1.0; write_json(path, value)
                arm = Gate(root, control, fake=e4.OfflineInstall())
                verify(arm, cleanup=False, echo=False)
                require(all(v == 'PASS' for v in arm.last_results.values()), 'Synthetic timing scorer control failed: ' + str(arm.last_results))
                gate.say('SELFTEST PASS: all scorer-control limbs PASS (synthetic timing copy only).')
                first = next(r for r in prep['runs'] if r['condition'] == 'metrics' and r['repetition'] == 1)
                thermal = next(r for r in prep['runs'] if r['condition'] == 'thermal')
                flood = next(r for r in prep['runs'] if r['condition'] == 'flood' and r['repetition'] == 1)
                identical = base / 'identical-progress'
                shutil.copytree(control, identical)
                diagnostic = [d for d in e4._diagnostics(control) if d.get('tool_call_id') == first['id'] and 'analysis_metrics' in d]
                payload = {k: v for k, v in diagnostic[0].items() if k not in ('timestamp', 'session_id', 'tool_call_id')}
                write_json(identical / 'runs' / first['id'] / 'browser-events.json', [payload] * len(diagnostic))
                repeated = [dict(payload, **{k: d[k] for k in ('timestamp', 'session_id', 'tool_call_id')}) for d in diagnostic]
                require(len(progress(identical, first, repeated)) == len(repeated), 'Identical startup payloads cannot be matched.')
                missing_stamp = deepcopy(repeated)
                missing_stamp[0].pop('timestamp')
                try:
                    progress(identical, first, missing_stamp)
                    raise AssertionError('Missing product timestamp was accepted')
                except NotExercised:
                    pass
                gate.say('SELFTEST PASS: identical startup payloads matched in order; missing timestamp NOT EXERCISED.')
                def edit(folder, relative, change):
                    path = folder / relative
                    value = read_json(path); change(value); write_json(path, value)
                def edit_job(folder, row, change):
                    relative = Path('runs') / row['id']
                    edit(folder, relative / 'job-0.json', change)
                    write_json(folder / relative / 'status-0.json', dict(analysis=read_json(folder / relative / 'job-0.json')))
                def remove_live(folder):
                    def change(event):
                        metric = event.get('analysis_metrics', {}).get('values', {}).get('flux')
                        if metric:
                            metric['frames'][1] = first['input']['n_frames'] - 1
                    edit(folder, Path('runs') / first['id'] / 'browser-events.json', lambda events: [change(e) for e in events])
                    for path in folder.glob('*_microclaw_acquisitions.jsonl'):
                        values = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
                        for event in values:
                            if event.get('tool_call_id') == first['id']:
                                change(event)
                        path.write_text(''.join(json.dumps(v)+'\n' for v in values), encoding='utf-8')
                def fast_progress(folder):
                    for path in folder.glob('*_microclaw_acquisitions.jsonl'):
                        values = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
                        events = [v for v in values if v.get('tool_call_id') == first['id'] and 'analysis_metrics' in v]
                        if len(events) >= 2:
                            events[1]['timestamp'] = (datetime.fromisoformat(events[0]['timestamp']) + timedelta(seconds=0.5)).isoformat()
                            path.write_text(''.join(json.dumps(v)+'\n' for v in values), encoding='utf-8')
                            return
                    raise AssertionError('No consecutive product timestamps to mutate')
                def edit_history(folder, tool, change):
                    history = folder / 'session-history/synthetic_microclaw_history.jsonl'
                    lines = [json.loads(line) for line in history.read_text(encoding='utf-8').splitlines()]
                    ident = next(block['id'] for line in lines for block in line['content'] if block.get('type') == 'tool_use' and block['name'] == tool)
                    for line in lines:
                        for block in line['content']:
                            if block.get('type') == 'tool_result' and block['tool_use_id'] == ident:
                                value = json.loads(block['content']); change(value); block['content'] = json.dumps(value)
                    history.write_text(''.join(json.dumps(v)+'\n' for v in lines), encoding='utf-8')
                def slow_metrics(folder):
                    for row in prep['runs']:
                        if row['condition'] == 'metrics' and scored(row):
                            edit(folder, Path('runs') / row['id'] / 'result.json', lambda v: v.update(duration_s=2.0))
                mutations = [
                    ('1', 'wrong publisher', lambda f: edit(f, 'installed.json', lambda v: v['thermal']['manifest'].update(publisher='wrong-lab'))),
                    ('2', 'missing frame', lambda f: edit(f, Path('runs')/first['id']/'dataset.json', lambda v: v.update(frames=1))),
                    ('3', 'no range before final ordinal', remove_live),
                    ('3', 'progress events only 0.5 seconds apart', fast_progress),
                    ('4', 'slow final range incomplete', lambda f: edit_job(f, first, lambda v: v['metrics']['latest']['slow'].update(frames=[0, 0]))),
                    ('5', 'thermal final range incomplete', lambda f: edit_job(f, thermal, lambda v: v['metrics']['latest']['temperature'].update(frames=[0, 0]))),
                    ('6', 'broken job succeeded', lambda f: edit_job(f, {'id': 'broken'}, lambda v: v.update(state='succeeded'))),
                    ('7', 'flood loss latch', lambda f: edit_job(f, flood, lambda v: v['metrics'].update(lost_frames=True))),
                    ('8', 'metrics cadence exceeds 1.10', slow_metrics),
                    ('S1', 'session result lacks metrics', lambda f: edit_history(f, 'run_timelapse', lambda v: v['analysis']['jobs'][0].pop('metrics'))),
                    ('S2', 'session status lacks metrics', lambda f: edit_history(f, 'analysis_job_status', lambda v: v['analysis'].pop('metrics'))),
                    ('O', 'operator NO', lambda f: edit(f, 'session.json', lambda v: v.update(working_answer=' NO '))),
                ]
                for index, (key, label, mutate) in enumerate(mutations):
                    folder = base / ('mutation-' + str(index) + '-' + key)
                    shutil.copytree(control, folder)
                    mutate(folder)
                    arm = Gate(root, folder, fake=e4.OfflineInstall())
                    verify(arm, cleanup=False, echo=False)
                    wrong = {k: v for k, v in arm.last_results.items() if v != 'PASS' and k != key}
                    ok = arm.last_results[key] == 'FAIL' and not wrong
                    failures += not ok
                    gate.say(f'SELFTEST {"PASS" if ok else "FAIL"}: limb {key} {label} -> {arm.last_results[key]}; collateral={wrong}')
                for key, relative in [('3', Path('runs')/first['id']/'browser-events.json'),
                        ('9', Path('runs')/flood['id']/'clock.json')]:
                    folder = base / ('missing-' + key); shutil.copytree(control, folder)
                    (folder / relative).unlink()
                    arm = Gate(root, folder, fake=e4.OfflineInstall()); verify(arm, cleanup=False, echo=False)
                    ok = arm.last_results[key] == 'NOT EXERCISED'
                    if key == '9':
                        ok = ok and all(v == 'PASS' for k, v in arm.last_results.items() if k != '9')
                    failures += not ok
                    gate.say(f'SELFTEST {"PASS" if ok else "FAIL"}: missing evidence limb {key} -> {arm.last_results[key]} (9 never FAIL).')
                # Off-rig verification must neither contact hardware nor touch
                # an unrelated local store, even when cleanup=True is requested.
                arm = Gate(root, control, fake=e4.OfflineInstall())
                with patch.object(arm, 'connect', side_effect=AssertionError('verify contacted hardware')), \
                        patch.object(arm, 'cleanup', side_effect=AssertionError('transported evidence touched store')):
                    require(verify(arm, cleanup=True, echo=False) == 0, 'Transported evidence failed file-only verification.')
                require(marker.read_bytes() == b'operator store must survive exactly\r\n', 'Off-rig scoring changed the operator store.')
                launcher = (HERE / '87-block87a-demo-gate.ps1').read_text(encoding='utf-8')
                runbook = (HERE / '87-block87a-demo-gate.md').read_text(encoding='utf-8')
                require(all(token in launcher for token in ("env-$active", "-I -c", "'1.2' in skill_packages.SUPPORTED_PROTOCOLS",
                    'JobHandle', 'metrics_snapshot', 'exit 2', '87-block87a-demo-gate.py', '--selftest-sizes')),
                    'PowerShell installed-slot/feature/phase wiring missing.')
                require(runbook.count('<PIN>') == 1 and 'git checkout block-87a' in runbook,
                    'Runbook pin/branch wiring invalid.')
                gate.say('SELFTEST PASS: transported file-only scoring; launcher/runbook source wiring (PowerShell execution not exercised).')
                gate.save('selftest', dict(failed_controls=failures, baseline=observed, mutations=len(mutations),
                    synthetic=['bridge/acquisition', 'UI/answers/history envelope/screenshots', 'timing scorer control'],
                    real=['offline installs', 'workers', 'growing NDTiff', 'product tools', 'diagnostic and job records']))
                gate.say('SELFTEST ' + ('PASSED' if not failures else 'FAILED') + f': {failures} failed controls.')
            except Exception as exc:
                failures += 1
                completed_dataset.close_analysis_supervisor()
                gate.say('SELFTEST FAILED: ' + str(exc) + '\n' + traceback.format_exc())
                try:
                    gate.cleanup()
                except Exception as cleanup_error:
                    gate.say('Cleanup failed: ' + str(cleanup_error))
            finally:
                if output:
                    shutil.copytree(gate.out, output, dirs_exist_ok=True)
    return int(bool(failures))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('phase', choices=('prepare', 'measure', 'session', 'verify', 'cleanup', 'selftest'))
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--port', type=int, default=4827)
    parser.add_argument('--selftest-sizes', type=int, nargs=3, default=(2, 120, 40), metavar=('REPETITIONS', 'BURST_FRAMES', 'SESSION_FRAMES'))
    args = parser.parse_args()
    gate = None
    try:
        if args.phase == 'selftest':
            return selftest(args.out, tuple(args.selftest_sizes))
        from microclaw import paths
        gate = Gate(paths.user_data_dir(), args.out)
        if args.phase == 'verify':
            return int(bool(verify(gate)))
        if args.phase == 'prepare':
            gate.prepare(port=args.port)
        else:
            result = getattr(gate, args.phase)()
            if args.phase == 'cleanup':
                gate.save('cleanup', result)
        gate.say('RECORDED: ' + args.phase)
        return 0
    except (NotExercised, f6.NotExercised, SystemExit) as exc:
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
