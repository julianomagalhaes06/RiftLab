from __future__ import annotations

import json
from dataclasses import dataclass, field

from riftlab.ingestion.infrastructure.raw_storage import RawMatchStorage
from riftlab.ingestion.infrastructure.riot_account import RiotAccountConfig
from riftlab.ingestion.infrastructure.riot_transport import RiotFetchBudget, RiotHttpTransport
from riftlab.shared.errors import (
    SourceAuthError,
    SourceError,
    SourceNotFoundError,
    SourceRateLimitedError,
)


@dataclass(slots=True)
class AuditSampleResult:
    """Resumo de uma execução do spike (CC-105); nunca é um relatório de
    auditoria real por si só — quem chama decide o que fazer com os IDs
    buscados (ex.: escrever `.private/validation/sprint-1/data-audit.md`)."""

    fetched: list[str] = field(default_factory=list)
    skipped_cached: list[str] = field(default_factory=list)
    excluded: dict[str, str] = field(default_factory=dict)
    stopped_early_reason: str | None = None


async def discover_seed_match_ids(
    transport: RiotHttpTransport,
    account: RiotAccountConfig,
    *,
    queue: int,
    max_seeds: int,
    page_size: int = 20,
) -> tuple[str, list[str]]:
    """Resolve puuid via Account-V1 e lista matches via Match-V5 (by-puuid) só
    para a conta local configurada — nenhum outro sujeito pode originar
    sementes. Pagina de forma limitada e deduplica; para ao atingir
    `max_seeds` ou numa página menor que `page_size` (fim real da lista).
    Retorna o puuid resolvido junto (reaproveitado por run_audit_sample para
    validar a identidade de cada partida, sem resolver a conta de novo).
    Nunca loga gameName/tagLine/puuid."""
    puuid = await transport.resolve_account_puuid(account.game_name, account.tag_line)

    seen: list[str] = []
    start = 0
    while len(seen) < max_seeds:
        remaining = max_seeds - len(seen)
        page = await transport.list_match_ids_by_puuid(
            puuid, queue=queue, start=start, count=min(page_size, remaining)
        )
        if not page:
            break
        for match_id in page:
            if match_id not in seen:
                seen.append(match_id)
            if len(seen) >= max_seeds:
                break  # nunca confia cegamente no servidor respeitar count
        start += len(page)
        if len(page) < page_size:
            break  # página incompleta: fim real da lista, não presumir mais

    return puuid, seen


async def run_audit_sample(
    transport: RiotHttpTransport,
    storage: RawMatchStorage,
    match_ids: list[str],
    budget: RiotFetchBudget,
    *,
    expected_puuid: str | None = None,
) -> AuditSampleResult:
    """Percorre as sementes sequencialmente (nunca em paralelo): pula o que já
    está válido em disco (retomada sem baixar de novo), registra 404 como
    exclusão diagnosticada e permanente, e para de vez em erro de autenticação
    ou limite de taxa/orçamento esgotado — sem tentar contornar. Falhas por
    match_id individual (schema/upstream) ficam só no resultado desta execução,
    nunca no manifesto: uma execução futura pode tentar de novo.

    Quando `expected_puuid` é dado (descoberta pela conta própria), cada
    partida buscada é conferida contra `metadata.participants` do corpo real
    antes de ser aceita — identidade da origem das sementes, não só do
    identificador. Divergência é uma exclusão silenciosa (nunca imprime o
    puuid), não um crash. Não é o worker durável do CC-201: sem fila, sem
    persistência em banco."""
    result = AuditSampleResult()

    for match_id in match_ids:
        if len(result.fetched) >= budget.max_matches:
            result.stopped_early_reason = "MAX_MATCHES_REACHED"
            break

        if storage.status(match_id) == "valid":
            result.skipped_cached.append(match_id)
            continue

        try:
            match = await transport.fetch_match(match_id)
            timeline = await transport.fetch_timeline(match_id)
        except SourceAuthError:
            result.stopped_early_reason = "RIOT_AUTH_FAILED"
            break
        except SourceRateLimitedError:
            result.stopped_early_reason = "RIOT_RATE_LIMITED"
            break
        except SourceNotFoundError:
            result.excluded[match_id] = "RIOT_NOT_FOUND"
            storage.record_exclusion(match_id, "RIOT_NOT_FOUND")
            continue
        except SourceError as exc:
            result.excluded[match_id] = exc.code
            continue

        if expected_puuid is not None and not _match_belongs_to_account(match.body, expected_puuid):
            result.excluded[match_id] = "ACCOUNT_IDENTITY_MISMATCH"
            continue

        storage.store(match, timeline)
        result.fetched.append(match_id)

    return result


def _match_belongs_to_account(match_body: bytes, expected_puuid: str) -> bool:
    try:
        document = json.loads(match_body)
    except json.JSONDecodeError:
        return False
    participants = (
        document.get("metadata", {}).get("participants", []) if isinstance(document, dict) else []
    )
    return expected_puuid in participants
