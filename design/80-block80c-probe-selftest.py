"""Off-rig, independent probe checks; no bridge or network is opened.

Fakes follow installed pyjavaz/bridge.py, not the probe's consumers. Static
helper success models Java reflection/Arrays contracts; actual overload matching
on a given bridge version remains a rig measurement, especially for asList.
"""
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from microclaw import controller

PROBE = ROOT / 'design/80-block80c-oughtafocus-probe.py'
# bridge.py:589-591, 595-644: class name translation, only reported fields and
# methods. 806-820: object-array shadow; 825-843: only primitive array conversion.
# No sequence hooks, iterator, length, or custom __str__; storage is external.
StringArray = type('[Ljava_lang_String;', (), {})
PropertyArray = type('[Lorg_micromanager_PropertyItem;', (), {})


class Collection:
    # bridge.py:811-820: iterate=False leaves a shadow with Java iterator().
    # 604-634 snake-cases methods; no Python iterable synthesized at 636-645.
    def __init__(self, values):
        self.values = values

    def __iter__(self):
        raise TypeError('bridge Collection is not Python iterable (design/59a)')

    def iterator(self):
        values = iter(self.values)
        remaining = len(self.values)

        class Iterator:
            def has_next(self):
                return remaining > 0

            def next(self):
                nonlocal remaining
                remaining -= 1
                return next(values)
        return Iterator()


class TextShadow:
    # bridge.py:604-644 exposes toString as to_string, no __str__ synthesis;
    # 797-798 returns runtime String as a real str.
    def to_string(self):
        return 'shadow value'


class Opaque:
    # bridge.py:595-644 only exposes server-reported methods; no text method.
    pass


class NotExercised(Exception):
    pass


def run(shape='array', unavailable=(), bad_value=False, names_raise=False,
        installed=True, unreachable=False, path=PROBE, guessed=True,
        shadow_names=False, properties_raise=False, empty=False, opaque_methods=False,
        opaque_names=False, disagreement=False, unexpected_json=False):
    names = [] if empty else [TextShadow() if shadow_names else 'SearchRange_um', 'Exposure']
    if opaque_names:
        names.append(Opaque())
    array = StringArray()
    items = PropertyArray()
    # bridge.py:595-602 preserves exact field names. Missing fields really raise.
    storage = {array: names, items: [SimpleNamespace(key='SearchRange_um', value=TextShadow()),
                                   SimpleNamespace(name='Exposure', value='10')]}
    calls, helpers = [], []

    class AF:
        def get_property_names(self):
            if names_raise:
                raise RuntimeError('property names unavailable')
            return Collection(names) if shape == 'collection' else array

        def get_property_value(self, name):
            calls.append(name)
            if bad_value and name == 'Exposure':
                raise RuntimeError('Exposure unreadable')
            return TextShadow() if name == 'SearchRange_um' else '10'

        def get_properties(self):
            if properties_raise:
                raise RuntimeError('secondary unavailable')
            return items

    class Manager:
        def get_all_autofocus_methods(self):
            return Collection(['OughtaFocus', Opaque() if opaque_methods else 'Other'] if installed else ['Other'])

        def set_autofocus_method_by_name(self, name):
            assert name == 'OughtaFocus'

        def get_autofocus_method(self):
            return AF()

    def manager():
        if unreachable:
            raise RuntimeError('manager unreachable')
        return Manager()

    # Use the real PluginAccess method as well as the real drain.
    plugins = object.__new__(controller.PluginAccess)
    plugins._studio = SimpleNamespace(get_autofocus_manager=manager)

    def static(port, classpath):
        assert port == 4912
        helpers.append(classpath)
        if classpath in unavailable:
            raise RuntimeError(classpath + ' unavailable')
        if classpath == 'java.lang.reflect.Array':
            return SimpleNamespace(get_length=lambda obj: len(storage[obj]),
                                   get=lambda obj, index: storage[obj][index])
        if classpath == 'java.util.Arrays':
            return SimpleNamespace(as_list=lambda obj: Collection(['Channel'] if disagreement and obj is array else storage[obj]))
        raise AssertionError('unexpected static class ' + classpath)

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / 'findings.json'
        spec = importlib.util.spec_from_file_location('probe_under_test', path)
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)
        if unexpected_json:
            original_finish = probe.finish

            def finish(args):
                probe.FINDINGS['unexpected'] = Opaque()
                return original_finish(args)
            probe.finish = finish
        argv = ['probe', '--port', '4912', '--out', str(out)]
        if guessed:
            argv += ['--probe-names', 'GuessedOne,GuessedTwo']
        stdout = io.StringIO()
        with patch.object(controller, 'MicroscopeController', return_value=SimpleNamespace(plugins=plugins)), \
             patch.object(controller, '_new_static_java_class', side_effect=static), \
             patch.object(sys, 'argv', argv), redirect_stdout(stdout):
            assert probe.main() == 0
        return json.loads(out.read_text(encoding='utf-8')), stdout.getvalue(), calls, helpers


