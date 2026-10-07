const assert = require("node:assert/strict");

const { readFileSync } = require("node:fs");
const path = require("node:path");
const { it } = require("node:test");

const app = readFileSync(path.join(__dirname, "..", "chatui", "app.js"), "utf8").replace(/\r/gu, "");
const viewState = readFileSync(path.join(__dirname, "..", "chatui", "view-state.js"), "utf8").replace(/\r/gu, "");
const harness = readFileSync(path.join(__dirname, "helpers", "view-state-harness.cjs"), "utf8").replace(/\r/gu, "");

it("keeps the analyse request list separate from the added-conversation list", () => {
  assert.match(viewState, /requestedConversations: new Set\(\)/u);
  // The vm harness maps every bare field name onto a ViewState domain; a field it does not
  // know about would silently read as undefined inside the UI tests.
  assert.match(harness, /"requestedConversations"/u);
  // Adding a conversation to the list is not a request to analyse it.
  assert.match(app, /const order = \[\.\.\.chatState\.requestedConversations\]/u);
  assert.ok(!/const order = \[\.\.\.chatState\.selectedConversations\]/u.test(app),
    "the sweep must not walk every added conversation");
});

it("puts a per-card analyse button left of the contact name", () => {
  assert.match(app,
    /top\.append\(analyseBtn, element\("span", "session-name"\), element\("span", "session-time"\)\)/u,
    "the button has to come before the name");
  assert.match(app, /analyseBtn\.dataset\.action = "analyze"/u);
  // Clicking the button must not also switch conversations.
  assert.match(app, /event\.stopPropagation\(\);\s*\n\s*void requestConversationAnalysis/u);
});

it("gives the button two distinguishable states", () => {
  assert.match(app, /const label = requested \? "已加入分析" : "开始分析"/u);
  assert.match(app, /analyseBtn\.classList\.toggle\("requested", requested\)/u);
  assert.match(app, /analyseBtn\.ariaPressed = String\(requested\)/u);
});

it("analyses the clicked conversation immediately, not only via the background switch", () => {
  // The sweep is gated on the background switch; the per-card click must not be, otherwise
  // pressing the button would do nothing until the switch was turned on.
  const click = app.slice(app.indexOf("async function requestConversationAnalysis("),
    app.indexOf("async function analyzeConversations("));
  assert.match(click, /await analyzeConversations\(\[username\]\)/u);
  assert.ok(!/backgroundAnalyzeAll\(\)/u.test(click), "the click must not rely on the sweep");
  const sweep = app.slice(app.indexOf("async function backgroundAnalyzeAll("),
    app.indexOf("// Messages per conversation an API sweep asks for"));
  assert.match(sweep, /!settingsState\.settings\.backgroundAnalyze/u,
    "the sweep still needs the background switch");
});

it("toggles a conversation off the list when the button is pressed again", () => {
  assert.match(app,
    /if \(requested\) chatState\.requestedConversations\.delete\(username\);\s*\n\s*else chatState\.requestedConversations\.add\(username\);/u);
});