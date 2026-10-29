/* SPDX-License-Identifier: MIT */
#include "loadables.h"
#include "bashgetopt.h"
#include "unwind_prot.h"
#include <errno.h>
#include <limits.h>
#include <stdarg.h>
#include <string.h>

volatile sig_atomic_t interrupt_state, terminating_signal;
static struct builtin *active;
static const char *command_name;
/* coreutils.c already puts the verb in many diagnostics. Direct entry points
 * set command_name to that same verb, so those lines must not be prefixed twice. */
static int verb_in_diagnostics;
WORD_LIST *loptend;
char *list_optarg;
static char *option_cursor;
static int option_started;

/* Upstream expand prints this inside "Bash %s". A bare revision would be
 * reported as a Bash version. Keep the accurate wording and name this module. */
const char *shell_version_string(void) {
    return "not linked; gnash/bash-os " GNASH_BASH_OS_REVISION;
}

/* A frame marker and its callbacks share one LIFO stack. Only the isolated,
 * single-threaded worker invokes these functions; normal cleanup runs explicitly.
 * Frame names must remain live until run_unwind_frame, as do callback arguments.
 */
struct unwind_entry {
    struct unwind_entry *next;
    const char *frame;
    void (*cleanup)(void *);
    void *argument;
};
static struct unwind_entry *unwind_stack;

void begin_unwind_frame(const char *name) {
    struct unwind_entry *entry = xmalloc(sizeof(*entry));
    *entry = (struct unwind_entry){unwind_stack, name, NULL, NULL};
    unwind_stack = entry;
}
void add_unwind_protect(void (*cleanup)(void *), void *argument) {
    if (!unwind_stack) fatal_error("cleanup registered without a frame");
    struct unwind_entry *entry = xmalloc(sizeof(*entry));
    *entry = (struct unwind_entry){unwind_stack, NULL, cleanup, argument};
    unwind_stack = entry;
}
void run_unwind_frame(const char *name) {
    while (unwind_stack) {
        struct unwind_entry *entry = unwind_stack;
        unwind_stack = entry->next;
        int done = entry->frame && !strcmp(entry->frame, name);
        if (entry->cleanup) entry->cleanup(entry->argument);
        free(entry);
        if (done) return;
    }
    fatal_error("unknown cleanup frame: %s", name);
}

