#!/usr/bin/env python3
"""End-to-end ABI, shell integration, and bundled coreutils tests (Linux)."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
GNASH = Path(os.environ.get('GNASH_TEST_BINARY', ROOT / 'out/gnash')).resolve()
Q = shlex.quote

FIXTURE = r'''
#include "gnash.h"
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
static int hello(int argc, char **argv) {
    printf("hello:%s\n", argc > 1 ? argv[1] : "world"); return 0;
}
static int context(int argc, char **argv) {
    (void)argc; (void)argv;
    char cwd[4096]; if (!getcwd(cwd, sizeof cwd)) return 1;
    printf("%s|%s\n", cwd, getenv("GNASH_TEST_VAR") ?: "unset"); return 0;
}
static int emit(int argc, char **argv) {
    (void)argc; (void)argv;
    for (int i = 0; i < 100000; ++i) puts("abcdefghij"); return 0;
}
static int status(int argc, char **argv) {
    return argc > 1 ? atoi(argv[1]) : 7;
}
static int fdwrite(int argc, char **argv) {
    (void)argc; (void)argv; return write(10, "fd10\n", 5) == 5 ? 0 : 1;
}
static const struct gnash_command commands[] = {
    {"hello", hello}, {"context", context}, {"emit", emit},
    {"status", status}, {"echo", hello}, {"fdwrite", fdwrite},
    {"eval", hello}
};
GNASH_MODULE(commands)
'''

class NativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not GNASH.is_file():
            raise RuntimeError('Build first: python3 scripts/build-gnash.py')
        cls.temp = tempfile.TemporaryDirectory(prefix='gnash tests ')
        cls.directory = Path(cls.temp.name)
        cls.module = cls.compile('fixture', FIXTURE)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    @classmethod
    def compile(cls, name, code):
        source = cls.directory / (name + '.c')
        module = cls.directory / (name + '.so')
        source.write_text(code)
        subprocess.run([os.environ.get('CC', 'cc'), '-shared', '-fPIC', '-I',
                        str(ROOT / 'include'), str(source), '-o', str(module)], check=True)
        return module

    def shell(self, script, *, autoload=False, data=None, env=None, timeout=25):
        environment = dict(os.environ, LC_ALL='C', GNASH_NO_COREUTILS='1')
        if autoload:
            environment.pop('GNASH_NO_COREUTILS')
        if env:
            environment.update(env)
        return subprocess.run([str(GNASH), '--no-config', '--noprofile', '--norc', '-c', script],
                              cwd=self.directory, env=environment, input=data,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)

    def success(self, script, expected, **kwargs):
        result = self.shell(script, **kwargs)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))
        self.assertEqual(result.stdout, expected)
        return result

    def load(self, names='hello'):
        return f'enable -f {Q(str(self.module))} {names}; '

    def test_load_invoke_builtin_and_unload(self):
        self.success(self.load() + 'type -t hello; hello "two words"; builtin hello direct; '
                     'enable -d hello; type -t hello >/dev/null 2>&1; echo $?',
                     b'builtin\nhello:two words\nhello:direct\n1\n')

    def test_failure_is_atomic(self):
        self.success(self.load('hello missing') + 'echo $?; type -t hello >/dev/null 2>&1; echo $?', b'1\n1\n')

    def test_replacement_restores_static_builtin(self):
        self.success(self.load('echo') + 'echo loaded; enable -d echo; echo restored',
                     b'hello:loaded\nrestored\n')

    def test_reload_preserves_original(self):
        self.success(self.load('echo') + self.load('echo') + 'enable -d echo; echo original', b'original\n')

    def test_disable_enable_and_function_precedence(self):
        self.success(self.load() + 'hello() { echo function; }; hello; command hello; '
                     'unset -f hello; enable -n hello; PATH=; hello 2>/dev/null; echo $?; '
                     'enable hello; hello', b'function\nhello:world\n127\nhello:world\n')

    def test_special_builtin_cannot_be_replaced(self):
        self.success(self.load('hello eval') + 'echo $?; type -t hello >/dev/null 2>&1; echo $?; eval "echo retained"',
                     b'1\n1\nretained\n')

    def test_unload_static_rejected(self):
        self.success('enable -d echo; echo $?', b'1\n')

    def test_invalid_modules(self):
        variants = {
            'symbol': 'int something_else(void) { return 0; }',
            'abi': '#include "gnash.h"\nconst struct gnash_module *gnash_module_v1(void) { '
                   'static const struct gnash_module m = {99, sizeof(m), 0, 0}; return &m; }',
            'null': '#include "gnash.h"\nconst struct gnash_module *gnash_module_v1(void) { return 0; }',
            'duplicate': '#include "gnash.h"\nstatic int f(int a,char**b){return 0;} '
                         'static const struct gnash_command c[]={{"hello",f},{"hello",f}}; GNASH_MODULE(c)',
            'nullrun': '#include "gnash.h"\nstatic const struct gnash_command c[]={{"hello",0}}; GNASH_MODULE(c)',
        }
        for name, code in variants.items():
            with self.subTest(name=name):
                module = self.compile(name, code)
                self.success(f'enable -f {Q(str(module))}; echo $?', b'1\n')
        self.success('enable -f does-not-exist.so; echo $?', b'1\n')
        invalid = self.directory / 'plain-text.so'
        invalid.write_text('not an ELF module')
        fifo = self.directory / 'module-fifo'
        os.mkfifo(fifo)
        for path in [invalid, fifo, self.directory]:
            with self.subTest(path=path):
                self.success(f'enable -f {Q(str(path))}; echo $?', b'1\n', timeout=3)

    def test_relative_path_and_arguments(self):
        self.success('enable -f ./fixture.so hello; hello ""; hello "a $ b"', b'hello:\nhello:a $ b\n')

    def test_subshell_registry_isolation(self):
        self.success(self.load() + '(enable -d hello); hello; '
                     '(enable -f ./fixture.so status; status 8); echo $?; '
                     'type -t status >/dev/null 2>&1; echo $?', b'hello:world\n8\n1\n')

    def test_working_dir_environment_and_redirections(self):
        folder = self.directory / 'working dir'
        folder.mkdir(exist_ok=True)
        self.success(self.load('context') + f'cd {Q(str(folder))}; GNASH_TEST_VAR=exported context > result; '
                     '/bin/cat result', f'{folder}|exported\n'.encode())

    def test_module_fd_does_not_replace_user_fd(self):
        self.success(self.load('fdwrite') + 'fdwrite 10>fd-result; /bin/cat fd-result', b'fd10\n')

    def test_pipeline_larger_than_pipe_buffer(self):
        self.success(self.load('emit') + 'emit | /usr/bin/wc -c', b'1100000\n')

    def test_command_substitution_and_status(self):
        self.success(self.load('hello status') + 'x=$(hello substitution); echo "$x"; '
                     'set -o pipefail; status 7 | /bin/cat; echo "$? ${PIPESTATUS[*]}"',
                     b'hello:substitution\n7 7 0\n')

    def test_snapshot_survives_overwrite_and_unlink(self):
        copy = self.directory / 'copied.so'
        shutil.copy2(self.module, copy)
        self.success(f'enable -f {Q(str(copy))} hello; printf overwritten > {Q(str(copy))}; '
                     f'/bin/rm {Q(str(copy))}; hello pinned', b'hello:pinned\n')

    def test_background_command_retains_module(self):
        self.success(self.load('emit') + 'emit > background-result & enable -d emit; wait; '
                     '/usr/bin/wc -c < background-result', b'1100000\n')

    def test_load_all_and_option_conflicts(self):
        module = self.compile('hello-only', (ROOT / 'examples/loadables/hello.c').read_text())
        self.success(f'enable -f {Q(str(module))}; hello; enable -f {Q(str(module))} -d hello; echo $?; '
                     'enable -d; echo $?', b'Hello, world!\n1\n1\n')

    def test_coreutils_without_path(self):
        self.success('PATH=; type -t ls cat chmod stat; printf "c\\na\\nb\\na\\n" | sort | uniq | '
                     'tee sorted | wc -l; cat sorted; seq 1 100000 | tail -n 1; '
                     'printf abc | sha256sum',
                     b'builtin\nbuiltin\nbuiltin\nbuiltin\n3\na\nb\nc\n100000\n'
                     b'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad  -\n', autoload=True)

    def test_coreutils_filesystem(self):
        self.success('PATH=; mkdir -p files; cd files; printf content > a; cp a b; mv b c; '
                     'chmod 600 c; stat -c %a c; head -c 3 c; printf "\\n"; '
                     'ln -s c symlink; readlink symlink; rm a c symlink; cd ..; rmdir files',
                     b'600\ncon\nc\n', autoload=True)

    def test_move_rejects_colliding_source_names(self):
        with tempfile.TemporaryDirectory(dir=self.directory) as folder:
            root = Path(folder)
            for name in ['a', 'b', 'dest']:
                (root / name).mkdir()
            (root / 'a/file').write_bytes(b'first')
            (root / 'b/file').write_bytes(b'second')
            result = self.shell(f'cd {Q(folder)}; PATH=; mv a/file b/file dest', autoload=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual((root / 'dest/file').read_bytes(), b'first')
            self.assertEqual((root / 'b/file').read_bytes(), b'second')
            self.assertFalse((root / 'a/file').exists())

    def test_move_preserves_directory_name_with_trailing_slash(self):
        with tempfile.TemporaryDirectory(dir=self.directory) as folder:
            root = Path(folder)
            (root / 'source').mkdir()
            (root / 'dest').mkdir()
            (root / 'source/file').write_bytes(b'content')
            self.success(f'cd {Q(folder)}; PATH=; mv source/ dest', b'', autoload=True)
            self.assertEqual((root / 'dest/source/file').read_bytes(), b'content')
            self.assertFalse((root / 'source').exists())

    def test_copy_no_clobber_preserves_files_and_symlinks(self):
        for flags in ['-n', '--no-clobber', '-fn']:
            for symlink in [False, True]:
                with self.subTest(flags=flags, symlink=symlink), tempfile.TemporaryDirectory(dir=self.directory) as folder:
                    root = Path(folder)
                    (root / 'source').write_bytes(b'new content')
                    (root / 'original').write_bytes(b'keep content')
                    target = root / 'target'
                    if symlink:
                        target.symlink_to('original')
                    else:
                        target.write_bytes(b'keep content')
                    self.success(f'cd {Q(folder)}; PATH=; cp {flags} source target', b'', autoload=True)
                    self.assertEqual(target.read_bytes(), b'keep content')
                    self.assertEqual(target.is_symlink(), symlink)

    def test_split_rejects_output_aliases_of_input(self):
        for alias in ['same-path', 'hardlink', 'symlink']:
            with self.subTest(alias=alias), tempfile.TemporaryDirectory(dir=self.directory) as folder:
                root = Path(folder)
                source = root / ('xaa' if alias == 'same-path' else 'input')
                content = b'line\n' * 16000
                source.write_bytes(content)
                if alias == 'hardlink':
                    os.link(source, root / 'xaa')
                elif alias == 'symlink':
                    (root / 'xaa').symlink_to(source.name)
                result = self.shell(f'cd {Q(folder)}; PATH=; split -l 1000 {source.name} x', autoload=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(source.read_bytes(), content)

    def test_dd_seek_preserves_existing_prefix(self):
        for options, data, expected in [
            ('bs=1 seek=3', b'XYZ', b'abcXYZ'),
            ('bs=1 seek=3 count=0', b'', b'abc'),
            ('bs=1 seek=3 conv=notrunc', b'XYZ', b'abcXYZghij'),
        ]:
            with self.subTest(options=options), tempfile.TemporaryDirectory(dir=self.directory) as folder:
                target = Path(folder) / 'target'
                target.write_bytes(b'abcdefghij')
                self.success(f'PATH=; dd of={Q(str(target))} {options} status=none',
                             b'', autoload=True, data=data)
                self.assertEqual(target.read_bytes(), expected)

    def test_dd_invalid_offsets_preserve_output(self):
        for options in ['ibs=2 skip=9223372036854775807', 'obs=2 seek=9223372036854775807']:
            with self.subTest(options=options), tempfile.TemporaryDirectory(dir=self.directory) as folder:
                target = Path(folder) / 'target'
                target.write_bytes(b'keep content')
                result = self.shell(f'PATH=; dd of={Q(str(target))} {options} status=none',
                                    autoload=True, data=b'new content')
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(target.read_bytes(), b'keep content')

    def test_dd_unblock_expansion_and_carried_spaces(self):
        # The sanitizer test also checks the allocation bounds for these inputs.
        for ibs in [1, 32, 512]:
            for cbs, data, expected in [
                (1, b'A' * 512, b'A\n' * 512),
                (64, b' ' * 63 + b'X', b' ' * 63 + b'X\n'),
            ]:
                with self.subTest(ibs=ibs, cbs=cbs):
                    self.success(f'PATH=; dd ibs={ibs} cbs={cbs} conv=unblock iflag=fullblock status=none',
                                 expected, autoload=True, data=data)

    def test_coreutils_binary_pipeline_and_sigpipe(self):
        self.success('PATH=; head -c 1048576 /dev/zero | cat | wc -c; yes | head -n 2',
                     b'1048576\ny\ny\n', autoload=True)

    def test_bash_os_manifest_commands_are_registered(self):
        manifest = json.loads((ROOT / 'vendor/bash-os/manifest.json').read_text())
        names = list(manifest['commands'])
        self.success('PATH=; ' + '; '.join('type -t ' + name for name in names),
                     b'builtin\n' * len(names), autoload=True)

    def test_cwd_isolated_between_pipeline_stages(self):
        folder = self.directory / 'other-cwd'
        folder.mkdir(exist_ok=True)
        self.success(self.load('context') + f'(cd {Q(str(folder))}; context) | /bin/cat; context',
                     f'{folder}|unset\n{self.directory}|unset\n'.encode())

    def test_coreutils_common_options_against_host(self):
        cases = [
            (['comm', '-12', '/dev/stdin', '/dev/null'], b'a\nb\n'),
            (['seq', '-w', '1', '12'], None),
            (['seq', '-s', ',', '1', '3'], None),
            (['sort', '-nr'], b'2\n10\n1\n'),
            (['uniq', '-c'], b'a\na\nb\n'),
            (['tail', '-n', '2'], b'a\nb\nc\n'),
            (['head', '-c', '5'], b'abc\x00def\n'),
            (['tr', 'a-z', 'A-Z'], b'Abcd\n'),
            (['cut', '-d', ':', '-f', '2'], b'a:b:c\n'),
            (['nl', '-ba'], b'a\n\nb\n'),
            (['fold', '-w', '3'], b'abcdef\n'),
            (['expand', '-t', '4'], b'a\tb\n'),
            (['unexpand', '-a', '-t', '4'], b'    a\n'),
            (['tac'], b'a\nb\nc\n'),
            (['paste', '-s', '-d', ','], b'a\nb\nc\n'),
            (['expr', '2', '+', '3'], None),
            (['date', '-u', '-d', '@0', '+%F'], None),
            (['factor', '12'], None),
        ]
        for command, data in cases:
            with self.subTest(command=command):
                reference = subprocess.run(command, input=data, stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE, env=dict(os.environ, LC_ALL='C'), timeout=5)
                actual = self.shell(shlex.join(command), autoload=True, data=data)
                self.assertEqual((actual.returncode, actual.stdout),
                                 (reference.returncode, reference.stdout), actual.stderr)

    def test_explicit_provider_selection(self):
        uutils = GNASH.parent / 'lib/gnash/uutils.so'
        revision = json.loads((ROOT / 'vendor/bash-os/manifest.json').read_text())['revision'][:12]
        self.success('fmt --version; '
                     f'enable -f {Q(str(uutils))} fmt; fmt --version',
                     f'fmt (gnash bash-os module) {revision}\nMIT License\n'.encode()
                     + b'fmt (uutils coreutils) 0.10.0\n', autoload=True)

    def test_c_output_errors_propagate(self):
        self.success('seq 1 100 > /dev/full; echo $?; echo alive', b'1\nalive\n', autoload=True)

    def test_uutils_buffered_output_errors_propagate(self):
        uutils = GNASH.parent / 'lib/gnash/uutils.so'
        for name, command in [('printf', 'printf x'), ('echo', 'echo -n x'),
                              ('printenv', 'printenv -0 GNASH_TEST_VAR')]:
            with self.subTest(command=command):
                result = self.success(
                    f'enable -f {Q(str(uutils))} {name}; '
                    f'{command} > /dev/full; status=$?; '
                    '/bin/echo "$status"; /bin/echo alive',
                    b'1\nalive\n', env={'GNASH_TEST_VAR': 'buffered'})
                self.assertIn(f'{name}: error flushing stdout:'.encode(), result.stderr)
                self.assertNotIn(b'brush:', result.stderr)

    def test_license_expression_policy(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('gnash_license_audit', ROOT / 'scripts/audit-licenses.py')
        audit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(audit)
        for expression in ['MIT', 'Apache-2.0 OR GPL-2.0-only', '(MIT OR Apache-2.0) AND Unicode-3.0',
                           'Apache-2.0/MIT', 'Apache-2.0 WITH LLVM-exception']:
            with self.subTest(expression=expression):
                self.assertTrue(audit.permissive(expression))
        for expression in ['GPL-3.0-only', 'MIT AND GPL-3.0-only', 'MPL-2.0', 'LicenseRef-Unknown']:
            with self.subTest(expression=expression):
                self.assertFalse(audit.permissive(expression))
        with self.assertRaises(ValueError):
            audit.permissive('MIT OR')

    def test_license_audit_removes_obsolete_managed_notices(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('gnash_license_audit', ROOT / 'scripts/audit-licenses.py')
        audit = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(audit)
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            current = output / 'example-2.0.0'
            old = output / 'example-1.0.0'
            unrelated = output / 'other-notices'
            for directory in [current, old, unrelated]:
                directory.mkdir()
            (current / 'LICENSE').write_text('current license')
            (current / 'obsolete-contact.txt').write_text('obsolete metadata')
            (old / 'LICENSE').write_text('old license')
            (output / 'bash-os-MIT.txt').write_text('retained top-level license')
            (unrelated / 'LICENSE').write_text('retained unrelated license')
            inventory = [{'name': 'example', 'version': '2.0.0', 'notices': ['example-2.0.0/LICENSE']}]
            previous = [{'name': 'example', 'version': '1.0.0'}]
            with self.assertRaises(ValueError):
                audit.prune_generated_notices(output, inventory, [{'name': '../outside', 'version': '1.0.0'}])
            self.assertTrue((current / 'obsolete-contact.txt').exists())
            audit.prune_generated_notices(output, inventory, previous)
            self.assertEqual((current / 'LICENSE').read_text(), 'current license')
            self.assertFalse((current / 'obsolete-contact.txt').exists())
            self.assertFalse(old.exists())
            self.assertTrue((output / 'bash-os-MIT.txt').exists())
            self.assertTrue((unrelated / 'LICENSE').exists())

if __name__ == '__main__':
    unittest.main(verbosity=2)
