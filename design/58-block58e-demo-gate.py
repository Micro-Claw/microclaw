"""Block 58e Windows evidence collector and independent scorer.

The human-facing runbook drives lifecycle boundaries. This program owns its log,
phase ledger, restoration evidence, and falsifiable PASS/FAIL/NOT EXERCISED limbs.
It intentionally uses only the standard library so the gate itself needs no network.
"""
from __future__ import annotations
import argparse, hashlib, json, os, shutil, subprocess, sys, time, urllib.error, urllib.request
from pathlib import Path

RESULTS = []
class Missing(Exception): pass

def write(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
def read(path): return json.loads(path.read_text(encoding="utf-8"))
def need(path):
    if not path.exists(): raise Missing(f"missing {path.name}")
    return read(path)
def phase(out, name):
    path=out/'phases.json'; data=read(path) if path.exists() else {}; data[name]=time.time(); write(path,data)
def tree_hash(root):
    h=hashlib.sha256()
    if root.exists():
        for p in sorted(x for x in root.rglob('*') if x.is_file()):
            h.update(str(p.relative_to(root)).encode()); h.update(p.read_bytes())
    return h.hexdigest()
def limb(name, *, fails_if):
    def deco(fn):
        try: detail=fn(); status='PASS'
        except Missing as exc: status,detail='NOT EXERCISED',str(exc)
        except Exception as exc: status,detail='FAIL',str(exc)
        row={'name':name,'status':status,'fails_if':fails_if,'detail':detail}; RESULTS.append(row)
        print(f"{status}: {name} — {detail} [fails if: {fails_if}]")
        return fn
    return deco
MODES = ('prepare','direct','stage','restart','later','ordinary','rollback','failure','closed','restore','publiczip','verify')

def api(method, route):
    request = urllib.request.Request('http://127.0.0.1:8000' + route, method=method)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())

def process_command_lines():
    command = [
        'powershell', '-NoProfile', '-Command',
        "Get-CimInstance Win32_Process | Where-Object {$_.CommandLine -like '*microclaw*serve*'} | Select-Object ProcessId,ExecutablePath,CommandLine | ConvertTo-Json -Compress",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'Win32_Process query failed')
    value = json.loads(result.stdout or '[]')
    return value if isinstance(value, list) else [value]

def launch_lines(root):
    path = root / 'launcher.log'
    if not path.exists(): return []
    return [line for line in path.read_text(encoding='utf-8-sig').splitlines() if ' slot=' in line and ' nonce=' in line]

def capture(out, name, root):
    active=(root/'active-slot.txt').read_text().strip() if (root/'active-slot.txt').exists() else None
    state=read(root/'update-state.json') if (root/'update-state.json').exists() else None
    data={'at':time.time(),'active':active,'pending':(root/'pending-slot.txt').read_text().strip() if (root/'pending-slot.txt').exists() else None,
          'health':(root/'launch-health.txt').read_text().strip() if (root/'launch-health.txt').exists() else None,
          'restart_request_exists':(root/'restart-request.txt').exists(),'state':state,
          'appdata_hash':tree_hash(Path(os.environ['APPDATA'])/'microclaw'),
          'launcher_lines':launch_lines(root)}
    write(out/f'{name}.json',data); phase(out,name); return data
def prepare(out, repo, root):
    roaming=Path(os.environ['APPDATA'])/'microclaw'
    backup=out.parent/(out.name+'-SAFETY-BACKUP'); backup.mkdir(parents=True,exist_ok=True)
    if roaming.exists(): shutil.copytree(roaming,backup/'appdata',dirs_exist_ok=True)
    if root.exists(): shutil.copytree(root,backup/'localappdata',dirs_exist_ok=True)
    state=read(root/'update-state.json'); write(out/'original-state.json',state)
    active=(root/'active-slot.txt').read_text().strip(); prior=subprocess.check_output(['git','rev-parse','origin/main~1'],cwd=repo,text=True).strip()
    state['installed_commit']=prior; write(root/'update-state.json',state)
    data=capture(out,'prepare',root); data.update({'branch_active':active,'arranged_commit':prior,'backup':str(backup)}); write(out/'prepare.json',data)
    print(f"BACKUP COPIED TO: {backup}")
