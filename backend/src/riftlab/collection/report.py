"""Relatório da coleta a partir do manifesto (CC-206): por rodada e acumulado. Só
contagens, rótulos pseudônimos, patches e datas — nenhum Riot ID, PUUID, hash, matchId
nem chave."""

from __future__ import annotations

from typing import Any

from riftlab.collection.manifest import CollectionManifest


def build_collection_report(
    manifest: CollectionManifest, *, run: int | None = None
) -> dict[str, Any]:
    """`run`: índice (1 = primeira) de uma rodada; None = a última."""
    runs = manifest.runs
    selected: dict[str, Any] | None = None
    if runs:
        selected = runs[(run - 1) if run is not None else -1]
    cooldown = manifest.cooldown_until
    blocked = manifest.auth_blocked_at
    known_by_seed = manifest.fetched_by_seed()
    return {
        "totals": {
            "runs": len(runs),
            "requestsUsedTotal": manifest.requests_used_total,
            "fetched": len(manifest.fetched_ids()),
            "fetchedByPatch": dict(sorted(manifest.fetched_by_patch().items())),
            "fetchedBySeed": dict(sorted(manifest.fetched_by_seed().items())),
            "excludedByReason": dict(sorted(manifest.excluded_by_reason().items())),
            "excludedTotal": sum(manifest.excluded_by_reason().values()),
            "gameDates": manifest.date_ranges(),
        },
        "resume": {
            "cooldownUntil": cooldown.isoformat() if cooldown else None,
            "authBlockedAt": blocked.isoformat() if blocked else None,
            "seeds": {
                label: {
                    "passesCompleted": manifest.cursor(label).passes_completed,
                    "exhausted": manifest.cursor(label).exhausted,
                    "known": known_by_seed.get(label, 0),
                }
                for label in manifest.seed_labels()
            },
        },
        "run": selected,
    }


def render_collection_report(data: dict[str, Any]) -> str:
    totals, resume, run = data["totals"], data["resume"], data["run"]
    lines = [
        f"coleta acumulada: {totals['runs']} rodadas | {totals['requestsUsedTotal']} requisições | "
        f"{totals['fetched']} partidas coletadas | {totals['excludedTotal']} excluídas",
        f"coletadas por patch: {totals['fetchedByPatch'] or '-'}",
        f"coletadas por semente: {totals['fetchedBySeed'] or '-'}",
        f"excluídas por motivo: {totals['excludedByReason'] or '-'}",
        f"datas (coletadas por patch): {totals['gameDates']['fetchedByPatch'] or '-'}",
        f"datas (excluídas por motivo): {totals['gameDates']['excludedByReason'] or '-'}",
    ]
    for label, cursor in resume["seeds"].items():
        state = (
            "passada concluída"
            if cursor["exhausted"]
            else "passada aberta (a próxima relista do início; as conhecidas custam 0 requisição)"
        )
        lines.append(
            f"  {label}: {state} | coletadas: {cursor['known']} "
            f"(passadas completas: {cursor['passesCompleted']})"
        )
    if resume["authBlockedAt"]:
        lines.append(
            "BLOQUEIO por credencial desde "
            f"{resume['authBlockedAt']}: troque a chave e use --resume-after-auth-failure"
        )
    if resume["cooldownUntil"]:
        lines.append(f"cooldown de rate limit até {resume['cooldownUntil']}")
    if run is None:
        lines.append("nenhuma rodada registrada.")
        return "\n".join(lines)
    lines += [
        (
            f"rodada {run['runId']}: parada={run['stopReason']} | requisições={run['requests']} "
            f"| raw reaproveitado={run['rawReused']} | espera do limitador="
            f"{run['limiterWaitSeconds']}s"
        ),
        (
            f"  listadas={run['listed']} | duplicadas={run['duplicates']} | novas válidas="
            f"{run['newValid']} | excluídas={run['excludedTotal']} {run['excluded'] or ''} | "
            f"falhas transitórias={run['transientFailures']}"
        ),
    ]
    observed = run.get("observed") or {}
    limiter = observed.get("rateLimiter") or {}
    lines.append(
        f"  observado: status={observed.get('statusByKind')} | maior página="
        f"{observed.get('maxListPageLength')}/{observed.get('requestedPageSize')} | janelas="
        f"{limiter.get('appWindowsInEffect')} (do servidor: "
        f"{limiter.get('appWindowsLearnedFromServer')}) | headers={limiter.get('headerNamesSeen')}"
        f" | Retry-After={limiter.get('retryAfterSecondsSeen')}"
    )
    for label, stats in run["perSeed"].items():
        error = f" ERRO={stats['error']}" if stats["error"] else ""
        lines.append(
            f"  {label}: listadas={stats['listed']} duplicadas={stats['duplicates']} "
            f"novas={stats['newValid']} excluídas={stats['excluded']}{error}"
        )
    if run["cooldownSecondsRemaining"]:
        lines.append(f"  cooldown restante: {run['cooldownSecondsRemaining']}s")
    return "\n".join(lines)
