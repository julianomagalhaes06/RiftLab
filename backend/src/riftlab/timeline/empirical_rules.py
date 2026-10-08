"""Registro de regras EMPÍRICAS do reducer de reconstrução (CC-207).

O reducer v6 marca como incerto tudo o que ele não sabe modelar (consumo, troca de
trinket, itens de sistema, ordem destroy→compra, undo). O CC-207 caracterizou essas
formas nos patches do recorte (16.16/16.17) com pré-registro, partição por partida e
confirmação única. Cada regra abaixo só vale para os PATCHES em que a confirmação a
aprovou: fora deles, e para regra reprovada, sem amostra ou ainda PENDENTE, o
comportamento é exatamente o da v6.

Este módulo é só configuração e proveniência (puro, sem I/O de banco): o arquivo
versionado `configs/empirical-rules.yaml` declara, por regra, o estado (`PENDING` |
`EMPIRICALLY_SUPPORTED`), a versão da regra, os patches validados, a taxa e a contagem
de contra-exemplos e a referência da evidência.

Invariante de identidade (sem migração): ativar uma regra muda o RESULTADO computado,
então exige uma nova versão de reconstrução. O carregador recusa uma regra
`EMPIRICALLY_SUPPORTED` cuja `activatedInVersion` não seja a versão de reconstrução
corrente, de modo que estados de uma versão nunca sejam reaproveitados com outro
conjunto de regras."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from riftlab.shared.errors import DomainError
from riftlab.timeline.domain import TimelineEvent

logger = logging.getLogger(__name__)

RULE_R1 = "R1"  # consumo de consumível POSSUÍDO
RULE_R2 = "R2"  # troca de trinket (destroy do antigo + compra do novo no mesmo instante)
RULE_R2I = "R2i"  # trinket inicial padrão
RULE_R4S = "R4s"  # itens de sistema/missão que nunca ocupam slot normal
RULE_R4W = "R4w"  # World Atlas por vínculo de papel (pré-passo de eventos)
RULE_R5 = "R5"  # libera UNVALIDATED_DESTROY_ORDER_RECONCILIATION
RULE_R6 = "R6"  # undo em pilha e liberação de UNVALIDATED_UNDO_SEMANTICS
RULE_R7B = "R7b"  # biscoito concedido por runa: destroy sem posse é consumo (CC-324)
RULE_R7M = "R7m"  # Calçados Levemente Mágicos concedidos por runa (CC-324)
RULE_R7X = "R7x"  # elixires concedidos pelo Tônico Triplo (CC-324)
RULE_R8 = "R8"  # missão de rota e bota tier 2: BOTTOM remove, MID troca pelo sucessor (CC-324)
RULE_R8T = "R8t"  # missão do BOTTOM remove as Botas básicas (1001) (CC-324, origem exploratória)
KNOWN_RULE_IDS = (
    RULE_R1,
    RULE_R2,
    RULE_R2I,
    RULE_R4S,
    RULE_R4W,
    RULE_R5,
    RULE_R6,
    RULE_R7B,
    RULE_R7M,
    RULE_R7X,
    RULE_R8,
    RULE_R8T,
)
# Itens concedidos sem evento de compra, por regra (forma da exploração CC-324).
GRANTED_ITEM_IDS_BY_RULE: dict[str, frozenset[int]] = {
    RULE_R7B: frozenset({2010}),
    RULE_R7M: frozenset({2422}),
    RULE_R7X: frozenset({2150, 2151, 2152}),
}
BOOTS_ITEM_ID = 1001
BOT_LANE_QUEST_ITEM_ID = 1202
MID_LANE_QUEST_ITEM_ID = 1201

STATUS_PENDING = "PENDING"
STATUS_SUPPORTED = "EMPIRICALLY_SUPPORTED"
SCHEMA_VERSION = "v1"

DEFAULT_TRINKET_ITEM_ID = 3340
WORLD_ATLAS_ITEM_ID = 3865
SUPPORT_ROLE = "UTILITY"

_RULE_KEYS = frozenset(
    {
        "ruleVersion",
        "description",
        "status",
        "patchesValidated",
        "contraRate",
        "counterExamples",
        "evidence",
        "activatedInVersion",
    }
)


class EmpiricalRulesConfigError(DomainError):
    """Registro de regras inválido: rejeitado sem fallback (nunca liga uma regra por engano)."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.code = "EMPIRICAL_RULES_CONFIG_INVALID"


