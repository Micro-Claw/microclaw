# Using Microclaw

You describe what you want in plain language; Microclaw plans it, calls
Micro-Manager tools, and reports what actually happened. This page covers what a
session is, what leaves the machine, the domain knowledge the agent carries, how
analysis is attached to an acquisition, and how to walk away with a plain
Pycro-Manager script.

For the interface itself — the composer, confirmations, the API-key chip, remote
mode — see [The browser GUI](browser-gui.md).

## A session

Microclaw attaches to a Micro-Manager instance you are already running. It does
not take the machine over: it opens mid-session and more than once, assumes it is
not the only client, and takes charge of hardware only when you ask it to. There
are no exclusive locks and no "Microclaw session" state on the microscope, so a
second launch does not trip over the first.

The server holds **one microscope and one conversation**. A turn takes the
session lock, so a second prompt is refused rather than interleaving tool calls
on the hardware.

Three things gate what a turn can do:

- **Security bounds.** Your reviewed `safety_config.yaml` is enforced before
  every hardware call, and the agent cannot override it. See
  [Safety configuration](safety-config.md).
- **Confirmations.** Anything irreversible or dose-bearing stops and asks —
  enabling illumination, exceeding an acquisition threshold, clearing
  Micro-Manager's position list, running MMStudio's MDA, writing a config.
  Illumination and acquisition-threshold confirmations can be granted for the
  rest of the session; the grants are listed in the page and individually
  revocable. Confirmations that gate Microclaw's own configuration are always
  asked.
- **Stop after the current step**, which asks the running turn to stop at the
  next round or tool boundary. It is cooperative: a tool call that is already
  blocking in Java cannot be interrupted, and it does not shutter lasers or halt
  the stage.

Everything is written to the transcript as it is produced — prompts, tool calls,
results — so a crash or a Ctrl+C does not cost you the record. On every exit path
Microclaw **reports** declared illumination state without changing it; nothing is
written to Micro-Manager on the way out.

## What leaves the machine

