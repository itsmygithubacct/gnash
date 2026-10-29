# Licensing and provenance

New gnash code is MIT licensed under the root `LICENSE`, which retains brush's
copyright notice. Imported bash-os files retain their original notices and have a
separate full MIT license in `vendor/bash-os/LICENSE`. Source hashes, upstream
revision, and the C symbol used for each command are in its `manifest.json`.
The `work/0.1.3` import also includes `loadables/common/nl-regex.h`, the private
`nl` fallback for platforms without `REG_STARTEND`. Its musl MIT and TRE
BSD-2-Clause notices are retained in the unmodified header and recorded in the
manifest's `file_licenses`. The build copies that header to
`out/licenses/bash-os-nl-regex.h`, which is also required in release archives.

The bash-os repository also uses GPL GNU Bash samples and some LGPL helpers.
Those files are not imported. The C adapter and public gnash ABI header are original
code; they do not incorporate Bash headers. Files mentioning the GPL consequence
of linking into GNU Bash retain that upstream comment; gnash builds them with their
independent MIT grant, without linking GNU Bash.

The uutils module uses MIT-licensed crates from uutils/coreutils 0.10.0. Cargo
dependencies retain their own licenses. `scripts/audit-licenses.py` traverses the
resolved normal/build dependency graph for the two delivered artifacts, excludes
development-only dependencies, and rejects licenses without a permissive choice.
For dual-licensed crates, use a permitted MIT/Apache/BSD/etc. alternative. In
particular, `self_cell` is used under Apache-2.0, not its optional GPL license.

The script writes `out/licenses/cargo-inventory.json` and copies license and notice
files next to it. Package directories listed by this audit are generated output:
obsolete notices are removed after a successful audit, including notices from
previously recorded package versions. Top-level gnash/bash-os notices are retained.
Supplemental notices missing from published crate archives are
recorded in `supplemental/manifest.json` with origin and SHA-256. Prepared extracts
are identified explicitly and retain the original source checksum separately.
The utf8-chars extract retains its author handle and license declaration; the
full Apache-2.0 terms accompany it. Keep `out/licenses`
with redistributed builds. The audit reports metadata and retained notices; it
does not relicense dependencies. System libraries such as libc are provided by the
host operating system and are not bundled by this build.

Brush's inherited test harness uses MPL-2.0 crates (`colored`, `indent`). They remain
development tools and are not dependencies of either delivered artifact. The
inherited workspace-wide `cargo deny` configuration accounts for those tools; the
separate gnash artifact audit enforces the stricter permissive runtime scope.
