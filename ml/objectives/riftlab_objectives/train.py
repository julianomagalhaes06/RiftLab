"""CC-503: ajuste supervisionado LoRA em CPU do interpretador de objetivo.

Uso: uv run python -m riftlab_objectives.train --config configs/experiments/objective-lora-*.yaml
Roda em CPU (padrão aqui) ou GPU CUDA quando houver (`device: auto`, usado no notebook do Colab);
o cálculo é o mesmo, em fp32. Perda só nos tokens da resposta. Treina no split train, mede perda no val a cada época e guarda o
adaptador de cada época; o escolhido é o de menor perda no val (o teste não é tocado aqui).
Teto de tempo (maxHours) interrompe com registro.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import resource
import time
from pathlib import Path

import torch
import torch.nn.functional as F
import yaml
from peft import LoraConfig, get_peft_model
from transformers import AutoModelForCausalLM, AutoTokenizer

from riftlab_objectives.prompt import messages, target

ROOT = Path(__file__).resolve().parents[3]
CORPUS = Path(__file__).resolve().parents[1] / "corpus"


def encode(tokenizer, text: str, label: str) -> tuple[list[int], list[int]]:
    prompt = tokenizer.apply_chat_template(messages(text), tokenize=False, add_generation_prompt=True)
    p_ids = tokenizer(prompt, add_special_tokens=False)["input_ids"]
    a_ids = tokenizer(target(label) + tokenizer.eos_token, add_special_tokens=False)["input_ids"]
    return p_ids + a_ids, [-100] * len(p_ids) + a_ids


def batches(examples, size: int, rng: random.Random | None, device: str = "cpu"):
    order = list(range(len(examples)))
    if rng:
        rng.shuffle(order)
    for start in range(0, len(order), size):
        chunk = [examples[i] for i in order[start : start + size]]
        width = max(len(ids) for ids, _ in chunk)
        ids = torch.full((len(chunk), width), 0, dtype=torch.long)
        labels = torch.full((len(chunk), width), -100, dtype=torch.long)
        mask = torch.zeros((len(chunk), width), dtype=torch.long)
        for row, (i, lab) in enumerate(chunk):
            ids[row, : len(i)] = torch.tensor(i)
            labels[row, : len(lab)] = torch.tensor(lab)
            mask[row, : len(i)] = 1
        yield ids.to(device), labels.to(device), mask.to(device)


def load_split(tokenizer, name: str):
    path = CORPUS / f"{name}-v1.jsonl"
    rows = [json.loads(line) for line in path.read_text("utf-8").splitlines()]
    return [encode(tokenizer, r["text"], r["label"]) for r in rows], hashlib.sha256(
        path.read_bytes()).hexdigest()


def answer_loss(model, ids, mask, labels):
    """Entropia cruzada só nos tokens da resposta. A projeção no vocabulário (152 mil
    tokens) é feita apenas nessas posições: calcular logits de todas as posições estourou
    a RAM (13 GB, OOM) no primeiro teste."""
    base = model.get_base_model()
    hidden = base.model(input_ids=ids, attention_mask=mask).last_hidden_state[:, :-1]
    target = labels[:, 1:]
    selected = target != -100
    logits = base.lm_head(hidden[selected])
    return F.cross_entropy(logits.float(), target[selected])


def val_loss(model, examples, size: int, device: str = "cpu") -> float:
    model.eval()
    total, count = 0.0, 0
    with torch.inference_mode():
        for ids, labels, mask in batches(examples, size, None, device):
            n = int((labels[:, 1:] != -100).sum())
            total, count = total + float(answer_loss(model, ids, mask, labels)) * n, count + n
    model.train()
    return total / count


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--max-steps", type=int, default=0, help="smoke: para após N passos")
    args = parser.parse_args()
    config = yaml.safe_load(Path(args.config).read_text())
    seed = config["seed"]
    random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.set_num_threads(config.get("threads", 4))
    wanted = config.get("device", "auto")
    device = ("cuda" if torch.cuda.is_available() else "cpu") if wanted == "auto" else wanted
    tokenizer = AutoTokenizer.from_pretrained(config["model"], revision=config["revision"])
    model = AutoModelForCausalLM.from_pretrained(
        config["model"], revision=config["revision"], torch_dtype=torch.float32)
    lora = config["lora"]
    model.config.use_cache = False
    if config.get("gradientCheckpointing", True):
        model.gradient_checkpointing_enable(gradient_checkpointing_kwargs={"use_reentrant": False})
    model = get_peft_model(model, LoraConfig(
        r=lora["r"], lora_alpha=lora["alpha"], lora_dropout=lora["dropout"],
        target_modules=lora["targetModules"], task_type="CAUSAL_LM"))
    model.to(device)
    train, train_sha = load_split(tokenizer, "train")
    val, val_sha = load_split(tokenizer, "val")
    optimizer = torch.optim.AdamW(
        [p for p in model.parameters() if p.requires_grad], lr=config["learningRate"])
    out = ROOT / config["output"]
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    log: list[dict] = []
    started = time.perf_counter()
    step, stop = 0, None
    accumulation = config["gradAccumulation"]
    model.train()
    for epoch in range(1, config["epochs"] + 1):
        optimizer.zero_grad()
        batch_iter = batches(train, config["batchSize"], rng, device)
        for index, (ids, labels, mask) in enumerate(batch_iter, 1):
            t0 = time.perf_counter()
            loss = answer_loss(model, ids, mask, labels) / accumulation
            loss.backward()
            if index % accumulation == 0:
                optimizer.step()
                optimizer.zero_grad()
            step += 1
            log.append({"epoch": epoch, "step": step, "loss": round(float(loss) * accumulation, 4),
                        "seconds": round(time.perf_counter() - t0, 2)})
            print(json.dumps(log[-1]), flush=True)
            hours = (time.perf_counter() - started) / 3600
            if args.max_steps and step >= args.max_steps:
                stop = "MAX_STEPS"
                break
            if hours >= config["maxHours"]:
                stop = "TIME_LIMIT"
                break
        optimizer.step()
        optimizer.zero_grad()
        if stop == "MAX_STEPS":
            break
        loss_val = val_loss(model, val, config["batchSize"], device)
        model.save_pretrained(out / f"epoch-{epoch}")
        log.append({"epoch": epoch, "valLoss": round(loss_val, 5)})
        print(json.dumps(log[-1]), flush=True)
        if stop:
            break
    epochs = [e for e in log if "valLoss" in e]
    best = min(epochs, key=lambda e: e["valLoss"]) if epochs else None
    manifest = {
        "config": config, "trainSha256": train_sha, "valSha256": val_sha, "steps": step,
        "stopReason": stop or "COMPLETED", "bestEpoch": best and best["epoch"],
        "valLossByEpoch": epochs, "hours": round((time.perf_counter() - started) / 3600, 3),
        "peakRssMb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024),
        "torch": torch.__version__, "trainableParams": sum(
            p.numel() for p in model.parameters() if p.requires_grad),
        "device": device,
    }
    if device == "cuda":
        manifest["gpu"] = torch.cuda.get_device_name(0)
        manifest["cuda"] = torch.version.cuda
        manifest["cudaMaxMemoryMb"] = round(torch.cuda.max_memory_allocated() / 2**20)
    if best:
        adapter = out / f"epoch-{best['epoch']}" / "adapter_model.safetensors"
        manifest["adapter"] = str((out / f"epoch-{best['epoch']}").relative_to(ROOT))
        manifest["adapterSha256"] = hashlib.sha256(adapter.read_bytes()).hexdigest()
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    (out / "log.jsonl").write_text("".join(json.dumps(e) + "\n" for e in log))
    print(json.dumps({k: v for k, v in manifest.items() if k != "config"}, indent=1))


if __name__ == "__main__":
    main()
