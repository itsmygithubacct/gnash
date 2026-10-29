#!/usr/bin/env python3
"""Regressions for native-worker stdio, closed descriptors, and fd moves."""
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

MODULE = r'''
#include "gnash.h"
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static int say(int argc, char **argv) {
    (void)argc; (void)argv;
    printf("1\n");
    return 0;
}
static int quiet(int argc, char **argv) {
    (void)argc; (void)argv;
    return 0;
}
static int read_stdin(int argc, char **argv) {
    (void)argc; (void)argv;
    char buf[8];
    ssize_t n = read(0, buf, sizeof buf);
    if (n < 0) {
        dprintf(2, "read:%s\n", strerror(errno));
        return 1;
    }
    printf("n=%zd\n", n);
    return 0;
}
static int saved(int argc, char **argv) {
    (void)argc; (void)argv;
    char byte = 'Z';
    ssize_t n = write(3, &byte, 1);
    int flags = fcntl(1, F_GETFD);
    dprintf(2, "w=%zd f=%d\n", n, flags);
    return (n == 1 && flags < 0) ? 0 : 1;
}
static int listfds(int argc, char **argv) {
    int out = argc > 1 ? atoi(argv[1]) : 1;
    dprintf(out, "stdout_open=%d\n", fcntl(1, F_GETFD));
    DIR *dir = opendir("/proc/self/fd");
    if (!dir) return 1;
    struct dirent *ent;
    while ((ent = readdir(dir)) != NULL) {
        if (ent->d_name[0] == '.') continue;
        char path[80], target[256];
        snprintf(path, sizeof path, "/proc/self/fd/%s", ent->d_name);
        ssize_t n = readlink(path, target, sizeof target - 1);
        if (n < 0) n = 0;
        target[n] = '\0';
        dprintf(out, "%s %s\n", ent->d_name, target);
    }
    closedir(dir);
    return 0;
}
static int lines(int argc, char **argv) {
    (void)argc; (void)argv;
    for (int i = 0; i < 1000; ++i) puts("abcdefghij");
    return 0;
}
static int group_leader(int argc, char **argv) {
    (void)argc; (void)argv;
    printf("%d\n", getpid() == getpgrp());
    return 0;
}
static const struct gnash_command commands[] = {
    {"say", say}, {"quiet", quiet}, {"read_stdin", read_stdin},
    {"saved", saved}, {"listfds", listfds}, {"lines", lines},
    {"group_leader", group_leader},
};
GNASH_MODULE(commands)
'''

PROBE = r'''
import os, sys
status = 0
def attempt(fd, label):
    global status
    try:
        os.write(fd, (label + "\n").encode())
    except OSError as exc:
        status = 1
        try:
            sys.stderr.write("%s:%s\n" % (label, exc.strerror))
        except OSError:
            pass
attempt(1, "out")
attempt(2, "err")
attempt(3, "saved")
sys.exit(status)
'''


class NativeReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not GNASH.is_file():
            raise RuntimeError('Build gnash and set GNASH_TEST_BINARY')
        cls.temp = tempfile.TemporaryDirectory(prefix='gnash-native-review ')
        cls.directory = Path(cls.temp.name)
        source = cls.directory / 'review.c'
        cls.module = cls.directory / 'review.so'
        source.write_text(MODULE)
        subprocess.run(
            [os.environ.get('CC', 'cc'), '-shared', '-fPIC', '-I', str(ROOT / 'include'),
             str(source), '-o', str(cls.module)],
            check=True,
        )
        cls.probe = cls.directory / 'probe.py'
        cls.probe.write_text(PROBE)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def shell(self, script, **kwargs):
        environment = dict(os.environ, LC_ALL='C', GNASH_NO_COREUTILS='1')
        return subprocess.run(
            [str(GNASH), '--no-config', '--noprofile', '--norc', '-c', script],
            cwd=self.directory,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=kwargs.get('timeout', 25),
            input=kwargs.get('input'),
        )

    def load(self, names):
        return f'enable -f {Q(str(self.module))} {names}; '

    def test_external_closed_stdout_and_stdin(self):
        closed = self.shell('/usr/bin/seq 1 1 1>&-')
        self.assertNotEqual(closed.returncode, 0, closed.stderr)
        self.assertEqual(closed.stdout, b'')
        self.assertIn(b'Bad file descriptor', closed.stderr)

        devnull = self.shell('/usr/bin/seq 1 1 >/dev/null')
        self.assertEqual(devnull.returncode, 0, devnull.stderr)
        self.assertEqual(devnull.stdout, b'')
        self.assertEqual(devnull.stderr, b'')

        true = self.shell('/bin/true 1>&-')
        self.assertEqual(true.returncode, 0, true.stderr)
        self.assertEqual(true.stdout, b'')

        wc = self.shell('/usr/bin/wc -c 0<&-')
        self.assertNotEqual(wc.returncode, 0, wc.stderr)
        self.assertIn(b'Bad file descriptor', wc.stderr)

    def test_closed_stdout_keeps_saved_duplicate(self):
        moved = self.shell('/usr/bin/seq 1 1 2>&1 1>&-')
        self.assertNotEqual(moved.returncode, 0, moved.stderr)
        self.assertIn(b'Bad file descriptor', moved.stdout)
        self.assertEqual(moved.stderr, b'')
        self.assertNotIn(b'1\n', moved.stdout)

        both_closed = self.shell('/usr/bin/seq 1 1 1>&- 2>&1')
        self.assertNotEqual(both_closed.returncode, 0)
        self.assertNotIn(b'1\n', both_closed.stdout)
        self.assertIn(b'file descriptor', both_closed.stderr.lower())

        same = self.shell('/bin/echo OUT 1>&1-')
        self.assertEqual(same.returncode, 0, same.stderr)
        self.assertEqual(same.stdout, b'OUT\n')

    def test_fd_move_closes_source_only(self):
        probe = Q(str(self.probe))
        cases = {
            '3>&1 1>&-': (b'saved\n', b'out:Bad file descriptor\nerr\n', 1),
            '3>&1-': (b'saved\n', b'out:Bad file descriptor\nerr\n', 1),
            '2>&1-': (b'out:Bad file descriptor\nerr\nsaved:Bad file descriptor\n', b'', 1),
            '1>&2-': (b'', b'out\n', 1),
            '1>&1-': (b'out\n', b'err\nsaved:Bad file descriptor\n', 1),
            '2>&-': (b'out\n', b'', 1),
        }
        for redir, (stdout, stderr, rc) in cases.items():
            with self.subTest(redir=redir):
                result = self.shell(f'python3 {probe} {redir}')
                self.assertEqual(result.returncode, rc, result.stderr)
                self.assertEqual(result.stdout, stdout)
                self.assertEqual(result.stderr, stderr)
        kept = self.shell(f'python3 {probe} 3>/dev/null 3>&3-')
        self.assertEqual(kept.returncode, 0, kept.stderr)
        self.assertEqual(kept.stdout, b'out\n')
        self.assertEqual(kept.stderr, b'err\n')

    def test_redirect_order_and_readonly_dup(self):
        result = self.shell('/bin/echo OUT 2>&1 >ordered-a; /bin/echo OUT >ordered-b 2>&1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b'')
        self.assertEqual(result.stderr, b'')
        self.assertEqual((self.directory / 'ordered-a').read_bytes(), b'OUT\n')
        self.assertEqual((self.directory / 'ordered-b').read_bytes(), b'OUT\n')

        source = self.directory / 'infile'
        source.write_text('IN\n')
        source.chmod(0o444)
        dup = self.shell(f'/bin/echo WORLD < {Q(str(source))} 1<&0')
        self.assertNotEqual(dup.returncode, 0)
        self.assertEqual(dup.stdout, b'')
        self.assertIn(b'Bad file descriptor', dup.stderr)
        self.assertEqual(source.read_text(), 'IN\n')

    def test_native_closed_stdio_and_snapshot(self):
        prefix = self.load('say quiet read_stdin saved listfds lines')
        plain = self.shell(prefix + 'say')
        self.assertEqual(plain.returncode, 0, plain.stderr)
        self.assertEqual(plain.stdout, b'1\n')

        closed = self.shell(prefix + 'say 1>&-')
        self.assertNotEqual(closed.returncode, 0, closed.stderr)
        self.assertEqual(closed.stdout, b'')

        discarded = self.shell(prefix + 'say >/dev/null')
        self.assertEqual(discarded.returncode, 0, discarded.stderr)
        self.assertEqual(discarded.stdout, b'')

        untouched = self.shell(prefix + 'quiet 1>&-')
        self.assertEqual(untouched.returncode, 0, untouched.stderr)
        self.assertEqual(untouched.stdout, b'')

        reader = self.shell(prefix + 'read_stdin 0<&-', input=b'')
        self.assertNotEqual(reader.returncode, 0)
        self.assertIn(b'Bad file descriptor', reader.stderr)
        self.assertNotIn(b'n=0', reader.stdout)

        saved = self.shell(prefix + 'saved 3>&1 1>&-')
        self.assertEqual(saved.returncode, 0, saved.stderr)
        self.assertEqual(saved.stdout, b'Z')
        self.assertIn(b'w=1 f=-1', saved.stderr)

        listed = self.shell(prefix + 'listfds 3 3>fds.txt 1>&-')
        self.assertEqual(listed.returncode, 0, listed.stderr)
        text = (self.directory / 'fds.txt').read_text()
        self.assertIn('stdout_open=-1', text)
        self.assertNotIn('memfd:', text)
        self.assertNotIn('gnash-module', text)

        piped = self.shell(prefix + 'lines | /usr/bin/wc -c')
        self.assertEqual(piped.returncode, 0, piped.stderr)
        self.assertEqual(piped.stdout, b'11000\n')

    def test_builtin_close_stays_in_process(self):
        result = self.shell('echo hidden 1>&-; echo shown')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b'shown\n')
        self.assertNotIn(b'hidden', result.stdout)

    def test_persistent_stdio_closures(self):
        for command in ('/usr/bin/seq 1 1', self.load('say') + 'say'):
            with self.subTest(command=command):
                result = self.shell('exec 3>&1 1>&-; ' + command)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, b'')
        result = self.shell(self.load('read_stdin') + 'exec 0<&-; read_stdin')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'Bad file descriptor', result.stderr)
        result = self.shell(self.load('saved') + 'exec 3>&1 1>&-; saved')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b'Z')

    def test_worker_rejects_invalid_descriptor(self):
        result = subprocess.run(
            [str(GNASH), '--gnash-native-worker-v1', '999999', '0', 'say'],
            capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 126, result.stderr)
        self.assertIn(b'Bad file descriptor', result.stderr)

    def test_job_control_changes_apply_to_later_commands(self):
        external = "/usr/bin/python3 -c 'import os; print(int(os.getpid() == os.getpgrp()))'"
        for command in ('group_leader', external):
            with self.subTest(command=command):
                result = self.shell(self.load('group_leader') +
                                    f'{command}; set -m; {command}; set +m; {command}; :')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, b'0\n1\n0\n')

    def test_worker_survives_executable_replacement(self):
        for replacement in (False, True):
            with self.subTest(replacement=replacement):
                executable = self.directory / 'gnash-copy'
                shutil.copy2(GNASH, executable)
                action = f'/bin/rm {Q(str(executable))}; '
                if replacement:
                    action += f'/bin/cp /bin/false {Q(str(executable))}; '
                script = self.load('say') + 'say; ' + action + 'say'
                result = subprocess.run(
                    [str(executable), '--no-config', '--noprofile', '--norc', '-c', script],
                    env=dict(os.environ, GNASH_NO_COREUTILS='1'), capture_output=True, timeout=25)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, b'1\n1\n')


if __name__ == '__main__':
    unittest.main()
