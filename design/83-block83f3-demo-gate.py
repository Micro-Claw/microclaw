"""83f-3 demo gate. Real runs use the installed slot; selftest never uses network.

83d owns launch/ask/store/cleanup plumbing; 83e-3 owns history and reporting.
Fixtures use public TEST-ONLY seeds. No product or test files are modified.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
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
import time
import traceback
import urllib.error
import zipfile

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('gate83e3', HERE / '83-block83e3-demo-gate.py')
gate83e3 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gate83e3)
gate83d = gate83e3.gate83d
read_json, write_json, now = gate83d.read_json, gate83d.write_json, gate83d.now
NotExercised = gate83d.NotExercised
BASE = 'https://raw.githubusercontent.com/Micro-Claw/microclaw/block-83f-3/design/83f3-gate/'
FIXTURES = HERE / '83f3-gate'
POINTER = '83f3-gate-evidence.txt'
PHASES = ('fixtures', 'prepare', 'session', 'verify', 'cleanup', 'selftest')
IDENTITY = ('publisher', 'package_id', 'version', 'artifact_digest')
JUDGED = gate83e3.OPERATOR_JUDGED
CHAT = {
    1: 'Search the community skill catalog for fixture skills and list what you find.',
    8: 'Load the fixture-lab executable-fixture workflow skill and tell me its first line.',
    12: 'Search the community skill catalog for fixture skills again.',
}
PROMPTS = {
    2: 'Open Community skill packages. Type the line starting Catalog / Offline / No community exactly as shown.',
    3: 'Click Install on fixture-lab/executable-fixture. Does the box show the publisher, licence, and "MicroClaw does not test or support this package"? Answer yes/no plus anything missing. Click Cancel; click Install again, then Install. Wait until the row shows installed before answering.',
    4: 'Install fixture-lab/markdown-fixture: Install, then Install. Type DONE when installed.',
    5: 'Install fixture-lab/tampered-fixture (Install, then Install). Does its card now show an install failure? Type yes/no and the text shown.',
    6: 'Install fixture-two/markdown-fixture (Install, then Install). Does its card now show an install failure? Type yes/no and the text shown. Is the error on fixture-two’s card and not on fixture-lab’s? (yes/no)',
    7: 'Type the reason on fixture-lab/incompatible-fixture and whether its row has an Install button (yes/no).',
    9: 'Click Check now. Does executable-fixture’s card say "Blocked by MicroClaw" and markdown-fixture’s say "Withdrawn by its publisher"? Type yes/no for each.',
    10: 'Click Update on fixture-lab/executable-fixture, then Install. Type DONE when finished.',
    11: 'Click Remove on fixture-lab/markdown-fixture. Keep the box open and read it: does it say only the installed files go and the results stay? Type yes/no here.',
}


def roots(base='catalog-1'):
    return dict(read_json(gate83d.FIXTURES / 'trust/roots-TEST-ONLY.json'), catalog_url=BASE + base + '/')


class DiskResponse(io.BytesIO):
    # urllib's response contract used by updates._open_manual: status, headers,
    # bounded read and close. Missing URLs raise HTTPError, never a status-200 stub.
    status = 200
    def __init__(self, data):
        super().__init__(data)
        self.headers = {'Content-Length': str(len(data))}


def disk_opener(directory):
    def open_file(request, timeout):
        url = request.full_url
        assert url.startswith(BASE), 'selftest refused unexpected URL: ' + url
        relative = url[len(BASE):]
        path = Path(directory) / relative
        assert path.resolve().is_relative_to(Path(directory).resolve())
        if not path.is_file():
            raise urllib.error.HTTPError(url, 404, 'Not Found', {}, io.BytesIO(b'404: Not Found'))
        return DiskResponse(path.read_bytes())
    return open_file


@contextmanager
def isolated_store(directory):
    from microclaw import skill_store
    from unittest.mock import patch
    with patch.object(skill_store, 'user_data_dir', lambda: Path(directory)):
        yield skill_store


def entries(directory=FIXTURES):
    first = read_json(Path(directory) / 'catalog-1/catalog.json')['releases']
    second = read_json(Path(directory) / 'catalog-2/catalog.json')['releases']
    lookup = {(e['publisher'], e['package_id'], e['version']): e for e in first + second}
    return dict(E1=lookup['fixture-lab', 'executable-fixture', '1.0.0'],
                E2=lookup['fixture-lab', 'executable-fixture', '1.1.0'],
                M1=lookup['fixture-lab', 'markdown-fixture', '1.0.0'],
                I1=lookup['fixture-lab', 'incompatible-fixture', '1.0.0'],
                T1=lookup['fixture-lab', 'tampered-fixture', '1.0.0'],
                B1=lookup['fixture-two', 'markdown-fixture', '1.0.0'])


def validate_fixtures(directory):
    """Exercise product verification before publishing any generated bytes."""
    with tempfile.TemporaryDirectory() as temporary, isolated_store(temporary) as store:
        open_file = disk_opener(directory)
        write_json(store.store_dir() / 'trust/roots.json', roots())
        first = store.refresh_catalog(opener=open_file, now=now())
        assert first['last_success'] and not first['error'], first
        expected = entries(directory)
        assert len(store.catalog_entries(now=now())['releases']) == len(read_json(Path(directory) / 'catalog-1/catalog.json')['releases'])
        write_json(store.store_dir() / 'trust/roots.json', roots('catalog-2'))
        second = store.refresh_catalog(opener=open_file, now=now())
        assert second['last_success'] and not second['error'], second
        catalog = store.catalog_entries(now=now())
        offered = store.select_catalog_releases(catalog)
        assert offered['fixture-lab', 'executable-fixture'][1]['artifact_digest'] == expected['E2']['artifact_digest']
        assert any(e.get('blocked') and e['entry']['artifact_digest'] == expected['E1']['artifact_digest'] for e in catalog['exclusions'])
        assert next(e for e in catalog['releases'] if e['artifact_digest'] == expected['M1']['artifact_digest'])['withdrawn']
        assert not next(e for e in catalog['releases'] if e['artifact_digest'] == expected['I1']['artifact_digest'])['compatible']
        package = store.store_dir() / 'tamper-probe'
        package.mkdir()
        try:
            with store.download_release(expected['T1'], package=package, opener=open_file):
                raise AssertionError('tampered artifact accepted')
        except store.PackageRefusal as exc:
            assert exc.field == 'artifact_digest'
        assert not list(package.glob('.download-*'))
        return read_json(store.store_dir() / 'catalog/catalog.json')


def fixtures(destination=FIXTURES):
    destination = Path(destination)
    builder = gate83d.load_builder()
    with tempfile.TemporaryDirectory() as temporary:
        stage = Path(temporary) / 'publish'
        (stage / 'artifacts').mkdir(parents=True)
        releases = {}
        for label, source, publisher, package, version in [
            ('E1', 'executable', 'fixture-lab', 'executable-fixture', '1.0.0'),
            ('M1', 'markdown', 'fixture-lab', 'markdown-fixture', '1.0.0'),
            ('I1', 'markdown', 'fixture-lab', 'incompatible-fixture', '1.0.0'),
            ('T1', 'markdown', 'fixture-lab', 'tampered-fixture', '1.0.0'),
            ('B1', 'markdown', 'fixture-two', 'markdown-fixture', '1.0.0'),
            ('E2', 'executable', 'fixture-lab', 'executable-fixture', '1.1.0'),
        ]:
            work = Path(temporary) / label
            shutil.copytree(gate83d.FIXTURES / source, work)
            name = f'{publisher}-{package}-{version}.zip'
            manifest = read_json(work / 'manifest.json')
            manifest.update(publisher=publisher, package_id=package, version=version, artifact=BASE + 'artifacts/' + name)
            if label == 'I1':
                manifest['microclaw'] = '>=99'
            if source == 'executable':
                manifest['locks'] = {p: [dict(requirement='iniconfig==2.0.0', hashes=[
                    'b6a85871a79d2e3b22d2d1b94ac2824226a63c6b741c88f7ae975f18b6778374'])] for p in manifest['platforms']}
            write_json(work / 'manifest.json', manifest)
            artifact = stage / 'artifacts' / name
            intake = builder.build_release(work, artifact)
            releases[label] = builder.sign(intake, 'publisher-b' if label == 'B1' else 'publisher-a')
            if label == 'T1':
                data = bytearray(artifact.read_bytes())
                data[-1] ^= 1
                artifact.write_bytes(data)
        policy = read_json(gate83d.FIXTURES / 'trust/policy-TEST-ONLY.json')
        keys = policy['publishers']['fixture-lab']['keys']
        policy.update(expires_at='2027-04-01T00:00:00Z', publishers={
            'fixture-lab': dict(state='active', keys=[keys[0]]),
            'fixture-two': dict(state='active', keys=[keys[1]])})
        for revision in (1, 2):
            document = deepcopy(policy)
            document.update(revision=revision, revoked_releases=[] if revision == 1 else [
                {k: releases['E1'][k] for k in ('package_id', 'version', 'artifact_digest')} | dict(reason='Gate: blocked by MicroClaw')])
            withdrawal = builder.sign(dict(type='microclaw.skill-withdrawal.v1',
                **{k: releases['M1'][k] for k in IDENTITY}, reason='Gate: withdrawn by its publisher'))
            write_json(stage / f'catalog-{revision}/policy.json', builder.sign(document, 'root'))
            write_json(stage / f'catalog-{revision}/catalog.json', dict(type='microclaw.catalog.v1',
                releases=[releases[k] for k in ('E1', 'M1', 'I1', 'T1', 'B1') + (('E2',) if revision == 2 else ())],
                withdrawals=[] if revision == 1 else [withdrawal]))
        validate_fixtures(stage)
        destination.mkdir(parents=True, exist_ok=True)
        for path in stage.rglob('*'):
            if path.is_file():
                target = destination / path.relative_to(stage)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(path.read_bytes())
    print('FIXTURES: both states verified by skill_store; blocked/withdrawn/incompatible/tampered controls passed; TEST-ONLY keys', flush=True)


class Gate(gate83e3.Gate):
    def discard_pending_input(self):
        """Drain Windows console characters before displaying each prompt."""
        if sys.platform != 'win32':
            return 0
        import msvcrt
        discarded = 0
        while msvcrt.kbhit():
            msvcrt.getwch()
            discarded += 1
        return discarded

    def ask(self, prompt, key, kind='done'):
        """Only validated answers enter session evidence; inherited launch/close
        prompts default to DONE. Rejected paste lines remain visible in the log."""
        if kind not in ('done', 'text'):
            raise ValueError('unknown answer kind: ' + kind)
        while True:
            discarded = self.discard_pending_input()
            if discarded:
                self.say(f'DISCARDED: {discarded} pending console input characters')
            self.say('OPERATOR: ' + prompt)
            answer = (self.fake.answer(key) if self.fake else input('> ')).strip()
            if answer.upper() == 'STOP':
                self.say(f'ANSWER[{key}]: {answer}')
                raise NotExercised('operator stopped the gate')
            if (kind == 'done' and answer.upper() != 'DONE') or not answer:
                self.say(f'REJECTED[{key}]: {answer!r}')
                self.say("Type DONE here when finished (or STOP). Your message goes in MicroClaw's chat box, not here."
                         if kind == 'done' else 'Type a non-empty answer here (or STOP).')
                continue
            self.say(f'ANSWER[{key}]: {answer}')
            return answer

    def prepare(self):
        if self.store_root.exists():
            raise NotExercised(f'{self.store_root} already exists; gate owns and removes its store. Do not delete existing user packages.')
        write_json(self.store_root / 'trust/roots.json', roots())
        self.save('prepare', dict(prepared_at=now().isoformat()))
        self.say('Prepared TEST-ONLY roots only. Startup is the first catalog fetch.')

    def snapshot(self, step):
        target = self.out / 'snapshots' / str(step)
        if target.exists():
            raise NotExercised(f'snapshot {step} already exists; refusing to overwrite evidence')
        shutil.copytree(self.store_root, target, ignore=shutil.ignore_patterns(
            'env', 'python', 'artifact.zip', 'release', 'requirements.txt'))
        # Preserve serve history before later turns/removal. This is a copy of the
        # real writer's JSONL, not a gate reconstruction of the conversation.
        history = target / 'history'
        history.mkdir()
        for path in self.root.glob('*_microclaw_history.jsonl'):
            shutil.copy2(path, history / path.name)
        write_json(target / 'snapshot.json', dict(step=step, captured_at=now().isoformat()))

    def session(self):
        self.need('prepare')
        session = dict(started_at=now().isoformat(), answers={}, completed=[])
        self.save('session', session)
        session['launch'] = self.launch_desktop('Session phase.')
        self.save('session', session)
        for step in range(1, 13):
            if step == 9:
                write_json(self.store_root / 'trust/roots.json', roots('catalog-2'))
            self.say(f'STEP {step}')
            if step in CHAT:
                self.say('\n' + CHAT[step] + '\n')
            prompt = ("Paste the message above into MicroClaw's chat box in Firefox (not here). "
                      "When the agent's reply has finished, type DONE here.") if step in CHAT else PROMPTS[step]
            began = time.perf_counter()
            try:
                answer = self.ask(prompt, str(step), kind='text' if step in (2, 3, 5, 6, 7, 9, 11) else 'done')
                if step == 11:
                    # Save the box-reading judgement before asking the operator
                    # to dismiss it, including when they STOP at the next prompt.
                    session['answers']['11'] = answer
                    self.save('session', session)
                    session['answers']['11_removed'] = self.ask(
                        'Now click Remove in the box. Type DONE when the card shows it removed.',
                        '11_removed', kind='done')
            except NotExercised:
                session['stopped_at'] = step
                self.save('session', session)
                raise
            session['answers'][str(step)] = answer
            if step == 9:
                session['refresh_wall_upper_bound_s'] = time.perf_counter() - began
                if self.fake:
                    session['refresh_request_wall_s'] = self.fake.refresh_wall_s
            self.snapshot(step)
            if step == 9:
                # refresh_catalog records its invocation time in last_attempt;
                # its final atomic state write is the file's preserved mtime.
                # Capture before evidence transport can round file timestamps.
                state_path = self.out / 'snapshots/9/catalog/state.json'
                state = read_json(state_path)
                if state.get('last_attempt'):
                    session['refresh_last_attempt'] = state['last_attempt']
                    session['refresh_state_written_at'] = state_path.stat().st_mtime
                    session['refresh_wall_s'] = state_path.stat().st_mtime - datetime.fromisoformat(state['last_attempt']).timestamp()
            session['completed'].append(step)
            self.save('session', session)

    def cleanup(self):
        # Reuse 83d's roots-first removal; its extra bin/registry probes are only
        # measurements. Avoid importing winreg on non-Windows selftest hosts.
        roots_file = self.store_root / 'trust/roots.json'
        if not self.store_root.exists():
            self.say(f'Nothing to clean up: {self.store_root} is absent.')
            (self.root / POINTER).unlink(missing_ok=True)
            return dict(roots_present=False, store_present=False, evidence=str(self.out))
        try:
            document = read_json(roots_file)
        except (OSError, ValueError) as exc:
            raise NotExercised(f'refusing cleanup: found store {self.store_root}, but {roots_file} is missing or unreadable: {exc}') from exc
        if not isinstance(document, dict) or document.get('environment') != 'test':
            raise NotExercised(f'refusing cleanup: {roots_file} has environment {document.get("environment") if isinstance(document, dict) else "non-object JSON"!r}; expected "test"')
        result = gate83d.Gate.cleanup(self)
        (self.root / POINTER).unlink(missing_ok=True)
        result['evidence'] = str(self.out)
        result['retained_root_files'] = sorted(p.name for p in self.root.iterdir() if p.is_file())
        return result


def snapshot(gate, step):
    directory = gate.out / 'snapshots' / str(step)
    if not (directory / 'snapshot.json').is_file():
        raise NotExercised(f'step {step} snapshot absent')
    return directory


def records(directory, entry):
    package = Path(directory) / 'packages' / entry['package_id']
    return [read_json(p) for p in package.glob('installs/*/install.json')
            if read_json(p).get('artifact_digest') == entry['artifact_digest']]


def ready(directory, entry):
    found = records(directory, entry)
    assert len(found) == 1 and found[0]['state'] == 'ready', found
    return found[0]


def job(directory, entry):
    return read_json(Path(directory) / 'packages' / entry['package_id'] / 'job.json')


def offline(gate):
    gate.need('prepare')
    root_file = gate.store_root / 'trust/roots.json'
    original = root_file.read_bytes()
    cache = gate.store_root / 'catalog/catalog.json'
    if not cache.is_file():
        raise NotExercised('no saved catalog for offline probe')
    before = cache.read_bytes()
    listing = gate.store.panel_catalog(now=now())['packages']
    keys = {(p['publisher'], p['package_id']) for p in listing}
    try:
        write_json(root_file, dict(read_json(root_file), catalog_url=BASE + 'does-not-exist-gate-404/'))
        result = gate.store.refresh_catalog(now=now())
        after = cache.read_bytes()
        state = gate.store.status()['catalog']['state']
        remaining = {(p['publisher'], p['package_id']) for p in gate.store.panel_catalog(now=now())['packages']}
        evidence = dict(state=state, before_sha256=hashlib.sha256(before).hexdigest(),
                        after_sha256=hashlib.sha256(after).hexdigest(), packages=sorted(remaining), error=result.get('error'))
        gate.save('offline', evidence)
        assert state == 'unreachable', evidence
        assert after == before, 'offline refresh changed catalog bytes'
        assert remaining == keys and keys, 'offline panel lost packages'
        return evidence
    finally:
        root_file.write_bytes(original)
        gate.store.refresh_catalog(now=now())


def verify(gate, *, cleanup=True):
    results = {}
    def score(name, fn):
        try:
            detail = fn()
            results[name] = detail if isinstance(detail, tuple) and detail[0] == JUDGED else ('PASS', detail)
        except NotExercised as exc:
            results[name] = ('NOT EXERCISED', str(exc))
        except Exception as exc:
            results[name] = ('FAIL', f'{type(exc).__name__}: {exc}')
    expected = entries()
    def answer(step):
        data = read_json(gate.out / 'session.json') if (gate.out / 'session.json').is_file() else {}
        if str(step) not in data.get('answers', {}):
            raise NotExercised(f'operator answer for step {step} absent')
        return data['answers'][str(step)]
    def real_fetch():
        for step in (2, 9):
            state = read_json(snapshot(gate, step) / 'catalog/state.json')
            assert state.get('last_success') and not state.get('error'), state
        final = snapshot(gate, 9)
        union = validate_fixtures(FIXTURES)
        actual = read_json(final / 'catalog/catalog.json')
        live = read_json(gate.store_root / 'catalog/catalog.json')
        for collection in ('releases', 'withdrawals'):
            canonical = lambda values: sorted(json.dumps(e, sort_keys=True) for e in values)
            assert canonical(actual[collection]) == canonical(union[collection]), collection
            assert canonical(live[collection]) == canonical(union[collection]), 'live ' + collection
        assert read_json(final / 'trust/policy.json')['revision'] == read_json(FIXTURES / 'catalog-2/policy.json')['revision']
        return 'both refreshes successful; complete verified monotonic union; policy revision matches'
    def pypi():
        for label, step in (('E1', 3), ('E2', 10)):
            record = ready(snapshot(gate, step), expected[label])
            with zipfile.ZipFile(FIXTURES / expected[label]['artifact'][len(BASE):]) as archive:
                manifest = json.loads(archive.read('manifest.json'))
            from packaging.requirements import Requirement
            from packaging.utils import canonicalize_name
            pins = {canonicalize_name(Requirement(item['requirement']).name): next(iter(Requirement(item['requirement']).specifier)).version
                    for item in next(iter(manifest['locks'].values()))}
            assert record['find_links'] is None, record['find_links']
            assert record['interpreter']['distributions'] == pins, record['interpreter']
            assert record['python'], 'missing interpreter'
        return 'E1/E2 ready, find_links null, distributions equal signed locks'
    def installs():
        after_install = snapshot(gate, 3)
        ready(after_install, expected['E1'])
        assert len(list((after_install / 'packages' / expected['E1']['package_id']).glob('installs/*/install.json'))) == 1, 'more than one E1 install record'
        directory = snapshot(gate, 4)
        ready(directory, expected['M1'])
        assert read_json(directory / 'packages/markdown-fixture/discovery.json')['enabled']
        # Cancel sending nothing is not visible in the store (a stray request would
        # leave the same single record); tests/test_transcript_js.py and the
        # operator's step-3 answer (limb 10) carry it.
        return 'exactly one E1 install record, ready; M1 ready with discovery on'
    def tampered():
        directory = snapshot(gate, 5)
        assert any(r['field'] == 'artifact_digest' for r in job(directory, expected['T1']).get('reasons', []))
        assert not any(r['state'] == 'ready' for r in records(directory, expected['T1']))
        assert not list(directory.rglob('.download-*')), 'temporary download survived'
        # The operator's typed text is judged in limb 10, never scored by wording.
        return 'digest refusal; no ready install or download debris'
    def collision():
        before, after = snapshot(gate, 4), snapshot(gate, 6)
        assert ready(before, expected['M1']) == ready(after, expected['M1']), 'M1 changed'
        pointer = Path('packages/markdown-fixture/pointer.json')
        assert read_json(before / pointer) == read_json(after / pointer)
        collision_job = job(after, expected['B1'])
        assert any(r['field'] == 'publisher' for r in collision_job.get('reasons', []))
        assert collision_job.get('release') == {k: expected['B1'][k] for k in IDENTITY}, collision_job
        return 'fixture-two release refused; M1 record and active pointer unchanged'
    def journey():
        from microclaw.skill_packages import qualified_name
        name = qualified_name(expected['E1']['publisher'], expected['E1']['package_id'], expected['E1']['skills'][0]['name'])
        started = datetime.fromisoformat(gate.need('session')['started_at'])
        def calls(step):
            return gate83e3.recorded_calls(snapshot(gate, step) / 'history', started)
        initial = [c for c in calls(1) if c['name'] == 'search_skill_catalog']
        if not initial:
            raise NotExercised('step 1 has no recorded catalog search')
        card = next((c for r in initial for c in r['result'].get('cards', []) if c['qualified_name'] == name), None)
        assert card and card['next_step']['state'] == 'not_installed', card
        prior_ids = {c['id'] for c in calls(7)}
        loaded = [c for c in calls(8) if c['id'] not in prior_ids and c['name'] == 'load_skill']
        if not loaded:
            raise NotExercised('step 8 has no recorded load_skill')
        with zipfile.ZipFile(FIXTURES / expected['E1']['artifact'][len(BASE):]) as archive:
            body = archive.read('SKILL.md').decode('utf-8')
        assert any(body in c['result'].get('text', '') for c in loaded), loaded
        prior_ids = {c['id'] for c in calls(11)}
        final = [c for c in calls(12) if c['id'] not in prior_ids and c['name'] == 'search_skill_catalog']
        if not final:
            raise NotExercised('step 12 has no recorded search')
        cards = [card for c in final for card in c['result'].get('cards', [])]
        assert any(c['qualified_name'] == name and c['version'] == expected['E2']['version'] and c['next_step']['state'] == 'enabled' for c in cards), cards
        prefix = expected['M1']['publisher'] + '/' + expected['M1']['package_id'] + '/'
        assert not any(c['qualified_name'].startswith(prefix) for c in cards), cards
        return 'recorded search → load with full body → updated loadable search; withdrawn M1 absent'
    def update():
        directory = snapshot(gate, 10)
        e1, e2 = ready(directory, expected['E1']), ready(directory, expected['E2'])
        pointer = read_json(directory / 'packages/executable-fixture/pointer.json')
        assert pointer == dict(active=e2['install_id'], previous=e1['install_id']), pointer
        captured = snapshot(gate, 9)
        policy = read_json(FIXTURES / 'catalog-2/policy.json')
        withdrawal = read_json(FIXTURES / 'catalog-2/catalog.json')['withdrawals'][0]
        # Replay the real listing against a disposable snapshot copy. Panel
        # listing uses durable install metadata, not the excluded environments.
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'microclaw'
            shutil.copytree(captured, root / 'skill-packages')
            assert not list((root / 'skill-packages').rglob('env')), 'snapshot unexpectedly includes environments'
            with isolated_store(root) as store:
                listing = store.panel_catalog(now=datetime.fromisoformat(read_json(captured / 'snapshot.json')['captured_at']))
        rows = {(r['publisher'], r['package_id']): r for r in listing['packages']}
        executable = rows[expected['E1']['publisher'], expected['E1']['package_id']]
        markdown = rows[expected['M1']['publisher'], expected['M1']['package_id']]
        assert executable['installed_version'] == expected['E1']['version'], executable
        assert executable.get('blocked'), executable
        assert executable['block_reason']['detail'] == policy['revoked_releases'][0]['reason'], executable
        assert executable['offered_release']['version'] == expected['E2']['version'] and executable['update_available'], executable
        assert markdown['installed_version'] == expected['M1']['version'] and markdown.get('withdrawn'), markdown
        assert markdown['withdrawal_reason'] == withdrawal['reason'], markdown
        return 'E2 active/E1 previous; step-9 listing has E1 installed/blocked with E2 update and M1 installed/withdrawn, with signed reasons'
    def removed():
        directory = snapshot(gate, 11)
        assert not (directory / 'packages' / expected['M1']['package_id']).exists(), 'package directory remains'
        assert not (gate.store_root / 'packages' / expected['M1']['package_id']).exists(), 'live package directory remains'
        return 'M1 package directory absent in snapshot and live store'
    def copy():
        return JUDGED, {str(step): answer(step) for step in (2, 3, 5, 6, 7, 9, 11)}
    for name, fn in [('1 Real fetch', real_fetch), ('2 PyPI route', pypi), ('3 Install from panel', installs),
                     ('4 Tampered', tampered), ('5 Other publisher', collision), ('6 Agent journey', journey),
                     ('7 Blocked, update, withdrawn', update), ('8 Remove', removed), ('9 Offline', lambda: offline(gate)),
                     ('10 Copy', copy)]:
        score(name, fn)
    for label, step in [('E1', 3), ('M1', 4), ('E2', 10)]:
        try:
            directory = snapshot(gate, step)
            record = ready(directory, expected[label])
            duration = record['created_at'] - job(directory, expected[label])['started_at']
            gate.say(f'MEASUREMENT {label}: job start to install record creation {duration:.3f} s (not completion)')
        except Exception as exc:
            gate.say(f'MEASUREMENT {label}: unavailable: {exc}')
    try:
        session = read_json(gate.out / 'session.json')
        gate.say('MEASUREMENT step 9 refresh wall (last_attempt to final state-file write, using preserved mtime): ' + str(session.get('refresh_wall_s')) + ' s')
        gate.say('MEASUREMENT step 9 operator-inclusive wall upper bound: ' + str(session.get('refresh_wall_upper_bound_s')) + ' s')
        if 'refresh_request_wall_s' in session:
            gate.say(f'MEASUREMENT selftest step 9 HTTP request wall: {session["refresh_request_wall_s"]:.3f} s')
    except Exception as exc:
        gate.say(f'MEASUREMENT refresh: unavailable: {exc}')
    cleanup_bad = 0
    if cleanup:
        try:
            remaining = gate.cleanup()
            gate.save('cleanup', remaining)
            gate.say('CLEANUP: ' + json.dumps(remaining))
            if remaining['roots_present'] or remaining['store_present']:
                cleanup_bad += 1
        except Exception as exc:
            gate.say(f'CLEANUP FAILED: {exc}')
            cleanup_bad += 1
    return gate83e3._report(gate, results) + cleanup_bad


class ScriptedOperator:
    """Panel actions use TestClient on build_app. Chat substitutes only the LLM;
    real execute_tool and serve's AuditLog write the tool journey."""
    def __init__(self, gate, client):
        self.gate, self.client = gate, client
        self.refresh_wall_s = None
        from microclaw.conversation import AuditLog
        self.audit = AuditLog(gate.root / '20261001_000000_000000_microclaw_history.jsonl')
        self.messages = []

    def launch(self):
        deadline = time.perf_counter() + 15
        while not (self.gate.store_root / 'catalog/state.json').exists():
            assert time.perf_counter() < deadline, 'startup refresh did not finish'
            time.sleep(.02)
        assert not read_json(self.gate.store_root / 'catalog/state.json').get('error')
        return dict(slot='a', nonce='scripted-selftest', line='scripted desktop')
    def close(self): pass
    def bin(self): return gate83d.bin_listing()
    def registry(self): return []

    def install(self, label):
        entry = entries()[label]
        response = self.client.post('/api/skill-packages/' + entry['package_id'] + '/install',
                                    json={k: entry[k] for k in IDENTITY})
        assert response.status_code == 202, response.text
        path = self.gate.store_root / 'packages' / entry['package_id'] / 'job.json'
        deadline = time.perf_counter() + 30
        while read_json(path)['running']:
            assert time.perf_counter() < deadline, 'install job timed out'
            time.sleep(.02)
        return read_json(path)

    def call(self, step):
        from microclaw import tools
        tool = 'load_skill' if step == 8 else 'search_skill_catalog'
        entry = entries()['E1']
        from microclaw.skill_packages import qualified_name
        arguments = dict(name=qualified_name(entry['publisher'], entry['package_id'], entry['skills'][0]['name'])) if step == 8 else dict(query='fixture')
        tool_id = f'toolu_selftest{step:02d}'
        use = dict(role='assistant', content=[dict(type='tool_use', id=tool_id, name=tool, input=arguments)])
        self.audit.append(use)
        self.messages.append(use)
        result = tools.execute_tool(tool, arguments, None, None, records=self.messages)
        message = dict(role='user', content=[dict(type='tool_result', tool_use_id=tool_id, content=result)])
        self.audit.append(message)
        self.messages.append(message)

    def answer(self, key):
        if key == '11_removed':
            assert (self.gate.store_root / 'packages/markdown-fixture').is_dir(), 'Remove happened before the box-reading answer'
            response = self.client.post('/api/skill-packages/markdown-fixture/remove')
            assert response.status_code == 200, response.text
            return 'DONE'
        step = int(key)
        if step in CHAT:
            self.call(step)
            return 'DONE'
        if step == 2:
            assert self.client.get('/api/skill-packages/catalog').status_code == 200
            return 'Catalog checked less than an hour ago'
        if step == 3:
            # A cancelled panel confirmation makes no HTTP request. Show its
            # storage invariant before making the actual Install request.
            assert not records(self.gate.store_root, entries()['E1'])
            result = self.install('E1')
            assert not result.get('reasons'), result
            return 'yes (scripted; visual copy still needs human judgement)'
        if step == 4:
            assert not self.install('M1').get('reasons')
            return 'DONE'
        if step in (5, 6):
            result = self.install('T1' if step == 5 else 'B1')
            text = '; '.join(r['field'] + ': ' + r['detail'] for r in result.get('reasons', []))
            return 'yes; ' + text + ('; error on fixture-two only: yes (scripted)' if step == 6 else '')
        if step == 7:
            row = next(p for p in self.client.get('/api/skill-packages/catalog').json()['packages'] if p['package_id'] == entries()['I1']['package_id'])
            assert not row['compatible']
            return row['compatibility_reason']['detail'] + '; Install button: no (scripted)'
        if step == 9:
            began = time.perf_counter()
            response = self.client.post('/api/skill-packages/check')
            self.refresh_wall_s = time.perf_counter() - began
            assert response.status_code == 200 and not response.json()['error'], response.text
            return 'executable-fixture blocked: yes; markdown-fixture withdrawn: yes (scripted)'
        if step == 10:
            assert not self.install('E2').get('reasons')
            return 'DONE'
        if step == 11:
            assert (self.gate.store_root / 'packages/markdown-fixture').is_dir(), 'package removed before reading the box'
            return 'yes (scripted; confirmation copy needs human judgement)'
        raise AssertionError(key)


