"""Deterministic offline fixtures. Signing seeds are PUBLIC, TEST-ONLY keys.

Import by path, or run this file to rebuild the committed dependency wheel.
"""
import base64
from copy import deepcopy
import csv
import hashlib
import io
import json
from pathlib import Path
import zipfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from microclaw.skill_packages import validate_manifest

FIXTURES = Path(__file__).resolve().parent
WHEEL_NAME = "fixture_dependency-1.2.3-py3-none-any.whl"


def _archive(entries):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, content in sorted(entries.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    return output.getvalue()


def build_wheel(destination=None):
    prefix = "fixture_dependency-1.2.3.dist-info/"
    entries = {
        "fixture_dependency/__init__.py": b'VERSION = "1.2.3"\n',
        prefix + "METADATA": b"Metadata-Version: 2.1\nName: fixture-dependency\nVersion: 1.2.3\n\n",
        prefix + "WHEEL": b"Wheel-Version: 1.0\nGenerator: microclaw-test-fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n",
    }
    record = io.StringIO(newline="")
    writer = csv.writer(record, lineterminator="\n")
    for name, content in sorted(entries.items()):
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode("ascii")
        writer.writerow([name, "sha256=" + digest, len(content)])
    writer.writerow([prefix + "RECORD", "", ""])
    entries[prefix + "RECORD"] = record.getvalue().encode("utf-8")
    data = _archive(entries)
    if destination is not None:
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    return data


def sign(document, key="publisher-a"):
    document = deepcopy(document)
    document.pop("signature", None)
    seed = json.loads((FIXTURES / "trust" / (key + "-TEST-ONLY-seed.json")).read_text(encoding="utf-8"))
    private = Ed25519PrivateKey.from_private_bytes(base64.b64decode(seed["seed"]))
    payload = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    document["signature"] = dict(alg="ed25519", key_id=hashlib.sha256(private.public_key().public_bytes_raw()).hexdigest(),
                                 value=base64.b64encode(private.sign(payload)).decode("ascii"))
    return document


def build_release(fixture_dir, artifact_path, *, version=None):
    """Write an archive and return its signed intake; optionally override version."""
    fixture_dir, artifact_path = Path(fixture_dir), Path(artifact_path)
    manifest = json.loads((fixture_dir / "manifest.json").read_text(encoding="utf-8"))
    if version is not None:
        manifest["version"] = version
    manifest = validate_manifest(manifest)
    entries = {asset["path"]: (fixture_dir / asset["path"]).read_bytes() for asset in manifest["assets"]}
    entries["manifest.json"] = json.dumps(manifest, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    artifact_path.write_bytes(_archive(entries))
    intake = {key: manifest[key] for key in ("package_id", "publisher", "version", "artifact", "kind", "microclaw")}
    if manifest["kind"] == "executable":
        intake.update({key: manifest[key] for key in ("python", "platforms", "protocol_version")})
    intake.update(type="microclaw.skill-release.v1", artifact_digest=hashlib.sha256(artifact_path.read_bytes()).hexdigest())
    return sign(intake)


if __name__ == "__main__":
    build_wheel(FIXTURES / "wheels" / WHEEL_NAME)
