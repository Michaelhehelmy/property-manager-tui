#!/usr/bin/env python3
"""Train assistant intent router (sklearn or SetFit) from seed + accepted feedback.

Usage:
  python3 scripts/train_assistant_intent.py --backend sklearn
  python3 scripts/train_assistant_intent.py --backend setfit --base-model paraphrase-MiniLM-L3-v2

Outputs model artifacts under models/ and prints metrics.
"""
from __future__ import annotations
import argparse, json, random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "data" / "assistant_nlu" / "intents_seed.jsonl"
FEEDBACK = ROOT / "data" / "assistant_nlu" / "feedback.jsonl"
SK_OUT = ROOT / "models" / "assistant_intent_sklearn.joblib"
SF_OUT = ROOT / "models" / "assistant_intent_setfit"


def load_jsonl(p: Path):
    items = []
    if not p.exists():
        return items
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line: continue
        try:
            items.append(json.loads(line))
        except Exception:
            pass
    return items


def load_dataset():
    rows = []
    for r in load_jsonl(SEED):
        if r.get("text") and r.get("label"):
            rows.append({"text": r["text"], "label": r["label"]})
    # include corrected/positive feedback as training data
    for r in load_jsonl(FEEDBACK):
        intent = r.get("corrected_intent") or (r.get("predicted_intent") if int(r.get("rating") or 0) == 1 else "")
        msg = r.get("message")
        if msg and intent:
            rows.append({"text": msg, "label": intent})
    # dedupe
    seen = set(); out = []
    for r in rows:
        k = (r["text"].strip().lower(), r["label"])
        if k in seen: continue
        seen.add(k); out.append(r)
    return out


def split(rows, test_ratio=0.2):
    by = {}
    for r in rows:
        by.setdefault(r["label"], []).append(r)
    train, test = [], []
    rng = random.Random(42)
    for lbl, items in by.items():
        items = list(items); rng.shuffle(items)
        n_test = max(1, int(len(items)*test_ratio)) if len(items) > 3 else (1 if len(items) > 1 else 0)
        test.extend(items[:n_test]); train.extend(items[n_test:])
    rng.shuffle(train); rng.shuffle(test)
    return train, test


def train_sklearn(rows):
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.metrics import classification_report, accuracy_score
    import joblib
    train, test = split(rows)
    Xtr = [r["text"] for r in train]; ytr = [r["label"] for r in train]
    Xte = [r["text"] for r in test]; yte = [r["label"] for r in test]
    vec = TfidfVectorizer(ngram_range=(1,2), analyzer='char_wb', min_df=1)
    clf = LogisticRegression(max_iter=400)
    Xtrv = vec.fit_transform(Xtr)
    clf.fit(Xtrv, ytr)
    metrics = {"backend": "sklearn", "train": len(train), "test": len(test)}
    if Xte:
        pred = clf.predict(vec.transform(Xte))
        metrics["accuracy"] = float(accuracy_score(yte, pred))
        metrics["report"] = classification_report(yte, pred, zero_division=0, output_dict=True)
    SK_OUT.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"vectorizer": vec, "classifier": clf, "labels": sorted({r['label'] for r in rows})}, SK_OUT)
    print(json.dumps(metrics, indent=2, ensure_ascii=False))
    print(f"Saved: {SK_OUT}")


def train_setfit(rows, base_model):
    from setfit import SetFitModel, Trainer, TrainingArguments
    from datasets import Dataset
    from sklearn.metrics import classification_report, accuracy_score
    train, test = split(rows)
    ds_train = Dataset.from_list(train)
    ds_test = Dataset.from_list(test) if test else None
    model = SetFitModel.from_pretrained(base_model)
    args = TrainingArguments(batch_size=16, num_epochs=1, evaluation_strategy="no" if ds_test is None else "epoch", save_strategy="no")
    trainer = Trainer(model=model, args=args, train_dataset=ds_train, eval_dataset=ds_test, metric="accuracy")
    trainer.train()
    metrics = {"backend": "setfit", "base_model": base_model, "train": len(train), "test": len(test)}
    if ds_test is not None and len(test):
        preds = model.predict([r["text"] for r in test])
        yte = [r["label"] for r in test]
        metrics["accuracy"] = float(accuracy_score(yte, preds))
        metrics["report"] = classification_report(yte, preds, zero_division=0, output_dict=True)
    SF_OUT.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(str(SF_OUT))
    (SF_OUT/"metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(metrics, indent=2, ensure_ascii=False))
    print(f"Saved: {SF_OUT}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["sklearn","setfit"], required=True)
    ap.add_argument("--base-model", default="sentence-transformers/paraphrase-MiniLM-L3-v2")
    args = ap.parse_args()
    rows = load_dataset()
    if not rows:
        raise SystemExit("No training data found. Ensure data/assistant_nlu/intents_seed.jsonl exists.")
    print(f"Loaded {len(rows)} labeled examples across {len(set(r['label'] for r in rows))} intents")
    if args.backend == "sklearn":
        train_sklearn(rows)
    else:
        train_setfit(rows, args.base_model)

if __name__ == "__main__":
    main()
