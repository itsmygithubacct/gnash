#!/usr/bin/env python3
"""Create a complete Linux release archive, or test it after relocation."""
import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parent.parent
TARGET = 'x86_64-unknown-linux-gnu'
REQUIRED = ('gnash', 'lib/gnash/uutils.so', 'lib/gnash/bash-os-coreutils.so',
            'lib/gnash/bash-os-manifest.json', 'licenses/cargo-inventory.json',
            'licenses/brush-MIT.txt', 'licenses/bash-os-MIT.txt')

def version():
    return tomllib.loads((ROOT / 'gnash/Cargo.toml').read_text())['package']['version']

def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()

def notices(layout):
    inventory = json.loads((layout / 'licenses/cargo-inventory.json').read_text())
    if inventory['target'] != TARGET or not inventory['packages']:
        raise ValueError('Missing license inventory or unsupported target')
    paths = set()
    for package in inventory['packages']:
        if not package['notices']:
            raise ValueError('Package is missing its license notices')
        for name in package['notices']:
            path = PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts or len(path.parts) != 2:
                raise ValueError('Invalid license notice path')
            paths.add('licenses/' + str(path))
    return paths

def create(args):
    rustc = shutil.which('rustc') or str(Path.home() / '.cargo/bin/rustc')
    compiler = subprocess.check_output([rustc, '-vV'], text=True)
    if f'host: {TARGET}\n' not in compiler:
        raise ValueError(f'Release packaging currently requires a native {TARGET} build')
    if args.tag and args.tag != 'v' + version():
        raise ValueError(f'Release tag must match v{version()}')
    if args.tag and subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT):
        raise ValueError('Tagged release packaging requires a clean source tree')
    layout = args.layout.resolve()
    files = {name: layout / name for name in (*REQUIRED, *notices(layout))}
    files.update({
        'README.md': ROOT / 'README.release.md',
        'LICENSE': ROOT / 'LICENSE',
        'include/gnash.h': ROOT / 'include/gnash.h',
        'examples/loadables/hello.c': ROOT / 'examples/loadables/hello.c',
        'docs/gnash-loadables.md': ROOT / 'docs/gnash-loadables.md',
        'docs/gnash-coreutils.md': ROOT / 'docs/gnash-coreutils.md',
        'licenses/README.md': ROOT / 'licenses/README.md',
    })
    vendor = ROOT / 'vendor/bash-os'
    manifest = json.loads((vendor / 'manifest.json').read_text())
    files['vendor/bash-os/manifest.json'] = vendor / 'manifest.json'
    for name, expected in manifest['files'].items():
        path = vendor / name
        if digest(path) != expected:
            raise ValueError(f'Pinned upstream source changed: {name}')
        files['vendor/bash-os/' + name] = path
    for name, source in files.items():
        if not source.is_file() or source.is_symlink():
            raise ValueError(f'Missing regular package file: {name}')
    if not os.access(layout / 'gnash', os.X_OK):
        raise ValueError('The gnash binary must be executable')
    revision = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    epoch = int(os.environ.get('SOURCE_DATE_EPOCH') or subprocess.check_output(
        ['git', 'log', '-1', '--format=%ct'], cwd=ROOT, text=True))
    info = json.dumps({'version': version(), 'target': TARGET, 'source_commit': revision,
                       'rustc': compiler.splitlines()[0], 'libc': platform.libc_ver()}, indent=2).encode() + b'\n'
    basename = f'gnash-{version()}-{TARGET}'
    args.out.mkdir(parents=True, exist_ok=True)
    archive_path = args.out / (basename + '.tar.gz')
    # Archive ownership and timestamps are independent of the local build account.
    with archive_path.open('wb') as raw, gzip.GzipFile(fileobj=raw, mode='wb', mtime=epoch, filename='') as compressed:
        with tarfile.open(fileobj=compressed, mode='w') as archive:
            for name, source in sorted(files.items()):
                member = archive.gettarinfo(str(source), arcname=basename + '/' + name)
                member.uid = member.gid = 0
                member.uname = member.gname = ''
                member.mtime = epoch
                member.pax_headers = {}
                with source.open('rb') as content:
                    archive.addfile(member, content)
            member = tarfile.TarInfo(basename + '/BUILD-INFO.json')
            member.size, member.mtime, member.mode = len(info), epoch, 0o644
            archive.addfile(member, io.BytesIO(info))
    archive_path.with_name(archive_path.name + '.sha256').write_text(
        f'{digest(archive_path)}  {archive_path.name}\n')
    print(archive_path)

def verify(args):
    archive_path = args.archive.resolve()
    checksum = archive_path.with_name(archive_path.name + '.sha256').read_text().strip()
    if checksum != f'{digest(archive_path)}  {archive_path.name}':
        raise ValueError('Archive checksum mismatch')
    basename = f'gnash-{version()}-{TARGET}'
    with tempfile.TemporaryDirectory(prefix='gnash release ') as folder:
        destination = Path(folder)
        with tarfile.open(archive_path, 'r:gz') as archive:
            seen = set()
            for member in archive.getmembers():
                name = PurePosixPath(member.name)
                if (not member.isfile() or name.is_absolute() or '..' in name.parts
                        or len(name.parts) < 2 or name.parts[0] != basename or name in seen):
                    raise ValueError('Unexpected archive entry')
                if member.uid or member.gid or member.uname or member.gname:
                    raise ValueError('Archive contains local ownership metadata')
                seen.add(name)
                path = destination / name
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, path.open('wb') as output:
                    shutil.copyfileobj(source, output)
                path.chmod(member.mode & 0o777)
        layout = destination / basename
        for name in (*REQUIRED, *notices(layout), 'include/gnash.h', 'README.md', 'LICENSE', 'BUILD-INFO.json'):
            if not (layout / name).is_file():
                raise ValueError(f'Archive is missing {name}')
        environment = dict(os.environ, GNASH_TEST_BINARY=str(layout / 'gnash'))
        environment.pop('GNASH_MODULE_DIR', None)
        subprocess.run([sys.executable, ROOT / 'tests/gnash/test_loadables.py'], cwd=destination,
                       env=environment, check=True)
    print(f'Verified checksum, contents, notices, and relocated runtime: {archive_path.name}')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    build = commands.add_parser('create', help='package a previously built release layout')
    build.add_argument('--layout', type=Path, default=ROOT / 'out')
    build.add_argument('--out', type=Path, default=ROOT / 'out/dist')
    build.add_argument('--tag', help='require the published tag to match the Cargo version')
    check = commands.add_parser('verify', help='test an archive from a temporary directory')
    check.add_argument('archive', type=Path)
    args = parser.parse_args()
    try:
        (create if args.command == 'create' else verify)(args)
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'{error}\n')

if __name__ == '__main__':
    main()
