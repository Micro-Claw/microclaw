"""88b spike: a community package following a growing MDA NDTiff, on a live Micro-Manager.

Standalone: imports pycromanager, ndstorage, numpy and tifffile, never microclaw. It runs
87a's real fixture worker (tests/fixtures/skill_packages/metrics/telemetry_worker/runner.py)
as a subprocess at below-normal priority, the way the package supervisor does, and speaks
its protocol directly on stdin/stdout.

Four 1000-frame ND_TIFF MDAs (interval 0), alternating: control, observed, control, observed.
It REPLACES the MDA window's settings and writes under --out.

  control   no reader, no extra folder: the baseline for every number below.
  observed  as soon as get_save_path() is non-null, creates <dataset>/analysis/<job>/
            (where AcquisitionAnalysisJob puts a job's output today), starts the fixture on
            the dataset, writes result.json there at once and a small .tif there at half
            the frames. At engine stop it sends `acquisition`; once isFrozen() and the
            ndstorage index equals getNumImages() it sends `writer`, then waits for the
            fixture's result.

Per arm, each step reported independently:
  run      start return, first save path, engine stop, frozen, index-complete times.
  reopen   ndstorage: index entries vs getNumImages, axes, first coordinates; frame gaps
           from ElapsedTime-ms (p50/p95/max; report only, n=2 per arm).
  mm       studio.data().load_data(path, True) -- virtual, which skips MM's memory dialog
           (DefaultDataManager bytecode, offset 141) -- its getNumImages, then close().
  fixture  (observed only) status-line frames_read against the polled getNumImages before
           and after it; metrics contiguity from frame 0; result state and frames_read;
           seconds from `writer` to result.

Writes spike-88b.json into --out. --selftest runs against a bridge-shaped fake whose
Store writes a real 4-axis 512 x 512 NDTiff with the index flushed ahead of its count, and
whose load_data follows MM 2.0.3's source (NDTiffAdapter.isNDTiffDataSet,
MultipageTiffReader.isMMMultipageTiff, "Cannot be both NDTiff and MultipageTiff").
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import traceback

REPORT = {}
FIXTURE = Path(__file__).resolve().parent.parent / 'tests' / 'fixtures' / 'skill_packages' / 'metrics' / 'telemetry_worker' / 'runner.py'


def step(key, fn):
    try:
        REPORT[key] = dict(ok=True, value=fn())
    except Exception as exc:
        REPORT[key] = dict(ok=False, error=f'{type(exc).__name__}: {exc}', trace=traceback.format_exc()[-1500:])
    print(f"{key}: {json.dumps(REPORT[key], default=str)[:700]}", flush=True)
    return REPORT[key]


def static_class(port, classpath):
    """controller._new_static_java_class, copied so this spike imports nothing from microclaw."""
    try:
        from pyjavaz.bridge import Bridge
        ref = Bridge._cached_bridges_by_port.get(port)
        bridge = ref() if ref is not None else None
        if bridge is not None:
            bridge._class_factory.classes.pop('java.lang.Class', None)
    except Exception:
        pass
    from pycromanager import JavaClass
    return JavaClass(classpath, port=port)


def build(manager, modes, root, prefix, frames):
    b = manager.sequence_settings_builder()
    for name, value in (('use_frames', True), ('use_slices', False), ('use_channels', False),
                        ('use_position_list', False), ('use_autofocus', False),
                        ('use_custom_intervals', False), ('is_test_acquisition', False),
                        ('save', True), ('save_mode', modes.value_of('ND_TIFF')),
                        ('root', str(root)), ('prefix', prefix), ('interval_ms', 0.0),
                        ('num_frames', frames)):
        b = getattr(b, name)(value)
    return b.build()


def open_dataset(path):
    """ndstorage prints a progress bar on every open; keep it out of the operator's console."""
    from ndstorage import Dataset
    with contextlib.redirect_stdout(io.StringIO()):
        return Dataset(str(path))


