# Retired terminal security-interview heuristics

Block 48e removed `first_launch.py`: schema 3 setup asks only for live stage
bounds and acquisition-confirmation thresholds, so property classification no
longer belongs in mandatory onboarding.

The useful knowledge in the deleted classifier was its conservative evidence
model, not its large device-name table:

- Micro-Manager metadata is a proposal, never proof of a property's physical
  meaning. Writable, numeric, allowed-value, and driver-range observations must
  still be confirmed by an operator before becoming policy.
- Current positions and current property values are observations, not safe
  limits. Missing units, ambiguous names, non-finite ranges, and incomplete
  metadata must remain unresolved rather than being guessed.
- Device and property names can suggest emission, exposure, gain, temperature,
  position, or categorical state, but labels vary by adapter and rig. Such
  matches are questions for the operator, not generic runtime facts.
- Read-only enumeration can still cause driver activity during connection and
  device initialization. Setup must describe that hardware contact honestly.

The retired implementation also carried a long, schema-2-specific mapping from
name fragments to policy sections and generated optional authorization,
illumination, camera, and plugin declarations. Preserving that executable table
would anchor new setup to the format deliberately replaced by schema 3. Future
optional-policy tooling should start from the principles above and fresh live
evidence, not resurrect the old classifier.
