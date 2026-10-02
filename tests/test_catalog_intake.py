"""Offline publisher-to-intake checks, with real signatures and hostile data."""
import base64
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import io
import os
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
# Intake runs only in the catalog's Action, on ubuntu-latest: Windows has no executable
# bit and no bash to run the workflow's script with.
LINUX_ONLY = 'catalog intake runs on the Action\'s Linux runner only'
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


@pytest.mark.parametrize('operation', ['delete', 'rename', 'modify',
    pytest.param('mode', marks=pytest.mark.skipif(sys.platform == 'win32', reason=LINUX_ONLY)), 'none'])
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
                                       ('retired', 'signature.key_id'), ('revoked-key', 'signature.key_id'),
                                       ('revoked-publisher', 'publisher'),
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
        elif change in ('retired', 'revoked-key'):
            policy['publishers']['fixture-lab']['keys'][0]['state'] = 'retired' if change == 'retired' else 'revoked'
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


@pytest.mark.parametrize('change,field', [('publisher', 'publisher'), ('key', 'signature.key_id'),
                                          ('blocked', 'artifact_digest')])
def test_ineligible_release_refuses_before_download(setup, change, field, monkeypatch):
    # An unadmitted or blocked submitter must not make intake fetch its URL.
    record = deepcopy(setup['record'])
    if change == 'publisher':
        record['publisher'] = 'unknown-publisher'
        record = intake.sign(record, private())
    elif change == 'key':
        record = intake.sign(record, private('publisher-b'))
    else:
        policy = intake.read(setup['base'] / 'policy.json')
        policy['revoked_releases'] = [{k: record[k] for k in ('package_id', 'version', 'artifact_digest')} | dict(reason='block')]
        policy = builder.sign(policy, 'root')
        write(setup['base'] / 'policy.json', policy)
        write(setup['head'] / 'policy.json', policy)
    (setup['head'] / setup['path']).unlink()
    write(setup['head'] / intake.record_path('releases', record), record)
    monkeypatch.setattr(store, 'download_release', lambda *a, **k: pytest.fail('ineligible release fetched'))
    assert_field(setup['check'](), field)


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
    for required in ('git fetch origin "$BASE_REF"', 'git fetch origin "pull/$PR_NUMBER/head"',
                     'git rev-parse FETCH_HEAD', 'catalog_intake materialize', 'catalog_intake check',
                     'catalog_intake build', 'merge_method=squash', '-f sha="$HEAD_SHA"',
                     '--body-file', 'git add catalog.json', 'roots-TEST-ONLY.json'):
        assert required in script
    assert 'git fetch origin "$HEAD_SHA"' not in script
    assert "python - <<" not in script
    rebuild = yaml.safe_load((SEED / '.github/workflows/rebuild.yml').read_text(encoding='utf-8'))
    assert rebuild.get('on', rebuild.get(True))['push']['paths'] == ['policy.json']
    assert rebuild['permissions'] == {'contents': 'write'}


