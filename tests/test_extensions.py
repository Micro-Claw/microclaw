"""Extension contracts. uv fixtures reproduce captured uv 0.12.8 stderr."""
import ast
import importlib
from importlib import metadata
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import venv

import pytest

from microclaw import extensions as ext, updates

UNCACHED = '''Using Python 3.12.14 environment at: uvprobe2
Resolved 2 packages in 214ms
Downloading numpy (5.2MiB)
Downloading h5py (2.9MiB)
 Downloaded h5py
 Downloaded numpy
Prepared 2 packages in 370ms
Installed 2 packages in 11ms
 + h5py==3.16.0
 + numpy==2.5.3
'''
WARM = '''Using Python 3.12.14 environment at: uvprobe3
Resolved 2 packages in 1ms
Installed 2 packages in 11ms
 + h5py==3.16.0
 + numpy==2.5.3
'''
CONFLICT = '''Using Python 3.12.14 environment at: uvprobe2
  × No solution found when resolving dependencies:
  ╰─▶ Because you require numpy>=2.6 and numpy==2.5.3, we can conclude that
      your requirements are unsatisfiable.
'''


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(ext, "user_data_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(ext, "ready", lambda name: False)
    monkeypatch.setattr(ext, "_verify", lambda name: None)
    monkeypatch.setattr(updates, "locate_uv", lambda: "fixture-uv")
    return tmp_path


def uv_fake(monkeypatch, tmp_path, output=UNCACHED, code=0):
    """Real pipes/exit codes; only replace which executable handles uv's argv."""
    script = tmp_path / "uv_fake.py"
    script.write_text(
        "import sys\n"
        "assert sys.argv[1:3] == ['pip', 'install']\n"
        f"sys.stderr.write({output!r})\n"
        f"sys.exit({code})\n", encoding="utf-8")
    real_popen = subprocess.Popen
    calls = []

    def popen(argv, **kw):
        if argv[0] == "fixture-uv":
            assert kw["stdin"] == subprocess.DEVNULL
            pins = Path(argv[argv.index("--constraint") + 1]).read_text(encoding="utf-8")
            calls.append((argv[:], pins))
            argv = [sys.executable, str(script), *argv[1:]]
        return real_popen(argv, **kw)

    monkeypatch.setattr(subprocess, "Popen", popen)
    return calls


def dist(root, name, version, *, directory=None, direct=None):
    location = root / (directory or f"{name}-{version}.dist-info")
    location.mkdir(parents=True)
    (location / "METADATA").write_text(f"Name: {name}\nVersion: {version}\n", encoding="utf-8")
    if direct:
        (location / "direct_url.json").write_text(json.dumps(direct), encoding="utf-8")
    return location


@pytest.fixture
def target(tmp_path):
    root = tmp_path / "target"
    venv.EnvBuilder(with_pip=False).create(root)
    python = root / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    payload = tmp_path / "site.json"
    subprocess.run([str(python), "-I", "-c", "import sysconfig,json,sys;from pathlib import Path;Path(sys.argv[1]).write_text(json.dumps(sysconfig.get_paths()),encoding='utf-8')", str(payload)],
                   stdin=subprocess.DEVNULL, capture_output=True, check=True)
    site = Path(json.loads(payload.read_text(encoding="utf-8"))["purelib"])
    return str(python), site


def test_unknown_extra_refuses_before_spawn(isolated, monkeypatch):
    def spawn(*a, **k):
        pytest.fail("unknown extra spawned a subprocess")
    monkeypatch.setattr(subprocess, "Popen", spawn)
    for name in ("absent", "h5py", "ilastik; rm -rf /", "serve"):
        with pytest.raises(ext.ExtensionInstallError, match="Unknown extension"):
            ext.install(name)


def test_requirements_come_only_from_wheel(isolated, monkeypatch):
    calls = uv_fake(monkeypatch, isolated)
    ext.install("ilastik")
    assert len(calls) == 1, "installation ran more than one resolver"
    for argv, _ in calls:
        assert argv[argv.index("--constraint") + 2:] == ["h5py>=3.10"]


def test_target_paths_pins_banner_and_interpreter(isolated, target, monkeypatch):
    python, site = target
    source = isolated / "editable"
    dist(source, "Editable_Name", "1.2", direct={"url": "file:///source", "dir_info": {"editable": True}})
    direct = isolated / "direct"
    dist(direct, "Direct.Name", "2.3", direct={"url": "https://example.invalid/pkg.whl"})
    platlib = isolated / "platlib"
    dist(platlib, "Platform_Name", "4.5")
    (site / "paths.pth").write_text(f"{source}\n{direct}\n{platlib}\nimport sys; print('real vendor banner')\n", encoding="utf-8")
    monkeypatch.setattr(ext, "sys", SimpleNamespace(executable=python))
    calls = uv_fake(monkeypatch, isolated)
    ext.install("ilastik")
    assert calls[0][1].splitlines() == ["direct-name==2.3", "editable-name==1.2", "platform-name==4.5"]
    assert all(argv[argv.index("--python") + 1] == python for argv, pins in calls)


@pytest.mark.parametrize("bad", ["conflict", "missing-name", "bad-version"])
def test_invalid_metadata_refuses_before_uv(isolated, target, monkeypatch, bad):
    python, site = target
    dist(site, "baz", "1.26.0")
    if bad == "conflict":
        dist(site, "baz", "2.5.3")
    elif bad == "missing-name":
        dist(site, "", "2.0", directory="invalid.dist-info")
    else:
        dist(site, "broken", "not-a-version")
    monkeypatch.setattr(ext, "sys", SimpleNamespace(executable=python))
    calls = uv_fake(monkeypatch, isolated)
    with pytest.raises(ext.ExtensionInstallError) as error:
        ext.install("ilastik")
    assert not calls, "invalid metadata reached uv"
    assert str(site) in str(error.value)
    if bad == "conflict":
        assert "1.26.0" in str(error.value) and "2.5.3" in str(error.value)


def test_cwd_difference_and_isolated_flag(isolated, monkeypatch):
    planted = isolated / "planted"
    dist(planted, "foo", "9.9.9", directory="foo.egg-info")
    monkeypatch.syspath_prepend("")
    monkeypatch.chdir(planted)
    bare = list(metadata.distributions())
    assert any(d.metadata.get("Name") == "foo" for d in bare)
    monkeypatch.chdir(isolated)
    clean = list(metadata.distributions())
    assert len(bare) - len(clean) == 1
    monkeypatch.chdir(planted)
    monkeypatch.setenv("PYTHONPATH", str(planted))
    work = isolated / "empty"
    work.mkdir()
    calls = []
    real_run = subprocess.run
    def run(argv, **kwargs):
        calls.append(argv)
        return real_run(argv, **kwargs)
    monkeypatch.setattr(subprocess, "run", run)
    pins = ext._pins(sys.executable, work)
    assert "foo==9.9.9" not in pins, "caller CWD/PYTHONPATH leaked into target pins"
    assert "-I" in calls[0], "enumeration omitted isolated mode"
    monkeypatch.chdir(isolated)
    assert ext._pins(sys.executable, work) == pins


def test_payload_file_not_stdout(isolated, monkeypatch):
    records = [{"name": "Only_File", "version": "1.0", "origin": "/public"}]
    def run(argv, **kw):
        Path(argv[-1]).write_text(json.dumps(records), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="noise, not JSON")
    monkeypatch.setattr(subprocess, "run", run)
    assert ext._pins("python", isolated) == ["only-file==1.0"]
    (isolated / "distributions.json").unlink()
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0, stdout=json.dumps(records)))
    with pytest.raises(ext.ExtensionInstallError, match="payload is missing"):
        ext._pins("python", isolated)


