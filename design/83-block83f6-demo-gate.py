"""83f-6 Windows panel/PyPI gate; offline selftest makes no live install claim."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from datetime import datetime
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import traceback
import urllib.request
import zipfile

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('gate83f3', HERE / '83-block83f3-demo-gate.py')
gate83f3 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate83f3)
read_json, write_json, now = gate83f3.read_json, gate83f3.write_json, gate83f3.now
isolated_store, NotExercised = gate83f3.isolated_store, gate83f3.NotExercised
BASE = 'https://raw.githubusercontent.com/Micro-Claw/microclaw/block-83f-6/design/83f6-gate/'
FIXTURES = HERE / '83f6-gate'
POINTER = '83f6-gate-evidence.txt'
ZIP = 'fixture-lab-lock-fixture-1.0.0.zip'
PHASES = gate83f3.PHASES
LIMBS = ('1 Real catalog fetch', '2 Active release', '3 PyPI route',
         '4 Exact Windows lock', '5 Scientific imports')


def roots():
    return dict(gate83f3.roots(), catalog_url=BASE + 'catalog-1/')


def disk_opener(directory):
    """Reuse 83f-3's bounded urllib/404-shaped disk fake with our URL prefix."""
    open_file = gate83f3.disk_opener(directory)
    def open_request(request, timeout):
        assert request.full_url.startswith(BASE), 'unexpected offline URL: ' + request.full_url
        translated = urllib.request.Request(gate83f3.BASE + request.full_url[len(BASE):],
                                            headers=dict(request.header_items()))
        return open_file(translated, timeout)
    return open_request


def release(directory=FIXTURES):
    return read_json(Path(directory) / 'catalog-1/catalog.json')['releases'][0]


def fixture_manifest(directory=FIXTURES):
    with zipfile.ZipFile(Path(directory) / 'artifacts' / ZIP) as archive:
        return json.loads(archive.read('manifest.json'))


def pins(lock):
    return {canonicalize_name(req.name): next(iter(req.specifier)).version
            for item in lock for req in [Requirement(item['requirement'])]}


def validate_fixtures(directory):
    """Product verification of signatures, catalog, download, manifest and locks."""
    from microclaw import catalog_intake
    with tempfile.TemporaryDirectory() as temporary, isolated_store(temporary) as store:
        write_json(store.store_dir() / 'trust/roots.json', roots())
        opener = disk_opener(directory)
        state = store.refresh_catalog(opener=opener, now=now())
        assert state['last_success'] and not state['error'], state
        entries = store.catalog_entries(now=now())['releases']
        assert len(entries) == 1 and entries[0]['compatible'], entries
        entry = release(directory)
        assert entries[0]['artifact_digest'] == entry['artifact_digest']
        package = store.store_dir() / 'download-probe'
        package.mkdir()
        with store.download_release(entry, package=package, opener=opener) as artifact:
            manifest = catalog_intake.archive_checks(artifact, entry)
        assert set(manifest['locks']) == {'win_amd64', 'macosx_arm64', 'manylinux_x86_64'}
        assert set(pins(manifest['locks']['win_amd64'])) == {'numpy', 'scipy', 'h5py', 'tifffile'}
        for platform in ('macosx_arm64', 'manylinux_x86_64'):
            assert set(pins(manifest['locks'][platform])) == {'numpy', 'scipy', 'h5py'}
        return manifest


