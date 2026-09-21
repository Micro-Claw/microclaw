"""First-party extras from the running wheel; imports in consumers stay lazy."""
import importlib
from importlib import metadata
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version, InvalidVersion

from microclaw import updates
from microclaw.paths import user_data_dir

EXTENSIONS = {"ilastik": "Read and run ilastik pixel-classification projects (.ilp)."}


class ExtensionInstallError(RuntimeError):
    pass


# -I excludes caller CWD, PYTHONPATH and user-site accidents. Normal site
# startup preserves .pth editables AND banners, so the payload is a file.
ENUMERATE_DISTRIBUTIONS = '''
import json, sys
from importlib.metadata import distributions
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps([
    {"name": d.metadata.get("Name"), "version": d.version,
     "origin": str(d.locate_file(""))} for d in distributions()
]), encoding="utf-8")
'''


def requirements_for(name):
    meta = metadata.metadata("microclaw")
    if not isinstance(name, str) or name not in EXTENSIONS or name not in meta.get_all("Provides-Extra", []):
        raise ExtensionInstallError(f"Unknown extension: {name!r}.")
    result = []
    for text in meta.get_all("Requires-Dist", []):
        req = Requirement(text)
        marker = str(req.marker) if req.marker else ""
        # Inspect extra clauses without evaluating any platform/environment
        # marker. A compound expression cannot be safely stripped in v1.
        clauses = re.findall(r'\bextra\s*==\s*"([^"]+)"', marker)
        if name not in clauses:
            continue
        if marker != f'extra == "{name}"':
            raise ExtensionInstallError(f"Extension {name} has an unsupported environment marker: {marker}.")
        req.marker = None
        result.append(str(req))
    if not result:
        raise ExtensionInstallError(f"Extension {name} has no installable requirements.")
    return result


def _verify(name):
    requirements = requirements_for(name)
    provided = metadata.packages_distributions()
    for text in requirements:
        req = Requirement(text)
        version = metadata.version(req.name)  # Distribution presence is separate from import.
        if req.specifier and not req.specifier.contains(version, prereleases=True):
            raise ImportError(f"{req.name} {version} does not satisfy {req.specifier}")
        modules = sorted(module for module, dists in provided.items()
                         if canonicalize_name(req.name) in {canonicalize_name(d) for d in dists})
        if not modules:
            raise ImportError(f"No top-level modules were reported for {req.name}")
        for module in modules:
            importlib.import_module(module)


def ready(name):
    try:
        _verify(name)
    except (ImportError, ValueError):
        return False
    return True


def _records():
    path = user_data_dir() / "extensions.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"installed": {}}
    except (OSError, ValueError) as exc:
        raise ExtensionInstallError(f"Could not read extension records: {exc}.") from exc
    if not isinstance(value, dict) or not isinstance(value.get("installed"), dict):
        raise ExtensionInstallError("Could not read extension records: expected an installed object.")
    if not isinstance(value.get("errors", {}), dict):
        raise ExtensionInstallError("Could not read extension records: expected an errors object.")
    return value


def recorded_errors():
    """Read panel diagnostics; damaged records must not hide cached readiness."""
    try:
        return _records().get("errors", {})
    except ExtensionInstallError:
        return {}


def record(name, requirements, *, error=None):
    value = _records()
    if error is None:
        # Staging clears an error without claiming an install in the running slot.
        if requirements is not None:
            marker = updates.read_slot_marker() or {}
            value["installed"][name] = {"at": time.time(), "commit": marker.get("commit"),
                                         "requirements": requirements}
        value.setdefault("errors", {}).pop(name, None)
    else:
        value.setdefault("errors", {})[name] = error
    updates._write_json_atomic(value, user_data_dir() / "extensions.json")


def forget(name):
    value = _records()
    value["installed"].pop(name, None)
    value.setdefault("errors", {}).pop(name, None)
    updates._write_json_atomic(value, user_data_dir() / "extensions.json")


def available():
    from microclaw.skills import SKILL_CATALOG
    value = _records()
    result = []
    for name, description in EXTENSIONS.items():
        requirements = requirements_for(name)
        result.append({"name": name, "description": description,
                       "requirements": requirements,
                       "packages": [Requirement(r).name for r in requirements],
                       "skills": [s.name for s in SKILL_CATALOG
                                  if name in getattr(s, "requires", ())],
                       "ready": ready(name), "recorded": name in value["installed"],
                       "error": value.get("errors", {}).get(name)})
    return result


