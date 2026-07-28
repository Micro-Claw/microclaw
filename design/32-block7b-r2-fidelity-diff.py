"""Block 7b R2: legacy vs migrated hook output over the same real frames.

The gate document originally said to compare against "the retained pre-Block-7
evidence". Block 7 retained the hook *source* and the manifest -- not their
outputs -- so that baseline may not exist. This is the stronger comparison
anyway: feed one saved dataset's frames to both the legacy class and its
migrated replacement in the same process, and diff the numbers. Identical input
is then guaranteed rather than assumed.

>>> NO HARDWARE IS TOUCHED, AND NO EXPOSURE IS TAKEN. <<<
It reads a dataset that already exists on disk. Micro-Manager need not be
running. The legacy classes are imported directly here, which is fine -- they
are refused by `_resolve_hook` for the acquisition path, not by Python.

Run from the repo root, off-rig or on:

    uv run python design\32-block7b-r2-fidelity-diff.py ^
        --dataset <path to an NDTiff written by an R1 migrated run> ^
        --hook mosaic_cell_counter > r2-cell-counter.txt 2>&1

`--hook` is the fixture stem shared by both versions, one of:
    filament_position_filter | mosaic_cell_counter | mosaic_stitcher | mosaic_stitcher_rot

Pass `--params` to give both versions identical constructor arguments, e.g.
    --params "{\"pixel_size_um\": 0.127, \"n_tiles\": 2}"
Anything the migrated version does not accept is dropped for it alone; that
difference is reported rather than hidden.
"""

import argparse
import itertools
import json
import importlib.util
import inspect
import sys
from pathlib import Path

import numpy as np
from ndstorage import Dataset


sys.stdout.reconfigure(line_buffering=True)

ROOT = Path(__file__).resolve().parents[1]
LEGACY = ROOT / "tests" / "fixtures" / "hooks" / "m5_legacy"
MIGRATED = ROOT / "tests" / "fixtures" / "hooks" / "m5_migrated"


def load_class(path: Path, stem: str):
    spec = importlib.util.spec_from_file_location(f"{stem}_{path.parent.name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in dir(module):
        obj = getattr(module, name)
        if isinstance(obj, type) and (
            hasattr(obj, "analyze_frame") or hasattr(obj, "image_process_fn")
        ) and obj.__module__ == module.__name__:
            return obj
    raise SystemExit(f"No hook class found in {path}")


def construct(cls, params: dict, label: str):
    accepted = set(inspect.signature(cls.__init__).parameters)
    usable = {k: v for k, v in params.items() if k in accepted}
    dropped = sorted(set(params) - set(usable))
    if dropped:
        print(f"  {label}: does not accept {dropped}; constructed without them")
    return cls(**usable)


def iter_frames(dataset_path: str):
    """Yield (coords, image, metadata) for every image actually present."""
    dataset = Dataset(dataset_path)
    axes = dataset.axes
    names = [a for a in ("time", "z", "channel", "position") if a in axes]
    names += [a for a in axes if a not in names]
    if not names:
        yield {}, dataset.read_image(), dataset.read_metadata()
        return
    values = {a: sorted(axes[a]) for a in names}
    for combo in itertools.product(*(values[a] for a in names)):
        coords = dict(zip(names, combo))
        if dataset.has_image(**coords):
            yield coords, dataset.read_image(**coords), dataset.read_metadata(**coords)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--hook", required=True)
    parser.add_argument("--params", default="{}",
                        help="JSON dict passed to BOTH constructors")
    args = parser.parse_args()

    params = json.loads(args.params)
    legacy_path = LEGACY / f"{args.hook}.py"
    migrated_path = MIGRATED / f"{args.hook}.py"
    for path in (legacy_path, migrated_path):
        if not path.exists():
            raise SystemExit(f"Missing fixture: {path}")

    print(f"dataset : {args.dataset}")
    print(f"hook    : {args.hook}")
    print(f"params  : {params}\n")

    legacy = construct(load_class(legacy_path, args.hook), params, "legacy")
    migrated = construct(load_class(migrated_path, args.hook), params, "migrated")

    class DeniedQueue:
        def put(self, *_a, **_k):
            raise RuntimeError("legacy hook tried to use the event queue")

    rows, mismatches, frames = [], 0, 0
    for coords, image, metadata in iter_frames(args.dataset):
        frames += 1
        legacy_returned = legacy.image_process_fn(image, metadata, DeniedQueue())
        legacy_entry = legacy._log[-1] if legacy._log else {}

        result = migrated.analyze_frame(image, metadata)
        migrated_measurements = dict(result.measurements) if result else {}
        actions = [type(a).__name__ for a in (result.actions if result else ())]

        # The legacy entry carries HookBase.where() keys the migrated
        # measurements do not; compare only the science the hook computed.
        legacy_science = {
            k: v for k, v in legacy_entry.items()
            if k not in {"position", "x_um", "y_um", "z_um", "frame", "time_s"}
        }
        migrated_science = {
            k: v for k, v in migrated_measurements.items()
            if k not in {"position", "phase", "frame"}
        }
        shared = sorted(set(legacy_science) & set(migrated_science))
        differing = [
            k for k in shared
            if not values_equal(legacy_science[k], migrated_science[k])
        ]
        only_legacy = sorted(set(legacy_science) - set(migrated_science))
        only_migrated = sorted(set(migrated_science) - set(legacy_science))

        legacy_discarded = legacy_returned is None
        migrated_discarded = "DiscardFrame" in actions
        discard_agrees = legacy_discarded == migrated_discarded
        if differing or not discard_agrees:
            mismatches += 1

        rows.append({
            "coords": coords, "compared": shared, "differing": differing,
            "only_legacy": only_legacy, "only_migrated": only_migrated,
            "legacy_discarded": legacy_discarded,
            "migrated_discarded": migrated_discarded,
            "actions": actions,
        })
        flag = "" if (not differing and discard_agrees) else "   <== MISMATCH"
        print(f"frame {frames} {coords}{flag}")
        for key in shared:
            print(f"    {key}: legacy={legacy_science[key]!r} "
                  f"migrated={migrated_science[key]!r}"
                  + ("   DIFFERS" if key in differing else ""))
        if only_legacy:
            print(f"    only in legacy  : {only_legacy}")
        if only_migrated:
            print(f"    only in migrated: {only_migrated}")
        print(f"    discard: legacy={legacy_discarded} migrated={migrated_discarded}"
              f"  actions={actions}")

    print("\n" + "=" * 68)
    print(f"frames compared : {frames}")
    print(f"mismatching     : {mismatches}")
    if frames == 0:
        print("R2 INCONCLUSIVE - the dataset yielded no images.")
        return 1
    print("R2 PASS" if mismatches == 0 else "R2 FAIL")
    print("No hardware was contacted and no exposure was taken.")
    return 0 if mismatches == 0 else 1


def values_equal(a, b) -> bool:
    if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
        return np.array_equal(np.asarray(a), np.asarray(b))
    if isinstance(a, float) and isinstance(b, float):
        return a == b or (np.isnan(a) and np.isnan(b))
    return a == b


if __name__ == "__main__":
    raise SystemExit(main())
