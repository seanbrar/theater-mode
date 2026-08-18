/*
 * theater-dimmer — Wayland layer-shell cinematic display dimmer.
 *
 * Draws layer-shell overlay surfaces over target outputs and animates alpha
 * transitions. Uses an empty input region so pointer and keyboard events pass
 * straight through. Surfaces display flat black or staged game artwork.
 *
 * Commands on stdin:
 *   ART <output> <width> <height> <path>   Stage artwork for an output
 *   ART <output>                           Clear staged artwork (revert to black)
 *   DIM <outputs_comma_separated> <target_alpha> <duration_sec> [easing]
 *   FADE_OUT <duration_sec> [easing]
 *   STATUS
 *   QUIT
 */

#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#include <wayland-client.h>

#include "alpha-modifier-v1-client-protocol.h"
#include "single-pixel-buffer-v1-client-protocol.h"
#include "viewporter-client-protocol.h"
#include "wlr-layer-shell-unstable-v1-client-protocol.h"

#define MAX_OUTPUTS 32
#define BUFFER_SIZE 4096

/* Maximum artwork dimension limit in pixels. */
#define MAX_ART_EDGE 16384

#define COMPOSITOR_VERSION 4
#define LAYER_SHELL_VERSION 4
#define OUTPUT_VERSION 4

/* Timeout to step animation if compositor stops delivering frame callbacks */
#define FRAME_STALL_SEC 0.1

enum easing_curve {
    EASING_SINE = 0,
    EASING_QUAD = 1,
    EASING_CUBIC = 2,
    EASING_LINEAR = 3,
};

struct dimmer_app;

/* State slot per output; reused on hotplug. */
struct output_state {
    struct dimmer_app *app;
    bool in_use;
    uint32_t global_id;
    struct wl_output *wl_output;
    char name[64];

    /* Staged artwork, or NULL for flat black. */
    struct wl_buffer *art;

    /* Overlay surface state */
    struct wl_surface *surface;
    struct zwlr_layer_surface_v1 *layer_surface;
    struct wp_viewport *viewport;
    struct wp_alpha_modifier_surface_v1 *alpha_surface;
    struct wl_callback *frame_cb;
    bool mapped;
    bool configured;
    double last_commit_sec;

    /* Animation */
    double start_alpha;
    double target_alpha;
    double current_alpha;
    double start_time_sec;
    double duration_sec;
    enum easing_curve easing;
    bool animating;
};

struct dimmer_app {
    struct wl_display *display;
    struct wl_registry *registry;
    struct wl_compositor *compositor;
    struct zwlr_layer_shell_v1 *layer_shell;
    struct wp_viewporter *viewporter;
    struct wp_single_pixel_buffer_manager_v1 *pixel_buffers;
    struct wp_alpha_modifier_v1 *alpha_modifier;
    struct wl_shm *shm;

    /* 1x1 opaque black single-pixel buffer shared across plain dim surfaces. */
    struct wl_buffer *black;

    struct output_state outputs[MAX_OUTPUTS];
    bool running;
};

static volatile sig_atomic_t g_signal_received = 0;

static void handle_signal(int sig) {
    (void)sig;
    g_signal_received = 1;
}

static double get_time_sec(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (double)ts.tv_sec + (double)ts.tv_nsec / 1e9;
}

static double apply_easing(double t, enum easing_curve curve) {
    if (t <= 0.0) return 0.0;
    if (t >= 1.0) return 1.0;

    switch (curve) {
        case EASING_QUAD:
            return (t < 0.5) ? (2.0 * t * t) : (1.0 - pow(-2.0 * t + 2.0, 2) / 2.0);
        case EASING_CUBIC:
            return (t < 0.5) ? (4.0 * t * t * t) : (1.0 - pow(-2.0 * t + 2.0, 3) / 2.0);
        case EASING_LINEAR:
            return t;
        case EASING_SINE:
        default:
            return (1.0 - cos(t * M_PI)) / 2.0;
    }
}