Microclaw itself opens network connections to exactly two places: the Anthropic
API, for the conversation, and GitHub, for the daily
[update check](install-windows.md#automatic-updates). Nothing else — the browser
page is self-contained and loads no fonts, scripts or styles from anywhere.

**Sent to the Anthropic API.** Your prompts and the agent's replies; every tool
call and its result. Tool results describe your rig, so this includes device
labels, property values, stage coordinates, configuration group and preset names,
and file paths. Assume anything the agent can read about the microscope is in the
conversation.

When a tool renders pixels — `snap_and_analyze`, `run_autofocus`, `open_artifact`
— it attaches a **thumbnail**: grayscale, percentile-stretched, 8-bit PNG, capped
at 512 px on the long edge. It is a preview for the model to look at, not your
data. Full-resolution frames never leave the machine; the numerical metrics are
computed locally against the full array before the thumbnail is made.

**Stays on the machine.** Acquisition data as it is written to disk. The JSONL
transcripts, confirmations, usage and acquisition diagnostics. Your
`safety_config.yaml`. Hook code and everything it computes — analysis runs inside
the hook, on this machine, which is one of the reasons analysis belongs there.
The history viewer builds its HTML locally and opens it from your temp directory;
it uploads nothing.

**The update check** asks GitHub what the newest commit on `main` is. It sends
nothing about your session, your rig, or your data.

**Your API key** is resolved environment variable → OS credential store →
`config.toml`, and it is never written into `safety_config.yaml` and never echoed
back to the page beyond a four-character suffix confirming which key is set.
Under `--allow-remote` it cannot be set from the browser at all.

> One caveat about the file fallback, which the UI also states. On Windows,
> `chmod` toggles the read-only attribute and writes no ACL, so a `config.toml`
> holding your key stays readable by **every account on that machine**. The
> credential store (Credential Manager, Keychain, Secret Service) is the only one
> of the two that is actually a secret store. The file exists because keyring is
> awkward on headless and locked-down machines — it is convenience, not
> protection.

## Skills

The agent carries packaged domain knowledge as *skills*: Markdown procedures that
ship inside the package. A catalog of names and one-line descriptions is always
in the system prompt, and the agent pulls the full text of one with `load_skill`
when the work calls for it.

| Skill | What it covers |
|---|---|
| `dna-paint` | Preparing and acquiring DNA-PAINT samples — kinetics, buffer, bench protocol |
| `fluorescence-microscopy` | General fluorescence planning: sample prep, objectives, sampling, channels, photobleaching, trustworthy intensity comparisons |
| `hook-authoring` | Writing and verifying acquisition hooks and saved analysis adapters |
| `htsmlm` | Operating htSMLM and EMU configurations through their semantic hardware mappings |
| `nikon-pfs` | Finding, engaging, verifying and tuning a Nikon Perfect Focus System lock |
| `optical-paths` | Interpreting optical paths, ports, objectives and manual components |
| `smlm` | SMLM workflows — dSTORM, PALM, PAINT, DNA-PAINT |
| `zeiss-can29` | Cubes, side port and illumination on a ZeissCAN29 stand, and Definite Focus |

A skill is **procedural guidance only**: loading one reads no hardware, changes
no state, and grants no authority. A skill cannot widen your security bounds.

Skills are repository-owned — they live at `microclaw/skills/<name>/SKILL.md`
inside the installed package, one directory per skill, each with `name` and
`description` frontmatter. Adding one means adding a file to the package, not
dropping a file on your machine.

## Hooks — where image analysis lives

Analysis runs as an **acquisition hook**, so it travels with the acquisition
rather than sitting beside it. That is a deliberate constraint: a hook is what
lets a decision be made between frames, and it is what survives into an exported
script.

Two kinds are available. **Pre-coded hooks** ship with Microclaw:

| Strategy | What it does |
|---|---|
| `autofocus_per_position` | Sweeps Z after the move to each event's XY and parks the focus device on the sharpest plane before the camera fires |
| `focus_feedback` | Corrects Z drift per frame during a timelapse, using a Tenengrad-over-flux² sharpness metric |
| `intensity_adaptive` | Adjusts exposure per frame to hold mean intensity near a target. Acts on hardware every frame, so it cannot run inside a hardware-sequenced burst |
| `position_filter` | Keeps images from low-intensity positions out of the dataset. **It discards arriving images only** — the position is still moved to and still exposed every round. To stop the exposure itself, use an adaptive survey |
| `snr_observer` | Records per-tile image statistics and changes nothing: every image is returned unchanged, no events are submitted, no hardware is touched |
| `mm_plugin_analyzer` | Delegates per-image analysis to an installed Micro-Manager plugin, treated strictly as an analyzer; the decision stays on the Python side |
| `autofocus_mm_plugin` | Delegates focusing to the lab's validated MM autofocus plugin. The plugin owns the motion; Microclaw guards the resulting position passively and never re-drives Z against the plugin's own controller |

**Saved hooks** are your own code, registered on your machine under
`~/.microclaw/hooks` (`%USERPROFILE%\.microclaw\hooks`).

### Registering one

Nothing is auto-discovered. A `.py` file that appears in the hooks directory is
not a hook — the `manifest.json` beside it is the register, and it pins the
SHA-256 of the exact source you consented to.

The flow is:

1. `read_hook_from_file` reads your file and runs an AST safety scan. It does
   **not** save; it returns the code and any warnings for you to read.
2. You review the code. The agent must show it to you in full.
3. `generate_and_save_hook` validates and saves it, recording the hash in the
   manifest. It refuses *before* writing if the source would be refused at run
   time, and returns every reason with the required fix.

Validation is static: the source is parsed, never imported or executed. Without
an OS sandbox, executing it to validate it would be the same thing as running
unreviewed code.

If the file changes after registration, its hash stops matching the manifest and
Microclaw refuses to run it until you review and re-save it. `list_hooks` shows
every strategy with its resolvable status and, where it is not resolvable, the
reason and the remedy; `describe_hook` gives one hook's constructor parameters,
callback, and source provenance.

### The contract a saved hook must meet

A top-level class must define exactly one of:

| Callback | Signature | Used for |
|---|---|---|
| `analyze_frame` | `(self, image, metadata)` | Live acquisition, adaptive routes |
| `image_process_fn` | `(self, image, metadata, event_queue)` | Live acquisition, full engine contract |
| `analyze_completed_dataset` | `(self, dataset_view, selection, context)` | Offline analysis of a saved dataset |
| `analyze_saved_frame` | `(self, image, metadata, context)` | Offline, frame at a time |

A saved hook **must not inherit `HookBase` and must not take `log_path`** — those
belong to the pre-coded hooks, which the loader bypasses. Saving an offline
adapter does not make it a live acquisition hook; the two contracts are declared
separately when you save.

An adaptive hook — one that decides whether the run continues — must reference
`ContinueAcquisition` or `StopAcquisition`. The preflight checks that the source
mentions them; it cannot prove every frame returns a routing decision.

`load_skill(name="hook-authoring")` is the agent's own reference for this, and
the most reliable way to get a hook written correctly is to ask for it and read
what comes back.

## Compiling a session to a standalone script

Everything Microclaw does has to be expressible as plain Pycro-Manager. Ask for
the session to be exported and `export_session_script` walks the recorded calls
and writes a script that **imports nothing from `microclaw`** and runs against
Micro-Manager on its own. The emitted file is parsed before it is written, so an
export that hands you a file which does not compile has already failed.

Analysis is inlined from source, so the `snr()` in the script *is* the one that
ran. An adaptive run emits **the program, not the trace**: the seed plan, the
hook's exact source, and the decision loop — not the list of tiles that one run
happened to visit. Exported scripts print their envelope — bounds, budget, guard,
read-back — and do not prompt; running the script is the consent.

Some things refuse, deliberately. A refusal names the exact missing capability in
`not_emitted_calls` with `complete: false`, and it is always a **Microclaw
capability gap, never a hardware or rig defect**. The standing cases are an
offline mosaic (its dependencies reach into the package, so inlining would not be
standalone), a plugin-analyzer hook (it constructs an arbitrary Java class and
consults a rig-configured blocklist — authorization state, not source), an
illumination envelope whose conversions are rig-configured, an unresolvable seed
position, and hook source that cannot be recovered.

A step that cannot be emitted is never guessed at. It becomes a
`# NOT EMITTED:` comment and a loud `RuntimeError`, because a plausible
fabrication of a step you did not run is worse than a script that stops.
