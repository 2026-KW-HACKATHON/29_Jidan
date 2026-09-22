#!/usr/bin/env python3
"""Remove only Jidan artifacts; never force-remove images or prune Docker globally."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

ROOT = Path(os.environ.get('JIDAN_APP_ROOT', '/home/ubuntu/apps/jidan'))
KEEP = 5
REPOSITORY = r'ghcr\.io/2026-kw-hackathon/29_jidan-(?:frontend|backend)'
REF = re.compile(REPOSITORY + r'(?:@[a-z0-9]+:[a-f0-9]{64}|:[a-f0-9]{40})$')


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def image_ref(directory):
    try:
        values = dict(line.split('=', 1) for line in (directory / '.env').read_text().splitlines() if '=' in line)
    except (OSError, ValueError):
        return None
    ref = values.get('IMAGE_REF', '')
    return ref if REF.fullmatch(ref) else None


def release_directories(root):
    for environment in ('dev', 'production'):
        for component in ('frontend', 'backend'):
            base = root / environment / component
            yield base


def prune_releases(base, keep=KEEP):
    # Caller holds this component's deploy.lock throughout deployment and cleanup.
    if (base / 'pending').is_symlink():
        return
    candidates = [p for p in (base / 'releases').glob('release.*')
                  if p.is_dir() and not p.is_symlink() and image_ref(p)]
    verified = sorted([p for p in candidates if (p / 'verified').is_file()],
                      key=lambda p: p.stat().st_mtime_ns, reverse=True)
    current = (base / 'current').resolve()
    protected = {p.resolve() for p in verified[:keep]} | {current}
    for path in candidates:
        if path.resolve() not in protected:
            shutil.rmtree(path)


def retained_refs(root=ROOT):
    refs = set()
    for base in release_directories(root):
        for directory in (base / 'releases').glob('release.*'):
            if directory.is_dir() and not directory.is_symlink():
                ref = image_ref(directory)
                if ref:
                    refs.add(ref)
    return refs


def removal_candidates(images, protected_ids, protected_refs, keep=KEEP):
    candidates = []
    for component in ('frontend', 'backend'):
        prefix = f'ghcr.io/2026-kw-hackathon/29_jidan-{component}'
        group = sorted([i for i in images if REF.fullmatch(i['ref'])
                        and i['ref'].split('@')[0].split(':')[0] == prefix],
                       key=lambda i: i['created'], reverse=True)
        for item in group[keep:]:
            if item['id'] not in protected_ids and item['ref'] not in protected_refs:
                candidates.append(item['ref'])
    return candidates


def prune_images(protected_refs):
    rows = command('docker', 'image', 'ls', '--digests', '--no-trunc', '--format', '{{json .}}')
    refs = set()
    for line in rows.splitlines():
        row = json.loads(line)
        repo, tag, digest = row['Repository'], row['Tag'], row['Digest']
        ref = f'{repo}:{tag}' if tag != '<none>' else f'{repo}@{digest}'
        if REF.fullmatch(ref):
            refs.add(ref)
    images = []
    for ref in sorted(refs):
        data = json.loads(command('docker', 'image', 'inspect', ref))[0]
        images.append(dict(ref=ref, id=data['Id'], created=data['Created']))
    ids = command('docker', 'ps', '-aq').splitlines()
    protected_ids = {json.loads(command('docker', 'inspect', cid))[0]['Image'] for cid in ids}
    for ref in removal_candidates(images, protected_ids, protected_refs):
        # No --force: Docker refuses removal if a container now needs the image.
        result = subprocess.run(['docker', 'image', 'rm', ref], capture_output=True, text=True)
        if result.returncode:
            print(f'Skipped image in use or unavailable: {ref}', file=sys.stderr)


if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1] not in ('build', 'deploy'):
        raise SystemExit('Usage: retention.py build | deploy <dev|production> <frontend|backend>')
    if sys.argv[1] == 'build':
        prune_images(set())
        subprocess.run(['docker', 'buildx', 'prune', '--builder', 'jidan-ci',
                        '--max-used-space', '4gb', '--min-free-space', '8gb', '--force'], check=True)
    else:
        if len(sys.argv) != 4 or sys.argv[2] not in ('dev', 'production') or sys.argv[3] not in ('frontend', 'backend'):
            raise SystemExit('Invalid deployment target')
        prune_releases(ROOT / sys.argv[2] / sys.argv[3])
        prune_images(retained_refs())
