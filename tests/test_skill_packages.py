"""Release format and real Ed25519 trust checks; no installer or worker."""
from copy import deepcopy
import base64
from datetime import datetime, timezone
import hashlib
import socket
import json
from pathlib import Path
import shutil

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from microclaw import skill_packages as packages, skills
from microclaw.tools import _recorded_outcome

FIXTURES = Path(__file__).parent / 'fixtures' / 'skill_packages'


NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)


def trust_file(name):
    return json.loads((FIXTURES / 'trust' / f'{name}-TEST-ONLY.json').read_text(encoding='utf-8'))


def sign(document, key='publisher-a'):
    value = deepcopy(document)
    value.pop('signature', None)
    seed = json.loads(
        (FIXTURES / 'trust' / f'{key}-TEST-ONLY-seed.json').read_text(encoding='utf-8'))
    private = Ed25519PrivateKey.from_private_bytes(base64.b64decode(seed['seed']))
    raw = private.public_key().public_bytes_raw()
    payload = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')
    value['signature'] = dict(alg='ed25519', key_id=hashlib.sha256(raw).hexdigest(),
                              value=base64.b64encode(private.sign(payload)).decode('ascii'))
    return value


def policy(document=None, **kwargs):
    return packages.verify_trust_policy(
        trust_file('policy') if document is None else sign(document, 'root'),
        trust_file('roots'), **kwargs)


def manifest(kind='executable'):
    return json.loads((FIXTURES / kind / 'manifest.json').read_text(encoding='utf-8'))


def intake(kind='executable'):
    return json.loads((FIXTURES / f'{kind}-intake.json').read_text(encoding='utf-8'))


def record(kind='executable', **changes):
    value = dict(manifest=manifest(kind), intake=intake(kind), release_dir=FIXTURES / kind,
                 enabled=True, verified=True)
    value.update(changes)
    return value


def refuses(expected_field, fn, *args, **kwargs):
    with pytest.raises(packages.PackageRefusal) as caught:
        fn(*args, **kwargs)
    assert caught.value.field == expected_field


def put(value, path, replacement):
    for key in path[:-1]:
        value = value[key]
    value[path[-1]] = replacement


@pytest.mark.parametrize('kind', ['markdown', 'executable'])
def test_fixture_validates_and_loads(kind):
    source = manifest(kind)
    assert packages.validate_manifest(source) == source
    assert packages.validate_intake(intake(kind)) == intake(kind)
    name = f'fixture-lab/{kind}-fixture/workflow'
    loaded = packages.load_external_skill(name, [record(kind)], policy(), now=NOW)
    assert loaded['text'].endswith((FIXTURES / kind / 'SKILL.md').read_text(encoding='utf-8'))
    assert loaded['artifact_digest'] == intake(kind)['artifact_digest']
    assert loaded['operations'] == source.get('operations', [])
    line, = packages.external_catalog_lines([record(kind)], policy(), now=NOW)
    for text in (name, 'publisher-provided', 'publisher=fixture-lab', 'release=1.0.0',
                 'sha256:' + intake(kind)['artifact_digest'], source['skills'][0]['description']):
        assert text in line
    assert '\n' not in line


# Mutate real fixtures, and assert structured field paths rather than prose.
CASES = [
    (('kind',), 'unknown', 'kind'),
    (('publisher',), '', 'publisher'),
    (('package_id',), 'bad/name', 'package_id'),
    (('version',), 'not-a-version', 'version'),
    (('microclaw',), 'not-a-range', 'microclaw'),
    (('license',), None, 'license'),
    (('source_url',), 'file:///source', 'source_url'),
    (('issues_url',), 'https://[broken', 'issues_url'),
    (('artifact',), 'https://user:password@example.org/a', 'artifact'),
    (('assets',), [], 'assets'),
    (('assets', 0), [], 'assets[0]'),
    (('assets', 0, 'path'), '../SKILL.md', 'assets[0].path'),
    (('assets', 0, 'sha256'), 'xyz', 'assets[0].sha256'),
    (('skills',), {}, 'skills'),
    (('skills', 0), None, 'skills[0]'),
    (('skills', 0, 'name'), 'Bad', 'skills[0].name'),
    (('skills', 0, 'description'), ' ', 'skills[0].description'),
    (('skills', 0, 'path'), 'missing/SKILL.md', 'skills[0].path'),
    (('skills', 0, 'path'), 'notes.txt', 'skills[0].path'),
    (('skills', 0, 'path'), '/SKILL.md', 'skills[0].path'),
    (('python',), '3.bogus', 'python'),
    (('platforms',), [], 'platforms'),
    (('platforms', 0), ['windows'], 'platforms[0]'),
    (('platforms', 0), 'bad platform', 'platforms[0]'),
    (('platforms',), ['win_amd64', 'win_amd64'], 'platforms[1]'),
    (('protocol_version',), 'bogus', 'protocol_version'),
    (('entry_point',), 'python -m worker', 'entry_point'),
    (('entry_point',), ['python', '-m', 'worker'], 'entry_point'),
    (('operations',), [], 'operations'),
    (('operations', 0), None, 'operations[0]'),
    (('operations', 0, 'name'), 'bad-name', 'operations[0].name'),
    (('operations', 0, 'input_schema'), [], 'operations[0].input_schema'),
    (('operations', 0, 'output_schema'), {}, 'operations[0].output_schema'),
    (('operations', 0, 'input_schema', 'type'), '', 'operations[0].input_schema.type'),
    (('locks',), [], 'locks'),
    (('locks', 'win_amd64'), {}, 'locks.win_amd64'),
    (('locks', 'win_amd64', 0), None, 'locks.win_amd64[0]'),
    (('locks', 'win_amd64', 0, 'requirement'), None, 'locks.win_amd64[0].requirement'),
    (('locks', 'win_amd64', 0, 'hashes'), [], 'locks.win_amd64[0].hashes'),
    (('locks', 'win_amd64', 0, 'hashes', 0), 'a'*63, 'locks.win_amd64[0].hashes[0]'),
]


