/*
 * pipeline.c — Image processing pipeline for theater-art.
 *
 * Ambient backdrop composition and downscaling pipeline for game artwork.
 */

#define _DEFAULT_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <math.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#pragma GCC diagnostic push
#pragma GCC diagnostic ignored "-Wunused-function"
#pragma GCC diagnostic ignored "-Wunused-parameter"
#define STBI_ONLY_JPEG
#define STBI_ONLY_PNG
#define STBI_MAX_DIMENSIONS 16384
#define STBI_NO_STDIO
#define STB_IMAGE_IMPLEMENTATION
#include "stb_image.h"
#pragma GCC diagnostic pop

#include "pipeline.h"

#ifndef M_PI
#define M_PI 3.14159265358979323846
#endif

static inline uint8_t clamp_u8(int v) {
    if (v < 0) return 0;
    if (v > 255) return 255;
    return (uint8_t)v;
}

struct image_buffer *image_buffer_create(int width, int height, int channels) {
    if (width <= 0 || height <= 0 || (channels != 3 && channels != 4)) {
        return NULL;
    }
    if ((uint64_t)width * (uint64_t)height > MAX_TOTAL_PIXELS) {
        return NULL;
    }
    if ((size_t)width > SIZE_MAX / (size_t)height / (size_t)channels) {
        return NULL;
    }
    struct image_buffer *buf = malloc(sizeof(struct image_buffer));
    if (!buf) return NULL;

    buf->width = width;
    buf->height = height;
    buf->channels = channels;
    buf->stride = width * channels;
    buf->data = malloc((size_t)buf->stride * (size_t)height);
    if (!buf->data) {
        free(buf);
        return NULL;
    }
    return buf;
}

void image_buffer_free(struct image_buffer *buf) {
    if (!buf) return;
    free(buf->data);
    free(buf);
}

/* Inspect container framing to reject damaged streams from interrupted
 * downloads before decoding with stb_image. */

static bool is_valid_complete_jpeg(const uint8_t *data, size_t size) {
    if (!data || size < 4) return false;
    if (data[0] != 0xFF || data[1] != 0xD8) return false; /* SOI */

    size_t pos = 2;
    bool seen_scan = false;

    while (pos + 1 < size) {
        if (data[pos] != 0xFF) return false;
        while (pos + 1 < size && data[pos + 1] == 0xFF) pos++;
        if (pos + 1 >= size) return false;

        uint8_t marker = data[pos + 1];
        pos += 2;

        if (marker == 0xD9) return seen_scan;                            /* EOI */
        if (marker == 0xD8) return false;                                /* nested SOI */
        if (marker == 0x01 || (marker >= 0xD0 && marker <= 0xD7)) continue; /* standalone */

        if (pos + 1 >= size) return false;
        size_t seg_len = ((size_t)data[pos] << 8) | (size_t)data[pos + 1];
        if (seg_len < 2 || seg_len > size - pos) return false;
        pos += seg_len;

        if (marker != 0xDA) continue; /* not Start Of Scan */

        /* Byte-stuffed 0xFF00 and restart markers belong to the scan data. */
        seen_scan = true;
        while (pos + 1 < size) {
            if (data[pos] != 0xFF) { pos++; continue; }
            uint8_t next = data[pos + 1];
            if (next == 0xFF) { pos++; continue; }
            if (next == 0x00) { pos += 2; continue; }
            if (next >= 0xD0 && next <= 0xD7) { pos += 2; continue; }
            break;
        }
        if (pos + 1 >= size) return false;
    }
    return false;
}

static bool is_valid_complete_png(const uint8_t *data, size_t size) {
    static const uint8_t signature[8] = {0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A};
    if (!data || size < sizeof signature + 12) return false;
    if (memcmp(data, signature, sizeof signature) != 0) return false;

    size_t pos = sizeof signature;
    bool seen_header = false;

    while (pos + 12 <= size) {
        size_t chunk_len = ((size_t)data[pos] << 24) | ((size_t)data[pos + 1] << 16) |
                           ((size_t)data[pos + 2] << 8) | (size_t)data[pos + 3];
        if (chunk_len > 0x7FFFFFFF) return false;
        if (chunk_len > size - pos - 12) return false;

        const uint8_t *type = data + pos + 4;
        if (!seen_header && memcmp(type, "IHDR", 4) != 0) return false;
        seen_header = true;

        if (memcmp(type, "IEND", 4) == 0) return true;
        pos += 12 + chunk_len;
    }
    return false;
}

