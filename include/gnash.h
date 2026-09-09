/* SPDX-License-Identifier: MIT
 * gnash native command ABI v1. Modules and descriptor memory are trusted code.
 * Compile: cc -shared -fPIC -I include example.c -o example.so
 */
#ifndef GNASH_H
#define GNASH_H
#include <stddef.h>
#include <stdint.h>
#define GNASH_ABI_VERSION 1u
struct gnash_command {
    const char *name;
    int (*run)(int argc, char **argv);
};
struct gnash_module {
    uint32_t abi_version;
    uint32_t struct_size;
    size_t command_count;
    const struct gnash_command *commands;
};
/* Return immutable storage valid until dlclose. argc includes argv[0]; argv[argc]
 * is NULL. run executes once in a fresh worker and can use ordinary libc APIs.
 * No parent shell mutation or Bash binary ABI is provided by ABI v1.
 */
const struct gnash_module *gnash_module_v1(void);
#define GNASH_MODULE(table) \
    const struct gnash_module *gnash_module_v1(void) { \
        static const struct gnash_module module = { \
            GNASH_ABI_VERSION, sizeof(struct gnash_module), \
            sizeof(table) / sizeof((table)[0]), table }; \
        return &module; \
    }
#endif
