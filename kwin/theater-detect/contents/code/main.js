/*
 * theater-detect — KWin window lifecycle detector for theater-mode.
 *
 * Reports window open, move, and close events to the theater-mode daemon via
 * D-Bus. Policy decisions (game identification, screen targeting, effects)
 * are handled by theater-moded.
 */

var SERVICE = "com.seanbrar.TheaterMode";
var OBJPATH = "/com/seanbrar/TheaterMode";
var IFACE = "com.seanbrar.TheaterMode";

// Periodic full snapshot interval to keep state synchronized with daemon.
var SNAPSHOT_INTERVAL_MS = 15000;

var tracked = {};

var watched = {};

// Delay to coalesce rapid screensChanged signals during display mode changes.
var SCREENS_SETTLE_MS = 1000;

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

function isCandidate(window) {
    return !!window && window.pid > 0 && window.normalWindow === true;
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
             String(fullscreen));
}

function announceChanged(window) {
    // A window can fail isCandidate after having passed it, while its listeners stay
    // connected. Retire it here rather than re-announcing it as opened.
    if (!isCandidate(window)) {
        announceClosed(window);
        return;
    }

    var id = idOf(window);
    var previous = tracked[id];
    if (!previous) {
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
    delete watched[id];
    if (!tracked[id]) {
        return;
    }
    delete tracked[id];
    callDBus(SERVICE, OBJPATH, IFACE, "WindowClosed", id);
}

function watch(window) {
    var id = idOf(window);
    if (watched[id]) {
        return;
    }
    watched[id] = true;
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

function screenNames() {
    var names = [];
    try {
        if (workspace.screens) {
            for (var i = 0; i < workspace.screens.length; i++) {
                var s = workspace.screens[i];
                if (s && s.name) {
                    names.push(String(s.name));
                }
            }
        }
    } catch (e) {
        // An empty list leaves the daemon on its DRM sysfs fallback.
    }
    return names;
}

function sendSnapshot() {
    // Names are joined into one string, as callDBus marshals scalar arguments only.
    var screens = screenNames();
    callDBus(SERVICE, OBJPATH, IFACE, "SnapshotBegin", screens.join(","));

    var windows = workspace.windowList();
    for (var i = 0; i < windows.length; i++) {
        if (isCandidate(windows[i])) {
            announceOpened(windows[i]);
            watch(windows[i]);
        }
    }

    callDBus(SERVICE, OBJPATH, IFACE, "SnapshotEnd");
}

function watchScreens() {
    if (!workspace.screensChanged) {
        return;
    }

    screenSettle = new QTimer();
    screenSettle.interval = SCREENS_SETTLE_MS;
    screenSettle.singleShot = true;
    screenSettle.timeout.connect(sendSnapshot);

    workspace.screensChanged.connect(function () {
        screenSettle.start();
    });
}

function init() {
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
