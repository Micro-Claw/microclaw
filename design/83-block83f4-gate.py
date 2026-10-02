"""83f-4 fork/PR gate. Fixtures and selftest are offline; run is operator-only."""
from __future__ import annotations
import argparse
import base64
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from microclaw import catalog_intake as intake
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_spec = importlib.util.spec_from_file_location('gate83f3', HERE / '83-block83f3-demo-gate.py')
previous = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(previous)
# Reuse the established response and isolation contracts. No prompts are needed.
isolated_store, disk_opener = previous.isolated_store, previous.disk_opener
FIXTURES = HERE / '83f4-gate'
SEED = HERE / '83f4-catalog-repo'
TRUST = HERE.parent / 'tests/fixtures/skill_packages/trust'
BASE = 'https://raw.githubusercontent.com/Micro-Claw/microclaw/block-83f-4/design/83f4-gate/'
CATALOG = 'https://raw.githubusercontent.com/Micro-Claw/package-catalog/test/'
REPO = 'Micro-Claw/package-catalog'
PREFIX = '83f-4 gate:'
MARKER = '83F4_HEAD_WORKFLOW_EXECUTED'
CASES = {'A': (True, None), 'B': (False, 'artifact_digest'), 'C': (False, 'publisher'),
         'D': (False, 'path'), 'E': (True, None), 'F': (False, 'publisher'),
         'G': (False, 'path'), 'H': (False, 'path'), 'I': (True, None)}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(intake.encode(value))


def command(args, *, cwd=None, input=None):
    return subprocess.run(args, cwd=cwd, input=input, stdin=subprocess.DEVNULL if input is None else None,
                          capture_output=True, text=True, check=True).stdout.strip()


def gh(*args, payload=None):
    return command(['gh', *args], input=json.dumps(payload) if payload is not None else None)


def api(endpoint, payload=None):
    args = ['api', endpoint]
    if payload is not None:
        args += ['--method', 'POST', '--input', '-']
    return json.loads(gh(*args, payload=payload))


def product(*args):
    # The gate exercises the publisher's actual CLI, never a gate-local signer.
    return command([sys.executable, '-m', 'microclaw.catalog_intake', *map(str, args)], cwd=HERE.parent)


def fixtures():
    builder = previous.gate83d.load_builder()
    FIXTURES.mkdir(exist_ok=True)
    (FIXTURES / 'artifacts').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        temporary = Path(temporary)
        keys = {}
        for name in ('publisher-a', 'publisher-b'):
            key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(read(TRUST / f'{name}-TEST-ONLY-seed.json')['seed']))
            keys[name] = temporary / (name + '.pem')
            keys[name].write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        releases = {}
        for label, publisher, package in [('A', 'fixture-lab', 'gate-good'), ('B', 'fixture-lab', 'gate-tamper'),
                                          ('C', 'unknown-lab', 'gate-unknown'), ('D', 'fixture-lab', 'gate-good'),
                                          ('I', 'fixture-lab', 'gate-stale')]:
            source = temporary / label
            shutil.copytree(previous.gate83d.FIXTURES / 'markdown', source)
            manifest = read(source / 'manifest.json')
            manifest.update(publisher=publisher, package_id=package, artifact=BASE + f'artifacts/{label}.zip')
            if label == 'D':
                manifest['skills'][0]['description'] = 'Different digest, same version'
            write(source / 'manifest.json', manifest)
            artifact = FIXTURES / 'artifacts' / f'{label}.zip'
            builder.build_release(source, artifact)
            out = FIXTURES / f'{label}.json'
            product('sign-release', '--key', keys['publisher-a'], '--artifact', artifact,
                    '--url', manifest['artifact'], '--out', out)
            releases[label] = read(out)
            if label == 'B':
                artifact.write_bytes(artifact.read_bytes() + b'tampered after signing')
        product('sign-withdrawal', '--key', keys['publisher-a'], '--release', FIXTURES / 'A.json',
                '--reason', 'Gate withdrawal', '--out', FIXTURES / 'E.json')
        # Admitted publisher two signs a withdrawal with A's digest but its own name.
        other = deepcopy(releases['A'])
        other['publisher'] = 'fixture-two'
        write(temporary / 'other.json', other)
        product('sign-withdrawal', '--key', keys['publisher-b'], '--release', temporary / 'other.json',
                '--reason', 'Gate cross-publisher withdrawal', '--out', FIXTURES / 'F.json')
    policy = read(TRUST / 'policy-TEST-ONLY.json')
    a, b = policy['publishers']['fixture-lab']['keys']
    policy['publishers'] = {'fixture-lab': dict(state='active', keys=[a]), 'fixture-two': dict(state='active', keys=[b])}
    policy = builder.sign(policy, 'root')
    write(SEED / 'test-branch/policy.json', policy)
    write(SEED / 'test-branch/roots-TEST-ONLY.json', read(TRUST / 'roots-TEST-ONLY.json'))
    write(SEED / 'test-branch/catalog.json', dict(type='microclaw.catalog.v1', releases=[], withdrawals=[]))
    print('Fixtures generated with product signing commands; TEST-ONLY public seeds.')


