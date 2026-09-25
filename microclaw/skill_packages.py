"""Community release format and publisher trust, without installation or workers.

The schema is the closed mapping checked by ``validate_manifest``. Metadata is
in the manifest; SKILL.md is opaque publisher text (not first-party frontmatter).
An artifact reference is a publisher HTTPS URL; only the *external* intake's
SHA-256 pins its bytes. A caller supplying verified installed records attests
that the manifest came from those bytes. Signature verification establishes
provenance, not safe behaviour.

Revocation affects future execution only: every future start or skill load must
pass check_release(purpose="execution"). Already-running jobs are not cancelled.
Trust checks never delete, move or write anything; installed files and acquisition
data remain untouched. A revoked release stays installed and visible but cannot
start. Reauthorization means installing a different release as a new admission,
only when release, key or publisher revocation makes a release unusable. Rotation
never requires it; staleness never requires it and is disclosed in the verdict.
Verification is a pure function of the caller's policy snapshot, with no I/O
except hashing a caller-named artifact. No refresh can block acquisition.

Operations carry name/input_schema/output_schema. Structural manifest validation
admits a closed JSON Schema 2020-12 subset; v1 execution requires object schemas.
Wire validators are pure data checks; skill_supervisor owns processes and I/O.
"""
from __future__ import annotations

import base64
import binascii
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import keyword
import math
from pathlib import Path
import re
from urllib.parse import urlsplit

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

RELEASE_TYPE = "microclaw.skill-release.v1"
TRUST_POLICY_TYPE = "microclaw.trust-policy.v1"
PRODUCTION_ROOTS = {"environment": "production", "keys": []}
MAX_SIGNATURE_LENGTH = 88
MAX_PUBLIC_KEY_LENGTH = 44
MAX_TIMESTAMP_LENGTH = 20
MAX_PUBLISHERS = 1024
MAX_KEYS = 64
MAX_REVOKED_RELEASES = 16384
MAX_PUBLISHER_LENGTH = 64
MAX_PACKAGE_ID_LENGTH = 64
MAX_SKILL_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 512
MAX_VERSION_LENGTH = 64
MAX_ARTIFACT_DIGEST_LENGTH = 64
MAX_LICENSE_LENGTH = 256
MAX_URL_LENGTH = 2048
MAX_MODULE_LENGTH = 256
MAX_OPERATION_NAME_LENGTH = 64
MAX_PLATFORM_LENGTH = 64
MAX_SPECIFIER_LENGTH = 256
MAX_PATH_LENGTH = 1024
MAX_REQUIREMENT_LENGTH = 512
MAX_ASSETS = 256
MAX_SKILLS = 64
MAX_OPERATIONS = 128
MAX_PLATFORMS = 32
MAX_LOCK_ENTRIES = 512
MAX_REQUIREMENT_HASHES = 64

_IDENTIFIER = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_COMMON = {
    "package_id", "publisher", "version", "microclaw", "kind", "license",
    "source_url", "issues_url", "artifact", "assets", "skills",
}
_EXECUTABLE = {"python", "platforms", "protocol_version", "entry_point", "operations", "locks"}


class PackageRefusal(ValueError):
    """A format/loading refusal with a machine-readable dotted field path."""

    def __init__(self, field: str, reason: str):
        self.field = field
        super().__init__(f"{field}: {reason}")


def _text(value, field, limit):
    if not isinstance(value, str) or not value.strip():
        raise PackageRefusal(field, "expected nonempty text")
    if len(value) > limit:
        raise PackageRefusal(field, f"exceeds {limit} characters")
    if any(ord(c) < 32 or 127 <= ord(c) <= 159 or c in "\u2028\u2029" for c in value):
        raise PackageRefusal(field, "control characters and line separators are forbidden")
    return value


def _identifier(value, field, limit):
    _text(value, field, limit)
    if not _IDENTIFIER.fullmatch(value):
        raise PackageRefusal(field, "expected lowercase ASCII words separated by single hyphens")
    return value


def _mapping(value, field, required, optional=()):
    if not isinstance(value, dict):
        raise PackageRefusal(field, "expected an object")
    for key in sorted(required):
        if key not in value:
            raise PackageRefusal(f"{field}.{key}" if field else key, "required field")
    for key in value:
        if key not in required and key not in optional:
            raise PackageRefusal(f"{field}.{key}" if field else str(key), "unknown or forbidden field")


def _list(value, field, limit, *, empty=False):
    if not isinstance(value, list) or (not empty and not value):
        raise PackageRefusal(field, "expected a list" if empty else "expected a nonempty list")
    if len(value) > limit:
        raise PackageRefusal(field, f"exceeds {limit} entries")


def _version(value, field):
    _text(value, field, MAX_VERSION_LENGTH)
    try:
        Version(value)
    except InvalidVersion as exc:
        raise PackageRefusal(field, "invalid version") from exc


