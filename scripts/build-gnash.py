#!/usr/bin/env python3
"""Build gnash and its two native coreutils modules. No Bash toolchain is used."""
import argparse
import concurrent.futures
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / 'vendor/bash-os'
IDENT = re.compile(r'[A-Za-z_][A-Za-z0-9_]*\Z')
COMMAND = re.compile(r'[A-Za-z_][A-Za-z0-9_-]*\Z')
SOURCE_INPUTS = (
    'README.release.md',
    'LICENSE',
    'include/gnash.h',
    'examples/loadables/hello.c',
    'docs/gnash-loadables.md',
    'docs/gnash-coreutils.md',
    'licenses/README.md',
    'vendor/bash-os/manifest.json',
)
LAYOUT_ARTIFACTS = (
    'gnash',
    'lib/gnash/uutils.so',
    'lib/gnash/bash-os-coreutils.so',
    'lib/gnash/bash-os-manifest.json',
    'licenses/cargo-inventory.json',
    'licenses/brush-MIT.txt',
    'licenses/bash-os-MIT.txt',
    'licenses/bash-os-nl-regex.h',
)

def run(args):
    subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True)

def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()

def safe_relative(name):
    """Reject manifest paths that would escape the vendor or snapshot tree."""
    if not isinstance(name, str) or not name or '\\' in name:
        raise SystemExit(f'Unsafe vendor path: {name}')
    path = Path(name)
    if path.is_absolute() or any(part in ('', '.', '..') for part in name.split('/')):
        raise SystemExit(f'Unsafe vendor path: {name}')
    return path

def load_manifest():
    return json.loads((VENDOR / 'manifest.json').read_text())

