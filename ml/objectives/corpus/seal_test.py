"""Gera test-v1.jsonl a partir de test_v1_source.py e imprime o SHA-256 (selo do CC-501)."""

import hashlib
import json
from collections import Counter
from pathlib import Path

from test_v1_source import TEST

NEGATION = {"negation", "exclusion_only"}
here = Path(__file__).parent
rows = []
for index, (text, label, category) in enumerate(TEST, 1):
    rows.append(
        {
            "id": f"test-{index:03d}",
            "text": text,
            "label": label,
            "category": category,
            "negation": category in NEGATION or " não " in f" {text.lower()} " or "nada de" in text.lower() or text.lower().startswith("sem "),
            "templateId": f"handwritten-test-{index:03d}",
            "origin": "synthetic:model",
            "split": "test",
        }
    )
assert len({r["text"].lower() for r in rows}) == len(rows), "duplicata no teste"
out = here / "test-v1.jsonl"
out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
print(len(rows), dict(Counter(r["label"] for r in rows)))
print("sha256", hashlib.sha256(out.read_bytes()).hexdigest())