static enum easing_curve parse_easing(const char *str) {
    if (!str || !*str) return EASING_SINE;
    if (strcasecmp(str, "quad") == 0) return EASING_QUAD;
    if (strcasecmp(str, "cubic") == 0) return EASING_CUBIC;
    if (strcasecmp(str, "linear") == 0) return EASING_LINEAR;
    return EASING_SINE;
}

static const char *easing_to_string(enum easing_curve curve) {
    switch (curve) {
        case EASING_QUAD: return "quad";
        case EASING_CUBIC: return "cubic";
        case EASING_LINEAR: return "linear";
        case EASING_SINE:
        default: return "sine";
    }
}

/* Convert 0.0-1.0 alpha to wp_alpha_modifier uint32 multiplier. */
static uint32_t alpha_to_multiplier(double alpha) {
    if (alpha <= 0.0) return 0;
    if (alpha >= 1.0) return UINT32_MAX;
    return (uint32_t)(alpha * (double)UINT32_MAX);
}

/* Log protocol errors and check Wayland display connection health. */
static bool display_is_alive(struct dimmer_app *app) {
    int err = wl_display_get_error(app->display);
    if (err == 0) return true;

    if (err == EPROTO) {
        const struct wl_interface *interface = NULL;
        uint32_t id = 0;
        uint32_t code = wl_display_get_protocol_error(app->display, &interface, &id);
        fprintf(stderr, "theater-dimmer: protocol error %u on %s (object %u); exiting\n",
                code, interface ? interface->name : "unknown", id);
    } else {
        fprintf(stderr, "theater-dimmer: display connection failed: %s\n", strerror(err));
    }
    return false;
}

static struct output_state *find_output(struct dimmer_app *app, const char *name) {
    for (int i = 0; i < MAX_OUTPUTS; i++) {
        if (app->outputs[i].in_use && strcmp(app->outputs[i].name, name) == 0) {
            return &app->outputs[i];
        }
    }
    return NULL;
}

static void destroy_art(struct output_state *out) {
    if (out->art) {
        wl_buffer_destroy(out->art);
        out->art = NULL;
    }
}

/* Tear down overlay surfaces for an output while retaining staged artwork. */
static void destroy_overlay(struct output_state *out) {
    if (out->frame_cb) {
        wl_callback_destroy(out->frame_cb);
        out->frame_cb = NULL;
    }
    if (out->alpha_surface) {
        wp_alpha_modifier_surface_v1_destroy(out->alpha_surface);
        out->alpha_surface = NULL;
    }
    if (out->viewport) {
        wp_viewport_destroy(out->viewport);
        out->viewport = NULL;
    }
    if (out->layer_surface) {
        zwlr_layer_surface_v1_destroy(out->layer_surface);
        out->layer_surface = NULL;
    }
    if (out->surface) {
        wl_surface_destroy(out->surface);
        out->surface = NULL;
    }
    out->mapped = false;
    out->configured = false;
    out->animating = false;
    out->current_alpha = 0.0;
    out->last_commit_sec = 0.0;
}

static void frame_done(void *data, struct wl_callback *callback, uint32_t time);

static const struct wl_callback_listener frame_listener = {
    .done = frame_done,
};

static void commit_overlay(struct output_state *out, bool request_frame) {
    if (!out->surface || !out->configured) return;

    if (out->frame_cb) {
        wl_callback_destroy(out->frame_cb);
        out->frame_cb = NULL;
    }
    if (request_frame) {
        out->frame_cb = wl_surface_frame(out->surface);
        wl_callback_add_listener(out->frame_cb, &frame_listener, out);
    }

    if (out->alpha_surface) {
        wp_alpha_modifier_surface_v1_set_multiplier(out->alpha_surface,
                                                    alpha_to_multiplier(out->current_alpha));
    }
    wl_surface_attach(out->surface, out->art ? out->art : out->app->black, 0, 0);
    wl_surface_damage_buffer(out->surface, 0, 0, INT32_MAX, INT32_MAX);
    wl_surface_commit(out->surface);
    out->last_commit_sec = get_time_sec();
}