@pytest.mark.parametrize('path,replacement,field', CASES)
def test_fixture_refusals(path, replacement, field):
    value = manifest()
    put(value, path, replacement)
    refuses(field, packages.validate_manifest, value)


@pytest.mark.parametrize('value', [None, [], 'manifest'])
def test_manifest_requires_object(value):
    refuses('manifest', packages.validate_manifest, value)


@pytest.mark.parametrize('field', sorted(manifest()))
def test_each_required_manifest_field(field):
    value = manifest()
    del value[field]
    refuses(field, packages.validate_manifest, value)


@pytest.mark.parametrize('field', ['python', 'platforms', 'protocol_version', 'entry_point',
                                  'operations', 'locks', 'environment', 'worker'])
def test_markdown_forbids_executable_fields(field):
    value = manifest('markdown')
    value[field] = manifest().get(field, {})
    refuses(field, packages.validate_manifest, value)


@pytest.mark.parametrize('field', ['artifact_digest', 'digest', 'sha256', 'signature', 'unknown'])
def test_manifest_has_no_self_digest_or_unrecognized_fields(field):
    value = manifest()
    value[field] = 'a'*64
    refuses(field, packages.validate_manifest, value)


@pytest.mark.parametrize('path,field', [
    (('assets', 0), 'assets[0]'), (('skills', 0), 'skills[0]'),
    (('entry_point',), 'entry_point'), (('operations', 0), 'operations[0]'),
    (('locks', 'win_amd64', 0), 'locks.win_amd64[0]'),
])
def test_nested_objects_closed_and_required(path, field):
    base = manifest()
    obj = base
    for key in path:
        obj = obj[key]
    for key in list(obj):
        value = deepcopy(base)
        replacement = deepcopy(obj)
        del replacement[key]
        put(value, path, replacement)
        refuses(field + '.' + key, packages.validate_manifest, value)
    value = deepcopy(base)
    put(value, path, {**obj, 'unknown': True})
    refuses(field + '.unknown', packages.validate_manifest, value)


@pytest.mark.parametrize('collection,key', [
    ('assets', 'path'), ('skills', 'name'), ('operations', 'name'),
])
def test_duplicate_declarations(collection, key):
    value = manifest()
    field = f'{collection}[{len(value[collection])}].{key}'
    value[collection].append(deepcopy(value[collection][0]))
    refuses(field, packages.validate_manifest, value)


@pytest.mark.parametrize('requirement', ['thing>=1.0', 'thing==1.*', 'thing', 'thing~=1.0',
    'thing===1.0', 'thing==1.0,!=1.1', 'thing @ https://example.org/a.whl',
    'thing==1.0; python_version>="3"', 'not a requirement', 'thing==nonsense'])
def test_lock_is_exact_and_unconditional(requirement):
    value = manifest()
    value['locks']['win_amd64'][0]['requirement'] = requirement
    refuses('locks.win_amd64[0].requirement', packages.validate_manifest, value)


@pytest.mark.parametrize('module', ['worker --run', 'worker;exit', 'worker|other', '$(worker)',
    'worker/runner', r'worker\runner', 'worker:main', 'worker..runner', '.worker',
    'worker.', '1worker', 'worker.class', ['worker'], 'worker\nrunner'])
def test_entry_point_is_only_a_module_declaration(module):
    value = manifest()
    value['entry_point']['module'] = module
    refuses('entry_point.module', packages.validate_manifest, value)


def test_protocol_is_structural_and_module_is_not_imported():
    value = manifest()
    value['protocol_version'] = '999.42'
    value['entry_point']['module'] = 'does_not_exist.some_runner'
    value['operations'][0]['name'] = 'future_operation'
    value['operations'][0]['input_schema']['type'] = 'future-type'
    value['locks']['win_amd64'] = []
    assert packages.validate_manifest(value) == value


def test_locks_match_declared_platforms():
    value = manifest()
    del value['locks']['win_amd64']
    refuses('locks.win_amd64', packages.validate_manifest, value)
    value = manifest()
    value['locks']['other'] = []
    refuses('locks.other', packages.validate_manifest, value)


RENDERED = [
    (('publisher',), 'publisher', packages.MAX_PUBLISHER_LENGTH, 'a'),
    (('package_id',), 'package_id', packages.MAX_PACKAGE_ID_LENGTH, 'a'),
    (('skills', 0, 'name'), 'skills[0].name', packages.MAX_SKILL_NAME_LENGTH, 'a'),
    (('skills', 0, 'description'), 'skills[0].description', packages.MAX_DESCRIPTION_LENGTH, 'a'),
    (('version',), 'version', packages.MAX_VERSION_LENGTH, '1'),
    (('protocol_version',), 'protocol_version', packages.MAX_VERSION_LENGTH, '1'),
    (('license',), 'license', packages.MAX_LICENSE_LENGTH, 'a'),
    (('entry_point', 'module'), 'entry_point.module', packages.MAX_MODULE_LENGTH, 'a'),
    (('operations', 0, 'name'), 'operations[0].name', packages.MAX_OPERATION_NAME_LENGTH, 'a'),
    (('operations', 0, 'input_schema', 'type'), 'operations[0].input_schema.type',
     packages.MAX_SCHEMA_TYPE_LENGTH, 'a'),
    (('operations', 0, 'output_schema', 'type'), 'operations[0].output_schema.type',
     packages.MAX_SCHEMA_TYPE_LENGTH, 'a'),
]


@pytest.mark.parametrize('path,field,limit,char', RENDERED)
def test_rendered_length_boundaries(path, field, limit, char):
    value = manifest()
    put(value, path, char * limit)
    assert packages.validate_manifest(value) == value
    put(value, path, char * (limit + 1))
    refuses(field, packages.validate_manifest, value)


