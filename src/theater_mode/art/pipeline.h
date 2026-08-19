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

/* Maximum total pixel count allowed for source and target images (32 Megapixels). */
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

/* Allocate an empty image buffer. */
struct image_buffer *image_buffer_create(int width, int height, int channels);

/* Free an image buffer and its pixel storage. */
void image_buffer_free(struct image_buffer *buf);

/* Load a JPEG or PNG from disk into an RGB24 image buffer. Returns NULL on failure or corrupt data. */
struct image_buffer *image_load(const char *path, size_t max_bytes);

/* Resample an RGB image using separable bilinear filtering (support-scaled for downscale). */
struct image_buffer *image_resample_bilinear(const struct image_buffer *src, int dst_w, int dst_h);

/* Resample an RGB image using separable Lanczos-3 sinc filtering. */
struct image_buffer *image_resample_lanczos(const struct image_buffer *src, int dst_w, int dst_h);

/* Apply 3-pass extended box blur (Kutskir integer-box Gaussian approximation) in place. */
void image_gaussian_blur(struct image_buffer *img, float radius);

/* Multiply RGB channels by factor, truncating/clamping in [0, 255]. */
void image_enhance_brightness(struct image_buffer *img, float factor);

/* Generate 1D feather gradient mask. */
struct mask_buffer *image_create_feather_mask(int length, int feather, bool horizontal);
void mask_buffer_free(struct mask_buffer *mask);

/* Composite foreground over backdrop with optional feather mask. */
void image_composite_over(
    struct image_buffer *backdrop,
    const struct image_buffer *foreground,
    int pos_x,
    int pos_y,
    const struct mask_buffer *mask
);

/* Write the final image as raw ARGB8888 (BGRA byte order) atomically via deterministic .tmp. */
int image_write_argb(const struct image_buffer *img, const char *target_path);

/* Full backdrop rendering pipeline from input image to output ARGB using integer dim_millis (0..1000). */
int render_artwork_pipeline(
    const char *input_path,
    const char *output_path,
    int target_width,
    int target_height,
    int dim_millis
);

#endif /* THEATER_ART_PIPELINE_H */