/* Advance one animation step and commit changes. */
static void tick_overlay(struct output_state *out) {
    if (!out->animating || !out->mapped) return;

    double elapsed = get_time_sec() - out->start_time_sec;
    double progress = (out->duration_sec > 0.0) ? (elapsed / out->duration_sec) : 1.0;

    if (progress >= 1.0) {
        out->animating = false;
        out->current_alpha = out->target_alpha;
    } else {
        double factor = apply_easing(progress, out->easing);
        out->current_alpha =
            out->start_alpha + (out->target_alpha - out->start_alpha) * factor;
    }

    /* Destroy overlay surface once fade-out completes. */
    if (!out->animating && out->current_alpha <= 0.001) {
        destroy_overlay(out);
        return;
    }

    commit_overlay(out, out->animating);
}

static void frame_done(void *data, struct wl_callback *callback, uint32_t time) {
    (void)time;
    struct output_state *out = (struct output_state *)data;
    if (callback) {
        wl_callback_destroy(callback);
    }
    out->frame_cb = NULL;
    tick_overlay(out);
}

/* Advance animations if frame callbacks are stalled (e.g. display blanking). */
static void advance_stalled_animations(struct dimmer_app *app) {
    double now = get_time_sec();
    for (int i = 0; i < MAX_OUTPUTS; i++) {
        struct output_state *out = &app->outputs[i];
        if (!out->in_use || !out->animating) continue;
        if (now - out->last_commit_sec >= FRAME_STALL_SEC) {
            tick_overlay(out);
        }
    }
}

/* Block indefinitely when idle; poll with timeout during active animations. */
static int poll_timeout_ms(const struct dimmer_app *app) {
    for (int i = 0; i < MAX_OUTPUTS; i++) {
        if (app->outputs[i].in_use && app->outputs[i].animating) {
            return (int)(FRAME_STALL_SEC * 1000.0);
        }
    }
    return -1;
}

static void layer_surface_configure(void *data, struct zwlr_layer_surface_v1 *layer_surface,
                                    uint32_t serial, uint32_t width, uint32_t height) {
    struct output_state *out = (struct output_state *)data;
    out->configured = true;

    zwlr_layer_surface_v1_ack_configure(layer_surface, serial);

    /* Scale viewport to match configured surface dimensions. */
    if (out->viewport && width > 0 && height > 0) {
        wp_viewport_set_destination(out->viewport, (int32_t)width, (int32_t)height);
    }

    commit_overlay(out, out->animating);
}

static void layer_surface_closed(void *data, struct zwlr_layer_surface_v1 *layer_surface) {
    (void)layer_surface;
    destroy_overlay((struct output_state *)data);
}

static const struct zwlr_layer_surface_v1_listener layer_surface_listener = {
    .configure = layer_surface_configure,
    .closed = layer_surface_closed,
};