@pytest.mark.parametrize('change', ['blocked', 'revoked-publisher', 'revoked-key', 'retired-key'])
def test_trusted_history_survives_policy_changes_and_unrelated_intake(setup, tmp_path, change):
    base, head = setup['base'], setup['head']
    write(base / setup['path'], setup['record'])
    withdrawal = intake.sign_withdrawal(private(), setup['record'], 'Previously admitted withdrawal')
    write(base / intake.record_path('withdrawals', withdrawal), withdrawal)
    kwargs = dict(environment='test', roots=base / 'roots-TEST-ONLY.json', now=NOW)
    intake.build(base, **kwargs)
    catalog_before = (base / 'catalog.json').read_bytes()
    release_before = (base / setup['path']).read_bytes()
    policy = intake.read(base / 'policy.json')
    if change == 'blocked':
        policy['revoked_releases'] = [{k: setup['record'][k] for k in ('package_id', 'version', 'artifact_digest')}
                                     | dict(reason='Operator block')]
    elif change == 'revoked-publisher':
        policy['publishers']['fixture-lab']['state'] = 'revoked'
    else:
        policy['publishers']['fixture-lab']['keys'][0]['state'] = 'retired' if change == 'retired-key' else 'revoked'
    write(base / 'policy.json', builder.sign(policy, 'root'))
    rebuilt = intake.build(base, **kwargs)
    assert rebuilt['releases'] == [setup['record']]
    assert rebuilt['withdrawals'] == [withdrawal]
    assert (base / 'catalog.json').read_bytes() == catalog_before
    assert (base / setup['path']).read_bytes() == release_before

    source = tmp_path / 'unrelated'
    shutil.copytree(FIXTURES / 'markdown', source)
    manifest = intake.read(source / 'manifest.json')
    manifest.update(publisher='fixture-two', package_id='unrelated')
    write(source / 'manifest.json', manifest)
    artifact = tmp_path / 'unrelated.zip'
    builder.build_release(source, artifact)
    record = intake.sign_release(private('publisher-b'), artifact, manifest['artifact'])
    shutil.rmtree(head)
    shutil.copytree(base, head)
    write(head / intake.record_path('releases', record), record)
    result = intake.check(base, head, opener=lambda *a, **k: Response(artifact.read_bytes()), **kwargs)
    assert result['accepted'], result


@pytest.mark.parametrize('collection,field', [('releases', 'license'), ('withdrawals', 'reason')])
def test_trusted_history_still_requires_structural_validation(setup, collection, field):
    write(setup['base'] / setup['path'], setup['record'])
    if collection == 'releases':
        value = dict(setup['record'], license='')
    else:
        value = dict(intake.sign_withdrawal(private(), setup['record'], 'Withdrawn'), reason='x' * 513)
    write(setup['base'] / intake.record_path(collection, value), value)
    with pytest.raises(packages.PackageRefusal) as caught:
        intake.build(setup['base'], environment='test', roots=setup['base'] / 'roots-TEST-ONLY.json', now=NOW)
    assert caught.value.field == field


def git(repo, *args):
    # Windows git converts LF to CRLF on checkout; the Action's Linux checkout does not,
    # and intake compares the working tree with raw blobs.
    return subprocess.run(['git', '-c', 'core.autocrlf=false', '-C', str(repo), *args], stdin=subprocess.DEVNULL,
                          capture_output=True, text=True, check=True).stdout.strip()


def stale_repo(setup, tmp_path):
    repo = tmp_path / 'git-repo'
    shutil.copytree(setup['base'], repo)
    (repo / 'README.md').write_text('Trusted publisher README\n', encoding='utf-8')
    git(repo, 'init', '-b', 'main')
    git(repo, 'config', 'user.name', 'Offline intake test')
    git(repo, 'config', 'user.email', 'intake-test@example.invalid')
    git(repo, 'add', 'README.md', 'policy.json', 'roots-TEST-ONLY.json', 'catalog.json')
    git(repo, 'commit', '-m', 'Trusted seed')
    ancestor = git(repo, 'rev-parse', 'HEAD')
    # The PR branch starts here, before somebody else's release and rebuilt catalog.
    git(repo, 'switch', '-c', 'publisher')
    write(repo / setup['path'], setup['record'])
    git(repo, 'add', setup['path'])
    git(repo, 'commit', '-m', 'Publisher release')
    return repo, ancestor


def advance_base(repo, ancestor, *, readme=None):
    head = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'switch', 'main')
    if readme is not None:
        (repo / 'README.md').write_text(readme, encoding='utf-8')
        git(repo, 'add', 'README.md')
    record = deepcopy(intake.read(ROOT / 'design/83f4-gate/I.json'))
    path = intake.record_path('releases', record)
    write(repo / path, record)
    intake.build(repo, environment='test', roots=repo / 'roots-TEST-ONLY.json', now=NOW)
    git(repo, 'add', path, 'catalog.json')
    git(repo, 'commit', '-m', 'Another publisher merged; bot rebuilt catalog')
    assert git(repo, 'rev-parse', 'HEAD') != ancestor
    return head