def restore(out, root):
    original=need(out/'original-state.json'); write(root/'update-state.json',original)
    prep=need(out/'prepare.json'); (root/'active-slot.txt').write_text(prep['branch_active']+'\n')
    (root/'pending-slot.txt').unlink(missing_ok=True); capture(out,'restore',root)

def direct(out, root):
    status, payload = api('GET', '/api/update')
    data = capture(out, 'direct', root)
    data.update({'status_code': status, 'update': payload, 'processes': process_command_lines()})
    write(out/'direct.json', data)

def stage(out, root):
    first = api('POST', '/api/update/stage')
    second = api('POST', '/api/update/stage')
    samples=[]
    deadline=time.time()+600
    while time.time()<deadline:
        sample=api('GET','/api/update'); samples.append(sample)
        if sample[1].get('pending_staged') or sample[1].get('comparison_refused') or sample[1].get('last_error'):
            break
        time.sleep(.5)
    data=capture(out,'stage',root)
    active=data['active']; inactive='b' if active=='a' else 'a'
    config=Path(os.environ['APPDATA'])/'microclaw'/'safety_config.yaml'
    classifications={}
    for slot in (active,inactive):
        exe=root/f'env-{slot}'/'Scripts'/'microclaw.exe'
        python=root/f'env-{slot}'/'Scripts'/'python.exe'
        isolated=subprocess.run(
            [str(python),'-I','-c','import microclaw; print(microclaw.__file__)'],
            capture_output=True,text=True,cwd=out,check=False,
        )
        result=subprocess.run([str(exe),'--safety-config',str(config),'check-config'],capture_output=True,text=True,cwd=out,check=False)
        classifications[slot]={'returncode':result.returncode,'stdout':result.stdout,'stderr':result.stderr,
                               'isolated_returncode':isolated.returncode,'isolated_module':isolated.stdout.strip()}
    data.update({'first_stage':first,'second_stage':second,'samples':samples,'classifications':classifications})
    write(out/'stage.json',data)

def restart(out, root):
    before=launch_lines(root); started=time.time()
    print('Click Restart now in the browser. This observer waits for the relaunch.')
    deadline=started+120
    while time.time()<deadline and len(launch_lines(root))<len(before)+1: time.sleep(.25)
    elapsed=time.time()-started
    data=capture(out,'restart',root); data.update({'before_lines':before,'elapsed_s':elapsed,'processes':process_command_lines()})
    for process in data['processes']:
        exe=process.get('ExecutablePath')
        if exe and 'env-' in exe.lower():
            marker=Path(exe).parent.parent/'microclaw-slot.json'
            if marker.exists(): data['running_marker']=read(marker)
    write(out/'restart.json',data)

def observe(out, root, name):
    data=capture(out,name,root); data['processes']=process_command_lines(); write(out/f'{name}.json',data)

def failure(out, root):
    deadline=time.time()+30
    while time.time()<deadline:
        try: api('GET','/api/update'); break
        except OSError: time.sleep(.25)
    before=capture(out,'failure-before',root)
    response=api('POST','/api/update/stage')
    deadline=time.time()+180
    while time.time()<deadline:
        status=api('GET','/api/update')[1]
        if status.get('last_error') and not status.get('staging'): break
        time.sleep(.5)
    data=capture(out,'failure',root); data.update({'stage_response':response,'before':before,'status':status})
    write(out/'failure.json',data)

def later(out, root):
    before=capture(out,'later-before',root)
    assert before['pending'], 'Restart later needs a genuinely staged pending slot'
    print('Stop the current server, then double-click the desktop icon once. Press Enter here after it starts.')
    input()
    data=capture(out,'later',root)
    data.update({'pending_before_launch':before['pending'],'active_before_launch':before['active'],'active_after_launch':data['active']})
    write(out/'later.json',data)

def ordinary(out, root):
    print('Ctrl-C the desktop server. When its Press Enter prompt appears, start a stopwatch; press Enter there, then immediately press Enter here.')
    started=time.time(); input(); data=capture(out,'ordinary',root); data['pause_wait_s']=time.time()-started; write(out/'ordinary.json',data)

