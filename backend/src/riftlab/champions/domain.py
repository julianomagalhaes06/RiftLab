"""Catálogo de campeões do Data Dragon (CC-208): parser puro e validação estrita.

`champion.json` (`data/{locale}`) traz, por campeão, o `key` NUMÉRICO — é ele que liga
ao `championId` da Match-V5 — e o `id` TEXTUAL, usado no nome do arquivo do retrato
(`image.full`, ex.: key 62 → id "MonkeyKing" → "MonkeyKing.png", nome "Wukong").
Nada é divulgado sem validar `type`, `version`, `key`, `name` e `image.full`; entrada
inválida fica fora do catálogo (a interface mostra o rótulo numérico), e documento
inválido é rejeitado inteiro."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from riftlab.shared.errors import SourceSchemaError

# Arquivo do retrato: só letras/dígitos + ".png" — sem "/", "..", esquema ou query.
IMAGE_FILE_PATTERN = re.compile(r"^[A-Za-z0-9]{1,64}\.png$")
_KEY_PATTERN = re.compile(r"^[1-9]\d{0,5}$")
MAX_NAME_LENGTH = 100


@dataclass(frozen=True, slots=True)
class ChampionEntry:
    champion_key: int
    champion_text_id: str
    name: str
    image_file: str


@dataclass(frozen=True, slots=True)
class ChampionCatalog:
    data_dragon_version: str
    locale: str
    entries: tuple[ChampionEntry, ...]
    rejected: int  # entradas descartadas por não passarem na validação


def _entry(raw: Any) -> ChampionEntry | None:
    if not isinstance(raw, dict):
        return None
    key, text_id, name, image = raw.get("key"), raw.get("id"), raw.get("name"), raw.get("image")
    full = image.get("full") if isinstance(image, dict) else None
    if not (isinstance(key, str) and _KEY_PATTERN.fullmatch(key)):
        return None
    if not (isinstance(text_id, str) and re.fullmatch(r"[A-Za-z0-9]{1,64}", text_id)):
        return None
    if not (isinstance(name, str) and 0 < len(name.strip()) <= MAX_NAME_LENGTH):
        return None
    if not (isinstance(full, str) and IMAGE_FILE_PATTERN.fullmatch(full)):
        return None
    return ChampionEntry(int(key), text_id, name.strip(), full)


def parse_champion_catalog(body: bytes, *, expected_version: str, locale: str) -> ChampionCatalog:
    try:
        document: Any = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise SourceSchemaError(
            code="CHAMPION_CATALOG_INVALID_JSON", detail="champion.json inválido"
        ) from exc
    if not isinstance(document, dict) or document.get("type") != "champion":
        raise SourceSchemaError(
            code="CHAMPION_CATALOG_WRONG_TYPE", detail="type diferente de 'champion'"
        )
    if document.get("version") != expected_version:
        raise SourceSchemaError(
            code="CHAMPION_CATALOG_VERSION_MISMATCH",
            detail="versão do documento diferente da versão fixada para o patch",
        )
    data = document.get("data")
    if not isinstance(data, dict) or not data:
        raise SourceSchemaError(
            code="CHAMPION_CATALOG_MISSING_DATA", detail="data ausente ou vazio"
        )
    entries: dict[int, ChampionEntry] = {}
    rejected = 0
    duplicated: set[int] = set()
    for raw in data.values():
        entry = _entry(raw)
        if entry is None:
            rejected += 1
            continue
        if entry.champion_key in entries:
            duplicated.add(entry.champion_key)
            continue
        entries[entry.champion_key] = entry
    for key in duplicated:  # key repetido é ambíguo: nenhum dos dois é divulgado
        entries.pop(key, None)
        rejected += 2
    return ChampionCatalog(
        expected_version,
        locale,
        tuple(sorted(entries.values(), key=lambda e: e.champion_key)),
        rejected,
    )