static char *format_message(const char *format, va_list args) {
    va_list measure;
    va_copy(measure, args);
    int needed = vsnprintf(NULL, 0, format, measure);
    va_end(measure);
    if (needed < 0 || needed == INT_MAX) return NULL;
    char *body = malloc((size_t)needed + 1);
    if (!body) return NULL;
    if (vsnprintf(body, (size_t)needed + 1, format, args) < 0) {
        free(body);
        return NULL;
    }
    return body;
}
void builtin_error(const char *format, ...) {
    va_list args;
    const char *name = command_name ? command_name : "gnash";
    va_start(args, format);
    char *body = format_message(format, args);
    va_end(args);
    if (!body) {
        fprintf(stderr, "%s: out of memory\n", name);
        return;
    }
    size_t namelen = strlen(name);
    int already = verb_in_diagnostics && namelen
        && strncmp(body, name, namelen) == 0 && body[namelen] == ':';
    if (already) fprintf(stderr, "%s\n", body);
    else fprintf(stderr, "%s: %s\n", name, body);
    free(body);
}
void builtin_warning(const char *format, ...) {
    va_list args;
    fprintf(stderr, "%s: warning: ", command_name ? command_name : "gnash");
    va_start(args, format);
    vfprintf(stderr, format, args);
    va_end(args);
    fputc('\n', stderr);
}
void builtin_usage(void) {
    fprintf(stderr, "usage: %s\n", active ? active->short_doc : "COMMAND [ARGS...]");
}
static int is_name_char(unsigned char c) {
    return (c >= '0' && c <= '9') || (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z')
        || c == '_' || c == '-';
}
/* Match VERB as a whole word so "fmt" does not hit "numfmt". */
static int line_has_verb(const char *line, const char *verb) {
    size_t n = strlen(verb);
    if (!n) return 0;
    for (const char *p = line; (p = strstr(p, verb)); p += n) {
        unsigned char left = p == line ? 0 : (unsigned char)p[-1];
        unsigned char right = (unsigned char)p[n];
        if ((p == line || !is_name_char(left)) && !is_name_char(right)) return 1;
    }
    return 0;
}
void builtin_help(void) {
    if (!active) return;
    for (char **line = active->long_doc; line && *line; ++line) puts(*line);
    printf("Usage: %s\n", active->short_doc ? active->short_doc : "COMMAND [ARGS...]");
}
/* The twelve multiplexer verbs share one upstream manual. A direct `fmt` entry
 * point should show that verb's usage line, not the bundle listing. Returns 0
 * when this struct has no matching line so the caller can print the full manual. */
static int print_verb_help(const char *invoked, const char *verb) {
    if (!active || !active->long_doc || !verb || !*verb) return 0;
    for (char **line = active->long_doc; *line; ++line) {
        if (!line_has_verb(*line, verb)) continue;
        const char *text = *line;
        while (*text == ' ' || *text == '\t') text++;
        char lead[96];
        int lead_n = snprintf(lead, sizeof lead, "coreutils %s", verb);
        const char *rest = text;
        if (lead_n > 0 && (size_t)lead_n < sizeof lead
            && strncmp(text, lead, (size_t)lead_n) == 0
            && (text[lead_n] == '\0' || text[lead_n] == ' ' || text[lead_n] == '\t')) {
            rest = text + lead_n;
            while (*rest == ' ' || *rest == '\t') rest++;
        }
        const char *name = invoked && *invoked ? invoked : verb;
        if (*rest) printf("%s %s\nUsage: %s %s\n", name, rest, name, rest);
        else printf("%s\nUsage: %s\n", name, name);
        return 1;
    }
    return 0;
}
/* Save the errno from the failing flush. A stale errno must not be reported
 * when the stream is already marked failed and fflush itself succeeds. */
static int stdout_write_failed(int *err) {
    errno = 0;
    int flush_failed = fflush(stdout) == EOF;
    if (!flush_failed && !ferror(stdout)) return 0;
    *err = errno ? errno : EIO;
    clearerr(stdout);
    return 1;
}
int sh_chkwrite(int status) {
    int err = 0;
    if (!stdout_write_failed(&err)) return status;
    /* seq returns failure through this function without its own diagnostic. */
    builtin_error("write error: %s", strerror(err));
    return EXECUTION_FAILURE;
}
/* Commands that already returned failure usually printed the reason. Do not
 * add a second write-error line for unexpand or the coreutils multiplexer. */
static int finish_command(int status) {
    int err = 0;
    if (!stdout_write_failed(&err)) return status;
    if (status == EXECUTION_SUCCESS)
        builtin_error("write error: %s", strerror(err));
    return EXECUTION_FAILURE;
}
void fatal_error(const char *format, ...) {
    va_list args;
    va_start(args, format);
    vfprintf(stderr, format, args);
    va_end(args);
    fputc('\n', stderr);
    exit(EXECUTION_FAILURE);
}
void *xmalloc(size_t size) {
    void *p = malloc(size ? size : 1);
    if (!p) fatal_error("out of memory");
    return p;
}
void *xrealloc(void *p, size_t size) {
    p = realloc(p, size ? size : 1);
    if (!p) fatal_error("out of memory");
    return p;
}
void reset_internal_getopt(void) {
    loptend = NULL; list_optarg = NULL; option_cursor = NULL; option_started = 0;
}
int internal_getopt(WORD_LIST *list, const char *options) {
    if (!option_started) { loptend = list; option_started = 1; }
    list_optarg = NULL;
    if (!option_cursor || !*option_cursor) {
        if (!loptend) return -1;
        char *word = loptend->word->word;
        if (word[0] != '-' || !word[1]) return -1;
        loptend = loptend->next;
        if (!strcmp(word, "--")) return -1;
        option_cursor = word + 1;
    }
    int option = (unsigned char)*option_cursor++;
    const char *spec = strchr(options, option);
    if (!spec || option == ':') return '?';
    if (spec[1] == ':') {
        if (*option_cursor) { list_optarg = option_cursor; option_cursor = NULL; }
        else if (loptend) { list_optarg = loptend->word->word; loptend = loptend->next; }
        else return '?';
    }
    return option;
}
int gnash_bash_run(int argc, char **argv, struct builtin *builtin, const char *subcommand) {
    active = builtin;
    command_name = argv && argc > 0 ? argv[0] : NULL;
    verb_in_diagnostics = subcommand != NULL;
    /* Sole --help/--version matches the commands that treat a later --help as data.
     * expr --help is help; expr --help + 1 is an expression error. */
    if (argc == 2 && argv && argv[1] && !strcmp(argv[1], "--help")) {
        if (!subcommand || !print_verb_help(command_name, subcommand)) builtin_help();
        return sh_chkwrite(0);
    }
    if (argc == 2 && argv && argv[1] && !strcmp(argv[1], "--version")) {
        printf("%s (gnash bash-os module) %s\nMIT License\n",
               command_name ? command_name : "gnash", GNASH_BASH_OS_REVISION);
        return sh_chkwrite(0);
    }
    if (argc < 1 || !argv) return EXECUTION_FAILURE;
    size_t count = (size_t)(argc - 1) + (subcommand != NULL);
    WORD_LIST *nodes = calloc(count ? count : 1, sizeof(*nodes));
    WORD_DESC *words = calloc(count ? count : 1, sizeof(*words));
    if (!nodes || !words) fatal_error("out of memory");
    for (size_t i = 0; i < count; ++i) {
        words[i].word = subcommand && i == 0 ? (char *)subcommand : argv[i + 1 - (subcommand != NULL)];
        nodes[i].word = &words[i];
        nodes[i].next = i + 1 < count ? &nodes[i + 1] : NULL;
    }
    int status = builtin->function(count ? nodes : NULL);
    free(nodes); free(words);
    return finish_command(status);
}
