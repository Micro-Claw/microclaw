"""Stage↔camera affine calibration (design/14 §8).

In the amr_test session the agent had no pixel size and no camera↔stage axis
mapping, so it navigated by nudging the stage and squinting at thumbnails —
three moves produced three mutually inconsistent conclusions about the axis
signs. The affine turns every "nudge and squint" loop into arithmetic.
"""
from __future__ import annotations
import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np

KNOWLEDGE_CATEGORY = "devices"
AFFINE_FIELDS = ("a", "b", "c", "d", "objective", "binning", "pixel_size_um")


class CalibrationResolutionError(ValueError):
    """The saved data does not identify a trustworthy calibration."""


@dataclass
class StageCameraAffine:
    """Maps an image-pixel offset from the field centre → the RELATIVE STAGE
    MOVE (µm) that brings that point to the centre.

        [dx_um]   [a b] [dx_px]
        [dy_um] = [c d] [dy_px]

    It is a **correction** vector, not a displacement: feed it where a feature
    is now, and it returns where to send the stage. The earlier wording
    ("image-pixel displacement → stage-µm displacement") was ambiguous and cost
    a systematically wrong-way `center_feature` — see design/64.

    This is Micro-Manager's own `PixelSizeAffine` convention, so an MM affine is
    adopted without a sign change. Derived from MM's consumer path rather than
    assumed: `CenterAndDragListener` double-click-to-centre passes the NEGATIVE
    of the clicked offset (`0.5 * width - center.x`) to
    `XYNavigator.moveSampleOnDisplayPixels`, and `XYNavigator.toStageSpace`
    applies the affine and then negates both axes ("not sure why, but for the
    stage movement to be correct, we need to invert both axes"). The two
    negations cancel: MM's stage move to centre a feature at offset r is +A·r,
    which is exactly `px_to_um(r)`.

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
    """Solve the 2×2 affine from two measured registration vectors.

    shift_*_px are (row, col) = (dy_px, dx_px) as returned by
    skimage.registration.phase_cross_correlation for a +step_um stage move along
    stage-X and stage-Y respectively. That is the vector which registers the
    moved frame back onto the reference — the NEGATIVE of the scene's observed
    displacement, not the displacement itself. Inverting the stacked pair
    therefore yields the correction map documented on StageCameraAffine: pixel
    offset from centre → the stage move that centres it. Do not "fix" this by
    negating here; design/64 verified the composition numerically and against
    Micro-Manager's own affine consumers.

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
    """Return the mutable current-alias key (not a durable identity)."""
    slug = re.sub(r"\W+", "_", objective).strip("_") or "default"
    return f"stage_camera_affine_{slug}_bin{binning}"


def canonical_affine_payload(affine: StageCameraAffine | dict) -> dict:
    source = asdict(affine) if isinstance(affine, StageCameraAffine) else affine
    payload = {field: source[field] for field in AFFINE_FIELDS}
    payload["binning"] = int(payload["binning"])
    for field in ("a", "b", "c", "d", "pixel_size_um"):
        payload[field] = float(payload[field])
    # json.dumps below rejects these too, but naming the failure is clearer.
    if not all(math.isfinite(payload[f]) for f in ("a", "b", "c", "d", "pixel_size_um")):
        raise ValueError("Affine payload contains a non-finite number")
    return payload


def serialize_affine_payload(affine: StageCameraAffine | dict) -> str:
    """Canonical UTF-8 JSON input for calibration SHA-256 identities."""
    return json.dumps(
        canonical_affine_payload(affine), sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    )


def affine_payload_hash(affine: StageCameraAffine | dict) -> str:
    return hashlib.sha256(serialize_affine_payload(affine).encode("utf-8")).hexdigest()


def affine_version_key(affine: StageCameraAffine | dict) -> str:
    payload = canonical_affine_payload(affine)
    return f"{affine_key(payload['objective'], payload['binning'])}_sha256_{affine_payload_hash(payload)}"


MM_AFFINE_SOURCE = "micro_manager_pixel_size_affine"
MEASURED_AFFINE_SOURCE = "calibrate_stage_to_camera"