def test_materialize_stale_fork_addition_against_current_base(setup, tmp_path):
    repo, ancestor = stale_repo(setup, tmp_path)
    head = advance_base(repo, ancestor)
    out = tmp_path / 'materialized'
    result = intake.materialize(repo, 'main', head, out)
    assert result['merge_base'] == ancestor
    assert result['changes'] == [dict(status='A', path=setup['path'])]
    assert (out / 'catalog.json').read_bytes() == (repo / 'catalog.json').read_bytes()
    assert (out / 'releases/fixture-lab/gate-stale/1.0.0.json').is_file()
    verdict = intake.check(repo, out, environment='test', roots=repo / 'roots-TEST-ONLY.json', now=NOW,
                           opener=setup['opener'])
    assert verdict['accepted'], verdict


@pytest.mark.parametrize('change', ['delete', 'modify', 'rename',
    pytest.param('mode', marks=pytest.mark.skipif(sys.platform == 'win32', reason=LINUX_ONLY))])
def test_materialize_applies_prs_own_forbidden_changes(setup, tmp_path, change):
    repo, ancestor = stale_repo(setup, tmp_path)
    readme = repo / 'README.md'
    if change == 'delete':
        readme.unlink()
        git(repo, 'add', 'README.md')
    elif change == 'modify':
        readme.write_text('PR changed this\n', encoding='utf-8')
        git(repo, 'add', 'README.md')
    elif change == 'mode':
        readme.chmod(0o755)
        git(repo, 'update-index', '--chmod=+x', 'README.md')
    else:
        git(repo, 'mv', 'README.md', 'renamed.md')
    git(repo, 'commit', '-m', 'Forbidden extra publisher change')
    head = advance_base(repo, ancestor)
    out = tmp_path / 'materialized'
    with pytest.raises(packages.PackageRefusal) as caught:
        intake.materialize(repo, 'main', head, out)
    assert caught.value.field == 'path'
    assert 'README.md' in str(caught.value)
    assert_field(intake.check(repo, out, environment='test', roots=repo / 'roots-TEST-ONLY.json',
                             now=NOW, opener=setup['opener']), 'path')


@pytest.mark.parametrize('mode', ['symlink', 'gitlink'])
def test_materialize_refuses_nonordinary_git_objects(setup, tmp_path, mode):
    repo, ancestor = stale_repo(setup, tmp_path)
    candidate = repo / setup['path']
    candidate.unlink()
    if mode == 'symlink':
        candidate.symlink_to('../outside')
        git(repo, 'add', setup['path'])
    else:
        git(repo, 'update-index', '--cacheinfo', '160000,' + ancestor + ',' + setup['path'])
    git(repo, 'commit', '-m', 'Hostile PR file type')
    head = advance_base(repo, ancestor)
    with pytest.raises(packages.PackageRefusal) as caught:
        intake.materialize(repo, 'main', head, tmp_path / 'materialized')
    assert caught.value.field == 'path'
    assert setup['path'] in str(caught.value)