def mutation_cases(gate):
    """One durable artifact per measured limb; Copy is intentionally judged.
    Offline mutates the live cache through its opener while the refresh runs."""
    def edit(step, relative, fn):
        path = snapshot(gate, step) / relative
        data = read_json(path)
        fn(data)
        write_json(path, data)
    def fetch():
        edit(9, 'catalog/state.json', lambda d: d.update(last_success=None))
    def pypi():
        path = next((snapshot(gate, 3) / 'packages/executable-fixture').glob('installs/*/install.json'))
        data = read_json(path)
        data['find_links'] = 'unexpected-local-wheelhouse'
        write_json(path, data)
    def panel():
        edit(4, 'packages/markdown-fixture/discovery.json', lambda d: d.update(enabled=False))
    def tamper():
        edit(5, 'packages/tampered-fixture/job.json', lambda d: d.update(reasons=[]))
    def publisher():
        edit(6, 'packages/markdown-fixture/job.json', lambda d: d.update(reasons=[]))
    def agent():
        root = snapshot(gate, 8) / 'history'
        gate83e3._edit_result(root, 'toolu_selftest08', lambda r: r.clear())
    def update():
        edit(10, 'packages/executable-fixture/pointer.json', lambda d: d.update(previous=None))
    def revocation_missing():
        edit(9, 'trust/policy.json', lambda d: d.update(revoked_releases=[]))
    def collision_identity():
        edit(6, 'packages/markdown-fixture/job.json', lambda d: d['release'].update(publisher='fixture-lab'))
    def remove():
        (snapshot(gate, 11) / 'packages/markdown-fixture').mkdir()
    def offline_damage():
        from microclaw import updates
        original = updates._default_opener
        def corrupt(request, timeout):
            if 'does-not-exist-gate-404/' in request.full_url:
                (gate.store_root / 'catalog/catalog.json').write_bytes(b'{}\n')
            return original(request, timeout)
        updates._default_opener = corrupt
    return [(fetch, '1 Real fetch'), (pypi, '2 PyPI route'), (panel, '3 Install from panel'),
            (tamper, '4 Tampered'), (publisher, '5 Other publisher'), (agent, '6 Agent journey'),
            (update, '7 Blocked, update, withdrawn'), (revocation_missing, '7 Blocked, update, withdrawn'),
            (collision_identity, '5 Other publisher'), (remove, '8 Remove'), (offline_damage, '9 Offline')]