def fixtures(destination=FIXTURES):
    """Read committed uv output; never resolve, compile, or use network."""
    from microclaw import catalog_intake
    builder = gate83f3.gate83d.load_builder()
    with tempfile.TemporaryDirectory() as temporary:
        stage, work = Path(temporary) / 'publish', Path(temporary) / 'package'
        (stage / 'artifacts').mkdir(parents=True)
        shutil.copytree(gate83f3.gate83d.FIXTURES / 'executable', work,
                        ignore=shutil.ignore_patterns('__pycache__'))
        manifest = read_json(work / 'manifest.json')
        manifest.update(package_id='lock-fixture', publisher='fixture-lab',
                        platforms=['win_amd64', 'macosx_arm64', 'manylinux_x86_64'])
        manifest.pop('locks')
        write_json(work / 'manifest.json', manifest)
        manifest, digest = catalog_intake.pack(work, BASE + 'artifacts/' + ZIP,
                                              stage / 'artifacts' / ZIP, locks=FIXTURES / 'locks')
        entry = builder.sign(catalog_intake.intake_from_manifest(manifest, digest), 'publisher-a')
        policy = read_json(gate83f3.gate83d.FIXTURES / 'trust/policy-TEST-ONLY.json')
        write_json(stage / 'catalog-1/policy.json', builder.sign(policy, 'root'))
        write_json(stage / 'catalog-1/catalog.json', dict(type='microclaw.catalog.v1',
                                                        releases=[entry], withdrawals=[]))
        assert validate_fixtures(stage) == manifest
        for path in stage.rglob('*'):
            if path.is_file():
                target = Path(destination) / path.relative_to(stage)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(path.read_bytes())
    print('FIXTURES: pack --locks from committed uv output; catalog and artifact product-verified; TEST-ONLY keys', flush=True)


class Gate(gate83f3.Gate):
    # A machine that has used packages has a real store (83f-5's gate kept one on
    # the demo machine). The gate never deletes it: prepare renames it aside and
    # cleanup renames it back. One rename each way; nothing copied or deleted.
    @property
    def saved_store(self):
        return self.store_root.with_name(self.store_root.name + '.83f6-saved')

    def test_store(self):
        try:
            return read_json(self.store_root / 'trust/roots.json').get('environment') == 'test'
        except (OSError, ValueError, AttributeError):
            return False

    def prepare(self):
        if self.saved_store.exists():
            raise NotExercised(f'your package store is still set aside at {self.saved_store}; '
                               'run -Phase cleanup first, which puts it back')
        set_aside = None
        if self.store_root.exists():
            if self.test_store():
                raise NotExercised(f'{self.store_root} is a TEST-ONLY store from an earlier round; run -Phase cleanup first')
            self.close_microclaw('Prepare sets your package store aside for this gate; cleanup puts it back.')
            try:
                os.replace(self.store_root, self.saved_store)
            except OSError as exc:
                raise NotExercised(f'could not set {self.store_root} aside ({exc}); nothing was moved. '
                                   'Close MicroClaw and run prepare again') from exc
            set_aside = str(self.saved_store)
            self.say(f'Your package store was set aside at {self.saved_store}; cleanup puts it back.')
        write_json(self.store_root / 'trust/roots.json', roots())
        self.save('prepare', dict(prepared_at=now().isoformat(), set_aside=set_aside))
        self.say('Prepared TEST-ONLY roots only. Startup is the first catalog fetch.')

    def session(self):
        self.need('prepare')
        session = dict(started_at=now().isoformat(), answers={}, completed=[])
        self.save('session', session)
        session['launch'] = self.launch_desktop('Session phase; type DONE in PowerShell, not in chat.')
        self.save('session', session)
        self.say('STEP 1')
        try:
            session['answers']['install'] = self.ask(
                'In Firefox, open Community skill packages. Click Install on fixture-lab/lock-fixture, '
                'then Install in the box. Wait until the row shows installed (several minutes is normal; '
                'scipy is about 37 MB). Type DONE here in PowerShell when installed; do not paste chat text here.',
                'install', kind='done')
        except NotExercised:
            session['stopped_at'] = 1
            self.save('session', session)
            raise
        self.snapshot(1)
        session['completed'] = [1]
        self.save('session', session)

    def cleanup(self):
        saved = self.saved_store
        if saved.exists() and self.store_root.exists() and not self.test_store():
            raise NotExercised(f'both {self.store_root} (not TEST-ONLY) and your set-aside store {saved} exist; '
                               'nothing changed. Decide which to keep by hand')
        if self.store_root.exists() and not self.test_store():
            self.say(f'Nothing to clean up: {self.store_root} is your own store (no TEST-ONLY roots); left untouched.')
            (self.root / POINTER).unlink(missing_ok=True)
            return dict(roots_present=False, store_present=False, saved_store_present=False,
                        restored_store=None, evidence=str(self.out))
        if self.store_root.exists():
            result = super().cleanup()  # roots-first removal and TEST-ONLY ownership check
        else:
            if saved.exists():
                self.close_microclaw('Cleanup puts your package store back.')
            result = dict(roots_present=False, store_present=False, evidence=str(self.out))
        (self.root / POINTER).unlink(missing_ok=True)
        result['restored_store'] = None
        if saved.exists() and not result['store_present']:
            os.replace(saved, self.store_root)
            result['restored_store'] = str(self.store_root)
            self.say(f'Your package store is back at {self.store_root}.')
        result['saved_store_present'] = saved.exists()
        if 'retained_root_files' in result:
            result['retained_root_files'] = sorted(p.name for p in self.root.iterdir() if p.is_file())
        return result