@pytest.mark.skipif(sys.platform == 'win32', reason=LINUX_ONLY)
@pytest.mark.parametrize('moves,attempts,exit_code', [(1, 2, 0), (5, 3, 1)])
def test_workflow_comments_and_rechecks_when_base_moves(tmp_path, moves, attempts, exit_code):
    workflow = yaml.safe_load((SEED / '.github/workflows/intake.yml').read_text(encoding='utf-8'))
    script = workflow['jobs']['intake']['steps'][-1]['run']
    (tmp_path / 'catalog').mkdir()
    runner_temp = tmp_path / 'runner-temp'
    runner_temp.mkdir()
    tools = tmp_path / 'bin'
    tools.mkdir()
    fake = tools / 'command.py'
    # Git argv and gh merge JSON are their actual interfaces. No network or head code.
    fake.write_text(f'#!{sys.executable}\n' + '''
import json, os, pathlib, sys
root = pathlib.Path(os.environ['FAKE_STATE'])
tool, args = pathlib.Path(sys.argv[0]).name, sys.argv[1:]
with (root / 'calls.jsonl').open('a') as stream:
    stream.write(json.dumps(dict(tool=tool, args=args)) + '\\n')
if tool == 'git':
    if args[:2] == ['rev-parse', 'FETCH_HEAD']:
        print(os.environ['HEAD_SHA'])
    elif args[:2] == ['rev-parse', 'HEAD']:
        path = root / 'attempt.txt'
        attempt = int(path.read_text()) + 1 if path.exists() else 1
        path.write_text(str(attempt))
        print(f'{attempt:040x}')
    elif args[:2] == ['rev-parse', 'origin/test']:
        attempt = int((root / 'attempt.txt').read_text())
        print(f'{attempt + (attempt <= int(os.environ["FAKE_MOVES"])):040x}')
    elif args[0] not in ('fetch', 'reset', 'config', 'add', 'diff'):
        raise SystemExit('Unexpected git call: ' + repr(args))
elif tool == 'gh':
    if args[:2] == ['pr', 'comment']:
        pass
    elif args[:3] == ['api', '--method', 'PUT']:
        print(json.dumps(dict(merged=True)))
    else:
        raise SystemExit('Unexpected gh call: ' + repr(args))
elif tool == 'python':
    if args[:2] == ['-m', 'microclaw.catalog_intake']:
        if args[2] == 'materialize':
            pathlib.Path(args[args.index('--out') + 1]).mkdir()
        elif args[2] not in ('check', 'build'):
            raise SystemExit('Unexpected intake command: ' + repr(args))
        print('Accepted offline workflow control')
    elif args[0] == '-c':
        os.execv(sys.executable, [sys.executable, *args])
    else:
        raise SystemExit('Unexpected Python call: ' + repr(args))
''', encoding='utf-8')
    fake.chmod(0o755)
    for name in ('git', 'gh', 'python'):
        (tools / name).symlink_to(fake)
    env = dict(os.environ, PATH=str(tools) + os.pathsep + os.environ['PATH'], FAKE_STATE=str(tmp_path),
               FAKE_MOVES=str(moves), RUNNER_TEMP=str(runner_temp), BASE_REF='test', HEAD_SHA='a' * 40,
               PR_NUMBER='1', GH_REPO='Micro-Claw/package-catalog', GH_TOKEN='offline-control')
    result = subprocess.run(['bash', '-c', script], cwd=tmp_path, env=env, stdin=subprocess.DEVNULL,
                            capture_output=True, text=True)
    assert result.returncode == exit_code, result.stdout + result.stderr
    calls = [json.loads(line) for line in (tmp_path / 'calls.jsonl').read_text(encoding='utf-8').splitlines()]
    checks = [c for c in calls if c['tool'] == 'python' and c['args'][:3] == ['-m', 'microclaw.catalog_intake', 'check']]
    assert len(checks) == attempts
    comments = [c['args'][c['args'].index('--body') + 1] for c in calls if c['tool'] == 'gh' and c['args'][:2] == ['pr', 'comment']]
    assert len(comments) == min(moves, 3)
    assert all('base branch moved' in c.lower() for c in comments)
    assert all('re-run' in c for c in comments)
    merges = [c for c in calls if c['tool'] == 'gh' and c['args'][:3] == ['api', '--method', 'PUT']]
    assert len(merges) == (1 if exit_code == 0 else 0)


def load_gate():
    spec = importlib.util.spec_from_file_location('intake_revision_gate', ROOT / 'design/83-block83f4-gate.py')
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)
    return gate


