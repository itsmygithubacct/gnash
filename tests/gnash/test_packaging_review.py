#!/usr/bin/env python3
"""Regressions for release atomicity, provenance, and archive checks."""
import argparse
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tarfile
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BUILD = load('gnash_build_review', 'scripts/build-gnash.py')
PACKAGE = load('gnash_package_review', 'scripts/package-gnash.py')
AUDIT = load('gnash_audit_review', 'scripts/audit-licenses.py')
VENDOR = load('gnash_vendor_review', 'scripts/vendor-bash-os.py')


def elf_bytes():
    data = bytearray(64)
    data[0:4] = b'\x7fELF'
    data[4] = 2
    data[5] = 1
    data[6] = 1
    data[18:20] = (PACKAGE.ELF_MACHINE_X86_64).to_bytes(2, 'little')
    return bytes(data)


class AtomicPublishTests(unittest.TestCase):
    def test_failed_shared_link_preserves_existing_module(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            good = directory / 'ok.c'
            good.write_text('int f(void){return 1;}\n')
            output = directory / 'bash-os-coreutils.so'
            cc = os.environ.get('CC', 'cc')
            BUILD.link_shared([cc, '-shared', '-fPIC', str(good)], output)
            original = output.read_bytes()
            bad = directory / 'bad.c'
            bad.write_text('extern void missing(void); int f(void){missing(); return 0;}\n')
            with self.assertRaises(subprocess.CalledProcessError):
                BUILD.link_shared(
                    [cc, '-shared', '-fPIC', '-Wl,-z,defs', str(bad)], output)
            self.assertEqual(output.read_bytes(), original)

    def test_failed_archive_write_preserves_previous_bytes(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / 'gnash.tar.gz'
            destination.write_bytes(b'previous-good-archive')

            def fail(temporary):
                temporary.write_bytes(b'truncated')
                raise RuntimeError('injected')

            with self.assertRaises(RuntimeError):
                PACKAGE.atomic_produce(destination, fail)
            self.assertEqual(destination.read_bytes(), b'previous-good-archive')
            self.assertFalse(list(Path(folder).glob('.gnash.tar.gz.*.tmp')))

    def test_object_paths_keep_same_stem_sources_apart(self):
        build = Path('/tmp/gnash-objects')
        self.assertNotEqual(
            BUILD.object_file(build, 'loadables/common/module.c'),
            BUILD.object_file(build, 'loadables/module.c'))

    def test_vendor_path_rejects_escape_before_write(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for name in ('../outside', '/etc/passwd', 'foo/../../etc/passwd', 'foo\\bar',
                         '', '.', 'foo/./bar', 'foo//bar', 'foo/'):
                with self.assertRaises(SystemExit):
                    VENDOR.safe_relative(name)
                with self.assertRaises(SystemExit):
                    BUILD.safe_relative(name)
            self.assertEqual(list(root.iterdir()), [])
            VENDOR.publish_blobs(root, {'loadables/wc.c': b'int x;\n'})
            self.assertEqual((root / 'loadables/wc.c').read_bytes(), b'int x;\n')

    def test_vendor_parent_symlink_is_rejected_before_any_write(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            vendor, outside = root / 'vendor', root / 'outside'
            vendor.mkdir()
            outside.mkdir()
            (vendor / 'loadables').symlink_to(outside, target_is_directory=True)
            with self.assertRaises(SystemExit):
                VENDOR.publish_blobs(vendor, {'LICENSE': b'notice', 'loadables/wc.c': b'code'})
            self.assertEqual(list(outside.iterdir()), [])
            self.assertFalse((vendor / 'LICENSE').exists())


class ProvenanceTests(unittest.TestCase):
    def test_fingerprint_covers_rust_dependencies_scripts_and_new_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            names = ('Cargo.toml', 'Cargo.lock', '.cargo/config.toml', 'gnash/src/main.rs',
                     'scripts/build.py', 'licenses/supplemental/manifest.json')
            for name in names:
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('original\n')
            with mock.patch.multiple(BUILD, ROOT=root, VENDOR=root / 'vendor', SOURCE_INPUTS=()):
                manifest = {'files': {}}
                before = BUILD.source_fingerprint(manifest)
                self.assertEqual(set(before[1]), set(names))
                for name in names:
                    with self.subTest(name=name):
                        (root / name).write_text('changed\n')
                        self.assertNotEqual(BUILD.source_fingerprint(manifest), before)
                        (root / name).write_text('original\n')
                (root / 'gnash/src/new.rs').write_text('new code\n')
                self.assertNotEqual(BUILD.source_fingerprint(manifest), before)

    def test_output_symlinks_are_rejected_by_build_cli(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            outside = root / 'outside'
            outside.mkdir()
            output = root / 'layout'
            output.symlink_to(outside, target_is_directory=True)
            for path in (output, root / 'nested'):
                if path.name == 'nested':
                    path.mkdir()
                    (path / 'lib').symlink_to(outside, target_is_directory=True)
                result = subprocess.run(
                    ['python3', str(ROOT / 'scripts/build-gnash.py'), '--c-only', '--out', str(path)],
                    capture_output=True, text=True, timeout=30)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('symlink', result.stderr)
                self.assertEqual(list(outside.iterdir()), [])

    def test_attest_source_requires_a_clean_matching_commit(self):
        cases = [
            ('abc', True, {'source_commit': 'abc', 'source_dirty': False}, (None, True, False)),
            ('abc', False, {'source_commit': 'abc', 'source_dirty': True}, (None, True, False)),
            ('abc', False, {'source_commit': 'def', 'source_dirty': False}, (None, False, False)),
            ('abc', False, {'source_commit': None, 'source_dirty': False}, (None, False, False)),
            ('abc', False, {'source_commit': 'abc', 'source_dirty': False}, ('abc', False, True)),
        ]
        for head, dirty, stamp, expected in cases:
            with self.subTest(head=head, dirty=dirty, stamp=stamp):
                self.assertEqual(PACKAGE.attest_source(head, dirty, stamp), expected)

    def test_non_elf_binary_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'gnash'
            path.write_bytes(b'not an elf')
            with self.assertRaises(ValueError):
                PACKAGE.assert_host_elf(path)

    def test_unsafe_command_and_nonpermissive_vendor_file_are_rejected(self):
        with self.assertRaises(SystemExit):
            BUILD.render_module({'commands': {'bad name': {'symbol': 'ok_struct'}}})
        with self.assertRaises(SystemExit):
            BUILD.render_module({'commands': {'ok': {'symbol': 'not a symbol'}}})
        manifest = BUILD.load_manifest()
        rendered = BUILD.render_module(manifest)
        for name in manifest['commands']:
            self.assertIn(f'{{"{name}", ', rendered)
        BUILD.require_permissive_manifest(manifest, AUDIT.permissive)
        with self.assertRaises(SystemExit):
            BUILD.require_permissive_manifest(
                {'files': {'loadables/common/nl-regex.h': 'abc'},
                 'file_licenses': {'loadables/common/nl-regex.h': 'GPL-3.0-only'}},
                AUDIT.permissive)

    def test_conflicting_notice_names_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            (directory / 'LICENSE').write_text('mit\n')
            bundled = directory / 'licenses'
            bundled.mkdir()
            (bundled / 'LICENSE').write_text('different\n')
            package = {'name': 'example', 'version': '1.0.0', 'source': 'registry+example',
                       'license_file': None}
            with self.assertRaises(SystemExit):
                AUDIT.notice_sources(package, directory, [])

    def test_symlink_package_directory_is_not_written_through(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            outside = root / 'outside'
            outside.mkdir()
            (outside / 'SECRET').write_text('secret')
            output = root / 'licenses'
            output.mkdir()
            (output / 'example-1.0.0').symlink_to(outside, target_is_directory=True)
            staging = root / 'staging'
            staged = staging / 'example-1.0.0'
            staged.mkdir(parents=True)
            (staged / 'LICENSE').write_text('mit\n')
            (staging / 'cargo-inventory.json').write_text('{}\n')
            inventory = [{'name': 'example', 'version': '1.0.0', 'notices': ['example-1.0.0/LICENSE']}]
            with self.assertRaises(ValueError):
                AUDIT.publish_notices(staging, output, inventory, [])
            self.assertEqual((outside / 'SECRET').read_text(), 'secret')
            self.assertFalse((outside / 'LICENSE').exists())

    def test_invalid_previous_inventory_does_not_publish(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            output = root / 'licenses'
            output.mkdir()
            marker = output / 'kept.txt'
            marker.write_text('kept')
            staging = root / 'staging'
            staged = staging / 'example-1.0.0'
            staged.mkdir(parents=True)
            (staged / 'LICENSE').write_text('mit\n')
            (staging / 'cargo-inventory.json').write_text('{}\n')
            inventory = [{'name': 'example', 'version': '1.0.0', 'notices': ['example-1.0.0/LICENSE']}]
            previous = [{'name': '../outside', 'version': '1.0.0'}]
            with self.assertRaises(ValueError):
                AUDIT.publish_notices(staging, output, inventory, previous)
            self.assertEqual(marker.read_text(), 'kept')
            self.assertFalse((output / 'example-1.0.0').exists())
            self.assertFalse((output / 'cargo-inventory.json').exists())


class ReleaseArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='gnash package review ')
        self.addCleanup(self.temp.cleanup)
        self.layout = Path(self.temp.name) / 'layout'
        self.populate_layout()
        epoch = os.environ.get('SOURCE_DATE_EPOCH')
        os.environ['SOURCE_DATE_EPOCH'] = '1700000000'
        self.addCleanup(self.restore_epoch, epoch)

    @staticmethod
    def restore_epoch(previous):
        if previous is None:
            os.environ.pop('SOURCE_DATE_EPOCH', None)
        else:
            os.environ['SOURCE_DATE_EPOCH'] = previous

    def populate_layout(self):
        binary = elf_bytes()
        for name in PACKAGE.BINARIES:
            path = self.layout / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(binary)
            path.chmod(0o700)
        manifest = ROOT / 'vendor/bash-os/manifest.json'
        shutil.copy2(manifest, self.layout / 'lib/gnash/bash-os-manifest.json')
        (self.layout / 'licenses').mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / 'LICENSE', self.layout / 'licenses/brush-MIT.txt')
        shutil.copy2(ROOT / 'vendor/bash-os/LICENSE', self.layout / 'licenses/bash-os-MIT.txt')
        shutil.copy2(ROOT / 'vendor/bash-os/loadables/common/nl-regex.h',
                     self.layout / 'licenses/bash-os-nl-regex.h')
        notice = self.layout / 'licenses/example-1.0.0/LICENSE'
        notice.parent.mkdir(parents=True)
        notice.write_text('permission notice\n')
        digest = hashlib.sha256(notice.read_bytes()).hexdigest()
        inventory = {
            'target': PACKAGE.TARGET,
            'scope': 'test',
            'packages': [{
                'name': 'example',
                'version': '1.0.0',
                'license': 'MIT',
                'source': 'registry+test',
                'notices': ['example-1.0.0/LICENSE'],
            }],
            'notice_sha256': {'example-1.0.0/LICENSE': digest},
            'packages_without_root_notice_files': [],
        }
        (self.layout / 'licenses/cargo-inventory.json').write_text(json.dumps(inventory, indent=2) + '\n')
        BUILD.write_provenance(self.layout, complete=True, rustc='rustc test')

    def create_args(self, out, tag=None):
        return argparse.Namespace(layout=self.layout, out=out, tag=tag)

    def archive_path(self, out):
        name = f'gnash-{PACKAGE.version()}-{PACKAGE.TARGET}.tar.gz'
        return out / name

    def test_archive_is_normalized_deterministic_and_honest(self):
        out_a = Path(self.temp.name) / 'dist-a'
        out_b = Path(self.temp.name) / 'dist-b'
        PACKAGE.create(self.create_args(out_a))
        PACKAGE.create(self.create_args(out_b))
        first = self.archive_path(out_a)
        second = self.archive_path(out_b)
        self.assertEqual(first.read_bytes(), second.read_bytes())
        self.assertEqual((out_a / (first.name + '.sha256')).read_text(),
                         f'{PACKAGE.digest(first)}  {first.name}\n')
        with tarfile.open(first, 'r:gz') as archive:
            modes = {}
            owners = set()
            for member in archive.getmembers():
                self.assertTrue(member.isreg())
                self.assertFalse(member.linkname)
                modes[PureName(member.name)] = stat.S_IMODE(member.mode)
                owners.add((member.uid, member.gid, member.uname, member.gname))
        self.assertEqual(owners, {(0, 0, '', '')})
        self.assertEqual(modes['LICENSE'], 0o644)
        self.assertEqual(modes['gnash'], 0o755)
        self.assertEqual(modes['lib/gnash/bash-os-coreutils.so'], 0o755)
        PACKAGE.check_archive(first)
        info = json.loads(archive_member(first, 'BUILD-INFO.json'))
        head, dirty = BUILD.git_state()
        if dirty:
            self.assertIsNone(info['source_commit'])
            self.assertTrue(info['source_dirty'])
            self.assertFalse(info['source_matches_head'])
        else:
            self.assertEqual(info['source_commit'], head)
            self.assertFalse(info['source_dirty'])
            self.assertTrue(info['source_matches_head'])
        self.assertEqual(info['vendor_revision'], BUILD.load_manifest()['revision'])

    def test_stale_binary_and_tagged_dirty_tree_are_refused(self):
        binary = self.layout / 'gnash'
        binary.write_bytes(binary.read_bytes() + b'\x00')
        with self.assertRaises(ValueError):
            PACKAGE.require_fresh(self.layout, json.loads((self.layout / 'PROVENANCE.json').read_text()),
                                  BUILD.load_manifest())
        binary.write_bytes(elf_bytes())
        binary.chmod(0o700)
        _head, dirty = BUILD.git_state()
        out = Path(self.temp.name) / 'tagged'
        if dirty:
            with self.assertRaises(ValueError):
                PACKAGE.create(self.create_args(out, tag='v' + PACKAGE.version()))
            self.assertFalse(out.exists())
        else:
            PACKAGE.create(self.create_args(out, tag='v' + PACKAGE.version()))

    def test_failed_member_write_does_not_replace_archive(self):
        out = Path(self.temp.name) / 'dist'
        PACKAGE.create(self.create_args(out))
        archive = self.archive_path(out)
        original = archive.read_bytes()
        original_add = PACKAGE.add_regular

        def fail(*_args, **_kwargs):
            raise RuntimeError('injected')

        PACKAGE.add_regular = fail
        try:
            with self.assertRaises(RuntimeError):
                PACKAGE.create(self.create_args(out))
        finally:
            PACKAGE.add_regular = original_add
        self.assertEqual(archive.read_bytes(), original)

    def test_extra_and_oversized_members_are_rejected(self):
        basename = f'gnash-{PACKAGE.version()}-{PACKAGE.TARGET}'
        huge = tarfile.TarInfo(f'{basename}/README.md')
        huge.size = PACKAGE.MAX_MEMBER_BYTES + 1
        huge.mode = 0o644
        huge.type = tarfile.REGTYPE
        with self.assertRaises(ValueError):
            PACKAGE.member_is_safe(huge, basename, set())
        extra = tarfile.TarInfo(f'{basename}/extra.so')
        extra.size = 1
        extra.mode = 0o644
        extra.type = tarfile.REGTYPE
        extra.uid = extra.gid = 0
        extra.uname = extra.gname = ''
        self.assertTrue(PACKAGE.member_is_safe(extra, basename, set()))
        names = PACKAGE.expected_names(BUILD.load_manifest(), self.layout)
        self.assertNotIn('extra.so', names)
        self.assertIn('vendor/bash-os/manifest.json', names)
        self.assertIn('examples/loadables/hello.c', names)
        self.assertIn('docs/gnash-loadables.md', names)

    def test_build_info_cannot_omit_artifact_checksums_or_escape_layout(self):
        out = Path(self.temp.name) / 'dist'
        PACKAGE.create(self.create_args(out))
        archive_path = self.archive_path(out)
        original = archive_path.read_bytes()
        for artifacts in ({}, {'../../outside': '0' * 64}):
            with self.subTest(artifacts=artifacts):
                with tarfile.open(fileobj=io.BytesIO(original), mode='r:gz') as source:
                    with tarfile.open(archive_path, 'w:gz') as destination:
                        for member in source.getmembers():
                            data = source.extractfile(member).read()
                            if member.name.endswith('/BUILD-INFO.json'):
                                info = json.loads(data)
                                info['artifacts'] = artifacts
                                data = json.dumps(info).encode()
                                member.size = len(data)
                            destination.addfile(member, io.BytesIO(data))
                archive_path.with_name(archive_path.name + '.sha256').write_text(
                    f'{PACKAGE.digest(archive_path)}  {archive_path.name}\n')
                with self.assertRaisesRegex(ValueError, 'hash every required artifact'):
                    PACKAGE.check_archive(archive_path)


class CModuleBuildTests(unittest.TestCase):
    def test_c_only_build_is_private_and_keeps_module_on_link_failure(self):
        with tempfile.TemporaryDirectory(prefix='gnash c-only ') as folder:
            output = Path(folder) / 'layout'
            subprocess.run([os.environ.get('PYTHON', 'python3'), str(ROOT / 'scripts/build-gnash.py'),
                            '--c-only', '--out', str(output)], check=True)
            module = output / 'lib/gnash/bash-os-coreutils.so'
            self.assertTrue(module.is_file())
            stamp = json.loads((output / 'PROVENANCE.json').read_text())
            self.assertFalse(stamp['complete'])
            original = module.read_bytes()
            bad = Path(folder) / 'bad.c'
            bad.write_text('extern void missing(void); int f(void){missing(); return 0;}\n')
            with self.assertRaises(subprocess.CalledProcessError):
                BUILD.link_shared([os.environ.get('CC', 'cc'), '-shared', '-fPIC', '-Wl,-z,defs', str(bad)],
                                  module)
            self.assertEqual(module.read_bytes(), original)
            second = Path(folder) / 'layout-2'
            subprocess.run([os.environ.get('PYTHON', 'python3'), str(ROOT / 'scripts/build-gnash.py'),
                            '--c-only', '--out', str(second)], check=True)
            self.assertEqual(module.read_bytes(), (second / 'lib/gnash/bash-os-coreutils.so').read_bytes())


def PureName(name):
    prefix, _sep, relative = name.partition('/')
    if not prefix.startswith('gnash-') or not relative:
        raise AssertionError(name)
    return relative


def archive_member(archive_path, suffix):
    with tarfile.open(archive_path, 'r:gz') as archive:
        for member in archive.getmembers():
            if member.name.endswith('/' + suffix):
                return archive.extractfile(member).read()
    raise AssertionError(suffix)


if __name__ == '__main__':
    unittest.main(verbosity=2)