static bool is_valid_complete_image(const uint8_t *data, size_t size) {
    if (!data || size < 4) return false;
    if (data[0] == 0xFF && data[1] == 0xD8) return is_valid_complete_jpeg(data, size);
    return is_valid_complete_png(data, size);
}

/* Read image dimensions without allocating the decoded pixel buffer. */
static bool image_dimensions_within_limit(
    const uint8_t *data,
    size_t size,
    int *out_width,
    int *out_height
) {
    if (!data || size == 0 || size > INT_MAX) return false;

    int width = 0, height = 0, channels = 0;
    if (!stbi_info_from_memory(data, (int)size, &width, &height, &channels)) return false;
    if (width <= 0 || height <= 0 || (uint64_t)width * (uint64_t)height > MAX_TOTAL_PIXELS) {
        return false;
    }

    if (out_width) *out_width = width;
    if (out_height) *out_height = height;
    return true;
}

struct image_buffer *image_load(const char *path, size_t max_bytes) {
    if (!path || max_bytes == 0) return NULL;

    struct stat st;
    if (stat(path, &st) != 0) return NULL;
    if (!S_ISREG(st.st_mode) || st.st_size <= 4 || (size_t)st.st_size > max_bytes) return NULL;

    int fd = open(path, O_RDONLY);
    if (fd < 0) return NULL;

    size_t file_size = (size_t)st.st_size;
    uint8_t *file_buf = malloc(file_size);
    if (!file_buf) {
        close(fd);
        return NULL;
    }

    size_t total_read = 0;
    while (total_read < file_size) {
        ssize_t n = read(fd, file_buf + total_read, file_size - total_read);
        if (n < 0) {
            if (errno == EINTR) continue;
            free(file_buf);
            close(fd);
            return NULL;
        }
        if (n == 0) break;
        total_read += (size_t)n;
    }
    close(fd);

    int expected_w = 0, expected_h = 0;
    if (total_read != file_size || !is_valid_complete_image(file_buf, file_size) ||
        !image_dimensions_within_limit(file_buf, file_size, &expected_w, &expected_h)) {
        free(file_buf);
        return NULL;
    }

    int w = 0, h = 0, channels_in_file = 0;
    uint8_t *decoded = stbi_load_from_memory(file_buf, (int)file_size, &w, &h, &channels_in_file, 3);
    free(file_buf);

    if (!decoded || w != expected_w || h != expected_h) {
        if (decoded) free(decoded);
        return NULL;
    }

    struct image_buffer *buf = malloc(sizeof(struct image_buffer));
    if (!buf) {
        free(decoded);
        return NULL;
    }
    buf->width = w;
    buf->height = h;
    buf->channels = 3;
    buf->stride = w * 3;
    buf->data = decoded;
    return buf;
}

struct filter_weight_entry {
    int min_idx;
    int count;
    int weight_offset;
};

struct filter_table {
    int dst_size;
    int max_count;
    struct filter_weight_entry *entries;
    float *weights_pool;
};

static void free_filter_table(struct filter_table *tbl) {
    if (!tbl) return;
    free(tbl->entries);
    free(tbl->weights_pool);
    free(tbl);
}

typedef double (*filter_fn)(double x);

static double filter_triangle(double x) {
    double ax = fabs(x);
    return ax < 1.0 ? (1.0 - ax) : 0.0;
}

static double filter_lanczos3(double x) {
    double ax = fabs(x);
    if (ax < 1e-8) return 1.0;
    if (ax >= 3.0) return 0.0;
    double pix = M_PI * ax;
    return (sin(pix) / pix) * (sin(pix / 3.0) / (pix / 3.0));
}

/* Downscaling expands the filter support by the inverse scale. Each destination
 * sample's weights are normalized so a constant source retains its brightness. */