def index_complete(path, count):
    reader = open_dataset(path)
    try:
        return len(reader.index) == count
    finally:
        reader.close()


class Fixture:
    """87a's worker, launched and spoken to as the package supervisor does."""

    def __init__(self, dataset, output_dir, parameters):
        flags = getattr(subprocess, 'BELOW_NORMAL_PRIORITY_CLASS', 0)
        self.proc = subprocess.Popen([sys.executable, '-I', str(FIXTURE)], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                     bufsize=1, creationflags=flags)
        self.lines, self.started = [], time.perf_counter()
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        self.send(dict(protocol='1.2', job_id='spike88b', operation='observe_dataset',
                       parameters=parameters, input=dict(dataset=str(dataset)), output_dir=str(output_dir)))

    def _read(self):
        for line in self.proc.stdout:
            self.lines.append((time.perf_counter(), line))

    def send(self, message):
        try:
            self.proc.stdin.write(json.dumps(message) + '\n')
            self.proc.stdin.flush()
        except OSError:
            pass

    def finish(self, timeout):
        try:
            self.proc.wait(timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(5)
        self.reader.join(5)
        return self.proc.returncode, self.proc.stderr.read()[-1500:]


def percentile(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))] if values else None


def analyze_fixture(lines, polls, writer_sent, count):
    """Status frames_read against the polls either side of it; metrics contiguity; result."""
    statuses, mean_ranges, result = [], [], None
    for t, raw in lines:
        try:
            message = json.loads(raw)
        except ValueError:
            continue
        if message.get('type') == 'status' and message.get('message', '').startswith('frames_read='):
            statuses.append((t, int(message['message'].split(';')[0].split('=')[1])))
        elif message.get('type') == 'metrics' and 'mean_intensity' in message.get('values', {}):
            mean_ranges.append(tuple(message['frames']))
        elif message.get('type') == 'result':
            result = (t, message)
    ahead_prev, ahead_next = [], []
    for t, read in statuses:
        before = [c for (pt, _, c) in polls if pt <= t]
        after = [c for (pt, _, c) in polls if pt >= t]
        if before:
            ahead_prev.append(read - before[-1])
        if after:
            ahead_next.append(read - after[0])
    expected, contiguous = 0, True
    for first, last in mean_ranges:
        if first != expected:
            contiguous = False
            break
        expected = last + 1
    state = result[1] if result else {}
    return dict(
        status_lines=len(statuses), metrics_lines=len(mean_ranges),
        max_ahead_of_previous_poll=max(ahead_prev, default=None),
        samples_ahead_of_previous_poll=sum(1 for a in ahead_prev if a > 0),
        max_ahead_of_next_poll=max(ahead_next, default=None),
        samples_ahead_of_next_poll=sum(1 for a in ahead_next if a > 0),
        mean_contiguous_from_0=contiguous and bool(mean_ranges),
        mean_reached=expected - 1 if mean_ranges else None, engine_images=count,
        result_state=state.get('state'), input_complete=state.get('input_complete'),
        result_frames_read=state.get('output', {}).get('frames_read'),
        writer_to_result_s=(round(result[0] - writer_sent, 3) if result and writer_sent else None))


