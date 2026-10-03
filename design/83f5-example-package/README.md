# Example MicroClaw skill package

This is the seed for `Micro-Claw/example-skill-package`, published by
`microclaw-examples`. It demonstrates a markdown skill, a reproducible release
zip, and a signed catalog record. The skill helps open an unfamiliar system.

To publish on GitHub, set the workflow's `MICROCLAW_COMMIT` to a reviewed full
MicroClaw commit SHA, store your unencrypted publisher PEM as the repository
secret `MICROCLAW_PUBLISHER_KEY`, and push a tag equal to `v` plus the manifest
version. Download `release.json` from the release and add it to the catalog at
the path printed by `sign-release`, by PR.

On any HTTPS host, run these commands from an installed MicroClaw checkout:

```sh
python -m microclaw.catalog_intake pack --dir /path/to/package --url https://example.org/package.zip --out package.zip
python -m microclaw.catalog_intake sign-release --key publisher-private.pem --artifact package.zip --url https://example.org/package.zip --out release.json
```

Upload the zip to that exact URL, then submit the signed file by catalog PR.
The source manifest stays unchanged; the packed manifest contains the URL and
asset hashes. The publisher must first be admitted by the catalog operator.
