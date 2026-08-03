# design/35 Block 4g — saved-hook byte-hash Windows gate

This gate verifies the saved-hook newline/hash repair on branch
`design32/hook-hash-newline`. It runs on any Windows machine; the demo machine
is sufficient and no microscope hardware is needed. Commands are PowerShell
safe, preserve their output with `> file.txt 2>&1`, and make no hardware calls.

The implementation pin is `50e5f66143dc98ec1aef0dce675b9686f6baeb64`.

## G0 — branch and suite

```powershell
git fetch origin > git-fetch.txt 2>&1
git switch design32/hook-hash-newline > git-switch.txt 2>&1
git pull --ff-only > git-pull.txt 2>&1
git status --short > status.txt 2>&1
git rev-parse HEAD > head.txt 2>&1
git merge-base --is-ancestor 50e5f66143dc98ec1aef0dce675b9686f6baeb64 HEAD
echo $LASTEXITCODE > implementation-ancestor-exit.txt
python -m pytest -q > pytest.txt 2>&1
$Stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$Evidence = "block4g-$Stamp"
New-Item -ItemType Directory -Path $Evidence
Copy-Item "design\35-block4g-gate-prompts.md" (Join-Path $Evidence "runbook.md")
Copy-Item implementation-ancestor-exit.txt,pytest.txt,head.txt,status.txt $Evidence
```

The ancestor command must exit `0`, the starting tree must be clean, and the
suite must show **1338 passed, 0 failed, 115 skipped**. This is the recorded
Windows baseline of 1311 passed / 23 failed / 115 skipped, with all 23 defect
failures repaired and four new tests added by this block.

**Round 1 (M5, 2026-08-03) failed here at 1308 passed / 23 failed**, while G1 and
G2a both passed — the product code was correct and two *test fixtures* still
wrote text while pinning the untranslated string. If this count is wrong again,
read the failure text: `changed on disk` and `legacy newline-normalized` are
different findings, and the second means the pin and the bytes disagree in the
fixture rather than in `hook_manager`. Preserve the warning
summary and report any warning-count change.

## G1 — fresh save, live load, describe, and offline load

This is one mechanical Python check against an isolated temporary registry. It
saves source containing explicit CRLF bytes, verifies the exact file bytes and
manifest digest, loads the hook through both loading paths, and checks describe.

```powershell
python -c "import hashlib,json,sys,tempfile;from pathlib import Path;from microclaw import hook_manager,completed_dataset;d=Path(tempfile.mkdtemp(prefix='mc-4g-'));hook_manager.HOOKS_DIR=d;hook_manager.MANIFEST=d/'manifest.json';completed_dataset.MANIFEST=hook_manager.MANIFEST;code='class GateHook:\r\n def analyze_frame(self,image,metadata): return None\r\n def analyze_saved_frame(self,image,metadata,context): return None\r\n';hook_manager.save_hook('block4g_gate',code,'Block 4g gate',source='user_provided');raw=(d/'block4g_gate.py').read_bytes();entry=json.loads(hook_manager.MANIFEST.read_text(encoding='utf-8'))['block4g_gate'];desc=hook_manager.describe_saved_hook('block4g_gate');live=hook_manager.load_hook_class('block4g_gate');offline=completed_dataset._load_saved_adapter('block4g_gate')[0];checks={'exact_bytes':raw==code.encode('utf-8'),'manifest_matches_bytes':entry['sha256']==hashlib.sha256(raw).hexdigest(),'live_load':live.__name__=='GateHook','offline_load':offline.__name__=='GateHook','describe_matches_manifest':desc['provenance']['matches_manifest'] is True};print(json.dumps(checks,indent=2));sys.exit(0 if all(checks.values()) else 1)" > block4g-roundtrip.txt 2>&1
echo $LASTEXITCODE > block4g-roundtrip-exit.txt
Copy-Item block4g-roundtrip.txt,block4g-roundtrip-exit.txt $Evidence
```

Every value in `block4g-roundtrip.txt` must be `true`, including
`offline_load` and `describe_matches_manifest`, and the exit file must contain
`0`.

## G2a — synthetic legacy pin, always runnable

