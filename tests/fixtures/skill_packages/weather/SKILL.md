# Thermal observer fixture

Run `observe_dataset` with `{}` to follow a growing NDTiff dataset. The gauge-only
`mean_frame_bytes` fixture metric reports mean bytes read per frame, in bytes; it is
synthetic telemetry derived from data, not a hardware sensor measurement.
The worker finishes after writer completion and final drain. It grants no
hardware authority. `lines` enables a separate scripted protocol-test mode.