def check_case(expected, route=None, **kwargs):
    data, output, calls, helpers = run(**kwargs)
    assert data.get('verdict') == expected, (data.get('verdict'), expected)
    assert expected in output
    assert data['names_route'] == route
    fallback = expected in ('names_returned_but_unreadable', 'names_not_exposed')
    assert ('GuessedOne' in calls) == fallback
    assert ('GUESSED NAMES, NOT ENUMERATED ONES' in output) == fallback
    assert data['port'] == 4912 and data['disclosure_required']
    assert all('version__' + p in data for p in ('pycromanager', 'pyjavaz'))
    if not kwargs.get('names_raise'):
        assert helpers[:2] == ['java.lang.reflect.Array', 'java.util.Arrays'], helpers
        assert all(label in data for label in ('names__drain', 'names__reflect_array', 'names__arrays_as_list'))
    if expected == 'names_returned_but_unreadable':
        assert '[Ljava_lang_String;' in output
        assert 'not the same as the plugin not exposing' in output
        for label in ('names__drain', 'names__reflect_array', 'names__arrays_as_list'):
            assert not data[label]['answered'] and data[label]['error'] in output
    if route == 'reflect.Array' and not kwargs.get('shadow_names'):
        assert data['names__reflect_array__element_0']['type'] == 'str'
        assert data['value__SearchRange_um__raw']['conversion'] == 'to_string'
        if not kwargs.get('properties_raise'):
            assert data['properties__reflect.Array__item_0__key']['value'] == 'SearchRange_um'
            assert not data['properties__reflect.Array__item_0__name']['answered']
            assert data['properties__reflect.Array__item_1__value']['value'] == '10'
    if kwargs.get('unavailable'):
        for classpath in kwargs['unavailable']:
            label = 'names__reflect_array' if classpath.endswith('.Array') else 'names__arrays_as_list'
            assert not data[label]['answered'] and classpath in data[label]['error']
    if kwargs.get('bad_value'):
        assert not data['property_values']['Exposure']['answered']
        assert 'Exposure unreadable' in output


def unavailable_plugin():
    data, output, calls, _ = run(installed=False)
    assert 'NOT installed' in output and "Installed: ['Other']" in output
    assert 'verdict' not in data and not calls


def unreachable_manager():
    data, output, calls, _ = run(unreachable=True)
    assert 'manager unreachable' in output
    assert 'verdict' not in data and not calls


def shape_contract():
    obj = StringArray()
    for attr in ('iterator', '__iter__', '__len__', '__getitem__', 'length'):
        assert not hasattr(obj, attr), attr
    try:
        list(Collection(['x']))
    except TypeError:
        pass
    else:
        raise AssertionError('collection accidentally Python iterable')


def guessed_fallback_only():
    # Independent check of the cross-case fallback rule, including early exits.
    scenarios = [({}, False), ({'unavailable': ('java.lang.reflect.Array',)}, False),
                 ({'unavailable': ('java.lang.reflect.Array', 'java.util.Arrays')}, True),
                 ({'bad_value': True}, False), ({'names_raise': True}, True),
                 ({'shape': 'collection'}, False), ({'installed': False}, False),
                 ({'unreachable': True}, False)]
    for kwargs, expected in scenarios:
        data, output, calls, _ = run(**kwargs)
        assert ('guessed_names' in data) == expected, kwargs
        assert ('GuessedOne' in calls and 'GuessedTwo' in calls) == expected, kwargs
        assert ('GUESSED NAMES, NOT ENUMERATED ONES' in output) == expected, kwargs
    data, _, calls, _ = run(names_raise=True, guessed=False)
    defaults = ['SearchRange_um', 'Tolerance_um', 'CropFactor', 'Exposure',
                'FFTLowerCutoff(%)', 'FFTUpperCutoff(%)', 'ShowImages', 'Maximize', 'Channel']
    assert calls == defaults and list(data['guessed_names']) == defaults