def _pins(python, work):
    payload = work / "distributions.json"
    # File-backed diagnostic streams bound memory even if site startup is noisy.
    with (work / "enumerate.out").open("w+b") as out, (work / "enumerate.err").open("w+b") as err:
        try:
            result = subprocess.run([python, "-I", "-c", ENUMERATE_DISTRIBUTIONS, str(payload)],
                                    cwd=work, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                    timeout=60, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ExtensionInstallError(f"Could not enumerate the target environment: {exc}.") from exc
        if result.returncode:
            err.seek(max(0, err.tell() - 4096))
            tail = err.read().decode("utf-8", errors="replace")
            raise ExtensionInstallError(f"Target environment enumeration failed: {tail or result.returncode}.")
    try:
        records = json.loads(payload.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ExtensionInstallError(f"Target environment metadata payload is missing or invalid: {exc}.") from exc
    if not isinstance(records, list):
        raise ExtensionInstallError("Target environment metadata is not a list.")
    versions, origins = {}, {}
    for item in records:
        if not isinstance(item, dict):
            raise ExtensionInstallError("An installed distribution has no usable identity.")
        raw, version, origin = item.get("name"), item.get("version"), item.get("origin")
        try:
            if not isinstance(raw, str) or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?", raw):
                raise ValueError("invalid name")
            if not isinstance(version, str) or not version or any(c.isspace() for c in version):
                raise ValueError("invalid version")
            Version(version)
        except (ValueError, InvalidVersion):
            raise ExtensionInstallError(f"An installed distribution has no usable identity at {origin}: {raw!r} {version!r}.") from None
        name = canonicalize_name(raw)
        if name == "microclaw":
            continue
        if name in versions and versions[name] != version:
            raise ExtensionInstallError(f"Conflicting installed metadata for {name}: {versions[name]} at {origins[name]} and {version} at {origin}; remove the stale metadata entry.")
        versions[name], origins[name] = version, origin
    return [f"{name}=={version}" for name, version in sorted(versions.items())]


def progress_phase(line):
    line = line.strip()
    for word, phase in (("Resolved", "Resolution complete"), ("Prepared", "Package preparation complete"),
                        ("Installed", "Package installation complete")):
        if re.match(rf"{word} \d+ packages?\b", line):
            return phase
    match = re.match(r"(Downloading|Downloaded) ([A-Za-z0-9_.-]+)(?:\s|$)", line)
    return f"{match[1]} {match[2]}" if match else None


def _installer_error(text):
    # Never echo credentials or an index URL; identify the controlling variable.
    for key in ("UV_INDEX_URL", "UV_DEFAULT_INDEX", "UV_EXTRA_INDEX_URL", "PIP_INDEX_URL"):
        if os.environ.get(key):
            text = text.replace(os.environ[key], f"<{key}>")
    text = re.sub(r"https?://\S+", "<package index URL>", text)
    overrides = [k for k in ("UV_INDEX_URL", "UV_DEFAULT_INDEX", "UV_EXTRA_INDEX_URL", "PIP_INDEX_URL") if os.environ.get(k)]
    return "Package installer failed: " + text.strip() + (f" Check index overrides: {', '.join(overrides)}." if overrides else "")


def install(name, *, progress=lambda **state: None):
    requirements = requirements_for(name)  # Refuse request text before any subprocess.
    try:
        progress(phase="checking environment")
        if ready(name):
            record(name, requirements)
            return {"message": f"{name} is already installed.", "added": []}
        python = sys.executable
        uv = updates.locate_uv()
        with tempfile.TemporaryDirectory(prefix="microclaw-extension-") as temporary:
            work = Path(temporary)
            pins = _pins(python, work)
            constraints = work / "constraints.txt"
            constraints.write_text("\n".join(pins) + "\n", encoding="utf-8")
            # No package name in this call came from the network.
            argv = [uv, "pip", "install", "--python", python, "--constraint", str(constraints), *requirements]
            progress(phase="running package installer")
            tail = deque(maxlen=100)
            added = []
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                       stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")

            def read_stderr():
                # The one consumer fans out conflict diagnostics and live phases.
                for line in process.stderr:
                    tail.append(line[-4096:])
                    phase = progress_phase(line)
                    if phase:
                        progress(phase=phase)
                    match = re.match(r"^ \+ ([A-Za-z0-9_.-]+==\S+)", line)
                    if match:
                        added.append(match[1])

            reader = threading.Thread(target=read_stderr, name="microclaw-extension-stderr", daemon=True)
            try:
                reader.start()
                code = process.wait()
                reader.join()
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
                process.stderr.close()
            if code:
                raise ExtensionInstallError(_installer_error("".join(tail) or f"exit {code}"))
        progress(phase="verifying")
        importlib.invalidate_caches()
        try:
            _verify(name)
        except (ImportError, ValueError) as exc:
            raise ExtensionInstallError(f"{name} is installed, not usable: {type(exc).__name__}: {exc}.") from exc
        record(name, requirements)
        return {"message": f"{name} is ready.", "added": added}
    except Exception as exc:
        message = str(exc) or type(exc).__name__
        try:
            record(name, requirements, error=message)
        except Exception as record_error:
            message += f" Could not save extension failure: {record_error}."
        raise ExtensionInstallError(message) from exc
