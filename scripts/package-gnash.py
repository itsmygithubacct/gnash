#!/usr/bin/env python3
"""Create a complete Linux release archive, or test it after relocation."""
import argparse
from contextlib import contextmanager
import fcntl
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import tomllib

ROOT = Path(__file__).resolve().parent.parent
TARGET = 'x86_64-unknown-linux-gnu'
REQUIRED = ('gnash', 'lib/gnash/uutils.so', 'lib/gnash/bash-os-coreutils.so',
            'lib/gnash/bash-os-manifest.json', 'licenses/cargo-inventory.json',
            'licenses/brush-MIT.txt', 'licenses/bash-os-MIT.txt', 'licenses/bash-os-nl-regex.h')
PACKAGED_SOURCES = {
    'README.md': 'README.release.md',
    'LICENSE': 'LICENSE',
    'include/gnash.h': 'include/gnash.h',
    'examples/loadables/hello.c': 'examples/loadables/hello.c',
    'docs/gnash-loadables.md': 'docs/gnash-loadables.md',
    'docs/gnash-coreutils.md': 'docs/gnash-coreutils.md',
    'licenses/README.md': 'licenses/README.md',
}
BINARIES = ('gnash', 'lib/gnash/uutils.so', 'lib/gnash/bash-os-coreutils.so')
MAX_MEMBER_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_BYTES = 2 * 1024 * 1024 * 1024
MAX_MEMBERS = 20000
ELF_MACHINE_X86_64 = 62

def version():
    return tomllib.loads((ROOT / 'gnash/Cargo.toml').read_text())['package']['version']

def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()

