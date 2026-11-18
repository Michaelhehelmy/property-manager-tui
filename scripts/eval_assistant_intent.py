#!/usr/bin/env python3
"""Quick eval: run current assistant classifier on seed dataset and print accuracy/confusion."""
from __future__ import annotations
import json, collections
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"src"))
from app_core import assistant

SEED = ROOT/"data/assistant_nlu/intents_seed.jsonl"

def main():
    rows = []
    for line in SEED.read_text(encoding='utf-8').splitlines():
        if line.strip():
            rows.append(json.loads(line))
    ok = 0
    conf = collections.Counter()
    for r in rows:
        pred = assistant.classify_intent(r['text'])
        if pred.get('intent') == r['label']:
            ok += 1
        else:
            conf[(r['label'], pred.get('intent'))] += 1
    total = len(rows) or 1
    print(f"Accuracy: {ok}/{len(rows)} = {ok/total:.3f} (router={assistant.classify_intent('test').get('router','?')})")
    if conf:
        print("Top confusions (true -> pred):")
        for (t,p), c in conf.most_common(15):
            print(f"  {c:3d}  {t} -> {p}")

if __name__ == '__main__':
    main()
