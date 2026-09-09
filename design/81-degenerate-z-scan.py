"""What the installed acquisition engine does with a degenerate Z sweep.

Reproduces design/81 F1's table against the INSTALLED
pycromanager.multi_d_acquisition_events, not against our reading of it. Run it
before implementing D1 and again after, and whenever pycro-manager is upgraded.
D1 validates the actual generated events; the mirror below is a historical
numerical diagnostic, not a production implementation or an equivalence proof.

    .venv/bin/python design/81-degenerate-z-scan.py

A negative step is NOT empty in general: the zero-event cases are the ones
where the step points away from z_end. The engine honours a descending sweep,
so D1's `z_step > 0` requirement is a Microclaw API choice, not a restatement
of an engine limit -- which is why the descending rows are here.

No hardware, no bridge, no Micro-Manager. Pure event construction.
"""

from pycromanager import multi_d_acquisition_events


CASES = [
    (0, 0, 1, "the incident: one plane at ABSOLUTE Z=0"),
    (65.183, 65.183, 1, "one plane; a 'stack' with no motion"),
    (0, 0, 0, "the z key vanishes: any([0,0,0]) is False"),
    (65.0, 65.0, 0, "raises inside numpy, not a typed refusal"),
    (70.0, 60.0, 1.0, "step points away from z_end: no events at all"),
    (60.0, 70.0, -1.0, "step points away from z_end: no events at all"),
    (70.0, 60.0, -1.0, "DESCENDING: a negative step the engine honours"),
    (70.0, 60.0, -2.5, "descending, fractional: also honoured"),
    (60.0, 70.0, 0.0, "raises inside numpy"),
    (60.0, 60.5, 1.0, "OVERSHOOT: visits 61.0, one step beyond z_end"),
    (0.0, 10.0, 1.0, "control: a real stack"),
]


def main() -> None:
    print(f"{'z_start':>9} {'z_end':>8} {'z_step':>7}  {'events':>6}  "
          f"{'has z key':>9}  planes / error")
    print("-" * 100)
    for z_start, z_end, z_step, note in CASES:
        try:
            events = multi_d_acquisition_events(
                z_start=z_start, z_end=z_end, z_step=z_step
            )
        except Exception as exc:
            outcome = f"{type(exc).__name__}: {exc}"
            count, has_z = "-", "-"
        else:
            count = len(events)
            has_z = str(bool(events) and "z" in events[0])
            planes = [float(e["z"]) for e in events if "z" in e]
            outcome = f"{planes}" if planes else "[]"
        print(f"{z_start:>9} {z_end:>8} {z_step:>7}  {count:>6}  "
              f"{has_z:>9}  {outcome}")
        print(f"{'':>36}  {note}")


MIRROR_CASES = [
    (0, 0, 1), (65.183, 65.183, 1), (60, 60.5, 1), (0, 10, 1), (0, 0.3, 0.1),
    (55.183, 75.183, 2.5), (0, 20, 0.5), (1.1, 2.2, 0.1), (-5, 5, 0.7),
    (0, 1e-9, 1), (0, 0.999999, 1), (0, 1.000001, 1), (65, 85, 0.5),
    (0, 100, 3), (2.5, 7.5, 0.25),
]


def mirror() -> int:
    """Compare the original proposal's arithmetic on a small set of examples.

    Agreement within 1e-9 on these inputs does not prove equivalence to numpy
    for other values. Production validation must inspect generated event Zs.
    """
    import math

    bad = 0
    for z_start, z_end, z_step in MIRROR_CASES:
        engine = [float(e["z"]) for e in multi_d_acquisition_events(
            z_start=z_start, z_end=z_end, z_step=z_step) if "z" in e]
        count = math.ceil((z_end + z_step - z_start) / z_step)
        mine = [z_start + i * z_step for i in range(count)]
        if len(engine) != count or any(
            abs(a - b) > 1e-9 for a, b in zip(engine, mine)
        ):
            bad += 1
            print(f"  MISMATCH {(z_start, z_end, z_step)}: engine "
                  f"{len(engine)} planes, helper {count}")
    print(f"\napproximate mirror diagnostic: {len(MIRROR_CASES) - bad} of "
          f"{len(MIRROR_CASES)} shapes agree")
    return bad


if __name__ == "__main__":
    main()
    raise SystemExit(1 if mirror() else 0)
