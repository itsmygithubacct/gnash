#!/usr/bin/env python3
"""Exercise native C bounds and cleanup with AddressSanitizer and UBSan."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
DRIVER = r'''
#include "loadables.h"
#include "unwind_prot.h"
#include <string.h>
extern struct builtin bashdd_struct, unexpand_struct;
static void print_cleanup(void *text) { puts(text); }
int main(int argc, char **argv) {
    if (argc < 2) return 2;
    if (!strcmp(argv[1], "cleanup")) {
        begin_unwind_frame("outer");
        add_unwind_protect(print_cleanup, "outer");
        begin_unwind_frame("inner");
        add_unwind_protect(print_cleanup, "inner");
        run_unwind_frame("inner");
        add_unwind_protect(print_cleanup, "late");
        run_unwind_frame("outer");
        begin_unwind_frame("root");
        add_unwind_protect(print_cleanup, "root");
        begin_unwind_frame("nested");
        add_unwind_protect(print_cleanup, "nested");
        run_unwind_frame("root");
        return sh_chkwrite(0);
    }
    struct builtin *command = !strcmp(argv[1], "dd") ? &bashdd_struct : &unexpand_struct;
    return gnash_bash_run(argc - 1, argv + 1, command, NULL);
}
'''


class CSanitizerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='gnash c sanitizers ')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.directory = Path(cls.temp.name)
        source = cls.directory / 'driver.c'
        source.write_text(DRIVER)
        cls.binary = cls.directory / 'driver'
        vendor = ROOT / 'vendor/bash-os'
        revision = json.loads((vendor / 'manifest.json').read_text())['revision'][:12]
        subprocess.run([
            os.environ.get('CC', 'cc'), '-std=gnu11', '-D_GNU_SOURCE', '-O1', '-g',
            '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
            '-DGNASH_BASH_OS_REVISION=' + json.dumps(revision),
            '-I', str(ROOT / 'loadables/compat'), '-I', str(vendor / 'loadables/common'),
            str(vendor / 'loadables/dd.c'), str(vendor / 'loadables/unexpand.c'),
            str(ROOT / 'loadables/compat/adapter.c'), str(source), '-lm', '-o', str(cls.binary),
        ], check=True)

    def command(self, args, data=b'', *, stdout=subprocess.PIPE, status=0):
        result = subprocess.run(
            [str(self.binary), *args], input=data, stdout=stdout, stderr=subprocess.PIPE,
            env=dict(os.environ, LC_ALL='C', ASAN_OPTIONS='detect_leaks=1:halt_on_error=1',
                     UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1'),
            cwd=self.directory, timeout=10)
        self.assertNotIn(b'Sanitizer', result.stderr, result.stderr.decode(errors='replace'))
        self.assertNotIn(b'runtime error:', result.stderr, result.stderr.decode(errors='replace'))
        self.assertEqual(result.returncode, status, result.stderr.decode(errors='replace'))
        return result

    def test_dd_unblock_bounds(self):
        for ibs in [1, 32, 512]:
            for cbs, data, expected in [
                (1, b'A' * 512, b'A\n' * 512),
                (64, b' ' * 63 + b'X', b' ' * 63 + b'X\n'),
            ]:
                with self.subTest(ibs=ibs, cbs=cbs):
                    result = self.command(['dd', f'ibs={ibs}', f'cbs={cbs}',
                                           'conv=unblock', 'iflag=fullblock', 'status=none'], data)
                    self.assertEqual(result.stdout, expected)

    def test_nested_cleanup_frames(self):
        result = self.command(['cleanup'])
        self.assertEqual(result.stdout, b'inner\nlate\nouter\nnested\nroot\n')

    def test_unexpand_cleans_up_regular_files(self):
        (self.directory / 'first').write_bytes(b' ' * 3)
        (self.directory / 'second').write_bytes(b' ' * 5 + b'content\n')
        result = self.command(['unexpand', '-a', 'first', 'second'])
        self.assertEqual(result.stdout, b'\tcontent\n')

    def test_unexpand_cleans_up_after_output_error(self):
        with open('/dev/full', 'wb') as output:
            self.command(['unexpand', '-a'], b'        content\n' * 10000,
                         stdout=output, status=1)


if __name__ == '__main__':
    unittest.main(verbosity=2)