STRUCTURED_TEXT = [
    (('source_url',), 'source_url', packages.MAX_URL_LENGTH, 'https://example.org/', ''),
    (('issues_url',), 'issues_url', packages.MAX_URL_LENGTH, 'https://example.org/', ''),
    (('artifact',), 'artifact', packages.MAX_URL_LENGTH, 'https://example.org/', ''),
    (('platforms', 0), 'platforms[0]', packages.MAX_PLATFORM_LENGTH, '', ''),
    (('microclaw',), 'microclaw', packages.MAX_SPECIFIER_LENGTH, '==1+', ''),
    (('python',), 'python', packages.MAX_SPECIFIER_LENGTH, '==1+', ''),
    (('assets', 1, 'path'), 'assets[1].path', packages.MAX_PATH_LENGTH, '', '.txt'),
    (('skills', 0, 'path'), 'skills[0].path', packages.MAX_PATH_LENGTH, '', '/SKILL.md'),
    (('locks', 'win_amd64', 0, 'requirement'), 'locks.win_amd64[0].requirement',
     packages.MAX_REQUIREMENT_LENGTH, '', '==1.0'),
]


@pytest.mark.parametrize('path,field,limit,prefix,suffix', STRUCTURED_TEXT)
def test_structured_text_length_boundaries(path, field, limit, prefix, suffix):
    value = manifest()
    text = prefix + 'a' * (limit - len(prefix) - len(suffix)) + suffix
    put(value, path, text)
    if field == 'platforms[0]':
        value['locks'][text] = value['locks'].pop('win_amd64')
    if field == 'skills[0].path':
        value['assets'][0]['path'] = text
    assert packages.validate_manifest(value) == value
    put(value, path, text + 'a')
    refuses(field, packages.validate_manifest, value)


@pytest.mark.parametrize('field,limit', [
    ('assets', packages.MAX_ASSETS), ('skills', packages.MAX_SKILLS),
    ('operations', packages.MAX_OPERATIONS), ('platforms', packages.MAX_PLATFORMS),
    ('locks.win_amd64', packages.MAX_LOCK_ENTRIES),
    ('locks.win_amd64[0].hashes', packages.MAX_REQUIREMENT_HASHES),
])
def test_collection_length_boundaries(field, limit):
    value = manifest()
    if field == 'assets':
        value['assets'] += [dict(path=f'asset-{i}.txt', sha256='a'*64)
                            for i in range(limit - len(value['assets']))]
        collection = value['assets']
    elif field == 'skills':
        value['skills'] = [dict(value['skills'][0], name=f'skill-{i}') for i in range(limit)]
        collection = value['skills']
    elif field == 'operations':
        value['operations'] = [dict(value['operations'][0], name=f'op_{i}') for i in range(limit)]
        collection = value['operations']
    elif field == 'platforms':
        value['platforms'] = [f'platform_{i}' for i in range(limit)]
        value['locks'] = {platform: [] for platform in value['platforms']}
        collection = value['platforms']
    elif field == 'locks.win_amd64':
        value['locks']['win_amd64'] = [dict(requirement=f'dep-{i}==1.0', hashes=['a'*64])
                                     for i in range(limit)]
        collection = value['locks']['win_amd64']
    else:
        value['locks']['win_amd64'][0]['hashes'] = [f'{i:064x}' for i in range(limit)]
        collection = value['locks']['win_amd64'][0]['hashes']
    assert packages.validate_manifest(value) == value
    collection.append(deepcopy(collection[-1]))
    refuses(field, packages.validate_manifest, value)


CONTROLS = [chr(n) for n in range(32)] + [chr(n) for n in range(127, 160)] + ['\u2028', '\u2029']


@pytest.mark.parametrize('path,field,limit,char', RENDERED)
@pytest.mark.parametrize('control', CONTROLS)
def test_every_rendered_manifest_field_refuses_controls(path, field, limit, char, control):
    value = manifest()
    put(value, path, char + control + char)
    refuses(field, packages.validate_manifest, value)


@pytest.mark.parametrize('path,field,limit,prefix,suffix', STRUCTURED_TEXT)
@pytest.mark.parametrize('control', CONTROLS)
def test_structured_text_refuses_controls(path, field, limit, prefix, suffix, control):
    value = manifest()
    put(value, path, prefix + 'a' + control + 'a' + suffix)
    refuses(field, packages.validate_manifest, value)


@pytest.mark.parametrize('control', CONTROLS)
def test_rendered_digest_refuses_controls(control):
    value = intake()
    value['artifact_digest'] = 'a'*31 + control + 'a'*32
    refuses('artifact_digest', packages.validate_intake, value)


def test_digest_length_bound():
    value = intake()
    value['artifact_digest'] = 'a' * packages.MAX_ARTIFACT_DIGEST_LENGTH
    assert packages.validate_intake(value) == value
    value['artifact_digest'] += 'a'
    refuses('artifact_digest', packages.validate_intake, value)


@pytest.mark.parametrize('field', sorted(intake()))
def test_intake_required_fields(field):
    value = intake()
    del value[field]
    refuses(field, packages.validate_intake, value)


@pytest.mark.parametrize('field,value', [('publisher','Bad'), ('package_id','bad/name'),
    ('version','bad'), ('artifact','relative.zip'), ('artifact_digest','A'*64),
    ('signatures',[]), ('signing_key','placeholder')])
def test_intake_refusals(field, value):
    data = intake()
    data[field] = value
    refuses(field, packages.validate_intake, data)


def test_intake_requires_mapping():
    refuses('intake', packages.validate_intake, [])


@pytest.mark.parametrize('url', ['http://example.org/a', '/a', 'https:///a',
    'https://example.org/a#fragment', 'https://example.org:bad/a',
    'https://example.org/a b', 'https://example.org:0/a', r'https://example.org\a'])
def test_reference_url_structure(url):
    value = manifest()
    value['artifact'] = url
    refuses('artifact', packages.validate_manifest, value)


BAD_PATHS = ['/absolute', '../escape', 'a/../escape', 'a//b', './a', 'a/',
             'C:/escape', 'C:escape', r'C:\escape', r'\\server\share\a',
             '//server/share/a', r'a\b', 'a:stream', 'a\x00b']


@pytest.mark.parametrize('path', BAD_PATHS)
def test_paths_refuse_cross_platform(path, tmp_path):
    value = manifest()
    value['assets'][0]['path'] = path
    refuses('assets[0].path', packages.validate_manifest, value)
    refuses('extraction.path', packages.safe_release_path, tmp_path, path, field='extraction.path')