static int map_overlay(struct dimmer_app *app, struct output_state *out) {
    if (out->mapped && out->surface) {
        return 0;
    }

    out->surface = wl_compositor_create_surface(app->compositor);
    if (!out->surface) return -1;

    out->layer_surface = zwlr_layer_shell_v1_get_layer_surface(
        app->layer_shell,
        out->surface,
        out->wl_output,
        ZWLR_LAYER_SHELL_V1_LAYER_OVERLAY,
        "theater-dimmer"
    );
    if (!out->layer_surface) {
        wl_surface_destroy(out->surface);
        out->surface = NULL;
        return -1;
    }

    zwlr_layer_surface_v1_set_anchor(
        out->layer_surface,
        ZWLR_LAYER_SURFACE_V1_ANCHOR_TOP |
        ZWLR_LAYER_SURFACE_V1_ANCHOR_BOTTOM |
        ZWLR_LAYER_SURFACE_V1_ANCHOR_LEFT |
        ZWLR_LAYER_SURFACE_V1_ANCHOR_RIGHT
    );
    zwlr_layer_surface_v1_set_exclusive_zone(out->layer_surface, -1);
    zwlr_layer_surface_v1_set_keyboard_interactivity(out->layer_surface, 0);
    zwlr_layer_surface_v1_add_listener(out->layer_surface, &layer_surface_listener, out);

    /* Empty input region allows mouse and keyboard events to pass through. */
    struct wl_region *empty_region = wl_compositor_create_region(app->compositor);
    wl_surface_set_input_region(out->surface, empty_region);
    wl_region_destroy(empty_region);

    out->viewport = wp_viewporter_get_viewport(app->viewporter, out->surface);
    out->alpha_surface = wp_alpha_modifier_v1_get_surface(app->alpha_modifier, out->surface);

    out->mapped = true;
    /* Initial commit without a buffer to trigger configuration. */
    wl_surface_commit(out->surface);
    return 0;
}

static void start_animation(struct dimmer_app *app, struct output_state *out,
                            double target_alpha, double duration_sec,
                            enum easing_curve easing) {
    if (target_alpha < 0.0) target_alpha = 0.0;
    if (target_alpha > 1.0) target_alpha = 1.0;

    if (target_alpha > 0.0) {
        if (!out->mapped && map_overlay(app, out) < 0) return;
    } else if (!out->mapped) {
        return;
    }

    out->start_alpha = out->current_alpha;
    out->target_alpha = target_alpha;
    out->duration_sec = (duration_sec > 0.0) ? duration_sec : 0.001;
    out->easing = easing;
    out->start_time_sec = get_time_sec();
    out->last_commit_sec = out->start_time_sec;
    out->animating = true;

    commit_overlay(out, true);
}

/* Stage premultiplied ARGB8888 raw image buffer for an output. */
static int stage_art(struct dimmer_app *app, struct output_state *out, const char *path,
                     int width, int height) {
    if (width <= 0 || height <= 0 || width > MAX_ART_EDGE || height > MAX_ART_EDGE) {
        fprintf(stderr, "theater-dimmer: refusing artwork sized %dx%d\n", width, height);
        return -1;
    }

    /* Open file descriptor for shm pool creation (requires PROT_WRITE for mmap). */
    int fd = open(path, O_RDWR | O_CLOEXEC);
    if (fd < 0) {
        fprintf(stderr, "theater-dimmer: cannot open artwork %s: %s\n", path, strerror(errno));
        return -1;
    }

    size_t stride = (size_t)width * 4;
    size_t needed = stride * (size_t)height;
    struct stat st;
    if (fstat(fd, &st) < 0) {
        fprintf(stderr, "theater-dimmer: cannot stat artwork %s: %s\n", path, strerror(errno));
        close(fd);
        return -1;
    }
    if ((size_t)st.st_size < needed) {
        fprintf(stderr, "theater-dimmer: artwork %s is %lld bytes, need %zu for %dx%d\n",
                path, (long long)st.st_size, needed, width, height);
        close(fd);
        return -1;
    }

    struct wl_shm_pool *pool = wl_shm_create_pool(app->shm, fd, (int32_t)needed);
    close(fd);
    if (!pool) {
        fprintf(stderr, "theater-dimmer: could not create a shm pool for %s\n", path);
        return -1;
    }

    struct wl_buffer *buffer = wl_shm_pool_create_buffer(
        pool, 0, width, height, (int32_t)stride, WL_SHM_FORMAT_ARGB8888);
    wl_shm_pool_destroy(pool);
    if (!buffer) {
        fprintf(stderr, "theater-dimmer: could not create a buffer for %s\n", path);
        return -1;
    }

    destroy_art(out);
    out->art = buffer;
    return 0;
}