def run_arm(studio, manager, modes, root, key, frames, observed, parameters=None):
    import numpy as np
    state = {}

    def run():
        prefix = 'spike88b-' + key
        settings = build(manager, modes, root, prefix, frames)
        start = time.perf_counter()
        rel = lambda: round(time.perf_counter() - start, 3)
        store = manager.run_acquisition_with_settings(settings, False)
        state.update(store=store, start=start)
        out = dict(start_returned_s=rel(), store_none=store is None)
        if store is None:
            raise RuntimeError('run_acquisition_with_settings returned null')
        polls, fixture, job_dir, tif_written, path = [], None, None, False, None
        while True:
            t = time.perf_counter()
            running, count, path_now = bool(manager.is_acquisition_running()), int(store.get_num_images()), store.get_save_path()
            polls.append((t, running, count))
            if path_now and path is None:
                path = str(path_now)
                out.update(path_first_seen_s=rel(), path_first_seen_images=count, save_path=path)
                if observed:
                    job_dir = Path(path) / 'analysis' / ('job-' + key)
                    job_dir.mkdir(parents=True)
                    (job_dir / 'result.json').write_text(json.dumps(dict(written_s=rel())), encoding='utf-8')
                    out['analysis_dir_created_s'] = rel()
                    fixture = Fixture(path, job_dir, parameters or {})
            if observed and job_dir and not tif_written and count >= frames // 2:
                import tifffile
                tifffile.imwrite(str(job_dir / 'mask.tif'), np.zeros((64, 64), np.uint16))
                tif_written, out['analysis_tif_written_s'] = True, rel()
            if not running:
                out.update(engine_stopped_s=rel(), images_at_stop=count)
                break
            if time.perf_counter() - start > 600:
                raise TimeoutError('engine still running after 600 s')
            time.sleep(0.1)
        if fixture:
            fixture.send(dict(type='acquisition', outcome='completed', writer='unknown'))
        while True:
            if store.is_frozen():
                out.setdefault('frozen_s', rel())
                count = int(store.get_num_images())
                if index_complete(path, count):
                    out.update(index_complete_s=rel(), images=count)
                    break
            if time.perf_counter() - start > 720:
                raise TimeoutError(f'not frozen and complete; {out}')
            time.sleep(0.05)
        if fixture:
            writer_sent = time.perf_counter()
            fixture.send(dict(type='writer'))
            code, stderr = fixture.finish(120)
            state['fixture'] = dict(exit_code=code, stderr_tail=stderr,
                                    **analyze_fixture(fixture.lines, polls, writer_sent, out['images']))
        out['polls'] = len(polls)
        out['top_level'] = sorted(p.name + ('/' if p.is_dir() else '') for p in Path(path).iterdir())
        state['path'], state['count'] = path, out['images']
        return out
    if not step(f'{key}_run', run)['ok']:
        return

    def reopen():
        reader = open_dataset(state['path'])
        try:
            coords = [dict(c) for c in reader.get_image_coordinates_list()]
            elapsed = []
            for c in coords:
                md = reader.read_metadata(**c)
                if 'ElapsedTime-ms' in md:
                    elapsed.append((c.get('time', 0), float(md['ElapsedTime-ms'])))
            elapsed.sort()
            gaps = [b - a for (_, a), (_, b) in zip(elapsed, elapsed[1:])]
            return dict(index_entries=len(reader.index), engine_images=state['count'],
                        matches=len(reader.index) == state['count'],
                        axes=sorted({k for c in coords for k in c}), first_coords=coords[0] if coords else None,
                        gap_ms=dict(n=len(gaps), p50=percentile(gaps, 0.5), p95=percentile(gaps, 0.95),
                                    max=max(gaps, default=None),
                                    span_s=round((elapsed[-1][1] - elapsed[0][1]) / 1000, 3) if elapsed else None))
        finally:
            reader.close()
    step(f'{key}_reopen', reopen)

    def mm():
        t = time.perf_counter()
        loaded = studio.data().load_data(state['path'], True)
        if loaded is None:
            raise RuntimeError('load_data returned null')
        try:
            return dict(images=int(loaded.get_num_images()), engine_images=state['count'],
                        matches=int(loaded.get_num_images()) == state['count'],
                        load_s=round(time.perf_counter() - t, 3))
        finally:
            loaded.close()
    step(f'{key}_mmload', mm)
    if observed:
        step(f'{key}_fixture', lambda: state['fixture'])


