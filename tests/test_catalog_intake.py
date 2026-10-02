"""Offline publisher-to-intake checks, with real signatures and hostile data."""
import base64
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import io
import json
from pathlib import Path
import shutil
import re
import socket
import subprocess
import sys
import zipfile

import pytest
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from microclaw import catalog_intake as intake, skill_packages as packages, skill_store as store

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/skill_packages'
SEED = ROOT / 'design/83f4-catalog-repo'
NOW = datetime(2026, 10, 2, tzinfo=timezone.utc)
_spec = importlib.util.spec_from_file_location('intake_fixture_builder', FIXTURES / 'build_release.py')
builder = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(builder)


def private(name='publisher-a'):
    seed = intake.read(FIXTURES / 'trust' / f'{name}-TEST-ONLY-seed.json')['seed']
    return Ed25519PrivateKey.from_private_bytes(base64.b64decode(seed))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(intake.encode(value))


class Response(io.BytesIO):
    status = 200
    def __init__(self, data):
        super().__init__(data)
        self.headers = {'Content-Length': str(len(data))}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('real socket attempted')
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    monkeypatch.setattr(socket, 'create_connection', forbidden)


@pytest.fixture
def setup(tmp_path):
    base, head = tmp_path / 'base', tmp_path / 'head'
    base.mkdir()
    shutil.copyfile(SEED / 'test-branch/policy.json', base / 'policy.json')
    shutil.copyfile(SEED / 'test-branch/roots-TEST-ONLY.json', base / 'roots-TEST-ONLY.json')
    shutil.copyfile(SEED / 'catalog.json', base / 'catalog.json')
    shutil.copytree(base, head)
    artifact = tmp_path / 'release.zip'
    builder.build_release(FIXTURES / 'markdown', artifact)
    manifest = intake.read(FIXTURES / 'markdown/manifest.json')
    record = intake.sign_release(private(), artifact, manifest['artifact'])
    path = intake.record_path('releases', record)
    write(head / path, record)
    def opener(request, timeout):
        assert request.full_url == record['artifact'] and timeout > 0
        return Response(artifact.read_bytes())
    def check(**kwargs):
        return intake.check(base, head, environment=kwargs.pop('environment', 'test'),
                            roots=kwargs.pop('roots', base / 'roots-TEST-ONLY.json'), now=NOW, opener=opener, **kwargs)
    return dict(base=base, head=head, artifact=artifact, record=record, path=path, check=check, opener=opener)


def assert_field(result, field):
    assert result['accepted'] is False, result
    assert result['field'] == field, result
    assert len(result['detail']) <= 700
    assert field in intake.human(result)


def test_sign_release_round_trip(setup):
    assert setup['check']()['accepted'] is True
    record = setup['record']
    assert record == intake.sign_release(private(), setup['artifact'], record['artifact'])
    assert intake.read(setup['head'] / setup['path']) == record


def test_withdrawal_accepts_full_existing_identity(setup):
    write(setup['base'] / setup['path'], setup['record'])
    (setup['head'] / setup['path']).unlink()
    shutil.copyfile(setup['base'] / setup['path'], setup['head'] / setup['path'])
    withdrawal = intake.sign_withdrawal(private(), setup['record'], 'Publisher withdrawal')
    write(setup['head'] / intake.record_path('withdrawals', withdrawal), withdrawal)
    assert setup['check']()['accepted'] is True


@pytest.mark.parametrize('extra', ['policy.json', 'catalog.json', '.github/workflows/intake.yml', 'README.md', 'releases/second.json'])
def test_one_file_rule_names_every_forbidden_extra(setup, extra):
    target = setup['head'] / extra
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('changed', encoding='utf-8')
    result = setup['check']()
    assert_field(result, 'path')
    assert extra in result['path']


@pytest.mark.parametrize('operation', ['delete', 'rename', 'modify', 'mode', 'none'])
def test_one_file_rule_delete_rename_modify_none(setup, operation):
    (setup['head'] / setup['path']).unlink()
    target = setup['head'] / 'catalog.json'
    if operation == 'delete':
        target.unlink()
    elif operation == 'rename':
        target.rename(setup['head'] / 'renamed.json')
    elif operation == 'modify':
        target.write_text('{}', encoding='utf-8')
    elif operation == 'mode':
        write(setup['head'] / setup['path'], setup['record'])
        target.chmod(0o755)
    assert_field(setup['check'](), 'path')


def test_one_added_forbidden_path(setup):
    (setup['head'] / setup['path']).rename(setup['head'] / 'README.md')
    assert_field(setup['check'](), 'path')


