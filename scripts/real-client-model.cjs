"use strict";

// Download only the pinned Release model asset; the Python installer verifies
// every archive member before placing it in update-preserved local storage.
const crypto = require("node:crypto");
const { execFile } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const { Readable, Transform } = require("node:stream");
const { pipeline } = require("node:stream/promises");

function modelAsset(root) {
  const file = path.join(root, "scripts", "model-asset.json");
  const asset = JSON.parse(fs.readFileSync(file, "utf8"));
  // App releases advance independently of this pinned model's file/hash revision.
  if (asset.schema !== 1 || asset.name !== "WechatVibe-Laya-model-v1.zip" ||
      !/^https:\/\/github\.com\/tswawa\/WechatVibe\/releases\/download\/v\d+\.\d+\.\d+\/WechatVibe-Laya-model-v1\.zip$/.test(asset.url) ||
      !Number.isSafeInteger(asset.bytes) || asset.bytes < 500_000_000 || asset.bytes > 800_000_000 ||
      !/^[a-f0-9]{64}$/.test(asset.sha256)) throw new Error("invalid pinned model asset");
  return asset;
}

function ownedDirectory(parent, name) {
  const target = path.join(parent, name);
  if (fs.existsSync(target)) {
    const info = fs.lstatSync(target);
    if (!info.isDirectory() || info.isSymbolicLink() ||
        path.resolve(fs.realpathSync.native(target)).toLowerCase() !== path.resolve(target).toLowerCase()) {
      throw new Error("model storage path is unsafe");
    }
  } else fs.mkdirSync(target);
  return target;
}

function installWithPython(python, script, archive, root) {
  return new Promise((resolve, reject) => {
    execFile(python, [script, archive, root],
      { cwd: root, windowsHide: true, timeout: 10 * 60 * 1000, maxBuffer: 8192 },
      (error, stdout) => {
        if (error) return reject(new Error("model archive validation or installation failed"));
        try {
          const result = JSON.parse(stdout);
          const expected = path.join(root, ".local", "models", "laya");
          if (result.installed !== true || path.resolve(result.path).toLowerCase() !== expected.toLowerCase()) {
            throw new Error("invalid model installer result");
          }
          resolve(result);
        } catch (cause) { reject(cause); }
      });
  });
}

class ModelDownload {
  constructor({ root, python, fetchImpl, enableFallback, onState, installImpl = installWithPython }) {
    this.root = path.resolve(root);
    this.python = python;
    this.fetchImpl = fetchImpl;
    this.enableFallback = enableFallback;
    this.installImpl = installImpl;
    this.onState = onState || (() => {});
    this.asset = modelAsset(this.root);
    this.state = { phase: "idle", received: 0, total: this.asset.bytes };
    this.inFlight = null;
    this.abort = null;
    this.cancelled = false;
  }

  publish(state) {
    this.state = state;
    this.onState({ ...state });
  }

  getState() { return { ...this.state }; }