class Gate:
    def __init__(self, evidence):
        self.directory = Path(evidence)
        self.directory.mkdir(parents=True, exist_ok=True)

    def log(self, text):
        print(text, flush=True)
        with (self.directory / 'gate.log').open('a', encoding='utf-8') as stream:
            stream.write(datetime.now(timezone.utc).isoformat() + ' ' + text + '\n')

    def save(self, name, value):
        write(self.directory / name, value)

    def capture(self, case):
        state = read(self.directory / 'state.json')
        number = state['prs'][case]['number']
        pr = json.loads(gh('pr', 'view', str(number), '--repo', REPO, '--json', 'number,title,state,mergedAt,comments,url'))
        runs = json.loads(gh('run', 'list', '--repo', REPO, '--workflow', 'intake.yml', '--event', 'pull_request_target',
                             '--limit', '100', '--json', 'databaseId,displayTitle,status,conclusion,headSha,url,createdAt'))
        # run-name equals the PR title; nonce prevents a previous gate's run being scored.
        runs = [r for r in runs if r['displayTitle'] == state['prs'][case]['title']]
        self.save(f'{case}-pr.json', pr)
        self.save(f'{case}-runs.json', runs)
        return pr, runs


def create_branch(fork, base, branch, title, path, content):
    base_tree = api(f'repos/{fork}/git/commits/{base}')['tree']['sha']
    tree = api(f'repos/{fork}/git/trees', dict(base_tree=base_tree,
               tree=[dict(path=path, mode='100644', type='blob', content=content)]))
    commit = api(f'repos/{fork}/git/commits', dict(message=title, tree=tree['sha'], parents=[base]))
    api(f'repos/{fork}/git/refs', dict(ref='refs/heads/' + branch, sha=commit['sha']))
    return commit