def check_operator_input(base):
    """Exercise the real ask with raw input lines, without ScriptedOperator."""
    from unittest.mock import patch
    from types import SimpleNamespace
    from collections import deque
    root = Path(base) / 'microclaw'
    root.mkdir(parents=True)
    with isolated_store(root):
        gate = Gate(root, Path(base) / 'evidence')
        paste = [f'agent reply line {i}' for i in range(1, 13)]
        source = iter(paste + ['DONE'])
        with patch('builtins.input', lambda _: next(source)):
            result = gate.ask('Finish the chat turn.', 'paste', kind='done')
        assert result == 'DONE', 'unvalidated pasted text was accepted as DONE'
        log = (gate.out / 'gate.log').read_text(encoding='utf-8')
        assert log.count('REJECTED[paste]:') == len(paste), 'paste lines were not rejected'
        assert log.count('ANSWER[paste]:') == 1 and 'ANSWER[paste]: DONE' in log
        assert not any('ANSWER[paste]: ' + line in log for line in paste), 'reply became an answer'
        for key, kind, lines, expected in [
            ('launched', 'done', ['', 'done'], 'done'),
            ('text', 'text', ['', 'the panel text'], 'the panel text'),
        ]:
            reads = []
            source = iter(lines)
            def input_line(_):
                line = next(source)
                reads.append(line)
                return line
            with patch('builtins.input', input_line):
                assert gate.ask('Read the prompt.', key, kind=kind) == expected
            assert reads == lines, 'empty input was accepted'
        for kind in ('done', 'text'):
            with patch('builtins.input', lambda _: 'sToP'):
                try:
                    gate.ask('Stop control.', 'stop', kind=kind)
                except NotExercised:
                    pass
                else:
                    raise AssertionError('STOP did not abandon the prompt')
        # Exercise the real Windows drain implementation against msvcrt's exact
        # kbhit/getwch interface. Only platform/console I/O is substituted.
        pending = deque('leftover paste\r\n')
        count = len(pending)
        console = SimpleNamespace(kbhit=lambda: bool(pending), getwch=pending.popleft)
        native_discard = gate.discard_pending_input
        def windows_discard():
            with patch.object(sys, 'platform', 'win32'), patch.dict(sys.modules, msvcrt=console):
                return native_discard()
        with patch.object(gate, 'discard_pending_input', windows_discard), \
             patch('builtins.input', lambda _: 'fresh operator text'):
            assert gate.ask('Next panel step.', 'drain', kind='text') == 'fresh operator text'
        assert not pending, 'pending Windows console input survived the prompt'
        log = (gate.out / 'gate.log').read_text(encoding='utf-8')
        assert f'DISCARDED: {count} pending console input characters' in log
        assert 'REJECTED[text]:' in log and 'REJECTED[launched]:' in log


