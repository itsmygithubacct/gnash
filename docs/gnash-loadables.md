# Native command ABI v1

The public C contract is [`include/gnash.h`](../include/gnash.h). Export a function
named `gnash_module_v1` returning a pointer to an immutable `struct gnash_module`.
Set `abi_version` to 1, `struct_size` to `sizeof(struct gnash_module)`, and provide
between 1 and 4096 command descriptors. Each descriptor names a command and an
`int run(int argc, char **argv)` function. Names are nonempty ASCII letters,
digits, underscores, and hyphens; duplicates are rejected.

`argc` includes the command name in `argv[0]`. Each argument is writable and
NUL-terminated, and `argv[argc]` is NULL. Argument pointers live through the call;
the module must not retain or free them. Return a shell status in 0–255. The worker
flushes C stdio before exiting. Modules with their own I/O runtime must flush that
runtime before returning. Exiting directly is also confined to the worker.

All descriptors, names, pointers, and function signatures must be valid and remain
immutable until the library unloads. Modules are trusted native code with the same
authority as the shell. Descriptor validation catches interface mistakes; it does
not turn arbitrary machine code into a sandbox. ELF constructors execute while
loading metadata in the shell and again when a worker loads the module; constructors
must not perform command work, create threads, or mutate shell process state.

## Registration and lifetime

`enable -f FILE NAME...` resolves FILE against the shell's working directory and
loads the exact file; it does not search `PATH`. Omitting NAME loads all entries.
The loader validates the complete table and requested names before changing the
builtin registry. Loading a name again replaces its previous native implementation.
Shell functions retain their usual precedence; `command NAME` and `builtin NAME`
invoke the enabled native builtin directly.
Like Bash, `exec NAME` searches for an external executable; it does not invoke
shell builtins, including native registrations. Use `NAME` or `command NAME`
to run an enabled native command.

`enable -n NAME` disables lookup without unloading. `enable NAME` reenables it.
`enable -d NAME...` removes native registrations, restoring any original static
builtin. A static builtin that was originally disabled is restored disabled.
Replacing POSIX special builtins is rejected. Subshells inherit registrations and
then modify their own registry independently.

The parent copies the ELF file into a sealed memfd before `dlopen`. References
retain both the library handle and that immutable file. Deleting, overwriting,
or replacing the source path cannot change an already loaded command. Shell clones
and running workers keep the necessary lifetime after other registrations unload.
Modules should be self-contained apart from ordinary system shared libraries;
`$ORIGIN` paths relative to the original `.so` location are not supported by the
memfd loading model. Build for the host architecture and libc.

## Execution

The shell routes native commands through its external-process launch machinery.
It starts its running image through `/proc/self/exe` using a private worker flag,
so an upgrade or removal of the executable path cannot redirect worker startup. It passes the sealed
module through an unused inherited fd. The worker dispatches before shell options,
profiles, or the asynchronous runtime are initialized. The launch machinery applies
the command's cwd, exported environment, redirections, pipeline fds, and process
group policy. An explicit close of standard input, output, or error is carried in
the worker protocol. The Rust runtime reopens those descriptors onto `/dev/null`
before `main`, so the worker closes them only after `dlopen` and locale setup.
SIGPIPE is reset to its ordinary CLI disposition and libc's locale
is initialized from the environment. The worker drops its copy of the sealed
snapshot after `dlopen`.

ABI v1 is for process-style commands. It does not expose parent shell mutation,
Bash symbols, or Rust objects across the library boundary. A future stateful ABI
will need an explicit host API. In particular, Bash array extensions from loadables
such as `cut -a` or `stat -A` are not part of this version.

## Source adaptation

The bash-os module links selected MIT C files, a small original compatibility
adapter in `loadables/compat`, and a descriptor table generated from the vendor
manifest. The adapter provides word lists, diagnostics, allocation helpers, and
the option parsing needed by these sources. It also supplies warning diagnostics
and explicit LIFO cleanup frames for `unexpand`. Signals terminate the worker
normally; Bash's in-process nonlocal exits are not part of this adapter.
GNU Bash headers are not used.
The upstream `coreutils` multiplexer is also exposed as individual command names.

`gnash-coreutils` is a Rust `cdylib` exposing the same C ABI around brush's existing
MIT uutils adapters. All Rust types stay inside the module. It can be loaded and
unloaded through the same `enable` interface as a C module.

The fork adds an optional `native` field to `brush_core::builtins::Registration`.
Consumers constructing registrations directly must set it to `None` for ordinary
Rust builtins. `Registration::special` is no longer a const function because a
registration can own a native module. This is a source API change in the fork;
the upstream crates and history remain available for future merges.
`ProcessGroupPolicy::ShellJobControl` follows the current job-control option at
each launch, including changes from `set -m` and `set +m` inside a script.
Consumers exhaustively matching that public enum must handle the new variant.
