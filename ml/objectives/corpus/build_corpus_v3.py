"""CC-1402: gera treino/validação v3 do interpretador de objetivo.

Escrito e congelado (hash) ANTES de existir o teste v3. O LoRA v2 resistiu a toda injeção, mas
pediu esclarecimento em pedidos claros (16 dos 19 erros no teste v2). O v3:
- baixa a fração de NEEDS_INPUT (de ~35% para ~25%);
- amplia pedidos diretos e situações, inclusive jargão de jogador (AD, AP, MR, CDR, MS, tank,
  burst, peel), com templates exclusivos por split;
- junta frases escritas à mão: o teste v1 (já usado) vai para o treino e o teste v2 (já usado),
  para a validação, que escolhe a época.
Reaproveita léxicos/templates do v1 e do v2 (build_corpus.py e build_corpus_v2.py seguem
reproduzindo as versões anteriores). Quase-duplicatas entre treino e validação saem da validação.
O teste v3, escrito depois, é que se afasta deste corpus (nunca o contrário).

Uso: python build_corpus_v3.py  (grava train-v3.jsonl, val-v3.jsonl e corpus-v3-report.json)
"""

from __future__ import annotations

import hashlib
import json
import random
from collections import Counter

import build_corpus as v1
import build_corpus_v2 as v2
from build_corpus import B, D, HERE, N, PRIORITIES, S, U, jaccard, normalise, shingles

SEED = 1402

# Pedidos diretos com jargão (sintagmas que aceitam "quero ...", "foca em ...").
JARGON = {
    S: [
        "build tank", "mais MR", "mais armor", "itens de resistência", "mais HP",
        "algo pra não explodir", "resistência contra AP", "virar tanque", "segurar a linha de frente",
        "item de vida e armadura", "algo pra não tomar burst", "defesa contra o assassino deles",
    ],
    D: [
        "mais AD", "mais AP", "build full dano", "letalidade pra assassinar", "dano pro late game",
        "matar o carry deles", "mais DPS", "item de crit", "penetração mágica pra furar MR",
        "ser glass cannon", "dano explosivo", "item pra derreter tanque",
    ],
    U: [
        "mais CDR", "mais haste", "mais MS", "move speed", "peel pro meu ADC", "utilidade pro time",
        "item de encantador", "ter a ult sempre pronta", "sustain de mana", "algo pra rotacionar rápido",
        "escudos pros aliados", "mais aceleração pras skills",
    ],
}
JARGON_TEMPLATES = [
    ("jg01", "{x}", "train"), ("jg02", "quero {x}", "train"), ("jg03", "bora {x}", "train"),
    ("jg04", "foca em {x}", "train"), ("jg05", "build com {x}", "train"),
    ("jg06", "nesse jogo {x}", "train"), ("jg07", "me vê {x} aí", "val"),
    ("jg08", "o que eu quero é {x}", "val"),
]
SITUATION_V3 = {
    S: [
        "{c} me deleta em dois segundos", "morro antes de chegar na luta", "tô 0/5 e morro toda hora",
        "o jungle deles me gankou e eu morri todas as vezes", "o assassino deles some comigo",
        "qualquer troca com {c} eu saio quase morto", "sou o primeiro a cair em toda luta",
    ],
    D: [
        "{c} sempre escapa com pouca vida", "tô bem de farm mas não mato ninguém",
        "o atirador deles tanka meu combo inteiro", "minha ult não mata nem o suporte",
        "{c} ri do meu dano", "acerto tudo e o inimigo continua cheio de vida",
    ],
    U: [
        "volto pra base toda hora sem mana", "minhas skills ficam em recarga a luta toda",
        "não chego a tempo nos objetivos", "meu time morre sem eu conseguir fazer nada por eles",
        "fico parado esperando a ult voltar", "sou lento demais pra acompanhar o time",
    ],
}
SITUATION_V3_TEMPLATES = [
    ("sv01", "{sit}", "train"), ("sv02", "{sit}, me ajuda", "train"), ("sv03", "{sit} kkk", "train"),
    ("sv04", "{sit}, o que compro?", "train"), ("sv05", "sério, {sit}", "val"),
    ("sv06", "{sit} e não sei o que fazer", "val"),
]

TARGETS_V3 = {
    S: {"direct": 30, "situation": 20, "negation": 22, "precedence": 22},
    D: {"direct": 30, "situation": 20, "negation": 22, "precedence": 22},
    U: {"direct": 30, "situation": 20, "negation": 22, "precedence": 22},
    B: {"balanced": 110},
    N: {"ambiguous": 15, "exclusion_only": 25},
}
JARGON_PER_CLASS = 40
SITUATION_V3_PER_CLASS = 25
AMBIGUOUS_V2_TARGET = 25
LOOKALIKE_TARGET = 30
INJECTION_PER_TEMPLATE = 3