def test_safe_extraction_destination(tmp_path):
    assert packages.safe_release_path(tmp_path, 'sub/asset.txt') == tmp_path/'sub'/'asset.txt'
    refuses('path', packages.safe_release_path, tmp_path, 'asset', is_symlink=True)


@pytest.mark.parametrize('target', ['inside', 'outside', 'dangling'])
def test_symlink_components_refuse(tmp_path, target):
    root = tmp_path/'release'
    root.mkdir()
    destination = root/'inside' if target == 'inside' else tmp_path/target
    if target != 'dangling':
        destination.mkdir()
    try:
        (root/'link').symlink_to(destination, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f'Host cannot create test symlinks: {exc}')
    refuses('assets[0].path', packages.safe_release_path, root, 'link/SKILL.md', field='assets[0].path')
    refuses('path', packages.safe_release_path, root/'link', 'SKILL.md')


def test_resolved_escape_refuses(tmp_path, monkeypatch):
    root = tmp_path/'release'
    original = Path.resolve
    def resolve(path, *args, **kwargs):
        if path == root/'asset':
            return tmp_path/'outside'
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'resolve', resolve)
    refuses('path', packages.safe_release_path, root, 'asset')


@pytest.mark.parametrize('parts', [('lab', 'package', 'skill'), ('a-b', 'a1', 's-2')])
def test_qualified_name_roundtrip(parts):
    assert packages.parse_qualified_name(packages.qualified_name(*parts)) == parts


@pytest.mark.parametrize('name', ['skill', '/package/skill', 'lab/package/skill/extra', None])
def test_qualified_name_refusals(name):
    refuses('publisher' if name == '/package/skill' else 'name', packages.parse_qualified_name, name)


def test_external_identity_cannot_shadow_builtin_catalog():
    original = skills.SKILL_CATALOG
    assert all('/' not in item.name for item in original)
    for item in original:
        # All built-in names remain distinct even if an external skill uses its leaf name.
        qualified = packages.qualified_name('lab', 'package', item.name)
        assert qualified not in {builtin.name for builtin in original}
        refuses('name', packages.load_external_skill, item.name, [record()], policy(), now=NOW)
    packages.external_catalog_lines([record()], policy(), now=NOW)
    assert skills.SKILL_CATALOG is original


@pytest.mark.parametrize('changes', [{'enabled':False}, {'verified':False}, {'enabled':1}, {'verified':'yes'}])
def test_only_explicit_enabled_verified_records_resolve(changes):
    value = record(**changes)
    # Ineligible metadata isn't even read, and no directory scan takes place.
    value['manifest'] = None
    assert packages.external_catalog_lines([value], policy(), now=NOW) == ()
    refuses('name', packages.load_external_skill, 'fixture-lab/executable-fixture/workflow', [value], policy(), now=NOW)


def test_no_implicit_discovery_or_duplicate_resolution():
    name = 'fixture-lab/executable-fixture/workflow'
    assert packages.external_catalog_lines([], policy(), now=NOW) == ()
    refuses('name', packages.load_external_skill, name, [], policy(), now=NOW)
    refuses('name', packages.load_external_skill, name, [record(), record()], policy(), now=NOW)
    exclusions = []
    assert packages.external_catalog_lines([record(), record()], policy(), now=NOW, exclusions=exclusions) == ()
    assert len(exclusions) == 2 and all(exc.field == 'name' for _, exc in exclusions)


@pytest.mark.parametrize('field,replacement', [('publisher','other'), ('package_id','other'),
    ('version','2.0'), ('artifact','https://example.org/other.zip'),
    ('microclaw','>=99'), ('python','>=99'), ('platforms',['linux_x86_64']),
    ('protocol_version','2.0'), ('kind','markdown')])
def test_manifest_and_external_release_identity_must_match(field, replacement):
    value = record()
    value['intake'][field] = replacement
    if field == 'kind':
        for key in ('python', 'platforms', 'protocol_version'):
            del value['intake'][key]
    refuses('intake.' + field, packages.load_external_skill, 'fixture-lab/executable-fixture/workflow', [value], policy(), now=NOW)


@pytest.mark.parametrize('asset', ['SKILL.md', 'notes.txt'])
def test_loaded_asset_bytes_are_bound_to_manifest(tmp_path, asset):
    root = tmp_path/'release'
    shutil.copytree(FIXTURES/'executable', root)
    (root/asset).write_text('modified', encoding='utf-8')
    index = 0 if asset == 'SKILL.md' else 1
    refuses(f'assets[{index}].sha256', packages.load_external_skill,
            'fixture-lab/executable-fixture/workflow', [record(release_dir=root)], policy(), now=NOW)


def test_missing_unreadable_or_symlink_asset_refuses(tmp_path):
    root = tmp_path/'release'
    shutil.copytree(FIXTURES/'markdown', root)
    (root/'SKILL.md').unlink()
    data = record('markdown', release_dir=root)
    name = 'fixture-lab/markdown-fixture/workflow'
    refuses('assets[0].path', packages.load_external_skill, name, [data], policy(), now=NOW)
    (root/'SKILL.md').mkdir()
    refuses('assets[0].path', packages.load_external_skill, name, [data], policy(), now=NOW)
    (root/'SKILL.md').rmdir()
    try:
        (root/'SKILL.md').symlink_to(FIXTURES/'markdown'/'SKILL.md')
    except OSError as exc:
        pytest.skip(f'Host cannot create test symlinks: {exc}')
    refuses('assets[0].path', packages.load_external_skill, name, [data], policy(), now=NOW)


def test_skill_must_decode_as_utf8(tmp_path):
    root = tmp_path/'release'
    shutil.copytree(FIXTURES/'markdown', root)
    (root/'SKILL.md').write_bytes(b'\xff')
    data = record('markdown', release_dir=root)
    data['manifest']['assets'][0]['sha256'] = hashlib.sha256(b'\xff').hexdigest()
    refuses('skills.path', packages.load_external_skill, 'fixture-lab/markdown-fixture/workflow', [data], policy(), now=NOW)