def empty_names():
    data, output, calls, _ = run(empty=True)
    assert data['verdict'] == 'names_returned_but_empty', f"empty enumeration classified as {data['verdict']}"
    assert data['names_routes'] == [] and data['names_route'] is None
    assert 'GuessedOne' in calls and 'GUESSED NAMES' in output
    assert '80c can print' not in output
    assert data['names__reflect_array']['value'] == []


def opaque_diagnostic(where):
    data, output, _, _ = run(**{f'opaque_{where}': True})
    assert 'verdict' in data, 'successful product drain lost; no verdict'
    if where == 'names':
        assert data['names__arrays_as_list']['answered'], 'successful name drain lost to diagnostic conversion'
    assert 'fell back' in output, 'diagnostic fallback not disclosed'
    assert any(not row.get('answered', True) for key, row in data.items()
               if '__element_' in key and isinstance(row, dict)), 'failed element not recorded'


def readable_transcript():
    data, output, _, _ = run()
    assert "{'answered':" not in output, 'stdout contains diagnostic dict reprs'
    assert not any('__has_next' in key for key in data), 'has_next findings retained'
    assert '1.' in output and 'VERDICT:' in output and 'SearchRange_um' in output


def json_resilience():
    data, _, _, _ = run(unexpected_json=True)
    assert isinstance(data['unexpected'], str)
    assert data['verdict'] == 'settings_readable'


def route_disagreement():
    data, output, calls, _ = run(disagreement=True)
    assert data.get('names_disagreement'), 'different route answers silently unioned'
    assert data['names_disagreement'] == {
        'reflect.Array': ['SearchRange_um', 'Exposure'], 'Arrays.asList': ['Channel']}
    assert 'disagree' in output.lower(), 'route disagreement missing from transcript'
    assert {'SearchRange_um', 'Exposure', 'Channel'} <= set(calls)


def control():
    try:
        old = subprocess.run(['git', 'show', 'd08ab45:design/80-block80c-oughtafocus-probe.py'],
                             cwd=ROOT, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise NotExercised(f'cannot extract pre-fix probe: {exc}') from exc
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / 'old_probe.py'
        path.write_text(old.stdout, encoding='utf-8')
        _, output, _, _ = run(path=path, guessed=False)
    wrong = 'VERDICT: the returned method does not expose its property names over this bridge.'
    failure = "'[Ljava_lang_String;' object has no attribute 'iterator'"
    assert wrong in output and failure in output, output
    return f'observed case 1 pre-fix failure: AttributeError: {failure}; {wrong}'


def main():
    reflect = 'java.lang.reflect.Array'
    arrays = 'java.util.Arrays'
    cases = [
        ('array reflection', lambda: check_case('settings_readable', 'reflect.Array')),
        ('array asList fallback', lambda: check_case('settings_readable', 'Arrays.asList', unavailable=(reflect,))),
        ('array unreadable + guessed names', lambda: check_case('names_returned_but_unreadable', unavailable=(reflect, arrays))),
        ('partial values', lambda: check_case('settings_partially_readable', 'reflect.Array', bad_value=True)),
        ('names call raises + guessed names', lambda: check_case('names_not_exposed', names_raise=True)),
        ('collection drain', lambda: check_case('settings_readable', '_drain_java_iterable', shape='collection')),
        ('plugin absent', unavailable_plugin),
        ('manager unreachable', unreachable_manager),
        ('guessed fallback only in cases 3 and 5 + defaults', guessed_fallback_only),
        ('dependency shape contracts', shape_contract),
        ('shadow name conversion', lambda: check_case('settings_readable', 'reflect.Array', shadow_names=True)),
        ('secondary failure isolated', lambda: check_case('settings_readable', 'reflect.Array', properties_raise=True)),
        ('empty enumeration', empty_names),
        ('opaque methods diagnostic', lambda: opaque_diagnostic('methods')),
        ('opaque names diagnostic', lambda: opaque_diagnostic('names')),
        ('readable transcript', readable_transcript),
        ('unexpected JSON object', json_resilience),
        ('route disagreement', route_disagreement),
        ('pre-fix control fires', control),
    ]
    failed = 0
    for name, fn in cases:
        try:
            detail = fn()
        except NotExercised as exc:
            print(f'NOT EXERCISED: {name}: {exc}')
            failed += 1
        except (Exception, SystemExit) as exc:
            print(f'FAIL: {name}: {type(exc).__name__}: {exc}')
            failed += 1
        else:
            print(f'PASS: {name}' + (f': {detail}' if detail else ''))
    print(f'{len(cases) - failed}/{len(cases)} passed; {failed} failed or not exercised')
    return int(bool(failed))


if __name__ == '__main__':
    raise SystemExit(main())