def _identity_fields(camera_device, camera_model, roi, source=None) -> dict:
    return {
        "camera_device": None if camera_device is None else str(camera_device),
        "camera_model": None if camera_model is None else str(camera_model),
        "roi": None if roi is None else [int(value) for value in roi],
        # Which routine measured this. Entries written before design/64 carry
        # None, and None means "ours": calibrate_stage_to_camera was the only
        # writer, so an unlabelled entry must never be treated as MM's and
        # silently replaced by it.
        "source": None if source is None else str(source),
    }


def save_affine(
    affine: StageCameraAffine,
    *,
    camera_device: str | None = None,
    camera_model: str | None = None,
    roi: list[int] | tuple[int, int, int, int] | None = None,
    source: str | None = None,
) -> str:
    """Persist under the knowledge base's devices category.

    Written via save_entry directly, NOT the confirm-gated save_knowledge
    tool: the gate exists for model-authored free text, and this value is
    deterministic measured numbers.
    """
    from microclaw.knowledge_manager import save_entry

    alias = affine_key(affine.objective, affine.binning)
    version = affine_version_key(affine)
    payload = canonical_affine_payload(affine)
    identity = _identity_fields(camera_device, camera_model, roi, source)
    save_entry(
        KNOWLEDGE_CATEGORY,
        version,
        {
            "description": (
                "Stage-camera affine calibration (px → µm) measured by "
                "calibrate_stage_to_camera."
            ),
            "immutable": True,
            "payload_sha256": affine_payload_hash(payload),
            "payload": payload,
            **identity,
        },
    )
    save_entry(
        KNOWLEDGE_CATEGORY, alias,
        {"description": "Mutable current stage-camera affine alias.",
         "current_version": version, **identity},
    )
    return alias


def load_affine_version(key: str) -> tuple[StageCameraAffine, dict]:
    from microclaw.knowledge_manager import load_knowledge

    entry = load_knowledge().get(KNOWLEDGE_CATEGORY, {}).get(key)
    if not isinstance(entry, dict) or not entry.get("immutable"):
        raise CalibrationResolutionError(f"No immutable calibration version: {key}")
    try:
        payload = canonical_affine_payload(entry.get("payload", {}))
    except (KeyError, TypeError, ValueError) as error:
        raise CalibrationResolutionError(
            f"Immutable calibration has an invalid payload: {key}"
        ) from error
    actual_hash = affine_payload_hash(payload)
    expected_suffix = f"_sha256_{actual_hash}"
    if entry.get("payload_sha256") != actual_hash or not key.endswith(expected_suffix):
        raise CalibrationResolutionError(
            f"Immutable calibration payload hash does not match key: {key}"
        )
    affine = StageCameraAffine(**payload)
    return affine, {
        "version_key": key,
        "payload": payload,
        "payload_sha256": actual_hash,
        **_identity_fields(entry.get("camera_device"), entry.get("camera_model"),
                           entry.get("roi"), entry.get("source")),
    }


def load_affine_entry(
    objective: str, binning: int,
) -> tuple[StageCameraAffine | None, str | None]:
    """The cached affine for this optical path, and the source that wrote it.

    The source decides whether Micro-Manager's own PixelSizeAffine may replace
    it (design/64): MM is the authority until the operator deliberately measures
    one with calibrate_stage_to_camera, and that measurement then stands.
    """
    from microclaw.knowledge_manager import load_knowledge

    entry = (
        load_knowledge()
        .get(KNOWLEDGE_CATEGORY, {})
        .get(affine_key(objective, binning))
    )
    if not entry:
        return None, None
    if isinstance(entry, dict) and entry.get("current_version"):
        affine, stored = load_affine_version(str(entry["current_version"]))
        return affine, entry.get("source") or stored.get("source")
    # Legacy aliases stored the mutable payload inline. Pin one immutable copy
    # before use, then replace the alias with a pointer to it.
    try:
        affine = StageCameraAffine(**{k: entry[k] for k in AFFINE_FIELDS})
    except (KeyError, TypeError, ValueError):
        return None, None
    source = entry.get("source") if isinstance(entry, dict) else None
    save_affine(
        affine, camera_device=entry.get("camera_device"),
        camera_model=entry.get("camera_model"), roi=entry.get("roi"), source=source,
    )
    return affine, source


def load_affine(objective: str, binning: int) -> StageCameraAffine | None:
    return load_affine_entry(objective, binning)[0]