static struct filter_table *build_filter_table(
    int src_size,
    int dst_size,
    filter_fn fn,
    double base_radius
) {
    struct filter_table *tbl = malloc(sizeof(struct filter_table));
    if (!tbl) return NULL;

    tbl->dst_size = dst_size;
    tbl->entries = malloc((size_t)dst_size * sizeof(struct filter_weight_entry));
    if (!tbl->entries) {
        free(tbl);
        return NULL;
    }

    double scale = (double)dst_size / (double)src_size;
    double filter_scale = scale < 1.0 ? scale : 1.0;
    double support = base_radius / filter_scale;

    int total_weights = 0;
    int max_count = 0;
    for (int dst_i = 0; dst_i < dst_size; dst_i++) {
        double center = (dst_i + 0.5) / scale;
        int min_idx = (int)floor(center - support);
        int max_idx = (int)ceil(center + support);

        int count = max_idx - min_idx + 1;
        if (count < 1) count = 1;
        if (count > max_count) max_count = count;

        tbl->entries[dst_i].min_idx = min_idx;
        tbl->entries[dst_i].count = count;
        tbl->entries[dst_i].weight_offset = total_weights;
        total_weights += count;
    }

    tbl->max_count = max_count;
    tbl->weights_pool = malloc((size_t)total_weights * sizeof(float));
    if (!tbl->weights_pool) {
        free(tbl->entries);
        free(tbl);
        return NULL;
    }

    for (int dst_i = 0; dst_i < dst_size; dst_i++) {
        double center = (dst_i + 0.5) / scale;
        int min_idx = tbl->entries[dst_i].min_idx;
        int count = tbl->entries[dst_i].count;
        float *w_ptr = tbl->weights_pool + tbl->entries[dst_i].weight_offset;

        double total_weight = 0.0;
        for (int k = 0; k < count; k++) {
            int src_idx = min_idx + k;
            double dist = (src_idx + 0.5 - center) * filter_scale;
            double w = fn(dist);
            w_ptr[k] = (float)w;
            total_weight += w;
        }

        if (fabs(total_weight) > 1e-8) {
            float inv_tw = (float)(1.0 / total_weight);
            for (int k = 0; k < count; k++) {
                w_ptr[k] *= inv_tw;
            }
        }
    }
    return tbl;
}

/* The power-of-two ring holds every horizontal row needed by one vertical
 * kernel. Tags distinguish source rows whose slots collide after wraparound. */
