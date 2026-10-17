// Account-pin copy: which setup message a locked window shows when its account is not ready.
// No DOM and no bridge: the two pure helpers are sliced out of app.js, like the other UI tests.
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");

const source = readFileSync(path.join(__dirname, "../chatui/app.js"), "utf8");

function slice(startMarker, endMarker) {
  const start = source.indexOf(startMarker);
  const end = source.indexOf(endMarker, start);
  assert.ok(start >= 0, `missing ${startMarker}`);
  assert.ok(end > start, `missing ${endMarker}`);
  return source.slice(start, end);
}

const pinHelpers = slice("function accountPinNeedsChoice(", "function formatBytes(");
const labelHelper = slice("function accountPinLabel(", "function renderAccountPin(");

const context = vm.createContext({});
vm.runInContext(`${labelHelper}\n${pinHelpers}\nthis.api = { accountPinNeedsChoice, accountUnavailableMessage, accountPinLabel };`,
  context);
const { accountPinNeedsChoice, accountUnavailableMessage } = context.api;

const pinned = {
  state: "pinned-missing",
  pinned: { accountDir: "C:\\xwechat_files\\wxid_locked", account: "wxid_locked",
    wechatId: "wxid_locked", label: "nickname-locked" },
  live: [],
};

test("several live accounts are offered as a choice", () => {
  const data = { state: "unpinned-ambiguous", pinned: null, live: [{}] };
  assert.equal(accountPinNeedsChoice(data), true);
  assert.equal(accountUnavailableMessage(data), "检测到多个微信账号，请选择本窗口使用的账号");
});

test("a logged-out locked account is never described as several accounts", () => {
  // The pinned account can be the only one logged out; telling the user that several accounts
  // were detected invites them to switch, which must stay an explicit decision.
  assert.equal(accountPinNeedsChoice(pinned), true);
  const message = accountUnavailableMessage(pinned);
  assert.doesNotMatch(message, /多个微信账号/);
  assert.match(message, /未登录/);
  // The locked account is named, so the user can tell which login is missing.
  assert.match(message, /nickname-locked/);
});

test("an ordinary not-ready state keeps the neutral message", () => {
  for (const state of ["unpinned-none", "invalid", undefined]) {
    const data = state === undefined ? null : { state, pinned: null, live: [] };
    assert.equal(accountPinNeedsChoice(data), false, String(state));
    assert.equal(accountUnavailableMessage(data), "当前微信账号未就绪", String(state));
  }
});

const { dom } = require("./helpers/synthetic-dom.cjs");
function pickerHarness(api, fetch) {
  const ui = dom(), effects = [];
  ui.byId("accountPicker").hidden = true;
  const context = vm.createContext({ ...ui, api, fetch,
    resetAccountView: () => effects.push("reset"), loadSessions: () => effects.push("sessions") });
  vm.runInContext(slice("let accountPinRequest =", "async function loadAccounts()") +
    "\nthis.fns = { openAccountPicker, chooseAccountPin, clearAccountPin };", context);
  return { ...ui, context, fns: context.fns, effects };
}
test("the account picker is above the startup overlay and exposes its pinned state", async () => {
  const css = readFileSync(path.join(__dirname, "../chatui/style.css"), "utf8");
  const pickerZ = Number(css.match(/\.account-picker\s*\{[^}]*z-index:\s*(\d+)/)?.[1]);
  const startupZ = Number(css.match(/\.startup-overlay\s*\{[^}]*z-index:\s*(\d+)/)?.[1]);
  assert.ok(pickerZ > startupZ, "startup account selection must stay above its startup blocker");
  const data = { ...pinned, live: [{ ...pinned.pinned, bytes: 1024 }] };
  const run = pickerHarness(async () => data);
  await run.fns.openAccountPicker();
  assert.equal(run.byId("accountPicker").hidden, false);
  assert.equal(run.byId("btnCancelAccountPicker").focused, true);
  const row = run.byId("accountPickerList").children[0];
  assert.equal(row.attributes["aria-pressed"], "true");
  assert.match(row.textContent, /nickname-locked.*wxid_locked.*1.0 KB.*正在使用/);
  await run.fns.openAccountPicker(false);
  assert.equal(run.document.activeElement.focused, true);
});
test("late account reads cannot overwrite a newer picker or reopen one that closed", async () => {
  const resolvers = [];
  const run = pickerHarness(() => new Promise(resolve => resolvers.push(resolve)));
  const first = run.fns.openAccountPicker();
  const second = run.fns.openAccountPicker();
  resolvers[1]({ state: "unpinned-one", live: [{ accountDir: "synthetic-b", label: "新账号" }] });
  await second;
  resolvers[0](pinned);
  await first;
  assert.match(run.byId("accountPickerList").textContent, /新账号/);
  assert.doesNotMatch(run.byId("accountPickerList").textContent, /读取失败/);
  const third = run.fns.openAccountPicker();
  await run.fns.openAccountPicker(false);
  resolvers[2](pinned);
  await third;
  assert.equal(run.byId("accountPicker").hidden, true);
});
test("a refused lease keeps the current account while successful pinning resets every view", async () => {
  for (const ok of [false, true]) {
    const posts = [];
    const run = pickerHarness(async () => pinned, async (url, options) => {
      posts.push({ url, body: JSON.parse(options.body) });
      return { ok, json: async () => ok ? pinned : { error: "另一个实例正在使用此账号" } };
    });
    await run.fns.openAccountPicker();
    await run.fns.chooseAccountPin("synthetic-account-dir");
    assert.deepEqual(posts, [{ url: "/api/account-pin", body: { accountDir: "synthetic-account-dir" } }]);
    assert.deepEqual(run.effects, ok ? ["reset", "sessions"] : []);
    assert.equal(run.byId("accountPicker").hidden, ok);
    if (!ok) assert.match(run.byId("accountPickerStatus").textContent, /另一个实例/);
  }
});
