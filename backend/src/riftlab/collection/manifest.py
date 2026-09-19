"""Manifesto durável da coleta (CC-206): cursor por semente, partidas coletadas e
excluídas (com motivo), cooldown, bloqueio por credencial e histórico de rodadas.

Só rótulos pseudônimos de semente, matchIds (identificadores públicos de partida) e
contagens: nenhum Riot ID, PUUID, hash de jogador, chave, nem qualquer valor derivado
da chave. Gravação atômica (arquivo temporário + fsync + os.replace): um crash nunca
deixa o manifesto pela metade. Uma execução por vez (flock exclusivo): duas coletas
simultâneas do mesmo manifesto são recusadas, não intercaladas. Arquivo corrompido ou
de versão desconhecida NUNCA é sobrescrito — a coleta para e pede resolução manual."""

from __future__ import annotations

import fcntl
import json
import os
import uuid
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

MANIFEST_SCHEMA_VERSION = 1
STATUS_FETCHED = "FETCHED"
STATUS_EXCLUDED = "EXCLUDED"
MAX_TRANSIENT_FAILURES_PER_MATCH = 3


class ManifestError(Exception):
    """Manifesto ilegível, com schema desconhecido ou inconsistente."""


class ManifestLockedError(Exception):
    """Outra coleta está usando este manifesto."""


@dataclass(frozen=True, slots=True)
class SeedCursor:
    """Estado da PASSADA por semente, sem posição na listagem. A listagem da Riot vem da
    mais nova para a mais antiga e cresce pela CABEÇA: um índice guardado entre execuções
    apontaria para outra partida assim que alguém jogasse (a nova entraria no índice 0 e a
    retomada a pularia até a passada seguinte), e mudar `--since`/`--until` mudaria a
    lista sob o mesmo índice. Por isso cada passada relista do início e o progresso vive
    nos ids conhecidos do manifesto (FETCHED/EXCLUDED): partida conhecida custa 0
    requisição, só a página de listagem é paga de novo (1 por página de até 100 ids)."""

    passes_completed: int = 0
    exhausted: bool = False  # a última passada chegou ao fim da listagem


def _empty_document() -> dict[str, Any]:
    return {
        "schemaVersion": MANIFEST_SCHEMA_VERSION,
        "requestsUsedTotal": 0,
        "cooldownUntil": None,
        "authBlockedAt": None,
        "seeds": {},
        "matches": {},
        "excluded": {},
        "transientFailures": {},
        "runs": [],
    }


