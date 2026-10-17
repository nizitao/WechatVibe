"use strict";

// Test-only wrapper. The normal desktop, recovery and shutdown paths are reused
// from an isolated source tree whose chat_server is permanently synthetic.
const fs = require("node:fs");
const path = require("node:path");
const { app, ipcMain } = require("electron");
const root = path.resolve(__dirname, "..");

function assertPlain(target) {
  let cursor = target;
  while (true) {
    if (fs.existsSync(cursor) && fs.lstatSync(cursor).isSymbolicLink()) {
      throw new Error("Synthetic client data paths cannot traverse links");
    }
    const parent = path.dirname(cursor);
    if (parent === cursor) break;
    cursor = parent;
  }
}

assertPlain(root);
assertPlain(path.join(root, ".synthetic-client.json"));
const marker = JSON.parse(fs.readFileSync(path.join(root, ".synthetic-client.json"), "utf8"));
if (marker.schema !== 1 || marker.mode !== "synthetic" || typeof marker.sourceRoot !== "string" ||
    path.resolve(marker.sourceRoot, ".local/test-client/client").toLowerCase() !== root.toLowerCase()) {
  throw new Error("Synthetic client identity is invalid");
}
assertPlain(path.join(root, ".local"));

const inheritedPython = process.env.WECHATVIBE_PYTHON;
const inheritedNode = process.env.WECHATVIBE_NODE;
const keep = new Set(["SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "PATHEXT", "SYSTEMDRIVE",
  "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER", "NUMBER_OF_PROCESSORS", "USERNAME", "USERDOMAIN", "OS"]);
for (const key of Object.keys(process.env)) {
  if (!keep.has(key.toUpperCase())) delete process.env[key];
}
for (const [key, relative] of Object.entries({ TEMP: "temp", TMP: "temp", LOCALAPPDATA: "Local",
  APPDATA: "Roaming", USERPROFILE: "user", HOME: "user" })) {
  const directory = path.join(root, ".local/test-os", relative);
  assertPlain(directory);
  fs.mkdirSync(directory, { recursive: true });
  process.env[key] = directory;
}
Object.assign(process.env, { WECHATVIBE_CLIENT_ROOT: root, WECHATVIBE_PROFILE: "synthetic",
  WECHATVIBE_SYNTHETIC: "1", PYTHONDONTWRITEBYTECODE: "1", PYTHONUTF8: "1" });
if (inheritedPython && path.isAbsolute(inheritedPython)) process.env.WECHATVIBE_PYTHON = inheritedPython;
if (inheritedNode && path.isAbsolute(inheritedNode)) process.env.WECHATVIBE_NODE = inheritedNode;
// An inherited --profile must never turn this into the default updating instance.
process.argv = process.argv.filter((argument, index, args) => argument !== "--profile" &&
  args[index - 1] !== "--profile" && !argument.startsWith("--profile="));
process.argv.push("--profile=synthetic");
app.setName("WechatVibe 测试数据");

// Disable host actions that could leave the synthetic environment. Their normal
// implementation stays untouched in production, and backend guards are separate.
const handle = ipcMain.handle.bind(ipcMain);
const blocked = new Map([
  ["real-client:model-download", { phase: "blocked" }],
  ["real-client:model-choose-directory", null],
  ["real-client:data-root-choose-directory", null],
  ["real-client:check-updates", { status: "blocked" }],
  ["real-client:begin-update", { phase: "blocked" }],
  ["real-client:rollback-update", { phase: "blocked" }],
]);
ipcMain.handle = (channel, listener) => handle(channel,
  blocked.has(channel) ? () => blocked.get(channel) : listener);
app.on("browser-window-created", (_event, window) => {
  const title = "WechatVibe · 测试数据（独立环境）";
  window.setTitle(title);
  window.webContents.on("page-title-updated", (event) => { event.preventDefault(); window.setTitle(title); });
  window.webContents.on("did-finish-load", () => {
    void window.webContents.executeJavaScript(`(() => {
      document.title = ${JSON.stringify(title)};
      if (!document.getElementById('syntheticDataBadge')) {
        const badge = document.createElement('div');
        badge.id = 'syntheticDataBadge';
        badge.textContent = '测试数据 · 独立环境';
        badge.setAttribute('role', 'note');
        badge.style.cssText = 'position:fixed;right:160px;top:8px;z-index:2147483646;pointer-events:none;padding:3px 9px;border-radius:6px;background:#284951;color:#ecf7f8;font:12px sans-serif;opacity:.9';
        document.body.appendChild(badge);
      }
    })()`).catch(() => {});
  });
});
require("./desktop-main.cjs");
