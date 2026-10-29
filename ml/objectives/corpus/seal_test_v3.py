"""Sela o teste principal v3 e o conjunto diagnóstico separado de ambiguidades reais."""

import hashlib
import json
from collections import Counter
from pathlib import Path

from test_v3_source import AMBIGUITIES, TEST

NEGATION = {"negation", "exclusion_only"}
here = Path(__file__).parent
rows = []
for index, (text, label, category) in enumerate(TEST, 1):
    rows.append(
        {
            "id": f"test-v3-{index:03d}",
            "text": text,
            "label": label,
            "category": category,
            "negation": category in NEGATION
            or " não " in f" {text.lower()} "
            or "nada de" in text.lower()
            or text.lower().startswith("sem "),
            "templateId": f"handwritten-test-v3-{index:03d}",
            "origin": "synthetic:model",
            "split": "test",
        }
    )
assert len({r["text"].lower() for r in rows}) == len(rows), "duplicata no teste"
out = here / "test-v3.jsonl"
out.write_text(
    "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
)
print(len(rows), dict(Counter(r["label"] for r in rows)))
print("sha256", hashlib.sha256(out.read_bytes()).hexdigest())

# Sem label único e sem split test: este conjunto não participa do gate principal.
ambiguities = [
    {
        "id": f"ambiguity-v3-{index:03d}",
        "text": text,
        "plausibleLabels": ["BALANCED", "NEEDS_INPUT"],
        "reason": reason,
        "originalTestId": original_id,
        "origin": "synthetic:model",
        "split": "diagnostic",
    }
    for index, (text, original_id, reason) in enumerate(AMBIGUITIES, 1)
]
assert not {r["text"] for r in rows} & {r["text"] for r in ambiguities}
diagnostic = here / "ambiguities-v3.jsonl"
diagnostic.write_text(
    "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in ambiguities),
    encoding="utf-8",
)
print(
    "ambiguidades",
    len(ambiguities),
    "sha256",
    hashlib.sha256(diagnostic.read_bytes()).hexdigest(),
)
