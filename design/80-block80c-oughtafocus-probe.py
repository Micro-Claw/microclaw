"""Measure autofocus settings through the product accessor; this is not a gate.

Moves no stage, drives no acquisition and spends no dose. Each bridge question
is independent: a failed reader is not evidence that the plugin did not answer.
Whether _drain_java_iterable should learn arrays, and whether the emitted script
prints settings or declares an external precondition, remain 80c's decisions
from this measurement. Whatever the answer, the 2026-08-17 operator decision
requires the envelope to disclose that behaviour is not determined by source.

Run with the Micro-Manager bridge on; send stdout and the --out JSON back.
"""
from __future__ import annotations

import argparse
from importlib.metadata import version
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from microclaw import controller

FINDINGS = {}
DEFAULT_NAMES = ('SearchRange_um,Tolerance_um,CropFactor,Exposure,'
                 'FFTLowerCutoff(%),FFTUpperCutoff(%),ShowImages,Maximize,Channel')
ROUTES = {'names__drain': '_drain_java_iterable',
          'names__reflect_array': 'reflect.Array',
          'names__arrays_as_list': 'Arrays.asList'}


def observe(label, obj):
    row = {'answered': True, 'type': type(obj).__name__}
    if obj is None or isinstance(obj, (str, int, float, bool)):
        row['value'] = obj
    if isinstance(obj, list):
        row['value'] = [x if isinstance(x, (str, int, float, bool)) or x is None
                        else {'type': type(x).__name__} for x in obj]
    FINDINGS[label] = row
    return obj


def ask(label, fn, quiet=False):
    try:
        value = observe(label, fn())
        if not quiet:
            if isinstance(value, list) and any(not isinstance(x, str) for x in value):
                display = f'{len(value)} items (types and fields in JSON)'
            else:
                display = repr(value) if isinstance(value, (str, int, float, bool, list)) else f'<{type(value).__name__}>'
            print(f'  {label}: {display}')
        return value
    except Exception as exc:
        FINDINGS[label] = {'answered': False,
                           'error': f'{type(exc).__name__}: {exc}'}
        print(f"  {label}: NOT AVAILABLE - {FINDINGS[label]['error']}")
        return None


def as_text(label, obj):
    observe(label, obj)
    conversion = 'str' if isinstance(obj, str) else 'to_string'
    FINDINGS[label]['conversion'] = conversion
    value = obj if isinstance(obj, str) else observe(label + '__to_string', obj.to_string())
    if not isinstance(value, str):
        raise TypeError('to_string() did not return str')
    return value


def drain(obj, label, convert=True):
    # First measure the unmodified product function on the actual bridge object.
    # Its str() discards element types, so separately inspect the elements for
    # diagnostic types and defensive text conversion; keep both findings.
    product = observe(label + '__product_result', controller._drain_java_iterable(obj))
    elements = []

    diagnostic_failed = False

    def element(value):
        nonlocal diagnostic_failed
        key = f'{label}__element_{len(elements)}'
        converted = ask(key + '__text', lambda: as_text(key, value), quiet=True) if convert else observe(key, value)
        if convert and converted is None:
            diagnostic_failed = True
        elements.append(converted)

    def inspect_elements():
        if isinstance(obj, (list, tuple, set)):
            for value in obj:
                element(value)
        else:
            iterator = observe(label + '__iterator', obj.iterator())
            has_next = iterator.has_next if hasattr(iterator, 'has_next') else iterator.hasNext
            while has_next():
                element(iterator.next())
        return True

    inspected = ask(label + '__inspection', inspect_elements, quiet=True)
    if convert and (not inspected or diagnostic_failed):
        FINDINGS[label + '__diagnostic_fallback'] = True
        print(f'  {label}: diagnostic inspection failed; fell back to the product drain result.')
        return product
    return elements


