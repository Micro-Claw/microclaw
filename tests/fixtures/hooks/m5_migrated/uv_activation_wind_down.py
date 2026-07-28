import numpy as np

from microclaw.hook_decisions import HookResult, SetIlluminationPower


class UVActivationWithWindDown:
    """Ramp pre-enabled activation power, then wind it down before the run ends.

    The wind-down is the point. End-of-run illumination policy belongs to the
    hook rather than to microclaw (operator ruling 2026-07-28), which is only
    safe if a hook's own downward write cannot be refused once its envelope
    budget is spent. A non-increasing proposal is therefore free: it costs no
    budget and is never refused for exhaustion.

    Set ``wind_down_after`` below the envelope's ``max_writes`` so the ramp is
    still spending budget when the wind-down arrives.
    """

    def __init__(self, threshold=0.0, start_percent=1.0, step_percent=1.0,
                 ceiling_percent=100.0, wind_down_after=None, floor_percent=0.0):
        self.threshold = float(threshold)
        self.value = float(start_percent)
        self.step = float(step_percent)
        self.ceiling = float(ceiling_percent)
        self.floor = float(floor_percent)
        self.wind_down_after = (
            None if wind_down_after is None else int(wind_down_after)
        )
        self.frames = 0

    def analyze_frame(self, image, metadata):
        self.frames += 1
        blink_density = float(np.mean(np.asarray(image) > self.threshold))
        if self.wind_down_after is not None and self.frames > self.wind_down_after:
            self.value = self.floor
            phase = "wind_down"
        else:
            self.value = min(self.ceiling, self.value + self.step)
            phase = "ramp"
        return HookResult(
            {"blink_density": blink_density, "frame": self.frames,
             "phase": phase, "proposed_power_percent": self.value},
            (SetIlluminationPower(self.value),),
        )
