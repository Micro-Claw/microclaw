import numpy as np

from microclaw.hook_decisions import HookResult, SetIlluminationPower


class UVActivation:
    """Raise pre-enabled 405-nm power as blink density falls."""

    def __init__(self, threshold=0.0, start_percent=1.0, step_percent=1.0,
                 ceiling_percent=5.0):
        self.threshold = float(threshold)
        self.value = float(start_percent)
        self.step = float(step_percent)
        self.ceiling = float(ceiling_percent)

    def analyze_frame(self, image, metadata):
        blink_density = float(np.mean(np.asarray(image) > self.threshold))
        self.value = min(self.ceiling, self.value + self.step)
        return HookResult(
            {"blink_density": blink_density, "proposed_power_percent": self.value},
            (SetIlluminationPower(self.value),),
        )
