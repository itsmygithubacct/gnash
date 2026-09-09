#!/usr/bin/env python3
"""Build gnash and its two native coreutils modules. No Bash toolchain is used."""
import argparse
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parent.parent

def run(args):
    subprocess.run([str(arg) for arg in args], cwd=ROOT, check=True)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", action="store_true")
    parser.add_argument("--c-only", action="store_true", help="rebuild just the bash-os module")
    parser.add_argument("--out", type=Path, default=ROOT / "out")
    args = parser.parse_args()
    output = args.out.resolve()
    lib = output / "lib/gnash"
    lib.mkdir(parents=True, exist_ok=True)
    build = ROOT / "target/gnash-c"
    build.mkdir(parents=True, exist_ok=True)
    vendor = ROOT / "vendor/bash-os"
    manifest = json.loads((vendor / "manifest.json").read_text())
    for name, expected in manifest["files"].items():
        if hashlib.sha256((vendor / name).read_bytes()).hexdigest() != expected:
            raise SystemExit(f"Vendor checksum mismatch: {name}")
    cc = os.environ.get("CC", "cc")
    flags = ["-std=gnu11", "-D_GNU_SOURCE", "-O2", "-fPIC", "-Wall", "-Wextra",
             "-Werror=implicit-function-declaration", "-I", ROOT / "loadables/compat",
             "-I", vendor / "loadables/common", "-I", ROOT / "include"]
    lines = ['/* Generated from the pinned vendor manifest. SPDX-License-Identifier: MIT */',
             '#include "gnash.h"', '#include "loadables.h"']
    table = []
    for name, info in manifest["commands"].items():
        symbol = info["symbol"]
        subcommand = json.dumps(info["subcommand"]) if "subcommand" in info else "NULL"
        lines += [f'extern struct builtin {symbol};',
                  f'static int run_{name}(int argc, char **argv) {{',
                  f'  return gnash_bash_run(argc, argv, &{symbol}, {subcommand});', '}']
        table.append(f'  {{"{name}", run_{name}}},')
    lines += ['static const struct gnash_command commands[] = {', *table, '};', 'GNASH_MODULE(commands)']
    wrapper = build / "module.c"
    wrapper.write_text('\n'.join(lines) + '\n')
    sources = [vendor / name for name in manifest["files"] if name.endswith('.c')]
    sources += [ROOT / "loadables/compat/adapter.c", wrapper]
    objects = [build / (source.stem + '.o') for source in sources]
    def compile_source(pair):
        source, obj = pair
        run([cc, *flags, '-c', source, '-o', obj])
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(os.cpu_count() or 1, 8)) as pool:
        list(pool.map(compile_source, zip(sources, objects)))
    run([cc, '-shared', '-Wl,-z,defs', '-Wl,-z,relro,-z,now', *objects,
         '-lm', '-ldl', '-o', lib / 'bash-os-coreutils.so'])
    if not args.c_only:
        cargo = shutil.which('cargo') or str(Path.home() / '.cargo/bin/cargo')
        command = [cargo, 'build', '--locked', '-p', 'gnash', '-p', 'gnash-coreutils']
        if args.release:
            command.append('--release')
        run(command)
        target = Path(os.environ.get('CARGO_TARGET_DIR', ROOT / 'target'))
        profile = target / ('release' if args.release else 'debug')
        shutil.copy2(profile / 'gnash', output / 'gnash')
        shutil.copy2(profile / 'libgnash_coreutils.so', lib / 'uutils.so')
        run([sys.executable, ROOT / 'scripts/audit-licenses.py', '--out', output / 'licenses'])
    notices = output / 'licenses'
    notices.mkdir(exist_ok=True)
    shutil.copy2(ROOT / 'LICENSE', notices / 'brush-MIT.txt')
    shutil.copy2(vendor / 'LICENSE', notices / 'bash-os-MIT.txt')
    shutil.copy2(vendor / 'manifest.json', lib / 'bash-os-manifest.json')
    print(f'Built {lib}/bash-os-coreutils.so ({len(manifest["commands"])} commands)')
    if not args.c_only:
        print(f'Run {output}/gnash')

if __name__ == '__main__':
    main()