@dataclass(frozen=True, slots=True)
class RuleEntry:
    rule_id: str
    rule_version: int
    description: str
    status: str
    patches_validated: frozenset[str] = frozenset()
    contra_rate: float | None = None
    counter_examples: int | None = None
    evidence: str | None = None
    activated_in_version: int | None = None

    def enabled_for(self, patch: str) -> bool:
        return self.status == STATUS_SUPPORTED and patch in self.patches_validated

    def provenance(self) -> dict[str, Any]:
        """Proveniência gravada em `quality.empiricalRules` (só das regras LIGADAS)."""
        return {
            "ruleId": self.rule_id,
            "ruleVersion": self.rule_version,
            "status": self.status,
            "patchesValidated": sorted(self.patches_validated),
            "contraRate": self.contra_rate,
            "counterExamples": self.counter_examples,
            "evidence": self.evidence,
            "activatedInVersion": self.activated_in_version,
        }


@dataclass(frozen=True, slots=True)
class EmpiricalRules:
    entries: Mapping[str, RuleEntry] = field(default_factory=dict)
    system_item_ids: frozenset[int] = frozenset()

    @classmethod
    def disabled(cls) -> EmpiricalRules:
        """Nenhuma regra ligada em nenhum patch: o reducer se comporta exatamente como a v6."""
        return cls()

    def enabled_for(self, patch: str) -> frozenset[str]:
        return frozenset(
            rule_id for rule_id, entry in self.entries.items() if entry.enabled_for(patch)
        )

    def provenance_for(self, patch: str) -> list[dict[str, Any]]:
        return [self.entries[rule_id].provenance() for rule_id in sorted(self.enabled_for(patch))]


def _fail(detail: str) -> EmpiricalRulesConfigError:
    return EmpiricalRulesConfigError(detail)