def test_loaded_operations_and_text_are_detached_from_caller_mutation():
    data = record()
    loaded = packages.load_external_skill('fixture-lab/executable-fixture/workflow', [data], policy(), now=NOW)
    data['manifest']['operations'][0]['name'] = 'changed'
    data['intake']['artifact_digest'] = 'b'*64
    assert loaded['operations'][0]['name'] == 'self_check'
    assert loaded['artifact_digest'] == '0'*64
    assert 'sha256:' + '0'*64 in loaded['text']


# 83e must record analysis outcomes under a nested dict, e.g.
# {"analysis": {"state": ..., "error": ...}}, never a top-level error or an
# error entry in a top-level list under ANY key. _recorded_outcome scans every
# top-level list-valued key; that shape would turn a flawless acquisition into
# a refuse() at export time.
@pytest.mark.parametrize('result', [
    {'analysis': {'state': 'failed', 'error': 'worker crashed'}},
    {'analysis': {'state': 'failed', 'details': [{'error': 'worker crashed'}]}},
    {'error_um': 0.4},
])
def test_recorded_outcome_nested_dict_is_invisible(result):
    assert _recorded_outcome(result) is None


@pytest.mark.parametrize('result,expected', [
    ({'analysis': [{'error': 'worker crashed'}, {'error': 'another failure'}]}, 'nothing'),
    ({'analysis': [{'error': 'worker crashed'}, {'state': 'complete'}]}, 'partial'),
    ({'error': 'call failed'}, 'nothing'),
])
def test_recorded_outcome_top_level_failures_are_visible(result, expected):
    outcome = _recorded_outcome(result)
    assert outcome is not None
    assert outcome[0] == expected


@pytest.mark.parametrize('kind', ['markdown', 'executable'])
def test_committed_intake_signature_is_reproducible_and_verifies(kind):
    value = intake(kind)
    assert sign(value) == value
    verdict = packages.check_release(value, policy(), purpose='execution', now=NOW)
    assert verdict == dict(publisher='fixture-lab', key_id=value['signature']['key_id'],
                           key_state='active', revision=1, stale=False, purpose='execution', environment='test')


def test_committed_policy_signature_is_reproducible_and_detached():
    document = trust_file('policy')
    assert sign(document, 'root') == document
    roots = trust_file('roots')
    snapshot = packages.verify_trust_policy(document, roots)
    document['publishers'].clear()
    roots['keys'].clear()
    assert snapshot == policy()


@pytest.mark.parametrize('purpose', ['admission', 'execution'])
def test_missing_policy_refuses(purpose):
    refuses('trust', packages.check_release, intake(), None, purpose=purpose, now=NOW)


def test_unsigned_smappy_refuses_admission():
    value = json.loads((FIXTURES / 'smappy-0.1.0-unsigned-intake.json').read_text(encoding='utf-8'))
    assert (value['publisher'], value['package_id'], value['version']) == ('ries-lab', 'smappy', '0.1.0')
    refuses('signature', packages.check_release, value, policy(), purpose='admission', now=NOW)


@pytest.mark.parametrize('path,replacement,field', [
    (('signature',), None, 'signature'),
    (('signature',), {}, 'signature.alg'),
    (('signature', 'alg'), 'rsa', 'signature.alg'),
    (('signature', 'key_id'), 'A'*64, 'signature.key_id'),
    (('signature', 'value'), '*', 'signature.value'),
    (('signature', 'value'), base64.b64encode(b'x'*63).decode(), 'signature.value'),
    (('signature', 'value'), base64.b64encode(b'x'*64).decode(), 'signature.value'),
    (('signature', 'extra'), True, 'signature.extra'),
    (('public_key',), 'submission key', 'public_key'),
    (('key',), 'submission key', 'key'),
])
def test_signature_structure_and_submission_keys_refuse(path, replacement, field):
    value = intake()
    put(value, path, replacement)
    refuses(field, packages.check_release, value, policy(), purpose='execution', now=NOW)


@pytest.mark.parametrize('purpose', ['admission', 'execution'])
def test_unknown_publisher_and_other_publishers_key(purpose):
    value = intake()
    value['publisher'] = 'unknown-lab'
    refuses('publisher', packages.check_release, sign(value), policy(), purpose=purpose, now=NOW)
    document = trust_file('policy')
    other = document['publishers']['fixture-lab']['keys'].pop()
    document['publishers']['other-lab'] = dict(state='active', keys=[other])
    value = sign(intake(), 'publisher-b')
    refuses('signature.key_id', packages.check_release, value, policy(document), purpose=purpose, now=NOW)


@pytest.mark.parametrize('purpose', ['admission', 'execution'])
def test_forgery_under_admitted_key_id(purpose):
    document = trust_file('policy')
    document['publishers']['fixture-lab']['keys'].pop()
    forged = sign(intake(), 'publisher-b')
    refuses('signature.key_id', packages.check_release, forged, policy(document),
            purpose=purpose, now=NOW)
    forged['signature']['key_id'] = intake()['signature']['key_id']
    refuses('signature.value', packages.check_release, forged, policy(document),
            purpose=purpose, now=NOW)


@pytest.mark.parametrize('field,replacement,expected', [
    ('type', packages.TRUST_POLICY_TYPE, 'type'),
    ('package_id', 'different', 'signature.value'),
    ('publisher', 'other-lab', 'signature.key_id'),
    ('version', '2.0', 'signature.value'),
    ('artifact', 'https://example.org/different.zip', 'signature.value'),
    ('artifact_digest', 'f'*64, 'signature.value'),
    ('kind', 'markdown', 'signature.value'),
    ('microclaw', '>=99', 'signature.value'),
    ('python', '>=99', 'signature.value'),
    ('platforms', ['linux_x86_64'], 'signature.value'),
    ('protocol_version', '2.0', 'signature.value'),
])
@pytest.mark.parametrize('purpose', ['admission', 'execution'])
def test_every_signed_release_field_is_bound(field, replacement, expected, purpose):
    value = intake()
    value[field] = replacement
    if field == 'kind':
        for key in ('python', 'platforms', 'protocol_version'):
            del value[key]
    document = trust_file('policy')
    other = document['publishers']['fixture-lab']['keys'].pop()
    document['publishers']['other-lab'] = dict(state='active', keys=[other])
    refuses(expected, packages.check_release, value, policy(document), purpose=purpose, now=NOW)


