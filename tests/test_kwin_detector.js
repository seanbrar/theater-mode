"use strict";

var assert = require("node:assert/strict");
var path = require("node:path");

function Signal() {
    this.handlers = [];
}

Signal.prototype.connect = function (handler) {
    this.handlers.push(handler);
};

Signal.prototype.emit = function () {
    var args = arguments;
    this.handlers.slice().forEach(function (handler) {
        handler.apply(null, args);
    });
};

function makeWindow(id, normalWindow) {
    return {
        internalId: id,
        pid: 1234,
        normalWindow: normalWindow,
        resourceClass: "steam_app_1234",
        output: { name: "DP-1" },
        fullScreen: true,
        outputChanged: new Signal(),
        fullScreenChanged: new Signal(),
    };
}

var calls = [];
var timers = [];
var existing = makeWindow("existing", true);
var late = makeWindow("late", false);
var windows = [existing, late];

global.callDBus = function () {
    var args = Array.prototype.slice.call(arguments);
    calls.push({ method: args[3], args: args.slice(4) });
};

// Mirror only the QTimer API exposed by KWin's Qt binding.
// A method added here that Qt lacks is callable in tests and missing in production.
global.QTimer = function () {
    this.timeout = new Signal();
    this.singleShot = false;
    this.interval = 0;
    this.running = false;
    this.start = function () {
        this.running = true;
    };
    this.stop = function () {
        this.running = false;
    };
    timers.push(this);
};

global.workspace = {
    windowList: function () { return windows; },
    windowAdded: new Signal(),
    windowRemoved: new Signal(),
    screensChanged: new Signal(),
};

require(path.join(__dirname, "../kwin/theater-detect/contents/code/main.js"));

function callsFor(method, id) {
    return calls.filter(function (call) {
        return call.method === method && (id === undefined || call.args[0] === id);
    });
}

function mark() {
    return calls.length;
}

// Return D-Bus calls recorded since marker formatted as "Method:windowId" or "Method".
function methodsSince(marker) {
    return calls.slice(marker).map(function (call) {
        return call.args.length ? call.method + ":" + call.args[0] : call.method;
    });
}

// Fire timer timeout signal, marking single-shot timers inactive first as Qt does.
// Emitting `timeout` directly would fire a timer that was never started.
function fire(timer) {
    if (timer.singleShot) {
        timer.running = false;
    }
    timer.timeout.emit();
}

// Assert timer interval, singleShot configuration, and armed state.
function assertTimer(timer, label, expected) {
    assert.ok(timer, label + " timer should be retained at script scope");
    assert.equal(timer.interval, expected.interval, label + " timer interval");
    assert.equal(timer.singleShot, expected.singleShot, label + " timer singleShot");
    assert.equal(timer.running, expected.running, label + " timer armed state");
}

// Initial snapshot announces existing normal windows bracketed by SnapshotBegin/End.
assert.deepEqual(methodsSince(0),
                 ["SnapshotBegin", "WindowOpened:existing", "SnapshotEnd"]);
assert.equal(existing.outputChanged.handlers.length, 1);
assert.equal(existing.fullScreenChanged.handlers.length, 1);

var heartbeat = timers.filter(function (timer) { return timer.singleShot === false; })[0];
assertTimer(heartbeat, "heartbeat", { interval: 15000, singleShot: false, running: true });

// Windows that become candidates later are announced on the next snapshot.
late.normalWindow = true;
var marker = mark();
fire(heartbeat);
assert.deepEqual(methodsSince(marker),
                 ["SnapshotBegin", "WindowOpened:existing", "WindowOpened:late", "SnapshotEnd"]);
assert.equal(late.outputChanged.handlers.length, 1);
assert.equal(late.fullScreenChanged.handlers.length, 1);

// Repeat snapshots must not attach duplicate signal listeners.
fire(heartbeat);
assert.equal(late.outputChanged.handlers.length, 1);
assert.equal(late.fullScreenChanged.handlers.length, 1);

late.output = { name: "HDMI-A-1" };
late.outputChanged.emit();
assert.deepEqual(callsFor("WindowChanged", "late").at(-1).args, ["late", "HDMI-A-1", "true"]);

// Suppress duplicate D-Bus signals when window state has not changed.
marker = mark();
late.outputChanged.emit();
assert.deepEqual(methodsSince(marker), []);

marker = mark();
workspace.windowRemoved.emit(late);
assert.deepEqual(methodsSince(marker), ["WindowClosed:late"]);

// Closing an untracked window emits no D-Bus signals.
marker = mark();
workspace.windowRemoved.emit(late);
assert.deepEqual(methodsSince(marker), []);

// A new window reusing a previous internalId is announced and watched.
var replacement = makeWindow("late", true);
windows = [existing, replacement];
marker = mark();
workspace.windowAdded.emit(replacement);
assert.deepEqual(methodsSince(marker), ["WindowOpened:late"]);
assert.equal(replacement.outputChanged.handlers.length, 1);
assert.equal(replacement.fullScreenChanged.handlers.length, 1);

// Windows without a valid PID are ignored.
var pidless = makeWindow("pidless", true);
pidless.pid = 0;
marker = mark();
workspace.windowAdded.emit(pidless);
assert.deepEqual(methodsSince(marker), []);

// Fall back to empty output if window properties are inaccessible during teardown.
var teardown = makeWindow("teardown", true);
Object.defineProperty(teardown, "output", {
    get: function () { throw new Error("window is being destroyed"); },
});
workspace.windowAdded.emit(teardown);
assert.deepEqual(callsFor("WindowOpened", "teardown").at(-1).args,
                 ["teardown", "steam_app_1234", "1234", "", "true"]);

// A tracked window that loses candidate status emits WindowClosed.
marker = mark();
replacement.normalWindow = false;
replacement.fullScreenChanged.emit();
assert.deepEqual(methodsSince(marker), ["WindowClosed:late"]);

var screenSettle = timers.filter(function (timer) { return timer.singleShot === true; })[0];
assertTimer(screenSettle, "screen settle", { interval: 1000, singleShot: true, running: false });

// Rapid screensChanged signals reset the settle debounce timer before snapshot fires.
marker = mark();
workspace.screensChanged.emit();
assert.deepEqual(methodsSince(marker), []);
assert.equal(screenSettle.running, true);

workspace.screensChanged.emit();
assert.equal(screenSettle.running, true);
assert.equal(timers.length, 2);

fire(screenSettle);
assert.deepEqual(methodsSince(marker),
                 ["SnapshotBegin", "WindowOpened:existing", "SnapshotEnd"]);
assert.equal(screenSettle.running, false);
