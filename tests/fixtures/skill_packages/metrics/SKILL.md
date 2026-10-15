# Photon observer fixture

Run `observe_dataset` with `{}` to follow a growing NDTiff dataset. `flux`
and slower `slow` are per-frame mean pixel values; `total` counts frames;
`load` reports bytes per frame. This observer grants no hardware authority.
For the cadence/load demo use `{"flood": true}` for sustained stdout load until
writer completion. `{"broken_contiguity": true}` deliberately skips ordinal 1.
`lines` and `scripted_flood` are separate synthetic protocol-test controls.
