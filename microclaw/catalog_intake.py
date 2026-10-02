"""Publisher signing and data-only catalog intake (stdlib, cryptography, packaging)."""
from __future__ import annotations

import argparse
import base64
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
import tempfile
import zipfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from microclaw import skill_packages as packages, skill_store as store

Refusal = packages.PackageRefusal
IDENTITY = ('publisher', 'package_id', 'version', 'artifact_digest')


def encode(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True) + '\n').encode('ascii')


def read(path, limit=packages.MAX_POLICY_BYTES):
    path = Path(path)
    try:
        with path.open('rb') as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise Refusal('document', 'document exceeds byte limit')
        return json.loads(data)
    except (OSError, ValueError) as exc:
        if isinstance(exc, Refusal):
            raise
        raise Refusal('document', 'expected a readable JSON document') from exc


def public_entry(private):
    raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return dict(key_id=hashlib.sha256(raw).hexdigest(), public_key=base64.b64encode(raw).decode('ascii'), state='active')


def sign(document, private):
    value = deepcopy(document)
    value.pop('signature', None)
    value['signature'] = dict(alg='ed25519', key_id=public_entry(private)['key_id'],
                             value=base64.b64encode(private.sign(packages._canonical(value))).decode('ascii'))
    return value


def load_key(path):
    try:
        key = serialization.load_pem_private_key(Path(path).read_bytes(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError('not ed25519')
        return key
    except (OSError, ValueError, TypeError) as exc:
        raise Refusal('key', 'expected an unencrypted Ed25519 PEM private key') from exc


def archive_checks(artifact, record):
    try:
        with tempfile.TemporaryDirectory() as temporary:
            manifest = store._extract(artifact, Path(temporary), record)
            if manifest['kind'] == 'executable':
                packages.supported_executable(manifest)
            return manifest
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        raise Refusal('artifact', 'expected a readable, valid release zip') from exc


def sign_release(private, artifact, url):
    artifact = Path(artifact)
    try:
        if artifact.stat().st_size > packages.MAX_ARTIFACT_DOWNLOAD_BYTES:
            raise Refusal('artifact', 'zip exceeds download byte limit')
        with zipfile.ZipFile(artifact) as archive:
            info = archive.getinfo('manifest.json')
            if info.file_size > store.MAX_MANIFEST_BYTES:
                raise Refusal('manifest.json', 'manifest exceeds byte limit')
            manifest = packages.validate_manifest(json.loads(archive.read(info)))
    except (OSError, KeyError, zipfile.BadZipFile, ValueError) as exc:
        if isinstance(exc, Refusal):
            raise
        raise Refusal('manifest.json', 'expected a valid zip manifest') from exc
    if url != manifest['artifact']:
        raise Refusal('artifact', 'URL must equal the manifest artifact field; fix the manifest or URL')
    record = packages.validate_intake(sign(intake_from_manifest(manifest, hashlib.sha256(artifact.read_bytes()).hexdigest()), private))
    archive_checks(artifact, record)
    return record


def intake_from_manifest(manifest, digest):
    """One listing projection, also used by fixtures that intentionally make bad zips."""
    fields = ('package_id', 'publisher', 'version', 'artifact', 'kind', 'microclaw',
              'license', 'source_url', 'issues_url')
    record = {k: manifest[k] for k in fields}
    record['skills'] = [{k: skill[k] for k in ('name', 'description')} for skill in manifest['skills']]
    if manifest['kind'] == 'executable':
        record.update({k: manifest[k] for k in ('python', 'platforms', 'protocol_version')})
    record.update(type=packages.RELEASE_TYPE, artifact_digest=digest)
    return record


def sign_withdrawal(private, release, reason):
    release = packages.validate_intake(release)
    value = sign(dict(type=packages.WITHDRAWAL_TYPE, **{k: release[k] for k in IDENTITY}, reason=reason), private)
    policy = dict(publishers={release['publisher']: dict(state='active', keys=[public_entry(private)])})
    return packages.verify_withdrawal(value, policy)


def record_path(collection, record):
    return f"{collection}/{record['publisher']}/{record['package_id']}/{record['version']}.json"


def policy_at(root, environment, roots_file, now):
    if environment == 'production':
        if roots_file is not None:
            raise Refusal('roots', 'production uses the roots shipped with MicroClaw; remove --roots')
        roots = packages.PRODUCTION_ROOTS
        if not roots['keys']:
            raise Refusal('trust', 'no verified policy: production roots have not been published')
    else:
        if roots_file is None:
            raise Refusal('roots', 'test requires --roots from the trusted base checkout')
        roots = read(roots_file)
        if not isinstance(roots, dict) or roots.get('environment') != 'test':
            raise Refusal('roots.environment', 'test requires a test roots document')
    policy = packages.verify_trust_policy(read(Path(root) / 'policy.json'), roots)
    if now > packages._expires(policy['expires_at']):
        raise Refusal('trust.expires_at', 'policy expired; ask the catalog operator to renew it')
    return policy


def tree(root):
    """Compare files as bytes; never follow links or execute head content."""
    root = Path(root)
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        if Path(directory) == root and '.git' in dirs:
            dirs.remove('.git')
        for name in dirs + files:
            path = Path(directory) / name
            relative = path.relative_to(root).as_posix()
            if relative == '.git':
                continue
            if path.is_symlink():
                raise Refusal('path', f'links are forbidden: {relative[:240]}')
            if name in files:
                # Stream hashes: even an unrelated hostile file need not fit in RAM.
                digest = hashlib.sha256()
                with path.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        digest.update(chunk)
                result[relative] = (digest.digest(), bool(path.stat().st_mode & 0o111))
    return result


def one_added_path(changes):
    """Enforce H2 on either a tree comparison or the PR's declared Git diff."""
    if len(changes) != 1 or changes[0][0] != 'A':
        paths = ', '.join(path for _, path in changes)[:240] or 'no file added'
        raise Refusal('path', 'add exactly one release or withdrawal file; no other changes: ' + paths)
    return changes[0][1]


def materialize(git_dir, base_ref, head_sha, out):
    """Overlay this PR's merge-base diff on the current base, using blobs only."""
    def git(*args):
        return subprocess.run(['git', '-C', str(git_dir), *args], stdin=subprocess.DEVNULL,
                              capture_output=True, check=True).stdout

    def listing(commit):
        result = {}
        for item in git('ls-tree', '-rz', commit).split(b'\0'):
            if item:
                meta, raw = item.split(b'\t', 1)
                result[raw.decode('utf-8')] = meta.decode('ascii').split()
        return result

    root = Path(out)
    if root.exists() or root.is_symlink():
        raise Refusal('path', 'materialized head directory must not already exist')
    if not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', head_sha):
        raise Refusal('head_sha', 'expected the full PR head commit SHA')
    try:
        base = git('rev-parse', '--verify', '--end-of-options', base_ref + '^{commit}').decode('ascii').strip()
        git('rev-parse', '--verify', '--end-of-options', head_sha + '^{commit}')
        merge_base = git('merge-base', base, head_sha).decode('ascii').strip()
        changes = git('diff', '--no-renames', '-z', '--name-status', merge_base, head_sha, '--').split(b'\0')
        changed = []
        for index in range(0, len(changes) - 1, 2):
            status, path = changes[index].decode('ascii'), changes[index + 1].decode('utf-8')
            if status not in ('A', 'M', 'D'):
                raise Refusal('path', 'unsupported PR file change: ' + path[:240])
            changed.append((status, path))
        base_files, head_files = listing(base), listing(head_sha)
        root.mkdir(parents=True)

        def destination(path):
            if '.git' in path.split('/'):
                raise Refusal('path', 'Git metadata is forbidden in submission data')
            return packages.safe_release_path(root, path, field='path')

        def write_blob(path, entry):
            target = destination(path)
            mode, kind, oid = entry
            if mode not in ('100644', '100755') or kind != 'blob':
                raise Refusal('path', 'only ordinary blobs are allowed: ' + path[:240])
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('wb') as stream:
                subprocess.run(['git', '-C', str(git_dir), 'cat-file', 'blob', oid],
                               stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.PIPE, check=True)
            target.chmod(0o755 if mode == '100755' else 0o644)

        for path, entry in base_files.items():
            write_blob(path, entry)
        # Deletions first also handle file-to-directory renames without following links.
        for status, path in changed:
            target = destination(path)
            if status == 'D':
                target.unlink(missing_ok=True)
        for status, path in changed:
            if status != 'D':
                write_blob(path, head_files[path])
        # An M/D may already match today's base, but it is still this PR's change.
        one_added_path(changed)
        return dict(base=base, head=head_sha, merge_base=merge_base,
                    changes=[dict(status=status, path=path) for status, path in changed])
    except (subprocess.CalledProcessError, UnicodeError) as exc:
        raise Refusal('git', 'could not read the base, merge-base and PR blobs') from exc
    except OSError as exc:
        raise Refusal('path', 'PR changes conflict with current base paths') from exc


def records(root):
    result = dict(type=packages.CATALOG_TYPE, releases=[], withdrawals=[])
    for collection in ('releases', 'withdrawals'):
        folder = Path(root) / collection
        if not folder.exists():
            continue
        for path in sorted(folder.rglob('*')):
            if path.is_symlink():
                raise Refusal('path', 'release trees may not contain links')
            if path.is_file():
                value = read(path)
                validate_path(path.relative_to(root).as_posix(), collection, value)
                result[collection].append(value)
    return result


def validate_path(path, collection, record):
    if collection == 'releases':
        packages.validate_intake(record)
    else:
        packages.validate_withdrawal(record)
    try:
        expected = record_path(collection, record)
    except (KeyError, TypeError) as exc:
        raise Refusal('path', 'record must name publisher, package_id and version') from exc
    if path != expected:
        raise Refusal('path', 'path components must equal publisher, package_id and version in the record')
    if len(path.split('/')) != 4 or not path.endswith('.json'):
        raise Refusal('path', 'use releases/<publisher>/<package_id>/<version>.json or withdrawals equivalent')


def identity(record):
    return tuple(record[k] for k in IDENTITY)


def verify_all(document):
    """Check trusted history as data; current eligibility belongs to admission."""
    packages._mapping(document, 'catalog', {'type', 'releases', 'withdrawals'})
    if document['type'] != packages.CATALOG_TYPE:
        raise Refusal('catalog.type', 'expected catalog type')
    for collection, limit, validator in (
            ('releases', packages.MAX_CATALOG_RELEASES, packages.validate_intake),
            ('withdrawals', packages.MAX_CATALOG_WITHDRAWALS, packages.validate_withdrawal)):
        packages._list(document[collection], 'catalog.' + collection, limit, empty=True)
        for record in document[collection]:
            validator(record)
    versions, digests = set(), set()
    for record in document['releases']:
        key = identity(record)[:3]
        if key in versions:
            raise Refusal('version', 'this publisher/package/version already exists; publish a new version')
        if record['artifact_digest'] in digests:
            raise Refusal('artifact_digest', 'this digest already appears; submit a distinct release')
        versions.add(key)
        digests.add(record['artifact_digest'])
    withdrawn = set()
    for record in document['withdrawals']:
        key = identity(record)[:3]
        if key in withdrawn:
            raise Refusal('version', 'a withdrawal already exists for this publisher/package/version')
        withdrawn.add(key)
        if identity(record) not in {identity(r) for r in document['releases']}:
            field = 'publisher' if any(r['artifact_digest'] == record['artifact_digest'] and r['publisher'] != record['publisher'] for r in document['releases']) else 'artifact_digest'
            raise Refusal(field, 'withdrawal must name an existing release in full, belonging to its publisher')
    if len(encode(document)) > packages.MAX_CATALOG_BYTES:
        raise Refusal('catalog', 'rebuilt catalog exceeds byte limit')
    return document


def build(root, *, environment='production', roots=None, now=None):
    root = Path(root)
    now = now or datetime.now(timezone.utc)
    policy_at(root, environment, roots, now)
    document = verify_all(records(root))
    target = root / 'catalog.json'
    if target.exists():
        previous = read(target, packages.MAX_CATALOG_BYTES)
        verify_all(previous)
        for collection in ('releases', 'withdrawals'):
            for entry in previous.get(collection, []):
                if entry not in document[collection]:
                    raise Refusal('catalog.' + collection, 'rebuild would drop a previous entry; restore its source file')
    data = encode(document)
    if not target.exists() or target.read_bytes() != data:
        # Replace atomically, after every verification has passed.
        with tempfile.NamedTemporaryFile(dir=root, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        temporary.replace(target)
    return document


def check(base, head, *, environment, roots=None, now=None, opener=None):
    path = None
    try:
        before, after = tree(base), tree(head)
        changed = sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))
        path = ', '.join(changed)[:240] or None
        path = one_added_path([('D' if p not in after else 'M' if p in before else 'A', p) for p in changed])
        collection = path.split('/')[0]
        if collection not in ('releases', 'withdrawals'):
            raise Refusal('path', 'only a new release or withdrawal file is allowed: ' + path[:240])
        now = now or datetime.now(timezone.utc)
        policy = policy_at(base, environment, roots, now)
        value = read(Path(head) / path)
        if collection == 'withdrawals':
            packages.verify_withdrawal(value, policy)
        validate_path(path, collection, value)
        document = records(base)
        previous_path = Path(base) / 'catalog.json'
        if previous_path.exists():
            previous = read(previous_path, packages.MAX_CATALOG_BYTES)
            verify_all(previous)
            for bucket in ('releases', 'withdrawals'):
                for entry in previous[bucket]:
                    if entry not in document[bucket]:
                        document[bucket].append(entry)
        # Duplicate checks precede fetch, so an existing version cannot cause network work.
        document[collection].append(value)
        verify_all(document)
        if collection == 'releases':
            with tempfile.TemporaryDirectory() as temporary:
                with store.download_release(value, package=Path(temporary), opener=opener) as artifact:
                    packages.check_release(value, policy, purpose='admission', artifact=artifact, now=now)
                    archive_checks(artifact, value)
        return dict(accepted=True, path=path, field=None, detail='Verified. The catalog can include this record.')
    except store.updates.UpdateError as exc:
        return dict(accepted=False, path=path[:240] if path else None, field='artifact', detail=('Download refused: ' + str(exc))[:700])
    except Refusal as exc:
        detail = str(exc).removeprefix(exc.field + ': ')
        return dict(accepted=False, path=path[:240] if path else None, field=exc.field[:240], detail=detail[:700])


def human(verdict):
    if verdict['accepted']:
        return f"Accepted `{verdict['path']}`. Signature, policy and release data checks passed."
    # Escape publisher-controlled Markdown and control characters, keep one paragraph.
    def plain(text):
        return ''.join(c if c.isprintable() and c not in '`<>[]\\*_' else ' ' for c in str(text))
    field = ''.join(c if c.isascii() and (c.isalnum() or c in '._[]-') else ' ' for c in str(verdict['field']))
    return f"Refused {plain(verdict['path'] or 'submission')}: field **{field}**. {plain(verdict['detail'])}. Correct this field and update the PR."


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    keygen = commands.add_parser('keygen')
    keygen.add_argument('--out', required=True)
    release = commands.add_parser('sign-release')
    for flag in ('key', 'artifact', 'url', 'out'):
        release.add_argument('--' + flag, required=True)
    withdrawal = commands.add_parser('sign-withdrawal')
    for flag in ('key', 'release', 'reason', 'out'):
        withdrawal.add_argument('--' + flag, required=True)
    materializer = commands.add_parser('materialize')
    for flag in ('git-dir', 'base-ref', 'head-sha', 'out'):
        materializer.add_argument('--' + flag, required=True)
    materializer.add_argument('--json', action='store_true')
    for name in ('check', 'build'):
        command = commands.add_parser(name)
        if name == 'check':
            command.add_argument('--base', required=True)
            command.add_argument('--head', required=True)
            command.add_argument('--json', action='store_true')
        else:
            command.add_argument('--root', required=True)
        command.add_argument('--environment', choices=('production', 'test'), default='production', required=name == 'check')
        command.add_argument('--roots')
    args = parser.parse_args(argv)
    try:
        if args.command == 'keygen':
            key = Ed25519PrivateKey.generate()
            data = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
            try:
                fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError as exc:
                raise Refusal('out', 'private key file exists; choose a new filename') from exc
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data)
            print(encode(public_entry(key)).decode('ascii'), end='')
            print('Keep the private key file secret.')
        elif args.command in ('sign-release', 'sign-withdrawal'):
            key = load_key(args.key)
            if args.command == 'sign-release':
                value = sign_release(key, args.artifact, args.url)
                collection = 'releases'
            else:
                value = sign_withdrawal(key, read(args.release), args.reason)
                collection = 'withdrawals'
            Path(args.out).write_bytes(encode(value))
            print('Repository path: ' + record_path(collection, value))
        elif args.command == 'materialize':
            result = materialize(args.git_dir, args.base_ref, args.head_sha, args.out)
            print(json.dumps(result) if args.json else 'PR changes materialized as data against the current base.')
        elif args.command == 'build':
            build(args.root, environment=args.environment, roots=args.roots)
            print('catalog.json verified and rebuilt.')
        else:
            verdict = check(args.base, args.head, environment=args.environment, roots=args.roots)
            print(json.dumps(verdict) if args.json else human(verdict))
            return 0 if verdict['accepted'] else 1
        return 0
    except Refusal as exc:
        verdict = dict(accepted=False, path=None, field=exc.field[:240], detail=str(exc)[:700])
        print(json.dumps(verdict) if getattr(args, 'json', False) else human(verdict))
        return 1
    except Exception:
        verdict = dict(accepted=False, path=None, field='internal', detail='Internal intake error; ask the catalog operator to inspect the job log.')
        print(json.dumps(verdict) if getattr(args, 'json', False) else human(verdict))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
