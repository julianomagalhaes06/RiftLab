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

Herança (schema v2, reconstrução v10): um patch SEM confirmação própria herda as regras
do patch validado mais recente ANTERIOR a ele (`inheritance.mode:
LATEST_VALIDATED_EARLIER`). Assim o patch atual e cada patch novo são reconstruídos com
as mesmas regras do último patch confirmado, mas a proveniência grava
`application: INHERITED` e o patch de origem: herdar não é validar, e a interface diz
isso. Um patch com qualquer regra própria usa só as suas (nunca mistura), patches
anteriores ao primeiro validado e patches não numéricos (DEMO) nunca herdam.

Domínios (schema v3, reconstrução v12, CC-802): uma confirmação pode valer só para
uma fila de um patch (`domains`, chave `patch/fila`). A resolução é por (patch,
fila): regras validadas no patch inteiro (`patchesValidated`) ou no domínio da fila
(`domainsValidated`). Uma fila sem confirmação própria herda do validado mais recente
anterior que cubra essa fila; outra fila do mesmo patch nunca é fonte. Cada domínio
declara o contexto de catálogo em que foi confirmado, e a reconstrução usa esse
contexto para as partidas do domínio e para quem herda dele.

Invariante de identidade (sem migração): ativar uma regra muda o RESULTADO computado,
então exige uma nova versão de reconstrução. O carregador recusa uma regra
`EMPIRICALLY_SUPPORTED` (ou uma herança ligada) cuja `activatedInVersion` não seja a
versão de reconstrução corrente, de modo que estados de uma versão nunca sejam
reaproveitados com outro conjunto de regras."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from riftlab.shared.errors import DomainError
from riftlab.shared.numeric import patch_order_key
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
# CC-1002 (exploração no treino do 16.19; nascem PENDING, só ligam com confirmação):
RULE_R1E = "R1e"  # consumível destruído antes da própria compra no mesmo instante
RULE_R6V = "R6v"  # undo correlacionado (imediato ou em pilha) aplicado sem marcar
RULE_R8M = "R8m"  # missão do MID já concluída: bota tier 2 comprada vira o sucessor na hora
RULE_R9 = "R9"  # transformação por linhagem (o resultado continua a identidade da origem)
RULE_R10J = "R10j"  # pet da Selva sai do inventário quando a missão da Selva termina
RULE_R11S = "R11s"  # cadeia do item de Apoio: Atlas -> Bússola -> Recompensa sem compra
RULE_R12W = "R12w"  # Sentinela de Controle do Apoio no espaço do papel, fora dos espaços normais
RULE_R8B = "R8b"  # missão do BOTTOM concluída: botas compradas/melhoradas no espaço do papel
RULE_R11F = "R11f"  # escolha final do Apoio sem compra: a Recompensa (3867) continua a identidade
RULE_R7H = "R7h"  # Olho do Arauto (3513) concedido ocupa o espaço do amuleto até ser usado
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
    RULE_R1E,
    RULE_R6V,
    RULE_R8M,
    RULE_R9,
    RULE_R10J,
    RULE_R11S,
    RULE_R12W,
    RULE_R8B,
    RULE_R11F,
    RULE_R7H,
)
# Itens concedidos sem evento de compra, por regra (forma da exploração CC-324).
GRANTED_ITEM_IDS_BY_RULE: dict[str, frozenset[int]] = {
    RULE_R7B: frozenset({2010}),
    RULE_R7M: frozenset({2422}),
    RULE_R7X: frozenset({2150, 2151, 2152}),
    RULE_R7H: frozenset({3513}),
}
BOOTS_ITEM_ID = 1001
BOT_LANE_QUEST_ITEM_ID = 1202
MID_LANE_QUEST_ITEM_ID = 1201
JUNGLE_QUEST_ITEM_ID = 1204
JUNGLE_PET_ITEM_IDS = frozenset({1101, 1102, 1103})
CONTROL_WARD_ITEM_ID = 2055
# R11s: cada etapa destruída vira a seguinte sem evento de compra (specialRecipe do Data Dragon).
SUPPORT_CHAIN_NEXT: dict[int, int] = {3865: 3866, 3866: 3867}
# R11f: a Recompensa dos Mundos e as cinco escolhas finais (receita "from 3867" no Data Dragon).
SUPPORT_BOUNTY_ITEM_ID = 3867
SUPPORT_FINAL_ITEM_IDS = frozenset({3869, 3870, 3871, 3876, 3877})
HERALD_EYE_ITEM_ID = 3513
# R9: origem -> resultado. specialRecipe do Data Dragon (3003, 3004, 3119, 2526) e a
# Braçadeira do Buscador quebrada depois do uso (2420 -> 2421, mesma receita).
TRANSFORM_RESULT_BY_ORIGIN: dict[int, int] = {
    3003: 3040,
    3004: 3042,
    3119: 3121,
    2526: 2530,
    2420: 2421,
}
# Marca do pré-passo R12w nos eventos de Sentinela de um Apoio.
ROLE_SLOT_PAYLOAD_KEY = "roleSlot"

