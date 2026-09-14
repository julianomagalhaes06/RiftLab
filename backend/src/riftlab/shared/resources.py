from __future__ import annotations

import json
from importlib import resources
from typing import Any


def read_package_bytes(package: str, relative_name: str) -> bytes:
    """Lê um recurso empacotado (ex.: fixture demo) pelo nome do pacote Python e um
    caminho relativo com '/' separando subdiretórios. Resolução independente do
    cwd: funciona igual em uv run, pytest e na imagem Docker, tanto em instalação
    editável (aponta para backend/src) quanto em wheel construído."""
    resource = resources.files(package)
    target = resource.joinpath(*relative_name.split("/"))
    return target.read_bytes()


def read_package_json(package: str, relative_name: str) -> Any:
    return json.loads(read_package_bytes(package, relative_name))
