#!/usr/bin/env python3
"""Verify or restore the exact bash-os sources named in the reviewed manifest."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / 'vendor/bash-os'

def safe_relative(name):
    """Reject manifest paths that would write outside the vendor directory."""
    if not isinstance(name, str) or not name or '\\' in name:
        raise SystemExit(f'Unsafe vendor path: {name}')
    path = Path(name)
    if path.is_absolute() or any(part in ('', '.', '..') for part in name.split('/')):
        raise SystemExit(f'Unsafe vendor path: {name}')
    return path

def load_manifest(vendor=VENDOR):
    return json.loads((vendor / 'manifest.json').read_text())

def read_local(manifest, vendor=VENDOR):
    blobs = {}
    for name in manifest['files']:
        blobs[name] = (vendor / safe_relative(name)).read_bytes()
    return blobs

def fetch_upstream(manifest):
    blobs = {}
    for name in manifest['files']:
        safe_relative(name)
        url = f'https://raw.githubusercontent.com/itsmygithubacct/bash-os/{manifest["revision"]}/{name}'
        with urllib.request.urlopen(url, timeout=60) as response:
            blobs[name] = response.read()
    return blobs

def check_blobs(manifest, blobs):
    for name, expected in manifest['files'].items():
        safe_relative(name)
        if hashlib.sha256(blobs[name]).hexdigest() != expected:
            raise SystemExit(f'Checksum mismatch: {name}')

def publish_blobs(vendor, blobs):
    """Write every file to a temporary sibling, then replace. Checksums are enforced first."""
    planned = []
    for name, data in blobs.items():
        destination = vendor / safe_relative(name)
        for parent in destination.parents:
            if parent.is_symlink():
                raise SystemExit(f'Refusing to write through symlink: {parent}')
        planned.append((destination, data))
    temps = []
    try:
        for destination, data in planned:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(f'.{destination.name}.{os.getpid()}.tmp')
            temporary.write_bytes(data)
            temps.append((temporary, destination))
        for temporary, destination in temps:
            os.replace(temporary, destination)
            temps = [(item, dest) for item, dest in temps if item != temporary]
    finally:
        for temporary, _destination in temps:
            if temporary.exists():
                temporary.unlink()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', action='store_true', help='restore pinned sources from upstream')
    args = parser.parse_args()
    manifest = load_manifest()
    blobs = fetch_upstream(manifest) if args.fetch else read_local(manifest)
    check_blobs(manifest, blobs)
    if args.fetch:
        publish_blobs(VENDOR, blobs)
    print(f'Verified {len(blobs)} upstream files at {manifest["revision"]}')

if __name__ == '__main__':
    main()
