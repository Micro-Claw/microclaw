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

Upload the zip at that exact URL. For executable packages, the publisher writes
`locks`; `pack` does not generate them, and every exact dependency pin requires
wheel SHA-256 hashes (see the manifest `locks.<platform>[].hashes` requirement).

On GitHub, copy `publishing/release.yml` to `.github/workflows/microclaw-release.yml`
in your repository, edit its configuration and pin, and set the repository secret
`MICROCLAW_PUBLISHER_KEY` to your unencrypted publisher PEM. Push a version tag.
The workflow publishes the zip and signed `release.json`; it does not open a PR.

The command prints the repository path: `releases/<publisher>/<package_id>/<version>.json`.
Fork this repository and open a PR adding just that file. Intake verifies the
signature, current policy, downloaded digest, zip structure, declared assets,
manifest binding and supported executable format. It does not run the worker.
A new version is required for a second release. Accepted PRs merge automatically;
refused PRs receive a comment naming the field to correct.

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

Commit the signed policy before pushing. A PR touching policy.json is refused.
Paste publisher entries under `publishers.<name>.keys`; set publisher/key states
to `revoked` or add `revoked_releases` entries as needed. `sign-policy` bumps the
revision and sets six months of validity, verifying against the shipped roots
before writing. Keep the root file and passphrase offline. The weekly reminder
opens one issue within 30 days of expiry; renew with `sign-policy` and push.
New installs and repairs pause at expiry; installed packages keep running.
