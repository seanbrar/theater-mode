/*
 * pipeline.h — Image processing pipeline for theater-art.
 *
 * Reentrant, zero-shared-state image transformation pipeline matching
 * theater-mode ambient backdrop composition.
 */

#ifndef THEATER_ART_PIPELINE_H
#define THEATER_ART_PIPELINE_H

#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

/* Source and target images above this pixel count are rejected before allocation. */
#define MAX_TOTAL_PIXELS (32 * 1024 * 1024)

struct image_buffer {
    int width;
    int height;
    int channels; /* 3 for RGB, 4 for ARGB/BGRA */
    int stride;   /* bytes per row */
    uint8_t *data;
};

struct mask_buffer {
    int length;
    bool horizontal;
    uint8_t *data;
};

/* Allocate an empty image buffer. Return NULL for invalid geometry or allocation failure. */
struct image_buffer *image_buffer_create(int width, int height, int channels);

/* Free an image buffer and its pixels. Accept NULL. */
void image_buffer_free(struct image_buffer *buf);

/* Load a bounded, complete JPEG or PNG into RGB24. Return NULL for invalid input,
 * an unreadable file, or allocation failure. The caller owns the returned buffer. */
struct image_buffer *image_load(const char *path, size_t max_bytes);

/* Resample RGB with a support-scaled triangle filter. Return NULL for invalid input
 * or allocation failure. The caller owns the returned buffer. */
struct image_buffer *image_resample_bilinear(const struct image_buffer *src, int dst_w, int dst_h);

/* Resample RGB with Lanczos-3. Return NULL for invalid input or allocation failure.
 * The caller owns the returned buffer. */
struct image_buffer *image_resample_lanczos(const struct image_buffer *src, int dst_w, int dst_h);

/* Apply 3-pass extended box blur (Kutskir integer-box Gaussian approximation) in place. */
void image_gaussian_blur(struct image_buffer *img, float radius);

/* Multiply RGB channels by factor, clamping in [0, 255]. */
void image_enhance_brightness(struct image_buffer *img, float factor);

/* Generate a 1D feather mask. Return NULL for invalid dimensions or allocation
 * failure. The caller owns the returned mask. */
struct mask_buffer *image_create_feather_mask(int length, int feather, bool horizontal);

/* Free a feather mask. Accept NULL. */
void mask_buffer_free(struct mask_buffer *mask);

/* Composite foreground over backdrop, clipping overhangs. A horizontal mask varies
 * by x; a vertical mask varies by y. NULL selects fully opaque compositing. */
void image_composite_over(
    struct image_buffer *backdrop,
    const struct image_buffer *foreground,
    int pos_x,
    int pos_y,
    const struct mask_buffer *mask
);

/* Atomically write raw ARGB8888 in BGRA byte order through `<target>.tmp`.
 * Return 0 on success and nonzero for invalid input, allocation failure, or I/O failure. */
int image_write_argb(const struct image_buffer *img, const char *target_path);

/* Render a backdrop to raw ARGB. `dim_millis` must be in 0..1000. Return 0 on
 * success and nonzero for invalid input, allocation failure, or an I/O failure. */
int render_artwork_pipeline(
    const char *input_path,
    const char *output_path,
    int target_width,
    int target_height,
    int dim_millis
);

#endif /* THEATER_ART_PIPELINE_H */