def run(studio, port, out_dir, frames, arms=('control1', 'observed1', 'control2', 'observed2'), parameters=None):
    if not FIXTURE.is_file():
        raise FileNotFoundError(f'fixture worker not found at {FIXTURE}')
    manager = studio.acquisitions()
    modes = static_class(port, 'org.micromanager.data.Datastore$SaveMode')
    root = Path(out_dir) / 'spike-mda-data'
    root.mkdir(parents=True, exist_ok=True)
    for key in arms:
        run_arm(studio, manager, modes, root, key, frames, key.startswith('observed'),
                (parameters or {}).get(key))
    gaps = {k: v['value']['gap_ms'] for k, v in REPORT.items() if k.endswith('_reopen') and v['ok']}
    REPORT['summary_gap_p50_ms'] = dict(ok=True, value={k: g['p50'] for k, g in gaps.items()})
    print(f"summary_gap_p50_ms: {REPORT['summary_gap_p50_ms']['value']}", flush=True)


# --- selftest: a bridge-shaped fake, written from MM 2.0.3's source --------------------------
class _Enum:                       # default repr on purpose, as pyjavaz returns
    def __init__(self, name): self._n = name
    def name(self): return self._n


class _StaticSaveMode:             # static methods only; enum constants are absent
    def value_of(self, name): return _Enum(name)


class _Settings:
    def __init__(self, v): self.v = v


class _Builder:
    def __init__(self): self.v = {}
    def __getattr__(self, n):
        def set_(value):
            self.v[n] = value.name() if isinstance(value, _Enum) else value
            return self
        return set_
    def build(self): return _Settings(dict(self.v))


class _Store:
    """Real NDTiff with MM's four axes. The index is flushed before the count moves, and
    isFrozen() turns true before the writer finishes, as DefaultDatastore.freeze() does."""

    def __init__(self, s):
        self.s, self.n, self.path, self.frozen, self.done = s, 0, None, False, threading.Event()
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        import numpy as np
        from ndstorage.ndtiff_dataset import NDTiffDataset
        w = NDTiffDataset(self.s.v['root'], name=self.s.v['prefix'], writable=True)
        w.initialize({})
        self.path = w.path
        t0 = time.perf_counter()
        rng = np.random.default_rng(0)
        for i in range(self.s.v['num_frames']):
            time.sleep(0.01)
            w.put_image({'channel': 0, 'position': 0, 'time': i, 'z': 0},
                        rng.integers(0, 4000, (512, 512), dtype=np.uint16),
                        {'ElapsedTime-ms': (time.perf_counter() - t0) * 1e3})
            w._index_file.flush()
            if i % 7 == 0:
                time.sleep(0.03)  # the count lags the index, as measured in 88a
            self.n = i + 1
        self.done.set()
        self.frozen = True
        time.sleep(0.2)
        w.finish()
        w.close()

    def get_num_images(self): return self.n
    def get_save_path(self): return self.path
    def is_frozen(self): return self.frozen
    def close(self): pass


def _fake_is_mm_multipage(path):
    """MultipageTiffReader.isMMMultipageTiff: a top-level .tif breaks; a directory
    contributes its first direct .tif and the loop continues; int at byte 32 == 2355492."""
    found = None
    for item in Path(path).iterdir():
        if item.is_dir():
            for child in item.iterdir():
                if not child.name.startswith('._') and child.name.endswith('.tif'):
                    found = child
                    break
        elif not item.name.startswith('._') and (item.name.endswith('.tif') or item.name.endswith('.TIF')):
            found = item
            break
    if found is None:
        raise OSError(f'Unable to find any .tif files in {path}; is this a Micro-Manager dataset?')
    header = found.read_bytes()[:36]
    order = {b'II': 'little', b'MM': 'big'}.get(header[:2])
    if order is None:
        raise OSError('Error reading TIFF header')
    return int.from_bytes(header[32:36], order) == 2355492


class _Loaded:
    def __init__(self, path):
        reader = open_dataset(path)
        self.n = len(reader.index)
        reader.close()
    def get_num_images(self): return self.n
    def close(self): pass