static void output_geometry(void *data, struct wl_output *wl_output, int32_t x, int32_t y,
                            int32_t physical_width, int32_t physical_height, int32_t subpixel,
                            const char *make, const char *model, int32_t transform) {
    (void)data; (void)wl_output; (void)x; (void)y; (void)physical_width; (void)physical_height;
    (void)subpixel; (void)make; (void)model; (void)transform;
}

/* Mode and geometry callbacks (viewport scaling handles surface dimensions). */
static void output_mode(void *data, struct wl_output *wl_output, uint32_t flags, int32_t width,
                        int32_t height, int32_t refresh) {
    (void)data; (void)wl_output; (void)flags; (void)width; (void)height; (void)refresh;
}

static void output_done(void *data, struct wl_output *wl_output) {
    (void)data; (void)wl_output;
}

static void output_scale(void *data, struct wl_output *wl_output, int32_t factor) {
    (void)data; (void)wl_output; (void)factor;
}

static void output_name(void *data, struct wl_output *wl_output, const char *name) {
    (void)wl_output;
    struct output_state *out = (struct output_state *)data;
    snprintf(out->name, sizeof(out->name), "%s", name);
}

static void output_description(void *data, struct wl_output *wl_output, const char *description) {
    (void)data; (void)wl_output; (void)description;
}

static const struct wl_output_listener output_listener = {
    .geometry = output_geometry,
    .mode = output_mode,
    .done = output_done,
    .scale = output_scale,
    .name = output_name,
    .description = output_description,
};

static void registry_global(void *data, struct wl_registry *registry, uint32_t id,
                            const char *interface, uint32_t version) {
    (void)version;
    struct dimmer_app *app = (struct dimmer_app *)data;

    if (strcmp(interface, wl_compositor_interface.name) == 0) {
        app->compositor =
            wl_registry_bind(registry, id, &wl_compositor_interface, COMPOSITOR_VERSION);
    } else if (strcmp(interface, zwlr_layer_shell_v1_interface.name) == 0) {
        app->layer_shell =
            wl_registry_bind(registry, id, &zwlr_layer_shell_v1_interface, LAYER_SHELL_VERSION);
    } else if (strcmp(interface, wp_viewporter_interface.name) == 0) {
        app->viewporter = wl_registry_bind(registry, id, &wp_viewporter_interface, 1);
    } else if (strcmp(interface, wp_single_pixel_buffer_manager_v1_interface.name) == 0) {
        app->pixel_buffers =
            wl_registry_bind(registry, id, &wp_single_pixel_buffer_manager_v1_interface, 1);
    } else if (strcmp(interface, wp_alpha_modifier_v1_interface.name) == 0) {
        app->alpha_modifier = wl_registry_bind(registry, id, &wp_alpha_modifier_v1_interface, 1);
    } else if (strcmp(interface, wl_shm_interface.name) == 0) {
        app->shm = wl_registry_bind(registry, id, &wl_shm_interface, 1);
    } else if (strcmp(interface, wl_output_interface.name) == 0) {
        struct output_state *out = NULL;
        for (int i = 0; i < MAX_OUTPUTS; i++) {
            if (!app->outputs[i].in_use) {
                out = &app->outputs[i];
                break;
            }
        }
        if (!out) {
            fprintf(stderr, "theater-dimmer: more than %d outputs; ignoring the rest\n",
                    MAX_OUTPUTS);
            return;
        }

        memset(out, 0, sizeof(*out));
        out->app = app;
        out->in_use = true;
        out->global_id = id;
        out->wl_output =
            wl_registry_bind(registry, id, &wl_output_interface, OUTPUT_VERSION);
        wl_output_add_listener(out->wl_output, &output_listener, out);
    }
}

