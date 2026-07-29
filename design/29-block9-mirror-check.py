"""Is the mosaic's rendering of a tile a pure rotation of the raw frame, or a mirror?

Overlap residuals cannot see a transform applied identically to tile placement AND
tile content -- that is what R5 was for. But a mirror IS visible without any
graticule: render one tile, and compare it against all eight dihedral transforms
of the raw camera frame. Exactly one should match. If the winner is a reflection,
the mosaic is mirrored relative to the camera.

A random bead constellation is a perfectly good fingerprint for this. What it
cannot settle is whether MM's stage frame is physically right-handed -- but that
is a rig/MM property, identical for MM's own display, not a Block 9 property.
"""
import json, sys
from pathlib import Path
import numpy as np
from ndstorage import Dataset

sys.path.insert(0, "/Users/zachcm/Code/microclaw")
from microclaw.calibration import StageCameraAffine, canonical_affine_payload
from microclaw.dataset_mosaic import MosaicGeometry, assemble_stage_coordinate_mosaic

DIHEDRAL = {
    "identity":            lambda a: a,
    "rot90 CCW":           lambda a: np.rot90(a, 1),
    "rot180":              lambda a: np.rot90(a, 2),
    "rot90 CW":            lambda a: np.rot90(a, 3),
    "MIRROR lr":           lambda a: np.fliplr(a),
    "MIRROR lr + rot90CCW": lambda a: np.rot90(np.fliplr(a), 1),
    "MIRROR lr + rot180":  lambda a: np.rot90(np.fliplr(a), 2),
    "MIRROR lr + rot90CW": lambda a: np.rot90(np.fliplr(a), 3),
}

def ncc(a, b):
    a = a.astype(float); b = b.astype(float)
    if a.shape != b.shape:
        return float("nan")
    a = a - a.mean(); b = b - b.mean()
    d = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / d) if d > 0 else float("nan")

base = sys.argv[1] if len(sys.argv) > 1 else "."
payload = json.loads(Path(sys.argv[2] if len(sys.argv) > 2 else "res1.json").read_text())
affine = StageCameraAffine(**canonical_affine_payload(payload["payload"]))
geom = MosaicGeometry(affine, affine.pixel_size_um)
print(f"affine pixel->stage 2x2 : [[{affine.a}, {affine.b}], [{affine.c}, {affine.d}]]")
print(f"determinant             : {affine.a*affine.d - affine.b*affine.c:+.6f} "
      f"(positive => the affine itself encodes NO reflection)\n")

d = Dataset(base)
pos = sorted(d.axes["position"], key=str)[0]
raw = d.read_image(position=pos, time=0)

# Render this tile alone, on its own, so the footprint is the tile and nothing else.
result = assemble_stage_coordinate_mosaic(
    [(raw, 0.0, 0.0)], geom)
placed = result["mosaic"]
print(f"tile                    : {pos}")
print(f"raw frame shape         : {raw.shape}")
print(f"rendered footprint      : {placed.shape}\n")

print(f"{'candidate':24} {'shape':12} {'NCC vs rendering':>18}")
scores = {}
for name, fn in DIHEDRAL.items():
    cand = fn(raw)
    s = ncc(cand, placed)
    scores[name] = s
    flag = "" if np.isnan(s) else ("   <== MATCH" if s > 0.99 else "")
    print(f"{name:24} {str(cand.shape):12} {s:18.4f}{flag}")

best = max((k for k in scores if not np.isnan(scores[k])), key=lambda k: scores[k])
print(f"\nbest match: {best}  (NCC {scores[best]:.4f})")
print("VERDICT:", "MIRRORED -- the rendering is a reflection of the camera frame"
      if best.startswith("MIRROR") else
      "NO MIRROR -- the rendering is a pure rotation of the camera frame")
