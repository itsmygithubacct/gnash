/* SPDX-License-Identifier: MIT */
#include "gnash.h"
#include <stdio.h>

static int hello(int argc, char **argv) {
    printf("Hello, %s!\n", argc > 1 ? argv[1] : "world");
    return 0;
}
static const struct gnash_command commands[] = {{"hello", hello}};
GNASH_MODULE(commands)