def _range(value, field):
    _text(value, field, MAX_SPECIFIER_LENGTH)
    try:
        SpecifierSet(value)
    except InvalidSpecifier as exc:
        raise PackageRefusal(field, "invalid version range") from exc


def _digest(value, field):
    _text(value, field, MAX_ARTIFACT_DIGEST_LENGTH)
    if not _DIGEST.fullmatch(value):
        raise PackageRefusal(field, "expected lowercase SHA-256 hex")


def _url(value, field):
    _text(value, field, MAX_URL_LENGTH)
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme == "https" and parsed.hostname and not parsed.username
                 and not parsed.password and not parsed.fragment and parsed.port != 0
                 and not any(c.isspace() for c in value) and "\\" not in value)
    except ValueError:
        valid = False
    if not valid:
        raise PackageRefusal(field, "expected an absolute HTTPS URL without credentials or fragment")


def _path(value, field):
    _text(value, field, MAX_PATH_LENGTH)
    # Cross-platform syntax, independent of the host's pathlib flavour. Colon
    # also excludes drive-relative paths and NTFS alternate data streams.
    if (value.startswith("/") or "\\" in value or ":" in value
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise PackageRefusal(field, "expected a relative forward-slash path without traversal")
    return value


def safe_release_path(release_dir, path, *, field="path", is_symlink=False):
    """Validate an asset/extraction destination, without extracting anything.

    Archive readers must pass their member's link status, including hard links;
    this format admits only ordinary files/directories. Existing symlink path
    components also refuse. Installation must own races during extraction (83d).
    """
    _path(path, field)
    root = Path(release_dir)
    if is_symlink or root.is_symlink():
        raise PackageRefusal(field, "links are forbidden")
    candidate = root
    for part in path.split("/"):
        candidate = candidate / part
        if candidate.is_symlink():
            raise PackageRefusal(field, "links are forbidden")
    try:
        candidate.resolve().relative_to(root.resolve())
    except (ValueError, OSError, RuntimeError) as exc:
        raise PackageRefusal(field, "path escapes or cannot resolve within release directory") from exc
    return candidate


def qualified_name(publisher, package_id, skill):
    """Return publisher/package/skill; each component excludes the delimiter."""
    _identifier(publisher, "publisher", MAX_PUBLISHER_LENGTH)
    _identifier(package_id, "package_id", MAX_PACKAGE_ID_LENGTH)
    _identifier(skill, "skill", MAX_SKILL_NAME_LENGTH)
    return f"{publisher}/{package_id}/{skill}"


def parse_qualified_name(name):
    if not isinstance(name, str) or len(name.split("/")) != 3:
        raise PackageRefusal("name", "expected publisher/package/skill")
    parts = tuple(name.split("/"))
    qualified_name(*parts)
    return parts


def _compatibility(record):
    _range(record["microclaw"], "microclaw")
    if record["kind"] == "executable":
        _range(record["python"], "python")
        _list(record["platforms"], "platforms", MAX_PLATFORMS)
        platforms = set()
        for i, platform in enumerate(record["platforms"]):
            field = f"platforms[{i}]"
            _text(platform, field, MAX_PLATFORM_LENGTH)
            if not re.fullmatch(r"[a-z0-9]+(?:[_-][a-z0-9]+)*", platform) or platform in platforms:
                raise PackageRefusal(field, "expected a unique platform tag")
            platforms.add(platform)
        _version(record["protocol_version"], "protocol_version")


def validate_intake(record):
    """Validate signed-record structure only; check_release establishes trust."""
    if not isinstance(record, dict):
        raise PackageRefusal("intake", "expected an object")
    required = {"type", "package_id", "publisher", "version", "artifact",
                "artifact_digest", "kind", "microclaw", "signature"}
    if record.get("kind") == "executable":
        required |= {"python", "platforms", "protocol_version"}
    _mapping(record, "", required)
    if record["type"] != RELEASE_TYPE:
        raise PackageRefusal("type", "expected skill release type")
    if record["kind"] not in ("markdown", "executable"):
        raise PackageRefusal("kind", "expected markdown or executable")
    _identifier(record["package_id"], "package_id", MAX_PACKAGE_ID_LENGTH)
    _identifier(record["publisher"], "publisher", MAX_PUBLISHER_LENGTH)
    _version(record["version"], "version")
    _url(record["artifact"], "artifact")
    _digest(record["artifact_digest"], "artifact_digest")
    _compatibility(record)
    _signature(record["signature"])
    return deepcopy(record)


_SCHEMA_TYPES = {'null', 'boolean', 'object', 'array', 'number', 'integer', 'string'}
_SCHEMA_KEYWORDS = {
    'type', 'properties', 'required', 'additionalProperties', 'items', 'enum',
    'minimum', 'maximum', 'minLength', 'maxLength', 'minItems', 'maxItems',
    'title', 'description', 'default',
}


def _json_equal(left, right):
    # Python's True == 1 is not JSON equality, including inside containers.
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_json_equal(v, right[k]) for k, v in left.items())
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(_json_equal(a, b) for a, b in zip(left, right))
    return left == right