static struct image_buffer *resample_separable_subregion(
    const struct image_buffer *src,
    int src_x, int src_y, int region_w, int region_h,
    int dst_w, int dst_h,
    filter_fn fn,
    double base_radius
) {
    if (!src || src->channels != 3 || dst_w <= 0 || dst_h <= 0 || region_w <= 0 || region_h <= 0) {
        return NULL;
    }
    if (src_x < 0 || src_y < 0 || src_x > src->width - region_w || src_y > src->height - region_h) {
        return NULL;
    }
    if ((uint64_t)dst_w * (uint64_t)dst_h > MAX_TOTAL_PIXELS) return NULL;

    if (src_x == 0 && src_y == 0 && region_w == src->width && region_h == src->height && dst_w == region_w && dst_h == region_h) {
        struct image_buffer *dst = image_buffer_create(dst_w, dst_h, 3);
        if (!dst) return NULL;
        memcpy(dst->data, src->data, (size_t)dst->stride * (size_t)dst_h);
        return dst;
    }

    struct filter_table *h_tbl = build_filter_table(region_w, dst_w, fn, base_radius);
    struct filter_table *v_tbl = build_filter_table(region_h, dst_h, fn, base_radius);
    if (!h_tbl || !v_tbl) {
        free_filter_table(h_tbl);
        free_filter_table(v_tbl);
        return NULL;
    }

    struct image_buffer *dst = image_buffer_create(dst_w, dst_h, 3);
    if (!dst) {
        free_filter_table(h_tbl);
        free_filter_table(v_tbl);
        return NULL;
    }

    int ring_size = v_tbl->max_count + 4;
    int ring_capacity = 1;
    while (ring_capacity < ring_size) ring_capacity <<= 1;
    int ring_mask = ring_capacity - 1;

    size_t row_floats = (size_t)dst_w * 3;
    float *ring_buf = malloc((size_t)ring_capacity * row_floats * sizeof(float));
    int *ring_tag = malloc((size_t)ring_capacity * sizeof(int));
    float *row_accum = malloc(row_floats * sizeof(float));

    if (!ring_buf || !ring_tag || !row_accum) {
        free(ring_buf);
        free(ring_tag);
        free(row_accum);
        image_buffer_free(dst);
        free_filter_table(h_tbl);
        free_filter_table(v_tbl);
        return NULL;
    }

    for (int i = 0; i < ring_capacity; i++) ring_tag[i] = -1;

    for (int y = 0; y < dst_h; y++) {
        const struct filter_weight_entry *v_ent = &v_tbl->entries[y];
        int v_min = v_ent->min_idx;
        int v_cnt = v_ent->count;
        const float *v_w = v_tbl->weights_pool + v_ent->weight_offset;

        for (int k = 0; k < v_cnt; k++) {
            int sy = v_min + k;
            if (sy < 0) sy = 0;
            else if (sy >= region_h) sy = region_h - 1;

            int slot = sy & ring_mask;
            if (ring_tag[slot] != sy) {
                int actual_y = src_y + sy;
                const uint8_t * __restrict src_row = src->data + (size_t)actual_y * (size_t)src->stride + (size_t)src_x * 3;
                float * __restrict dst_intermediate = ring_buf + (size_t)slot * row_floats;

                for (int x = 0; x < dst_w; x++) {
                    const struct filter_weight_entry *h_ent = &h_tbl->entries[x];
                    int h_min = h_ent->min_idx;
                    int h_cnt = h_ent->count;
                    const float * __restrict hw = h_tbl->weights_pool + h_ent->weight_offset;

                    float r_sum = 0.0f, g_sum = 0.0f, b_sum = 0.0f;
                    if (h_min >= 0 && h_min + h_cnt <= region_w) {
                        const uint8_t * __restrict sp = src_row + (size_t)h_min * 3;
                        #pragma GCC unroll 8
                        for (int hk = 0; hk < h_cnt; hk++) {
                            float w = hw[hk];
                            r_sum += sp[hk * 3 + 0] * w;
                            g_sum += sp[hk * 3 + 1] * w;
                            b_sum += sp[hk * 3 + 2] * w;
                        }
                    } else {
                        for (int hk = 0; hk < h_cnt; hk++) {
                            int sx = h_min + hk;
                            if (sx < 0) sx = 0;
                            else if (sx >= region_w) sx = region_w - 1;
                            float w = hw[hk];
                            r_sum += src_row[sx * 3 + 0] * w;
                            g_sum += src_row[sx * 3 + 1] * w;
                            b_sum += src_row[sx * 3 + 2] * w;
                        }
                    }
                    dst_intermediate[x * 3 + 0] = r_sum;
                    dst_intermediate[x * 3 + 1] = g_sum;
                    dst_intermediate[x * 3 + 2] = b_sum;
                }
                ring_tag[slot] = sy;
            }
        }

        uint8_t * __restrict dst_row = dst->data + (size_t)y * (size_t)dst->stride;
        float * __restrict accum = row_accum;

        int sy0 = v_min;
        if (sy0 < 0) sy0 = 0;
        else if (sy0 >= region_h) sy0 = region_h - 1;
        float w0 = v_w[0];
        const float * __restrict src_row0 = ring_buf + (size_t)(sy0 & ring_mask) * row_floats;

        #pragma GCC ivdep
        for (size_t i = 0; i < row_floats; i++) {
            accum[i] = src_row0[i] * w0;
        }

        for (int k = 1; k < v_cnt; k++) {
            int sy = v_min + k;
            if (sy < 0) sy = 0;
            else if (sy >= region_h) sy = region_h - 1;

            float w = v_w[k];
            const float * __restrict src_row = ring_buf + (size_t)(sy & ring_mask) * row_floats;

            #pragma GCC ivdep
            for (size_t i = 0; i < row_floats; i++) {
                accum[i] += src_row[i] * w;
            }
        }

        #pragma GCC ivdep
        for (size_t i = 0; i < row_floats; i++) {
            int val = (int)(accum[i] + 0.5f);
            dst_row[i] = (uint8_t)(val < 0 ? 0 : (val > 255 ? 255 : val));
        }
    }

    free(row_accum);
    free(ring_tag);
    free(ring_buf);
    free_filter_table(h_tbl);
    free_filter_table(v_tbl);
    return dst;
}

static struct image_buffer *resample_separable(
    const struct image_buffer *src,
    int dst_w,
    int dst_h,
    filter_fn fn,
    double base_radius
) {
    if (!src) return NULL;
    return resample_separable_subregion(src, 0, 0, src->width, src->height, dst_w, dst_h, fn, base_radius);
}

struct image_buffer *image_resample_bilinear(const struct image_buffer *src, int dst_w, int dst_h) {
    return resample_separable(src, dst_w, dst_h, filter_triangle, 1.0);
}