def mm_affine_is_orientation_only(affine: StageCameraAffine) -> bool:
    """True when this affine carries MM's *orientation* and an unmeasured scale.

    MM's Manual-Simple calibrator never measures a scale: it applies the pixel
    size you already had to BOTH axes and snaps orientation to one of eight
    axis-aligned cases (design/29, from `javap` on MMJ_.jar). Its output is
    therefore always `scalar × signed permutation` — exact zeros, zero shear,
    identical column scales. On M2 the true optics were anisotropic by 14% and
    the two columns were 3.5% and 15.7% off.

    A closed loop like center_feature converges through a scale error anyway
    (it just takes more iterations); a mosaic does not. So this is reported, not
    refused — the operator decides whether to run calibrate_stage_to_camera.
    """
    a, b, c, d = affine.a, affine.b, affine.c, affine.d
    axis_aligned = (b == 0.0 and c == 0.0) or (a == 0.0 and d == 0.0)
    scales = (math.hypot(a, c), math.hypot(b, d))
    return axis_aligned and math.isclose(scales[0], scales[1], rel_tol=1e-9)


def _metadata_value(metadata: dict, *keys: str):
    for key in keys:
        if key in metadata and metadata[key] not in (None, ""):
            return metadata[key]
    return None


def _parse_roi(raw: Any) -> list[int] | None:
    if raw is None or raw == "":
        return None
    values = re.split(r"[-,;]", raw) if isinstance(raw, str) else raw
    if not isinstance(values, (list, tuple)) or len(values) != 4:
        raise CalibrationResolutionError(
            f"Invalid acquisition ROI {raw!r}; expected x-y-width-height"
        )
    try:
        return [int(value) for value in values]
    except (TypeError, ValueError) as error:
        raise CalibrationResolutionError(
            f"Invalid acquisition ROI {raw!r}; expected four integers"
        ) from error


def parse_mm_pixel_size_affine(raw: Any, *, objective: str, binning: int) -> StageCameraAffine | None:
    """Decode either MM affine representation, with one sentinel policy.

    MMCore exposes six row-major values separated by semicolons; NDTiff summary
    metadata stores the four linear terms separated by underscores.
    """
    if raw is None or str(raw).strip().lower() == "undefined":
        return None
    try:
        text = str(raw)
        delimiter = ";" if ";" in text else "_"
        values = [float(value.strip()) for value in text.split(delimiter)]
    except (TypeError, ValueError):
        return None
    if len(values) == 6:
        m00, m01, _m02, m10, m11, _m12 = values
    elif len(values) == 4:
        # java.awt.geom.AffineTransform.getMatrix stores its flat array in
        # column-major order: {m00, m10, m01, m11[, m02, m12]}.
        m00, m10, m01, m11 = values
    else:
        return None
    if not all(math.isfinite(value) for value in values):
        return None
    linear = (m00, m01, m10, m11)
    if all(value == 0.0 for value in values):
        return None
    if np.allclose(np.array([[m00, m01], [m10, m11]]), np.eye(2), rtol=0, atol=1e-12):
        return None
    determinant = m00 * m11 - m01 * m10
    if math.isclose(determinant, 0.0, abs_tol=1e-12):
        return None
    return StageCameraAffine(
        *linear, objective=str(objective), binning=int(binning),
        pixel_size_um=math.sqrt(abs(determinant)),
    )


def _complete_identity(identity: dict, source_kind: str, source_reference: dict) -> dict:
    missing = [
        field for field in ("camera_device", "camera_model", "roi")
        if identity.get(field) is None
    ]
    if missing:
        raise CalibrationResolutionError(
            "Calibration identity is incomplete; missing " + ", ".join(missing)
        )
    roi = identity["roi"]
    if not isinstance(roi, (list, tuple)) or len(roi) != 4:
        raise CalibrationResolutionError("Calibration ROI must be [x, y, width, height]")
    return {
        "source_kind": source_kind,
        "source_reference": source_reference,
        **identity,
    }


def _identity_for_affine(
    affine: StageCameraAffine, *, source_kind: str, source_reference: dict,
    camera_device: Any, camera_model: Any, roi: Any, version_key: str | None = None,
) -> dict:
    payload = canonical_affine_payload(affine)
    identity = {
        "camera_device": None if camera_device is None else str(camera_device),
        "camera_model": None if camera_model is None else str(camera_model),
        "roi": None if roi is None else [int(value) for value in roi],
        "objective": payload["objective"], "binning": payload["binning"],
        "payload": payload, "payload_sha256": affine_payload_hash(payload),
    }
    if version_key is not None:
        identity["version_key"] = version_key
    return _complete_identity(identity, source_kind, source_reference)