def check_cleanup_phase(base):
    """Invoke main's cleanup dispatch with planted stores, including a refusal."""
    from unittest.mock import patch
    from microclaw import paths
    root = Path(base) / 'microclaw'
    root.mkdir(parents=True)
    out = Path(base) / 'evidence'
    store = root / 'skill-packages'
    pointer = root / POINTER
    close_calls = []
    actual_rmtree = shutil.rmtree
    removals = []
    def roots_first(path, *args, **kwargs):
        if Path(path) == store:
            assert not (store / 'trust/roots.json').exists(), 'cleanup removed store before roots'
            removals.append(str(path))
        return actual_rmtree(path, *args, **kwargs)
    with isolated_store(root), patch.object(paths, 'user_data_dir', lambda: root), \
         patch.object(sys, 'argv', [__file__, 'cleanup', '--out', str(out)]), \
         patch.object(Gate, 'close_microclaw', lambda self, why: close_calls.append(why)), \
         patch.object(Gate, 'bin', lambda self: {}), patch.object(Gate, 'registry', lambda self: []), \
         patch.object(shutil, 'rmtree', roots_first):
        write_json(store / 'trust/roots.json', roots())
        (store / 'leftover.txt').write_text('failed gate', encoding='utf-8')
        pointer.write_text(str(out), encoding='utf-8')
        assert main() == 0, 'TEST-ONLY cleanup failed'
        assert not store.exists() and not pointer.exists()
        assert len(removals) == len(close_calls) == 1, 'roots-first removal not exercised'
        for document in (dict(environment='production'), None):
            store.mkdir()
            if document is not None:
                write_json(store / 'trust/roots.json', document)
            (store / 'user-package.txt').write_text('keep me', encoding='utf-8')
            pointer.write_text(str(out), encoding='utf-8')
            before = {p.relative_to(store): p.read_bytes() for p in store.rglob('*') if p.is_file()}
            assert main() == 2, 'non-test or rootless store was not refused'
            after = {p.relative_to(store): p.read_bytes() for p in store.rglob('*') if p.is_file()}
            assert before == after and pointer.read_text(encoding='utf-8') == str(out), 'refusal changed user files'
            assert len(removals) == len(close_calls) == 1, 'refusal attempted close or deletion'
            actual_rmtree(store)  # remove only the isolated selftest planting
        assert main() == 0, 'absent store was not an idempotent success'
        assert not pointer.exists(), 'absent-store cleanup left the evidence pointer'