def test_helper_uses_public_origin(tmp_path, monkeypatch):
    double = SimpleNamespace(metadata={"Name": "fixture"}, version="1", locate_file=lambda p: Path("/public"))
    monkeypatch.setattr(metadata, "distributions", lambda: [double])
    monkeypatch.setattr(sys, "argv", ["helper", str(tmp_path / "payload")])
    exec(ext.ENUMERATE_DISTRIBUTIONS, {})
    assert json.loads((tmp_path / "payload").read_text(encoding="utf-8"))[0]["origin"] == str(Path("/public"))


def test_live_pins_ignore_stale_record(isolated, target, monkeypatch):
    python, site = target
    dist(site, "numpy", "2.5.3")
    monkeypatch.setattr(ext, "sys", SimpleNamespace(executable=python))
    state = isolated / "state"
    state.mkdir()
    (state / "constraints.txt").write_text("numpy==1.26.0\n", encoding="utf-8")
    (state / "extensions.json").write_text(json.dumps({"installed": {"ilastik": {"requirements": ["numpy==1.26.0"]}}}), encoding="utf-8")
    calls = uv_fake(monkeypatch, isolated)
    ext.install("ilastik")
    assert calls[-1][1] == "numpy==2.5.3\n", "install reused a stale pin"
    assert not (state / "distributions.json").exists()