struct image_buffer *image_resample_lanczos(const struct image_buffer *src, int dst_w, int dst_h) {
    return resample_separable(src, dst_w, dst_h, filter_lanczos3, 3.0);
}

/* A running accumulator makes each edge-clamped box pass independent of radius. */
static void box_blur_1d(
    const uint8_t *src,
    uint8_t *dst,
    int width,
    int height,
    int stride,
    int radius,
    bool horizontal
) {
    if (radius <= 0) {
        if (src != dst) memcpy(dst, src, (size_t)height * (size_t)stride);
        return;
    }

    int window_size = 2 * radius + 1;
    float inv_window = 1.0f / (float)window_size;

    if (horizontal) {
        for (int y = 0; y < height; y++) {
            const uint8_t *s_row = src + (size_t)y * (size_t)stride;
            uint8_t *d_row = dst + (size_t)y * (size_t)stride;

            int sum_r = 0, sum_g = 0, sum_b = 0;
            sum_r += s_row[0] * (radius + 1);
            sum_g += s_row[1] * (radius + 1);
            sum_b += s_row[2] * (radius + 1);

            for (int x = 1; x <= radius; x++) {
                int px = x < width ? x : width - 1;
                sum_r += s_row[px * 3 + 0];
                sum_g += s_row[px * 3 + 1];
                sum_b += s_row[px * 3 + 2];
            }

            for (int x = 0; x < width; x++) {
                d_row[x * 3 + 0] = (uint8_t)((float)sum_r * inv_window + 0.5f);
                d_row[x * 3 + 1] = (uint8_t)((float)sum_g * inv_window + 0.5f);
                d_row[x * 3 + 2] = (uint8_t)((float)sum_b * inv_window + 0.5f);

                int right_px = x + radius + 1;
                if (right_px >= width) right_px = width - 1;

                int left_px = x - radius;
                if (left_px < 0) left_px = 0;

                sum_r += s_row[right_px * 3 + 0] - s_row[left_px * 3 + 0];
                sum_g += s_row[right_px * 3 + 1] - s_row[left_px * 3 + 1];
                sum_b += s_row[right_px * 3 + 2] - s_row[left_px * 3 + 2];
            }
        }
    } else {
        for (int x = 0; x < width; x++) {
            int sum_r = 0, sum_g = 0, sum_b = 0;
            sum_r += src[x * 3 + 0] * (radius + 1);
            sum_g += src[x * 3 + 1] * (radius + 1);
            sum_b += src[x * 3 + 2] * (radius + 1);

            for (int y = 1; y <= radius; y++) {
                int py = y < height ? y : height - 1;
                sum_r += src[(size_t)py * (size_t)stride + x * 3 + 0];
                sum_g += src[(size_t)py * (size_t)stride + x * 3 + 1];
                sum_b += src[(size_t)py * (size_t)stride + x * 3 + 2];
            }

            for (int y = 0; y < height; y++) {
                dst[(size_t)y * (size_t)stride + x * 3 + 0] = (uint8_t)((float)sum_r * inv_window + 0.5f);
                dst[(size_t)y * (size_t)stride + x * 3 + 1] = (uint8_t)((float)sum_g * inv_window + 0.5f);
                dst[(size_t)y * (size_t)stride + x * 3 + 2] = (uint8_t)((float)sum_b * inv_window + 0.5f);

                int bottom_py = y + radius + 1;
                if (bottom_py >= height) bottom_py = height - 1;

                int top_py = y - radius;
                if (top_py < 0) top_py = 0;

                sum_r += src[(size_t)bottom_py * (size_t)stride + x * 3 + 0] - src[(size_t)top_py * (size_t)stride + x * 3 + 0];
                sum_g += src[(size_t)bottom_py * (size_t)stride + x * 3 + 1] - src[(size_t)top_py * (size_t)stride + x * 3 + 1];
                sum_b += src[(size_t)bottom_py * (size_t)stride + x * 3 + 2] - src[(size_t)top_py * (size_t)stride + x * 3 + 2];
            }
        }
    }
}