def run(gate, deadline_seconds):
    if (gate.directory / 'state.json').exists():
        raise RuntimeError('Evidence folder already has a run; choose a fresh --evidence folder')
    owner = api('user')['login']
    try:
        gh('repo', 'view', owner + '/package-catalog', '--json', 'nameWithOwner')
    except subprocess.CalledProcessError:
        gh('repo', 'fork', REPO, '--clone=false')
    fork = owner + '/package-catalog'
    info = api('repos/' + fork)
    if not info.get('fork') or info.get('parent', {}).get('full_name') != REPO:
        raise RuntimeError('The existing repository is not a fork of ' + REPO)
    nonce = datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')
    state = dict(fork=fork, nonce=nonce, prs={})
    gate.save('state.json', state)
    # Create I now, from the pre-A base. Opening it later must not rebuild its tree.
    gh('repo', 'sync', fork, '--branch', 'test')
    initial_base = api(f'repos/{REPO}/git/ref/heads/test')['object']['sha']
    stale = read(FIXTURES / 'I.json')
    stale_branch = f'83f4-{nonce}-I'
    stale_commit = create_branch(fork, initial_base, stale_branch, f'{PREFIX} {nonce} I',
                                 intake.record_path('releases', stale), intake.encode(stale).decode('ascii'))
    state['prepared'] = dict(branch=stale_branch, sha=stale_commit['sha'], base=initial_base)
    gate.save('I-creation.json', stale_commit)
    gate.save('state.json', state)
    a_completed = False
    for case, (accepted, field) in CASES.items():
        if case in ('D', 'E', 'I') and not a_completed:
            gate.log(f'{case}: A did not merge; NOT EXERCISED')
            continue
        # E and D follow A; every other refusal is independent of prior traffic.
        gh('repo', 'sync', fork, '--branch', 'test')
        base = api(f'repos/{REPO}/git/ref/heads/test')['object']['sha']
        branch = f'83f4-{nonce}-{case}'
        title = f'{PREFIX} {nonce} {case}'
        if case in 'ABCDEFI':
            value = read(FIXTURES / f'{case}.json')
            collection = 'withdrawals' if case in 'EF' else 'releases'
            path = intake.record_path(collection, value)
            content = intake.encode(value).decode('ascii')
        elif case == 'G':
            path, content = 'catalog.json', '{"type":"edited-by-pr","releases":[],"withdrawals":[]}\n'
        else:
            path = '.github/workflows/intake.yml'
            content = ('name: Hostile head\non: pull_request_target\npermissions:\n  pull-requests: write\njobs:\n'
                       '  marker:\n    runs-on: ubuntu-latest\n    steps:\n      - env:\n'
                       '          GH_TOKEN: ${{ github.token }}\n        run: gh pr comment ${{ github.event.pull_request.number }} '
                       '--repo ${{ github.repository }} --body ' + MARKER + '\n')
        if case == 'I':
            commit = stale_commit
        else:
            commit = create_branch(fork, base, branch, title, path, content)
            gate.save(f'{case}-creation.json', commit)
        pr = api(f'repos/{REPO}/pulls', dict(title=title, head=owner + ':' + branch, base='test', body='Automated 83f-4 fixture gate.'))
        state['prs'][case] = dict(number=pr['number'], title=title, branch=branch, sha=commit['sha'])
        gate.save('state.json', state)
        gate.log(f'{case}: opened {pr["html_url"]}')
        deadline = time.monotonic() + deadline_seconds
        while time.monotonic() < deadline:
            observed, runs = gate.capture(case)
            complete = any(r['status'] == 'completed' for r in runs)
            settled = bool(observed['mergedAt']) if accepted else any(field in c['body'] for c in observed['comments'])
            if complete and settled:
                break
            time.sleep(10)
        else:
            gate.log(f'{case}: deadline reached; verify will score available evidence')
        # Rebuild happens after merging; wait for the whole run, not just mergedAt.
        if case == 'A':
            if not observed.get('mergedAt') or not any(r['status'] == 'completed' and r['conclusion'] == 'success' for r in runs):
                gate.log('A did not complete; dependent D/E/I will be NOT EXERCISED')
            else:
                a_completed = True
    # Raw GitHub can lag the completed merge/rebuild. Wait for the actual bytes.
    expected = {}
    for label, bucket in (('A', 'releases'), ('E', 'withdrawals'), ('I', 'releases')):
        pr_path = gate.directory / f'{label}-pr.json'
        if pr_path.exists() and read(pr_path).get('mergedAt'):
            expected.setdefault(bucket, []).append(read(FIXTURES / f'{label}.json'))
    deadline = time.monotonic() + deadline_seconds
    while True:
        collect_served(gate)
        served = gate.directory / 'served-catalog.json'
        if served.exists() and all(entry in read(served).get(bucket, []) for bucket, entries in expected.items() for entry in entries):
            break
        if not expected or time.monotonic() >= deadline:
            gate.log('Served catalog deadline reached; scoring captured bytes')
            break
        time.sleep(10)
    return verify(gate)


def collect_served(gate):
    for name in ('policy.json', 'catalog.json'):
        target = gate.directory / ('served-' + name)
        target.unlink(missing_ok=True)
        try:
            with urllib.request.urlopen(CATALOG + name, timeout=30) as response:
                gate.save('served-' + name, json.load(response))
        except Exception as exc:
            gate.log('Could not capture served ' + name + ': ' + str(exc))


def score(prs, runs, catalog, expected):
    """Fields are gh pr view/run list's documented JSON shape, not API aliases."""
    result = {}
    for case, (accepted, field) in CASES.items():
        pr, executions = prs.get(case), runs.get(case, [])
        if pr is None or not executions:
            result[case] = 'NOT EXERCISED'
            continue
        if not any(r.get('status') == 'completed' for r in executions):
            result[case] = 'NOT EXERCISED'
            continue
        success = any(r.get('status') == 'completed' and r.get('conclusion') == 'success' for r in executions)
        if accepted:
            good = pr.get('state') == 'MERGED' and bool(pr.get('mergedAt'))
        else:
            good = pr.get('state') == 'OPEN' and not pr.get('mergedAt') and any(
                '**' + field + '**' in c.get('body', '') for c in pr.get('comments', []))
        result[case] = 'PASS' if success and good else 'FAIL'
    # Served data and hostile marker are separate limbs, independent of PR verdict.
    result['A-served'] = ('NOT EXERCISED' if catalog is None or result['A'] == 'NOT EXERCISED' else
                          'PASS' if expected['A'] in catalog.get('releases', []) else 'FAIL')
    result['E-served'] = ('NOT EXERCISED' if catalog is None or result['E'] == 'NOT EXERCISED' else
                          'PASS' if expected['E'] in catalog.get('withdrawals', []) else 'FAIL')
    result['I-served'] = ('NOT EXERCISED' if catalog is None or result['I'] == 'NOT EXERCISED' else
                          'PASS' if expected['I'] in catalog.get('releases', []) else 'FAIL')
    h = prs.get('H')
    result['H-base-workflow'] = 'NOT EXERCISED'
    if h and any(r.get('status') == 'completed' for r in runs.get('H', [])):
        # Require the base intake refusal as well as absence: absence alone proves nothing.
        bodies = [c.get('body', '') for c in h.get('comments', [])]
        result['H-base-workflow'] = 'PASS' if result['H'] == 'PASS' and not any(MARKER in b for b in bodies) else 'FAIL'
    return result


