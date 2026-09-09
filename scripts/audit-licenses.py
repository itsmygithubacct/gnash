#!/usr/bin/env python3
"""Check permissive licenses in the Linux build/runtime graph; preserve notices."""
import argparse
import json
import hashlib
from pathlib import Path
import re
import shutil
import subprocess

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
    inventory = []
    missing_notices = []
    output = args.out.resolve()
    output.mkdir(parents=True, exist_ok=True)
    inventory_path = output / 'cargo-inventory.json'
    previous = json.loads(inventory_path.read_text())['packages'] if inventory_path.exists() else []
    for package in sorted((packages[i] for i in selected), key=lambda p: (p['name'], p['version'])):
        expression = package['license'] or ''
        if not expression or not permissive(expression):
            raise SystemExit(f'Non-permissive or unknown license: {package["name"]} {expression!r}')
        directory = Path(package['manifest_path']).parent
        notice_files = []
        for file in directory.iterdir():
            if file.is_file() and file.name.upper().startswith(('LICENSE', 'LICENCE', 'COPYING', 'NOTICE', 'UNLICENSE')):
                notice_files.append(file)
        for folder in ['LICENSES', 'licenses']:
            if (directory / folder).is_dir():
                notice_files.extend(f for f in (directory / folder).rglob('*') if f.is_file())
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
        target = output / f'{package["name"]}-{package["version"]}'
        if notice_files:
            target.mkdir(exist_ok=True)
            for file in set(notice_files):
                shutil.copy2(file, target / file.name)
        else:
            missing_notices.append(package['name'])
        inventory.append({'name': package['name'], 'version': package['version'],
                          'license': expression, 'source': package['source'],
                          'notices': [str((target / f.name).relative_to(output)) for f in sorted(set(notice_files))]})
    if missing_notices:
        raise SystemExit('Missing notices: ' + ', '.join(missing_notices))
    prune_generated_notices(output, inventory, previous)
    inventory_path.write_text(json.dumps({
        'target': host, 'scope': 'gnash and gnash-coreutils normal/build dependencies; excludes dev dependencies',
        'packages': inventory, 'packages_without_root_notice_files': missing_notices}, indent=2) + '\n')
    print(f'Permissive build/runtime licenses verified for {len(inventory)} packages ({host}).')

if __name__ == '__main__':
    main()
