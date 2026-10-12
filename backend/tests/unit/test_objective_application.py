"""CC-504: provedor intercambiável, validação estrita da saída do modelo, timeout e falha."""

import asyncio
import json

import httpx
import pytest

from riftlab.objectives.application import (
    Interpretation,
    LocalModelParser,
    RulesParser,
    build_parser,
    interpret_objective,
    validate_model_output,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('{"priority": "SURVIVAL"}', "SURVIVAL"),
        (' {"priority":"NEEDS_INPUT"}\n', "NEEDS_INPUT"),
        ('{"priority": "SURVIVAL", "debug": true}', None),  # chave extra
        ('{"priority": "AGGRESSIVE"}', None),  # fora do enum
        ('{"priority": ["DAMAGE"]}', None),
        ("DAMAGE", None),  # não é JSON
        ('["DAMAGE"]', None),
        ('{"priority": "DAMAGE"} e mais texto', None),
        ('{"priority": "DAMAGE"' + " " * 300 + "}", None),  # longo demais
    ],
)
def test_model_output_is_validated_strictly(raw: str, expected: str | None) -> None:
    assert validate_model_output(raw) == expected


def _sidecar(raw: object, status: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content) == {"text": "quero aguentar"}
        return httpx.Response(status, json={"raw": raw})

    return httpx.MockTransport(handler)


async def test_local_model_valid_output_is_traceable() -> None:
    parser = LocalModelParser(
        "http://sidecar/interpret", "lora_v1", _sidecar('{"priority": "SURVIVAL"}')
    )
    result = await interpret_objective(parser, "quero aguentar", 15)
    assert result.as_payload() == {
        "status": "INTERPRETED",
        "objective": {"priority": "SURVIVAL", "parserVersion": "lora_v1"},
        "parserVersion": "lora_v1",
        "provider": "LOCAL_MODEL",
        "reasonCodes": [],
    }


async def test_local_model_invalid_output_opens_form() -> None:
    parser = LocalModelParser(
        "http://sidecar/interpret", "lora_v1", _sidecar('{"priority": "DAMAGE", "x": 1}')
    )
    result = await interpret_objective(parser, "quero aguentar", 15)
    assert (result.status, result.priority, result.reason_codes) == (
        "NEEDS_INPUT",
        None,
        ("INVALID_MODEL_OUTPUT",),
    )


async def test_local_model_http_error_is_provider_error() -> None:
    parser = LocalModelParser("http://sidecar/interpret", "lora_v1", _sidecar("x", status=500))
    result = await interpret_objective(parser, "quero aguentar", 15)
    assert result.reason_codes == ("PROVIDER_ERROR",)


async def test_timeout_returns_needs_input_without_raising() -> None:
    class Slow:
        provider, version = "LOCAL_MODEL", "slow"

        async def interpret(self, text: str) -> Interpretation:
            await asyncio.sleep(5)
            raise AssertionError("não deveria terminar")

    result = await interpret_objective(Slow(), "quero dano", 0.05)
    assert (result.status, result.reason_codes, result.provider) == (
        "NEEDS_INPUT",
        ("TIMEOUT",),
        "LOCAL_MODEL",
    )


async def test_rules_provider_and_default_selection() -> None:
    result = await interpret_objective(RulesParser(), "quero mais mana", 15)
    assert (result.status, result.priority, result.provider, result.parser_version) == (
        "INTERPRETED",
        "UTILITY",
        "RULES",
        "rules_v1",
    )
    assert isinstance(build_parser("rules", None, "v"), RulesParser)
    # sem URL o modelo local não é selecionado: cai para regras
    assert isinstance(build_parser("local_model", None, "v"), RulesParser)
    assert isinstance(
        build_parser("local_model", "http://127.0.0.1:8765/interpret", "v"), LocalModelParser
    )