def client_fetch(gate, *, opener=None):
    with tempfile.TemporaryDirectory() as temporary, isolated_store(temporary) as store:
        roots = dict(read(SEED / 'test-branch/roots-TEST-ONLY.json'), catalog_url=CATALOG)
        write(store.store_dir() / 'trust/roots.json', roots)
        state = store.refresh_catalog(now=datetime.now(timezone.utc), opener=opener)
        catalog = store.catalog_entries(now=datetime.now(timezone.utc))
        gate.save('client-state.json', state)
        gate.save('client-catalog.json', catalog)
        expected = read(FIXTURES / 'A.json')
        good = bool(state.get('last_success')) and not state.get('error') and not catalog['exclusions']
        good = good and any(all(r[k] == expected[k] for k in intake.IDENTITY) and r['withdrawn'] for r in catalog['releases'])
        stale = read(FIXTURES / 'I.json')
        good = good and any(all(r[k] == stale[k] for k in intake.IDENTITY) for r in catalog['releases'])
        return 'PASS' if good else 'FAIL'


def verify(gate, *, collect=False, opener=None):
    if collect:
        state = read(gate.directory / 'state.json')
        for case in state['prs']:
            try:
                gate.capture(case)
            except Exception as exc:
                gate.log(f'{case}: capture failed: {exc}')
                (gate.directory / f'{case}-pr.json').unlink(missing_ok=True)
                (gate.directory / f'{case}-runs.json').unlink(missing_ok=True)
        collect_served(gate)
    prs, runs = {}, {}
    for case in CASES:
        path = gate.directory / f'{case}-pr.json'
        if path.exists():
            prs[case] = read(path)
        path = gate.directory / f'{case}-runs.json'
        if path.exists():
            runs[case] = read(path)
    catalog_path = gate.directory / 'served-catalog.json'
    catalog = read(catalog_path) if catalog_path.exists() else None
    result = score(prs, runs, catalog, {c: read(FIXTURES / f'{c}.json') for c in ('A', 'E', 'I')})
    result['client-fetch'] = 'NOT EXERCISED'
    if catalog is not None:
        try:
            result['client-fetch'] = client_fetch(gate, opener=opener)
        except Exception as exc:
            gate.log('Client fetch failed: ' + str(exc))
            result['client-fetch'] = 'FAIL'
    gate.save('scores.json', result)
    for limb, verdict in result.items():
        gate.log(f'{limb}: {verdict}')
    return 0 if all(v == 'PASS' for v in result.values()) else 1


def cleanup(gate):
    state = read(gate.directory / 'state.json')
    for case, pr in state['prs'].items():
        current = json.loads(gh('pr', 'view', str(pr['number']), '--repo', REPO, '--json', 'state'))
        if current['state'] == 'OPEN':
            gh('pr', 'close', str(pr['number']), '--repo', REPO)
        gate.log(f'{case}: closed if open')
    branches = {pr['branch'] for pr in state['prs'].values()}
    if 'prepared' in state:
        branches.add(state['prepared']['branch'])
    for branch in sorted(branches):
        try:
            gh('api', '--method', 'DELETE', f'repos/{state["fork"]}/git/refs/heads/{branch}')
        except subprocess.CalledProcessError as exc:
            if '404' not in exc.stderr and '422' not in exc.stderr:
                raise
        gate.log(branch + ': fork branch removed')


