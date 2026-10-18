"use strict";
const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const { it } = require("node:test");

const read = (...parts) => readFileSync(path.join(__dirname, "..", ...parts), "utf8");
const app = read("chatui", "app.js");
const css = read("chatui", "style.css");

const toggle = target => `byId("${target}")?.classList?.toggle?.("ai-busy"`;
const count = fragment => app.split(fragment).length - 1;

it("keeps the phase text and rides the marker on the status node", () => {
  // The spinner is a class on the node the text already lives in, so no extra element has to be
  // kept in sync and every existing text write keeps working.
  assert.equal(count(toggle("apiPortraitStatus")), 6);
  assert.equal(count(toggle("guidanceStatus")), 2);
  assert.ok(app.includes('text("apiPortraitStatus", state);\n' + toggle("apiPortraitStatus")));
  assert.ok(app.includes('text("guidanceStatus", state);\n' + toggle("guidanceStatus")));
});

it("spins for portrait work and reply options, and stops on errors or hints", () => {
  assert.ok(app.includes('text("apiPortraitStatus", "正在读取会话消息");\n' + toggle("apiPortraitStatus") + ', !!(true));'));
  assert.ok(app.includes(toggle("apiPortraitStatus") + ', !!(running || job.status === "queued"'));
  assert.ok(app.includes('text("apiPortraitStatus", "画像读取失败，请稍后重试");\n' + toggle("apiPortraitStatus") + ', !!(false));'));
  assert.ok(app.includes(toggle("guidanceStatus") + ', !!(portraitState.guidanceRunning'));
});

it("marks intent recognition while a job runs and clears it otherwise", () => {
  assert.ok(app.includes("let statusBusy = false;"));
  assert.ok(app.includes("statusBusy = true;"));
  assert.ok(app.includes('node.classList?.toggle?.("ai-busy", statusBusy);'));
  assert.ok(app.includes('node.classList?.remove?.("ai-busy");'));
});

it("the spinner is a css animation that respects reduced motion", () => {
  assert.match(css, /\.ai-busy::before \{[^}]*border-right-color: transparent[^}]*animation: ai-busy-spin \.8s linear infinite/);
  assert.match(css, /@keyframes ai-busy-spin \{ to \{ transform: rotate\(360deg\); \} \}/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)[^}]*\.ai-busy::before \{ animation: none;/);
});
