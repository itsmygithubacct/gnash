# gnash

An MIT-licensed fork of [brush](https://github.com/reubeno/brush) with runtime `.so`
command modules. This first iteration supplies coreutils from the MIT portions of
[bash-os](https://github.com/itsmygithubacct/bash-os) and
[uutils/coreutils](https://github.com/uutils/coreutils).

## Build and run

Requires Linux with `/proc` and `memfd_create`, Rust 1.89 or newer,
Python 3.11+, a C compiler, and the usual native build tools. The initial build was
verified with Rust 1.98.1 on x86_64 Linux.

```sh
python3 scripts/build-gnash.py
./out/gnash
./out/gnash --no-config --norc --noprofile -c \
  'PATH=; printf "b\na\nb\n" | sort | uniq | wc -l'
```

Use `--release` for an optimized build. Release archives currently target x86_64
Linux with glibc; the release workflow builds on Ubuntu 24.04. The output layout is relocatable as a unit:

```text
out/gnash
out/lib/gnash/uutils.so
out/lib/gnash/bash-os-coreutils.so
out/licenses/
```

The two modules load before shell startup scripts. bash-os supplies 47 commands;
uutils fills out the command set. Existing shell builtins such as `echo`, `printf`,
`pwd`, `true`, `false`, and `test` retain their normal shell implementations.
See the [command inventory](docs/gnash-coreutils.md) for the provider of each name.

`GNASH_MODULE_DIR=/absolute/path` overrides the module directory.
`GNASH_NO_COREUTILS=1` starts without the bundled modules. For development,
`GNASH_NO_COREUTILS=1 cargo run -p gnash -- -c 'echo hello'` builds just the shell.
The installed `out/` layout is produced by the build script, not by `cargo build` alone.

## Release archives

From a built release layout, create and test the relocatable archive:

```sh
python3 scripts/build-gnash.py --release
python3 scripts/package-gnash.py create
python3 scripts/package-gnash.py verify out/dist/gnash-0.1.0-x86_64-unknown-linux-gnu.tar.gz
```

The archive and SHA-256 checksum are written to `out/dist/`. The build records
`out/PROVENANCE.json`. Packaging refuses a layout whose binaries or source inputs,
including Rust code and dependencies, no longer match that stamp. A dirty tree
is identified by a source fingerprint instead of a source commit.
`--tag` requires a clean tree at the build commit. Extract the archive and run
`./gnash` inside its directory. Keep its `lib/`, `licenses/`, and documentation
together. The included native ABI header can be used to build modules.
Cargo registry and container publication are disabled.

## Runtime modules

```sh
cc -shared -fPIC -I include examples/loadables/hello.c -o /tmp/hello.so
./out/gnash --no-config -c '
  enable -f /tmp/hello.so hello
  hello world
  enable -n hello
  enable hello
  enable -d hello
'
```

Omitting names after `enable -f FILE` registers every command in that module.
`enable -d NAME` unloads a registration and restores a replaced shell builtin.
Names are validated before any registration changes. Special builtins cannot be replaced.

The [ABI documentation](docs/gnash-loadables.md) and [C header](include/gnash.h)
describe how to write modules. ABI v1 runs each command in an isolated worker
process so C stdio, cwd, environment, signals, and file descriptors work with
brush's concurrent shell execution. Modules use **gnash's ABI**, and existing
Bash `.so` binaries need recompilation through the source adapter.

This is an initial implementation, with process startup overhead per native command.
ABI v1 commands cannot modify variables, arrays, traps, or cwd in the parent shell.
Commands that launch other programs, such as `env`, `nice`, and `timeout`, still
resolve those programs through the worker's `PATH`. bash-os commands implement
their upstream option sets; full GNU coreutils parity is not claimed. Brush's
existing limitations remain, including `wait PID`; plain `wait` works.

## Sources and licenses

- Brush base: `fca2095ccc49fed0949924cbaf1433c44b61c164`, preserved in the source notices.
- bash-os: `work/0.1.3` at `955f9c93ebaadf13adedeb446e9e2e6f5d442a95`, selected files
  in [vendor/bash-os](vendor/bash-os/manifest.json), unchanged and SHA-256 checked.
- uutils crates: version 0.10.0, resolved versions and checksums in `Cargo.lock`.

Gnash, brush, the selected bash-os commands, and uutils are MIT licensed. The `nl`
regex fallback retains musl's MIT and TRE's BSD-2-Clause notices. Dependencies
retain their own permissive licenses. The build verifies a permissive
license choice for every package in the Linux normal/build dependency graph and
collects notices in `out/licenses/`. Upstream test tooling has MPL dependencies;
they are excluded from the delivered binaries. No GNU Bash code or headers, GPL
Bash samples, or LGPL bash-os helpers are used in the modules.

See [LICENSE](LICENSE), [third-party notes](licenses/README.md), and the preserved
[upstream brush README](README.brush.md).

## Checks and source refresh

```sh
python3 tests/gnash/test_loadables.py
python3 tests/gnash/test_native_review.py
python3 tests/gnash/test_adapter_review.py
python3 tests/gnash/test_packaging_review.py
python3 tests/gnash/test_sanitizers.py
python3 scripts/vendor-bash-os.py
python3 scripts/audit-licenses.py
cargo fmt --all --check
cargo clippy --workspace --all-features --all-targets
cargo test --workspace
cargo deny --all-features check all
```

`python3 scripts/vendor-bash-os.py --fetch` restores the exact reviewed upstream
files. Updating the revision or command set requires reviewing licenses and updating
the manifest checksums; the build never silently tracks upstream `main`.
