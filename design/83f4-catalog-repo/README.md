# Community package catalog

MicroClaw does not test or support packages. Each package is its publisher's;
report problems using its issues URL. Intake checks provenance and release data
without executing publisher code.

Use Python from a MicroClaw checkout on PYTHONPATH, with cryptography and
packaging installed. These single-line commands work in PowerShell too.

Generate your private key, keep it secret, and send the printed public key entry
and your publisher name to the catalog operator for admission:

```powershell
python -m microclaw.catalog_intake keygen --out publisher-private.pem
```

After admission, build your release zip with its manifest's artifact field set
to the exact HTTPS download URL. Sign it:

```powershell
python -m microclaw.catalog_intake sign-release --key publisher-private.pem --artifact package.zip --url https://example.org/package.zip --out release.json
```

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
