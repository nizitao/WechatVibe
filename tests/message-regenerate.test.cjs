"use strict";
// PR #22 efcd892: preserve the author's menu/request cases and exercise real job completion.
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const { it } = require("node:test");
const vm = require("node:vm");
const app = readFileSync(path.join(__dirname, "../chatui/app.js"), "utf8");
const html = readFileSync(path.join(__dirname, "../chatui/index.html"), "utf8");
const START = "// Right-click a bubble to recompute that one message.";
const END = 'byId("chatMessages").addEventListener("scroll", closeMessageMenu);';
const first = app.indexOf(START), last = app.indexOf(END, first);
assert.ok(first >= 0 && last > first);

function harness({ mode = "local", fail = false, statuses = ["queued", "running", "done"], getJob } = {}) {
  const nodes = new Map(), posts = [], gets = [], toasts = [], loadCalls = [];
  const byId = id => {
    if (!nodes.has(id)) nodes.set(id, { id, hidden: true, disabled: false, dataset: {}, style: {},
      offsetWidth: 132, offsetHeight: 36, addEventListener() {} });
    return nodes.get(id);
  };
  const saved = { id: "m2", status: "ok", intents: ["合成"] };
  const backgroundJob = { id: "background", status: "running", processed: 4, total: 9 };
  const entry = { results: { m2: saved }, job: backgroundJob, requestedSignature: "existing" };
  const cacheKey = JSON.stringify(["acct", "friend", mode === "api" ? "source-v1" : "local:laya"]);
  const context = vm.createContext({
    URLSearchParams, AbortController,
    chatState: { currentAccount: "acct", currentUser: "friend", generation: 1, historyState: null,
      messages: [{ id: "m1", side: "self", kind: "text" }, { id: "m2", side: "other", kind: "text" }],
      controller: { signal: null } },
    settingsState: { modelSourceResolved: true, modelSourceSnapshot: { mode,
      sourceId: mode === "api" ? "source-v1" : "local:laya" }, suppressedApiSources: new Set(), suppressedLocalAccounts: new Set() },
    labelState: { results: { m2: saved }, apiInsightCache: new Map([[cacheKey, entry]]) },
    byId, refreshLabels() {}, canAnalyzeLocal: () => true,
    pickableMessage: message => message.side === "other" && message.kind === "text",
    apiInsightKey: (...parts) => JSON.stringify(parts), validApiInsight: value => value?.status === "ok",
    document: { addEventListener() {} }, window: { innerWidth: 1200, innerHeight: 800 },
    toast: value => toasts.push(value),
    setTimeout: callback => { callback(); return 1; }, clearTimeout() {},
    async api(url, options = {}) {
      if (fail) throw new Error("synthetic failure");
      if (options.method === "POST") {
        posts.push({ url, body: JSON.parse(options.body) });
        return { job: { id: "job-1", status: "queued", total: 1, processed: 0 } };
      }
      gets.push(url);
      if (url.startsWith("/api/analysis-job?")) {
        if (getJob) return getJob(context);
        return { account: "acct", user: "friend", sourceId: context.settingsState.modelSourceSnapshot.sourceId,
          job: { id: "job-1", status: statuses.shift() || "done" } };
      }
      if (url.startsWith("/api/model-insights?")) return { account: "acct", sourceId: "source-v1",
        results: { m2: saved }, job: { id: "different-background-job" } };
      throw new Error("unexpected GET " + url);
    },
    async loadAnalysis(user, token, signal) { loadCalls.push({ user, token, signal }); },
  });
  vm.runInContext(app.slice(first, last + END.length) +
    "\nglobalThis.fns = { openMessageMenu, closeMessageMenu, regenerateMessage, cancelMessageRegeneration };", context);
  return { context, fns: context.fns, byId, posts, gets, toasts, loadCalls, entry, backgroundJob };
}

