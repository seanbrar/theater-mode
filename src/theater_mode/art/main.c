/*
 * main.c — CLI entry point for theater-art.
 *
 * Usage: theater-art <input_image> <output_argb> <target_width> <target_height> <dim_millis>
 */

#define _DEFAULT_SOURCE
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "pipeline.h"

#ifndef THEATER_ART_VERSION
#define THEATER_ART_VERSION "unknown"
#endif

static void print_usage(FILE *out) {
    fprintf(out,
            "theater-art %s — Steam library hero ambient backdrop renderer.\n"
            "\n"
            "Generates raw ARGB8888 composited ambient backdrops from Steam hero art.\n"
            "\n"
            "Usage: theater-art [--version] [--help]\n"
            "       theater-art <input_image> <output_argb> <target_width> <target_height> <dim_millis>\n"
            "\n"
            "Arguments:\n"
            "  input_image    Path to input hero artwork (JPEG or PNG)\n"
            "  output_argb    Path to destination raw ARGB8888 file\n"
            "  target_width   Target canvas width in pixels (1..16384)\n"
            "  target_height  Target canvas height in pixels (1..16384)\n"
            "  dim_millis     Dimming factor in integer thousandths (0..1000, e.g. 400 for 0.40)\n",
            THEATER_ART_VERSION);
}

int main(int argc, char **argv) {
    if (argc == 2) {
        if (strcmp(argv[1], "--version") == 0 || strcmp(argv[1], "-V") == 0) {
            printf("theater-art %s\n", THEATER_ART_VERSION);
            return 0;
        }
        if (strcmp(argv[1], "--help") == 0 || strcmp(argv[1], "-h") == 0) {
            print_usage(stdout);
            return 0;
        }
    }

    if (argc != 6) {
        print_usage(stderr);
        return 1;
    }

    const char *input_path = argv[1];
    const char *output_path = argv[2];

    char *endptr = NULL;
    errno = 0;
    long width = strtol(argv[3], &endptr, 10);
    if (errno != 0 || !endptr || endptr == argv[3] || *endptr != '\0' || width <= 0 || width > 16384) {
        fprintf(stderr, "error: invalid target_width '%s' (expected 1..16384)\n", argv[3]);
        return 1;
    }

    errno = 0;
    long height = strtol(argv[4], &endptr, 10);
    if (errno != 0 || !endptr || endptr == argv[4] || *endptr != '\0' || height <= 0 || height > 16384) {
        fprintf(stderr, "error: invalid target_height '%s' (expected 1..16384)\n", argv[4]);
        return 1;
    }

    errno = 0;
    long dim_millis = strtol(argv[5], &endptr, 10);
    if (errno != 0 || !endptr || endptr == argv[5] || *endptr != '\0' || dim_millis < 0 || dim_millis > 1000) {
        fprintf(stderr, "error: invalid dim_millis '%s' (expected integer 0..1000)\n", argv[5]);
        return 1;
    }

    int ret = render_artwork_pipeline(input_path, output_path, (int)width, (int)height, (int)dim_millis);
    if (ret != 0) {
        fprintf(stderr, "error: failed to render artwork '%s' -> '%s'\n", input_path, output_path);
        return 1;
    }

    return 0;
}