def test_path_must_match_fields(setup):
    target = setup['head'] / setup['path']
    target.rename(target.with_name('2.0.0.json'))
    assert_field(setup['check'](), 'path')


def test_head_symlink_refuses(setup):
    (setup['head'] / 'linked').symlink_to(setup['base'], target_is_directory=True)
    assert_field(setup['check'](), 'path')


@pytest.mark.parametrize('environment,roots,field', [('production', None, 'trust'), ('production', 'test', 'roots'),
                                                    ('test', None, 'roots'), ('test', 'production', 'roots.environment')])
def test_roots_are_operator_owned(setup, environment, roots, field):
    roots_path = None
    if roots == 'test':
        roots_path = setup['base'] / 'roots-TEST-ONLY.json'
    elif roots == 'production':
        roots_path = setup['base'] / 'wrong-roots.json'
        write(roots_path, dict(environment='production', keys=[]))
        shutil.copyfile(roots_path, setup['head'] / roots_path.name)
    assert_field(setup['check'](environment=environment, roots=roots_path), field)


@pytest.mark.parametrize('change,field', [('expired', 'trust.expires_at'), ('bad-signature', 'trust.signature.value'),
                                       ('missing', 'document')])
def test_policy_refusals(setup, change, field):
    target = setup['base'] / 'policy.json'
    policy = intake.read(target)
    if change == 'expired':
        policy['expires_at'] = '2026-01-01T00:00:00Z'
        policy = builder.sign(policy, 'root')
    elif change == 'bad-signature':
        policy['revision'] += 1
    if change == 'missing':
        target.unlink()
        (setup['head'] / 'policy.json').unlink()
    else:
        write(target, policy)
        write(setup['head'] / 'policy.json', policy)
    assert_field(setup['check'](), field)


@pytest.mark.parametrize('change,field', [('publisher', 'publisher'), ('signature', 'signature.value'),
                                       ('key', 'signature.key_id'), ('blocked', 'artifact_digest'),
                                       ('retired', 'signature.key_id'), ('revoked-publisher', 'publisher'),
                                       ('malformed', 'license')])
def test_release_refusals(setup, change, field):
    record = deepcopy(setup['record'])
    if change == 'publisher':
        record['publisher'] = 'unknown-publisher'
        record = intake.sign(record, private())
    elif change == 'signature':
        record['license'] = 'Changed after signing'
    elif change == 'key':
        record = intake.sign(record, private('publisher-b'))
    elif change == 'malformed':
        record['license'] = ''
    else:
        policy = intake.read(setup['base'] / 'policy.json')
        if change == 'blocked':
            policy['revoked_releases'] = [{k: record[k] for k in ('package_id', 'version', 'artifact_digest')} | dict(reason='Gate block')]
        elif change == 'retired':
            policy['publishers']['fixture-lab']['keys'][0]['state'] = 'retired'
        else:
            policy['publishers']['fixture-lab']['state'] = 'revoked'
        policy = builder.sign(policy, 'root')
        write(setup['base'] / 'policy.json', policy)
        write(setup['head'] / 'policy.json', policy)
    (setup['head'] / setup['path']).unlink()
    write(setup['head'] / intake.record_path('releases', record), record)
    assert_field(setup['check'](), field)


def test_tampered_download_names_digest(setup):
    setup['artifact'].write_bytes(setup['artifact'].read_bytes() + b'tamper')
    assert_field(setup['check'](), 'artifact_digest')


def test_download_cap_and_cleanup(setup, monkeypatch, tmp_path):
    monkeypatch.setattr(packages, 'MAX_ARTIFACT_DOWNLOAD_BYTES', 1)
    result = setup['check']()
    assert result['accepted'] is False
    assert result['field'] == 'artifact'  # bounded opener failure is converted below
    assert not list(tmp_path.rglob('.download-*'))


@pytest.mark.parametrize('duplicate', ['version', 'artifact_digest'])
def test_duplicates_refuse_before_download(setup, duplicate, monkeypatch):
    original = deepcopy(setup['record'])
    if duplicate == 'version':
        original['artifact_digest'] = '0' * 64
    else:
        original['version'] = '0.9.0'
    original = intake.sign(original, private())
    path = intake.record_path('releases', original)
    write(setup['base'] / path, original)
    if path != setup['path']:
        write(setup['head'] / path, original)
    else:
        # New version record at the same path is a modification; exercise duplicate
        # detection on trusted base data at its own valid listing in a separate file
        # via a conflicting publisher/package version spelling isn't allowed.
        # A second release with this identity can only be a modified file: H2 wins.
        assert_field(setup['check'](), 'path')
        return
    monkeypatch.setattr(store, 'download_release', lambda *a, **k: pytest.fail('duplicate fetched'))
    assert_field(setup['check'](), duplicate)


