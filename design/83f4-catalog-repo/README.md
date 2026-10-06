# Community package catalog

MicroClaw does not test or support packages. Each package is its publisher's;
report problems using its issues URL. Intake checks provenance and release data
without executing publisher code.

The commands below come with MicroClaw. Clone it, install the two libraries
they need, and run them from inside that folder (PowerShell or any shell):

```powershell
git clone https://github.com/Micro-Claw/microclaw
cd microclaw
python -m pip install cryptography packaging
```

Generate your private key and keep it secret. Open an issue in this repository
with the printed public entry and your publisher name to request admission:

```powershell
python -m microclaw.catalog_intake keygen --out publisher-private.pem
```

After admission, pack the package folder and sign its release on any HTTPS host:

```powershell
python -m microclaw.catalog_intake pack --dir package --url https://example.org/package.zip --out package.zip
python -m microclaw.catalog_intake sign-release --key publisher-private.pem --artifact package.zip --url https://example.org/package.zip --out release.json
```

Upload the zip at that exact URL. For executable packages, commit `locks/` next
to the package folder, with one file per declared platform. MicroClaw runs
workers on Python 3.12. From your `requirements.in`, generate the files:

```sh
uv pip compile --no-config --python-version 3.12 --python-platform x86_64-pc-windows-msvc requirements.in -o locks/pylock.win_amd64.toml
uv pip compile --no-config --python-version 3.12 --python-platform aarch64-apple-darwin requirements.in -o locks/pylock.macosx_arm64.toml
uv pip compile --no-config --python-version 3.12 --python-platform x86_64-manylinux_2_28 requirements.in -o locks/pylock.manylinux_x86_64.toml
```

Pass `--locks locks` to `pack`; it fills exact pins and fitting wheel SHA-256s.

`sign-release` prints the repository path: `releases/<publisher>/<package_id>/<version>.json`.
Fork this repository and open a PR adding just that file. Intake verifies the
signature, current policy, downloaded digest, zip structure, declared assets,
manifest binding and supported executable format. It does not run the worker.
A new version is required for a second release. Accepted PRs merge automatically;
refused PRs receive a comment naming the field to correct.

On GitHub, copy `publishing/release.yml` to `.github/workflows/microclaw-release.yml`
in your repository and edit its configuration. Use the `MICROCLAW_COMMIT` from
this repository's `.github/workflows/intake.yml` so signing uses the same rules
as intake. Set the repository secret
`MICROCLAW_PUBLISHER_KEY` to your unencrypted publisher PEM. Push a version tag.
This shortcut packs, signs and uploads the release; add its `release.json` by PR
as above. The workflow does not open the PR.

To permanently withdraw a release, sign its full identity:

```powershell
python -m microclaw.catalog_intake sign-withdrawal --key publisher-private.pem --release release.json --reason "Publisher withdrew this release" --out withdrawal.json
```

Add only the printed `withdrawals/<publisher>/<package_id>/<version>.json` path
in a new PR. Withdrawals retain the release's history. Publishers cannot edit
policy, catalog, workflows or other repository files. Production submissions go
to `main`; fixture traffic belongs only on `test`.

## Catalog operator

Run `git pull`, edit `policy.json` by hand, then renew and push directly to `main`:

```sh
python -m microclaw.catalog_intake sign-policy --key /offline/root.pem --policy policy.json
git push origin main
```

With no `policy.json`, `sign-policy` creates an empty one.
Add publishers and sign again before pushing.

Commit the signed policy before pushing. A PR touching policy.json is refused.
Paste publisher entries under `publishers.<name>.keys`; set publisher/key states
to `revoked` or add `revoked_releases` entries as needed. `sign-policy` bumps the
revision and sets six months of validity, verifying against the shipped roots
before writing. Keep the root file and passphrase offline. The weekly reminder
opens one issue within 30 days of expiry; renew with `sign-policy` and push.
New installs and repairs pause at expiry; installed packages keep running.