class _DataManager:
    def load_data(self, path, virtual):
        if not virtual:
            raise AssertionError('a non-virtual load can open MM\'s memory dialog')
        nd = (Path(path) / 'NDTiff.index').exists()
        if nd and _fake_is_mm_multipage(path):
            raise RuntimeError('Cannot be both NDTiff and MultipageTiff')
        if not nd:
            raise AssertionError('fake supports NDTiff only')
        return _Loaded(path)


class _Manager:
    def __init__(self): self.store = None
    def sequence_settings_builder(self): return _Builder()
    def run_acquisition_with_settings(self, s, block):
        assert block is False
        self.store = _Store(s)
        return self.store
    def is_acquisition_running(self): return self.store is not None and not self.store.done.is_set()


def selftest():
    import pycromanager
    from unittest import mock
    with tempfile.TemporaryDirectory() as tmp:
        manager = _Manager()
        studio = type('Studio', (), {'acquisitions': lambda self: manager, 'data': lambda self: _DataManager()})()
        with mock.patch.object(pycromanager, 'JavaClass', lambda classpath, port=None: _StaticSaveMode()):
            run(studio, 4827, tmp, 300, arms=('control1', 'observed1', 'observedbroken'),
                parameters={'observedbroken': {'broken_contiguity': True}})
    v = lambda k: REPORT[k]['value'] if REPORT.get(k, {}).get('ok') else None
    checks = {
        'control reopens complete with four axes': lambda: v('control1_reopen')['matches'] and v('control1_reopen')['axes'] == ['channel', 'position', 'time', 'z'],
        'observed: analysis dir created at first path': lambda: 'analysis/' in v('observed1_run')['top_level'],
        'observed: MM reopen counts every image': lambda: v('observed1_mmload')['matches'],
        'observed: fixture succeeded on every frame': lambda: v('observed1_fixture')['result_state'] == 'succeeded' and v('observed1_fixture')['result_frames_read'] == 300,
        'observed: metrics contiguous to the last frame': lambda: v('observed1_fixture')['mean_contiguous_from_0'] and v('observed1_fixture')['mean_reached'] == 299,
        'observed: the fake index ran ahead and was seen': lambda: v('observed1_fixture')['samples_ahead_of_previous_poll'] > 0,
        'control: broken contiguity is detected': lambda: v('observedbroken_fixture')['mean_contiguous_from_0'] is False,
    }
    bad = []
    for name, check in checks.items():
        try:
            ok = bool(check())
        except Exception as exc:
            ok = False
            name += f' ({type(exc).__name__}: {exc})'
        print(('ok   ' if ok else 'FAIL ') + name, flush=True)
        if not ok:
            bad.append(name)
    # The fake's MM detection must be able to fire, or "MM reopened it" proves nothing.
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / 'NDTiff.index').write_bytes(b'')
        (Path(tmp) / 'analysis').mkdir()
        (Path(tmp) / 'analysis' / 'x.tif').write_bytes(b'II*\x00' + b'\x00' * 28 + (2355492).to_bytes(4, 'little'))
        try:
            _DataManager().load_data(tmp, True)
            bad.append('control: an MM-magic .tif one level down must trip detection')
            print('FAIL control: detection did not fire', flush=True)
        except RuntimeError:
            print('ok   control: an MM-magic .tif one level down trips detection', flush=True)
    print('SELFTEST ' + ('PASSED' if not bad else f'FAILED {bad}'), flush=True)
    return 1 if bad else 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default='.')
    parser.add_argument('--port', type=int, default=4827)
    parser.add_argument('--frames', type=int, default=1000)
    parser.add_argument('--selftest', action='store_true')
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    from pycromanager import Studio
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    try:
        run(Studio(port=args.port), args.port, out, args.frames)
    finally:
        (out / 'spike-88b.json').write_text(json.dumps(REPORT, indent=2, default=str), encoding='utf-8')
        print(f"Wrote {out / 'spike-88b.json'}", flush=True)
    return 0 if all(v['ok'] for v in REPORT.values()) else 1


if __name__ == '__main__':
    sys.exit(main())