def active_record(gate):
    snapshot = gate83f3.snapshot(gate, 1)
    package = snapshot / 'packages/lock-fixture'
    pointer = package / 'pointer.json'
    if not pointer.is_file() or not read_json(pointer).get('active'):
        raise NotExercised('no active lock-fixture install was captured')
    install_id = read_json(pointer)['active']
    # IDs are product-generated directory names. Do not let transported evidence
    # point the verifier at arbitrary files outside this package.
    assert isinstance(install_id, str) and Path(install_id).name == install_id
    path = package / 'installs' / install_id / 'install.json'
    if not path.is_file():
        raise NotExercised('active install record is absent')
    record = read_json(path)
    assert record['install_id'] == install_id
    return record, path


def verify(gate, *, cleanup=True):
    results = {}
    expected = release()
    committed = fixture_manifest()
    expected_pins = pins(committed['locks']['win_amd64'])

    def score(name, fn):
        try:
            results[name] = ('PASS', fn())
        except NotExercised as exc:
            results[name] = ('NOT EXERCISED', str(exc))
        except Exception as exc:
            results[name] = ('FAIL', f'{type(exc).__name__}: {exc}')

    def fetch():
        snapshot = gate83f3.snapshot(gate, 1)
        state = read_json(snapshot / 'catalog/state.json')
        configured = read_json(snapshot / 'trust/roots.json')
        assert configured == roots(), configured
        assert state.get('last_success') and not state.get('error'), state
        began = datetime.fromisoformat(gate.need('prepare')['prepared_at'])
        success = datetime.fromisoformat(state['last_success'])
        captured = datetime.fromisoformat(read_json(snapshot / 'snapshot.json')['captured_at'])
        assert began <= success <= captured, 'catalog success is outside this prepared session'
        assert read_json(snapshot / 'catalog/catalog.json') == read_json(FIXTURES / 'catalog-1/catalog.json')
        return 'product catalog state records success with TEST-ONLY roots at ' + roots()['catalog_url']

    def active():
        record, _ = active_record(gate)
        assert record['state'] == 'ready', record
        digest = hashlib.sha256((FIXTURES / 'artifacts' / ZIP).read_bytes()).hexdigest()
        assert record['artifact_digest'] == expected['artifact_digest'] == digest
        assert record['intake'] == expected
        return 'active release is ready; digest equals committed artifact'

    def pypi():
        record, _ = active_record(gate)
        assert record['find_links'] is None, record['find_links']
        return 'install record find_links is null (index route)'

    def exact_lock():
        record, _ = active_record(gate)
        lock = record['manifest']['locks']['win_amd64']
        assert lock == committed['locks']['win_amd64'], 'installed lock differs from pack fixture'
        assert set(pins(lock)) == {'numpy', 'scipy', 'h5py', 'tifffile'}
        assert record['interpreter']['distributions'] == expected_pins, record['interpreter']
        python = record.get('python')
        if not python or not Path(python).is_file():
            raise NotExercised('release environment python is absent; verify on the demo machine before cleanup')
        identity = gate.store.probe(python)  # product probe runs child with stdin closed
        gate.save('probe', identity)
        assert gate.store._platform(identity, record['manifest']) == 'win_amd64', identity
        assert identity['distributions'] == expected_pins, identity
        return 'fresh release probe and saved distributions equal the four pack-filled Windows pins, including tifffile'

    def imports():
        record, _ = active_record(gate)
        python = record.get('python')
        if not python or not Path(python).is_file():
            raise NotExercised('release environment python is absent')
        command = [python, '-I', '-c',
                   'import json, numpy, scipy, h5py, tifffile; '
                   'print(json.dumps({m.__name__: m.__version__ for m in (numpy, scipy, h5py, tifffile)}, sort_keys=True))']
        result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True,
                                text=True, encoding='utf-8', errors='replace', timeout=120)
        gate.save('imports', dict(command=command, returncode=result.returncode,
                                  stdout=result.stdout, stderr=result.stderr))
        assert result.returncode == 0, result.stderr
        versions = json.loads(result.stdout)
        assert versions == expected_pins, versions
        return 'release python imports all four at the exact pins: ' + json.dumps(versions, sort_keys=True)

    for name, fn in zip(LIMBS, (fetch, active, pypi, exact_lock, imports)):
        score(name, fn)
    # Product has created_at but no completion timestamp in install.json. The
    # snapshot preserves the ready record's mtime with copy2. This measures the
    # environment transaction, excluding the preceding artifact download.
    try:
        record, path = active_record(gate)
        assert record['state'] == 'ready'
        duration = path.stat().st_mtime - record['created_at']
        assert duration >= 0
        measurement = dict(n=1, duration_s=duration,
                           method='install.json created_at to ready-record preserved mtime; excludes artifact download')
        gate.save('duration', measurement)
        gate.say('MEASUREMENT install: ' + json.dumps(measurement) + ' (reported, not scored)')
    except Exception as exc:
        gate.say('MEASUREMENT install: unavailable (not scored): ' + str(exc))
    bad = gate83f3.gate83e3._report(gate, results)
    if cleanup:
        try:
            remaining = gate.cleanup()
            gate.save('cleanup', remaining)
            gate.say('CLEANUP: ' + json.dumps(remaining))
            bad += bool(remaining['roots_present'] or remaining['store_present'] or remaining['saved_store_present'])
        except Exception as exc:
            gate.say('CLEANUP FAILED: ' + str(exc))
            bad += 1
    return bad


