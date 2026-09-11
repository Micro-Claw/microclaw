# Installing on Windows

**Windows 10 or newer.** Microclaw has been tested on Windows 10 and Windows 11
only. `uv`, which the installer uses to build the environment, documents support
back to Windows 8, but nothing older than Windows 10 has been tried here, so
that is all this project guarantees.

You do not need Python; the installer brings its own. Have your microscope in
front of you: after installing the files, `install.bat` walks you through opening
Micro-Manager before restricted setup opens in the browser.

**1. Install Micro-Manager.**
Install [Micro-Manager 2.0](https://micro-manager.org/Download_Micro-Manager_Latest_Release)
(a build later than 2026.06.26, for full functionality). You can leave it closed
until the Microclaw installer asks you to open it.

**2. Download Microclaw.**
On this repository's page, click **Code → Download ZIP**, then right-click the
downloaded file and choose **Extract All**.

**3. Double-click `install.bat`** inside the extracted folder.

It installs everything into `%LOCALAPPDATA%\microclaw` — no administrator rights,
nothing else on your machine is touched — and puts a **Microclaw** icon on your
desktop. The program itself goes into one of two environments, `env-a` and
`env-b`, so that an update can be built into the spare one while the running one
keeps working; `active-slot.txt` names the live one. It takes a few minutes.
Then it asks you to open Micro-Manager and tick
**Tools → Options → Run pycro-manager server on port 4827**. After you confirm,
the installer checks that the bridge really answers. It gives you three attempts;
when the bridge is ready, restricted setup opens in your browser.

**The installer window then becomes the setup server** and keeps running while you
work in the browser. When the browser says your security bounds are saved, press
**Ctrl+C** in that window to stop it, then launch Microclaw from the desktop icon.

> Windows may show a blue **"Windows protected your PC"** banner, because the file
> came from the internet. Click **More info → Run anyway**.

> If the bridge is still unavailable after three checks, installation remains
> complete and the installer prints the manual setup command. Until setup is
> complete, the ordinary desktop icon opens restricted read-only setup without
> permission to write the security-bounds file.

> If security bounds already exist but are invalid or unreviewed, the installer
> preserves them and starts setup with **no write authority at all** — the refusal
> arrives before you spend a conversation capturing endpoints that could not be
> saved. Move that file aside or repair it deliberately, then rerun the one-time
> setup command the installer prints.

**4. Record your security bounds in the browser.**
The installer points you at console.anthropic.com for an Anthropic API key — it
names the site and nothing further — then opens restricted setup. Hardware control and acquisition stay locked. Setup discovers the live axes
— the core XY stage, the core focus device, and any single-axis stage addressed by
its own device label. Move each one to its safe endpoints in Micro-Manager;
Microclaw reads those positions, asks you to approve the exact ranges and
acquisition-warning thresholds, and shows the exact schema-3 YAML before one
confirmed write to `%APPDATA%\microclaw\safety_config.yaml`. Named single-axis
stages get their own `named_stages` entry, because one global `z_min`/`z_max`
cannot describe a piezo and a TIRF stage at once. A rig with a *second XY* stage
is refused for now: Microclaw cannot yet express per-axis bounds for it, and
nothing is written.

If you need to run setup by hand, keep Micro-Manager open with its ZMQ server
enabled and use this full path in PowerShell or Command Prompt:

```
"%LOCALAPPDATA%\microclaw\env-a\Scripts\microclaw.exe" --setup-write-security-config serve
```

The quotes matter, and the full path is needed because the installer does not put
`microclaw` on your `PATH`. This flag grants only the single confirmed
default-file write and never appears in the desktop shortcut. If a config already
exists but is invalid or unreviewed, setup names it and refuses to overwrite it;
move it aside or repair it deliberately before retrying.

A fresh install is always slot `a`; after an update it may be `b`, and this
prints which one is live:

```
type "%LOCALAPPDATA%\microclaw\active-slot.txt"
```

**5. Double-click the Microclaw icon.**
A console window opens — that is the server — and a browser window follows. It
will ask for an Anthropic API key the first time.

**When you are finished, press Ctrl+C in that console window to stop the
server.** It prints `(Ctrl-C to stop)` when it starts, for exactly this reason.
Closing the browser tab does not stop anything: the server keeps running, keeps
holding its connection to Micro-Manager, and keeps the installed files locked
against an upgrade. Closing the console window stops it too, but only Ctrl+C
shuts down cleanly — it closes the acquisition diagnostics file and prints the
declared illumination state on the way out. (The transcript is safe either way;
it is flushed message by message as the session runs. No exit path writes
anything to Micro-Manager.)

Microclaw updates itself — see [Automatic updates](#automatic-updates). The ZIP
remains the manual route: download it again and double-click `install.bat`. It is
safe to re-run: it upgrades in place, and leaves your safety limits and API key
alone. When it finds valid reviewed security bounds it skips setup; you
do **not** repeat step 4 on an upgrade. **Close Microclaw first** —
while its console window is open, Windows holds the installed files locked and
the upgrade will fail.

## Running it later

The desktop icon is the whole interface, and you should not need a terminal
again. If you'd rather use one, note the installer does not put `microclaw` on
your `PATH`, so spell out the same path as step 4:

```
"%LOCALAPPDATA%\microclaw\env-a\Scripts\microclaw.exe" serve   # browser GUI
"%LOCALAPPDATA%\microclaw\env-a\Scripts\microclaw.exe"         # REPL
```

Substitute `env-b` if `active-slot.txt` says `b`. Either way, **stop the server
with Ctrl+C in that window when you are done** — `serve` runs until you do.

See the [CLI reference](cli-reference.md) for the flags, which go *before*
the subcommand.

## Automatic updates

An install made by `install.bat` keeps itself up to date. It is Windows-only,
and it is the only install that does: a source checkout updates with `git pull`.

**Checking.** At most once a day (plus jitter), in the background at startup, on
a daemon thread that never delays the interface. Every outcome is cached, so an
offline machine does not retry on every launch. The browser reads that cached
state and never contacts GitHub itself. A failed check is not an error you have
to act on — Microclaw keeps running the version you have.

**Where updates come from.** If `install.bat` ran inside a Git clone of this
repository, that clone is the source: Microclaw runs a bounded, non-interactive
`git fetch` there and offers the new commit on `main`. It never touches your
checkout — no pull, merge, rebase, stash or branch switch — so a dirty working
tree and a feature branch are both fine. If Git cannot reuse GitHub Desktop's
credentials, the message says so: open GitHub Desktop, Fetch origin, then check
again. An install with no clone behind it follows public `main` instead, which
starts working on the day this repository becomes public and says so until then.

**Accepting one.** A banner appears at the top of the browser GUI:

> A newer Microclaw commit is available: `4ab91cd` — Improve setup flow.
> **Update** · **Later** · **View on GitHub**

**Update** builds the new commit into the spare environment while your session
keeps running, smoke-tests it there, and then offers **Restart now** /
**Restart later**. Restart now is refused while an agent turn, an acquisition, a
pending confirmation, or a setup write is in progress, and it is offered only
when the desktop icon started this process — a server you launched by hand from
a terminal cannot relaunch itself, so you get Restart later only. Restart later
means the new version becomes active at the next desktop launch. **Later** hides
that commit for seven days. **Check for updates** in the header forces a check
now (rate-limited to three a minute).

The terminal REPL prints one line about a waiting update and asks
`Update now? [y/N]`, only when its input is a real terminal. `serve` never
prompts.

**If the new version does not start**, the launcher switches back to the
previous one on its own and reports the rollback at the next successful launch.
The old environment is kept until the new one has proved it starts.

**If the update would need your safety config changed**, the banner says the
update needs the maintainer and names the reason, and nothing is staged. Your
reviewed `safety_config.yaml` is never rewritten by an update.

**Turning it off.** `--no-update-check` skips one launch's check;
`MICROCLAW_UPDATE_CHECK=0` skips it on that machine for good. Neither affects
running the version you already have. The agent has no part in any of this: the
update surface is operator UI, not a tool the model can call.

## Uninstalling

There is no uninstaller. To remove Microclaw by hand:

1. Stop it — **Ctrl+C** in its console window.
2. Delete the desktop icon. From an active environment you can also run
   `microclaw install-shortcut --remove`, which deletes the `.lnk` and the icon.
3. Delete `%LOCALAPPDATA%\microclaw`. This is the program itself: both
   environments, the launcher, and the update state.
4. Delete `%APPDATA%\microclaw` **only if you mean to**. This is your reviewed
   security bounds and, if you did not store the key in the credential store,
   your API key. Keep this directory if you intend to reinstall — a reinstall
   that finds valid reviewed bounds skips setup entirely.
5. Optionally delete `%USERPROFILE%\.microclaw\hooks` (registered hooks and
   their manifest) and any `*_microclaw_*.jsonl` transcripts in the folders you
   ran Microclaw from.

A key kept in the Windows credential store is not removed by any of the above;
remove it from Credential Manager if you want it gone.

Micro-Manager is untouched throughout. Microclaw never writes to it on any exit
path, and uninstalling changes nothing about your Micro-Manager configuration.