@pytest.mark.parametrize('kind', ['markdown', 'executable'])
def test_admission_computes_artifact_digest(kind, tmp_path):
    artifact = b'illustrative release bytes\x00\xff'
    value = intake(kind)
    value['artifact_digest'] = hashlib.sha256(artifact).hexdigest()
    value = sign(value)
    path = tmp_path / 'release.zip'
    path.write_bytes(artifact)
    for source in (artifact, path, str(path)):
        assert packages.check_release(value, policy(), purpose='admission', now=NOW,
                                      artifact=source)['stale'] is False
    for source in (None, b'changed', b'', 'missing-artifact', '\x00', {'digest': value['artifact_digest']}):
        refuses('artifact_digest', packages.check_release, value, policy(),
                purpose='admission', now=NOW, artifact=source)
    path.write_bytes(b'changed')
    refuses('artifact_digest', packages.check_release, value, policy(),
            purpose='admission', now=NOW, artifact=path)


@pytest.mark.parametrize('purpose', ['admission', 'execution'])
@pytest.mark.parametrize('change,field', [
    ('retired', 'signature.key_id'), ('revoked', 'signature.key_id'),
    ('publisher', 'publisher'), ('release', 'artifact_digest'),
])
def test_rotation_and_revocation_across_revisions(purpose, change, field):
    value = intake()
    value['artifact_digest'] = hashlib.sha256(b'release').hexdigest()
    value = sign(value)
    previous = policy()
    assert packages.check_release(value, previous, purpose=purpose, now=NOW,
                                  artifact=b'release')['revision'] == 1
    document = trust_file('policy')
    document['revision'] = 2
    if change in ('retired', 'revoked'):
        document['publishers']['fixture-lab']['keys'][0]['state'] = change
    elif change == 'publisher':
        document['publishers']['fixture-lab']['state'] = 'revoked'
    else:
        # The digest is the revocation identity even if descriptive fields differ.
        document['revoked_releases'] = [dict(package_id='another', version='9.0',
                                            artifact_digest=value['artifact_digest'])]
    current = policy(document, previous=previous)
    if change == 'retired' and purpose == 'execution':
        result = packages.check_release(value, current, purpose=purpose, now=NOW)
        assert result['key_state'] == 'retired'
        assert result['revision'] == 2
        replacement = sign(value, 'publisher-b')
        assert packages.check_release(replacement, current, purpose='admission',
                                      now=NOW, artifact=b'release')['key_state'] == 'active'
    else:
        refuses(field, packages.check_release, value, current, purpose=purpose,
                now=NOW, artifact=b'release')


@pytest.mark.parametrize('state,field', [('active', None), ('retired', None),
                                       ('revoked', 'signature.key_id'),
                                       ('publisher', 'publisher'), ('release', 'artifact_digest')])
def test_stale_execution_still_applies_revocations(state, field):
    document = trust_file('policy')
    document['expires_at'] = '2020-01-01T00:00:00Z'
    if state == 'publisher':
        document['publishers']['fixture-lab']['state'] = 'revoked'
    elif state == 'release':
        document['revoked_releases'] = [{key: intake()[key] for key in
                                        ('package_id', 'version', 'artifact_digest')}]
    else:
        document['publishers']['fixture-lab']['keys'][0]['state'] = state
    snapshot = policy(document)
    if field:
        refuses(field, packages.check_release, intake(), snapshot, purpose='execution', now=NOW)
    else:
        assert packages.check_release(intake(), snapshot, purpose='execution', now=NOW)['stale']
    refuses(field or ('signature.key_id' if state == 'retired' else 'trust.expires_at'),
            packages.check_release, intake(), snapshot, purpose='admission', now=NOW)


def test_expiry_boundary_and_explicit_time():
    snapshot = policy()
    expiry = datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert not packages.check_release(intake(), snapshot, purpose='execution', now=expiry)['stale']
    for now in (None, datetime(2026, 1, 1), '2026-01-01'):
        refuses('now', packages.check_release, intake(), snapshot, purpose='execution', now=now)
    refuses('purpose', packages.check_release, intake(), snapshot, purpose='refresh', now=NOW)


def test_production_roots_fail_closed_and_test_environment_is_separate():
    roots = trust_file('roots')
    document = trust_file('policy')
    assert packages.PRODUCTION_ROOTS == dict(environment='production', keys=[])
    assert not ({k['key_id'] for k in roots['keys']} &
                {k['key_id'] for k in packages.PRODUCTION_ROOTS['keys']})
    refuses('trust.environment', packages.verify_trust_policy, document)
    document['environment'] = 'production'
    document = sign(document, 'root')
    refuses('trust.environment', packages.verify_trust_policy, document, roots)
    refuses('trust.signature.key_id', packages.verify_trust_policy, document)


def test_policy_rollback_and_equal_revision_content():
    first = policy()
    assert policy(previous=first) == first
    document = trust_file('policy')
    document['revision'] = 2
    second = policy(document, previous=first)
    refuses('trust.revision', packages.verify_trust_policy, trust_file('policy'),
            trust_file('roots'), previous=second)
    document['expires_at'] = '2031-01-01T00:00:00Z'
    refuses('trust.revision', policy, document, previous=second)


@pytest.mark.parametrize('field,replacement', [
    ('environment', 'production'), ('type', packages.RELEASE_TYPE),
    ('revision', 2), ('expires_at', '2031-01-01T00:00:00Z'),
    ('publishers', {}), ('revoked_releases', [dict(package_id='a',version='1',artifact_digest='0'*64)]),
])
def test_policy_signed_fields_cannot_change(field, replacement):
    document = trust_file('policy')
    document[field] = replacement
    expected = {'environment': 'trust.environment', 'type': 'trust.type'}.get(field, 'trust.signature.value')
    refuses(expected, packages.verify_trust_policy, document, trust_file('roots'))