/* Three extended-box passes approximate the requested Gaussian radius. */
void image_gaussian_blur(struct image_buffer *img, float radius) {
    if (!img || img->channels != 3 || radius <= 0.0f) return;

    float w_ideal = sqrtf((12.0f * radius * radius / 3.0f) + 1.0f);
    int wl = (int)floorf(w_ideal);
    if (wl % 2 == 0) wl--;
    int wu = wl + 2;

    float m_ideal = (12.0f * radius * radius - 3.0f * (float)wl * (float)wl - 12.0f * (float)wl - 9.0f) / (-4.0f * (float)wl - 4.0f);
    int m = (int)roundf(m_ideal);

    int boxes[3];
    for (int i = 0; i < 3; i++) {
        int w = (i < m) ? wl : wu;
        boxes[i] = (w - 1) / 2;
    }

    uint8_t *tmp = malloc((size_t)img->stride * (size_t)img->height);
    if (!tmp) return;

    for (int i = 0; i < 3; i++) {
        int r = boxes[i];
        if (r > 0) {
            box_blur_1d(img->data, tmp, img->width, img->height, img->stride, r, true);
            box_blur_1d(tmp, img->data, img->width, img->height, img->stride, r, false);
        }
    }
    free(tmp);
}

void image_enhance_brightness(struct image_buffer *img, float factor) {
    if (!img || img->channels != 3) return;
    uint8_t lut[256];
    for (int i = 0; i < 256; i++) {
        lut[i] = clamp_u8((int)(i * factor));
    }
    size_t total_bytes = (size_t)img->stride * (size_t)img->height;
    uint8_t *p = img->data;

    #pragma GCC ivdep
    for (size_t i = 0; i < total_bytes; i++) {
        p[i] = lut[p[i]];
    }
}

struct mask_buffer *image_create_feather_mask(int length, int feather, bool horizontal) {
    if (length <= 0 || feather <= 0) return NULL;
    struct mask_buffer *mask = malloc(sizeof(struct mask_buffer));
    if (!mask) return NULL;

    mask->length = length;
    mask->horizontal = horizontal;
    mask->data = malloc((size_t)length);
    if (!mask->data) {
        free(mask);
        return NULL;
    }

    int denom = (feather - 1 > 0) ? (feather - 1) : 1;
    for (int i = 0; i < feather && i < length; i++) {
        mask->data[i] = (uint8_t)((255 * i) / denom);
    }

    int mid_start = feather;
    int mid_end = length - feather;
    if (mid_end > mid_start) {
        memset(mask->data + mid_start, 0xFF, (size_t)(mid_end - mid_start));
    }

    for (int i = 0; i < feather; i++) {
        int idx = length - feather + i;
        if (idx >= 0 && idx < length) {
            mask->data[idx] = (uint8_t)((255 * (feather - 1 - i)) / denom);
        }
    }

    return mask;
}

void mask_buffer_free(struct mask_buffer *mask) {
    if (!mask) return;
    free(mask->data);
    free(mask);
}

/*
 * Composite foreground over backdrop with optional 1D edge feather mask.
 *
 * Blend arithmetic (t * 257 + 128) >> 16 approximates Pillow paste-with-mask
 * and is locked against a checksum in tests/art/test_pipeline.c.
 */
void image_composite_over(
    struct image_buffer *backdrop,
    const struct image_buffer *foreground,
    int pos_x,
    int pos_y,
    const struct mask_buffer *mask
) {
    if (!backdrop || !foreground || backdrop->channels != 3 || foreground->channels != 3) {
        return;
    }

    for (int fy = 0; fy < foreground->height; fy++) {
        int by = pos_y + fy;
        if (by < 0 || by >= backdrop->height) continue;

        const uint8_t *fg_row = foreground->data + (size_t)fy * (size_t)foreground->stride;
        uint8_t *bg_row = backdrop->data + (size_t)by * (size_t)backdrop->stride;

        int fx0 = pos_x < 0 ? -pos_x : 0;
        int render_w = foreground->width - fx0;
        if (pos_x + foreground->width > backdrop->width) {
            render_w = backdrop->width - pos_x - fx0;
        }
        if (render_w <= 0) continue;

        uint8_t *b = bg_row + (size_t)(pos_x + fx0) * 3;
        const uint8_t *f = fg_row + (size_t)fx0 * 3;

        if (!mask) {
            memcpy(b, f, (size_t)render_w * 3);
            continue;
        }

        if (!mask->horizontal) {
            uint32_t row_alpha = (fy < mask->length) ? mask->data[fy] : 255;
            if (row_alpha == 0) continue;
            if (row_alpha == 255) {
                memcpy(b, f, (size_t)render_w * 3);
                continue;
            }

            uint32_t inv_a = 255 - row_alpha;
            int total_chans = render_w * 3;
            #pragma GCC ivdep
            for (int i = 0; i < total_chans; i++) {
                uint32_t t = row_alpha * (uint32_t)f[i] + inv_a * (uint32_t)b[i] + 128;
                b[i] = (uint8_t)((t * 257 + 128) >> 16);
            }
            continue;
        }

        for (int fx = fx0; fx < fx0 + render_w; fx++) {
            uint32_t alpha = (fx < mask->length) ? mask->data[fx] : 255;
            if (alpha == 0) continue;

            const uint8_t *fp = fg_row + (size_t)fx * 3;
            uint8_t *bp = bg_row + (size_t)(pos_x + fx) * 3;

            if (alpha == 255) {
                bp[0] = fp[0];
                bp[1] = fp[1];
                bp[2] = fp[2];
            } else {
                uint32_t inv_a = 255 - alpha;
                uint32_t t0 = alpha * (uint32_t)fp[0] + inv_a * (uint32_t)bp[0] + 128;
                uint32_t t1 = alpha * (uint32_t)fp[1] + inv_a * (uint32_t)bp[1] + 128;
                uint32_t t2 = alpha * (uint32_t)fp[2] + inv_a * (uint32_t)bp[2] + 128;
                bp[0] = (uint8_t)((t0 * 257 + 128) >> 16);
                bp[1] = (uint8_t)((t1 * 257 + 128) >> 16);
                bp[2] = (uint8_t)((t2 * 257 + 128) >> 16);
            }
        }
    }
}

