"use strict";
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const { it } = require("node:test");
const vm = require("node:vm");
const { seed } = require("./helpers/view-state-harness.cjs");

const source = readFileSync(path.join(__dirname, "../chatui/app.js"), "utf8").replace(/\r/gu, "");
function body(signature) {
  const start = source.indexOf(signature);
  const end = source.indexOf("\n}\n", start);
  assert.ok(start >= 0 && end > start, `Missing function: ${signature}`);
  return source.slice(start, end + 3);
}
const code = [
  "function profileCacheKey(", "function observeProfileRate(", "function profileRateText(",
  "function updateProfileProgress(", "function cancelLocalProfilePoll(", "async function loadProfile(",
].map(body).join("\n") + "\nglobalThis.ui = { loadProfile, cancelLocalProfilePoll };";
const tick = () => new Promise(resolve => setImmediate(resolve));
function profile(status, analyzed = 0, member = "") {
  return {
    account: "fixture-account", username: member || "fixture-friend", isGroup: false,
    stats: { analyzedCount: analyzed, textCount: 6 },
    dataStatus: analyzed === 6 ? "analyzed" : "partial",
    job: { id: "fixture-job", status, checkpointComplete: status === "done" },
  };
}
function harness(apiImpl) {
  const nodes = new Map(), timers = new Map(), calls = [], rendered = [], remembered = [];
  let serial = 0, now = 0;
  const byId = id => {
    if (!nodes.has(id)) nodes.set(id, { textContent: "", hidden: false, style: {}, title: "" });
    return nodes.get(id);
  };
  const context = vm.createContext({
    AbortController, console,
    byId, text: (id, value) => { byId(id).textContent = String(value ?? ""); },
    setStripStatus: value => { byId("stripStatusText").textContent = value; },
    syncPortraitMode: () => context.settingsState.modelSourceSnapshot.mode === "api",
    loadApiPortrait: member => { calls.push({ apiPortrait: member }); },
    cachedProfileFor: () => null,
    renderProfile: value => rendered.push(value),
    clearProfileView() {}, rememberProfileMember() {},
    rememberProfile: value => remembered.push(value),
    acceptResponseAccount: account => account === context.chatState.currentAccount,
    handleAccountBoundaryError: () => false,
    status: (target, value) => { target.textContent = value; },
    api: async (...args) => { calls.push({ url: args[0] }); return apiImpl(...args); },
    performance: { now: () => now },
    setTimeout: (callback, delay) => { const id = ++serial; timers.set(id, { callback, delay }); return id; },
    clearTimeout: id => timers.delete(id),
    // Replaying a stale analysis snapshot is the original defect: it must never run here.
    renderJob: () => { throw new Error("loadProfile replayed a stale conversation job"); },
  });
  seed(context, {
    sessions: new Map([["fixture-friend", { name: "Fixture", isGroup: false }]]),
    selectedConversations: new Set(["fixture-friend"]),
    currentAccount: "fixture-account", currentUser: "fixture-friend", activeMember: "",
    view: "persona", modelSourceResolved: true,
    modelSourceSnapshot: { mode: "local", sourceId: "local:laya" },
    controller: new AbortController(), profileGeneration: 0, profilePending: false,
    profileCache: new Map(), profileSnapshotsRequireRefresh: new Set(),
    profileRateSamples: new Map(), renderedProfileKey: null, renderedProfileSignature: null,
    currentAnalysisJob: { status: "done", checkpointComplete: true },
  });
  vm.runInContext(code, context);
  async function fireNext() {
    const next = timers.entries().next();
    assert.equal(next.done, false, "Expected a pending profile poll");
    const [id, timer] = next.value;
    timers.delete(id);
    now += timer.delay;
    timer.callback();
    await tick();
  }
  return { ui: context.ui, context, byId, timers, calls, rendered, remembered, fireNext };
}

it("polls actual local loadProfile through queued, running and done, then stops at 6/6", async () => {
  const replies = [profile("queued"), profile("running", 3), profile("done", 6)];
  const h = harness(async () => replies.shift());
  await h.ui.loadProfile();
  assert.equal(h.byId("stripConfidence").textContent, "0 / 6 条文本");
  assert.equal(h.byId("stripStatusText").textContent, "画像排队中");
  assert.equal(h.timers.size, 1);
  assert.equal([...h.timers.values()][0].delay, 1500);
  await h.fireNext();
  assert.equal(h.byId("stripConfidence").textContent, "3 / 6 条文本");
  assert.equal(h.timers.size, 1);
  await h.fireNext();
  assert.equal(h.byId("stripConfidence").textContent, "6 / 6 条文本");
  assert.equal(h.byId("stripStatusText").textContent, "已完成");
  assert.equal(h.timers.size, 0);
  assert.equal(h.context.portraitState.localProfilePollTimer, null);
  assert.equal(h.context.portraitState.profilePending, false);
  assert.equal(h.calls.length, 3);
  assert.deepEqual(h.calls.map(call => call.url), Array(3).fill("/api/profile?user=fixture-friend"));
  assert.deepEqual(h.rendered.map(value => value.job.status), ["queued", "running", "done"]);
  const saved = [...h.context.portraitState.profileCache.values()][0];
  assert.equal(saved.job.status, "done");
});