def license_audit():
    import importlib.util
    spec = importlib.util.spec_from_file_location('gnash_license_audit', ROOT / 'scripts/audit-licenses.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def require_permissive_manifest(manifest, permissive):
    for name, expression in manifest.get('file_licenses', {}).items():
        safe_relative(name)
        if name not in manifest['files']:
            raise SystemExit(f'file_licenses entry is not a vendored file: {name}')
        try:
            allowed = permissive(expression)
        except ValueError as error:
            raise SystemExit(f'Invalid file license for {name}: {error}') from error
        if not allowed:
            raise SystemExit(f'Non-permissive vendored file license: {name} {expression}')

def require_vendor_checksums(manifest):
    for name, expected in manifest['files'].items():
        path = VENDOR / safe_relative(name)
        if digest(path) != expected:
            raise SystemExit(f'Vendor checksum mismatch: {name}')

def render_module(manifest):
    """Generate the descriptor table. Command names are data, not raw C tokens."""
    lines = ['/* Generated from the pinned vendor manifest. SPDX-License-Identifier: MIT */',
             '#include "gnash.h"', '#include "loadables.h"']
    table = []
    functions = set()
    for name, info in manifest['commands'].items():
        if not COMMAND.fullmatch(name):
            raise SystemExit(f'Unsafe command name: {name}')
        symbol = info['symbol']
        if not isinstance(symbol, str) or not IDENT.fullmatch(symbol):
            raise SystemExit(f'Unsafe builtin symbol: {symbol}')
        if 'subcommand' in info:
            subcommand = info['subcommand']
            if not isinstance(subcommand, str) or not COMMAND.fullmatch(subcommand):
                raise SystemExit(f'Unsafe subcommand: {subcommand!r}')
            subcommand_c = json.dumps(subcommand)
        else:
            subcommand_c = 'NULL'
        function = 'run_' + name.replace('-', '_')
        if function in functions:
            raise SystemExit(f'Command function collision: {name}')
        functions.add(function)
        lines += [f'extern struct builtin {symbol};',
                  f'static int {function}(int argc, char **argv) {{',
                  f'  return gnash_bash_run(argc, argv, &{symbol}, {subcommand_c});', '}']
        table.append(f'  {{"{name}", {function}}},')
    lines += ['static const struct gnash_command commands[] = {', *table, '};', 'GNASH_MODULE(commands)']
    return '\n'.join(lines) + '\n'

def object_file(build, relative):
    """Keep same-stem sources in different directories from sharing one .o."""
    token = hashlib.sha256(relative.encode()).hexdigest()[:12]
    safe = re.sub(r'[^A-Za-z0-9._-]+', '_', relative)
    return build / f'{safe}.{token}.o'

def tracked_source_files():
    # Include uncommitted fork sources as well as Rust, Cargo, scripts, configuration,
    # and packaged documentation. Ignored build outputs are deliberately excluded.
    names = subprocess.check_output(
        ['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'], cwd=ROOT)
    paths = {ROOT / os.fsdecode(name) for name in names.split(b'\0') if name}
    paths.update(ROOT / relative for relative in SOURCE_INPUTS)
    return sorted(path for path in paths if path.exists() or path.is_symlink())

def git_state():
    head = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    status = subprocess.check_output(
        ['git', 'status', '--porcelain=v1', '--untracked-files=all'], cwd=ROOT)
    return head, bool(status.strip())

def source_fingerprint(manifest):
    """Hash the complete source tree, including Rust and dependency inputs."""
    require_vendor_checksums(manifest)
    vendor_files = {name: digest(VENDOR / safe_relative(name)) for name in manifest['files']}
    source_files = {}
    for path in tracked_source_files():
        if not path.is_file() or not path.resolve().is_relative_to(ROOT):
            raise SystemExit(f'Packaged source input is not a regular file: {path}')
        value = digest(path)
        if path.is_symlink():
            # Workspace license symlinks are intentional; bind both link and content.
            value = hashlib.sha256((os.readlink(path) + '\0' + value).encode()).hexdigest()
        source_files[path.relative_to(ROOT).as_posix()] = value
    return vendor_files, source_files

def write_provenance(layout, *, complete, rustc=None, fingerprint=None):
    """Record the layout that was actually published. Call this last."""
    manifest = load_manifest()
    vendor_files, source_files = source_fingerprint(manifest)
    if fingerprint is not None and (vendor_files, source_files) != fingerprint:
        raise SystemExit('Source inputs changed during the build')
    head, dirty = git_state()
    artifacts = {}
    for name in LAYOUT_ARTIFACTS:
        path = layout / name
        if path.is_file() and not path.is_symlink():
            artifacts[name] = digest(path)
    if complete and set(artifacts) != set(LAYOUT_ARTIFACTS):
        missing = sorted(set(LAYOUT_ARTIFACTS) - set(artifacts))
        raise SystemExit('Refusing to record a complete build without ' + ', '.join(missing))
    cc = os.environ.get('CC', 'cc')
    cc_version = subprocess.check_output([cc, '--version'], text=True).splitlines()[0]
    document = {
        'complete': complete,
        'version': tomllib_version(),
        'target': rustc_host()[1],
        'source_commit': None if dirty else head,
        'source_dirty': dirty,
        'vendor_revision': manifest['revision'],
        'vendor_files': vendor_files,
        'source_files': source_files,
        'artifacts': artifacts,
        'rustc': rustc,
        'cc': cc_version,
        'libc': list(platform.libc_ver()),
    }
    destination = layout / 'PROVENANCE.json'
    temporary = destination.with_name(f'.PROVENANCE.json.{os.getpid()}.tmp')
    temporary.write_text(json.dumps(document, indent=2, sort_keys=True) + '\n')
    os.replace(temporary, destination)
    return document

def tomllib_version():
    import tomllib
    return tomllib.loads((ROOT / 'gnash/Cargo.toml').read_text())['package']['version']

def rustc_host():
    rustc = shutil.which('rustc') or str(Path.home() / '.cargo/bin/rustc')
    text = subprocess.check_output([rustc, '-vV'], text=True)
    return text, re.search(r'^host: (.+)$', text, re.M)[1]

def atomic_copy(source, dest):
    """Copy a finished file over dest. A failed copy leaves dest unchanged."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_name(f'.{dest.name}.{os.getpid()}.tmp')
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, dest)
    finally:
        if temporary.exists():
            temporary.unlink()

def link_shared(command, output):
    """Link to a temporary sibling. gcc deletes the -o path when collect2 fails."""
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f'.{output.name}.{os.getpid()}.tmp')
    try:
        subprocess.run([*command, '-o', str(temporary)], cwd=ROOT, check=True)
        os.replace(temporary, output)
    finally:
        if temporary.exists():
            temporary.unlink()

def reject_output_symlink(output):
    for path in (output, *output.parents):
        if path.is_symlink():
            raise SystemExit(f'Refusing to build through symlink: {path}')

def snapshot_sources(manifest, build):
    root = build / 'src'
    for name in manifest['files']:
        relative = safe_relative(name)
        destination = root / 'vendor' / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(VENDOR / relative, destination)
    shutil.copytree(ROOT / 'loadables/compat', root / 'compat')
    (root / 'include').mkdir()
    shutil.copy2(ROOT / 'include/gnash.h', root / 'include/gnash.h')
    return root

def compile_module(manifest, build, snapshot):
    cc = os.environ.get('CC', 'cc')
    flags = ['-std=gnu11', '-D_GNU_SOURCE', '-O2', '-fPIC', '-Wall', '-Wextra',
             '-DGNASH_BASH_OS_REVISION=' + json.dumps(manifest['revision'][:12]),
             '-Werror=implicit-function-declaration', '-I', snapshot / 'compat',
             '-I', snapshot / 'vendor/loadables/common', '-I', snapshot / 'include',
             f'-ffile-prefix-map={snapshot}=/gnash',
             f'-ffile-prefix-map={ROOT}=/gnash']
    wrapper = snapshot / 'module.c'
    wrapper.write_text(render_module(manifest))
    sources = []
    for name in manifest['files']:
        if name.endswith('.c'):
            sources.append((f'vendor/{name}', snapshot / 'vendor' / safe_relative(name)))
    sources.append(('compat/adapter.c', snapshot / 'compat/adapter.c'))
    sources.append(('module.c', wrapper))
    objects = []
    jobs = []
    for relative, source in sources:
        obj = object_file(build, relative)
        objects.append(obj)
        seed = hashlib.sha256(relative.encode()).hexdigest()[:16]
        jobs.append((source, obj, seed))

    def compile_source(job):
        source, obj, seed = job
        run([cc, *flags, f'-frandom-seed={seed}', '-c', source, '-o', obj])

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(os.cpu_count() or 1, 8)) as pool:
        list(pool.map(compile_source, jobs))
    return cc, objects

def publish_bash_os_notices(layout):
    notices = layout / 'licenses'
    notices.mkdir(parents=True, exist_ok=True)
    atomic_copy(ROOT / 'LICENSE', notices / 'brush-MIT.txt')
    atomic_copy(VENDOR / 'LICENSE', notices / 'bash-os-MIT.txt')
    # The optional nl regex fallback retains musl's MIT and TRE's BSD notices.
    atomic_copy(VENDOR / 'loadables/common/nl-regex.h', notices / 'bash-os-nl-regex.h')

def build_locked(args, output):
    manifest = load_manifest()
    require_vendor_checksums(manifest)
    require_permissive_manifest(manifest, license_audit().permissive)
    before = source_fingerprint(manifest)
    for name in ('lib/gnash', 'licenses', '.build.lock', 'PROVENANCE.json'):
        reject_output_symlink(output / name)
    lib = output / 'lib/gnash'
    lib.mkdir(parents=True, exist_ok=True)
    module_output = lib / 'bash-os-coreutils.so'
    build = Path(tempfile.mkdtemp(prefix='gnash-c-'))
    staged = build / 'layout'
    (staged / 'lib/gnash').mkdir(parents=True)
    staged_module = staged / 'lib/gnash/bash-os-coreutils.so'
    try:
        snapshot = snapshot_sources(manifest, build)
        cc, objects = compile_module(manifest, build, snapshot)
        subprocess.run([cc, '-shared', '-Wl,-z,defs', '-Wl,-z,relro,-z,now', '-Wl,--build-id=none',
                        *objects, '-lm', '-ldl', '-o', str(staged_module)], cwd=ROOT, check=True)
        if source_fingerprint(manifest) != before:
            raise SystemExit('Source inputs changed during the build')
        if args.c_only:
            atomic_copy(VENDOR / 'manifest.json', staged / 'lib/gnash/bash-os-manifest.json')
            publish_bash_os_notices(staged)
            # A C-only rebuild does not refresh the Rust artifacts, so the layout
            # cannot be attested as a complete release.
            write_provenance(staged, complete=False, fingerprint=before)
        else:
            cargo = shutil.which('cargo') or str(Path.home() / '.cargo/bin/cargo')
            command = [cargo, 'build', '--locked', '-p', 'gnash', '-p', 'gnash-coreutils']
            if args.release:
                command.append('--release')
            run(command)
            compiler, _host = rustc_host()
            target = Path(os.environ.get('CARGO_TARGET_DIR', ROOT / 'target'))
            profile = target / ('release' if args.release else 'debug')
            run([sys.executable, ROOT / 'scripts/audit-licenses.py', '--out', staged / 'licenses'])
            publish_bash_os_notices(staged)
            atomic_copy(profile / 'gnash', staged / 'gnash')
            atomic_copy(profile / 'libgnash_coreutils.so', staged / 'lib/gnash/uutils.so')
            atomic_copy(VENDOR / 'manifest.json', staged / 'lib/gnash/bash-os-manifest.json')
            write_provenance(staged, complete=True, rustc=compiler.splitlines()[0], fingerprint=before)
        # Finish all compilation, auditing, and input checks before replacing live files.
        if not args.c_only:
            inventory = json.loads((staged / 'licenses/cargo-inventory.json').read_text())
            previous_path = output / 'licenses/cargo-inventory.json'
            previous = json.loads(previous_path.read_text())['packages'] if previous_path.exists() else []
            license_audit().publish_notices(staged / 'licenses', output / 'licenses',
                                           inventory['packages'], previous)
        for path in sorted(staged.rglob('*')):
            if not path.is_file() or path.name == 'PROVENANCE.json':
                continue
            relative = path.relative_to(staged)
            if not args.c_only and relative.parts[0] == 'licenses':
                continue
            atomic_copy(path, output / relative)
        atomic_copy(staged / 'PROVENANCE.json', output / 'PROVENANCE.json')
        print(f'Built {module_output} ({len(manifest["commands"])} commands)')
        if not args.c_only:
            print(f'Run {output}/gnash')
    finally:
        if staged_module.exists():
            staged_module.unlink()
        shutil.rmtree(build, ignore_errors=True)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', action='store_true')
    parser.add_argument('--c-only', action='store_true',
                        help='rebuild the bash-os module without attesting a release layout')
    parser.add_argument('--out', type=Path, default=ROOT / 'out')
    args = parser.parse_args()
    output = args.out.absolute()
    reject_output_symlink(output)
    output.mkdir(parents=True, exist_ok=True)
    lock_path = output / '.build.lock'
    reject_output_symlink(lock_path)
    with lock_path.open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            build_locked(args, output)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

if __name__ == '__main__':
    main()