static bool write_all(int fd, const void *buf, size_t count) {
    const uint8_t *p = (const uint8_t *)buf;
    size_t written = 0;
    while (written < count) {
        ssize_t nw = write(fd, p + written, count - written);
        if (nw < 0) {
            if (errno == EINTR) continue;
            return false;
        }
        if (nw == 0) return false;
        written += (size_t)nw;
    }
    return true;
}

int image_write_argb(const struct image_buffer *img, const char *target_path) {
    if (!img || !target_path || img->channels != 3) return -1;

    size_t tmp_len = strlen(target_path) + 5;
    char *tmp_path = malloc(tmp_len);
    if (!tmp_path) return -1;
    snprintf(tmp_path, tmp_len, "%s.tmp", target_path);

    int fd = open(tmp_path, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) {
        free(tmp_path);
        return -1;
    }

    size_t chunk_bytes = 65536;
    uint8_t *chunk = malloc(chunk_bytes);
    if (!chunk) {
        close(fd);
        unlink(tmp_path);
        free(tmp_path);
        return -1;
    }

    size_t chunk_pos = 0;
    for (int y = 0; y < img->height; y++) {
        const uint8_t *src_p = img->data + (size_t)y * (size_t)img->stride;
        int x = 0;
        while (x < img->width) {
            size_t rem_pixels = (chunk_bytes - chunk_pos) / 4;
            int n = img->width - x;
            if ((size_t)n > rem_pixels) n = (int)rem_pixels;

            uint8_t *dst_p = chunk + chunk_pos;
            #pragma GCC ivdep
            for (int i = 0; i < n; i++) {
                dst_p[i * 4 + 0] = src_p[(x + i) * 3 + 2]; /* Blue */
                dst_p[i * 4 + 1] = src_p[(x + i) * 3 + 1]; /* Green */
                dst_p[i * 4 + 2] = src_p[(x + i) * 3 + 0]; /* Red */
                dst_p[i * 4 + 3] = 255;                    /* Alpha */
            }

            chunk_pos += (size_t)n * 4;
            x += n;

            if (chunk_pos >= chunk_bytes) {
                if (!write_all(fd, chunk, chunk_pos)) {
                    free(chunk);
                    close(fd);
                    unlink(tmp_path);
                    free(tmp_path);
                    return -1;
                }
                chunk_pos = 0;
            }
        }
    }

    if (chunk_pos > 0) {
        if (!write_all(fd, chunk, chunk_pos)) {
            free(chunk);
            close(fd);
            unlink(tmp_path);
            free(tmp_path);
            return -1;
        }
    }

    free(chunk);
    close(fd);

    if (rename(tmp_path, target_path) != 0) {
        unlink(tmp_path);
        free(tmp_path);
        return -1;
    }

    free(tmp_path);
    return 0;
}

