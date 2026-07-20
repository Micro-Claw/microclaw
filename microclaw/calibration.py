"""Stage↔camera affine calibration (design/14 §8).

In the amr_test session the agent had no pixel size and no camera↔stage axis
mapping, so it navigated by nudging the stage and squinting at thumbnails —
three moves produced three mutually inconsistent conclusions about the axis
signs. The affine turns every "nudge and squint" loop into arithmetic.
"""
from __future__ import annotations
import re
from dataclasses import asdict, dataclass

import numpy as np

KNOWLEDGE_CATEGORY = "devices"


@dataclass
class StageCameraAffine:
    """Maps image-pixel displacement → stage-µm displacement.

        [dx_um]   [a b] [dx_px]
        [dy_um] = [c d] [dy_px]

    Captures pixel size, camera rotation, and BOTH axis flips in one object.
    It is a property of the optical path (objective + binning), not of the
    session, so it is cached in the knowledge base.
    """

    a: float
    b: float
    c: float
    d: float
    objective: str
    binning: int
    pixel_size_um: float  # sqrt(|det|), for reporting

    def px_to_um(self, dx_px: float, dy_px: float) -> tuple[float, float]:
        return (
            self.a * dx_px + self.b * dy_px,
            self.c * dx_px + self.d * dy_px,
        )


def solve_affine(
    shift_x_px: tuple[float, float],
    shift_y_px: tuple[float, float],
    step_um: float,
    objective: str,
    binning: int,
) -> StageCameraAffine:
    """Solve the 2×2 affine from two measured image shifts.

    shift_*_px are (row, col) = (dy_px, dx_px) image shifts observed for a
    +step_um stage move along stage-X and stage-Y respectively (the output
    convention of skimage.registration.phase_cross_correlation).

    Raises ValueError when the shifts are degenerate — a garbage affine is worse
    than none. This is a geometric backstop; calibrate_stage_to_camera diagnoses
    the specific cause (step too large for the FOV / too small / no structure —
    design/28 F4) before reaching here, so the message here only lists the
    possibilities rather than naming one, which the old text got backwards
    ("step too small" when the real cause was a step LARGER than the FOV).
    """
    m_px_per_um = np.array(
        [
            [shift_x_px[1], shift_y_px[1]],   # dx_px per um along stage X / Y
            [shift_x_px[0], shift_y_px[0]],   # dy_px per um along stage X / Y
        ]
    ) / step_um
    det = float(np.linalg.det(m_px_per_um))
    if abs(det) < 1e-9:
        raise ValueError(
            "Measured image shifts are degenerate (near-zero determinant): the "
            "two stage moves did not produce two independent, measurable image "
            "shifts. Causes, most common first on a cropped ROI: step_um too "
            "LARGE for the field of view (the move pushed the scene out of frame, "
            "leaving no overlap to register), step_um too small to move the "
            "image, or a featureless/periodic field. A good step is about a "
            "quarter of the smaller FOV dimension."
        )
    m = np.linalg.inv(m_px_per_um)
    return StageCameraAffine(
        a=float(m[0, 0]),
        b=float(m[0, 1]),
        c=float(m[1, 0]),
        d=float(m[1, 1]),
        objective=objective,
        binning=binning,
        pixel_size_um=float(np.sqrt(abs(np.linalg.det(m)))),
    )


def affine_key(objective: str, binning: int) -> str:
    slug = re.sub(r"\W+", "_", objective).strip("_") or "default"
    return f"stage_camera_affine_{slug}_bin{binning}"


def save_affine(affine: StageCameraAffine) -> str:
    """Persist under the knowledge base's devices category.

    Written via save_entry directly, NOT the confirm-gated save_knowledge
    tool: the gate exists for model-authored free text, and this value is
    deterministic measured numbers.
    """
    from microclaw.knowledge_manager import save_entry

    key = affine_key(affine.objective, affine.binning)
    save_entry(
        KNOWLEDGE_CATEGORY,
        key,
        {
            "description": (
                "Stage-camera affine calibration (px → µm) measured by "
                "calibrate_stage_to_camera."
            ),
            **asdict(affine),
        },
    )
    return key


def load_affine(objective: str, binning: int) -> StageCameraAffine | None:
    from microclaw.knowledge_manager import load_knowledge

    entry = (
        load_knowledge()
        .get(KNOWLEDGE_CATEGORY, {})
        .get(affine_key(objective, binning))
    )
    if not entry:
        return None
    try:
        return StageCameraAffine(
            **{k: entry[k] for k in
               ("a", "b", "c", "d", "objective", "binning", "pixel_size_um")}
        )
    except (KeyError, TypeError):
        return None