STATUS_PENDING = "PENDING"
STATUS_SUPPORTED = "EMPIRICALLY_SUPPORTED"
SCHEMA_VERSION = "v3"
_LEGACY_SCHEMA_VERSION = "v2"  # sem domínios; ainda aceito
CONTEXT_CLOSURE = "EVENT_RECIPE_CLOSURE_V11"
CONTEXT_FULL = "FULL_CANONICAL_V1"
_CONTEXTS = frozenset({CONTEXT_CLOSURE, CONTEXT_FULL})
_QUEUES = frozenset({420, 440})

INHERITANCE_NONE = "NONE"
INHERITANCE_LATEST_EARLIER = "LATEST_VALIDATED_EARLIER"
APPLICATION_VALIDATED = "VALIDATED"
APPLICATION_INHERITED = "INHERITED"

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
_DOMAIN_KEYS = frozenset({"patch", "queueId", "catalogContext", "evidence", "activatedInVersion"})
_DOMAIN_RULE_KEYS = frozenset({"domain", "contraRate", "counterExamples", "evidence"})

Domain = tuple[str, int]


def domain_key(domain: Domain) -> str:
    return f"{domain[0]}/{domain[1]}"


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
    # Confirmação restrita a (patch, fila): taxa, contraexemplos e evidência do domínio.
    domains_validated: Mapping[Domain, tuple[float, int, str]] = field(default_factory=dict)

    def enabled_for(self, patch: str) -> bool:
        return self.status == STATUS_SUPPORTED and patch in self.patches_validated

    def enabled_in(self, patch: str, queue: int | None) -> bool:
        return self.enabled_for(patch) or (
            self.status == STATUS_SUPPORTED and (patch, queue) in self.domains_validated
        )

    def provenance(self, domain: Domain | None = None) -> dict[str, Any]:
        """Proveniência gravada em `quality.empiricalRules` (só das regras LIGADAS)."""
        payload = {
            "ruleId": self.rule_id,
            "ruleVersion": self.rule_version,
            "status": self.status,
            "patchesValidated": sorted(self.patches_validated),
            "contraRate": self.contra_rate,
            "counterExamples": self.counter_examples,
            "evidence": self.evidence,
            "activatedInVersion": self.activated_in_version,
        }
        if self.domains_validated:
            payload["domainsValidated"] = sorted(domain_key(d) for d in self.domains_validated)
        if domain is not None and domain in self.domains_validated:
            rate, examples, evidence = self.domains_validated[domain]
            payload.update(contraRate=rate, counterExamples=examples, evidence=evidence)
        return payload


@dataclass(frozen=True, slots=True)
class DomainEntry:
    patch: str
    queue: int
    catalog_context: str
    evidence: str


@dataclass(frozen=True, slots=True)
class RuleResolution:
    """Regras ligadas num (patch, fila) e de onde vêm: VALIDATED (confirmação do próprio
    patch ou domínio), INHERITED (do `source_patch`, o validado mais recente anterior que
    cobre a fila) ou nada. `source_queue` só existe quando a origem é um domínio."""

    rule_ids: frozenset[str]
    application: str | None = None
    source_patch: str | None = None
    source_queue: int | None = None
    catalog_context: str = CONTEXT_CLOSURE