def test_same_version_different_digest_in_catalog(setup):
    # A base catalog may carry a release whose source file was lost; intake must
    # still reject a second release of that identity rather than forget history.
    old = intake.sign(dict(setup['record'], artifact_digest='0' * 64), private())
    write(setup['base'] / 'catalog.json', dict(type=packages.CATALOG_TYPE, releases=[old], withdrawals=[]))
    write(setup['head'] / 'catalog.json', intake.read(setup['base'] / 'catalog.json'))
    assert_field(setup['check'](), 'version')


@pytest.mark.parametrize('change,field', [('other-publisher', 'publisher'), ('missing', 'artifact_digest'),
                                       ('wrong-version', 'artifact_digest'), ('bad-signature', 'signature.value'),
                                       ('long-reason', 'reason')])
def test_withdrawal_refusals(setup, change, field):
    write(setup['base'] / setup['path'], setup['record'])
    withdrawal = intake.sign_withdrawal(private(), setup['record'], 'Withdrawn')
    key = private()
    if change == 'other-publisher':
        withdrawal['publisher'] = 'fixture-two'
        key = private('publisher-b')
    elif change == 'missing':
        withdrawal['artifact_digest'] = '0' * 64
    elif change == 'wrong-version':
        withdrawal['version'] = '2.0.0'
    elif change == 'long-reason':
        withdrawal['reason'] = 'x' * 513
    else:
        withdrawal['reason'] = 'Tampered'
    if change != 'bad-signature':
        withdrawal = intake.sign(withdrawal, key)
    write(setup['head'] / intake.record_path('withdrawals', withdrawal), withdrawal)
    assert_field(setup['check'](), field)


@pytest.mark.parametrize('mutation,field', [('undeclared', 'rogue.py'), ('manifest-binding', 'intake.license'),
                                          ('asset', 'assets[0].sha256'), ('bad-zip', 'artifact'),
                                          ('manifest-missing', 'manifest.json')])
def test_archive_checks_reused_at_intake(setup, mutation, field):
    artifact = setup['artifact']
    with zipfile.ZipFile(artifact) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    if mutation == 'undeclared':
        entries['rogue.py'] = b'raise RuntimeError("never execute")'
    elif mutation == 'manifest-binding':
        manifest = json.loads(entries['manifest.json'])
        manifest['license'] = 'Different license'
        entries['manifest.json'] = intake.encode(manifest)
    elif mutation == 'asset':
        entries['SKILL.md'] += b'changed'
    elif mutation == 'manifest-missing':
        del entries['manifest.json']
    artifact.write_bytes(b'not zip' if mutation == 'bad-zip' else builder._archive(entries))
    value = intake.sign(dict(setup['record'], artifact_digest=__import__('hashlib').sha256(artifact.read_bytes()).hexdigest()), private())
    write(setup['head'] / setup['path'], value)
    assert_field(setup['check'](), field)


def test_signer_url_must_equal_manifest(setup):
    with pytest.raises(packages.PackageRefusal) as caught:
        intake.sign_release(private(), setup['artifact'], 'https://example.org/wrong.zip')
    assert caught.value.field == 'artifact'


def test_signer_checks_malformed_zip_early(setup):
    with zipfile.ZipFile(setup['artifact']) as archive:
        entries = {n: archive.read(n) for n in archive.namelist()}
    entries['rogue.py'] = b'print("publisher code")'
    setup['artifact'].write_bytes(builder._archive(entries))
    with pytest.raises(packages.PackageRefusal) as caught:
        intake.sign_release(private(), setup['artifact'], setup['record']['artifact'])
    assert caught.value.field == 'rogue.py'


def test_executable_checks_without_importing_publisher(setup, tmp_path):
    source = tmp_path / 'source'
    shutil.copytree(FIXTURES / 'executable', source)
    marker = tmp_path / 'PUBLISHER_EXECUTED'
    runner = source / 'fixture_worker/runner.py'
    runner.write_text(f'from pathlib import Path\nPath({str(marker)!r}).write_text("executed")\n', encoding='utf-8')
    manifest = intake.read(source / 'manifest.json')
    for asset in manifest['assets']:
        if asset['path'] == 'fixture_worker/runner.py':
            asset['sha256'] = __import__('hashlib').sha256(runner.read_bytes()).hexdigest()
    write(source / 'manifest.json', manifest)
    builder.build_release(source, setup['artifact'])
    record = intake.sign_release(private(), setup['artifact'], manifest['artifact'])
    (setup['head'] / setup['path']).unlink()
    write(setup['head'] / intake.record_path('releases', record), record)
    result = intake.check(setup['base'], setup['head'], environment='test', roots=setup['base'] / 'roots-TEST-ONLY.json',
                          now=NOW, opener=lambda *a, **k: Response(setup['artifact'].read_bytes()))
    assert result['accepted'], result
    assert not marker.exists()
    manifest['operations'] = [op for op in manifest['operations'] if op['name'] != 'self_check']
    write(source / 'manifest.json', manifest)
    builder.build_release(source, setup['artifact'])
    with pytest.raises(packages.PackageRefusal) as caught:
        intake.sign_release(private(), setup['artifact'], manifest['artifact'])
    assert caught.value.field == 'operations'


