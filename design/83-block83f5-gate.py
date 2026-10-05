"""83f-5 production gate. Live phases are operator-only; selftest is offline."""
from __future__ import annotations
import argparse
import ast
import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
import zlib

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('catalog_gate83f4', HERE / '83-block83f4-gate.py')
catalog_gate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(catalog_gate)
demo = catalog_gate.previous
from microclaw import catalog_intake as intake, skill_packages as packages, skill_store as store

read, write, gh, api = catalog_gate.read, catalog_gate.write, catalog_gate.gh, catalog_gate.api
NotExercised = demo.NotExercised
REPO = catalog_gate.REPO
EXAMPLE = 'Micro-Claw/example-skill-package'
TAG = 'v1.0.0'
ZIP = 'session-start.zip'
QUALIFIED = 'microclaw-examples/session-start/open-unfamiliar-system'
PREFIX = '83f-5 gate:'
POINTER = '83f5-gate-evidence.txt'
PHASES = ('publish', 'prepare', 'session', 'verify', 'cleanup', 'selftest')


def shipped_roots(source=None):
    source = source if source is not None else (HERE.parent / 'microclaw/skill_packages.py').read_text(encoding='utf-8')
    roots = next(ast.literal_eval(node.value) for node in ast.parse(source).body
                 if isinstance(node, ast.Assign) and getattr(node.targets[0], 'id', None) == 'PRODUCTION_ROOTS')
    assert roots['environment'] == 'production'
    return roots


def slot_roots():
    # -I prevents this checkout (and PYTHONPATH) from hiding an old installed slot.
    output = catalog_gate.command([sys.executable, '-I', '-c',
        'import json; from microclaw.skill_packages import PRODUCTION_ROOTS; print(json.dumps(PRODUCTION_ROOTS))'])
    return json.loads(output)