class PanelOperator(gate83f3.ScriptedOperator):
    def __init__(self, gate, client):
        self.gate, self.client = gate, client

    def answer(self, key):
        from unittest.mock import patch
        assert key == 'install', key
        with patch.object(gate83f3, 'entries', lambda: dict(E1=release())):
            job = self.install('E1')  # real panel API and product background transaction
        assert not job.get('reasons'), job
        return 'DONE'


def selftest():
    from contextlib import ExitStack
    from types import SimpleNamespace
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from microclaw import paths, skill_store, updates, webserve

    lines = ['SELFTEST 83f-6: disk opener; real panel API, catalog verification and install records.',
             'SELFTEST fakes: 83f-3 real-uv venv builder with synthetic dist-info/version modules; Windows platform identity; desktop/operator. PyPI and scientific binaries NOT EXERCISED.']
    with tempfile.TemporaryDirectory() as temporary, redirect_stdout(io.StringIO()):
        base = Path(temporary)
        root, out = base / 'microclaw', base / 'evidence'
        root.mkdir()
        real_probe = skill_store.probe
        def windows_probe(python):
            identity = real_probe(python)
            identity['platform'] = 'win-amd64'  # only platform identity is simulated
            return identity
        with ExitStack() as stack:
            stack.enter_context(isolated_store(root))
            stack.enter_context(patch.object(paths, 'user_data_dir', lambda: root))
            stack.enter_context(patch.dict(os.environ, LOCALAPPDATA=str(base), XDG_DATA_HOME=str(base), UV_OFFLINE='1'))
            stack.enter_context(patch.object(updates, '_default_opener', disk_opener(FIXTURES)))
            stack.enter_context(patch.object(skill_store, 'probe', windows_probe))
            environment = gate83f3.fake_environment(base, platform='win_amd64', modules=True)
            stack.enter_context(patch.object(skill_store, 'build_environment', environment))
            gate = Gate(root, out)
            gate.fake = SimpleNamespace(close=lambda: None)
            # The demo machine keeps 83f-5's production store; never a test store.
            real = gate.store_root
            write_json(real / 'packages/microclaw-examples/session-start/pointer.json', dict(active='real'))
            write_json(real / 'catalog/state.json', dict(last_success='production'))
            def tree(path):
                return {p.relative_to(path).as_posix(): p.read_bytes() for p in path.rglob('*') if p.is_file()}
            real_tree = tree(real)
            assert gate.cleanup()['store_present'] is False and tree(real) == real_tree
            lines.append('SELFTEST real store: cleanup leaves it untouched and succeeds.')
            gate.prepare()
            assert tree(gate.saved_store) == real_tree, 'set-aside store changed'
            assert gate.need('prepare')['set_aside'] == str(gate.saved_store)
            assert [p.relative_to(gate.store_root).as_posix() for p in gate.store_root.rglob('*') if p.is_file()] == ['trust/roots.json']
            try:
                gate.prepare()
                raise AssertionError('second prepare accepted while a store is set aside')
            except NotExercised as exc:
                assert 'cleanup' in str(exc)
            lines.append('SELFTEST real store: prepare sets it aside unchanged; a second prepare refuses.')
            with TestClient(webserve.build_app(SimpleNamespace(mode=webserve.SessionMode.NORMAL))) as client:
                gate.fake = PanelOperator(gate, client)
                gate.session()
                assert verify(gate, cleanup=False) == 0, gate.last_results
                lines.append('SELFTEST baseline: PASS 5/5; pack-filled Windows lock, fresh probe and import versions agree.')
                record, path = active_record(gate)
                original = path.read_bytes()
                # Mutate product-written record fields, never a scorer-shaped fake.
                for field, target in [('distributions', LIMBS[3]), ('find_links', LIMBS[2])]:
                    changed = read_json(path)
                    if field == 'distributions':
                        changed['interpreter']['distributions']['numpy'] = '0.0.0'
                    else:
                        changed['find_links'] = str(base / 'unexpected-wheelhouse')
                    write_json(path, changed)
                    verify(gate, cleanup=False)
                    assert gate.last_results[target][0] == 'FAIL', gate.last_results
                    assert all(v[0] == 'PASS' for k, v in gate.last_results.items() if k != target), gate.last_results
                    lines.append(f'SELFTEST mutation killed: {field} -> {target} FAIL; other four limbs PASS.')
                    path.write_bytes(original)
                # Keep the saved record intact and change the environment's real
                # dist-info: this must fail the fresh probe, independently of the
                # saved distribution comparison and the version-module imports.
                site = subprocess.run([record['python'], '-I', '-c',
                                       'import sysconfig; print(sysconfig.get_path("purelib"))'],
                                      stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                      encoding='utf-8', check=True).stdout.strip()
                version = pins(fixture_manifest()['locks']['win_amd64'])['numpy']
                metadata = Path(site) / f'numpy-{version}.dist-info/METADATA'
                original_metadata = metadata.read_bytes()
                metadata.write_text(metadata.read_text(encoding='utf-8').replace(
                    f'Version: {version}', 'Version: 0.0.0'), encoding='utf-8')
                verify(gate, cleanup=False)
                assert gate.last_results[LIMBS[3]][0] == 'FAIL', gate.last_results
                assert all(v[0] == 'PASS' for k, v in gate.last_results.items() if k != LIMBS[3]), gate.last_results
                metadata.write_bytes(original_metadata)
                lines.append('SELFTEST mutation killed: fresh distributions -> 4 Exact Windows lock FAIL; other four limbs PASS.')
                # A separate uninstalled tree: retain a real fetched catalog but
                # remove install artifacts from the captured tree.
                snapshot = gate83f3.snapshot(gate, 1)
                packages = snapshot / 'packages'
                saved = base / 'saved-packages'
                shutil.move(packages, saved)
                verify(gate, cleanup=False)
                assert gate.last_results[LIMBS[0]][0] == 'PASS'
                assert all(gate.last_results[name][0] == 'NOT EXERCISED' for name in LIMBS[1:]), gate.last_results
                lines.append('SELFTEST no-install tree: limb 1 PASS; limbs 2-5 NOT EXERCISED; none scored PASS for an absent install.')
                shutil.move(saved, packages)
                # Inherited DONE validation is used by the actual session prompt.
                answers = iter(['pasted chat reply', '', 'DONE'])
                with patch.object(gate, 'fake', None), patch('builtins.input', lambda _: next(answers)):
                    assert gate.ask('Type DONE here in PowerShell.', 'input') == 'DONE'
                lines.append('SELFTEST input: pasted text and empty input rejected; DONE accepted.')
                real_rmtree = shutil.rmtree
                def roots_first(path, *args, **kwargs):
                    if Path(path) == gate.store_root:
                        assert not (gate.store_root / 'trust/roots.json').exists()
                    return real_rmtree(path, *args, **kwargs)
                (root / POINTER).write_text(str(out), encoding='utf-8')
                with patch.object(shutil, 'rmtree', roots_first):
                    assert verify(gate) == 0, gate.last_results
                assert not (root / POINTER).exists() and not gate.saved_store.exists()
                assert tree(gate.store_root) == real_tree, 'real store not restored byte-identically'
                lines.append('SELFTEST cleanup: TEST-ONLY roots removed first; test store and pointer removed; real store restored byte-identically.')
                # Interrupted cleanup: test store gone, real store still aside.
                gate.prepare()
                shutil.rmtree(gate.store_root)
                assert gate.cleanup()['restored_store'] and tree(gate.store_root) == real_tree
                # Both present and the live one is not TEST-ONLY: refuse, change nothing.
                gate.prepare()
                (gate.store_root / 'trust/roots.json').unlink()
                try:
                    gate.cleanup()
                    raise AssertionError('cleanup chose between two real stores')
                except NotExercised as exc:
                    assert 'by hand' in str(exc)
                assert gate.saved_store.exists() and gate.store_root.exists()
                lines.append('SELFTEST interrupted cleanup restores on re-run; two non-test stores refuse untouched.')
    lines.append('SELFTEST SUMMARY: PASS baseline; 3/3 mutations killed independently; no-install NOT EXERCISED; live Windows/PyPI NOT EXERCISED.')
    print('\n'.join(lines), flush=True)
    return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('phase', choices=PHASES)
    parser.add_argument('--out')
    args = parser.parse_args()
    if args.phase == 'fixtures':
        fixtures()
        return 0
    if args.phase == 'selftest':
        return selftest()
    from microclaw import paths
    root = paths.user_data_dir()
    pointer = root / POINTER
    out = args.out or (pointer.read_text(encoding='utf-8-sig').strip() if pointer.exists() else None)
    if not out:
        if args.phase not in ('prepare', 'cleanup'):
            raise SystemExit('NOT EXERCISED: no evidence pointer; run prepare or supply --out')
        out = root.parent / ('block83f6-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    gate = Gate(root, out)
    try:
        if args.phase == 'verify':
            return 1 if verify(gate) else 0
        result = getattr(gate, args.phase)()
        if args.phase == 'prepare':
            pointer.write_text(str(gate.out.resolve()), encoding='utf-8')
        if args.phase == 'cleanup':
            gate.save('cleanup', result)
            gate.say('CLEANUP: ' + json.dumps(result))
            if result['roots_present'] or result['store_present'] or result['saved_store_present']:
                return 1
        gate.say('RECORDED: ' + args.phase)
        return 0
    except NotExercised as exc:
        gate.say(f'NOT EXERCISED: {args.phase}: {exc}')
        if args.phase != 'session':
            gate.save(args.phase, dict(phase_error=str(exc)))
        return 2
    except Exception:
        gate.say(f'FAILED: {args.phase}:\n{traceback.format_exc()}')
        if args.phase != 'session':
            gate.save(args.phase, dict(phase_error=traceback.format_exc()))
        return 1


if __name__ == '__main__':
    sys.exit(main())