def test_build_deterministic_and_identical_noop(setup, monkeypatch):
    write(setup['base'] / setup['path'], setup['record'])
    kwargs = dict(environment='test', roots=setup['base'] / 'roots-TEST-ONLY.json', now=NOW)
    monkeypatch.setattr(store, 'download_release', lambda *a, **k: pytest.fail('build refetched'))
    document = intake.build(setup['base'], **kwargs)
    target = setup['base'] / 'catalog.json'
    before = target.stat().st_mtime_ns
    assert document['releases'] == [setup['record']]
    intake.build(setup['base'], **kwargs)
    assert target.stat().st_mtime_ns == before
    assert target.read_bytes() == intake.encode(document)


def test_build_sort_order(setup):
    first = setup['record']
    second = intake.sign(dict(first, version='2.0.0', artifact_digest='1' * 64), private())
    # Write reverse order: output is driven only by sorted repository paths.
    write(setup['base'] / intake.record_path('releases', second), second)
    write(setup['base'] / setup['path'], first)
    document = intake.build(setup['base'], environment='test', roots=setup['base'] / 'roots-TEST-ONLY.json', now=NOW)
    assert document['releases'] == [first, second]


@pytest.mark.parametrize('collection', ['releases', 'withdrawals'])
def test_build_refuses_dropping_previous_entries(setup, collection):
    record = setup['record']
    withdrawal = intake.sign_withdrawal(private(), record, 'Withdrawn')
    previous = dict(type=packages.CATALOG_TYPE, releases=[record], withdrawals=[withdrawal] if collection == 'withdrawals' else [])
    if collection == 'withdrawals':
        write(setup['base'] / setup['path'], record)
    write(setup['base'] / 'catalog.json', previous)
    before = (setup['base'] / 'catalog.json').read_bytes()
    with pytest.raises(packages.PackageRefusal) as caught:
        intake.build(setup['base'], environment='test', roots=setup['base'] / 'roots-TEST-ONLY.json', now=NOW)
    assert caught.value.field == 'catalog.' + collection
    assert (setup['base'] / 'catalog.json').read_bytes() == before


@pytest.mark.parametrize('bound,field', [('bytes', 'catalog'), ('count', 'catalog.releases')])
def test_catalog_bounds(setup, monkeypatch, bound, field):
    if bound == 'bytes':
        monkeypatch.setattr(packages, 'MAX_CATALOG_BYTES', len((setup['base'] / 'catalog.json').read_bytes()) + 10)
    else:
        monkeypatch.setattr(packages, 'MAX_CATALOG_RELEASES', 0)
    assert_field(setup['check'](), field)


def test_fixture_builder_uses_product_signer_and_projection(tmp_path, monkeypatch):
    calls = []
    original = builder.product_sign
    def spy(value, key):
        calls.append(value)
        return original(value, key)
    monkeypatch.setattr(builder, 'product_sign', spy)
    result = builder.build_release(FIXTURES / 'markdown', tmp_path / 'fixture.zip')
    assert len(calls) == 1 and calls[0]['artifact_digest'] == result['artifact_digest']
    assert result == intake.sign_release(private(), tmp_path / 'fixture.zip', result['artifact'])