def validate_parameter_schema(schema, field='schema'):
    """Admit only our closed 2020-12 vocabulary, recursively; never apply defaults."""
    if isinstance(schema, bool):
        return
    if not isinstance(schema, dict):
        raise PackageRefusal(field, 'expected schema object or boolean')
    for key, value in schema.items():
        where = f'{field}.{key}'
        if key not in _SCHEMA_KEYWORDS:
            raise PackageRefusal(where, 'unsupported schema keyword')
        if key == 'type':
            if not isinstance(value, str) or value not in _SCHEMA_TYPES:
                raise PackageRefusal(where, 'expected one JSON Schema type')
        elif key == 'properties':
            if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
                raise PackageRefusal(where, 'expected object with string keys')
            for name, child in value.items():
                validate_parameter_schema(child, f'{where}.{name}')
        elif key == 'items':
            validate_parameter_schema(value, where)
        elif key == 'required':
            if (not isinstance(value, list) or any(not isinstance(v, str) for v in value)
                    or len(set(value)) != len(value)):
                raise PackageRefusal(where, 'expected unique string array')
        elif key == 'additionalProperties':
            if not isinstance(value, bool):
                raise PackageRefusal(where, 'expected boolean')
        elif key == 'enum':
            if not isinstance(value, list):
                raise PackageRefusal(where, 'expected array')
        elif key in {'minimum', 'maximum'}:
            if type(value) not in (int, float) or (isinstance(value, float) and not math.isfinite(value)):
                raise PackageRefusal(where, 'expected finite number')
        elif key in {'minLength', 'maxLength', 'minItems', 'maxItems'}:
            if (type(value) not in (int, float) or (isinstance(value, float) and not math.isfinite(value))
                    or value < 0 or value != int(value)):
                raise PackageRefusal(where, 'expected nonnegative integer')
        elif key in {'title', 'description'} and not isinstance(value, str):
            raise PackageRefusal(where, 'expected string')


def validate_parameters(schema, value, field='parameters'):
    """Validate JSON data against an admitted schema, with 2020-12 semantics."""
    if schema is True:
        return
    if schema is False:
        raise PackageRefusal(field, 'false schema')
    number = type(value) in (int, float)
    types = dict(null=value is None, boolean=isinstance(value, bool),
                 object=isinstance(value, dict), array=isinstance(value, list),
                 number=number, integer=number and value == int(value), string=isinstance(value, str))
    if 'type' in schema and not types[schema['type']]:
        raise PackageRefusal(field, f"expected {schema['type']}")
    if 'enum' in schema and not any(_json_equal(value, item) for item in schema['enum']):
        raise PackageRefusal(field, 'not in enum')
    if isinstance(value, dict):
        properties = schema.get('properties', {})
        for name in schema.get('required', []):
            if name not in value:
                raise PackageRefusal(f'{field}.{name}', 'required property')
        for name, item in value.items():
            if name in properties:
                validate_parameters(properties[name], item, f'{field}.{name}')
            elif schema.get('additionalProperties', True) is False:
                raise PackageRefusal(f'{field}.{name}', 'additional property forbidden')
    if isinstance(value, list) and 'items' in schema:
        for i, item in enumerate(value):
            validate_parameters(schema['items'], item, f'{field}[{i}]')
    bounds = []
    if number:
        bounds += [('minimum', value, True), ('maximum', value, False)]
    if isinstance(value, str):
        bounds += [('minLength', len(value), True), ('maxLength', len(value), False)]
    if isinstance(value, list):
        bounds += [('minItems', len(value), True), ('maxItems', len(value), False)]
    for key, actual, lower in bounds:
        if key in schema and (actual < schema[key] if lower else actual > schema[key]):
            raise PackageRefusal(field, f'violates {key} {schema[key]}')


