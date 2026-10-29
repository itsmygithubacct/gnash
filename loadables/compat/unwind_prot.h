/* SPDX-License-Identifier: MIT
 * Original adapter for explicit cleanup frames in process-style loadables.
 * Signals terminate the worker; there is no Bash nonlocal-exit machinery.
 */
#ifndef GNASH_COMPAT_UNWIND_PROT_H
#define GNASH_COMPAT_UNWIND_PROT_H
void begin_unwind_frame(const char *name);
void add_unwind_protect(void (*cleanup)(void *), void *argument);
void run_unwind_frame(const char *name);
#endif
