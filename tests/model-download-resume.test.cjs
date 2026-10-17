"use strict";
const assert = require("node:assert/strict");
const test = require("node:test");
const fs = require("node:fs");
const path = require("node:path");
const crypto = require("node:crypto");
const { Readable } = require("node:stream");
const { setTimeout: delay } = require("node:timers/promises");
const { ModelDownload } = require("../scripts/real-client-model.cjs");

function fixture(t, fetchImpl, extra = {}) {
  const base = path.resolve(__dirname, "../.local/implementation-tests");
  fs.mkdirSync(base, { recursive: true });
  const root = fs.mkdtempSync(path.join(base, "download-"));
  t.after(() => {
    assert.equal(path.dirname(fs.realpathSync(root)), fs.realpathSync(base));
    fs.rmSync(root, { recursive: true, force: true });
  });
  fs.mkdirSync(path.join(root, "scripts"));
  fs.copyFileSync(path.resolve(__dirname, "../scripts/model-asset.json"), path.join(root, "scripts/model-asset.json"));
  const bytes = Buffer.from("synthetic pinned model body, no real model or download");
  const installed = [];
  const download = new ModelDownload({ root, python: "unused", fetchImpl,
    installImpl: async (_python, _script, archive) => {
      assert.deepEqual(fs.readFileSync(archive), bytes);
      installed.push(true);
    }, ...extra });
  download.asset = { ...download.asset, bytes: bytes.length,
    sha256: crypto.createHash("sha256").update(bytes).digest("hex") };
  const parts = () => fs.readdirSync(path.join(root, ".local/model-downloads")).filter(n => n.endsWith(".part"));
  return { root, download, bytes, installed, parts };
}

test("interrupted download retains its prefix and resumes the exact pinned asset", async t => {
  let f, calls = 0;
  f = fixture(t, async (_url, options) => {
    calls++;
    if (calls === 1) {
      const stream = Readable.toWeb(Readable.from((async function* () {
        yield f.bytes.subarray(0, 12);
        await delay(30);
        throw new Error("synthetic network interruption");
      })()));
      return new Response(stream, { status: 200, headers: { "content-length": String(f.bytes.length) } });
    }
    assert.equal(options.headers.Range, "bytes=12-");
    return new Response(f.bytes.subarray(12), { status: 206,
      headers: { "content-range": `bytes 12-${f.bytes.length - 1}/${f.bytes.length}`,
        "content-length": String(f.bytes.length - 12) } });
  });
  assert.equal((await f.download.start()).phase, "failed");
  assert.equal(f.parts().length, 1);
  assert.equal((await f.download.start()).phase, "ready");
  assert.equal(f.installed.length, 1);
  assert.deepEqual(f.parts(), []);
});

test("server ignoring Range replaces partial content rather than appending it", async t => {
  let f, calls = 0;
  f = fixture(t, async (_url, options) => {
    calls++;
    if (calls === 1) return new Response(f.bytes.subarray(0, 5), { status: 200 });
    assert.equal(options.headers.Range, "bytes=5-");
    return new Response(f.bytes, { status: 200 });
  });
  assert.equal((await f.download.start()).phase, "failed");
  assert.equal((await f.download.start()).phase, "ready");
  assert.equal(f.installed.length, 1);
});

test("incorrect range never appends bytes or discards a valid prefix", async t => {
  let f, calls = 0;
  f = fixture(t, async () => {
    if (++calls === 1) return new Response(f.bytes.subarray(0, 5), { status: 200 });
    return new Response(f.bytes.subarray(4), { status: 206,
      headers: { "content-range": `bytes 4-${f.bytes.length - 1}/${f.bytes.length}` } });
  });
  await f.download.start();
  assert.match((await f.download.start()).error, /range/);
  assert.deepEqual(fs.readFileSync(path.join(f.root, ".local/model-downloads", f.parts()[0])), f.bytes.subarray(0, 5));
  assert.equal(f.installed.length, 0);
});

test("a full bad checksum is discarded and never installed", async t => {
  let f;
  f = fixture(t, async () => new Response(Buffer.alloc(f.bytes.length, 42), { status: 200 }));
  assert.match((await f.download.start()).error, /checksum/);
  assert.deepEqual(f.parts(), []);
  assert.equal(f.installed.length, 0);
});

test("verified completed download retries installation without downloading again", async t => {
  let f, requests = 0, attempts = 0;
  f = fixture(t, async () => { requests++; return new Response(f.bytes, { status: 200 }); }, {
    installImpl: async (_p, _s, archive) => {
      assert.deepEqual(fs.readFileSync(archive), f.bytes);
      if (++attempts === 1) throw new Error("synthetic install failure");
    },
  });
  assert.equal((await f.download.start()).phase, "failed");
  assert.equal((await f.download.start()).phase, "ready");
  assert.equal(requests, 1);
  assert.equal(attempts, 2);
});

test("explicit cancel does not trigger fallback or an extra request", async t => {
  let f, requests = 0, fallback = 0;
  f = fixture(t, async (_url, { signal }) => {
    requests++;
    setImmediate(() => f.download.cancel());
    return new Promise((_resolve, reject) => signal.addEventListener("abort", () => reject(new Error("cancelled")), { once: true }));
  }, { enableFallback: async () => { fallback++; return true; } });
  assert.equal((await f.download.start()).phase, "failed");
  assert.equal(requests, 1);
  assert.equal(fallback, 0);
});