def validate_manifest(manifest):
    """Validate both closed manifest kinds and return a detached data snapshot."""
    if not isinstance(manifest, dict):
        raise PackageRefusal("manifest", "expected an object")
    kind = manifest.get("kind")
    if kind not in ("markdown", "executable"):
        raise PackageRefusal("kind", "expected markdown or executable")
    _mapping(manifest, "", _COMMON | (_EXECUTABLE if kind == "executable" else set()))
    _identifier(manifest["package_id"], "package_id", MAX_PACKAGE_ID_LENGTH)
    _identifier(manifest["publisher"], "publisher", MAX_PUBLISHER_LENGTH)
    _version(manifest["version"], "version")
    _compatibility(manifest)
    _text(manifest["license"], "license", MAX_LICENSE_LENGTH)
    for field in ("source_url", "issues_url", "artifact"):
        _url(manifest[field], field)
    _list(manifest["assets"], "assets", MAX_ASSETS)
    paths = set()
    for i, asset in enumerate(manifest["assets"]):
        field = f"assets[{i}]"
        _mapping(asset, field, {"path", "sha256"})
        path = _path(asset["path"], field + ".path")
        if path in paths:
            raise PackageRefusal(field + ".path", "duplicate asset path")
        paths.add(path)
        _digest(asset["sha256"], field + ".sha256")
    _list(manifest["skills"], "skills", MAX_SKILLS)
    names = set()
    for i, skill in enumerate(manifest["skills"]):
        field = f"skills[{i}]"
        _mapping(skill, field, {"name", "description", "path"})
        _identifier(skill["name"], field + ".name", MAX_SKILL_NAME_LENGTH)
        _text(skill["description"], field + ".description", MAX_DESCRIPTION_LENGTH)
        if skill["name"] in names:
            raise PackageRefusal(field + ".name", "duplicate skill name")
        names.add(skill["name"])
        path = _path(skill["path"], field + ".path")
        if path not in paths or path.split("/")[-1] != "SKILL.md":
            raise PackageRefusal(field + ".path", "expected a declared SKILL.md asset")
    if kind == "executable":
        platforms = set(manifest["platforms"])
        _mapping(manifest["entry_point"], "entry_point", {"module"})
        module = _text(manifest["entry_point"]["module"], "entry_point.module", MAX_MODULE_LENGTH)
        if any(not part.isidentifier() or keyword.iskeyword(part) for part in module.split(".")):
            raise PackageRefusal("entry_point.module", "expected a dotted Python module name")
        _list(manifest["operations"], "operations", MAX_OPERATIONS)
        operations = set()
        for i, operation in enumerate(manifest["operations"]):
            field = f"operations[{i}]"
            _mapping(operation, field, {"name", "input_schema", "output_schema"})
            name = _text(operation["name"], field + ".name", MAX_OPERATION_NAME_LENGTH)
            if not re.fullmatch(r"[a-z][a-z0-9_]*", name) or name in operations:
                raise PackageRefusal(field + ".name", "expected a unique operation identifier")
            operations.add(name)
            for key in ("input_schema", "output_schema"):
                schema = operation[key]
                if not isinstance(schema, dict) or "type" not in schema:
                    raise PackageRefusal(field + "." + key, "expected a schema object with type")
                validate_parameter_schema(schema, field + "." + key)
        _mapping(manifest["locks"], "locks", platforms)
        for platform, lock in manifest["locks"].items():
            field = f"locks.{platform}"
            _list(lock, field, MAX_LOCK_ENTRIES, empty=True)  # Stdlib-only workers need none.
            for i, item in enumerate(lock):
                item_field = f"{field}[{i}]"
                _mapping(item, item_field, {"requirement", "hashes"})
                req_field = item_field + ".requirement"
                _text(item["requirement"], req_field, MAX_REQUIREMENT_LENGTH)
                try:
                    req = Requirement(item["requirement"])
                    pins = list(req.specifier)
                    if (req.url or req.marker or len(pins) != 1 or pins[0].operator != "=="
                            or "*" in pins[0].version):
                        raise ValueError("not an unconditional exact pin")
                    Version(pins[0].version)
                except (InvalidRequirement, InvalidVersion, ValueError) as exc:
                    raise PackageRefusal(req_field, "expected one unconditional exact version pin") from exc
                _list(item["hashes"], item_field + ".hashes", MAX_REQUIREMENT_HASHES)
                for j, digest in enumerate(item["hashes"]):
                    _digest(digest, f"{item_field}.hashes[{j}]")
    return deepcopy(manifest)


def _bound_release(manifest, intake):
    """Bind validated manifest and intake without I/O."""
    fields = ["publisher", "package_id", "version", "artifact", "kind", "microclaw"]
    if manifest["kind"] == "executable":
        fields += ["python", "platforms", "protocol_version"]
    for field in fields:
        if manifest[field] != intake[field]:
            raise PackageRefusal("intake." + field, "does not match verified manifest")


def _enabled_releases(records, policy, now, *, exclusions=None):
    """Isolate bad records; report (record, refusal) pairs to the caller.

    Count names before validation so even a malformed carrier cannot make an
    ambiguous name resolve to whichever record happens to validate first.
    """
    candidates = []
    carriers = {}
    for record in records:
        names = set()
        try:
            if record.get("enabled") is not True or record.get("verified") is not True:
                raise PackageRefusal("discovery", "not enabled and verified")
            raw = record.get("manifest") or {}
            for skill in raw.get("skills", []):
                try:
                    names.add(qualified_name(raw["publisher"], raw["package_id"], skill["name"]))
                except (PackageRefusal, KeyError, TypeError):
                    pass
            error = None
        except (AttributeError, TypeError, PackageRefusal) as exc:
            error = exc if isinstance(exc, PackageRefusal) else PackageRefusal("record", str(exc))
        for name in names:
            carriers[name] = carriers.get(name, 0) + 1
        candidates.append((record, names, error))
    for record, names, error in candidates:
        try:
            if any(carriers[name] > 1 for name in names):
                raise PackageRefusal("name", "ambiguous enabled external skill")
            if error:
                raise error
            if record.get("eligible", True) is not True:
                raise PackageRefusal("eligibility", "release is not eligible")
            manifest = validate_manifest(record["manifest"])
            intake = validate_intake(record["intake"])
            _bound_release(manifest, intake)
            check_release(intake, policy, purpose="execution", now=now)
        except (PackageRefusal, KeyError, TypeError, AttributeError, ValueError) as exc:
            refusal = exc if isinstance(exc, PackageRefusal) else PackageRefusal("record", str(exc))
            if exclusions is not None:
                exclusions.append((record, refusal))
            continue
        yield record, manifest, intake