def test_gate_prepares_stale_i_before_a_then_opens_after_merge(tmp_path, monkeypatch):
    module = load_gate()
    monkeypatch.setattr(module, 'CASES', {'A': (True, None), 'I': (True, None)})
    base = ['1' * 40]
    events, commits = [], {}
    def api(endpoint, payload=None):
        if endpoint == 'user':
            return dict(login='offline-operator')
        if endpoint == 'repos/offline-operator/package-catalog':
            return dict(fork=True, parent=dict(full_name=module.REPO))
        if endpoint.endswith('/git/ref/heads/test'):
            return dict(object=dict(sha=base[0]))
        if '/git/commits/' in endpoint:
            return dict(tree=dict(sha=endpoint.rsplit('/', 1)[-1]))
        if endpoint.endswith('/git/trees'):
            return dict(sha='3' * 40)
        if endpoint.endswith('/git/commits'):
            label = payload['message'].rsplit(' ', 1)[-1]
            commit = dict(sha=f'{len(commits) + 10:040x}', parents=[dict(sha=p) for p in payload['parents']])
            events.append('create-' + label)
            commits[label] = commit
            return commit
        if endpoint.endswith('/git/refs'):
            return dict(ref=payload['ref'], object=dict(sha=payload['sha']))
        if endpoint.endswith('/pulls'):
            label = payload['title'].rsplit(' ', 1)[-1]
            events.append('open-' + label)
            return dict(number=ord(label), html_url='https://github.com/' + module.REPO + '/pull/' + str(ord(label)))
        raise AssertionError(endpoint)
    monkeypatch.setattr(module, 'api', api)
    monkeypatch.setattr(module, 'gh', lambda *args, **kwargs: '')
    def capture(gate, case):
        if case == 'A':
            base[0] = '2' * 40
        pr = dict(state='MERGED', mergedAt='2026-10-02T10:00:00Z', comments=[])
        runs = [dict(status='completed', conclusion='success')]
        gate.save(f'{case}-pr.json', pr)
        gate.save(f'{case}-runs.json', runs)
        return pr, runs
    monkeypatch.setattr(module.Gate, 'capture', capture)
    def served(gate):
        gate.save('served-catalog.json', dict(type=packages.CATALOG_TYPE,
                  releases=[intake.read(module.FIXTURES / f'{c}.json') for c in ('A', 'I')], withdrawals=[]))
    monkeypatch.setattr(module, 'collect_served', served)
    monkeypatch.setattr(module, 'verify', lambda gate: 0)
    assert module.run(module.Gate(tmp_path / 'evidence'), 5) == 0
    assert events.index('create-I') < events.index('open-A') < events.index('open-I')
    assert commits['I']['parents'] == [dict(sha='1' * 40)]
    state = intake.read(tmp_path / 'evidence/state.json')
    assert state['prs']['I']['sha'] == state['prepared']['sha'] == commits['I']['sha']


def test_design_keeps_fixtures_but_no_handoff_outputs():
    for name in ('83-block83f4-report.md', '83-block83f4-mutations.py', '83f4-gate/mutation-output.txt',
                 '83f4-gate/mutations.json', '83f4-gate/selftest.txt', '83f4-gate/targeted-tests.txt'):
        assert not (ROOT / 'design' / name).exists()
    assert (ROOT / 'design/83f4-gate/I.json').is_file()
    assert (ROOT / 'design/83f4-gate/artifacts/I.zip').is_file()


def test_materialize_refuses_pr_edit_even_if_current_base_already_matches(setup, tmp_path):
    repo, ancestor = stale_repo(setup, tmp_path)
    text = 'Same edit made independently by operator and PR\n'
    (repo / 'README.md').write_text(text, encoding='utf-8')
    git(repo, 'add', 'README.md')
    git(repo, 'commit', '-m', 'Publisher edits README too')
    head = advance_base(repo, ancestor, readme=text)
    out = tmp_path / 'materialized'
    with pytest.raises(packages.PackageRefusal) as caught:
        intake.materialize(repo, 'main', head, out)
    assert caught.value.field == 'path'
    assert 'README.md' in str(caught.value)
    assert (out / 'README.md').read_bytes() == (repo / 'README.md').read_bytes()


def test_resubmitted_version_says_publish_a_new_version(setup, monkeypatch):
    # Gate case D on 2026-10-02: the refusal named H2's rule, not the publisher's fix.
    earlier = deepcopy(setup['record'])
    earlier['artifact_digest'] = '0' * 64
    write(setup['base'] / setup['path'], intake.sign(earlier, private()))
    monkeypatch.setattr(store, 'download_release', lambda *a, **k: pytest.fail('resubmission fetched'))
    result = setup['check']()
    assert_field(result, 'path')
    assert 'publish a new version' in result['detail']
