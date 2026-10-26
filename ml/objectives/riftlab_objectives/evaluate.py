"""Avalia um interpretador (modelo base ou base + adaptador LoRA) num split do corpus.

Uso: uv run python -m riftlab_objectives.evaluate --config CONFIG --split val [--limit N]
Grava predições e resumo em artifacts/objectives/<run>/. Decodificação gulosa, mesmo prompt
para base e ajustado (prompt.py).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import resource
import time
from pathlib import Path

import torch
import yaml
from transformers import AutoModelForCausalLM, AutoTokenizer

from riftlab_objectives.metrics import summarise
from riftlab_objectives.prompt import messages, parse

ROOT = Path(__file__).resolve().parents[3]
CORPUS = Path(__file__).resolve().parents[1] / "corpus"


def load(config: dict):
    torch.manual_seed(config.get("seed", 0))
    torch.set_num_threads(config.get("threads", 4))
    tokenizer = AutoTokenizer.from_pretrained(config["model"], revision=config["revision"])
    model = AutoModelForCausalLM.from_pretrained(
        config["model"], revision=config["revision"], dtype=torch.float32
    )
    if config.get("adapter"):
        from peft import PeftModel

        weights = (ROOT / config["adapter"] / "adapter_model.safetensors").read_bytes()
        expected = config.get("adapterSha256")
        if expected and hashlib.sha256(weights).hexdigest() != expected:
            raise SystemExit(f"adaptador {config['adapter']} não confere com adapterSha256")
        model = PeftModel.from_pretrained(model, ROOT / config["adapter"])
        model = model.merge_and_unload()
    wanted = config.get("device", "auto")
    device = ("cuda" if torch.cuda.is_available() else "cpu") if wanted == "auto" else wanted
    model.to(device).eval()
    return tokenizer, model


def generate(tokenizer, model, text: str, max_new_tokens: int) -> str:
    prompt = tokenizer.apply_chat_template(messages(text), tokenize=False, add_generation_prompt=True)
    inputs = tokenizer(prompt, return_tensors="pt").to(model.device)
    with torch.inference_mode():
        out = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False,
                             pad_token_id=tokenizer.eos_token_id)
    return tokenizer.decode(out[0, inputs["input_ids"].shape[1]:], skip_special_tokens=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--split", required=True, choices=["train", "val", "test", "smoke"])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--run", default="")
    args = parser.parse_args()
    config = yaml.safe_load(Path(args.config).read_text())
    name = "val" if args.split == "smoke" else args.split
    path = CORPUS / f"{name}-{config.get('corpus', 'v1')}.jsonl"  # v2: CC-1302
    rows = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
    if args.split == "smoke":
        rows = rows[:: max(1, len(rows) // 20)][:20]
    if args.limit:
        rows = rows[: args.limit]
    started = time.perf_counter()
    tokenizer, model = load(config)
    load_s = time.perf_counter() - started
    for row in rows:
        t0 = time.perf_counter()
        raw = generate(tokenizer, model, row["text"], config.get("maxNewTokens", 12))
        row["latencyS"] = round(time.perf_counter() - t0, 4)
        row["raw"] = raw
        row["pred"], row["jsonValid"] = parse(raw)
    summary = summarise(rows) | {
        "config": config,
        "split": args.split,
        "corpusSha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "loadS": round(load_s, 2),
        "peakRssMb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024),
        "torch": torch.__version__,
        "cpu": platform.processor() or platform.machine(),
    }
    run = args.run or f"{Path(args.config).stem}-{args.split}"
    out = ROOT / "artifacts" / "objectives" / run
    out.mkdir(parents=True, exist_ok=True)
    (out / "predictions.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in summary.items() if k not in {"perClass", "config"}},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