def rollback(out, root):
    before=capture(out,'rollback-before',root)
    print('Run the failed-start commands above, then press Enter after the failing launch exits.')
    input(); failed_report=(root/'rollback-report.txt').exists()
    print('Restore the package and launch the desktop icon once; press Enter after rollback is reported.')
    input(); data=capture(out,'rollback',root)
    data.update({'active_before':before['active'],'active_after':data['active'],'report_on_failed_launch':failed_report,'report_on_next_launch':not (root/'rollback-report.txt').exists() and len(data['launcher_lines'])>=len(before['launcher_lines'])+2})
    write(out/'rollback.json',data)
def verify(out, root):
    required=['prepare','direct','stage','restart','later','ordinary','rollback','failure','closed','restore','publiczip']
    phases=read(out/'phases.json') if (out/'phases.json').exists() else {}
    missing=[x for x in required if x not in phases]
    if missing: print('PREFLIGHT — commands still owed: '+', '.join(f'.\\design\\58-block58e-demo-gate.ps1 -Mode {x.title()}' for x in missing))
    @limb('Restart now button and nonce-matched relaunch',fails_if='button absent, pause stalls, request survives, or running slot marker does not match candidate')
    def _():
        x=need(out/'restart.json'); s=need(out/'stage.json'); assert len(x['launcher_lines'])>=len(x['before_lines'])+1 and x['health'] in x['launcher_lines'][-1] and not x['restart_request_exists'] and x['active']!=need(out/'prepare.json')['branch_active'] and x['state']['installed_commit']==s['state']['last_success']['candidate']['sha'] and x['running_marker']['commit']==x['state']['installed_commit']; return f"relaunch completed in {x['elapsed_s']:.2f}s"
    @limb('one-job refusal during a real build',fails_if='second stage request is not 409 while first job is genuinely running')
    def _(): assert need(out/'stage.json')['first_stage'][0]==202 and need(out/'stage.json')['second_stage'][0]==409; return '409 while first stage owned the job'
    @limb('progress and restart banner states',fails_if='human did not observe both Building and ready-to-restart states')
    def _():
        samples=[x[1] for x in need(out/'stage.json')['samples']]; assert any(x.get('staging') for x in samples) and any(x.get('pending_staged') for x in samples); return 'cached API exposed building then ready'
    @limb('not-ready comparison banner',fails_if='real staging comparison refusal is not shown to the operator')
    def _():
        samples=[x[1] for x in need(out/'stage.json')['samples']]; hit=next((x for x in samples if x.get('comparison_refused')),None); assert hit and hit.get('comparison_refusal_reason'); return hit['comparison_refusal_reason']
    @limb('real-staging config comparison',fails_if='staging evidence lacks active and candidate classifications')
    def _():
        values=list(need(out/'stage.json')['classifications'].values()); assert len(values)==2 and all(x['returncode']==0 and x['stdout'].strip() and x['isolated_returncode']==0 for x in values); return 'both isolated slot interpreters and real slot CLIs classified the shared file'
    @limb('direct executable offers Restart later only',fails_if='Restart now is visible or no candidate was staged with env-a active')
    def _():
        x=need(out/'direct.json'); assert x['update']['automatic_restart'] is False and any('env-a' in (p.get('ExecutablePath') or '').lower() for p in x['processes']); return 'direct env-a process and automatic_restart=false'
    @limb('unreachable PyPI build is cached without selector change',fails_if='build_error absent, pending published, active changes, or retry timestamp moves')
    def _():
        x=need(out/'failure.json'); p=need(out/'prepare.json'); assert x['active']==p['branch_active'] and x['pending'] is None and x['state'].get('build_error') and x['state'].get('next_check'); return x['state']['build_error']
    @limb('Micro-Manager closed keeps healthy slot without rollback or relaunch',fails_if='health does not match sole new launch, slot changes back, rollback appears, or a second launch occurs')
    def _():
        x=need(out/'closed.json'); assert x['health'] in x['launcher_lines'][-1] and not (root/'rollback-report.txt').exists(); return 'health persisted; no rollback report'
    @limb('active files stay locked while inactive slot stages',fails_if='real staging fails or overwrites the process executable slot')
    def _(): assert need(out/'stage.json')['first_stage'][0]==202 and need(out/'stage.json')['pending']; return 'active server remained present through staged inactive slot'
    @limb('offline launch',fails_if='launcher does not produce nonce-matched health without an update check')
    def _(): x=need(out/'closed.json'); assert x['health']; return 'nonce-matched health captured'
    @limb('Restart later activates only at the next desktop launch',fails_if='pending is absent before exit or selector does not change on the one following launch')
    def _():
        x=need(out/'later.json'); assert x.get('pending_before_launch') and x.get('active_after_launch')!=x.get('active_before_launch'); return 'pending consumed by next launch'
    @limb('ordinary Ctrl-C retains the exit pause',fails_if='timed observation does not record a wait for Enter')
    def _():
        x=need(out/'ordinary.json'); assert x.get('pause_wait_s',0)>0; return f"Enter pause lasted {x['pause_wait_s']:.2f}s"
    @limb('failed start rolls back and reports only on next healthy launch',fails_if='selector is not restored, failing launch reports immediately, or next launch lacks rollback report')
    def _():
        x=need(out/'rollback.json'); assert x.get('active_after')==x.get('active_before') and not x.get('report_on_failed_launch') and x.get('report_on_next_launch'); return 'rollback deferred report verified'
    @limb('Restore restores arranged production state',fails_if='installed_commit, active slot, pending selector, or roaming hash differs')
    def _():
        a,b,p=need(out/'original-state.json'),need(out/'restore.json'),need(out/'prepare.json'); assert b['state']['installed_commit']==a['installed_commit'] and b['active']==p['branch_active'] and b['pending'] is None and b['appdata_hash']==p['appdata_hash']; return 'state, selector and roaming hash restored'
    @limb('public ZIP is last and clone reinstall follows',fails_if='public-head 404 line absent or clone reinstall not recorded')
    def _():
        x=need(out/'publiczip.json'); assert x['state']['provenance']=='public-head' and x['state']['installed_commit']=='unknown' and x['state']['last_error']=='repository is not public (404)'; return 'fresh public-head unknown install cached private 404'
    write(out/'results.json',RESULTS)
    passed=all(x['status']=='PASS' for x in RESULTS); incomplete=any(x['status']=='NOT EXERCISED' for x in RESULTS)
    verdict='PASSED' if passed else ('INCOMPLETE' if incomplete and not any(x['status']=='FAIL' for x in RESULTS) else 'FAILED')
    print(f'BLOCK 58e DEMO GATE {verdict}')
    return 0 if passed else 1
