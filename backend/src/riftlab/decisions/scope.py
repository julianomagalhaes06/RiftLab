"""Recorte de decisões por origem (CC-204, D5). REAL usa `configs/scope.yaml`
(congelado no CC-105, nunca alterado aqui); DEMO usa um recorte sintético
explícito e identificado, para poder testar um caso elegível sem afrouxar o
recorte real. A versão do recorte acompanha toda saída."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from riftlab.decisions.domain import (
    DecisionConfigError,
    DecisionInputError,
    MatchContext,
    ParticipantView,
)
from riftlab.shared.domain import SourceOrigin

DEMO_SCOPE_VERSION = "demo_scope_v1"


@dataclass(frozen=True, slots=True)
class DecisionScope:
    version: str
    origin: SourceOrigin
    queues: frozenset[int]
    map_id: int
    patches: frozenset[str]
    role: str
    champion_ids: frozenset[int]

    def out_of_scope_reasons(
        self, match: MatchContext, participant: ParticipantView
    ) -> tuple[str, ...]:
        """Sub-motivos (vazio = dentro do recorte). Role ausente nunca casa:
        `None` não é a role do recorte."""
        reasons: list[str] = []
        if match.queue not in self.queues:
            reasons.append("QUEUE_OUT_OF_SCOPE")
        if match.map_id != self.map_id:
            reasons.append("MAP_OUT_OF_SCOPE")
        if match.patch not in self.patches:
            reasons.append("PATCH_OUT_OF_SCOPE")
        if participant.role is None:
            reasons.append("ROLE_UNKNOWN")
        elif participant.role != self.role:
            reasons.append("ROLE_OUT_OF_SCOPE")
        if participant.champion_id not in self.champion_ids:
            reasons.append("CHAMPION_OUT_OF_SCOPE")
        return tuple(reasons)


# Recorte SINTÉTICO das fixtures demo (patches DEMO-1 e DEMO-3, queue/map e campeões/role
# das partidas DEMO). Nunca confundível com o recorte real: origem CURATED_DEMO
# e versão própria.
DEMO_SCOPE = DecisionScope(
    version=DEMO_SCOPE_VERSION,
    origin=SourceOrigin.CURATED_DEMO,
    queues=frozenset({420}),
    map_id=11,
    patches=frozenset({"DEMO-1", "DEMO-3"}),  # DEMO-3: roteiro da demonstração (CC-605)
    role="BOTTOM",
    champion_ids=frozenset({9001, 9002}),
)


def _require(condition: bool, detail: str) -> None:
    if not condition:
        raise DecisionConfigError(code="SCOPE_DOCUMENT_INVALID", detail=detail)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _queues(document: dict[str, Any]) -> frozenset[int]:
    """`queues` (lista, v3+) ou `queue` (inteiro, v1/v2); nunca os dois nem lista vazia."""
    if "queues" in document:
        _require("queue" not in document, "queue e queues ao mesmo tempo")
        queues = document["queues"]
        _require(
            isinstance(queues, list)
            and bool(queues)
            and all(map(_is_int, queues))
            and len(set(queues)) == len(queues),
            "queues inválido",
        )
        return frozenset(queues)
    _require(_is_int(document.get("queue")), "queue inválido")
    return frozenset({document["queue"]})


def parse_real_scope_document(
    content: bytes, adjacent_patches: tuple[str, ...] | None = None
) -> DecisionScope:
    """Lê `configs/scope.yaml`. Estrutura inválida é erro explícito, nunca um
    recorte parcial ou um default."""
    try:
        document: Any = yaml.safe_load(content)
    except yaml.YAMLError as exc:
        raise DecisionConfigError(code="SCOPE_DOCUMENT_INVALID", detail="YAML inválido") from exc
    _require(isinstance(document, dict), "raiz do recorte não é um objeto")
    schema_version, frozen_at = document.get("schemaVersion"), document.get("frozenAt")
    _require(isinstance(schema_version, str) and bool(schema_version), "schemaVersion ausente")
    _require(isinstance(frozen_at, str) and bool(frozen_at), "frozenAt ausente")
    queues = _queues(document)
    _require(_is_int(document.get("map")), "map inválido")
    role = document.get("role")
    _require(isinstance(role, str) and bool(role), "role inválido")
    champion_ids = document.get("championIds")
    _require(
        isinstance(champion_ids, list) and bool(champion_ids) and all(map(_is_int, champion_ids)),
        "championIds inválido",
    )
    game_patches: list[str] = []
    window = document.get("patchWindow")
    if window is None:
        patches = document.get("patches")
        _require(isinstance(patches, list) and bool(patches), "patches inválido")
        for entry in patches:
            game_patch = entry.get("gamePatch") if isinstance(entry, dict) else None
            if not isinstance(game_patch, str) or not game_patch:
                raise DecisionConfigError(
                    code="SCOPE_DOCUMENT_INVALID", detail="gamePatch inválido"
                )
            game_patches.append(game_patch)
    else:
        _require(
            isinstance(window, dict) and window.get("mode") == "rolling", "patchWindow inválido"
        )
        size = window.get("size")
        _require(_is_int(size) and 1 <= size <= 10, "patchWindow.size inválido")
        if adjacent_patches is None:
            raise DecisionConfigError(
                code="CURRENT_PATCH_NOT_DETECTED",
                detail="execute cc patch detect antes de usar a janela móvel",
            )
        _require(len(adjacent_patches) >= size, "janela detectada incompleta")
        game_patches = list(adjacent_patches[:size])
    effective = ",".join(game_patches)
    return DecisionScope(
        version=(
            f"scope_yaml:{schema_version}:{frozen_at}:{effective}"
            if window is not None
            else f"scope_yaml:{schema_version}:{frozen_at}"
        ),
        origin=SourceOrigin.COMPLETED_MATCH,
        queues=queues,
        map_id=document["map"],
        patches=frozenset(game_patches),
        role=role,
        champion_ids=frozenset(champion_ids),
    )


def load_real_scope(path: Path, adjacent_patches: tuple[str, ...] | None = None) -> DecisionScope:
    return parse_real_scope_document(path.read_bytes(), adjacent_patches)


def scope_for_origin(origin: SourceOrigin, real_scope: DecisionScope | None) -> DecisionScope:
    """DEMO usa o recorte sintético; REAL exige o recorte real explícito e
    nunca cai para o demo (nem o contrário)."""
    if origin is SourceOrigin.CURATED_DEMO:
        return DEMO_SCOPE
    if real_scope is None or real_scope.origin is not origin:
        raise DecisionInputError(
            code="REAL_SCOPE_REQUIRED",
            detail="origem real exige o recorte real (configs/scope.yaml) explícito",
        )
    return real_scope


__all__ = [
    "DEMO_SCOPE",
    "DEMO_SCOPE_VERSION",
    "DecisionScope",
    "load_real_scope",
    "parse_real_scope_document",
    "scope_for_origin",
]
