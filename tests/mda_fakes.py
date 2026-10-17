"""Micro-Manager 2.0.3 interfaces, transcribed from javap-mm-2.0.3.txt.

No Java collection is a Python sequence. SaveMode is only exposed by the
static-class factory; builder method names follow pyjavaz's method mapping.
"""
import threading
from types import SimpleNamespace


class JavaList:
    def __init__(self, values=()): self.values = values
    def size(self): return len(self.values)
    def get(self, i): return self.values[i]
    def __iter__(self): raise TypeError('Java collection is not iterable')


class SaveMode:
    def __init__(self, name): self.name = name
    def __str__(self): return self.name


class Settings:
    def __init__(self, values=None):
        self.values = dict(prefix='run', root='/data', save=True, save_mode=SaveMode('ND_TIFF'),
            use_frames=True, num_frames=2, interval_ms=0, use_position_list=False,
            use_slices=False, use_channels=False, use_autofocus=False,
            use_custom_intervals=False)
        self.values.update(values or {})
    def __getattr__(self, name):
        # All accessors here exist in the javap dump. Unknown names refuse.
        if name in {
            'prefix', 'root', 'save', 'save_mode', 'should_display_images',
            'use_frames', 'num_frames', 'interval_ms', 'use_position_list',
            'use_slices', 'slice_z_bottom_um', 'slice_z_top_um', 'slice_z_step_um',
            'relative_z_slice', 'use_channels', 'channel_group', 'use_autofocus',
            'skip_autofocus_count', 'keep_shutter_open_channels',
            'keep_shutter_open_slices', 'acq_order_mode', 'camera_timeout',
            'comment', 'use_custom_intervals',
        }: return lambda: self.values.get(name)
        raise AttributeError(name)
    def slices(self): return JavaList([0., 1.])
    def channels(self): return JavaList()
    def copy_builder(self):
        builder = Builder()
        builder.values.update(self.values)
        return builder


class Builder:
    def __init__(self): self.values = {}
    def __getattr__(self, name):
        if name in {'num_frames', 'interval_ms', 'use_frames', 'use_slices',
            'use_channels', 'use_position_list', 'use_autofocus', 'save',
            'save_mode', 'root', 'prefix', 'use_custom_intervals', 'is_test_acquisition'}:
            def set_value(value):
                self.values[name] = value
                return self
            return set_value
        raise AttributeError(name)
    def build(self): return Settings(self.values)


class Store:
    def __init__(self, manager): self.manager = manager
    def get_num_images(self): return self.manager.observation[1]
    def get_save_path(self): return '/data/actual_suffix'
    def is_frozen(self):
        self.manager.frozen_reads += 1
        return self.manager.observation[2]
    def get_summary_metadata(self):
        return SimpleNamespace(get_sequence_settings=lambda: self.manager.settings)


class Manager:
    def __init__(self, observations=None, block=None):
        self.observations = list(observations or [(True, 1, False, False), (False, 2, True, True)])
        self.observation = self.observations[0]
        self.settings = Settings()
        self.store = Store(self)
        self.calls = []; self.frozen_reads = 0; self.index_reads = 0
        self.block = block
        self.entered = threading.Event(); self.release = threading.Event()
    def blocked(self, call):
        if self.entered.is_set() and not self.release.is_set():
            raise AssertionError('Second bridge call queued behind a hung call')
        if self.block == call:
            self.entered.set(); self.release.wait()
    def sequence_settings_builder(self):
        self.calls.append('builder'); return Builder()
    def get_acquisition_settings(self):
        self.calls.append('settings'); return self.settings
    def run_acquisition_with_settings(self, settings, block):
        assert block is False
        self.calls.append('start'); self.settings = settings
        self.blocked('start'); return self.store
    def run_acquisition_nonblocking(self):
        self.calls.append('start'); self.blocked('start'); return self.store
    def is_acquisition_running(self):
        self.calls.append('poll'); self.blocked('poll')
        self.observation = self.observations.pop(0) if len(self.observations) > 1 else self.observations[0]
        return self.observation[0]
    def abort_acquisition(self):
        self.calls.append('abort'); self.blocked('abort')
    def index_complete(self, path, count):
        assert self.frozen_reads, 'index read before frozen'
        assert self.observation[2], 'index read before frozen'
        self.index_reads += 1
        return self.observation[3]


class Studio:
    def __init__(self, manager): self.manager = manager
    def acquisitions(self): return self.manager


class Core:
    def __init__(self): self.exposures = []; self.probes = 0
    def get_exposure(self): return 10.
    def set_exposure(self, value): self.exposures.append(value)
    def get_image_width(self): return 16
    def get_image_height(self): return 16
    def get_bytes_per_pixel(self): return 2
    def is_sequence_running(self):
        self.probes += 1
        raise AssertionError('Must not probe behind a live worker')


def static_class(port, classpath):
    assert classpath == 'org.micromanager.data.Datastore$SaveMode'
    return SimpleNamespace(**{name: SaveMode(name) for name in
        ('ND_TIFF', 'MULTIPAGE_TIFF', 'SINGLEPLANE_TIFF_SERIES')})
