"""88a demo gate: a supervised, non-blocking run_mda on Micro-Manager's own engine.

Phases (the launcher picks the installed slot's interpreter):

- measure  MicroClaw closed. Drives the real `tools.execute_tool("run_mda", ...)`
           and `run_timelapse` on the live bridge. V1-V3 on three completed MDA
           bursts and three run_timelapse bursts; V4 on three MDA runs aborted through
           the session cancel event (what the web UI's Stop sets) and one through a
           real KeyboardInterrupt in the main thread (what Ctrl-C raises).
- session  Firefox. Literal chat lines: one run_mda whose dialog update the
           operator judges against a screenshot, export_session_script, and one long
           run_mda stopped with MicroClaw's Stop button.
- script   MicroClaw closed. Runs the exported script with this interpreter and
           checks the dataset it writes.
- verify   Scores only files in the evidence folder; re-scores off-rig.

Pass/fail limbs are what 88a owns. Timings that describe Micro-Manager's engine
(V3 interval spread, V4 abort latency, V2 save-path timing, growing reads) are
MEASUREMENT lines with no bound (operator decision, 2026-10-16).

`selftest` runs measure -> session -> script -> verify against bridge-shaped fakes
written from the 2.0.3 jars (tests/mda_fakes.py plus a real ndstorage writer at
512 x 512), then mutates one artifact per limb. Run it on the pre-block tree too:
it must not pass there.
"""
from __future__ import annotations

import _thread
import argparse
import ast
from copy import deepcopy
from datetime import datetime
import importlib.util
import json
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import traceback

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('gate88_base84', HERE / '84-block84b-demo-gate.py')
b84 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(b84)
e4 = b84.e4
read_json, write_json, now = e4.read_json, e4.write_json, e4.now
NotExercised = e4.NotExercised

POINTER = '88a-gate-evidence.txt'
PORT = 4827
# reps, burst frames, abort-run planned frames, abort at frames, session frames, stop-run frames
SIZES = (3, 1000, 2000, 200, 1500, 6000)
SELFTEST_SIZES = (2, 40, 400, 30, 40, 400)
EXPOSURE_MS = 10
SESSION_NAME, STOP_NAME, SCRIPT_NAME = 'gate88a-dialog', 'gate88a-stop', 'gate88a-export.py'
LIFECYCLE = ('acquisition_construction', 'acquisition_finished', 'writer_finished',
             'acquisition_teardown_completion')
LIMBS = {
    1: 'V1 start returns a datastore while the engine is still acquiring',
    2: 'E1 settings that ran equal the settings requested, nothing else enabled',
    3: 'V2 completed MDA: all frames, readable, index = image count, writer proved',
    4: 'V4 abort via the Stop event: cancelled, stopped early, readable, writer proved',
    5: 'V4 abort via Ctrl-C: same, and the interrupt did not escape the tool',
    6: 'engine idle after every run, exposure left at the requested value',
    7: 'D4 lifecycle records in the diagnostics file, in order, outcome matching',
    8: 'duration_breakdown present, accounted, residual named',
    'S1': 'session run_mda from chat completed (history)',
    'S2': 'operator: MDA window shows the requested Count and Name prefix',
    'S3': 'session Stop button: run_mda cancelled, writer proved (history)',
    'S4': 'operator: no window asked to confirm the abort',
    'X1': 'exported script: parses, emits the settings run, imports nothing from microclaw',
    'X2': 'exported script runs: exit 0, new dataset with every planned frame',
}


# --- plan -------------------------------------------------------------------------------------
def plan(directory, sizes=SIZES):
    reps, burst, abort_frames, abort_at = sizes[:4]
    rows = []
    def row(kind, rep, tool, frames, abort=None):
        name = f'r{rep}-{kind}'
        args = dict(n_frames=frames, interval_s=0, exposure_ms=EXPOSURE_MS, name=name,
                    save_dir=str(directory))
        if tool == 'run_mda':
            args = dict(settings=dict(args, save_mode='ND_TIFF'))
        return dict(id=name, kind=kind, rep=rep, tool=tool, input=args, abort=abort,
                    abort_at=abort_at if abort else None, frames=frames)
    for rep in range(reps):
        pair = [row('mda', rep, 'run_mda', burst), row('timelapse', rep, 'run_timelapse', burst)]
        rows.extend(pair if rep % 2 == 0 else pair[::-1])
    for rep in range(reps):
        rows.append(row('abort-stop', rep, 'run_mda', abort_frames, 'stop'))
    rows.append(row('abort-ctrlc', 0, 'run_mda', abort_frames, 'ctrlc'))
    return rows


def session_inputs(prep):
    base = dict(interval_s=0, exposure_ms=EXPOSURE_MS, save_dir=prep['session_dir'], save_mode='ND_TIFF')
    return dict(
        dialog={'settings': dict(base, n_frames=prep['sizes'][4], name=SESSION_NAME)},
        export={'output_path': str(Path(prep['session_dir']) / SCRIPT_NAME)},
        stop={'settings': dict(base, n_frames=prep['sizes'][5], name=STOP_NAME)})


def chat(tool, arguments):
    return (f'Call {tool} exactly once with exactly these arguments: {json.dumps(arguments)}. '
            'Do not change, add or drop any value, or substitute another tool. Call no other tool before it.')


def recording_confirm(prompts, on_write):
    """Approve acquisition prompts only; record every prompt verbatim."""
    def confirm(summary, kind='action', subject=None, **_):
        approved = kind == 'acquisition'
        prompts.append(dict(summary=summary, kind=kind, subject=subject, approved=approved,
                            timestamp=now().isoformat()))
        on_write(prompts)
        return approved
    return confirm


def dataset_facts(path):
    """Frame count, axes and the first image's metadata keys, read with ndstorage."""
    from ndstorage import Dataset
    reader = Dataset(str(path))
    try:
        coords = reader.get_image_coordinates_list()
        first = dict(coords[0]) if coords else {}
        keys = sorted(reader.read_metadata(**first).keys()) if coords else []
        return dict(frames=len(coords), index_entries=len(reader.index), first_coords=first,
                    axes=sorted({k for c in coords for k in c}), first_metadata_keys=keys)
    finally:
        reader.close()


