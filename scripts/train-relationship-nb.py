"""Train the local character n-gram relationship classifier.

This is a real, reproducible trained model (multinomial Naive Bayes), separate from the fixed
Laya ONNX bundle. It consumes the hand-authored affinity corpus and writes:

  electron/laya/relationship-nb-model.json   inference artifact
  docs/laya-relationship-nb-report.json      held-out metrics and model provenance

Method:
  1. 5-fold cross-validation on `split=train` selects the smoothing alpha.
  2. The selected alpha is fit on train only; validation remains a true holdout.
  3. `split=test` is evaluated once and is the release gate (default >= 90%).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from collections import Counter
from pathlib import Path

LABELS = ("rejecting", "distant", "neutral", "warm", "romantic")
ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "tests" / "fixtures" / "laya-affinity-corpus.jsonl"
MODEL = ROOT / "electron" / "laya" / "relationship-nb-model.json"
REPORT = ROOT / "docs" / "laya-relationship-nb-report.json"
ALPHAS = (0.5, 0.75, 1.0, 1.25, 1.5, 2.0)


def features(text: str) -> list[str]:
    compact = "".join(char for char in text if not char.isspace())
    grams: list[str] = []
    for size in (1, 2, 3):
        grams.extend(compact[index:index + size] for index in range(len(compact) - size + 1))
    return grams


def load_corpus() -> list[dict]:
    rows = [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()]
    ids = {row["id"] for row in rows}
    texts = {row["text"] for row in rows}
    if len(ids) != len(rows) or len(texts) != len(rows):
        raise SystemExit("corpus has duplicate ids or texts")
    expected = {
        "train": {"rejecting": 56, "distant": 60, "neutral": 58, "warm": 92, "romantic": 89},
        "validation": {"rejecting": 16, "distant": 16, "neutral": 16, "warm": 24, "romantic": 24},
        "test": {label: 20 for label in LABELS},
    }
    for split, per_label in expected.items():
        for label, count in per_label.items():
            actual = sum(row["split"] == split and row["label"] == label for row in rows)
            if actual != count:
                raise SystemExit(f"unbalanced corpus: {split}/{label}: {actual} != {count}")
    return rows


def fit(rows: list[dict], alpha: float) -> dict:
    counts = {label: Counter() for label in LABELS}
    totals = {label: 0 for label in LABELS}
    docs = Counter()
    for row in rows:
        label = row["label"]
        docs[label] += 1
        for gram in features(row["text"]):
            counts[label][gram] += 1
            totals[label] += 1
    vocabulary = set().union(*(set(counter) for counter in counts.values()))
    return {
        "alpha": alpha,
        "vocabSize": len(vocabulary),
        "docCounts": [docs[label] for label in LABELS],
        "tokenTotals": [totals[label] for label in LABELS],
        "counts": {gram: [counts[label][gram] for label in LABELS] for gram in sorted(vocabulary)},
    }


def score_vector(model: dict, text: str) -> list[float]:
    tokens = features(text)
    alpha = model["alpha"]
    vocab_size = model["vocabSize"]
    total_docs = sum(model["docCounts"])
    scores = []
    for index in range(len(LABELS)):
        score = math.log(model["docCounts"][index] / total_docs)
        score -= len(tokens) * math.log(model["tokenTotals"][index] + alpha * vocab_size)
        scores.append(score)
    for gram in tokens:
        values = model["counts"].get(gram)
        for index in range(len(LABELS)):
            count = (values[index] if values else 0) + alpha
            scores[index] += math.log(count)
    return scores


def classify(model: dict, text: str) -> str:
    scores = score_vector(model, text)
    return LABELS[max(range(len(LABELS)), key=scores.__getitem__)]


def probabilities(model: dict, text: str) -> list[float]:
    scores = [score / model.get("temperature", 1.0) for score in score_vector(model, text)]
    maximum = max(scores)
    exponents = [math.exp(score - maximum) for score in scores]
    total = sum(exponents)
    return [value / total for value in exponents]


def calibration_loss(model: dict, rows: list[dict]) -> tuple[float, float]:
    """Negative log likelihood and Brier score after temperature scaling."""
    nll = 0.0
    brier = 0.0
    for row in rows:
        probability = probabilities(model, row["text"])
        expected = LABELS.index(row["label"])
        nll -= math.log(max(1e-12, probability[expected]))
        brier += sum((value - (1.0 if index == expected else 0.0)) ** 2
                     for index, value in enumerate(probability))
    return nll / max(1, len(rows)), brier / max(1, len(rows))


def accuracy(model: dict, rows: list[dict]) -> dict:
    confusion = {expected: Counter() for expected in LABELS}
    correct = 0
    for row in rows:
        predicted = classify(model, row["text"])
        confusion[row["label"]][predicted] += 1
        correct += int(predicted == row["label"])
    return {
        "rows": len(rows),
        "correct": correct,
        "accuracy": correct / max(1, len(rows)),
        "macroRecall": sum(confusion[label][label] / max(1, sum(confusion[label].values()))
                           for label in LABELS) / len(LABELS),
        "perClass": {label: {"correct": confusion[label][label],
                             "total": sum(confusion[label].values())} for label in LABELS},
        "confusion": {label: dict(confusion[label]) for label in LABELS},
    }


def cross_validate(rows: list[dict], alpha: float, folds: int = 5) -> float:
    order = list(range(len(rows)))
    random.Random(20261101).shuffle(order)
    scores = []
    for fold in range(folds):
        holdout = {index for index in order if index % folds == fold}
        training = [row for index, row in enumerate(rows) if index not in holdout]
        model = fit(training, alpha)
        scores.append(accuracy(model, [row for index, row in enumerate(rows) if index in holdout])["accuracy"])
    return sum(scores) / len(scores)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=float, default=.90, help="held-out accuracy gate")
    parser.add_argument("--check", action="store_true", help="validate the shipped model instead of retraining")
    args = parser.parse_args()
    rows = load_corpus()
    train = [row for row in rows if row["split"] == "train"]
    validation = [row for row in rows if row["split"] == "validation"]
    test = [row for row in rows if row["split"] == "test"]

    if args.check:
        model = json.loads(MODEL.read_text(encoding="utf-8"))
        test_metrics = accuracy(model, test)
        print(json.dumps({"model": model["version"], "test": test_metrics}, ensure_ascii=False, indent=2))
        raise SystemExit(0 if test_metrics["accuracy"] >= args.target else 1)

    cv = {alpha: cross_validate(train, alpha) for alpha in ALPHAS}
    selected_alpha = max(ALPHAS, key=lambda alpha: (cv[alpha], -abs(alpha - 1.0)))
    model = fit(train, selected_alpha)
    # Temperature is fit on validation only. It cannot change argmax, so classification stays
    # untouched while the probability scale becomes usable for affinity.
    temperatures = (0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 8.0, 12.0, 20.0)
    calibration = {}
    for temperature in temperatures:
        model["temperature"] = temperature
        calibration[temperature] = calibration_loss(model, validation)
    selected_temperature = min(temperatures, key=lambda temperature:
                               (calibration[temperature][0], calibration[temperature][1]))
    model["temperature"] = selected_temperature
    test_metrics = accuracy(model, test)
    validation_metrics = accuracy(model, validation)
    validation_metrics["nll"], validation_metrics["brier"] = calibration[selected_temperature]
    digest = hashlib.sha256(CORPUS.read_bytes()).hexdigest()
    signature = hashlib.sha256(
        f"{digest}|{selected_alpha}|{selected_temperature}|unicode-char-ngram-1-3".encode()
    ).hexdigest()[:8]
    version = f"relationship-nb-v1-{signature}"
    model.update({
        "version": version,
        "corpusSha256": digest,
        "trainRows": len(train),
        "validationAccuracy": validation_metrics["accuracy"],
        "testAccuracy": test_metrics["accuracy"],
        "features": "unicode-char-ngram-1-3",
    })
    MODEL.write_text(json.dumps(model, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    report = {
        "version": version,
        "corpusSha256": digest,
        "rows": {"train": len(train), "validation": len(validation), "test": len(test)},
        "crossValidation": {str(alpha): cv[alpha] for alpha in ALPHAS},
        "selectedAlpha": selected_alpha,
        "selectedTemperature": selected_temperature,
        "calibration": {str(t): {"nll": value[0], "brier": value[1]}
                        for t, value in calibration.items()},
        "validation": validation_metrics,
        "test": test_metrics,
        "target": args.target,
        "passed": test_metrics["accuracy"] >= args.target,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in
                      ("version", "rows", "selectedAlpha", "selectedTemperature",
                       "validation", "test", "target", "passed")},
                     ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