def _acquisition_calibration(
    dataset, fixed_axes: dict,
) -> tuple[tuple[StageCameraAffine, dict] | None, str | None]:
    from microclaw.tools import _iter_present_coords

    summary = getattr(dataset, "summary_metadata", {}) or {}
    summary_affine = _metadata_value(summary, "AffineTransform")
    frame_records = [
        (coords, dataset.read_metadata(**coords))
        for coords in _iter_present_coords(dataset, fixed_axes)
    ]
    per_frame_affines = [
        _metadata_value(metadata, "PixelSizeAffine", "PixelSizeAffineString")
        for _coords, metadata in frame_records
    ]
    # Summary metadata is a fallback for MM datasets that omit the affine from
    # every frame. It must never mask a present or inconsistent per-frame value.
    use_summary = summary_affine is not None and all(
        raw is None for raw in per_frame_affines
    )
    candidates = []
    frame_count = 0
    rois = []
    for (coords, metadata), per_frame_raw in zip(frame_records, per_frame_affines):
        frame_count += 1
        raw = summary_affine if use_summary else per_frame_raw
        affine_source = "AffineTransform" if use_summary else "PixelSizeAffine"
        objective = _metadata_value(
            metadata, "Objective", "ObjectiveLabel", "PixelSizeConfig", "PixelSizeConfigName"
        )
        binning = _metadata_value(metadata, "Binning", "Camera-Binning")
        camera_device = _metadata_value(metadata, "Camera", "CameraDevice", "Core-Camera")
        camera_model = _metadata_value(
            metadata, "CameraModel", "CameraDeviceName", "CameraAdapter",
            f"{camera_device}-Camera" if camera_device is not None else "",
            f"{camera_device}-CameraName" if camera_device is not None else "",
        )
        roi = _parse_roi(_metadata_value(metadata, "ROI", "Roi", "CameraROI"))
        rois.append(None if roi is None else tuple(roi))
        try:
            affine = parse_mm_pixel_size_affine(
                raw, objective=str(objective) if objective is not None else "",
                binning=int(str(binning).split("x")[0]),
            )
        except (TypeError, ValueError):
            affine = None
        candidates.append((affine, objective, camera_device, camera_model, roi, coords,
                           affine_source))
    known_rois = {roi for roi in rois if roi is not None}
    if len(known_rois) > 1 or (known_rois and any(roi is None for roi in rois)):
        raise CalibrationResolutionError(
            "Dataset ROI changes or becomes unknown between frames; "
            "one relative calibration is unsafe"
        )
    usable = [item for item in candidates if item[0] is not None]
    if usable and len(usable) != frame_count:
        raise CalibrationResolutionError("Dataset PixelSizeAffine changes between frames")
    if not usable:
        return None, "PixelSizeAffine is absent, a sentinel, or invalid"
    first = usable[0]
    first_linear = (first[0].a, first[0].b, first[0].c, first[0].d)
    if any((item[0].a, item[0].b, item[0].c, item[0].d) != first_linear
           for item in usable[1:]):
        raise CalibrationResolutionError("Dataset PixelSizeAffine changes between frames")
    first_context = (first[1], first[0].binning, first[2], first[3])
    if any((item[1], item[0].binning, item[2], item[3]) != first_context
           for item in usable[1:]):
        raise CalibrationResolutionError(
            "Dataset calibration identity changes between frames"
        )
    affine, objective, camera_device, camera_model, roi, coords, _source = first
    missing = [name for name, value in (
        ("binning", affine.binning),
        ("camera_device", camera_device), ("camera_model", camera_model), ("roi", roi),
    ) if value is None or value == ""]
    if missing:
        return None, "acquisition calibration identity is incomplete; missing " + ", ".join(missing)
    affine.objective = "" if objective is None else str(objective)
    identity = _identity_for_affine(
        affine, source_kind="acquisition_recorded",
        source_reference={"metadata_key": first[6], "coordinates": coords},
        camera_device=camera_device, camera_model=camera_model, roi=roi,
    )
    if objective is None or objective == "":
        identity["objective_unrecorded"] = True
        identity["objective_unrecorded_reason"] = (
            "The dataset records affine geometry and binning but no objective label; "
            "verify the optical path before reusing this calibration outside this dataset."
        )
    return (affine, identity), None