@dataclass(frozen=True, slots=True)
class EmpiricalRules:
    entries: Mapping[str, RuleEntry] = field(default_factory=dict)
    system_item_ids: frozenset[int] = frozenset()
    inheritance: str = INHERITANCE_NONE
    domains: Mapping[Domain, DomainEntry] = field(default_factory=dict)

    @classmethod
    def disabled(cls) -> EmpiricalRules:
        """Nenhuma regra ligada em nenhum patch: o reducer se comporta exatamente como a v6."""
        return cls()

    def validated_for(self, patch: str, queue: int | None = None) -> frozenset[str]:
        return frozenset(
            rule_id for rule_id, entry in self.entries.items() if entry.enabled_in(patch, queue)
        )

    def _context(self, patch: str, queue: int | None) -> tuple[int | None, str]:
        domain = self.domains.get((patch, queue)) if queue is not None else None
        if domain is None or not any(
            (patch, queue) in entry.domains_validated for entry in self.entries.values()
        ):
            return None, CONTEXT_CLOSURE
        return queue, domain.catalog_context

    def resolve(self, patch: str, queue: int | None = None) -> RuleResolution:
        own = self.validated_for(patch, queue)
        if own:
            source_queue, context = self._context(patch, queue)
            return RuleResolution(own, APPLICATION_VALIDATED, patch, source_queue, context)
        key = patch_order_key(patch)
        if self.inheritance != INHERITANCE_LATEST_EARLIER or key is None:
            return RuleResolution(frozenset())
        # Fontes: validação do patch inteiro ou do domínio da MESMA fila; outra fila do
        # mesmo patch nunca é fonte. Domínio vence o patch inteiro no mesmo patch.
        earlier = [
            (candidate_key, 0, candidate)
            for entry in self.entries.values()
            if entry.status == STATUS_SUPPORTED
            for candidate in entry.patches_validated
            if (candidate_key := patch_order_key(candidate)) is not None and candidate_key < key
        ] + [
            (candidate_key, 1, candidate)
            for entry in self.entries.values()
            if entry.status == STATUS_SUPPORTED
            for candidate, candidate_queue in entry.domains_validated
            if candidate_queue == queue
            and (candidate_key := patch_order_key(candidate)) is not None
            and candidate_key < key
        ]
        if not earlier:
            return RuleResolution(frozenset())
        source = max(earlier)[2]
        source_queue, context = self._context(source, queue)
        return RuleResolution(
            self.validated_for(source, queue), APPLICATION_INHERITED, source, source_queue, context
        )

    def enabled_for(self, patch: str, queue: int | None = None) -> frozenset[str]:
        return self.resolve(patch, queue).rule_ids

    def provenance_for(self, patch: str, queue: int | None = None) -> list[dict[str, Any]]:
        resolution = self.resolve(patch, queue)
        domain = (
            (resolution.source_patch, resolution.source_queue)
            if resolution.source_patch is not None and resolution.source_queue is not None
            else None
        )
        extra = (
            {
                "appliedFromQueue": resolution.source_queue,
                "catalogContext": resolution.catalog_context,
            }
            if domain is not None
            else {}
        )
        return [
            {
                **self.entries[rule_id].provenance(domain),
                "application": resolution.application,
                "appliedFromPatch": resolution.source_patch,
                **extra,
            }
            for rule_id in sorted(resolution.rule_ids)
        ]


def _fail(detail: str) -> EmpiricalRulesConfigError:
    return EmpiricalRulesConfigError(detail)