it("ships the per-message menu and clamps it inside the viewport", () => {
  assert.match(html, /id="msgMenu"[\s\S]{0,220}id="btnRegenerateMessage"/u);
  assert.match(html, /id="btnRegenerateMessage"[^>]*>重新生成测评<\/button>/u);
  const run = harness();
  run.fns.openMessageMenu({ clientX: 40, clientY: 60 }, "m2");
  assert.equal(run.byId("msgMenu").hidden, false);
  assert.equal(run.byId("msgMenu").dataset.messageId, "m2");
  assert.equal(run.byId("msgMenu").style.left, "40px");
  assert.equal(run.byId("msgMenu").style.top, "60px");
  run.fns.openMessageMenu({ clientX: 1200, clientY: -5 }, "m2");
  assert.equal(run.byId("msgMenu").style.left, "1060px");
  assert.equal(run.byId("msgMenu").style.top, "8px");
  run.fns.closeMessageMenu();
  assert.equal(run.byId("msgMenu").hidden, true);
});

for (const mode of ["local", "api"]) it(`${mode}: waits for the exact job, accepting identical saved output as success`, async () => {
  const run = harness({ mode });
  await run.fns.regenerateMessage("m2");
  assert.deepEqual(run.posts, [{ url: "/api/analyze", body: {
    account: "acct", user: "friend", mode: "message", messageId: "m2", limit: 80 } }]);
  assert.equal(run.gets.filter(url => url === "/api/analysis-job?jobId=job-1").length, 3);
  assert.equal(run.toasts.at(-1), "这条消息的测评已重新生成");
  assert.equal(run.byId("btnRegenerateMessage").disabled, false);
  assert.equal(run.loadCalls.length, mode === "local" ? 1 : 0);
  assert.equal(run.entry.job, run.backgroundJob, "force must not replace the background job display");
  assert.equal(run.entry.requestedSignature, "existing");
  if (mode === "api") assert.deepEqual(JSON.parse(new URL(run.gets.at(-1), "https://synthetic.test").searchParams.get("ids")), ["m2"]);
});

it("reports submission or terminal failure and permits retry", async () => {
  for (const options of [{ fail: true }, { statuses: ["error"] }]) {
    const run = harness(options);
    await run.fns.regenerateMessage("m2");
    assert.equal(run.toasts.at(-1), "重新生成失败，请稍后重试");
    assert.equal(run.byId("btnRegenerateMessage").disabled, false);
    assert.equal(run.loadCalls.length, 0);
  }
});

it("rejects unrelated terminal jobs without using them as completion", async () => {
  for (const change of [{ account: "other" }, { user: "other" }, { sourceId: "other" },
    { job: { id: "background", status: "done" } }]) {
    const run = harness({ getJob: () => ({ account: "acct", user: "friend", sourceId: "local:laya",
      job: { id: "job-1", status: "done" }, ...change }) });
    await run.fns.regenerateMessage("m2");
    assert.equal(run.loadCalls.length, 0);
    assert.equal(run.toasts.at(-1), "重新生成失败，请稍后重试");
  }
});

it("ignores an old terminal response after switching away and back", async () => {
  let resolve;
  const run = harness({ getJob: () => new Promise(done => { resolve = done; }) });
  const waiting = run.fns.regenerateMessage("m2");
  await new Promise(setImmediate);
  assert.equal(typeof resolve, "function");
  run.fns.cancelMessageRegeneration();
  run.context.chatState.generation += 2;
  resolve({ account: "acct", user: "friend", sourceId: "local:laya", job: { id: "job-1", status: "done" } });
  await waiting;
  assert.equal(run.loadCalls.length, 0);
  assert.equal(run.toasts.length, 1);
  assert.equal(run.byId("btnRegenerateMessage").disabled, false);
});

it("does not regenerate self messages, history windows, suspended sources or unresolved models", async () => {
  for (const [messageId, setup] of [["m1", () => {}], ["m2", run => { run.context.chatState.historyState = {}; }],
    ["m2", run => { run.context.settingsState.suppressedLocalAccounts.add("acct"); }],
    ["m2", run => { run.context.settingsState.modelSourceResolved = false; }]]) {
    const run = harness();
    setup(run);
    await run.fns.regenerateMessage(messageId);
    assert.equal(run.posts.length, 0);
  }
});
