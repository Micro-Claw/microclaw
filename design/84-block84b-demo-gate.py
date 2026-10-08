"""84b package-window demo gate: lifecycle, user Close, quit, cadence and lag.

Measure is standalone, with real execute_tool/consent and one window at a time.
Verify reads files only; transported evidence can be scored without a microscope.
Selftest uses 83e4's bridge-shaped fakes, real offline installs and worker trees.
Tk availability and human-session evidence are explicitly synthetic in selftest.
"""
from __future__ import annotations

import argparse
import ast
import base64
from datetime import datetime
import importlib.util
import inspect
import json
import math
import os
from pathlib import Path
import re
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import time
import traceback

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('gate83e4', HERE / '83-block83e4-demo-gate.py')
e4 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(e4)
_spec = importlib.util.spec_from_file_location('gate83f6', HERE / '83-block83f6-demo-gate.py')
f6 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(f6)
read_json, write_json, now = e4.read_json, e4.write_json, e4.now
NotExercised, FIXTURES = e4.NotExercised, e4.FIXTURES
# f6 imports a separate instance of 83d; its exception class is distinct.
StoreNotExercised = f6.NotExercised
PACKAGE = 'fixture-lab/executable-fixture'
ADAPTER = PACKAGE + ':fixture_window'
POINTER = '84b-gate-evidence.txt'
IDLE_S = 5
SIZES = (6, 1000)  # one warm-up and five repetitions, two alternating conditions
PREFIX = "Opens a window on this computer's desktop ("
THRESHOLD = dict(cadence_ratio=1.10, lag_p95_s=0.100, lag_max_s=1.0)
LIMBS = {
    1: 'tkinter in the fixture interpreter', 2: 'frames saved equal planned',
    3: 'job final while window open (measure)', 4: 'close_window kills (measure)',
    5: 'consent', 6: 'session: job final while open', 7: 'session: panel Close kills',
    8: 'session: quit kills', 10: 'responsiveness and cadence',
}
SCREENSHOTS = ('window.png', 'panel.png')


def plan(digest, save_dir, sizes=SIZES, backend='tkinter'):
    runs = []
    for rep in range(sizes[0]):
        for condition in (('none', 'window') if rep % 2 == 0 else ('window', 'none')):
            name = f'r{rep}-{condition}'
            args = dict(n_frames=sizes[1], interval_s=0, exposure_ms=10,
                        save_dir=str(save_dir), name=name)
            if condition == 'window':
                args['analysis'] = dict(adapter=ADAPTER, release_digest=digest,
                                        parameters={'display_backend': backend})
            runs.append(dict(id=name, repetition=rep, type='burst', condition=condition, input=args))
    return runs


def product_disclosure():
    """Read the installed f-string's literal prefix, as 83e4 reads its disclosure."""
    from microclaw import completed_dataset
    found = {node.value for node in ast.walk(ast.parse(inspect.getsource(completed_dataset)))
             if isinstance(node, ast.Constant) and isinstance(node.value, str)
             and node.value.startswith(PREFIX)}
    return found.pop() if len(found) == 1 else None


def process_snapshot():
    """Actual fixture processes; query failures are never recorded as an empty list."""
    if os.name == 'nt':
        script = ("$ErrorActionPreference='Stop'; [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); "
                  "@(Get-CimInstance Win32_Process | "
                  "Where-Object { $_.ProcessId -ne $PID -and $_.CommandLine -match "
                  "'(?i)(?:^|\\s)-m\\s+fixture_worker[.]runner(?:\\s|$)' } | "
                  "Select-Object @{n='pid';e={$_.ProcessId}},@{n='command';e={$_.CommandLine}}) | ConvertTo-Json -Compress")
        command = ['powershell', '-NoProfile', '-Command', script]
    else:
        command = ['ps', '-axo', 'pid=,command=']
    try:
        captured = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                                  text=True, encoding='utf-8', timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return dict(captured_at=now().isoformat(), exit_code=None, stderr=str(exc), processes=None)
    result = dict(captured_at=now().isoformat(), exit_code=captured.returncode,
                  stderr=captured.stderr, processes=None)
    if captured.returncode:
        return result
    if os.name == 'nt':
        value = json.loads(captured.stdout or '[]')
        processes = value if isinstance(value, list) else [value] if isinstance(value, dict) else []
    else:
        processes = []
        for line in captured.stdout.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) == 2 and re.search(r'(?:^|\s)-m\s+fixture_worker[.]runner(?:\s|$)', parts[1]):
                processes.append(dict(pid=int(parts[0]), command=parts[1]))
    result['processes'] = [p for p in processes if int(p['pid']) != os.getpid()]
    return result


def process_count(snapshot):
    if snapshot.get('exit_code') != 0 or not isinstance(snapshot.get('processes'), list):
        raise NotExercised('process query unavailable: ' + str(snapshot.get('stderr')))
    return len(snapshot['processes'])


def lag_samples(tail):
    samples = []
    for value in re.findall(r'^event_loop_lag_s=([^\s]+)\s*$', tail or '', re.MULTILINE):
        try:
            number = float(value)
            if math.isfinite(number) and number >= 0:
                samples.append(number)
        except ValueError:
            continue
    return samples


def lag_stats(tail, *, drop_first=False):
    from microclaw.skill_supervisor import MAX_STDERR_BYTES
    samples = lag_samples(tail)[1:] if drop_first else lag_samples(tail)
    return dict(n=len(samples), p50_s=statistics.median(samples) if samples else None,
                p95_s=e4.p95(samples) if samples else None, max_s=max(samples) if samples else None,
                tail_truncated=len((tail or '').encode('utf-8')) >= MAX_STDERR_BYTES,
                n_is_lower_bound=len((tail or '').encode('utf-8')) >= MAX_STDERR_BYTES)


def screenshot(path, *, synthetic=False):
    """Capture the virtual desktop; failures are evidence, never exceptions."""
    result = dict(ok=False, path=str(path), error=None)
    try:
        path = path if isinstance(path, Path) else Path(path)
        if synthetic or os.name != 'nt':
            Path(path).write_bytes(base64.b64decode(
                'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aSFAAAAAASUVORK5CYII='))
            result.update(ok=True, synthetic=True)
            return result
        destination = str(path).replace("'", "''")
        command = (
            "$ErrorActionPreference='Stop'; "
            "Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
            "$screen=[System.Windows.Forms.SystemInformation]::VirtualScreen; "
            "$bitmap=New-Object System.Drawing.Bitmap($screen.Width,$screen.Height); "
            "$graphics=$null; try { $graphics=[System.Drawing.Graphics]::FromImage($bitmap); "
            "$graphics.CopyFromScreen($screen.Left,$screen.Top,0,0,$screen.Size); "
            "$bitmap.Save('" + destination + "',[System.Drawing.Imaging.ImageFormat]::Png) "
            "} finally { if ($graphics) { $graphics.Dispose() }; $bitmap.Dispose() }")
        capture = subprocess.run(['powershell', '-NoProfile', '-Command', command],
                                 stdin=subprocess.DEVNULL, capture_output=True, timeout=30,
                                 encoding='utf-8', errors='replace')
        result.update(exit_code=capture.returncode, stdout=capture.stdout, stderr=capture.stderr)
        result['ok'] = capture.returncode == 0 and path.is_file() and path.stat().st_size > 0
        if not result['ok']:
            result['error'] = capture.stderr or capture.stdout or 'Screenshot was not saved.'
    except Exception as exc:
        result['error'] = str(exc)
    return result