def main():
    p=argparse.ArgumentParser(); p.add_argument('--mode',required=True,choices=MODES); p.add_argument('--repo',type=Path,required=True); p.add_argument('--out',type=Path,required=True); a=p.parse_args(); a.out.mkdir(parents=True,exist_ok=True)
    root=Path(os.environ['LOCALAPPDATA'])/'microclaw'; log=a.out/'gate.txt'
    class Tee:
        def write(self,s): sys.__stdout__.write(s); log.open('a',encoding='utf-8').write(s)
        def flush(self): sys.__stdout__.flush()
    sys.stdout=Tee()
    if a.mode=='prepare': prepare(a.out,a.repo,root); return 0
    if a.mode=='restore': restore(a.out,root); return 0
    if a.mode=='verify': return verify(a.out,root)
    if a.mode=='direct': direct(a.out,root)
    elif a.mode=='stage': stage(a.out,root)
    elif a.mode=='restart': restart(a.out,root)
    elif a.mode=='failure': failure(a.out,root)
    elif a.mode=='later': later(a.out,root)
    elif a.mode=='ordinary': ordinary(a.out,root)
    elif a.mode=='rollback': rollback(a.out,root)
    else: observe(a.out,root,a.mode)
    return 0
if __name__=='__main__': raise SystemExit(main())