def _int(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise _fail(f"{name} deve ser um inteiro")
    return value


def _parse_entry(rule_id: str, raw: Any, reconstruction_version: int) -> RuleEntry:
    if not isinstance(raw, dict) or set(raw) != _RULE_KEYS:
        raise _fail(f"regra {rule_id}: chaves obrigatórias {sorted(_RULE_KEYS)}")
    status = raw["status"]
    if status not in (STATUS_PENDING, STATUS_SUPPORTED):
        raise _fail(f"regra {rule_id}: status deve ser {STATUS_PENDING} ou {STATUS_SUPPORTED}")
    patches = raw["patchesValidated"]
    if not isinstance(patches, list) or not all(isinstance(p, str) and p for p in patches):
        raise _fail(f"regra {rule_id}: patchesValidated deve ser uma lista de patches")
    if len(set(patches)) != len(patches):
        raise _fail(f"regra {rule_id}: patchesValidated com repetição")
    description = raw["description"]
    if not isinstance(description, str) or not description.strip():
        raise _fail(f"regra {rule_id}: description obrigatória")
    rule_version = _int(raw["ruleVersion"], f"regra {rule_id}: ruleVersion")
    if rule_version < 1:
        raise _fail(f"regra {rule_id}: ruleVersion deve ser >= 1")

    if status == STATUS_PENDING:
        if (
            patches
            or raw["contraRate"] is not None
            or raw["counterExamples"] is not None
            or raw["evidence"] is not None
            or raw["activatedInVersion"] is not None
        ):
            raise _fail(
                f"regra {rule_id}: PENDING não pode carregar patches, taxa, evidência nem versão"
            )
        return RuleEntry(rule_id, rule_version, description, status)

    rate = raw["contraRate"]
    if (
        isinstance(rate, bool)
        or not isinstance(rate, (int, float))
        or not 0.0 <= float(rate) <= 1.0
    ):
        raise _fail(f"regra {rule_id}: contraRate deve estar em [0, 1]")
    examples = _int(raw["counterExamples"], f"regra {rule_id}: counterExamples")
    if examples < 0:
        raise _fail(f"regra {rule_id}: counterExamples deve ser >= 0")
    evidence = raw["evidence"]
    if not patches:
        raise _fail(f"regra {rule_id}: EMPIRICALLY_SUPPORTED exige patchesValidated")
    if not isinstance(evidence, str) or not evidence.strip():
        raise _fail(f"regra {rule_id}: EMPIRICALLY_SUPPORTED exige a referência da evidência")
    activated = _int(raw["activatedInVersion"], f"regra {rule_id}: activatedInVersion")
    if activated != reconstruction_version:
        raise _fail(
            f"regra {rule_id}: activatedInVersion={activated} difere da versão de "
            f"reconstrução corrente ({reconstruction_version}); ativar uma regra exige "
            "nova versão de reconstrução"
        )
    return RuleEntry(
        rule_id,
        rule_version,
        description,
        status,
        frozenset(patches),
        float(rate),
        examples,
        evidence,
        activated,
    )


def parse_empirical_rules(document: Any, *, reconstruction_version: int) -> EmpiricalRules:
    if not isinstance(document, dict) or set(document) != {
        "schemaVersion",
        "systemItemIds",
        "rules",
    }:
        raise _fail("documento deve ter exatamente schemaVersion, systemItemIds e rules")
    if document["schemaVersion"] != SCHEMA_VERSION:
        raise _fail(f"schemaVersion deve ser {SCHEMA_VERSION}")
    system = document["systemItemIds"]
    if not isinstance(system, list):
        raise _fail("systemItemIds deve ser uma lista")
    ids = [_int(item, "systemItemIds") for item in system]
    if len(set(ids)) != len(ids):
        raise _fail("systemItemIds com repetição")
    raw_rules = document["rules"]
    if not isinstance(raw_rules, dict) or set(raw_rules) != set(KNOWN_RULE_IDS):
        raise _fail(f"rules deve declarar exatamente {list(KNOWN_RULE_IDS)}")
    entries = {
        rule_id: _parse_entry(rule_id, raw_rules[rule_id], reconstruction_version)
        for rule_id in KNOWN_RULE_IDS
    }
    if entries[RULE_R4S].status == STATUS_SUPPORTED and not ids:
        raise _fail("R4s EMPIRICALLY_SUPPORTED exige systemItemIds não vazio")
    return EmpiricalRules(entries=entries, system_item_ids=frozenset(ids))


def load_empirical_rules(path: Path, *, reconstruction_version: int) -> EmpiricalRules:
    """Carrega e valida o registro. Inválido ⇒ erro explícito (nunca liga nada em silêncio)."""
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise _fail("registro de regras ilegível") from exc
    except yaml.YAMLError as exc:
        raise _fail("registro de regras não é um YAML válido") from exc
    return parse_empirical_rules(document, reconstruction_version=reconstruction_version)


def load_empirical_rules_or_disabled(path: Path, *, reconstruction_version: int) -> EmpiricalRules:
    """Arquivo AUSENTE ⇒ todas as regras desligadas (a imagem sem o registro se comporta
    como a v6). Arquivo presente porém inválido continua sendo erro."""
    if not path.exists():
        logger.warning("registro de regras empíricas ausente; regras desligadas")
        return EmpiricalRules.disabled()
    return load_empirical_rules(path, reconstruction_version=reconstruction_version)


def attribute_system_purchases(
    events: Sequence[TimelineEvent], support_participant_ids: Sequence[int]
) -> list[TimelineEvent]:
    """R4w (pré-passo PURO): as compras do World Atlas (`3865`) com `participantId` 0 são
    o item inicial de cada Apoio. Vínculo observável: na exploração havia exatamente 2
    compras do participante 0 e 2 participantes `UTILITY` em 20/20 partidas, e 38/38
    destroys do `3865` eram de Apoios. FALHA SEGURA: só reatribui se o número de compras
    do participante 0 for IGUAL ao de Apoios (e maior que zero); senão devolve os eventos
    sem alteração. Pareamento determinístico: compras em ordem de fonte × Apoios em ordem
    crescente de participantId (os eventos são idênticos, então o pareamento não muda
    nenhum resultado)."""
    zero = sorted(
        (
            event
            for event in events
            if event.type == "ITEM_PURCHASED"
            and event.payload.get("participantId") == 0
            and event.payload.get("itemId") == WORLD_ATLAS_ITEM_ID
        ),
        key=lambda event: event.source_index,
    )
    supports = sorted(support_participant_ids)
    if not zero or len(zero) != len(supports):
        return list(events)
    remap = {
        event.source_index: participant_id
        for event, participant_id in zip(zero, supports, strict=True)
    }
    return [
        TimelineEvent(
            event.match_id,
            event.source_index,
            event.timestamp_ms,
            event.type,
            {**event.payload, "participantId": remap[event.source_index]},
            event.schema_version,
        )
        if event.source_index in remap
        else event
        for event in events
    ]