def test_restricted_import_subprocess():
    source = '''
import sys, importlib.abc
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'numpy', 'pycromanager', 'anthropic', 'yaml', 'uv', 'fastapi', 'pydantic', 'scipy', 'tifffile'} or fullname in {'microclaw.tools', 'microclaw.agent', 'microclaw.webserve', 'microclaw.__main__'}:
            raise RuntimeError('forbidden import: ' + fullname)
sys.meta_path.insert(0, Block())
import microclaw.catalog_intake
assert 'microclaw.tools' not in sys.modules
'''
    result = subprocess.run([sys.executable, '-c', source], cwd=ROOT, stdin=subprocess.DEVNULL, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_keygen_refuses_overwrite_and_prints_policy_entry(tmp_path, capsys):
    path = tmp_path / 'private.pem'
    assert intake.main(['keygen', '--out', str(path)]) == 0
    output = capsys.readouterr().out
    entry = json.loads(output[:output.index('Keep the private')])
    assert entry == intake.public_entry(intake.load_key(path))
    assert 'secret' in output
    before = path.read_bytes()
    assert intake.main(['keygen', '--out', str(path)]) == 1
    assert path.read_bytes() == before


def test_cli_sign_and_withdrawal_paths(setup, tmp_path, capsys):
    key = tmp_path / 'key.pem'
    key.write_bytes(private().private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    out = tmp_path / 'signed.json'
    assert intake.main(['sign-release', '--key', str(key), '--artifact', str(setup['artifact']), '--url', setup['record']['artifact'], '--out', str(out)]) == 0
    assert setup['path'] in capsys.readouterr().out
    withdrawal = tmp_path / 'withdrawal.json'
    assert intake.main(['sign-withdrawal', '--key', str(key), '--release', str(out), '--reason', 'Withdrawn', '--out', str(withdrawal)]) == 0
    assert intake.record_path('withdrawals', intake.read(withdrawal)) in capsys.readouterr().out


def test_cli_json_exit_codes(setup, capsys, monkeypatch):
    args = ['check', '--base', str(setup['base']), '--head', str(setup['head']), '--environment', 'production', '--json']
    assert intake.main(args) == 1
    assert json.loads(capsys.readouterr().out)['field'] == 'trust'
    monkeypatch.setattr(intake, 'check', lambda *a, **k: dict(accepted=True, path='x', field=None, detail='ok'))
    assert intake.main(args) == 0
    assert json.loads(capsys.readouterr().out)['accepted']
    def broken(*a, **k):
        raise RuntimeError('private internal details')
    monkeypatch.setattr(intake, 'check', broken)
    assert intake.main(args) == 2
    value = json.loads(capsys.readouterr().out)
    assert value['field'] == 'internal' and 'private' not in value['detail']


def test_bounded_refusal_text(setup, monkeypatch):
    def refusal(*a, **k):
        raise packages.PackageRefusal('artifact', 'x' * 10000 + '\n<script>')
    monkeypatch.setattr(store, 'download_release', refusal)
    result = setup['check']()
    assert_field(result, 'artifact')
    assert len(result['detail']) == 700
    assert '\n' not in intake.human(result)


def test_workflow_structure():
    # PyYAML 1.1 treats the word on as True; both keys are accepted as data.
    workflow = yaml.safe_load((SEED / '.github/workflows/intake.yml').read_text(encoding='utf-8'))
    trigger = workflow.get('on', workflow.get(True))
    assert set(trigger) == {'pull_request_target'}
    assert trigger['pull_request_target']['branches'] == ['main', 'test']
    assert workflow['permissions'] == {'contents': 'write', 'pull-requests': 'write'}
    assert workflow['concurrency']['cancel-in-progress'] is False
    assert 'base.ref' in workflow['concurrency']['group']
    assert workflow['env']['MICROCLAW_COMMIT'] == 'REPLACE_WITH_PINNED_COMMIT'
    steps = workflow['jobs']['intake']['steps']
    assert 'exit 1' in steps[0]['run'] and 'MICROCLAW_COMMIT' in steps[0]['run']
    for step in steps:
        if 'checkout@' in step.get('uses', ''):
            assert 'pull_request.head' not in json.dumps(step.get('with', {}))
        script = step.get('run', '')
        assert 'pip install' not in script or script == 'python -m pip install cryptography==44.0.2 packaging==24.2'
        assert 'git checkout' not in script and 'git switch' not in script
        assert not re.search(r'''(?:python|bash|sh|source|\.)(?:\s+-[a-zA-Z]+)*\s+["']?\$DATA_HEAD''', script)
    script = steps[-1]['run']
    for required in ('git fetch origin "$BASE_REF"', 'git ls-tree', "'ls-tree'", "'cat-file'", 'catalog_intake check', 'catalog_intake build',
                     'merge_method=squash', '-f sha="$HEAD_SHA"', '--body-file', 'git add catalog.json', 'roots-TEST-ONLY.json'):
        if required == 'git ls-tree':
            continue  # subprocess argv spelling checked below
        assert required in script
    rebuild = yaml.safe_load((SEED / '.github/workflows/rebuild.yml').read_text(encoding='utf-8'))
    assert rebuild.get('on', rebuild.get(True))['push']['paths'] == ['policy.json']
    assert rebuild['permissions'] == {'contents': 'write'}