static void release_output(struct dimmer_app *app, struct output_state *out) {
    (void)app;
    if (!out->wl_output) return;
    wl_output_release(out->wl_output);
    out->wl_output = NULL;
}

static void registry_global_remove(void *data, struct wl_registry *registry, uint32_t id) {
    (void)registry;
    struct dimmer_app *app = (struct dimmer_app *)data;

    for (int i = 0; i < MAX_OUTPUTS; i++) {
        struct output_state *out = &app->outputs[i];
        if (!out->in_use || out->global_id != id) continue;

        destroy_overlay(out);
        destroy_art(out);
        release_output(app, out);
        /* Return slot to pool for subsequent hotplug detection. */
        memset(out, 0, sizeof(*out));
        return;
    }
}

static const struct wl_registry_listener registry_listener = {
    .global = registry_global,
    .global_remove = registry_global_remove,
};

static int count_outputs(const struct dimmer_app *app) {
    int count = 0;
    for (int i = 0; i < MAX_OUTPUTS; i++) {
        if (app->outputs[i].in_use) count++;
    }
    return count;
}

static void handle_art(struct dimmer_app *app) {
    char *name = strtok(NULL, " \t\r\n");
    char *width_str = strtok(NULL, " \t\r\n");
    char *height_str = strtok(NULL, " \t\r\n");
    /* Remaining line content is treated as file path to allow spaces in paths. */
    char *path = strtok(NULL, "\r\n");

    if (!name) {
        printf("ERR invalid ART arguments\n");
        fflush(stdout);
        return;
    }

    struct output_state *out = find_output(app, name);
    if (!out) {
        fprintf(stderr, "theater-dimmer: ART names unknown output '%s'\n", name);
        printf("ERR unknown output '%s'\n", name);
        fflush(stdout);
        return;
    }

    if (!path || !*path) {
        destroy_art(out);
        printf("OK ART %s cleared\n", name);
        fflush(stdout);
        return;
    }

    int width = width_str ? atoi(width_str) : 0;
    int height = height_str ? atoi(height_str) : 0;
    if (stage_art(app, out, path, width, height) < 0) {
        printf("ERR ART %s rejected\n", name);
        fflush(stdout);
        return;
    }

    printf("OK ART %s %dx%d\n", name, width, height);
    fflush(stdout);
}

static void handle_dim(struct dimmer_app *app) {
    char *outputs_str = strtok(NULL, " \t\r\n");
    char *alpha_str = strtok(NULL, " \t\r\n");
    char *duration_str = strtok(NULL, " \t\r\n");
    char *easing_str = strtok(NULL, " \t\r\n");

    if (!outputs_str || !alpha_str) {
        printf("ERR invalid DIM arguments\n");
        fflush(stdout);
        return;
    }

    double target_alpha = atof(alpha_str);
    double duration_sec = duration_str ? atof(duration_str) : 1.5;
    enum easing_curve easing = parse_easing(easing_str);

    char *targets[MAX_OUTPUTS];
    bool matched[MAX_OUTPUTS] = {false};
    int target_count = 0;
    char *token = strtok(outputs_str, ",");
    while (token && target_count < MAX_OUTPUTS) {
        targets[target_count++] = token;
        token = strtok(NULL, ",");
    }

    /* Retarget displays: animate active outputs to target alpha, fade out unselected outputs. */
    for (int i = 0; i < MAX_OUTPUTS; i++) {
        struct output_state *out = &app->outputs[i];
        if (!out->in_use) continue;

        bool is_target = false;
        for (int j = 0; j < target_count; j++) {
            if (strcmp(out->name, targets[j]) == 0) {
                is_target = true;
                matched[j] = true;
                break;
            }
        }

        if (is_target) {
            /* Staged artwork has dim factor pre-applied; flat black uses target alpha. */
            start_animation(app, out, out->art ? 1.0 : target_alpha, duration_sec, easing);
        } else if (out->mapped || out->current_alpha > 0.0) {
            start_animation(app, out, 0.0, duration_sec, easing);
        }
    }

    int matched_count = 0;
    for (int j = 0; j < target_count; j++) {
        if (matched[j]) {
            matched_count++;
        } else {
            fprintf(stderr, "theater-dimmer: no output named '%s'\n", targets[j]);
        }
    }

    printf("OK DIM alpha=%.2f duration=%.2fs easing=%s matched=%d/%d\n",
           target_alpha, duration_sec, easing_to_string(easing), matched_count, target_count);
    fflush(stdout);
}

