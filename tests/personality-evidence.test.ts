import assert from "node:assert/strict";
import { it } from "node:test";
import { PERSONALITY_QUESTIONS, personalityEvidenceFromAnswers } from "../electron/laya/personality";
import type { Answer } from "../electron/laya/types";

function answer(axis: string, weights: number[]): Answer {
  const labels = PERSONALITY_QUESTIONS[`mbti_${axis}`]!.criteria as string[];
  return { type: "choice", choice: labels[weights.indexOf(Math.max(...weights))]!,
    probabilities: Object.fromEntries(labels.map((label, i) => [label, weights[i]])),
    confidence: Math.max(...weights), action: { act_probability: 1 } };
}

it("local evidence keeps SN when other axes are missing or malformed", () => {
  const evidence = personalityEvidenceFromAnswers({ mbti_SN: answer("SN", [0.1, 0.7, 0.2]),
    mbti_EI: answer("EI", [NaN, 0.1, 0.9]) });
  assert.deepEqual(evidence, { EI: { E: 0, I: 0, insufficient: 1 },
    SN: { S: 0.7, N: 0.2, insufficient: 0.1 }, TF: { T: 0, F: 0, insufficient: 1 },
    JP: { J: 0, P: 0, insufficient: 1 } });
});

it("missing and fully uncertain evidence does not invent scores", () => {
  assert.equal(personalityEvidenceFromAnswers({}), null);
  const answers = Object.fromEntries(["EI", "SN", "TF", "JP"].map(axis =>
    [`mbti_${axis}`, answer(axis, [1, 0, 0])]));
  assert.equal(personalityEvidenceFromAnswers(answers), null);
});

it("balanced supported evidence survives without choosing a pole", () => {
  assert.deepEqual(personalityEvidenceFromAnswers({ mbti_TF: answer("TF", [0, 0.5, 0.5]) })!.TF,
    { T: 0.5, F: 0.5, insufficient: 0 });
});