def download_asset(asset, target):
    with Path(target).open('wb') as stream:
        result = subprocess.run(['gh', 'api', f'repos/{EXAMPLE}/releases/assets/{asset["id"]}',
                                 '-H', 'Accept: application/octet-stream'], stdin=subprocess.DEVNULL,
                                stdout=stream, stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError('release asset download failed: ' + result.stderr.decode('utf-8', 'replace'))


CHAT_STEPS = (1, 4)
SCREENSHOTS = {2: 'screenshot-catalog.png', 3: 'screenshot-installed.png'}


class Gate(demo.Gate):
    def __init__(self, root, out, *, fake=None):
        super().__init__(root, out, fake=fake)
        self.directory = self.out

    log = demo.Gate.say

    def save(self, name, value):
        write(self.out / (name if name.endswith('.json') else name + '.json'), value)

    def artifact(self, name):
        path = self.out / name
        if not path.is_file():
            raise NotExercised('missing artifact: ' + name)
        return read(path)

    def snapshot(self, step):
        if self.store_root.exists():
            demo.Gate.snapshot(self, step)
            write(self.out / 'snapshots' / str(step) / 'catalog-status.json', self.store.status()['catalog'])
        else:
            target = self.out / 'snapshots' / str(step)
            target.mkdir(parents=True)
            history = target / 'history'
            history.mkdir()
            for path in self.root.glob('*_microclaw_history.jsonl'):
                shutil.copy2(path, history / path.name)
            write(target / 'snapshot.json', dict(step=step, captured_at=demo.now().isoformat(), store_absent=True))

    def capture(self, case):
        pr, runs = catalog_gate.Gate.capture(self, case)
        number = self.artifact('state.json')['prs'][case]['number']
        self.save(case + '-api-pr', api(f'repos/{REPO}/pulls/{number}'))
        for run in runs:
            if run['status'] == 'completed':
                text = gh('run', 'view', str(run['databaseId']), '--repo', REPO, '--log')
                (self.out / f'{case}-run-{run["databaseId"]}.log').write_text(text + '\n', encoding='utf-8')
        return pr, runs

    def prepare(self):
        roots_file = self.store_root / 'trust/roots.json'
        if roots_file.exists() or roots_file.is_symlink():
            raise NotExercised('trust/roots.json exists; remove only the TEST-ONLY override, then retry: '
                'Remove-Item -LiteralPath "$env:LOCALAPPDATA\\microclaw\\skill-packages\\trust\\roots.json"')
        expected, installed = shipped_roots(), slot_roots()
        ids = set(packages._keys(expected['keys'], 'roots.keys'))
        actual = set(packages._keys(installed['keys'], 'roots.keys', empty=True))
        self.save('slot-roots', dict(expected=expected, installed=installed, interpreter=sys.executable))
        if len(ids) != 2 or installed['environment'] != 'production' or actual != ids:
            raise NotExercised('installed slot roots differ from this checkout. Run:\n'
                'cd D:\\Code\\microclaw\n'
                'git checkout block-83f-5\n'
                'git merge-base --is-ancestor f903fa3 HEAD\n'
                'if ($LASTEXITCODE -ne 0) { throw "WRONG TREE - stop" }\n'
                '.\\install.bat')
        if not (self.out / 'snapshots/0').exists():
            self.snapshot(0)
        self.save('prepare', dict(prepared_at=demo.now().isoformat(), root_ids=sorted(ids)))
        self.say('RECORDED: prepare; installed slot has both production roots; no trust override.')

    def session(self):
        self.need('prepare')
        state = read(self.out / 'session.json') if (self.out / 'session.json').exists() else dict(
            started_at=demo.now().isoformat(), completed=[], answers={})
        self.save('session', state)
        if len(state['completed']) == 4:
            self.say('Session already captured; run verify. Screenshots stay in the evidence folder.')
            return
        state['launch'] = self.launch_desktop('Session phase.')
        self.save('session', state)
        if not (self.out / 'snapshots/launch').exists():
            self.snapshot('launch')
        prompts = {
            1: 'Search the community skill catalog for "session" and list what you find.',
            2: 'Open Community skill packages in Firefox, scrolled so the Catalog line at the top shows. Take a Firefox screenshot: press Ctrl+Shift+S, click Save visible, then Download (the gate copies it from Downloads) or save it yourself as ' + str(self.out / SCREENSHOTS[2]) + '. Then type DONE here.',
            3: 'On microclaw-examples / session-start, click Install, read the confirmation box, then click Install in that box. Wait until the card says Installed (if a partial run already installed it, keep it), with that card in view. Take a Firefox screenshot: press Ctrl+Shift+S, click Save visible, then Download (the gate copies it from Downloads) or save it yourself as ' + str(self.out / SCREENSHOTS[3]) + '. Then type DONE here.',
            4: 'Load the skill microclaw-examples/session-start/open-unfamiliar-system and quote its first step. Do not carry it out.',
        }
        for step, prompt in prompts.items():
            if step in state['completed']:
                continue
            self.say(f'STEP {step}')
            if step in CHAT_STEPS:  # 83f-3 round 1: the message stands alone, the instruction follows
                self.say('\n' + prompt + '\n')
                prompt = ("Paste the message above into MicroClaw's chat box in Firefox (not here). "
                          "When the agent's reply has finished, type DONE here.")
            started = time.time()
            state['answers'][str(step)] = self.ask(prompt, str(step))
            # 83f-5 round 1: the operator saved straight into the evidence folder and the gate
            # announced "missing" and moved on; now it accepts either place and asks until one holds it.
            while step in SCREENSHOTS and self.collect_screenshot(SCREENSHOTS[step], started) is None:
                self.ask(f'{SCREENSHOTS[step]} is not in {self.out} and no new Screenshot*.png is in Downloads. '
                         'Save the screenshot now (either place), then type DONE (or STOP).', str(step))
            self.snapshot(step)
            state['completed'].append(step)
            self.save('session', state)

    def collect_screenshot(self, name, started, downloads=None):
        """Return the evidence copy of this step's screenshot, copying the newest Firefox download if needed."""
        if (self.out / name).exists():  # saved there directly, or kept from a partial run
            return self.out / name
        downloads = Path(downloads or Path.home() / 'Downloads')
        shots = [p for p in downloads.glob('Screenshot*.png') if p.stat().st_mtime >= started - 1]
        if not shots:
            return None
        newest = max(shots, key=lambda p: p.stat().st_mtime)
        shutil.copyfile(newest, self.out / name)
        self.say(f'Saved {newest.name} as {name}.')
        return self.out / name

    def cleanup(self):
        catalog_gate.require_gh()
        state = self.artifact('state.json')
        catalog_gate.cleanup(self, cases={'control'}, branches=set(state.get('created_branches', [])))
        self.say('Retained installed example, store, and merged release. Only control PR and gate fork branches cleaned up.')
        self.save('cleanup', dict(cleaned_at=demo.now().isoformat(), retained_store=True))


def open_case(gate, state, case, record=None):
    title = f'{PREFIX} ' + ('example v1.0.0' if case == 'example' else 'unadmitted control')
    # Discover across evidence folders, including a crash between PR creation and state write.
    opened = api(f'repos/{REPO}/pulls?state=open&base=main&per_page=100', paginate=True)
    matches = [p for p in opened if (p['title'] == title or p['title'].startswith(title + ' '))
               and (p['head'].get('repo') or {}).get('full_name') == state['fork']]
    if len(matches) > 1:
        raise RuntimeError('multiple open gate PRs; refusing to guess which to score')
    if matches:
        pr = matches[0]
        files = api(f'repos/{REPO}/pulls/{pr["number"]}/files')
        if len(files) != 1 or files[0]['status'] != 'added':
            raise RuntimeError('existing gate PR is not exactly one addition')
        content = api(f'repos/{state["fork"]}/contents/{files[0]["filename"]}?ref={pr["head"]["ref"]}')
        existing = json.loads(base64.b64decode(content['content']))
        if record is not None and existing != record:
            raise RuntimeError('existing example PR has different release bytes')
        record = existing
        branch, sha = pr['head']['ref'], pr['head']['sha']
    else:
        if record is None:
            source = gate.out / 'control-package'
            if not source.exists():
                shutil.copytree(HERE / '83f5-example-package/package', source)
            manifest = read(source / 'manifest.json')
            manifest['publisher'] = 'gate-unadmitted'
            write(source / 'manifest.json', manifest)
            key = gate.out / 'control.pem'
            if not key.exists():
                gate.say(catalog_gate.product('keygen', '--out', key))
            url = 'https://example.org/83f5-gate-unadmitted.zip'
            artifact = gate.out / 'control.zip'
            gate.say(catalog_gate.product('pack', '--dir', source, '--url', url, '--out', artifact))
            gate.say(catalog_gate.product('sign-release', '--key', key, '--artifact', artifact,
                                         '--url', url, '--out', gate.out / 'control.json'))
            record = read(gate.out / 'control.json')
        path = intake.record_path('releases', record)
        pending = state.setdefault('pending', {})
        branch = pending.setdefault(case, '83f5-' + demo.now().strftime('%Y%m%d%H%M%S%f') + '-' + case)
        if case == 'control':
            title += ' ' + branch
        if branch not in state['created_branches']:
            state['created_branches'].append(branch)
        gate.save('state', state)
        try:
            sha = api(f'repos/{state["fork"]}/git/ref/heads/{branch}')['object']['sha']
            blob = api(f'repos/{state["fork"]}/contents/{path}?ref={branch}')
            if json.loads(base64.b64decode(blob['content'])) != record:
                raise RuntimeError('existing pending branch differs from the intended record')
        except RuntimeError as exc:
            if '404' not in str(exc):
                raise
            base = api(f'repos/{REPO}/git/ref/heads/main')['object']['sha']
            commit = catalog_gate.create_branch(state['fork'], base, branch, title, path,
                                                intake.encode(record).decode('ascii'))
            sha = commit['sha']
            gate.save(case + '-creation', commit)
        pr = api(f'repos/{REPO}/pulls', dict(title=title, head=state['fork'].split('/')[0] + ':' + branch,
                                           base='main', body='83f-5 production gate; one signed record.'))
    if branch.startswith('83f5-') and branch not in state['created_branches']:
        state['created_branches'].append(branch)
    state.get('pending', {}).pop(case, None)
    state['prs'][case] = dict(number=pr['number'], title=pr['title'], branch=branch, sha=sha,
                              path=intake.record_path('releases', record))
    gate.save(case + '.json' if case == 'control' else 'release.json', record)
    gate.save('state', state)
    gate.say(f'{case}: using {pr["html_url"]}')


def capture_remote(gate):
    state = gate.artifact('state.json')
    for case in state['prs']:
        try:
            gate.capture(case)
        except Exception as exc:
            for name in (case + '-pr.json', case + '-api-pr.json', case + '-runs.json'):
                (gate.out / name).unlink(missing_ok=True)
            gate.say(f'{case}: capture failed: {exc}')
    (gate.out / 'after-merge-commits.json').unlink(missing_ok=True)
    try:
        example = gate.artifact('example-api-pr.json')
        merge = example.get('merge_commit_sha') if example.get('merged_at') else None
        if merge:
            commits = api(f'repos/{REPO}/compare/{merge}...main')['commits']
            gate.save('after-merge-commits', [api(f'repos/{REPO}/commits/{c["sha"]}') for c in commits])
    except Exception as exc:
        gate.say('Rebuild capture failed: ' + str(exc))
    for name, fetch in (
        ('policy-commits', lambda: api(f'repos/{REPO}/commits?path=policy.json&sha=main&per_page=1')),
        ('reminder-runs', lambda: json.loads(gh('run', 'list', '--repo', REPO, '--workflow', 'policy-reminder.yml',
            '--branch', 'main', '--limit', '100', '--json', 'databaseId,status,conclusion,headBranch,createdAt,url'))),
        ('reminder-issues', lambda: json.loads(gh('issue', 'list', '--repo', REPO, '--state', 'open',
            '--search', 'Renew catalog trust policy in:title', '--limit', '1000', '--json', 'title,state,url')))):
        try:
            gate.save(name, fetch())
        except Exception as exc:
            (gate.out / (name + '.json')).unlink(missing_ok=True)
            gate.say(name + ' capture failed: ' + str(exc))
    catalog_gate.collect_served(gate, base=store.CATALOG_URL)


def publish(gate, deadline_seconds=900):
    catalog_gate.require_gh()
    release = api(f'repos/{EXAMPLE}/releases/tags/{TAG}')
    gate.save('example-release', release)
    gate.save('release-runs', json.loads(gh('run', 'list', '--repo', EXAMPLE, '--workflow', 'microclaw-release.yml',
        '--branch', TAG, '--event', 'push', '--limit', '100', '--json', 'databaseId,status,conclusion,headBranch,headSha,createdAt,url')))
    for name in ('release.json', ZIP):
        found = [a for a in release['assets'] if a['name'] == name]
        if len(found) != 1:
            raise RuntimeError('expected one release asset: ' + name)
        download_asset(found[0], gate.out / name)
    record = packages.validate_intake(gate.artifact('release.json'))
    assert (record['publisher'], record['package_id'], record['version']) == ('microclaw-examples', 'session-start', TAG[1:])
    state = read(gate.out / 'state.json') if (gate.out / 'state.json').exists() else dict(
        fork=catalog_gate.ensure_fork(), nonce=demo.now().strftime('%Y%m%d%H%M%S'), prs={}, created_branches=[])
    gate.save('state', state)
    path = intake.record_path('releases', record)
    try:
        blob = api(f'repos/{REPO}/contents/{path}?ref=main')
    except RuntimeError as exc:
        if '404' not in str(exc):
            raise
        blob = None
    if blob is not None:
        if json.loads(base64.b64decode(blob['content'])) != record:
            raise RuntimeError('version 1.0.0 is already committed with different bytes; never resubmit it')
        commits = api(f'repos/{REPO}/commits?path={path}&sha=main&per_page=1')
        prs = api(f'repos/{REPO}/commits/{commits[0]["sha"]}/pulls')
        merged = [p for p in prs if p.get('merged_at') and p['base']['ref'] == 'main']
        if len(merged) != 1:
            raise RuntimeError('release already exists but its one merged production PR could not be identified')
        pr = merged[0]
        state['prs']['example'] = dict(number=pr['number'], title=pr['title'], branch=pr['head']['ref'],
                                      sha=pr['head']['sha'], path=path)
        gate.save('state', state)
        gate.say('example: already merged; scoring it, without opening another PR')
    else:
        open_case(gate, state, 'example', record)
    open_case(gate, state, 'control')
    for case in ('example', 'control'):
        deadline = time.monotonic() + deadline_seconds
        while True:
            pr, runs = gate.capture(case)
            complete = any(r['status'] == 'completed' for r in runs)
            settled = bool(pr.get('mergedAt')) if case == 'example' else any(
                '**publisher**' in c.get('body', '') for c in pr['comments'])
            if complete and settled:
                break
            if time.monotonic() >= deadline:
                gate.say(case + ': deadline reached; verify scores captured artifacts')
                break
            time.sleep(10)
    deadline = time.monotonic() + deadline_seconds
    while True:
        capture_remote(gate)
        commits = read(gate.out / 'after-merge-commits.json') if (gate.out / 'after-merge-commits.json').exists() else []
        catalog = read(gate.out / 'served-catalog.json') if (gate.out / 'served-catalog.json').exists() else {}
        if any(c['commit']['message'].splitlines()[0] == 'Rebuild verified package catalog' for c in commits) and record in catalog.get('releases', []):
            break
        if time.monotonic() >= deadline:
            gate.say('Rebuild/served deadline reached; verify scores captured artifacts')
            break
        time.sleep(10)
    gate.save('publish', dict(captured_at=demo.now().isoformat()))
    gate.say('RECORDED: publish; continue with prepare, session, verify.')
    return 0


def verify(gate, *, collect=True, opener=None):
    if collect:
        try:
            catalog_gate.require_gh()
            capture_remote(gate)
        except Exception as exc:
            gate.say('Remote capture unavailable: ' + str(exc))
            for name in ('example-pr.json', 'example-api-pr.json', 'example-runs.json',
                         'control-pr.json', 'control-api-pr.json', 'control-runs.json',
                         'after-merge-commits.json', 'policy-commits.json', 'reminder-runs.json',
                         'reminder-issues.json', 'served-policy.json', 'served-catalog.json'):
                (gate.out / name).unlink(missing_ok=True)
    results = {}
    def score(label, fn):
        try:
            detail = fn()
            results[label] = detail if isinstance(detail, tuple) else ('PASS', detail)
        except NotExercised as exc:
            results[label] = ('NOT EXERCISED', str(exc))
        except Exception as exc:
            results[label] = ('FAIL', f'{type(exc).__name__}: {exc}')
    def record():
        return gate.artifact('release.json')
    def policy():
        return packages.verify_trust_policy(gate.artifact('served-policy.json'), packages.PRODUCTION_ROOTS)
    def successful(name):
        runs = gate.artifact(name)
        if not runs or not any(r['status'] == 'completed' for r in runs):
            raise NotExercised(name + ': no completed run')
        assert any(r['status'] == 'completed' and r['conclusion'] == 'success' for r in runs), runs
        return runs
    def logs(case):
        successful(case + '-runs.json')
        state = gate.artifact('state.json')['prs'][case]
        matching = [r for r in gate.artifact(case + '-runs.json') if r['status'] == 'completed' and r['conclusion'] == 'success']
        text = '\n'.join((gate.out / f'{case}-run-{r["databaseId"]}.log').read_text(encoding='utf-8') for r in matching)
        # Ignore the echoed shell source; require materialize's actual result line.
        lines = [line.split('PR changes materialized as data against the current base: ', 1)[1].strip()
                 for line in text.splitlines() if 'PR changes materialized as data against the current base: ' in line]
        assert lines and all(line == 'A ' + state['path'] for line in lines), lines
        return text
    def l1():
        runs = successful('release-runs.json')
        assert any(r.get('headBranch') == TAG and r['conclusion'] == 'success' for r in runs)
        release = gate.artifact('example-release.json')
        assert release['tag_name'] == TAG
        assets = release['assets']
        assert sorted(a['name'] for a in assets) == sorted([ZIP, 'release.json']), assets
        return 'tag workflow success; exactly session-start.zip and release.json'
    def l2():
        entry = record()
        assert (entry['publisher'], entry['package_id'], entry['version']) == ('microclaw-examples', 'session-start', TAG[1:])
        path = gate.out / ZIP
        if not path.exists():
            raise NotExercised('release zip missing')
        packages.check_release(entry, policy(), purpose='admission', artifact=path, now=demo.now())
        assert hashlib.sha256(path.read_bytes()).hexdigest() == entry['artifact_digest']
        url = next(a['browser_download_url'] for a in gate.artifact('example-release.json')['assets'] if a['name'] == ZIP)
        with zipfile.ZipFile(path) as archive:
            manifest = packages.validate_manifest(json.loads(archive.read('manifest.json')))
        assert manifest['artifact'] == entry['artifact'] == url
        intake.archive_checks(path, entry)
        return 'publisher signature, zip digest, archive, and release URL verified'
    def l3():
        pr = gate.artifact('example-pr.json')
        raw = gate.artifact('example-api-pr.json')
        assert pr['state'] == 'MERGED' and pr['mergedAt']
        assert raw['merged_by']['login'] == 'github-actions[bot]', raw.get('merged_by')
        logs('example')
        merge = raw['merge_commit_sha']
        rebuilt = [c for c in gate.artifact('after-merge-commits.json')
                   if c['commit']['message'].splitlines()[0] == 'Rebuild verified package catalog'
                   and any(p['sha'] == merge for p in c['parents'])]
        assert len(rebuilt) == 1, rebuilt
        assert [f['filename'] for f in rebuilt[0]['files']] == ['catalog.json']
        assert datetime.fromisoformat(rebuilt[0]['commit']['committer']['date'].replace('Z', '+00:00')) >= datetime.fromisoformat(pr['mergedAt'].replace('Z', '+00:00'))
        return 'Action merged; exactly one direct rebuild; materialize named the sole addition'
    def l4():
        pr = gate.artifact('control-pr.json')
        assert pr['state'] == 'OPEN' and pr['mergedAt'] is None
        assert any('**publisher**' in c['body'] and c.get('author', {}).get('login') == 'github-actions' for c in pr['comments'])
        text = logs('control')
        control = gate.artifact('control.json')
        assert control['publisher'] == 'gate-unadmitted'
        assert control['artifact'] not in text and not re.search(r'(?i)(download refused|downloading .*\.zip|fetching .*\.zip)', text)
        return 'publisher refusal comment, successful intake, open/unmerged; no artifact download in log'
    def l5():
        document = policy()
        assert document['environment'] == 'production'
        publisher = document['publishers']['microclaw-examples']
        assert publisher['state'] == 'active' and any(k['state'] == 'active' for k in publisher['keys'])
        commits = gate.artifact('policy-commits.json')
        if not commits:
            raise NotExercised('policy signing commit missing')
        signed = datetime.fromisoformat(commits[0]['commit']['committer']['date'].replace('Z', '+00:00'))
        days = (packages._expires(document['expires_at']) - signed).total_seconds() / 86400
        assert 150 <= days <= 200, days
        assert record() in gate.artifact('served-catalog.json')['releases']
        return f'production policy verified; active example publisher; expiry {days:.2f} days after signing commit; release served'
    def l6():
        assert catalog_gate.client_fetch(gate, environment='production', expected=[record()], opener=opener) == 'PASS'
        status = gate.artifact('client-status.json')
        assert status['state'] == 'ok' and status['last_success'] and not status['fetch_exclusions']
        assert not gate.artifact('client-state.json')['exclusions']
        return 'isolated production fetch ok; release listed; zero exclusions'
    def snap(step):
        return demo.snapshot(gate, step)
    def l7():
        directory = snap(2)
        status = read(directory / 'catalog-status.json')
        raw = read(directory / 'catalog/state.json')
        assert status['state'] == 'ok' and status['last_success']
        assert raw['last_success'] and not raw.get('error')
        cached = packages.verify_trust_policy(read(directory / 'trust/policy.json'), packages.PRODUCTION_ROOTS)
        assert cached['revision'] == policy()['revision']
        assert not (directory / 'trust/roots.json').exists()
        assert not (gate.store_root / 'trust/roots.json').exists()
        return 'step-2 cache ok and current; no trust override'
    def l8():
        directory = snap(3)
        pointer = read(directory / 'packages/session-start/pointer.json')
        installed = next((r for r in demo.records(directory, record())
                          if r['state'] == 'ready' and r['install_id'] == pointer['active']), None)
        assert installed, 'no active ready install for the signed digest'
        assert all(installed['intake'][key] == record()[key] for key in intake.IDENTITY)
        return 'example install ready with exact signed identity/digest'
    def l9():
        since = datetime.fromisoformat(gate.need('session')['started_at'])
        def calls(step):
            return demo.gate83e3.recorded_calls(snap(step) / 'history', since)
        baseline = {c['id'] for c in calls('launch')}
        searched = [c for c in calls(1) if c['id'] not in baseline and c['name'] == 'search_skill_catalog']
        if not searched:
            raise NotExercised('step 1 has no new search_skill_catalog call')
        assert any(card['qualified_name'] == QUALIFIED for call in searched for card in call['result'].get('cards', []))
        before_load = {c['id'] for c in calls(3)}
        loaded = [c for c in calls(4) if c['id'] not in before_load and c['name'] == 'load_skill']
        if not loaded:
            raise NotExercised('step 4 has no new load_skill call')
        title = (HERE / '83f5-example-package/package/SKILL.md').read_text(encoding='utf-8').splitlines()[0]
        assert any(c['input'].get('name') == QUALIFIED and title in c['result'].get('text', '')
                   and 'Publisher-provided skill (publisher-provided; publisher=microclaw-examples;'
                   in c['result'].get('text', '') for c in loaded)
        return 'serve history contains new search card and publisher-provenance skill text/title'
    def l10():
        for name in ('screenshot-catalog.png', 'screenshot-installed.png'):
            path = gate.out / name
            if not path.exists():
                raise NotExercised(name + ' missing')
            data = path.read_bytes()
            assert data.startswith(b'\x89PNG\r\n\x1a\n'), name + ': not PNG'
            offset, kinds = 8, []
            while offset < len(data):
                assert offset + 12 <= len(data), name + ': truncated PNG chunk'
                size = int.from_bytes(data[offset:offset + 4], 'big')
                kind = data[offset + 4:offset + 8]
                end = offset + 8 + size
                assert end + 4 <= len(data), name + ': truncated PNG data'
                assert zlib.crc32(data[offset + 4:end]) & 0xffffffff == int.from_bytes(data[end:end + 4], 'big'), name + ': PNG CRC mismatch'
                kinds.append(kind)
                if kind == b'IHDR':
                    assert size == 13 and all(struct.unpack('>II', data[offset + 8:offset + 16])), name + ': invalid PNG dimensions'
                offset = end + 4
                if kind == b'IEND':
                    assert size == 0 and offset == len(data), name + ': invalid PNG end'
                    break
            assert kinds[0] == b'IHDR' and kinds[-1] == b'IEND' and b'IDAT' in kinds, name + ': missing PNG chunks'
        return demo.JUDGED, 'coordinator must read screenshot-catalog.png and screenshot-installed.png'
    def l11():
        runs = successful('reminder-runs.json')
        assert any(r.get('headBranch') == 'main' and r['conclusion'] == 'success' for r in runs)
        assert not any(i['title'].startswith('Renew catalog trust policy') for i in gate.artifact('reminder-issues.json'))
        return 'reminder succeeded on main; no open renewal issue (firing branch is unit-tested only)'
    for label, fn in [('L1', l1), ('L2', l2), ('L3', l3), ('L4', l4), ('L5', l5), ('L6', l6),
                      ('L7', l7), ('L8', l8), ('L9', l9), ('L10', l10), ('L11', l11)]:
        score(label, fn)
    try:
        pr, raw = gate.artifact('example-pr.json'), gate.artifact('example-api-pr.json')
        merged = datetime.fromisoformat(pr['mergedAt'].replace('Z', '+00:00'))
        opened = datetime.fromisoformat(raw['created_at'].replace('Z', '+00:00'))
        gate.say(f'MEASUREMENT PR open -> merged: {(merged - opened).total_seconds():.3f} s')
        rebuild = next(c for c in gate.artifact('after-merge-commits.json') if any(p['sha'] == raw['merge_commit_sha'] for p in c['parents']))
        at = datetime.fromisoformat(rebuild['commit']['committer']['date'].replace('Z', '+00:00'))
        gate.say(f'MEASUREMENT merge -> rebuild commit: {(at - merged).total_seconds():.3f} s')
    except Exception as exc:
        gate.say('MEASUREMENT PR/rebuild unavailable: ' + str(exc))
    gate.say('MEASUREMENT install click -> ready: R145; install records carry creation time, not click/completion times.')
    return demo.gate83e3._report(gate, results)


class FakeGitHub:
    """The gh pr/run JSON shapes from 83f-4, plus GitHub REST's raw shapes.
    Unexpected commands refuse; binary/HTTP responses stay inside this object."""
    def __init__(self, directory, entry, artifact, policy):
        self.directory, self.entry, self.artifact, self.policy = Path(directory), entry, artifact, policy
        self.stamp = demo.now().strftime('%Y-%m-%dT%H:%M:%SZ')
        self.fork = 'offline-operator/package-catalog'
        self.prs, self.refs, self.blobs, self.trees, self.commits = {}, {}, {}, {}, {}
        self.created_prs = 0
        self.pr_views, self.run_views = catalog_gate.fake_observations(
            {'example': (True, None), 'control': (False, 'publisher')}, prefix=PREFIX)
        self.merge = 'a' * 40
        self.rebuild = 'b' * 40
        self.base = 'c' * 40
        self.release = dict(tag_name=TAG, assets=[dict(id=i, name=name,
            browser_download_url=f'https://github.com/{EXAMPLE}/releases/download/{TAG}/{name}')
            for i, name in enumerate(['release.json', ZIP], 1)])
        self.catalog = dict(type=packages.CATALOG_TYPE, releases=[], withdrawals=[])

    def run(self, case, title=None):
        run = deepcopy(self.run_views['example' if case == 'example' else 'control'][0])
        run.update(databaseId=10 if case == 'example' else 11, displayTitle=title,
                   headSha=self.base, headBranch=TAG if case == 'release' else 'main', createdAt=self.stamp)
        return run

    def command(self, args, *, cwd=None, input=None):
        if args[0] != 'gh':
            # Only the product's offline publisher commands may spawn a process.
            assert args[1:3] == ['-m', 'microclaw.catalog_intake'], args
            assert args[3] in ('keygen', 'pack', 'sign-release'), args
            return self.real_command(args, cwd=cwd, input=input)
        rest = args[1:]
        if rest == ['auth', 'status']:
            return 'Logged in to github.com account offline-operator'
        if rest[:2] == ['repo', 'view']:
            return json.dumps(dict(nameWithOwner=self.fork))
        if rest[:1] == ['api']:
            if '--method' in rest and rest[rest.index('--method') + 1] == 'DELETE':
                endpoint = rest[-1]
                self.refs.pop(endpoint.rsplit('/', 1)[-1], None)
                return ''
            value = self.api(rest[1], json.loads(input) if input else None)
            return json.dumps([value] if '--slurp' in rest else value)
        if rest[:2] == ['pr', 'view']:
            raw = self.prs[int(rest[2])]
            case = 'example' if raw['title'].endswith('v1.0.0') else 'control'
            view = deepcopy(self.pr_views[case])
            view.update(number=raw['number'], title=raw['title'],
                        state='MERGED' if raw['merged_at'] else raw['state'].upper(),
                        mergedAt=raw['merged_at'], url=raw['html_url'])
            return json.dumps(view)
        if rest[:2] == ['pr', 'close']:
            self.prs[int(rest[2])]['state'] = 'closed'
            return ''
        if rest[:2] == ['run', 'list']:
            workflow = rest[rest.index('--workflow') + 1]
            if workflow == 'microclaw-release.yml':
                return json.dumps([self.run('release')])
            if workflow == 'policy-reminder.yml':
                return json.dumps([self.run('reminder')])
            assert workflow == 'intake.yml'
            return json.dumps([self.run('example' if p['merged_at'] else 'control', p['title']) for p in self.prs.values()])
        if rest[:2] == ['run', 'view']:
            number = int(rest[2])
            case = 'example' if number == 10 else 'control'
            pr = next(p for p in self.prs.values() if (p['title'].endswith('v1.0.0')) == (case == 'example'))
            path = self.blobs[pr['head']['sha']]['path']
            return 'Intake\tPR changes materialized as data against the current base: A ' + path
        if rest[:2] == ['issue', 'list']:
            return '[]'
        raise AssertionError('Unexpected gh invocation: ' + repr(rest))

    def api(self, endpoint, payload=None):
        if endpoint == 'user':
            return dict(login='offline-operator')
        if endpoint == 'repos/' + self.fork:
            return dict(fork=True, parent=dict(full_name=REPO))
        if endpoint == f'repos/{EXAMPLE}/releases/tags/{TAG}':
            return self.release
        if endpoint.endswith('/git/ref/heads/main'):
            return dict(object=dict(sha=self.base))
        if '/git/ref/heads/' in endpoint:
            branch = endpoint.rsplit('/', 1)[-1]
            if branch not in self.refs:
                raise RuntimeError('HTTP 404: ref not found')
            return dict(object=dict(sha=self.refs[branch]))
        if '/git/commits/' in endpoint:
            return dict(tree=dict(sha=self.base))
        if endpoint.endswith('/git/trees'):
            sha = f'{len(self.trees) + 20:040x}'
            self.trees[sha] = payload['tree'][0]
            return dict(sha=sha)
        if endpoint.endswith('/git/commits'):
            sha = f'{len(self.commits) + 30:040x}'
            self.blobs[sha] = self.trees[payload['tree']]
            self.commits[sha] = dict(sha=sha, parents=[dict(sha=p) for p in payload['parents']])
            return self.commits[sha]
        if endpoint.endswith('/git/refs'):
            self.refs[payload['ref'].removeprefix('refs/heads/')] = payload['sha']
            return dict(ref=payload['ref'], object=dict(sha=payload['sha']))
        if endpoint == f'repos/{REPO}/pulls?state=open&base=main&per_page=100':
            return [deepcopy(p) for p in self.prs.values() if p['state'] == 'open' and not p['merged_at']]
        if endpoint.endswith('/pulls') and payload is not None:
            branch = payload['head'].split(':')[1]
            self.created_prs += 1
            example = payload['title'].endswith('v1.0.0')
            pr = dict(number=self.created_prs, title=payload['title'], html_url=f'https://github.com/{REPO}/pull/{self.created_prs}',
                state='closed' if example else 'open', created_at=self.stamp, merged_at=self.stamp if example else None,
                merged_by=dict(login='github-actions[bot]') if example else None,
                merge_commit_sha=self.merge if example else None, base=dict(ref='main'),
                head=dict(ref=branch, sha=self.refs[branch], repo=dict(full_name=self.fork)))
            self.prs[pr['number']] = pr
            if example:
                self.catalog['releases'] = [self.entry]
            return deepcopy(pr)
        if endpoint == f'repos/{REPO}/commits/{self.merge}/pulls':
            return [deepcopy(p) for p in self.prs.values() if p['merged_at']]
        if '/pulls/' in endpoint:
            number = int(endpoint.split('/pulls/')[1].split('/')[0])
            if endpoint.endswith('/files'):
                return [dict(filename=self.blobs[self.prs[number]['head']['sha']]['path'], status='added')]
            return deepcopy(self.prs[number])
        if '/contents/' in endpoint:
            path, query = endpoint.split('/contents/')[1].split('?ref=')
            if query == 'main':
                if self.entry not in self.catalog['releases']:
                    raise RuntimeError('HTTP 404: file not found')
                value = self.entry
            else:
                blob = self.blobs[self.refs[query]]
                assert blob['path'] == path
                value = json.loads(blob['content'])
            return dict(content=base64.b64encode(intake.encode(value)).decode('ascii'))
        if '/commits?path=' in endpoint:
            if 'path=policy.json' in endpoint:
                return [dict(sha='d' * 40, commit=dict(committer=dict(date=self.stamp)))]
            return [dict(sha=self.merge)]
        if '/compare/' in endpoint:
            return dict(commits=[dict(sha=self.rebuild)])
        if endpoint.endswith('/commits/' + self.rebuild):
            return dict(sha=self.rebuild, parents=[dict(sha=self.merge)], files=[dict(filename='catalog.json')],
                        commit=dict(message='Rebuild verified package catalog', committer=dict(date=self.stamp)))
        raise AssertionError('Unexpected API endpoint: ' + endpoint)

    def download(self, asset, target):
        Path(target).write_bytes(intake.encode(self.entry) if asset['name'] == 'release.json' else self.artifact)

    def open(self, request, timeout):
        url = request.full_url if hasattr(request, 'full_url') else request
        assert url.startswith(store.CATALOG_URL) and timeout > 0, url
        name = url[len(store.CATALOG_URL):]
        assert name in ('policy.json', 'catalog.json'), name
        return demo.DiskResponse(intake.encode(self.policy if name == 'policy.json' else self.catalog))


def selftest():
    """Only policy cryptography is stubbed: the operator's offline roots cannot
    sign a test. The stub demands the shipped production roots and exact bytes.
    Publisher signatures, packs, store fetch/install and history parsing are real."""
    from contextlib import ExitStack, redirect_stdout
    import io
    from unittest.mock import patch
    from microclaw.conversation import AuditLog
    current_roots = shipped_roots()
    assert len(packages._keys(current_roots['keys'], 'roots.keys')) == 2
    with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack, redirect_stdout(io.StringIO()):
        base = Path(temporary)
        root = base / 'microclaw'
        evidence = base / 'evidence'
        root.mkdir()
        evidence.mkdir()
        key = evidence / 'publisher.pem'
        catalog_gate.product('keygen', '--out', key)
        url = f'https://github.com/{EXAMPLE}/releases/download/{TAG}/{ZIP}'
        catalog_gate.product('pack', '--dir', HERE / '83f5-example-package/package', '--url', url, '--out', evidence / ZIP)
        catalog_gate.product('sign-release', '--key', key, '--artifact', evidence / ZIP, '--url', url, '--out', evidence / 'release.json')
        entry = read(evidence / 'release.json')
        from datetime import timedelta
        policy = dict(type=packages.TRUST_POLICY_TYPE, environment='production', revision=2,
            expires_at=(demo.now() + timedelta(days=182)).strftime('%Y-%m-%dT%H:%M:%SZ'),
            publishers={'microclaw-examples': dict(state='active', keys=[intake.public_entry(intake.load_key(key))])},
            revoked_releases=[], signature=dict(alg='ed25519', key_id=current_roots['keys'][0]['key_id'], value='offline-policy-stub'))
        fake = FakeGitHub(base, entry, (evidence / ZIP).read_bytes(), policy)
        fake.real_command = catalog_gate.command
        def verify_policy(document, roots=None, **kwargs):
            roots = packages.PRODUCTION_ROOTS if roots is None else roots
            assert roots == current_roots, 'selftest must use the shipped production roots'
            assert document == policy, 'policy bytes changed or unverified'
            return deepcopy(document)
        # All HTTP and gh calls are intercepted; unexpected requests refuse.
        stack.enter_context(patch.object(catalog_gate, 'command', fake.command))
        stack.enter_context(patch.object(catalog_gate.shutil, 'which', lambda name: '/offline/gh' if name == 'gh' else None))
        stack.enter_context(patch.object(urllib.request, 'urlopen', fake.open))
        stack.enter_context(patch.object(store.updates, '_default_opener', fake.open))
        stack.enter_context(patch.object(store, 'user_data_dir', lambda: root))
        stack.enter_context(patch.object(store, 'store_dir', lambda: store.user_data_dir() / 'skill-packages'))
        stack.enter_context(patch.object(packages, 'PRODUCTION_ROOTS', current_roots))
        stack.enter_context(patch.object(packages, 'verify_trust_policy', verify_policy))
        # Use function globals, so import-by-path (pytest) needs no sys.modules registration.
        stack.enter_context(patch.dict(globals(), download_asset=fake.download, slot_roots=lambda: deepcopy(current_roots)))
        gate = Gate(root, evidence)
        assert publish(gate, 1) == 0
        assert fake.created_prs == 2
        assert publish(gate, 1) == 0
        assert fake.created_prs == 2, 'partial rerun submitted a permanent release twice'
        # Before intake settles, the accepted PR must also be reused.
        example_pr = next(p for p in fake.prs.values() if p['merged_at'])
        merged_at, old_state = example_pr['merged_at'], example_pr['state']
        example_pr.update(merged_at=None, state='open')
        state = gate.artifact('state.json')
        open_case(gate, state, 'example', entry)
        assert fake.created_prs == 2
        example_pr.update(merged_at=merged_at, state=old_state)
        # A new evidence folder rediscovers both the merged release and open control.
        rediscovered = Gate(root, base / 'rediscovered')
        assert publish(rediscovered, 1) == 0 and fake.created_prs == 2
        gate.prepare()
        # Installed-slot mismatch: pre-root tree is read as source, not imported.
        before = fake.real_command(
            ['git', 'show', '54a07d6:microclaw/skill_packages.py'], cwd=HERE.parent)
        pre_roots = shipped_roots(before)
        assert pre_roots['keys'] == []
        with patch.dict(globals(), slot_roots=lambda: pre_roots):
            try:
                gate.prepare()
                raise AssertionError('pre-root installed slot was accepted')
            except NotExercised as exc:
                assert 'install.bat' in str(exc)
        override = gate.store_root / 'trust/roots.json'
        write(override, dict(environment='test', keys=[]))
        try:
            gate.prepare()
            raise AssertionError('trust override was accepted')
        except NotExercised as exc:
            assert 'Remove-Item -LiteralPath' in str(exc)
        override.unlink()
        gate.prepare()
        audit = AuditLog(root / 'selftest_microclaw_history.jsonl')
        class Operator:
            def launch(self):
                store.refresh_catalog(now=demo.now(), opener=fake.open)
                (root / 'launcher.log').write_text('launch slot=a nonce=selftest\n', encoding='utf-8')
                (root / 'launch-health.txt').write_text('selftest', encoding='ascii')
            def answer(self, key):
                if key in ('1', '4'):
                    tool = 'search_skill_catalog' if key == '1' else 'load_skill'
                    arguments = dict(query='session') if key == '1' else dict(name=QUALIFIED)
                    from microclaw import tools
                    call_id = 'selftest-' + key
                    use = dict(role='assistant', content=[dict(type='tool_use', id=call_id, name=tool, input=arguments)])
                    audit.append(use)
                    # Actual product tool results; the microscope bridge is not needed.
                    result = tools.execute_tool(tool, arguments, None, None)
                    audit.append(dict(role='user', content=[dict(type='tool_result', tool_use_id=call_id, content=result)]))
                elif key == '2':
                    (evidence / 'screenshot-catalog.png').write_bytes(png)
                elif key == '3':
                    store.install(entry, evidence / ZIP, policy=policy, now=demo.now(), retained_digests=frozenset())
                    (evidence / 'screenshot-installed.png').write_bytes(png)
                else:
                    raise AssertionError(key)
                return 'DONE'
        # A real tiny PNG (IHDR, IDAT, IEND), not a filename-only screenshot fake.
        def png_chunk(kind, data):
            return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
        png = (b'\x89PNG\r\n\x1a\n' + png_chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 0, 0, 0, 0))
               + png_chunk(b'IDAT', zlib.compress(b'\x00\x00')) + png_chunk(b'IEND', b''))
        gate.fake = Operator()
        gate.session()
        assert verify(gate, collect=False, opener=fake.open) == 0, gate.last_results
        assert gate.last_results['L10'][0] == demo.JUDGED
        assert all(v[0] == 'PASS' for k, v in gate.last_results.items() if k != 'L10')
        # A prior failed attempt for the same digest must not break a partial-run retry.
        snapshot_root = evidence / 'snapshots/3/packages/session-start/installs'
        installed = read(next(snapshot_root.glob('*/install.json')))
        prior = snapshot_root / 'failed-old/install.json'
        write(prior, dict(installed, install_id='failed-old', state='failed'))
        assert verify(gate, collect=False, opener=fake.open) == 0
        shutil.rmtree(prior.parent)
        backup = base / 'pristine'
        shutil.copytree(evidence, backup)
        mutations = {
            'L1': ('release-runs.json', lambda d: d[0].update(conclusion='failure')),
            'L2': ('release.json', lambda d: d.update(artifact_digest='0' * 64)),
            'L3': ('example-api-pr.json', lambda d: d['merged_by'].update(login='human')),
            'L4': ('control-pr.json', lambda d: d.update(comments=[])),
            'L5': ('policy-commits.json', lambda d: d[0]['commit']['committer'].update(date='2000-01-01T00:00:00Z')),
            'L7': ('snapshots/2/catalog-status.json', lambda d: d.update(last_success=None)),
            'L8': ('snapshots/3/packages/session-start/installs/*/install.json', lambda d: d.update(state='failed')),
            'L11': ('reminder-issues.json', lambda d: d.append(dict(title='Renew catalog trust policy'))),
        }
        killed = 0
        for label in [f'L{i}' for i in range(1, 12)]:
            if label in mutations:
                relative, edit = mutations[label]
                path = next(evidence.glob(relative))
                document = read(path)
                edit(document)
                write(path, document)
            elif label == 'L6':
                fake.catalog['releases'] = []
            elif label == 'L9':
                history = next((evidence / 'snapshots/4/history').glob('*.jsonl'))
                demo.gate83e3._edit_result(history.parent, 'selftest-4', lambda d: d.update(text='no provenance'))
            else:
                (evidence / 'screenshot-installed.png').write_bytes(b'not PNG')
            assert verify(gate, collect=False, opener=fake.open) != 0
            assert gate.last_results[label][0] == 'FAIL', (label, gate.last_results[label])
            killed += 1
            fake.catalog['releases'] = [entry]
            shutil.rmtree(evidence)
            shutil.copytree(backup, evidence)
        # Additional controls reach the mechanisms that an absence-only check misses.
        log = evidence / 'control-run-11.log'
        log.write_text(log.read_text(encoding='utf-8') + '\nDownloading ' + read(evidence / 'control.json')['artifact'], encoding='utf-8')
        assert verify(gate, collect=False, opener=fake.open) != 0 and gate.last_results['L4'][0] == 'FAIL'
        shutil.rmtree(evidence)
        shutil.copytree(backup, evidence)
        write(evidence / 'after-merge-commits.json', [])
        assert verify(gate, collect=False, opener=fake.open) != 0 and gate.last_results['L3'][0] == 'FAIL'
        shutil.rmtree(evidence)
        shutil.copytree(backup, evidence)
        for name in ('release-runs.json', 'control-runs.json', 'reminder-runs.json'):
            saved = read(evidence / name)
            write(evidence / name, [])
            assert verify(gate, collect=False, opener=fake.open) != 0
            limb = {'release-runs.json': 'L1', 'control-runs.json': 'L4', 'reminder-runs.json': 'L11'}[name]
            assert gate.last_results[limb][0] == 'NOT EXERCISED'
            write(evidence / name, saved)
        # No phase artifacts: all limbs remain independent and none can silently pass.
        absent = Gate(root, base / 'absent')
        assert verify(absent, collect=False, opener=fake.open) != 0
        assert len(absent.last_results) == 11 and all(v[0] == 'NOT EXERCISED' for v in absent.last_results.values())
        gate.cleanup()
        gate.cleanup()
        assert next(p for p in fake.prs.values() if not p['merged_at'])['state'] == 'closed'
        assert next(p for p in fake.prs.values() if p['merged_at'])['merged_at']
        assert gate.store_root.exists() and fake.catalog['releases'] == [entry]
        # Replay the reused DONE-only and non-empty prompt contract without a scripted operator.
        gate.fake = None
        lines = iter(['pasted reply', '', 'DONE'])
        with patch('builtins.input', lambda _: next(lines)):
            assert gate.ask('Finish the chat turn.', 'input-control') == 'DONE'
        lines = iter(['', 'panel text'])
        with patch('builtins.input', lambda _: next(lines)):
            assert gate.ask('Type panel text.', 'text-control', kind='text') == 'panel text'
    print('SELFTEST: publish/prepare/session/verify/cleanup passed with offline gh and HTTP fakes.')
    print('SELFTEST: merged-release and open-PR reruns created no duplicate PRs; pre-root slot and trust override refused.')
    print(f'SELFTEST: 11 limbs scored independently; {killed} per-limb mutations killed; download/rebuild and missing-artifact/run controls passed.')
    print('SELFTEST: L10 OPERATOR-JUDGED; DONE-only/non-empty prompts passed; installed example and merged history retained.')
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=PHASES)
    parser.add_argument('--out', required=False)
    parser.add_argument('--deadline', type=int, default=900)
    args = parser.parse_args(argv)
    if args.phase == 'selftest':
        return selftest()
    root = store.user_data_dir()
    pointer = root / POINTER
    out = Path(args.out) if args.out else (Path(pointer.read_text(encoding='utf-8').strip()) if pointer.exists()
        else root.parent / ('block83f5-' + demo.now().strftime('%Y%m%d-%H%M%S')))
    gate = Gate(root, out)
    gate.say('Evidence: ' + str(out))
    root.mkdir(parents=True, exist_ok=True)
    pointer.write_text(str(out), encoding='utf-8')
    try:
        if args.phase == 'publish':
            return publish(gate, args.deadline)
        if args.phase == 'verify':
            return verify(gate)
        getattr(gate, args.phase)()
        return 0
    except Exception as exc:
        gate.say(f'{args.phase}: {"NOT EXERCISED" if isinstance(exc, NotExercised) else "FAIL"}: {exc}')
        gate.save(args.phase, dict(phase_error=str(exc)))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
