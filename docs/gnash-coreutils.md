# First-iteration coreutils inventory

103 command names: 47 from the MIT bash-os sources, 50 from MIT uutils, and 6 existing brush shell builtins.

The bash-os commands are pinned to `work/0.1.3` commit
`955f9c93ebaadf13adedeb446e9e2e6f5d442a95`. This refresh includes upstream fixes for
`dd` reblocking and seek handling, `split` input aliases, `mv` destination
collisions and directory names, and `cp` no-clobber handling.

The source implementations remain selectable: `enable -f /path/to/uutils.so cp` loads the uutils variant, and `enable -f /path/to/bash-os-coreutils.so cp` loads the bash-os variant. This explicit choice can replace an existing shell builtin; automatic loading preserves shell builtins.

bash-os implements its upstream option sets. Help is available with `COMMAND --help` when that is the only argument; the adapter handles that option using upstream documentation. `coreutils --help` prints the twelve-verb bundle listing. A direct name such as `fmt --help` prints that verb's usage line from the same manual, which is the implemented option set rather than the full GNU surface. The `coreutils` name retains the upstream twelve-command multiplexer in addition to individual names.

A failed write to standard output is a status 1 error. The bash-os adapter reports `write error` when the command itself did not already return failure. Commands that already diagnosed the failure keep that diagnostic. This is not a claim of complete GNU diagnostics.

For example, both shipped `fmt` implementations wrap `one two three four` at width 10 as `one two` / `three four`. GNU fmt on the validation host instead produced `one two` / `three` / `four`. The availability of a uutils variant does not imply exact GNU parity for every option.

