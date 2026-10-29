#!/usr/bin/env python3
"""Regressions for confirmed bash-os adapter and uutils bridge defects."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]

DRIVER = r'''
#include "loadables.h"
#include "bashgetopt.h"
#include <string.h>
extern struct builtin seq_struct, hostid_struct, coreutils_struct, expand_struct, unexpand_struct;

static int fail(int code) { return code; }

static int opt_attached(void) {
    char arg[] = "-nvalue";
    WORD_DESC desc = {arg, 0};
    WORD_LIST list = {NULL, &desc};
    reset_internal_getopt();
    int opt = internal_getopt(&list, "n:");
    if (opt != 'n' || !list_optarg || strcmp(list_optarg, "value")) return fail(20);
    if (internal_getopt(&list, "n:") != -1 || loptend) return fail(21);
    return 0;
}
static int opt_separate(void) {
    char flag[] = "-n", value[] = "sep";
    WORD_DESC dflag = {flag, 0}, dvalue = {value, 0};
    WORD_LIST tail = {NULL, &dvalue}, list = {&tail, &dflag};
    reset_internal_getopt();
    int opt = internal_getopt(&list, "n:");
    if (opt != 'n' || !list_optarg || strcmp(list_optarg, "sep")) return fail(22);
    if (internal_getopt(&list, "n:") != -1) return fail(23);
    return 0;
}
static int opt_end_and_stdin(void) {
    char end[] = "--", operand[] = "-n", dash[] = "-", file[] = "file";
    WORD_DESC dend = {end, 0}, dop = {operand, 0}, ddash = {dash, 0}, dfile = {file, 0};
    WORD_LIST op = {NULL, &dop}, list = {&op, &dend};
    reset_internal_getopt();
    if (internal_getopt(&list, "n") != -1) return fail(30);
    if (!loptend || strcmp(loptend->word->word, "-n")) return fail(31);
    WORD_LIST file_node = {NULL, &dfile}, dashed = {&file_node, &ddash};
    reset_internal_getopt();
    if (internal_getopt(&dashed, "n") != -1) return fail(32);
    if (!loptend || strcmp(loptend->word->word, "-")) return fail(33);
    return 0;
}
static int opt_missing_unknown_and_cluster(void) {
    char missing[] = "-n", unknown[] = "-x", cluster[] = "-hn";
    WORD_DESC dmiss = {missing, 0}, dunk = {unknown, 0}, dcluster = {cluster, 0};
    WORD_LIST mlist = {NULL, &dmiss}, ulist = {NULL, &dunk}, clist = {NULL, &dcluster};
    reset_internal_getopt();
    if (internal_getopt(&mlist, "n:") != '?') return fail(40);
    reset_internal_getopt();
    if (internal_getopt(&ulist, "h") != '?') return fail(41);
    reset_internal_getopt();
    if (internal_getopt(&clist, "hn:") != 'h') return fail(42);
    if (internal_getopt(&clist, "hn:") != '?') return fail(43);
    return 0;
}
static int opt_flag_then_operand(void) {
    char flag[] = "-h", owner[] = "root";
    WORD_DESC dflag = {flag, 0}, downer = {owner, 0};
    WORD_LIST tail = {NULL, &downer}, list = {&tail, &dflag};
    reset_internal_getopt();
    if (internal_getopt(&list, "h") != 'h') return fail(50);
    if (internal_getopt(&list, "h") != -1) return fail(51);
    if (!loptend || strcmp(loptend->word->word, "root")) return fail(52);
    return 0;
}

int main(int argc, char **argv) {
    if (argc < 2) return 2;
    if (!strcmp(argv[1], "getopt")) {
        int rc = opt_attached();
        if (rc) return rc;
        rc = opt_separate();
        if (rc) return rc;
        rc = opt_end_and_stdin();
        if (rc) return rc;
        rc = opt_missing_unknown_and_cluster();
        if (rc) return rc;
        return opt_flag_then_operand();
    }
    if (!strcmp(argv[1], "empty"))
        return gnash_bash_run(0, NULL, &hostid_struct, NULL);
    struct builtin *command = NULL;
    const char *sub = NULL;
    if (!strcmp(argv[1], "seq")) command = &seq_struct;
    else if (!strcmp(argv[1], "hostid")) command = &hostid_struct;
    else if (!strcmp(argv[1], "expand")) command = &expand_struct;
    else if (!strcmp(argv[1], "unexpand")) command = &unexpand_struct;
    else if (!strcmp(argv[1], "fmt")) { command = &coreutils_struct; sub = "fmt"; }
    else if (!strcmp(argv[1], "numfmt")) { command = &coreutils_struct; sub = "numfmt"; }
    else if (!strcmp(argv[1], "yes")) { command = &coreutils_struct; sub = "yes"; }
    else if (!strcmp(argv[1], "coreutils")) command = &coreutils_struct;
    else return 2;
    return gnash_bash_run(argc - 1, argv + 1, command, sub);
}
'''


class AdapterReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='gnash adapter review ')
        cls.addClassCleanup(cls.temp.cleanup)
        cls.directory = Path(cls.temp.name)
        source = cls.directory / 'driver.c'
        source.write_text(DRIVER)
        cls.binary = cls.directory / 'driver'
        vendor = ROOT / 'vendor/bash-os'
        revision = json.loads((vendor / 'manifest.json').read_text())['revision'][:12]
        cls.revision = revision
        sources = [
            vendor / 'loadables/seq.c', vendor / 'loadables/hostid.c',
            vendor / 'loadables/expand.c', vendor / 'loadables/unexpand.c',
            vendor / 'loadables/coreutils.c', ROOT / 'loadables/compat/adapter.c', source,
        ]
        subprocess.run([
            os.environ.get('CC', 'cc'), '-std=gnu11', '-D_GNU_SOURCE', '-O1', '-g',
            '-DGNASH_BASH_OS_REVISION=' + json.dumps(revision),
            '-I', str(ROOT / 'loadables/compat'), '-I', str(vendor / 'loadables/common'),
            *map(str, sources), '-lm', '-o', str(cls.binary),
        ], check=True)

    def command(self, args, data=b'', *, stdout=subprocess.PIPE, status=0):
        result = subprocess.run(
            [str(self.binary), *args], input=data, stdout=stdout, stderr=subprocess.PIPE,
            cwd=self.directory, timeout=10, env=dict(os.environ, LC_ALL='C'))
        self.assertEqual(result.returncode, status, result.stderr.decode(errors='replace'))
        return result

    def test_getopt_contracts_used_by_chown(self):
        self.command(['getopt'])

    def test_empty_argc_does_not_overflow(self):
        self.command(['empty'], status=1)

    def test_seq_and_hostid_report_write_errors(self):
        with open('/dev/full', 'wb') as output:
            seq = self.command(['seq', '1', '5'], stdout=output, status=1)
            host = self.command(['hostid'], stdout=output, status=1)
        self.assertEqual(seq.stderr, b'seq: write error: No space left on device\n')
        self.assertEqual(host.stderr, b'hostid: write error: No space left on device\n')

    def test_yes_write_error_is_not_repeated(self):
        with open('/dev/full', 'wb') as output:
            result = self.command(['yes'], stdout=output, status=1)
        self.assertEqual(result.stderr, b'yes: write: No space left on device\n')

    def test_unexpand_write_error_is_not_repeated(self):
        with open('/dev/full', 'wb') as output:
            result = self.command(['unexpand', '-a'], b'        content\n' * 1000,
                                  stdout=output, status=1)
        self.assertEqual(result.stderr.count(b'write error'), 1)
        self.assertIn(b'unexpand: write error: No space left on device\n', result.stderr)

    def test_direct_verb_help_is_not_the_bundle_manual(self):
        fmt = self.command(['fmt', '--help'])
        numfmt = self.command(['numfmt', '--help'])
        bundle = self.command(['coreutils', '--help'])
        self.assertEqual(fmt.stdout, b'fmt [-w WIDTH] [FILE...]\nUsage: fmt [-w WIDTH] [FILE...]\n')
        self.assertIn(b'numfmt [--to=iec|iec-i|si] [--from=iec|iec-i|si] N\n', numfmt.stdout)
        self.assertNotIn(b'[-w WIDTH]', numfmt.stdout)
        self.assertIn(b'coreutils tac', bundle.stdout)
        self.assertIn(b'Usage: coreutils VERB [ARGS...]\n', bundle.stdout)

    def test_help_requires_the_sole_argument(self):
        missing = self.directory / 'no-such-help-operand'
        result = self.command(['fmt', '--help', str(missing)], status=1)
        self.assertIn(b'No such file or directory', result.stderr)
        self.assertNotIn(b'Usage: fmt ', result.stdout)

    def test_direct_verb_diagnostic_is_not_doubled(self):
        direct = self.command(['fmt', '-w', '0'], status=2)
        multiplexed = self.command(['coreutils', 'fmt', '-w', '0'], status=2)
        missing = self.command(['fmt', 'no-such-fmt-file'], status=1)
        self.assertEqual(direct.stderr,
                         b'fmt: bad -w\nfmt: usage: coreutils fmt [-w WIDTH] [FILE...]\n')
        self.assertEqual(multiplexed.stderr,
                         b'coreutils: fmt: bad -w\n'
                         b'coreutils: usage: coreutils fmt [-w WIDTH] [FILE...]\n')
        self.assertIn(b'fmt: no-such-fmt-file:', missing.stderr)
        self.assertNotIn(b'fmt: fmt:', missing.stderr)

    def test_expand_version_names_the_module_without_a_bash_version(self):
        result = self.command(['expand', '--version', 'ignored'])
        self.assertEqual(
            result.stdout,
            f'expand (bash-os builtin, Bash not linked; gnash/bash-os {self.revision})\n'.encode())
        self.assertNotIn(f'Bash {self.revision}'.encode(), result.stdout)


if __name__ == '__main__':
    unittest.main(verbosity=2)
