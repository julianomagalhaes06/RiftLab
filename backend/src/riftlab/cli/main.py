from __future__ import annotations

import typer
import uvicorn
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig

from riftlab.config import get_settings

app = typer.Typer(
    help="RiftLab CLI (cc). Comandos implementados nesta etapa: api, worker, health, db.",
    no_args_is_help=True,
)

db_app = typer.Typer(help="Migrações (Alembic).", no_args_is_help=True)
app.add_typer(db_app, name="db")


def _alembic_config() -> AlembicConfig:
    # alembic.ini fica na raiz do pacote backend; cc e alembic.ini compartilham cwd
    # tanto em desenvolvimento (uv run, a partir de backend/) quanto na imagem
    # (WORKDIR /app).
    return AlembicConfig("alembic.ini")


@db_app.command("upgrade")
def db_upgrade(
    revision: str = typer.Argument("head", help="Revisão alvo (padrão: head)."),
) -> None:
    """Aplica migrations até a revisão informada. Idempotente em head."""
    alembic_command.upgrade(_alembic_config(), revision)


@db_app.command("current")
def db_current() -> None:
    """Mostra a revisão atual do banco configurado (RIFTLAB_DATABASE_URL)."""
    alembic_command.current(_alembic_config(), verbose=True)


@app.command()
def api(
    host: str = typer.Option(None, help="Sobrescreve RIFTLAB_API_HOST."),
    port: int = typer.Option(None, help="Sobrescreve RIFTLAB_API_PORT."),
    reload: bool = typer.Option(False, help="Recarrega em mudancas de codigo (desenvolvimento)."),
) -> None:
    """Inicia a API FastAPI (uvicorn), health checks e nada mais nesta etapa."""
    settings = get_settings()
    uvicorn.run(
        "riftlab.api.main:app",
        host=host or settings.api_host,
        port=port or settings.api_port,
        reload=reload,
        log_config=None,
    )


@app.command()
def worker() -> None:
    """Inicia o worker minimo com heartbeat verificavel; sem processamento de fila."""
    from riftlab.worker.main import main as worker_main

    worker_main()


@app.command()
def health(
    host: str = typer.Option("127.0.0.1", help="Host da API a consultar."),
    port: int = typer.Option(None, help="Porta da API; usa RIFTLAB_API_PORT por padrao."),
    timeout: float = typer.Option(3.0, help="Timeout em segundos por chamada."),
) -> None:
    """Consulta /health/live e /health/ready localmente; saida != 0 se alguma falhar."""
    import httpx

    settings = get_settings()
    resolved_port = port or settings.api_port
    exit_code = 0
    for path in ("/health/live", "/health/ready"):
        url = f"http://{host}:{resolved_port}{path}"
        try:
            response = httpx.get(url, timeout=timeout)
            ok = response.status_code == 200
            detail = str(response.status_code)
        except httpx.HTTPError as exc:
            ok = False
            detail = f"sem resposta ({exc.__class__.__name__})"
        typer.echo(f"{path}: {'OK' if ok else 'FAIL'} ({detail})")
        if not ok:
            exit_code = 1
    raise typer.Exit(code=exit_code)


if __name__ == "__main__":
    app()