int render_artwork_pipeline(
    const char *input_path,
    const char *output_path,
    int target_width,
    int target_height,
    int dim_millis
) {
    if (!input_path || !output_path || target_width <= 0 || target_height <= 0) {
        return -1;
    }
    if ((uint64_t)target_width * (uint64_t)target_height > MAX_TOTAL_PIXELS) {
        return -1;
    }

    struct image_buffer *source = image_load(input_path, 64 * 1024 * 1024);
    if (!source) return -1;

    int clamped_dim = dim_millis < 0 ? 0 : (dim_millis > 1000 ? 1000 : dim_millis);
    double brightness = 1.0 - (double)clamped_dim / 1000.0;

    int src_w = source->width;
    int src_h = source->height;

    double target_ar = (double)target_width / (double)target_height;
    double src_ar = (double)src_w / (double)src_h;

    int crop_x = 0, crop_y = 0, crop_w = src_w, crop_h = src_h;
    if (src_ar > target_ar) {
        crop_w = (int)round((double)src_h * target_ar);
        if (crop_w > src_w) crop_w = src_w;
        if (crop_w < 1) crop_w = 1;
        crop_x = (src_w - crop_w) / 2;
        if (crop_x < 0) crop_x = 0;
    } else {
        crop_h = (int)round((double)src_w / target_ar);
        if (crop_h > src_h) crop_h = src_h;
        if (crop_h < 1) crop_h = 1;
        crop_y = (src_h - crop_h) / 2;
        if (crop_y < 0) crop_y = 0;
    }

    int downscale = 8;
    int low_w = target_width / downscale;
    if (low_w < 1) low_w = 1;
    int low_h = target_height / downscale;
    if (low_h < 1) low_h = 1;

    struct image_buffer *backdrop_low = resample_separable_subregion(
        source, crop_x, crop_y, crop_w, crop_h, low_w, low_h, filter_triangle, 1.0
    );
    if (!backdrop_low) {
        image_buffer_free(source);
        return -1;
    }

    int blur_radius = (target_width / 60) / downscale;
    if (blur_radius < 2) blur_radius = 2;
    image_gaussian_blur(backdrop_low, (float)blur_radius);
    image_enhance_brightness(backdrop_low, (float)(0.45 * brightness));

    struct image_buffer *backdrop = image_resample_bilinear(backdrop_low, target_width, target_height);
    image_buffer_free(backdrop_low);
    if (!backdrop) {
        image_buffer_free(source);
        return -1;
    }

    image_enhance_brightness(source, (float)(0.75 * brightness));

    double scale_w = (double)target_width / (double)src_w;
    double scale_h = (double)target_height / (double)src_h;
    double fg_scale = scale_w < scale_h ? scale_w : scale_h;

    int fg_w = (int)round((double)src_w * fg_scale);
    if (fg_w > target_width) fg_w = target_width;
    if (fg_w < 1) fg_w = 1;
    int fg_h = (int)round((double)src_h * fg_scale);
    if (fg_h > target_height) fg_h = target_height;
    if (fg_h < 1) fg_h = 1;

    struct image_buffer *foreground = image_resample_lanczos(source, fg_w, fg_h);
    if (!foreground) {
        image_buffer_free(backdrop);
        image_buffer_free(source);
        return -1;
    }

    struct mask_buffer *mask = NULL;
    if (fg_w < target_width) {
        int feather = (target_width - fg_w) / 2 + fg_w / 8;
        if (feather > fg_w / 4) feather = fg_w / 4;
        if (feather < 1) feather = 1;
        mask = image_create_feather_mask(fg_w, feather, true);
        if (!mask) {
            image_buffer_free(foreground);
            image_buffer_free(backdrop);
            image_buffer_free(source);
            return -1;
        }
    } else if (fg_h < target_height) {
        int feather = (target_height - fg_h) / 2 + fg_h / 8;
        if (feather > fg_h / 4) feather = fg_h / 4;
        if (feather < 1) feather = 1;
        mask = image_create_feather_mask(fg_h, feather, false);
        if (!mask) {
            image_buffer_free(foreground);
            image_buffer_free(backdrop);
            image_buffer_free(source);
            return -1;
        }
    }

    int pos_x = (target_width - fg_w) / 2;
    int pos_y = (target_height - fg_h) / 2;
    image_composite_over(backdrop, foreground, pos_x, pos_y, mask);

    image_buffer_free(foreground);
    if (mask) mask_buffer_free(mask);

    int ret = image_write_argb(backdrop, output_path);
    image_buffer_free(backdrop);
    image_buffer_free(source);

    return ret;
}
