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

global.QTimer = function () {
    this.timeout = new Signal();
    this.start = function () {};
    this.restart = function () {};
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

assert.equal(callsFor("WindowOpened", "existing").length, 1);
assert.equal(callsFor("WindowOpened", "late").length, 0);
assert.equal(existing.outputChanged.handlers.length, 1);
assert.equal(existing.fullScreenChanged.handlers.length, 1);

var heartbeat = timers.filter(function (timer) { return timer.singleShot === false; })[0];
assert.ok(heartbeat, "the detector should retain a periodic snapshot timer");

late.normalWindow = true;
heartbeat.timeout.emit();
assert.equal(callsFor("WindowOpened", "late").length, 1);
assert.equal(late.outputChanged.handlers.length, 1);
assert.equal(late.fullScreenChanged.handlers.length, 1);

heartbeat.timeout.emit();
assert.equal(late.outputChanged.handlers.length, 1);
assert.equal(late.fullScreenChanged.handlers.length, 1);

late.output = { name: "HDMI-A-1" };
late.outputChanged.emit();
assert.deepEqual(callsFor("WindowChanged", "late").at(-1).args, ["late", "HDMI-A-1", "true"]);

workspace.windowRemoved.emit(late);
assert.equal(callsFor("WindowClosed", "late").length, 1);

var replacement = makeWindow("late", true);
windows = [existing, replacement];
workspace.windowAdded.emit(replacement);
assert.equal(callsFor("WindowOpened", "late").length, 3);
assert.equal(replacement.outputChanged.handlers.length, 1);
assert.equal(replacement.fullScreenChanged.handlers.length, 1);