def selftest():
    from unittest.mock import patch
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from microclaw import skill_store, updates, webserve
    print('SELFTEST product under test: ' + str(Path(skill_store.__file__).parent), flush=True)
    print('SELFTEST fakes: disk urllib opener (HTTPError 404); desktop/operator; LLM tool selection. Real TestClient routes, execute_tool/AuditLog, signature/digest checks, transactions, worker self_check.', flush=True)
    print('SELFTEST build_environment boundary FAKE: real uv creates a local venv; empty local find-links cannot supply iniconfig, and managed Python/PyPI would require network. Synthetic dist-info metadata stands in for wheel installation; no PyPI or interpreter provisioning claim.', flush=True)
    with tempfile.TemporaryDirectory() as temporary:
        base = Path(temporary)
        check_operator_input(base / 'input-baseline')
        print('SELFTEST input: 12 paste lines rejected; DONE accepted; empty DONE/text re-asked; STOP accepted; Windows pending input drained and logged', flush=True)
        def accepts_any_line(self, prompt, key, kind='done'):
            return gate83d.Gate.ask(self, prompt, key)
        with patch.object(Gate, 'ask', accepts_any_line):
            try:
                check_operator_input(base / 'input-mutant')
            except AssertionError as exc:
                assert 'unvalidated pasted text' in str(exc), exc
                print('SELFTEST mutation killed: ask_accepts_any_line -> input validation FAIL', flush=True)
            else:
                raise AssertionError('accept-any-line ask mutation survived')
        check_cleanup_phase(base / 'cleanup-phase')
        print('SELFTEST cleanup phase: TEST-ONLY store removed roots first; non-test/rootless stores refused untouched; absent store exits 0; evidence pointer removed', flush=True)
        generated = base / 'generated'
        fixtures(generated)
        file_bytes = lambda directory: {p.relative_to(directory).as_posix(): p.read_bytes()
                                        for p in directory.rglob('*') if p.is_file()}
        first_generation = file_bytes(generated)
        fixtures(generated)
        assert first_generation == file_bytes(generated) == file_bytes(FIXTURES), 'fixtures are not deterministic or committed fixtures differ'
        print('SELFTEST fixtures: two generations byte-identical to committed files; both product-verified', flush=True)
        data = base / 'data'
        root = data / 'microclaw'
        root.mkdir(parents=True)
        out = base / 'evidence'
        empty = base / 'empty-find-links'
        empty.mkdir()
        uv = shutil.which('uv')
        assert uv, 'real uv required for selftest'
        original_probe = skill_store.probe
        def environment(directory, manifest, **kwargs):
            assert kwargs.get('find_links') is None, kwargs
            env = dict(os.environ, UV_OFFLINE='1', UV_CACHE_DIR=str(base / 'uv-cache'))
            subprocess.run([uv, 'venv', '--no-config', '--no-python-downloads', '--python', sys.executable, str(directory / 'env')],
                           env=env, capture_output=True, text=True, check=True)
            python = directory / 'env' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
            # Prove this route cannot install from the permitted empty wheelhouse.
            attempted = subprocess.run([uv, 'pip', 'install', '--no-config', '--offline', '--no-index', '--find-links', str(empty),
                                        '--python', str(python), 'iniconfig==2.0.0'], env=env, capture_output=True, text=True)
            assert attempted.returncode != 0, 'empty wheelhouse unexpectedly supplied dependency'
            site = subprocess.run([str(python), '-I', '-c', 'import sysconfig; print(sysconfig.get_path("purelib"))'],
                                  capture_output=True, text=True, check=True).stdout.strip()
            metadata = Path(site) / 'iniconfig-2.0.0.dist-info'
            metadata.mkdir()
            (metadata / 'METADATA').write_text('Metadata-Version: 2.1\nName: iniconfig\nVersion: 2.0.0\n', encoding='utf-8')
            return str(python), original_probe(python)
        open_file = disk_opener(FIXTURES)
        with patch.dict(os.environ, LOCALAPPDATA=str(data), XDG_DATA_HOME=str(data), UV_OFFLINE='1'), \
             patch.object(skill_store, 'user_data_dir', lambda: root), \
             patch.object(updates, '_default_opener', open_file), \
             patch.object(skill_store, 'build_environment', environment):
            gate = Gate(root, out)
            gate.prepare()
            assert sorted(p.relative_to(gate.store_root).as_posix() for p in gate.store_root.rglob('*') if p.is_file()) == ['trust/roots.json'], 'prepare fetched or wrote extra store state'
            session = SimpleNamespace(mode=webserve.SessionMode.NORMAL)
            with TestClient(webserve.build_app(session)) as client:
                operator = ScriptedOperator(gate, client)
                gate.fake = operator
                operator.launch()
                gate.session()
                baseline = base / 'backup'
                shutil.copytree(out, baseline / 'evidence')
                shutil.copytree(root, baseline / 'root')
                def restore():
                    updates._default_opener = open_file
                    shutil.rmtree(out)
                    shutil.rmtree(root)
                    shutil.copytree(baseline / 'evidence', out)
                    shutil.copytree(baseline / 'root', root)
                    fresh = Gate(root, out, fake=operator)
                    operator.gate = fresh
                    return fresh
                clean = restore()
                bad = verify(clean, cleanup=False)
                assert bad == 0, clean.last_results
                print(f'SELFTEST baseline: {len(clean.last_results)} limbs; {bad} failed/not exercised; 1 operator-judged', flush=True)
                killed = 0
                for index in range(len(mutation_cases(clean))):
                    fresh = restore()
                    mutate, target = mutation_cases(fresh)[index]
                    mutate()
                    verify(fresh, cleanup=False)
                    verdict = fresh.last_results[target][0]
                    assert verdict == 'FAIL', (mutate.__name__, target, verdict)
                    others = [k for k, v in fresh.last_results.items() if v[0] not in ('PASS', JUDGED) and k != target]
                    assert not others, ('mutation affected independent limbs', mutate.__name__, others)
                    killed += 1
                    print(f'SELFTEST mutation killed: {mutate.__name__} -> {target} FAIL; other limbs pass', flush=True)
                clean = restore()
                real_rmtree = shutil.rmtree
                def roots_first(path, *args, **kwargs):
                    if Path(path) == clean.store_root:
                        assert not (clean.store_root / 'trust/roots.json').exists(), 'store removal preceded roots removal'
                    return real_rmtree(path, *args, **kwargs)
                with patch.object(shutil, 'rmtree', roots_first):
                    final_bad = verify(clean, cleanup=True)
                assert final_bad == 0 and not clean.store_root.exists(), 'cleanup failed'
                assert not (root / POINTER).exists()
                print(f'SELFTEST SUMMARY: 10 limbs (9 measured, 1 operator-judged); baseline 0 failed/not exercised; mutations killed {killed + 1}/12 (11 limb mutations + input mutation); input and cleanup-phase checks passed; roots-first cleanup passed. Live network and visual confirmations NOT EXERCISED by selftest.', flush=True)
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
        out = root.parent / ('block83f3-' + datetime.now().strftime('%Y%m%d-%H%M%S'))
    gate = Gate(root, out)
    if args.phase == 'prepare':
        root.mkdir(parents=True, exist_ok=True)
        pointer.write_text(str(gate.out.resolve()), encoding='utf-8')
    try:
        if args.phase == 'verify':
            return 1 if verify(gate) else 0
        result = getattr(gate, args.phase)()
        if args.phase == 'cleanup':
            gate.save('cleanup', result)
            gate.say('CLEANUP: ' + json.dumps(result))
            if result['roots_present'] or result['store_present']:
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