@pytest.mark.parametrize('root_key', [True, False])
def test_key_ids_are_derived_at_every_admission(root_key):
    document, roots = trust_file('policy'), trust_file('roots')
    if root_key:
        roots['keys'][0]['key_id'] = 'f'*64
        expected = 'roots.keys[0].key_id'
    else:
        document['publishers']['fixture-lab']['keys'][0]['key_id'] = 'f'*64
        expected = 'trust.publishers.fixture-lab.keys[0].key_id'
    refuses(expected, packages.verify_trust_policy, sign(document, 'root'), roots)


def test_key_cannot_bind_two_publishers():
    document = trust_file('policy')
    document['publishers']['other-lab'] = deepcopy(document['publishers']['fixture-lab'])
    refuses('trust.publishers.other-lab.keys[0].key_id', policy, document)


@pytest.mark.parametrize('path,replacement,field', [
    (('revision',), True, 'trust.revision'), (('revision',), 0, 'trust.revision'),
    (('expires_at',), '2030-02-30T00:00:00Z', 'trust.expires_at'),
    (('expires_at',), '2030-01-01T00:00:00+00:00', 'trust.expires_at'),
    (('expires_at',), '', 'trust.expires_at'),
    (('publishers',), [], 'trust.publishers'),
    (('publishers','fixture-lab','state'), 'retired', 'trust.publishers.fixture-lab.state'),
    (('publishers','fixture-lab','keys'), [], 'trust.publishers.fixture-lab.keys'),
    (('publishers','fixture-lab','keys',0,'state'), 'unknown', 'trust.publishers.fixture-lab.keys[0].state'),
    (('publishers','fixture-lab','keys',0,'public_key'), '*', 'trust.publishers.fixture-lab.keys[0].public_key'),
    (('revoked_releases',), {}, 'trust.revoked_releases'),
    (('revoked_releases',), [{}], 'trust.revoked_releases[0].artifact_digest'),
])
def test_policy_field_refusals(path, replacement, field):
    document = trust_file('policy')
    put(document, path, replacement)
    refuses(field, policy, document)


@pytest.mark.parametrize('location,field', [
    ((), 'trust'), (('publishers', 'fixture-lab'), 'trust.publishers.fixture-lab'),
    (('publishers', 'fixture-lab', 'keys', 0), 'trust.publishers.fixture-lab.keys[0]'),
    (('signature',), 'trust.signature'),
])
def test_policy_closed_required_objects(location, field):
    base = trust_file('policy')
    obj = base
    for component in location:
        obj = obj[component]
    for key in [*obj, 'unknown']:
        value = deepcopy(base)
        target = value
        for component in location:
            target = target[component]
        if key == 'unknown':
            target[key] = True
        else:
            del target[key]
        refuses(field + '.' + key, packages.verify_trust_policy, value, trust_file('roots'))


def test_release_gates_verify_only_release_signatures(monkeypatch):
    snapshot = policy()
    records = [record('markdown'), record('executable')]
    calls = []
    original = packages._verify_signature

    def verify(document, keys, field="signature"):
        calls.append(document['type'])
        return original(document, keys, field)

    monkeypatch.setattr(packages, '_verify_signature', verify)
    packages.check_release(intake(), snapshot, purpose='execution', now=NOW)
    assert calls == [packages.RELEASE_TYPE]
    calls.clear()
    assert len(packages.external_catalog_lines(records, snapshot, now=NOW)) == len(records)
    assert calls == [packages.RELEASE_TYPE] * len(records)
    calls.clear()
    assert policy(previous=snapshot) == snapshot
    assert calls == [packages.TRUST_POLICY_TYPE]


def test_loading_gates_require_verified_policy():
    exclusions = []
    assert packages.external_catalog_lines([record()], None, now=NOW, exclusions=exclusions) == ()
    assert exclusions[0][1].field == 'trust'
    refuses('trust', packages.load_external_skill, 'fixture-lab/executable-fixture/workflow',
            [record()], None, now=NOW)


@pytest.mark.parametrize('kind', ['markdown', 'executable'])
def test_revocation_blocks_loading_and_catalog_without_filesystem_or_network_effects(kind, tmp_path, monkeypatch):
    root = tmp_path / 'installed'
    shutil.copytree(FIXTURES / kind, root)
    def contents():
        return {str(p.relative_to(root)): p.read_bytes() if p.is_file() else None
                for p in root.rglob('*')}
    before = contents()
    document = trust_file('policy')
    document['revision'] = 2
    document['publishers']['fixture-lab']['state'] = 'revoked'
    snapshot = policy(document, previous=policy())
    installed = record(kind, release_dir=root)
    def forbidden(*args, **kwargs):
        raise AssertionError('trust verification must not perform I/O')
    with monkeypatch.context() as patch:
        patch.setattr(socket, 'socket', forbidden)
        patch.setattr(Path, 'open', forbidden)
        refuses('publisher', packages.check_release, installed['intake'], snapshot,
                purpose='execution', now=NOW)
        exclusions = []
        assert packages.external_catalog_lines([installed], snapshot, now=NOW, exclusions=exclusions) == ()
        assert exclusions[0][1].field == 'publisher'
        refuses('publisher', packages.load_external_skill,
                f'fixture-lab/{kind}-fixture/workflow', [installed], snapshot, now=NOW)
    assert contents() == before


def test_execution_and_byte_admission_open_no_files_or_sockets(monkeypatch):
    snapshot = policy()
    value = intake()
    value['artifact_digest'] = hashlib.sha256(b'release').hexdigest()
    value = sign(value)
    def forbidden(*args, **kwargs):
        raise AssertionError('unexpected I/O')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setattr(Path, 'open', forbidden)
    for purpose in ('admission', 'execution'):
        assert packages.check_release(value, snapshot, purpose=purpose, now=NOW,
                                      artifact=b'release')['purpose'] == purpose