for (const [name, mutate] of [
  ["account", c => { c.chatState.currentAccount = "fixture-other-account"; }],
  ["conversation", c => { c.chatState.currentUser = "fixture-other-friend"; }],
  ["member", c => { c.portraitState.activeMember = "fixture-member"; }],
  ["model source", c => { c.settingsState.modelSourceSnapshot.sourceId = "other-source"; }],
  ["API mode", c => { c.settingsState.modelSourceSnapshot.mode = "api"; }],
  ["unresolved source", c => { c.settingsState.modelSourceResolved = false; }],
  ["closed portrait", c => { c.chatState.view = "chat"; }],
  ["newer read", c => { c.portraitState.profileGeneration++; }],
]) {
  it(`does not issue a queued local poll after switching ${name}`, async () => {
    const h = harness(async () => profile("queued"));
    await h.ui.loadProfile();
    mutate(h.context);
    await h.fireNext();
    assert.equal(h.calls.length, 1);
    assert.equal(h.timers.size, 0);
    assert.equal(h.context.portraitState.localProfilePollTimer, null);
  });
}

for (const [name, mutate] of [
  ["account", c => { c.chatState.currentAccount = "fixture-other-account"; }],
  ["conversation", c => { c.chatState.currentUser = "fixture-other-friend"; }],
  ["member", c => { c.portraitState.activeMember = "fixture-member"; }],
  ["source", c => { c.settingsState.modelSourceSnapshot.sourceId = "other-source"; }],
  ["API mode", c => { c.settingsState.modelSourceSnapshot.mode = "api"; }],
  ["unresolved source", c => { c.settingsState.modelSourceResolved = false; }],
  ["newer read", c => { c.portraitState.profileGeneration++; }],
]) {
  it(`ignores the late local profile response after switching ${name}`, async () => {
    let release;
    const h = harness(() => new Promise(resolve => { release = resolve; }));
    const read = h.ui.loadProfile();
    mutate(h.context);
    release(profile("queued"));
    await read;
    assert.equal(h.rendered.length, 0);
    assert.equal(h.remembered.length, 0);
    assert.equal(h.context.portraitState.profileCache.size, 0);
    assert.equal(h.timers.size, 0);
  });
}

it("cancels scheduled local polling before handing an API portrait to its own loader", async () => {
  const h = harness(async () => profile("queued"));
  await h.ui.loadProfile();
  assert.equal(h.timers.size, 1);
  h.context.settingsState.modelSourceSnapshot = { mode: "api", sourceId: "fixture-api" };
  await h.ui.loadProfile();
  assert.equal(h.timers.size, 0);
  assert.equal(h.context.portraitState.profilePending, false);
  assert.deepEqual(h.calls, [{ url: "/api/profile?user=fixture-friend" }, { apiPortrait: "" }]);
});

it("explicit cancellation discards the timer without fetching or mutating saved results", async () => {
  const h = harness(async () => profile("queued"));
  await h.ui.loadProfile();
  const saved = [...h.context.portraitState.profileCache.values()][0];
  h.ui.cancelLocalProfilePoll();
  assert.equal(h.timers.size, 0);
  assert.equal(h.context.portraitState.localProfilePollTimer, null);
  assert.equal(h.calls.length, 1);
  assert.equal([...h.context.portraitState.profileCache.values()][0], saved);
});

it("selecting the already-open conversation keeps its queued profile poll alive", async () => {
  const h = harness(async () => profile("queued"));
  h.context.chatState.messageSourceReady = true;
  h.context.localStorage = { setItem() {} };
  h.context.markSessionAsRead = () => {};
  h.context.renderSessions = () => {};
  // The real navigation prefix includes its same-user return and the timer cancellation.
  const start = source.indexOf("function switchSession(");
  const end = source.indexOf("  cacheCurrentSession(", start);
  assert.ok(start >= 0 && end > start);
  vm.runInContext(source.slice(start, end) + "}\nglobalThis.sameSessionCheck = switchSession;", h.context);
  await h.ui.loadProfile();
  const timer = h.context.portraitState.localProfilePollTimer;
  h.context.sameSessionCheck("fixture-friend");
  assert.equal(h.context.portraitState.localProfilePollTimer, timer);
  assert.equal(h.timers.size, 1);
  assert.equal(h.calls.length, 1);
  await h.fireNext();
  assert.equal(h.calls.length, 2);
  assert.equal(h.timers.size, 1);
});

it("stops polling on a terminal backend error and shows the retry state", async () => {
  const h = harness(async () => profile("error", 3));
  await h.ui.loadProfile();
  assert.equal(h.timers.size, 0);
  assert.equal(h.byId("btnRetryProfile").hidden, false);
  assert.equal(h.byId("stripStatusText").textContent, "画像分析未完成，可重试或重置画像");
});

it("stops polling after a network failure while keeping the previously rendered snapshot", async () => {
  let calls = 0;
  const h = harness(async () => {
    if (++calls === 1) return profile("queued", 3);
    throw new Error("synthetic connection failure");
  });
  await h.ui.loadProfile();
  await h.fireNext();
  assert.equal(h.byId("stripStatusText").textContent, "画像刷新失败");
  assert.equal(h.context.portraitState.profilePending, false);
  assert.equal(h.timers.size, 0);
  assert.equal(h.rendered.length, 1);
  assert.equal([...h.context.portraitState.profileCache.values()][0].stats.analyzedCount, 3);
});
