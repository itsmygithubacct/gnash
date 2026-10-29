#!/usr/bin/env python3
"""Check permissive licenses in the Linux build/runtime graph; preserve notices."""
import argparse
import json
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
ALLOWED = {'MIT', 'MIT-0', 'Apache-2.0', 'BSD-2-Clause', 'BSD-3-Clause', 'ISC',
           '0BSD', 'Zlib', 'Unicode-3.0', 'Unicode-DFS-2016', 'Unlicense',
           'CC0-1.0', 'WTFPL', 'BSL-1.0', 'NCSA'}

def permissive(expression):
    """Evaluate AND/OR/parentheses, including legacy Cargo slash alternatives."""
    tokens = re.findall(r'\(|\)|[^\s()]+', expression.replace('/', ' OR '))
    index = 0
    def atom():
        nonlocal index
        if index >= len(tokens):
            raise ValueError('incomplete license expression')
        token = tokens[index]
        index += 1
        if token == '(':
            value = disjunction()
            if index >= len(tokens) or tokens[index] != ')':
                raise ValueError('unbalanced license expression')
            index += 1
            return value
        value = token in ALLOWED
        if index < len(tokens) and tokens[index] == 'WITH':
            index += 1
            if index >= len(tokens):
                raise ValueError('missing license exception')
            value = value and token == 'Apache-2.0' and tokens[index] == 'LLVM-exception'
            index += 1
        return value
    def conjunction():
        nonlocal index
        value = atom()
        while index < len(tokens) and tokens[index] == 'AND':
            index += 1
            right = atom()
            value = value and right
        return value
    def disjunction():
        nonlocal index
        value = conjunction()
        while index < len(tokens) and tokens[index] == 'OR':
            index += 1
            right = conjunction()
            value = value or right
        return value
    result = disjunction()
    if index != len(tokens):
        raise ValueError('unexpected license expression token')
    return result

def prune_generated_notices(output, inventory, previous):
    """Remove obsolete files only inside package directories owned by this audit."""
    directories = set()
    expected = set()
    for package in [*previous, *inventory]:
        name, version = package['name'], package['version']
        if not re.fullmatch(r'[A-Za-z0-9_-]+', name) or not re.fullmatch(r'[A-Za-z0-9.+-]+', version):
            raise ValueError('Invalid package directory in license inventory')
        directories.add(output / f'{name}-{version}')
    for package in inventory:
        directory = output / f'{package["name"]}-{package["version"]}'
        for notice in package['notices']:
            path = output / notice
            if path.parent != directory or path.name in ('', '.', '..'):
                raise ValueError('Notice path is outside its package directory')
            expected.add(path)
    # Validate every managed directory before removing anything. Top-level notices
    # from gnash/bash-os and unrelated output directories are not managed here.
    if any(directory.is_symlink() for directory in directories):
        raise ValueError('Managed notice directories must not be symlinks')
    for directory in directories:
        if not directory.exists():
            continue
        for path in directory.iterdir():
            if path not in expected:
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path)
                else:
                    path.unlink()
        if not any(directory.iterdir()):
            directory.rmdir()

def notice_sources(package, directory, supplements):
    """Collect notice files. Identical names may alias one text; conflicting texts fail."""
    notice_files = []
    if directory.exists():
        for file in directory.iterdir():
            if file.is_file() and file.name.upper().startswith(('LICENSE', 'LICENCE', 'COPYING', 'NOTICE', 'UNLICENSE')):
                notice_files.append(file)
        for folder in ['LICENSES', 'licenses']:
            if (directory / folder).is_dir():
                notice_files.extend(file for file in (directory / folder).rglob('*') if file.is_file())
    for notice in supplements:
        if notice['package'] == package['name'] and notice['version'] == package['version']:
            file = ROOT / 'licenses/supplemental' / notice['file']
            if hashlib.sha256(file.read_bytes()).hexdigest() != notice['sha256']:
                raise SystemExit(f'Supplemental notice checksum mismatch: {file}')
            notice_files.append(file)
    if package.get('license_file'):
        notice_files.append(directory / package['license_file'])
    if package['source'] is None and not notice_files:
        notice_files.append(ROOT / 'LICENSE')
    chosen = {}
    for file in notice_files:
        if not file.is_file():
            raise SystemExit(f'Missing license notice: {file}')
        previous = chosen.get(file.name)
        if previous is not None and hashlib.sha256(previous.read_bytes()).digest() != hashlib.sha256(file.read_bytes()).digest():
            raise SystemExit(f'License notice name collision: {package["name"]} {file.name}')
        chosen[file.name] = file
    return chosen

def reject_parent_symlinks(root, relative):
    if root.is_symlink():
        raise ValueError(f'Refusing to publish through symlink: {root}')
    cursor = root
    for part in relative.parts[:-1]:
        cursor = cursor / part
        if cursor.is_symlink():
            raise ValueError(f'Refusing to publish through symlink: {cursor}')