def array_read(obj, label, port, route, convert=True):
    path = 'java.lang.reflect.Array' if route == 'reflect.Array' else 'java.util.Arrays'
    helper = observe(label + '__helper', controller._new_static_java_class(port, path))
    if route == 'Arrays.asList':
        collection = observe(label + '__list', helper.as_list(obj))
        return drain(collection, label, convert)
    count = observe(label + '__length', helper.get_length(obj))
    items = []
    for i in range(count):
        key = f'{label}__element_{i}'
        value = helper.get(obj, i)
        if convert:
            text = ask(key + '__text', lambda: as_text(key, value), quiet=True)
            if text is None:
                text = str(value)
                FINDINGS[key + '__diagnostic_fallback'] = True
                print(f'  {key}: conversion failed; fell back to str(), which may be a shadow repr.')
            items.append(text)
        else:
            items.append(observe(key, value))
    return items


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=4827)
    parser.add_argument('--plugin', default='OughtaFocus')
    parser.add_argument('--out', type=Path, default=Path('oughtafocus-probe.json'))
    parser.add_argument('--probe-names', default=DEFAULT_NAMES,
                        help='comma-separated guessed names; used only without enumerated names')
    args = parser.parse_args()
    FINDINGS.clear()
    FINDINGS.update(port=args.port, disclosure_required=True)
    for package in ('pycromanager', 'pyjavaz'):
        ask('version__' + package, lambda package=package: version(package))
    print(f'Tree: {ROOT}\nAsking about {args.plugin!r} on port {args.port}')
    ctrl = ask('controller', lambda: controller.MicroscopeController(port=args.port), quiet=True)
    print('\n1. What autofocus methods does this Micro-Manager have?')
    manager = ask('autofocus_manager', lambda: ctrl.plugins._studio.get_autofocus_manager(), quiet=True)
    raw_methods = ask('all_autofocus_methods__object', lambda: manager.get_all_autofocus_methods(), quiet=True)
    methods = ask('all_autofocus_methods', lambda: drain(raw_methods, 'methods'))
    if methods is None:
        print('The autofocus manager itself is unreachable or its methods could not be read.')
        return finish(args)
    if args.plugin not in methods:
        print(f'{args.plugin!r} is NOT installed. Installed: {methods}')
        return finish(args)
    print('\n2. Select through the product\'s own accessor.')
    af = ask('get_autofocus_method', lambda: ctrl.plugins.get_autofocus_method(args.plugin))
    if af is None:
        return finish(args)
    print('\n3. Does the returned method expose its own settings?')
    obj = ask('get_property_names', lambda: af.get_property_names())
    names = []
    worked = []
    read_routes = {}
    if FINDINGS['get_property_names']['answered']:
        for label, route in ROUTES.items():
            result = ask(label, lambda label=label, route=route: (
                drain(obj, label) if route == '_drain_java_iterable' else
                array_read(obj, label, args.port, route)))
            if result is not None:
                read_routes[route] = result
                if result:
                    worked.append(route)
                    names.extend(name for name in result if name not in names)
    disagreement = len({tuple(result) for result in read_routes.values()}) > 1
    FINDINGS['names_disagreement'] = read_routes if disagreement else False
    if disagreement:
        print('  Name routes disagree; reading the union of their answers:')
        for route, result in read_routes.items():
            print(f'    {route}: {result!r}')
    FINDINGS['names_routes'] = worked
    FINDINGS['names_route'] = worked[0] if worked else None
    values = {}
    print('\n4. Enumerated values')
    for name in names:
        label = 'value__' + name
        ask(label, lambda name=name, label=label: as_text(label + '__raw', af.get_property_value(name)))
        values[name] = FINDINGS[label]
    FINDINGS['property_values'] = values
    if not FINDINGS['get_property_names']['answered']:
        verdict = 'names_not_exposed'
        detail = 'the returned method does not expose its property names over this bridge.'
    elif read_routes and not names:
        verdict = 'names_returned_but_empty'
        detail = 'The names call answered and readers returned no names; settings readability is not established.'
    elif not worked:
        verdict = 'names_returned_but_unreadable'
        detail = (f"Returned {type(obj).__name__}; every route failed (see failures above). "
                  'This is not the same as the plugin not exposing its settings.')
    else:
        failed = [name for name, row in values.items() if not row['answered']]
        verdict = 'settings_partially_readable' if failed else 'settings_readable'
        detail = f'Names read via {worked}; {len(names) - len(failed)}/{len(names)} values read. '
        detail += (f'The doc must say which values were unreadable: {failed}.' if failed else
                   '80c can print these settings in the emitted envelope.')
    FINDINGS['verdict'] = verdict
    print(f'\nVERDICT: {verdict} — {detail}')

    print('\n5. Secondary PropertyItem cross-check (does not alter the verdict)')
    items_obj = ask('get_properties', lambda: af.get_properties())
    for route in read_routes:
        if route == '_drain_java_iterable':
            continue
        label = 'properties__' + route
        items = ask(label, lambda: array_read(items_obj, label, args.port, route, False))
        if items is None:
            continue
        for i, item in enumerate(items):
            # Public fields retain their exact Java names, never snake_case.
            for field in ('key', 'name', 'value'):
                key = f'{label}__item_{i}__{field}'
                ask(key, lambda item=item, field=field, key=key:
                    as_text(key + '__raw', getattr(item, field)))
    if not names:
        print('\n6. GUESSED NAMES, NOT ENUMERATED ONES; failures say nothing about the plugin.')
        FINDINGS['guessed_names'] = {}
        for name in filter(None, (n.strip() for n in args.probe_names.split(','))):
            label = 'guessed__' + name
            ask(label, lambda name=name, label=label: as_text(label + '__raw', af.get_property_value(name)))
            FINDINGS['guessed_names'][name] = FINDINGS[label]
    return finish(args)


def finish(args):
    print('\nDisclosure required: exported script behaviour is not determined by its source; '
          'the script must print this in its envelope.')
    args.out.write_text(json.dumps(FINDINGS, indent=2, default=str), encoding='utf-8')
    print(f'Findings written to {args.out}. Send this whole output back.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