class Gate(e4.Gate, f6.Gate):
    """Reuse connection, wait_terminal, log, ask and install tooling from earlier gates."""
    @property
    def saved_store(self):
        return self.store_root.with_name(self.store_root.name + '.84b-saved')

    def owned_store(self):
        try:
            own = read_json(self.store_root / '.84b-gate-owner.json')
            return own.get('host') == socket.gethostname() and own.get('store') == str(self.store_root.resolve())
        except (OSError, ValueError, AttributeError):
            return False

    def cleanup(self):
        # 83f6's rename/restore pattern, with only this gate's pointer removed.
        # An interrupted restore with no test store is also safe to finish.
        self.close_microclaw('Cleanup restores your package store.')
        saved = self.saved_store
        if self.store_root.exists() and not self.owned_store():
            if saved.exists():
                raise NotExercised('Both a non-test store and its backup exist; neither was changed.')
            self.say('Operator store is already restored; left untouched.')
        else:
            if self.store_root.exists():
                (self.store_root / 'trust' / 'roots.json').unlink(missing_ok=True)
                shutil.rmtree(self.store_root)
            if saved.exists():
                os.replace(saved, self.store_root)
                self.say('Operator package store restored: ' + str(self.store_root))
        (self.root / POINTER).unlink(missing_ok=True)
        return dict(saved_store_present=saved.exists(), test_store_present=self.test_store(),
                    evidence=str(self.out))

    def prepare(self, *, port=4827, sizes=SIZES):
        self.close_microclaw('Prepare needs MicroClaw closed; leave Micro-Manager open.')
        if self.saved_store.exists() or self.test_store() or self.owned_store():
            raise NotExercised('A previous gate store/backup exists; run cleanup first.')
        parsed, guard, ctrl = self.connect(port)
        workspace = parsed.constraints.workspace_dir
        base = Path(guard.resolve_in_workspace('.') if workspace else self.root).resolve()
        save_dir = base / ('block84b-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
        backend = 'headless' if self.fake else 'tkinter'
        disk = e4.disk_check(ctrl.core, base, plan('', save_dir, sizes, backend))
        # Durable ownership is recorded before the first store mutation.
        self.save('ownership', dict(store=str(self.store_root.resolve()), host=socket.gethostname(),
                                    evidence=str(self.out.resolve()), saved_store=str(self.saved_store)))
        if self.store_root.exists():
            os.replace(self.store_root, self.saved_store)
            self.say('Operator store set aside: ' + str(self.saved_store))
        write_json(self.store_root / '.84b-gate-owner.json', self.need('ownership'))
        write_json(self.store_root / 'trust' / 'roots.json',
                   read_json(FIXTURES / 'trust' / 'roots-TEST-ONLY.json'))
        policy = self.store.store_trust_policy(read_json(FIXTURES / 'trust' / 'policy-TEST-ONLY.json'))
        artifact = self.out / 'releases' / 'executable-1.0.0.zip'
        artifact.parent.mkdir(parents=True, exist_ok=True)
        intake = e4.gate83d.load_builder().build_release(FIXTURES / 'executable', artifact, version='1.0.0')
        record = self.store.install(intake, artifact, policy=policy, now=now(), uv_executable=self.uv(),
                                    retained_digests=frozenset(), find_links=(FIXTURES / 'wheels').resolve(),
                                    base_python=self.base_python())
        if self.fake:
            probe = dict(exit_code=0, stdout='', stderr='', python=record['python'], synthetic=True,
                         reason='SELFTEST synthetic Tk availability; headless worker does not exercise Tk')
        else:
            try:
                completed = subprocess.run([record['python'], '-I', '-c',
                    'import tkinter; r = tkinter.Tk(); r.withdraw(); r.update(); r.destroy()'],
                    stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8', timeout=60)
                probe = dict(exit_code=completed.returncode, stdout=completed.stdout, stderr=completed.stderr,
                             python=record['python'], synthetic=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                probe = dict(exit_code=None, stdout='', stderr=str(exc), python=record['python'], synthetic=False)
        self.save('tkinter-probe', probe)
        prep = dict(digest=intake['artifact_digest'], save_dir=str(save_dir), port=port, disk=disk,
                    sizes=list(sizes), backend=backend, idle_s=0.1 if self.fake else IDLE_S,
                    selftest=bool(self.fake), prepared_at=now().isoformat(),
                    runs=plan(intake['artifact_digest'], save_dir, sizes, backend))
        self.save('prepare', prep)
        (self.root / POINTER).write_text(str(self.out.resolve()) + '\n', encoding='utf-8')
        self.say(f"Disk: {disk['free_bytes']} free bytes, {disk['projected_bytes']} projected bytes.")
        self.say('Tk probe: ' + json.dumps(probe))
        for rep in range(sizes[0]):
            self.say(f"PLAN repetition {rep}{' (warm-up)' if rep == 0 else ''}: " +
                     ', '.join(r['condition'] for r in prep['runs'] if r['repetition'] == rep))

    def window_run(self, job, folder, prep):
        from microclaw.completed_dataset import analysis_supervisor
        self.wait_terminal(job, folder, 0)
        supervisor = analysis_supervisor()
        job_id = job['job_id']
        write_json(folder / 'windows-open.json', dict(windows=supervisor.list_open_windows(),
                   diagnostics=supervisor.window_diagnostics(job_id), captured_at=now().isoformat()))
        write_json(folder / 'processes-open.json', process_snapshot())
        time.sleep(prep['idle_s'])
        write_json(folder / 'windows-idle.json', dict(diagnostics=supervisor.window_diagnostics(job_id),
                                                   captured_at=now().isoformat()))
        outcome = supervisor.close_window(job_id)
        write_json(folder / 'close.json', outcome)
        deadline = time.perf_counter() + 10
        windows = supervisor.list_open_windows()
        while any(w['job_id'] == job_id for w in windows) and time.perf_counter() < deadline:
            time.sleep(0.05)
            windows = supervisor.list_open_windows()
        write_json(folder / 'windows-close.json', dict(windows=windows, captured_at=now().isoformat()))
        write_json(folder / 'processes-close.json', process_snapshot())
        if windows:
            raise NotExercised('Close left a window listed; no next window will be launched.')

    def measure(self):
        from microclaw import tools, completed_dataset
        from microclaw.conversation import AcquisitionDiagnosticWriter, AuditLog
        prep = self.need('prepare')
        if (self.out / 'measure.json').exists():
            raise NotExercised('Measure already started here; preserve this evidence and prepare afresh.')
        self.close_microclaw('Measure drives acquisitions without serve.')
        parsed, guard, ctrl = self.connect(prep['port'])
        disk = e4.disk_check(ctrl.core, prep['disk']['dataset_root'], prep['runs'])
        if disk['bytes_per_frame'] != prep['disk']['bytes_per_frame']:
            raise NotExercised('Camera frame size changed since prepare.')
        stamp = datetime.now().strftime('%Y%m%d_%H%M%S_%f')
        session_id = stamp + '_microclaw_history'
        writer = AcquisitionDiagnosticWriter(AuditLog(self.out / (stamp + '_microclaw_acquisitions.jsonl'),
                                                     enabled=True, secrets=()))
        self.save('measure', dict(started_at=now().isoformat(), hostname=socket.gethostname(),
                                 disclosure_prefix=product_disclosure(), python=sys.executable,
                                 product=str(Path(tools.__file__).resolve().parent),
                                 session_id=session_id, disk=disk))
        previous = tools.CONFIRM_FN
        completed = 0
        probe = self.need('tkinter-probe')
        try:
            for row in prep['runs']:
                folder = self.out / 'runs' / row['id']
                folder.mkdir(parents=True)
                write_json(folder / 'input.json', row['input'])
                if row['condition'] == 'window' and probe['exit_code'] != 0:
                    write_json(folder / 'skipped.json', dict(reason='Tk probe failed: ' + probe['stderr']))
                    continue
                if completed_dataset.analysis_supervisor().list_open_windows():
                    raise NotExercised('A previous window is still listed; stopped to avoid overlap.')
                prompts = []
                write_json(folder / 'confirmations.json', prompts)
                tools.SESSION_GRANTS.clear()
                tools.CONFIRM_FN = e4.recording_confirm(prompts, PACKAGE + '@' + prep['digest'] + '+window',
                    lambda p, f=folder: write_json(f / 'confirmations.json', p))
                clock = dict(start_utc=now().isoformat(), start_perf=time.perf_counter())
                self.say('RUN ' + row['id'])
                try:
                    raw = tools.execute_tool('run_timelapse', json.loads(json.dumps(row['input'])), ctrl, guard,
                                             acquisition_diagnostic_writer=writer,
                                             acquisition_session_id=session_id, tool_call_id=row['id'])
                    result = json.loads(raw)
                    write_json(folder / 'result.json', result)
                finally:
                    clock.update(end_utc=now().isoformat(), end_perf=time.perf_counter())
                    write_json(folder / 'clock.json', clock)
                try:
                    write_json(folder / 'dataset.json', e4.dataset_evidence(result))
                except Exception as exc:
                    write_json(folder / 'dataset.json', dict(error=str(exc)))
                jobs = (result.get('analysis') or {}).get('jobs') or []
                if row['condition'] == 'window' and len(jobs) == 1:
                    self.window_run(jobs[0], folder, prep)
                if any(not p['approved'] for p in prompts) or 'error' in result:
                    self.say('Run returned an error or unexpected consent; evidence retained: ' + row['id'])
                completed += 1
        finally:
            tools.CONFIRM_FN = previous
            writer.close()
            completed_dataset.close_analysis_supervisor()
            self.save('measure-end', dict(ended_at=now().isoformat(), completed=completed, planned=len(prep['runs'])))

    def session_snapshot(self, step, existing):
        # This is the product's record directory, never a guessed store subpath.
        directory = self.store.analysis_job_path('0' * 32).parent
        records = []
        for path in sorted(directory.glob('*.json')):
            if path.name not in existing:
                try:
                    records.append(read_json(path))
                except (OSError, ValueError) as exc:
                    records.append(dict(path=str(path), error=str(exc)))
        snapshot = dict(captured_at=now().isoformat(), records=records,
                        processes=process_snapshot(), listening=bool(e4.serve_listening()))
        if step in (2, 3):
            name = SCREENSHOTS[step - 2]
            snapshot['screenshots'] = {name: screenshot(self.out / name, synthetic=bool(self.fake))}
        self.save(f'session-step-{step}', snapshot)

    def session(self):
        prep = self.need('prepare')
        if self.need('tkinter-probe')['exit_code'] != 0:
            raise NotExercised('Tk probe failed; human window steps cannot be exercised.')
        if (self.out / 'session.json').exists():
            raise NotExercised('Session evidence already exists; refusing to overwrite.')
        # A saved dataset actually measured, not a path invented from the plan.
        dataset = next((read_json(self.out / 'runs' / r['id'] / 'result.json').get('dataset_path')
                        for r in prep['runs'] if (self.out / 'runs' / r['id'] / 'result.json').is_file()), None)
        if not dataset:
            raise NotExercised('No measured dataset available; run measure first.')
        existing = {p.name for p in self.store.analysis_job_path('0' * 32).parent.glob('*.json')}
        session = dict(started_at=now().isoformat(), completed=[], answers={}, dataset=dataset)
        self.save('session', session)
        def chat(output):
            return (f'Run the community package analysis {ADAPTER} with release digest {prep["digest"]} '
                    f'on the saved dataset {dataset}, parameters {{"display_backend": "tkinter"}}, '
                    f'output directory {output}.')
        outputs = [str(Path(prep['save_dir']) / ('session-out-' + str(i))) for i in (1, 2)]
        for output in outputs:
            if Path(output).exists():
                raise NotExercised('Session output already exists: ' + output)
        steps = {
            1: 'Launch MicroClaw from its desktop icon and wait for Firefox.',
            2: 'Paste into Firefox chat:\n' + chat(outputs[0]) + '\nApprove the confirmation; wait for '
               'the fixture window and for the reply to finish. Leave the fixture window visible, '
               'not behind Firefox. The gate captures window.png after DONE in ' + str(self.out),
            3: 'Open Community skill packages so the open-window row is visible. '
               'The gate captures panel.png after DONE in ' + str(self.out),
            4: 'Click Close on that open-window row.',
            5: 'Paste into Firefox chat:\n' + chat(outputs[1]) + '\nApprove; wait for the fixture window '
               'and for the analysis reply to finish before DONE.',
            6: 'Quit MicroClaw by closing its console and launcher windows. Leave Firefox open.',
        }
        for step, prompt in steps.items():
            self.say(f'STEP {step}: ' + prompt)
            try:
                session['answers'][str(step)] = self.ask('Type DONE when finished (STOP abandons).', str(step))
                self.session_snapshot(step, existing)
                session['completed'].append(step)
                self.save('session', session)
                if step == 2:
                    while True:
                        self.discard_pending_input()
                        self.say('Did a window titled "MicroClaw fixture window" appear? Type YES or NO (STOP abandons).')
                        answer = input('> ')
                        self.say('ANSWER[appeared]: ' + repr(answer))
                        if answer.strip().upper() == 'STOP':
                            raise NotExercised('Operator stopped the gate.')
                        if answer.strip().upper() in ('YES', 'NO'):
                            session['appeared'] = answer  # verbatim, including spaces/case
                            self.save('session', session)
                            break
            except (NotExercised, StoreNotExercised):
                session['stopped_at'] = step
                self.save('session', session)
                raise


def load(path):
    try:
        return e4._load(path)
    except (OSError, ValueError) as exc:
        raise NotExercised(f'Unreadable {Path(path).name}: {exc}') from exc


def require(condition, detail):
    if not condition:
        raise AssertionError(detail)
    return 'ok'


def tk_available(out):
    probe = load(out / 'tkinter-probe.json')
    if probe.get('exit_code') != 0:
        raise NotExercised('Tk probe failed: ' + str(probe.get('stderr') or probe.get('reason')))
    return probe


def threshold_table(out, prep):
    table = []
    for row in prep['runs']:
        folder = out / 'runs' / row['id']
        try:
            duration = load(folder / 'result.json').get('duration_s')
        except NotExercised:
            duration = None
        tail = None
        if row['condition'] == 'window':
            try:
                tail = load(folder / 'job-0.json').get('stderr_tail')
            except NotExercised:
                pass
        table.append(dict(id=row['id'], condition=row['condition'], repetition=row['repetition'],
                          duration_s=duration, lag=lag_samples(tail)[1:], truncated=lag_stats(tail)['tail_truncated']))
    return table


def score_threshold(table):
    """Exclude warm-up and each window's startup tick from scored lag."""
    for row in table:
        duration = row.get('duration_s')
        if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
            return dict(status='NOT EXERCISED', detail=row['id'] + ': missing/invalid duration_s')
        if row['condition'] == 'window' and row['repetition'] >= 1 and not row['lag']:
            return dict(status='NOT EXERCISED', detail=row['id'] + ': no pre-result lag samples left after dropping first sample')
    durations = {condition: [r['duration_s'] for r in table if r['condition'] == condition and r['repetition'] >= 1]
                 for condition in ('none', 'window')}
    if not all(durations.values()):
        return dict(status='NOT EXERCISED', detail='Missing measured repetitions of a condition.')
    medians = {condition: statistics.median(values) for condition, values in durations.items()}
    samples = [v for row in table if row['condition'] == 'window' and row['repetition'] >= 1 for v in row['lag']]
    if not samples:
        return dict(status='NOT EXERCISED', detail='No window lag samples.')
    ratio, p95, maximum = medians['window'] / medians['none'], e4.p95(samples), max(samples)
    passed = ratio <= THRESHOLD['cadence_ratio'] and p95 <= THRESHOLD['lag_p95_s'] and maximum <= THRESHOLD['lag_max_s']
    truncated = [row['id'] for row in table if row['condition'] == 'window' and row['repetition'] >= 1 and row['truncated']]
    detail = (f"median cadence ratio={ratio:.6f} <= {THRESHOLD['cadence_ratio']:.2f} "
              f"(window={medians['window']:.6f}s, none={medians['none']:.6f}s, repetitions 1+); "
              f"pooled pre-result lag p95={p95:.6f}s <= {THRESHOLD['lag_p95_s']:.3f}s, "
              f"max={maximum:.6f}s <= {THRESHOLD['lag_max_s']:.1f}s, n={len(samples)} (window repetitions 1+, warm-up excluded, first sample per run dropped)")
    if truncated:
        detail += '; 64 KiB tails truncated (sample counts are lower bounds): ' + ', '.join(truncated)
    return dict(status='PASS' if passed else 'FAIL', detail=detail)


def verify(gate, *, cleanup=True, echo=True):
    """Each limb/run scores independently. No current microscope or supervisor reads."""
    say = gate.say if echo else lambda text: None
    out = gate.out
    outcomes = {limb: [] for limb in LIMBS}
    def check(limb, name, fn):
        try:
            value = fn()
            item = value if isinstance(value, dict) and 'status' in value else dict(status='PASS', detail=value)
        except (NotExercised, StoreNotExercised) as exc:
            item = dict(status='NOT EXERCISED', detail=str(exc))
        except Exception as exc:
            item = dict(status='FAIL', detail=f'{type(exc).__name__}: {exc}')
        outcomes[limb].append(dict(run=name, **item))
    try:
        prep = gate.need('prepare')
        runs = prep['runs']
    except Exception as exc:
        prep, runs = {}, []
        for limb in outcomes:
            outcomes[limb].append(dict(run='prepare', status='NOT EXERCISED', detail=str(exc)))
    def probe_score():
        probe = tk_available(out)
        return 'Tk interpreter probe succeeded' + (' (SYNTHETIC selftest probe)' if probe.get('synthetic') else '')
    check(1, 'prepare', probe_score)
    diagnostics = e4._diagnostics(out)
    for row in runs:
        folder = out / 'runs' / row['id']
        def frames():
            result, dataset = load(folder / 'result.json'), load(folder / 'dataset.json')
            require(load(folder / 'input.json') == row['input'], 'Tool input differs from recorded plan.')
            n = row['input']['n_frames']
            mine = [d for d in diagnostics if d.get('tool_call_id') == row['id']]
            built = [d for d in mine if d.get('type') == 'acquisition_construction']
            torn = [d for d in mine if d.get('type') == 'acquisition_teardown_completion']
            if not built or not torn:
                raise NotExercised('No construction/teardown diagnostics.')
            require(len(built) == len(torn) == 1, 'Expected one construction and teardown.')
            count = (result.get('inter_frame_gap_summary') or {}).get('count')
            return require('error' not in result and built[0].get('frames_planned') == n
                           and torn[0].get('frames_accounted') == n and dataset.get('frames') == n
                           and type(count) is int and count + 1 == n,
                           f"planned={n}, accounted={torn[0].get('frames_accounted')}, "
                           f"dataset={dataset.get('frames')}, callback gaps={count}")
        check(2, row['id'], frames)
        if row['condition'] != 'window':
            continue
        def final_open():
            tk_available(out)
            record = load(folder / 'job-0.json')
            result = load(folder / 'result.json')
            dispatched = (result.get('analysis') or {}).get('jobs') or []
            require(len(dispatched) == 1 and dispatched[0].get('job_id') == record.get('job_id'),
                    'Not this acquisition\'s single analysis job.')
            snap = load(folder / 'windows-open.json')
            require(record.get('state') == 'succeeded' and record.get('window_retained') is True
                    and record.get('exit_code') is None, 'Job was not succeeded and handed off: ' + str(record.get('state')))
            require(any(w.get('job_id') == record['job_id'] for w in snap['windows']), 'Job not in open list.')
            require(isinstance(snap.get('diagnostics'), dict) and snap['diagnostics'].get('exit_code') is None,
                    'Window diagnostics do not show a live process.')
            return require(process_count(load(folder / 'processes-open.json')) >= 1, 'No fixture process alive.')
        check(3, row['id'], final_open)
        def close_kills():
            tk_available(out)
            if process_count(load(folder / 'processes-open.json')) == 0:
                raise NotExercised('Control: no fixture process before Close.')
            record = load(folder / 'job-0.json')
            require(load(folder / 'close.json') == {'state': 'closed'}, 'Close did not report closed.')
            require(not any(w.get('job_id') == record['job_id'] for w in load(folder / 'windows-close.json')['windows']),
                    'Closed job still in window list.')
            return require(process_count(load(folder / 'processes-close.json')) == 0, 'Fixture process survived Close.')
        check(4, row['id'], close_kills)
        def consent():
            info = load(out / 'measure.json')
            prefix, host = info.get('disclosure_prefix'), info.get('hostname')
            require(isinstance(prefix, str) and prefix.startswith(PREFIX) and bool(host), 'Missing installed disclosure/host.')
            prompts = load(folder / 'confirmations.json')
            analysis = [p for p in prompts if p.get('kind') == 'analysis']
            subject = PACKAGE + '@' + prep['digest'] + '+window'
            require(len(analysis) == 1 and analysis[0].get('subject') == subject
                    and analysis[0].get('approved') is True, 'Missing exact +window analysis consent.')
            require(prefix + host + ')' in analysis[0].get('summary', ''), 'Missing host disclosure in analysis prompt.')
            return require(all(p.get('approved') and ((p.get('kind') == 'analysis' and p.get('subject') == subject)
                    or (p.get('kind') == 'acquisition' and p.get('subject') == 'threshold')) for p in prompts),
                    'Only analysis and acquisition-size prompts approved.')
        check(5, row['id'], consent)
    def session_step(step):
        tk_available(out)
        return load(out / f'session-step-{step}.json')
    def viewer_records(snapshot):
        return [r for r in snapshot['records'] if r.get('operation') == 'fixture_window']
    def final_session():
        snap = session_step(2)
        records = viewer_records(snap)
        require(any(r.get('state') == 'succeeded' and r.get('window_retained') is True for r in records),
                'No succeeded, handed-off fixture_window record at step 2.')
        return require(process_count(snap['processes']) >= 1, 'No process while session job was final.')
    check(6, 'step-2', final_session)
    def panel_close():
        before, after = session_step(3), session_step(4)
        if process_count(before['processes']) == 0:
            raise NotExercised('Control: no fixture process at step 3.')
        ids = {r['job_id'] for r in viewer_records(before)}
        require(ids and ids <= {r['job_id'] for r in viewer_records(after)}, 'Step 4 lacks step 3 job record.')
        return require(process_count(after['processes']) == 0 and after.get('listening') is True,
                       'Close must kill while MicroClaw remains listening at step 4.')
    check(7, 'step-4', panel_close)
    def quit_kills():
        previous, before, after = session_step(4), session_step(5), session_step(6)
        old = {r['job_id'] for r in viewer_records(previous)}
        new = [r for r in viewer_records(before) if r['job_id'] not in old]
        require(new and process_count(before['processes']) >= 1, 'No new fixture job/process at step 5.')
        return require(process_count(after['processes']) == 0 and after.get('listening') is False,
                       'Fixture survived quit or MicroClaw still listening at step 6.')
    check(8, 'step-6', quit_kills)
    def responsiveness():
        tk_available(out)
        if not runs:
            raise NotExercised('No plan captured.')
        return score_threshold(threshold_table(out, prep))
    check(10, 'all-runs', responsiveness)
    try:
        session = load(out / 'session.json')
        answer = session.get('appeared')
        screenshots = {}
        for step, name in zip((2, 3), SCREENSHOTS):
            try:
                screenshots[name] = load(out / f'session-step-{step}.json').get('screenshots', {}).get(
                    name, dict(ok=False, error='No screenshot result recorded.'))
            except NotExercised as exc:
                screenshots[name] = dict(ok=False, error=str(exc))
        judged = dict(status='OPERATOR-JUDGED' if isinstance(answer, str) and answer.strip().upper() in ('YES', 'NO')
                      else 'NOT EXERCISED', answer=answer, screenshots=screenshots,
                      synthetic=session.get('synthetic', False))
    except (NotExercised, StoreNotExercised) as exc:
        judged = dict(status='NOT EXERCISED', detail=str(exc))
    summary = {limb: 'FAIL' if any(r['status'] == 'FAIL' for r in values) else
               'NOT EXERCISED' if not values or any(r['status'] != 'PASS' for r in values) else 'PASS'
               for limb, values in outcomes.items()}
    gate.last_results = summary
    for limb, values in outcomes.items():
        for item in values:
            say(f"{item['status']}: {limb} {LIMBS[limb]} [{item['run']}]: {item['detail']}")
    say('9 operator-judged: window appeared: ' + json.dumps(judged))
    measurement = measurements(gate, prep, diagnostics, say)
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
    bad = sum(v != 'PASS' for v in summary.values())
    gate.save('verify', dict(summary=summary, limbs=outcomes, operator_judged=judged,
                            measurement=measurement, cleanup=cleanup_note, computed_nonpass=bad))
    say(f'RESULT: {bad} computed FAIL/NOT EXERCISED limbs / {len(LIMBS)}; operator judgement counted separately.')
    say('CLEANUP: ' + str(cleanup_note))
    return bad + int(cleanup and isinstance(cleanup_note, str) and cleanup_note.startswith('Cleanup could not finish:'))


def measurements(gate, prep, diagnostics, say):
    say('MEASUREMENT (demo-machine data, not a claim about other rigs)')
    table = {'none': [], 'window': []}
    windows = []
    for row in prep.get('runs', []):
        if row['repetition'] >= 1:
            try:
                stats = e4.measurements(gate.out, row, diagnostics, 0)
            except Exception as exc:
                stats = dict(unavailable=str(exc))
            table[row['condition']].append(dict(run=row['id'], **stats))
            say(row['condition'] + ': ' + json.dumps(dict(run=row['id'], **stats)))
        if row['condition'] == 'window':
            folder = gate.out / 'runs' / row['id']
            try:
                record = load(folder / 'job-0.json')
                diagnostic = load(folder / 'windows-idle.json').get('diagnostics') or {}
                samples = lag_samples(record.get('stderr_tail'))
                evidence = dict(run=row['id'], startup_first_tick_s=samples[0] if samples else None,
                                scored=row['repetition'] >= 1,
                                during_acquisition=lag_stats(record.get('stderr_tail'), drop_first=True),
                                idle=lag_stats(diagnostic.get('stderr_tail')), priority=record.get('priority'))
            except Exception as exc:
                evidence = dict(run=row['id'], unavailable=str(exc))
            windows.append(evidence)
            say('window lag and supervisor priority (requested/at_start/at_end/processes_read/reason): ' + json.dumps(evidence))
    return dict(conditions=table, windows=windows)


def synthetic_session(gate, prep):
    """Scorer evidence ONLY: no browser, visibility, desktop capture or quit exercised."""
    rows = [r for r in prep['runs'] if r['condition'] == 'window']
    records = [load(gate.out / 'runs' / r['id'] / 'job-0.json') for r in rows[:2]]
    processes = load(gate.out / 'runs' / rows[0]['id'] / 'processes-open.json')
    empty = dict(exit_code=0, stderr='', processes=[], synthetic=True)
    for step, jobs, alive, listening in [(1, [], False, True), (2, records[:1], True, True),
                                        (3, records[:1], True, True), (4, records[:1], False, True),
                                        (5, records, True, True), (6, records, False, False)]:
        gate.save(f'session-step-{step}', dict(records=jobs, processes=processes if alive else empty,
                                             listening=listening, synthetic=True,
                                             screenshots={SCREENSHOTS[step - 2]: screenshot(
                                                 gate.out / SCREENSHOTS[step - 2], synthetic=True)}
                                             if step in (2, 3) else {}))
    gate.save('session', dict(appeared='YES', completed=[1, 2, 3, 4, 5, 6], synthetic=True,
                             note='Synthesized for scorer only; session cannot be driven without a human.'))


def selftest(output=None):
    output = Path(output).resolve() if output is not None else None
    if output is not None and output.exists() and any(output.iterdir()):
        raise SystemExit('Selftest output is not empty; refusing to overwrite evidence: ' + str(output))
    def persist(gate):
        if output is not None:
            shutil.copytree(gate.out, output, dirs_exist_ok=True)

    from unittest.mock import patch
    from microclaw import paths, controller, tools, completed_dataset, skill_supervisor
    failures = 0
    with tempfile.TemporaryDirectory(prefix='block84b-') as temporary:
        base = Path(temporary)
        env = {name: str(base / name) for name in ('XDG_DATA_HOME', 'LOCALAPPDATA', 'XDG_CONFIG_HOME', 'APPDATA')}
        env.update(UV_OFFLINE='1', UV_CACHE_DIR=str(base / 'uv-cache'))
        with patch.dict(os.environ, env):
            config = paths.default_safety_config()
            config.parent.mkdir(parents=True)
            config.write_text(e4.SAFETY_YAML, encoding='utf-8')
            mm_root = base / 'fake-mm'
            (mm_root / 'plugins').mkdir(parents=True)
            root = paths.user_data_dir()
            root.mkdir(parents=True)
            gate = Gate(root, base / 'evidence', fake=e4.OfflineInstall())
            operator_file = gate.store_root / 'operator-marker.txt'
            operator_file.parent.mkdir(parents=True)
            operator_file.write_text('operator store must survive\n', encoding='utf-8')
            gate.say('SELFTEST product under test: ' + str(Path(tools.__file__).resolve().parent))
            gate.say('SELFTEST real: offline install, execute_tool, bridge-shaped fakes, supervisor, '
                     'headless worker, OS process list, Close and store restoration.')
            gate.say('SELFTEST SYNTHETIC: desktop eligibility, Tk probe, human session snapshots/judgement/screenshots; '
                     'not Tk visibility or desktop/panel/quit evidence.')
            live_plan = plan('digest', base / 'datasets')
            expected = [('none', 'window') if rep % 2 == 0 else ('window', 'none') for rep in range(6)]
            ok = len(live_plan) == 12 and all(tuple(r['condition'] for r in live_plan if r['repetition'] == rep) == order
                                            for rep, order in enumerate(expected))
            ok = ok and all(r['input']['n_frames'] == 1000 and r['input']['interval_s'] == 0
                            and r['input']['exposure_ms'] == 10 for r in live_plan)
            failures += not ok
            gate.say(f'SELFTEST {"ok" if ok else "WRONG"}: fixed 12-run alternating 1000-frame plan.')
            gate.fake.listening = 'selftest listener'
            try:
                gate.prepare(sizes=(2, 20))
                refused = False
            except NotExercised:
                refused = operator_file.is_file() and not gate.saved_store.exists()
            gate.fake.listening = None
            failures += not refused
            gate.say(f'SELFTEST {"ok" if refused else "WRONG"}: listening port refuses prepare before store mutation.')
            with patch.object(controller, 'Core', e4.BridgeCore), patch.object(controller, 'Studio', e4.BridgeStudio), \
                    patch.object(controller.MicroscopeController, 'get_mm_app_dir', return_value=str(mm_root)), \
                    patch.object(tools, 'Acquisition', e4.FakeAcquisition), \
                    patch.object(skill_supervisor, 'interactive_desktop', return_value=True):
                try:
                    gate.prepare(sizes=(2, 20))
                    gate.measure()
                except Exception as exc:
                    gate.say(f'SELFTEST phase stopped: {type(exc).__name__}: {exc}')
                    completed_dataset.close_analysis_supervisor()
                    try:
                        verify(gate)
                    except Exception:
                        gate.say(traceback.format_exc())
                    gate.say('SELFTEST FAILED: prepare/measure did not complete.')
                    persist(gate)
                    return 1
            prep = gate.need('prepare')
            synthetic_session(gate, prep)
            real_before = base / 'real-evidence'
            shutil.copytree(gate.out, real_before)
            verify(gate, cleanup=True)
            # Physical clocks are observations only. Selftest never asserts elapsed timing.
            structural = all(gate.last_results[k] == 'PASS' for k in (2, 3, 4, 5))
            restored = operator_file.read_text(encoding='utf-8') == 'operator store must survive\n'
            failures += not (structural and restored)
            gate.say(f'SELFTEST {"ok" if structural and restored else "WRONG"}: real lifecycle limbs 2–5; '
                     f'operator store restored={restored}; observed limb 10={gate.last_results[10]} '
                     '(no wall-clock assertion).')
            gate.cleanup()
            # Partial cleanup after roots removal, with the durable owner marker still present.
            os.replace(gate.store_root, gate.saved_store)
            write_json(gate.store_root / '.84b-gate-owner.json', gate.need('ownership'))
            write_json(gate.store_root / 'trust' / 'roots.json', read_json(FIXTURES / 'trust' / 'roots-TEST-ONLY.json'))
            (gate.store_root / 'trust' / 'roots.json').unlink()
            (root / POINTER).write_text(str(gate.out), encoding='utf-8')
            gate.cleanup()
            gate.cleanup()
            # Partial restore: test store already gone, rename back still pending.
            os.replace(gate.store_root, gate.saved_store)
            gate.cleanup()
            restored = operator_file.read_text(encoding='utf-8') == 'operator store must survive\n'
            failures += not (restored and not gate.saved_store.exists() and not (root / POINTER).exists())
            gate.say(f'SELFTEST {"ok" if restored else "WRONG"}: interrupted cleanup/restore and idempotence.')
            # Missing roots after partial cleanup must still refuse a new prepare,
            # even when no operator store had originally needed a backup.
            marker = gate.store_root / '.84b-gate-owner.json'
            write_json(marker, gate.need('ownership'))
            try:
                with patch.object(gate, 'connect', side_effect=AssertionError('prepare bypassed ownership guard')):
                    try:
                        gate.prepare(sizes=(2, 20))
                        refused = False
                    except NotExercised:
                        refused = True
                    except AssertionError:
                        refused = False
            finally:
                marker.unlink()
            failures += not refused
            gate.say(f'SELFTEST {"ok" if refused else "WRONG"}: partial test store is never set aside as operator data.')
            # Fixed synthetic timing control. Only the scorer sees these fabricated clocks.
            clean = base / 'synthetic-scorer-control'
            shutil.copytree(real_before, clean)
            for row in prep['runs']:
                folder = clean / 'runs' / row['id']
                result = read_json(folder / 'result.json')
                result['duration_s'] = 1.0
                write_json(folder / 'result.json', result)
                if row['condition'] == 'window':
                    record = read_json(folder / 'job-0.json')
                    record['stderr_tail'] = 'event_loop_lag_s=0.001\n' * 3
                    write_json(folder / 'job-0.json', record)
            control = base / 'mutation'
            first = next(r for r in prep['runs'] if r['condition'] == 'window' and r['repetition'] >= 1)
            run = Path('runs') / first['id']
            def alter(folder, relative, fn):
                value = read_json(folder / relative)
                fn(value)
                write_json(folder / relative, value)
            mutations = [
                (1, 'failed Tk probe', 'NOT EXERCISED', {3, 4, 6, 7, 8, 10},
                 lambda f: alter(f, 'tkinter-probe.json', lambda v: v.update(exit_code=1, stderr='missing Tcl/Tk'))),
                (2, 'one frame missing', 'FAIL', set(),
                 lambda f: alter(f, run / 'dataset.json', lambda v: v.update(frames=19))),
                (3, 'job not final', 'FAIL', set(),
                 lambda f: alter(f, run / 'job-0.json', lambda v: v.update(state='running'))),
                (4, 'Close did not close', 'FAIL', set(),
                 lambda f: alter(f, run / 'close.json', lambda v: v.update(state='already_closed'))),
                (5, 'window consent laundered', 'FAIL', set(),
                 lambda f: alter(f, run / 'confirmations.json',
                                  lambda v: [p.update(subject=p['subject'].replace('+window', '')) for p in v if p['kind'] == 'analysis'])),
                (6, 'session job not final', 'FAIL', set(),
                 lambda f: alter(f, 'session-step-2.json', lambda v: v['records'][0].update(state='running'))),
                (7, 'MicroClaw was quit, not panel Close', 'FAIL', set(),
                 lambda f: alter(f, 'session-step-4.json', lambda v: v.update(listening=False))),
                (8, 'process survived quit', 'FAIL', set(),
                 lambda f: alter(f, 'session-step-6.json', lambda v: v['processes'].update(
                     processes=[dict(pid=123456, command='python -m fixture_worker.runner')]))),
                (10, 'cadence exceeds 1.10x', 'FAIL', set(),
                 lambda f: alter(f, run / 'result.json', lambda v: v.update(duration_s=1.11))),
                (10, 'lag p95 exceeds 0.100s but max is below 1.0s', 'FAIL', set(),
                 lambda f: alter(f, run / 'job-0.json', lambda v: v.update(stderr_tail='event_loop_lag_s=0.001\nevent_loop_lag_s=0.2\n'))),
                (10, 'lag max exceeds 1.0s', 'FAIL', set(),
                 lambda f: alter(f, run / 'job-0.json', lambda v: v.update(stderr_tail='event_loop_lag_s=0.001\nevent_loop_lag_s=1.5\n'))),
                (10, 'one window has no lag samples', 'NOT EXERCISED', set(),
                 lambda f: alter(f, run / 'job-0.json', lambda v: v.update(stderr_tail='no lag samples\n'))),
                (10, 'only startup sample remains', 'NOT EXERCISED', set(),
                 lambda f: alter(f, run / 'job-0.json', lambda v: v.update(stderr_tail='event_loop_lag_s=0.001\n'))),
                (10, 'one condition lacks a duration', 'NOT EXERCISED', set(),
                 lambda f: alter(f, Path('runs') / 'r1-none' / 'result.json', lambda v: v.pop('duration_s'))),
                (4, 'zero processes before Close (control)', 'NOT EXERCISED', {3},
                 lambda f: alter(f, run / 'processes-open.json', lambda v: v.update(processes=[]))),
            ]
            def reset():
                if control.exists():
                    shutil.rmtree(control)
                shutil.copytree(clean, control)
                return Gate(root, control, fake=e4.OfflineInstall())
            arm = reset()
            verify(arm, cleanup=False, echo=False)
            ok = all(v == 'PASS' for v in arm.last_results.values())
            failures += not ok
            gate.say(f'SELFTEST {"ok" if ok else "WRONG"}: synthetic scorer control {arm.last_results}')
            for limb, label, expected, collateral, mutate in mutations:
                arm = reset()
                mutate(control)
                verify(arm, cleanup=False, echo=False)
                other = {k: v for k, v in arm.last_results.items() if v != 'PASS' and k != limb and k not in collateral}
                ok = arm.last_results[limb] == expected and not other
                if limb == 1:
                    ok = ok and all(arm.last_results[k] == 'NOT EXERCISED' for k in collateral)
                failures += not ok
                gate.say(f'SELFTEST {"ok" if ok else "WRONG"}: limb {limb} {label} -> '
                         f'{arm.last_results[limb]}; other non-pass={other}; declared collateral={sorted(collateral)}')
            arm = reset()
            (control / 'window.png').unlink()
            alter(control, 'session-step-2.json', lambda v: v['screenshots']['window.png'].update(
                ok=False, error='Synthetic capture failure.'))
            verify(arm, cleanup=False, echo=False)
            judged = read_json(control / 'verify.json')['operator_judged']
            ok = judged['status'] == 'OPERATOR-JUDGED' and not judged['screenshots']['window.png']['ok'] and all(v == 'PASS' for v in arm.last_results.values())
            failures += not ok
            gate.say(f'SELFTEST {"ok" if ok else "WRONG"}: limb 9 failed screenshot -> OPERATOR-JUDGED with capture failure; '
                     'computed limbs unchanged.')
            arm = reset()
            alter(control, 'session.json', lambda v: v.pop('appeared'))
            verify(arm, cleanup=False, echo=False)
            ok = read_json(control / 'verify.json')['operator_judged']['status'] == 'NOT EXERCISED'
            failures += not ok
            gate.say(f'SELFTEST {"ok" if ok else "WRONG"}: limb 9 no answer -> NOT EXERCISED.')
            for label, relative, tail in [
                ('startup first tick excluded', run / 'job-0.json',
                 'event_loop_lag_s=1.5\n' + 'event_loop_lag_s=0.001\n' * 3),
                ('warm-up lag excluded', Path('runs/r0-window/job-0.json'),
                 'event_loop_lag_s=0.001\nevent_loop_lag_s=1.5\n')]:
                arm = reset()
                alter(control, relative, lambda v: v.update(stderr_tail=tail))
                verify(arm, cleanup=False, echo=False)
                ok = all(v == 'PASS' for v in arm.last_results.values())
                if label.startswith('startup'):
                    window = next(w for w in read_json(control / 'verify.json')['measurement']['windows']
                                  if w['run'] == first['id'])
                    ok = ok and window['startup_first_tick_s'] == 1.5 and window['during_acquisition']['max_s'] == .001
                failures += not ok
                gate.say(f'SELFTEST {"ok" if ok else "WRONG"}: limb 10 {label} -> PASS.')
            # A truncated tail is flagged but remains scoreable.
            arm = reset()
            alter(control, run / 'job-0.json', lambda v: v.update(stderr_tail='x' * (65536 - len('\nevent_loop_lag_s=0.001\nevent_loop_lag_s=0.001\n')) + '\nevent_loop_lag_s=0.001\nevent_loop_lag_s=0.001\n'))
            verify(arm, cleanup=False, echo=False)
            detail = read_json(control / 'verify.json')['limbs']['10'][0]['detail']
            ok = arm.last_results[10] == 'PASS' and 'truncated' in detail
            failures += not ok
            gate.say(f'SELFTEST {"ok" if ok else "WRONG"}: truncated stderr tail is scored and flagged.')
            arm = reset()
            verify(arm, cleanup=True, echo=False)
            ok = operator_file.is_file() and 'cleanup skipped' in str(read_json(control / 'verify.json')['cleanup'])
            failures += not ok
            gate.say(f'SELFTEST {"ok" if ok else "WRONG"}: transported evidence re-scores without touching local stores.')
            # PowerShell ConvertTo-Json yields no output, one object, or an array.
            # Responses are synthetic here; the Windows command itself needs the demo machine.
            from types import SimpleNamespace
            for stdout, count in [('', 0), ('null', 0),
                                  (json.dumps(dict(pid=123, command='python -m fixture_worker.runner')), 1),
                                  (json.dumps([dict(pid=123, command='python -m fixture_worker.runner'),
                                               dict(pid=124, command='python -m fixture_worker.runner')]), 2)]:
                captured = SimpleNamespace(returncode=0, stdout=stdout, stderr='')
                with patch.object(os, 'name', 'nt'), patch.object(subprocess, 'run', return_value=captured) as run_mock:
                    snap = process_snapshot()
                ok = process_count(snap) == count and run_mock.call_args.kwargs['stdin'] == subprocess.DEVNULL
                failures += not ok
            gate.say('SELFTEST Windows CIM JSON shapes checked with synthetic responses; Windows command not executed.')
            # Execute capture success/failure branches with synthetic PowerShell responses.
            capture_path = base / 'capture.png'
            def capture_ok(*args, **kwargs):
                capture_path.write_bytes(b'synthetic PNG response')
                return SimpleNamespace(returncode=0, stdout='', stderr='')
            capture_checks = []
            with patch.object(os, 'name', 'nt'), patch.object(subprocess, 'run', side_effect=capture_ok) as mocked:
                captured = screenshot(capture_path)
            command = mocked.call_args.args[0][-1]
            capture_checks.append(captured['ok'] and all(token in command for token in
                                  ('Add-Type', 'VirtualScreen', 'CopyFromScreen', 'ImageFormat]::Png'))
                                  and mocked.call_args.kwargs['stdin'] == subprocess.DEVNULL
                                  and mocked.call_args.kwargs['timeout'] == 30)
            for response in (SimpleNamespace(returncode=1, stdout='', stderr='Capture failed'),
                             subprocess.TimeoutExpired('powershell', 30), OSError('No PowerShell')):
                options = dict(side_effect=response) if isinstance(response, Exception) else dict(return_value=response)
                with patch.object(os, 'name', 'nt'), patch.object(subprocess, 'run', **options):
                    captured = screenshot(capture_path)
                capture_checks.append(not captured['ok'] and bool(captured['error']))
            ok = all(capture_checks)
            failures += not ok
            gate.say(f'SELFTEST {"ok" if ok else "WRONG"}: Windows screenshot success/error/timeout '
                     'with synthetic responses; actual desktop capture not exercised.')
            launcher = (HERE / '84-block84b-demo-gate.ps1').read_text(encoding='utf-8')
            ok = ('84-block84b-demo-gate.py' in launcher and '/api/skill-packages/windows' in launcher
                  and 'already_closed' in launcher and "'prepare','measure','session','verify','cleanup','selftest'" in launcher)
            failures += not ok
            gate.say(f'SELFTEST {"ok" if ok else "WRONG"}: launcher phase/path and installed-feature probe wiring '
                     '(source check; PowerShell not executed).')
            gate.save('selftest', dict(failed_controls=failures, synthesized=['desktop eligibility', 'Tk availability', 'human session', 'timing scorer control', 'Windows CIM responses', 'Windows screenshot responses']))
            gate.say(f'SELFTEST {"PASSED" if not failures else f"FAILED ({failures})"}; no wall-clock assertions.')
            persist(gate)
            return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('phase', choices=('prepare', 'measure', 'session', 'verify', 'cleanup', 'selftest'))
    parser.add_argument('--out', type=Path)
    parser.add_argument('--port', type=int, default=4827)
    args = parser.parse_args()
    if args.phase == 'selftest':
        return int(bool(selftest(args.out)))
    if args.out is None:
        parser.error('--out is required')
    from microclaw import paths
    gate = Gate(paths.user_data_dir(), args.out)
    try:
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
    except (NotExercised, StoreNotExercised, SystemExit) as exc:
        gate.say(f'NOT EXERCISED: {args.phase}: {exc}')
        gate.save(args.phase + '-error', dict(error=str(exc), traceback=traceback.format_exc()))
        return 2
    except Exception as exc:
        gate.say(f'FAILED: {args.phase}: {type(exc).__name__}: {exc}\n{traceback.format_exc()}')
        gate.save(args.phase + '-error', dict(error=str(exc), traceback=traceback.format_exc()))
        return 1


if __name__ == '__main__':
    sys.exit(main())
