"""88a spike: what the bridge really gives for Studio's MDA, on a live Micro-Manager.

Standalone: imports pycromanager and ndstorage, never microclaw. Every step is
reported independently, so one failure does not hide the others.

Part A (read-only):
  A1  Datastore$SaveMode as a static class: which attributes exist; is ND_TIFF one?
  A2  SaveMode.value_of("ND_TIFF"): does it return; what are name() / to_string() / str()?
  A3  The dialog's current settings: save_mode() read twice -> name() stable? str() stable?
  A4  values(): what comes back (an array shadow?).

Part B (one short MDA, only with --acquire; it REPLACES the MDA window's settings):
  B1  fresh builder + value_of -> run_acquisition_with_settings(s, False): return time,
      running / image count right after return.
  B2  does get_acquisition_settings() now show our frames / prefix (the dialog follows)?
  B3  poll ~0.2 s: when get_save_path() becomes non-null; ndstorage reads while growing.
  B4  after the engine stops: is_frozen() timing; ndstorage index count vs get_num_images();
      first image's axes and metadata keys (is there ElapsedTime-ms?).
  B5  summary metadata: get_sequence_settings() present? its save_mode name.

Part C (only with --tiff; one short MDA per TIFF mode, MULTIPAGE_TIFF then
SINGLEPLANE_TIFF_SERIES). Measures the layout a "dataset reopens complete" check
would read, instead of guessing it:
  C<mode>  save path; engine stop and isFrozen() timing; the file tree under the save
           path with sizes at freeze, +1 s and +3 s (does anything still change after
           isFrozen?); per .tif: tifffile page count, is_ome / is_micromanager flags,
           series shapes and whether a series spans files; the total pages counted per
           file vs getNumImages().

Writes spike-88a.json next to --out and prints each step. --selftest runs against a
pyjavaz-shaped fake: a static shadow carries only static methods, an enum shadow has
the default repr plus name()/to_string(), and a fresh shadow comes back on every read.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import traceback

REPORT = {}


def step(key, fn):
    try:
        REPORT[key] = dict(ok=True, value=fn())
    except Exception as exc:
        REPORT[key] = dict(ok=False, error=f'{type(exc).__name__}: {exc}', trace=traceback.format_exc()[-1500:])
    print(f"{key}: {json.dumps(REPORT[key], default=str)[:600]}", flush=True)
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


def public(obj):
    return sorted(n for n in dir(obj) if not n.startswith('_'))


def enum_text(value):
    out = {'str': str(value), 'default_repr': ' object at 0x' in str(value)}
    for method in ('name', 'to_string'):
        try:
            out[method] = str(getattr(value, method)())
        except Exception as exc:
            out[method] = f'<{type(exc).__name__}: {exc}>'
    return out


def tree(path):
    """Every file under path with its size; never raises."""
    out = {}
    for item in sorted(Path(path).rglob('*')):
        if item.is_file():
            try:
                out[str(item.relative_to(path))] = item.stat().st_size
            except OSError as exc:
                out[str(item.relative_to(path))] = f'<{type(exc).__name__}>'
    return out


def tiff_facts(path):
    """What tifffile sees, file by file. Pages are counted per file, never per series."""
    import tifffile
    files = {}
    for item in sorted(Path(path).rglob('*.tif')):
        rel = str(item.relative_to(path))
        try:
            with tifffile.TiffFile(str(item)) as tif:
                series = []
                for s in tif.series:
                    try:
                        spans = sorted({str(Path(p.parent.filehandle.path).name)
                                        for p in s.pages if p is not None and getattr(p, 'parent', None)})
                    except Exception as exc:
                        spans = f'<{type(exc).__name__}: {exc}>'
                    series.append(dict(shape=list(s.shape), axes=s.axes, kind=getattr(s, 'kind', None),
                                       files=spans))
                files[rel] = dict(pages=len(tif.pages), is_ome=tif.is_ome,
                                  is_micromanager=tif.is_micromanager, series=series[:4])
        except Exception as exc:
            files[rel] = dict(error=f'{type(exc).__name__}: {exc}'[:300])
    pages = [f['pages'] for f in files.values() if 'pages' in f]
    return dict(files=files, tif_files=len(files), pages_summed_per_file=sum(pages))


def tiff_run(manager, modes, out_dir, mode, frames):
    root = Path(out_dir) / 'spike-mda-data'
    root.mkdir(parents=True, exist_ok=True)
    key = 'C_' + mode

    def go():
        b = manager.sequence_settings_builder()
        for name, value in (('use_frames', True), ('use_slices', False), ('use_channels', False),
                            ('use_position_list', False), ('use_autofocus', False),
                            ('use_custom_intervals', False), ('is_test_acquisition', False),
                            ('save', True), ('save_mode', modes.value_of(mode)),
                            ('root', str(root)), ('prefix', 'spike88a-' + mode.lower()),
                            ('interval_ms', 0.0), ('num_frames', frames)):
            b = getattr(b, name)(value)
        start = time.perf_counter()
        store = manager.run_acquisition_with_settings(b.build(), False)
        returned = round(time.perf_counter() - start, 3)
        stopped = frozen = None
        while time.perf_counter() - start < 600:
            t = round(time.perf_counter() - start, 3)
            if stopped is None and not manager.is_acquisition_running():
                stopped = t
            if stopped is not None and store.is_frozen():
                frozen = t
                break
            time.sleep(0.1)
        path = store.get_save_path()
        count = int(store.get_num_images())
        if frozen is None:
            raise TimeoutError(f'not frozen within 600 s (stopped at {stopped}); path {path}')
        snapshots = {'at_freeze': tree(path)}
        time.sleep(1)
        snapshots['plus_1s'] = tree(path)
        time.sleep(2)
        snapshots['plus_3s'] = tree(path)
        changed = sorted(k for k in set(snapshots['at_freeze']) | set(snapshots['plus_3s'])
                         if snapshots['at_freeze'].get(k) != snapshots['plus_3s'].get(k))
        facts = tiff_facts(path)
        return dict(save_path=str(path), start_returned_s=returned, engine_stopped_s=stopped,
                    frozen_s=frozen, engine_images=count, changed_after_freeze=changed,
                    tree_at_freeze=snapshots['at_freeze'], **facts,
                    pages_match_images=facts['pages_summed_per_file'] == count)
    return step(key, go)


def run(studio, port, out_dir, acquire, frames, tiff=False):
    manager = studio.acquisitions()
    modes = static_class(port, 'org.micromanager.data.Datastore$SaveMode')
    step('A1_static_attributes', lambda: dict(attributes=public(modes), has_ND_TIFF=hasattr(modes, 'ND_TIFF'),
                                              type=type(modes).__name__))
    a2 = step('A2_value_of', lambda: enum_text(modes.value_of('ND_TIFF')))

    def a3():
        current = manager.get_acquisition_settings()
        first, second = current.save_mode(), current.save_mode()
        return dict(first=enum_text(first), second=enum_text(second),
                    str_stable=str(first) == str(second),
                    name_stable=enum_text(first)['name'] == enum_text(second)['name'])
    step('A3_dialog_save_mode_twice', a3)

    def a4():
        values = modes.values()
        info = dict(type=type(values).__name__, attributes=public(values)[:40])
        for probe in ('__len__', '__iter__', '__getitem__'):
            info[probe] = hasattr(values, probe)
        return info
    step('A4_values', a4)
    if tiff and a2['ok']:
        for mode in ('MULTIPAGE_TIFF', 'SINGLEPLANE_TIFF_SERIES'):
            tiff_run(manager, modes, out_dir, mode, frames)
    if not acquire:
        print('Part B skipped (pass --acquire to run one short MDA).', flush=True)
        return
    if not a2['ok']:
        print('Part B skipped: value_of failed, so no settings can be built.', flush=True)
        return
    root = Path(out_dir) / 'spike-mda-data'
    root.mkdir(parents=True, exist_ok=True)
    prefix = 'spike88a'

    def build():
        b = manager.sequence_settings_builder()
        for name, value in (('use_frames', True), ('use_slices', False), ('use_channels', False),
                            ('use_position_list', False), ('use_autofocus', False),
                            ('use_custom_intervals', False), ('is_test_acquisition', False),
                            ('save', True), ('save_mode', modes.value_of('ND_TIFF')),
                            ('root', str(root)), ('prefix', prefix), ('interval_ms', 0.0),
                            ('num_frames', frames)):
            b = getattr(b, name)(value)
        return b.build()
    built = step('B0_build', lambda: enum_text(build().save_mode()))
    if not built['ok']:
        return
    settings = build()
    state = {}

    def b1():
        start = time.perf_counter()
        store = manager.run_acquisition_with_settings(settings, False)
        state['store'], state['start'] = store, start
        returned = time.perf_counter() - start
        return dict(returned_s=round(returned, 4), store_type=type(store).__name__,
                    running_after=bool(manager.is_acquisition_running()),
                    images_after=int(store.get_num_images()), save_path_after=store.get_save_path())
    if not step('B1_start', b1)['ok']:
        return
    store, start = state['store'], state['start']

    def b2():
        now = manager.get_acquisition_settings()
        return dict(num_frames=int(now.num_frames()), prefix=str(now.prefix()), root=str(now.root()),
                    save=bool(now.save()), save_mode=enum_text(now.save_mode()))
    step('B2_engine_settings_now', b2)

    def b3():
        from ndstorage import Dataset
        polls, path_seen, growing = [], None, []
        while True:
            t = round(time.perf_counter() - start, 3)
            running, count, path = bool(manager.is_acquisition_running()), int(store.get_num_images()), store.get_save_path()
            if path and path_seen is None:
                path_seen = dict(t_s=t, path=str(path), images=count)
            if path and running and len(growing) < 8:
                try:
                    reader = Dataset(str(path))
                    try:
                        growing.append(dict(t_s=t, index_entries=len(reader.index), engine_images=count))
                    finally:
                        reader.close()
                except Exception as exc:
                    growing.append(dict(t_s=t, error=f'{type(exc).__name__}: {exc}'[:200]))
            polls.append((t, running, count))
            if not running:
                return dict(engine_stopped_s=t, images=count, path_first_seen=path_seen,
                            growing_reads=growing, polls=len(polls))
            if t > 600:
                raise TimeoutError('engine still running after 600 s')
            time.sleep(0.2)
    if not step('B3_running', b3)['ok']:
        return

    def b4():
        from ndstorage import Dataset
        frozen_at, reads = None, []
        deadline = time.perf_counter() + 120
        while time.perf_counter() < deadline:
            t = round(time.perf_counter() - start, 3)
            frozen = bool(store.is_frozen())
            if frozen and frozen_at is None:
                frozen_at = t
            path = store.get_save_path()
            try:
                reader = Dataset(str(path))
                try:
                    coords = reader.get_image_coordinates_list()
                    first = dict(coords[0]) if coords else {}
                    keys = sorted(reader.read_metadata(**first).keys()) if coords else []
                    reads.append(dict(t_s=t, frozen=frozen, index_entries=len(reader.index),
                                      engine_images=int(store.get_num_images())))
                    if frozen and len(reader.index) == int(store.get_num_images()):
                        return dict(frozen_first_seen_s=frozen_at, complete_at_s=t, reads=reads[-5:],
                                    first_coords=first, axes=sorted({k for c in coords for k in c}),
                                    first_metadata_keys=keys, has_elapsed_ms='ElapsedTime-ms' in keys)
                finally:
                    reader.close()
            except Exception as exc:
                reads.append(dict(t_s=t, frozen=frozen, error=f'{type(exc).__name__}: {exc}'[:200]))
            time.sleep(0.2)
        raise TimeoutError(f'not frozen and complete within 120 s; last reads {reads[-5:]}')
    step('B4_finalized', b4)

    def b5():
        summary = store.get_summary_metadata()
        ran = summary.get_sequence_settings()
        return dict(present=ran is not None, num_frames=int(ran.num_frames()) if ran is not None else None,
                    save_mode=enum_text(ran.save_mode()) if ran is not None else None)
    step('B5_summary_settings', b5)


# --- selftest: a pyjavaz-shaped fake ---------------------------------------------------------
class _Enum:                       # default repr on purpose: that is what the bridge returns
    def __init__(self, name): self._n = name
    def name(self): return self._n
    def to_string(self): return self._n


class _Array:                      # pyjavaz: a non-primitive array is an opaque shadow
    pass


class _StaticSaveMode:             # static methods only; enum constants are fields and are absent
    def value_of(self, name):
        if name not in ('ND_TIFF', 'MULTIPAGE_TIFF', 'SINGLEPLANE_TIFF_SERIES'):
            raise ValueError(name)
        return _Enum(name)
    def values(self): return _Array()


class _Settings:
    def __init__(self, v): self.v = v
    def save_mode(self): return _Enum(self.v.get('save_mode', 'MULTIPAGE_TIFF'))   # fresh shadow per read
    def __getattr__(self, n):
        if n in ('num_frames', 'prefix', 'root', 'save'):
            return lambda: self.v.get(n)
        raise AttributeError(n)


class _Builder:
    def __init__(self): self.v = {}
    def __getattr__(self, n):
        def set_(value):
            self.v[n] = value.name() if isinstance(value, _Enum) else value
            return self
        return set_
    def build(self): return _Settings(dict(self.v))


class _Store:
    def __init__(self, s):
        self.s, self.n, self.path, self.frozen, self.done = s, 0, None, False, threading.Event()
        threading.Thread(target=self.run, daemon=True).start()
    def run(self):
        import numpy as np
        if self.s.v.get('save_mode') != 'ND_TIFF':
            return self.run_tiff()
        from ndstorage.ndtiff_dataset import NDTiffDataset
        w = NDTiffDataset(self.s.v['root'], name=self.s.v['prefix'], writable=True)
        w.initialize({})
        t0 = time.perf_counter()
        for i in range(self.s.v['num_frames']):
            time.sleep(0.01)
            w.put_image({'time': i}, np.zeros((512, 512), np.uint16), {'ElapsedTime-ms': (time.perf_counter() - t0) * 1e3})
            w._index_file.flush()
            self.path, self.n = w.path, self.n + 1
        self.done.set()
        self.frozen = True
        time.sleep(0.2)
        w.finish()
        w.close()
    def run_tiff(self):
        """A plausible layout for the selftest only; the spike exists to measure the real one."""
        import numpy as np
        import tifffile
        folder = Path(self.s.v['root']) / (self.s.v['prefix'] + '_1')
        folder.mkdir(parents=True)
        self.path = str(folder)
        frame = np.zeros((64, 64), np.uint16)
        if self.s.v['save_mode'] == 'MULTIPAGE_TIFF':
            with tifffile.TiffWriter(str(folder / 'stack_MMStack_Pos0.ome.tif')) as w:
                for i in range(self.s.v['num_frames']):
                    w.write(frame, contiguous=False)
                    self.n += 1
        else:
            for i in range(self.s.v['num_frames']):
                tifffile.imwrite(str(folder / f'img_{i:09d}.tif'), frame)
                self.n += 1
        self.done.set()
        self.frozen = True

    def get_num_images(self): return self.n
    def get_save_path(self): return self.path
    def is_frozen(self): return self.frozen
    def get_summary_metadata(self):
        s = self.s
        return type('S', (), {'get_sequence_settings': lambda _: s})()


class _Manager:
    def __init__(self): self.current, self.store = _Settings({'save_mode': 'MULTIPAGE_TIFF'}), None
    def get_acquisition_settings(self): return self.current
    def sequence_settings_builder(self): return _Builder()
    def run_acquisition_with_settings(self, s, block):
        assert block is False
        self.current, self.store = s, _Store(s)
        return self.store
    def is_acquisition_running(self): return self.store is not None and not self.store.done.is_set()


def selftest():
    import pycromanager
    from unittest import mock
    with tempfile.TemporaryDirectory() as tmp:
        studio = type('Studio', (), {'acquisitions': lambda self: manager})()
        manager = _Manager()
        with mock.patch.object(pycromanager, 'JavaClass', lambda classpath, port=None: _StaticSaveMode()):
            run(studio, 4827, tmp, True, 30, tiff=True)
    want = dict(A1=lambda v: v['has_ND_TIFF'] is False, A2=lambda v: v['name'] == 'ND_TIFF' and v['default_repr'],
                A3=lambda v: v['name_stable'] and not v['str_stable'], B1=lambda v: v['running_after'],
                B2=lambda v: v['num_frames'] == 30 and v['prefix'] == 'spike88a',
                B4=lambda v: v['has_elapsed_ms'], B5=lambda v: v['save_mode']['name'] == 'ND_TIFF',
                C_MULTIPAGE=lambda v: v['pages_match_images'] and v['tif_files'] == 1,
                C_SINGLEPLANE=lambda v: v['pages_match_images'] and v['tif_files'] == 30)
    bad = []
    for prefix, check in want.items():
        key = next(k for k in REPORT if k.startswith(prefix + '_'))
        if not (REPORT[key]['ok'] and check(REPORT[key]['value'])):
            bad.append(key)
    print('SELFTEST ' + ('PASSED' if not bad else f'FAILED {bad}'), flush=True)
    return 1 if bad else 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--out', default='.')
    parser.add_argument('--port', type=int, default=4827)
    parser.add_argument('--acquire', action='store_true')
    parser.add_argument('--frames', type=int, default=300)
    parser.add_argument('--tiff', action='store_true', help='Part C: one short MDA per TIFF mode')
    parser.add_argument('--selftest', action='store_true')
    args = parser.parse_args()
    if args.selftest:
        return selftest()
    from pycromanager import Studio
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    try:
        run(Studio(port=args.port), args.port, out, args.acquire, args.frames, tiff=args.tiff)
    finally:
        (out / 'spike-88a.json').write_text(json.dumps(REPORT, indent=2, default=str), encoding='utf-8')
        print(f"Wrote {out / 'spike-88a.json'}", flush=True)
    return 0 if all(v['ok'] for v in REPORT.values()) else 1


if __name__ == '__main__':
    sys.exit(main())