class CollectionManifest:
    def __init__(self, directory: Path) -> None:
        self._directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        self._path = directory / "manifest.json"
        self._lock_path = directory / "manifest.lock"
        self._doc = self._load()

    # ---- ciclo de vida -------------------------------------------------------

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        """Uma coleta por vez neste manifesto (flock não bloqueante)."""
        handle = self._lock_path.open("a+")
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ManifestLockedError(
                    "outra coleta está em andamento neste manifesto; nada foi feito"
                ) from exc
            # depois do lock: recarrega (outra execução pode ter gravado antes)
            self._doc = self._load()
            yield
        finally:
            handle.close()  # fecha o descritor e libera o flock

    def _load(self) -> dict[str, Any]:
        if not self._path.exists():
            return _empty_document()
        try:
            loaded = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ManifestError("manifesto ilegível; resolução manual exigida") from exc
        if not isinstance(loaded, dict) or loaded.get("schemaVersion") != MANIFEST_SCHEMA_VERSION:
            raise ManifestError("manifesto com schemaVersion desconhecida; resolução manual")
        document = _empty_document()
        document.update(loaded)
        for key in ("seeds", "matches", "excluded", "transientFailures"):
            if not isinstance(document[key], dict):
                raise ManifestError(f"manifesto inconsistente: {key} não é um objeto")
        if not isinstance(document["runs"], list):
            raise ManifestError("manifesto inconsistente: runs não é uma lista")
        return document

    def save(self) -> None:
        tmp_path = self._path.with_name(f"{self._path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
        payload = json.dumps(self._doc, indent=2, sort_keys=True, ensure_ascii=False)
        with tmp_path.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, self._path)

    # ---- partidas ------------------------------------------------------------

    def status_of(self, match_id: str) -> str | None:
        if match_id in self._doc["matches"]:
            return STATUS_FETCHED
        if match_id in self._doc["excluded"]:
            return STATUS_EXCLUDED
        return None

    def mark_fetched(
        self,
        match_id: str,
        *,
        seed: str,
        patch: str,
        run_id: str,
        at: datetime,
        game_date: str | None = None,
    ) -> None:
        self._doc["matches"][match_id] = {
            "seed": seed,
            "patch": patch,
            "runId": run_id,
            "fetchedAt": at.isoformat(),
            "gameDate": game_date,
        }
        self._doc["transientFailures"].pop(match_id, None)

    def mark_excluded(
        self, match_id: str, *, reason: str, seed: str, run_id: str, game_date: str | None = None
    ) -> None:
        self._doc["excluded"][match_id] = {
            "reason": reason,
            "seed": seed,
            "runId": run_id,
            "gameDate": game_date,
        }
        self._doc["transientFailures"].pop(match_id, None)

    def record_transient_failure(self, match_id: str) -> int:
        count = int(self._doc["transientFailures"].get(match_id, 0)) + 1
        self._doc["transientFailures"][match_id] = count
        return count

    def fetched_ids(self) -> list[str]:
        return sorted(self._doc["matches"])

    def excluded_by_reason(self) -> Counter[str]:
        return Counter(entry["reason"] for entry in self._doc["excluded"].values())

    def date_ranges(self) -> dict[str, dict[str, dict[str, str]]]:
        """Menor e maior data de partida (AAAA-MM-DD) das COLETADAS por patch e das
        EXCLUÍDAS por motivo (quando a data era conhecida)."""

        def spread(entries: dict[str, list[str]]) -> dict[str, dict[str, str]]:
            return {key: {"min": min(v), "max": max(v)} for key, v in sorted(entries.items()) if v}

        fetched: dict[str, list[str]] = {}
        for entry in self._doc["matches"].values():
            if entry.get("gameDate"):
                fetched.setdefault(entry["patch"], []).append(entry["gameDate"])
        excluded: dict[str, list[str]] = {}
        for entry in self._doc["excluded"].values():
            if entry.get("gameDate"):
                excluded.setdefault(entry["reason"], []).append(entry["gameDate"])
        return {"fetchedByPatch": spread(fetched), "excludedByReason": spread(excluded)}

    def fetched_by_seed(self) -> Counter[str]:
        return Counter(entry["seed"] for entry in self._doc["matches"].values())

    def fetched_by_patch(self) -> Counter[str]:
        return Counter(entry["patch"] for entry in self._doc["matches"].values())

    # ---- cursor --------------------------------------------------------------

    def cursor(self, label: str) -> SeedCursor:
        raw = self._doc["seeds"].get(label, {})
        # manifestos antigos (Rodadas 1 a 2b) traziam "nextStart": lido e descartado de
        # propósito (a posição não é mais usada; a primeira gravação a remove)
        return SeedCursor(
            passes_completed=int(raw.get("passesCompleted", 0)),
            exhausted=bool(raw.get("exhausted", False)),
        )

    def set_cursor(self, label: str, cursor: SeedCursor) -> None:
        self._doc["seeds"][label] = {
            "passesCompleted": cursor.passes_completed,
            "exhausted": cursor.exhausted,
        }

    def seed_labels(self) -> list[str]:
        return sorted(self._doc["seeds"])

    # ---- orçamento, cooldown e credencial ------------------------------------

    @property
    def requests_used_total(self) -> int:
        return int(self._doc["requestsUsedTotal"])

    def add_requests(self, count: int) -> None:
        if count > 0:
            self._doc["requestsUsedTotal"] = self.requests_used_total + count

    @property
    def cooldown_until(self) -> datetime | None:
        raw = self._doc["cooldownUntil"]
        return datetime.fromisoformat(raw) if raw else None

    def set_cooldown_until(self, until: datetime | None) -> None:
        self._doc["cooldownUntil"] = until.isoformat() if until else None

    @property
    def auth_blocked_at(self) -> datetime | None:
        raw = self._doc["authBlockedAt"]
        return datetime.fromisoformat(raw) if raw else None

    def set_auth_blocked_at(self, at: datetime | None) -> None:
        self._doc["authBlockedAt"] = at.isoformat() if at else None

    # ---- rodadas -------------------------------------------------------------

    def append_run(self, run: dict[str, Any]) -> None:
        self._doc["runs"].append(run)

    @property
    def runs(self) -> list[dict[str, Any]]:
        return list(self._doc["runs"])