def _provenance(manifest, intake):
    return (f"publisher-provided; publisher={manifest['publisher']}; "
            f"release={manifest['version']} sha256:{intake['artifact_digest']}")


def external_catalog_lines(records, policy, *, now, exclusions=None):
    """Render metadata in stable order, isolating excluded records with reasons.

    Optional ``exclusions`` receives (record, PackageRefusal) pairs. Publisher
    metadata is bounded text, never authority.
    """
    lines = []
    for _, manifest, intake in _enabled_releases(records, policy, now, exclusions=exclusions):
        for skill in manifest["skills"]:
            name = qualified_name(manifest["publisher"], manifest["package_id"], skill["name"])
            lines.append(f"- {name} [{_provenance(manifest, intake)}]: {skill['description']}")
    return tuple(sorted(lines))


def verify_release_assets(release_dir, manifest, *, read_path=None):
    """Hash every asset once, retaining only requested skill text, if any."""
    selected = None
    for i, asset in enumerate(manifest["assets"]):
        field = f"assets[{i}]"
        path = safe_release_path(release_dir, asset["path"], field=field + ".path")
        digest = hashlib.sha256()
        content = bytearray() if asset["path"] == read_path else None
        try:
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                    if content is not None:
                        content.extend(chunk)
        except OSError as exc:
            raise PackageRefusal(field + ".path", "asset is not a readable file") from exc
        if digest.hexdigest() != asset["sha256"]:
            raise PackageRefusal(field + ".sha256", "asset digest mismatch")
        if content is not None:
            selected = bytes(content)
    return selected


def load_external_skill(name, records, policy, *, now):
    """Return text and operations with the same release identity, without executing.

    ``records`` contains plain dictionaries with enabled/verified booleans,
    manifest, intake and release_dir. The verified flag attests installed-byte
    verification by the caller (83d); signatures are independently checked here.
    All declared assets are checked so a changed ancillary file cannot accompany
    apparently intact skill prose.
    Malformed records are isolated; a requested excluded skill refuses with its
    reason. Asset failures use PackageRefusal.
    """
    publisher, package, skill_name = parse_qualified_name(name)
    matches = []
    exclusions = []
    for record, manifest, intake in _enabled_releases(records, policy, now, exclusions=exclusions):
        if (manifest["publisher"], manifest["package_id"]) == (publisher, package):
            for skill in manifest["skills"]:
                if skill["name"] == skill_name:
                    matches.append((record, manifest, intake, skill))
    if len(matches) != 1:
        for record, refusal in exclusions:
            manifest = record.get("manifest") if isinstance(record, dict) else None
            if isinstance(manifest, dict) and (manifest.get("publisher"), manifest.get("package_id")) == (publisher, package):
                raise refusal
        raise PackageRefusal("name", "unknown or ambiguous enabled verified external skill")
    record, manifest, intake, skill = matches[0]
    skill_bytes = verify_release_assets(record["release_dir"], manifest, read_path=skill["path"])
    try:
        text = skill_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PackageRefusal("skills.path", "SKILL.md must be UTF-8") from exc
    return {
        "name": name, "publisher": publisher, "package_id": package,
        "version": intake["version"], "artifact_digest": intake["artifact_digest"],
        "text": f"Publisher-provided skill ({_provenance(manifest, intake)}; {name})\n\n{text}",
        "operations": deepcopy(manifest.get("operations", [])),
    }


def _base64(value, field, size, limit):
    _text(value, field, limit)
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise PackageRefusal(field, "expected base64") from exc
    if len(raw) != size or base64.b64encode(raw).decode("ascii") != value:
        raise PackageRefusal(field, "wrong length or noncanonical base64")
    return raw


def _signature(signature, field="signature"):
    _mapping(signature, field, {"alg", "key_id", "value"})
    if signature["alg"] != "ed25519":
        raise PackageRefusal(field + ".alg", "expected ed25519")
    _digest(signature["key_id"], field + ".key_id")
    return _base64(signature["value"], field + ".value", 64, MAX_SIGNATURE_LENGTH)


