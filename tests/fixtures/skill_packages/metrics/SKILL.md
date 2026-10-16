# Photon observer fixture

Run `observe_dataset` with `{}` to follow a growing NDTiff dataset. `mean_intensity`
and slower `peak_intensity` are the mean and maximum of up to 1,024 evenly
spaced sampled pixels per frame (ADU), reported every 0.2 and 0.6 s; `frames_read` counts frames;
`frame_bytes` reports bytes per frame. This observer grants no hardware authority.
For the cadence/frame_bytes demo use `{"flood": true}` for sustained stdout frame_bytes until
writer completion. `{"broken_contiguity": true}` deliberately skips ordinal 1.
`lines` and `scripted_flood` are separate synthetic protocol-test controls.
