/* SPDX-License-Identifier: MIT
 * Minimal source adapter for MIT bash-os commands. This is original gnash code;
 * no GNU Bash headers or implementation are used. It is NOT a Bash binary ABI.
 */
#ifndef GNASH_COMPAT_LOADABLES_H
#define GNASH_COMPAT_LOADABLES_H
#include <stdio.h>
#include <stdlib.h>
#include <signal.h>
#include <unistd.h>
#include <stdint.h>
typedef struct word_desc { char *word; int flags; } WORD_DESC;
typedef struct word_list { struct word_list *next; WORD_DESC *word; } WORD_LIST;
struct builtin {
    const char *name;
    int (*function)(WORD_LIST *);
    int flags;
    char **long_doc;
    const char *short_doc;
    void *handle;
};
#define EXECUTION_SUCCESS 0
#define EXECUTION_FAILURE 1
#define EX_USAGE 2
#define BUILTIN_ENABLED 1
/* Workers use ordinary process signals, not Bash's in-process longjmp traps. */
#define QUIT ((void) 0)
extern volatile sig_atomic_t interrupt_state, terminating_signal;
void builtin_error(const char *, ...) __attribute__((format(printf, 1, 2)));
void builtin_warning(const char *, ...) __attribute__((format(printf, 1, 2)));
const char *shell_version_string(void);
void builtin_usage(void);
void builtin_help(void);
int sh_chkwrite(int);
void *xmalloc(size_t);
void *xrealloc(void *, size_t);
void fatal_error(const char *, ...) __attribute__((noreturn, format(printf, 1, 2)));
int gnash_bash_run(int, char **, struct builtin *, const char *);
#endif
