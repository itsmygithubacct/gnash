# gnash

This archive contains gnash for x86_64 Linux with glibc, its two native coreutils
modules, and their license notices. `BUILD-INFO.json` records the source revision
when that revision matches the packaged tree, whether the tree was dirty, the
source fingerprint, compilers, pinned bash-os revision, artifact checksums, and
the build host's libc version. Files
are mode 0644, and the executable and native modules are mode 0755. Use a Linux
installation with compatible system libraries.

Run `./gnash` from this directory, or add this directory to your `PATH`. Keep
`gnash`, `lib/`, and `licenses/` together when relocating the installation.

```sh
./gnash --no-config --noprofile --norc -c \
  'PATH=; printf "b\na\nb\n" | sort | uniq | wc -l'
```

The modules load automatically. Set `GNASH_NO_COREUTILS=1` to start without them.
`GNASH_MODULE_DIR` can select a different module directory. The
[command inventory](docs/gnash-coreutils.md) identifies each command's provider,
and the [module ABI](docs/gnash-loadables.md) explains how to add commands.

The archive includes `include/gnash.h` and `examples/loadables/hello.c` for building
a sample module with a C compiler. Existing Bash module binaries require a source
adapter and recompilation. Module commands cannot change the parent shell's
variables or working directory.

Source code and issue tracking: [itsmygithubacct/gnash](https://github.com/itsmygithubacct/gnash).
Gnash and its brush base are MIT licensed. Third-party packages retain their own
licenses; retain `LICENSE`, `licenses/`, and the pinned bash-os notices when
redistributing this archive.