def install_file(source, dest):
    """Replace dest atomically. A symlink at dest is replaced; it is not followed."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    temporary = dest.with_name(f'.{dest.name}.{os.getpid()}.tmp')
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, dest)
    finally:
        if temporary.exists():
            temporary.unlink()

def validate_managed_packages(packages):
    """Reject inventory names prune would refuse, before any notice is published."""
    for package in packages:
        name, version = package['name'], package['version']
        if not re.fullmatch(r'[A-Za-z0-9_-]+', name) or not re.fullmatch(r'[A-Za-z0-9.+-]+', version):
            raise ValueError('Invalid package directory in license inventory')
        directory = f'{name}-{version}'
        for notice in package.get('notices') or []:
            path = Path(notice)
            if (path.is_absolute() or '..' in path.parts or '.' in path.parts
                    or path.parent.as_posix() != directory):
                raise ValueError('Notice path is outside its package directory')

def publish_notices(staging, output, inventory, previous):
    """Publish a completed staging tree. Failures before this call leave output unchanged."""
    validate_managed_packages(inventory)
    validate_managed_packages(previous)
    output.mkdir(parents=True, exist_ok=True)
    if output.is_symlink():
        raise ValueError(f'Refusing to publish through symlink: {output}')
    managed = [output / f'{package["name"]}-{package["version"]}' for package in (*previous, *inventory)]
    if any(directory.is_symlink() for directory in managed):
        raise ValueError('Managed notice directories must not be symlinks')
    files = [path for path in staging.rglob('*') if path.is_file() and path.name != 'cargo-inventory.json']
    for path in files:
        relative = path.relative_to(staging)
        reject_parent_symlinks(output, relative)
    for path in files:
        install_file(path, output / path.relative_to(staging))
    prune_generated_notices(output, inventory, previous)
    inventory_file = staging / 'cargo-inventory.json'
    reject_parent_symlinks(output, Path('cargo-inventory.json'))
    install_file(inventory_file, output / 'cargo-inventory.json')

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, default=ROOT / 'out/licenses')
    args = parser.parse_args()
    cargo = shutil.which('cargo') or str(Path.home() / '.cargo/bin/cargo')
    rustc = shutil.which('rustc') or str(Path.home() / '.cargo/bin/rustc')
    host = re.search(r'^host: (.+)$', subprocess.check_output([rustc, '-vV'], text=True), re.M)[1]
    metadata = json.loads(subprocess.check_output(
        [cargo, 'metadata', '--locked', '--format-version', '1', '--filter-platform', host], cwd=ROOT))
    packages = {p['id']: p for p in metadata['packages']}
    nodes = {n['id']: n for n in metadata['resolve']['nodes']}
    stack = [i for i, p in packages.items() if p['name'] in ('gnash', 'gnash-coreutils')]
    selected = set()
    while stack:
        package_id = stack.pop()
        if package_id in selected:
            continue
        selected.add(package_id)
        stack.extend(d['pkg'] for d in nodes[package_id]['deps']
                     if any(k['kind'] != 'dev' for k in d['dep_kinds']))
    supplements = json.loads((ROOT / 'licenses/supplemental/manifest.json').read_text())
    output = args.out.absolute()
    for path in (output, *output.parents):
        if path.is_symlink():
            raise SystemExit(f'Refusing to publish through symlink: {path}')
    output.parent.mkdir(parents=True, exist_ok=True)
    inventory_path = output / 'cargo-inventory.json'
    if inventory_path.is_symlink():
        raise SystemExit(f'Refusing to read license inventory through symlink: {inventory_path}')
    if inventory_path.exists():
        try:
            previous = json.loads(inventory_path.read_text())['packages']
        except (OSError, json.JSONDecodeError, KeyError) as error:
            raise SystemExit(f'Unreadable license inventory: {inventory_path}') from error
    else:
        previous = []
    staging = Path(tempfile.mkdtemp(prefix='.gnash-licenses-', dir=output.parent))
    try:
        inventory = []
        notice_sha256 = {}
        missing_notices = []
        for package in sorted((packages[i] for i in selected), key=lambda item: (item['name'], item['version'])):
            expression = package['license'] or ''
            if not expression or not permissive(expression):
                raise SystemExit(f'Non-permissive or unknown license: {package["name"]} {expression!r}')
            directory = Path(package['manifest_path']).parent
            chosen = notice_sources(package, directory, supplements)
            target = staging / f'{package["name"]}-{package["version"]}'
            notices = []
            if chosen:
                target.mkdir(parents=True)
                for name, file in sorted(chosen.items()):
                    shutil.copy2(file, target / name)
                    relative = f'{package["name"]}-{package["version"]}/{name}'
                    notices.append(relative)
                    notice_sha256[relative] = hashlib.sha256((target / name).read_bytes()).hexdigest()
            else:
                missing_notices.append(package['name'])
            inventory.append({'name': package['name'], 'version': package['version'],
                              'license': expression, 'source': package['source'],
                              'notices': notices})
        if missing_notices:
            raise SystemExit('Missing notices: ' + ', '.join(missing_notices))
        document = {
            'target': host,
            'scope': 'gnash and gnash-coreutils normal/build dependencies; excludes dev dependencies',
            'packages': inventory,
            'notice_sha256': notice_sha256,
            'packages_without_root_notice_files': missing_notices,
        }
        (staging / 'cargo-inventory.json').write_text(json.dumps(document, indent=2) + '\n')
        publish_notices(staging, output, inventory, previous)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    print(f'Permissive build/runtime licenses verified for {len(inventory)} packages ({host}).')

if __name__ == '__main__':
    main()
