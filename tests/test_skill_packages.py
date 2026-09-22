"""Release format tests; verification/installation/worker execution are not faked."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil

import pytest

from microclaw import skill_packages as packages, skills
from microclaw.tools import _recorded_outcome

FIXTURES = Path(__file__).parent / 'fixtures' / 'skill_packages'


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
    loaded = packages.load_external_skill(name, [record(kind)])
    assert loaded['text'].endswith((FIXTURES / kind / 'SKILL.md').read_text(encoding='utf-8'))
    assert loaded['artifact_digest'] == intake(kind)['artifact_digest']
    assert loaded['operations'] == source.get('operations', [])
    line, = packages.external_catalog_lines([record(kind)])
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


@pytest.mark.parametrize('collection,key,field', [
    ('assets', 'path', 'assets[2].path'), ('skills', 'name', 'skills[1].name'),
    ('operations', 'name', 'operations[1].name'),
])
def test_duplicate_declarations(collection, key, field):
    value = manifest()
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
    ('signature',{}), ('signatures',[]), ('signing_key','placeholder')])
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
        refuses('name', packages.load_external_skill, item.name, [record()])
    packages.external_catalog_lines([record()])
    assert skills.SKILL_CATALOG is original


@pytest.mark.parametrize('changes', [{'enabled':False}, {'verified':False}, {'enabled':1}, {'verified':'yes'}])
def test_only_explicit_enabled_verified_records_resolve(changes):
    value = record(**changes)
    # Ineligible metadata isn't even read, and no directory scan takes place.
    value['manifest'] = None
    assert packages.external_catalog_lines([value]) == ()
    refuses('name', packages.load_external_skill, 'fixture-lab/executable-fixture/workflow', [value])


def test_no_implicit_discovery_or_duplicate_resolution():
    name = 'fixture-lab/executable-fixture/workflow'
    assert packages.external_catalog_lines([]) == ()
    refuses('name', packages.load_external_skill, name, [])
    refuses('name', packages.load_external_skill, name, [record(), record()])
    refuses('name', packages.external_catalog_lines, [record(), record()])


@pytest.mark.parametrize('field,replacement', [('publisher','other'), ('package_id','other'),
    ('version','2.0'), ('artifact','https://example.org/other.zip')])
def test_manifest_and_external_release_identity_must_match(field, replacement):
    value = record()
    value['intake'][field] = replacement
    refuses('intake.' + field, packages.load_external_skill, 'fixture-lab/executable-fixture/workflow', [value])


@pytest.mark.parametrize('asset', ['SKILL.md', 'notes.txt'])
def test_loaded_asset_bytes_are_bound_to_manifest(tmp_path, asset):
    root = tmp_path/'release'
    shutil.copytree(FIXTURES/'executable', root)
    (root/asset).write_text('modified', encoding='utf-8')
    index = 0 if asset == 'SKILL.md' else 1
    refuses(f'assets[{index}].sha256', packages.load_external_skill,
            'fixture-lab/executable-fixture/workflow', [record(release_dir=root)])


def test_missing_unreadable_or_symlink_asset_refuses(tmp_path):
    root = tmp_path/'release'
    shutil.copytree(FIXTURES/'markdown', root)
    (root/'SKILL.md').unlink()
    data = record('markdown', release_dir=root)
    name = 'fixture-lab/markdown-fixture/workflow'
    refuses('assets[0].path', packages.load_external_skill, name, [data])
    (root/'SKILL.md').mkdir()
    refuses('assets[0].path', packages.load_external_skill, name, [data])
    (root/'SKILL.md').rmdir()
    try:
        (root/'SKILL.md').symlink_to(FIXTURES/'markdown'/'SKILL.md')
    except OSError as exc:
        pytest.skip(f'Host cannot create test symlinks: {exc}')
    refuses('assets[0].path', packages.load_external_skill, name, [data])


def test_skill_must_decode_as_utf8(tmp_path):
    root = tmp_path/'release'
    shutil.copytree(FIXTURES/'markdown', root)
    (root/'SKILL.md').write_bytes(b'\xff')
    data = record('markdown', release_dir=root)
    data['manifest']['assets'][0]['sha256'] = hashlib.sha256(b'\xff').hexdigest()
    refuses('skills.path', packages.load_external_skill, 'fixture-lab/markdown-fixture/workflow', [data])


def test_loaded_operations_and_text_are_detached_from_caller_mutation():
    data = record()
    loaded = packages.load_external_skill('fixture-lab/executable-fixture/workflow', [data])
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
