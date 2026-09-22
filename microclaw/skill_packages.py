"""Community release format, without admission, installation or execution.

The schema is the closed mapping checked by ``validate_manifest``. Metadata is
in the manifest; SKILL.md is opaque publisher text (not first-party frontmatter).
An artifact reference is a publisher HTTPS URL; only the *external* intake's
SHA-256 pins its bytes. A caller supplying verified installed records attests
that the manifest came from those bytes. This module does not establish trust.

Operations carry name/input_schema/output_schema. Here a schema is an object
with a nonempty type label; protocol support and schema semantics belong to 83c.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import keyword
from pathlib import Path
import re
from urllib.parse import urlsplit

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

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
MAX_SCHEMA_TYPE_LENGTH = 64
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


def validate_intake(record):
    """Validate the unsigned structural record only; never an admission verdict."""
    if not isinstance(record, dict):
        raise PackageRefusal("intake", "expected an object")
    _mapping(record, "", {"package_id", "publisher", "version", "artifact", "artifact_digest"})
    _identifier(record["package_id"], "package_id", MAX_PACKAGE_ID_LENGTH)
    _identifier(record["publisher"], "publisher", MAX_PUBLISHER_LENGTH)
    _version(record["version"], "version")
    _url(record["artifact"], "artifact")
    _digest(record["artifact_digest"], "artifact_digest")
    return deepcopy(record)


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
    _range(manifest["microclaw"], "microclaw")
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
        _range(manifest["python"], "python")
        _list(manifest["platforms"], "platforms", MAX_PLATFORMS)
        platforms = set()
        for i, platform in enumerate(manifest["platforms"]):
            field = f"platforms[{i}]"
            _text(platform, field, MAX_PLATFORM_LENGTH)
            if not re.fullmatch(r"[a-z0-9]+(?:[_-][a-z0-9]+)*", platform) or platform in platforms:
                raise PackageRefusal(field, "expected a unique platform tag")
            platforms.add(platform)
        _version(manifest["protocol_version"], "protocol_version")
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
                _text(schema["type"], field + "." + key + ".type", MAX_SCHEMA_TYPE_LENGTH)
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


def _enabled_releases(records):
    # Records are explicit caller data, never discovered from directories.
    for record in records:
        if record.get("enabled") is not True or record.get("verified") is not True:
            continue
        manifest = validate_manifest(record["manifest"])
        intake = validate_intake(record["intake"])
        for field in ("publisher", "package_id", "version", "artifact"):
            if manifest[field] != intake[field]:
                raise PackageRefusal("intake." + field, "does not match verified manifest")
        yield record, manifest, intake


def _provenance(manifest, intake):
    return (f"publisher-provided; publisher={manifest['publisher']}; "
            f"release={manifest['version']} sha256:{intake['artifact_digest']}")


def external_catalog_lines(records):
    """Render enabled verified metadata; bounded formatting is not trust/authority.

    Raising for a malformed record is deliberate in 83a; per-record isolation
    and the disabled-and-why discovery state land in 83e.
    """
    lines = []
    names = set()
    for _, manifest, intake in _enabled_releases(records):
        for skill in manifest["skills"]:
            name = qualified_name(manifest["publisher"], manifest["package_id"], skill["name"])
            if name in names:
                raise PackageRefusal("name", "ambiguous enabled external skill")
            names.add(name)
            lines.append(f"- {name} [{_provenance(manifest, intake)}]: {skill['description']}")
    return tuple(lines)


def load_external_skill(name, records):
    """Return text and operations with the same release identity, without executing.

    ``records`` contains plain dictionaries with enabled/verified booleans,
    manifest, intake and release_dir. Verification is a caller obligation (83b/d),
    not something a package can assert for itself. All declared assets are checked
    so a changed ancillary file cannot accompany apparently intact skill prose.
    Missing caller-owned record keys raise KeyError as programming errors; package
    format and asset failures use PackageRefusal.
    """
    publisher, package, skill_name = parse_qualified_name(name)
    matches = []
    for record, manifest, intake in _enabled_releases(records):
        if (manifest["publisher"], manifest["package_id"]) == (publisher, package):
            for skill in manifest["skills"]:
                if skill["name"] == skill_name:
                    matches.append((record, manifest, intake, skill))
    if len(matches) != 1:
        raise PackageRefusal("name", "unknown or ambiguous enabled verified external skill")
    record, manifest, intake, skill = matches[0]
    skill_bytes = None
    for i, asset in enumerate(manifest["assets"]):
        field = f"assets[{i}]"
        path = safe_release_path(record["release_dir"], asset["path"], field=field + ".path")
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise PackageRefusal(field + ".path", "asset is not a readable file") from exc
        if hashlib.sha256(content).hexdigest() != asset["sha256"]:
            raise PackageRefusal(field + ".sha256", "asset digest mismatch")
        if asset["path"] == skill["path"]:
            skill_bytes = content
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
