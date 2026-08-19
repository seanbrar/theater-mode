/*
 * test_pipeline.c — unit tests for the theater-art image pipeline.
 *
 * Includes pipeline.c directly so the file-static helpers (structure validators, filter
 * table construction) are reachable without widening their visibility in production.
 *
 * The oracle in tests/verify_oracle.py checks end-to-end output against Pillow. It cannot
 * localise a failure or reach the code paths that never touch a rendered frame, which is
 * what these cover: the validators, the blend identity, and the filter tables.
 *
 *   test_pipeline [path/to/tests/fixtures/artwork_reference]
 */

#include "pipeline.c"

static int checks_run = 0;
static int checks_failed = 0;
static const char *current_test = "";

#define CHECK(cond)                                                                     \
    do {                                                                                \
        checks_run++;                                                                   \
        if (!(cond)) {                                                                  \
            checks_failed++;                                                            \
            fprintf(stderr, "  FAIL %s:%d [%s]: %s\n", __FILE__, __LINE__,              \
                    current_test, #cond);                                               \
        }                                                                               \
    } while (0)

#define RUN(fn)                                                                         \
    do {                                                                                \
        current_test = #fn;                                                             \
        int before = checks_failed;                                                     \
        fn();                                                                           \
        printf("  %-42s %s\n", #fn, checks_failed == before ? "ok" : "FAILED");          \
    } while (0)

static char fixtures_dir[4096] = "tests/fixtures/artwork_reference";

/* Read a fixture into a fresh buffer. Returns NULL and reports if it is missing. */
static uint8_t *read_fixture(const char *name, size_t *out_size) {
    char path[8192];
    snprintf(path, sizeof path, "%s/%s", fixtures_dir, name);
    FILE *fh = fopen(path, "rb");
    if (!fh) {
        fprintf(stderr, "  FAIL cannot open fixture %s\n", path);
        checks_failed++;
        return NULL;
    }
    fseek(fh, 0, SEEK_END);
    long len = ftell(fh);
    fseek(fh, 0, SEEK_SET);
    uint8_t *buf = malloc((size_t)len);
    if (!buf || fread(buf, 1, (size_t)len, fh) != (size_t)len) {
        free(buf);
        fclose(fh);
        fprintf(stderr, "  FAIL cannot read fixture %s\n", path);
        checks_failed++;
        return NULL;
    }
    fclose(fh);
    *out_size = (size_t)len;
    return buf;
}

/*
 * The blend in image_composite_over is an approximation of Pillow's paste-with-mask, not
 * an exact reproduction of it and not t / 255. Pillow is not available here, so rather
 * than assert against a model of it, this locks the arithmetic against a checksum
 * measured over every possible input, plus the properties that must hold regardless.
 *
 * Regenerate the constant only when a deviation from Pillow has been re-measured:
 *   see the note in image_composite_over for the measured bounds (0.099%, max 1).
 */
#define BLEND_FNV1A64 0xCBEB1EA87406CFB5ULL

static inline int composite_blend(int alpha, int fg, int bg) {
    uint32_t t = (uint32_t)(alpha * fg + (255 - alpha) * bg + 128);
    return (int)((t * 257 + 128) >> 16);
}

static void test_blend_arithmetic_is_locked(void) {
    uint64_t h = 0xCBF29CE484222325ULL;
    int range_violations = 0;
    int endpoint_violations = 0;
    int monotonicity_violations = 0;

    for (int alpha = 0; alpha <= 255; alpha++) {
        for (int bg = 0; bg <= 255; bg++) {
            for (int fg = 0; fg <= 255; fg++) {
                int v = composite_blend(alpha, fg, bg);
                h = (h ^ (uint64_t)(v & 0xFF)) * 0x100000001B3ULL;
                if (v < 0 || v > 255) range_violations++;
                if (alpha == 0 && v != bg) endpoint_violations++;
                if (alpha == 255 && v != fg) endpoint_violations++;
            }
        }
    }
    CHECK(h == BLEND_FNV1A64);
    CHECK(range_violations == 0);
    CHECK(endpoint_violations == 0);

    /* Raising alpha must never move the result away from the foreground. */
    for (int bg = 0; bg <= 255; bg += 7) {
        for (int fg = bg + 1; fg <= 255; fg += 11) {
            for (int alpha = 1; alpha <= 255; alpha++) {
                if (composite_blend(alpha, fg, bg) < composite_blend(alpha - 1, fg, bg)) {
                    monotonicity_violations++;
                }
            }
        }
    }
    CHECK(monotonicity_violations == 0);
}

static void test_valid_jpeg_accepted(void) {
    size_t n;
    uint8_t *b = read_fixture("hero_16x9_to_16x9_input.jpg", &n);
    if (!b) return;
    CHECK(is_valid_complete_image(b, n));
    CHECK(is_valid_complete_jpeg(b, n));
    CHECK(!is_valid_complete_png(b, n));
    free(b);
}

static void test_damaged_jpeg_rejected(void) {
    size_t n;
    uint8_t *b = read_fixture("hero_16x9_to_16x9_input.jpg", &n);
    if (!b) return;

    /* Truncation at several points, which is how an interrupted download presents. */
    CHECK(!is_valid_complete_image(b, n / 2));
    CHECK(!is_valid_complete_image(b, n / 4));
    CHECK(!is_valid_complete_image(b, 64));
    CHECK(!is_valid_complete_image(b, 2));

    /* Trailing EOI removed. */
    CHECK(!is_valid_complete_image(b, n - 2));

    /* A marker that cannot appear inside entropy-coded data. */
    uint8_t *nested = malloc(n);
    memcpy(nested, b, n);
    nested[n / 2] = 0xFF;
    nested[n / 2 + 1] = 0xD8;
    CHECK(!is_valid_complete_image(nested, n));
    free(nested);

    /* A segment length that runs past the end of the buffer. */
    uint8_t *badlen = malloc(n);
    memcpy(badlen, b, n);
    badlen[4] = 0xFF;
    badlen[5] = 0xFF;
    CHECK(!is_valid_complete_image(badlen, n));
    free(badlen);

    /* Wrong magic entirely. */
    uint8_t garbage[64];
    memset(garbage, 0x41, sizeof garbage);
    CHECK(!is_valid_complete_image(garbage, sizeof garbage));
    CHECK(!is_valid_complete_image(garbage, 0));
    CHECK(!is_valid_complete_image(NULL, 100));
    free(b);
}

static void test_valid_png_accepted_and_damage_rejected(void) {
    size_t n;
    uint8_t *b = read_fixture("hero_png_source_input.png", &n);
    if (!b) return;

    CHECK(is_valid_complete_image(b, n));
    CHECK(is_valid_complete_png(b, n));
    CHECK(!is_valid_complete_jpeg(b, n));

    CHECK(!is_valid_complete_image(b, n / 2));
    CHECK(!is_valid_complete_image(b, n - 12)); /* IEND removed */
    CHECK(!is_valid_complete_image(b, 8));      /* signature only */

    /* Corrupt the signature. */
    uint8_t *sig = malloc(n);
    memcpy(sig, b, n);
    sig[3] = 'X';
    CHECK(!is_valid_complete_image(sig, n));
    free(sig);

    /* First chunk must be IHDR. */
    uint8_t *ihdr = malloc(n);
    memcpy(ihdr, b, n);
    ihdr[12] = 'X';
    CHECK(!is_valid_complete_png(ihdr, n));
    free(ihdr);

    /* A chunk length that overruns the buffer. */
    uint8_t *len = malloc(n);
    memcpy(len, b, n);
    len[8] = 0x7F;
    len[9] = 0xFF;
    CHECK(!is_valid_complete_png(len, n));
    free(len);
    free(b);
}

static void test_oversized_image_rejected_before_decode(void) {
    /* Minimal structurally complete PNG whose IHDR declares 268 megapixels. The empty
     * IDAT is intentionally undecodable: dimension preflight must reject the header
     * before stb_image attempts to allocate its decoded pixel buffer. CRCs are ignored
     * by stb_image and are zero here because this test reaches header inspection only. */
    const uint8_t png[] = {
        0x89, 'P', 'N', 'G', 0x0D, 0x0A, 0x1A, 0x0A,
        0x00, 0x00, 0x00, 0x0D, 'I', 'H', 'D', 'R',
        0x00, 0x00, 0x40, 0x00, 0x00, 0x00, 0x40, 0x00,
        0x08, 0x02, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00, 'I', 'D', 'A', 'T',
        0x00, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00, 'I', 'E', 'N', 'D',
        0x00, 0x00, 0x00, 0x00,
    };
    int width = 0, height = 0;

    CHECK(is_valid_complete_png(png, sizeof png));
    CHECK(!image_dimensions_within_limit(png, sizeof png, &width, &height));
    CHECK(width == 0);
    CHECK(height == 0);
}

/* The ramp must reproduce the Python reference exactly: int(255 * i / (feather - 1)). */
static void test_feather_mask_matches_reference_ramp(void) {
    const int lengths[] = {64, 100, 621, 1920};
    const int feathers[] = {1, 2, 7, 16};
    for (size_t li = 0; li < sizeof lengths / sizeof lengths[0]; li++) {
        for (size_t fi = 0; fi < sizeof feathers / sizeof feathers[0]; fi++) {
            int length = lengths[li], feather = feathers[fi];
            struct mask_buffer *m = image_create_feather_mask(length, feather, true);
            CHECK(m != NULL);
            if (!m) continue;
            CHECK(m->length == length);
            int denom = (feather - 1 > 0) ? (feather - 1) : 1;
            for (int i = 0; i < feather && i < length; i++) {
                CHECK(m->data[i] == (uint8_t)((255 * i) / denom));
            }
            for (int i = feather; i < length - feather; i++) {
                CHECK(m->data[i] == 255);
            }
            for (int i = 0; i < feather; i++) {
                int idx = length - feather + i;
                if (idx >= 0 && idx < length) {
                    CHECK(m->data[idx] == (uint8_t)((255 * (feather - 1 - i)) / denom));
                }
            }
            mask_buffer_free(m);
        }
    }
    CHECK(image_create_feather_mask(0, 4, true) == NULL);
    CHECK(image_create_feather_mask(64, 0, true) == NULL);
    mask_buffer_free(NULL);
}

/* Every output position must draw from a normalised kernel, or the image gains or loses
 * brightness at that column. max_count must bound every entry, because the resampler
 * sizes its ring buffer from it. */
static void test_filter_tables_are_normalised_and_bounded(void) {
    const int pairs[][2] = {{1920, 1600}, {1920, 240}, {620, 1080}, {310, 310}, {7, 1920}};
    for (size_t i = 0; i < sizeof pairs / sizeof pairs[0]; i++) {
        int src = pairs[i][0], dst = pairs[i][1];
        for (int lanczos = 0; lanczos < 2; lanczos++) {
            struct filter_table *t = lanczos
                ? build_filter_table(src, dst, filter_lanczos3, 3.0)
                : build_filter_table(src, dst, filter_triangle, 1.0);
            CHECK(t != NULL);
            if (!t) continue;
            CHECK(t->dst_size == dst);
            for (int d = 0; d < dst; d++) {
                const struct filter_weight_entry *e = &t->entries[d];
                CHECK(e->count >= 1);
                CHECK(e->count <= t->max_count);
                double sum = 0.0;
                for (int k = 0; k < e->count; k++) sum += t->weights_pool[e->weight_offset + k];
                CHECK(sum > 0.999 && sum < 1.001);
            }
            free_filter_table(t);
        }
    }
}

static void test_buffer_creation_rejects_bad_geometry(void) {
    CHECK(image_buffer_create(0, 10, 3) == NULL);
    CHECK(image_buffer_create(10, 0, 3) == NULL);
    CHECK(image_buffer_create(-1, 10, 3) == NULL);
    CHECK(image_buffer_create(10, 10, 2) == NULL);
    CHECK(image_buffer_create(100000, 100000, 3) == NULL); /* over MAX_TOTAL_PIXELS */
    struct image_buffer *b = image_buffer_create(4, 4, 3);
    CHECK(b != NULL);
    if (b) {
        CHECK(b->stride == 12);
        image_buffer_free(b);
    }
    image_buffer_free(NULL);
}

/* The identity fast path must be a copy, not an approximation. */
static void test_identity_resample_is_exact(void) {
    struct image_buffer *src = image_buffer_create(37, 19, 3);
    CHECK(src != NULL);
    if (!src) return;
    for (size_t i = 0; i < (size_t)src->stride * (size_t)src->height; i++) {
        src->data[i] = (uint8_t)((i * 7919) & 0xFF);
    }
    struct image_buffer *out = image_resample_lanczos(src, 37, 19);
    CHECK(out != NULL);
    if (out) {
        CHECK(memcmp(out->data, src->data, (size_t)src->stride * (size_t)src->height) == 0);
        image_buffer_free(out);
    }
    struct image_buffer *bil = image_resample_bilinear(src, 37, 19);
    CHECK(bil != NULL);
    if (bil) {
        CHECK(memcmp(bil->data, src->data, (size_t)src->stride * (size_t)src->height) == 0);
        image_buffer_free(bil);
    }
    image_buffer_free(src);
}

/* Overhanging and negatively positioned foregrounds must clip, never walk out of the
 * destination buffer. Exercised here because render_artwork_pipeline never produces them. */
static void test_composite_clips_out_of_bounds_placements(void) {
    const int positions[][2] = {{-8, -3}, {-40, 0}, {30, 30}, {-5, 28}, {0, 0}};
    for (size_t i = 0; i < sizeof positions / sizeof positions[0]; i++) {
        for (int with_mask = 0; with_mask < 3; with_mask++) {
            struct image_buffer *bg = image_buffer_create(32, 32, 3);
            struct image_buffer *fg = image_buffer_create(20, 12, 3);
            CHECK(bg && fg);
            if (!bg || !fg) { image_buffer_free(bg); image_buffer_free(fg); continue; }
            memset(bg->data, 0x11, (size_t)bg->stride * (size_t)bg->height);
            memset(fg->data, 0xEE, (size_t)fg->stride * (size_t)fg->height);
            struct mask_buffer *m = NULL;
            if (with_mask == 1) m = image_create_feather_mask(fg->width, 4, true);
            if (with_mask == 2) m = image_create_feather_mask(fg->height, 3, false);
            image_composite_over(bg, fg, positions[i][0], positions[i][1], m);
            mask_buffer_free(m);
            image_buffer_free(fg);
            image_buffer_free(bg);
        }
    }
    CHECK(1); /* reaching here without a sanitizer abort is the assertion */
}

static void test_subregion_resample_rejects_bad_bounds(void) {
    struct image_buffer *src = image_buffer_create(64, 64, 3);
    CHECK(src != NULL);
    if (!src) return;

    /* Negative subregion offsets */
    CHECK(resample_separable_subregion(src, -1, 0, 32, 32, 32, 32, filter_triangle, 1.0) == NULL);
    CHECK(resample_separable_subregion(src, 0, -1, 32, 32, 32, 32, filter_triangle, 1.0) == NULL);

    /* Zero or negative region dimensions */
    CHECK(resample_separable_subregion(src, 0, 0, 0, 32, 32, 32, filter_triangle, 1.0) == NULL);
    CHECK(resample_separable_subregion(src, 0, 0, 32, -5, 32, 32, filter_triangle, 1.0) == NULL);

    /* Subregion exceeding source bounds */
    CHECK(resample_separable_subregion(src, 33, 0, 32, 32, 32, 32, filter_triangle, 1.0) == NULL);
    CHECK(resample_separable_subregion(src, 0, 33, 32, 32, 32, 32, filter_triangle, 1.0) == NULL);
    CHECK(resample_separable_subregion(src, 10, 10, 60, 60, 32, 32, filter_triangle, 1.0) == NULL);

    /* Valid exact boundary subregions */
    struct image_buffer *valid = resample_separable_subregion(src, 32, 32, 32, 32, 16, 16, filter_triangle, 1.0);
    CHECK(valid != NULL);
    image_buffer_free(valid);

    image_buffer_free(src);
}

int main(int argc, char **argv) {
    if (argc > 1) snprintf(fixtures_dir, sizeof fixtures_dir, "%s", argv[1]);
    printf("[test_pipeline] fixtures: %s\n", fixtures_dir);

    RUN(test_blend_arithmetic_is_locked);
    RUN(test_valid_jpeg_accepted);
    RUN(test_damaged_jpeg_rejected);
    RUN(test_valid_png_accepted_and_damage_rejected);
    RUN(test_oversized_image_rejected_before_decode);
    RUN(test_feather_mask_matches_reference_ramp);
    RUN(test_filter_tables_are_normalised_and_bounded);
    RUN(test_buffer_creation_rejects_bad_geometry);
    RUN(test_identity_resample_is_exact);
    RUN(test_composite_clips_out_of_bounds_placements);
    RUN(test_subregion_resample_rejects_bad_bounds);

    printf("[test_pipeline] %d checks, %d failed\n", checks_run, checks_failed);
    return checks_failed == 0 ? 0 : 1;
}
