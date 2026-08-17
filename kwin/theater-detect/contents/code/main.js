/*
 * theater-detect — the KWin half of theater mode.
 *
 * This script is deliberately dumb. It reports window lifecycle facts to the
 * theater-mode daemon and makes no decisions of its own: what counts as a game,
 * which screens to affect, and what effect to apply all live in theater-moded,
 * where they can be logged, tested and changed without reloading the
 * compositor. Keeping policy out of here matters because a KWin script has no
 * filesystem access, no process spawning, and no usable error reporting.
 *
 * Written in ES5 style on purpose — KWin's JS engine is not a browser, and this
 * runs inside the compositor, so it stays boring.
 */

var SERVICE = "org.theatermode.TheaterMode";
var OBJPATH = "/org/theatermode/TheaterMode";
var IFACE = "org.theatermode.TheaterMode";

// The daemon may be restarted independently of KWin. A periodic full snapshot
// lets the two converge no matter which one restarted, without the script
// needing to know whether the daemon is listening.
var SNAPSHOT_INTERVAL_MS = 60000;

// Tracked windows, keyed by internalId, holding the last state we announced so
// we only send changes the daemon has not already seen.
var tracked = {};

// Held at script scope on purpose. A QTimer that is only referenced by a local
// variable inside a function gets garbage collected once that function returns,
// and the heartbeat silently stops firing.
var heartbeat = null;

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

/*
 * The only thing filtered here is a window with no process behind it, which
 * nothing can ever be attributed to. Whether a window is "normal" is reported
 * rather than judged: a game hosted inside gamescope shows up as gamescope's
 * own surface, and guessing wrong about its window type here would drop it
 * before the daemon could log why.
 */
function isCandidate(window) {
    return !!window && window.pid > 0;
}

function announceOpened(window) {
    var id = idOf(window);
    var output = outputNameOf(window);
    var fullscreen = window.fullScreen === true;

    tracked[id] = { output: output, fullscreen: fullscreen };

    // Everything is sent as a string. callDBus infers D-Bus types from JS
    // values and cannot produce the uint32 or boolean a richer signature would
    // need; a mismatch is rejected by the bus with no error surfaced here, so
    // the daemon does the parsing instead.
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
        // A window we never announced changed state — announce it properly
        // rather than sending a delta the daemon cannot place.
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

/*
 * A window's screen and fullscreen state both change during normal play — a
 * game going fullscreen, or the user dragging a window between monitors — and
 * the daemon needs to follow both to keep the effect on the right screens.
 */
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
    /*
     * Deliberately not filtered through isCandidate(): by the time a window is
     * removed its properties are already being torn down, and pid or
     * normalWindow can read back as invalid. Whether we announced it is
     * recorded in `tracked`, which is the only reliable thing left to consult.
     */
    announceClosed(window);
}

/*
 * Send the complete current picture. The daemon brackets these with
 * SnapshotBegin/SnapshotEnd so it can drop anything it still believes is open
 * that we did not mention — which is how state heals after either side
 * restarts, or after a window we somehow missed disappears.
 */
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

function init() {
    var windows = workspace.windowList();
    for (var i = 0; i < windows.length; i++) {
        if (isCandidate(windows[i])) {
            watch(windows[i]);
        }
    }

    workspace.windowAdded.connect(onWindowAdded);
    workspace.windowRemoved.connect(onWindowRemoved);

    sendSnapshot();

    heartbeat = new QTimer();
    heartbeat.interval = SNAPSHOT_INTERVAL_MS;
    heartbeat.singleShot = false;
    heartbeat.timeout.connect(sendSnapshot);
    heartbeat.start();
}

init();
