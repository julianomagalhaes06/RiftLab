from __future__ import annotations

import hashlib
import hmac

from riftlab.shared.errors import DomainError


class SubjectHashSecretMissingError(DomainError):
    """RIFTLAB_SUBJECT_HASH_SECRET ausente em modo RIOT (CC-201). Falha segura
    e explícita: nunca substitui por hash simples, chave fixa ou vazio — isso
    tornaria subject_hash reversível/correlacionável entre partidas. DEMO
    nunca levanta isto (fixtures não têm PUUID real, ver
    ingestion.infrastructure.demo_source._demo_subject_hash)."""


def compute_real_subject_hash(puuid: str, secret: str | None) -> str:
    """HMAC-SHA256(secret, puuid) — nunca hash(puuid) sozinho (dicionário/
    rainbow table trivial sobre o espaço de PUUIDs reais já vistos) nem uma
    chave pública/fixa (perderia a propriedade de segredo). O puuid em si
    nunca é logado nem devolvido; só o hash resultante."""
    if not secret:
        raise SubjectHashSecretMissingError(
            "RIFTLAB_SUBJECT_HASH_SECRET não configurado; importação RIOT não "
            "pode persistir identidade de participante com segurança"
        )
    return hmac.new(secret.encode("utf-8"), puuid.encode("utf-8"), hashlib.sha256).hexdigest()
