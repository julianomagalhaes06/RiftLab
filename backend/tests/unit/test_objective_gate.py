"""CC-506: o interpretador padrão segue o gate registrado em configs/objective-parser-gate.yaml.

O registro guarda as métricas de cada versão no teste reservado e o veredito. Estes testes
conferem que cada veredito decorre dos limiares registrados e que o padrão do backend é uma versão
aprovada — trocar o padrão, ou mudar as regras sem nova versão avaliada, quebra aqui.
"""

from pathlib import Path

import yaml

from riftlab.config import Settings
from riftlab.objectives.application import build_parser
from riftlab.objectives.rules import RULES_VERSION

_HERE = Path(__file__).resolve()
_GATE_PATH = next(
    path
    for path in (
        root / "configs" / "objective-parser-gate.yaml"
        for root in (_HERE.parents[2], _HERE.parents[3])
    )
    if path.is_file()
)  # container (/app/configs) ou raiz do repositório (host)
GATE = yaml.safe_load(_GATE_PATH.read_text(encoding="utf-8"))
ROWS = {row["parserVersion"]: row for row in GATE["results"]}


def _passes(row: dict, reference: dict) -> bool:
    limits = GATE["gate"]["thresholds"]
    return (
        row["jsonValidRate"] >= limits["jsonValidRate"]
        and row["macroF1"] >= limits["macroF1"]
        and row["needsInputRecall"]
        >= reference["needsInputRecall"] - limits["maxNeedsInputRecallDrop"]
    )


def test_each_verdict_follows_the_registered_thresholds() -> None:
    reference = ROWS[GATE["gate"]["reference"]]
    for row in GATE["results"]:
        assert row["approved"] is _passes(row, reference), row["parserVersion"]


def test_backend_default_is_the_approved_provider_of_the_gate() -> None:
    decision = GATE["decision"]
    approved = {
        (row["provider"], row["parserVersion"]) for row in GATE["results"] if row["approved"]
    }
    assert (decision["defaultProvider"], decision["defaultParserVersion"]) in approved

    default_kind = Settings.model_fields["objective_parser"].default
    parser = build_parser(default_kind, None, "unused")
    assert parser.provider == decision["defaultProvider"]
    assert parser.version == decision["defaultParserVersion"] == RULES_VERSION


def test_local_model_without_url_never_becomes_the_provider() -> None:
    # configurar só o tipo, sem sidecar, mantém as regras (o formulário segue funcionando)
    assert build_parser("local_model", None, "qwen2.5-1.5b-lora-v1").provider == "RULES"