def build_tool():
    spec = importlib.util.spec_from_file_location('gnash_build', ROOT / 'scripts/build-gnash.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def load_vendor_manifest():
    return json.loads((ROOT / 'vendor/bash-os/manifest.json').read_text())

def archive_mode(path):
    """Ship owner-readable texts as 0644 and executables as 0755, ignoring umask."""
    return 0o755 if path.stat().st_mode & 0o111 else 0o644

def assert_host_elf(path):
    with path.open('rb') as source:
        data = source.read(64)
    if len(data) < 20 or data[:4] != b'\x7fELF' or data[4] != 2 or data[5] != 1:
        raise ValueError(f'{path} is not a 64-bit little-endian ELF file')
    machine = int.from_bytes(data[18:20], 'little')
    if machine != ELF_MACHINE_X86_64:
        raise ValueError(f'{path} is not an x86_64 ELF file')

def notice_paths(layout):
    inventory = json.loads((layout / 'licenses/cargo-inventory.json').read_text())
    if inventory.get('target') != TARGET or not inventory.get('packages'):
        raise ValueError('Missing license inventory or unsupported target')
    hashes = inventory.get('notice_sha256')
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError('License inventory is missing notice checksums')
    paths = []
    for package in inventory['packages']:
        if not package.get('notices'):
            raise ValueError('Package is missing its license notices')
        for name in package['notices']:
            path = PurePosixPath(name)
            if path.is_absolute() or '..' in path.parts or len(path.parts) != 2:
                raise ValueError('Invalid license notice path')
            key = path.as_posix()
            if hashes.get(key) is None:
                raise ValueError(f'Missing notice checksum: {key}')
            file_path = layout / 'licenses' / key
            if not file_path.is_file() or file_path.is_symlink():
                raise ValueError(f'Missing license notice: {key}')
            if digest(file_path) != hashes[key]:
                raise ValueError(f'License notice checksum mismatch: {key}')
            paths.append('licenses/' + key)
    return paths

def attest_source(head, worktree_dirty, stamp):
    """Return the commit only when the build and the tree being packaged are that commit."""
    dirty = bool(worktree_dirty or stamp.get('source_dirty'))
    commit = stamp.get('source_commit')
    if dirty or not commit or commit != head:
        return None, dirty, False
    return commit, False, True

def require_fresh(layout, stamp, manifest):
    if not stamp.get('complete'):
        raise ValueError('Layout provenance is incomplete; run python3 scripts/build-gnash.py before packaging')
    if stamp.get('target') != TARGET or stamp.get('version') != version():
        raise ValueError('Layout provenance does not match this version or target')
    if stamp.get('vendor_revision') != manifest['revision']:
        raise ValueError('Vendor revision does not match the build stamp')
    tool = build_tool()
    vendor_files, source_files = tool.source_fingerprint(manifest)
    if stamp.get('vendor_files') != vendor_files:
        raise ValueError('Vendor sources changed since the layout was built')
    if stamp.get('source_files') != source_files:
        raise ValueError('Source inputs changed since the layout was built')
    artifacts = stamp.get('artifacts') or {}
    for name in tool.LAYOUT_ARTIFACTS:
        path = layout / name
        if name not in artifacts or not path.is_file() or path.is_symlink() or digest(path) != artifacts[name]:
            raise ValueError(f'Stale or mismatched layout file: {name}')
    pinned = (ROOT / 'vendor/bash-os/manifest.json').read_bytes()
    if (layout / 'lib/gnash/bash-os-manifest.json').read_bytes() != pinned:
        raise ValueError('Layout manifest does not match the pinned vendor manifest')
    comparisons = {
        'licenses/brush-MIT.txt': ROOT / 'LICENSE',
        'licenses/bash-os-MIT.txt': ROOT / 'vendor/bash-os/LICENSE',
        'licenses/bash-os-nl-regex.h': ROOT / 'vendor/bash-os/loadables/common/nl-regex.h',
    }
    for name, source in comparisons.items():
        if (layout / name).read_bytes() != source.read_bytes():
            raise ValueError(f'Layout notice does not match the pinned source: {name}')
    notice_paths(layout)
    for name in BINARIES:
        assert_host_elf(layout / name)
    if not os.access(layout / 'gnash', os.X_OK):
        raise ValueError('The gnash binary must be executable')

def packaged_files(layout, manifest):
    files = {name: layout / name for name in (*REQUIRED, *notice_paths(layout))}
    files.update({name: ROOT / source for name, source in PACKAGED_SOURCES.items()})
    vendor = ROOT / 'vendor/bash-os'
    files['vendor/bash-os/manifest.json'] = vendor / 'manifest.json'
    for name in manifest['files']:
        relative = build_tool().safe_relative(name)
        path = vendor / relative
        if digest(path) != manifest['files'][name]:
            raise ValueError(f'Pinned upstream source changed: {name}')
        files['vendor/bash-os/' + name] = path
    for name, source in files.items():
        if not source.is_file() or source.is_symlink():
            raise ValueError(f'Missing regular package file: {name}')
    return files

def atomic_produce(destination, write):
    """Write a sibling temporary file and replace destination only after success."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f'.{destination.name}.{os.getpid()}.tmp')
    try:
        write(temporary)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()

def add_regular(archive, arcname, source, epoch):
    info = tarfile.TarInfo(arcname)
    info.size = source.stat().st_size
    info.mtime = int(epoch)
    info.mode = archive_mode(source)
    info.type = tarfile.REGTYPE
    info.uid = info.gid = 0
    info.uname = info.gname = ''
    with source.open('rb') as content:
        archive.addfile(info, content)

def create(args):
    tool = build_tool()
    layout = args.layout.absolute()
    tool.reject_output_symlink(layout)
    lock_path = layout / '.build.lock'
    tool.reject_output_symlink(lock_path)
    # Serialize against layout publication and other packaging of this layout.
    with lock_path.open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        create_locked(args, layout, tool)

def create_locked(args, layout, tool):
    _compiler, host = tool.rustc_host()
    if host != TARGET:
        raise ValueError(f'Release packaging currently requires a native {TARGET} build')
    if args.tag and args.tag != 'v' + version():
        raise ValueError(f'Release tag must match v{version()}')
    stamp_path = layout / 'PROVENANCE.json'
    if not stamp_path.is_file() or stamp_path.is_symlink():
        raise ValueError('Layout has no PROVENANCE.json; build it with scripts/build-gnash.py')
    stamp = json.loads(stamp_path.read_text())
    manifest = load_vendor_manifest()
    require_fresh(layout, stamp, manifest)
    head, dirty = tool.git_state()
    commit, source_dirty, matches = attest_source(head, dirty, stamp)
    if args.tag and not matches:
        raise ValueError('Tagged release packaging requires a clean tree matching the build commit')
    files = packaged_files(layout, manifest)
    epoch = int(os.environ.get('SOURCE_DATE_EPOCH') or subprocess.check_output(
        ['git', 'log', '-1', '--format=%ct'], cwd=ROOT, text=True))
    info = {
        'artifacts': {name: digest(layout / name) for name in REQUIRED},
        'cc': stamp.get('cc'),
        'libc': stamp.get('libc'),
        'rustc': stamp.get('rustc'),
        'source_commit': commit,
        'source_dirty': source_dirty,
        'source_matches_head': matches,
        'source_sha256': hashlib.sha256(json.dumps(
            [stamp['vendor_files'], stamp['source_files']], sort_keys=True).encode()).hexdigest(),
        'target': TARGET,
        'vendor_revision': manifest['revision'],
        'version': version(),
    }
    payload = json.dumps(info, indent=2, sort_keys=True).encode() + b'\n'
    basename = f'gnash-{version()}-{TARGET}'
    args.out.mkdir(parents=True, exist_ok=True)
    archive_path = args.out / (basename + '.tar.gz')

    def write_archive(temporary):
        # Ownership, mode, and timestamps are independent of the local account and umask.
        with temporary.open('wb') as raw, gzip.GzipFile(
                fileobj=raw, mode='wb', mtime=epoch, filename='') as compressed:
            with tarfile.open(fileobj=compressed, mode='w', format=tarfile.PAX_FORMAT) as archive:
                for name, source in sorted(files.items()):
                    add_regular(archive, basename + '/' + name, source, epoch)
                member = tarfile.TarInfo(basename + '/BUILD-INFO.json')
                member.size = len(payload)
                member.mtime = int(epoch)
                member.mode = 0o644
                member.type = tarfile.REGTYPE
                member.uid = member.gid = 0
                member.uname = member.gname = ''
                archive.addfile(member, io.BytesIO(payload))
        # A source edit during compression must not replace an existing good archive.
        require_fresh(layout, stamp, load_vendor_manifest())

    atomic_produce(archive_path, write_archive)
    checksum = f'{digest(archive_path)}  {archive_path.name}\n'
    atomic_produce(archive_path.with_name(archive_path.name + '.sha256'),
                   lambda path: path.write_text(checksum))
    print(archive_path)

def member_is_safe(member, basename, seen):
    name = PurePosixPath(member.name)
    if (not member.isreg() or member.issym() or member.islnk() or member.linkname
            or name.is_absolute() or '..' in name.parts or len(name.parts) < 2
            or name.parts[0] != basename or name in seen):
        raise ValueError('Unexpected archive entry')
    if member.uid or member.gid or member.uname or member.gname:
        raise ValueError('Archive contains local ownership metadata')
    if stat.S_IMODE(member.mode) not in (0o644, 0o755):
        raise ValueError('Archive file mode is not normalized')
    if member.size < 0 or member.size > MAX_MEMBER_BYTES:
        raise ValueError('Archive member is too large')
    return name

def expected_names(manifest, layout):
    names = set(REQUIRED)
    names.update(notice_paths(layout))
    names.update(PACKAGED_SOURCES)
    names.add('vendor/bash-os/manifest.json')
    names.add('BUILD-INFO.json')
    for name in manifest['files']:
        build_tool().safe_relative(name)
        names.add('vendor/bash-os/' + name)
    return names

@contextmanager
def checked_archive(archive_path):
    archive_path = archive_path.resolve()
    checksum_path = archive_path.with_name(archive_path.name + '.sha256')
    checksum = checksum_path.read_text().strip()
    if checksum != f'{digest(archive_path)}  {archive_path.name}':
        raise ValueError('Archive checksum mismatch')
    basename = f'gnash-{version()}-{TARGET}'
    manifest = load_vendor_manifest()
    with tempfile.TemporaryDirectory(prefix='gnash release ') as folder:
        destination = Path(folder)
        total = 0
        seen = set()
        with tarfile.open(archive_path, 'r:gz') as archive:
            for member in archive.getmembers():
                if len(seen) >= MAX_MEMBERS:
                    raise ValueError('Archive has too many entries')
                name = member_is_safe(member, basename, seen)
                total += member.size
                if total > MAX_ARCHIVE_BYTES:
                    raise ValueError('Archive is too large')
                seen.add(name)
                path = destination / name
                path.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, path.open('wb') as output:
                    shutil.copyfileobj(source, output)
                path.chmod(stat.S_IMODE(member.mode))
        layout = destination / basename
        names = {path.relative_to(layout).as_posix() for path in layout.rglob('*') if path.is_file()}
        expected = expected_names(manifest, layout)
        if names != expected:
            missing = sorted(expected - names)
            extra = sorted(names - expected)
            raise ValueError(f'Archive file set mismatch; missing={missing} extra={extra}')
        pinned = (ROOT / 'vendor/bash-os/manifest.json').read_bytes()
        if (layout / 'vendor/bash-os/manifest.json').read_bytes() != pinned:
            raise ValueError('Archived vendor manifest does not match the source pin')
        for name, expected_hash in manifest['files'].items():
            if digest(layout / 'vendor/bash-os' / name) != expected_hash:
                raise ValueError(f'Archived vendor source does not match the pin: {name}')
        for arcname, source_name in PACKAGED_SOURCES.items():
            if (layout / arcname).read_bytes() != (ROOT / source_name).read_bytes():
                raise ValueError(f'Archive copy of {arcname} does not match {source_name}')
        info = json.loads((layout / 'BUILD-INFO.json').read_text())
        if info.get('version') != version() or info.get('target') != TARGET:
            raise ValueError('BUILD-INFO does not match this package version or target')
        if info.get('vendor_revision') != manifest['revision']:
            raise ValueError('BUILD-INFO vendor revision does not match the pin')
        artifacts = info.get('artifacts')
        if not isinstance(artifacts, dict) or set(artifacts) != set(REQUIRED):
            raise ValueError('BUILD-INFO must hash every required artifact')
        for name, expected_hash in artifacts.items():
            if digest(layout / name) != expected_hash:
                raise ValueError(f'BUILD-INFO artifact hash mismatch: {name}')
        for name in BINARIES:
            assert_host_elf(layout / name)
        if not os.access(layout / 'gnash', os.X_OK):
            raise ValueError('The gnash binary must be executable')
        notice_paths(layout)
        example = layout / 'examples/loadables/hello.c'
        header = layout / 'include'
        compiled = layout / '.hello.so'
        subprocess.run([os.environ.get('CC', 'cc'), '-shared', '-fPIC', '-I', str(header),
                        str(example), '-o', str(compiled)], check=True)
        yield layout

def check_archive(archive_path):
    with checked_archive(archive_path):
        pass

def verify(args):
    archive_path = args.archive.resolve()
    with checked_archive(archive_path) as layout:
        environment = dict(os.environ, GNASH_TEST_BINARY=str(layout / 'gnash'))
        environment.pop('GNASH_MODULE_DIR', None)
        for suite in ('test_loadables.py', 'test_native_review.py'):
            subprocess.run([sys.executable, ROOT / 'tests/gnash' / suite], cwd=layout.parent,
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
    except (ValueError, OSError, subprocess.CalledProcessError, json.JSONDecodeError) as error:
        parser.exit(1, f'{error}\n')

if __name__ == '__main__':
    main()
