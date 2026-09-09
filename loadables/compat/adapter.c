/* SPDX-License-Identifier: MIT */
#include "loadables.h"
#include "bashgetopt.h"
#include <stdarg.h>
#include <string.h>

volatile sig_atomic_t interrupt_state, terminating_signal;
static struct builtin *active;
static const char *command_name;
WORD_LIST *loptend;
char *list_optarg;
static char *option_cursor;
static int option_started;

void builtin_error(const char *format, ...) {
    va_list args;
    fprintf(stderr, "%s: ", command_name ? command_name : "gnash");
    va_start(args, format);
    vfprintf(stderr, format, args);
    va_end(args);
    fputc('\n', stderr);
}
void builtin_usage(void) {
    fprintf(stderr, "usage: %s\n", active ? active->short_doc : "COMMAND [ARGS...]");
}
void builtin_help(void) {
    if (!active) return;
    for (char **line = active->long_doc; line && *line; ++line) puts(*line);
    printf("Usage: %s\n", active->short_doc);
}
int sh_chkwrite(int status) {
    return fflush(stdout) == EOF || ferror(stdout) ? EXECUTION_FAILURE : status;
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
    command_name = argv[0];
    if (argc == 2 && !strcmp(argv[1], "--help")) { builtin_help(); return sh_chkwrite(0); }
    if (argc == 2 && !strcmp(argv[1], "--version")) {
        printf("%s (gnash bash-os module) 3ca562b0eb97\nMIT License\n", command_name);
        return sh_chkwrite(0);
    }
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
    return sh_chkwrite(status);
}
