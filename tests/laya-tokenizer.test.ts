// Offline regression for the tokenizer/prompt fast paths.
//
// Uses a synthetic Metaspace BPE tokenizer (empty merge table, one vocab entry per
// character) instead of the 34 MB production `tokenizer.json`, so every expected id below is
// readable by hand and the test needs no model download. What it pins:
// - `isUnicodeWhitespace` is exactly `\p{White_Space}`, including the astral planes.
// - `LayaTokenizer.encode` memoizes, survives cache eviction, and still isolates an lstrip
//   added token from a whitespace-only added token ("\n<mask>").
// - `buildSequence` with precomputed state ids equals the per-question encoding path and
//   leaves the caller's array untouched.
// - `LayaAgent.prepare` encodes the shared state exactly once.
import assert from "node:assert/strict";
import { it } from "node:test";

import { LayaAgent, type Runner } from "../electron/laya/agent";
import { buildSequence, serializeState } from "../electron/laya/prompt";
import {
  isUnicodeWhitespace,
  LayaTokenizer,
  type TokenizerConfig,
  type TokenizerJson,
} from "../electron/laya/tokenizer";
import type { AgentConfig, Question, RunnerOutput, State } from "../electron/laya/types";

const PAD = 0;
const EOS = 1;
const BOS = 2;
const MASK = 3;
const NEWLINE = 8;
const REPLACEMENT = "▁";

/** `▁` plus every ASCII letter/digit, so the test texts below never hit `<unk>`. */
const CHARS = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";

function syntheticTokenizerJson(): TokenizerJson {
  const vocab: Record<string, number> = {
    "<pad>": PAD,
    "<eos>": EOS,
    "<bos>": BOS,
    "<mask>": MASK,
    "<unk>": 4,
    [REPLACEMENT]: 5,
    "\n": NEWLINE,
  };
  let next = 10;
  for (const ch of CHARS) vocab[ch] = next++;
  return {
    added_tokens: [
      { id: PAD, content: "<pad>", lstrip: false, rstrip: false, normalized: false, single_word: false },
      { id: EOS, content: "<eos>", lstrip: false, rstrip: false, normalized: false, single_word: false },
      { id: BOS, content: "<bos>", lstrip: false, rstrip: false, normalized: false, single_word: false },
      { id: MASK, content: "<mask>", lstrip: true, rstrip: false, normalized: false, single_word: false },
      { id: NEWLINE, content: "\n", lstrip: false, rstrip: false, normalized: false, single_word: false },
    ],
    normalizer: { type: "Replace", pattern: { String: " " }, content: REPLACEMENT },
    pre_tokenizer: { type: "Metaspace", replacement: REPLACEMENT, prepend_scheme: "always", split: true },
    // `LayaTokenizer.encode` never decodes or post-processes, and the underlying library only
    // requires these keys to be present. The model body needs a BPE vocab and a merge list.
    decoder: null,
    post_processor: null,
    model: { type: "BPE", unk_token: "<unk>", fuse_unk: true, byte_fallback: false, vocab, merges: [] },
  } as unknown as TokenizerJson;
}

function syntheticTokenizerConfig(): TokenizerConfig {
  return {
    cls_token: { content: "<bos>" },
    sep_token: { content: "<eos>" },
    pad_token: { content: "<pad>" },
    mask_token: { content: "<mask>" },
  };
}

function newTokenizer(): LayaTokenizer {
  return new LayaTokenizer(syntheticTokenizerJson(), syntheticTokenizerConfig());
}

const WHITE_SPACE = /\p{White_Space}/u;

it("isUnicodeWhitespace matches \\p{White_Space} over the whole BMP and astral planes", () => {
  for (let cp = 0; cp <= 0xffff; cp++) {
    const ch = String.fromCharCode(cp);
    assert.equal(isUnicodeWhitespace(ch), WHITE_SPACE.test(ch), `U+${cp.toString(16)}`);
  }
  for (const cp of [0x10000, 0x1f600, 0x20000, 0xe0001, 0x10ffff]) {
    const ch = String.fromCodePoint(cp);
    assert.equal(isUnicodeWhitespace(ch), WHITE_SPACE.test(ch), `U+${cp.toString(16)}`);
  }
  // A lone surrogate is not White_Space, same as the `u`-flagged regex.
  assert.equal(isUnicodeWhitespace("\ud83d"), WHITE_SPACE.test("\ud83d"));
  assert.equal(isUnicodeWhitespace(""), false);
});