def _int(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise _fail(f"{name} deve ser um inteiro")
    return value


def _rate(value: Any, name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not 0.0 <= float(value) <= 1.0
    ):
        raise _fail(f"{name} deve estar em [0, 1]")
    return float(value)


def _parse_domain_rules(
    rule_id: str, raw: Any, domains: Mapping[Domain, DomainEntry]
) -> dict[Domain, tuple[float, int, str]]:
    if not isinstance(raw, list):
        raise _fail(f"regra {rule_id}: domainsValidated deve ser uma lista")
    parsed: dict[Domain, tuple[float, int, str]] = {}
    for item in raw:
        if not isinstance(item, dict) or set(item) != _DOMAIN_RULE_KEYS:
            raise _fail(f"regra {rule_id}: domínio exige {sorted(_DOMAIN_RULE_KEYS)}")
        key = item["domain"]
        domain = next((d for d in domains if domain_key(d) == key), None)
        if domain is None:
            raise _fail(f"regra {rule_id}: domínio {key!r} não declarado em domains")
        if domain in parsed:
            raise _fail(f"regra {rule_id}: domínio {key!r} repetido")
        examples = _int(item["counterExamples"], f"regra {rule_id}: counterExamples do domínio")
        evidence = item["evidence"]
        if examples < 0 or not isinstance(evidence, str) or not evidence.strip():
            raise _fail(f"regra {rule_id}: domínio {key!r} exige contraexemplos e evidência")
        parsed[domain] = (
            _rate(item["contraRate"], f"regra {rule_id}: contraRate"),
            examples,
            evidence,
        )
    return parsed


def _parse_entry(
    rule_id: str,
    raw: Any,
    reconstruction_version: int,
    domains: Mapping[Domain, DomainEntry] | None = None,
) -> RuleEntry:
    keys = _RULE_KEYS if domains is None else _RULE_KEYS | {"domainsValidated"}
    if not isinstance(raw, dict) or set(raw) != keys:
        raise _fail(f"regra {rule_id}: chaves obrigatórias {sorted(keys)}")
    domain_rules = (
        _parse_domain_rules(rule_id, raw["domainsValidated"], domains)
        if domains is not None
        else {}
    )
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
            or domain_rules
            or raw["contraRate"] is not None
            or raw["counterExamples"] is not None
            or raw["evidence"] is not None
            or raw["activatedInVersion"] is not None
        ):
            raise _fail(
                f"regra {rule_id}: PENDING não pode carregar patches, taxa, evidência nem versão"
            )
        return RuleEntry(rule_id, rule_version, description, status)

    rate = _rate(raw["contraRate"], f"regra {rule_id}: contraRate")
    examples = _int(raw["counterExamples"], f"regra {rule_id}: counterExamples")
    if examples < 0:
        raise _fail(f"regra {rule_id}: counterExamples deve ser >= 0")
    evidence = raw["evidence"]
    if not patches and not domain_rules:
        raise _fail(
            f"regra {rule_id}: EMPIRICALLY_SUPPORTED exige patchesValidated ou domainsValidated"
        )
    if any(patch in patches for patch, _ in domain_rules):
        raise _fail(f"regra {rule_id}: domínio repete um patch já validado por inteiro")
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
        rate,
        examples,
        evidence,
        activated,
        domain_rules,
    )


def _parse_domains(raw: Any, reconstruction_version: int) -> dict[Domain, DomainEntry]:
    if not isinstance(raw, dict):
        raise _fail("domains deve ser um mapa patch/fila -> domínio")
    domains: dict[Domain, DomainEntry] = {}
    for key, item in raw.items():
        if not isinstance(item, dict) or set(item) != _DOMAIN_KEYS:
            raise _fail(f"domínio {key!r}: chaves obrigatórias {sorted(_DOMAIN_KEYS)}")
        patch, queue = item["patch"], item["queueId"]
        if not isinstance(patch, str) or patch_order_key(patch) is None:
            raise _fail(f"domínio {key!r}: patch numérico obrigatório")
        if isinstance(queue, bool) or queue not in _QUEUES:
            raise _fail(f"domínio {key!r}: queueId deve ser 420 ou 440")
        if key != domain_key((patch, queue)):
            raise _fail(f"domínio {key!r}: chave deve ser {domain_key((patch, queue))!r}")
        if item["catalogContext"] not in _CONTEXTS:
            raise _fail(f"domínio {key!r}: catalogContext desconhecido")
        if not isinstance(item["evidence"], str) or not item["evidence"].strip():
            raise _fail(f"domínio {key!r}: evidence obrigatória")
        if _int(item["activatedInVersion"], f"domínio {key!r}: activatedInVersion") != (
            reconstruction_version
        ):
            raise _fail(
                f"domínio {key!r}: activatedInVersion difere da versão de reconstrução corrente "
                f"({reconstruction_version}); ativar um domínio exige nova versão"
            )
        domains[(patch, queue)] = DomainEntry(
            patch, queue, item["catalogContext"], item["evidence"]
        )
    return domains