@pytest.mark.parametrize('purpose', ['admission', 'execution'])
def test_missing_and_invalid_signature_at_release_gate(purpose):
    value = intake()
    del value['signature']
    refuses('signature', packages.check_release, value, policy(), purpose=purpose, now=NOW)
    value = intake()
    value['signature']['value'] = base64.b64encode(b'x'*64).decode('ascii')
    refuses('signature.value', packages.check_release, value, policy(), purpose=purpose, now=NOW)


def test_policy_requires_real_root_signature():
    document = trust_file('policy')
    del document['signature']
    refuses('trust.signature', packages.verify_trust_policy, document, trust_file('roots'))
    document = sign(document, 'publisher-a')
    refuses('trust.signature.key_id', packages.verify_trust_policy, document, trust_file('roots'))
    document['signature']['key_id'] = trust_file('roots')['keys'][0]['key_id']
    refuses('trust.signature.value', packages.verify_trust_policy, document, trust_file('roots'))


@pytest.mark.parametrize('location,field', [((), 'roots'), (('keys', 0), 'roots.keys[0]')])
def test_roots_closed_and_required(location, field):
    base = trust_file('roots')
    obj = base
    for component in location:
        obj = obj[component]
    for key in [*obj, 'unknown']:
        roots = deepcopy(base)
        target = roots
        for component in location:
            target = target[component]
        if key == 'unknown':
            target[key] = True
        else:
            del target[key]
        refuses(field + '.' + key, packages.verify_trust_policy, trust_file('policy'), roots)


@pytest.mark.parametrize('field,value', [
    ('environment', 'staging'), ('keys', None),
    ('keys', trust_file('roots')['keys'] * 2),
    ('keys', trust_file('roots')['keys'] * (packages.MAX_KEYS + 1)),
])
def test_root_structure_and_duplicate_key_refusals(field, value):
    roots = trust_file('roots')
    roots[field] = value
    expected = 'roots.keys[1].key_id' if field == 'keys' and isinstance(value, list) and len(value) == 2 else 'roots.' + field
    refuses(expected, packages.verify_trust_policy, trust_file('policy'), roots)


@pytest.mark.parametrize('field,value', [
    ('package_id', 'Bad'), ('version', 'bad'), ('artifact_digest', 'A'*64),
    ('unknown', True),
])
def test_revoked_release_fields(field, value):
    document = trust_file('policy')
    revoked = {key: intake()[key] for key in ('package_id', 'version', 'artifact_digest')}
    revoked[field] = value
    document['revoked_releases'] = [revoked]
    refuses('trust.revoked_releases[0].' + field, policy, document)


@pytest.mark.parametrize('collection', ['publishers', 'keys', 'revoked_releases'])
def test_trust_collection_limits(collection):
    document = trust_file('policy')
    if collection == 'publishers':
        document['publishers'] = {f'lab-{i}': {} for i in range(packages.MAX_PUBLISHERS + 1)}
        field = 'trust.publishers'
    elif collection == 'keys':
        document['publishers']['fixture-lab']['keys'] *= packages.MAX_KEYS
        field = 'trust.publishers.fixture-lab.keys'
    else:
        document['revoked_releases'] = [{}] * (packages.MAX_REVOKED_RELEASES + 1)
        field = 'trust.revoked_releases'
    refuses(field, policy, document)


@pytest.mark.parametrize('field,value,expected', [
    ('microclaw', 'bad', 'microclaw'), ('python', 'bad', 'python'),
    ('platforms', [], 'platforms'), ('platforms', ['x', 'x'], 'platforms[1]'),
    ('protocol_version', 'bad', 'protocol_version'),
])
def test_intake_compatibility_uses_manifest_validators(field, value, expected):
    document = intake()
    document[field] = value
    refuses(expected, packages.validate_intake, document)


def test_bad_record_is_excluded_without_hiding_other_skills():
    good = record('markdown')
    bad = record('executable')
    bad['intake']['signature']['value'] = base64.b64encode(b'x'*64).decode('ascii')
    assert packages.load_external_skill('fixture-lab/markdown-fixture/workflow',
        [good, bad], policy(), now=NOW)['publisher'] == 'fixture-lab'
    exclusions = []
    assert len(packages.external_catalog_lines([good, bad], policy(), now=NOW, exclusions=exclusions)) == 1
    assert exclusions == [(bad, exclusions[0][1])]
    assert exclusions[0][1].field == 'signature.value'


@pytest.mark.parametrize("version", ["1.1", "2.0"])
@pytest.mark.parametrize("purpose", ["admission", "execution"])
def test_supported_protocol_after_signature(version, purpose):
    value = intake()
    value["protocol_version"] = version
    refuses("signature.value", packages.check_release, value, policy(), purpose=purpose, now=NOW)
    refuses("protocol_version", packages.check_release, sign(value), policy(), purpose=purpose, now=NOW)


@pytest.mark.parametrize("bad", [None, [], {}, {"enabled": True, "verified": True, "manifest": []},
    {"enabled": True, "verified": True, "manifest": {"skills": [None]}}])
def test_malformed_record_never_hides_another_package(bad):
    good = record('markdown')
    exclusions = []
    assert len(packages.external_catalog_lines([bad, good], policy(), now=NOW, exclusions=exclusions)) == 1
    assert len(exclusions) == 1 and exclusions[0][0] is bad


@pytest.mark.parametrize("change", ["signature", "description", "eligibility"])
def test_duplicate_excludes_both_even_when_one_carrier_is_invalid(change):
    good, bad = record(), record()
    if change == "signature":
        bad['intake']['signature']['value'] = 'invalid'
    elif change == "description":
        bad['manifest']['skills'][0]['description'] = None
    else:
        bad['eligible'] = False
    exclusions = []
    assert packages.external_catalog_lines([good, bad], policy(), now=NOW, exclusions=exclusions) == ()
    assert len(exclusions) == 2
    assert all(exc.field == 'name' for _, exc in exclusions)
    refuses('name', packages.load_external_skill, 'fixture-lab/executable-fixture/workflow',
            [good, bad], policy(), now=NOW)
