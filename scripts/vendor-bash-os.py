#!/usr/bin/env python3
"""Verify or restore the exact MIT bash-os sources named in the reviewed manifest."""
import argparse
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / 'vendor/bash-os'

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fetch', action='store_true', help='restore pinned sources from upstream')
    args = parser.parse_args()
    manifest = json.loads((VENDOR / 'manifest.json').read_text())
    pending = {}
    for name, expected in manifest['files'].items():
        if args.fetch:
            url = f'https://raw.githubusercontent.com/itsmygithubacct/bash-os/{manifest["revision"]}/{name}'
            with urllib.request.urlopen(url, timeout=60) as response:
                data = response.read()
        else:
            data = (VENDOR / name).read_bytes()
        if hashlib.sha256(data).hexdigest() != expected:
            raise SystemExit(f'Checksum mismatch: {name}')
        pending[name] = data
    if args.fetch:
        for name, data in pending.items():
            (VENDOR / name).parent.mkdir(parents=True, exist_ok=True)
            (VENDOR / name).write_bytes(data)
    print(f'Verified {len(pending)} upstream files at {manifest["revision"]}')

if __name__ == '__main__':
    main()
