/*
 * theater-detect — KWin window lifecycle detector for theater-mode.
 *
 * Reports window open, move, and close events to the theater-mode daemon via
 * D-Bus. Policy decisions (game identification, screen targeting, effects)
 * are handled by theater-moded.
 */

var SERVICE = "org.theatermode.TheaterMode";
var OBJPATH = "/org/theatermode/TheaterMode";
var IFACE = "org.theatermode.TheaterMode";

// Periodic full snapshot interval to keep state synchronized with daemon.
var SNAPSHOT_INTERVAL_MS = 60000;

// Tracked windows, keyed by internalId, storing last announced state.
var tracked = {};

// Delay to coalesce rapid screensChanged signals during display mode changes.
var SCREENS_SETTLE_MS = 1000;

// Retain timer references at script scope to prevent garbage collection.
var heartbeat = null;
var screenSettle = null;

function idOf(window) {
    return String(window.internalId);
}

function outputNameOf(window) {
    try {
        return window.output ? String(window.output.name) : "";
    } catch (e) {
        return "";
    }
}

// Filter out windows without a valid PID.
function isCandidate(window) {
    return !!window && window.pid > 0;
}

function announceOpened(window) {
    var id = idOf(window);
    var output = outputNameOf(window);
    var fullscreen = window.fullScreen === true;

    tracked[id] = { output: output, fullscreen: fullscreen };

    // Arguments are passed as strings to prevent D-Bus type inference mismatches.
    callDBus(SERVICE, OBJPATH, IFACE, "WindowOpened",
             id,
             String(window.resourceClass),
             String(window.pid),
             output,
             String(fullscreen),
             String(window.normalWindow === true));
}

function announceChanged(window) {
    var id = idOf(window);
    var previous = tracked[id];
    if (!previous) {
        // Window was not previously tracked; announce as opened.
        announceOpened(window);
        return;
    }

    var output = outputNameOf(window);
    var fullscreen = window.fullScreen === true;
    if (previous.output === output && previous.fullscreen === fullscreen) {
        return;
    }

    tracked[id] = { output: output, fullscreen: fullscreen };
    callDBus(SERVICE, OBJPATH, IFACE, "WindowChanged", id, output, String(fullscreen));
}

function announceClosed(window) {
    var id = idOf(window);
    if (!tracked[id]) {
        return;
    }
    delete tracked[id];
    callDBus(SERVICE, OBJPATH, IFACE, "WindowClosed", id);
}

// Track display output and fullscreen state changes for a window.
function watch(window) {
    var onChanged = function () { announceChanged(window); };
    window.outputChanged.connect(onChanged);
    window.fullScreenChanged.connect(onChanged);
}

function onWindowAdded(window) {
    if (!isCandidate(window)) {
        return;
    }
    announceOpened(window);
    watch(window);
}

function onWindowRemoved(window) {
    // Window properties may already be torn down during removal; consult tracked state.
    announceClosed(window);
}

// Send full window snapshot to synchronize daemon state.
function sendSnapshot() {
    callDBus(SERVICE, OBJPATH, IFACE, "SnapshotBegin");

    var windows = workspace.windowList();
    for (var i = 0; i < windows.length; i++) {
        if (isCandidate(windows[i])) {
            announceOpened(windows[i]);
        }
    }

    callDBus(SERVICE, OBJPATH, IFACE, "SnapshotEnd");
}

// Trigger a snapshot when display configuration changes.
function watchScreens() {
    if (!workspace.screensChanged) {
        return;
    }

    screenSettle = new QTimer();
    screenSettle.interval = SCREENS_SETTLE_MS;
    screenSettle.singleShot = true;
    screenSettle.timeout.connect(sendSnapshot);

    workspace.screensChanged.connect(function () {
        screenSettle.restart();
    });
}

function init() {
    var windows = workspace.windowList();
    for (var i = 0; i < windows.length; i++) {
        if (isCandidate(windows[i])) {
            watch(windows[i]);
        }
    }

    workspace.windowAdded.connect(onWindowAdded);
    workspace.windowRemoved.connect(onWindowRemoved);
    watchScreens();

    sendSnapshot();

    heartbeat = new QTimer();
    heartbeat.interval = SNAPSHOT_INTERVAL_MS;
    heartbeat.singleShot = false;
    heartbeat.timeout.connect(sendSnapshot);
    heartbeat.start();
}

init();
