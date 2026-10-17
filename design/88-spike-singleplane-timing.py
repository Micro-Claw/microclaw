"""88a spike: where did the 36 s go after a 'Separate image files' MDA stopped?

The second 88a gate measured writer_finalization = 35.96 s for 1000 separate files, with
no progress record in between, so one worker call took that long. Opening the 1000
finished files later cost 0.2 s, so the open pass alone does not explain it.

This runs one fresh MDA (SINGLEPLANE_TIFF_SERIES, --frames, default 1000) and, from the
moment is_acquisition_running() turns false, times every call the product's worker
makes, in its order: is_acquisition_running, get_num_images, get_save_path, is_frozen,
then the completion check (rglob count, then tifffile-open every file). It repeats until
the check passes or 300 s elapse. Standalone: imports pycromanager and tifffile only.
REPLACES the MDA window's settings, like the other 88a spikes.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import tifffile


def static_class(port, classpath):
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


def timed(fn):
    t0 = time.perf_counter()
    try:
        value = fn()
        return round(time.perf_counter() - t0, 4), value, None
    except Exception as exc:
        return round(time.perf_counter() - t0, 4), None, f'{type(exc).__name__}: {exc}'[:200]


def check(path, count):
    files = list(Path(path).rglob('*.tif'))
    if len(files) != count:
        return False, len(files)
    for f in files:
        with tifffile.TiffFile(f) as tif:
            if len(tif.pages) != 1:
                return False, len(files)
    return True, len(files)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--out', required=True)
    p.add_argument('--port', type=int, default=4827)
    p.add_argument('--frames', type=int, default=1000)
    a = p.parse_args()
    from pycromanager import Studio
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    manager = Studio(port=a.port).acquisitions()
    modes = static_class(a.port, 'org.micromanager.data.Datastore$SaveMode')
    # Create the folder first: AcquisitionWrapperEngine.runAcquisition shows a modal
    # 'Directory not found. Create it?' for a root that cannot be written, and the bridge
    # blocks until someone answers it (found 2026-10-17 by this spike's first attempt).
    (out / 'data').mkdir(parents=True, exist_ok=True)
    b = manager.sequence_settings_builder()
    for name, value in (('use_frames', True), ('use_slices', False), ('use_channels', False),
                        ('use_position_list', False), ('use_autofocus', False),
                        ('use_custom_intervals', False), ('is_test_acquisition', False),
                        ('save', True), ('save_mode', modes.value_of('SINGLEPLANE_TIFF_SERIES')),
                        ('root', str(out / 'data')), ('prefix', 'timing'), ('interval_ms', 0.0),
                        ('num_frames', a.frames)):
        b = getattr(b, name)(value)
    start = time.perf_counter()
    store = manager.run_acquisition_with_settings(b.build(), False)
    while manager.is_acquisition_running():
        time.sleep(0.1)
        if time.perf_counter() - start > 600:
            sys.exit('engine still running after 600 s')
    stopped = time.perf_counter()
    rows = []
    while time.perf_counter() - stopped < 300:
        row = dict(t_s=round(time.perf_counter() - stopped, 3))
        for name, fn in (('is_acquisition_running', manager.is_acquisition_running),
                         ('get_num_images', store.get_num_images), ('get_save_path', store.get_save_path),
                         ('is_frozen', store.is_frozen)):
            row[name + '_s'], value, error = timed(fn)
            row[name] = str(value) if error is None else error
        if row['is_frozen'] == 'True':
            row['check_s'], value, error = timed(lambda: check(row['get_save_path'], int(row['get_num_images'])))
            row['check'] = error or list(value)
        rows.append(row)
        print(json.dumps(row), flush=True)
        if row.get('check') and row['check'][0] is True:
            break
        time.sleep(1.0)
    report = dict(engine_ran_s=round(stopped - start, 3), rows=rows)
    (out / 'spike-88a-singleplane-timing.json').write_text(json.dumps(report, indent=1), encoding='utf-8')
    print('Wrote', out / 'spike-88a-singleplane-timing.json')


if __name__ == '__main__':
    main()
