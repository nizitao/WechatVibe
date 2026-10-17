"use strict";
// A packaged script may only require files that electron-builder copies into app.asar.
// 1.3.5 was blocked because desktop-main.cjs required ./runtime-paths.cjs while the
// allowlist still listed only the pre-profile scripts, so the packaged app could not
// start at all. This keeps the allowlist and the requires in step.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { it } = require("node:test");

const ROOT = path.join(__dirname, "..");
const config = JSON.parse(fs.readFileSync(path.join(ROOT, "electron-builder.real-client.json"), "utf8"));
const LOCAL_REQUIRE = /require\((['"])\.\/([^'"]+)\1\)/g;

it("every script the packaged app requires is inside the app.asar allowlist", () => {
  const listed = config.files.filter(name => !name.startsWith("!"));
  const scripts = listed.filter(name => name.startsWith("scripts/"));
  assert.ok(scripts.length > 0, "the asar allowlist must list the desktop scripts");
  const missing = new Set();
  for (const relative of scripts) {
    const file = path.join(ROOT, relative);
    assert.ok(fs.existsSync(file), "allowlisted script is missing: " + relative);
    for (const match of fs.readFileSync(file, "utf8").matchAll(LOCAL_REQUIRE)) {
      const dependency = "scripts/" + match[2];
      if (!listed.includes(dependency)) missing.add(dependency);
    }
  }
  assert.deepEqual([...missing].sort(), [],
    "a packaged script requires a file the allowlist would leave out of app.asar");
});
