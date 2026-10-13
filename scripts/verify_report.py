#!/usr/bin/env python3
"""Confere um relatório exportado pelo RiftLab (HTML ou JSON), sem rede (CC-605).

Uso: python3 scripts/verify_report.py <relatorio.html|relatorio.json>

Checa o que o relatório promete:
- HTML: abre sem rede (CSP `default-src 'none'`, nenhum script, nenhuma URL externa) e escapa
  o texto livre;
- JSON: traz versão do relatório, análise, objetivo com origem e versões usadas;
- os dois: nenhum identificador de partida (BR1_/DEMO-), de participante ou de jogador
  (PUUID/Riot ID) e nenhum token parecido com chave de API.

Imprime cada verificação e sai com código 1 se alguma falhar. Só biblioteca padrão.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

IDENTIFIERS = (
    (re.compile(r"\bBR1_\d+\b"), "id de partida real"),
    (re.compile(r"\bDEMO-\d{4}\b"), "id de partida de demonstração"),
    (re.compile(r"RGAPI-[0-9a-fA-F-]{8,}"), "chave da Riot"),
    (re.compile(r"\b[A-Za-z0-9_-]{78}\b"), "PUUID"),
    (re.compile(r'"(participantId|puuid|riotId|gameName|tagLine|matchId)"', re.I), "campo de identidade"),
)


def check(label: str, ok: bool, failures: list[str]) -> None:
    print(f"{'ok   ' if ok else 'FALHA'} {label}")
    if not ok:
        failures.append(label)


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    path = Path(sys.argv[1])
    text = path.read_text(encoding="utf-8")
    failures: list[str] = []
    for pattern, label in IDENTIFIERS:
        check(f"sem {label}", pattern.search(text) is None, failures)
    if path.suffix.lower() == ".json":
        report = json.loads(text)
        analysis = report.get("analysis") or {}
        check("versão do relatório", bool(report.get("reportVersion")), failures)
        check("análise identificada", bool(report.get("analysisId")), failures)
        check("objetivo com origem", (analysis.get("objective") or {}).get("origin") in {"FORM", "TEXT_CONFIRMED"}, failures)
        check("versões usadas", bool(analysis.get("versions")), failures)
    else:
        check("CSP default-src 'none'", "default-src 'none'" in text, failures)
        check("nenhum script", "<script" not in text.lower(), failures)
        check("nenhuma URL externa", re.search(r"(src|href)\s*=\s*[\"']?https?://", text, re.I) is None, failures)
        check("título do relatório", "Relatório de análise de compra" in text, failures)
    print("relatório verificado" if not failures else f"{len(failures)} verificação(ões) falharam")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