static void handle_fade_out(struct dimmer_app *app) {
    char *duration_str = strtok(NULL, " \t\r\n");
    char *easing_str = strtok(NULL, " \t\r\n");

    double duration_sec = duration_str ? atof(duration_str) : 1.5;
    enum easing_curve easing = parse_easing(easing_str);

    for (int i = 0; i < MAX_OUTPUTS; i++) {
        struct output_state *out = &app->outputs[i];
        if (!out->in_use) continue;
        if (out->mapped || out->current_alpha > 0.0) {
            start_animation(app, out, 0.0, duration_sec, easing);
        }
    }

    printf("OK FADE_OUT duration=%.2fs easing=%s\n", duration_sec, easing_to_string(easing));
    fflush(stdout);
}

static void handle_status(struct dimmer_app *app) {
    printf("STATUS {");
    bool first = true;
    for (int i = 0; i < MAX_OUTPUTS; i++) {
        struct output_state *out = &app->outputs[i];
        if (!out->in_use) continue;
        printf("%s\"%s\":{\"alpha\":%.3f,\"target\":%.3f,\"animating\":%s,\"art\":%s}",
               first ? "" : ",", out->name, out->current_alpha, out->target_alpha,
               out->animating ? "true" : "false", out->art ? "true" : "false");
        first = false;
    }
    printf("}\n");
    fflush(stdout);
}

static void handle_command(struct dimmer_app *app, char *line) {
    char *cmd = strtok(line, " \t\r\n");
    if (!cmd) return;

    if (strcasecmp(cmd, "ART") == 0) {
        handle_art(app);
    } else if (strcasecmp(cmd, "DIM") == 0) {
        handle_dim(app);
    } else if (strcasecmp(cmd, "FADE_OUT") == 0) {
        handle_fade_out(app);
    } else if (strcasecmp(cmd, "STATUS") == 0) {
        handle_status(app);
    } else if (strcasecmp(cmd, "QUIT") == 0) {
        app->running = false;
        printf("OK BYE\n");
        fflush(stdout);
    } else {
        printf("ERR unknown command '%s'\n", cmd);
        fflush(stdout);
    }
}