def test_conflict_preserves_environment(isolated, target, monkeypatch):
    python, site = target
    dist(site, "numpy", "2.5.3")
    monkeypatch.setattr(ext, "sys", SimpleNamespace(executable=python))
    monkeypatch.setattr(ext, "requirements_for", lambda name: ["numpy>=2.6"])
    before = {str(p): p.read_bytes() for p in site.rglob("*") if p.is_file()}
    calls = uv_fake(monkeypatch, isolated, CONFLICT, 1)
    with pytest.raises(ext.ExtensionInstallError, match=r"numpy>=2.6 and numpy==2.5.3"):
        ext.install("ilastik")
    assert calls[-1][0][-1] == "numpy>=2.6"
    assert calls[-1][1] == "numpy==2.5.3\n"
    assert {str(p): p.read_bytes() for p in site.rglob("*") if p.is_file()} == before
    assert "No solution found" in ext._records()["errors"]["ilastik"]


def test_readiness_import_valueerror(monkeypatch):
    monkeypatch.setattr(metadata, "version", lambda name: "3.16.0")
    monkeypatch.setattr(metadata, "packages_distributions", lambda: {"h5py": ["h5py"]})
    def broken(name):
        raise ValueError("numpy.dtype size changed")
    monkeypatch.setattr(importlib, "import_module", broken)
    assert not ext.ready("ilastik"), "distribution presence was mistaken for readiness"


def test_readiness_normalizes_distribution_and_imports(monkeypatch):
    monkeypatch.setattr(ext, "requirements_for", lambda name: ["fixture-dist>=1"])
    monkeypatch.setattr(metadata, "version", lambda name: "1.0")
    monkeypatch.setattr(metadata, "packages_distributions", lambda: {"fixture_module": ["Fixture_Dist"]})
    imported = []
    monkeypatch.setattr(importlib, "import_module", imported.append)
    assert ext.ready("ilastik")
    assert imported == ["fixture_module"]


def test_installed_not_usable_names_exception(isolated, monkeypatch):
    calls = uv_fake(monkeypatch, isolated)
    def broken(name):
        raise ValueError("numpy.dtype size changed")
    monkeypatch.setattr(ext, "_verify", broken)
    with pytest.raises(ext.ExtensionInstallError, match="installed, not usable: ValueError: numpy.dtype size changed"):
        ext.install("ilastik")
    assert "ilastik" not in ext._records()["installed"]


def test_consumers_import_extensions_lazily():
    modules = metadata.packages_distributions()
    optional = {module for name in ext.EXTENSIONS for req in ext.requirements_for(name)
                for module, dists in modules.items()
                if canonical(req) in {canonical(d) for d in dists}}
    assert optional, "fixture environment must expose the extension module metadata"
    def imports_at_scope(nodes):
        for node in nodes:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if isinstance(node, ast.Import):
                yield from (n.name.split('.')[0] for n in node.names)
            elif isinstance(node, ast.ImportFrom):
                yield (node.module or '').split('.')[0]
            for _, value in ast.iter_fields(node):
                if isinstance(value, list):
                    yield from imports_at_scope([v for v in value if isinstance(v, ast.AST)])
    for path in Path(ext.__file__).parent.rglob("*.py"):
        imports = set(imports_at_scope(ast.parse(path.read_text(encoding="utf-8")).body))
        assert not imports & optional, f"{path.name} imports extension at module scope: {imports & optional}"


def canonical(text):
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
    return canonicalize_name(Requirement(text).name)


def test_rejects_compound_marker(monkeypatch):
    from email.message import Message
    meta = Message()
    meta["Provides-Extra"] = "ilastik"
    meta["Requires-Dist"] = 'h5py>=3.10; python_version < "3.13" and extra == "ilastik"'
    monkeypatch.setattr(metadata, "metadata", lambda name: meta)
    with pytest.raises(ext.ExtensionInstallError, match="unsupported environment marker"):
        ext.requirements_for("ilastik")


