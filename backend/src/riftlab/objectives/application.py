"""CC-504: objective interpretation behind an interchangeable provider.

Providers: RULES (deterministic, default, no model) and LOCAL_MODEL (an optional loopback
sidecar that only generates text from the user's text; it has no tools, network, shell or
secrets). Every model output is validated strictly against configs/objective-schema.json;
anything else, a timeout or a provider failure becomes NEEDS_INPUT and the UI opens the
form. The text is never logged and never changes legality, prices or attributes: the
confirmed objective feeds the same explicit reordering as the form (CC-304).
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Protocol

import httpx

from riftlab.objectives.rules import NEEDS_INPUT, RULES_VERSION, interpret

logger = logging.getLogger(__name__)
PRIORITIES = ("BALANCED", "SURVIVAL", "DAMAGE", "UTILITY")
INTERPRETED = "INTERPRETED"
MAX_RAW_OUTPUT = 200


@dataclass(frozen=True, slots=True)
class Interpretation:
    status: str
    priority: str | None
    parser_version: str
    provider: str
    reason_codes: tuple[str, ...] = field(default_factory=tuple)

    def as_payload(self) -> dict[str, object]:
        return {
            "status": self.status,
            "objective": {"priority": self.priority, "parserVersion": self.parser_version}
            if self.priority
            else None,
            "parserVersion": self.parser_version,
            "provider": self.provider,
            "reasonCodes": list(self.reason_codes),
        }


class ObjectiveParser(Protocol):
    provider: str
    version: str

    async def interpret(self, text: str) -> Interpretation: ...


def from_label(label: str, provider: str, version: str) -> Interpretation:
    if label in PRIORITIES:
        return Interpretation(INTERPRETED, label, version, provider)
    return Interpretation(NEEDS_INPUT, None, version, provider, ("AMBIGUOUS_OR_OUT_OF_SCOPE",))


def validate_model_output(raw: str) -> str | None:
    """The label if `raw` is exactly {"priority": <enum>}, else None (strict schema)."""
    if len(raw) > MAX_RAW_OUTPUT:
        return None
    try:
        value = json.loads(raw.strip())
    except ValueError:
        return None
    if not isinstance(value, dict) or set(value) != {"priority"}:
        return None
    label = value["priority"]
    return label if label in (*PRIORITIES, NEEDS_INPUT) else None


class RulesParser:
    provider = "RULES"
    version = RULES_VERSION

    async def interpret(self, text: str) -> Interpretation:
        return from_label(interpret(text), self.provider, self.version)


class LocalModelParser:
    """POSTs {"text"} to the sidecar (ml/objectives serve) and validates {"raw"}."""

    provider = "LOCAL_MODEL"

    def __init__(self, url: str, version: str, transport: httpx.AsyncBaseTransport | None = None):
        self.url = url
        self.version = version
        self._transport = transport

    async def interpret(self, text: str) -> Interpretation:
        async with httpx.AsyncClient(transport=self._transport, timeout=None) as client:
            response = await client.post(self.url, json={"text": text})
        response.raise_for_status()
        body = response.json()
        raw = body.get("raw") if isinstance(body, dict) else None
        label = validate_model_output(raw) if isinstance(raw, str) else None
        if label is None:
            return Interpretation(
                NEEDS_INPUT, None, self.version, self.provider, ("INVALID_MODEL_OUTPUT",)
            )
        return from_label(label, self.provider, self.version)


async def interpret_objective(
    parser: ObjectiveParser, text: str, timeout_seconds: float
) -> Interpretation:
    try:
        return await asyncio.wait_for(parser.interpret(text), timeout_seconds)
    except TimeoutError:
        return Interpretation(NEEDS_INPUT, None, parser.version, parser.provider, ("TIMEOUT",))
    except Exception as exc:  # noqa: BLE001 — qualquer falha do provedor abre o formulário
        logger.warning("objective parser failed: %s", type(exc).__name__)
        return Interpretation(
            NEEDS_INPUT, None, parser.version, parser.provider, ("PROVIDER_ERROR",)
        )


def build_parser(kind: str, url: str | None, version: str) -> ObjectiveParser:
    if kind == "local_model" and url:
        return LocalModelParser(url, version)
    return RulesParser()