def selftest():
    # gh documented output: state is OPEN/MERGED, mergedAt ISO timestamp/null,
    # comments are nodes with body; run list uses status/conclusion/displayTitle.
    fixtures()
    expected = {c: read(FIXTURES / f'{c}.json') for c in ('A', 'E', 'I')}
    prs, runs = {}, {}
    for case, (accepted, field) in CASES.items():
        prs[case] = dict(number=100 + ord(case), title=PREFIX + ' fake ' + case,
                         state='MERGED' if accepted else 'OPEN', mergedAt='2026-10-02T10:00:00Z' if accepted else None,
                         comments=[] if accepted else [dict(id='IC_fake', author=dict(login='github-actions'),
                             body='Refused: field **' + field + '**. Correct this field.', createdAt='2026-10-02T10:00:00Z',
                             url='https://github.com/Micro-Claw/package-catalog/pull/1#issuecomment-1')],
                         url='https://github.com/Micro-Claw/package-catalog/pull/1')
        runs[case] = [dict(databaseId=100 + ord(case), displayTitle=prs[case]['title'], status='completed',
                           conclusion='success', headSha='0' * 40, createdAt='2026-10-02T10:00:00Z',
                           url='https://github.com/Micro-Claw/package-catalog/actions/runs/1')]
    catalog = dict(type='microclaw.catalog.v1', releases=[expected['A'], expected['I']], withdrawals=[expected['E']])
    control = score(prs, runs, catalog, expected)
    assert all(v == 'PASS' for v in control.values()), control
    mutations = 0
    for limb in control:
        p, r, c = deepcopy(prs), deepcopy(runs), deepcopy(catalog)
        if limb in CASES:
            if CASES[limb][0]:
                p[limb]['mergedAt'] = None
            else:
                p[limb]['comments'] = []
        elif limb == 'A-served':
            c['releases'] = []
        elif limb == 'I-served':
            c['releases'] = [expected['A']]
        elif limb == 'E-served':
            c['withdrawals'] = []
        else:
            p['H']['comments'].append(dict(body=MARKER))
        assert score(p, r, c, expected)[limb] == 'FAIL', limb
        mutations += 1
    for case in CASES:
        missing = deepcopy(runs)
        missing[case] = []
        absent = score(prs, missing, catalog, expected)
        assert absent[case] == 'NOT EXERCISED'
        if case in ('A', 'E', 'I'):
            assert absent[case + '-served'] == 'NOT EXERCISED'
        failed = deepcopy(runs)
        failed[case][0]['conclusion'] = 'failure'
        assert score(prs, failed, catalog, expected)[case] == 'FAIL'
    with tempfile.TemporaryDirectory() as temporary:
        gate = Gate(temporary)
        # Reuse 83f-3's disk_opener unchanged by mapping our served URL to its BASE.
        disk = Path(temporary) / 'disk'
        write(disk / 'catalog.json', catalog)
        write(disk / 'policy.json', read(SEED / 'test-branch/policy.json'))
        original = disk_opener(disk)
        def opener(request, timeout):
            assert request.full_url.startswith(CATALOG)
            rewritten = urllib.request.Request(previous.BASE + request.full_url[len(CATALOG):])
            return original(rewritten, timeout)
        assert client_fetch(gate, opener=opener) == 'PASS'
        # Mutation control for client limb: valid served catalog omits withdrawal.
        write(disk / 'catalog.json', dict(catalog, withdrawals=[]))
        assert client_fetch(gate, opener=opener) == 'FAIL'
        mutations += 1
        for case in CASES:
            gate.save(f'{case}-pr.json', prs[case])
            gate.save(f'{case}-runs.json', runs[case])
        gate.save('served-catalog.json', catalog)
        write(disk / 'catalog.json', catalog)
        assert verify(gate, opener=opener) == 0
    print(f'SELFTEST: {len(control) + 1} limbs passed; {mutations} per-limb mutations killed; missing/failed run controls passed.')
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase', choices=('fixtures', 'run', 'verify', 'cleanup', 'selftest'))
    parser.add_argument('--evidence', default='83f4-evidence')
    parser.add_argument('--deadline', type=int, default=900, help='seconds per PR')
    args = parser.parse_args()
    if args.phase == 'fixtures':
        fixtures()
        return 0
    if args.phase == 'selftest':
        return selftest()
    gate = Gate(args.evidence)
    try:
        if args.phase == 'run':
            return run(gate, args.deadline)
        if args.phase == 'verify':
            return verify(gate, collect=True)
        cleanup(gate)
        return 0
    except Exception as exc:
        gate.log(f'{args.phase} failed: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