def test_record_and_update_writes_are_independent(isolated):
    update = isolated / "state" / "update-state.json"
    stale = {"staging": "running"}
    updates.write_state(stale, update)
    ext.record("ilastik", ["h5py>=3.10"])
    updates.write_state({**stale, "staging": "ready"}, update)
    assert ext._records()["installed"]["ilastik"]["requirements"] == ["h5py>=3.10"]
    assert json.loads(update.read_text(encoding="utf-8"))["staging"] == "ready"
    ext.forget("ilastik")
    assert not ext._records()["installed"]


@pytest.mark.parametrize("output", [UNCACHED, WARM])
def test_transcripts_progress(isolated, monkeypatch, output):
    calls = uv_fake(monkeypatch, isolated, output)
    seen = []
    result = ext.install("ilastik", progress=lambda **kw: seen.append(kw))
    phases = [s["phase"] for s in seen if "phase" in s]
    assert phases[:3] == ["checking environment", "running package installer", "Resolution complete"]
    assert phases[-2:] == ["Package installation complete", "verifying"]
    assert result["added"] == ["h5py==3.16.0", "numpy==2.5.3"]
    assert ext.progress_phase("unclassified output") is None


@pytest.mark.parametrize("key", ["UV_INDEX_URL", "UV_DEFAULT_INDEX", "UV_EXTRA_INDEX_URL", "PIP_INDEX_URL"])
def test_index_failure_names_variable(isolated, monkeypatch, key):
    monkeypatch.setenv(key, "https://secret:token@example.invalid/simple")
    uv_fake(monkeypatch, isolated, "fetch failed https://secret:token@example.invalid/simple", 1)
    with pytest.raises(ext.ExtensionInstallError) as error:
        ext.install("ilastik")
    assert key in str(error.value)
    assert "secret" not in str(error.value) and "token" not in str(error.value)


def test_catalog_name_absent_from_provides_extra_refuses(isolated, monkeypatch):
    from email.message import Message
    meta = Message()
    meta['Provides-Extra'] = 'serve'
    meta['Requires-Dist'] = 'h5py>=3.10; extra == "ilastik"'
    monkeypatch.setattr(metadata, 'metadata', lambda name: meta)
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **kw: pytest.fail('absent Provides-Extra spawned uv'))
    with pytest.raises(ext.ExtensionInstallError, match='Unknown extension'):
        ext.install('ilastik')


def test_enumerator_failure_and_timeout_refuse_before_uv(isolated, monkeypatch):
    def failed(argv, **kw):
        kw['stderr'].write(b'broken site initialization')
        kw['stderr'].flush()
        return SimpleNamespace(returncode=1)
    monkeypatch.setattr(subprocess, 'run', failed)
    with pytest.raises(ext.ExtensionInstallError, match='broken site initialization'):
        ext._pins('target', isolated)
    def timeout(*a, **kw):
        raise subprocess.TimeoutExpired('target', 60)
    monkeypatch.setattr(subprocess, 'run', timeout)
    with pytest.raises(ext.ExtensionInstallError, match='enumerate the target environment'):
        ext._pins('target', isolated)


def test_gate_keeps_only_changed_observations(tmp_path):
    gate = Path(__file__).resolve().parents[1] / "design" / "71-block71a-demo-gate.py"
    result = subprocess.run([sys.executable, str(gate), "--selftest", "--out", str(tmp_path)],
                            stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
    # Score the gate from gate.log, which the gate owns and writes with an
    # explicit encoding -- not from captured stdout, which came back as None on
    # the Windows CI runner and turned this assertion into a TypeError. It is
    # the same rule the gate itself follows: read the artifact, not the stream.
    log_path = tmp_path / "gate.log"
    assert log_path.exists(), (result.stdout or b"").decode("utf-8", "replace")
    log = log_path.read_text(encoding="utf-8")
    assert "FAIL " not in log, log
    observations = json.loads((tmp_path / "observations.json").read_text(encoding="utf-8"))
    assert observations
    assert all(a["state"] != b["state"] for a, b in zip(observations, observations[1:])), "gate retained duplicate poll observations"
    assert all(a["at"] <= b["at"] for a, b in zip(observations, observations[1:]))


def test_record_error_clear_preserves_install_metadata(isolated):
    ext.record("ilastik", ["h5py>=3.10"])
    installed = ext._records()["installed"]
    ext.record("ilastik", None, error="staged extras failed")
    assert ext.available()[0]["error"] == "staged extras failed"
    ext.record("ilastik", None)
    assert ext._records()["installed"] == installed
    assert "ilastik" not in ext._records()["errors"]
    assert ext.available()[0]["error"] is None
