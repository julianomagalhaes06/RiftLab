from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from riftlab.ingestion.infrastructure.riot_transport import RawRiotResponse

# backend/src/riftlab/ingestion/infrastructure/raw_storage.py -> parents[5] é a
# raiz do repositório. Absoluto e fixo pelo layout do pacote — nunca relativo
# ao cwd de quem invoca `cc`. Bug real encontrado na retomada do CC-105: um
# default relativo (`Path("data/raw/cc-105")`) gravou dados reais em
# `backend/data/...` quando a CLI rodou a partir de `backend/` — caminho que o
# `.gitignore` da raiz (`/data/`, ancorado) não cobre. Corrigido para sempre
# apontar para `<raiz>/data/raw/cc-105`, que o `.gitignore` cobre de verdade.
DEFAULT_OUT_DIR = Path(__file__).resolve().parents[5] / "data" / "raw" / "cc-105"


class RawStorageIntegrityError(Exception):
    """Arquivo local existente diverge do hash registrado no manifesto local
    (CC-105); resolução manual é exigida — a fonte nunca sobrescreve dado
    divergente automaticamente."""


class RawMatchStorage:
    """Persistência local do spike (CC-105): corpos brutos match+timeline e um
    manifesto JSON, exclusivamente em diretório git-ignorado (ex.: data/raw/cc-105),
    para retomar sem baixar de novo respostas já válidas. Não é o volume/tabela do
    worker durável (CC-201); nenhum corpo real chega a fixtures versionadas."""

    def __init__(self, directory: Path) -> None:
        self._directory = directory
        self._manifest_path = directory / "manifest.json"
        directory.mkdir(parents=True, exist_ok=True)
        self._manifest: dict[str, Any] = self._load_manifest()

    def status(self, match_id: str) -> str:
        """ "missing" (nunca buscado, ou arquivo ausente), "valid" (bytes em disco
        batem com o hash do manifesto — pode pular a nova busca) ou "corrupted"
        (diverge; retomada automática seria uma sobrescrita silenciosa, proibida
        pelo plano do card)."""
        entry = self._manifest["matches"].get(match_id)
        if entry is None:
            return "missing"
        match_path = self._body_path(match_id, "match")
        timeline_path = self._body_path(match_id, "timeline")
        if not match_path.exists() or not timeline_path.exists():
            return "missing"
        match_ok = hashlib.sha256(match_path.read_bytes()).hexdigest() == entry["matchSha256"]
        timeline_ok = (
            hashlib.sha256(timeline_path.read_bytes()).hexdigest() == entry["timelineSha256"]
        )
        return "valid" if (match_ok and timeline_ok) else "corrupted"

    def store(self, match: RawRiotResponse, timeline: RawRiotResponse) -> None:
        if match.kind != "match" or timeline.kind != "timeline":
            raise ValueError("store() espera exatamente um RawRiotResponse de cada kind")
        if match.match_id != timeline.match_id:
            raise ValueError("match e timeline pertencem a match_id diferentes")

        status = self.status(match.match_id)
        if status == "corrupted":
            raise RawStorageIntegrityError(
                f"dado local existente para {match.match_id} diverge do manifesto; "
                "resolucao manual exigida, sem sobrescrita automatica"
            )

        self._body_path(match.match_id, "match").write_bytes(match.body)
        self._body_path(match.match_id, "timeline").write_bytes(timeline.body)
        self._manifest["matches"][match.match_id] = {
            "matchSha256": match.sha256,
            "timelineSha256": timeline.sha256,
            "fetchedAt": match.fetched_at.isoformat(),
        }
        self._write_manifest()

    def record_exclusion(self, match_id: str, reason_code: str) -> None:
        """Registra uma exclusão diagnosticada de verdade (ex.: RIOT_NOT_FOUND,
        docs/contracts.md) sem gravar corpo algum. Só chamado para motivos
        permanentes — falhas transitórias (rede/5xx/orçamento) não entram aqui,
        para que uma execução futura possa tentar de novo."""
        self._manifest["excluded"][match_id] = {"reasonCode": reason_code}
        self._write_manifest()

    def excluded_reason(self, match_id: str) -> str | None:
        entry = self._manifest["excluded"].get(match_id)
        return entry["reasonCode"] if entry else None

    def requests_used_total(self) -> int:
        """Requisições HTTP reais já consumidas por execuções anteriores deste
        manifesto (persistido, não só em memória). Permite que um smoke run
        pequeno e uma execução ampliada depois compartilhem um único teto de
        requisições, sem reiniciar o orçamento artificialmente a cada
        invocação da CLI."""
        budget_state: dict[str, Any] = self._manifest["budget"]
        return int(budget_state.get("requestsUsedTotal", 0))

    def record_requests_used(self, count: int) -> None:
        if count <= 0:
            return
        budget_state: dict[str, Any] = self._manifest["budget"]
        budget_state["requestsUsedTotal"] = budget_state.get("requestsUsedTotal", 0) + count
        self._write_manifest()

    def _body_path(self, match_id: str, kind: str) -> Path:
        return self._directory / f"{match_id}.{kind}.json"

    def _load_manifest(self) -> dict[str, Any]:
        if not self._manifest_path.exists():
            return {"matches": {}, "excluded": {}, "budget": {}}
        loaded: dict[str, Any] = json.loads(self._manifest_path.read_text())
        loaded.setdefault("matches", {})
        loaded.setdefault("excluded", {})
        loaded.setdefault("budget", {})
        return loaded

    def _write_manifest(self) -> None:
        self._manifest_path.write_text(json.dumps(self._manifest, indent=2, sort_keys=True))
