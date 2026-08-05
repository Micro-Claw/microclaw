import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, NamedTuple, Optional
import numpy as np
from pycromanager import Acquisition, Core, multi_d_acquisition_events


core = Core()
mm = SimpleNamespace(core=core)

# RECORDED TOOL: move_stage_xy
core.set_xy_position(12.5, -4.0)

# RECORDED TOOL: run_timelapse
events = multi_d_acquisition_events(**{'num_time_points': 2, 'time_interval_s': 0, 'channel_group': 'Channel', 'channels': ['DAPI'], 'channel_exposures_ms': [10]})
with Acquisition(directory='session-data', name='cells') as acq:
    acq.acquire(events)
