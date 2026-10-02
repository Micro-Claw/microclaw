"""Offline 83f-4 mutation evidence; do not run concurrently with product tests."""
from pathlib import Path
import difflib
import subprocess, json, xml.etree.ElementTree as ET
root=Path(__file__).resolve().parents[1]
module=root/'microclaw/catalog_intake.py'
workflow=root/'design/83f4-catalog-repo/.github/workflows/intake.yml'
build=root/'tests/fixtures/skill_packages/build_release.py'
store=root/'microclaw/skill_store.py'
package=root/'microclaw/skill_packages.py'
originals={p:p.read_text() for p in (module,workflow,build,store,package)}
cases=[]
def case(name,path,old,new,selectors):
    assert old in originals[path], name
    cases.append((name,path,originals[path].replace(old,new,1),selectors))
case('roundtrip-signature',module,"value=base64.b64encode(private.sign(packages._canonical(value))).decode('ascii')", "value=base64.b64encode(bytes(64)).decode('ascii')", ['test_sign_release_round_trip','test_withdrawal_accepts_full_existing_identity'])
case('one-file-guard',module, "raise Refusal('path', 'add exactly one release or withdrawal file; no other changes: ' + (path or 'no file added'))", "return dict(accepted=True, path=path, field=None, detail='mutant permits extra changes')", ['test_one_file_rule_names_every_forbidden_extra','test_one_file_rule_delete_rename_modify_none','test_duplicates_refuse_before_download'])
case('allowed-collection',module, "raise Refusal('path', 'only a new release or withdrawal file is allowed: ' + path[:240])", "return dict(accepted=True, path=path, field=None, detail='mutant permits forbidden collection')", ['test_one_added_forbidden_path'])
case('file-mode-comparison',module, 'result[relative] = (digest.digest(), bool(path.stat().st_mode & 0o111))', 'result[relative] = digest.digest()', ['test_one_file_rule_delete_rename_modify_none and mode'])
case('record-path-binding',module,'if path != expected:', 'if False:', ['test_path_must_match_fields'])
case('symlink-guard',module,'if path.is_symlink():','if False:', ['test_head_symlink_refuses'])
case('roots-boundary',module,"def policy_at(root, environment, roots_file, now):", "def policy_at(root, environment, roots_file, now):\n    return packages.verify_trust_policy(read(Path(root) / 'policy.json'), read(Path(root) / 'roots-TEST-ONLY.json'))",['test_roots_are_operator_owned'])
case('policy-check',module,"def policy_at(root, environment, roots_file, now):", "def policy_at(root, environment, roots_file, now):\n    return packages.verify_trust_policy(read(Path(__file__).resolve().parents[1] / 'design/83f4-catalog-repo/test-branch/policy.json'), read(Path(root) / 'roots-TEST-ONLY.json'))",['test_policy_refusals'])
# A bypass of the release verdict must kill every invalid admission case, including retired key.
case('release-admission-bypass',module,"        now = now or datetime.now(timezone.utc)\n        policy = policy_at(base", "        return dict(accepted=True, path=path, field=None, detail='mutant bypass')\n        now = now or datetime.now(timezone.utc)\n        policy = policy_at(base",['test_release_refusals','test_tampered_download_names_digest','test_download_cap_and_cleanup','test_duplicates_refuse_before_download','test_same_version_different_digest_in_catalog','test_withdrawal_refusals','test_archive_checks_reused_at_intake','test_catalog_bounds'])
case('url-equality',module,"if url != manifest['artifact']:","if False:",['test_signer_url_must_equal_manifest'])
case('signer-archive-check',module,'    archive_checks(artifact, record)\n    return record','    return record',['test_signer_checks_malformed_zip_early'])
case('execute-publisher',module,"            if manifest['kind'] == 'executable':", "            if manifest['kind'] == 'executable':\n                exec((Path(temporary) / 'release/fixture_worker/runner.py').read_text())",['test_executable_checks_without_importing_publisher'])
case('build-noop',module,'if not target.exists() or target.read_bytes() != data:', 'if True:', ['test_build_deterministic_and_identical_noop'])
case('build-order',module,"for path in sorted(folder.rglob('*')):","for path in sorted(folder.rglob('*'), reverse=True):",['test_build_sort_order'])
case('build-drop',module,'if entry not in document[collection]:','if False:', ['test_build_refuses_dropping_previous_entries'])
case('fixture-signer',build,'    return product_sign(document, private)', "    result = deepcopy(document)\n    result['signature'] = dict(alg='ed25519', key_id='0'*64, value=base64.b64encode(bytes(64)).decode('ascii'))\n    return result",['test_fixture_builder_uses_product_signer_and_projection'])
case('heavy-import',module,'import argparse','import numpy\nimport argparse',['test_restricted_import_subprocess'])
case('overwrite-key',module,'os.O_WRONLY | os.O_CREAT | os.O_EXCL','os.O_WRONLY | os.O_CREAT | os.O_TRUNC',['test_keygen_refuses_overwrite_and_prints_policy_entry'])
case('cli-output-path',module,"print('Repository path: ' + record_path(collection, value))","print('Repository path omitted')",['test_cli_sign_and_withdrawal_paths'])
case('cli-exit-code',module,"return 0 if verdict['accepted'] else 1",'return 0',['test_cli_json_exit_codes'])
case('bounded-detail',module,"detail=detail[:700]",'detail=detail',['test_bounded_refusal_text'])
case('yaml-trigger',workflow,'  pull_request_target:','  pull_request:',['test_workflow_structure'])
case('yaml-permissions',workflow,'  pull-requests: write','  pull-requests: write\n  actions: write',['test_workflow_structure'])
case('yaml-head-checkout',workflow,'ref: ${{ github.event.pull_request.base.ref }}','ref: ${{ github.event.pull_request.head.sha }}',['test_workflow_structure'])
case('yaml-head-script',workflow,'          set -euo pipefail','          set -euo pipefail\n          python "$DATA_HEAD/worker.py"',['test_workflow_structure'])
case('yaml-pin-check',workflow,'            exit 1','            echo skipped-pin-check',['test_workflow_structure'])
# More specific mutants ensure the general bypass above did not hide weak structure tests.
case('download-digest',store,'if hashlib.sha256(data).hexdigest() != entry["artifact_digest"]:', 'if False:', ['test_tampered_download_names_digest'])
# Also bypass admission hashing (otherwise it correctly catches the download mutant).
cases[-1]=(cases[-1][0],cases[-1][1],cases[-1][2],cases[-1][3])
results=[]
try:
    for index,(name,path,mutant,selectors) in enumerate(cases):
        for p,s in originals.items(): p.write_text(s)
        path.write_text(mutant)
        if name=='download-digest':
            package.write_text(originals[package].replace('if digest.hexdigest() != intake["artifact_digest"]:', 'if False:',1))
        junit=Path('/tmp')/f'83f4-mutant-{index}.xml'
        expression=' or '.join(selectors)
        proc=subprocess.run([str(root/'.venv/bin/python'),'-m','pytest','-q','tests/test_catalog_intake.py','-k',expression,f'--junitxml={junit}'],cwd=root,stdin=subprocess.DEVNULL,capture_output=True,text=True)
        Path('/tmp',f'83f4-mutant-{index}.txt').write_text(proc.stdout+proc.stderr)
        nodes=ET.parse(junit).findall('.//testcase')
        failed=[node.attrib['name'] for node in nodes if node.find('failure') is not None]
        survived=[node.attrib['name'] for node in nodes if node.find('failure') is None]
        errors=[node.attrib['name'] for node in nodes if node.find('error') is not None]
        killed=bool(failed) and not errors
        patches = {str(p.relative_to(root)): ''.join(difflib.unified_diff(originals[p].splitlines(True), p.read_text().splitlines(True))) for p in originals if originals[p] != p.read_text()}
        result=dict(mutant=name,patches=patches,tests=selectors,failed=failed,survived=survived,errors=errors,killed=killed)
        results.append(result)
        print(name, 'KILLED' if killed else 'SURVIVED',f'{len(failed)} failures, {len(survived)} survivors',flush=True)
finally:
    for p,s in originals.items(): p.write_text(s)
    (root/'design/83f4-gate/mutations.json').write_text(json.dumps(results,indent=2)+'\n')
assert all(r['killed'] for r in results)
collected=subprocess.run([str(root/'.venv/bin/python'),'-m','pytest','--collect-only','-q','tests/test_catalog_intake.py'],cwd=root,stdin=subprocess.DEVNULL,capture_output=True,text=True,check=True).stdout
names={line.split('::')[-1] for line in collected.splitlines() if '::test_' in line}
failed={name for result in results for name in result['failed']}
assert names <= failed, 'Tests without a killed mutant: ' + str(sorted(names-failed))
print(f'{len(results)}/{len(results)} mutants killed; {len(names)}/{len(names)} test cases failed on at least one mutant.')