| Command | Default provider | Source |
| --- | --- | --- |
| `arch` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `b2sum` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `base32` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `base64` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `basename` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `basenc` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `cat` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `chgrp` | bash-os (MIT) | [loadables/chgrp.c](../vendor/bash-os/loadables/chgrp.c) |
| `chmod` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `chown` | bash-os (MIT) | [loadables/chown.c](../vendor/bash-os/loadables/chown.c) |
| `cksum` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `comm` | bash-os (MIT) | [loadables/comm.c](../vendor/bash-os/loadables/comm.c) |
| `coreutils` | bash-os (MIT) | [loadables/coreutils.c](../vendor/bash-os/loadables/coreutils.c) |
| `cp` | bash-os (MIT) | [loadables/cp.c](../vendor/bash-os/loadables/cp.c) |
| `csplit` | bash-os (MIT) | [loadables/csplit.c](../vendor/bash-os/loadables/csplit.c) |
| `cut` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `date` | bash-os (MIT) | [loadables/date.c](../vendor/bash-os/loadables/date.c) |
| `dd` | bash-os (MIT) | [loadables/dd.c](../vendor/bash-os/loadables/dd.c) |
| `df` | bash-os (MIT) | [loadables/df.c](../vendor/bash-os/loadables/df.c) |
| `dir` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `dircolors` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `dirname` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `du` | bash-os (MIT) | [loadables/du.c](../vendor/bash-os/loadables/du.c) |
| `echo` | brush (MIT) | Shell builtin |
| `env` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `expand` | bash-os (MIT) | [loadables/expand.c](../vendor/bash-os/loadables/expand.c) |
| `expr` | bash-os (MIT) | [loadables/expr.c](../vendor/bash-os/loadables/expr.c) |
| `factor` | bash-os (MIT) | [loadables/coreutils.c](../vendor/bash-os/loadables/coreutils.c) |
| `false` | brush (MIT) | Shell builtin |
| `fmt` | bash-os (MIT) | [loadables/coreutils.c](../vendor/bash-os/loadables/coreutils.c) |
| `fold` | bash-os (MIT) | [loadables/fold.c](../vendor/bash-os/loadables/fold.c) |
| `groups` | bash-os (MIT) | [loadables/coreutils.c](../vendor/bash-os/loadables/coreutils.c) |
| `head` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `hostid` | bash-os (MIT) | [loadables/hostid.c](../vendor/bash-os/loadables/hostid.c) |
| `hostname` | bash-os (MIT) | [loadables/hostname.c](../vendor/bash-os/loadables/hostname.c) |
| `id` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `install` | bash-os (MIT) | [loadables/coreutils.c](../vendor/bash-os/loadables/coreutils.c) |
| `join` | bash-os (MIT) | [loadables/join.c](../vendor/bash-os/loadables/join.c) |
| `link` | bash-os (MIT) | [loadables/link.c](../vendor/bash-os/loadables/link.c) |
| `ln` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `logname` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `ls` | bash-os (MIT) | [loadables/ls.c](../vendor/bash-os/loadables/ls.c) |
| `md5sum` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `mkdir` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `mkfifo` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `mknod` | bash-os (MIT) | [loadables/coreutils.c](../vendor/bash-os/loadables/coreutils.c) |
| `mktemp` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `more` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `mv` | bash-os (MIT) | [loadables/mv.c](../vendor/bash-os/loadables/mv.c) |
| `nice` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `nl` | bash-os (MIT) | [loadables/nl.c](../vendor/bash-os/loadables/nl.c) |
| `nohup` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `nproc` | bash-os (MIT) | [loadables/coreutils.c](../vendor/bash-os/loadables/coreutils.c) |
| `numfmt` | bash-os (MIT) | [loadables/coreutils.c](../vendor/bash-os/loadables/coreutils.c) |
| `od` | bash-os (MIT) | [loadables/od.c](../vendor/bash-os/loadables/od.c) |
| `paste` | bash-os (MIT) | [loadables/paste.c](../vendor/bash-os/loadables/paste.c) |
| `pathchk` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `pinky` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `pr` | bash-os (MIT) | [loadables/pr.c](../vendor/bash-os/loadables/pr.c) |
| `printenv` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `printf` | brush (MIT) | Shell builtin |
| `ptx` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `pwd` | brush (MIT) | Shell builtin |
| `readlink` | bash-os (MIT) | [loadables/readlink.c](../vendor/bash-os/loadables/readlink.c) |
| `realpath` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `rm` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `rmdir` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `seq` | bash-os (MIT) | [loadables/seq.c](../vendor/bash-os/loadables/seq.c) |
| `sha1sum` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `sha224sum` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `sha256sum` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `sha384sum` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `sha512sum` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `shred` | bash-os (MIT) | [loadables/coreutils.c](../vendor/bash-os/loadables/coreutils.c) |
| `shuf` | bash-os (MIT) | [loadables/coreutils.c](../vendor/bash-os/loadables/coreutils.c) |
| `sleep` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `sort` | bash-os (MIT) | [loadables/sort.c](../vendor/bash-os/loadables/sort.c) |
| `split` | bash-os (MIT) | [loadables/split.c](../vendor/bash-os/loadables/split.c) |
| `stat` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `stty` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `sum` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `sync` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `tac` | bash-os (MIT) | [loadables/tac.c](../vendor/bash-os/loadables/tac.c) |
| `tail` | bash-os (MIT) | [loadables/tail.c](../vendor/bash-os/loadables/tail.c) |
| `tee` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `test` | brush (MIT) | Shell builtin |
| `timeout` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `touch` | bash-os (MIT) | [loadables/touch.c](../vendor/bash-os/loadables/touch.c) |
| `tr` | bash-os (MIT) | [loadables/tr.c](../vendor/bash-os/loadables/tr.c) |
| `true` | brush (MIT) | Shell builtin |
| `truncate` | bash-os (MIT) | [loadables/truncate.c](../vendor/bash-os/loadables/truncate.c) |
| `tsort` | bash-os (MIT) | [loadables/coreutils.c](../vendor/bash-os/loadables/coreutils.c) |
| `tty` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `uname` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `unexpand` | bash-os (MIT) | [loadables/unexpand.c](../vendor/bash-os/loadables/unexpand.c) |
| `uniq` | bash-os (MIT) | [loadables/uniq.c](../vendor/bash-os/loadables/uniq.c) |
| `unlink` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `users` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `vdir` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `wc` | bash-os (MIT) | [loadables/wc.c](../vendor/bash-os/loadables/wc.c) |
| `who` | bash-os (MIT) | [loadables/who.c](../vendor/bash-os/loadables/who.c) |
| `whoami` | uutils 0.10.0 (MIT) | `brush-coreutils-builtins` |
| `yes` | bash-os (MIT) | [loadables/coreutils.c](../vendor/bash-os/loadables/coreutils.c) |

This is the initial command set, not a promise to implement every GNU coreutils utility or option. SELinux tools and `stdbuf` are outside this iteration. The first bundle does not include the rest of bash-os’s networking, service, UI, or shell-state loadables.