def _canonical(document):
    return json.dumps({k: v for k, v in document.items() if k != "signature"},
                      sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def _keys(keys, field, *, states=False, empty=False):
    _list(keys, field, MAX_KEYS, empty=empty)
    admitted = {}
    for i, key in enumerate(keys):
        prefix = f"{field}[{i}]"
        _mapping(key, prefix, {"key_id", "public_key"} | ({"state"} if states else set()))
        _digest(key["key_id"], prefix + ".key_id")
        raw = _base64(key["public_key"], prefix + ".public_key", 32, MAX_PUBLIC_KEY_LENGTH)
        if hashlib.sha256(raw).hexdigest() != key["key_id"] or key["key_id"] in admitted:
            raise PackageRefusal(prefix + ".key_id", "mismatched or duplicate key identity")
        if states and key["state"] not in ("active", "retired", "revoked"):
            raise PackageRefusal(prefix + ".state", "unknown key state")
        admitted[key["key_id"]] = raw
    return admitted


def _verify_signature(document, keys, field="signature"):
    signature = _signature(document["signature"], field)
    key = keys.get(document["signature"]["key_id"])
    if key is None:
        raise PackageRefusal(field + ".key_id", "key is not admitted")
    try:
        Ed25519PublicKey.from_public_bytes(key).verify(signature, _canonical(document))
    except InvalidSignature as exc:
        raise PackageRefusal(field + ".value", "invalid signature") from exc


def _expires(value):
    _text(value, "trust.expires_at", MAX_TIMESTAMP_LENGTH)
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z", value):
        raise PackageRefusal("trust.expires_at", "expected UTC timestamp")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise PackageRefusal("trust.expires_at", "invalid UTC timestamp") from exc


def verify_trust_policy(document, roots=None, *, previous=None):
    """Return the detached verified policy document.

    Roots are caller-owned MicroClaw configuration, never submission data.
    Omission selects the empty production root set (fails closed). Preserve the
    last returned document as previous to enforce monotonic revisions. Freshness
    is purpose-dependent and is checked by check_release, not this function.
    """
    roots = PRODUCTION_ROOTS if roots is None else roots
    _mapping(roots, "roots", {"environment", "keys"})
    if roots["environment"] not in ("production", "test"):
        raise PackageRefusal("roots.environment", "unknown environment")
    root_keys = _keys(roots["keys"], "roots.keys", empty=True)
    _mapping(document, "trust", {"type", "environment", "revision", "expires_at",
                                "publishers", "revoked_releases", "signature"})
    if document["type"] != TRUST_POLICY_TYPE:
        raise PackageRefusal("trust.type", "expected trust policy type")
    if document["environment"] != roots["environment"]:
        raise PackageRefusal("trust.environment", "root environment mismatch")
    if type(document["revision"]) is not int or document["revision"] < 1:
        raise PackageRefusal("trust.revision", "expected positive integer")
    _expires(document["expires_at"])
    publishers = document["publishers"]
    if not isinstance(publishers, dict) or len(publishers) > MAX_PUBLISHERS:
        raise PackageRefusal("trust.publishers", "expected bounded publisher mapping")
    identities = set()
    for publisher, entry in publishers.items():
        field = f"trust.publishers.{publisher}"
        _identifier(publisher, "trust.publishers", MAX_PUBLISHER_LENGTH)
        _mapping(entry, field, {"state", "keys"})
        if entry["state"] not in ("active", "revoked"):
            raise PackageRefusal(field + ".state", "unknown publisher state")
        keys = _keys(entry["keys"], field + ".keys", states=True)
        for i, key in enumerate(entry["keys"]):
            if key["key_id"] in identities:
                raise PackageRefusal(f"{field}.keys[{i}].key_id", "key belongs to another publisher")
        identities.update(keys)
    _list(document["revoked_releases"], "trust.revoked_releases", MAX_REVOKED_RELEASES, empty=True)
    for i, release in enumerate(document["revoked_releases"]):
        field = f"trust.revoked_releases[{i}]"
        _mapping(release, field, {"package_id", "version", "artifact_digest"})
        _identifier(release["package_id"], field + ".package_id", MAX_PACKAGE_ID_LENGTH)
        _version(release["version"], field + ".version")
        _digest(release["artifact_digest"], field + ".artifact_digest")
    _verify_signature(document, root_keys, "trust.signature")
    if previous is not None:
        if previous["environment"] != document["environment"]:
            raise PackageRefusal("trust.environment", "previous environment mismatch")
        if (document["revision"] < previous["revision"]
                or (document["revision"] == previous["revision"] and _canonical(document) != _canonical(previous))):
            raise PackageRefusal("trust.revision", "rollback or conflicting revision")
    return deepcopy(document)


def check_release(intake, policy, *, purpose, now, artifact=None):
    """Check admission or future execution against a verified policy snapshot.

    policy must be the return value of verify_trust_policy, never package data.
    The caller attests this, as with an installed record's verified flag. A future
    cache (83f) must re-verify stored documents against the module's roots when
    loading them. This gate does not repeat policy verification.
    Admission hashes bytes or a caller-named path; execution performs no I/O.
    """
    intake = validate_intake(intake)
    if purpose not in ("admission", "execution"):
        raise PackageRefusal("purpose", "expected admission or execution")
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise PackageRefusal("now", "expected timezone-aware datetime")
    if policy is None:
        raise PackageRefusal("trust", "no verified policy")
    stale = now > _expires(policy["expires_at"])
    publisher = policy["publishers"].get(intake["publisher"])
    if publisher is None or publisher["state"] == "revoked":
        raise PackageRefusal("publisher", "unknown or revoked publisher")
    key = next((key for key in publisher["keys"]
                if key["key_id"] == intake["signature"]["key_id"]), None)
    if key is None or key["state"] == "revoked" or (purpose == "admission" and key["state"] == "retired"):
        raise PackageRefusal("signature.key_id", "unknown or ineligible publisher key")
    _verify_signature(intake, {key["key_id"]: base64.b64decode(key["public_key"])})
    if intake["kind"] == "executable" and intake["protocol_version"] not in SUPPORTED_PROTOCOLS:
        raise PackageRefusal("protocol_version", "unsupported executable protocol")
    if any(release["artifact_digest"] == intake["artifact_digest"]
           for release in policy["revoked_releases"]):
        raise PackageRefusal("artifact_digest", "release revoked")
    if purpose == "admission":
        if stale:
            raise PackageRefusal("trust.expires_at", "policy expired")
        digest = hashlib.sha256()
        if isinstance(artifact, bytes):
            digest.update(artifact)
        elif isinstance(artifact, (str, Path)):
            try:
                with Path(artifact).open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
            except (OSError, ValueError) as exc:
                raise PackageRefusal("artifact_digest", "artifact unreadable") from exc
        else:
            raise PackageRefusal("artifact_digest", "artifact bytes or path required")
        if digest.hexdigest() != intake["artifact_digest"]:
            raise PackageRefusal("artifact_digest", "artifact digest mismatch")
    return {"publisher": intake["publisher"], "key_id": key["key_id"],
            "key_state": key["state"], "revision": policy["revision"],
            "stale": stale, "purpose": purpose, "environment": policy["environment"]}


ANALYSIS_PROTOCOL = "microclaw.analysis.v1"
SUPPORTED_PROTOCOLS = {"1.0": ANALYSIS_PROTOCOL}
MAX_MESSAGE_BYTES = 65536
MAX_STATUS_MESSAGE_LENGTH = 512
MAX_FAILURE_MESSAGE_LENGTH = 1024
MAX_ARTIFACTS = 256


def supported_executable(manifest):
    """Check execution semantics after structural validation; no imports or I/O."""
    if manifest["kind"] != "executable":
        raise PackageRefusal("kind", "expected executable release")
    if "self_check" not in {op["name"] for op in manifest["operations"]}:
        raise PackageRefusal("operations", "self_check is required")
    for i, op in enumerate(manifest["operations"]):
        for key in ("input_schema", "output_schema"):
            if op[key]["type"] != "object":
                raise PackageRefusal(f"operations[{i}].{key}.type", "v1 requires object")


def _json_object(value, field):
    if not isinstance(value, dict):
        raise PackageRefusal(field, "expected JSON object")


def encode_message(message):
    """Encode a bounded UTF-8 NDJSON line (newline included in the bound)."""
    # Walk before encoding: json.dumps accepts non-string keys and tuples, and
    # can allocate an arbitrarily large escaped string before checking its size.
    budget = MAX_MESSAGE_BYTES
    stack = [message]
    nodes = 0
    while stack:
        value = stack.pop()
        nodes += 1
        if nodes > MAX_MESSAGE_BYTES:
            raise PackageRefusal("message", "JSON structure exceeds wire bound")
        if isinstance(value, dict):
            if len(value) > budget:
                raise PackageRefusal("message", "JSON object exceeds wire bound")
            for key, item in value.items():
                if not isinstance(key, str):
                    raise PackageRefusal("message", "JSON keys must be strings")
                stack.extend((key, item))
        elif isinstance(value, list):
            if len(value) > budget:
                raise PackageRefusal("message", "JSON array exceeds wire bound")
            stack.extend(value)
        elif isinstance(value, str):
            budget -= len(value)
        elif value is None or type(value) in (bool, int, float):
            budget -= 1
        else:
            raise PackageRefusal("message", "expected JSON values")
        if len(stack) + nodes > MAX_MESSAGE_BYTES:
            raise PackageRefusal("message", "JSON structure exceeds wire bound")
        if budget < 0:
            raise PackageRefusal("message", "message exceeds wire bound")
    try:
        line = (json.dumps(message, ensure_ascii=False, allow_nan=False,
                           separators=(",", ":")) + "\n").encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise PackageRefusal("message", "invalid JSON") from exc
    if len(line) > MAX_MESSAGE_BYTES:
        raise PackageRefusal("message", "message exceeds wire bound")
    return line


def _wire(message, types, job_id=None):
    _json_object(message, "message")
    if message.get("protocol") != ANALYSIS_PROTOCOL:
        raise PackageRefusal("protocol", "unsupported or missing protocol")
    if not isinstance(message.get("type"), str) or message["type"] not in types:
        raise PackageRefusal("type", "unknown message type")
    value = message.get("job_id")
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise PackageRefusal("job_id", "expected uuid4 hex identifier")
    if job_id is not None and value != job_id:
        raise PackageRefusal("job_id", "wrong job")


def validate_job(message):
    """Validate the closed first-line schema; paths are checked lexically only."""
    _wire(message, {"job"})
    if "operation" not in message:
        raise PackageRefusal("operation", "required field")
    required = {"protocol", "type", "job_id", "release", "operation", "parameters"}
    if message.get("operation") != "self_check":
        required |= {"input", "output_dir"}
    _mapping(message, "", required)
    release = message["release"]
    _mapping(release, "release", {"publisher", "package_id", "version", "artifact_digest"})
    _identifier(release["publisher"], "release.publisher", MAX_PUBLISHER_LENGTH)
    _identifier(release["package_id"], "release.package_id", MAX_PACKAGE_ID_LENGTH)
    _version(release["version"], "release.version")
    _digest(release["artifact_digest"], "release.artifact_digest")
    _text(message["operation"], "operation", MAX_OPERATION_NAME_LENGTH)
    if not re.fullmatch(r"[a-z][a-z0-9_]*", message["operation"]):
        raise PackageRefusal("operation", "expected an operation identifier")
    _json_object(message["parameters"], "parameters")
    if message["operation"] != "self_check":
        _mapping(message["input"], "input", {"dataset"})
        for field, value in (("input.dataset", message["input"]["dataset"]),
                             ("output_dir", message["output_dir"])):
            _text(value, field, MAX_PATH_LENGTH)
            if not Path(value).is_absolute():
                raise PackageRefusal(field, "expected absolute path")
    encode_message(message)
    return deepcopy(message)


def validate_notification(message, *, job_id, operation):
    """Validate notification shape; the handle owns lifecycle ordering."""
    _wire(message, {"acquisition", "writer", "cancel"}, job_id)
    kind = message["type"]
    extra = {"acquisition": {"outcome", "writer"}, "writer": {"state"}, "cancel": set()}[kind]
    _mapping(message, "", {"protocol", "type", "job_id"} | extra)
    if kind != "cancel" and operation == "self_check":
        raise PackageRefusal("type", "self_check has no acquisition lifecycle")
    if kind == "acquisition":
        if message["outcome"] not in ("completed", "failed", "cancelled", "unterminated"):
            raise PackageRefusal("outcome", "unknown acquisition outcome")
        if message["writer"] not in ("finished", "unknown"):
            raise PackageRefusal("writer", "unknown writer state")
        if message["outcome"] == "unterminated" and message["writer"] != "unknown":
            raise PackageRefusal("writer", "unterminated writer must be unknown")
    elif kind == "writer" and message["state"] != "finished":
        raise PackageRefusal("state", "expected finished")
    encode_message(message)
    return deepcopy(message)


def validate_artifact(artifact, field="artifact"):
    _mapping(artifact, field, {"path", "sha256", "validity"})
    _path(artifact["path"], field + ".path")
    _digest(artifact["sha256"], field + ".sha256")
    if artifact["validity"] not in ("partial", "final"):
        raise PackageRefusal(field + ".validity", "expected partial or final")


def validate_worker_message(message, *, job_id, operation):
    """Validate stdout without touching files; output is opaque publisher JSON."""
    _wire(message, {"status", "artifact", "result"}, job_id)
    kind = message["type"]
    extra = {"status": {"message"}, "artifact": {"artifact"},
             "result": {"state", "output", "artifacts"}}[kind]
    if kind == "result":
        if message.get("state") == "failed":
            extra |= {"failure"}
        if operation != "self_check":
            extra |= {"input_complete"}
    _mapping(message, "", {"protocol", "type", "job_id"} | extra)
    if kind == "status":
        _text(message["message"], "message", MAX_STATUS_MESSAGE_LENGTH)
    elif kind == "artifact":
        if operation == "self_check":
            raise PackageRefusal("artifact", "self_check has no artifacts")
        validate_artifact(message["artifact"])
    else:
        if message["state"] not in ("succeeded", "failed", "cancelled"):
            raise PackageRefusal("state", "unknown terminal state")
        _json_object(message["output"], "output")
        _list(message["artifacts"], "artifacts", MAX_ARTIFACTS, empty=True)
        if operation == "self_check" and message["artifacts"]:
            raise PackageRefusal("artifacts", "self_check has no artifacts")
        for i, descriptor in enumerate(message["artifacts"]):
            validate_artifact(descriptor, f"artifacts[{i}]")
        if message["state"] == "failed":
            _mapping(message["failure"], "failure", {"message"})
            _text(message["failure"]["message"], "failure.message", MAX_FAILURE_MESSAGE_LENGTH)
        if operation != "self_check" and type(message["input_complete"]) is not bool:
            raise PackageRefusal("input_complete", "expected boolean")
    encode_message(message)
    return deepcopy(message)
