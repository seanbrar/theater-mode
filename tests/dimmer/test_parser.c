#pragma GCC diagnostic ignored "-Wunused-function"
#define DIMMER_TEST_HARNESS
#include "dimmer.c"

static int checks_run;
static int checks_failed;
static const char *current_test;

#define CHECK(condition)                                                               \
    do {                                                                               \
        checks_run++;                                                                  \
        if (!(condition)) {                                                            \
            checks_failed++;                                                           \
            fprintf(stderr, "  FAIL %s:%d [%s]: %s\n", __FILE__, __LINE__,            \
                    current_test, #condition);                                         \
        }                                                                              \
    } while (0)

#define RUN(function)                                                                  \
    do {                                                                               \
        current_test = #function;                                                      \
        int failures_before = checks_failed;                                           \
        function();                                                                    \
        printf("  %-38s %s\n", #function,                                             \
               checks_failed == failures_before ? "ok" : "FAILED");                  \
    } while (0)

static char captured_output[4096];
static int output_pipe[2];
static int saved_stdout;

static void start_capture(void) {
    fflush(stdout);
    CHECK(pipe(output_pipe) == 0);
    saved_stdout = dup(STDOUT_FILENO);
    CHECK(saved_stdout >= 0);
    CHECK(dup2(output_pipe[1], STDOUT_FILENO) >= 0);
    close(output_pipe[1]);
}

static const char *stop_capture(void) {
    fflush(stdout);
    CHECK(dup2(saved_stdout, STDOUT_FILENO) >= 0);
    close(saved_stdout);
    ssize_t count = read(output_pipe[0], captured_output, sizeof(captured_output) - 1);
    close(output_pipe[0]);
    CHECK(count >= 0);
    captured_output[count > 0 ? count : 0] = '\0';
    return captured_output;
}

static struct dimmer_app app_with_output(void) {
    struct dimmer_app app = {.running = true};
    app.outputs[0].in_use = true;
    snprintf(app.outputs[0].name, sizeof(app.outputs[0].name), "DP-1");
    app.outputs[0].layer = ZWLR_LAYER_SHELL_V1_LAYER_OVERLAY;
    return app;
}

static const char *run_command(struct dimmer_app *app, char *command) {
    start_capture();
    handle_command(app, command);
    return stop_capture();
}

static void test_art_argument_validation(void) {
    struct dimmer_app app = app_with_output();
    char clear[] = "ART DP-1";
    CHECK(strstr(run_command(&app, clear), "OK ART DP-1 cleared") != NULL);

    char missing_dimensions[] = "ART DP-1 /tmp/art.argb";
    CHECK(strstr(run_command(&app, missing_dimensions), "ERR invalid ART arguments") != NULL);

    char missing_height[] = "ART DP-1 1920 /tmp/art.argb";
    CHECK(strstr(run_command(&app, missing_height), "ERR invalid ART arguments") != NULL);

    char trailing_junk[] = "ART DP-1 1920x 1080 /tmp/art.argb";
    CHECK(strstr(run_command(&app, trailing_junk), "ERR invalid ART dimensions") != NULL);

    char excessive[] = "ART DP-1 16385 1080 /tmp/art.argb";
    CHECK(strstr(run_command(&app, excessive), "ERR invalid ART dimensions") != NULL);
}

static void test_oversized_line_recovery(void) {
    struct dimmer_app app = app_with_output();
    struct command_input input = {0};
    int input_pipe[2];
    CHECK(pipe(input_pipe) == 0);

    char payload[BUFFER_SIZE + 24];
    memset(payload, ' ', BUFFER_SIZE - 1);
    payload[BUFFER_SIZE - 1] = '\n';
    CHECK(write(input_pipe[1], payload, BUFFER_SIZE) == BUFFER_SIZE);
    start_capture();
    read_commands(&app, &input, input_pipe[0]);
    CHECK(input.len == 0);

    memset(payload, 'A', BUFFER_SIZE - 1);
    const char tail[] = "tail\nLAYER DP-1 bottom\n";
    memcpy(payload + BUFFER_SIZE - 1, tail, sizeof(tail) - 1);
    size_t payload_size = BUFFER_SIZE - 1 + sizeof(tail) - 1;
    CHECK(write(input_pipe[1], payload, payload_size) == (ssize_t)payload_size);

    read_commands(&app, &input, input_pipe[0]);
    CHECK(input.len == DISCARDING_LINE);
    read_commands(&app, &input, input_pipe[0]);
    const char *response = stop_capture();

    CHECK(strstr(response, "ERR line too long") != NULL);
    CHECK(strstr(response, "OK LAYER DP-1 bottom") != NULL);
    CHECK(app.outputs[0].layer == ZWLR_LAYER_SHELL_V1_LAYER_BOTTOM);
    CHECK(input.len == 0);
    close(input_pipe[0]);
    close(input_pipe[1]);
}

int main(void) {
    printf("[test_dimmer_parser]\n");
    RUN(test_art_argument_validation);
    RUN(test_oversized_line_recovery);
    printf("[test_dimmer_parser] %d checks, %d failed\n", checks_run, checks_failed);
    return checks_failed ? 1 : 0;
}
