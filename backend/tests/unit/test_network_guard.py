"""CC-601: a guarda de rede dos testes bloqueia hosts externos e deixa o loopback passar."""

import socket

import pytest

BLOCKED = "rede externa bloqueada nos testes"  # mensagem da guarda em tests/conftest.py


def test_external_address_is_blocked_before_leaving_the_machine() -> None:
    # 203.0.113.0/24 é reservado para documentação: nada responderia, e nada chega a sair
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(1)
        with pytest.raises(OSError, match=BLOCKED):
            sock.connect(("203.0.113.10", 443))


def test_external_host_name_is_blocked() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        with pytest.raises(OSError, match=BLOCKED):
            sock.connect(("americas.api.riotgames.com", 443))


def test_loopback_is_not_blocked() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.create_connection(server.getsockname(), timeout=1):
            pass