def _read_artifact(path: str, guard) -> dict:
    resolved = guard.resolve_readable_path(path) if guard is not None else path
    try:
        data = json.loads(Path(resolved).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CalibrationResolutionError(f"Cannot read calibration artifact: {error}") from error
    data = data.get("manifest_payload", data)
    identity = data.get("calibration_identity", data)
    if not isinstance(identity, dict):
        raise CalibrationResolutionError("Calibration artifact has no calibration identity")
    return identity


def _config_mismatches_for_message(ctrl) -> list[dict] | str:
    """The config walk, for interpolation into another error's message.

    `_config_mismatches` deliberately no longer swallows an enumeration failure:
    an empty list is a statement, not a silence (design/59, demo gate round 1).
    But the two callers below put it inside a `raise`, where an exception would
    replace the diagnosis the operator needed with the failure that produced it.
    """
    try:
        return _config_mismatches(ctrl)
    except Exception as exc:
        return f"unavailable ({type(exc).__name__}: {exc})"


def _config_mismatches(
    ctrl, *, cached: bool = False, read_live: bool = True,
    live_values: dict[tuple[str, str], str] | None = None,
) -> list[dict]:
    if ctrl is None:
        return []
    static_results = None
    if cached:
        static_results = getattr(ctrl, "_pixel_size_config_inventory", None)
    if static_results is None:
        static_results = []
        from microclaw.authorization import _strings
        configs = _strings(ctrl.core.get_available_pixel_size_configs())
        for config in configs:
            rules = []
            try:
                data = ctrl.core.get_pixel_size_config_data(config)
                for index in range(int(data.size())):
                    setting = data.get_setting(index)
                    rules.append({
                        "device": str(setting.get_device_label()),
                        "property": str(setting.get_property_name()),
                        "expected": str(setting.get_property_value()),
                    })
            except Exception as error:
                rules.append({"error": str(error)})
            try:
                pixel_size_um = float(ctrl.core.get_pixel_size_um_by_id(config))
            except Exception:
                pixel_size_um = None
            try:
                raw_affine = ";".join(_strings(
                    ctrl.core.get_pixel_size_affine_by_id(config)
                ))
                affine_verdict = (
                    "usable" if parse_mm_pixel_size_affine(
                        raw_affine, objective=str(config), binning=1
                    ) is not None else "sentinel_or_invalid"
                )
            except Exception:
                affine_verdict = "unavailable"
            static_results.append({
                "config": str(config), "pixel_size_um": pixel_size_um,
                "affine_verdict": affine_verdict, "rules": rules,
            })
        if cached:
            ctrl._pixel_size_config_inventory = static_results
    if not read_live:
        return static_results

    results = []
    shared = live_values or {}
    for config in static_results:
        rules = []
        for retained in config["rules"]:
            rule = dict(retained)
            if "device" not in rule:
                rule["matches"] = False
            else:
                pair = (rule["device"], rule["property"])
                if pair in shared:
                    live = shared[pair]
                else:
                    try:
                        live = str(ctrl.core.get_property(*pair))
                    except Exception as error:
                        live = f"ERROR: {error}"
                    shared[pair] = live
                rule["live"] = live
                rule["matches"] = live == rule["expected"]
            rules.append(rule)
        results.append({
            **{key: value for key, value in config.items() if key != "rules"},
            "rules": rules,
            "would_activate": all(rule["matches"] for rule in rules),
        })
    return results


def resolve_calibration(
    dataset,
    calibration_ref: dict | None,
    *,
    fixed_axes: dict | None = None,
    ctrl=None,
    guard=None,
) -> tuple[StageCameraAffine, dict]:
    """Resolve one trustworthy historical calibration without guessing."""
    # An explicit reference selects the result below. We still audit recorded
    # metadata first: incompleteness is annotated on that explicit identity,
    # while contradictory per-frame geometry remains a hard refusal.
    acquisition, acquisition_fallthrough = _acquisition_calibration(
        dataset, fixed_axes or {}
    )
    if calibration_ref is None:
        if acquisition is not None:
            return acquisition
        configs = _config_mismatches_for_message(ctrl)
        detail = f" Available pixel-size configs: {configs}" if configs else ""
        raise CalibrationResolutionError(
            f"Dataset does not record a usable calibration ({acquisition_fallthrough}); "
            "supply an artifact, "
            "immutable version, or explicitly confirmed current calibration." + detail
        )
    accepted = (
        "{'kind':'artifact','path':'...'}, "
        "{'kind':'knowledge_version','key':'...'}, "
        "{'kind':'confirmed_current','objective':'...','binning':1}, or "
        "{'kind':'legacy_derived','pixel_size_um':...,'objective':'...','binning':1}"
    )
    if not isinstance(calibration_ref, dict) or "kind" not in calibration_ref:
        raise CalibrationResolutionError(
            "calibration_ref must contain a 'kind' discriminator. Accepted shapes: "
            + accepted
        )
    kind = calibration_ref["kind"]

    def with_fallthrough(identity: dict) -> dict:
        if acquisition_fallthrough is not None:
            identity["acquisition_fallthrough_reason"] = acquisition_fallthrough
        elif acquisition is not None:
            identity["acquisition_recorded_not_used_reason"] = (
                "explicit calibration_ref supplied"
            )
        return identity

    if kind == "artifact":
        identity = _read_artifact(str(calibration_ref.get("path", "")), guard)
        payload = identity.get("payload")
        try:
            affine = StageCameraAffine(**canonical_affine_payload(payload))
        except (KeyError, TypeError, ValueError) as error:
            raise CalibrationResolutionError(
                "Calibration artifact has an invalid affine payload"
            ) from error
        if identity.get("payload_sha256") != affine_payload_hash(affine):
            raise CalibrationResolutionError("Calibration artifact payload hash mismatch")
        return affine, with_fallthrough(_identity_for_affine(
            affine, source_kind="artifact", source_reference={"path": calibration_ref["path"]},
            camera_device=identity.get("camera_device"), camera_model=identity.get("camera_model"),
            roi=identity.get("roi"), version_key=identity.get("version_key"),
        ))
    if kind in ("knowledge_version", "confirmed_current"):
        if kind == "knowledge_version":
            version = str(calibration_ref.get("key", ""))
        else:
            objective = str(calibration_ref.get("objective", ""))
            binning = int(calibration_ref.get("binning", 0))
            from microclaw.knowledge_manager import load_knowledge
            alias = load_knowledge().get(KNOWLEDGE_CATEGORY, {}).get(affine_key(objective, binning))
            if isinstance(alias, dict) and not alias.get("current_version"):
                load_affine(objective, binning)  # migrate the legacy alias first
                alias = load_knowledge().get(KNOWLEDGE_CATEGORY, {}).get(affine_key(objective, binning))
            if not isinstance(alias, dict) or not alias.get("current_version"):
                raise CalibrationResolutionError(
                    f"No current calibration for {objective!r} binning {binning}. "
                    f"Available pixel-size configs: {_config_mismatches_for_message(ctrl)}"
                )
            version = str(alias["current_version"])
        affine, stored = load_affine_version(version)
        return affine, with_fallthrough(_identity_for_affine(
            affine, source_kind=kind, source_reference=dict(calibration_ref),
            camera_device=stored.get("camera_device"), camera_model=stored.get("camera_model"),
            roi=stored.get("roi"), version_key=version,
        ))
    if kind == "legacy_derived":
        try:
            pixel_size = float(calibration_ref["pixel_size_um"])
            turns = int(calibration_ref.get("rot90_k", 0)) % 4
            matrix = np.rot90(np.eye(2), -turns) * pixel_size
            if calibration_ref.get("flip_x"):
                matrix[:, 0] *= -1
            if calibration_ref.get("flip_y"):
                matrix[:, 1] *= -1
            affine = StageCameraAffine(
                float(matrix[0, 0]), float(matrix[0, 1]),
                float(matrix[1, 0]), float(matrix[1, 1]),
                str(calibration_ref["objective"]), int(calibration_ref["binning"]), pixel_size,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise CalibrationResolutionError(f"Invalid legacy_derived reference: {error}") from error
        return affine, with_fallthrough(_identity_for_affine(
            affine, source_kind=kind, source_reference=dict(calibration_ref),
            camera_device=calibration_ref.get("camera_device"),
            camera_model=calibration_ref.get("camera_model"), roi=calibration_ref.get("roi"),
        ))
    raise CalibrationResolutionError(
        f"Unknown calibration_ref kind: {kind!r}. Accepted shapes: {accepted}"
    )