def _parse_inheritance(raw: Any, reconstruction_version: int) -> str:
    if not isinstance(raw, dict) or set(raw) != {"mode", "activatedInVersion"}:
        raise _fail("inheritance deve ter exatamente mode e activatedInVersion")
    mode = raw["mode"]
    if mode not in (INHERITANCE_NONE, INHERITANCE_LATEST_EARLIER):
        raise _fail(f"inheritance.mode deve ser {INHERITANCE_NONE} ou {INHERITANCE_LATEST_EARLIER}")
    if mode == INHERITANCE_NONE:
        if raw["activatedInVersion"] is not None:
            raise _fail("inheritance NONE não pode carregar activatedInVersion")
        return mode
    activated = _int(raw["activatedInVersion"], "inheritance.activatedInVersion")
    if activated != reconstruction_version:
        raise _fail(
            f"inheritance.activatedInVersion={activated} difere da versão de reconstrução "
            f"corrente ({reconstruction_version}); ligar a herança exige nova versão"
        )
    return mode


def parse_empirical_rules(document: Any, *, reconstruction_version: int) -> EmpiricalRules:
    base_keys = {"schemaVersion", "inheritance", "systemItemIds", "rules"}
    version = document.get("schemaVersion") if isinstance(document, dict) else None
    expected = base_keys | {"domains"} if version == SCHEMA_VERSION else base_keys
    if not isinstance(document, dict) or set(document) != expected:
        raise _fail(
            "documento deve ter exatamente schemaVersion, inheritance, systemItemIds e rules"
            " (e domains no schema v3)"
        )
    if version not in (SCHEMA_VERSION, _LEGACY_SCHEMA_VERSION):
        raise _fail(f"schemaVersion deve ser {SCHEMA_VERSION} ou {_LEGACY_SCHEMA_VERSION}")
    domains = (
        _parse_domains(document["domains"], reconstruction_version)
        if version == SCHEMA_VERSION
        else None
    )
    inheritance = _parse_inheritance(document["inheritance"], reconstruction_version)
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
        rule_id: _parse_entry(rule_id, raw_rules[rule_id], reconstruction_version, domains)
        for rule_id in KNOWN_RULE_IDS
    }
    if entries[RULE_R4S].status == STATUS_SUPPORTED and not ids:
        raise _fail("R4s EMPIRICALLY_SUPPORTED exige systemItemIds não vazio")
    unused = set(domains or {}) - {d for e in entries.values() for d in e.domains_validated}
    if unused:
        raise _fail(f"domínios sem regra validada: {sorted(domain_key(d) for d in unused)}")
    return EmpiricalRules(
        entries=entries,
        system_item_ids=frozenset(ids),
        inheritance=inheritance,
        domains=domains or {},
    )


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


def mark_support_ward_slot(
    events: Sequence[TimelineEvent], support_participant_ids: Sequence[int]
) -> list[TimelineEvent]:
    """R12w (pré-passo PURO): os eventos de Sentinela de Controle (`2055`) de cada Apoio
    ganham a marca `roleSlot`. Forma da exploração CC-1002 (treino 16.19): a missão do Apoio
    repõe a Sentinela sem custo (compra no mesmo instante do destroy de 1203/1208), metade
    dos Apoios termina com a Sentinela no espaço do papel (`roleBoundItem`) e nenhum dos
    14.919 Apoios a tinha nos espaços normais no fim. O reducer, com a regra ligada, não
    conta essas Sentinelas no inventário normal."""
    supports = set(support_participant_ids)
    return [
        TimelineEvent(
            event.match_id,
            event.source_index,
            event.timestamp_ms,
            event.type,
            {**event.payload, ROLE_SLOT_PAYLOAD_KEY: True},
            event.schema_version,
        )
        if event.payload.get("participantId") in supports
        and event.type in ("ITEM_PURCHASED", "ITEM_DESTROYED", "ITEM_SOLD")
        and event.payload.get("itemId") == CONTROL_WARD_ITEM_ID
        else event
        for event in events
    ]


def apply_role_prepasses(
    events: Sequence[TimelineEvent],
    enabled_rules: frozenset[str],
    support_participant_ids: Sequence[int],
) -> list[TimelineEvent]:
    """Pré-passos que dependem da função (R4w, R12w), na ordem fixa, só com a regra ligada."""
    result = list(events)
    if RULE_R4W in enabled_rules:
        result = attribute_system_purchases(result, support_participant_ids)
    if RULE_R12W in enabled_rules:
        result = mark_support_ward_slot(result, support_participant_ids)
    return result
