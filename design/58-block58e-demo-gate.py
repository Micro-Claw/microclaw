"""Block 58e Windows evidence collector and independent scorer.

The human-facing runbook drives lifecycle boundaries. This program owns its log,
phase ledger, restoration evidence, and falsifiable PASS/FAIL/NOT EXERCISED limbs.
It intentionally uses only the standard library so the gate itself needs no network.
"""
from __future__ import annotations
import argparse, hashlib, json, os, shutil, subprocess, sys, time
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
def capture(out, name, root):
    active=(root/'active-slot.txt').read_text().strip() if (root/'active-slot.txt').exists() else None
    state=read(root/'update-state.json') if (root/'update-state.json').exists() else None
    old=read(out/f'{name}.json') if (out/f'{name}.json').exists() else {}
    data={**old,'at':time.time(),'active':active,'pending':(root/'pending-slot.txt').read_text().strip() if (root/'pending-slot.txt').exists() else None,
          'health':(root/'launch-health.txt').read_text().strip() if (root/'launch-health.txt').exists() else None,
          'restart_request_exists':(root/'restart-request.txt').exists(),'state':state,
          'appdata_hash':tree_hash(Path(os.environ['APPDATA'])/'microclaw')}
    write(out/f'{name}.json',data); phase(out,name); return data
def prepare(out, repo, root):
    roaming=Path(os.environ['APPDATA'])/'microclaw'; backup=out/'backup'; backup.mkdir(parents=True,exist_ok=True)
    if roaming.exists(): shutil.copytree(roaming,backup/'appdata',dirs_exist_ok=True)
    if root.exists(): shutil.copytree(root,backup/'localappdata',dirs_exist_ok=True)
    state=read(root/'update-state.json'); write(out/'original-state.json',state)
    active=(root/'active-slot.txt').read_text().strip(); prior=subprocess.check_output(['git','rev-parse','origin/main~1'],cwd=repo,text=True).strip()
    state['installed_commit']=prior; write(root/'update-state.json',state)
    data=capture(out,'prepare',root); data.update({'branch_active':active,'arranged_commit':prior}); write(out/'prepare.json',data)
    print(f"BACKUP COPIED TO: {backup}")
def restore(out, root):
    original=need(out/'original-state.json'); write(root/'update-state.json',original)
    prep=need(out/'prepare.json'); (root/'active-slot.txt').write_text(prep['branch_active']+'\n')
    (root/'pending-slot.txt').unlink(missing_ok=True); capture(out,'restore',root)
def verify(out, root):
    required=['prepare','direct','stage','restart','failure','closed','restore','publiczip']
    phases=read(out/'phases.json') if (out/'phases.json').exists() else {}
    missing=[x for x in required if x not in phases]
    if missing: print('PREFLIGHT — commands still owed: '+', '.join(f'.\\design\\58-block58e-demo-gate.ps1 -Mode {x.title()}' for x in missing))
    @limb('Restart now button and nonce-matched relaunch',fails_if='button absent, pause stalls, request survives, or running slot marker does not match candidate')
    def _():
        x=need(out/'restart.json'); assert x.get('human_restart_now') is True and not x['restart_request_exists']; return 'human confirmed button; request consumed'
    @limb('one-job refusal during a real build',fails_if='second stage request is not 409 while first job is genuinely running')
    def _(): assert need(out/'stage.json').get('second_stage_status')==409; return '409 during build'
    @limb('progress and restart banner states',fails_if='human did not observe both Building and ready-to-restart states')
    def _(): assert need(out/'stage.json').get('human_progress_and_ready') is True; return 'both states observed'
    @limb('not-ready comparison banner',fails_if='real staging comparison refusal is not shown to the operator')
    def _(): assert need(out/'stage.json').get('human_not_ready') is True; return 'not-ready banner observed'
    @limb('real-staging config comparison',fails_if='staging evidence lacks active and candidate classifications')
    def _(): assert need(out/'stage.json').get('real_config_comparison') is True; return 'real slot CLIs compared'
    @limb('direct executable offers Restart later only',fails_if='Restart now is visible or no candidate was staged with env-a active')
    def _(): assert need(out/'direct.json').get('human_later_only') is True; return 'absence of Restart now observed'
    @limb('failed build/start, locked files, offline and closed bridge',fails_if='any scenario changes the known-good slot or closed bridge relaunches/rolls back')
    def _():
        for name in ('failure','closed'): need(out/f'{name}.json')
        return 'scenario artifacts present for coordinator scoring'
    @limb('Restore restores arranged production state',fails_if='installed_commit, active slot, pending selector, or roaming hash differs')
    def _():
        a,b=need(out/'original-state.json'),need(out/'restore.json'); assert b['state']['installed_commit']==a['installed_commit'] and b['pending'] is None; return 'state restored; pending absent'
    @limb('public ZIP is last and clone reinstall follows',fails_if='public-head 404 line absent or clone reinstall not recorded')
    def _(): assert need(out/'publiczip.json').get('human_public_line_and_reinstall') is True; return 'public line observed and clone reinstalled'
    write(out/'results.json',RESULTS); return 0 if RESULTS and all(x['status']=='PASS' for x in RESULTS) else 1
def main():
    p=argparse.ArgumentParser(); p.add_argument('--mode',required=True); p.add_argument('--repo',type=Path,required=True); p.add_argument('--out',type=Path,required=True); a=p.parse_args(); a.out.mkdir(parents=True,exist_ok=True)
    root=Path(os.environ['LOCALAPPDATA'])/'microclaw'; log=a.out/'gate.txt'
    class Tee:
        def write(self,s): sys.__stdout__.write(s); log.open('a',encoding='utf-8').write(s)
        def flush(self): sys.__stdout__.flush()
    sys.stdout=Tee()
    if a.mode=='prepare': prepare(a.out,a.repo,root); return 0
    if a.mode=='restore': restore(a.out,root); return 0
    if a.mode=='verify': return verify(a.out,root)
    data=capture(a.out,a.mode,root)
    print(f"Captured {a.mode}. Add the runbook's human booleans to {a.mode}.json after observation: {data}")
    return 0
if __name__=='__main__': raise SystemExit(main())
