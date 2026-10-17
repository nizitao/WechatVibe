"use strict";
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const { it } = require("node:test");
const vm = require("node:vm");
const { dom, element } = require("./helpers/synthetic-dom.cjs");
const app = readFileSync(path.join(__dirname, "../chatui/app.js"), "utf8");
const start = app.indexOf("settingsState.apiWorkerRequest = 0;"), end = app.indexOf("function showChatEmptyState()", start);
assert.ok(start >= 0 && end > start);
function harness(api) {
  const ui = dom();
  ui.byId("apiWorkerLimitHint").parentElement = element();
  const context = vm.createContext({ ...ui, api, settingsState: {} });
  vm.runInContext(app.slice(start, end) + "\nthis.fns = { loadApiWorkerSettings, changeApiWorkerSettings };", context);
  return { ...ui, context, fns: context.fns };
}
it("shows a provider throttle all the way down to one lane and bounds controls", async () => {
  const run = harness(async () => ({ workers: 4, limit: 1, max: 4 }));
  await run.fns.loadApiWorkerSettings();
  assert.match(run.byId("apiWorkerLimitHint").textContent, /当前 1 \/ 4 路.*自动降档/);
  assert.equal(run.byId("btnApiWorkerPlus").disabled, true);
  assert.equal(run.byId("btnApiWorkerMinus").disabled, false);
});
it("serializes clicks and ignores a read that predates the successful write", async () => {
  let readDone, writeDone;
  const calls = [];
  const run = harness((url, options = {}) => {
    calls.push(options.method || "GET");
    return new Promise(resolve => {
      if (options.method === "POST") {
        assert.equal(JSON.parse(options.body).workers, 3);
        writeDone = resolve;
      } else readDone = resolve;
    });
  });
  run.context.settingsState.apiWorkerSettings = { workers: 2, limit: 2, max: 4 };
  const reading = run.fns.loadApiWorkerSettings();
  const writing = run.fns.changeApiWorkerSettings(1);
  await run.fns.changeApiWorkerSettings(1);
  assert.equal(run.byId("btnApiWorkerMinus").disabled, true);
  writeDone({ workers: 3, limit: 3, max: 4 });
  await writing;
  readDone({ workers: 1, limit: 1, max: 4 });
  await reading;
  assert.deepEqual(calls, ["GET", "POST"]);
  assert.equal(run.byId("apiWorkerValue").textContent, "3");
});
it("keeps the confirmed value and restores controls when saving fails", async () => {
  const run = harness(async () => { throw new Error("synthetic"); });
  run.context.settingsState.apiWorkerSettings = { workers: 2, limit: 2, max: 4 };
  await run.fns.changeApiWorkerSettings(1);
  assert.equal(run.byId("apiWorkerValue").textContent, "2");
  assert.equal(run.byId("btnApiWorkerPlus").disabled, false);
  assert.match(run.byId("apiWorkerLimitHint").textContent, /保存失败/);
});
