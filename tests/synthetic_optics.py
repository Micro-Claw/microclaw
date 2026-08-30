"""ONE synthetic optical model, shared by the calibration and centring tests.

design/64. The centring sign defect survived a full green suite because the two
halves of the loop were tested against fakes that contradicted each other: the
calibration fake shifted its scene one way for a +X stage move and the centring
fake shifted its spot the other, so each passed alone while the pair described
two different microscopes — and the tool, which has to work on one, moved the
wrong way on every real rig.

The only physical statement here is `m_phys`: a stage move `s` (µm) translates
the scene in the image by `m_phys @ s` (px). Every sign convention downstream —
what `phase_cross_correlation` returns, what `solve_affine` stores, which way
`center_feature` moves — is *derived* from that by running the real code, never
asserted alongside it. Nothing in this file may name an affine coefficient.
"""
from __future__ import annotations

import numpy as np

_PX_UM = 0.5                      # µm per pixel in every synthetic rig below
_K = 1.0 / _PX_UM                 # px per µm

# Both axis flips and a 90° camera rotation (design/64 required test 2).
OPTICS_CASES = {
    "aligned":    [[_K, 0.0], [0.0, _K]],
    "flip_x":     [[-_K, 0.0], [0.0, _K]],
    "flip_y":     [[_K, 0.0], [0.0, -_K]],
    "flip_both":  [[-_K, 0.0], [0.0, -_K]],
    "rot90":      [[0.0, -_K], [_K, 0.0]],
    "rot90_flip": [[0.0, _K], [_K, 0.0]],
}


class SyntheticOptics:
    """A rig whose only property is how the scene moves when the stage does."""

    def __init__(self, m_phys, shape=(160, 160), punctum=(52.0, 118.0),
                 amplitude=6000.0, second_punctum=None, second_amplitude=0.0,
                 quantum_um=0.0):
        self.m_phys = np.asarray(m_phys, dtype=float)
        self.shape = shape
        self.punctum = punctum
        self.amplitude = amplitude
        self.second_punctum = second_punctum
        self.second_amplitude = second_amplitude
        # Stage quantization, off by default. design/29 measured ~0.8 µm of it
        # on M2 — identical 2 µm commands produced 14.8 or 22.4 px — so a rig
        # has a residual floor and a closed loop cannot always reach an
        # arbitrary tolerance. Modelling it is what makes the non-convergence
        # path testable without hand-writing a wrong affine.
        self.quantum_um = float(quantum_um)
        self.pos = {"x": 0.0, "y": 0.0}
        rng = np.random.default_rng(7)
        # Texture so phase correlation has something to lock onto; the punctum
        # is drawn on top so the very same frame serves the blob detector.
        self.texture = rng.random(shape).astype(np.float32) * 300.0 + 400.0

    def scene_shift_px(self) -> tuple[float, float]:
        shift = self.m_phys @ np.array([self.pos["x"], self.pos["y"]], dtype=float)
        return float(shift[0]), float(shift[1])          # (dx_px, dy_px)

    def _at(self, punctum):
        dx, dy = self.scene_shift_px()
        height, width = self.shape
        return (punctum[0] + dy) % height, (punctum[1] + dx) % width

    def snap(self) -> np.ndarray:
        from scipy.ndimage import shift as ndshift

        dx, dy = self.scene_shift_px()
        image = ndshift(self.texture, (dy, dx), order=1, mode="wrap")
        height, width = self.shape
        yy, xx = np.mgrid[0:height, 0:width]
        for punctum, amplitude in (
            (self.punctum, self.amplitude),
            (self.second_punctum, self.second_amplitude),
        ):
            if punctum is None or amplitude <= 0:
                continue
            py, px = self._at(punctum)
            image = image + amplitude * np.exp(
                -((yy - py) ** 2 + (xx - px) ** 2) / 8.0
            )
        return image.astype(np.uint16)

    def punctum_offset_px(self, punctum=None) -> tuple[float, float]:
        """Where a punctum actually is, relative to the field centre."""
        height, width = self.shape
        py, px = self._at(self.punctum if punctum is None else punctum)
        return px - width / 2, py - height / 2

    def drive(self, mock_ctrl, monkeypatch):
        """Point a mock controller's stage and camera at this model."""
        mock_ctrl.core.get_x_position.side_effect = lambda: self.pos["x"]
        mock_ctrl.core.get_y_position.side_effect = lambda: self.pos["y"]
        mock_ctrl.core.set_relative_xy_position.side_effect = self.move
        mock_ctrl.core.get_pixel_size_um.return_value = 0.0
        monkeypatch.setattr("microclaw.tools.snap_to_numpy", lambda ctrl: self.snap())
        return self

    def move(self, dx_um, dy_um):
        if self.quantum_um > 0:
            quantum = self.quantum_um
            dx_um = round(dx_um / quantum) * quantum
            dy_um = round(dy_um / quantum) * quantum
        self.pos["x"] += dx_um
        self.pos["y"] += dy_um
