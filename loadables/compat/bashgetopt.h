/* SPDX-License-Identifier: MIT */
#ifndef GNASH_COMPAT_GETOPT_H
#define GNASH_COMPAT_GETOPT_H
#include "loadables.h"
extern WORD_LIST *loptend;
extern char *list_optarg;
void reset_internal_getopt(void);
int internal_getopt(WORD_LIST *, const char *);
#endif