int main(int argc, char **argv) {
    (void)argc; (void)argv;

    static struct dimmer_app app;

    setvbuf(stdin, NULL, _IOLBF, 0);
    setvbuf(stdout, NULL, _IOLBF, 0);

    signal(SIGINT, handle_signal);
    signal(SIGTERM, handle_signal);
    signal(SIGHUP, handle_signal);
    signal(SIGPIPE, SIG_IGN);

    app.running = true;

    app.display = wl_display_connect(NULL);
    if (!app.display) {
        fprintf(stderr, "theater-dimmer: failed to connect to Wayland display\n");
        return 1;
    }

    app.registry = wl_display_get_registry(app.display);
    wl_registry_add_listener(app.registry, &registry_listener, &app);

    wl_display_roundtrip(app.display);
    wl_display_roundtrip(app.display);

    if (!app.compositor || !app.layer_shell || !app.viewporter || !app.pixel_buffers ||
        !app.alpha_modifier || !app.shm) {
        fprintf(stderr,
                "theater-dimmer: compositor is missing required Wayland extensions "
                "(need wl_compositor, wl_shm, zwlr_layer_shell_v1, wp_viewporter, "
                "wp_single_pixel_buffer_manager_v1 and wp_alpha_modifier_v1)\n");
        wl_display_disconnect(app.display);
        return 1;
    }

    /* Pre-multiplied single pixel buffer for black overlays. */
    app.black = wp_single_pixel_buffer_manager_v1_create_u32_rgba_buffer(
        app.pixel_buffers, 0, 0, 0, UINT32_MAX);

    int stdin_flags = fcntl(STDIN_FILENO, F_GETFL, 0);
    fcntl(STDIN_FILENO, F_SETFL, stdin_flags | O_NONBLOCK);

    printf("READY theater-dimmer initialized with %d outputs\n", count_outputs(&app));
    fflush(stdout);

    int exit_code = 0;
    char input_buf[BUFFER_SIZE];
    size_t input_len = 0;

    struct pollfd fds[2];
    fds[0].fd = wl_display_get_fd(app.display);
    fds[0].events = POLLIN;
    fds[1].fd = STDIN_FILENO;
    fds[1].events = POLLIN;

    while (app.running && !g_signal_received) {
        while (wl_display_prepare_read(app.display) != 0) {
            wl_display_dispatch_pending(app.display);
        }

        if (wl_display_flush(app.display) < 0 && errno != EAGAIN) {
            wl_display_cancel_read(app.display);
            break;
        }

        int ret = poll(fds, 2, poll_timeout_ms(&app));

        if (ret < 0) {
            wl_display_cancel_read(app.display);
            if (errno == EINTR) continue;
            break;
        }

        if (fds[0].revents & POLLIN) {
            wl_display_read_events(app.display);
            wl_display_dispatch_pending(app.display);
        } else {
            wl_display_cancel_read(app.display);
        }

        if (!display_is_alive(&app)) {
            exit_code = 1;
            break;
        }

        if (fds[1].revents & POLLIN) {
            ssize_t n = read(STDIN_FILENO, input_buf + input_len, sizeof(input_buf) - input_len - 1);
            if (n > 0) {
                input_len += (size_t)n;
                input_buf[input_len] = '\0';

                char *line_start = input_buf;
                char *newline;
                while ((newline = strchr(line_start, '\n')) != NULL) {
                    *newline = '\0';
                    handle_command(&app, line_start);
                    line_start = newline + 1;
                }

                size_t consumed = (size_t)(line_start - input_buf);
                if (consumed < input_len) {
                    memmove(input_buf, line_start, input_len - consumed);
                    input_len -= consumed;
                } else {
                    input_len = 0;
                }
            } else if (n == 0) {
                app.running = false;
            }
        }

        if (fds[1].revents & (POLLHUP | POLLERR)) {
            app.running = false;
        }

        advance_stalled_animations(&app);
    }

    for (int i = 0; i < MAX_OUTPUTS; i++) {
        if (app.outputs[i].in_use) {
            destroy_overlay(&app.outputs[i]);
            destroy_art(&app.outputs[i]);
            release_output(&app, &app.outputs[i]);
        }
    }

    if (app.black) wl_buffer_destroy(app.black);
    if (app.alpha_modifier) wp_alpha_modifier_v1_destroy(app.alpha_modifier);
    if (app.shm) wl_shm_destroy(app.shm);
    if (app.pixel_buffers) wp_single_pixel_buffer_manager_v1_destroy(app.pixel_buffers);
    if (app.viewporter) wp_viewporter_destroy(app.viewporter);
    if (app.layer_shell) {
        zwlr_layer_shell_v1_destroy(app.layer_shell);
    }
    if (app.compositor) wl_compositor_destroy(app.compositor);
    if (app.registry) wl_registry_destroy(app.registry);

    if (exit_code == 0) {
        wl_display_roundtrip(app.display);
    }
    wl_display_disconnect(app.display);

    return exit_code;
}