it("encodes added tokens, lstrip and Metaspace pieces the same way as before", () => {
  const tokenizer = newTokenizer();
  // No merges: "▁a" becomes [<replacement>, a].
  const a = tokenizer.encode("a")[1]!;
  assert.deepEqual(tokenizer.encode("a"), [5, a]);
  assert.deepEqual(tokenizer.encode("a<mask>b"), [5, a, MASK, 5, tokenizer.encode("b")[1]!]);
  // The "\n" added token keeps its own id instead of being absorbed by <mask>'s lstrip.
  assert.deepEqual(tokenizer.encode("\n<mask>"), [NEWLINE, MASK]);
  assert.equal(tokenizer.encode("")[0], undefined);
  assert.deepEqual(tokenizer.encode(""), []);
});

it("memoizes repeated encodes and still matches a cold tokenizer after eviction", () => {
  const tokenizer = newTokenizer();
  const texts = ["hello world", "a<mask>b", "\n<mask>", "weekend plan?"];
  const cold = texts.map((text) => [...newTokenizer().encode(text)]);
  // Reference equality is the memoization contract: `encode` returns the cached array.
  assert.equal(tokenizer.encode(texts[0]!), tokenizer.encode(texts[0]!));
  // Overflow the bounded cache, then re-encode: the ids must be identical either way.
  for (let i = 0; i < 600; i++) tokenizer.encode(`filler ${i}`);
  texts.forEach((text, i) => {
    assert.deepEqual(tokenizer.encode(text), cold[i], text);
  });
});

it("buildSequence is unchanged when the state ids are passed in, and does not mutate them", () => {
  const tokenizer = newTokenizer();
  const state: State = "TARGET message to judge:\n[them]: hi there";
  const q = { t: "choice", ins: "pick one", crit: { yes: null, no: null } } as const;
  const config: AgentConfig = {
    encoder: "synthetic-encoder", head_layers: 1, max_len: 64, head_max_len: 32,
    temperature: [1, 1, 1], temperature_by_options: {},
  };
  const stateIds = tokenizer.encode(serializeState(state).replaceAll(tokenizer.maskToken, " "));
  const snapshot = [...stateIds];
  const hoisted = buildSequence(tokenizer, state, q, config.max_len, config.head_max_len, stateIds);
  const perQuestion = buildSequence(tokenizer, state, q, config.max_len, config.head_max_len);
  assert.deepEqual(hoisted, perQuestion);
  assert.deepEqual(stateIds, snapshot);
});

it("prepares a question set after encoding the shared state exactly once", () => {
  const tokenizer = newTokenizer();
  const seen = new Map<string, number>();
  const counting = Object.create(tokenizer) as LayaTokenizer;
  counting.encode = (text: string): number[] => {
    seen.set(text, (seen.get(text) ?? 0) + 1);
    return tokenizer.encode(text);
  };
  const runner: Runner = {
    async run(): Promise<RunnerOutput> {
      throw new Error("prepare must not run inference");
    },
  };
  const agent = new LayaAgent({
    config: {
      encoder: "synthetic-encoder", head_layers: 1, max_len: 64, head_max_len: 32,
      temperature: [1, 1, 1], temperature_by_options: {},
    },
    tokenizer: counting,
    runner,
  });
  const state: State = "TARGET message to judge:\n[them]: saturday?";
  const questions: Record<string, Question> = {
    emotion: { type: "choice", instructions: "How does it feel?", criteria: ["good", "bad"] },
    intent: { type: "choice", instructions: "What is the intent?", criteria: ["plan", "small"] },
    relationship: { type: "noul", instructions: "Is it warm?" },
  };
  const first = agent.prepare(state, questions);
  const stateText = serializeState(state).replaceAll(tokenizer.maskToken, " ");
  // Three questions, one shared state: the hoisted encode runs once per `prepare`.
  assert.equal(seen.get(stateText), 1);
  const second = agent.prepare(state, questions);
  assert.deepEqual(first.items, second.items);
  assert.equal(seen.get(stateText), 2);
  assert.equal(Object.keys(questions).length, 3);
});