  async downloadOnce(archive) {
    const controller = new AbortController();
    this.abort = controller;
    const timer = setTimeout(() => controller.abort(), 30 * 60 * 1000);
    try {
      let received = 0;
      if (fs.existsSync(archive)) {
        const stat = fs.lstatSync(archive);
        if (!stat.isFile() || stat.isSymbolicLink() || stat.nlink !== 1 ||
            path.resolve(fs.realpathSync.native(archive)).toLowerCase() !== path.resolve(archive).toLowerCase()) {
          throw new Error("model download path is unsafe");
        }
        received = stat.size;
        if (received > this.asset.bytes) {
          fs.unlinkSync(archive);
          received = 0;
        }
      }
      let hash = crypto.createHash("sha256");
      if (received) {
        for await (const chunk of fs.createReadStream(archive)) {
          if (this.cancelled) throw new Error("model download cancelled");
          hash.update(chunk);
        }
      }
      this.publish({ phase: "downloading", received, total: this.asset.bytes });
      if (received < this.asset.bytes) {
        const response = await this.fetchImpl(this.asset.url, {
          headers: { Accept: "application/octet-stream", "Accept-Encoding": "identity",
            "User-Agent": "WechatVibe-model", ...(received ? { Range: `bytes=${received}-` } : {}) },
          signal: controller.signal,
        });
        if (!response.ok || !response.body) throw new Error("model download unavailable");
        if (response.status === 206) {
          const range = /^bytes (\d+)-(\d+)\/(\d+)$/.exec(response.headers.get("content-range") || "");
          if (!range || Number(range[1]) !== received || Number(range[2]) !== this.asset.bytes - 1 ||
              Number(range[3]) !== this.asset.bytes) {
            await response.body.cancel();
            throw new Error("model download range changed");
          }
        } else if (response.status === 200) {
          // Some proxies ignore Range. Replace the partial file, never append a full body.
          received = 0;
          hash = crypto.createHash("sha256");
        } else {
          await response.body.cancel();
          throw new Error("model download response invalid");
        }
        const reported = response.headers.get("content-length");
        if (reported !== null && Number(reported) !== this.asset.bytes - received) {
          await response.body.cancel();
          throw new Error("model download size changed");
        }
        let lastReport = 0;
        const progress = new Transform({ transform: (chunk, _encoding, done) => {
          received += chunk.length;
          if (received > this.asset.bytes) return done(new Error("model download exceeded pinned size"));
          hash.update(chunk);
          if (Date.now() - lastReport > 250) {
            lastReport = Date.now();
            this.publish({ phase: "downloading", received, total: this.asset.bytes });
          }
          done(null, chunk);
        } });
        await pipeline(Readable.fromWeb(response.body), progress,
          fs.createWriteStream(archive, { flags: received ? "a" : "w" }),
          { signal: controller.signal });
      }
      if (received !== this.asset.bytes) throw new Error("model download incomplete; retry to resume");
      if (hash.digest("hex") !== this.asset.sha256) {
        fs.unlinkSync(archive);
        throw new Error("model download checksum mismatch");
      }
      if (this.cancelled) throw new Error("model download cancelled");
      this.publish({ phase: "installing", received, total: this.asset.bytes });
      await this.installImpl(this.python, path.join(this.root, "bridge", "model_install.py"),
        archive, this.root);
      this.publish({ phase: "ready", received, total: this.asset.bytes });
    } finally {
      clearTimeout(timer);
      this.abort = null;
    }
  }

  async start() {
    if (this.inFlight) return this.inFlight;
    this.cancelled = false;
    this.inFlight = (async () => {
      let archive;
      try {
        const local = ownedDirectory(this.root, ".local");
        const downloads = ownedDirectory(local, "model-downloads");
        const profile = crypto.createHash("sha256").update(process.env.WECHATVIBE_PROFILE || "default").digest("hex").slice(0, 16);
        archive = path.join(downloads, `.laya-${this.asset.sha256}-${profile}.zip.part`);
        this.publish({ phase: "downloading", received: 0, total: this.asset.bytes });
        try { await this.downloadOnce(archive); }
        catch (error) {
          if (this.cancelled || this.state.phase !== "downloading" || !this.enableFallback ||
              !await this.enableFallback()) throw error;
          await this.downloadOnce(archive);
        }
        return this.getState();
      } catch (error) {
        const stage = this.state.phase;
        this.publish({ phase: "failed", received: this.state.received, total: this.asset.bytes,
          stage,
          error: error instanceof Error ? error.message : "模型下载失败" });
        return this.getState();
      } finally {
        this.inFlight = null;
        if (this.state.phase === "ready" && archive && fs.existsSync(archive)) {
          try { fs.unlinkSync(archive); } catch (_) { /* Keep the failure state. */ }
        }
      }
    })();
    return this.inFlight;
  }

  cancel() { this.cancelled = true; this.abort?.abort(); }
}

module.exports = { ModelDownload, modelAsset, ownedDirectory };