def generate() -> list[dict]:
    rng = random.Random(SEED)
    rows: list[dict] = []

    def add(text: str, label: str, category: str, tid: str, split: str, decorate: bool = True):
        rows.append({"text": v1.decorate(rng, text) if decorate else text, "label": label,
                     "category": category, "templateId": tid, "split": split})

    for label in PRIORITIES:
        for category, count in TARGETS_V3[label].items():
            pool = (v1.TEMPLATES["direct_vp"] + v1.TEMPLATES["direct_np"]) if category == "direct" \
                else v1.TEMPLATES[category]
            for _ in range(count):
                tid, template = rng.choice(pool)
                add(v1.fill(rng, template, label), label, category, tid, v2.split_of(tid))
        for _ in range(JARGON_PER_CLASS):
            tid, template, split = rng.choice(JARGON_TEMPLATES)
            add(template.format(x=rng.choice(JARGON[label])), label, "direct", tid, split)
        for _ in range(SITUATION_V3_PER_CLASS):
            tid, template, split = rng.choice(SITUATION_V3_TEMPLATES)
            c = rng.choice(v1.CHAMPS)
            sit = rng.choice(SITUATION_V3[label]).format(c=c)
            add(template.format(sit=sit), label, "situation", tid, split)
    for _ in range(TARGETS_V3[B]["balanced"]):
        tid, template = rng.choice(v1.TEMPLATES["balanced"])
        add(v1.fill(rng, template, B), B, "negation" if tid == "bal08" else "balanced", tid,
            v2.split_of(tid))
    for category in ("ambiguous", "exclusion_only"):
        for _ in range(TARGETS_V3[N][category]):
            tid, template = rng.choice(v1.TEMPLATES[category])
            add(v1.fill(rng, template, rng.choice(PRIORITIES)), N, category, tid, v2.split_of(tid))
    for _ in range(AMBIGUOUS_V2_TARGET):
        tid, template, split = rng.choice(v2.AMBIGUOUS_V2)
        add(v1.fill(rng, template, rng.choice(PRIORITIES)), N, "ambiguous", tid, split)
    for _ in range(LOOKALIKE_TARGET):
        tid, template, split = rng.choice(v2.LOOKALIKE)
        label = rng.choice(PRIORITIES)
        add(v1.fill(rng, template, label), label, "lookalike", tid, split)
    for tid, template, split in v2.INJECTION_V2:
        for _ in range(INJECTION_PER_TEMPLATE):
            asked = rng.choice(PRIORITIES)
            dictated = rng.choice([p for p in (S, D, U, B) if p != asked])
            text = template.format(L=dictated, vp=rng.choice(v1.VP[asked]), np=rng.choice(v1.NP[asked]))
            add(text, N, "injection", tid, split, decorate=rng.random() < 0.5)
    for category, phrases in (("vague", v1.VAGUE), ("out_of_domain", v1.OUT_OF_DOMAIN)):
        for index, phrase in enumerate(phrases):
            tid = f"{category[:3]}{index:02d}"
            add(phrase, N, category, tid, "val" if index >= len(phrases) - 5 else "train")
    for index, template in enumerate(v1.INJECTION):
        label = rng.choice(PRIORITIES)
        text = template.format(L=rng.choice([S, D, U, B]), vp=rng.choice(v1.VP[label]),
                               np=rng.choice(v1.NP[label]))
        add(text, N, "injection", f"inj{index:02d}",
            "val" if index >= len(v1.INJECTION) - 5 else "train", decorate=False)
    # Frases escritas à mão dos testes já usados (rótulos revisados): v1 → treino, v2 → validação.
    for name, split in (("test-v1.jsonl", "train"), ("test-v2.jsonl", "val")):
        for line in (HERE / name).read_text("utf-8").splitlines():
            row = json.loads(line)
            rows.append({"text": row["text"], "label": row["label"], "category": row["category"],
                         "templateId": row["templateId"], "split": split})
    return rows


def main() -> None:
    rows = generate()
    report: dict = {"generated": len(rows), "dropped": Counter()}
    kept: list[dict] = []
    seen: set[str] = set()
    for row in rows:
        key = normalise(row["text"])
        if key in seen:
            report["dropped"]["exact_duplicate"] += 1
            continue
        seen.add(key)
        kept.append(row)
    train_sh = [shingles(r["text"]) for r in kept if r["split"] == "train"]
    final: list[dict] = []
    for row in kept:
        if row["split"] == "val" and any(jaccard(shingles(row["text"]), t) >= 0.8 for t in train_sh):
            report["dropped"]["near_train_in_val"] += 1
            continue
        final.append(row)
    rng = random.Random(SEED + 1)
    for split in ("train", "val"):
        part = [r for r in final if r["split"] == split]
        rng.shuffle(part)
        for index, row in enumerate(part, 1):
            row["id"] = f"{split}-v3-{index:03d}"
            row["negation"] = row["category"] in {"negation", "exclusion_only"}
            row["origin"] = "synthetic:model"
        path = HERE / f"{split}-v3.jsonl"
        keys = ("id", "text", "label", "category", "negation", "templateId", "origin", "split")
        path.write_text("".join(json.dumps({k: r[k] for k in keys}, ensure_ascii=False) + "\n"
                                for r in part), encoding="utf-8")
        report[f"{split}_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        report[f"{split}_size"] = len(part)
        report[f"{split}_labels"] = dict(Counter(r["label"] for r in part))
        report[f"{split}_categories"] = dict(Counter(r["category"] for r in part))
        report[f"{split}_templates"] = sorted({r["templateId"] for r in part})
    overlap = set(report["train_templates"]) & set(report["val_templates"])
    assert not overlap, overlap
    report["template_overlap_train_val"] = sorted(overlap)
    report["dropped"] = dict(report["dropped"])
    (HERE / "corpus-v3-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({k: v for k, v in report.items() if not k.endswith("_templates")},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
