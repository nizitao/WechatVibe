// Contract checks for the trained char-ngram relationship classifier.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";
import { it } from "node:test";

import {
  RELATIONSHIP_NB_LABELS,
  RELATIONSHIP_NB_VERSION,
  relationshipTextProbabilities,
} from "../electron/laya/relationship-nb";

const model = JSON.parse(readFileSync(
  path.resolve("electron/laya/relationship-nb-model.json"), "utf8")) as {
  version: string; corpusSha256: string; temperature: number;
};

it("ships a model trained from the current corpus", () => {
  const digest = createHash("sha256")
    .update(readFileSync(path.resolve("tests/fixtures/laya-affinity-corpus.jsonl")))
    .digest("hex");
  assert.equal(model.corpusSha256, digest, "corpus changed; rerun scripts/train-relationship-nb.py");
  assert.equal(RELATIONSHIP_NB_VERSION, model.version);
  assert.ok(model.temperature > 0 && Number.isFinite(model.temperature));
});

it("holds the held-out accuracy gate through the shipped TypeScript path", () => {
  const rows = readFileSync(path.resolve("tests/fixtures/laya-affinity-corpus.jsonl"), "utf8")
    .trim().split(/\r?\n/u).map((line) => JSON.parse(line) as {
      text: string; label: string; split: string;
    }).filter((row) => row.split === "test");
  let correct = 0;
  for (const row of rows) {
    const best = Object.entries(relationshipTextProbabilities(row.text))
      .sort((a, b) => b[1] - a[1])[0]![0];
    correct += Number(best === row.label);
  }
  const accuracy = correct / rows.length;
  assert.ok(accuracy >= 0.9, `TS model accuracy ${accuracy} below 90%`);
});

it("returns normalized probabilities and separates representative relationship signals", () => {
  const cases: Array<[string, string]> = [
    ["请你别再联系我。", "rejecting"],
    ["我想先一个人待一段时间。", "distant"],
    ["会议改到周三下午。", "neutral"],
    ["谢谢你帮我整理资料。", "warm"],
    ["我想和你一起看未来。", "romantic"],
  ];
  for (const [text, expected] of cases) {
    const probabilities = relationshipTextProbabilities(text);
    assert.deepEqual(Object.keys(probabilities).sort(), [...RELATIONSHIP_NB_LABELS].sort());
    const total = Object.values(probabilities).reduce((sum, value) => sum + value, 0);
    assert.ok(Math.abs(total - 1) < 1e-9, `${text}: ${total}`);
    const best = Object.entries(probabilities).sort((a, b) => b[1] - a[1])[0]![0];
    assert.equal(best, expected, `${text} => ${best}`);
  }
});