class GrowingReader:
    """Opens the dataset with ndstorage while it grows (V2). Filesystem only, never the bridge."""
    def __init__(self, events, period_s=0.5):
        self.events, self.period_s, self.reads = events, period_s, []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def path(self):
        for event in list(self.events):
            if event.get('dataset_path'):
                return event['dataset_path']
        return None

    def run(self):
        from ndstorage import Dataset
        start = time.perf_counter()
        while not self.stop.wait(self.period_s):
            path = self.path()
            if not path:
                continue
            item = dict(t_s=round(time.perf_counter() - start, 3))
            try:
                reader = Dataset(str(path))
                try:
                    item['index_entries'] = len(reader.index)
                finally:
                    reader.close()
            except Exception as exc:
                item['error'] = f'{type(exc).__name__}: {exc}'[:300]
            self.reads.append(item)


# --- the gate ---------------------------------------------------------------------------------
class Gate:
    def __init__(self, root, out, *, fake=None):
        self.root, self.out, self.fake = Path(root), Path(out), fake
        self.out.mkdir(parents=True, exist_ok=True)
        self.last_results = {}

    def say(self, text):
        line = f"{datetime.now().isoformat(timespec='seconds')} {text}"
        print(line, flush=True)
        with (self.out / 'gate.log').open('a', encoding='utf-8') as log:
            log.write(line + '\n')

    def save(self, name, data):
        write_json(self.out / f'{name}.json', data)

    def need(self, name):
        path = self.out / f'{name}.json'
        if not path.exists():
            raise NotExercised(f'phase {name} was not captured')
        return read_json(path)

    def ask(self, prompt, key, allowed=None):
        if self.fake:
            return self.fake.answer(key)
        while True:
            self.say('OPERATOR: ' + prompt)
            answer = input('> ')
            self.say(f'ANSWER[{key}]: {answer!r}')
            if answer.strip().upper() == 'STOP':
                raise NotExercised('operator stopped the gate')
            if allowed is None or answer.strip().upper() in allowed:
                return answer

    def close_microclaw(self, why):
        who = self.fake.serve_listening() if self.fake else e4.serve_listening()
        if who:
            raise NotExercised(f'{why} {who} is listening on port {e4.SERVE_PORT}: close MicroClaw\'s '
                               'console and launcher windows (leave Micro-Manager open) and rerun.')

    def connect(self, port):
        return e4.Gate.connect(None, port)

    # measure ---------------------------------------------------------------------------------
    def measure(self, *, port=PORT, sizes=SIZES):
        from microclaw import tools
        from microclaw.conversation import AcquisitionDiagnosticWriter, AuditLog
        from microclaw.tools_schema import TOOLS
        mda = next(t for t in TOOLS if t['name'] == 'run_mda')
        if 'settings' not in mda['input_schema'].get('properties', {}):
            raise NotExercised('installed product has no run_mda settings route; run step 0 (install.bat)')
        if (self.out / 'measure.json').exists():
            raise NotExercised('measure evidence already exists here; refusing to overwrite')
        self.close_microclaw('Measure drives acquisitions without MicroClaw running.')
        parsed, guard, ctrl = self.connect(port)
        base = Path(guard.resolve_in_workspace('.') if parsed.constraints.workspace_dir else self.root).resolve()
        directory = base / ('block88a-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
        rows = plan(directory, sizes)
        extra = [dict(input=dict(n_frames=n)) for n in (sizes[4], sizes[4], sizes[5])]
        flat = [dict(input=dict(n_frames=r['frames'])) for r in rows] + extra
        disk = e4.disk_check(ctrl.core, base, flat)
        self.say(f"Disk: free {disk['free_bytes']} bytes; projected need {disk['projected_bytes']} bytes.")
        prep = dict(port=port, sizes=list(sizes), directory=str(directory),
                    session_dir=str(directory / 'session'), runs=rows, disk=disk,
                    started_at=now().isoformat(), product=str(Path(tools.__file__).parent))
        self.save('measure', prep)
        writer = AcquisitionDiagnosticWriter(AuditLog(self.out / 'measure_microclaw_acquisitions.jsonl',
                                                      enabled=True, secrets=()))
        completed = 0
        try:
            for row in rows:
                result = self.run_one(row, self.out / 'runs' / row['id'], ctrl, guard, writer)
                completed += 1
                if result.get('acquisition') == 'refused' or result.get('outcome') == 'unterminated':
                    self.say(f"STOPPING: {row['id']} left the engine unterminated or was refused; "
                             'later runs would be refused too.')
                    break
        finally:
            writer.close()
            self.save('measure-end', dict(completed=completed, planned=len(rows), ended_at=now().isoformat()))
        self.say('RECORDED: measure')

    def run_one(self, row, folder, ctrl, guard, writer):
        from microclaw import tools
        folder.mkdir(parents=True, exist_ok=False)
        write_json(folder / 'input.json', row)
        prompts, events, receipts = [], [], []
        trigger = dict(fired_perf=None, frames=None)
        cancel = threading.Event()
        start = time.perf_counter()

        def sink(event):
            events.append(deepcopy(event))
            receipts.append(round(time.perf_counter() - start, 4))
            if (row['abort'] and trigger['fired_perf'] is None and event.get('type') == 'acquisition_progress'
                    and (event.get('frames_accounted') or 0) >= row['abort_at']):
                trigger.update(fired_perf=time.perf_counter(), frames=event['frames_accounted'])
                if row['abort'] == 'stop':
                    cancel.set()
                else:
                    _thread.interrupt_main()

        manager = ctrl.studio.acquisitions()
        pre = dict(exposure_ms=float(ctrl.core.get_exposure()), running=bool(manager.is_acquisition_running()))
        reader = GrowingReader(events) if row['abort'] else None
        if reader:
            reader.thread.start()
        previous = tools.CONFIRM_FN
        tools.CONFIRM_FN = recording_confirm(prompts, lambda p: write_json(folder / 'confirmations.json', p))
        clock = dict(clock='time.perf_counter, relative to the gate calling execute_tool')
        escaped = None
        self.say('RUN ' + row['id'])
        try:
            try:
                raw = tools.execute_tool(row['tool'], deepcopy(row['input']), ctrl, guard, cancel=cancel,
                                         acquisition_event_sink=sink, acquisition_diagnostic_writer=writer,
                                         acquisition_session_id='gate88a-measure', tool_call_id=row['id'])
                result = json.loads(raw)
            except KeyboardInterrupt:
                escaped = 'KeyboardInterrupt escaped execute_tool'
                result = dict(error=escaped)
            clock['tool_return_s'] = round(time.perf_counter() - start, 4)
            # A Ctrl-C delivered after the tool returned is late, not escaped; absorb it.
            try:
                time.sleep(0.3)
            except KeyboardInterrupt:
                clock['interrupt_after_return'] = True
        finally:
            tools.CONFIRM_FN = previous
            if reader:
                reader.stop.set()
                reader.thread.join(5)
        if trigger['fired_perf'] is not None:
            clock['abort_requested_s'] = round(trigger['fired_perf'] - start, 4)
            clock['abort_requested_at_frames'] = trigger['frames']
        write_json(folder / 'result.json', result)
        post = dict(exposure_ms=float(ctrl.core.get_exposure()), running=bool(manager.is_acquisition_running()))
        if row['tool'] == 'run_mda':
            try:
                post['engine_settings'] = tools._read_mda_settings(manager.get_acquisition_settings())
            except Exception as exc:
                post['engine_settings_error'] = f'{type(exc).__name__}: {exc}'
        write_json(folder / 'state.json', dict(pre=pre, post=post, escaped=escaped))
        write_json(folder / 'events.json', dict(events=events, receipt_s=receipts))
        write_json(folder / 'clock.json', clock)
        write_json(folder / 'confirmations.json', prompts)
        if reader:
            write_json(folder / 'growing-reads.json', reader.reads)
        evidence = {}
        path = result.get('dataset_path')
        if path:
            try:
                evidence.update(dataset_facts(path))
            except Exception as exc:
                evidence['read_error'] = f'{type(exc).__name__}: {exc}'
            try:
                evidence['spacing'] = e4.dataset_evidence(dict(dataset_path=path))
            except Exception as exc:
                evidence['spacing_error'] = f'{type(exc).__name__}: {exc}'
        write_json(folder / 'dataset.json', evidence)
        self.say(f"  {row['id']}: outcome={result.get('outcome', result.get('status'))} "
                 f"frames={evidence.get('frames')} writer={(result.get('writer_completion') or {}).get('state')}")
        return result

    # session ---------------------------------------------------------------------------------
    def capture_history(self, started):
        target = self.out / 'session-history'
        target.mkdir(exist_ok=True)
        for path in self.root.glob('*_microclaw_history.jsonl'):
            if datetime.fromtimestamp(path.stat().st_mtime, started.tzinfo) >= started:
                shutil.copy2(path, target / path.name)

    def session(self):
        prep = self.need('measure')
        if (self.out / 'session.json').exists():
            raise NotExercised('session evidence already exists; refusing to overwrite')
        if self.fake:
            return self.fake.session(self, prep)
        self.close_microclaw('Begin the session with MicroClaw closed.')
        Path(prep['session_dir']).mkdir(parents=True, exist_ok=True)
        started = now()
        inputs = session_inputs(prep)
        data = dict(started_at=started.isoformat(), inputs=inputs, answers={}, synthetic=False)
        self.save('session', data)
        self.say("STEP 1: In Micro-Manager choose Tools > Multi-Dimensional Acquisition... and leave the "
                 'Multi-Dimensional Acquisition window open where you can see it. Then launch MicroClaw '
                 'from its desktop icon and wait for Firefox. Put Firefox, the Multi-Dimensional '
                 'Acquisition window and this PowerShell window side by side, all visible.')
        data['answers']['1'] = self.ask('Type DONE when all three are visible.', '1', {'DONE'})
        self.say('STEP 2: Paste this literal line into MicroClaw and approve the acquisition. Type DONE '
                 'while the frames are still being acquired, before MicroClaw replies:\n'
                 + chat('run_mda', inputs['dialog']))
        data['answers']['2'] = self.ask('Type DONE while frames are being acquired.', '2', {'DONE'})
        data['dialog_screenshot'] = b84.screenshot(self.out / 'dialog.png')
        self.save('session', data)
        data['dialog_answer'] = self.ask(
            "Look at Micro-Manager's Multi-Dimensional Acquisition window. In the 'Time Points' area, "
            f"does 'Count' read {prep['sizes'][4]}? In the 'Save Images' area, does 'Name prefix' read "
            f"{SESSION_NAME}? Type YES only if both do, otherwise NO.", 'dialog', {'YES', 'NO'})
        self.save('session', data)
        data['answers']['2b'] = self.ask("Wait for MicroClaw's reply, then type DONE.", '2b', {'DONE'})
        self.say('STEP 3: Paste this literal line and wait for the reply:\n' + chat('export_session_script', inputs['export']))
        data['answers']['3'] = self.ask('Type DONE after the reply.', '3', {'DONE'})
        self.capture_history(started)
        self.say('STEP 4: Paste this literal line and approve. When the frame count is moving, click '
                 "MicroClaw's 'Stop after the current step' button, then immediately type DONE here:\n"
                 + chat('run_mda', inputs['stop']))
        data['answers']['4'] = self.ask('Type DONE right after clicking Stop.', '4', {'DONE'})
        time.sleep(3)
        data['stop_screenshot'] = b84.screenshot(self.out / 'stop.png')
        self.save('session', data)
        data['abort_window_answer'] = self.ask(
            'Since you clicked Stop, has Micro-Manager opened any window asking you to confirm stopping '
            'or aborting the acquisition? Type NO if no such window appeared, YES if one did.',
            'abort-window', {'YES', 'NO'})
        self.save('session', data)
        data['answers']['4b'] = self.ask("Wait for MicroClaw's reply, then type DONE.", '4b', {'DONE'})
        self.capture_history(started)
        self.say("STEP 5: Quit MicroClaw by closing its console and launcher windows. Leave Micro-Manager open.")
        data['answers']['5'] = self.ask('Type DONE after quitting.', '5', {'DONE'})
        self.capture_history(started)
        self.save('session', data)
        self.say('RECORDED: session')

    # script ----------------------------------------------------------------------------------
    def script(self):
        prep, session = self.need('measure'), self.need('session')
        if (self.out / 'script.json').exists():
            raise NotExercised('script evidence already exists; refusing to overwrite')
        self.close_microclaw('The exported script must run without MicroClaw.')
        source = Path(session['inputs']['export']['output_path'])
        if not source.is_file():
            raise NotExercised(f'no exported script at {source}; step 3 of the session did not write it')
        shutil.copy2(source, self.out / SCRIPT_NAME)
        session_dir = Path(prep['session_dir'])
        before = sorted(p.name for p in session_dir.iterdir())
        data = dict(script=str(source), before=before, started_at=now().isoformat())
        self.say(f'RUN exported script with {sys.executable}')
        if self.fake:
            code, stdout, stderr = self.fake.run_script(source)
        else:
            proc = subprocess.run([sys.executable, str(source)], cwd=str(source.parent), stdin=subprocess.DEVNULL,
                                  capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=1800)
            code, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
        (self.out / 'script-stdout.txt').write_text(stdout, encoding='utf-8')
        (self.out / 'script-stderr.txt').write_text(stderr, encoding='utf-8')
        after = sorted(p.name for p in session_dir.iterdir())
        new = [n for n in after if n not in before and n.startswith(SESSION_NAME) and (session_dir / n).is_dir()]
        data.update(exit_code=code, after=after, new_datasets=new, ended_at=now().isoformat())
        if len(new) == 1:
            try:
                data['dataset'] = dataset_facts(session_dir / new[0])
            except Exception as exc:
                data['dataset_error'] = f'{type(exc).__name__}: {exc}'
        self.save('script', data)
        self.say(f'RECORDED: script (exit {code}, new datasets {new})')


# --- scoring ----------------------------------------------------------------------------------
def require(condition, detail):
    if not condition:
        raise AssertionError(detail)
    return True


def load(path):
    path = Path(path)
    if not path.exists():
        raise NotExercised(f'missing {path.parent.name}/{path.name}')
    return read_json(path)


def rows_of(out, kind):
    prep = load(Path(out) / 'measure.json')
    rows = [r for r in prep['runs'] if r['kind'] == kind]
    present = [r for r in rows if (Path(out) / 'runs' / r['id'] / 'result.json').exists()]
    if not present:
        raise NotExercised(f'no {kind} run was captured')
    return prep, present


def run_files(out, row):
    folder = Path(out) / 'runs' / row['id']
    return (load(folder / 'result.json'), load(folder / 'dataset.json'), load(folder / 'state.json'),
            load(folder / 'events.json'), load(folder / 'clock.json'))


def lifecycle_types(diagnostics, call):
    return [d.get('type') for d in diagnostics if d.get('tool_call_id') == call and d.get('type') in LIFECYCLE]


def check_stopped(out, row, *, ctrlc=False):
    result, ds, state, _, clock = run_files(out, row)
    if clock.get('abort_requested_s') is None:
        raise NotExercised(f"{row['id']}: the abort was never requested (the run ended before frame {row['abort_at']})")
    if ctrlc and clock.get('interrupt_after_return'):
        raise NotExercised(f"{row['id']}: the interrupt arrived after the tool returned")
    require(not state.get('escaped'), f"{row['id']}: {state.get('escaped')}")
    require(result.get('outcome') == 'cancelled', f"{row['id']}: outcome {result.get('outcome')!r}, not cancelled")
    require((result.get('writer_completion') or {}).get('state') == 'proved',
            f"{row['id']}: writer {result.get('writer_completion')}")
    frames = ds.get('frames')
    require(isinstance(frames, int) and 0 < frames < row['frames'],
            f"{row['id']}: {frames} frames saved of {row['frames']} planned: not stopped early")
    require(frames == ds.get('index_entries') == (result.get('datastore') or {}).get('image_count'),
            f"{row['id']}: ndstorage {frames}, index {ds.get('index_entries')}, datastore "
            f"{(result.get('datastore') or {}).get('image_count')}")
    return True


def recorded_calls(out, session):
    return e4.e3.recorded_calls(Path(out) / 'session-history', datetime.fromisoformat(session['started_at']))


def session_call(out, name, arguments):
    session = load(Path(out) / 'session.json')
    found = [c for c in recorded_calls(out, session) if c['name'] == name and c['input'] == arguments]
    if not found:
        raise NotExercised(f'no {name} call with the literal arguments in the captured history')
    require(len(found) == 1, f'{name} was called {len(found)} times with the literal arguments')
    return session, found[0]


def measurements(out):
    """Report-only: Micro-Manager's engine, not 88a's code. Never pass/fail."""
    prep = load(Path(out) / 'measure.json')
    report = {}
    for row in prep['runs']:
        folder = Path(out) / 'runs' / row['id']
        if not (folder / 'result.json').exists():
            continue
        result, ds = read_json(folder / 'result.json'), read_json(folder / 'dataset.json')
        events, clock = read_json(folder / 'events.json'), read_json(folder / 'clock.json')
        item = dict(kind=row['kind'], frames=ds.get('frames'), axes=ds.get('axes'),
                    timestamp_key=(ds.get('spacing') or {}).get('frame_timestamp_metadata_key'))
        spacing = (ds.get('spacing') or {}).get('observed_per_field_spacing') or {}
        if isinstance(spacing, dict) and len(spacing) == 1:
            gaps = sorted(next(iter(spacing.values())))
            if gaps:
                item['gap_s'] = dict(p50=statistics.median(gaps), p95=e4.p95(gaps), max=gaps[-1], n=len(gaps))
        else:
            item['gap_s'] = f"unavailable: {(ds.get('spacing') or {}).get('verification') or ds.get('spacing_error')}"
        breakdown = result.get('duration_breakdown') or {}
        phases = breakdown.get('phases') or {}
        item['phases_s'] = {k: round(v.get('total_s', 0), 3) for k, v in phases.items()}
        first_path = next((t for e, t in zip(events['events'], events['receipt_s']) if e.get('dataset_path')), None)
        item['save_path_first_seen_s'] = first_path
        if clock.get('abort_requested_s') is not None:
            item['abort_requested_at_frames'] = clock['abort_requested_at_frames']
            item['abort_to_tool_return_s'] = round(clock['tool_return_s'] - clock['abort_requested_s'], 3)
        reads = folder / 'growing-reads.json'
        if reads.exists():
            r = read_json(reads)
            ok = [x['index_entries'] for x in r if 'index_entries' in x]
            item['growing_reads'] = dict(reads=len(r), ok=len(ok), errors=len(r) - len(ok),
                                         distinct_counts=len(set(ok)), max_seen=max(ok) if ok else None)
        report[row['id']] = item
    return report


def verify(gate, *, echo=True):
    out = gate.out
    results, details = {}, {}

    def check(limb, fn):
        try:
            fn()
            results[limb], details[limb] = 'PASS', ''
        except NotExercised as exc:
            results[limb], details[limb] = 'NOT EXERCISED', str(exc)
        except AssertionError as exc:
            results[limb], details[limb] = 'FAIL', str(exc)
        except Exception as exc:
            results[limb], details[limb] = 'FAIL', f'{type(exc).__name__}: {exc}'
        if echo:
            gate.say(f"LIMB {limb} {results[limb]}: {LIMBS[limb]}" + (f' -- {details[limb]}' if details[limb] else ''))

    def limb1():
        _, rows = rows_of(out, 'mda')
        for row in rows:
            result, _, _, events, _ = run_files(out, row)
            progress = [e for e in events['events'] if e.get('type') == 'acquisition_progress']
            require(result.get('dataset_path'), f"{row['id']}: no datastore path in the result")
            require(progress, f"{row['id']}: no progress event, so no poll was observed")
            first = progress[0]
            require(first.get('phase') == 'acquiring' and (first.get('frames_accounted') or 0) < row['frames'],
                    f"{row['id']}: first poll saw phase {first.get('phase')!r} at "
                    f"{first.get('frames_accounted')} of {row['frames']} frames: the start blocked")
    check(1, limb1)

    def limb2():
        _, rows = rows_of(out, 'mda')
        for row in rows:
            result, _, state, _, _ = run_files(out, row)
            want = row['input']['settings']
            for source, ran in (('settings_that_ran', result.get('settings_that_ran') or {}),
                                ('engine current settings', state['post'].get('engine_settings') or {})):
                require(ran, f"{row['id']}: {source} missing ({state['post'].get('engine_settings_error')})")
                expected = dict(num_frames=want['n_frames'], interval_ms=0, save_mode='ND_TIFF',
                                prefix=want['name'], use_frames=True, save=True, use_slices=False,
                                use_channels=False, use_position_list=False, use_autofocus=False,
                                use_custom_intervals=False)
                wrong = {k: ran.get(k) for k, v in expected.items() if ran.get(k) != v}
                require(not wrong, f"{row['id']}: {source} differ: {wrong}")
                require(os.path.normcase(os.path.normpath(str(ran.get('root')))) ==
                        os.path.normcase(os.path.normpath(want['save_dir'])),
                        f"{row['id']}: {source} root {ran.get('root')!r}")
    check(2, limb2)

    def limb3():
        _, rows = rows_of(out, 'mda')
        for row in rows:
            result, ds, _, _, _ = run_files(out, row)
            require(result.get('outcome') == 'completed', f"{row['id']}: outcome {result.get('outcome')!r}")
            require((result.get('writer_completion') or {}).get('state') == 'proved',
                    f"{row['id']}: writer {result.get('writer_completion')}")
            path = Path(result['dataset_path'])
            require(path.name.startswith(row['id']) and os.path.normcase(str(path.parent)) ==
                    os.path.normcase(row['input']['settings']['save_dir']),
                    f"{row['id']}: dataset at {path}, not a {row['id']}* folder in save_dir")
            require(ds.get('frames') == ds.get('index_entries') == row['frames'] ==
                    (result.get('datastore') or {}).get('image_count'),
                    f"{row['id']}: ndstorage {ds.get('frames')}, index {ds.get('index_entries')}, datastore "
                    f"{(result.get('datastore') or {}).get('image_count')}, planned {row['frames']}")
    check(3, limb3)

    def limb4():
        _, rows = rows_of(out, 'abort-stop')
        for row in rows:
            check_stopped(out, row)
    check(4, limb4)

    def limb5():
        _, rows = rows_of(out, 'abort-ctrlc')
        for row in rows:
            check_stopped(out, row, ctrlc=True)
    check(5, limb5)

    def limb6():
        prep = load(out / 'measure.json')
        present = [r for r in prep['runs'] if (out / 'runs' / r['id'] / 'state.json').exists()]
        require(present, 'no run state captured')
        for row in present:
            state = read_json(out / 'runs' / row['id'] / 'state.json')
            require(state['post']['running'] is False, f"{row['id']}: engine still running after the tool returned")
            if row['tool'] == 'run_mda':
                require(abs(state['post']['exposure_ms'] - EXPOSURE_MS) < 1e-6,
                        f"{row['id']}: exposure {state['post']['exposure_ms']} after the run")
    check(6, limb6)

    def limb7():
        prep = load(out / 'measure.json')
        diagnostics = e4._diagnostics(out)
        mda = [r for r in prep['runs'] if r['tool'] == 'run_mda' and (out / 'runs' / r['id'] / 'result.json').exists()]
        require(mda, 'no MDA run captured')
        for row in mda:
            types = lifecycle_types(diagnostics, row['id'])
            require(types == list(LIFECYCLE), f"{row['id']}: lifecycle records {types}")
            finished = [d for d in diagnostics if d.get('tool_call_id') == row['id']
                        and d.get('type') == 'acquisition_finished']
            result = read_json(out / 'runs' / row['id'] / 'result.json')
            require(finished[0].get('outcome') == result.get('outcome'),
                    f"{row['id']}: record says {finished[0].get('outcome')!r}, result {result.get('outcome')!r}")
    check(7, limb7)

    def limb8():
        prep = load(out / 'measure.json')
        mda = [r for r in prep['runs'] if r['tool'] == 'run_mda' and (out / 'runs' / r['id'] / 'result.json').exists()]
        require(mda, 'no MDA run captured')
        for row in mda:
            b = read_json(out / 'runs' / row['id'] / 'result.json').get('duration_breakdown') or {}
            phases = b.get('phases') or {}
            require({'start_call', 'acquiring', 'writer_finalization'} <= set(phases),
                    f"{row['id']}: phases {sorted(phases)}")
            require(b.get('accounted_s', -1) >= 0 and b['accounted_s'] <= b.get('duration_s', -1) + 1e-6
                    and b.get('phase_meaning'), f"{row['id']}: breakdown {b}")
    check(8, limb8)

    def s1():
        prep = load(out / 'measure.json')
        _, call = session_call(out, 'run_mda', session_inputs(prep)['dialog'])
        result = call['result']
        require(result.get('outcome') == 'completed' and (result.get('writer_completion') or {}).get('state') == 'proved',
                f"session run: outcome {result.get('outcome')!r}, writer {result.get('writer_completion')}")
    check('S1', s1)

    def s2():
        session = load(out / 'session.json')
        shot = session.get('dialog_screenshot') or {}
        if 'dialog_answer' not in session:
            raise NotExercised('the dialog question was not answered')
        require(shot.get('ok'), f"dialog.png not captured: {shot.get('error')}")
        require(session['dialog_answer'].strip().upper() == 'YES', f"operator answered {session['dialog_answer']!r}")
    check('S2', s2)

    def s3():
        prep = load(out / 'measure.json')
        _, call = session_call(out, 'run_mda', session_inputs(prep)['stop'])
        result = call['result']
        require(result.get('outcome') == 'cancelled', f"Stop run outcome {result.get('outcome')!r}")
        require((result.get('writer_completion') or {}).get('state') == 'proved',
                f"Stop run writer {result.get('writer_completion')}")
        require(0 < (result.get('datastore') or {}).get('image_count', 0) < prep['sizes'][5],
                f"Stop run saved {(result.get('datastore') or {}).get('image_count')} of {prep['sizes'][5]}")
    check('S3', s3)

    def s4():
        session = load(out / 'session.json')
        if 'abort_window_answer' not in session:
            raise NotExercised('the abort-window question was not answered')
        require((session.get('stop_screenshot') or {}).get('ok'), 'stop.png not captured')
        require(session['abort_window_answer'].strip().upper() == 'NO',
                f"operator answered {session['abort_window_answer']!r}")
    check('S4', s4)

    def x1():
        source = (out / SCRIPT_NAME)
        if not source.exists():
            raise NotExercised(f'{SCRIPT_NAME} was not captured (run -Phase script)')
        text = source.read_text(encoding='utf-8')
        tree = ast.parse(text)
        imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
        names = [a.name for n in imports for a in n.names] + [getattr(n, 'module', '') or '' for n in imports]
        require(not any(name.split('.')[0] == 'microclaw' for name in names), 'script imports microclaw')
        require('run_acquisition_with_settings' in text and SESSION_NAME in text and 'abort_acquisition' in text,
                'script lacks the settings-route MDA, its name or its abort path')
    check('X1', x1)

    def x2():
        data = load(out / 'script.json')
        prep = load(out / 'measure.json')
        require(data['exit_code'] == 0, f"script exited {data['exit_code']}; see script-stderr.txt")
        require(len(data['new_datasets']) == 1, f"new datasets {data['new_datasets']}")
        ds = data.get('dataset') or {}
        require(ds.get('frames') == ds.get('index_entries') == prep['sizes'][4],
                f"script dataset frames {ds.get('frames')}, index {ds.get('index_entries')}, "
                f"planned {prep['sizes'][4]} ({data.get('dataset_error')})")
    check('X2', x2)

    report = {}
    try:
        report = measurements(out)
        if echo:
            gate.say('MEASUREMENT (reported, no bound; Micro-Manager engine timings, n=3 per arm on this machine):')
            for key, item in report.items():
                gate.say(f'  {key}: {json.dumps(item)}')
    except Exception as exc:
        gate.say(f'MEASUREMENT unavailable: {type(exc).__name__}: {exc}')
    gate.last_results = results
    gate.save('verify', dict(results=results, details=details, measurements=report, at=now().isoformat()))
    bad = sum(v != 'PASS' for v in results.values())
    if echo:
        gate.say(f'RESULT: {len(results) - bad}/{len(results)} PASS' + ('' if not bad else ' -- gate NOT passed'))
    return bad


# --- selftest: bridge-shaped fakes ------------------------------------------------------------
_fakes_spec = importlib.util.spec_from_file_location('mda_fakes_for_gate', HERE.parent / 'tests' / 'mda_fakes.py')


def _mda_fakes():
    module = importlib.util.module_from_spec(_fakes_spec)
    _fakes_spec.loader.exec_module(module)
    return module


class FakeEngine:
    """Studio's AcquisitionManager, from the 2.0.3 signatures: snake-cased methods, an enum
    shadow for SaveMode, a datastore whose isFrozen flips BEFORE the writer flushes (as
    DefaultDatastore.freeze does), frames written by ndstorage at 512 x 512 uint16."""
    FRAME_S = 0.01

    def __init__(self, fakes, size=512):
        self.f, self.size = fakes, size
        self.settings = fakes.Settings()
        self.running = False
        self.abort_calls = 0
        self.store = None

    def sequence_settings_builder(self):
        return self.f.Builder()

    def get_acquisition_settings(self):
        return self.settings

    def is_acquisition_running(self):
        return self.running

    def abort_acquisition(self):
        self.abort_calls += 1
        if self.store:
            self.store.abort.set()

    def run_acquisition_nonblocking(self):
        return self.run_acquisition_with_settings(self.settings, False)

    def run_acquisition_with_settings(self, settings, block):
        assert block is False
        if self.running:
            raise RuntimeError('IllegalThreadStateException: acquisition already running')
        self.settings = settings
        self.store = FakeStore(self, settings)
        self.running = True
        self.store.thread.start()
        return self.store


class FakeStore:
    def __init__(self, engine, settings):
        self.engine, self.settings = engine, settings
        self.count, self.path, self.frozen = 0, None, False
        self.abort = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        import numpy as np
        from ndstorage.ndtiff_dataset import NDTiffDataset
        s = self.settings
        writer = NDTiffDataset(str(s.root()), name=str(s.prefix()), writable=True)
        writer.initialize({})
        start = time.perf_counter()
        frame = np.zeros((self.engine.size, self.engine.size), np.uint16)
        try:
            for i in range(int(s.num_frames())):
                if self.abort.is_set():
                    break
                time.sleep(FakeEngine.FRAME_S)
                # Studio's MDA writes four axes even for a time-lapse (spike, 2026-10-17).
                writer.put_image({'channel': 0, 'position': 0, 'time': i, 'z': 0}, frame, {'ElapsedTime-ms': (time.perf_counter() - start) * 1000})
                writer._index_file.flush()
                self.path = writer.path
                self.count += 1
        finally:
            self.engine.running = False
            self.frozen = True            # DefaultDatastore.freeze: flag first...
            time.sleep(0.2)
            writer.finish()               # ...then the storage flush
            writer.close()

    def get_num_images(self): return self.count
    def get_save_path(self): return self.path
    def is_frozen(self): return self.frozen
    def get_summary_metadata(self):
        return type('Summary', (), {'get_sequence_settings': lambda _: self.settings})()


class SelftestFake:
    listening = None

    def __init__(self, gate_module_root, studio_cls, core_cls, engine, answers):
        self.root, self.studio_cls, self.core_cls, self.engine = gate_module_root, studio_cls, core_cls, engine
        self.answers = answers

    def serve_listening(self): return self.listening
    def answer(self, key): return self.answers.get(key, 'DONE')

    def session(self, gate, prep):
        """Synthetic UI; real execute_tool, export and history envelope."""
        from microclaw import tools
        from microclaw.conversation import AuditLog
        _, guard, ctrl = gate.connect(prep['port'])
        Path(prep['session_dir']).mkdir(parents=True, exist_ok=True)
        started = now()
        inputs = session_inputs(prep)
        data = dict(started_at=started.isoformat(), inputs=inputs, synthetic=True,
                    dialog_answer=self.answer('dialog'), abort_window_answer=self.answer('abort-window'),
                    dialog_screenshot=b84.screenshot(gate.out / 'dialog.png', synthetic=True),
                    stop_screenshot=b84.screenshot(gate.out / 'stop.png', synthetic=True))
        history = gate.out / 'session-history' / 'synthetic_microclaw_history.jsonl'
        history.parent.mkdir(exist_ok=True)
        audit, messages = AuditLog(history, enabled=True, secrets=()), []
        previous = tools.CONFIRM_FN
        tools.CONFIRM_FN = recording_confirm([], lambda p: None)
        cancel = threading.Event()

        def sink(event):
            if event.get('type') == 'acquisition_progress' and (event.get('frames_accounted') or 0) >= 10:
                cancel.set()
        try:
            for ident, tool in (('dialog', 'run_mda'), ('export', 'export_session_script'), ('stop', 'run_mda')):
                use = dict(role='assistant', content=[dict(type='tool_use', id='toolu_' + ident, name=tool,
                                                          input=inputs[ident])])
                messages.append(use)
                audit.append(use)
                raw = tools.execute_tool(tool, deepcopy(inputs[ident]), ctrl, guard, records=list(messages),
                                         cancel=cancel if ident == 'stop' else None,
                                         acquisition_event_sink=sink if ident == 'stop' else None)
                reply = dict(role='user', content=[dict(type='tool_result', tool_use_id='toolu_' + ident,
                                                        content=raw if isinstance(raw, str) else json.dumps(raw))])
                messages.append(reply)
                audit.append(reply)
        finally:
            tools.CONFIRM_FN = previous
        gate.save('session', data)

    def run_script(self, source):
        """Exec the emitted source in-process against the same fakes (subprocess cannot be patched)."""
        import contextlib
        import io
        import pycromanager
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            old = (pycromanager.Studio, pycromanager.Core)
            pycromanager.Studio, pycromanager.Core = self.studio_cls, self.core_cls
            try:
                exec(compile(Path(source).read_text(encoding='utf-8'), str(source), 'exec'),
                     {'__name__': '__main__', '__file__': str(source)})
                code = 0
            except SystemExit as exc:
                code = exc.code if isinstance(exc.code, int) else 1
            except BaseException:
                traceback.print_exc()
                code = 1
            finally:
                pycromanager.Studio, pycromanager.Core = old
        return code, stdout.getvalue(), stderr.getvalue()


def selftest(sizes=SELFTEST_SIZES, frame_size=512, keep=None):
    from unittest.mock import patch
    failures = 0
    with tempfile.TemporaryDirectory(prefix='block88a-') as temporary:
        base = Path(temporary)
        env = {name: str(base / name) for name in ('XDG_DATA_HOME', 'LOCALAPPDATA', 'XDG_CONFIG_HOME', 'APPDATA')}
        with patch.dict(os.environ, env):
            import microclaw
            from microclaw import paths, controller, tools
            print(f'SELFTEST product under test: {Path(microclaw.__file__).parent}', flush=True)
            fakes = _mda_fakes()
            engine = FakeEngine(fakes, size=frame_size)
            config = paths.default_safety_config()
            config.parent.mkdir(parents=True)
            config.write_text(e4.SAFETY_YAML, encoding='utf-8')
            mm_root = base / 'fake-mm'
            (mm_root / 'plugins').mkdir(parents=True)
            root = paths.user_data_dir()
            root.mkdir(parents=True)

            class Core(e4.BridgeCore):
                _creation_port = PORT
                exposure = 10.0
                def get_exposure(self): return type(self).exposure
                def set_exposure(self, *args): type(self).exposure = float(args[-1])
                def get_image_width(self): return frame_size
                def get_image_height(self): return frame_size
                def get_bytes_per_pixel(self): return 2

            class Studio(e4.BridgeStudio):
                def acquisitions(self): return engine

            answers = {'dialog': 'YES', 'abort-window': 'NO'}
            fake = SelftestFake(root, Studio, Core, engine, answers)
            out = base / 'evidence'
            gate = Gate(root, out, fake=fake)
            # Fake one level below _new_static_java_class (its pycromanager.JavaClass), never the
            # function itself: the exporter inlines that function's source, so patching it would
            # export the fake (found by this selftest, 2026-10-17).
            import pycromanager
            java_class = lambda classpath, port=None, **_: fakes.static_class(port, classpath)
            with patch.object(controller, 'Core', Core), patch.object(controller, 'Studio', Studio), \
                    patch.object(pycromanager, 'JavaClass', java_class), \
                    patch.object(controller.MicroscopeController, 'get_mm_app_dir', return_value=str(mm_root)), \
                    patch.object(tools, 'Acquisition', e4.FakeAcquisition):
                for phase in (lambda: gate.measure(sizes=sizes), gate.session, gate.script):
                    try:
                        phase()
                    except Exception as exc:     # a pre-block tree stops; verify scores what exists
                        gate.say(f'SELFTEST phase stopped: {type(exc).__name__}: {exc}')
                        traceback.print_exc()
            bad = verify(gate)
            scored = dict(gate.last_results)
            if keep:
                shutil.copytree(out, Path(keep) / 'evidence', dirs_exist_ok=True)
            ok = bad == 0
            failures += not ok
            gate.say(f"SELFTEST {'ok' if ok else 'WRONG'}: unmutated evidence scores every limb PASS ({bad} not)")
            if not ok:
                return 1
            # One mutation per limb: each must turn exactly its limb non-PASS.
            backup = base / 'evidence-unmutated'
            shutil.copytree(out, backup)
            prep = read_json(out / 'measure.json')
            first = {k: next(r for r in prep['runs'] if r['kind'] == k)['id']
                     for k in ('mda', 'abort-stop', 'abort-ctrlc')}

            def edit(path, change):
                data = read_json(path)
                change(data)
                write_json(path, data)

            def history_edit(change):
                path = out / 'session-history' / 'synthetic_microclaw_history.jsonl'
                lines = [json.loads(l) for l in path.read_text(encoding='utf-8').splitlines()]
                change(lines)
                path.write_text(''.join(json.dumps(l) + '\n' for l in lines), encoding='utf-8')

            def result_of(ident, change):
                def apply(lines):
                    for line in lines:
                        for block in line.get('content', []):
                            if block.get('tool_use_id') == 'toolu_' + ident:
                                data = json.loads(block['content'])
                                change(data)
                                block['content'] = json.dumps(data)
                return apply

            run = lambda k: out / 'runs' / first[k]
            mutations = {
                1: lambda: edit(run('mda') / 'events.json', lambda d: [e.update(phase='finished', frames_accounted=10**6)
                                                                      for e in d['events'] if e.get('type') == 'acquisition_progress']),
                2: lambda: edit(run('mda') / 'result.json', lambda d: d['settings_that_ran'].update(use_autofocus=True)),
                3: lambda: edit(run('mda') / 'result.json', lambda d: d['writer_completion'].update(state='frozen-unverified')),
                4: lambda: edit(run('abort-stop') / 'dataset.json', lambda d: d.update(frames=sizes[2])),
                5: lambda: edit(run('abort-ctrlc') / 'state.json', lambda d: d.update(escaped='KeyboardInterrupt escaped execute_tool')),
                6: lambda: edit(run('mda') / 'state.json', lambda d: d['post'].update(running=True)),
                7: lambda: (out / 'measure_microclaw_acquisitions.jsonl').write_text(
                    ''.join(l + '\n' for l in (out / 'measure_microclaw_acquisitions.jsonl').read_text(encoding='utf-8').splitlines()
                            if not ('"writer_finished"' in l and first['mda'] in l)), encoding='utf-8'),
                8: lambda: edit(run('mda') / 'result.json', lambda d: d['duration_breakdown'].pop('phase_meaning')),
                'S1': lambda: history_edit(result_of('dialog', lambda d: d.update(outcome='cancelled'))),
                'S2': lambda: edit(out / 'session.json', lambda d: d.update(dialog_answer='NO')),
                'S3': lambda: history_edit(result_of('stop', lambda d: d['writer_completion'].update(state='not_reached'))),
                'S4': lambda: edit(out / 'session.json', lambda d: d.update(abort_window_answer='YES')),
                'X1': lambda: (out / SCRIPT_NAME).write_text('import microclaw\n' + (out / SCRIPT_NAME).read_text(encoding='utf-8'), encoding='utf-8'),
                'X2': lambda: edit(out / 'script.json', lambda d: d['dataset'].update(frames=1)),
            }
            for limb, mutate in mutations.items():
                shutil.rmtree(out)
                shutil.copytree(backup, out)
                mutate()
                verify(gate, echo=False)
                changed = {k for k, v in gate.last_results.items() if v != scored[k]}
                good = changed == {limb}
                failures += not good
                gate.say(f"SELFTEST {'ok' if good else 'WRONG'}: mutating limb {limb} changed {sorted(map(str, changed))}")
            # Refusal that needs no rig: MicroClaw running.
            fake.listening = 'a selftest listener'
            try:
                gate.close_microclaw('Probe.')
                ok = False
            except NotExercised as exc:
                ok = str(e4.SERVE_PORT) in str(exc)
            fake.listening = None
            failures += not ok
            gate.say(f"SELFTEST {'ok' if ok else 'WRONG'}: a listener on port {e4.SERVE_PORT} refuses the run")
    print(f'SELFTEST {"PASSED" if not failures else f"FAILED ({failures})"}', flush=True)
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=('measure', 'session', 'script', 'verify', 'selftest'))
    parser.add_argument('--out')
    parser.add_argument('--frame-size', type=int, default=512)
    parser.add_argument('--keep', help='selftest: copy the unmutated evidence here')
    args = parser.parse_args()
    if args.phase == 'selftest':
        return selftest(frame_size=args.frame_size, keep=args.keep)
    from microclaw import paths
    gate = Gate(paths.user_data_dir(), Path(args.out))
    try:
        if args.phase == 'verify':
            return 1 if verify(gate) else 0
        getattr(gate, args.phase)()
        return 0
    except NotExercised as exc:
        gate.say(f'NOT EXERCISED: {exc}')
        return 2
    except Exception as exc:
        gate.say(f'PHASE FAILED: {type(exc).__name__}: {exc}')
        traceback.print_exc()
        return 1


if __name__ == '__main__':
    sys.exit(main())