**Run this one first, and run it whether or not G2 below is possible.** G2 needs
the gate machine to happen to own a hook saved under the old convention; if it
owns none, the migration path would ship to every operator untested. This step
removes that dependency. It builds a throwaway registry in a temp directory,
plants a hook pinned the old way, and walks the whole migration — refusal,
review, re-save, reload. It reads and writes nothing outside that temp
directory and touches no hardware.

```powershell
python design\35-block4g-legacy-migration-check.py > legacy-synthetic.txt 2>&1
echo $LASTEXITCODE > legacy-synthetic-exit.txt
Copy-Item legacy-synthetic.txt,legacy-synthetic-exit.txt $Evidence
```

The exit file must contain `0` and every check in `legacy-synthetic.txt` must be
`true`. The two `*_message` entries are recorded text, not booleans — they must
each contain `legacy newline-normalized` and `re-save`, and neither may read
`NO REFUSAL`.

## G2 — existing old-convention manifest and deliberate migration

**Skip this step and say so if the machine has no hook saved under the old
convention** — G2a has already covered the mechanism, and inventing a legacy
hook by hand-editing a real manifest would test the edit, not the code.

Choose an existing hook whose manifest was pinned under the old convention and
whose file has not otherwise been edited. Set its exact manifest name here:

```powershell
$LegacyHook = "<existing-hook-name>"
python -c "import json,sys;from microclaw.hook_manager import describe_saved_hook;print(json.dumps(describe_saved_hook(sys.argv[1]),indent=2))" $LegacyHook > legacy-describe-before.txt 2>&1
python -c "import sys;from microclaw.hook_manager import load_hook_class;load_hook_class(sys.argv[1])" $LegacyHook > legacy-load-before.txt 2>&1
echo $LASTEXITCODE > legacy-load-before-exit.txt
```

The description must report `"legacy_newline_pin": true`,
`"matches_manifest": false`, and a refusal reason telling the user to review
and re-save. The load must fail with an actionable message containing
`legacy newline-normalized hash`, `Review`, and `re-save`. This is the expected
safe migration: the old pin is not silently accepted or rewritten.

Now open the path printed in `legacy-describe-before.txt` and review the complete
source. Only if the operator deliberately approves those exact on-disk bytes,
re-save the same entry with this mechanical command. This explicit command is
the consent action; do not run it before the review.

```powershell
python -c "import json,sys;from pathlib import Path;from microclaw.hook_manager import MANIFEST,save_hook;m=json.loads(MANIFEST.read_text(encoding='utf-8'));n=sys.argv[1];e=m[n];code=Path(e['path']).read_bytes().decode('utf-8');save_hook(n,code,e.get('description','Re-approved legacy hook'),e.get('source','user_provided'));print('RE-SAVED AFTER EXPLICIT OPERATOR REVIEW:',n)" $LegacyHook > legacy-resave.txt 2>&1
echo $LASTEXITCODE > legacy-resave-exit.txt
python -c "import json,sys;from microclaw.hook_manager import describe_saved_hook,load_hook_class;c=load_hook_class(sys.argv[1]);d=describe_saved_hook(sys.argv[1]);print(json.dumps({'loaded_class':c.__name__,'matches_manifest':d['provenance']['matches_manifest'],'legacy_newline_pin':d['provenance']['legacy_newline_pin']},indent=2))" $LegacyHook > legacy-after.txt 2>&1
echo $LASTEXITCODE > legacy-after-exit.txt
Copy-Item legacy-describe-before.txt,legacy-load-before.txt,legacy-load-before-exit.txt,legacy-resave.txt,legacy-resave-exit.txt,legacy-after.txt,legacy-after-exit.txt $Evidence
```

Both re-save and final-load exits must be `0`; the final description must show
`matches_manifest: true` and `legacy_newline_pin: false`. If the pre-migration
description does not identify a legacy newline pin, stop: the file is either
already byte-pinned or differs for another reason, and this procedure must not
turn generic tampering into trust.

## Return to the coordinator

Return the complete `$Evidence` directory plus `git-fetch.txt`,
`git-switch.txt`, and `git-pull.txt`. Report the machine, exact pytest summary
and warnings, every G1 boolean, every G2a check, and — if G2 ran — the old hook
name, its exact before-load error, and its after-migration description. If G2
was skipped for want of a legacy-pinned hook, say so plainly; that is an
expected outcome, not a gap. Report any intervention or ambiguity.

Do not merge the branch. The coordinator reviews the evidence.
