# Publishing a skill package

A community skill package is how you ship a MicroClaw skill from your own
repository. You host and sign the release. MicroClaw lists it in a catalog and
installs exactly what you signed. You do not need to send us code or wait for a
MicroClaw release.

> **Status:** protocols `1.0` and `1.1`. Headless and window operations have
> passed their demo-machine gates. No external package has
> been admitted yet. Details
> may change after the first one. This page will be updated when they do.

Problems with your package's behaviour are yours to support: the Extensions
panel's *Report to publisher* button opens your `issues_url`. Problems with
installation, the protocol or the supervisor belong in
[MicroClaw's issues](https://github.com/Micro-Claw/microclaw/issues).

## Pick the kind

| Kind | What it is | Start from |
|---|---|---|
| `markdown` | A `SKILL.md` that teaches MicroClaw's agent a workflow it carries out with the tools MicroClaw already has. There is no code and no environment. | [`Micro-Claw/example-skill-package`](https://github.com/Micro-Claw/example-skill-package), the whole working example including its release workflow |
| `executable` | The same `SKILL.md`, plus a worker: your Python code run out of process over a saved or growing NDTiff dataset, in its own locked environment. | The example package for the repository shape, plus the [reference worker](https://github.com/Micro-Claw/microclaw/blob/main/tests/fixtures/skill_packages/executable/fixture_worker/runner.py) for the protocol |

A skill can only describe what MicroClaw can already do. If your workflow needs
hardware MicroClaw cannot drive, that is a MicroClaw feature request, not a
package. A worker is a read-only observer: it gets data and an output folder,
never a hardware handle, and its results do not steer acquisition.

**A worker runs with the user's permissions.** Nothing sandboxes it. Read only
the input dataset, write only `output_dir`, use no network.
Those are your obligations as a publisher; MicroClaw does not enforce them.

## Repository layout

```
package/                      # becomes the release zip, exactly as-is
  manifest.json
  SKILL.md
  your_worker/                # executable only
    __init__.py
    runner.py
locks/                        # executable only: one file per platform
  pylock.win_amd64.toml
requirements.in               # executable only
.github/workflows/microclaw-release.yml
```

`pack` zips every file under `package/` except names that start with a dot.
That includes `__pycache__/`, so delete it before packing. The release
directory goes on the worker's `PYTHONPATH`, so your runner module lives in
`package/`. Your library comes from the lock as a wheel; do not copy its source
into `package/`.

## manifest.json

You write the source manifest. `pack` adds `artifact` (the release URL) and
`assets` (a SHA-256 for every file), and fills `locks` from `locks/`. Do not
write those three yourself. A Markdown manifest is the
[example package's](https://github.com/Micro-Claw/example-skill-package/blob/main/package/manifest.json).
An executable one adds six fields:

```json
{
  "package_id": "my-analysis",
  "publisher": "my-lab",
  "version": "1.0.0",
  "microclaw": ">=0.1,<2",
  "kind": "executable",
  "license": "MIT",
  "source_url": "https://github.com/my-lab/my-analysis",
  "issues_url": "https://github.com/my-lab/my-analysis/issues",
  "skills": [
    {"name": "my-analysis", "description": "One line, at most 512 characters.", "path": "SKILL.md"}
  ],
  "python": ">=3.12,<3.13",
  "platforms": ["win_amd64"],
  "protocol_version": "1.0",
  "entry_point": {"module": "your_worker.runner"},
  "operations": [
    {"name": "self_check", "input_schema": {"type": "object"}, "output_schema": {"type": "object"}},
    {
      "name": "localize_ndtiff",
      "input_schema": {
        "type": "object",
        "properties": {
          "pixel_size_um": {"type": "number", "minimum": 0.001, "maximum": 100,
                            "description": "The agent reads this. Say what it means and its unit."}
        },
        "additionalProperties": false
      },
      "output_schema": {"type": "object"}
    }
  ]
}
```

These rules are enforced. A violation is refused with the field named.

- **Identifiers.** Publisher, package and skill names match
  `[a-z][a-z0-9]*(?:-[a-z0-9]+)*`. Operation names match `[a-z][a-z0-9_]*`.
  An executable package must declare `self_check`.
- **Operation schemas.** Both schemas of every operation are
  `"type": "object"`. They use a closed subset of JSON Schema 2020-12: `type`,
  `properties`, `required`, `additionalProperties`, `items`, `enum`, `minimum`,
  `maximum`, `minLength`, `maxLength`, `minItems`, `maxItems`, `title`,
  `description` and `default`. A conditional requirement cannot be expressed,
  so check it in the worker and return a `failed` result. The agent reads every
  `description`.
- **Platforms.** `platforms` is any subset of `win_amd64`, `macosx_arm64` and
  `manylinux_x86_64`. Microscope PCs are almost all `win_amd64`; start there.
- **Everything else.** URLs are HTTPS. Manifests are closed mappings: an
  unknown key is refused. Descriptions are a single line. The full list of
  bounds is in the
  [fixtures README](https://github.com/Micro-Claw/microclaw/blob/main/tests/fixtures/skill_packages/README.md).

## Locks (executable only)

Workers run on **Python 3.12**, and installs never build from source. So every
locked dependency needs a cp312 or pure-Python wheel for each declared
platform. Write one lock per platform with uv:

```sh
uv pip compile --no-config --python-version 3.12 --python-platform x86_64-pc-windows-msvc requirements.in -o locks/pylock.win_amd64.toml
uv pip compile --no-config --python-version 3.12 --python-platform aarch64-apple-darwin  requirements.in -o locks/pylock.macosx_arm64.toml
uv pip compile --no-config --python-version 3.12 --python-platform x86_64-manylinux_2_28 requirements.in -o locks/pylock.manylinux_x86_64.toml
```

Commit `locks/`. `pack --locks locks` refuses a missing or extra platform file,
a dependency with no fitting wheel, and any VCS, directory or sdist source. Each
refusal names the file and the package. The pins are yours: MicroClaw installs
exactly what you locked and resolves nothing itself.

## The worker protocol (executable only)

**Copy the
[reference worker](https://github.com/Micro-Claw/microclaw/blob/main/tests/fixtures/skill_packages/executable/fixture_worker/runner.py)
and replace its analysis.** It is stdlib-only and already does the hard parts.
It tails a growing NDTiff index, including a partly written last entry, the
`Full resolution/` subdirectory and rollover files. It also handles every
lifecycle message. The normative validators are `validate_job`,
`validate_notification` and `validate_worker_message` in
[`microclaw/skill_packages.py`](https://github.com/Micro-Claw/microclaw/blob/main/microclaw/skill_packages.py).

**The process.** MicroClaw runs `python -u -m <entry_point.module>` in your
release's own environment. The working directory is `output_dir`, or a
temporary directory for `self_check`. The release directory is on
`PYTHONPATH`, and environment variables starting with `MICROCLAW_` are removed.
The process runs at below-normal priority. It is killed, with its children,
when a headless job ends; window operations use the handoff below.

**Messages.** Every message, in both directions, is one UTF-8 JSON object on
one line. Each is at most 65,536 bytes including the newline. Each carries
`"protocol": "microclaw.analysis.v1"` and the job's `job_id` (32 lowercase hex
characters). Key sets are closed, so do not add keys.

### stdin

The **first line is the job**:

```json
{"protocol":"microclaw.analysis.v1","type":"job","job_id":"…",
 "release":{"publisher":"my-lab","package_id":"my-analysis","version":"1.0.0","artifact_digest":"…"},
 "operation":"localize_ndtiff","parameters":{"pixel_size_um":0.1},
 "input":{"dataset":"C:\\abs\\path\\dataset"},"output_dir":"C:\\abs\\path\\out"}
```

`self_check` carries no `input` and no `output_dir`. MicroClaw runs it when the
package is installed, so it should prove the environment works without a
dataset — for example, import your library and fit a tiny synthetic frame.
`STARTUP_DEADLINE_S` (60 s) is time to the first message: send a `status`
before importing your GUI toolkit. Window startup after that has no deadline
unless the caller sets one; a status is not proof a window appeared.
`self_check` has a 120 s deadline. Analysis operations otherwise have none.

**Later lines are lifecycle notifications.** Keep reading until you finish:

- `{"type":"acquisition","outcome":"completed|failed|cancelled|unterminated","writer":"finished|unknown"}`.
  `unterminated` always comes with `writer: unknown`. The writer's state is not
  known then, so do not claim the input is complete on that basis alone.
- `{"type":"writer","state":"finished"}`: the dataset is final. Read the rest
  and finish.
- `{"type":"cancel"}`: stop, keep what you have, and report `cancelled`.
- End of input on stdin before the result: treat it as a cancel. For window
  operations, EOF after the result is handoff, never a request to close.

For a **saved** dataset, `acquisition completed / writer finished` arrives
straight after the job. For a **live** one, it arrives only when the
acquisition ends; until then, keep reading the index as it grows. Acquisition
never waits for your worker. If you fall behind, the backlog is yours, not the
camera's.

### stdout

Write only protocol messages to stdout. Tracebacks and logs go to **stderr**;
MicroClaw keeps the last 64 KiB of it.

- **Status:** `{"type":"status","message":"…"}`, at most 512 characters on one
  line.
- **Artifact:**
  `{"type":"artifact","artifact":{"path":"locs.h5","sha256":"…","validity":"partial|final"}}`,
  with `path` relative to `output_dir`. Announce a partial file early, so a
  cancelled run still leaves something readable.
- **Result, exactly once:**
  `{"type":"result","state":"succeeded|failed|cancelled","output":{…},"artifacts":[…],"input_complete":true|false}`.
  - A `failed` result adds `"failure":{"message":"…"}`: one line, at most 1,024
    characters.
  - A `self_check` result has no `input_complete` and no artifacts.
  - `output` is your own JSON, for summary numbers.
  - Headless operations exit 0 after the result and print nothing more.

**For headless operations, what counts as a worker failure.** Malformed or
oversized output, a second result, anything printed after the result, exiting without a result, or a
non-zero exit after one. MicroClaw hashes every declared artifact at the end
and never deletes any. If the run did not end in a `succeeded` result, every
artifact is labelled partial.

### Operations that open a window

Declare `"opens_window": true` on the operation under manifest
`"protocol_version": "1.1"`. Never declare it on `self_check`; protocol 1.0
rejects the key, even when false. The job ends at any valid `result`, freeing
the worker slot while the window outlives it. Stdin EOF then means handoff.
MicroClaw keeps ownership of the process tree: do not daemonize or break away.
Windows close when MicroClaw quits, and at most four windows may be open
(including reservations for queued/running operations).

If the user closes the window before the result, send `cancelled` with partial
artifacts and `input_complete: false`, then exit. Never exit without a result.
Every artifact named in the result must be closed before sending it and never
rewritten afterwards. Do not declare continuously updated files; write a
separate snapshot file if you want the job record to vouch for it.

Idle time is not writer completion. Wait for the lifecycle notification that
establishes writer completion, drain remaining frames, then finish. Keep
cancellation responsive during pauses. MicroClaw refuses the operation where
no desktop is visible; name a headless alternative in your `SKILL.md`.

The user sees each open window in the *Community skill packages* panel and can
close it there, which ends your process tree. Your worker runs at below-normal
priority. On MicroClaw's demo machine, a minimal Tk window stayed responsive
beside a 1000-frame burst (event-loop lag p95 about 10 ms, max under 20 ms) and
did not slow the burst. That is one Windows machine and a window that fits
nothing, so it does not predict your fitting load.

There is no publisher-side `check-worker` yet (`R150`, open). The first run of
a window operation under MicroClaw is its first real test; installation's
headless `self_check` does not exercise it.

## SKILL.md

Write it for an agent that already drives the microscope through MicroClaw's
tools. It should say:

- when your package is the right tool, and when it is not;
- what each operation does, and what its parameters mean, with units and
  sensible values;
- how to call each operation:
  - on a saved dataset, through `run_analysis_on_saved_dataset` with
    `adapter="<publisher>/<package_id>:<operation>"` and the `release_digest`
    the agent sees when it loads your skill;
  - on a dataset as it is acquired, through the
    `analysis: {adapter, release_digest, parameters}` argument of
    `run_timelapse`, `run_zstack` and the other acquisition tools;
- what the outputs are, and how the user opens them.

The user confirms each analysis run before it starts. Do not describe hardware
writes or MicroClaw internals, and do not promise anything MicroClaw's tools
cannot do.

## Test the worker in your own CI

Catalog intake checks the signature, the digest, the zip structure, the
manifest and the lock format. **It does not run your worker.** The first time
your code runs under MicroClaw is `self_check`, on a user's machine. So test it
yourself, on each declared platform:

1. Build a fresh Python 3.12 environment from your lock.
2. Start `python -m <your module>` as a subprocess and send a `self_check` job.
   Expect exactly one valid `succeeded` result.
3. Send an analysis job against a small NDTiff fixture **that is still being
   written**: append frames while the worker runs, then send `writer finished`.
   Check the result and the final artifact's hash.
4. Repeat step 3, but send `cancel` partway through. Expect `cancelled` and a
   readable partial artifact.

In that CI job, install MicroClaw at the commit your release workflow pins. Run
every line your worker prints through
`microclaw.skill_packages.validate_worker_message`. A schema mistake then fails
in your CI instead of in a user's session. MicroClaw does not yet ship a
command that runs a worker for you, so these steps are the check for now.

## Sign, release and submit

Check the format locally before you tag. This runs the same checks intake does,
without signing anything:

```sh
python -m microclaw.catalog_intake pack --locks locks --dir package --url https://example.org/x.zip --out x.zip
```

Leave out `--locks locks` for a Markdown package.

Then follow the
[package catalog's README](https://github.com/Micro-Claw/package-catalog#readme).
It has the exact commands, and it is the one place they are kept. In outline:

1. Generate a publisher key with `catalog_intake keygen` and keep it secret.
2. Open an issue on `Micro-Claw/package-catalog` with the printed public entry
   and your publisher name. Your key is admitted into the signed trust policy
   by hand. Until then every submission is refused, so start early.
3. Copy the catalog's `publishing/release.yml` into your repository, as the
   example package does. Pin `MICROCLAW_COMMIT` to the value in the catalog's
   `intake.yml`, and store your key as the secret `MICROCLAW_PUBLISHER_KEY`.
4. Push the tag `v<version>`. The workflow packs, signs and creates a GitHub
   release with `package.zip` and `release.json`.
5. Open a PR on the catalog adding only `release.json`, at the path the
   workflow prints. An accepted PR merges automatically. A refused one gets a
   comment naming the field to fix.

Releases are immutable, so every change needs a new version. To withdraw a
release, use `sign-withdrawal` as the catalog README describes.
